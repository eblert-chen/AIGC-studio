//go:build integration

package platformrelay_test

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

func TestIntegrationUsesPostgreSQL16Redis7AOFAndMultipleConnections(t *testing.T) {
	var postgresVersion string
	requireNoError(t, integrationDB.Raw("SHOW server_version").Scan(&postgresVersion).Error)
	if !strings.HasPrefix(postgresVersion, "16.") {
		t.Fatalf("expected PostgreSQL 16, got %q", postgresVersion)
	}

	redisClient := newRedisClient(t)
	serverInfo, err := redisClient.Info(context.Background(), "server").Result()
	requireNoError(t, err)
	if !strings.Contains(serverInfo, "redis_version:7.") {
		t.Fatalf("expected Redis 7, server INFO was %q", serverInfo)
	}
	persistenceInfo, err := redisClient.Info(context.Background(), "persistence").Result()
	requireNoError(t, err)
	if !strings.Contains(persistenceInfo, "aof_enabled:1") {
		t.Fatalf("Redis integration server must have AOF enabled: %q", persistenceInfo)
	}

	const workers = 12
	start := make(chan struct{})
	pids := make(chan int, workers)
	errorsCh := make(chan error, workers)
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			var pid int
			err := integrationSQLDB.QueryRowContext(
				context.Background(),
				"SELECT pg_backend_pid() FROM pg_sleep(0.15)",
			).Scan(&pid)
			if err != nil {
				errorsCh <- err
				return
			}
			pids <- pid
		}()
	}
	close(start)
	wait.Wait()
	close(pids)
	close(errorsCh)
	for err := range errorsCh {
		requireNoError(t, err)
	}
	unique := make(map[int]struct{})
	for pid := range pids {
		unique[pid] = struct{}{}
	}
	if len(unique) < 2 {
		t.Fatalf("integration harness did not exercise multiple PostgreSQL connections: %v", unique)
	}
}

func TestPostgresChannelInfoPersistsWithSimpleProtocol(t *testing.T) {
	resetIntegrationState(t)
	channel := model.Channel{
		Id:     900001,
		Name:   "postgres-channel-info-regression",
		Key:    "integration-key",
		Status: common.ChannelStatusEnabled,
		ChannelInfo: model.ChannelInfo{
			IsMultiKey:           true,
			MultiKeySize:         2,
			MultiKeyStatusList:   map[int]int{0: common.ChannelStatusEnabled, 1: common.ChannelStatusManuallyDisabled},
			MultiKeyPollingIndex: 1,
		},
	}
	requireNoError(t, integrationDB.Create(&channel).Error)

	var saved model.Channel
	requireNoError(t, integrationDB.First(&saved, channel.Id).Error)
	if !saved.ChannelInfo.IsMultiKey || saved.ChannelInfo.MultiKeySize != 2 || saved.ChannelInfo.MultiKeyPollingIndex != 1 {
		t.Fatalf("channel_info did not round trip through PostgreSQL: %+v", saved.ChannelInfo)
	}
	if saved.ChannelInfo.MultiKeyStatusList[0] != common.ChannelStatusEnabled ||
		saved.ChannelInfo.MultiKeyStatusList[1] != common.ChannelStatusManuallyDisabled {
		t.Fatalf("channel_info status map changed during PostgreSQL round trip: %+v", saved.ChannelInfo.MultiKeyStatusList)
	}
}

func TestPostgresAdmissionFencesAcceptedChannelTypeDrift(t *testing.T) {
	resetIntegrationState(t)
	route := createProviderRoute(t, "integration.accepted-channel-type", "text_to_video", 20, 2)
	job, outbox := createQueuedGeneration(t, route.Model, route.Mode)
	workerClaim, err := model.ClaimPlatformGenerationSubmission(30*time.Second, outbox.ID)
	requireNoError(t, err)
	routeClaim, err := model.ClaimPlatformGenerationProviderRoute(job.ID, route.Model, route.Mode)
	requireNoError(t, err)
	_, err = model.BeginPlatformGenerationRouteSubmission(
		job.ID,
		route.ID,
		workerClaim.Token,
		routeClaim.SubmissionToken,
	)
	requireNoError(t, err)

	driftedType := route.AcceptedChannelType + 1
	requireNoError(t, integrationDB.Model(&model.Channel{}).Where("id = ?", route.ChannelID).
		UpdateColumn("type", driftedType).Error)
	_, err = model.ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
	if !errors.Is(err, model.ErrPlatformGenerationProviderRouteUnavailable) {
		t.Fatalf("PostgreSQL admitted a route after native channel type drift: %v", err)
	}
	admission, assignedRoute, err := model.GetPlatformGenerationProviderRouteAssignment(job.ID)
	requireNoError(t, err)
	if admission.State != model.PlatformGenerationRouteAdmissionPosting ||
		assignedRoute.AcceptedChannelType != route.AcceptedChannelType {
		t.Fatalf("existing route binding changed after adapter drift: admission=%+v route=%+v", admission, assignedRoute)
	}
	finished, err := model.FinishPlatformGenerationProviderRoute(job.ID, routeClaim.SubmissionToken)
	requireNoError(t, err)
	if !finished {
		t.Fatal("existing PostgreSQL route binding could not finish after adapter drift")
	}

	requireNoError(t, integrationDB.Model(&model.Channel{}).Where("id = ?", route.ChannelID).
		UpdateColumn("type", route.AcceptedChannelType).Error)
	requireNoError(t, model.RotateChannelCredentialSet(
		route.ChannelID,
		"rotated-after-channel-type-restore",
	))
	_, err = model.ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
	if !errors.Is(err, model.ErrPlatformGenerationProviderRouteUnavailable) {
		t.Fatalf("restored channel type bypassed the key fingerprint fence: %v", err)
	}
}

