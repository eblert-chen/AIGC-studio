package doubao

import (
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/QuantumNous/new-api/common"

	"github.com/QuantumNous/new-api/constant"
	taskdto "github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay/channel"
	"github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/service"

	"github.com/gin-gonic/gin"
	"github.com/pkg/errors"
	"github.com/samber/lo"
)

// ============================
// Request / Response structures
// ============================

type ContentItem struct {
	Type     string    `json:"type,omitempty"`
	Text     string    `json:"text,omitempty"`
	ImageURL *MediaURL `json:"image_url,omitempty"`
	VideoURL *MediaURL `json:"video_url,omitempty"`
	AudioURL *MediaURL `json:"audio_url,omitempty"`
	Role     string    `json:"role,omitempty"`
}

type MediaURL struct {
	URL string `json:"url,omitempty"`
}

type requestPayload struct {
	Model                 string         `json:"model"`
	Content               []ContentItem  `json:"content,omitempty"`
	CallbackURL           string         `json:"callback_url,omitempty"`
	ReturnLastFrame       *dto.BoolValue `json:"return_last_frame,omitempty"`
	ServiceTier           string         `json:"service_tier,omitempty"`
	ExecutionExpiresAfter *dto.IntValue  `json:"execution_expires_after,omitempty"`
	GenerateAudio         *dto.BoolValue `json:"generate_audio,omitempty"`
	Draft                 *dto.BoolValue `json:"draft,omitempty"`
	Tools                 []struct {
		Type string `json:"type,omitempty"`
	} `json:"tools,omitempty"`
	SafetyIdentifier      string         `json:"safety_identifier,omitempty"`
	Priority              *dto.IntValue  `json:"priority,omitempty"`
	Resolution            string         `json:"resolution,omitempty"`
	Ratio                 string         `json:"ratio,omitempty"`
	Duration              *dto.IntValue  `json:"duration,omitempty"`
	Frames                *dto.IntValue  `json:"frames,omitempty"`
	Seed                  *dto.IntValue  `json:"seed,omitempty"`
	CameraFixed           *dto.BoolValue `json:"camera_fixed,omitempty"`
	Watermark             *dto.BoolValue `json:"watermark,omitempty"`
	OmniReferenceTaskType *string        `json:"omni_reference_task_type,omitempty"`
	OutputFormat          *string        `json:"output_format,omitempty"`
}

type responsePayload struct {
	ID string `json:"id"` // task_id
}

type seedreamImageRequest struct {
	Model                     string `json:"model"`
	Prompt                    string `json:"prompt"`
	Size                      string `json:"size"`
	SequentialImageGeneration string `json:"sequential_image_generation"`
	Stream                    bool   `json:"stream"`
	ResponseFormat            string `json:"response_format"`
	OutputFormat              string `json:"output_format"`
	Watermark                 bool   `json:"watermark"`
}

type seedreamImageResponse struct {
	Model   string `json:"model"`
	Created int64  `json:"created"`
	Data    []struct {
		URL  string `json:"url"`
		Size string `json:"size"`
	} `json:"data"`
	Usage struct {
		GeneratedImages int `json:"generated_images"`
		OutputTokens    int `json:"output_tokens"`
		TotalTokens     int `json:"total_tokens"`
	} `json:"usage"`
}

type seedreamImageTaskReceipt struct {
	ID      string `json:"id"`
	Model   string `json:"model"`
	Status  string `json:"status"`
	Size    string `json:"size"`
	Created int64  `json:"created,omitempty"`
	Usage   struct {
		GeneratedImages int `json:"generated_images,omitempty"`
		OutputTokens    int `json:"output_tokens,omitempty"`
		TotalTokens     int `json:"total_tokens,omitempty"`
	} `json:"usage,omitempty"`
}

type responseTask struct {
	ID      string `json:"id"`
	Model   string `json:"model"`
	Status  string `json:"status"`
	Content struct {
		VideoURL string `json:"video_url"`
	} `json:"content"`
	Seed            int    `json:"seed"`
	Resolution      string `json:"resolution"`
	Duration        string `json:"duration"`
	Ratio           string `json:"ratio"`
	FramesPerSecond int    `json:"framespersecond"`
	ServiceTier     string `json:"service_tier"`
	Tools           []struct {
		Type string `json:"type"`
	} `json:"tools"`
	Usage struct {
		CompletionTokens int `json:"completion_tokens"`
		TotalTokens      int `json:"total_tokens"`
		ToolUsage        struct {
			WebSearch int `json:"web_search"`
		} `json:"tool_usage"`
	} `json:"usage"`
	Error struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
	CreatedAt dto.IntValue `json:"created_at"`
	UpdatedAt dto.IntValue `json:"updated_at"`
}

