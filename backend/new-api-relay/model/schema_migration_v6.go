package model

import (
	"errors"
	"fmt"

	"gorm.io/gorm"
)

type relaySchemaV6Step struct {
	ID string
	Up func(*gorm.DB) error
}

// relaySchemaV6BootstrapSteps is the complete fresh-v6 snapshot. Historical
// v1-v5 migrations are never replayed or reinterpreted by this bootstrap.
func relaySchemaV6BootstrapSteps() []relaySchemaV6Step {
	return []relaySchemaV6Step{
		{ID: "subscription-plan-price-decimal-v6", Up: migrateSubscriptionPlanPriceAmountWithDB},
		{ID: "token-model-limits-text-v6", Up: migrateTokenModelLimitsToTextWithDB},
		{ID: "channel-cost-digest-varchar-v6", Up: migratePlatformChannelCostDocumentDigestStorageWithDB},
		{ID: "gorm-model-bootstrap-v6", Up: migrateRelaySchemaV6Models},
		{ID: "terminal-platform-native-result-url-scrub-v6", Up: scrubPlatformGenerationTerminalNativeResultURLsV4},
		{ID: "previous-candidate-catalog-normalization-v6", Up: migrateRelaySchemaV6PreviousCandidateCatalog},
		{ID: "provider-task-credential-vault-v6", Up: MigrateProviderCredentialVaultStorageWithDB},
		{ID: "provider-channel-credential-vault-v6", Up: migrateProviderChannelCredentialVaultStorageV3WithDB},
		{ID: "artifact-intent-v6", Up: MigratePlatformArtifactUploadIntentStorageWithDB},
		{ID: "shared-account-state-v6", Up: MigratePlatformGenerationProviderAccountStateWithDB},
		{ID: "reconciliation-append-only-v6", Up: MigratePlatformGenerationReconciliationStorageWithDB},
		{ID: "callback-redrive-append-only-v6", Up: MigratePlatformGenerationCallbackOperationsStorageWithDB},
		{ID: "channel-control-submission-lifecycle-v6", Up: MigratePlatformChannelControlStorageV6WithDB},
		{ID: "provider-cost-monitor-guards-v6", Up: MigratePlatformProviderMonitorAndCostStorageWithDB},
		{ID: "auth-version-backfill-v6", Up: InitializeUserAuthVersionsWithDB},
		{ID: "external-identity-backfill-v6", Up: InitializeExternalIdentityClaimsWithDB},
		{ID: "subscription-plan-v6", Up: migrateRelaySchemaV6SubscriptionPlan},
		{ID: "retired-option-migration-v6", Up: migrateRetiredFrontendOptionsStrictWithDB},
		{ID: "generation-route-release-binding-guards-v6", Up: installPlatformGenerationRouteBindingGuardsV4},
		{ID: "runtime-dml-privilege-manifest-v6", Up: ApplyRelayDatabasePrivilegeManifestWithDB},
		{ID: "download-edge-rls-v6", Up: MigratePlatformDownloadEdgeIsolationWithDB},
		{ID: "download-edge-dml-privilege-manifest-v6", Up: ApplyRelayDownloadEdgeDatabasePrivilegeManifestWithDB},
	}
}

func migrateRelaySchemaV6Bootstrap(db *gorm.DB) error {
	for _, step := range relaySchemaV6BootstrapSteps() {
		if err := step.Up(db); err != nil {
			return fmt.Errorf("Relay schema v6 bootstrap step %s failed: %w", step.ID, err)
		}
	}
	return nil
}

func migrateRelaySchemaV6Models(db *gorm.DB) error {
	return db.AutoMigrate(relaySchemaV6Models()...)
}

func migrateRelaySchemaV6PreviousCandidateCatalog(db *gorm.DB) error {
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

func migrateRelaySchemaV6SubscriptionPlan(db *gorm.DB) error {
	if db.Dialector.Name() == "sqlite" {
		return ensureSubscriptionPlanTableSQLiteWithDB(db)
	}
	return db.AutoMigrate(&SubscriptionPlan{})
}

// migrateRelaySchemaV6ChannelTestLifecycle accepts only the exact clean v5
// state/catalog and applies the v6 columns, indexes, constraints and guards.
func migrateRelaySchemaV6ChannelTestLifecycle(db *gorm.DB) error {
	if db == nil {
		return errors.New("Relay schema v6 channel-test lifecycle database is unavailable")
	}
	var state RelaySchemaState
	if err := db.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error; err != nil {
		return errors.New("Relay schema v6 channel-test lifecycle state is unavailable")
	}
	v5Catalog := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), relaySchemaV5FrozenVersion)
	v6Catalog := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), relaySchemaV6FrozenVersion)
	if state.CurrentVersion != relaySchemaV5FrozenVersion || state.TargetVersion != relaySchemaV6FrozenVersion ||
		state.State != RelaySchemaStateApplying || !state.Dirty || state.AttemptID == "" ||
		state.CurrentChecksum != relaySchemaV5FrozenChecksumSHA256 || state.TargetChecksum != relaySchemaV6FrozenChecksumSHA256 ||
		(v5Catalog != "" && state.CurrentCatalogSHA256 != v5Catalog) || state.TargetCatalogSHA256 != v6Catalog {
		return errors.New("Relay schema v6 channel-test lifecycle migration requires the exact v5 state")
	}
	if v5Catalog != "" {
		actualCatalog, err := relaySchemaCatalogFingerprintForRuntime(db, relaySchemaV5FrozenVersion)
		if err != nil || actualCatalog != v5Catalog {
			return errors.New("Relay schema v6 channel-test lifecycle migration requires the exact v5 catalog")
		}
	}
	return MigratePlatformChannelControlStorageV6WithDB(db)
}
