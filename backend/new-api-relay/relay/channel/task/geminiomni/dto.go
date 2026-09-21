package geminiomni

type interactionContent struct {
	Type       string `json:"type"`
	Text       string `json:"text,omitempty"`
	Data       string `json:"data,omitempty"`
	MIMEType   string `json:"mime_type,omitempty"`
	URI        string `json:"uri,omitempty"`
	Name       string `json:"name,omitempty"`
	Processing string `json:"processing,omitempty"`
	Resolution string `json:"resolution,omitempty"`
}

type videoResponseFormat struct {
	Type        string `json:"type"`
	AspectRatio string `json:"aspect_ratio"`
	Delivery    string `json:"delivery"`
	Duration    string `json:"duration"`
	Resolution  string `json:"resolution"`
}

type videoGenerationConfig struct {
	VideoConfig struct {
		Task string `json:"task,omitempty"`
	} `json:"video_config"`
}

type interactionRequest struct {
	Model                 string                 `json:"model"`
	Input                 []interactionContent   `json:"input"`
	PreviousInteractionID string                 `json:"previous_interaction_id,omitempty"`
	ResponseFormat        videoResponseFormat    `json:"response_format"`
	GenerationConfig      *videoGenerationConfig `json:"generation_config,omitempty"`
	Background            bool                   `json:"background"`
	Store                 bool                   `json:"store"`
	Stream                bool                   `json:"stream"`
}

type interactionError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type interactionUsage struct {
	InputTokensByModality  []interactionModalityUsage `json:"input_tokens_by_modality,omitempty"`
	OutputTokensByModality []interactionModalityUsage `json:"output_tokens_by_modality,omitempty"`
	TotalCachedTokens      int                        `json:"total_cached_tokens"`
	TotalInputTokens       int                        `json:"total_input_tokens"`
	TotalOutputTokens      int                        `json:"total_output_tokens"`
	TotalThoughtTokens     int                        `json:"total_thought_tokens,omitempty"`
	TotalTokens            int                        `json:"total_tokens"`
	TotalToolUseTokens     int                        `json:"total_tool_use_tokens,omitempty"`
}

type interactionModalityUsage struct {
	Modality string `json:"modality"`
	Tokens   int    `json:"tokens"`
}

type interactionResponse struct {
	Created string             `json:"created,omitempty"`
	ID      string             `json:"id"`
	Model   string             `json:"model"`
	Object  string             `json:"object"`
	Status  string             `json:"status"`
	Steps   []interactionStep  `json:"steps"`
	Errors  []interactionError `json:"errors"`
	Updated string             `json:"updated,omitempty"`
	Usage   *interactionUsage  `json:"usage,omitempty"`
}

type interactionStep struct {
	Type    string               `json:"type"`
	Content []interactionContent `json:"content"`
}

type submissionReceipt struct {
	SchemaVersion     int    `json:"schema_version"`
	Object            string `json:"object"`
	InteractionID     string `json:"interaction_id"`
	FileID            string `json:"file_id"`
	Model             string `json:"model"`
	Status            string `json:"status"`
	UsageComplete     bool   `json:"usage_complete"`
	OutputURISHA256   string `json:"output_uri_sha256"`
	TotalCachedTokens int    `json:"total_cached_tokens"`
	TotalInputTokens  int    `json:"total_input_tokens"`
	TotalOutputTokens int    `json:"total_output_tokens"`
	OutputVideoTokens int    `json:"output_video_tokens"`
	OutputTextTokens  int    `json:"output_text_tokens"`
	ThoughtTokens     int    `json:"thought_tokens"`
	TotalTokens       int    `json:"total_tokens"`
}

type googleFile struct {
	Name           string `json:"name"`
	DisplayName    string `json:"displayName"`
	MIMEType       string `json:"mimeType"`
	SizeBytes      string `json:"sizeBytes"`
	CreateTime     string `json:"createTime"`
	UpdateTime     string `json:"updateTime"`
	ExpirationTime string `json:"expirationTime"`
	SHA256Hash     string `json:"sha256Hash"`
	URI            string `json:"uri"`
	DownloadURI    string `json:"downloadUri"`
	State          string `json:"state"`
	Source         string `json:"source"`
	VideoMetadata  struct {
		VideoDuration string `json:"videoDuration"`
	} `json:"videoMetadata"`
	Error struct {
		Code    int              `json:"code"`
		Message string           `json:"message"`
		Details []map[string]any `json:"details"`
	} `json:"error"`
}

// fileStatusEnvelope is produced locally after a strict Google Files response
// check. It keeps ParseTaskResult independent from mutable adaptor state and
// binds the provider file evidence to the exact request specification and usage
// carried by the persisted upstream task identifier.
type fileStatusEnvelope struct {
	SchemaVersion     int    `json:"schema_version"`
	Object            string `json:"object"`
	TaskID            string `json:"task_id"`
	ProviderModel     string `json:"provider_model"`
	FileID            string `json:"file_id"`
	State             string `json:"state"`
	MIMEType          string `json:"mime_type,omitempty"`
	SizeBytes         int64  `json:"size_bytes,omitempty"`
	SHA256Hash        string `json:"sha256_hash,omitempty"`
	VideoDuration     string `json:"video_duration,omitempty"`
	ArtifactURL       string `json:"artifact_url,omitempty"`
	ProviderErrorCode int    `json:"provider_error_code,omitempty"`
	DurationSeconds   int    `json:"duration_seconds"`
	Resolution        string `json:"resolution"`
	AspectRatio       string `json:"aspect_ratio"`
	UsageComplete     bool   `json:"usage_complete"`
	TotalCachedTokens int    `json:"total_cached_tokens"`
	TotalInputTokens  int    `json:"total_input_tokens"`
	TotalOutputTokens int    `json:"total_output_tokens"`
	OutputVideoTokens int    `json:"output_video_tokens"`
	OutputTextTokens  int    `json:"output_text_tokens"`
	ThoughtTokens     int    `json:"thought_tokens"`
	TotalTokens       int    `json:"total_tokens"`
}
