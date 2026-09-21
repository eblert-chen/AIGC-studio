package model

import (
	"errors"
	"fmt"

	"gorm.io/gorm"
)

type relaySchemaV5Step struct {
	ID string
	Up func(*gorm.DB) error
}

// relaySchemaV5BootstrapSteps is a complete fresh-v5 snapshot. The only
// catalog delta from v4 is the channel-test receipt guard's closed diagnostic
// taxonomy; the route binding, append-only ledgers, RLS and least-privilege
// manifests remain part of the same independently frozen bootstrap.
func relaySchemaV5BootstrapSteps() []relaySchemaV5Step {
	return []relaySchemaV5Step{
		{ID: "subscription-plan-price-decimal-v5", Up: migrateSubscriptionPlanPriceAmountWithDB},
		{ID: "token-model-limits-text-v5", Up: migrateTokenModelLimitsToTextWithDB},
		{ID: "channel-cost-digest-varchar-v5", Up: migratePlatformChannelCostDocumentDigestStorageWithDB},
		{ID: "gorm-model-bootstrap-v5", Up: migrateRelaySchemaV5Models},
		{ID: "terminal-platform-native-result-url-scrub-v5", Up: scrubPlatformGenerationTerminalNativeResultURLsV4},
		{ID: "previous-candidate-catalog-normalization-v5", Up: migrateRelaySchemaV5PreviousCandidateCatalog},
		{ID: "provider-task-credential-vault-v5", Up: MigrateProviderCredentialVaultStorageWithDB},
		{ID: "provider-channel-credential-vault-v5", Up: migrateProviderChannelCredentialVaultStorageV3WithDB},
		{ID: "artifact-intent-v5", Up: MigratePlatformArtifactUploadIntentStorageWithDB},
		{ID: "shared-account-state-v5", Up: MigratePlatformGenerationProviderAccountStateWithDB},
		{ID: "reconciliation-append-only-v5", Up: MigratePlatformGenerationReconciliationStorageWithDB},
		{ID: "callback-redrive-append-only-v5", Up: MigratePlatformGenerationCallbackOperationsStorageWithDB},
		{ID: "channel-control-guards-v5", Up: MigratePlatformChannelControlStorageWithDB},
		{ID: "provider-cost-monitor-guards-v5", Up: MigratePlatformProviderMonitorAndCostStorageWithDB},
		{ID: "auth-version-backfill-v5", Up: InitializeUserAuthVersionsWithDB},
		{ID: "external-identity-backfill-v5", Up: InitializeExternalIdentityClaimsWithDB},
		{ID: "subscription-plan-v5", Up: migrateRelaySchemaV5SubscriptionPlan},
		{ID: "retired-option-migration-v5", Up: migrateRetiredFrontendOptionsStrictWithDB},
		{ID: "generation-route-release-binding-guards-v5", Up: installPlatformGenerationRouteBindingGuardsV4},
		{ID: "channel-test-diagnostic-taxonomy-v5", Up: installPlatformChannelControlDiagnosticTaxonomyV5WithDB},
		{ID: "runtime-dml-privilege-manifest-v5", Up: ApplyRelayDatabasePrivilegeManifestWithDB},
		{ID: "download-edge-rls-v5", Up: MigratePlatformDownloadEdgeIsolationWithDB},
		{ID: "download-edge-dml-privilege-manifest-v5", Up: ApplyRelayDownloadEdgeDatabasePrivilegeManifestWithDB},
	}
}

func migrateRelaySchemaV5Bootstrap(db *gorm.DB) error {
	for _, step := range relaySchemaV5BootstrapSteps() {
		if err := step.Up(db); err != nil {
			return fmt.Errorf("Relay schema v5 bootstrap step %s failed: %w", step.ID, err)
		}
	}
	return nil
}

func migrateRelaySchemaV5Models(db *gorm.DB) error {
	return db.AutoMigrate(relaySchemaV5Models()...)
}

func migrateRelaySchemaV5PreviousCandidateCatalog(db *gorm.DB) error {
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

func migrateRelaySchemaV5SubscriptionPlan(db *gorm.DB) error {
	if db.Dialector.Name() == "sqlite" {
		return ensureSubscriptionPlanTableSQLiteWithDB(db)
	}
	return db.AutoMigrate(&SubscriptionPlan{})
}

// migrateRelaySchemaV5ChannelTestDiagnosticTaxonomy accepts only the exact
// clean v4 release and replaces one PostgreSQL guard function. It does not
// rewrite an existing receipt and does not add a column where raw provider
// material could accidentally be stored.
func migrateRelaySchemaV5ChannelTestDiagnosticTaxonomy(db *gorm.DB) error {
	if db == nil {
		return errors.New("Relay schema v5 channel-test diagnostic database is unavailable")
	}
	var state RelaySchemaState
	if err := db.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error; err != nil {
		return errors.New("Relay schema v5 channel-test diagnostic state is unavailable")
	}
	v4Catalog := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), relaySchemaV4FrozenVersion)
	v5Catalog := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), relaySchemaV5FrozenVersion)
	if state.CurrentVersion != relaySchemaV4FrozenVersion || state.TargetVersion != relaySchemaV5FrozenVersion ||
		state.State != RelaySchemaStateApplying || !state.Dirty || state.AttemptID == "" ||
		state.CurrentChecksum != relaySchemaV4FrozenChecksumSHA256 || state.TargetChecksum != relaySchemaV5FrozenChecksumSHA256 ||
		(v4Catalog != "" && state.CurrentCatalogSHA256 != v4Catalog) || state.TargetCatalogSHA256 != v5Catalog {
		return errors.New("Relay schema v5 channel-test diagnostic migration requires the exact v4 state")
	}
	if v4Catalog != "" {
		actualCatalog, err := relaySchemaCatalogFingerprintForRuntime(db, relaySchemaV4FrozenVersion)
		if err != nil || actualCatalog != v4Catalog {
			return errors.New("Relay schema v5 channel-test diagnostic migration requires the exact v4 catalog")
		}
	}
	return installPlatformChannelControlDiagnosticTaxonomyV5WithDB(db)
}
