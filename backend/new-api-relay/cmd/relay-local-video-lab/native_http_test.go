//go:build relay_local_video_lab

package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/alicebob/miniredis/v2"
	"github.com/go-redis/redis/v8"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func labTestProviderTLS(t *testing.T, models []labMockModel, handler http.Handler) (*httptest.Server, []byte) {
	t.Helper()
	directory := t.TempDir()
	require.NoError(t, prepareLabMockTLS(directory, models))
	certificate, err := tls.LoadX509KeyPair(filepath.Join(directory, "mock-certificate.pem"), filepath.Join(directory, "mock-private-key.pem"))
	require.NoError(t, err)
	server := httptest.NewUnstartedServer(handler)
	server.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
	server.StartTLS()
	t.Cleanup(server.Close)
	ca, err := os.ReadFile(filepath.Join(directory, "mock-ca.pem"))
	require.NoError(t, err)
	return server, ca
}

func TestLabNativeHTTPUnknownHasOneProviderEffectAndNoNativeCharge(t *testing.T) {
	config := labTestConfig(t, "mock")
	directory := labTestDatabase(t, config)
	redisServer := miniredis.RunT(t)
	redisClient := redis.NewClient(&redis.Options{Addr: redisServer.Addr(), MaxRetries: -1})
	oldRedis, oldEnabled := common.RDB, common.RedisEnabled
	common.RDB, common.RedisEnabled = redisClient, true
	t.Cleanup(func() { _ = redisClient.Close(); common.RDB, common.RedisEnabled = oldRedis, oldEnabled })
	options := labOptions{Mode: "mock", StateDirectory: directory, PublicBaseURL: "http://127.0.0.1:3000"}
	state := &labState{Directory: directory, Manifest: labStateManifest{StateID: config.StateID}}
	engine, err := buildLabRouter(options, state, config, buildLabSummary(config, options), nil, nil)
	require.NoError(t, err)
	relayServer := httptest.NewServer(engine)
	t.Cleanup(relayServer.Close)
	t.Setenv("RELAY_COMPAT_INTERNAL_BASE_URL", relayServer.URL)
	t.Setenv("RELAY_ARTIFACT_FILESYSTEM_ROOT", filepath.Join(directory, "artifacts"))
	t.Setenv("RELAY_ARTIFACT_PUBLIC_BASE_URL", relayServer.URL)
	t.Setenv("RELAY_ARTIFACT_SPOOL_DIRECTORY", filepath.Join(directory, "spool"))
	t.Setenv("RELAY_COMPAT_SUBMISSION_TIMEOUT_SECONDS", "5")
	models := []labMockModel{{ID: "MiniMax-H3", Family: "minimax", Host: "api.minimax.cn", Key: config.Channels[0].Key}}
	var providerPosts atomic.Int64
	provider, ca := labTestProviderTLS(t, models, http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		assert.Equal(t, "api.minimax.cn", request.Host)
		assert.Equal(t, "/v2/video_generation", request.URL.Path)
		assert.Equal(t, "Bearer "+config.Channels[0].Key, request.Header.Get("Authorization"))
		payload, readErr := io.ReadAll(request.Body)
		assert.NoError(t, readErr)
		var body struct {
			Model      string `json:"model"`
			Duration   int    `json:"duration"`
			Resolution string `json:"resolution"`
			Ratio      string `json:"ratio"`
		}
		assert.NoError(t, common.Unmarshal(payload, &body))
		assert.Equal(t, "MiniMax-H3", body.Model)
		assert.Equal(t, 4, body.Duration)
		assert.Equal(t, "768P", body.Resolution)
		assert.Equal(t, "16:9", body.Ratio)
		providerPosts.Add(1)
		connection, _, hijackErr := writer.(http.Hijacker).Hijack()
		assert.NoError(t, hijackErr)
		if hijackErr == nil {
			_ = connection.Close()
		}
	}))
	transport, err := newLabMockTransport(models, ca, provider.Listener.Addr().String(), relayServer.URL)
	require.NoError(t, err)
	t.Cleanup(transport.provider.CloseIdleConnections)
	t.Cleanup(transport.internal.CloseIdleConnections)
	require.NoError(t, service.InitHttpClient())
	client, err := service.GetHttpClientWithProxy("")
	require.NoError(t, err)
	client.Transport = transport
	client.Timeout = 10 * time.Second
	require.NoError(t, service.ValidatePlatformGenerationWorkerConfiguration())
	require.NoError(t, service.StartPlatformGenerationWorkers())
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		assert.NoError(t, service.StopPlatformGenerationWorkers(ctx))
	})
	input := dto.NewPlatformGenerationRequest()
	input.Model, input.Mode = "minimax-h3", "text_to_video"
	input.ExpectedCapabilityRevision = config.Models[0].CapabilityRevision
	input.Inputs.Prompt = "A deterministic local transport-failure test."
	input.Output.DurationSeconds, input.Output.Resolution = 4, "768p"
	payload, err := common.Marshal(input)
	require.NoError(t, err)
	submit := func() *httptest.ResponseRecorder {
		request := httptest.NewRequest(http.MethodPost, "/v1/generations", bytes.NewReader(payload))
		request.Header.Set("X-Client-ID", config.Principal.ClientID)
		request.Header.Set("X-API-Key", config.Principal.APIKey)
		request.Header.Set("X-Request-ID", "lab-native-lost-response")
		request.Header.Set("Idempotency-Key", "lab-native-unknown-once")
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		engine.ServeHTTP(response, request)
		return response
	}
	first := submit()
	require.Equal(t, http.StatusAccepted, first.Code, first.Body.String())
	var job model.PlatformGenerationJob
	settled := assert.Eventually(t, func() bool {
		return model.DB.Where("idempotency_key = ?", "lab-native-unknown-once").First(&job).Error == nil && job.Status == model.PlatformGenerationStatusReconciliationRequired
	}, 12*time.Second, 25*time.Millisecond)
	if !settled {
		var outbox model.PlatformGenerationOutbox
		_ = model.DB.Where("job_id = ?", job.ID).First(&outbox).Error
		t.Fatalf("native failure diagnostic: status=%s code=%s message=%s outbox=%s attempts=%d last_error=%s provider_posts=%d", job.Status, job.ErrorCode, job.ErrorMessage, outbox.State, outbox.Attempts, outbox.LastError, providerPosts.Load())
	}
	assert.Equal(t, int64(1), providerPosts.Load())
	assert.Empty(t, job.UpstreamTaskID)
	assert.JSONEq(t, `[]`, job.OutputsJSON)
	replayed := submit()
	assert.Equal(t, http.StatusAccepted, replayed.Code, replayed.Body.String())
	assert.Contains(t, replayed.Body.String(), job.ID)
	assert.NotContains(t, replayed.Body.String(), config.Channels[0].Key)
	_, err = service.RunPlatformGenerationSubmissionOnce(context.Background())
	assert.Error(t, err, "the consumed outbox must not enqueue another provider POST")
	assert.Equal(t, int64(1), providerPosts.Load())
	var nativeTasks, jobs int64
	require.NoError(t, model.DB.Model(&model.Task{}).Count(&nativeTasks).Error)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).Count(&jobs).Error)
	assert.Zero(t, nativeTasks)
	assert.Equal(t, int64(1), jobs)
	var user model.User
	require.NoError(t, model.DB.Where("username = ?", config.Principal.UserName).First(&user).Error)
	assert.Zero(t, user.Quota)
	assert.Zero(t, user.UsedQuota)
}

