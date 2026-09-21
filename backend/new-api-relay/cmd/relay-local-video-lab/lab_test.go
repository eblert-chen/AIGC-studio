//go:build relay_local_video_lab

package main

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/localvideoconfig"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func labTestConfig(t *testing.T, mode string) localvideoconfig.Config {
	t.Helper()
	options := localvideoconfig.Options{Mode: mode, Environment: "development", Namespace: "test-video-lab", Now: time.Now().UTC().Truncate(time.Second), CreatedBy: "lab-test", Reason: "Isolated package regression", RuntimeSeed: bytes.Repeat([]byte{23}, 32), CallbackURL: "http://127.0.0.1:3000/lab/callback", ModelIDs: []string{"minimax-h3"}}
	if mode == "live" {
		options.Keys.MiniMax = "test-only-not-a-real-provider-key"
	}
	config, err := localvideoconfig.Build(options)
	require.NoError(t, err)
	return config
}

func TestLabPrepareIsStablePrivateAndNetworkFree(t *testing.T) {
	options, err := parseLabOptions([]string{"--prepare", "--state-dir", t.TempDir(), "--models", "minimax-h3"}, io.Discard)
	require.NoError(t, err)
	var output bytes.Buffer
	require.NoError(t, runLab(context.Background(), options, &output))
	var summary labSummary
	require.NoError(t, common.Unmarshal(output.Bytes(), &summary))
	assert.Equal(t, "mock", summary.Mode)
	assert.Len(t, summary.ProbeRequests, 1)
	assert.Equal(t, "minimax-h3", summary.ProbeRequests[0].Body.PublicModelID)
	assert.False(t, summary.PaidCreateLocked)
	assert.NotContains(t, output.String(), localvideoconfig.MockMiniMaxProviderKey)
	privateBytes, err := readLabPrivateFile(filepath.Join(options.StateDirectory, "runtime-environment.json"), 1024*1024)
	require.NoError(t, err)
	var environment map[string]string
	require.NoError(t, common.Unmarshal(privateBytes, &environment))
	for _, name := range []string{"PLATFORM_LAB_RELAY_API_KEY", "PLATFORM_LAB_RELAY_OPERATIONS_TOKEN", "PLATFORM_LAB_RELAY_CALLBACK_SIGNING_SECRET", "PLATFORM_LAB_RELAY_UPSTREAM_TOKEN"} {
		require.NotEmpty(t, environment[name])
		assert.NotContains(t, output.String(), environment[name])
	}
	assert.NotContains(t, string(privateBytes), localvideoconfig.MockMiniMaxProviderKey)
	_, err = os.Stat(filepath.Join(options.StateDirectory, "relay.sqlite"))
	assert.True(t, os.IsNotExist(err), "prepare must never open the database")
	output.Reset()
	require.NoError(t, runLab(context.Background(), options, &output))
	var restarted labSummary
	require.NoError(t, common.Unmarshal(output.Bytes(), &restarted))
	assert.Equal(t, summary.StateID, restarted.StateID)
	assert.Equal(t, summary.ProbeRequests, restarted.ProbeRequests)
	options.Mode = "live"
	assert.ErrorContains(t, runLab(context.Background(), options, io.Discard), "separate lab state directory")
}

func TestLabOptionsFailClosed(t *testing.T) {
	base := []string{"--state-dir", filepath.Join(t.TempDir(), "private")}
	for _, test := range []struct {
		name string
		args []string
	}{
		{"network listener", []string{"--listen", "0.0.0.0:3000"}},
		{"invalid port", []string{"--listen", "127.0.0.1:70000"}},
		{"external Redis", []string{"--redis-url", "redis://cache.example.com:6379"}},
		{"public Relay", []string{"--public-base-url", "https://relay.example.com"}},
		{"mock key", []string{"--minimax-key-file", "/private/key"}},
		{"paid flag alone", []string{"--mode", "live", "--allow-paid-probe"}},
		{"live fixture", []string{"--mode", "live", "--fixture-manifest", "/fixtures/manifest.json"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			_, err := parseLabOptions(append(append([]string{}, base...), test.args...), io.Discard)
			assert.Error(t, err)
		})
	}
	options, err := parseLabOptions(append(base, "--container-lab", "--listen", "0.0.0.0:3000", "--redis-url", "redis://redis-lab:6379/0"), io.Discard)
	require.NoError(t, err)
	assert.Equal(t, "http://relay-lab:3000", options.PublicBaseURL)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	assert.ErrorContains(t, rejectProtectedLabEnvironment(), "production")
}

