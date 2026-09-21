package relay

import (
	"bytes"
	"errors"
	"io"
	"net/http"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/stretchr/testify/require"
)

func TestPlatformExternalTaskSerializersSuppressProviderTransferCredentials(t *testing.T) {
	providerURL := "https://provider.example/result.png?X-Signature=secret"
	task := &model.Task{
		TaskID: "platform-native-task", Status: model.TaskStatusSuccess, Progress: "100%",
		FailReason: providerURL,
		Data:       []byte(`{"content":{"video_url":"` + providerURL + `"}}`),
		PrivateData: model.TaskPrivateData{
			BillingSource: model.TaskBillingSourcePlatformExternal,
			ResultURL:     providerURL,
		},
	}

	publicDTO := TaskModel2Dto(task)
	require.Empty(t, publicDTO.ResultURL)
	require.Equal(t, "provider task failed", publicDTO.FailReason)
	require.Empty(t, publicDTO.Data)
	serializedDTO, err := common.Marshal(publicDTO)
	require.NoError(t, err)
	require.NotContains(t, string(serializedDTO), providerURL)
	require.NotContains(t, string(serializedDTO), "X-Signature")
	serializedVideo, err := common.Marshal(task.ToOpenAIVideo())
	require.NoError(t, err)
	require.NotContains(t, string(serializedVideo), providerURL)
	require.NotContains(t, string(serializedVideo), "X-Signature")

	ordinary := *task
	ordinary.PrivateData.BillingSource = "wallet"
	require.Equal(t, providerURL, TaskModel2Dto(&ordinary).ResultURL)
}

func TestBufferProviderTaskResponseRejectsMissingAndOversizedBodies(t *testing.T) {
	exact := bytes.Repeat([]byte{'a'}, relaycommon.ProviderTaskResponseBodyLimit)
	response := &http.Response{Body: io.NopCloser(bytes.NewReader(exact))}
	contents, err := bufferProviderTaskResponse(response)
	require.NoError(t, err)
	require.Len(t, contents, relaycommon.ProviderTaskResponseBodyLimit)
	replayed, err := io.ReadAll(response.Body)
	require.NoError(t, err)
	require.Equal(t, exact, replayed, "the validated body must be replayable by the provider adaptor")

	oversized := &http.Response{Body: io.NopCloser(bytes.NewReader(
		bytes.Repeat([]byte{'b'}, relaycommon.ProviderTaskResponseBodyLimit+1),
	))}
	contents, err = bufferProviderTaskResponse(oversized)
	require.ErrorIs(t, err, relaycommon.ErrProviderTaskResponseBodyTooLarge)
	require.Nil(t, contents)

	contents, err = bufferProviderTaskResponse(&http.Response{})
	require.ErrorIs(t, err, relaycommon.ErrProviderTaskResponseBodyMissing)
	require.Nil(t, contents)
}

func TestProtectedProviderTaskErrorsNeverExposeProviderEvidence(t *testing.T) {
	providerURL := "https://provider.example/result.png?X-Signature=secret"
	for _, test := range []struct {
		name       string
		code       string
		statusCode int
		evidence   []byte
	}{
		{name: "DoRequest error", code: "do_request_failed", statusCode: 502, evidence: []byte(errors.New("POST " + providerURL).Error())},
		{name: "non-200 body", code: "provider_response_status", statusCode: 429, evidence: []byte(`{"error":"` + providerURL + `"}`)},
		{name: "DoResponse parse error", code: "provider_response_invalid", statusCode: 502, evidence: []byte("malformed payload " + providerURL)},
		{name: "missing response", code: "provider_response_missing", statusCode: 502},
	} {
		t.Run(test.name, func(t *testing.T) {
			taskErr := protectedProviderTaskError(test.code, test.statusCode, test.evidence)
			require.Equal(t, test.code, taskErr.Code)
			require.Equal(t, test.statusCode, taskErr.StatusCode)
			require.NotContains(t, taskErr.Message, providerURL)
			require.NotContains(t, taskErr.Message, "X-Signature")
			require.NotContains(t, taskErr.Error.Error(), providerURL)
		})
	}
}

func TestValidateImmediateTerminalTaskResultNormalizesValidatedURL(t *testing.T) {
	upstreamTaskID := "seedream:0123456789abcdef"
	result, err := validateImmediateTerminalTaskResult(upstreamTaskID, &relaycommon.TaskInfo{
		TaskID:   upstreamTaskID,
		Status:   string(model.TaskStatusSuccess),
		Progress: "100%",
		Url:      "  https://provider.example/result.png?signature=test  ",
	})

	require.NoError(t, err)
	require.NotNil(t, result)
	require.Equal(t, upstreamTaskID, result.TaskID)
	require.Equal(t, "https://provider.example/result.png?signature=test", result.Url)
}

func TestValidateImmediateTerminalTaskResultLeavesAsyncAdaptersUnchanged(t *testing.T) {
	result, err := validateImmediateTerminalTaskResult("ordinary-provider-task", nil)
	require.NoError(t, err)
	require.Nil(t, result)
}

func TestValidateImmediateTerminalTaskResultRejectsIdentityAndURLDrift(t *testing.T) {
	valid := func() *relaycommon.TaskInfo {
		return &relaycommon.TaskInfo{
			TaskID:   "seedream:0123456789abcdef",
			Status:   string(model.TaskStatusSuccess),
			Progress: "100%",
			Url:      "https://provider.example/result.png",
		}
	}

	tests := []struct {
		name       string
		upstreamID string
		mutate     func(*relaycommon.TaskInfo)
	}{
		{
			name:       "DoResponse id mismatch",
			upstreamID: "seedream:fedcba9876543210",
		},
		{
			name:       "control character in id",
			upstreamID: "seedream:0123456789abcdef\n",
			mutate: func(result *relaycommon.TaskInfo) {
				result.TaskID = "seedream:0123456789abcdef\n"
			},
		},
		{
			name:       "non HTTPS artifact",
			upstreamID: "seedream:0123456789abcdef",
			mutate: func(result *relaycommon.TaskInfo) {
				result.Url = "http://provider.example/result.png"
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result := valid()
			if test.mutate != nil {
				test.mutate(result)
			}
			validated, err := validateImmediateTerminalTaskResult(test.upstreamID, result)
			require.Error(t, err)
			require.Nil(t, validated)
		})
	}
}