func TestLabMockTransportRejectsEveryUnlistedDestinationBeforeDial(t *testing.T) {
	models := []labMockModel{{ID: "MiniMax-H3", Family: "minimax", Host: "api.minimax.cn", Key: "not-live"}}
	var calls atomic.Int64
	server, ca := labTestProviderTLS(t, models, http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		calls.Add(1)
		writer.WriteHeader(http.StatusNoContent)
	}))
	transport, err := newLabMockTransport(models, ca, server.Listener.Addr().String(), "http://127.0.0.1:3000")
	require.NoError(t, err)
	t.Cleanup(transport.provider.CloseIdleConnections)
	client := &http.Client{Transport: transport, Timeout: time.Second}
	for _, target := range []string{
		"https://api.minimax.cn/v1/video_generation",
		"https://api.minimax.cn:444/v2/video_generation",
		"http://api.minimax.cn/v2/video_generation",
		"https://unapproved.example/v2/video_generation",
		"https://api.minimaxi.com/v2/video_generation",
		"https://api.minimax.cn/v2/video_generation?callback_url=https://unapproved.example",
		"http://127.0.0.1:3000/v1/generations",
	} {
		request, err := http.NewRequest(http.MethodPost, target, bytes.NewReader([]byte(`{}`)))
		require.NoError(t, err)
		_, err = client.Do(request)
		assert.Error(t, err)
	}
	assert.Zero(t, calls.Load())
	request, err := http.NewRequest(http.MethodPost, "https://api.minimax.cn/v2/video_generation", bytes.NewReader([]byte(`{}`)))
	require.NoError(t, err)
	response, err := client.Do(request)
	require.NoError(t, err)
	require.NoError(t, response.Body.Close())
	assert.Equal(t, http.StatusNoContent, response.StatusCode)
	assert.Equal(t, int64(1), calls.Load())
}

