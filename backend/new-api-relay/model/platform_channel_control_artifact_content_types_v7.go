package model

import (
	"errors"
	"fmt"
	"strings"

	"gorm.io/gorm"
)

// MigratePlatformChannelControlStorageV7WithDB materializes the complete
// channel-control projection for a fresh v7 database without executing the
// frozen v6 migration. The only v7 catalog delta is the protected artifact
// evidence MIME allowlist installed after the unchanged lifecycle columns and
// indexes exist.
func MigratePlatformChannelControlStorageV7WithDB(db *gorm.DB) error {
	if db == nil {
		return errors.New("Relay schema v7 channel-test artifact evidence database is unavailable")
	}
	if !db.Migrator().HasTable(&PlatformChannelControlOperation{}) {
		if err := migratePlatformChannelControlBaseStorageV6WithDB(db); err != nil {
			return err
		}
	}
	for _, field := range []string{
		"IntentPublicModelID",
		"IntentRouteID",
		"IntentTransportRevision",
		"IntentTransportSHA256",
		"ProviderSubmissionState",
		"ProviderBlockerCode",
		"ProviderTaskID",
		"ProviderArtifactSHA256",
		"ProviderArtifactSizeBytes",
		"ProviderArtifactContentType",
		"ReconciliationActor",
		"ReconciliationReason",
		"ReconciledAt",
	} {
		if db.Migrator().HasColumn(&PlatformChannelControlOperation{}, field) {
			continue
		}
		if err := db.Migrator().AddColumn(&PlatformChannelControlOperation{}, field); err != nil {
			return fmt.Errorf("Relay schema v7 channel-test lifecycle column %s could not be added", field)
		}
	}
	if err := installPlatformChannelControlLifecycleIndexesV7WithDB(db); err != nil {
		return err
	}
	switch db.Dialector.Name() {
	case "postgres":
		return installPostgresPlatformChannelControlArtifactEvidenceV7WithDB(db)
	case "sqlite":
		return installSQLitePlatformChannelControlArtifactEvidenceV7WithDB(db)
	default:
		return nil
	}
}

func installPlatformChannelControlLifecycleIndexesV7WithDB(db *gorm.DB) error {
	routeIndexMatches, err := platformChannelControlRouteIndexMatchesV7(db)
	if err != nil {
		return errors.New("Relay schema v7 channel-test lifecycle route index could not be inspected")
	}
	if !routeIndexMatches {
		for _, statement := range []string{
			`DROP INDEX IF EXISTS idx_platform_channel_control_route`,
			`CREATE INDEX idx_platform_channel_control_route
ON platform_channel_control_operations (channel_id, intent_public_model_id, intent_route_id, state)`,
		} {
			if err := db.Exec(statement).Error; err != nil {
				return errors.New("Relay schema v7 channel-test lifecycle route index could not be installed")
			}
		}
	}
	for _, statement := range []string{
		`CREATE INDEX IF NOT EXISTS idx_platform_channel_control_submission
ON platform_channel_control_operations (provider_submission_state, state)`,
		`CREATE UNIQUE INDEX IF NOT EXISTS ux_platform_channel_control_unresolved_route
ON platform_channel_control_operations (channel_id, intent_public_model_id, intent_route_id)
WHERE kind = 'test' AND state = 'pending' AND intent_route_id <> ''`,
		`CREATE UNIQUE INDEX IF NOT EXISTS ux_platform_channel_control_provider_task
ON platform_channel_control_operations (channel_id, intent_route_id, provider_task_id)
WHERE kind = 'test' AND intent_route_id <> '' AND provider_task_id <> ''`,
	} {
		if err := db.Exec(statement).Error; err != nil {
			return errors.New("Relay schema v7 channel-test lifecycle index could not be installed")
		}
	}
	return nil
}

