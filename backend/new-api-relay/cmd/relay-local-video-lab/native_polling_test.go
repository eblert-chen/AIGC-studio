//go:build relay_local_video_lab

package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay"
	"github.com/QuantumNous/new-api/service"
	"github.com/alicebob/miniredis/v2"
	"github.com/go-redis/redis/v8"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestLabNativePOSTAutomaticallyPollsRealProviderReceiptToVerifiedTerminal(t *testing.T) {
	// The production worker starter is deliberately once-per-process, including
	// after Stop. A fresh executable, not resetting that production singleton or
	// weakening admission, isolates this scenario from the lost-POST test.
	const childEnvironment = "RELAY_LOCAL_VIDEO_LAB_NATIVE_POLL_TEST_CHILD"
	if os.Getenv(childEnvironment) != "1" {
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()
		command := exec.CommandContext(ctx, os.Args[0], "-test.run=^"+t.Name()+"$", "-test.v", "-test.timeout=45s")
		command.Env = append(os.Environ(), childEnvironment+"=1")
		output, err := command.CombinedOutput()
		require.NoError(t, err, "isolated real-native-poll regression failed:\n%s", output)
		t.Logf("isolated real-native-poll regression:\n%s", output)
		return
	}
	config := labTestConfig(t, "mock")
	directory := labTestDatabase(t, config)
	oldLimit, oldTimeout, oldFactory := constant.TaskQueryLimit, constant.TaskTimeoutMinutes, service.GetTaskAdaptorFunc
	t.Cleanup(func() {
		constant.TaskQueryLimit, constant.TaskTimeoutMinutes, service.GetTaskAdaptorFunc = oldLimit, oldTimeout, oldFactory
	})
	// Reproduce the independent executable's pre-init zero values, rather than
	// inheriting values initialized by unrelated package tests or production main.
	constant.TaskQueryLimit, constant.TaskTimeoutMinutes = 0, 0
	service.GetTaskAdaptorFunc = func(platform constant.TaskPlatform) service.TaskPollingAdaptor {
		return relay.GetTaskAdaptor(platform)
	}
	redisServer := miniredis.RunT(t)
	redisClient := redis.NewClient(&redis.Options{Addr: redisServer.Addr(), MaxRetries: -1})
	oldRedis, oldEnabled := common.RDB, common.RedisEnabled
	common.RDB, common.RedisEnabled = redisClient, true
	t.Cleanup(func() { _ = redisClient.Close(); common.RDB, common.RedisEnabled = oldRedis, oldEnabled })
	options := labOptions{Mode: "mock", StateDirectory: directory, PublicBaseURL: "http://127.0.0.1:3000"}
	state := &labState{Directory: directory, Manifest: labStateManifest{StateID: config.StateID}}
	videoBytes := labTestInputMP4(t)
	fixture := labVideoFixture{Resolution: "768p", DurationSeconds: 4, AspectRatio: "16:9", payload: videoBytes, SHA256: fmt.Sprintf("%x", sha256.Sum256(videoBytes))}
	models := []labMockModel{{ID: "MiniMax-H3", Family: "minimax", Host: "api.minimax.cn", Key: config.Channels[0].Key}}
	mock, err := newLabMockProvider(filepath.Join(directory, "mock-tasks"), models, map[string]labVideoFixture{labFixtureKey("768p", 4, "16:9"): fixture}, bytes.Repeat([]byte{14}, 32))
	require.NoError(t, err)
	engine, err := buildLabRouter(options, state, config, buildLabSummary(config, options), mock, nil)
	require.NoError(t, err)
	relayServer := httptest.NewServer(engine)
	t.Cleanup(relayServer.Close)
	t.Setenv("RELAY_COMPAT_INTERNAL_BASE_URL", relayServer.URL)
	t.Setenv("RELAY_ARTIFACT_FILESYSTEM_ROOT", filepath.Join(directory, "artifacts"))
	t.Setenv("RELAY_ARTIFACT_PUBLIC_BASE_URL", relayServer.URL)
	t.Setenv("RELAY_ARTIFACT_SPOOL_DIRECTORY", filepath.Join(directory, "spool"))
	provider, ca := labTestProviderTLS(t, models, mock)
	transport, err := newLabMockTransport(models, ca, provider.Listener.Addr().String(), relayServer.URL)
	require.NoError(t, err)
	t.Cleanup(transport.provider.CloseIdleConnections)
	t.Cleanup(transport.internal.CloseIdleConnections)
	require.NoError(t, service.InitHttpClient())
	client, err := service.GetHttpClientWithProxy("")
	require.NoError(t, err)
	client.Transport, client.Timeout = transport, 10*time.Second
	require.NoError(t, service.ValidatePlatformGenerationWorkerConfiguration())
	require.NoError(t, service.StartPlatformGenerationWorkers())
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		assert.NoError(t, service.StopPlatformGenerationWorkers(ctx))
	})
	beforeBilling, err := readLabNativeBillingEvidence(context.Background(), state, config)
	require.NoError(t, err)
	input := dto.NewPlatformGenerationRequest()
	input.Model, input.Mode = "minimax-h3", "text_to_video"
	input.ExpectedCapabilityRevision = config.Models[0].CapabilityRevision
	input.Inputs.Prompt = "A real isolated native POST followed by automatic provider polling."
	input.Output.DurationSeconds, input.Output.Resolution = 4, "768p"
	payload, err := common.Marshal(input)
	require.NoError(t, err)
	submit := func() *httptest.ResponseRecorder {
		request := httptest.NewRequest(http.MethodPost, "/v1/generations", bytes.NewReader(payload))
		request.Header.Set("X-Client-ID", config.Principal.ClientID)
		request.Header.Set("X-API-Key", config.Principal.APIKey)
		request.Header.Set("X-Request-ID", "lab-native-auto-poll")
		request.Header.Set("Idempotency-Key", "lab-native-auto-poll-once")
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		engine.ServeHTTP(response, request)
		return response
	}
	response := submit()
	require.Equal(t, http.StatusAccepted, response.Code, response.Body.String())
	var job model.PlatformGenerationJob
	var native model.Task
	require.Eventually(t, func() bool {
		return model.DB.Where("idempotency_key = ?", "lab-native-auto-poll-once").First(&job).Error == nil &&
			job.Status == model.PlatformGenerationStatusProcessing &&
			model.DB.Where("task_id = ?", job.NativeTaskID).First(&native).Error == nil
	}, 8*time.Second, 20*time.Millisecond)
	assert.Equal(t, int64(1), mock.posts.Load())
	assert.Zero(t, mock.polls.Load())
	assert.Equal(t, model.TaskStatusNotStart, native.Status)
	assert.Empty(t, model.GetAllUnFinishSyncTasks(constant.TaskQueryLimit), "the missing-default bug silently discards the real pending row")
	initializeLabNativePollingDefaults()
	assert.Equal(t, 1000, constant.TaskQueryLimit)
	assert.Equal(t, 1440, constant.TaskTimeoutMinutes)
	require.Len(t, model.GetAllUnFinishSyncTasks(constant.TaskQueryLimit), 1)
	pollCtx, cancelPoll := context.WithCancel(context.Background())
	pollDone := make(chan struct{})
	go runLabNativePolling(pollCtx, pollDone)
	t.Cleanup(func() {
		cancelPoll()
		select {
		case <-pollDone:
		case <-time.After(10 * time.Second):
			t.Error("automatic lab native poller did not stop")
		}
	})
	// No manual poll, native-task write or proof constructor is called here. The
	// same timer used by the executable reaches the official mock GET through the
	// real adapter, produces the receipt, and persists it through normal guards.
	require.Eventually(t, func() bool {
		return model.DB.Where("task_id = ?", job.NativeTaskID).First(&native).Error == nil && native.Status == model.TaskStatusSuccess
	}, 8*time.Second, 20*time.Millisecond)
	cancelPoll()
	select {
	case <-pollDone:
	case <-time.After(5 * time.Second):
		t.Fatal("automatic native polling remained active")
	}
	assert.Equal(t, int64(1), mock.posts.Load())
	assert.Equal(t, int64(3), mock.polls.Load())
	assert.Equal(t, "100%", native.Progress)
	receipt, err := model.DecodePlatformGenerationProviderResultReceipt(native.Data)
	require.NoError(t, err)
	binding, err := model.ResolvePlatformGenerationProviderResultBinding(native)
	require.NoError(t, err)
	require.NoError(t, model.ValidatePlatformGenerationProviderResultReceipt(native, binding, receipt))
	assert.Equal(t, model.PlatformGenerationProviderResultReceiptStateVerified, receipt.State)
	assert.Equal(t, generationprofile.MiniMaxH3VideoProtocolV2, receipt.Protocol)
	assert.Equal(t, job.ID, receipt.JobID)
	assert.Equal(t, job.UpstreamTaskID, receipt.UpstreamTaskID)
	assert.Equal(t, "MiniMax-H3", receipt.UpstreamModel)
	assert.NotContains(t, string(native.Data), "https://")
	assert.NotContains(t, string(native.Data), config.Channels[0].Key)
	replay := submit()
	require.Equal(t, http.StatusAccepted, replay.Code, replay.Body.String())
	assert.Contains(t, replay.Body.String(), job.ID)
	assert.Equal(t, int64(1), mock.posts.Load())
	afterBilling, err := readLabNativeBillingEvidence(context.Background(), state, config)
	require.NoError(t, err)
	assert.Equal(t, beforeBilling, afterBilling)
	stopCtx, stopCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer stopCancel()
	require.NoError(t, service.StopPlatformGenerationWorkers(stopCtx))
	// All mutations below target this test's temporary SQLite database after its
	// workers have stopped. The successful receipt above came only from real HTTP
	// and automatic polling; none of the negative cases invents successful proof.
	assertLabNativeResultImmutableBinding(t, native, receipt)
}

