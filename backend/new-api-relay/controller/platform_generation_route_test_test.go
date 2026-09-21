package controller

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayservice "github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

const routeTestUpstreamModel = "doubao-seedance-2-0-260128"

func seedanceRouteTestFixture(t *testing.T, server *httptest.Server) (*model.Channel, *relayservice.PlatformGenerationRouteTestBinding) {
	t.Helper()
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkVideoGenerationV1)
	require.True(t, ok)
	baseURL := server.URL
	return &model.Channel{
			Id:      71,
			Type:    constant.ChannelTypeVolcEngine,
			Key:     "fixture-route-key",
			BaseURL: &baseURL,
		}, &relayservice.PlatformGenerationRouteTestBinding{
			PublicModelID:             "video.seedance.acceptance",
			RouteID:                   "route-seedance-acceptance",
			ChannelID:                 71,
			NativeChannelType:         constant.ChannelTypeVolcEngine,
			KeyIndex:                  0,
			UpstreamModel:             routeTestUpstreamModel,
			CapabilityProfileID:       profile.ID,
			CapabilityProfileRevision: profile.Revision,
		}
}

func seedreamRouteTestFixture(t *testing.T, server *httptest.Server) (*model.Channel, *relayservice.PlatformGenerationRouteTestBinding) {
	t.Helper()
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkImageGenerationV1)
	require.True(t, ok)
	baseURL := server.URL
	return &model.Channel{
			Id:      72,
			Type:    constant.ChannelTypeVolcEngine,
			Key:     "fixture-seedream-route-key",
			BaseURL: &baseURL,
		}, &relayservice.PlatformGenerationRouteTestBinding{
			PublicModelID:             constant.PlatformGenerationPublicSeedream50Model,
			RouteID:                   "route-seedream-acceptance",
			ChannelID:                 72,
			NativeChannelType:         constant.ChannelTypeVolcEngine,
			KeyIndex:                  0,
			UpstreamModel:             constant.PlatformGenerationArkSeedream50Model,
			CapabilityProfileID:       profile.ID,
			CapabilityProfileRevision: profile.Revision,
		}
}