func TestPostgresConcurrentSubmissionClaimHasExactlyOneOwner(t *testing.T) {
	resetIntegrationState(t)
	job, outbox := createQueuedGeneration(t, "integration.claim", "text_to_video")

	const workers = 32
	type result struct {
		claim *model.PlatformGenerationClaim
		err   error
	}
	results := make(chan result, workers)
	start := make(chan struct{})
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			claim, err := model.ClaimPlatformGenerationSubmission(10*time.Second, outbox.ID)
			results <- result{claim: claim, err: err}
		}()
	}
	close(start)
	wait.Wait()
	close(results)

	var winner *model.PlatformGenerationClaim
	notFound := 0
	for result := range results {
		switch {
		case result.err == nil:
			if winner != nil {
				t.Fatal("more than one worker acquired the PostgreSQL submission lease")
			}
			winner = result.claim
		case errors.Is(result.err, gorm.ErrRecordNotFound):
			notFound++
		default:
			t.Fatalf("unexpected claim error: %v", result.err)
		}
	}
	if winner == nil || notFound != workers-1 {
		t.Fatalf("expected one winner and %d fenced workers, winner=%v fenced=%d", workers-1, winner != nil, notFound)
	}

	var persistedJob model.PlatformGenerationJob
	var persistedOutbox model.PlatformGenerationOutbox
	requireNoError(t, integrationDB.First(&persistedJob, "id = ?", job.ID).Error)
	requireNoError(t, integrationDB.First(&persistedOutbox, outbox.ID).Error)
	if persistedJob.SubmissionLeaseToken != winner.Token || persistedOutbox.ClaimToken != winner.Token || persistedOutbox.Attempts != 1 {
		t.Fatalf("submission owner was not atomically mirrored to job/outbox: job=%q outbox=%q attempts=%d winner=%q",
			persistedJob.SubmissionLeaseToken, persistedOutbox.ClaimToken, persistedOutbox.Attempts, winner.Token)
	}
}

func TestPostgresSharedAccountAdmissionIsBoundedAcrossRoutes(t *testing.T) {
	resetIntegrationState(t)
	base := createProviderRoute(t, "integration.account.video", "text_to_video", 100, 5)
	shared := createRouteForSharedAccount(t, base, "integration.account.image", "image_to_video")

	const workers = 32
	type result struct {
		claim *model.PlatformGenerationRouteClaim
		err   error
	}
	results := make(chan result, workers)
	start := make(chan struct{})
	var wait sync.WaitGroup
	for index := range workers {
		wait.Add(1)
		go func(index int) {
			defer wait.Done()
			<-start
			route := base
			if index%2 == 1 {
				route = shared
			}
			claim, err := model.ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
			results <- result{claim: claim, err: err}
		}(index)
	}
	close(start)
	wait.Wait()
	close(results)

	claims := make([]*model.PlatformGenerationRouteClaim, 0, base.ActiveLimit)
	busy := 0
	for result := range results {
		switch {
		case result.err == nil:
			claims = append(claims, result.claim)
		case errors.Is(result.err, model.ErrPlatformGenerationProviderRouteBusy):
			busy++
		default:
			t.Fatalf("unexpected account admission error: %v", result.err)
		}
	}
	if len(claims) != base.ActiveLimit || busy != workers-base.ActiveLimit {
		t.Fatalf("physical account capacity multiplied across routes: claims=%d busy=%d", len(claims), busy)
	}

	var state model.PlatformGenerationProviderAccountState
	requireNoError(t, integrationDB.First(&state, base.AccountStateID).Error)
	if state.ActiveCount != base.ActiveLimit || state.RPMWindowCount != base.ActiveLimit {
		t.Fatalf("unexpected shared account counters: active=%d rpm=%d", state.ActiveCount, state.RPMWindowCount)
	}
	for _, claim := range claims {
		won, err := model.ReleasePlatformGenerationProviderRoute(claim.JobID, claim.SubmissionToken)
		requireNoError(t, err)
		if !won {
			t.Fatalf("live route claim %s could not release its slot", claim.JobID)
		}
	}
	requireNoError(t, integrationDB.First(&state, base.AccountStateID).Error)
	if state.ActiveCount != 0 || state.RPMWindowCount != base.ActiveLimit {
		t.Fatalf("slot release corrupted account counters: active=%d rpm=%d", state.ActiveCount, state.RPMWindowCount)
	}
}

