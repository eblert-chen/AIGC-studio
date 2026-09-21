package gemini

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	taskdto "github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay/channel"
	taskcommon "github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/setting/model_setting"
	"github.com/gin-gonic/gin"
	"github.com/pkg/errors"
)

// ============================
// Adaptor implementation
// ============================

type TaskAdaptor struct {
	taskcommon.BaseBilling
	ChannelType int
	apiKey      string
	baseURL     string
}

func (a *TaskAdaptor) Init(info *relaycommon.RelayInfo) {
	a.ChannelType = info.ChannelType
	a.baseURL = info.ChannelBaseUrl
	a.apiKey = info.ApiKey
}

// ValidateRequestAndSetAction parses body, validates fields and sets default action.
func (a *TaskAdaptor) ValidateRequestAndSetAction(c *gin.Context, info *relaycommon.RelayInfo) (taskErr *taskdto.TaskError) {
	if taskErr := relaycommon.ValidateBasicTaskRequest(c, info, constant.TaskActionTextGenerate); taskErr != nil {
		return taskErr
	}
	req, err := relaycommon.GetTaskRequest(c)
	if err != nil {
		return service.TaskErrorWrapperLocal(err, "invalid_request", http.StatusBadRequest)
	}
	if _, err := resolveVeoTaskSpec(req, info.UpstreamModelName); err != nil {
		return service.TaskErrorWrapperLocal(err, "invalid_request", http.StatusBadRequest)
	}
	if len(req.Images) > 1 || len(req.Videos) != 0 || len(req.Audios) != 0 {
		return service.TaskErrorWrapperLocal(fmt.Errorf("Gemini Veo accepts at most one initial image in this adapter profile"), "invalid_request", http.StatusBadRequest)
	}
	if len(req.Images) == 1 || strings.TrimSpace(req.Image) != "" {
		info.Action = constant.TaskActionGenerate
	}
	return nil
}

// BuildRequestURL constructs the Gemini API predictLongRunning endpoint for Veo.
func (a *TaskAdaptor) BuildRequestURL(info *relaycommon.RelayInfo) (string, error) {
	modelName := info.UpstreamModelName
	if _, supported := veoModelCodes[modelName]; !supported {
		return "", fmt.Errorf("unsupported Gemini Veo model")
	}
	version := model_setting.GetGeminiVersionSetting(modelName)

	return fmt.Sprintf(
		"%s/%s/models/%s:predictLongRunning",
		a.baseURL,
		version,
		modelName,
	), nil
}