func TestPlatformGenerationRouteTestAcceptsSynchronousSeedreamPNG(t *testing.T) {
	originalVerifier := verifyPlatformGenerationRouteTestArtifact
	verifyPlatformGenerationRouteTestArtifact = func(ctx context.Context, providerURL string, artifact generationprofile.ArtifactContract) (model.PlatformChannelTestArtifactEvidence, error) {
		require.NotNil(t, ctx)
		require.Equal(t, "https://artifacts.example/route-test.png", providerURL)
		require.Equal(t, generationprofile.ArtifactContract{
			MediaType: "image", ContentType: "image/png", Width: 2048, Height: 2048, Count: 1,
		}, artifact)
		return model.PlatformChannelTestArtifactEvidence{
			SHA256: strings.Repeat("b", 64), SizeBytes: 256, ContentType: "image/png",
		}, nil
	}
	t.Cleanup(func() { verifyPlatformGenerationRouteTestArtifact = originalVerifier })

	var submissions atomic.Int32
	var polls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		require.Equal(t, "Bearer fixture-seedream-route-key", request.Header.Get("Authorization"))
		if request.Method != http.MethodPost || request.URL.Path != "/api/v3/images/generations" {
			polls.Add(1)
			http.NotFound(w, request)
			return
		}
		submissions.Add(1)
		var payload struct {
			Model                     string `json:"model"`
			Prompt                    string `json:"prompt"`
			Size                      string `json:"size"`
			SequentialImageGeneration string `json:"sequential_image_generation"`
			ResponseFormat            string `json:"response_format"`
			OutputFormat              string `json:"output_format"`
			Stream                    bool   `json:"stream"`
			Watermark                 bool   `json:"watermark"`
		}
		require.NoError(t, json.NewDecoder(request.Body).Decode(&payload))
		require.Equal(t, constant.PlatformGenerationArkSeedream50Model, payload.Model)
		require.NotEmpty(t, payload.Prompt)
		require.Equal(t, constant.PlatformGenerationArkSeedream50CompatibilitySize, payload.Size)
		require.Equal(t, "disabled", payload.SequentialImageGeneration)
		require.Equal(t, "url", payload.ResponseFormat)
		require.Equal(t, "png", payload.OutputFormat)
		require.False(t, payload.Stream)
		require.True(t, payload.Watermark)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"model":"doubao-seedream-5-0-260128","created":1,"data":[{"url":"https://artifacts.example/route-test.png","size":"2048x2048"}],"usage":{"generated_images":1,"output_tokens":2,"total_tokens":2}}`))
	}))
	defer server.Close()
	channel, binding := seedreamRouteTestFixture(t, server)
	evidence := model.PlatformChannelTestArtifactEvidence{}
	recordedTaskID := ""

	err := runPlatformGenerationRouteChannelTestDurable(
		context.Background(), channel, binding, time.Millisecond, time.Second, "",
		func(taskID string) error {
			recordedTaskID = taskID
			return nil
		},
		&evidence,
	)

	require.NoError(t, err)
	require.True(t, strings.HasPrefix(recordedTaskID, "seedream:"))
	require.Equal(t, "image/png", evidence.ContentType)
	require.EqualValues(t, 1, submissions.Load())
	require.EqualValues(t, 0, polls.Load(), "synchronous image acceptance must not enter the video poll path")
}

func TestPlatformGenerationRouteTestRejectsSeedreamWrongArtifactMediaType(t *testing.T) {
	originalVerifier := verifyPlatformGenerationRouteTestArtifact
	verifyPlatformGenerationRouteTestArtifact = func(_ context.Context, _ string, artifact generationprofile.ArtifactContract) (model.PlatformChannelTestArtifactEvidence, error) {
		require.Equal(t, "image/png", artifact.ContentType)
		require.Equal(t, 2048, artifact.Width)
		require.Equal(t, 2048, artifact.Height)
		return model.PlatformChannelTestArtifactEvidence{
			SHA256: strings.Repeat("c", 64), SizeBytes: 256, ContentType: "video/mp4",
		}, nil
	}
	t.Cleanup(func() { verifyPlatformGenerationRouteTestArtifact = originalVerifier })

	var submissions atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		require.Equal(t, http.MethodPost, request.Method)
		require.Equal(t, "/api/v3/images/generations", request.URL.Path)
		submissions.Add(1)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"model":"doubao-seedream-5-0-260128","created":1,"data":[{"url":"https://artifacts.example/route-test.png","size":"2048x2048"}],"usage":{"generated_images":1,"output_tokens":2,"total_tokens":2}}`))
	}))
	defer server.Close()
	channel, binding := seedreamRouteTestFixture(t, server)
	evidence := model.PlatformChannelTestArtifactEvidence{}

	err := runPlatformGenerationRouteChannelTestDurable(
		context.Background(), channel, binding, time.Millisecond, time.Second, "", func(string) error { return nil }, &evidence,
	)

	require.Error(t, err)
	require.Equal(t, model.PlatformChannelControlErrorTestArtifact, platformGenerationRouteTestReceiptCode(err))
	var classified *platformGenerationRouteTestError
	require.ErrorAs(t, err, &classified)
	require.True(t, classified.pollPending)
	require.EqualValues(t, 1, submissions.Load())
	require.Empty(t, evidence.ContentType)
}

