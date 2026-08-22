package model

import (
	"context"
	"errors"
	"fmt"
	"testing"

	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

type relaySchemaSimulatedV2Record struct {
	ID          int64  `gorm:"primaryKey"`
	FutureField string `gorm:"type:text;not null"`
}

func (relaySchemaSimulatedV2Record) TableName() string { return "relay_schema_simulated_v2_records" }

func newRelaySchemaSQLite(t *testing.T) *gorm.DB {
	t.Helper()
	database, err := gorm.Open(
		sqlite.Open("file:relay-schema-"+uuid.NewString()+"?mode=memory&cache=shared"),
		&gorm.Config{Logger: logger.Default.LogMode(logger.Silent)},
	)
	require.NoError(t, err)
	originalDB, originalLogDB := DB, LOG_DB
	DB, LOG_DB = database, database
	t.Cleanup(func() {
		DB, LOG_DB = originalDB, originalLogDB
		sqlDB, sqlErr := database.DB()
		if sqlErr == nil {
			_ = sqlDB.Close()
		}
	})
	return database
}

func TestRelaySchemaMetadataBootstrapRejectsPartialState(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, database.AutoMigrate(&RelaySchemaState{}))
	require.Error(t, ensureRelaySchemaMetadata(database))
	require.False(t, database.Migrator().HasTable(&RelaySchemaMigration{}))
	status, err := GetRelaySchemaStatus(database)
	require.NoError(t, err)
	require.Equal(t, RelaySchemaStatusCorrupt, status.Classification)
}

func TestRelaySchemaStatusValidatesStateLedgerCombinations(t *testing.T) {
	t.Run("clean target mismatch is corrupt", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		require.NoError(t, ensureRelaySchemaMetadata(database))
		require.NoError(t, database.Model(&RelaySchemaState{}).Where("id = ?", relaySchemaStateSingletonID).
			Updates(map[string]any{"target_version": 1, "target_checksum": RelaySchemaV1Checksum()}).Error)
		status, err := GetRelaySchemaStatus(database)
		require.NoError(t, err)
		require.Equal(t, RelaySchemaStatusCorrupt, status.Classification)
	})

	t.Run("dirty target checksum is corrupt", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		require.NoError(t, ensureRelaySchemaMetadata(database))
		require.NoError(t, database.Model(&RelaySchemaState{}).Where("id = ?", relaySchemaStateSingletonID).
			Updates(map[string]any{
				"target_version": 1, "state": RelaySchemaStateFailed, "dirty": true,
				"attempt_id": uuid.NewString(), "target_checksum": "sha256:tampered",
			}).Error)
		status, err := GetRelaySchemaStatus(database)
		require.NoError(t, err)
		require.Equal(t, RelaySchemaStatusCorrupt, status.Classification)
	})

	t.Run("state ledger mismatch is gap before ahead", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		require.NoError(t, ensureRelaySchemaMetadata(database))
		definition := relaySchemaMigrations()[0]
		require.NoError(t, database.Create(&RelaySchemaMigration{
			Version: 1, Name: definition.Name, Phase: definition.Phase,
			Checksum: definition.Checksum, CatalogSHA256: "sha256:placeholder",
		}).Error)
		require.NoError(t, database.Model(&RelaySchemaState{}).Where("id = ?", relaySchemaStateSingletonID).
			Updates(map[string]any{
				"baseline_version": 2, "fresh_bootstrap": false, "current_version": 2, "target_version": 2,
			}).Error)
		status, err := GetRelaySchemaStatus(database)
		require.NoError(t, err)
		require.Equal(t, RelaySchemaStatusLedgerGap, status.Classification)
	})

	t.Run("consistent future ledger is ahead", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		require.NoError(t, ensureRelaySchemaMetadata(database))
		require.NoError(t, database.Create(&RelaySchemaMigration{
			Version: 3, Name: "future", Phase: "expand", Checksum: "sha256:future", CatalogSHA256: "sha256:v3",
		}).Error)
		require.NoError(t, database.Model(&RelaySchemaState{}).Where("id = ?", relaySchemaStateSingletonID).
			Updates(map[string]any{
				"baseline_version": 3, "fresh_bootstrap": false, "current_version": 3,
				"target_version": 3, "current_checksum": "sha256:future",
				"target_checksum": "sha256:future", "current_catalog_sha256": "sha256:v3",
				"target_catalog_sha256": "sha256:v3",
			}).Error)
		status, err := GetRelaySchemaStatus(database)
		require.NoError(t, err)
		require.Equal(t, RelaySchemaStatusAhead, status.Classification)
	})
}