// BuildRequestHeader sets required headers.
func (a *TaskAdaptor) BuildRequestHeader(c *gin.Context, req *http.Request, info *relaycommon.RelayInfo) error {
	if strings.TrimSpace(a.apiKey) == "" {
		return fmt.Errorf("Gemini API key is missing")
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("x-goog-api-key", a.apiKey)
	return nil
}

// BuildRequestBody converts request into the Veo predictLongRunning format.
func (a *TaskAdaptor) BuildRequestBody(c *gin.Context, info *relaycommon.RelayInfo) (io.Reader, error) {
	v, ok := c.Get("task_request")
	if !ok {
		return nil, fmt.Errorf("request not found in context")
	}
	req, ok := v.(relaycommon.TaskSubmitReq)
	if !ok {
		return nil, fmt.Errorf("unexpected task_request type")
	}

	spec, err := resolveVeoTaskSpec(req, info.UpstreamModelName)
	if err != nil {
		return nil, err
	}
	instance := VeoInstance{Prompt: req.Prompt}
	if img := ExtractMultipartImage(c, info); img != nil {
		instance.Image = img
	} else if len(req.Images) > 0 {
		if parsed := ParseImageInput(req.Images[0]); parsed != nil {
			instance.Image = parsed
			info.Action = constant.TaskActionGenerate
		} else {
			return nil, fmt.Errorf("Gemini Veo initial image must be valid inline base64")
		}
	}

	params := &VeoParameters{}
	if err := taskcommon.UnmarshalMetadata(req.Metadata, params); err != nil {
		return nil, errors.Wrap(err, "unmarshal metadata failed")
	}
	params.DurationSeconds = spec.DurationSeconds
	params.Resolution = spec.Resolution
	params.AspectRatio = spec.AspectRatio
	params.NumberOfVideos = 1
	params.SampleCount = 0
	params.StorageUri = ""

	body := VeoRequestPayload{
		Instances:  []VeoInstance{instance},
		Parameters: params,
	}

	data, err := common.Marshal(body)
	if err != nil {
		return nil, err
	}
	return bytes.NewReader(data), nil
}

// DoRequest delegates to common helper.
func (a *TaskAdaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (*http.Response, error) {
	// Google long-running generation is a paid, non-idempotent POST. Never let
	// net/http replay it at a redirect target (307/308 preserve both method and
	// credentials). A 3xx is returned to the Relay state machine as a provider
	// failure instead.
	return channel.DoTaskApiRequestNoRedirect(a, c, info, requestBody)
}

// DoResponse handles upstream response, returns taskID etc.
func (a *TaskAdaptor) DoResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (taskID string, taskData []byte, taskErr *taskdto.TaskError) {
	responseBody, err := relaycommon.ReadProviderTaskResponseBody(resp.Body)
	if err != nil {
		return "", nil, service.TaskErrorWrapper(err, "read_response_body_failed", http.StatusInternalServerError)
	}
	_ = resp.Body.Close()

	if err := common.RejectDuplicateJSONKeys(responseBody); err != nil {
		return "", nil, service.TaskErrorWrapperLocal(fmt.Errorf("Gemini Veo submit response is invalid"), "invalid_response", http.StatusBadGateway)
	}
	var s submitResponse
	if err := common.Unmarshal(responseBody, &s); err != nil {
		return "", nil, service.TaskErrorWrapperLocal(fmt.Errorf("Gemini Veo submit response is invalid"), "invalid_response", http.StatusBadGateway)
	}
	req, err := relaycommon.GetTaskRequest(c)
	if err != nil {
		return "", nil, service.TaskErrorWrapperLocal(fmt.Errorf("Gemini Veo request identity is unavailable"), "submission_result_uncertain", http.StatusBadGateway)
	}
	spec, err := resolveVeoTaskSpec(req, info.UpstreamModelName)
	if err != nil {
		return "", nil, service.TaskErrorWrapperLocal(fmt.Errorf("Gemini Veo request identity is invalid"), "submission_result_uncertain", http.StatusBadGateway)
	}
	taskID, err = buildVeoTaskID(s.Name, spec)
	if err != nil {
		return "", nil, service.TaskErrorWrapperLocal(err, "submission_result_uncertain", http.StatusBadGateway)
	}
	taskData, err = common.Marshal(veoSubmissionReceipt{
		SchemaVersion:   1,
		Object:          veoSubmissionReceiptName,
		TaskID:          taskID,
		OperationName:   s.Name,
		ProviderModel:   spec.Model,
		DurationSeconds: spec.DurationSeconds,
		Resolution:      spec.Resolution,
		AspectRatio:     spec.AspectRatio,
	})
	if err != nil {
		return "", nil, service.TaskErrorWrapper(err, "marshal_response_failed", http.StatusInternalServerError)
	}
	ov := dto.NewOpenAIVideo()
	ov.ID = info.PublicTaskID
	ov.TaskID = info.PublicTaskID
	ov.CreatedAt = time.Now().Unix()
	ov.Model = info.OriginModelName
	c.JSON(http.StatusOK, ov)
	return taskID, taskData, nil
}

func (a *TaskAdaptor) GetModelList() []string {
	return []string{
		"veo-3.0-generate-001",
		"veo-3.0-fast-generate-001",
		"veo-3.1-generate-preview",
		"veo-3.1-fast-generate-preview",
	}
}

func (a *TaskAdaptor) GetChannelName() string {
	return "gemini"
}

// EstimateBilling returns OtherRatios based on durationSeconds and resolution.
func (a *TaskAdaptor) EstimateBilling(c *gin.Context, info *relaycommon.RelayInfo) map[string]float64 {
	v, ok := c.Get("task_request")
	if !ok {
		return nil
	}
	req, ok := v.(relaycommon.TaskSubmitReq)
	if !ok {
		return nil
	}

	seconds := ResolveVeoDuration(req.Metadata, req.Duration, req.Seconds)
	resolution := ResolveVeoResolution(req.Metadata, req.Size)
	resRatio := VeoResolutionRatio(info.UpstreamModelName, resolution)

	return map[string]float64{
		"seconds":    float64(seconds),
		"resolution": resRatio,
	}
}

// FetchTask polls task status via the Gemini operations GET endpoint.
func (a *TaskAdaptor) FetchTask(baseUrl, key string, body map[string]any, proxy string) (*http.Response, error) {
	return a.FetchTaskWithContext(context.Background(), baseUrl, key, body, proxy)
}

func (a *TaskAdaptor) FetchTaskWithContext(ctx context.Context, baseUrl, key string, body map[string]any, proxy string) (*http.Response, error) {
	taskID, ok := body["task_id"].(string)
	if !ok {
		return nil, fmt.Errorf("invalid task_id")
	}

	identity, identityErr := parseVeoTaskID(taskID)
	upstreamName := ""
	if identityErr == nil {
		upstreamName = identity.OperationName
		if providerModel, _ := body["provider_model"].(string); providerModel != "" && providerModel != identity.Model {
			return nil, fmt.Errorf("Gemini Veo polling model drifted from its task identity")
		}
	} else {
		var err error
		upstreamName, err = decodeLegacyVeoTaskID(taskID)
		if err != nil {
			return nil, fmt.Errorf("decode task_id failed: %w", err)
		}
	}

	requestURL, err := buildVeoOperationURL(baseUrl, upstreamName)
	if err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, requestURL, nil)
	if err != nil {
		return nil, err
	}

	req.Header.Set("Accept", "application/json")
	if strings.TrimSpace(key) == "" {
		return nil, fmt.Errorf("Gemini API key is missing")
	}
	req.Header.Set("x-goog-api-key", key)

	client, err := service.GetHttpClientWithProxy(proxy)
	if err != nil {
		return nil, fmt.Errorf("new proxy http client failed: %w", err)
	}
	isolated := *client
	isolated.CheckRedirect = func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}
	response, err := isolated.Do(req)
	if err != nil || response == nil || response.Body == nil || identityErr != nil ||
		response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return response, err
	}
	raw, err := relaycommon.ReadProviderTaskResponseBody(response.Body)
	_ = response.Body.Close()
	if err != nil {
		return nil, err
	}
	providerModel, _ := body["provider_model"].(string)
	envelope, err := buildVeoOperationEnvelope(raw, taskID, providerModel)
	if err != nil {
		return nil, err
	}
	return replaceVeoResponseBody(response, envelope), nil
}