func TestPlatformGenerationRouteTestSubmitsOncePollsStickyRouteAndVerifiesArtifact(t *testing.T) {
	originalVerifier := verifyPlatformGenerationRouteTestArtifact
	verifyPlatformGenerationRouteTestArtifact = func(ctx context.Context, providerURL string, artifact generationprofile.ArtifactContract) (model.PlatformChannelTestArtifactEvidence, error) {
		require.NotNil(t, ctx)
		require.Equal(t, "https://artifacts.example/route-test.mp4", providerURL)
		require.Equal(t, "video/mp4", artifact.ContentType)
		require.Zero(t, artifact.Width)
		require.Zero(t, artifact.Height)
		return model.PlatformChannelTestArtifactEvidence{
			SHA256: strings.Repeat("a", 64), SizeBytes: 128, ContentType: "video/mp4",
		}, nil
	}
	t.Cleanup(func() { verifyPlatformGenerationRouteTestArtifact = originalVerifier })

	var submissions atomic.Int32
	var polls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		require.Equal(t, "Bearer fixture-route-key", request.Header.Get("Authorization"))
		switch request.Method + " " + request.URL.Path {
		case http.MethodPost + " /api/v3/contents/generations/tasks":
			submissions.Add(1)
			var payload struct {
				Model      string `json:"model"`
				Resolution string `json:"resolution"`
				Ratio      string `json:"ratio"`
				Duration   int    `json:"duration"`
				Content    []struct {
					Type string `json:"type"`
					Text string `json:"text"`
				} `json:"content"`
			}
			require.NoError(t, json.NewDecoder(request.Body).Decode(&payload))
			require.Equal(t, routeTestUpstreamModel, payload.Model)
			require.Equal(t, "720p", payload.Resolution)
			require.Equal(t, "16:9", payload.Ratio)
			require.Equal(t, 4, payload.Duration)
			require.Len(t, payload.Content, 1)
			require.Equal(t, "text", payload.Content[0].Type)
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"id":"cgt-route-test"}`))
		case http.MethodGet + " /api/v3/contents/generations/tasks/cgt-route-test":
			poll := polls.Add(1)
			w.Header().Set("Content-Type", "application/json")
			if poll == 1 {
				_, _ = w.Write([]byte(`{"id":"cgt-route-test","model":"doubao-seedance-2-0-260128","status":"queued"}`))
				return
			}
			_, _ = w.Write([]byte(`{"id":"cgt-route-test","model":"doubao-seedance-2-0-260128","status":"succeeded","content":{"video_url":"https://artifacts.example/route-test.mp4"},"resolution":"720p","duration":"4","ratio":"16:9","usage":{"completion_tokens":1,"total_tokens":2}}`))
		default:
			http.NotFound(w, request)
		}
	}))
	defer server.Close()
	channel, binding := seedanceRouteTestFixture(t, server)

	err := runPlatformGenerationRouteChannelTest(context.Background(), channel, binding, time.Millisecond, time.Second)

	require.NoError(t, err)
	require.EqualValues(t, 1, submissions.Load(), "the acceptance test must issue one provider POST")
	require.EqualValues(t, 2, polls.Load())
}

func TestPlatformGenerationRouteTestRejectsSuccessfulPollFromDifferentIdentityOrOutput(t *testing.T) {
	tests := []struct {
		name     string
		terminal string
	}{
		{
			name:     "task identity",
			terminal: `{"id":"cgt-other-task","model":"doubao-seedance-2-0-260128","status":"succeeded","content":{"video_url":"https://artifacts.example/route-test.mp4"},"resolution":"720p","duration":"4","ratio":"16:9","usage":{"completion_tokens":1,"total_tokens":2}}`,
		},
		{
			name:     "model identity",
			terminal: `{"id":"cgt-proof-drift","model":"doubao-seedance-wrong-model","status":"succeeded","content":{"video_url":"https://artifacts.example/route-test.mp4"},"resolution":"720p","duration":"4","ratio":"16:9","usage":{"completion_tokens":1,"total_tokens":2}}`,
		},
		{
			name:     "resolution",
			terminal: `{"id":"cgt-proof-drift","model":"doubao-seedance-2-0-260128","status":"succeeded","content":{"video_url":"https://artifacts.example/route-test.mp4"},"resolution":"1080p","duration":"4","ratio":"16:9","usage":{"completion_tokens":1,"total_tokens":2}}`,
		},
		{
			name:     "duration",
			terminal: `{"id":"cgt-proof-drift","model":"doubao-seedance-2-0-260128","status":"succeeded","content":{"video_url":"https://artifacts.example/route-test.mp4"},"resolution":"720p","duration":"5","ratio":"16:9","usage":{"completion_tokens":1,"total_tokens":2}}`,
		},
		{
			name:     "aspect ratio",
			terminal: `{"id":"cgt-proof-drift","model":"doubao-seedance-2-0-260128","status":"succeeded","content":{"video_url":"https://artifacts.example/route-test.mp4"},"resolution":"720p","duration":"4","ratio":"9:16","usage":{"completion_tokens":1,"total_tokens":2}}`,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var submissions atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if request.Method == http.MethodPost {
					submissions.Add(1)
					_, _ = w.Write([]byte(`{"id":"cgt-proof-drift"}`))
					return
				}
				_, _ = w.Write([]byte(test.terminal))
			}))
			defer server.Close()
			channel, binding := seedanceRouteTestFixture(t, server)

			err := runPlatformGenerationRouteChannelTest(context.Background(), channel, binding, time.Millisecond, time.Second)

			require.Error(t, err)
			require.Equal(t, model.PlatformChannelControlErrorTestTerminal, platformGenerationRouteTestReceiptCode(err))
			var classified *platformGenerationRouteTestError
			require.ErrorAs(t, err, &classified)
			require.True(t, classified.pollPending)
			require.False(t, classified.providerTerminal)
			require.EqualValues(t, 1, submissions.Load())
		})
	}
}

func TestPlatformGenerationRouteTestRejectsQueuedAndFailedIdentityDriftImmediately(t *testing.T) {
	tests := []struct {
		name string
		body string
	}{
		{
			name: "queued model drift",
			body: `{"id":"cgt-state-drift","model":"doubao-seedance-wrong-model","status":"queued"}`,
		},
		{
			name: "failed task drift",
			body: `{"id":"cgt-other-task","model":"doubao-seedance-2-0-260128","status":"failed","error":{"code":"InvalidParameter","message":"wrong task"}}`,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var submissions atomic.Int32
			var polls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if request.Method == http.MethodPost {
					submissions.Add(1)
					_, _ = w.Write([]byte(`{"id":"cgt-state-drift"}`))
					return
				}
				polls.Add(1)
				_, _ = w.Write([]byte(test.body))
			}))
			defer server.Close()
			channel, binding := seedanceRouteTestFixture(t, server)

			err := runPlatformGenerationRouteChannelTest(context.Background(), channel, binding, time.Millisecond, time.Second)

			require.Error(t, err)
			require.Equal(t, model.PlatformChannelControlErrorTestTerminal, platformGenerationRouteTestReceiptCode(err))
			var classified *platformGenerationRouteTestError
			require.ErrorAs(t, err, &classified)
			require.True(t, classified.pollPending)
			require.False(t, classified.providerTerminal)
			require.EqualValues(t, 1, submissions.Load())
			require.EqualValues(t, 1, polls.Load(), "identity drift must fail on the first poll")
		})
	}
}

func TestPlatformGenerationRouteTestSuccessAcceptanceBlockersResumeSameTaskWithoutPOST(t *testing.T) {
	tests := []struct {
		name         string
		terminalBody string
		verifierErr  error
		expectedCode string
	}{
		{
			name:         "proof mismatch",
			terminalBody: `{"id":"cgt-acceptance-blocked","model":"doubao-seedance-2-0-260128","status":"succeeded","content":{"video_url":"https://artifacts.example/route-test.mp4"},"resolution":"1080p","duration":"4","ratio":"16:9","usage":{"completion_tokens":1,"total_tokens":2}}`,
			expectedCode: model.PlatformChannelControlErrorTestTerminal,
		},
		{
			name:         "artifact url invalid",
			terminalBody: `{"id":"cgt-acceptance-blocked","model":"doubao-seedance-2-0-260128","status":"succeeded","content":{"video_url":"http://artifacts.example/route-test.mp4"},"resolution":"720p","duration":"4","ratio":"16:9","usage":{"completion_tokens":1,"total_tokens":2}}`,
			// The Doubao adaptor rejects the unsafe URL before emitting a
			// success proof, so the closed code is provider-terminal. The
			// critical disposition remains pollPending on the sticky task.
			expectedCode: model.PlatformChannelControlErrorTestTerminal,
		},
		{
			name:         "artifact timeout",
			terminalBody: `{"id":"cgt-acceptance-blocked","model":"doubao-seedance-2-0-260128","status":"succeeded","content":{"video_url":"https://artifacts.example/route-test.mp4"},"resolution":"720p","duration":"4","ratio":"16:9","usage":{"completion_tokens":1,"total_tokens":2}}`,
			verifierErr:  context.DeadlineExceeded,
			expectedCode: model.PlatformChannelControlErrorTestArtifact,
		},
		{
			name:         "artifact media signature invalid",
			terminalBody: `{"id":"cgt-acceptance-blocked","model":"doubao-seedance-2-0-260128","status":"succeeded","content":{"video_url":"https://artifacts.example/route-test.mp4"},"resolution":"720p","duration":"4","ratio":"16:9","usage":{"completion_tokens":1,"total_tokens":2}}`,
			verifierErr:  errors.New("controlled media signature rejected"),
			expectedCode: model.PlatformChannelControlErrorTestArtifact,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			originalVerifier := verifyPlatformGenerationRouteTestArtifact
			verifyPlatformGenerationRouteTestArtifact = func(_ context.Context, _ string, artifact generationprofile.ArtifactContract) (model.PlatformChannelTestArtifactEvidence, error) {
				require.Equal(t, "video/mp4", artifact.ContentType)
				if test.verifierErr != nil {
					return model.PlatformChannelTestArtifactEvidence{}, test.verifierErr
				}
				return model.PlatformChannelTestArtifactEvidence{
					SHA256: strings.Repeat("a", 64), SizeBytes: 128, ContentType: "video/mp4",
				}, nil
			}
			defer func() { verifyPlatformGenerationRouteTestArtifact = originalVerifier }()

			var submissions atomic.Int32
			var polls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if request.Method == http.MethodPost {
					submissions.Add(1)
					_, _ = w.Write([]byte(`{"id":"cgt-acceptance-blocked"}`))
					return
				}
				polls.Add(1)
				require.Equal(t, "/api/v3/contents/generations/tasks/cgt-acceptance-blocked", request.URL.Path)
				_, _ = w.Write([]byte(test.terminalBody))
			}))
			defer server.Close()
			channel, binding := seedanceRouteTestFixture(t, server)
			providerTaskID := ""

			for attempt := 0; attempt < 2; attempt++ {
				evidence := model.PlatformChannelTestArtifactEvidence{}
				err := runPlatformGenerationRouteChannelTestDurable(
					context.Background(), channel, binding, time.Millisecond, time.Second,
					providerTaskID,
					func(taskID string) error {
						providerTaskID = taskID
						return nil
					},
					&evidence,
				)
				require.Error(t, err)
				var classified *platformGenerationRouteTestError
				require.ErrorAs(t, err, &classified)
				require.True(t, classified.pollPending)
				require.False(t, classified.providerTerminal)
				require.Equal(t, test.expectedCode, classified.receiptCode)
			}

			require.Equal(t, "cgt-acceptance-blocked", providerTaskID)
			require.EqualValues(t, 1, submissions.Load(), "resume must never submit the same operation again")
			require.EqualValues(t, 2, polls.Load(), "both attempts must poll the sticky provider task")
		})
	}
}

func TestPlatformGenerationRoutePollIdentityProofRejectsProtocolAndStatusDrift(t *testing.T) {
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkVideoGenerationV1)
	require.True(t, ok)
	binding := &relayservice.PlatformGenerationRouteTestBinding{UpstreamModel: routeTestUpstreamModel}
	newQueued := func() *relaycommon.TaskInfo {
		return &relaycommon.TaskInfo{
			TaskID:   "cgt-identity-exact",
			Status:   string(model.TaskStatusQueued),
			Progress: "10%",
			ProviderResultProof: &relaycommon.ProviderTaskResultProof{
				SchemaVersion:  1,
				Protocol:       profile.Protocol,
				TaskID:         "cgt-identity-exact",
				Model:          routeTestUpstreamModel,
				ProviderStatus: "queued",
			},
		}
	}
	require.NoError(t, validatePlatformGenerationRoutePollIdentityProof(
		newQueued(), "cgt-identity-exact", binding, profile,
	))

	tests := []struct {
		name   string
		mutate func(*relaycommon.TaskInfo)
	}{
		{name: "result task", mutate: func(result *relaycommon.TaskInfo) { result.TaskID = "cgt-other" }},
		{name: "proof task", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.TaskID = "cgt-other" }},
		{name: "model", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.Model = "other-model" }},
		{name: "protocol", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.Protocol = "other-protocol" }},
		{name: "provider status", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.ProviderStatus = "failed" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result := newQueued()
			test.mutate(result)
			require.Error(t, validatePlatformGenerationRoutePollIdentityProof(
				result, "cgt-identity-exact", binding, profile,
			))
		})
	}
}

func TestPlatformGenerationRouteSuccessProofRejectsEveryBoundFieldDrift(t *testing.T) {
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkVideoGenerationV1)
	require.True(t, ok)
	binding := &relayservice.PlatformGenerationRouteTestBinding{UpstreamModel: routeTestUpstreamModel}
	artifact, ok := profile.Artifact("text_to_video")
	require.True(t, ok)
	plan := platformGenerationRouteTestPlan{
		Profile: profile, Mode: "text_to_video", Artifact: artifact,
		Resolution: "720p", Duration: 4, AspectRatio: "16:9", Asynchronous: true,
	}
	newResult := func() *relaycommon.TaskInfo {
		return &relaycommon.TaskInfo{
			TaskID:   "cgt-proof-exact",
			Status:   string(model.TaskStatusSuccess),
			Progress: "100%",
			Url:      "https://artifacts.example/route-test.mp4",
			ProviderResultProof: &relaycommon.ProviderTaskResultProof{
				SchemaVersion:   1,
				Protocol:        profile.Protocol,
				TaskID:          "cgt-proof-exact",
				Model:           routeTestUpstreamModel,
				ProviderStatus:  "succeeded",
				Resolution:      "720p",
				DurationSeconds: 4,
				AspectRatio:     "16:9",
				OutputCount:     1,
				MediaType:       "video",
			},
		}
	}
	require.NoError(t, validatePlatformGenerationRouteSuccessProof(
		newResult(), "cgt-proof-exact", binding, plan,
	))

	tests := []struct {
		name   string
		mutate func(*relaycommon.TaskInfo)
	}{
		{name: "result task id", mutate: func(result *relaycommon.TaskInfo) { result.TaskID = "cgt-other" }},
		{name: "result progress", mutate: func(result *relaycommon.TaskInfo) { result.Progress = "99%" }},
		{name: "missing proof", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof = nil }},
		{name: "proof schema", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.SchemaVersion = 2 }},
		{name: "proof protocol", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.Protocol = "other-protocol" }},
		{name: "proof task id", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.TaskID = "cgt-other" }},
		{name: "proof model", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.Model = "other-model" }},
		{name: "provider status", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.ProviderStatus = "processing" }},
		{name: "resolution", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.Resolution = "1080p" }},
		{name: "duration", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.DurationSeconds = 5 }},
		{name: "aspect ratio", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.AspectRatio = "9:16" }},
		{name: "frames", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.FramesPresent = true }},
		{name: "output count", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.OutputCount = 2 }},
		{name: "media type", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.MediaType = "image" }},
		{name: "failure owner", mutate: func(result *relaycommon.TaskInfo) { result.ProviderResultProof.FailureOwner = "provider" }},
		{name: "failure code", mutate: func(result *relaycommon.TaskInfo) {
			result.ProviderResultProof.FailureCode = "provider_service_failure"
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result := newResult()
			test.mutate(result)
			require.Error(t, validatePlatformGenerationRouteSuccessProof(
				result, "cgt-proof-exact", binding, plan,
			))
		})
	}
}

func TestPlatformGenerationRouteTestUnknownSubmissionNeverRetries(t *testing.T) {
	var submissions atomic.Int32
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		if request.Method == http.MethodPost {
			submissions.Add(1)
			hijacker, ok := w.(http.Hijacker)
			require.True(t, ok)
			connection, _, err := hijacker.Hijack()
			require.NoError(t, err)
			_ = connection.Close()
			return
		}
		http.NotFound(w, request)
	}))
	server.Start()
	defer server.Close()
	channel, binding := seedanceRouteTestFixture(t, server)

	err := runPlatformGenerationRouteChannelTest(context.Background(), channel, binding, time.Millisecond, time.Second)

	require.ErrorIs(t, err, errPlatformGenerationRouteSubmissionUnknown)
	require.EqualValues(t, 1, submissions.Load(), "an ambiguous POST outcome must never be replayed")
}

func TestPlatformGenerationRouteTestNeverFollowsPaidPOSTRedirect(t *testing.T) {
	for _, status := range []int{http.StatusTemporaryRedirect, http.StatusPermanentRedirect} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			var initialPosts atomic.Int32
			var redirectedRequests atomic.Int32
			target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
				redirectedRequests.Add(1)
				http.Error(w, "must not be reached", http.StatusInternalServerError)
			}))
			defer target.Close()
			provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
				require.Equal(t, http.MethodPost, request.Method)
				require.Equal(t, "/api/v3/contents/generations/tasks", request.URL.Path)
				initialPosts.Add(1)
				w.Header().Set("Location", target.URL+"/replayed-provider-post")
				w.WriteHeader(status)
			}))
			defer provider.Close()
			channel, binding := seedanceRouteTestFixture(t, provider)

			err := runPlatformGenerationRouteChannelTest(
				context.Background(), channel, binding, time.Millisecond, time.Second,
			)

			require.ErrorIs(t, err, errPlatformGenerationRouteSubmissionUnknown)
			require.EqualValues(t, 1, initialPosts.Load(), "the paid exact-route probe may issue only one provider POST")
			require.EqualValues(t, 0, redirectedRequests.Load(), "a redirect Location must never receive the paid POST")
		})
	}
}

func TestPlatformGenerationRouteTestSubmittedReplayPollsStoredTaskWithoutPOST(t *testing.T) {
	var submissions atomic.Int32
	var polls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if request.Method == http.MethodPost {
			submissions.Add(1)
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		polls.Add(1)
		require.Equal(t, "/api/v3/contents/generations/tasks/cgt-sticky-resume", request.URL.Path)
		_, _ = w.Write([]byte(`{"id":"cgt-sticky-resume","model":"doubao-seedance-2-0-260128","status":"failed","error":{"code":"InvalidParameter","message":"safe fixture"}}`))
	}))
	defer server.Close()
	channel, binding := seedanceRouteTestFixture(t, server)
	evidence := model.PlatformChannelTestArtifactEvidence{}
	recorded := false

	err := runPlatformGenerationRouteChannelTestDurable(
		context.Background(), channel, binding, time.Millisecond, time.Second,
		"cgt-sticky-resume",
		func(string) error {
			recorded = true
			return nil
		},
		&evidence,
	)

	require.Error(t, err)
	var classified *platformGenerationRouteTestError
	require.ErrorAs(t, err, &classified)
	require.True(t, classified.providerTerminal)
	require.Equal(t, model.PlatformChannelControlErrorTestValidation, classified.receiptCode)
	require.EqualValues(t, 0, submissions.Load(), "a submitted replay must never issue another provider POST")
	require.EqualValues(t, 1, polls.Load())
	require.False(t, recorded, "the already persisted provider task id must not be rebound")
}

func TestPlatformGenerationRouteTestTwoXXWithoutTaskIDRemainsUnknown(t *testing.T) {
	var submissions atomic.Int32
	var polls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if request.Method == http.MethodPost {
			submissions.Add(1)
			_, _ = w.Write([]byte(`{"status":"accepted-but-task-id-missing"}`))
			return
		}
		polls.Add(1)
		http.NotFound(w, request)
	}))
	defer server.Close()
	channel, binding := seedanceRouteTestFixture(t, server)
	evidence := model.PlatformChannelTestArtifactEvidence{}
	recorded := false

	err := runPlatformGenerationRouteChannelTestDurable(
		context.Background(), channel, binding, time.Millisecond, time.Second,
		"",
		func(string) error {
			recorded = true
			return nil
		},
		&evidence,
	)

	require.ErrorIs(t, err, errPlatformGenerationRouteSubmissionUnknown)
	require.EqualValues(t, 1, submissions.Load())
	require.Zero(t, polls.Load())
	require.False(t, recorded)
}

func TestPlatformGenerationRouteTestDeterministicProviderRejectionIsNotUnknown(t *testing.T) {
	var submissions atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		submissions.Add(1)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"error":{"code":"InvalidParameter","message":"SECRET_PROVIDER_DETAIL"}}`))
	}))
	defer server.Close()
	channel, binding := seedanceRouteTestFixture(t, server)
	evidence := model.PlatformChannelTestArtifactEvidence{}

	err := runPlatformGenerationRouteChannelTestDurable(
		context.Background(), channel, binding, time.Millisecond, time.Second,
		"", nil, &evidence,
	)

	require.Error(t, err)
	require.False(t, errors.Is(err, errPlatformGenerationRouteSubmissionUnknown))
	var classified *platformGenerationRouteTestError
	require.ErrorAs(t, err, &classified)
	require.True(t, classified.submissionRejected)
	require.Equal(t, model.PlatformChannelControlErrorTestValidation, classified.receiptCode)
	require.NotContains(t, err.Error(), "SECRET_PROVIDER_DETAIL")
	require.EqualValues(t, 1, submissions.Load())
}