func platformChannelControlRouteIndexMatchesV7(db *gorm.DB) (bool, error) {
	expectedColumns := []string{"channel_id", "intent_public_model_id", "intent_route_id", "state"}
	switch db.Dialector.Name() {
	case "postgres":
		var index struct {
			Unique    bool   `gorm:"column:is_unique"`
			Predicate string `gorm:"column:predicate"`
			Columns   string `gorm:"column:columns"`
		}
		result := db.Raw(`
SELECT definition.indisunique AS is_unique,
       COALESCE(pg_get_expr(definition.indpred, definition.indrelid), '') AS predicate,
       COALESCE(string_agg(attribute.attname, ',' ORDER BY key.ordinality), '') AS columns
  FROM pg_class index_relation
  JOIN pg_namespace namespace ON namespace.oid = index_relation.relnamespace
  JOIN pg_index definition ON definition.indexrelid = index_relation.oid
  JOIN LATERAL unnest(definition.indkey) WITH ORDINALITY key(attnum, ordinality) ON true
  LEFT JOIN pg_attribute attribute ON attribute.attrelid = definition.indrelid AND attribute.attnum = key.attnum
 WHERE namespace.nspname = current_schema() AND index_relation.relname = 'idx_platform_channel_control_route'
 GROUP BY definition.indisunique, definition.indpred, definition.indrelid`).Scan(&index)
		if result.Error != nil {
			return false, result.Error
		}
		return result.RowsAffected == 1 && !index.Unique && index.Predicate == "" &&
			index.Columns == strings.Join(expectedColumns, ","), nil
	case "sqlite":
		var indexes []struct {
			Name    string `gorm:"column:name"`
			Unique  bool   `gorm:"column:unique"`
			Partial bool   `gorm:"column:partial"`
		}
		if err := db.Raw(`PRAGMA index_list('platform_channel_control_operations')`).Scan(&indexes).Error; err != nil {
			return false, err
		}
		found := false
		for _, index := range indexes {
			if index.Name == "idx_platform_channel_control_route" {
				if index.Unique || index.Partial {
					return false, nil
				}
				found = true
				break
			}
		}
		if !found {
			return false, nil
		}
		var columns []struct {
			Sequence int    `gorm:"column:seqno"`
			Name     string `gorm:"column:name"`
		}
		if err := db.Raw(`PRAGMA index_info('idx_platform_channel_control_route')`).Scan(&columns).Error; err != nil {
			return false, err
		}
		if len(columns) != len(expectedColumns) {
			return false, nil
		}
		for index, column := range columns {
			if column.Sequence != index || column.Name != expectedColumns[index] {
				return false, nil
			}
		}
		return true, nil
	default:
		return false, nil
	}
}