var (
	arkProviderTaskIDPattern          = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$`)
	arkPromptControlPattern           = regexp.MustCompile(`(?i)(^|\s)--(?:dur|duration|ratio|resolution)(?:\s|=|$)`)
	errDoubaoProviderResponseTooLarge = fmt.Errorf("Doubao provider response exceeds the maximum receipt size")
)

const (
	doubaoProviderResponseLimit  = 1 << 20
	seedreamArtifactURLMaxLength = 8192
)

// ============================
// Adaptor implementation
// ============================

type TaskAdaptor struct {
	taskcommon.BaseBilling
	ChannelType       int
	apiKey            string
	baseURL           string
	seedreamImage     bool
	generationProfile *generationprofile.Profile
	immediateTerminal *relaycommon.TaskInfo
}

func (a *TaskAdaptor) Init(info *relaycommon.RelayInfo) {
	a.ChannelType = info.ChannelType
	a.baseURL = info.ChannelBaseUrl
	a.apiKey = info.ApiKey
	a.seedreamImage = false
	a.generationProfile = nil
	a.immediateTerminal = nil
}

// ValidateRequestAndSetAction parses body, validates fields and sets default action.
func (a *TaskAdaptor) ValidateRequestAndSetAction(c *gin.Context, info *relaycommon.RelayInfo) (taskErr *taskdto.TaskError) {
	// Accept only POST /v1/video/generations as "generate" action.
	return relaycommon.ValidateBasicTaskRequest(c, info, constant.TaskActionGenerate)
}

// BuildRequestURL constructs the upstream URL.

func (a *TaskAdaptor) BuildRequestURL(_ *relaycommon.RelayInfo) (string, error) {
	if a.seedreamImage {
		return fmt.Sprintf("%s/api/v3/images/generations", strings.TrimRight(a.baseURL, "/")), nil
	}
	return fmt.Sprintf("%s/api/v3/contents/generations/tasks", strings.TrimRight(a.baseURL, "/")), nil
}

// BuildRequestHeader sets required headers.
func (a *TaskAdaptor) BuildRequestHeader(_ *gin.Context, req *http.Request, _ *relaycommon.RelayInfo) error {
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", "Bearer "+a.apiKey)
	return nil
}

// BuildRequestBody converts request into Doubao specific format.
func (a *TaskAdaptor) BuildRequestBody(c *gin.Context, info *relaycommon.RelayInfo) (io.Reader, error) {
	req, err := relaycommon.GetTaskRequest(c)
	if err != nil {
		return nil, err
	}
	a.seedreamImage = false
	a.generationProfile = nil
	a.immediateTerminal = nil
	if isPinnedPlatformRoute(info) {
		mode, _ := req.Metadata["platform_generation_mode"].(string)
		mode = strings.TrimSpace(mode)
		profile, hasProfile, profileErr := resolvePinnedGenerationProfile(&req, info)
		if profileErr != nil {
			return nil, profileErr
		}
		if mode == "text_to_image" {
			if !hasProfile || profile.Image == nil {
				return nil, fmt.Errorf("pinned image route requires native channel type and upstream model from an immutable adapter capability profile")
			}
			body, convertErr := convertPinnedSeedreamImageRequest(&req, info, profile)
			if convertErr != nil {
				return nil, errors.Wrap(convertErr, "convert request payload failed")
			}
			data, marshalErr := common.Marshal(body)
			if marshalErr != nil {
				return nil, marshalErr
			}
			a.seedreamImage = true
			a.generationProfile = &profile
			return bytes.NewReader(data), nil
		}
	}

	body, err := a.convertToRequestPayload(&req, info)
	if err != nil {
		return nil, errors.Wrap(err, "convert request payload failed")
	}
	if info.IsModelMapped || isPinnedPlatformRoute(info) {
		body.Model = info.UpstreamModelName
	} else {
		info.UpstreamModelName = body.Model
	}
	data, err := common.Marshal(body)
	if err != nil {
		return nil, err
	}
	return bytes.NewReader(data), nil
}

// DoRequest delegates to common helper.
func (a *TaskAdaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (*http.Response, error) {
	return channel.DoTaskApiRequest(a, c, info, requestBody)
}

// DoRequestNoRedirect is the paid exact-route probe seam. It intentionally
// leaves ordinary Doubao traffic on the shared redirect policy while ensuring
// a 307/308 cannot replay this provider POST through net/http GetBody.
func (a *TaskAdaptor) DoRequestNoRedirect(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (*http.Response, error) {
	return channel.DoTaskApiRequestNoRedirect(a, c, info, requestBody)
}

func readDoubaoProviderResponse(body io.Reader) ([]byte, error) {
	responseBody, err := io.ReadAll(io.LimitReader(body, doubaoProviderResponseLimit+1))
	if err != nil {
		return nil, fmt.Errorf("Doubao provider response could not be read")
	}
	if len(responseBody) > doubaoProviderResponseLimit {
		return nil, errDoubaoProviderResponseTooLarge
	}
	return responseBody, nil
}

func decodeUnambiguousProviderJSON(responseBody []byte, target any) error {
	if err := common.RejectDuplicateJSONKeys(responseBody); err != nil {
		return fmt.Errorf("Doubao provider response is not valid unambiguous JSON")
	}
	if err := common.Unmarshal(responseBody, target); err != nil {
		return fmt.Errorf("Doubao provider response is not valid unambiguous JSON")
	}
	return nil
}

// DoResponse handles upstream response, returns taskID etc.
func (a *TaskAdaptor) DoResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (taskID string, taskData []byte, taskErr *taskdto.TaskError) {
	if resp == nil || resp.Body == nil {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("Doubao provider response body is unavailable"), "invalid_response", http.StatusInternalServerError)
	}
	defer func() { _ = resp.Body.Close() }()
	responseBody, err := readDoubaoProviderResponse(resp.Body)
	if err != nil {
		code := "read_response_body_failed"
		if err == errDoubaoProviderResponseTooLarge {
			code = "invalid_response"
		}
		taskErr = service.TaskErrorWrapper(err, code, http.StatusInternalServerError)
		return
	}
	if a.seedreamImage {
		return a.doSeedreamImageResponse(c, responseBody, info)
	}

	// Parse Doubao response
	var dResp responsePayload
	if err := decodeUnambiguousProviderJSON(responseBody, &dResp); err != nil {
		taskErr = service.TaskErrorWrapper(err, "unmarshal_response_body_failed", http.StatusInternalServerError)
		return
	}

	if dResp.ID == "" {
		taskErr = service.TaskErrorWrapper(fmt.Errorf("task_id is empty"), "invalid_response", http.StatusInternalServerError)
		return
	}
	if !arkProviderTaskIDPattern.MatchString(dResp.ID) {
		taskErr = service.TaskErrorWrapper(fmt.Errorf("task_id is invalid"), "invalid_response", http.StatusInternalServerError)
		return
	}

	ov := dto.NewOpenAIVideo()
	ov.ID = info.PublicTaskID
	ov.TaskID = info.PublicTaskID
	ov.CreatedAt = time.Now().Unix()
	ov.Model = info.OriginModelName

	c.JSON(http.StatusOK, ov)
	return dResp.ID, responseBody, nil
}

func (a *TaskAdaptor) doSeedreamImageResponse(c *gin.Context, responseBody []byte, info *relaycommon.RelayInfo) (taskID string, taskData []byte, taskErr *taskdto.TaskError) {
	if a.generationProfile == nil || a.generationProfile.Image == nil {
		profile, found, err := generationprofile.Resolve("", info.ChannelType, strings.TrimSpace(info.UpstreamModelName))
		if err != nil || !found || profile.Image == nil {
			return "", nil, service.TaskErrorWrapper(fmt.Errorf("Seedream capability profile is unavailable"), "invalid_response", http.StatusInternalServerError)
		}
		a.generationProfile = &profile
	}
	profile := *a.generationProfile
	providerModelID := strings.TrimSpace(info.UpstreamModelName)
	if providerModelID == "" {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("Seedream pinned provider model is unavailable"), "invalid_response", http.StatusInternalServerError)
	}
	artifact, ok := profile.Artifact("text_to_image")
	if !ok || artifact.Count < 1 {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("Seedream artifact profile is unavailable"), "invalid_response", http.StatusInternalServerError)
	}
	modeCapability, ok := profile.Capability.Modes["text_to_image"]
	if !ok || len(modeCapability.Limits.AspectRatios) != 1 {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("Seedream acceptance output profile is unavailable"), "invalid_response", http.StatusInternalServerError)
	}
	var provider seedreamImageResponse
	if err := decodeUnambiguousProviderJSON(responseBody, &provider); err != nil {
		return "", nil, service.TaskErrorWrapper(err, "unmarshal_response_body_failed", http.StatusInternalServerError)
	}
	if profile.Image.RequireExactResponseModel && provider.Model != providerModelID {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("Seedream response model does not match the pinned route"), "invalid_response", http.StatusInternalServerError)
	}
	if provider.Created < 0 || provider.Usage.GeneratedImages < 0 || provider.Usage.OutputTokens < 0 ||
		provider.Usage.TotalTokens < 0 || provider.Usage.TotalTokens < provider.Usage.OutputTokens {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("Seedream response contains invalid usage values"), "invalid_response", http.StatusInternalServerError)
	}
	if len(provider.Data) != artifact.Count {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("Seedream response must contain exactly %d artifact(s)", artifact.Count), "invalid_response", http.StatusInternalServerError)
	}
	if profile.Image.RequireGeneratedImageUsage && provider.Usage.GeneratedImages != artifact.Count {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("Seedream response usage must confirm exactly %d generated image(s)", artifact.Count), "invalid_response", http.StatusInternalServerError)
	}
	if provider.Data[0].Size != profile.Image.Size {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("Seedream response size does not match the accepted output"), "invalid_response", http.StatusInternalServerError)
	}
	artifactURL, err := normalizeSeedreamArtifactURL(provider.Data[0].URL)
	if err != nil {
		return "", nil, service.TaskErrorWrapper(err, "invalid_response", http.StatusInternalServerError)
	}
	digest := sha256.Sum256(responseBody)
	taskID = fmt.Sprintf("seedream:%x", digest)
	receipt := seedreamImageTaskReceipt{
		ID:      taskID,
		Model:   providerModelID,
		Status:  "succeeded",
		Size:    profile.Image.Size,
		Created: provider.Created,
	}
	receipt.Usage.GeneratedImages = provider.Usage.GeneratedImages
	receipt.Usage.OutputTokens = provider.Usage.OutputTokens
	receipt.Usage.TotalTokens = provider.Usage.TotalTokens
	taskData, err = common.Marshal(receipt)
	if err != nil {
		return "", nil, service.TaskErrorWrapper(err, "marshal_task_receipt_failed", http.StatusInternalServerError)
	}
	a.immediateTerminal = &relaycommon.TaskInfo{
		TaskID:           taskID,
		Status:           string(model.TaskStatusSuccess),
		Progress:         "100%",
		Url:              artifactURL,
		CompletionTokens: provider.Usage.OutputTokens,
		TotalTokens:      provider.Usage.TotalTokens,
		ProviderResultProof: &relaycommon.ProviderTaskResultProof{
			SchemaVersion:   1,
			Protocol:        profile.Protocol,
			TaskID:          taskID,
			Model:           providerModelID,
			ProviderStatus:  "succeeded",
			Resolution:      profile.Image.Size,
			DurationSeconds: 0,
			AspectRatio:     modeCapability.Limits.AspectRatios[0],
			FramesPresent:   false,
			OutputCount:     artifact.Count,
			MediaType:       artifact.MediaType,
		},
	}
	completedAt := time.Now().Unix()
	result := dto.NewOpenAIVideo()
	result.ID = info.PublicTaskID
	result.TaskID = info.PublicTaskID
	result.CreatedAt = completedAt
	result.CompletedAt = completedAt
	result.Model = info.OriginModelName
	result.Status = dto.VideoStatusCompleted
	result.Progress = 100
	c.JSON(http.StatusOK, result)
	return taskID, taskData, nil
}

func normalizeSeedreamArtifactURL(raw string) (string, error) {
	if raw == "" || len(raw) > seedreamArtifactURLMaxLength || raw != strings.TrimSpace(raw) || strings.IndexFunc(raw, unicode.IsControl) >= 0 {
		return "", fmt.Errorf("Seedream response artifact URL must be an exact bounded HTTPS URL")
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" {
		return "", fmt.Errorf("Seedream response artifact URL must be an absolute HTTPS URL")
	}
	return raw, nil
}

// ImmediateTerminalTaskResult exposes a defensive copy only for the protected
// synchronous Seedream branch. Existing asynchronous Ark video submissions
// leave this nil and preserve their polling lifecycle.
func (a *TaskAdaptor) ImmediateTerminalTaskResult() *relaycommon.TaskInfo {
	if a.immediateTerminal == nil {
		return nil
	}
	result := *a.immediateTerminal
	if a.immediateTerminal.ProviderResultProof != nil {
		proof := *a.immediateTerminal.ProviderResultProof
		result.ProviderResultProof = &proof
	}
	return &result
}

// FetchTask fetch task status
func (a *TaskAdaptor) FetchTask(baseUrl, key string, body map[string]any, proxy string) (*http.Response, error) {
	return a.FetchTaskWithContext(context.Background(), baseUrl, key, body, proxy)
}

func (a *TaskAdaptor) FetchTaskWithContext(ctx context.Context, baseUrl, key string, body map[string]any, proxy string) (*http.Response, error) {
	taskID, ok := body["task_id"].(string)
	if !ok || !arkProviderTaskIDPattern.MatchString(taskID) {
		return nil, fmt.Errorf("invalid task_id")
	}

	uri := fmt.Sprintf("%s/api/v3/contents/generations/tasks/%s", strings.TrimRight(baseUrl, "/"), taskID)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, uri, nil)
	if err != nil {
		return nil, err
	}

	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)

	client, err := service.GetHttpClientWithProxy(proxy)
	if err != nil {
		return nil, fmt.Errorf("new proxy http client failed: %w", err)
	}
	return client.Do(req)
}

func (a *TaskAdaptor) GetModelList() []string {
	return ModelList
}

func (a *TaskAdaptor) GetChannelName() string {
	return ChannelName
}

func (a *TaskAdaptor) convertToRequestPayload(req *relaycommon.TaskSubmitReq, info *relaycommon.RelayInfo) (*requestPayload, error) {
	if isPinnedPlatformRoute(info) {
		return convertPinnedPlatformRequest(req, info)
	}

	r := requestPayload{
		Model:   req.Model,
		Content: []ContentItem{},
	}

	// Add images if present
	if req.HasImage() {
		for _, imgURL := range req.Images {
			r.Content = append(r.Content, ContentItem{
				Type: "image_url",
				ImageURL: &MediaURL{
					URL: imgURL,
				},
			})
		}
	}

	metadata := req.Metadata
	if err := taskcommon.UnmarshalMetadata(metadata, &r); err != nil {
		return nil, errors.Wrap(err, "unmarshal metadata failed")
	}

	if sec, _ := strconv.Atoi(req.Seconds); sec > 0 {
		r.Duration = lo.ToPtr(dto.IntValue(sec))
	}

	r.Content = lo.Reject(r.Content, func(c ContentItem, _ int) bool { return c.Type == "text" })
	r.Content = append(r.Content, ContentItem{
		Type: "text",
		Text: req.Prompt,
	})

	return &r, nil
}

func isPinnedPlatformRoute(info *relaycommon.RelayInfo) bool {
	return info != nil && info.TaskRelayInfo != nil && info.PinnedProviderRoute
}

func convertPinnedSeedreamImageRequest(req *relaycommon.TaskSubmitReq, info *relaycommon.RelayInfo, profile generationprofile.Profile) (*seedreamImageRequest, error) {
	providerModelID := ""
	if info != nil {
		providerModelID = strings.TrimSpace(info.UpstreamModelName)
	}
	if req == nil || info == nil || !isPinnedPlatformRoute(info) || info.ChannelType != profile.NativeChannelType ||
		providerModelID == "" || profile.Image == nil {
		return nil, fmt.Errorf("Seedream image request is not bound to the supported pinned route")
	}
	mode, _ := req.Metadata["platform_generation_mode"].(string)
	if strings.TrimSpace(mode) != "text_to_image" {
		return nil, fmt.Errorf("Seedream image route requires text_to_image mode")
	}
	if strings.TrimSpace(req.Prompt) == "" {
		return nil, fmt.Errorf("Seedream image prompt is required")
	}
	if len(req.Images) != 0 || strings.TrimSpace(req.Image) != "" || strings.TrimSpace(req.InputReference) != "" {
		return nil, fmt.Errorf("Seedream text_to_image does not accept input assets")
	}
	if strings.TrimSpace(req.Size) != profile.Image.Size {
		return nil, fmt.Errorf("Seedream image route requires size %q", profile.Image.Size)
	}
	resolution, _ := req.Metadata["resolution"].(string)
	aspectRatio, _ := req.Metadata["aspectRatio"].(string)
	modeCapability := profile.Capability.Modes["text_to_image"]
	if !containsExactString(modeCapability.Limits.Resolutions, strings.TrimSpace(resolution)) ||
		!containsExactString(modeCapability.Limits.AspectRatios, strings.TrimSpace(aspectRatio)) {
		return nil, fmt.Errorf("Seedream image route requires the admitted resolution and aspect ratio")
	}
	artifact, hasArtifact := profile.Artifact("text_to_image")
	if !hasArtifact || artifact.Count < 1 {
		return nil, fmt.Errorf("Seedream image artifact profile is unavailable")
	}
	if sampleCount, ok := exactMetadataInt(req.Metadata["sampleCount"]); !ok || sampleCount != artifact.Count {
		if artifact.Count == 1 {
			return nil, fmt.Errorf("Seedream image route requires exactly one output")
		}
		return nil, fmt.Errorf("Seedream image route requires exactly %d outputs", artifact.Count)
	}
	if faceEnabled, ok := req.Metadata["face_enabled"].(bool); !ok || faceEnabled {
		return nil, fmt.Errorf("Seedream image route requires face controls to be explicitly disabled")
	}

	return &seedreamImageRequest{
		Model:                     providerModelID,
		Prompt:                    req.Prompt,
		Size:                      profile.Image.Size,
		SequentialImageGeneration: profile.Image.SequentialImageGeneration,
		Stream:                    profile.Image.Stream,
		ResponseFormat:            profile.Image.ResponseFormat,
		OutputFormat:              profile.Image.OutputFormat,
		Watermark:                 profile.Image.Watermark,
	}, nil
}

func resolvePinnedGenerationProfile(req *relaycommon.TaskSubmitReq, info *relaycommon.RelayInfo) (generationprofile.Profile, bool, error) {
	if req == nil || info == nil {
		return generationprofile.Profile{}, false, nil
	}
	profile, found, err := generationprofile.ResolveSnapshot(req.Metadata)
	if err != nil || found {
		if found && profile.NativeChannelType != info.ChannelType {
			return generationprofile.Profile{}, false, fmt.Errorf("generation capability profile snapshot does not match the pinned route")
		}
		return profile, found, err
	}
	return generationprofile.Resolve("", info.ChannelType, strings.TrimSpace(info.UpstreamModelName))
}

func containsExactString(values []string, expected string) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}

func exactMetadataInt(value any) (int, bool) {
	switch typed := value.(type) {
	case int:
		return typed, true
	case float64:
		if typed != float64(int(typed)) {
			return 0, false
		}
		return int(typed), true
	default:
		return 0, false
	}
}

// convertPinnedPlatformRequest is the narrow bridge from the provider-neutral
// Platform contract to Ark's current asynchronous video API. The public task
// metadata is intentionally not decoded into the provider request: doing so
// would allow arbitrary metadata to replace the pinned model, content, callback
// or output controls after capability admission.
func convertPinnedPlatformRequest(req *relaycommon.TaskSubmitReq, info *relaycommon.RelayInfo) (*requestPayload, error) {
	return convertPinnedSeedanceRequest(req, info)
}

func (a *TaskAdaptor) ParseTaskResult(respBody []byte) (*relaycommon.TaskInfo, error) {
	if err := common.RejectDuplicateJSONKeys(respBody); err != nil {
		return nil, fmt.Errorf("Ark task response contains duplicate fields: %w", err)
	}
	var responseFields map[string]any
	if err := common.Unmarshal(respBody, &responseFields); err != nil {
		return nil, errors.Wrap(err, "unmarshal task result fields failed")
	}
	_, framesPresent := responseFields["frames"]
	contentFields, _ := responseFields["content"].(map[string]any)
	_, alternateVideosPresent := contentFields["videos"]
	resTask := responseTask{}
	if err := common.Unmarshal(respBody, &resTask); err != nil {
		return nil, errors.Wrap(err, "unmarshal task result failed")
	}

	providerTaskID := resTask.ID
	if providerTaskID == "" || providerTaskID != strings.TrimSpace(providerTaskID) || !arkProviderTaskIDPattern.MatchString(providerTaskID) {
		return nil, fmt.Errorf("Ark task response does not contain a valid task id")
	}
	if resTask.Model == "" || resTask.Model != strings.TrimSpace(resTask.Model) ||
		resTask.Status == "" || resTask.Status != strings.TrimSpace(resTask.Status) {
		return nil, fmt.Errorf("Ark task response identity is incomplete")
	}
	taskResult := relaycommon.TaskInfo{
		Code:   0,
		TaskID: providerTaskID,
		ProviderResultProof: &relaycommon.ProviderTaskResultProof{
			SchemaVersion:  1,
			Protocol:       generationprofile.VolcengineArkVideoProtocolV1,
			TaskID:         providerTaskID,
			Model:          resTask.Model,
			ProviderStatus: resTask.Status,
		},
	}

	// Map Doubao status to internal status
	switch resTask.Status {
	case "pending", "queued":
		taskResult.Status = model.TaskStatusQueued
		taskResult.Progress = "10%"
	case "processing", "running":
		taskResult.Status = model.TaskStatusInProgress
		taskResult.Progress = "50%"
	case "succeeded":
		if !validArkProviderArtifactURL(resTask.Content.VideoURL) {
			return nil, fmt.Errorf("succeeded Ark task does not contain video_url")
		}
		durationSeconds, err := strconv.Atoi(resTask.Duration)
		if err != nil || durationSeconds <= 0 || strconv.Itoa(durationSeconds) != resTask.Duration {
			return nil, fmt.Errorf("succeeded Ark task does not contain a valid duration")
		}
		if resTask.Resolution == "" || resTask.Resolution != strings.TrimSpace(resTask.Resolution) ||
			resTask.Ratio == "" || resTask.Ratio != strings.TrimSpace(resTask.Ratio) ||
			framesPresent || alternateVideosPresent || resTask.Usage.CompletionTokens <= 0 ||
			resTask.Usage.TotalTokens < resTask.Usage.CompletionTokens {
			return nil, fmt.Errorf("succeeded Ark task does not contain complete generation evidence")
		}
		taskResult.Status = model.TaskStatusSuccess
		taskResult.Progress = "100%"
		taskResult.Url = resTask.Content.VideoURL
		taskResult.ProviderResultProof.Resolution = resTask.Resolution
		taskResult.ProviderResultProof.DurationSeconds = durationSeconds
		taskResult.ProviderResultProof.AspectRatio = resTask.Ratio
		taskResult.ProviderResultProof.FramesPresent = framesPresent
		taskResult.ProviderResultProof.OutputCount = 1
		taskResult.ProviderResultProof.MediaType = "video"
		// Provider-reported token usage is part of the canonical, secret-free
		// terminal receipt. Platform cost reconciliation consumes this proof;
		// requested duration is never substituted for token usage.
		taskResult.ProviderResultProof.InputTokens = resTask.Usage.TotalTokens - resTask.Usage.CompletionTokens
		taskResult.ProviderResultProof.OutputTokens = resTask.Usage.CompletionTokens
		taskResult.ProviderResultProof.TotalTokens = resTask.Usage.TotalTokens
		taskResult.CompletionTokens = resTask.Usage.CompletionTokens
		taskResult.TotalTokens = resTask.Usage.TotalTokens
	case "failed", "cancelled", "expired":
		if resTask.Content.VideoURL != "" || resTask.Resolution != "" || resTask.Duration != "" ||
			resTask.Ratio != "" || framesPresent || alternateVideosPresent ||
			(resTask.Status == "failed" && strings.TrimSpace(resTask.Error.Code) == "" && strings.TrimSpace(resTask.Error.Message) == "") {
			return nil, fmt.Errorf("failed Ark task contains an inconsistent terminal shape")
		}
		taskResult.Status = model.TaskStatusFailure
		taskResult.Progress = "100%"
		taskResult.ProviderResultProof.FailureOwner, taskResult.ProviderResultProof.FailureCode =
			arkProviderFailureClassification(resTask.Status, resTask.Error.Code)
		taskResult.Reason = arkTaskFailureReason(resTask.Status, resTask.Error.Code, resTask.Error.Message)
	default:
		return nil, fmt.Errorf("unknown Ark task status: %s", resTask.Status)
	}

	return &taskResult, nil
}

func arkProviderFailureClassification(status string, providerCode string) (string, string) {
	switch status {
	case "cancelled":
		return "client", "task_cancelled"
	case "expired":
		return "relay", "task_expired"
	}
	normalized := normalizeArkProviderFailureCode(providerCode)
	switch {
	case containsArkProviderFailureCode(normalized,
		"unauthorized", "authentication", "invalidapikey", "invalidaccesskey", "accessdenied", "permissiondenied", "forbidden"):
		return "relay", "provider_authentication_failed"
	case containsArkProviderFailureCode(normalized,
		"quota", "setlimitexceeded", "insufficientbalance", "insufficientcredit", "ratelimit", "toomanyrequests", "resourceexhausted"):
		return "provider", "provider_quota_exhausted"
	case containsArkProviderFailureCode(normalized,
		"contentpolicy", "sensitivecontentdetected", "invalidprompt", "invalidparameter", "invalidrequest", "invalidmodel", "modelnotfound", "badrequest", "validation"):
		if strings.Contains(normalized, "contentpolicy") || strings.Contains(normalized, "sensitivecontentdetected") || strings.Contains(normalized, "invalidprompt") {
			return "client", "content_policy_rejected"
		}
		return "client", "provider_validation_failed"
	case containsArkProviderFailureCode(normalized,
		"internalerror", "internalserviceerror", "serviceunavailable", "serveroverloaded"):
		return "provider", "provider_service_failure"
	default:
		return "relay", "unclassified_provider_terminal"
	}
}

func normalizeArkProviderFailureCode(value string) string {
	var normalized strings.Builder
	for _, character := range strings.ToLower(strings.TrimSpace(value)) {
		if character >= 'a' && character <= 'z' || character >= '0' && character <= '9' {
			normalized.WriteRune(character)
		}
	}
	return normalized.String()
}

func containsArkProviderFailureCode(value string, candidates ...string) bool {
	for _, candidate := range candidates {
		if strings.Contains(value, candidate) {
			return true
		}
	}
	return false
}

func validArkProviderArtifactURL(raw string) bool {
	if raw == "" || raw != strings.TrimSpace(raw) || len(raw) > 8192 || strings.ContainsAny(raw, "\x00\r\n") {
		return false
	}
	parsed, err := url.Parse(raw)
	return err == nil && parsed.Scheme == "https" && parsed.Host != "" && parsed.User == nil && parsed.Fragment == ""
}

// arkTaskFailureReason keeps provider terminal classes machine-distinguishable
// after they cross the provider-neutral task/callback boundary. Provider text is
// retained only as a bounded single-line diagnostic; callers must make charging
// decisions from the terminal task status, never by guessing from this string.
func arkTaskFailureReason(status, providerCode, providerMessage string) string {
	class := "ARK_TASK_" + strings.ToUpper(strings.TrimSpace(status))
	code := boundedSingleLine(providerCode, 96)
	message := boundedSingleLine(providerMessage, 512)
	if code != "" && message != "" {
		return class + " [" + code + "]: " + message
	}
	if code != "" {
		return class + " [" + code + "]"
	}
	if message != "" {
		return class + ": " + message
	}
	return class
}

func boundedSingleLine(value string, limit int) string {
	normalized := strings.Join(strings.Fields(value), " ")
	characters := []rune(normalized)
	if len(characters) > limit {
		return string(characters[:limit])
	}
	return normalized
}

func (a *TaskAdaptor) ConvertToOpenAIVideo(originTask *model.Task) ([]byte, error) {
	var dResp responseTask
	if err := common.Unmarshal(originTask.Data, &dResp); err != nil {
		return nil, errors.Wrap(err, "unmarshal doubao task data failed")
	}

	openAIVideo := dto.NewOpenAIVideo()
	openAIVideo.ID = originTask.TaskID
	openAIVideo.TaskID = originTask.TaskID
	openAIVideo.Status = originTask.Status.ToVideoStatus()
	openAIVideo.SetProgressStr(originTask.Progress)
	openAIVideo.SetMetadata("url", dResp.Content.VideoURL)
	openAIVideo.CreatedAt = originTask.CreatedAt
	openAIVideo.CompletedAt = originTask.UpdatedAt
	openAIVideo.Model = originTask.Properties.OriginModelName

	if dResp.Status == "failed" {
		openAIVideo.Error = &dto.OpenAIVideoError{
			Message: dResp.Error.Message,
			Code:    dResp.Error.Code,
		}
	}

	return common.Marshal(openAIVideo)
}