func TestPlatformGenerationRouteTestAmbiguousProviderHTTPFailureRemainsUnknown(t *testing.T) {
	for _, statusCode := range []int{
		http.StatusRequestTimeout,
		http.StatusInternalServerError,
		http.StatusBadGateway,
		http.StatusServiceUnavailable,
		http.StatusGatewayTimeout,
	} {
		t.Run(http.StatusText(statusCode), func(t *testing.T) {
			var submissions atomic.Int32
			var polls atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
				if request.Method == http.MethodPost {
					submissions.Add(1)
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(statusCode)
					_, _ = w.Write([]byte(`{"error":{"code":"InternalError","message":"SECRET_PROVIDER_DETAIL"}}`))
					return
				}
				polls.Add(1)
				http.NotFound(w, request)
			}))
			defer server.Close()
			channel, binding := seedanceRouteTestFixture(t, server)
			evidence := model.PlatformChannelTestArtifactEvidence{}

			err := runPlatformGenerationRouteChannelTestDurable(
				context.Background(), channel, binding, time.Millisecond, time.Second,
				"", nil, &evidence,
			)

			require.ErrorIs(t, err, errPlatformGenerationRouteSubmissionUnknown)
			require.NotContains(t, err.Error(), "SECRET_PROVIDER_DETAIL")
			require.EqualValues(t, 1, submissions.Load())
			require.Zero(t, polls.Load())
		})
	}
}

