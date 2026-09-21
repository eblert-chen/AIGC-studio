package geminiomni

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	taskdto "github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay/channel"
	taskcommon "github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	openai "github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

type TaskAdaptor struct {
	taskcommon.BaseBilling
	ChannelType int
	apiKey      string
	baseURL     string
	submitSpec  requestSpec
}

func (a *TaskAdaptor) Init(info *relaycommon.RelayInfo) {
	a.ChannelType = info.ChannelType
	a.apiKey = info.ApiKey
	a.baseURL = info.ChannelBaseUrl
}

func (a *TaskAdaptor) ValidateRequestAndSetAction(c *gin.Context, info *relaycommon.RelayInfo) *taskdto.TaskError {
	if taskErr := relaycommon.ValidateBasicTaskRequest(c, info, constant.TaskActionTextGenerate); taskErr != nil {
		return taskErr
	}
	req, err := relaycommon.GetTaskRequest(c)
	if err != nil {
		return service.TaskErrorWrapperLocal(err, "invalid_request", http.StatusBadRequest)
	}
	spec, err := resolveRequestSpec(req)
	if err != nil {
		return service.TaskErrorWrapperLocal(err, "invalid_request", http.StatusBadRequest)
	}
	if err := validateTaskRequest(req, spec); err != nil {
		return service.TaskErrorWrapperLocal(err, "invalid_request", http.StatusBadRequest)
	}
	if len(req.Images) > 0 || strings.TrimSpace(req.Image) != "" || len(normalizedVideos(req)) > 0 || spec.PreviousInteractionID != "" {
		info.Action = constant.TaskActionGenerate
	}
	return nil
}

func (a *TaskAdaptor) BuildRequestURL(_ *relaycommon.RelayInfo) (string, error) {
	base, err := url.Parse(strings.TrimRight(strings.TrimSpace(a.baseURL), "/"))
	if err != nil || base.Scheme == "" || base.Host == "" || base.User != nil || base.RawQuery != "" || base.Fragment != "" {
		return "", errors.New("Gemini base URL is invalid")
	}
	base.Path = strings.TrimSuffix(base.Path, "/v1beta") + "/v1beta/interactions"
	return base.String(), nil
}