func installPostgresPlatformChannelControlArtifactEvidenceV7WithDB(db *gorm.DB) error {
	guardSQL, err := platformChannelControlPostgresGuardV7SQL()
	if err != nil {
		return err
	}
	return db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec(
			"SELECT pg_advisory_xact_lock(?)",
			platformChannelControlGuardMigrationAdvisoryLock,
		).Error; err != nil {
			return errors.New("Relay schema v7 channel-test artifact evidence guard lock could not be acquired")
		}
		// A fresh v7 bootstrap has no v6 catalog underneath it, while an exact
		// v6-to-v7 upgrade has already proved these five historical checks by the
		// frozen v6 catalog fingerprint. Add only missing checks: never drop or
		// rewrite a v6 object during the incremental path. The v6 artifact check
		// is intentionally excluded because v7 replaces that one below.
		for _, constraint := range []struct {
			name      string
			statement string
		}{
			{name: "ck_platform_channel_control_route_projection_v6", statement: `ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_route_projection_v6 CHECK (
    (intent_public_model_id = '' AND intent_route_id = '')
    OR (kind = 'test' AND intent_public_model_id <> '' AND intent_route_id <> '')
)`},
			{name: "ck_platform_channel_control_transport_pair_v6", statement: `ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_transport_pair_v6 CHECK (
    (intent_route_id = '' AND intent_transport_revision = '' AND intent_transport_sha256 = '')
    OR (
        intent_route_id <> ''
        AND intent_transport_revision ~ '^sha256:[0-9a-f]{64}$'
        AND intent_transport_sha256 ~ '^sha256:[0-9a-f]{64}$'
    )
)`},
			{name: "ck_platform_channel_control_submission_state_v6", statement: `ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_submission_state_v6 CHECK (
    provider_submission_state IN (
        '', 'not_started', 'submission_unknown', 'submitted',
        'artifact_verified', 'provider_terminal', 'provider_rejected',
        'pre_submit_failed', 'reconciled_no_creation'
    )
)`},
			{name: "ck_platform_channel_control_blocker_code_v6", statement: `ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_blocker_code_v6 CHECK (
    provider_blocker_code IN (
        '', 'CHANNEL_TEST_FAILED', 'CHANNEL_TEST_UNAVAILABLE',
        'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
        'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL',
        'CHANNEL_TEST_ARTIFACT_INVALID', 'CHANNEL_TEST_ROUTE_DRIFT'
    )
)`},
			{name: "ck_platform_channel_control_reconciliation_v6", statement: `ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_reconciliation_v6 CHECK (
    (reconciliation_actor = '' AND reconciliation_reason = '' AND reconciled_at IS NULL)
    OR (
        reconciliation_actor <> '' AND reconciliation_reason <> '' AND reconciled_at IS NOT NULL
        AND provider_submission_state = 'reconciled_no_creation'
    )
)`},
		} {
			var count int64
			if err := tx.Raw(`SELECT COUNT(*) FROM pg_constraint
WHERE conrelid = 'platform_channel_control_operations'::regclass AND conname = ?`, constraint.name).
				Scan(&count).Error; err != nil || count < 0 || count > 1 {
				return errors.New("Relay schema v7 unchanged channel-test lifecycle constraint could not be inspected")
			}
			if count == 1 {
				continue
			}
			if err := tx.Exec(constraint.statement).Error; err != nil {
				return errors.New("Relay schema v7 unchanged channel-test lifecycle constraint could not be installed")
			}
		}
		for _, statement := range []string{
			`ALTER TABLE platform_channel_control_operations
DROP CONSTRAINT IF EXISTS ck_platform_channel_control_artifact_evidence_v6`,
			`ALTER TABLE platform_channel_control_operations
DROP CONSTRAINT IF EXISTS ck_platform_channel_control_artifact_evidence_v7`,
			`ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_artifact_evidence_v7 CHECK (
    (provider_artifact_sha256 = '' AND provider_artifact_size_bytes = 0 AND provider_artifact_content_type = '')
    OR (
        provider_artifact_sha256 ~ '^[0-9a-f]{64}$'
        AND provider_artifact_size_bytes > 0
        AND provider_artifact_content_type IN ('image/png', 'video/mp4')
    )
)`,
			guardSQL,
		} {
			if err := tx.Exec(statement).Error; err != nil {
				return errors.New("Relay schema v7 channel-test artifact evidence constraint or guard could not be installed")
			}
		}
		for _, trigger := range []string{
			"trg_platform_channel_control_operations_transition",
			"trg_platform_channel_control_operations_no_truncate",
		} {
			if err := tx.Exec(fmt.Sprintf(
				"DROP TRIGGER IF EXISTS %s ON platform_channel_control_operations", trigger,
			)).Error; err != nil {
				return errors.New("Relay schema v7 channel-test artifact evidence trigger could not be replaced")
			}
		}
		if err := tx.Exec(`CREATE TRIGGER trg_platform_channel_control_operations_transition
BEFORE INSERT OR UPDATE OR DELETE ON platform_channel_control_operations
FOR EACH ROW EXECUTE FUNCTION enforce_platform_channel_control_operation_transition()`).Error; err != nil {
			return errors.New("Relay schema v7 channel-test artifact evidence trigger could not be installed")
		}
		if err := tx.Exec(`CREATE TRIGGER trg_platform_channel_control_operations_no_truncate
BEFORE TRUNCATE ON platform_channel_control_operations
FOR EACH STATEMENT EXECUTE FUNCTION enforce_platform_channel_control_operation_transition()`).Error; err != nil {
			return errors.New("Relay schema v7 channel-test artifact evidence truncate guard could not be installed")
		}
		return nil
	})
}

