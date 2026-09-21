package model

import (
	"errors"

	"gorm.io/gorm"
)

const (
	platformChannelCostPersonalScopeIndexV7        = "idx_platform_channel_cost_personal_workspace"
	platformChannelCostBillingScopeConstraintV7    = "ck_platform_channel_cost_billing_scope_v7"
	platformChannelCostBillingScopeInsertTriggerV7 = "trg_platform_channel_cost_billing_scope_v7_insert"
	platformChannelCostBillingScopeUpdateTriggerV7 = "trg_platform_channel_cost_billing_scope_v7_update"
)

// MigratePlatformChannelCostPersonalScopeV7WithDB adds the personal billing
// scope to Relay's immutable provider-cost fact. Historical schema snapshots
// deliberately ignore the live ORM field; only the v7 migration may create
// this column and its database-enforced task-linkage contract.
func MigratePlatformChannelCostPersonalScopeV7WithDB(db *gorm.DB) error {
	if db == nil {
		return errors.New("Relay channel-cost personal scope database is unavailable")
	}
	migrator := db.Migrator()
	if !migrator.HasTable(&PlatformChannelCostEvent{}) {
		return errors.New("Relay channel-cost event table is unavailable")
	}
	if !migrator.HasColumn(&PlatformChannelCostEvent{}, "personal_workspace_id") {
		if err := db.Exec(`ALTER TABLE platform_channel_cost_events ADD COLUMN personal_workspace_id varchar(64)`).Error; err != nil {
			return errors.New("Relay channel-cost personal scope column could not be added")
		}
	}

	if !migrator.HasIndex(&PlatformChannelCostEvent{}, platformChannelCostPersonalScopeIndexV7) {
		var statement string
		switch db.Dialector.Name() {
		case "postgres", "sqlite":
			statement = `CREATE INDEX IF NOT EXISTS idx_platform_channel_cost_personal_workspace ON platform_channel_cost_events (personal_workspace_id)`
		case "mysql":
			statement = `CREATE INDEX idx_platform_channel_cost_personal_workspace ON platform_channel_cost_events (personal_workspace_id)`
		default:
			return errors.New("Relay channel-cost personal scope index dialect is unsupported")
		}
		if err := db.Exec(statement).Error; err != nil {
			return errors.New("Relay channel-cost personal scope index could not be added")
		}
	}

	if db.Dialector.Name() == "sqlite" {
		return migratePlatformChannelCostBillingScopeSQLiteV7(db)
	}
	if db.Dialector.Name() != "postgres" {
		return nil
	}
	var constraintCount int64
	if err := db.Raw(`
SELECT COUNT(*)
FROM pg_constraint
WHERE conrelid = 'platform_channel_cost_events'::regclass
  AND conname = ?`, platformChannelCostBillingScopeConstraintV7).Scan(&constraintCount).Error; err != nil {
		return errors.New("Relay channel-cost billing scope constraint could not be inspected")
	}
	if constraintCount > 1 {
		return errors.New("Relay channel-cost billing scope constraint is ambiguous")
	}
	if constraintCount == 1 {
		return nil
	}
	if err := db.Exec(`
ALTER TABLE platform_channel_cost_events
ADD CONSTRAINT ck_platform_channel_cost_billing_scope_v7 CHECK (
	  num_nonnulls(NULLIF(company_id, ''), NULLIF(personal_workspace_id, '')) <= 1
  AND (
    NULLIF(task_id, '') IS NULL
    OR (
	      num_nonnulls(NULLIF(company_id, ''), NULLIF(personal_workspace_id, '')) = 1
      AND NULLIF(relay_job_id, '') IS NOT NULL
    )
  )
)`).Error; err != nil {
		return errors.New("Relay channel-cost billing scope constraint could not be added")
	}
	return nil
}

func migratePlatformChannelCostBillingScopeSQLiteV7(db *gorm.DB) error {
	return db.Transaction(func(tx *gorm.DB) error {
		var invalidRows int64
		if err := tx.Raw(`SELECT COUNT(*)
FROM platform_channel_cost_events
WHERE (
  (CASE WHEN NULLIF(company_id, '') IS NOT NULL THEN 1 ELSE 0 END) +
  (CASE WHEN NULLIF(personal_workspace_id, '') IS NOT NULL THEN 1 ELSE 0 END) > 1
) OR (
  NULLIF(task_id, '') IS NOT NULL
  AND (
    (CASE WHEN NULLIF(company_id, '') IS NOT NULL THEN 1 ELSE 0 END) +
    (CASE WHEN NULLIF(personal_workspace_id, '') IS NOT NULL THEN 1 ELSE 0 END) <> 1
    OR NULLIF(relay_job_id, '') IS NULL
  )
)`).Scan(&invalidRows).Error; err != nil {
			return errors.New("Relay channel-cost SQLite billing scope rows could not be inspected")
		}
		if invalidRows != 0 {
			return errors.New("Relay channel-cost SQLite billing scope rows are invalid")
		}
		for _, trigger := range []string{
			platformChannelCostBillingScopeInsertTriggerV7,
			platformChannelCostBillingScopeUpdateTriggerV7,
		} {
			if err := tx.Exec("DROP TRIGGER IF EXISTS " + trigger).Error; err != nil {
				return errors.New("Relay channel-cost SQLite billing scope trigger could not be replaced")
			}
		}
		for _, statement := range []string{
			`CREATE TRIGGER trg_platform_channel_cost_billing_scope_v7_insert
BEFORE INSERT ON platform_channel_cost_events
FOR EACH ROW
WHEN (
  (CASE WHEN NULLIF(NEW.company_id, '') IS NOT NULL THEN 1 ELSE 0 END) +
  (CASE WHEN NULLIF(NEW.personal_workspace_id, '') IS NOT NULL THEN 1 ELSE 0 END) > 1
) OR (
  NULLIF(NEW.task_id, '') IS NOT NULL
  AND (
    (CASE WHEN NULLIF(NEW.company_id, '') IS NOT NULL THEN 1 ELSE 0 END) +
    (CASE WHEN NULLIF(NEW.personal_workspace_id, '') IS NOT NULL THEN 1 ELSE 0 END) <> 1
    OR NULLIF(NEW.relay_job_id, '') IS NULL
  )
)
BEGIN
  SELECT RAISE(ABORT, 'platform channel cost billing scope is invalid');
END`,
			`CREATE TRIGGER trg_platform_channel_cost_billing_scope_v7_update
BEFORE UPDATE ON platform_channel_cost_events
FOR EACH ROW
WHEN (
  (CASE WHEN NULLIF(NEW.company_id, '') IS NOT NULL THEN 1 ELSE 0 END) +
  (CASE WHEN NULLIF(NEW.personal_workspace_id, '') IS NOT NULL THEN 1 ELSE 0 END) > 1
) OR (
  NULLIF(NEW.task_id, '') IS NOT NULL
  AND (
    (CASE WHEN NULLIF(NEW.company_id, '') IS NOT NULL THEN 1 ELSE 0 END) +
    (CASE WHEN NULLIF(NEW.personal_workspace_id, '') IS NOT NULL THEN 1 ELSE 0 END) <> 1
    OR NULLIF(NEW.relay_job_id, '') IS NULL
  )
)
BEGIN
  SELECT RAISE(ABORT, 'platform channel cost billing scope is invalid');
END`,
		} {
			if err := tx.Exec(statement).Error; err != nil {
				return errors.New("Relay channel-cost SQLite billing scope trigger could not be installed")
			}
		}
		return nil
	})
}
