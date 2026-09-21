package geminiomni

import (
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

const (
	defaultDurationSeconds = 5
	defaultResolution      = "720p"
	defaultAspectRatio     = "16:9"
	maxOmniImageInputs     = 2
	maxOmniPromptRunes     = 2_000
	maxOmniTokenCount      = 1_000_000_000
)

var interactionIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,512}$`)

type requestSpec struct {
	DurationSeconds       int
	Resolution            string
	AspectRatio           string
	Task                  string
	PreviousInteractionID string
}

func resolveRequestSpec(req relaycommon.TaskSubmitReq) (requestSpec, error) {
	spec := requestSpec{
		DurationSeconds: defaultDurationSeconds,
		Resolution:      defaultResolution,
		AspectRatio:     defaultAspectRatio,
	}
	if req.Duration > 0 {
		spec.DurationSeconds = req.Duration
	} else if strings.TrimSpace(req.Seconds) != "" {
		seconds, err := strconv.Atoi(strings.TrimSpace(req.Seconds))
		if err != nil {
			return requestSpec{}, errors.New("seconds must be an integer")
		}
		spec.DurationSeconds = seconds
	}
	if value := metadataString(req.Metadata, "durationSeconds", "duration_seconds"); value != "" {
		seconds, err := strconv.Atoi(value)
		if err != nil {
			return requestSpec{}, errors.New("durationSeconds must be an integer")
		}
		spec.DurationSeconds = seconds
	}
	if spec.DurationSeconds < 3 || spec.DurationSeconds > 10 {
		return requestSpec{}, errors.New("duration must be between 3 and 10 seconds")
	}

	if value := metadataString(req.Metadata, "resolution"); value != "" {
		spec.Resolution = strings.ToLower(value)
	} else if inferred := resolutionFromSize(req.Size); inferred != "" {
		spec.Resolution = inferred
	}
	if spec.Resolution == "2160p" || spec.Resolution == "4k" {
		spec.Resolution = "4k"
	}
	switch spec.Resolution {
	case "360p", "720p", "1080p", "4k":
	default:
		return requestSpec{}, errors.New("resolution is not supported by Gemini Omni")
	}

	if value := metadataString(req.Metadata, "aspectRatio", "aspect_ratio"); value != "" {
		spec.AspectRatio = value
	} else if inferred := aspectRatioFromSize(req.Size); inferred != "" {
		spec.AspectRatio = inferred
	}
	switch spec.AspectRatio {
	case "16:9", "9:16":
	default:
		return requestSpec{}, errors.New("aspect ratio is not supported by Gemini Omni")
	}

	mode := strings.TrimSpace(req.Mode)
	if mode == "" {
		mode = metadataString(req.Metadata, "platform_generation_mode", "mode")
	}
	explicitTask := metadataString(req.Metadata, "omni_task", "task")
	if explicitTask != "" {
		spec.Task = explicitTask
	} else {
		switch mode {
		case "", "text_to_video":
			spec.Task = "text_to_video"
		case "image_to_video":
			spec.Task = "image_to_video"
		default:
			return requestSpec{}, errors.New("Gemini Omni lineage and reference modes are not published by this adapter")
		}
	}
	switch spec.Task {
	case "text_to_video", "image_to_video":
	default:
		return requestSpec{}, errors.New("Gemini Omni lineage and reference tasks are not published by this adapter")
	}

	spec.PreviousInteractionID = metadataString(req.Metadata, "previous_interaction_id")
	if spec.PreviousInteractionID != "" {
		return requestSpec{}, errors.New("previous_interaction_id requires a future versioned lineage contract")
	}
	return spec, nil
}

func validateTaskRequest(req relaycommon.TaskSubmitReq, spec requestSpec) error {
	prompt := strings.TrimSpace(req.Prompt)
	if prompt == "" {
		return errors.New("prompt is required")
	}
	if !utf8.ValidString(prompt) || utf8.RuneCountInString(prompt) > maxOmniPromptRunes {
		return fmt.Errorf("prompt must contain at most %d characters", maxOmniPromptRunes)
	}
	if len(req.Audios) != 0 || len(normalizedVideos(req)) != 0 {
		return errors.New("Gemini Omni audio/video references require a future versioned reference contract")
	}
	images := normalizedImages(req)
	if len(images) > maxOmniImageInputs {
		return fmt.Errorf("Gemini Omni image_to_video accepts at most %d images", maxOmniImageInputs)
	}
	switch spec.Task {
	case "text_to_video":
		if len(images) != 0 {
			return errors.New("text_to_video does not accept media or interaction lineage")
		}
	case "image_to_video":
		if len(images) == 0 || len(images) > 2 {
			return errors.New("image_to_video requires one or two images and no video lineage")
		}
	}
	return nil
}

func buildInteractionInput(req relaycommon.TaskSubmitReq) ([]interactionContent, error) {
	inputs := make([]interactionContent, 0, len(req.Images)+2)
	images := normalizedImages(req)
	for _, raw := range images {
		content, err := parseMediaInput(raw, "image", "image/jpeg")
		if err != nil {
			return nil, fmt.Errorf("invalid image input: %w", err)
		}
		inputs = append(inputs, content)
	}
	inputs = append(inputs, interactionContent{Type: "text", Text: strings.TrimSpace(req.Prompt)})
	return inputs, nil
}

func parseMediaInput(raw string, mediaType string, defaultMIME string) (interactionContent, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return interactionContent{}, errors.New("media input is empty")
	}
	if strings.HasPrefix(strings.ToLower(raw), "data:") {
		comma := strings.IndexByte(raw, ',')
		if comma <= len("data:") {
			return interactionContent{}, errors.New("data URI is malformed")
		}
		header := raw[len("data:"):comma]
		if !strings.HasSuffix(strings.ToLower(header), ";base64") {
			return interactionContent{}, errors.New("data URI must be base64 encoded")
		}
		mime := header[:len(header)-len(";base64")]
		if !validMediaMIME(mediaType, mime) {
			return interactionContent{}, errors.New("data URI media type is unsupported")
		}
		data := raw[comma+1:]
		if err := validateBase64(data); err != nil {
			return interactionContent{}, err
		}
		return interactionContent{Type: mediaType, Data: data, MIMEType: mime}, nil
	}
	if parsed, err := url.Parse(raw); err == nil && parsed.IsAbs() {
		if parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" {
			return interactionContent{}, errors.New("media URI must be an absolute HTTPS URL")
		}
		return interactionContent{Type: mediaType, URI: parsed.String()}, nil
	}
	if err := validateBase64(raw); err != nil {
		return interactionContent{}, errors.New("media input must be HTTPS or base64")
	}
	return interactionContent{Type: mediaType, Data: raw, MIMEType: defaultMIME}, nil
}

func validateBase64(value string) error {
	if value == "" {
		return errors.New("base64 media is empty")
	}
	decoder := base64.NewDecoder(base64.StdEncoding.Strict(), strings.NewReader(value))
	if _, err := io.Copy(io.Discard, decoder); err != nil {
		return errors.New("base64 media is invalid")
	}
	return nil
}

func validMediaMIME(mediaType string, mime string) bool {
	mime = strings.ToLower(strings.TrimSpace(mime))
	if mediaType == "image" {
		switch mime {
		case "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif", "image/gif", "image/bmp", "image/tiff":
			return true
		}
	}
	if mediaType == "video" {
		switch mime {
		case "video/mp4", "video/mpeg", "video/mpg", "video/mov", "video/avi", "video/x-flv", "video/webm", "video/wmv", "video/3gpp":
			return true
		}
	}
	return false
}

func normalizedVideos(req relaycommon.TaskSubmitReq) []string {
	videos := append([]string(nil), req.Videos...)
	reference := strings.TrimSpace(req.InputReference)
	if reference == "" {
		return videos
	}
	for _, value := range videos {
		if strings.TrimSpace(value) == reference {
			return videos
		}
	}
	return append(videos, reference)
}

func normalizedImages(req relaycommon.TaskSubmitReq) []string {
	images := append([]string(nil), req.Images...)
	legacy := strings.TrimSpace(req.Image)
	if legacy == "" {
		return images
	}
	for _, value := range images {
		if strings.TrimSpace(value) == legacy {
			return images
		}
	}
	return append(images, legacy)
}

func metadataString(metadata map[string]any, keys ...string) string {
	for _, key := range keys {
		value, ok := metadata[key]
		if !ok || value == nil {
			continue
		}
		switch typed := value.(type) {
		case string:
			if result := strings.TrimSpace(typed); result != "" {
				return result
			}
		case float64:
			if typed == float64(int64(typed)) {
				return strconv.FormatInt(int64(typed), 10)
			}
		case int:
			return strconv.Itoa(typed)
		case int64:
			return strconv.FormatInt(typed, 10)
		}
	}
	return ""
}

func resolutionFromSize(size string) string {
	switch strings.ToLower(strings.TrimSpace(size)) {
	case "640x360", "360x640":
		return "360p"
	case "1280x720", "720x1280":
		return "720p"
	case "1920x1080", "1080x1920":
		return "1080p"
	case "3840x2160", "2160x3840":
		return "4k"
	default:
		return ""
	}
}

func aspectRatioFromSize(size string) string {
	parts := strings.Split(strings.ToLower(strings.TrimSpace(size)), "x")
	if len(parts) != 2 {
		return ""
	}
	width, errWidth := strconv.Atoi(parts[0])
	height, errHeight := strconv.Atoi(parts[1])
	if errWidth != nil || errHeight != nil || width <= 0 || height <= 0 {
		return ""
	}
	if width > height {
		return "16:9"
	}
	return "9:16"
}

func validateUsage(usage interactionUsage) error {
	values := []int{
		usage.TotalCachedTokens,
		usage.TotalInputTokens,
		usage.TotalOutputTokens,
		usage.TotalThoughtTokens,
		usage.TotalToolUseTokens,
		usage.TotalTokens,
	}
	for _, value := range values {
		if value < 0 || value > maxOmniTokenCount {
			return errors.New("interaction usage is outside the supported range")
		}
	}
	videoTokens, textTokens, err := outputTokensByModality(usage.OutputTokensByModality)
	if err != nil {
		return err
	}
	if usage.TotalInputTokens <= 0 || usage.TotalOutputTokens <= 0 || usage.TotalTokens <= 0 ||
		usage.TotalCachedTokens > usage.TotalInputTokens ||
		videoTokens <= 0 ||
		videoTokens+textTokens != usage.TotalOutputTokens ||
		usage.TotalToolUseTokens != 0 ||
		usage.TotalTokens != usage.TotalInputTokens+usage.TotalOutputTokens+usage.TotalThoughtTokens {
		return errors.New("interaction usage is incomplete")
	}
	return nil
}

func interactionUsageEmpty(usage interactionUsage) bool {
	return len(usage.InputTokensByModality) == 0 &&
		len(usage.OutputTokensByModality) == 0 &&
		usage.TotalCachedTokens == 0 &&
		usage.TotalInputTokens == 0 &&
		usage.TotalOutputTokens == 0 &&
		usage.TotalThoughtTokens == 0 &&
		usage.TotalTokens == 0 &&
		usage.TotalToolUseTokens == 0
}

func outputTokensByModality(values []interactionModalityUsage) (int, int, error) {
	if len(values) == 0 {
		return 0, 0, errors.New("interaction output usage by modality is missing")
	}
	seen := make(map[string]struct{}, len(values))
	videoTokens := 0
	textTokens := 0
	for _, value := range values {
		if value.Tokens <= 0 || value.Tokens > maxOmniTokenCount {
			return 0, 0, errors.New("interaction output modality usage is outside the supported range")
		}
		if _, exists := seen[value.Modality]; exists {
			return 0, 0, errors.New("interaction output usage contains a duplicate modality")
		}
		seen[value.Modality] = struct{}{}
		switch value.Modality {
		case "video":
			videoTokens = value.Tokens
		case "text":
			textTokens = value.Tokens
		default:
			return 0, 0, errors.New("interaction output usage contains an unsupported modality")
		}
	}
	return videoTokens, textTokens, nil
}