func platformChannelControlPostgresGuardV7SQL() (string, error) {
	const v6ArtifactCondition = "NEW.provider_artifact_size_bytes <= 0 OR NEW.provider_artifact_content_type <> 'video/mp4'"
	const v7ArtifactCondition = "NEW.provider_artifact_size_bytes <= 0 OR NEW.provider_artifact_content_type NOT IN ('image/png', 'video/mp4')"
	if strings.Count(platformChannelControlV6GuardSQL, v6ArtifactCondition) != 1 {
		return "", errors.New("Relay schema v7 PostgreSQL guard source does not match the frozen v6 artifact condition")
	}
	return strings.Replace(platformChannelControlV6GuardSQL, v6ArtifactCondition, v7ArtifactCondition, 1), nil
}

func installSQLitePlatformChannelControlArtifactEvidenceV7WithDB(db *gorm.DB) error {
	insertGuard, updateGuard, err := platformChannelControlSQLiteGuardsV7()
	if err != nil {
		return err
	}
	return db.Transaction(func(tx *gorm.DB) error {
		for _, trigger := range []string{
			"trg_platform_channel_control_v6_insert",
			"trg_platform_channel_control_v6_update",
			"trg_platform_channel_control_v6_delete",
			"trg_platform_channel_control_v7_insert",
			"trg_platform_channel_control_v7_update",
			"trg_platform_channel_control_v7_delete",
		} {
			if err := tx.Exec("DROP TRIGGER IF EXISTS " + trigger).Error; err != nil {
				return errors.New("Relay SQLite schema v7 channel-test artifact evidence trigger could not be replaced")
			}
		}
		for _, statement := range []string{
			insertGuard,
			updateGuard,
			`CREATE TRIGGER trg_platform_channel_control_v7_delete
BEFORE DELETE ON platform_channel_control_operations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'platform channel control operations cannot be deleted');
END`,
		} {
			if err := tx.Exec(statement).Error; err != nil {
				return errors.New("Relay SQLite schema v7 channel-test artifact evidence guard could not be installed")
			}
		}
		return nil
	})
}

func platformChannelControlSQLiteGuardsV7() (string, string, error) {
	const v6ArtifactCondition = "NEW.provider_artifact_size_bytes > 0 AND NEW.provider_artifact_content_type = 'video/mp4'"
	const v7ArtifactCondition = "NEW.provider_artifact_size_bytes > 0 AND NEW.provider_artifact_content_type IN ('image/png', 'video/mp4')"
	if strings.Count(platformChannelControlV6SQLiteInsertGuardSQL, "trg_platform_channel_control_v6_insert") != 1 ||
		strings.Count(platformChannelControlV6SQLiteUpdateGuardSQL, "trg_platform_channel_control_v6_update") != 1 ||
		strings.Count(platformChannelControlV6SQLiteUpdateGuardSQL, v6ArtifactCondition) != 1 {
		return "", "", errors.New("Relay schema v7 SQLite guard source does not match the frozen v6 artifact condition")
	}
	insertGuard := strings.Replace(
		platformChannelControlV6SQLiteInsertGuardSQL,
		"trg_platform_channel_control_v6_insert",
		"trg_platform_channel_control_v7_insert",
		1,
	)
	updateGuard := strings.Replace(
		platformChannelControlV6SQLiteUpdateGuardSQL,
		"trg_platform_channel_control_v6_update",
		"trg_platform_channel_control_v7_update",
		1,
	)
	updateGuard = strings.Replace(updateGuard, v6ArtifactCondition, v7ArtifactCondition, 1)
	return insertGuard, updateGuard, nil
}