func TestRelaySchemaDirtyResumeRequiresExactAttemptAndArtifact(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, ensureRelaySchemaMetadata(database))
	attemptID := uuid.NewString()
	definition := relaySchemaMigrations()[1]
	require.NoError(t, database.Model(&RelaySchemaState{}).Where("id = ?", relaySchemaStateSingletonID).
		Updates(map[string]any{
			"target_version": definition.Version, "state": RelaySchemaStateFailed, "dirty": true,
			"attempt_id": attemptID, "target_checksum": definition.Checksum, "error_code": "TEST_FAILURE",
		}).Error)

	status, err := GetRelaySchemaStatus(database)
	require.NoError(t, err)
	require.Equal(t, RelaySchemaStatusDirty, status.Classification)
	_, err = RunRelaySchemaMigrations(context.Background(), uuid.NewString())
	require.ErrorContains(t, err, "exact resume attempt id")
	result, err := RunRelaySchemaMigrations(context.Background(), attemptID)
	require.NoError(t, err)
	require.Equal(t, "migrated", result.State)
	require.True(t, result.Status.Current)
}

func TestRelaySchemaMigrationIsIdempotentAndDetectsCatalogDrift(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	result, err := RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.Equal(t, "migrated", result.State)
	require.Equal(t, RelaySchemaStatusCurrent, result.Status.Classification)

	result, err = RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.Equal(t, "current", result.State)

	require.NoError(t, database.Exec("DROP TRIGGER trg_relay_schema_migrations_no_update").Error)
	status, err := GetRelaySchemaStatus(database)
	require.NoError(t, err)
	require.Equal(t, RelaySchemaStatusCorrupt, status.Classification)
}

func TestRelaySchemaCommitAckLossCannotRewriteCleanCommit(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	result, err := RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	definition := relaySchemaMigrations()[1]
	require.Error(t, markRelaySchemaFailed(database, definition, result.AttemptID, 0, "SIMULATED_ACK_LOSS"))
	committed, err := reconcileRelaySchemaCommitOutcome(database, definition, result.AttemptID, 0)
	require.NoError(t, err)
	require.True(t, committed)
	status, err := GetRelaySchemaStatus(database)
	require.NoError(t, err)
	require.Equal(t, RelaySchemaStatusCurrent, status.Classification)
}

func TestRelaySchemaFreshAndLegacyDatabasesRecordBaseline(t *testing.T) {
	t.Run("fresh snapshot", func(t *testing.T) {
		newRelaySchemaSQLite(t)
		result, err := RunRelaySchemaMigrations(context.Background(), "")
		require.NoError(t, err)
		require.True(t, result.Status.Current)
		require.Equal(t, int64(2), result.Status.BaselineVersion)
		require.Equal(t, int64(2), result.Status.CurrentVersion)
		require.Equal(t, int64(2), result.Status.TargetVersion)
		require.Equal(t, int64(1), result.Status.MinVersion)
		require.Equal(t, int64(2), result.Status.MaxVersion)
		require.False(t, result.Status.FreshBootstrap)
		var ledger []RelaySchemaMigration
		require.NoError(t, DB.Order("version ASC").Find(&ledger).Error)
		require.Len(t, ledger, 1)
		require.Equal(t, int64(2), ledger[0].Version)
	})

	t.Run("unversioned legacy v1", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		require.NoError(t, database.AutoMigrate(&Option{}))
		require.NoError(t, database.Create(&Option{Key: "legacy-preserved", Value: "value"}).Error)
		require.NoError(t, ensureRelaySchemaMetadata(database))
		var initial RelaySchemaState
		require.NoError(t, database.First(&initial, relaySchemaStateSingletonID).Error)
		require.False(t, initial.FreshBootstrap)

		_, err := RunRelaySchemaMigrations(context.Background(), "")
		require.ErrorContains(t, err, "pinned v1 migrator image")
		var preserved Option
		require.NoError(t, database.Where("key = ?", "legacy-preserved").First(&preserved).Error)
	})
}