func TestLabGenericProbeLostAcknowledgementIsDurableAndNeverReposts(t *testing.T) {
	config := labTestConfig(t, "mock")
	directory := labTestDatabase(t, config)
	options := labOptions{Mode: "mock", StateDirectory: directory, PublicBaseURL: "http://127.0.0.1:3000"}
	state := &labState{Directory: directory, Manifest: labStateManifest{StateID: config.StateID}}
	summary := buildLabSummary(config, options)
	engine, err := buildLabRouter(options, state, config, summary, nil, nil)
	require.NoError(t, err)
	models := []labMockModel{{ID: "MiniMax-H3", Family: "minimax", Host: "api.minimax.cn", Key: config.Channels[0].Key}}
	var posts atomic.Int64
	provider, ca := labTestProviderTLS(t, models, http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		assert.Equal(t, http.MethodPost, request.Method)
		assert.Equal(t, "/v2/video_generation", request.URL.Path)
		assert.Equal(t, "api.minimax.cn", request.Host)
		assert.Equal(t, "Bearer "+config.Channels[0].Key, request.Header.Get("Authorization"))
		_, err := io.Copy(io.Discard, request.Body)
		assert.NoError(t, err)
		posts.Add(1)
		connection, _, err := writer.(http.Hijacker).Hijack()
		assert.NoError(t, err)
		if err == nil {
			_ = connection.Close()
		}
	}))
	transport, err := newLabMockTransport(models, ca, provider.Listener.Addr().String(), "http://127.0.0.1:3000")
	require.NoError(t, err)
	t.Cleanup(transport.provider.CloseIdleConnections)
	require.NoError(t, service.InitHttpClient())
	client, err := service.GetHttpClientWithProxy("")
	require.NoError(t, err)
	client.Transport, client.Timeout = transport, 5*time.Second
	probe := summary.ProbeRequests[0]
	payload, err := common.Marshal(probe.Body)
	require.NoError(t, err)
	for _, expectedReplay := range []bool{false, true} {
		request := httptest.NewRequest(http.MethodPost, probe.Path, bytes.NewReader(payload))
		request.Header.Set("X-Relay-Operations-Token", config.Principal.OperationsToken)
		request.Header.Set("X-Request-ID", probe.RequestID)
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		engine.ServeHTTP(response, request)
		require.Equal(t, http.StatusAccepted, response.Code, response.Body.String())
		var receipt dto.PlatformChannelControlOperation
		require.NoError(t, common.Unmarshal(response.Body.Bytes(), &receipt))
		assert.Equal(t, model.PlatformChannelControlOperationPending, receipt.State)
		assert.Equal(t, model.PlatformChannelTestSubmissionUnknown, receipt.ProviderSubmissionState)
		assert.Equal(t, expectedReplay, receipt.IdempotentReplay)
		assert.NotContains(t, response.Body.String(), config.Channels[0].Key)
		assert.NotContains(t, response.Body.String(), "api.minimax.cn")
		assert.Equal(t, int64(1), posts.Load())
	}
	var durable model.PlatformChannelControlOperation
	require.NoError(t, model.DB.Where("tenant_id = ? AND operation_id = ?", config.Principal.TenantID, probe.Body.OperationID).First(&durable).Error)
	assert.Equal(t, model.PlatformChannelTestSubmissionUnknown, durable.ProviderSubmissionState)
	assert.Empty(t, durable.ProviderTaskID)
	assert.Nil(t, durable.ResultSuccess)
	assert.NotEmpty(t, durable.IntentTransportSHA256)
	assert.ErrorIs(t, model.DB.Model(&durable).Update("provider_submission_state", model.PlatformChannelTestSubmissionNotStarted).Error, model.ErrPlatformChannelControlOperationImmutable)
}
