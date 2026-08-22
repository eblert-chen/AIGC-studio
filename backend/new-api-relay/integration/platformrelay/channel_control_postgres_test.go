//go:build integration

package platformrelay_test

import (
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

func TestPostgresChannelControlGuardConcurrentInstallAndTamperRejection(t *testing.T) {
	const installers = 8
	start := make(chan struct{})
	errorsCh := make(chan error, installers)
	var wait sync.WaitGroup
	for range installers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			if err := model.InstallPlatformChannelControlRevisionGuard(); err != nil {
				errorsCh <- err
				return
			}
			errorsCh <- model.InstallPlatformChannelControlOperationGuards()
		}()
	}
	close(start)
	wait.Wait()
	close(errorsCh)
	for err := range errorsCh {
		requireNoError(t, err)
	}

	channel := model.Channel{
		Id:          int(time.Now().UnixNano()%1_000_000_000) + 1,
		Name:        "channel-control-guard-" + uuid.NewString(),
		Key:         "guard-provider-key-" + uuid.NewString(),
		Status:      common.ChannelStatusEnabled,
		Models:      "provider-model",
		CreatedTime: time.Now().Unix(),
	}
	requireNoError(t, integrationDB.Create(&channel).Error)
	operationID := "channel-control-guard-" + uuid.NewString()
	tenantID := "00000000-0000-4000-8000-000000000001"
	operation, execute, replay, err := model.BeginPlatformChannelTestOperation(model.PlatformChannelControlIntent{
		OperationID: operationID,
		TenantID:    tenantID,
		ChannelID:   channel.Id,
		Kind:        model.PlatformChannelControlOperationKindTest,
		RequestID:   "channel-control-guard-request",
		Actor:       "platform-owner-1",
		Reason:      "Verify PostgreSQL receipt guards",
	})
	requireNoError(t, err)
	if !execute || replay {
		t.Fatalf("first guard test intent was not executable: execute=%v replay=%v", execute, replay)
	}

	if err := integrationDB.Exec(
		"UPDATE platform_channel_control_operations SET actor = 'tampered' WHERE id = ?",
		operation.ID,
	).Error; err == nil {
		t.Fatal("PostgreSQL accepted channel-control intent tampering")
	}
	if err := integrationDB.Exec(
		"DELETE FROM platform_channel_control_operations WHERE id = ?",
		operation.ID,
	).Error; err == nil {
		t.Fatal("PostgreSQL accepted channel-control receipt deletion")
	}
	if err := integrationDB.Exec("TRUNCATE TABLE platform_channel_control_operations").Error; err == nil {
		t.Fatal("PostgreSQL accepted channel-control receipt truncation")
	}

	completed, err := model.CompletePlatformChannelTestOperation(
		tenantID,
		operationID,
		false,
		17,
		model.PlatformChannelControlErrorTestFailed,
	)
	requireNoError(t, err)
	if completed.State != model.PlatformChannelControlOperationFailed {
		t.Fatalf("unexpected completed state %q", completed.State)
	}
	if err := integrationDB.Exec(
		"UPDATE platform_channel_control_operations SET result_response_ms = 99 WHERE id = ?",
		operation.ID,
	).Error; err == nil {
		t.Fatal("PostgreSQL accepted terminal channel-control receipt tampering")
	}

	var actor string
	requireNoError(t, integrationDB.Raw(
		"SELECT actor FROM platform_channel_control_operations WHERE id = ?",
		operation.ID,
	).Scan(&actor).Error)
	if actor != "platform-owner-1" {
		t.Fatal(fmt.Sprintf("channel-control actor was mutated: %q", actor))
	}
}

