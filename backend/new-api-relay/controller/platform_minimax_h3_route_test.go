package controller

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	relayservice "github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type miniMaxH3RouteTestTransport struct {
	target   *url.URL
	delegate http.RoundTripper
}

func (transport miniMaxH3RouteTestTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	clone := request.Clone(request.Context())
	clonedURL := *request.URL
	clonedURL.Scheme = transport.target.Scheme
	clonedURL.Host = transport.target.Host
	clone.URL = &clonedURL
	return transport.delegate.RoundTrip(clone)
}

func miniMaxH3RouteTestFixture(t *testing.T, server *httptest.Server, modelID string) (*model.Channel, *relayservice.PlatformGenerationRouteTestBinding) {
	t.Helper()
	manifest, found, err := generationprofile.ResolveMiniMaxH3ProviderModel(modelID)
	require.NoError(t, err)
	require.True(t, found)
	profile, found := generationprofile.Get(manifest.AdapterProfileID)
	require.True(t, found)
	client, err := relayservice.GetHttpClientWithProxy("")
	require.NoError(t, err)
	require.NotNil(t, client)
	original := *client
	target, err := url.Parse(server.URL)
	require.NoError(t, err)
	client.Transport = miniMaxH3RouteTestTransport{
		target: target, delegate: server.Client().Transport,
	}
	t.Cleanup(func() { *client = original })
	// The production contract accepts only an approved MiniMax origin.  The
	// test transport resolves that exact logical origin into the private TLS
	// fixture without weakening the provider-origin validator.
	baseURL := "https://api.minimax.cn"
	return &model.Channel{
			Id: 73, Type: constant.ChannelTypeMiniMax, Key: "fixture-h3-route-key", BaseURL: &baseURL,
		}, &relayservice.PlatformGenerationRouteTestBinding{
			PublicModelID: manifest.PublicModelID, RouteID: "h3-offline-probe", ChannelID: 73,
			NativeChannelType: constant.ChannelTypeMiniMax, UpstreamModel: modelID,
			CapabilityProfileID: profile.ID, CapabilityProfileRevision: profile.Revision,
		}
}

func miniMaxH3ProbeSuccess(taskID, modelID, resolution string, duration int) string {
	return fmt.Sprintf(`{"task":{"id":%q,"model":%q,"status":"succeeded","task_type":"generation","modality":"video","resolution":%q,"duration":%d,"ratio":"16:9","content":{"url":"https://artifacts.example/h3-probe.mp4?signature=private"},"usage":{"total_seconds":%d,"input_seconds":0,"output_seconds":%d,"input_image_count":0,"input_audio_seconds":0,"total_tokens":0,"prompt_tokens":0,"completion_tokens":0}}}`,
		taskID, modelID, strings.ToUpper(resolution), duration, duration, duration)
}

func TestMiniMaxH3RouteProbeBindsBothModelsToOneSubmitAndStickyPoll(t *testing.T) {
	for _, test := range []struct {
		model, resolution string
		duration          int
	}{{"MiniMax-H3", "768p", 4}, {"MiniMax-H3-Max", "480p", 5}} {
		t.Run(test.model, func(t *testing.T) {
			var posts, polls, verifies atomic.Int32
			originalVerifier := verifyPlatformGenerationRouteTestArtifact
			verifyPlatformGenerationRouteTestArtifact = func(_ context.Context, locator string, artifact generationprofile.ArtifactContract) (model.PlatformChannelTestArtifactEvidence, error) {
				verifies.Add(1)
				assert.Equal(t, "https://artifacts.example/h3-probe.mp4?signature=private", locator)
				assert.Equal(t, generationprofile.ArtifactContract{MediaType: "video", ContentType: "video/mp4", Count: 1}, artifact)
				return model.PlatformChannelTestArtifactEvidence{SHA256: strings.Repeat("a", 64), SizeBytes: 128, ContentType: "video/mp4"}, nil
			}
			t.Cleanup(func() { verifyPlatformGenerationRouteTestArtifact = originalVerifier })
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				assert.Equal(t, "Bearer fixture-h3-route-key", r.Header.Get("Authorization"))
				w.Header().Set("Content-Type", "application/json")
				switch r.Method + " " + r.URL.Path {
				case "POST /v2/video_generation":
					posts.Add(1)
					raw, err := io.ReadAll(r.Body)
					require.NoError(t, err)
					var payload map[string]any
					require.NoError(t, common.Unmarshal(raw, &payload))
					assert.Len(t, payload, 5, "only reviewed provider fields may leave Relay")
					assert.Equal(t, test.model, payload["model"])
					assert.Equal(t, strings.ToUpper(test.resolution), payload["resolution"])
					assert.EqualValues(t, test.duration, payload["duration"])
					assert.Equal(t, "16:9", payload["ratio"])
					_, _ = io.WriteString(w, `{"task_id":"h3-exact-probe"}`)
				case "GET /v2/query/video_generation/h3-exact-probe":
					if polls.Add(1) == 1 {
						_, _ = fmt.Fprintf(w, `{"task":{"id":"h3-exact-probe","model":%q,"status":"queued","task_type":"generation","modality":"video"}}`, test.model)
						return
					}
					_, _ = io.WriteString(w, miniMaxH3ProbeSuccess("h3-exact-probe", test.model, test.resolution, test.duration))
				default:
					http.NotFound(w, r)
				}
			}))
			t.Cleanup(server.Close)
			channel, binding := miniMaxH3RouteTestFixture(t, server, test.model)
			evidence := model.PlatformChannelTestArtifactEvidence{}
			recordedID := ""
			err := runPlatformGenerationRouteChannelTestDurable(context.Background(), channel, binding, time.Millisecond, time.Second,
				"", func(taskID string) error { recordedID = taskID; return nil }, &evidence)
			require.NoError(t, err)
			assert.Equal(t, "h3-exact-probe", recordedID)
			assert.Equal(t, "video/mp4", evidence.ContentType)
			assert.EqualValues(t, 1, posts.Load())
			assert.EqualValues(t, 2, polls.Load())
			assert.EqualValues(t, 1, verifies.Load())

			// Retrying the acceptance check resumes the stored provider task,
			// never another paid POST, including after the prior HTTP turn ended.
			err = runPlatformGenerationRouteChannelTestDurable(context.Background(), channel, binding, time.Millisecond, time.Second,
				recordedID, func(string) error { t.Error("resume cannot create another task"); return nil }, &evidence)
			require.NoError(t, err)
			assert.EqualValues(t, 1, posts.Load())
		})
	}
}