func TestPlatformGenerationRouteTestTimesOutWithoutResubmitting(t *testing.T) {
	var submissions atomic.Int32
	var polls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if request.Method == http.MethodPost {
			submissions.Add(1)
			_, _ = w.Write([]byte(`{"id":"cgt-timeout"}`))
			return
		}
		polls.Add(1)
		_, _ = w.Write([]byte(`{"id":"cgt-timeout","model":"doubao-seedance-2-0-260128","status":"processing"}`))
	}))
	defer server.Close()
	channel, binding := seedanceRouteTestFixture(t, server)

	err := runPlatformGenerationRouteChannelTest(context.Background(), channel, binding, time.Millisecond, 20*time.Millisecond)

	require.Equal(t, model.PlatformChannelControlErrorTestUnavailable, platformGenerationRouteTestReceiptCode(err))
	require.EqualValues(t, 1, submissions.Load())
	require.Positive(t, polls.Load())
}

func TestPlatformGenerationRouteTestRejectsProviderTerminalFailure(t *testing.T) {
	var submissions atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if request.Method == http.MethodPost {
			submissions.Add(1)
			_, _ = w.Write([]byte(`{"id":"cgt-failed"}`))
			return
		}
		_, _ = w.Write([]byte(`{"id":"cgt-failed","model":"doubao-seedance-2-0-260128","status":"failed","error":{"code":"InvalidParameter","message":"SECRET_PROVIDER_MESSAGE https://signed.invalid/artifact?token=secret"}}`))
	}))
	defer server.Close()
	channel, binding := seedanceRouteTestFixture(t, server)

	err := runPlatformGenerationRouteChannelTest(context.Background(), channel, binding, time.Millisecond, time.Second)

	require.Error(t, err)
	require.False(t, errors.Is(err, errPlatformGenerationRouteSubmissionUnknown), "a provider terminal failure is proven, not submission_unknown")
	require.Equal(t, model.PlatformChannelControlErrorTestValidation, platformGenerationRouteTestReceiptCode(err))
	require.NotContains(t, err.Error(), "SECRET_PROVIDER_MESSAGE")
	require.NotContains(t, err.Error(), "signed.invalid")
	require.EqualValues(t, 1, submissions.Load())
}

