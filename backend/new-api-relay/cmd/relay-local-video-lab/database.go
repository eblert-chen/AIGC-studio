//go:build relay_local_video_lab

package main

import (
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/localvideoconfig"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func initializeLabDatabase(directory string, config localvideoconfig.Config) (func(), error) {
	common.SQLitePath = filepath.Join(directory, "relay.sqlite") + "?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)"
	common.MemoryCacheEnabled, common.BatchUpdateEnabled = false, false
	if err := model.InitDB(); err != nil {
		return nil, errors.New("could not open the isolated Relay SQLite database")
	}
	model.DB.Logger = logger.Default.LogMode(logger.Silent)
	model.LOG_DB = model.DB
	database, err := model.DB.DB()
	if err != nil {
		return nil, errors.New("could not open isolated Relay database connection")
	}
	database.SetMaxOpenConns(1)
	database.SetMaxIdleConns(1)
	// SQLite serializes writers. GORM's shared prepared-statement cache can
	// deadlock a one-connection pool when a worker holds a transaction while a
	// concurrent reader prepares the same statement. This isolated connection
	// uses the plain SQL pool; production PostgreSQL initialization is unchanged.
	if prepared, ok := model.DB.ConnPool.(*gorm.PreparedStmtDB); ok {
		prepared.Close()
	}
	model.DB.Config.PrepareStmt = false
	model.DB.Config.ConnPool = database
	model.DB.Statement.ConnPool = database
	cleanup := func() { _ = database.Close() }
	// This schema belongs only to the explicit lab-state SQLite file. No frozen
	// production catalog, migration identity, or production database is touched.
	if err := model.DB.AutoMigrate(
		&model.User{}, &model.Token{}, &model.Channel{}, &model.Ability{}, &model.Option{}, &model.Log{},
		&model.Task{}, &model.ProviderCredentialVersion{}, &model.ProviderChannelCredentialSetVersion{},
		&model.PlatformGenerationJob{}, &model.PlatformGenerationOutbox{}, &model.PlatformArtifactUploadIntent{},
		&model.PlatformGenerationProviderRoute{}, &model.PlatformGenerationProviderAccountState{},
		&model.PlatformGenerationRouteAdmission{}, &model.PlatformGenerationCallbackDelivery{}, &labCallbackRecord{},
	); err != nil {
		cleanup()
		return nil, errors.New("could not initialize isolated lab tables")
	}
	for _, migration := range []struct {
		name string
		run  func(*gorm.DB) error
	}{
		{"provider credential vault", model.MigrateProviderCredentialVaultStorageWithDB},
		{"channel credential vault", model.MigrateProviderChannelCredentialVaultStorageWithDB},
		{"generation reconciliation", model.MigratePlatformGenerationReconciliationStorageWithDB},
		{"callback operations", model.MigratePlatformGenerationCallbackOperationsStorageWithDB},
		{"provider account state", model.MigratePlatformGenerationProviderAccountStateWithDB},
		// v7 bootstraps its own base table and preserves the complete lifecycle
		// guards. Re-running the historical v6 AutoMigrate over an existing v7
		// table rewrites its older projection and fails with SQLite invalid DDL.
		{"channel artifacts v7", model.MigratePlatformChannelControlStorageV7WithDB},
		{"provider monitor and cost", model.MigratePlatformProviderMonitorAndCostStorageWithDB},
		{"personal cost scope v7", model.MigratePlatformChannelCostPersonalScopeV7WithDB},
	} {
		if err := migration.run(model.DB); err != nil {
			cleanup()
			return nil, fmt.Errorf("could not install the lab database's existing immutable guards (%s): %w", migration.name, err)
		}
	}
	if err := seedLabNativePrincipalAndChannels(config); err != nil {
		cleanup()
		return nil, err
	}
	if err := model.InstallLocalVideoLabRouteBindingGuardsWithDB(model.DB, directory, config.StateID, config.Principal.UserName); err != nil {
		cleanup()
		return nil, err
	}
	if err := validateLabDatabaseGuards(); err != nil {
		cleanup()
		return nil, err
	}
	model.InitOptionMap()
	// The ordinary native task parser still requires a model-price entry before
	// its Platform-owned billing sentinel takes effect. These nonzero, local-only
	// preflight values are NOT provider costs or customer prices. Only the private
	// Platform bridge is registered and its real billing guard must keep all native
	// balances/logs unchanged; the regression test verifies that explicitly.
	nativePreflightPrices := make(map[string]float64, len(config.Models))
	for _, item := range config.Models {
		nativePreflightPrices[item.PublicModelID] = 1
	}
	prices, err := common.Marshal(nativePreflightPrices)
	if err != nil {
		cleanup()
		return nil, errors.New("could not encode local native preflight prices")
	}
	if err := model.UpdateOption("ModelPrice", string(prices)); err != nil {
		cleanup()
		return nil, errors.New("could not initialize local native preflight prices")
	}
	return cleanup, nil
}