func TestPostgresSharedAccountRPMLimitIsAtomic(t *testing.T) {
	resetIntegrationState(t)
	route := createProviderRoute(t, "integration.account.rpm", "text_to_video", 7, 64)

	const workers = 32
	type result struct {
		claim *model.PlatformGenerationRouteClaim
		err   error
	}
	results := make(chan result, workers)
	start := make(chan struct{})
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			claim, err := model.ClaimPlatformGenerationProviderRoute(uuid.NewString(), route.Model, route.Mode)
			results <- result{claim: claim, err: err}
		}()
	}
	close(start)
	wait.Wait()
	close(results)

	claims := make([]*model.PlatformGenerationRouteClaim, 0, route.RPMLimit)
	rateLimited := 0
	for result := range results {
		switch {
		case result.err == nil:
			claims = append(claims, result.claim)
		case errors.Is(result.err, model.ErrPlatformGenerationProviderRouteRateLimited):
			rateLimited++
		default:
			t.Fatalf("unexpected RPM admission error: %v", result.err)
		}
	}
	if len(claims) != route.RPMLimit || rateLimited != workers-route.RPMLimit {
		t.Fatalf("fixed-window RPM was not atomic: claims=%d limited=%d", len(claims), rateLimited)
	}

	var state model.PlatformGenerationProviderAccountState
	requireNoError(t, integrationDB.First(&state, route.AccountStateID).Error)
	if state.RPMWindowCount != route.RPMLimit || state.ActiveCount != route.RPMLimit {
		t.Fatalf("unexpected account counters after RPM contention: active=%d rpm=%d", state.ActiveCount, state.RPMWindowCount)
	}
	for _, claim := range claims {
		_, err := model.ReleasePlatformGenerationProviderRoute(claim.JobID, claim.SubmissionToken)
		requireNoError(t, err)
	}
}

func TestPostgresMixedClaimBeginAndFinishUsesOneLockOrder(t *testing.T) {
	resetIntegrationState(t)
	base := createProviderRoute(t, "integration.mixed.video", "text_to_video", 1000, 64)
	shared := createRouteForSharedAccount(t, base, "integration.mixed.image", "image_to_video")

	const workers = 24
	type workItem struct {
		job    model.PlatformGenerationJob
		outbox model.PlatformGenerationOutbox
		route  *model.PlatformGenerationProviderRoute
	}
	items := make([]workItem, 0, workers)
	for index := range workers {
		route := base
		if index%2 == 1 {
			route = shared
		}
		job, outbox := createQueuedGeneration(t, route.Model, route.Mode)
		items = append(items, workItem{job: job, outbox: outbox, route: route})
	}

	start := make(chan struct{})
	errorsCh := make(chan error, workers)
	var wait sync.WaitGroup
	for index := range items {
		item := items[index]
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			workerClaim, err := model.ClaimPlatformGenerationSubmission(30*time.Second, item.outbox.ID)
			if err != nil {
				errorsCh <- err
				return
			}
			routeClaim, err := model.ClaimPlatformGenerationProviderRoute(item.job.ID, item.route.Model, item.route.Mode)
			if err != nil {
				errorsCh <- err
				return
			}
			if _, err := model.BeginPlatformGenerationRouteSubmission(
				item.job.ID,
				routeClaim.Route.ID,
				workerClaim.Token,
				routeClaim.SubmissionToken,
			); err != nil {
				errorsCh <- err
				return
			}
			won, err := model.FinishPlatformGenerationProviderRoute(item.job.ID, routeClaim.SubmissionToken)
			if err != nil {
				errorsCh <- err
				return
			}
			if !won {
				errorsCh <- errors.New("live mixed-path route owner lost its finish fence")
			}
		}()
	}
	close(start)
	wait.Wait()
	close(errorsCh)
	for err := range errorsCh {
		requireNoError(t, err)
	}

	var state model.PlatformGenerationProviderAccountState
	requireNoError(t, integrationDB.First(&state, base.AccountStateID).Error)
	if state.ActiveCount != 0 || state.RPMWindowCount != workers {
		t.Fatalf("mixed claim/begin/finish corrupted shared counters: active=%d rpm=%d", state.ActiveCount, state.RPMWindowCount)
	}
	var finished int64
	requireNoError(t, integrationDB.Model(&model.PlatformGenerationRouteAdmission{}).
		Where("state = ? AND slot_held = ?", model.PlatformGenerationRouteAdmissionFinished, false).
		Count(&finished).Error)
	if finished != workers {
		t.Fatalf("mixed path left unfinished route admissions: got=%d want=%d", finished, workers)
	}
}
