package model

import (
	"errors"
	"fmt"

	"gorm.io/gorm"
)

type relaySchemaV7Step struct {
	ID string
	Up func(*gorm.DB) error
}

// relaySchemaV7BootstrapSteps is the complete fresh-v7 snapshot. Historical
// v1-v6 migrations are never replayed or reinterpreted by this bootstrap.
func relaySchemaV7BootstrapSteps() []relaySchemaV7Step {
	return []relaySchemaV7Step{
		{ID: "subscription-plan-price-decimal-v7", Up: migrateSubscriptionPlanPriceAmountWithDB},
		{ID: "token-model-limits-text-v7", Up: migrateTokenModelLimitsToTextWithDB},
		{ID: "channel-cost-digest-varchar-v7", Up: migratePlatformChannelCostDocumentDigestStorageWithDB},
		{ID: "gorm-model-bootstrap-v7", Up: migrateRelaySchemaV7Models},
		{ID: "terminal-platform-native-result-url-scrub-v7", Up: scrubPlatformGenerationTerminalNativeResultURLsV4},
		{ID: "previous-candidate-catalog-normalization-v7", Up: migrateRelaySchemaV7PreviousCandidateCatalog},
		{ID: "provider-task-credential-vault-v7", Up: MigrateProviderCredentialVaultStorageWithDB},
		{ID: "provider-channel-credential-vault-v7", Up: migrateProviderChannelCredentialVaultStorageV3WithDB},
		{ID: "artifact-intent-v7", Up: MigratePlatformArtifactUploadIntentStorageWithDB},
		{ID: "shared-account-state-v7", Up: MigratePlatformGenerationProviderAccountStateWithDB},
		{ID: "reconciliation-append-only-v7", Up: MigratePlatformGenerationReconciliationStorageWithDB},
		{ID: "callback-redrive-append-only-v7", Up: MigratePlatformGenerationCallbackOperationsStorageWithDB},
		{ID: "channel-control-artifact-content-types-v7", Up: MigratePlatformChannelControlStorageV7WithDB},
		{ID: "provider-cost-monitor-guards-v7", Up: MigratePlatformProviderMonitorAndCostStorageWithDB},
		{ID: "channel-cost-personal-scope-v7", Up: MigratePlatformChannelCostPersonalScopeV7WithDB},
		{ID: "auth-version-backfill-v7", Up: InitializeUserAuthVersionsWithDB},
		{ID: "external-identity-backfill-v7", Up: InitializeExternalIdentityClaimsWithDB},
		{ID: "subscription-plan-v7", Up: migrateRelaySchemaV7SubscriptionPlan},
		{ID: "retired-option-migration-v7", Up: migrateRetiredFrontendOptionsStrictWithDB},
		{ID: "generation-route-release-binding-guards-v7", Up: installPlatformGenerationRouteBindingGuardsV4},
		{ID: "runtime-dml-privilege-manifest-v7", Up: ApplyRelayDatabasePrivilegeManifestWithDB},
		{ID: "download-edge-rls-v7", Up: MigratePlatformDownloadEdgeIsolationWithDB},
		{ID: "download-edge-dml-privilege-manifest-v7", Up: ApplyRelayDownloadEdgeDatabasePrivilegeManifestWithDB},
	}
}

func migrateRelaySchemaV7Bootstrap(db *gorm.DB) error {
	for _, step := range relaySchemaV7BootstrapSteps() {
		if err := step.Up(db); err != nil {
			return fmt.Errorf("Relay schema v7 bootstrap step %s failed: %w", step.ID, err)
		}
	}
	return nil
}

func migrateRelaySchemaV7Models(db *gorm.DB) error {
	return db.AutoMigrate(relaySchemaV7Models()...)
}

func migrateRelaySchemaV7PreviousCandidateCatalog(db *gorm.DB) error {
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

func migrateRelaySchemaV7SubscriptionPlan(db *gorm.DB) error {
	if db.Dialector.Name() == "sqlite" {
		return ensureSubscriptionPlanTableSQLiteWithDB(db)
	}
	return db.AutoMigrate(&SubscriptionPlan{})
}

// migrateRelaySchemaV7ChannelTestArtifactContentTypes accepts only the exact
// clean v6 release. It widens the channel-test artifact evidence and adds the
// personal provider-cost scope without modifying historical receipts.
func migrateRelaySchemaV7ChannelTestArtifactContentTypes(db *gorm.DB) error {
	if db == nil {
		return errors.New("Relay schema v7 channel-test artifact evidence database is unavailable")
	}
	var state RelaySchemaState
	if err := db.Where("id = ?", relaySchemaStateSingletonID).First(&state).Error; err != nil {
		return errors.New("Relay schema v7 channel-test artifact evidence state is unavailable")
	}
	v6Catalog := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), relaySchemaV6FrozenVersion)
	v7Catalog := relaySchemaExpectedCatalogForRuntime(db.Dialector.Name(), relaySchemaV7FrozenVersion)
	if state.CurrentVersion != relaySchemaV6FrozenVersion || state.TargetVersion != relaySchemaV7FrozenVersion ||
		state.State != RelaySchemaStateApplying || !state.Dirty || state.AttemptID == "" ||
		state.CurrentChecksum != relaySchemaV6FrozenChecksumSHA256 || state.TargetChecksum != relaySchemaV7FrozenChecksumSHA256 ||
		(v6Catalog != "" && state.CurrentCatalogSHA256 != v6Catalog) || state.TargetCatalogSHA256 != v7Catalog {
		return errors.New("Relay schema v7 channel-test artifact evidence migration requires the exact v6 state")
	}
	if v6Catalog != "" {
		actualCatalog, err := relaySchemaCatalogFingerprintForRuntime(db, relaySchemaV6FrozenVersion)
		if err != nil || actualCatalog != v6Catalog {
			return errors.New("Relay schema v7 channel-test artifact evidence migration requires the exact v6 catalog")
		}
	}
	if err := MigratePlatformChannelControlStorageV7WithDB(db); err != nil {
		return err
	}
	return MigratePlatformChannelCostPersonalScopeV7WithDB(db)
}
