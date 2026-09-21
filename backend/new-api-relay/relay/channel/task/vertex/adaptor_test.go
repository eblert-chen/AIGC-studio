package vertex

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	taskcommon "github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	vertexcore "github.com/QuantumNous/new-api/relay/channel/vertex"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func vertexRedirectTestAdaptor(t *testing.T, baseURL string) (*TaskAdaptor, *relaycommon.RelayInfo, *gin.Context) {
	t.Helper()
	credentials, err := common.Marshal(vertexcore.Credentials{ProjectID: "project-test"})
	require.NoError(t, err)
	info := &relaycommon.RelayInfo{
		OriginModelName: "veo-3.1",
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:       constant.ChannelTypeVertexAi,
			ChannelBaseUrl:    baseURL,
			ApiKey:            string(credentials),
			UpstreamModelName: "veo-3.1-generate-preview",
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PublicTaskID: "task_vertex_public"},
	}
	adaptor := &TaskAdaptor{
		acquireAccessToken: func(vertexcore.Credentials, string) (string, error) {
			return "vertex-oauth-secret", nil
		},
	}
	adaptor.Init(info)
	gin.SetMode(gin.TestMode)
	context, _ := gin.CreateTestContext(httptest.NewRecorder())
	context.Request = httptest.NewRequest(http.MethodPost, "/internal/platform-generations/native-submit", strings.NewReader("{}"))
	return adaptor, info, context
}

func TestVertexSubmitNeverFollowsOrForwardsCredentialOnRedirect(t *testing.T) {
	var targetCalls atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		targetCalls.Add(1)
		require.Empty(t, request.Header.Get("Authorization"))
		writer.WriteHeader(http.StatusOK)
	}))
	defer target.Close()
	origin := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		require.Equal(t, "Bearer vertex-oauth-secret", request.Header.Get("Authorization"))
		writer.Header().Set("Location", target.URL+"/credential-sink")
		writer.WriteHeader(http.StatusTemporaryRedirect)
	}))
	defer origin.Close()

	adaptor, info, context := vertexRedirectTestAdaptor(t, origin.URL)
	response, err := adaptor.DoRequest(context, info, strings.NewReader("{}"))
	require.NoError(t, err)
	defer response.Body.Close()
	require.Equal(t, http.StatusTemporaryRedirect, response.StatusCode)
	require.Zero(t, targetCalls.Load())
}

func TestVertexPollNeverFollowsOrForwardsCredentialOnRedirect(t *testing.T) {
	var targetCalls atomic.Int32
	target := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		targetCalls.Add(1)
		require.Empty(t, request.Header.Get("Authorization"))
		writer.WriteHeader(http.StatusOK)
	}))
	defer target.Close()
	origin := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		require.Equal(t, "Bearer vertex-oauth-secret", request.Header.Get("Authorization"))
		writer.Header().Set("Location", target.URL+"/credential-sink")
		writer.WriteHeader(http.StatusTemporaryRedirect)
	}))
	defer origin.Close()

	adaptor, info, _ := vertexRedirectTestAdaptor(t, origin.URL)
	operationName := "projects/project-test/locations/global/publishers/google/models/veo-3.1-generate-preview/operations/operation-test"
	response, err := adaptor.FetchTask(origin.URL, info.ApiKey, map[string]any{
		"task_id": taskcommon.EncodeLocalTaskID(operationName),
	}, "")
	require.NoError(t, err)
	defer response.Body.Close()
	_, err = io.ReadAll(response.Body)
	require.NoError(t, err)
	require.Equal(t, http.StatusTemporaryRedirect, response.StatusCode)
	require.Zero(t, targetCalls.Load())
}