func TestLabStateRequiresExclusiveMarkedDirectory(t *testing.T) {
	options, err := parseLabOptions([]string{"--state-dir", t.TempDir()}, io.Discard)
	require.NoError(t, err)
	state, err := openLabState(options)
	require.NoError(t, err)
	t.Cleanup(state.close)
	_, err = openLabState(options)
	assert.ErrorContains(t, err, "locked")
	state.close()
	options.Namespace = "different-lab"
	_, err = openLabState(options)
	assert.ErrorContains(t, err, "separate lab state")
	options.StateDirectory = t.TempDir()
	require.NoError(t, writeLabPrivateFile(filepath.Join(options.StateDirectory, "unrelated-user-data"), []byte("untouched"), true))
	_, err = openLabState(options)
	assert.ErrorContains(t, err, "unmarked nonempty")
	payload, err := os.ReadFile(filepath.Join(options.StateDirectory, "unrelated-user-data"))
	require.NoError(t, err)
	assert.Equal(t, "untouched", string(payload))
}

func labTestDatabase(t *testing.T, config localvideoconfig.Config) string {
	t.Helper()
	environment, err := config.RuntimeEnvironment()
	require.NoError(t, err)
	for key, value := range environment {
		t.Setenv(key, value)
	}
	t.Setenv("SQL_DSN", "")
	t.Setenv("LOG_SQL_DSN", "")
	oldDB, oldLogDB, oldPath := model.DB, model.LOG_DB, common.SQLitePath
	t.Cleanup(func() { model.DB, model.LOG_DB, common.SQLitePath = oldDB, oldLogDB, oldPath })
	directory := t.TempDir()
	cleanup, err := initializeLabDatabase(directory, config)
	require.NoError(t, err)
	t.Cleanup(cleanup)
	require.NoError(t, service.SyncPlatformGenerationProviderRoutes())
	return directory
}

func TestLabDatabaseAndDiscoveryUseRealProductionContracts(t *testing.T) {
	config := labTestConfig(t, "mock")
	directory := labTestDatabase(t, config)
	options := labOptions{Mode: "mock", PublicBaseURL: "http://127.0.0.1:3000", StateDirectory: directory}
	state := &labState{Directory: directory, Manifest: labStateManifest{StateID: config.StateID}}
	engine, err := buildLabRouter(options, state, config, buildLabSummary(config, options), nil, nil)
	require.NoError(t, err)
	request := httptest.NewRequest(http.MethodGet, "/v1/models/minimax-h3", nil)
	request.Header.Set("X-Client-ID", config.Principal.ClientID)
	request.Header.Set("X-API-Key", config.Principal.APIKey)
	request.Header.Set("X-Request-ID", "lab-discovery-contract")
	response := httptest.NewRecorder()
	engine.ServeHTTP(response, request)
	require.Equal(t, http.StatusOK, response.Code, response.Body.String())
	assert.Contains(t, response.Body.String(), config.Models[0].CapabilityRevision)
	assert.NotContains(t, response.Body.String(), config.Channels[0].Key)
	var user model.User
	require.NoError(t, model.DB.Where("username = ?", config.Principal.UserName).First(&user).Error)
	assert.Zero(t, user.Quota)
	assert.Zero(t, user.UsedQuota)
	var routes int64
	require.NoError(t, model.DB.Model(&model.PlatformGenerationProviderRoute{}).Count(&routes).Error)
	assert.Equal(t, int64(3), routes)
	// Reopening the same native identity must not create a second account/token.
	require.NoError(t, seedLabNativePrincipalAndChannels(config))
	var tokens int64
	require.NoError(t, model.DB.Model(&model.Token{}).Count(&tokens).Error)
	assert.Equal(t, int64(1), tokens)
}

