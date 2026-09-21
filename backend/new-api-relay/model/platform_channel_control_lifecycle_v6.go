package model

import (
	"errors"
	"fmt"

	"gorm.io/gorm"
)

// MigratePlatformChannelControlStorageV6WithDB adds only the durable,
// secret-free route-test lifecycle projection to the frozen v5 receipt table.
// The canonical intent JSON remains the complete route binding; these columns
// exist solely for atomic admission, sticky polling and operator
// reconciliation without parsing or persisting provider payloads.
func MigratePlatformChannelControlStorageV6WithDB(db *gorm.DB) error {
	if db == nil {
		return errors.New("Relay schema v6 channel-test lifecycle database is unavailable")
	}
	if err := migratePlatformChannelControlBaseStorageV6WithDB(db); err != nil {
		return err
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
			return fmt.Errorf("Relay schema v6 channel-test lifecycle column %s could not be added", field)
		}
	}
	if err := installPlatformChannelControlLifecycleIndexesV6WithDB(db); err != nil {
		return err
	}
	switch db.Dialector.Name() {
	case "postgres":
		if err := installPostgresPlatformChannelControlLifecycleConstraintsV6WithDB(db); err != nil {
			return err
		}
		return installPostgresPlatformChannelControlLifecycleGuardV6WithDB(db)
	case "sqlite":
		return installSQLitePlatformChannelControlLifecycleGuardV6WithDB(db)
	default:
		return nil
	}
}

// migratePlatformChannelControlBaseStorageV6WithDB owns the historical base
// table bootstrap needed by a fresh v6 database. It deliberately does not
// reinstall the frozen v5 transition function: an incremental v5 database
// keeps its existing guard until the v6 replacement is ready, while a fresh
// v6 database installs only the final v6 guard below.
func migratePlatformChannelControlBaseStorageV6WithDB(db *gorm.DB) error {
	if err := db.AutoMigrate(&Channel{}, &platformChannelControlOperationPreV5{}); err != nil {
		return err
	}
	return InstallPlatformChannelControlRevisionGuardWithDB(db)
}

func installPlatformChannelControlLifecycleIndexesV6WithDB(db *gorm.DB) error {
	for _, statement := range []string{
		`DROP INDEX IF EXISTS idx_platform_channel_control_route`,
		`CREATE INDEX idx_platform_channel_control_route
ON platform_channel_control_operations (channel_id, intent_public_model_id, intent_route_id, state)`,
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
			return errors.New("Relay schema v6 channel-test lifecycle index could not be installed")
		}
	}
	return nil
}

func installPostgresPlatformChannelControlLifecycleConstraintsV6WithDB(db *gorm.DB) error {
	return db.Transaction(func(tx *gorm.DB) error {
		for _, statement := range []string{
			`ALTER TABLE platform_channel_control_operations
DROP CONSTRAINT IF EXISTS ck_platform_channel_control_route_projection_v6`,
			`ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_route_projection_v6 CHECK (
    (intent_public_model_id = '' AND intent_route_id = '')
    OR (kind = 'test' AND intent_public_model_id <> '' AND intent_route_id <> '')
)`,
			`ALTER TABLE platform_channel_control_operations
DROP CONSTRAINT IF EXISTS ck_platform_channel_control_transport_pair_v6`,
			`ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_transport_pair_v6 CHECK (
    (intent_route_id = '' AND intent_transport_revision = '' AND intent_transport_sha256 = '')
    OR (
        intent_route_id <> ''
        AND intent_transport_revision ~ '^sha256:[0-9a-f]{64}$'
        AND intent_transport_sha256 ~ '^sha256:[0-9a-f]{64}$'
    )
)`,
			`ALTER TABLE platform_channel_control_operations
DROP CONSTRAINT IF EXISTS ck_platform_channel_control_submission_state_v6`,
			`ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_submission_state_v6 CHECK (
    provider_submission_state IN (
        '', 'not_started', 'submission_unknown', 'submitted',
        'artifact_verified', 'provider_terminal', 'provider_rejected',
        'pre_submit_failed', 'reconciled_no_creation'
    )
)`,
			`ALTER TABLE platform_channel_control_operations
DROP CONSTRAINT IF EXISTS ck_platform_channel_control_blocker_code_v6`,
			`ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_blocker_code_v6 CHECK (
    provider_blocker_code IN (
        '', 'CHANNEL_TEST_FAILED', 'CHANNEL_TEST_UNAVAILABLE',
        'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
        'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL',
        'CHANNEL_TEST_ARTIFACT_INVALID', 'CHANNEL_TEST_ROUTE_DRIFT'
    )
)`,
			`ALTER TABLE platform_channel_control_operations
DROP CONSTRAINT IF EXISTS ck_platform_channel_control_artifact_evidence_v6`,
			`ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_artifact_evidence_v6 CHECK (
    (provider_artifact_sha256 = '' AND provider_artifact_size_bytes = 0 AND provider_artifact_content_type = '')
    OR (
        provider_artifact_sha256 ~ '^[0-9a-f]{64}$'
        AND provider_artifact_size_bytes > 0
        AND provider_artifact_content_type = 'video/mp4'
    )
)`,
			`ALTER TABLE platform_channel_control_operations
DROP CONSTRAINT IF EXISTS ck_platform_channel_control_reconciliation_v6`,
			`ALTER TABLE platform_channel_control_operations
ADD CONSTRAINT ck_platform_channel_control_reconciliation_v6 CHECK (
    (reconciliation_actor = '' AND reconciliation_reason = '' AND reconciled_at IS NULL)
    OR (
        reconciliation_actor <> '' AND reconciliation_reason <> '' AND reconciled_at IS NOT NULL
        AND provider_submission_state = 'reconciled_no_creation'
    )
)`,
		} {
			if err := tx.Exec(statement).Error; err != nil {
				return errors.New("Relay schema v6 channel-test lifecycle constraint could not be installed")
			}
		}
		return nil
	})
}

