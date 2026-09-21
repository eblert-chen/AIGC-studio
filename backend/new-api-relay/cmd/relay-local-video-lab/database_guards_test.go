//go:build relay_local_video_lab

package main

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestLabRouteGuardInstallerRequiresExactIsolatedIdentity(t *testing.T) {
	config := labTestConfig(t, "mock")
	directory := labTestDatabase(t, config)
	for _, test := range []struct {
		name, directory, stateID, username, envName, envValue string
	}{
		{"relative_database", "relative", config.StateID, config.Principal.UserName, "", ""},
		{"different_database", filepath.Join(directory, "other"), config.StateID, config.Principal.UserName, "", ""},
		{"short_state", directory, "mock-01", config.Principal.UserName, "", ""},
		{"invalid_mode", directory, "production-" + strings.Repeat("1", 64), config.Principal.UserName, "", ""},
		{"wrong_native_identity", directory, config.StateID, "unrelated-user", "", ""},
		{"wrong_namespace", directory, config.StateID, config.Principal.UserName, "RELAY_COMPAT_DELAY_QUEUE_NAMESPACE", "another-state"},
		{"protected_environment", directory, config.StateID, config.Principal.UserName, "APP_ENV", "production"},
		{"missing_environment", directory, config.StateID, config.Principal.UserName, "DEPLOYMENT_ENV", ""},
	} {
		t.Run(test.name, func(t *testing.T) {
			if test.envName != "" {
				t.Setenv(test.envName, test.envValue)
			}
			require.Error(t, model.InstallLocalVideoLabRouteBindingGuardsWithDB(model.DB, test.directory, test.stateID, test.username))
			require.NoError(t, validateLabDatabaseGuards(), "rejected scope must not drop any existing guard")
		})
	}
	require.Error(t, model.InstallLocalVideoLabRouteBindingGuardsWithDB(nil, directory, config.StateID, config.Principal.UserName))
	require.NoError(t, model.InstallLocalVideoLabRouteBindingGuardsWithDB(model.DB, directory, config.StateID, config.Principal.UserName))
}

func TestLabDatabasePreflightInspectsActualV4GuardsWithoutRewritingRows(t *testing.T) {
	config := labTestConfig(t, "mock")
	directory := labTestDatabase(t, config)
	var originalSQL []string
	require.NoError(t, model.DB.Raw("SELECT sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name").Scan(&originalSQL).Error)
	require.Len(t, originalSQL, 40)
	var beforeChanges int64
	require.NoError(t, model.DB.Raw("SELECT total_changes()").Scan(&beforeChanges).Error)
	// Only this disposable test database is intentionally made incomplete. The
	// executable must fail its preflight, and the existing production installer
	// must restore the real trigger without repairing or rewriting historical rows.
	require.NoError(t, model.DB.Exec("DROP TRIGGER trg_platform_generation_route_binding_v4_update").Error)
	require.ErrorContains(t, validateLabDatabaseGuards(), "platform_generation_provider_routes")
	require.NoError(t, model.InstallLocalVideoLabRouteBindingGuardsWithDB(model.DB, directory, config.StateID, config.Principal.UserName))
	require.NoError(t, validateLabDatabaseGuards())
	require.NoError(t, model.InstallLocalVideoLabRouteBindingGuardsWithDB(model.DB, directory, config.StateID, config.Principal.UserName))
	var afterChanges int64
	require.NoError(t, model.DB.Raw("SELECT total_changes()").Scan(&afterChanges).Error)
	assert.Equal(t, beforeChanges, afterChanges, "installing or re-installing guards performs no row writes")
	var currentSQL []string
	require.NoError(t, model.DB.Raw("SELECT sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name").Scan(&currentSQL).Error)
	assert.Equal(t, originalSQL, currentSQL)

	var route model.PlatformGenerationProviderRoute
	require.NoError(t, model.DB.Where("model = ? AND mode = ?", "minimax-h3", "text_to_video").First(&route).Error)
	incomplete := route
	incomplete.ID = 0
	incomplete.Mode = "invalid-lab-test-mode"
	incomplete.ModelReleaseRevision = ""
	require.ErrorContains(t, model.DB.Create(&incomplete).Error, "generation route release binding must be all-or-none")
	var count int64
	require.NoError(t, model.DB.Model(&model.PlatformGenerationProviderRoute{}).Where("mode = ?", incomplete.Mode).Count(&count).Error)
	assert.Zero(t, count)
}
