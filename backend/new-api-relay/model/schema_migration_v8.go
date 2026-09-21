package model

import (
	"errors"
	"fmt"
	"strings"

	"gorm.io/gorm"
)

type relaySchemaV8Step struct {
	ID string
	Up func(*gorm.DB) error
}

// relaySchemaV8BootstrapSteps is the complete fresh-v8 snapshot. Historical
// migrations are not replayed or reinterpreted by a new installation.
func relaySchemaV8BootstrapSteps() []relaySchemaV8Step {
	return []relaySchemaV8Step{
		{ID: "subscription-plan-price-decimal-v8", Up: migrateSubscriptionPlanPriceAmountWithDB},
		{ID: "token-model-limits-text-v8", Up: migrateTokenModelLimitsToTextWithDB},
		{ID: "channel-cost-digest-varchar-v8", Up: migratePlatformChannelCostDocumentDigestStorageWithDB},
		{ID: "gorm-model-bootstrap-v8", Up: migrateRelaySchemaV8Models},
		{ID: "terminal-platform-native-result-url-scrub-v8", Up: scrubPlatformGenerationTerminalNativeResultURLsV4},
		{ID: "previous-candidate-catalog-normalization-v8", Up: migrateRelaySchemaV8PreviousCandidateCatalog},
		{ID: "provider-task-credential-vault-v8", Up: MigrateProviderCredentialVaultStorageWithDB},
		{ID: "provider-channel-credential-vault-v8", Up: migrateProviderChannelCredentialVaultStorageV3WithDB},
		{ID: "artifact-intent-v8", Up: MigratePlatformArtifactUploadIntentStorageWithDB},
		{ID: "shared-account-state-v8", Up: MigratePlatformGenerationProviderAccountStateWithDB},
		{ID: "reconciliation-append-only-v8", Up: MigratePlatformGenerationReconciliationStorageWithDB},
		{ID: "callback-redrive-append-only-v8", Up: MigratePlatformGenerationCallbackOperationsStorageWithDB},
		{ID: "channel-control-artifact-content-types-v8", Up: MigratePlatformChannelControlStorageV7WithDB},
		{ID: "provider-cost-monitor-guards-v8", Up: MigratePlatformProviderMonitorAndCostStorageWithDB},
		{ID: "channel-cost-personal-scope-v8", Up: MigratePlatformChannelCostPersonalScopeV7WithDB},
		{ID: "provider-cost-allocation-evidence-v8", Up: MigratePlatformProviderCostEvidenceStorageV8WithDB},
		{ID: "auth-version-backfill-v8", Up: InitializeUserAuthVersionsWithDB},
		{ID: "external-identity-backfill-v8", Up: InitializeExternalIdentityClaimsWithDB},
		{ID: "subscription-plan-v8", Up: migrateRelaySchemaV8SubscriptionPlan},
		{ID: "retired-option-migration-v8", Up: migrateRetiredFrontendOptionsStrictWithDB},
		{ID: "generation-route-release-binding-guards-v8", Up: installPlatformGenerationRouteBindingGuardsV4},
		{ID: "runtime-dml-privilege-manifest-v8", Up: ApplyRelayDatabasePrivilegeManifestWithDB},
		{ID: "download-edge-rls-v8", Up: MigratePlatformDownloadEdgeIsolationWithDB},
		{ID: "download-edge-dml-privilege-manifest-v8", Up: ApplyRelayDownloadEdgeDatabasePrivilegeManifestWithDB},
	}
}

func migrateRelaySchemaV8Bootstrap(db *gorm.DB) error {
	for _, step := range relaySchemaV8BootstrapSteps() {
		if err := step.Up(db); err != nil {
			return fmt.Errorf("Relay schema v8 bootstrap step %s failed: %w", step.ID, err)
		}
	}
	return nil
}

func migrateRelaySchemaV8Models(db *gorm.DB) error {
	return db.AutoMigrate(relaySchemaV8Models()...)
}

func migrateRelaySchemaV8PreviousCandidateCatalog(db *gorm.DB) error {
	if db == nil || db.Dialector.Name() != "postgres" {
		return nil
	}
	for _, statement := range []string{
		`ALTER TABLE public.prefill_groups DROP CONSTRAINT IF EXISTS idx_prefill_groups_name`,
		`DROP INDEX IF EXISTS public.idx_prefill_groups_name`,
	} {
		if err := db.Exec(statement).Error; err != nil {
			return errors.New("previous Relay candidate catalog could not be normalized")
		}
	}
	return nil
}

func migrateRelaySchemaV8SubscriptionPlan(db *gorm.DB) error {
	if db.Dialector.Name() == "sqlite" {
		return ensureSubscriptionPlanTableSQLiteWithDB(db)
	}
	return db.AutoMigrate(&SubscriptionPlan{})
}