func installPostgresPlatformChannelControlLifecycleGuardV6WithDB(db *gorm.DB) error {
	return db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec(
			"SELECT pg_advisory_xact_lock(?)",
			platformChannelControlGuardMigrationAdvisoryLock,
		).Error; err != nil {
			return errors.New("Relay schema v6 channel-test lifecycle guard lock could not be acquired")
		}
		if err := tx.Exec(platformChannelControlV6GuardSQL).Error; err != nil {
			return errors.New("Relay schema v6 channel-test lifecycle guard could not be installed")
		}
		for _, trigger := range []string{
			"trg_platform_channel_control_operations_transition",
			"trg_platform_channel_control_operations_no_truncate",
		} {
			if err := tx.Exec(fmt.Sprintf(
				"DROP TRIGGER IF EXISTS %s ON platform_channel_control_operations", trigger,
			)).Error; err != nil {
				return errors.New("Relay schema v6 channel-test lifecycle trigger could not be replaced")
			}
		}
		if err := tx.Exec(`CREATE TRIGGER trg_platform_channel_control_operations_transition
BEFORE INSERT OR UPDATE OR DELETE ON platform_channel_control_operations
FOR EACH ROW EXECUTE FUNCTION enforce_platform_channel_control_operation_transition()`).Error; err != nil {
			return errors.New("Relay schema v6 channel-test lifecycle trigger could not be installed")
		}
		if err := tx.Exec(`CREATE TRIGGER trg_platform_channel_control_operations_no_truncate
BEFORE TRUNCATE ON platform_channel_control_operations
FOR EACH STATEMENT EXECUTE FUNCTION enforce_platform_channel_control_operation_transition()`).Error; err != nil {
			return errors.New("Relay schema v6 channel-test lifecycle truncate guard could not be installed")
		}
		return nil
	})
}

