package hailuo

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	platformdto "github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

// The V2 protocol is deliberately separate from Hailuo's V1 DTOs. In
// particular, an unlabelled image means an adaptive first frame upstream;
// every image in this fixed-ratio contract must be a reference_image.
// https://platform.minimaxi.com/docs/api-reference/video/generation/api/v2-video-generation.json
type h3VideoRequest struct {
	Model      string          `json:"model"`
	Content    []h3ContentItem `json:"content"`
	Resolution string          `json:"resolution"`
	Duration   int             `json:"duration"`
	Ratio      string          `json:"ratio"`
}

type h3ContentItem struct {
	Type     string      `json:"type"`
	Text     string      `json:"text,omitempty"`
	ImageURL *h3MediaURL `json:"image_url,omitempty"`
	VideoURL *h3MediaURL `json:"video_url,omitempty"`
	AudioURL *h3MediaURL `json:"audio_url,omitempty"`
	Role     string      `json:"role,omitempty"`
}

type h3MediaURL struct {
	URL string `json:"url"`
}

type h3VideoTask struct {
	ID         string                     `json:"id"`
	Model      string                     `json:"model"`
	Status     string                     `json:"status"`
	TaskType   string                     `json:"task_type"`
	Modality   string                     `json:"modality"`
	Content    map[string]json.RawMessage `json:"content"`
	Resolution string                     `json:"resolution"`
	Duration   *int                       `json:"duration"`
	Ratio      string                     `json:"ratio"`
	Error      *h3TaskError               `json:"error"`
	Usage      *h3TaskUsage               `json:"usage"`
	Frames     json.RawMessage            `json:"frames"`
	Outputs    json.RawMessage            `json:"outputs"`
}

type h3TaskUsage struct {
	TotalSeconds      int `json:"total_seconds"`
	InputSeconds      int `json:"input_seconds"`
	OutputSeconds     int `json:"output_seconds"`
	InputImageCount   int `json:"input_image_count"`
	InputAudioSeconds int `json:"input_audio_seconds"`
	TotalTokens       int `json:"total_tokens"`
	PromptTokens      int `json:"prompt_tokens"`
	CompletionTokens  int `json:"completion_tokens"`
}

type h3TaskError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

const h3ProviderResponseLimit = 1 << 20