func (a *TaskAdaptor) BuildRequestHeader(_ *gin.Context, req *http.Request, _ *relaycommon.RelayInfo) error {
	if strings.TrimSpace(a.apiKey) == "" {
		return errors.New("Gemini API key is missing")
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("x-goog-api-key", a.apiKey)
	return nil
}

func (a *TaskAdaptor) BuildRequestBody(c *gin.Context, info *relaycommon.RelayInfo) (io.Reader, error) {
	req, err := relaycommon.GetTaskRequest(c)
	if err != nil {
		return nil, err
	}
	spec, err := resolveRequestSpec(req)
	if err != nil {
		return nil, err
	}
	if err := validateTaskRequest(req, spec); err != nil {
		return nil, err
	}
	inputs, err := buildInteractionInput(req)
	if err != nil {
		return nil, err
	}
	modelName := strings.TrimSpace(info.UpstreamModelName)
	if modelName != ModelGeminiOmni11Flash {
		return nil, errors.New("unsupported Gemini Omni model")
	}
	generationConfig := &videoGenerationConfig{}
	generationConfig.VideoConfig.Task = spec.Task
	payload := interactionRequest{
		Model:                 modelName,
		Input:                 inputs,
		PreviousInteractionID: spec.PreviousInteractionID,
		ResponseFormat: videoResponseFormat{
			Type:        "video",
			AspectRatio: spec.AspectRatio,
			Delivery:    "uri",
			Duration:    strconv.Itoa(spec.DurationSeconds) + "s",
			Resolution:  spec.Resolution,
		},
		GenerationConfig: generationConfig,
		Background:       false,
		Store:            false,
		Stream:           false,
	}
	body, err := common.Marshal(payload)
	if err != nil {
		return nil, err
	}
	a.submitSpec = spec
	return bytes.NewReader(body), nil
}

func (a *TaskAdaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (*http.Response, error) {
	// Interactions video creation is a paid, non-idempotent POST. Surface every
	// redirect to the caller so neither the request body nor x-goog-api-key can
	// be replayed at another origin.
	return channel.DoTaskApiRequestNoRedirect(a, c, info, requestBody)
}

func (a *TaskAdaptor) DoResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (string, []byte, *taskdto.TaskError) {
	responseBody, err := relaycommon.ReadProviderTaskResponseBody(resp.Body)
	if err != nil {
		return "", nil, service.TaskErrorWrapper(err, "read_response_body_failed", http.StatusInternalServerError)
	}
	_ = resp.Body.Close()
	if err := common.RejectDuplicateJSONKeys(responseBody); err != nil {
		return "", nil, service.TaskErrorWrapperLocal(errors.New("Gemini interaction response is invalid"), "invalid_response", http.StatusBadGateway)
	}
	var interaction interactionResponse
	// Interaction is an extensible resource: the provider can add unrelated
	// step and usage fields. Duplicate keys are rejected above and every field
	// that affects identity, state, billing evidence or output is validated
	// explicitly below.
	if err := common.Unmarshal(responseBody, &interaction); err != nil {
		return "", nil, service.TaskErrorWrapperLocal(errors.New("Gemini interaction response is invalid"), "invalid_response", http.StatusBadGateway)
	}
	if interaction.Object != "" && interaction.Object != "interaction" {
		return "", nil, service.TaskErrorWrapperLocal(errors.New("Gemini interaction object is invalid"), "invalid_response", http.StatusBadGateway)
	}
	if !interactionIDPattern.MatchString(interaction.ID) || interaction.Model != info.UpstreamModelName || interaction.Model != ModelGeminiOmni11Flash {
		return "", nil, service.TaskErrorWrapperLocal(errors.New("Gemini interaction identity is invalid"), "invalid_response", http.StatusBadGateway)
	}
	if interaction.Status != "completed" || len(interaction.Errors) != 0 {
		return "", nil, service.TaskErrorWrapperLocal(errors.New("Gemini interaction did not synchronously complete"), "provider_interaction_not_terminal", http.StatusBadGateway)
	}
	usage := interactionUsage{}
	usageComplete := false
	outputVideoTokens := 0
	outputTextTokens := 0
	if interaction.Usage != nil && !interactionUsageEmpty(*interaction.Usage) {
		usage = *interaction.Usage
		if err := validateUsage(usage); err != nil {
			return "", nil, service.TaskErrorWrapperLocal(err, "invalid_response", http.StatusBadGateway)
		}
		var err error
		outputVideoTokens, outputTextTokens, err = outputTokensByModality(usage.OutputTokensByModality)
		if err != nil {
			return "", nil, service.TaskErrorWrapperLocal(err, "invalid_response", http.StatusBadGateway)
		}
		usageComplete = true
	}
	outputURI, err := completedVideoURI(interaction)
	if err != nil {
		return "", nil, service.TaskErrorWrapperLocal(err, "invalid_response", http.StatusBadGateway)
	}
	fileID, err := extractFileIDFromOutputURI(outputURI)
	if err != nil {
		return "", nil, service.TaskErrorWrapperLocal(err, "invalid_response", http.StatusBadGateway)
	}
	var taskID string
	if usageComplete {
		taskID, err = buildPersistedTaskID(fileID, a.submitSpec, usage)
	} else {
		taskID, err = buildPersistedTaskIDWithoutUsage(fileID, a.submitSpec)
	}
	if err != nil {
		return "", nil, service.TaskErrorWrapperLocal(err, "invalid_response", http.StatusBadGateway)
	}
	uriDigest := sha256.Sum256([]byte(outputURI))
	receiptSchemaVersion := 3
	if usageComplete {
		receiptSchemaVersion = 2
	}
	receipt, err := common.Marshal(submissionReceipt{
		SchemaVersion:     receiptSchemaVersion,
		Object:            submissionReceiptObject,
		InteractionID:     interaction.ID,
		FileID:            fileID,
		Model:             interaction.Model,
		Status:            interaction.Status,
		UsageComplete:     usageComplete,
		OutputURISHA256:   hex.EncodeToString(uriDigest[:]),
		TotalCachedTokens: usage.TotalCachedTokens,
		TotalInputTokens:  usage.TotalInputTokens,
		TotalOutputTokens: usage.TotalOutputTokens,
		OutputVideoTokens: outputVideoTokens,
		OutputTextTokens:  outputTextTokens,
		ThoughtTokens:     usage.TotalThoughtTokens,
		TotalTokens:       usage.TotalTokens,
	})
	if err != nil {
		return "", nil, service.TaskErrorWrapper(err, "marshal_response_failed", http.StatusInternalServerError)
	}
	video := openai.NewOpenAIVideo()
	video.ID = info.PublicTaskID
	video.TaskID = info.PublicTaskID
	video.CreatedAt = time.Now().Unix()
	video.Model = info.OriginModelName
	c.JSON(http.StatusOK, video)
	return taskID, receipt, nil
}

func completedVideoURI(interaction interactionResponse) (string, error) {
	var video *interactionContent
	for _, step := range interaction.Steps {
		if step.Type != "model_output" {
			continue
		}
		for index := range step.Content {
			content := step.Content[index]
			if content.Type != "video" {
				continue
			}
			if video != nil {
				return "", errors.New("Gemini interaction returned multiple videos")
			}
			video = &content
		}
	}
	if video == nil || strings.TrimSpace(video.URI) == "" || strings.TrimSpace(video.Data) != "" {
		return "", errors.New("Gemini interaction did not return one URI-delivered video")
	}
	if video.MIMEType != "" && video.MIMEType != "video/mp4" {
		return "", errors.New("Gemini interaction returned an unsupported video type")
	}
	return strings.TrimSpace(video.URI), nil
}

func (a *TaskAdaptor) FetchTask(baseURL string, key string, body map[string]any, proxy string) (*http.Response, error) {
	return a.FetchTaskWithContext(context.Background(), baseURL, key, body, proxy)
}

func (a *TaskAdaptor) FetchTaskWithContext(ctx context.Context, baseURL string, key string, body map[string]any, proxy string) (*http.Response, error) {
	taskID, ok := body["task_id"].(string)
	if !ok {
		return nil, errors.New("invalid task_id")
	}
	identity, err := parsePersistedTaskID(taskID)
	if err != nil {
		return nil, err
	}
	providerModel, _ := body["provider_model"].(string)
	if providerModel == "" {
		providerModel = ModelGeminiOmni11Flash
	}
	if providerModel != ModelGeminiOmni11Flash {
		return nil, errors.New("invalid Gemini Omni provider model")
	}
	requestURL, err := buildFilesURL(baseURL, identity.FileID, false)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, requestURL, nil)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(key) == "" {
		return nil, errors.New("Gemini API key is missing")
	}
	req.Header.Set("Accept", "application/json")
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
	if err != nil || response == nil || response.Body == nil {
		return response, err
	}
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return response, nil
	}
	raw, err := relaycommon.ReadProviderTaskResponseBody(response.Body)
	_ = response.Body.Close()
	if err != nil {
		return nil, err
	}
	envelope, err := buildFileStatusEnvelope(raw, baseURL, taskID, providerModel)
	if err != nil {
		return nil, err
	}
	return replaceResponseBody(response, envelope), nil
}

