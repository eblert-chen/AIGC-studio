package model

import (
	"errors"

	"gorm.io/gorm"
)

// installPlatformChannelControlDiagnosticTaxonomyV5WithDB preserves the
// already-released v5 table shape. V5 widened only the closed, secret-free
// failure taxonomy accepted by the historical pending -> terminal receipt
// transition; route identity and provider-submission lifecycle columns belong
// to v6 and must never be retrofitted into this frozen migration.
func installPlatformChannelControlDiagnosticTaxonomyV5WithDB(db *gorm.DB) error {
	if db == nil {
		return errors.New("Relay schema v5 channel-test diagnostic database is unavailable")
	}
	if db.Dialector.Name() != "postgres" {
		return nil
	}
	return db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec(
			"SELECT pg_advisory_xact_lock(?)",
			platformChannelControlGuardMigrationAdvisoryLock,
		).Error; err != nil {
			return errors.New("Relay schema v5 channel-test diagnostic guard lock could not be acquired")
		}
		if err := tx.Exec(platformChannelControlV5GuardSQL).Error; err != nil {
			return errors.New("Relay schema v5 channel-test diagnostic guard could not be installed")
		}
		return nil
	})
}

// This definition is the exact function body attested by the deployed v5
// PostgreSQL catalog. Do not add v6 columns or transitions here.
const platformChannelControlV5GuardSQL = `
CREATE OR REPLACE FUNCTION enforce_platform_channel_control_operation_transition()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'platform channel control operations cannot be truncated';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'platform channel control operations cannot be deleted';
    END IF;
    IF OLD.state <> 'pending'
       OR NEW.state NOT IN ('succeeded', 'failed')
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
       OR OLD.created_at IS DISTINCT FROM NEW.created_at
       OR OLD.completed_at IS NOT NULL
       OR NEW.completed_at IS NULL
       OR OLD.kind NOT IN ('test', 'status')
       OR (OLD.kind = 'test' AND (
            OLD.previous_revision IS DISTINCT FROM NEW.previous_revision
            OR OLD.result_revision IS DISTINCT FROM NEW.result_revision
            OR OLD.result_previous_status IS DISTINCT FROM NEW.result_previous_status
            OR OLD.result_current_status IS DISTINCT FROM NEW.result_current_status
            OR OLD.result_changed IS DISTINCT FROM NEW.result_changed
            OR NEW.result_success IS NULL
            OR NEW.result_response_ms IS NULL
            OR NEW.result_response_ms < 0
            OR (NEW.state = 'succeeded' AND (NEW.result_success IS DISTINCT FROM TRUE OR NEW.result_error_code <> ''))
            OR (NEW.state = 'failed' AND (NEW.result_success IS DISTINCT FROM FALSE OR NEW.result_error_code NOT IN (
                'CHANNEL_TEST_FAILED',
                'CHANNEL_TEST_UNAVAILABLE',
                'CHANNEL_TEST_PROVIDER_VALIDATION',
                'CHANNEL_TEST_PROVIDER_AUTH',
                'CHANNEL_TEST_PROVIDER_QUOTA',
                'CHANNEL_TEST_PROVIDER_TERMINAL'
            )))
       ))
       OR (OLD.kind = 'status' AND (
            NEW.result_success IS NOT NULL
            OR NEW.result_response_ms IS NOT NULL
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
       ))
    THEN
        RAISE EXCEPTION 'invalid platform channel control operation transition';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql`
