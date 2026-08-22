package dto

import (
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	PlatformRelayAPIVersion    = "v1"
	PlatformRelaySchemaVersion = 1
)

var (
	platformRelayRevisionPattern    = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	platformRelayAspectRatioPattern = regexp.MustCompile(`^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$`)
	platformRelayResolutionPattern  = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$`)
)

type PlatformGenerationAssetInput struct {
	URL       string `json:"url"`
	MediaType string `json:"media_type"`
}

type PlatformGenerationInputs struct {
	Prompt string                         `json:"prompt"`
	Assets []PlatformGenerationAssetInput `json:"assets"`
}

type PlatformGenerationOutputOptions struct {
	DurationSeconds int    `json:"duration_seconds"`
	AspectRatio     string `json:"aspect_ratio"`
	Resolution      string `json:"resolution"`
	Count           int    `json:"count"`
	FaceEnabled     bool   `json:"face_enabled"`
}

type PlatformGenerationCallback struct {
	URL string `json:"url"`
}

type PlatformGenerationRequest struct {
	ClientReferenceID          *string                         `json:"client_reference_id"`
	Model                      string                          `json:"model"`
	ExpectedCapabilityRevision string                          `json:"expected_capability_revision"`
	Mode                       string                          `json:"mode"`
	Inputs                     PlatformGenerationInputs        `json:"inputs"`
	Output                     PlatformGenerationOutputOptions `json:"output"`
	Metadata                   map[string]any                  `json:"metadata"`
	Callback                   *PlatformGenerationCallback     `json:"callback,omitempty"`
}

func NewPlatformGenerationRequest() PlatformGenerationRequest {
	return PlatformGenerationRequest{
		Inputs: PlatformGenerationInputs{Assets: make([]PlatformGenerationAssetInput, 0)},
		Output: PlatformGenerationOutputOptions{
			DurationSeconds: 5,
			AspectRatio:     "16:9",
			Resolution:      "720p",
			Count:           1,
		},
		Metadata: make(map[string]any),
	}
}

func (r PlatformGenerationRequest) Validate() error {
	if r.ClientReferenceID != nil && utf8.RuneCountInString(*r.ClientReferenceID) > 128 {
		return fmt.Errorf("client_reference_id exceeds 128 characters")
	}
	if strings.TrimSpace(r.Model) == "" || utf8.RuneCountInString(r.Model) > 128 {
		return fmt.Errorf("model must contain 1 to 128 characters")
	}
	if !platformRelayRevisionPattern.MatchString(r.ExpectedCapabilityRevision) {
		return fmt.Errorf("expected_capability_revision is invalid")
	}
	switch r.Mode {
	case "text_to_image", "text_to_video", "image_to_video", "video_to_video":
	default:
		return fmt.Errorf("mode is not supported")
	}
	promptLength := utf8.RuneCountInString(r.Inputs.Prompt)
	if promptLength < 1 || promptLength > 10_000 {
		return fmt.Errorf("inputs.prompt must contain 1 to 10000 characters")
	}
	if len(r.Inputs.Assets) > 15 {
		return fmt.Errorf("inputs.assets exceeds 15 items")
	}
	hasImage := false
	hasVideo := false
	for _, asset := range r.Inputs.Assets {
		parsed, err := url.Parse(asset.URL)
		if err != nil || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
			return fmt.Errorf("asset URL is invalid")
		}
		switch asset.MediaType {
		case "image":
			hasImage = true
		case "video":
			hasVideo = true
		case "audio":
		default:
			return fmt.Errorf("asset media_type is invalid")
		}
	}
	if r.Mode == "image_to_video" && !hasImage {
		return fmt.Errorf("image_to_video requires at least one image asset")
	}
	if r.Mode == "video_to_video" && !hasVideo {
		return fmt.Errorf("video_to_video requires at least one video asset")
	}
	if r.Output.DurationSeconds < 1 || r.Output.DurationSeconds > 3600 {
		return fmt.Errorf("output.duration_seconds must be between 1 and 3600")
	}
	if !platformRelayAspectRatioPattern.MatchString(r.Output.AspectRatio) {
		return fmt.Errorf("output.aspect_ratio is invalid")
	}
	if !platformRelayResolutionPattern.MatchString(r.Output.Resolution) {
		return fmt.Errorf("output.resolution is invalid")
	}
	if r.Output.Count < 1 || r.Output.Count > 16 {
		return fmt.Errorf("output.count must be between 1 and 16")
	}
	if r.Callback != nil {
		parsed, err := url.Parse(r.Callback.URL)
		if err != nil || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
			return fmt.Errorf("callback URL is invalid")
		}
	}
	return nil
}

type PlatformGenerationErrorDetail struct {
	Code      string         `json:"code"`
	Message   string         `json:"message"`
	Retryable bool           `json:"retryable"`
	Details   map[string]any `json:"details"`
}

type PlatformGenerationErrorEnvelopeDetail struct {
	Code      string         `json:"code"`
	Message   string         `json:"message"`
	Retryable bool           `json:"retryable"`
	Details   map[string]any `json:"details"`
	RequestID string         `json:"request_id"`
}

type PlatformGenerationErrorEnvelope struct {
	APIVersion    string                                `json:"api_version"`
	SchemaVersion int                                   `json:"schema_version"`
	Error         PlatformGenerationErrorEnvelopeDetail `json:"error"`
}

type PlatformGenerationArtifact struct {
	AssetID     string `json:"asset_id"`
	ObjectKey   string `json:"object_key"`
	MediaType   string `json:"media_type"`
	ContentType string `json:"content_type"`
	SizeBytes   int64  `json:"size_bytes"`
	SHA256      string `json:"sha256"`
}

type PlatformGenerationAccepted struct {
	APIVersion                 string    `json:"api_version"`
	SchemaVersion              int       `json:"schema_version"`
	Object                     string    `json:"object"`
	ID                         string    `json:"id"`
	JobID                      string    `json:"job_id"`
	Status                     string    `json:"status"`
	ExpectedCapabilityRevision string    `json:"expected_capability_revision"`
	CapabilityRevision         string    `json:"capability_revision"`
	ReservationAction          string    `json:"reservation_action"`
	IdempotentReplay           bool      `json:"idempotent_replay"`
	CreatedAt                  time.Time `json:"created_at"`
}

type PlatformGenerationSnapshot struct {
	APIVersion                 string                          `json:"api_version"`
	SchemaVersion              int                             `json:"schema_version"`
	Object                     string                          `json:"object"`
	ID                         string                          `json:"id"`
	ClientReferenceID          *string                         `json:"client_reference_id"`
	Model                      string                          `json:"model"`
	ExpectedCapabilityRevision string                          `json:"expected_capability_revision"`
	CapabilityRevision         string                          `json:"capability_revision"`
	Mode                       string                          `json:"mode"`
	Inputs                     PlatformGenerationInputs        `json:"inputs"`
	Output                     PlatformGenerationOutputOptions `json:"output"`
	Metadata                   map[string]any                  `json:"metadata"`
	Status                     string                          `json:"status"`
	ReservationAction          string                          `json:"reservation_action"`
	Progress                   int                             `json:"progress"`
	Outputs                    []PlatformGenerationArtifact    `json:"outputs"`
	Error                      *PlatformGenerationErrorDetail  `json:"error"`
	CreatedAt                  time.Time                       `json:"created_at"`
	UpdatedAt                  time.Time                       `json:"updated_at"`
}

type PlatformSignedDownload struct {
	APIVersion     string                          `json:"api_version"`
	SchemaVersion  int                             `json:"schema_version"`
	URL            string                          `json:"url"`
	ExpiresSeconds int                             `json:"expires_seconds"`
	StorageBinding *PlatformArtifactStorageBinding `json:"storage_binding,omitempty"`
}

type PlatformArtifactStorageBinding struct {
	Provider     string `json:"provider"`
	EndpointHost string `json:"endpoint_host"`
	Bucket       string `json:"bucket"`
	ObjectKey    string `json:"object_key"`
	IssuedAt     string `json:"issued_at"`
	ExpiresAt    string `json:"expires_at"`
	URLSHA256    string `json:"url_sha256"`
}

type PlatformCapabilityLimits struct {
	MaxPromptLength int      `json:"max_prompt_length"`
	MaxImages       int      `json:"max_images"`
	MaxVideos       int      `json:"max_videos"`
	MaxAudio        int      `json:"max_audio"`
	DurationSeconds []int    `json:"duration_seconds"`
	AspectRatios    []string `json:"aspect_ratios"`
	Resolutions     []string `json:"resolutions"`
	OutputCounts    []int    `json:"output_counts"`
}

type PlatformModeCapability struct {
	InputMediaTypes      []string                 `json:"input_media_types"`
	SupportsFace         bool                     `json:"supports_face"`
	RequiredResourceKeys []string                 `json:"required_resource_keys"`
	Limits               PlatformCapabilityLimits `json:"limits"`
}

type PlatformGenerationCapabilities struct {
	SchemaVersion int                               `json:"schema_version"`
	Modes         map[string]PlatformModeCapability `json:"modes"`
}

type PlatformModelResource struct {
	APIVersion         string                         `json:"api_version"`
	SchemaVersion      int                            `json:"schema_version"`
	ID                 string                         `json:"id"`
	Object             string                         `json:"object"`
	CapabilityRevision string                         `json:"capability_revision"`
	Capabilities       PlatformGenerationCapabilities `json:"capabilities"`
}

type PlatformModelCatalog struct {
	APIVersion      string                  `json:"api_version"`
	SchemaVersion   int                     `json:"schema_version"`
	Object          string                  `json:"object"`
	Data            []PlatformModelResource `json:"data"`
	CatalogRevision string                  `json:"catalog_revision"`
}