const platformChannelControlV6GuardSQL = `
CREATE OR REPLACE FUNCTION enforce_platform_channel_control_operation_transition()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'platform channel control operations cannot be truncated';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'platform channel control operations cannot be deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'pending'
           OR NEW.completed_at IS NOT NULL
           OR NEW.result_success IS NOT NULL
           OR NEW.result_response_ms IS NOT NULL
           OR NEW.result_error_code <> ''
           OR NEW.previous_revision <> ''
           OR NEW.result_revision <> ''
           OR NEW.result_previous_status <> ''
           OR NEW.result_current_status <> ''
           OR NEW.result_changed IS NOT NULL
           OR NEW.provider_blocker_code <> ''
           OR (
                NEW.kind = 'status'
                AND (
                    NEW.intent_public_model_id <> '' OR NEW.intent_route_id <> ''
                    OR NEW.intent_transport_revision <> '' OR NEW.intent_transport_sha256 <> ''
                    OR NEW.provider_submission_state <> '' OR NEW.provider_task_id <> ''
                    OR NEW.provider_artifact_sha256 <> '' OR NEW.provider_artifact_size_bytes <> 0
                    OR NEW.provider_artifact_content_type <> ''
                    OR NEW.reconciliation_actor <> '' OR NEW.reconciliation_reason <> '' OR NEW.reconciled_at IS NOT NULL
                )
           )
           OR (
                NEW.kind = 'test' AND NEW.intent_route_id = ''
                AND (
                    NEW.intent_public_model_id <> '' OR NEW.intent_transport_revision <> '' OR NEW.intent_transport_sha256 <> ''
                    OR NEW.provider_submission_state <> '' OR NEW.provider_task_id <> ''
                    OR NEW.provider_artifact_sha256 <> '' OR NEW.provider_artifact_size_bytes <> 0
                    OR NEW.provider_artifact_content_type <> ''
                    OR NEW.reconciliation_actor <> '' OR NEW.reconciliation_reason <> '' OR NEW.reconciled_at IS NOT NULL
                )
           )
           OR (
                NEW.kind = 'test' AND NEW.intent_route_id <> ''
                AND (
                    NEW.intent_public_model_id = ''
                    OR NEW.provider_submission_state <> 'not_started'
                    OR NEW.provider_task_id <> ''
                    OR NEW.provider_artifact_sha256 <> '' OR NEW.provider_artifact_size_bytes <> 0
                    OR NEW.provider_artifact_content_type <> ''
                    OR NEW.reconciliation_actor <> '' OR NEW.reconciliation_reason <> '' OR NEW.reconciled_at IS NOT NULL
                )
           )
           OR NEW.kind NOT IN ('test', 'status')
        THEN
            RAISE EXCEPTION 'invalid platform channel control operation insert';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.state <> 'pending'
       OR OLD.id IS DISTINCT FROM NEW.id
       OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.operation_id IS DISTINCT FROM NEW.operation_id
       OR OLD.channel_id IS DISTINCT FROM NEW.channel_id
       OR OLD.kind IS DISTINCT FROM NEW.kind
       OR OLD.request_id IS DISTINCT FROM NEW.request_id
       OR OLD.actor IS DISTINCT FROM NEW.actor
       OR OLD.reason IS DISTINCT FROM NEW.reason
       OR OLD.intent_sha256 IS DISTINCT FROM NEW.intent_sha256
       OR OLD.intent_json IS DISTINCT FROM NEW.intent_json
       OR OLD.intent_expected_revision IS DISTINCT FROM NEW.intent_expected_revision
       OR OLD.intent_target_status IS DISTINCT FROM NEW.intent_target_status
       OR OLD.intent_public_model_id IS DISTINCT FROM NEW.intent_public_model_id
       OR OLD.intent_route_id IS DISTINCT FROM NEW.intent_route_id
       OR OLD.intent_transport_revision IS DISTINCT FROM NEW.intent_transport_revision
       OR OLD.intent_transport_sha256 IS DISTINCT FROM NEW.intent_transport_sha256
       OR OLD.created_at IS DISTINCT FROM NEW.created_at
    THEN
        RAISE EXCEPTION 'invalid platform channel control operation transition';
    END IF;

    IF OLD.kind = 'test' AND OLD.intent_route_id <> '' AND NEW.state = 'pending' THEN
        IF OLD.completed_at IS NOT NULL OR NEW.completed_at IS NOT NULL
           OR OLD.result_success IS DISTINCT FROM NEW.result_success
           OR OLD.result_response_ms IS DISTINCT FROM NEW.result_response_ms
           OR OLD.result_error_code IS DISTINCT FROM NEW.result_error_code
           OR OLD.previous_revision IS DISTINCT FROM NEW.previous_revision
           OR OLD.result_revision IS DISTINCT FROM NEW.result_revision
           OR OLD.result_previous_status IS DISTINCT FROM NEW.result_previous_status
           OR OLD.result_current_status IS DISTINCT FROM NEW.result_current_status
           OR OLD.result_changed IS DISTINCT FROM NEW.result_changed
           OR OLD.provider_artifact_sha256 IS DISTINCT FROM NEW.provider_artifact_sha256
           OR OLD.provider_artifact_size_bytes IS DISTINCT FROM NEW.provider_artifact_size_bytes
           OR OLD.provider_artifact_content_type IS DISTINCT FROM NEW.provider_artifact_content_type
           OR OLD.reconciliation_actor IS DISTINCT FROM NEW.reconciliation_actor
           OR OLD.reconciliation_reason IS DISTINCT FROM NEW.reconciliation_reason
           OR OLD.reconciled_at IS DISTINCT FROM NEW.reconciled_at
           OR NOT (
                (OLD.provider_submission_state = 'not_started'
                 AND NEW.provider_submission_state = 'submission_unknown'
                 AND OLD.provider_task_id = '' AND NEW.provider_task_id = ''
                 AND OLD.provider_blocker_code = '' AND NEW.provider_blocker_code = '')
                OR
                (OLD.provider_submission_state = 'submission_unknown'
                 AND NEW.provider_submission_state = 'submitted'
                 AND OLD.provider_task_id = ''
                 AND NEW.provider_task_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'
                 AND OLD.provider_blocker_code = '' AND NEW.provider_blocker_code = '')
                OR
                (OLD.provider_submission_state = 'submitted'
                 AND NEW.provider_submission_state = 'submitted'
                 AND OLD.provider_task_id <> '' AND OLD.provider_task_id IS NOT DISTINCT FROM NEW.provider_task_id
                 AND NEW.provider_blocker_code <> '')
           )
        THEN
            RAISE EXCEPTION 'invalid platform channel control operation transition';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.state NOT IN ('succeeded', 'failed')
       OR OLD.completed_at IS NOT NULL
       OR NEW.completed_at IS NULL
       OR OLD.kind NOT IN ('test', 'status')
    THEN
        RAISE EXCEPTION 'invalid platform channel control operation transition';
    END IF;

    IF OLD.kind = 'test' AND OLD.intent_route_id = '' THEN
        IF OLD.previous_revision IS DISTINCT FROM NEW.previous_revision
           OR OLD.result_revision IS DISTINCT FROM NEW.result_revision
           OR OLD.result_previous_status IS DISTINCT FROM NEW.result_previous_status
           OR OLD.result_current_status IS DISTINCT FROM NEW.result_current_status
           OR OLD.result_changed IS DISTINCT FROM NEW.result_changed
           OR OLD.provider_submission_state IS DISTINCT FROM NEW.provider_submission_state
           OR OLD.provider_blocker_code IS DISTINCT FROM NEW.provider_blocker_code
           OR OLD.provider_task_id IS DISTINCT FROM NEW.provider_task_id
           OR OLD.provider_artifact_sha256 IS DISTINCT FROM NEW.provider_artifact_sha256
           OR OLD.provider_artifact_size_bytes IS DISTINCT FROM NEW.provider_artifact_size_bytes
           OR OLD.provider_artifact_content_type IS DISTINCT FROM NEW.provider_artifact_content_type
           OR OLD.reconciliation_actor IS DISTINCT FROM NEW.reconciliation_actor
           OR OLD.reconciliation_reason IS DISTINCT FROM NEW.reconciliation_reason
           OR OLD.reconciled_at IS DISTINCT FROM NEW.reconciled_at
           OR NEW.result_success IS NULL
           OR NEW.result_response_ms IS NULL OR NEW.result_response_ms < 0
           OR (NEW.state = 'succeeded' AND (NEW.result_success IS DISTINCT FROM TRUE OR NEW.result_error_code <> ''))
           OR (NEW.state = 'failed' AND (NEW.result_success IS DISTINCT FROM FALSE OR NEW.result_error_code NOT IN (
                'CHANNEL_TEST_FAILED', 'CHANNEL_TEST_UNAVAILABLE',
                'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
                'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL',
                'CHANNEL_TEST_ARTIFACT_INVALID', 'CHANNEL_TEST_ROUTE_DRIFT'
           )))
        THEN
            RAISE EXCEPTION 'invalid platform channel control operation transition';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.kind = 'test' THEN
        IF OLD.previous_revision IS DISTINCT FROM NEW.previous_revision
           OR OLD.result_revision IS DISTINCT FROM NEW.result_revision
           OR OLD.result_previous_status IS DISTINCT FROM NEW.result_previous_status
           OR OLD.result_current_status IS DISTINCT FROM NEW.result_current_status
           OR OLD.result_changed IS DISTINCT FROM NEW.result_changed
           OR NEW.result_success IS NULL
           OR NEW.result_response_ms IS NULL OR NEW.result_response_ms < 0
        THEN
            RAISE EXCEPTION 'invalid platform channel control operation transition';
        END IF;

        IF NEW.state = 'succeeded' THEN
            IF OLD.provider_submission_state <> 'submitted'
               OR NEW.provider_submission_state <> 'artifact_verified'
               OR OLD.provider_task_id = '' OR OLD.provider_task_id IS DISTINCT FROM NEW.provider_task_id
               OR NEW.result_success IS DISTINCT FROM TRUE OR NEW.result_error_code <> ''
               OR NEW.provider_blocker_code <> ''
               OR NEW.provider_artifact_sha256 !~ '^[0-9a-f]{64}$'
               OR NEW.provider_artifact_size_bytes <= 0 OR NEW.provider_artifact_content_type <> 'video/mp4'
               OR NEW.reconciliation_actor <> '' OR NEW.reconciliation_reason <> '' OR NEW.reconciled_at IS NOT NULL
            THEN
                RAISE EXCEPTION 'invalid platform channel control operation transition';
            END IF;
            RETURN NEW;
        END IF;

        IF OLD.provider_submission_state = 'not_started' THEN
            IF NEW.provider_submission_state <> 'pre_submit_failed'
               OR NEW.provider_task_id <> ''
               OR NEW.provider_blocker_code <> ''
               OR NEW.result_success IS DISTINCT FROM FALSE
               OR NEW.result_error_code NOT IN (
                    'CHANNEL_TEST_FAILED', 'CHANNEL_TEST_UNAVAILABLE',
                    'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
                    'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL',
                    'CHANNEL_TEST_ARTIFACT_INVALID', 'CHANNEL_TEST_ROUTE_DRIFT'
               )
               OR NEW.provider_artifact_sha256 <> '' OR NEW.provider_artifact_size_bytes <> 0
               OR NEW.provider_artifact_content_type <> ''
               OR NEW.reconciliation_actor <> '' OR NEW.reconciliation_reason <> '' OR NEW.reconciled_at IS NOT NULL
            THEN
                RAISE EXCEPTION 'invalid platform channel control operation transition';
            END IF;
            RETURN NEW;
        END IF;

        IF OLD.provider_submission_state = 'submission_unknown' THEN
            IF NEW.provider_submission_state = 'reconciled_no_creation' THEN
                IF OLD.provider_task_id <> '' OR NEW.provider_task_id <> ''
                   OR NEW.provider_blocker_code <> ''
                   OR NEW.result_success IS DISTINCT FROM FALSE OR NEW.result_response_ms <> 0
                   OR NEW.result_error_code <> 'CHANNEL_TEST_RECONCILED_NO_CREATION'
                   OR NEW.provider_artifact_sha256 <> '' OR NEW.provider_artifact_size_bytes <> 0
                   OR NEW.provider_artifact_content_type <> ''
                   OR NEW.reconciliation_actor = '' OR NEW.reconciliation_reason = '' OR NEW.reconciled_at IS NULL
                THEN
                    RAISE EXCEPTION 'invalid platform channel control operation transition';
                END IF;
                RETURN NEW;
            END IF;
			IF NEW.provider_submission_state <> 'provider_rejected'
               OR OLD.provider_task_id <> '' OR NEW.provider_task_id <> ''
               OR NEW.provider_blocker_code <> ''
               OR NEW.result_success IS DISTINCT FROM FALSE
			   OR NEW.result_error_code NOT IN (
					'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
					'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL'
			   )
               OR NEW.provider_artifact_sha256 <> '' OR NEW.provider_artifact_size_bytes <> 0
               OR NEW.provider_artifact_content_type <> ''
               OR NEW.reconciliation_actor <> '' OR NEW.reconciliation_reason <> '' OR NEW.reconciled_at IS NOT NULL
            THEN
                RAISE EXCEPTION 'invalid platform channel control operation transition';
            END IF;
            RETURN NEW;
        END IF;

        IF OLD.provider_submission_state = 'submitted' THEN
            IF NEW.provider_submission_state <> 'provider_terminal'
               OR OLD.provider_task_id = '' OR OLD.provider_task_id IS DISTINCT FROM NEW.provider_task_id
               OR NEW.provider_blocker_code <> ''
               OR NEW.result_success IS DISTINCT FROM FALSE
               OR NEW.result_error_code NOT IN (
                    'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
                    'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL'
               )
               OR NEW.provider_artifact_sha256 <> '' OR NEW.provider_artifact_size_bytes <> 0
               OR NEW.provider_artifact_content_type <> ''
               OR NEW.reconciliation_actor <> '' OR NEW.reconciliation_reason <> '' OR NEW.reconciled_at IS NOT NULL
            THEN
                RAISE EXCEPTION 'invalid platform channel control operation transition';
            END IF;
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'invalid platform channel control operation transition';
    END IF;

    IF OLD.kind = 'status' THEN
        IF NEW.result_success IS NOT NULL
           OR NEW.result_response_ms IS NOT NULL
           OR OLD.provider_submission_state IS DISTINCT FROM NEW.provider_submission_state
           OR OLD.provider_blocker_code IS DISTINCT FROM NEW.provider_blocker_code
           OR OLD.provider_task_id IS DISTINCT FROM NEW.provider_task_id
           OR OLD.provider_artifact_sha256 IS DISTINCT FROM NEW.provider_artifact_sha256
           OR OLD.provider_artifact_size_bytes IS DISTINCT FROM NEW.provider_artifact_size_bytes
           OR OLD.provider_artifact_content_type IS DISTINCT FROM NEW.provider_artifact_content_type
           OR OLD.reconciliation_actor IS DISTINCT FROM NEW.reconciliation_actor
           OR OLD.reconciliation_reason IS DISTINCT FROM NEW.reconciliation_reason
           OR OLD.reconciled_at IS DISTINCT FROM NEW.reconciled_at
           OR NEW.previous_revision !~ '^sha256:[0-9a-f]{64}$'
           OR NEW.result_revision !~ '^sha256:[0-9a-f]{64}$'
           OR NEW.result_previous_status NOT IN ('enabled', 'manually_disabled', 'auto_disabled')
           OR NEW.result_current_status NOT IN ('enabled', 'manually_disabled', 'auto_disabled')
           OR NEW.result_changed IS NULL
           OR (NEW.state = 'succeeded' AND (
                NEW.result_error_code <> ''
                OR NEW.previous_revision <> OLD.intent_expected_revision
                OR NEW.result_current_status <> OLD.intent_target_status
           ))
           OR (NEW.state = 'failed' AND (
                NEW.result_error_code <> 'CHANNEL_REVISION_CONFLICT'
                OR NEW.previous_revision = OLD.intent_expected_revision
                OR NEW.result_previous_status <> NEW.result_current_status
                OR NEW.result_changed IS DISTINCT FROM FALSE
           ))
        THEN
            RAISE EXCEPTION 'invalid platform channel control operation transition';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'invalid platform channel control operation transition';
END;
$$ LANGUAGE plpgsql`