// The provider has no documented task-ID length limit, but accepted IDs must
// fit the durable generation job and its terminal-outcome identity boundary.
// Keep submit, acceptance probing and polling on the same 191-byte ASCII cap.
var h3ProviderTaskIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,190}$`)

func isH3Model(providerModel string) bool {
	return providerModel == "MiniMax-H3" || providerModel == "MiniMax-H3-Max"
}

// convertPinnedH3Request consumes only the immutable Platform contract. No
// metadata is unmarshaled into a provider DTO: model, callbacks, input roles,
// duration, count and output options cannot be replaced after admission.
func convertPinnedH3Request(req *relaycommon.TaskSubmitReq, info *relaycommon.RelayInfo) (*h3VideoRequest, error) {
	if req == nil || info == nil || info.ChannelMeta == nil || info.TaskRelayInfo == nil || !info.PinnedProviderRoute {
		return nil, fmt.Errorf("MiniMax H3 requires a pinned Platform route")
	}
	if _, err := h3ProviderBaseURL(info.ChannelBaseUrl); err != nil {
		return nil, err
	}
	manifest, found, err := generationprofile.ResolveMiniMaxH3ProviderModel(info.UpstreamModelName)
	if err != nil || !found || info.UpstreamModelName != manifest.ProviderModelID {
		return nil, fmt.Errorf("MiniMax H3 provider model is not reviewed")
	}
	profile, found, err := generationprofile.ResolveSnapshot(req.Metadata)
	if err != nil || !found || profile.ID != manifest.AdapterProfileID ||
		profile.Protocol != generationprofile.MiniMaxH3VideoProtocolV2 ||
		profile.NativeChannelType != constant.ChannelTypeMiniMax || info.ChannelType != profile.NativeChannelType {
		return nil, fmt.Errorf("MiniMax H3 adapter profile does not match the pinned provider route")
	}
	mode, ok := req.Metadata["platform_generation_mode"].(string)
	modeCapability, supported := manifest.CompatibleCapability().Modes[mode]
	if !ok || !supported {
		return nil, fmt.Errorf("MiniMax H3 generation mode is outside the reviewed contract")
	}
	if !utf8.ValidString(req.Prompt) || strings.TrimSpace(req.Prompt) == "" ||
		utf8.RuneCountInString(req.Prompt) > modeCapability.Limits.MaxPromptLength {
		return nil, fmt.Errorf("MiniMax H3 prompt is required and must fit the reviewed character limit")
	}
	duration, exactDuration := h3MetadataInteger(req.Metadata["durationSeconds"])
	count, exactCount := h3MetadataInteger(req.Metadata["sampleCount"])
	faceEnabled, explicitFace := req.Metadata["face_enabled"].(bool)
	resolution, exactResolution := req.Metadata["resolution"].(string)
	ratio, exactRatio := req.Metadata["aspectRatio"].(string)
	if !exactDuration || duration != req.Duration || !containsInt(modeCapability.Limits.DurationSeconds, duration) ||
		(req.Seconds != "" && req.Seconds != strconv.Itoa(duration)) ||
		!exactCount || count != 1 || !explicitFace || faceEnabled ||
		!exactResolution || !contains(modeCapability.Limits.Resolutions, resolution) ||
		!exactRatio || !contains(modeCapability.Limits.AspectRatios, ratio) {
		return nil, fmt.Errorf("MiniMax H3 output options exceed the reviewed contract")
	}

	images := append([]string(nil), req.Images...)
	if len(images) == 0 && req.Image != "" {
		images = append(images, req.Image)
	} else if req.Image != "" && req.Image != images[0] {
		return nil, fmt.Errorf("MiniMax H3 image aliases are inconsistent")
	}
	videos := append([]string(nil), req.Videos...)
	if len(videos) == 0 && req.InputReference != "" {
		videos = append(videos, req.InputReference)
	} else if req.InputReference != "" && req.InputReference != videos[0] {
		return nil, fmt.Errorf("MiniMax H3 video aliases are inconsistent")
	}
	if len(images) > modeCapability.Limits.MaxImages || len(videos) > modeCapability.Limits.MaxVideos ||
		len(req.Audios) > modeCapability.Limits.MaxAudio || len(images)+len(videos)+len(req.Audios) > 12 ||
		(mode == "image_to_video" && len(images) == 0) || (mode == "video_to_video" && len(videos) == 0) {
		return nil, fmt.Errorf("MiniMax H3 reference media counts exceed the reviewed contract")
	}
	content := []h3ContentItem{{Type: "text", Text: req.Prompt}}
	assets := make([]platformdto.PlatformGenerationAssetInput, 0, len(images)+len(videos)+len(req.Audios))
	for _, media := range []struct {
		kind string
		urls []string
	}{{"image", images}, {"video", videos}, {"audio", req.Audios}} {
		for _, mediaURL := range media.urls {
			if !validH3HTTPSURL(mediaURL) {
				return nil, fmt.Errorf("MiniMax H3 reference media must use absolute HTTPS URLs")
			}
			item := h3ContentItem{Type: media.kind + "_url", Role: "reference_" + media.kind}
			locator := &h3MediaURL{URL: mediaURL}
			switch media.kind {
			case "image":
				item.ImageURL = locator
			case "video":
				item.VideoURL = locator
			case "audio":
				item.AudioURL = locator
			}
			content = append(content, item)
			assets = append(assets, platformdto.PlatformGenerationAssetInput{MediaType: media.kind, URL: mediaURL})
		}
	}
	if err := profile.ValidateRequest(platformdto.PlatformGenerationRequest{
		Mode:   mode,
		Inputs: platformdto.PlatformGenerationInputs{Prompt: req.Prompt, Assets: assets},
		Output: platformdto.PlatformGenerationOutputOptions{
			DurationSeconds: duration, AspectRatio: ratio, Resolution: resolution, Count: count,
		},
	}); err != nil {
		return nil, fmt.Errorf("MiniMax H3 request exceeds the immutable profile")
	}
	return &h3VideoRequest{
		Model: manifest.ProviderModelID, Content: content,
		Resolution: strings.ToUpper(resolution), Duration: duration, Ratio: ratio,
	}, nil
}

// Metadata arrives as float64 through ordinary JSON decoding. Require exact,
// bounded integers before any conversion or billing multiplier can use it.
func h3MetadataInteger(value any) (int, bool) {
	switch typed := value.(type) {
	case int:
		return typed, typed >= 1 && typed <= relaycommon.MaxTaskDurationSeconds
	case int64:
		if typed >= 1 && typed <= relaycommon.MaxTaskDurationSeconds {
			return int(typed), true
		}
	case float64:
		if !math.IsNaN(typed) && !math.IsInf(typed, 0) && typed >= 1 && typed <= relaycommon.MaxTaskDurationSeconds && math.Trunc(typed) == typed {
			return int(typed), true
		}
	case json.Number:
		parsed, err := typed.Int64()
		if err == nil && parsed >= 1 && parsed <= relaycommon.MaxTaskDurationSeconds {
			return int(parsed), true
		}
	}
	return 0, false
}

func h3ProviderBaseURL(raw string) (string, error) {
	parsed, err := url.Parse(raw)
	if err != nil || raw != strings.TrimSpace(raw) || parsed.Scheme != "https" || parsed.Host == "" ||
		parsed.User != nil || parsed.RawQuery != "" || parsed.ForceQuery || strings.Contains(raw, "#") ||
		(parsed.EscapedPath() != "" && parsed.EscapedPath() != "/") || parsed.Port() != "" ||
		(parsed.Hostname() != "api.minimax.cn" && parsed.Hostname() != "api.minimax.io") {
		return "", fmt.Errorf("MiniMax H3 requires an explicit HTTPS provider origin")
	}
	return "https://" + parsed.Hostname(), nil
}

func validH3HTTPSURL(raw string) bool {
	if raw == "" || raw != strings.TrimSpace(raw) || len(raw) > 8192 || strings.ContainsAny(raw, "\x00\r\n") {
		return false
	}
	parsed, err := url.Parse(raw)
	return err == nil && parsed.Scheme == "https" && parsed.Host != "" && parsed.User == nil && !strings.Contains(raw, "#")
}

func decodeH3ProviderJSON(raw []byte, target any) error {
	if len(raw) == 0 || len(raw) > h3ProviderResponseLimit || common.RejectDuplicateJSONKeys(raw) != nil || common.Unmarshal(raw, target) != nil {
		return fmt.Errorf("MiniMax H3 provider response is not bounded unambiguous JSON")
	}
	return nil
}

func (a *TaskAdaptor) doH3Response(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (string, []byte, *platformdto.TaskError) {
	if resp == nil || resp.Body == nil {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("MiniMax H3 provider response is unavailable"), "invalid_response", http.StatusBadGateway)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, h3ProviderResponseLimit+1))
	if err != nil || len(raw) > h3ProviderResponseLimit {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("MiniMax H3 provider response could not be read within its limit"), "invalid_response", http.StatusBadGateway)
	}
	if resp.StatusCode != http.StatusOK {
		status := resp.StatusCode
		if status < 400 || status > 599 {
			status = http.StatusBadGateway
		}
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("MiniMax H3 provider did not acknowledge task creation"), "upstream_status", status)
	}
	var fields map[string]json.RawMessage
	if err := decodeH3ProviderJSON(raw, &fields); err != nil {
		return "", nil, service.TaskErrorWrapper(err, "invalid_response", http.StatusBadGateway)
	}
	var providerTaskID string
	if common.Unmarshal(fields["task_id"], &providerTaskID) != nil || !h3ProviderTaskIDPattern.MatchString(providerTaskID) ||
		fields["error"] != nil || fields["type"] != nil || fields["base_resp"] != nil || fields["task"] != nil {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("MiniMax H3 provider task acknowledgement is invalid"), "invalid_response", http.StatusBadGateway)
	}
	// The original response may contain future URLs or diagnostics. Keep only
	// a secret-free receipt, never the raw provider body, in native task Data.
	digest := sha256.Sum256(raw)
	receipt, err := common.Marshal(struct {
		Object                 string `json:"object"`
		ProviderResponseSHA256 string `json:"provider_response_sha256"`
		ProviderResponseBytes  int    `json:"provider_response_bytes"`
	}{"minimax.video.submission_receipt", fmt.Sprintf("sha256:%x", digest), len(raw)})
	if err != nil {
		return "", nil, service.TaskErrorWrapper(fmt.Errorf("MiniMax H3 submission receipt is unavailable"), "invalid_response", http.StatusBadGateway)
	}
	video := dto.NewOpenAIVideo()
	video.ID, video.TaskID = info.PublicTaskID, info.PublicTaskID
	video.Model = info.OriginModelName
	video.CreatedAt = time.Now().Unix()
	c.JSON(http.StatusOK, video)
	return providerTaskID, receipt, nil
}

func (a *TaskAdaptor) fetchH3Task(ctx context.Context, baseURL, key string, body map[string]any, proxy, providerModel string) (*http.Response, error) {
	if !isH3Model(providerModel) {
		return nil, fmt.Errorf("MiniMax H3 poll provider model is not reviewed")
	}
	taskID, ok := body["task_id"].(string)
	if !ok || !h3ProviderTaskIDPattern.MatchString(taskID) {
		return nil, fmt.Errorf("MiniMax H3 poll task identity is invalid")
	}
	origin, err := h3ProviderBaseURL(baseURL)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, origin+H3QueryTaskEndpoint+url.PathEscape(taskID), nil)
	if err != nil {
		return nil, fmt.Errorf("MiniMax H3 poll request is invalid")
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	client, err := service.GetHttpClientWithProxy(proxy)
	if err != nil {
		return nil, fmt.Errorf("MiniMax H3 poll transport is unavailable")
	}
	// Never send the pinned bearer credential to a redirected task endpoint.
	isolated := *client
	isolated.CheckRedirect = func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }
	return isolated.Do(req)
}

func h3TaskEnvelopePresent(raw []byte) bool {
	if len(raw) > h3ProviderResponseLimit {
		return true
	}
	var fields map[string]json.RawMessage
	if common.Unmarshal(raw, &fields) != nil {
		return false
	}
	_, present := fields["task"]
	return present
}

func parseH3TaskResult(raw []byte) (*relaycommon.TaskInfo, error) {
	var envelope struct {
		Task     *h3VideoTask    `json:"task"`
		Error    json.RawMessage `json:"error"`
		BaseResp json.RawMessage `json:"base_resp"`
		Type     json.RawMessage `json:"type"`
		TaskID   json.RawMessage `json:"task_id"`
	}
	if err := decodeH3ProviderJSON(raw, &envelope); err != nil {
		return nil, err
	}
	task := envelope.Task
	if task == nil || envelope.Error != nil || envelope.BaseResp != nil || envelope.Type != nil || envelope.TaskID != nil ||
		!h3ProviderTaskIDPattern.MatchString(task.ID) || !isH3Model(task.Model) || task.TaskType != "generation" || task.Modality != "video" ||
		task.Frames != nil || task.Outputs != nil {
		return nil, fmt.Errorf("MiniMax H3 task response identity or generation type is incomplete")
	}
	manifest, found, err := generationprofile.ResolveMiniMaxH3ProviderModel(task.Model)
	if err != nil || !found {
		return nil, fmt.Errorf("MiniMax H3 task response model is unavailable")
	}
	outputContract := manifest.CompatibleCapability().Modes["text_to_video"].Limits
	canonicalResolution := strings.ToLower(task.Resolution)
	if (task.Resolution != "" && (task.Resolution != strings.ToUpper(canonicalResolution) || !contains(outputContract.Resolutions, canonicalResolution))) ||
		(task.Duration != nil && !containsInt(outputContract.DurationSeconds, *task.Duration)) ||
		(task.Ratio != "" && !contains(outputContract.AspectRatios, task.Ratio)) {
		return nil, fmt.Errorf("MiniMax H3 task response output options are outside the reviewed contract")
	}
	result := &relaycommon.TaskInfo{
		TaskID: task.ID,
		ProviderResultProof: &relaycommon.ProviderTaskResultProof{
			SchemaVersion: 1, Protocol: generationprofile.MiniMaxH3VideoProtocolV2,
			TaskID: task.ID, Model: task.Model, ProviderStatus: task.Status,
		},
	}
	switch task.Status {
	case "queued", "running":
		if len(task.Content) != 0 || task.Usage != nil || (task.Error != nil && (task.Error.Code != "" || task.Error.Message != "")) {
			return nil, fmt.Errorf("MiniMax H3 pending task contains contradictory terminal evidence")
		}
		result.Status, result.Progress = model.TaskStatusQueued, "10%"
		if task.Status == "running" {
			result.Status, result.Progress = model.TaskStatusInProgress, "50%"
		}
	case "succeeded":
		var artifactURL string
		if len(task.Content) != 1 || common.Unmarshal(task.Content["url"], &artifactURL) != nil || !validH3HTTPSURL(artifactURL) ||
			task.Resolution == "" || task.Duration == nil || task.Ratio == "" || task.Usage == nil ||
			(task.Error != nil && (task.Error.Code != "" || task.Error.Message != "")) {
			return nil, fmt.Errorf("MiniMax H3 success response lacks unambiguous video evidence")
		}
		usage := task.Usage
		if usage.OutputSeconds != *task.Duration || usage.OutputSeconds < 1 || usage.OutputSeconds > 15 ||
			usage.InputSeconds < 0 || usage.InputSeconds > 15 || usage.TotalSeconds != usage.InputSeconds+usage.OutputSeconds || usage.TotalSeconds > 30 ||
			usage.InputImageCount < 0 || usage.InputImageCount > 9 || usage.InputAudioSeconds < 0 || usage.InputAudioSeconds > 15 ||
			usage.TotalTokens < 0 || usage.PromptTokens < 0 || usage.CompletionTokens < 0 ||
			usage.TotalTokens < usage.PromptTokens+usage.CompletionTokens ||
			(task.Model == "MiniMax-H3-Max" && (usage.InputSeconds != 0 || usage.InputImageCount != 0 || usage.InputAudioSeconds != 0)) {
			return nil, fmt.Errorf("MiniMax H3 success response usage is inconsistent")
		}
		result.Status, result.Progress, result.Url = model.TaskStatusSuccess, "100%", artifactURL
		result.ProviderResultProof.Resolution = canonicalResolution
		result.ProviderResultProof.DurationSeconds = *task.Duration
		result.ProviderResultProof.AspectRatio = task.Ratio
		result.ProviderResultProof.OutputCount = 1
		result.ProviderResultProof.MediaType = "video"
		result.ProviderResultProof.ProviderTotalSeconds = usage.TotalSeconds
		result.ProviderResultProof.InputSeconds = usage.InputSeconds
		result.ProviderResultProof.OutputSeconds = usage.OutputSeconds
		result.ProviderResultProof.InputImageCount = usage.InputImageCount
	case "failed", "cancelled":
		// The V2 query contract echoes requested resolution/duration/ratio on
		// failures. These are not proof of generated output; no artifact may
		// accompany the failure and only its reviewed classification is kept.
		if len(task.Content) != 0 || task.Usage != nil || (task.Status == "failed" && (task.Error == nil || strings.TrimSpace(task.Error.Code) == "")) {
			return nil, fmt.Errorf("MiniMax H3 failure response is inconsistent")
		}
		owner, code := "relay", "unclassified_provider_terminal"
		if task.Status == "cancelled" {
			owner, code = "client", "task_cancelled"
		} else if task.Error.Code == "1026" {
			owner, code = "client", "content_policy_rejected"
		} else if task.Error.Code == "1000" {
			owner, code = "provider", "provider_service_failure"
		}
		result.Status, result.Progress = model.TaskStatusFailure, "100%"
		result.ProviderResultProof.FailureOwner, result.ProviderResultProof.FailureCode = owner, code
		result.Reason = "MINIMAX_H3_TASK_" + strings.ToUpper(task.Status) + ": " + code
	default:
		return nil, fmt.Errorf("MiniMax H3 task response has an unknown status")
	}
	return result, nil
}