func TestPlatformGenerationRouteTestClassifiesArkSensitiveContentWithoutLeakingProviderText(t *testing.T) {
	for _, providerCode := range []string{
		"InputTextSensitiveContentDetected",
		"InputImageSensitiveContentDetected",
		"OutputVideoSensitiveContentDetected",
	} {
		t.Run(providerCode, func(t *testing.T) {
			var submissions atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if request.Method == http.MethodPost {
					submissions.Add(1)
					_, _ = w.Write([]byte(`{"id":"cgt-sensitive-content"}`))
					return
				}
				_, _ = w.Write([]byte(`{"id":"cgt-sensitive-content","model":"doubao-seedance-2-0-260128","status":"failed","error":{"code":"` + providerCode + `","message":"SECRET_PROVIDER_MESSAGE https://signed.invalid/artifact?token=secret"}}`))
			}))
			defer server.Close()
			channel, binding := seedanceRouteTestFixture(t, server)

			err := runPlatformGenerationRouteChannelTest(context.Background(), channel, binding, time.Millisecond, time.Second)

			require.Error(t, err)
			require.Equal(t, model.PlatformChannelControlErrorTestValidation, platformGenerationRouteTestReceiptCode(err))
			require.NotContains(t, err.Error(), providerCode)
			require.NotContains(t, err.Error(), "SECRET_PROVIDER_MESSAGE")
			require.NotContains(t, err.Error(), "signed.invalid")
			require.EqualValues(t, 1, submissions.Load())
		})
	}
}