func (a *TaskAdaptor) ParseTaskResult(respBody []byte) (*relaycommon.TaskInfo, error) {
	var envelope fileStatusEnvelope
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(string(respBody)), &envelope); err != nil {
		return nil, fmt.Errorf("decode Gemini Omni file envelope failed: %w", err)
	}
	identity, err := parsePersistedTaskID(envelope.TaskID)
	if err != nil {
		return nil, err
	}
	if envelope.SchemaVersion != identity.SchemaVersion || envelope.Object != fileEnvelopeObject ||
		envelope.ProviderModel != ModelGeminiOmni11Flash || envelope.FileID != identity.FileID ||
		envelope.DurationSeconds != identity.DurationSeconds || envelope.Resolution != identity.Resolution ||
		envelope.AspectRatio != identity.AspectRatio || envelope.TotalInputTokens != identity.TotalInputTokens ||
		envelope.UsageComplete != identity.UsageComplete ||
		envelope.TotalOutputTokens != identity.TotalOutputTokens || envelope.TotalCachedTokens != identity.TotalCachedTokens ||
		envelope.OutputVideoTokens != identity.OutputVideoTokens || envelope.OutputTextTokens != identity.OutputTextTokens ||
		envelope.ThoughtTokens != identity.ThoughtTokens ||
		envelope.TotalTokens != identity.TotalTokens {
		return nil, errors.New("Gemini Omni file envelope binding is invalid")
	}
	result := &relaycommon.TaskInfo{
		Code:             0,
		TaskID:           envelope.TaskID,
		CompletionTokens: identity.TotalOutputTokens,
		TotalTokens:      identity.TotalTokens,
		ProviderResultProof: &relaycommon.ProviderTaskResultProof{
			SchemaVersion:  1,
			Protocol:       ProviderProtocolV1,
			TaskID:         envelope.TaskID,
			Model:          envelope.ProviderModel,
			ProviderStatus: strings.ToLower(envelope.State),
			UsageComplete:  identity.UsageComplete,
		},
	}
	switch envelope.State {
	case "PROCESSING":
		result.Status = model.TaskStatusInProgress
		result.Progress = taskcommon.ProgressInProgress
		result.ProviderResultProof.ProviderStatus = "processing"
	case "ACTIVE":
		if err := validateActiveEnvelope(envelope); err != nil {
			return nil, err
		}
		artifactHash, err := base64.StdEncoding.Strict().DecodeString(envelope.SHA256Hash)
		if err != nil || len(artifactHash) != sha256.Size {
			return nil, errors.New("Gemini Omni active file digest is invalid")
		}
		result.Status = model.TaskStatusSuccess
		result.Progress = taskcommon.ProgressComplete
		result.Url = envelope.ArtifactURL
		result.ProviderResultProof.ProviderStatus = "succeeded"
		result.ProviderResultProof.Resolution = identity.Resolution
		result.ProviderResultProof.DurationSeconds = identity.DurationSeconds
		result.ProviderResultProof.AspectRatio = identity.AspectRatio
		result.ProviderResultProof.OutputCount = 1
		result.ProviderResultProof.MediaType = "video"
		result.ProviderResultProof.InputTokens = identity.TotalInputTokens
		result.ProviderResultProof.OutputTokens = identity.TotalOutputTokens
		result.ProviderResultProof.OutputVideoTokens = identity.OutputVideoTokens
		result.ProviderResultProof.OutputTextTokens = identity.OutputTextTokens
		result.ProviderResultProof.ThoughtTokens = identity.ThoughtTokens
		result.ProviderResultProof.CachedTokens = identity.TotalCachedTokens
		result.ProviderResultProof.TotalTokens = identity.TotalTokens
		result.ProviderResultProof.ArtifactSizeBytes = envelope.SizeBytes
		result.ProviderResultProof.ArtifactSHA256 = hex.EncodeToString(artifactHash)
	case "FAILED":
		result.Status = model.TaskStatusFailure
		result.Progress = taskcommon.ProgressComplete
		result.Reason = "Gemini generated file processing failed"
		result.ProviderResultProof.ProviderStatus = "failed"
		result.ProviderResultProof.FailureOwner = "provider"
		result.ProviderResultProof.FailureCode = "provider_service_failure"
	default:
		return nil, errors.New("Gemini Omni file envelope state is unknown")
	}
	return result, nil
}