func (a *TaskAdaptor) ParseTaskResult(respBody []byte) (*relaycommon.TaskInfo, error) {
	if err := common.RejectDuplicateJSONKeys(respBody); err != nil {
		return nil, fmt.Errorf("Gemini Veo task response contains duplicate fields")
	}
	var discriminator struct {
		Object string `json:"object"`
		TaskID string `json:"task_id"`
	}
	if err := common.Unmarshal(respBody, &discriminator); err != nil {
		return nil, fmt.Errorf("unmarshal operation response failed: %w", err)
	}
	if discriminator.Object == veoOperationEnvelopeName || strings.HasPrefix(discriminator.TaskID, veoTaskIdentityPrefix+":") {
		return parseVeoOperationEnvelope(respBody)
	}
	return parseLegacyVeoOperationResult(respBody)
}

func parseVeoOperationEnvelope(respBody []byte) (*relaycommon.TaskInfo, error) {
	var envelope veoOperationEnvelope
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(string(respBody)), &envelope); err != nil {
		return nil, fmt.Errorf("decode Gemini Veo operation envelope failed: %w", err)
	}
	identity, err := parseVeoTaskID(envelope.TaskID)
	if err != nil {
		return nil, err
	}
	if envelope.SchemaVersion != 1 || envelope.Object != veoOperationEnvelopeName ||
		envelope.OperationName != identity.OperationName || envelope.ProviderModel != identity.Model ||
		envelope.DurationSeconds != identity.DurationSeconds || envelope.Resolution != identity.Resolution ||
		envelope.AspectRatio != identity.AspectRatio {
		return nil, fmt.Errorf("Gemini Veo operation envelope binding is invalid")
	}
	if err := validateVeoEnvelopeDigest(envelope); err != nil {
		return nil, err
	}
	result := &relaycommon.TaskInfo{
		Code:   0,
		TaskID: envelope.TaskID,
		ProviderResultProof: &relaycommon.ProviderTaskResultProof{
			SchemaVersion:  1,
			Protocol:       ProviderProtocolV1,
			TaskID:         envelope.TaskID,
			Model:          envelope.ProviderModel,
			ProviderStatus: envelope.ProviderState,
		},
	}
	switch envelope.ProviderState {
	case "processing":
		if envelope.ArtifactURL != "" || envelope.FailureOwner != "" || envelope.FailureCode != "" {
			return nil, fmt.Errorf("Gemini Veo processing envelope contains terminal material")
		}
		result.Status = model.TaskStatusInProgress
		result.Progress = taskcommon.ProgressInProgress
	case "succeeded":
		artifactURL, err := canonicalVeoFileDownloadURL(envelope.ArtifactURL)
		if err != nil || artifactURL != envelope.ArtifactURL || envelope.FailureOwner != "" || envelope.FailureCode != "" {
			return nil, fmt.Errorf("Gemini Veo success envelope is invalid")
		}
		result.Status = model.TaskStatusSuccess
		result.Progress = taskcommon.ProgressComplete
		result.Url = artifactURL
		result.RemoteUrl = artifactURL
		result.ProviderResultProof.Resolution = identity.Resolution
		result.ProviderResultProof.DurationSeconds = identity.DurationSeconds
		result.ProviderResultProof.AspectRatio = identity.AspectRatio
		result.ProviderResultProof.OutputCount = 1
		result.ProviderResultProof.MediaType = "video"
	case "failed":
		if envelope.ArtifactURL != "" || (envelope.FailureOwner != "client" && envelope.FailureOwner != "provider") ||
			(envelope.FailureCode != "content_policy_rejected" && envelope.FailureCode != "provider_service_failure") {
			return nil, fmt.Errorf("Gemini Veo failure envelope is invalid")
		}
		if (envelope.FailureOwner == "client") != (envelope.FailureCode == "content_policy_rejected") {
			return nil, fmt.Errorf("Gemini Veo failure classification is inconsistent")
		}
		result.Status = model.TaskStatusFailure
		result.Progress = taskcommon.ProgressComplete
		result.Reason = "Gemini Veo generation failed"
		result.ProviderResultProof.FailureOwner = envelope.FailureOwner
		result.ProviderResultProof.FailureCode = envelope.FailureCode
	default:
		return nil, fmt.Errorf("Gemini Veo operation state is unknown")
	}
	return result, nil
}

