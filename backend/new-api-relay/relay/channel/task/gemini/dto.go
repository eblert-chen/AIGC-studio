package gemini

// VeoImageInput represents an image input for Veo image-to-video.
// Used by both Gemini and Vertex adaptors.
type VeoImageInput struct {
	BytesBase64Encoded string `json:"bytesBase64Encoded"`
	MimeType           string `json:"mimeType"`
}

// VeoInstance represents a single instance in the Veo predictLongRunning request.
type VeoInstance struct {
	Prompt string         `json:"prompt"`
	Image  *VeoImageInput `json:"image,omitempty"`
	// TODO: support referenceImages (style/asset references, up to 3 images)
	// TODO: support lastFrame (first+last frame interpolation, Veo 3.1)
}

// VeoParameters represents the parameters block for Veo predictLongRunning.
type VeoParameters struct {
	// Gemini Developer API currently calls this numberOfVideos. SampleCount is
	// retained for the shared Vertex converter, whose wire contract is distinct.
	NumberOfVideos     int    `json:"numberOfVideos,omitempty"`
	SampleCount        int    `json:"sampleCount,omitempty"`
	DurationSeconds    int    `json:"durationSeconds,omitempty"`
	AspectRatio        string `json:"aspectRatio,omitempty"`
	Resolution         string `json:"resolution,omitempty"`
	NegativePrompt     string `json:"negativePrompt,omitempty"`
	PersonGeneration   string `json:"personGeneration,omitempty"`
	StorageUri         string `json:"storageUri,omitempty"`
	CompressionQuality string `json:"compressionQuality,omitempty"`
	ResizeMode         string `json:"resizeMode,omitempty"`
	Seed               *int   `json:"seed,omitempty"`
	GenerateAudio      *bool  `json:"generateAudio,omitempty"`
}

// VeoRequestPayload is the top-level request body for the Veo
// predictLongRunning endpoint (used by both Gemini and Vertex).
type VeoRequestPayload struct {
	Instances  []VeoInstance  `json:"instances"`
	Parameters *VeoParameters `json:"parameters,omitempty"`
}

type submitResponse struct {
	Name string `json:"name"`
}

type operationVideo struct {
	URI                string `json:"uri"`
	MimeType           string `json:"mimeType"`
	BytesBase64Encoded string `json:"bytesBase64Encoded"`
	VideoBytes         string `json:"videoBytes"`
	Encoding           string `json:"encoding"`
}

type generatedVideo struct {
	Video operationVideo `json:"video"`
}

type operationResponse struct {
	Name     string `json:"name"`
	Done     bool   `json:"done"`
	Response struct {
		Type                  string           `json:"@type"`
		RaiMediaFilteredCount int              `json:"raiMediaFilteredCount"`
		Videos                []operationVideo `json:"videos"`
		BytesBase64Encoded    string           `json:"bytesBase64Encoded"`
		Encoding              string           `json:"encoding"`
		Video                 string           `json:"video"`
		GenerateVideoResponse struct {
			GeneratedSamples      []generatedVideo `json:"generatedSamples"`
			GeneratedVideos       []generatedVideo `json:"generatedVideos"`
			RaiMediaFilteredCount int              `json:"raiMediaFilteredCount"`
		} `json:"generateVideoResponse"`
	} `json:"response"`
	Error struct {
		Code    int              `json:"code"`
		Message string           `json:"message"`
		Details []map[string]any `json:"details"`
	} `json:"error"`
}

type veoSubmissionReceipt struct {
	SchemaVersion   int    `json:"schema_version"`
	Object          string `json:"object"`
	TaskID          string `json:"task_id"`
	OperationName   string `json:"operation_name"`
	ProviderModel   string `json:"provider_model"`
	DurationSeconds int    `json:"duration_seconds"`
	Resolution      string `json:"resolution"`
	AspectRatio     string `json:"aspect_ratio"`
}

// veoOperationEnvelope is a local, fixed-shape rendering of a provider LRO.
// ParseTaskResult never relies on adaptor state or mutable channel settings.
type veoOperationEnvelope struct {
	SchemaVersion          int    `json:"schema_version"`
	Object                 string `json:"object"`
	TaskID                 string `json:"task_id"`
	OperationName          string `json:"operation_name"`
	ProviderModel          string `json:"provider_model"`
	ProviderState          string `json:"provider_state"`
	DurationSeconds        int    `json:"duration_seconds"`
	Resolution             string `json:"resolution"`
	AspectRatio            string `json:"aspect_ratio"`
	ArtifactURL            string `json:"artifact_url,omitempty"`
	FailureOwner           string `json:"failure_owner,omitempty"`
	FailureCode            string `json:"failure_code,omitempty"`
	ProviderResponseBytes  int    `json:"provider_response_bytes"`
	ProviderResponseSHA256 string `json:"provider_response_sha256"`
}