func validateActiveEnvelope(envelope fileStatusEnvelope) error {
	videoDuration, err := time.ParseDuration(envelope.VideoDuration)
	if envelope.MIMEType != "video/mp4" || envelope.SizeBytes <= 0 || err != nil ||
		videoDuration != time.Duration(envelope.DurationSeconds)*time.Second {
		return errors.New("Gemini Omni active file metadata is invalid")
	}
	decodedHash, err := base64.StdEncoding.Strict().DecodeString(envelope.SHA256Hash)
	if err != nil || len(decodedHash) != 32 {
		return errors.New("Gemini Omni active file digest is invalid")
	}
	parsed, err := url.Parse(strings.TrimSpace(envelope.ArtifactURL))
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" {
		return errors.New("Gemini Omni artifact URL is invalid")
	}
	if parsed.Query().Get("alt") != "media" {
		return errors.New("Gemini Omni artifact URL delivery mode is invalid")
	}
	return nil
}

func (a *TaskAdaptor) GetModelList() []string {
	return append([]string(nil), ModelList...)
}

func (a *TaskAdaptor) GetChannelName() string {
	return ChannelName
}

func (a *TaskAdaptor) ConvertToOpenAIVideo(task *model.Task) ([]byte, error) {
	video := openai.NewOpenAIVideo()
	video.ID = task.TaskID
	video.Model = task.Properties.UpstreamModelName
	if video.Model == "" {
		video.Model = ModelGeminiOmni11Flash
	}
	video.Status = task.Status.ToVideoStatus()
	video.SetProgressStr(task.Progress)
	video.CreatedAt = task.CreatedAt
	if task.FinishTime > 0 {
		video.CompletedAt = task.FinishTime
	} else {
		video.CompletedAt = task.UpdatedAt
	}
	return common.Marshal(video)
}