func assertLabNativeResultImmutableBinding(t *testing.T, native model.Task, receipt model.PlatformGenerationProviderResultReceipt) {
	t.Helper()
	binding, err := model.ResolvePlatformGenerationProviderResultBinding(native)
	require.NoError(t, err)
	require.NoError(t, model.ValidatePlatformGenerationProviderResultReceipt(native, binding, receipt))
	require.NotEqual(t, binding.Job.CapabilityRevision, binding.Route.ModelReleaseCapabilityRevision,
		"real discovery and release revisions must exercise their distinct canonical hashing contracts")
	request, _, _, err := model.ResolvePlatformGenerationProviderResultContract(binding)
	require.NoError(t, err)
	assert.Equal(t, binding.Job.CapabilityRevision, request.ExpectedCapabilityRevision)
	assert.Equal(t, binding.Job.ExpectedCapabilityRevision, request.ExpectedCapabilityRevision)
	var anotherRoute model.PlatformGenerationProviderRoute
	require.NoError(t, model.DB.Where("model = ? AND id <> ?", binding.Job.Model, binding.Route.ID).Order("id ASC").First(&anotherRoute).Error)
	changedRevision := "sha256:" + strings.Repeat("1", 64)
	require.NotEqual(t, binding.Job.CapabilityRevision, changedRevision)
	require.NotEqual(t, binding.Route.ModelReleaseRevision, changedRevision)
	require.NotEqual(t, binding.Route.ModelReleaseCapabilityRevision, changedRevision)
	request.ExpectedCapabilityRevision = changedRevision
	changedRequest, err := common.Marshal(request)
	require.NoError(t, err)
	var recovery map[string]any
	require.NoError(t, common.Unmarshal([]byte(binding.Job.NativeTaskRecoveryJSON), &recovery))
	require.Equal(t, float64(4), recovery["schema_version"])
	recovery["route_id"] = anotherRoute.ID
	changedRecovery, err := common.Marshal(recovery)
	require.NoError(t, err)
	for _, test := range []struct {
		name, column    string
		value, original any
	}{
		{"public_revision", "capability_revision", changedRevision, binding.Job.CapabilityRevision},
		{"release_revision_cannot_replace_public_revision", "capability_revision", binding.Route.ModelReleaseCapabilityRevision, binding.Job.CapabilityRevision},
		{"expected_public_revision", "expected_capability_revision", changedRevision, binding.Job.ExpectedCapabilityRevision},
		{"request_must_match_existing_v4_sha_fence", "request_json", string(changedRequest), binding.Job.RequestJSON},
		{"cross_route_job", "provider_route_id", anotherRoute.ID, binding.Job.ProviderRouteID},
		{"cross_route_recovery", "native_task_recovery_json", string(changedRecovery), binding.Job.NativeTaskRecoveryJSON},
	} {
		t.Run(test.name, func(t *testing.T) {
			require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", binding.Job.ID).UpdateColumn(test.column, test.value).Error)
			t.Cleanup(func() {
				require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", binding.Job.ID).UpdateColumn(test.column, test.original).Error)
				_, err := model.ResolvePlatformGenerationProviderResultBinding(native)
				require.NoError(t, err, "restoring only the test fixture must restore its valid binding")
			})
			_, err := model.ResolvePlatformGenerationProviderResultBinding(native)
			require.Error(t, err)
		})
	}
	for _, test := range []struct {
		column string
		value  any
	}{
		{"model_release_id", "different-valid-release"},
		{"model_release_revision", changedRevision},
		{"model_release_capability_revision", changedRevision},
		{"capability_profile_id", "different-valid-profile"},
		{"capability_profile_revision", changedRevision},
		{"capability_profile_snapshot", "{}"},
	} {
		t.Run("sql_immutable_"+test.column, func(t *testing.T) {
			// This is a real SQL UPDATE, not a validator call or a mocked failure.
			// The production V4 trigger installed by the tagged lab must abort it.
			err := model.DB.Model(&model.PlatformGenerationProviderRoute{}).Where("id = ?", binding.Route.ID).UpdateColumn(test.column, test.value).Error
			require.ErrorContains(t, err, "generation route release binding is immutable")
			var persisted model.PlatformGenerationProviderRoute
			require.NoError(t, model.DB.Where("id = ?", binding.Route.ID).First(&persisted).Error)
			assert.Equal(t, binding.Route, persisted)
			_, err = model.ResolvePlatformGenerationProviderResultBinding(native)
			require.NoError(t, err)
		})
	}
	for _, test := range []struct {
		name   string
		mutate func(*model.PlatformGenerationProviderResultReceipt)
	}{
		{"receipt_release_id", func(value *model.PlatformGenerationProviderResultReceipt) {
			value.ModelReleaseID = "different-valid-release"
		}},
		{"receipt_release_revision", func(value *model.PlatformGenerationProviderResultReceipt) {
			value.ModelReleaseRevision = changedRevision
		}},
		{"receipt_release_capability_revision", func(value *model.PlatformGenerationProviderResultReceipt) {
			value.ModelReleaseCapabilityRevision = changedRevision
		}},
		{"receipt_cross_route", func(value *model.PlatformGenerationProviderResultReceipt) { value.RouteID = anotherRoute.ID }},
	} {
		t.Run(test.name, func(t *testing.T) {
			changed := receipt
			test.mutate(&changed)
			require.Error(t, model.ValidatePlatformGenerationProviderResultReceipt(native, binding, changed))
		})
	}
	for _, test := range []struct {
		name   string
		mutate func(*model.PlatformGenerationProviderResultBinding)
	}{
		{"missing_release", func(value *model.PlatformGenerationProviderResultBinding) { value.Route.ModelReleaseID = "" }},
		{"invalid_release_revision", func(value *model.PlatformGenerationProviderResultBinding) {
			value.Route.ModelReleaseRevision = "unfenced"
		}},
		{"invalid_release_capability_revision", func(value *model.PlatformGenerationProviderResultBinding) {
			value.Route.ModelReleaseCapabilityRevision = "unfenced"
		}},
		{"cross_route_contract", func(value *model.PlatformGenerationProviderResultBinding) { value.Route = anotherRoute }},
		{"profile_snapshot_contract", func(value *model.PlatformGenerationProviderResultBinding) {
			value.Route.CapabilityProfileSnapshot = "{}"
		}},
		{"channel_contract", func(value *model.PlatformGenerationProviderResultBinding) { value.Route.AcceptedChannelType++ }},
	} {
		t.Run(test.name, func(t *testing.T) {
			changed := binding
			test.mutate(&changed)
			_, _, _, err := model.ResolvePlatformGenerationProviderResultContract(changed)
			require.Error(t, err)
		})
	}
	_, err = model.ResolvePlatformGenerationProviderResultBinding(native)
	require.NoError(t, err)
}