func TestRelaySchemaV2PlanningNeverReplaysLiveV1(t *testing.T) {
	v1Checksum := RelaySchemaV1Checksum()
	require.False(t, relaySchemaV1LiveArtifactValidationRequired(2))
	noop := func(*gorm.DB) error { return nil }
	definitions := []relaySchemaMigrationDefinition{
		{Version: 1, Name: "v1", Phase: "baseline", Checksum: v1Checksum, Up: noop, Bootstrap: noop},
		{Version: 2, Name: "v2", Phase: "expand", Checksum: "sha256:v2", Up: noop, Bootstrap: noop},
	}

	freshPlan, err := buildRelaySchemaExecutionPlan(RelaySchemaStatus{
		Classification: RelaySchemaStatusTooOld, CurrentVersion: 0, FreshBootstrap: true,
	}, definitions, 2)
	require.NoError(t, err)
	require.NotNil(t, freshPlan.Bootstrap)
	require.Equal(t, int64(2), freshPlan.Bootstrap.Version)
	require.Empty(t, freshPlan.Incremental)
	require.Equal(t, v1Checksum, RelaySchemaV1Checksum())

	upgradePlan, err := buildRelaySchemaExecutionPlan(RelaySchemaStatus{
		Classification: RelaySchemaStatusCompatible, BaselineVersion: 1, CurrentVersion: 1,
	}, definitions, 2)
	require.NoError(t, err)
	require.Nil(t, upgradePlan.Bootstrap)
	require.Len(t, upgradePlan.Incremental, 1)
	require.Equal(t, int64(2), upgradePlan.Incremental[0].Version)

	_, err = buildRelaySchemaExecutionPlan(RelaySchemaStatus{
		Classification: RelaySchemaStatusTooOld, CurrentVersion: 0, FreshBootstrap: false,
	}, definitions, 2)
	require.ErrorContains(t, err, "pinned v1")
	_, err = buildRelaySchemaExecutionPlan(RelaySchemaStatus{
		Classification: RelaySchemaStatusDirty, CurrentVersion: 0, PendingVersion: 1, FreshBootstrap: true,
	}, definitions, 2)
	require.ErrorContains(t, err, "pinned migrator image")

	t.Run("fresh v2 snapshot materializes v2 without running v1", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		require.NoError(t, ensureRelaySchemaMetadata(database))
		v1Ran := false
		freshDefinitions := []relaySchemaMigrationDefinition{
			{
				Version: 1, Name: "v1", Phase: "baseline", Checksum: v1Checksum,
				Up:        func(*gorm.DB) error { v1Ran = true; return errors.New("historical v1 must be unreachable") },
				Bootstrap: func(*gorm.DB) error { v1Ran = true; return errors.New("historical v1 must be unreachable") },
			},
			{
				Version: 2, Name: "v2", Phase: "expand", Checksum: "sha256:v2", Up: noop,
				Bootstrap: func(db *gorm.DB) error {
					return db.AutoMigrate(&Option{}, &relaySchemaSimulatedV2Record{})
				},
			},
		}
		attemptID := uuid.NewString()
		require.NoError(t, markRelaySchemaApplying(database, freshDefinitions[1], attemptID))
		require.NoError(t, runRelaySchemaBootstrapTransaction(database, freshDefinitions, freshDefinitions[1], attemptID))
		require.False(t, v1Ran)
		require.True(t, database.Migrator().HasColumn(&relaySchemaSimulatedV2Record{}, "future_field"))
		var state RelaySchemaState
		require.NoError(t, database.First(&state, relaySchemaStateSingletonID).Error)
		require.Equal(t, int64(2), state.BaselineVersion)
		require.Equal(t, int64(2), state.CurrentVersion)
		var historicalCount int64
		require.NoError(t, database.Model(&RelaySchemaMigration{}).Where("version = ?", 1).Count(&historicalCount).Error)
		require.Zero(t, historicalCount)
		var current RelaySchemaMigration
		require.NoError(t, database.First(&current, 2).Error)
		require.Equal(t, "sha256:v2", current.Checksum)
	})

	t.Run("v1 database takes only v2 incremental", func(t *testing.T) {
		database := newRelaySchemaSQLite(t)
		require.NoError(t, ensureRelaySchemaMetadata(database))
		v1 := relaySchemaMigrationDefinition{
			Version: 1, Name: "v1", Phase: "baseline", Checksum: v1Checksum,
			Up:        func(db *gorm.DB) error { return db.AutoMigrate(&Option{}) },
			Bootstrap: func(db *gorm.DB) error { return db.AutoMigrate(&Option{}) },
		}
		v1Attempt := uuid.NewString()
		require.NoError(t, markRelaySchemaApplying(database, v1, v1Attempt))
		require.NoError(t, runRelaySchemaBootstrapTransaction(database, []relaySchemaMigrationDefinition{v1}, v1, v1Attempt))

		v2 := relaySchemaMigrationDefinition{
			Version: 2, Name: "v2", Phase: "expand", Checksum: "sha256:v2",
			Up:        func(db *gorm.DB) error { return db.AutoMigrate(&relaySchemaSimulatedV2Record{}) },
			Bootstrap: func(db *gorm.DB) error { return db.AutoMigrate(&Option{}, &relaySchemaSimulatedV2Record{}) },
		}
		v2Attempt := uuid.NewString()
		require.NoError(t, markRelaySchemaApplying(database, v2, v2Attempt))
		require.NoError(t, runRelaySchemaDefinitionTransaction(database, v2, v2Attempt))
		require.True(t, database.Migrator().HasColumn(&relaySchemaSimulatedV2Record{}, "future_field"))
		var state RelaySchemaState
		require.NoError(t, database.First(&state, relaySchemaStateSingletonID).Error)
		require.Equal(t, int64(1), state.BaselineVersion)
		require.Equal(t, int64(2), state.CurrentVersion)
	})
}