func installSQLitePlatformChannelControlLifecycleGuardV6WithDB(db *gorm.DB) error {
	return db.Transaction(func(tx *gorm.DB) error {
		for _, trigger := range []string{
			"trg_platform_channel_control_v6_insert",
			"trg_platform_channel_control_v6_update",
			"trg_platform_channel_control_v6_delete",
		} {
			if err := tx.Exec("DROP TRIGGER IF EXISTS " + trigger).Error; err != nil {
				return errors.New("Relay SQLite schema v6 channel-test lifecycle trigger could not be replaced")
			}
		}
		for _, statement := range []string{
			platformChannelControlV6SQLiteInsertGuardSQL,
			platformChannelControlV6SQLiteUpdateGuardSQL,
			`CREATE TRIGGER trg_platform_channel_control_v6_delete
BEFORE DELETE ON platform_channel_control_operations
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'platform channel control operations cannot be deleted');
END`,
		} {
			if err := tx.Exec(statement).Error; err != nil {
				return errors.New("Relay SQLite schema v6 channel-test lifecycle guard could not be installed")
			}
		}
		return nil
	})
}

const platformChannelControlV6SQLiteInsertGuardSQL = `
CREATE TRIGGER trg_platform_channel_control_v6_insert
BEFORE INSERT ON platform_channel_control_operations
FOR EACH ROW
WHEN NOT (
    NEW.state = 'pending'
    AND NEW.completed_at IS NULL
    AND NEW.result_success IS NULL
    AND NEW.result_response_ms IS NULL
    AND NEW.result_error_code = ''
    AND NEW.previous_revision = ''
    AND NEW.result_revision = ''
    AND NEW.result_previous_status = ''
    AND NEW.result_current_status = ''
    AND NEW.result_changed IS NULL
    AND NEW.provider_blocker_code = ''
    AND (
        (NEW.kind = 'status'
         AND NEW.intent_public_model_id = '' AND NEW.intent_route_id = ''
         AND NEW.intent_transport_revision = '' AND NEW.intent_transport_sha256 = ''
         AND NEW.provider_submission_state = '' AND NEW.provider_task_id = ''
         AND NEW.provider_artifact_sha256 = '' AND NEW.provider_artifact_size_bytes = 0
         AND NEW.provider_artifact_content_type = ''
         AND NEW.reconciliation_actor = '' AND NEW.reconciliation_reason = '' AND NEW.reconciled_at IS NULL)
        OR
        (NEW.kind = 'test' AND NEW.intent_route_id = ''
         AND NEW.intent_public_model_id = ''
         AND NEW.intent_transport_revision = '' AND NEW.intent_transport_sha256 = ''
         AND NEW.provider_submission_state = '' AND NEW.provider_task_id = ''
         AND NEW.provider_artifact_sha256 = '' AND NEW.provider_artifact_size_bytes = 0
         AND NEW.provider_artifact_content_type = ''
         AND NEW.reconciliation_actor = '' AND NEW.reconciliation_reason = '' AND NEW.reconciled_at IS NULL)
        OR
        (NEW.kind = 'test' AND NEW.intent_route_id <> '' AND NEW.intent_public_model_id <> ''
         AND length(NEW.intent_transport_revision) = 71 AND substr(NEW.intent_transport_revision, 1, 7) = 'sha256:'
         AND substr(NEW.intent_transport_revision, 8) NOT GLOB '*[^0-9a-f]*'
         AND length(NEW.intent_transport_sha256) = 71 AND substr(NEW.intent_transport_sha256, 1, 7) = 'sha256:'
         AND substr(NEW.intent_transport_sha256, 8) NOT GLOB '*[^0-9a-f]*'
         AND NEW.provider_submission_state = 'not_started' AND NEW.provider_task_id = ''
         AND NEW.provider_artifact_sha256 = '' AND NEW.provider_artifact_size_bytes = 0
         AND NEW.provider_artifact_content_type = ''
         AND NEW.reconciliation_actor = '' AND NEW.reconciliation_reason = '' AND NEW.reconciled_at IS NULL)
    )
)
BEGIN
    SELECT RAISE(ABORT, 'invalid platform channel control operation insert');
END`

