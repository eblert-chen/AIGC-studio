package model

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestRelaySchemaSQLiteV6ToV7WidensOnlyProtectedArtifactContentTypes(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, MigratePlatformChannelControlStorageV6WithDB(database))

	image := relaySchemaV6PendingRouteTest("v7-image", 71)
	require.NoError(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&image).Error)
	relaySchemaV7RequireSubmitted(t, database, image.ID, "provider-task-v7-image")
	require.Error(t, relaySchemaV7CompleteArtifact(database, image.ID, "image/png"),
		"the frozen v6 SQLite guard must still reject PNG evidence")

	require.NoError(t, MigratePlatformChannelControlStorageV7WithDB(database))
	require.NoError(t, relaySchemaV7CompleteArtifact(database, image.ID, "image/png"))

	video := relaySchemaV6PendingRouteTest("v7-video", 71)
	require.NoError(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&video).Error)
	relaySchemaV7RequireSubmitted(t, database, video.ID, "provider-task-v7-video")
	require.NoError(t, relaySchemaV7CompleteArtifact(database, video.ID, "video/mp4"))

	unsupported := relaySchemaV6PendingRouteTest("v7-unsupported", 71)
	require.NoError(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&unsupported).Error)
	relaySchemaV7RequireSubmitted(t, database, unsupported.ID, "provider-task-v7-unsupported")
	require.Error(t, relaySchemaV7CompleteArtifact(database, unsupported.ID, "image/jpeg"))

	illegalTransition := relaySchemaV6PendingRouteTest("v7-illegal-transition", 71)
	require.NoError(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&illegalTransition).Error)
	require.Error(t, relaySchemaV7CompleteArtifact(database, illegalTransition.ID, "image/png"),
		"v7 must not widen the submitted-to-terminal state machine")
	require.Error(t, database.Exec(
		`UPDATE platform_channel_control_operations SET actor = 'tampered' WHERE id = ?`, image.ID,
	).Error)
	require.Error(t, database.Exec(
		`DELETE FROM platform_channel_control_operations WHERE id = ?`, image.ID,
	).Error)

	for _, trigger := range []string{
		"trg_platform_channel_control_v7_insert",
		"trg_platform_channel_control_v7_update",
		"trg_platform_channel_control_v7_delete",
	} {
		var count int64
		require.NoError(t, database.Raw(
			`SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name = ?`, trigger,
		).Scan(&count).Error)
		require.Equal(t, int64(1), count, "missing v7 trigger %s", trigger)
	}
	var v6TriggerCount int64
	require.NoError(t, database.Raw(
		`SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_platform_channel_control_v6_%'`,
	).Scan(&v6TriggerCount).Error)
	require.Zero(t, v6TriggerCount, "v7 must replace, not stack, the v6 SQLite guards")
}