func TestPlatformGenerationRouteTestClassifiesSecretFreeProviderHTTPFailures(t *testing.T) {
	tests := []struct {
		name         string
		status       int
		body         string
		expectedCode string
	}{
		{name: "validation", status: http.StatusBadRequest, body: `{"error":{"code":"InvalidParameter","message":"SECRET_VALIDATION_MESSAGE"}}`, expectedCode: model.PlatformChannelControlErrorTestValidation},
		{name: "auth", status: http.StatusUnauthorized, body: `{"error":{"code":"InvalidAPIKey","message":"SECRET_AUTH_MESSAGE"}}`, expectedCode: model.PlatformChannelControlErrorTestAuth},
		{name: "quota", status: http.StatusTooManyRequests, body: `{"error":{"code":"QuotaExceeded","message":"SECRET_QUOTA_MESSAGE"}}`, expectedCode: model.PlatformChannelControlErrorTestQuota},
		{name: "volcengine set limit quota", status: http.StatusBadRequest, body: `{"error":{"code":"SetLimitExceeded","message":"SECRET_QUOTA_MESSAGE"}}`, expectedCode: model.PlatformChannelControlErrorTestQuota},
		{name: "terminal", status: http.StatusInternalServerError, body: `{"error":{"code":"InternalError","message":"SECRET_TERMINAL_MESSAGE"}}`, expectedCode: model.PlatformChannelControlErrorTestTerminal},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var submissions atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, request *http.Request) {
				submissions.Add(1)
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(test.status)
				_, _ = w.Write([]byte(test.body))
			}))
			defer server.Close()
			channel, binding := seedanceRouteTestFixture(t, server)

			err := runPlatformGenerationRouteChannelTest(context.Background(), channel, binding, time.Millisecond, time.Second)

			require.Error(t, err)
			require.Equal(t, test.expectedCode, platformGenerationRouteTestReceiptCode(err))
			require.NotContains(t, err.Error(), "SECRET_")
			require.EqualValues(t, 1, submissions.Load())
		})
	}
}