// migrateRelaySchemaV8ProviderCostAllocationEvidence accepts only the exact
// clean v7 release. The enclosing migration transaction installs the v8
// runtime and download-edge privilege manifests after this DDL succeeds.
func migrateRelaySchemaV8ProviderCostAllocationEvidence(db *gorm.DB) error {
	if db == nil {
		return errors.New("Relay schema v8 provider cost evidence database is unavailable")
	}
	var state RelaySchemaState
	if err := db.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error; err != nil {
		return errors.New("Relay schema v8 provider cost evidence state is unavailable")
	}
	v7Catalog := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), relaySchemaV7FrozenVersion)
	v8Catalog := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), relaySchemaV8FrozenVersion)
	if state.CurrentVersion != relaySchemaV7FrozenVersion || state.TargetVersion != relaySchemaV8FrozenVersion ||
		state.State != RelaySchemaStateApplying || !state.Dirty || state.AttemptID == "" ||
		state.CurrentChecksum != relaySchemaV7FrozenChecksumSHA256 || state.TargetChecksum != relaySchemaV8FrozenChecksumSHA256 ||
		(v7Catalog != "" && state.CurrentCatalogSHA256 != v7Catalog) || state.TargetCatalogSHA256 != v8Catalog {
		return errors.New("Relay schema v8 provider cost evidence migration requires the exact v7 state")
	}
	if v7Catalog != "" {
		actualCatalog, err := relaySchemaCatalogFingerprintForRuntime(db, relaySchemaV7FrozenVersion)
		if err != nil || actualCatalog != v7Catalog {
			return errors.New("Relay schema v8 provider cost evidence migration requires the exact v7 catalog")
		}
	}
	if db.Migrator().HasTable(&PlatformProviderCostAllocationEvidence{}) {
		return errors.New("Relay schema v8 provider cost evidence table already exists")
	}
	return MigratePlatformProviderCostEvidenceStorageV8WithDB(db)
}

// MigratePlatformProviderCostEvidenceStorageV8WithDB creates the v8-owned
// immutable allocation ledger and its database-enforced mutation guards.
func MigratePlatformProviderCostEvidenceStorageV8WithDB(db *gorm.DB) error {
	if db == nil {
		return errors.New("provider cost evidence database is unavailable")
	}
	if err := db.AutoMigrate(&PlatformProviderCostAllocationEvidence{}); err != nil {
		return err
	}
	if db.Dialector.Name() == "postgres" {
		for _, statement := range []string{
			`ALTER TABLE platform_provider_cost_allocation_evidence DROP CONSTRAINT IF EXISTS platform_provider_cost_allocation_evidence_outcome_id_key`,
			`ALTER TABLE platform_provider_cost_allocation_evidence DROP CONSTRAINT IF EXISTS platform_provider_cost_allocation_evidence_relay_job_id_key`,
			`CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_provider_cost_allocation_evidence_outcome_id ON platform_provider_cost_allocation_evidence (outcome_id)`,
			`CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_provider_cost_allocation_evidence_relay_job_id ON platform_provider_cost_allocation_evidence (relay_job_id)`,
		} {
			if err := db.Exec(statement).Error; err != nil {
				return err
			}
		}
	}
	return installPlatformProviderCostEvidenceAppendOnlyGuardsV8(db)
}

func installPlatformProviderCostEvidenceAppendOnlyGuardsV8(db *gorm.DB) error {
	const table = "platform_provider_cost_allocation_evidence"
	switch db.Dialector.Name() {
	case "postgres":
		for _, statement := range []string{
			`CREATE OR REPLACE FUNCTION reject_platform_relay_append_only_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'platform Relay event tables are append-only';
END;
$$ LANGUAGE plpgsql`,
			`DROP TRIGGER IF EXISTS trg_platform_provider_cost_allocation_evidence_no_mutation ON platform_provider_cost_allocation_evidence`,
			`CREATE TRIGGER trg_platform_provider_cost_allocation_evidence_no_mutation BEFORE UPDATE OR DELETE ON platform_provider_cost_allocation_evidence FOR EACH ROW EXECUTE FUNCTION reject_platform_relay_append_only_mutation()`,
			`DROP TRIGGER IF EXISTS trg_platform_provider_cost_allocation_evidence_no_truncate ON platform_provider_cost_allocation_evidence`,
			`CREATE TRIGGER trg_platform_provider_cost_allocation_evidence_no_truncate BEFORE TRUNCATE ON platform_provider_cost_allocation_evidence FOR EACH STATEMENT EXECUTE FUNCTION reject_platform_relay_append_only_mutation()`,
		} {
			if err := db.Exec(statement).Error; err != nil {
				return err
			}
		}
	case "mysql":
		for _, operation := range []string{"UPDATE", "DELETE"} {
			trigger := "trg_" + table + "_no_" + strings.ToLower(operation)
			if err := db.Exec("DROP TRIGGER IF EXISTS " + trigger).Error; err != nil {
				return err
			}
			if err := db.Exec(fmt.Sprintf(
				"CREATE TRIGGER %s BEFORE %s ON %s FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'platform Relay event tables are append-only'",
				trigger, operation, table,
			)).Error; err != nil {
				return err
			}
		}
	default:
		for _, operation := range []string{"UPDATE", "DELETE"} {
			trigger := "trg_" + table + "_no_" + strings.ToLower(operation)
			statement := fmt.Sprintf(
				"CREATE TRIGGER IF NOT EXISTS %s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, 'platform Relay event tables are append-only'); END",
				trigger, operation, table,
			)
			if err := db.Exec(statement).Error; err != nil {
				return err
			}
		}
	}
	return nil
}