func TestRelaySchemaSQLiteFreshV7AndV6UpgradeUseTheSameGuard(t *testing.T) {
	definitions := relaySchemaMigrations()
	require.GreaterOrEqual(t, len(definitions), int(relaySchemaV7FrozenVersion))
	v7Definitions := append([]relaySchemaMigrationDefinition(nil), definitions[:relaySchemaV7FrozenVersion]...)
	originalDefinitions := relaySchemaDefinitionsForRuntime
	originalContract := relaySchemaContractForRuntime
	relaySchemaDefinitionsForRuntime = func() []relaySchemaMigrationDefinition {
		return v7Definitions
	}
	relaySchemaContractForRuntime = func() RelaySchemaContract {
		contract := GetRelaySchemaContract()
		contract.TargetVersion = relaySchemaV7FrozenVersion
		contract.MaxVersion = relaySchemaV7FrozenVersion
		delete(contract.Checksums, relaySchemaV8FrozenVersion)
		return contract
	}
	t.Cleanup(func() {
		relaySchemaDefinitionsForRuntime = originalDefinitions
		relaySchemaContractForRuntime = originalContract
	})

	t.Run("fresh v7", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		result, err := RunRelaySchemaMigrations(context.Background(), "")
		require.NoError(t, err)
		require.Equal(t, relaySchemaV7FrozenVersion, result.Status.BaselineVersion)
		require.Equal(t, relaySchemaV7FrozenVersion, result.Status.CurrentVersion)
		var ledger []RelaySchemaMigration
		require.NoError(t, database.Order("version ASC").Find(&ledger).Error)
		require.Len(t, ledger, 1)
		require.Equal(t, relaySchemaV7FrozenVersion, ledger[0].Version)
		relaySchemaV7RequirePNGCompletion(t, database, "fresh", 72)
		relaySchemaV7RequireUnchangedLifecycleGuards(t, database, "fresh", 72)
		relaySchemaV7RequirePersonalCostScope(t, database)
	})

	t.Run("exact v6 upgrade", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		require.NoError(t, ensureRelaySchemaMetadata(database))
		v6Attempt := uuid.NewString()
		require.NoError(t, markRelaySchemaApplying(database, v7Definitions[5], v6Attempt))
		require.NoError(t, runRelaySchemaBootstrapTransaction(
			database, v7Definitions[:6], v7Definitions[5], v6Attempt,
		))
		require.False(t, database.Migrator().HasColumn(&PlatformChannelCostEvent{}, "personal_workspace_id"),
			"the frozen v6 catalog must not absorb the v7 personal cost scope")
		var v6Ledger RelaySchemaMigration
		require.NoError(t, database.First(&v6Ledger, relaySchemaV6FrozenVersion).Error)

		result, err := RunRelaySchemaMigrations(context.Background(), "")
		require.NoError(t, err)
		require.Equal(t, relaySchemaV6FrozenVersion, result.FromVersion)
		require.Equal(t, relaySchemaV7FrozenVersion, result.ToVersion)
		var ledger []RelaySchemaMigration
		require.NoError(t, database.Order("version ASC").Find(&ledger).Error)
		require.Len(t, ledger, 2)
		require.Equal(t, v6Ledger, ledger[0], "v7 must not rewrite the frozen v6 ledger row")
		require.Equal(t, relaySchemaV7FrozenVersion, ledger[1].Version)
		relaySchemaV7RequirePNGCompletion(t, database, "upgrade", 73)
		relaySchemaV7RequireUnchangedLifecycleGuards(t, database, "upgrade", 73)
		relaySchemaV7RequirePersonalCostScope(t, database)
	})
}

func TestPlatformChannelCostPersonalScopeV7SQLiteGuardsInsertAndUpdate(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, database.AutoMigrate(&PlatformChannelCostEvent{}))
	require.NoError(t, MigratePlatformChannelCostPersonalScopeV7WithDB(database))

	taskID := uuid.NewString()
	personalWorkspaceID := uuid.NewString()
	relayJobID := uuid.NewString()
	validID := uuid.NewString()
	require.NoError(t, relaySchemaV7InsertChannelCostScopeProbe(
		database, validID, "", personalWorkspaceID, taskID, relayJobID,
	))
	require.Error(t, database.Exec(
		`UPDATE platform_channel_cost_events SET company_id = ? WHERE id = ?`, uuid.NewString(), validID,
	).Error, "the v7 SQLite update trigger must reject a dual billing scope")

	require.NoError(t, database.Exec(
		`UPDATE platform_channel_cost_events SET note = 'still-valid' WHERE id = ?`, validID,
	).Error, "the v7 SQLite update trigger must allow a scope-preserving update when tested without append-only guards")
}