func TestPostgresChannelControlRevisionIsMonotonicAcrossNativeConcurrencyAndABA(t *testing.T) {
	weight := uint(0)
	channel := model.Channel{
		Id:          int(time.Now().UnixNano()%1_000_000_000) + 50,
		Name:        "channel-control-revision-" + uuid.NewString(),
		Key:         "revision-provider-key-" + uuid.NewString(),
		Status:      common.ChannelStatusEnabled,
		Models:      "provider-model",
		Weight:      &weight,
		CreatedTime: time.Now().Unix(),
	}
	requireNoError(t, integrationDB.Create(&channel).Error)
	baselineRevision := channel.ControlRevision

	const writers = 12
	start := make(chan struct{})
	errorsCh := make(chan error, writers)
	var wait sync.WaitGroup
	for range writers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			errorsCh <- integrationDB.Model(&model.Channel{}).Where("id = ?", channel.Id).
				Update("weight", gorm.Expr("COALESCE(weight, 0) + 1")).Error
		}()
	}
	close(start)
	wait.Wait()
	close(errorsCh)
	for err := range errorsCh {
		requireNoError(t, err)
	}

	var saved model.Channel
	requireNoError(t, integrationDB.First(&saved, channel.Id).Error)
	if saved.ControlRevision != baselineRevision+writers {
		t.Fatalf("concurrent native mutations lost revisions: got=%d want=%d", saved.ControlRevision, baselineRevision+writers)
	}
	if saved.Weight == nil || *saved.Weight != writers {
		t.Fatalf("concurrent native weight updates were lost: %#v", saved.Weight)
	}

	// Health observations and accounting are deliberately revision-neutral.
	requireNoError(t, integrationDB.Model(&model.Channel{}).Where("id = ?", channel.Id).Updates(map[string]any{
		"test_time":            time.Now().Unix(),
		"response_time":        83,
		"balance":              7.5,
		"balance_updated_time": time.Now().Unix(),
		"used_quota":           int64(81),
	}).Error)
	requireNoError(t, integrationDB.First(&saved, channel.Id).Error)
	if saved.ControlRevision != baselineRevision+writers {
		t.Fatalf("health/accounting noise changed control revision: got=%d want=%d", saved.ControlRevision, baselineRevision+writers)
	}

	// A stale native full-row value cannot write its old control_revision over
	// a newer database-owned value.
	stale := saved
	requireNoError(t, model.RotateChannelCredentialSet(channel.Id, "rotated-provider-key-"+uuid.NewString()))
	stale.Name = "stale-native-save-" + uuid.NewString()
	requireNoError(t, stale.SaveWithoutKey())
	requireNoError(t, integrationDB.First(&saved, channel.Id).Error)
	if saved.ControlRevision != baselineRevision+writers+2 || saved.ControlRevision <= stale.ControlRevision {
		t.Fatalf("stale native save overwrote control revision: stale=%d saved=%d", stale.ControlRevision, saved.ControlRevision)
	}

	// Platform status uses the same trigger and therefore advances exactly one
	// revision, not one in Go plus another in PostgreSQL.
	platformExpectedRevision := model.PlatformChannelControlRevision(saved)
	receipt, replay, err := model.ApplyPlatformChannelStatusOperation(model.PlatformChannelControlIntent{
		OperationID:      "channel-control-pg-status-" + uuid.NewString(),
		TenantID:         "00000000-0000-4000-8000-000000000001",
		ChannelID:        channel.Id,
		Kind:             model.PlatformChannelControlOperationKindStatus,
		RequestID:        "channel-control-pg-request-1",
		Actor:            "platform-owner-1",
		Reason:           "Verify one PostgreSQL revision bump",
		ExpectedRevision: platformExpectedRevision,
		TargetStatus:     common.ChannelStatusManuallyDisabled,
	})
	requireNoError(t, err)
	if replay {
		t.Fatal("first PostgreSQL status operation unexpectedly replayed")
	}
	requireNoError(t, integrationDB.First(&saved, channel.Id).Error)
	if saved.ControlRevision != baselineRevision+writers+3 || receipt.ResultRevision != model.PlatformChannelControlRevision(saved) {
		t.Fatalf("Platform status did not produce one exact revision bump: revision=%d receipt=%q", saved.ControlRevision, receipt.ResultRevision)
	}

	// Native status ABA returns to the same visible status but must still make
	// an earlier Platform approval stale.
	approvalRevision := model.PlatformChannelControlRevision(saved)
	requireNoError(t, integrationDB.Model(&model.Channel{}).Where("id = ?", channel.Id).
		Update("status", common.ChannelStatusEnabled).Error)
	requireNoError(t, integrationDB.Model(&model.Channel{}).Where("id = ?", channel.Id).
		Update("status", common.ChannelStatusManuallyDisabled).Error)
	requireNoError(t, integrationDB.First(&saved, channel.Id).Error)
	if saved.ControlRevision != baselineRevision+writers+5 || saved.Status != common.ChannelStatusManuallyDisabled {
		t.Fatalf("native status ABA was not versioned: status=%d revision=%d", saved.Status, saved.ControlRevision)
	}

	failed, _, err := model.ApplyPlatformChannelStatusOperation(model.PlatformChannelControlIntent{
		OperationID:      "channel-control-pg-stale-" + uuid.NewString(),
		TenantID:         "00000000-0000-4000-8000-000000000001",
		ChannelID:        channel.Id,
		Kind:             model.PlatformChannelControlOperationKindStatus,
		RequestID:        "channel-control-pg-request-2",
		Actor:            "platform-owner-1",
		Reason:           "Reject approval captured before native ABA",
		ExpectedRevision: approvalRevision,
		TargetStatus:     common.ChannelStatusEnabled,
	})
	if !errors.Is(err, model.ErrPlatformChannelControlRevisionConflict) || failed == nil {
		t.Fatalf("stale Platform approval was not rejected after native ABA: receipt=%#v err=%v", failed, err)
	}
}