func TestRelaySchemaV2TopLevelMigrationNeverExecutesLiveV1(t *testing.T) {
	runWithSentinel := func(t *testing.T, seedV1 bool) {
		t.Helper()
		database := newRelaySchemaSQLite(t)
		definitions := relaySchemaMigrations()
		if seedV1 {
			require.NoError(t, ensureRelaySchemaMetadata(database))
			v1Attempt := uuid.NewString()
			require.NoError(t, markRelaySchemaApplying(database, definitions[0], v1Attempt))
			require.NoError(t, runRelaySchemaBootstrapTransaction(
				database,
				[]relaySchemaMigrationDefinition{definitions[0]},
				definitions[0],
				v1Attempt,
			))
			compatible, err := RequireRelaySchemaCompatible(database)
			require.NoError(t, err)
			require.Equal(t, RelaySchemaStatusCompatible, compatible.Classification)
			require.False(t, compatible.Current)
		}

		liveV1Calls := 0
		liveV1Sentinel := func(*gorm.DB) error {
			liveV1Calls++
			return errors.New("live v1 sentinel executed")
		}
		definitions[0].Up = liveV1Sentinel
		definitions[0].Bootstrap = liveV1Sentinel
		originalDefinitions := relaySchemaDefinitionsForRuntime
		relaySchemaDefinitionsForRuntime = func() []relaySchemaMigrationDefinition {
			return definitions
		}
		t.Cleanup(func() { relaySchemaDefinitionsForRuntime = originalDefinitions })

		result, err := RunRelaySchemaMigrations(context.Background(), "")
		require.NoError(t, err)
		require.Zero(t, liveV1Calls, "top-level v2 orchestration must never execute the live v1 definition")
		require.True(t, result.Status.Current)
		require.Equal(t, int64(2), result.Status.CurrentVersion)
		var ledger []RelaySchemaMigration
		require.NoError(t, database.Order("version ASC").Find(&ledger).Error)
		if seedV1 {
			require.Len(t, ledger, 2)
			require.Equal(t, int64(1), ledger[0].Version)
			require.Equal(t, int64(2), ledger[1].Version)
			return
		}
		require.Len(t, ledger, 1)
		require.Equal(t, int64(2), ledger[0].Version)
	}

	t.Run("fresh v2 bootstrap", func(t *testing.T) { runWithSentinel(t, false) })
	t.Run("exact v1 to v2 bridge", func(t *testing.T) { runWithSentinel(t, true) })
}

func TestRelaySchemaCommitReconciliationAcceptsAttestedTooOldIntermediate(t *testing.T) {
	definition := relaySchemaMigrationDefinition{Version: 1, Checksum: "sha256:v1"}
	require.True(t, relaySchemaDefinitionCommitted(RelaySchemaStatus{
		Classification:  RelaySchemaStatusTooOld,
		State:           RelaySchemaStateClean,
		CurrentVersion:  1,
		CurrentChecksum: "sha256:v1",
	}, definition))
}