func TestPlatformChannelCostPersonalScopeV7SQLiteRejectsInvalidLegacyRows(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, database.AutoMigrate(&PlatformChannelCostEvent{}))
	id := uuid.NewString()
	now := time.Now().UTC()
	require.NoError(t, database.Exec(`INSERT INTO platform_channel_cost_events (
id, amount_cents, idempotency_key, channel_key, channel_type, occurred_at,
external_reference, company_id, task_id, relay_job_id, note, evidence_source,
evidence_reference, source_document_sha256, payload_json, payload_sha256, created_at
) VALUES (?, 1, ?, 'scope-v7-legacy', 'official', ?, ?, '', ?, '', '',
'provider_reported', '', '', '{}', ?, ?)`,
		id,
		"scope-v7-legacy-"+id,
		now,
		"provider-scope-v7-legacy-"+id,
		uuid.NewString(),
		strings.Repeat("7", 64),
		now,
	).Error)

	err := database.Transaction(func(tx *gorm.DB) error {
		return MigratePlatformChannelCostPersonalScopeV7WithDB(tx)
	})
	require.Error(t, err, "SQLite v7 must validate the same existing rows as PostgreSQL CHECK installation")
	require.False(t, database.Migrator().HasColumn(&PlatformChannelCostEvent{}, "personal_workspace_id"),
		"a rejected SQLite v7 migration must roll back its partial column")
}

func relaySchemaV7RequirePersonalCostScope(t *testing.T, database *gorm.DB) {
	t.Helper()
	require.True(t, database.Migrator().HasColumn(&PlatformChannelCostEvent{}, "personal_workspace_id"),
		"v7 must persist personal workspace provider-cost linkage")
	require.True(t, database.Migrator().HasIndex(&PlatformChannelCostEvent{}, platformChannelCostPersonalScopeIndexV7),
		"v7 must index personal workspace provider-cost linkage")
	require.NoError(t, MigratePlatformChannelCostPersonalScopeV7WithDB(database),
		"the v7 personal cost migration must be idempotent")
	if database.Dialector.Name() != "sqlite" {
		return
	}
	for _, trigger := range []string{
		platformChannelCostBillingScopeInsertTriggerV7,
		platformChannelCostBillingScopeUpdateTriggerV7,
	} {
		var count int64
		require.NoError(t, database.Raw(
			`SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name = ?`, trigger,
		).Scan(&count).Error)
		require.Equal(t, int64(1), count, "missing v7 channel-cost scope trigger %s", trigger)
	}

	for name, linkage := range map[string]struct {
		companyID           string
		personalWorkspaceID string
		taskID              string
		relayJobID          string
	}{
		"dual scope": {
			companyID: uuid.NewString(), personalWorkspaceID: uuid.NewString(),
			taskID: uuid.NewString(), relayJobID: uuid.NewString(),
		},
		"task without scope": {
			taskID: uuid.NewString(), relayJobID: uuid.NewString(),
		},
		"task without Relay job": {
			companyID: uuid.NewString(), taskID: uuid.NewString(),
		},
	} {
		t.Run(name, func(t *testing.T) {
			relaySchemaV5RequireRejectedStatement(t, database, func(probe *gorm.DB) error {
				return relaySchemaV7InsertChannelCostScopeProbe(
					probe,
					uuid.NewString(),
					linkage.companyID,
					linkage.personalWorkspaceID,
					linkage.taskID,
					linkage.relayJobID,
				)
			})
		})
	}
}

func relaySchemaV7InsertChannelCostScopeProbe(
	database *gorm.DB,
	id string,
	companyID string,
	personalWorkspaceID string,
	taskID string,
	relayJobID string,
) error {
	now := time.Now().UTC()
	return database.Exec(`INSERT INTO platform_channel_cost_events (
id, amount_cents, idempotency_key, channel_key, channel_type, occurred_at,
external_reference, company_id, personal_workspace_id, task_id, relay_job_id,
note, evidence_source, evidence_reference, source_document_sha256,
payload_json, payload_sha256, created_at
) VALUES (?, 1, ?, 'scope-v7-sqlite', 'official', ?, ?, ?, ?, ?, ?, '',
'provider_reported', '', '', '{}', ?, ?)`,
		id,
		"scope-v7-sqlite-"+id,
		now,
		"provider-scope-v7-sqlite-"+id,
		companyID,
		personalWorkspaceID,
		taskID,
		relayJobID,
		strings.Repeat("7", 64),
		now,
	).Error
}