const platformChannelControlV6SQLiteUpdateGuardSQL = `
CREATE TRIGGER trg_platform_channel_control_v6_update
BEFORE UPDATE ON platform_channel_control_operations
FOR EACH ROW
WHEN NOT (
    OLD.state = 'pending'
    AND OLD.id IS NEW.id
    AND OLD.tenant_id IS NEW.tenant_id
    AND OLD.operation_id IS NEW.operation_id
    AND OLD.channel_id IS NEW.channel_id
    AND OLD.kind IS NEW.kind
    AND OLD.request_id IS NEW.request_id
    AND OLD.actor IS NEW.actor
    AND OLD.reason IS NEW.reason
    AND OLD.intent_sha256 IS NEW.intent_sha256
    AND OLD.intent_json IS NEW.intent_json
    AND OLD.intent_expected_revision IS NEW.intent_expected_revision
    AND OLD.intent_target_status IS NEW.intent_target_status
    AND OLD.intent_public_model_id IS NEW.intent_public_model_id
    AND OLD.intent_route_id IS NEW.intent_route_id
    AND OLD.intent_transport_revision IS NEW.intent_transport_revision
    AND OLD.intent_transport_sha256 IS NEW.intent_transport_sha256
    AND OLD.created_at IS NEW.created_at
    AND (
        (OLD.kind = 'test' AND OLD.intent_route_id <> '' AND NEW.state = 'pending'
         AND OLD.completed_at IS NULL AND NEW.completed_at IS NULL
         AND OLD.result_success IS NEW.result_success
         AND OLD.result_response_ms IS NEW.result_response_ms
         AND OLD.result_error_code IS NEW.result_error_code
         AND OLD.previous_revision IS NEW.previous_revision
         AND OLD.result_revision IS NEW.result_revision
         AND OLD.result_previous_status IS NEW.result_previous_status
         AND OLD.result_current_status IS NEW.result_current_status
         AND OLD.result_changed IS NEW.result_changed
         AND OLD.provider_artifact_sha256 IS NEW.provider_artifact_sha256
         AND OLD.provider_artifact_size_bytes IS NEW.provider_artifact_size_bytes
         AND OLD.provider_artifact_content_type IS NEW.provider_artifact_content_type
         AND OLD.reconciliation_actor IS NEW.reconciliation_actor
         AND OLD.reconciliation_reason IS NEW.reconciliation_reason
         AND OLD.reconciled_at IS NEW.reconciled_at
         AND (
             (OLD.provider_submission_state = 'not_started' AND NEW.provider_submission_state = 'submission_unknown'
              AND OLD.provider_task_id = '' AND NEW.provider_task_id = ''
              AND OLD.provider_blocker_code = '' AND NEW.provider_blocker_code = '')
             OR
             (OLD.provider_submission_state = 'submission_unknown' AND NEW.provider_submission_state = 'submitted'
              AND OLD.provider_task_id = '' AND length(NEW.provider_task_id) BETWEEN 1 AND 256
              AND substr(NEW.provider_task_id, 1, 1) GLOB '[A-Za-z0-9]'
              AND NEW.provider_task_id NOT GLOB '*[^A-Za-z0-9._:-]*'
              AND OLD.provider_blocker_code = '' AND NEW.provider_blocker_code = '')
             OR
             (OLD.provider_submission_state = 'submitted' AND NEW.provider_submission_state = 'submitted'
              AND length(OLD.provider_task_id) > 0 AND OLD.provider_task_id IS NEW.provider_task_id
              AND NEW.provider_blocker_code IN (
                    'CHANNEL_TEST_FAILED', 'CHANNEL_TEST_UNAVAILABLE',
                    'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
                    'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL',
                    'CHANNEL_TEST_ARTIFACT_INVALID', 'CHANNEL_TEST_ROUTE_DRIFT'))
         ))
        OR
        (OLD.kind = 'test' AND OLD.intent_route_id = ''
         AND NEW.state IN ('succeeded', 'failed')
         AND OLD.completed_at IS NULL AND NEW.completed_at IS NOT NULL
         AND OLD.previous_revision IS NEW.previous_revision
         AND OLD.result_revision IS NEW.result_revision
         AND OLD.result_previous_status IS NEW.result_previous_status
         AND OLD.result_current_status IS NEW.result_current_status
         AND OLD.result_changed IS NEW.result_changed
         AND OLD.provider_submission_state IS NEW.provider_submission_state
         AND OLD.provider_blocker_code IS NEW.provider_blocker_code
         AND OLD.provider_task_id IS NEW.provider_task_id
         AND OLD.provider_artifact_sha256 IS NEW.provider_artifact_sha256
         AND OLD.provider_artifact_size_bytes IS NEW.provider_artifact_size_bytes
         AND OLD.provider_artifact_content_type IS NEW.provider_artifact_content_type
         AND OLD.reconciliation_actor IS NEW.reconciliation_actor
         AND OLD.reconciliation_reason IS NEW.reconciliation_reason
         AND OLD.reconciled_at IS NEW.reconciled_at
         AND NEW.result_success IS NOT NULL AND NEW.result_response_ms >= 0
         AND ((NEW.state = 'succeeded' AND NEW.result_success = 1 AND NEW.result_error_code = '')
              OR (NEW.state = 'failed' AND NEW.result_success = 0 AND NEW.result_error_code IN (
                    'CHANNEL_TEST_FAILED', 'CHANNEL_TEST_UNAVAILABLE',
                    'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
                    'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL',
                    'CHANNEL_TEST_ARTIFACT_INVALID', 'CHANNEL_TEST_ROUTE_DRIFT'))))
        OR
        (OLD.kind = 'test' AND OLD.intent_route_id <> ''
         AND NEW.state = 'succeeded' AND OLD.completed_at IS NULL AND NEW.completed_at IS NOT NULL
         AND OLD.provider_submission_state = 'submitted' AND NEW.provider_submission_state = 'artifact_verified'
         AND length(OLD.provider_task_id) > 0 AND OLD.provider_task_id IS NEW.provider_task_id
         AND NEW.result_success = 1 AND NEW.result_response_ms >= 0 AND NEW.result_error_code = ''
         AND NEW.provider_blocker_code = ''
         AND length(NEW.provider_artifact_sha256) = 64
         AND NEW.provider_artifact_sha256 NOT GLOB '*[^0-9a-f]*'
         AND NEW.provider_artifact_size_bytes > 0 AND NEW.provider_artifact_content_type = 'video/mp4'
         AND NEW.reconciliation_actor = '' AND NEW.reconciliation_reason = '' AND NEW.reconciled_at IS NULL
         AND OLD.previous_revision IS NEW.previous_revision AND OLD.result_revision IS NEW.result_revision
         AND OLD.result_previous_status IS NEW.result_previous_status
         AND OLD.result_current_status IS NEW.result_current_status
         AND OLD.result_changed IS NEW.result_changed)
        OR
        (OLD.kind = 'test' AND OLD.intent_route_id <> ''
         AND NEW.state = 'failed' AND OLD.completed_at IS NULL AND NEW.completed_at IS NOT NULL
         AND NEW.result_success = 0 AND NEW.result_response_ms >= 0
         AND NEW.result_error_code IN (
              'CHANNEL_TEST_FAILED', 'CHANNEL_TEST_UNAVAILABLE',
              'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
              'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL',
              'CHANNEL_TEST_ARTIFACT_INVALID', 'CHANNEL_TEST_ROUTE_DRIFT')
         AND NEW.provider_blocker_code = ''
         AND NEW.provider_artifact_sha256 = '' AND NEW.provider_artifact_size_bytes = 0
         AND NEW.provider_artifact_content_type = ''
         AND NEW.reconciliation_actor = '' AND NEW.reconciliation_reason = '' AND NEW.reconciled_at IS NULL
         AND OLD.previous_revision IS NEW.previous_revision AND OLD.result_revision IS NEW.result_revision
         AND OLD.result_previous_status IS NEW.result_previous_status
         AND OLD.result_current_status IS NEW.result_current_status
         AND OLD.result_changed IS NEW.result_changed
         AND (
              (OLD.provider_submission_state = 'not_started' AND NEW.provider_submission_state = 'pre_submit_failed'
               AND OLD.provider_task_id = '' AND NEW.provider_task_id = '')
              OR
			(OLD.provider_submission_state = 'submission_unknown'
			 AND NEW.provider_submission_state = 'provider_rejected'
			   AND OLD.provider_task_id = '' AND NEW.provider_task_id = ''
			   AND NEW.result_error_code IN (
					'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
					'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL'))
              OR
              (OLD.provider_submission_state = 'submitted' AND NEW.provider_submission_state = 'provider_terminal'
               AND length(OLD.provider_task_id) > 0 AND OLD.provider_task_id IS NEW.provider_task_id
               AND NEW.result_error_code IN (
                    'CHANNEL_TEST_PROVIDER_VALIDATION', 'CHANNEL_TEST_PROVIDER_AUTH',
                    'CHANNEL_TEST_PROVIDER_QUOTA', 'CHANNEL_TEST_PROVIDER_TERMINAL'))
         ))
        OR
        (OLD.kind = 'test' AND OLD.intent_route_id <> ''
         AND NEW.state = 'failed' AND OLD.completed_at IS NULL AND NEW.completed_at IS NOT NULL
         AND OLD.provider_submission_state = 'submission_unknown'
         AND NEW.provider_submission_state = 'reconciled_no_creation'
         AND OLD.provider_task_id = '' AND NEW.provider_task_id = ''
         AND NEW.result_success = 0 AND NEW.result_response_ms = 0
         AND NEW.result_error_code = 'CHANNEL_TEST_RECONCILED_NO_CREATION'
         AND NEW.provider_blocker_code = ''
         AND NEW.provider_artifact_sha256 = '' AND NEW.provider_artifact_size_bytes = 0
         AND NEW.provider_artifact_content_type = ''
         AND NEW.reconciliation_actor <> '' AND NEW.reconciliation_reason <> '' AND NEW.reconciled_at IS NOT NULL
         AND OLD.previous_revision IS NEW.previous_revision AND OLD.result_revision IS NEW.result_revision
         AND OLD.result_previous_status IS NEW.result_previous_status
         AND OLD.result_current_status IS NEW.result_current_status
         AND OLD.result_changed IS NEW.result_changed)
        OR
        (OLD.kind = 'status' AND NEW.state IN ('succeeded', 'failed')
         AND OLD.completed_at IS NULL AND NEW.completed_at IS NOT NULL
         AND NEW.result_success IS NULL AND NEW.result_response_ms IS NULL
         AND OLD.provider_submission_state IS NEW.provider_submission_state
         AND OLD.provider_blocker_code IS NEW.provider_blocker_code
         AND OLD.provider_task_id IS NEW.provider_task_id
         AND OLD.provider_artifact_sha256 IS NEW.provider_artifact_sha256
         AND OLD.provider_artifact_size_bytes IS NEW.provider_artifact_size_bytes
         AND OLD.provider_artifact_content_type IS NEW.provider_artifact_content_type
         AND OLD.reconciliation_actor IS NEW.reconciliation_actor
         AND OLD.reconciliation_reason IS NEW.reconciliation_reason
         AND OLD.reconciled_at IS NEW.reconciled_at
         AND length(NEW.previous_revision) = 71 AND substr(NEW.previous_revision, 1, 7) = 'sha256:'
         AND substr(NEW.previous_revision, 8) NOT GLOB '*[^0-9a-f]*'
         AND length(NEW.result_revision) = 71 AND substr(NEW.result_revision, 1, 7) = 'sha256:'
         AND substr(NEW.result_revision, 8) NOT GLOB '*[^0-9a-f]*'
         AND NEW.result_previous_status IN ('enabled', 'manually_disabled', 'auto_disabled')
         AND NEW.result_current_status IN ('enabled', 'manually_disabled', 'auto_disabled')
         AND NEW.result_changed IS NOT NULL
         AND ((NEW.state = 'succeeded' AND NEW.result_error_code = ''
               AND NEW.previous_revision = OLD.intent_expected_revision
               AND NEW.result_current_status = OLD.intent_target_status)
              OR (NEW.state = 'failed' AND NEW.result_error_code = 'CHANNEL_REVISION_CONFLICT'
                  AND NEW.previous_revision <> OLD.intent_expected_revision
                  AND NEW.result_previous_status = NEW.result_current_status
                  AND NEW.result_changed = 0)))
    )
)
BEGIN
    SELECT RAISE(ABORT, 'invalid platform channel control operation transition');
END`