func parseLegacyVeoOperationResult(respBody []byte) (*relaycommon.TaskInfo, error) {
	var op operationResponse
	if err := common.Unmarshal(respBody, &op); err != nil {
		return nil, fmt.Errorf("unmarshal operation response failed: %w", err)
	}

	ti := &relaycommon.TaskInfo{}

	if op.Error.Message != "" {
		ti.Status = model.TaskStatusFailure
		ti.Reason = op.Error.Message
		ti.Progress = "100%"
		return ti, nil
	}

	if !op.Done {
		ti.Status = model.TaskStatusInProgress
		ti.Progress = "50%"
		return ti, nil
	}

	ti.Status = model.TaskStatusSuccess
	ti.Progress = "100%"

	ti.TaskID = taskcommon.EncodeLocalTaskID(op.Name)

	outputs := append([]generatedVideo(nil), op.Response.GenerateVideoResponse.GeneratedSamples...)
	outputs = append(outputs, op.Response.GenerateVideoResponse.GeneratedVideos...)
	if len(outputs) == 1 {
		if uri, err := canonicalVeoFileDownloadURL(outputs[0].Video.URI); err == nil {
			ti.RemoteUrl = uri
		}
	}

	return ti, nil
}

func (a *TaskAdaptor) ConvertToOpenAIVideo(task *model.Task) ([]byte, error) {
	upstreamTaskID := task.GetUpstreamTaskID()
	modelName := ""
	if identity, err := parseVeoTaskID(upstreamTaskID); err == nil {
		modelName = identity.Model
	} else if upstreamName, legacyErr := decodeLegacyVeoTaskID(upstreamTaskID); legacyErr == nil {
		modelName, _, _ = parseVeoOperationName(upstreamName)
	}
	if strings.TrimSpace(modelName) == "" {
		modelName = "veo-3.0-generate-001"
	}

	video := dto.NewOpenAIVideo()
	video.ID = task.TaskID
	video.Model = modelName
	video.Status = task.Status.ToVideoStatus()
	video.SetProgressStr(task.Progress)
	video.CreatedAt = task.CreatedAt
	if task.FinishTime > 0 {
		video.CompletedAt = task.FinishTime
	} else if task.UpdatedAt > 0 {
		video.CompletedAt = task.UpdatedAt
	}

	return common.Marshal(video)
}