func validateLabDatabaseGuards() error {
	// Check actual persisted guards after the installers, not just their return
	// values. These are the existing production guard names, not lab substitutes.
	required := map[string][]string{
		"channels":                                    {"trg_channels_control_revision", "trg_channels_provider_credential_storage_insert", "trg_channels_provider_credential_storage_update"},
		"tasks":                                       {"trg_tasks_no_plaintext_provider_credential_insert", "trg_tasks_no_plaintext_provider_credential_update"},
		"platform_generation_jobs":                    {"trg_platform_generation_jobs_no_plaintext_provider_credential_insert", "trg_platform_generation_jobs_no_plaintext_provider_credential_update"},
		"platform_generation_provider_routes":         {"trg_platform_generation_route_binding_v4_insert", "trg_platform_generation_route_binding_v4_update"},
		"provider_credential_versions":                {"trg_provider_credential_versions_no_delete", "trg_provider_credential_versions_no_update"},
		"provider_channel_credential_set_versions":    {"trg_provider_channel_credential_set_versions_no_delete", "trg_provider_channel_credential_set_versions_no_update"},
		"platform_generation_reconciliation_events":   {"trg_platform_generation_reconciliation_no_delete", "trg_platform_generation_reconciliation_no_update"},
		"platform_generation_callback_redrive_events": {"trg_platform_generation_callback_redrive_no_delete", "trg_platform_generation_callback_redrive_no_update"},
		"platform_channel_control_operations":         {"trg_platform_channel_control_v7_delete", "trg_platform_channel_control_v7_insert", "trg_platform_channel_control_v7_update"},
		"platform_channel_cost_events":                {"trg_platform_channel_cost_billing_scope_v7_insert", "trg_platform_channel_cost_billing_scope_v7_update", "trg_platform_channel_cost_events_no_delete", "trg_platform_channel_cost_events_no_update"},
	}
	for _, table := range []string{"platform_download_completion_events", "platform_download_completion_proofs", "platform_operations_snapshot_events", "platform_provider_alert_events", "platform_provider_contract_rates", "platform_provider_retirement_acknowledgements", "platform_provider_terminal_outcomes", "platform_task_stage_events"} {
		required[table] = []string{"trg_" + table + "_no_delete", "trg_" + table + "_no_update"}
	}
	for table, names := range required {
		if !model.DB.Migrator().HasTable(table) {
			return fmt.Errorf("local database prerequisite table is missing: %s", table)
		}
		var count int64
		if err := model.DB.Raw("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ? AND name IN ? AND sql IS NOT NULL", table, names).Scan(&count).Error; err != nil || count != int64(len(names)) {
			return fmt.Errorf("local database immutable guards are missing: %s", table)
		}
	}
	return nil
}

func seedLabNativePrincipalAndChannels(config localvideoconfig.Config) error {
	principal := config.Principal
	var user model.User
	err := model.DB.Where("username = ?", principal.UserName).First(&user).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		user = model.User{Username: principal.UserName, DisplayName: "Local video lab", Password: "!no-interactive-login", Role: common.RoleCommonUser, Status: common.UserStatusEnabled, Group: "default"}
		if err := model.DB.Create(&user).Error; err != nil {
			return errors.New("could not provision the local native service user")
		}
	} else if err != nil || user.Role != common.RoleCommonUser || user.Status != common.UserStatusEnabled || user.UsedQuota != 0 {
		return errors.New("local native service user binding changed")
	}
	key := strings.TrimPrefix(principal.UpstreamToken, "sk-")
	var token model.Token
	err = model.DB.Where("key = ?", key).First(&token).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		token = model.Token{UserId: user.Id, Key: key, Name: "local-video-platform-external", Status: common.TokenStatusEnabled, UnlimitedQuota: true, ExpiredTime: -1, Group: "default", CreatedTime: time.Now().UTC().Unix()}
		if err := model.DB.Create(&token).Error; err != nil {
			return errors.New("could not provision the local native service token")
		}
	} else if err != nil || token.UserId != user.Id || token.Status != common.TokenStatusEnabled || !token.UnlimitedQuota || token.UsedQuota != 0 {
		return errors.New("local native token binding changed")
	}
	for _, declared := range config.Channels {
		channel, err := model.GetChannelById(declared.ID, true)
		if errors.Is(err, gorm.ErrRecordNotFound) {
			baseURL := declared.BaseURL
			channel = &model.Channel{
				Id: declared.ID, Type: declared.Type, Name: declared.Name, Key: declared.Key, BaseURL: &baseURL,
				Models: strings.Join(declared.ModelIDs, ","), Status: common.ChannelStatusEnabled, Group: "default", CreatedTime: time.Now().UTC().Unix(),
			}
			if err := channel.Insert(); err != nil {
				return errors.New("could not provision a local native channel")
			}
			continue
		}
		if err != nil || channel.Type != declared.Type || channel.BaseURL == nil || *channel.BaseURL != declared.BaseURL || channel.Models != strings.Join(declared.ModelIDs, ",") {
			return errors.New("local native channel binding changed")
		}
		key, err := channel.GetKeyAt(0)
		if err != nil || !labExactSecret(key, declared.Key) {
			return errors.New("local native channel credential changed; use a new state directory")
		}
	}
	return nil
}