func relaySchemaV7RequireUnchangedLifecycleGuards(t *testing.T, database *gorm.DB, suffix string, channelID int) {
	t.Helper()

	invalidRouteProjection := relaySchemaV6PendingRouteTest("v7-invalid-route-"+suffix, channelID)
	invalidRouteProjection.IntentRouteID = ""
	relaySchemaV5RequireRejectedStatement(t, database, func(probe *gorm.DB) error {
		return probe.Session(&gorm.Session{SkipHooks: true}).Create(&invalidRouteProjection).Error
	})

	invalidTransport := relaySchemaV6PendingRouteTest("v7-invalid-transport-"+suffix, channelID)
	invalidTransport.IntentTransportRevision = "sha256:invalid"
	relaySchemaV5RequireRejectedStatement(t, database, func(probe *gorm.DB) error {
		return probe.Session(&gorm.Session{SkipHooks: true}).Create(&invalidTransport).Error
	})

	operation := relaySchemaV6PendingRouteTest("v7-unchanged-guards-"+suffix, channelID)
	require.NoError(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&operation).Error)
	relaySchemaV5RequireRejectedStatement(t, database, func(probe *gorm.DB) error {
		return probe.Exec(
			`UPDATE platform_channel_control_operations SET provider_submission_state = 'invalid' WHERE id = ?`,
			operation.ID,
		).Error
	})
	relaySchemaV5RequireRejectedStatement(t, database, func(probe *gorm.DB) error {
		return probe.Exec(
			`UPDATE platform_channel_control_operations SET provider_blocker_code = 'PROVIDER_RAW_ERROR' WHERE id = ?`,
			operation.ID,
		).Error
	})
	relaySchemaV5RequireRejectedStatement(t, database, func(probe *gorm.DB) error {
		return probe.Exec(`UPDATE platform_channel_control_operations
SET reconciliation_actor = 'operator', reconciliation_reason = 'invalid', reconciled_at = ? WHERE id = ?`,
			time.Now().UTC(), operation.ID,
		).Error
	})
	var transactionProof int
	require.NoError(t, database.Raw(`SELECT 1`).Scan(&transactionProof).Error)
	require.Equal(t, 1, transactionProof, "guard probes must leave the surrounding transaction usable")
}

func relaySchemaV7RequireSubmitted(t *testing.T, database *gorm.DB, operationID string, providerTaskID string) {
	t.Helper()
	require.NoError(t, database.Exec(
		`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submission_unknown' WHERE id = ?`, operationID,
	).Error)
	require.NoError(t, database.Exec(
		`UPDATE platform_channel_control_operations
SET provider_submission_state = 'submitted', provider_task_id = ? WHERE id = ?`,
		providerTaskID, operationID,
	).Error)
}

func relaySchemaV7CompleteArtifact(database *gorm.DB, operationID string, contentType string) error {
	return database.Exec(
		`UPDATE platform_channel_control_operations
SET state = 'succeeded', provider_submission_state = 'artifact_verified',
    result_success = true, result_response_ms = 31, result_error_code = '',
    provider_artifact_sha256 = ?, provider_artifact_size_bytes = 1024,
    provider_artifact_content_type = ?, completed_at = ?
WHERE id = ?`,
		strings.Repeat("7", 64), contentType, time.Now().UTC(), operationID,
	).Error
}

func relaySchemaV7RequirePNGCompletion(t *testing.T, database *gorm.DB, suffix string, channelID int) {
	t.Helper()
	operation := relaySchemaV6PendingRouteTest("v7-"+suffix, channelID)
	require.NoError(t, database.Session(&gorm.Session{SkipHooks: true}).Create(&operation).Error)
	relaySchemaV7RequireSubmitted(t, database, operation.ID, "provider-task-v7-"+suffix)
	require.NoError(t, relaySchemaV7CompleteArtifact(database, operation.ID, "image/png"))
}