func TestRelaySchemaRegistryRejectsMissingChecksumOrPostgresCatalogBeforeDDL(t *testing.T) {
	v1 := relaySchemaMigrations()[0]
	v2 := relaySchemaMigrations()[1]
	definitions := []relaySchemaMigrationDefinition{v1, v2}
	contract := RelaySchemaContract{TargetVersion: 2, MinVersion: 1, MaxVersion: 2, Checksums: map[int64]string{1: v1.Checksum}}
	validManifest := func(int64) (map[string]relayTablePrivilegeSet, error) {
		return map[string]relayTablePrivilegeSet{"probe": {Select: true}}, nil
	}
	validAlgorithm := func(string, int64) bool { return true }
	require.ErrorContains(t, validateRelaySchemaRegistry(contract, definitions, "postgres", func(string, int64) string {
		return "sha256:catalog"
	}, validAlgorithm, validManifest), "contract")

	contract.Checksums[2] = v2.Checksum
	require.ErrorContains(t, validateRelaySchemaRegistry(contract, definitions, "postgres", func(_ string, version int64) string {
		if version == 1 {
			return relaySchemaV1PostgresCatalogSHA256
		}
		return ""
	}, validAlgorithm, validManifest), "catalog registry")
	require.NoError(t, validateRelaySchemaRegistry(contract, definitions, "postgres", func(_ string, version int64) string {
		return "sha256:catalog-v" + fmt.Sprint(version)
	}, validAlgorithm, validManifest))
	missingV2Manifest := func(version int64) (map[string]relayTablePrivilegeSet, error) {
		if version == 2 {
			return nil, errors.New("missing v2 manifest")
		}
		return validManifest(version)
	}
	require.ErrorContains(t, validateRelaySchemaRegistry(contract, definitions, "postgres", func(_ string, version int64) string {
		return "sha256:catalog-v" + fmt.Sprint(version)
	}, validAlgorithm, missingV2Manifest), "privilege manifest")
	missingV2Algorithm := func(_ string, version int64) bool { return version != 2 }
	require.ErrorContains(t, validateRelaySchemaRegistry(contract, definitions, "postgres", func(_ string, version int64) string {
		return "sha256:catalog-v" + fmt.Sprint(version)
	}, missingV2Algorithm, validManifest), "catalog registry")
	require.NoError(t, validateRelaySchemaRegistry(
		GetRelaySchemaContract(), relaySchemaMigrations(), "postgres",
		expectedRelaySchemaCatalogFingerprint, relaySchemaCatalogAlgorithmAvailable,
		relaySchemaPrivilegeManifestRegistryForVersion,
	))
}

func TestRelayDatabaseRoleAttestationRequiredForSecureStaging(t *testing.T) {
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv(relayDatabaseRoleAttestationEnvironment, "true")
	require.True(t, RelayDatabaseRoleAttestationRequired())

	t.Setenv(relayDatabaseRoleAttestationEnvironment, "false")
	require.False(t, RelayDatabaseRoleAttestationRequired())

	// A typo may make a staging deployment unavailable, but must never make it
	// silently run without database-role attestation.
	t.Setenv(relayDatabaseRoleAttestationEnvironment, "definitely")
	require.True(t, RelayDatabaseRoleAttestationRequired())
}

func TestRelaySchemaStrictRetiredOptionMigrationRollsBackMalformedData(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, database.AutoMigrate(&Option{}))
	require.NoError(t, database.Create(&Option{Key: "ApiInfo", Value: "not-json"}).Error)
	require.Error(t, database.Transaction(func(tx *gorm.DB) error {
		return migrateRetiredFrontendOptionsStrictWithDB(tx)
	}))

	var source Option
	require.NoError(t, database.Where("key = ?", "ApiInfo").First(&source).Error)
	var targetCount int64
	require.NoError(t, database.Model(&Option{}).Where("key = ?", "console_setting.api_info").Count(&targetCount).Error)
	require.Zero(t, targetCount)
	var themeCount int64
	require.NoError(t, database.Model(&Option{}).Where("key = ?", retiredThemeOptionKey).Count(&themeCount).Error)
	require.Zero(t, themeCount)
}