func TestMiniMaxH3RouteProbeNeverAcceptsMismatchedTerminalProof(t *testing.T) {
	valid := miniMaxH3ProbeSuccess("h3-probe", "MiniMax-H3", "768p", 4)
	for name, response := range map[string]string{
		"different model":      strings.Replace(valid, `"MiniMax-H3"`, `"MiniMax-H3-Max"`, 1),
		"different task":       strings.Replace(valid, `"h3-probe"`, `"other-task"`, 1),
		"different resolution": strings.Replace(valid, `"768P"`, `"2K"`, 1),
		"different duration":   strings.Replace(valid, `"duration":4`, `"duration":5`, 1),
		"prompt only task":     strings.Replace(valid, `"modality":"video"`, `"modality":"text"`, 1),
	} {
		t.Run(name, func(t *testing.T) {
			var posts, verifies atomic.Int32
			originalVerifier := verifyPlatformGenerationRouteTestArtifact
			verifyPlatformGenerationRouteTestArtifact = func(context.Context, string, generationprofile.ArtifactContract) (model.PlatformChannelTestArtifactEvidence, error) {
				verifies.Add(1)
				return model.PlatformChannelTestArtifactEvidence{}, nil
			}
			t.Cleanup(func() { verifyPlatformGenerationRouteTestArtifact = originalVerifier })
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == http.MethodPost {
					posts.Add(1)
				}
				_, _ = io.WriteString(w, response)
			}))
			t.Cleanup(server.Close)
			channel, binding := miniMaxH3RouteTestFixture(t, server, "MiniMax-H3")
			err := runPlatformGenerationRouteChannelTestDurable(context.Background(), channel, binding, time.Millisecond, time.Second,
				"h3-probe", nil, &model.PlatformChannelTestArtifactEvidence{})
			var classified *platformGenerationRouteTestError
			require.ErrorAs(t, err, &classified)
			assert.True(t, classified.pollPending)
			assert.False(t, classified.submissionRejected)
			assert.False(t, classified.providerTerminal)
			assert.Zero(t, posts.Load())
			assert.Zero(t, verifies.Load())
		})
	}
}

func TestMiniMaxH3RouteProbeDistinguishesRejectionFromUnknownWithoutResubmit(t *testing.T) {
	for _, test := range []struct {
		name     string
		status   int
		body     string
		rejected bool
	}{
		{"invalid parameters", 400, `{"error":{"type":"invalid_params","message":"private upstream diagnostic"}}`, true},
		{"insufficient balance", 402, `{"error":{"type":"insufficient_balance"}}`, true},
		{"bad acknowledgement", 200, `{"task_id":""}`, false},
		{"duplicate acknowledgement", 200, `{"task_id":"first","task_id":"second"}`, false},
		{"server error", 500, `{"error":{"message":"private upstream diagnostic"}}`, false},
		{"redirect", 307, `{}`, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			var posts atomic.Int32
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				assert.Equal(t, http.MethodPost, r.Method)
				posts.Add(1)
				if test.status == 307 {
					w.Header().Set("Location", "/must-not-follow")
				}
				w.WriteHeader(test.status)
				_, _ = io.WriteString(w, test.body)
			}))
			t.Cleanup(server.Close)
			channel, binding := miniMaxH3RouteTestFixture(t, server, "MiniMax-H3")
			err := runPlatformGenerationRouteChannelTest(context.Background(), channel, binding, time.Millisecond, time.Second)
			var classified *platformGenerationRouteTestError
			require.ErrorAs(t, err, &classified)
			assert.Equal(t, test.rejected, classified.submissionRejected)
			assert.Equal(t, !test.rejected, classified.submissionUnknown)
			assert.EqualValues(t, 1, posts.Load())
			assert.NotContains(t, err.Error(), "private upstream diagnostic")
		})
	}
}