func TestPostgresChannelControlSameOperationAcrossChannelsIsStableConflict(t *testing.T) {
	tenantID := "00000000-0000-4000-8000-000000000001"
	operationID := "channel-control-cross-channel-" + uuid.NewString()
	channels := make([]model.Channel, 2)
	for index := range channels {
		channels[index] = model.Channel{
			Id:          int(time.Now().UnixNano()%1_000_000_000) + index + 100,
			Name:        fmt.Sprintf("cross-channel-%d-%s", index, uuid.NewString()),
			Key:         "provider-key-" + uuid.NewString(),
			Status:      common.ChannelStatusEnabled,
			Models:      "provider-model",
			CreatedTime: time.Now().Unix(),
		}
		requireNoError(t, integrationDB.Create(&channels[index]).Error)
	}

	type outcome struct {
		receipt *model.PlatformChannelControlOperation
		err     error
	}
	start := make(chan struct{})
	outcomes := make(chan outcome, len(channels))
	var wait sync.WaitGroup
	for index := range channels {
		wait.Add(1)
		go func(channel model.Channel, index int) {
			defer wait.Done()
			<-start
			receipt, _, err := model.ApplyPlatformChannelStatusOperation(model.PlatformChannelControlIntent{
				OperationID:      operationID,
				TenantID:         tenantID,
				ChannelID:        channel.Id,
				Kind:             model.PlatformChannelControlOperationKindStatus,
				RequestID:        fmt.Sprintf("cross-channel-request-%d", index),
				Actor:            "platform-owner-1",
				Reason:           "Exercise cross-channel operation fencing",
				ExpectedRevision: model.PlatformChannelControlRevision(channel),
				TargetStatus:     common.ChannelStatusManuallyDisabled,
			})
			outcomes <- outcome{receipt: receipt, err: err}
		}(channels[index], index)
	}
	close(start)
	wait.Wait()
	close(outcomes)
	succeeded := 0
	conflicted := 0
	for result := range outcomes {
		switch {
		case result.err == nil:
			succeeded++
			if result.receipt == nil || result.receipt.State != model.PlatformChannelControlOperationSucceeded {
				t.Fatalf("successful cross-channel outcome lacked terminal receipt: %#v", result.receipt)
			}
		case errors.Is(result.err, model.ErrPlatformChannelControlOperationConflict):
			conflicted++
		default:
			t.Fatalf("unexpected cross-channel operation error: %v", result.err)
		}
	}
	if succeeded != 1 || conflicted != 1 {
		t.Fatalf("expected one success and one semantic conflict, got success=%d conflict=%d", succeeded, conflicted)
	}
	var count int64
	requireNoError(t, integrationDB.Model(&model.PlatformChannelControlOperation{}).
		Where("tenant_id = ? AND operation_id = ?", tenantID, operationID).Count(&count).Error)
	if count != 1 {
		t.Fatalf("expected one durable operation receipt, got %d", count)
	}
}