func TestLabDatabaseClosesAndReopensWithoutChangingImmutableEvidence(t *testing.T) {
	config := labTestConfig(t, "mock")
	directory := labTestDatabase(t, config)
	var credentialVersions []model.ProviderChannelCredentialSetVersion
	require.NoError(t, model.DB.Order("credential_set_version ASC").Find(&credentialVersions).Error)
	require.NotEmpty(t, credentialVersions)
	var triggersBefore []string
	require.NoError(t, model.DB.Raw("SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name").Scan(&triggersBefore).Error)
	require.NotEmpty(t, triggersBefore)
	firstConnection, err := model.DB.DB()
	require.NoError(t, err)
	require.NoError(t, firstConnection.Close())
	assert.Error(t, firstConnection.Ping(), "the test must really close the original database")
	cleanup, err := initializeLabDatabase(directory, config)
	require.NoError(t, err)
	t.Cleanup(cleanup)
	require.NoError(t, service.SyncPlatformGenerationProviderRoutes())
	secondConnection, err := model.DB.DB()
	require.NoError(t, err)
	assert.NotSame(t, firstConnection, secondConnection)
	var versionsAfter []model.ProviderChannelCredentialSetVersion
	require.NoError(t, model.DB.Order("credential_set_version ASC").Find(&versionsAfter).Error)
	assert.Equal(t, credentialVersions, versionsAfter)
	var triggersAfter []string
	require.NoError(t, model.DB.Raw("SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name").Scan(&triggersAfter).Error)
	assert.Equal(t, triggersBefore, triggersAfter)
	assert.Error(t, model.DB.Exec("UPDATE provider_channel_credential_set_versions SET credential_set_version = credential_set_version").Error)
	assert.Error(t, model.DB.Exec("DELETE FROM provider_channel_credential_set_versions").Error)
	assert.Error(t, model.DB.Exec("UPDATE channels SET key = 'forbidden-plaintext'").Error)
	require.NoError(t, validateLabDatabaseGuards())
	for _, column := range []string{"model_release_id", "model_release_revision", "model_release_capability_revision", "capability_profile_id", "capability_profile_revision", "capability_profile_snapshot"} {
		assert.ErrorContains(t, model.DB.Model(&model.PlatformGenerationProviderRoute{}).Where("model = ?", "minimax-h3").UpdateColumn(column, "invalid-after-real-reopen").Error,
			"generation route release binding is immutable", "guard must still enforce %s after closing and reopening SQLite", column)
	}
	var users, tokens int64
	require.NoError(t, model.DB.Model(&model.User{}).Count(&users).Error)
	require.NoError(t, model.DB.Model(&model.Token{}).Count(&tokens).Error)
	assert.Equal(t, int64(1), users)
	assert.Equal(t, int64(1), tokens)
}

func TestLabLiveBatchBudgetIsDurableAndNeverRefundsUnknown(t *testing.T) {
	config := labTestConfig(t, "live")
	directory := t.TempDir()
	approval := labPaidApproval{SchemaVersion: 1, StateID: config.StateID, OperationID: "approved-batch-001", ProviderModelIDs: []string{"MiniMax-H3"}, MaxProviderCreates: 2, ExpiresAt: time.Now().UTC().Add(time.Hour)}
	path := filepath.Join(directory, "approval.json")
	require.NoError(t, writeLabJSON(path, approval, true))
	options := labOptions{Mode: "live", AllowPaidProbe: true, PaidApprovalFile: path}
	paid, err := loadLabPaidAuthorization(options, config, directory)
	require.NoError(t, err)
	for _, idempotencyKey := range []string{"first-distinct-job", "second-distinct-job"} {
		request := httptest.NewRequest(http.MethodPost, "/v1/generations", strings.NewReader(`{"model":"minimax-h3"}`))
		request.Header.Set("Idempotency-Key", idempotencyKey)
		assert.True(t, labPaidInboundAllowed(request, paid), "the batch does not replace the real per-job idempotency key")
	}
	assert.Error(t, paid.consume("MiniMax-H3-Max"))
	require.NoError(t, paid.consume("MiniMax-H3"))
	// A lost upstream response has no refund API. Reopen the real on-disk budget.
	restarted, err := loadLabPaidAuthorization(options, config, directory)
	require.NoError(t, err)
	assert.Equal(t, 1, restarted.budget.UsedCreates)
	require.NoError(t, restarted.consume("MiniMax-H3"))
	assert.Error(t, restarted.consume("MiniMax-H3"))
	assert.False(t, labPaidInboundAllowed(httptest.NewRequest(http.MethodPost, "/v1/generations", strings.NewReader(`{"model":"minimax-h3-max"}`)), restarted))
	approval.MaxProviderCreates = 3
	require.NoError(t, writeLabJSON(path, approval, false))
	_, err = loadLabPaidAuthorization(options, config, directory)
	assert.ErrorContains(t, err, "changed")
	assert.Error(t, (*labPaidAuthorization)(nil).consume("MiniMax-H3"))
}
