package gemini

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/common"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
)

const (
	ProviderProtocolV1       = "google-gemini-veo-predict-long-running-v1"
	veoTaskIdentityPrefix    = "gveo"
	veoTaskIdentityRevision  = "v1"
	veoOperationEnvelopeName = "gemini_veo_operation_status"
	veoSubmissionReceiptName = "gemini_veo_submission_receipt"
)

var (
	veoOperationIDPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,128}$`)
	veoFileIDPattern      = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$`)
)

type veoTaskSpec struct {
	Model           string
	DurationSeconds int
	Resolution      string
	AspectRatio     string
}

type veoTaskIdentity struct {
	veoTaskSpec
	OperationID   string
	OperationName string
}

var veoModelCodes = map[string]string{
	"veo-3.0-generate-001":          "30",
	"veo-3.0-fast-generate-001":     "30f",
	"veo-3.1-generate-preview":      "31p",
	"veo-3.1-fast-generate-preview": "31fp",
}

var veoCodeModels = func() map[string]string {
	models := make(map[string]string, len(veoModelCodes))
	for model, code := range veoModelCodes {
		models[code] = model
	}
	return models
}()

func resolveVeoTaskSpec(req relaycommon.TaskSubmitReq, modelName string) (veoTaskSpec, error) {
	if _, supported := veoModelCodes[modelName]; !supported {
		return veoTaskSpec{}, errors.New("unsupported Gemini Veo model")
	}
	spec := veoTaskSpec{
		Model:           modelName,
		DurationSeconds: 8,
		Resolution:      "720p",
		AspectRatio:     "16:9",
	}
	if raw, exists := req.Metadata["durationSeconds"]; exists {
		value, err := exactVeoInteger(raw)
		if err != nil {
			return veoTaskSpec{}, errors.New("durationSeconds must be an integer")
		}
		spec.DurationSeconds = value
	} else if req.Duration != 0 {
		spec.DurationSeconds = req.Duration
	} else if strings.TrimSpace(req.Seconds) != "" {
		value, err := strconv.Atoi(strings.TrimSpace(req.Seconds))
		if err != nil {
			return veoTaskSpec{}, errors.New("seconds must be an integer")
		}
		spec.DurationSeconds = value
	}
	switch spec.DurationSeconds {
	case 4, 6, 8:
	default:
		return veoTaskSpec{}, errors.New("Gemini Veo duration must be 4, 6, or 8 seconds")
	}

	if raw, exists := req.Metadata["resolution"]; exists {
		value, ok := raw.(string)
		if !ok || strings.TrimSpace(value) == "" {
			return veoTaskSpec{}, errors.New("resolution must be a string")
		}
		spec.Resolution = strings.ToLower(strings.TrimSpace(value))
	} else if strings.TrimSpace(req.Size) != "" {
		spec.Resolution = SizeToVeoResolution(req.Size)
	}
	switch spec.Resolution {
	case "720p", "1080p", "4k":
	default:
		return veoTaskSpec{}, errors.New("Gemini Veo resolution is unsupported")
	}
	if spec.Resolution != "720p" && spec.DurationSeconds != 8 {
		return veoTaskSpec{}, errors.New("Gemini Veo high-resolution output requires 8 seconds")
	}

	if raw, exists := req.Metadata["aspectRatio"]; exists {
		value, ok := raw.(string)
		if !ok || strings.TrimSpace(value) == "" {
			return veoTaskSpec{}, errors.New("aspectRatio must be a string")
		}
		spec.AspectRatio = strings.TrimSpace(value)
	} else if strings.TrimSpace(req.Size) != "" {
		spec.AspectRatio = SizeToVeoAspectRatio(req.Size)
	}
	switch spec.AspectRatio {
	case "16:9", "9:16":
	default:
		return veoTaskSpec{}, errors.New("Gemini Veo aspect ratio is unsupported")
	}
	if value, exists := req.Metadata["storageUri"]; exists && strings.TrimSpace(fmt.Sprint(value)) != "" {
		return veoTaskSpec{}, errors.New("Gemini Veo custom storage output is unsupported")
	}
	return spec, nil
}

func exactVeoInteger(raw any) (int, error) {
	switch value := raw.(type) {
	case int:
		return value, nil
	case int64:
		if value < -1<<31 || value > 1<<31-1 {
			return 0, errors.New("integer is out of range")
		}
		return int(value), nil
	case float64:
		if value != float64(int64(value)) {
			return 0, errors.New("number is not integral")
		}
		return int(value), nil
	case string:
		return strconv.Atoi(strings.TrimSpace(value))
	default:
		return 0, errors.New("value is not an integer")
	}
}

func parseVeoOperationName(name string) (modelName string, operationID string, err error) {
	parts := strings.Split(strings.TrimSpace(name), "/")
	if len(parts) != 4 || parts[0] != "models" || parts[2] != "operations" ||
		!veoOperationIDPattern.MatchString(parts[3]) {
		return "", "", errors.New("Gemini Veo operation name is invalid")
	}
	if _, supported := veoModelCodes[parts[1]]; !supported {
		return "", "", errors.New("Gemini Veo operation model is unsupported")
	}
	return parts[1], parts[3], nil
}

func buildVeoTaskID(operationName string, spec veoTaskSpec) (string, error) {
	modelName, operationID, err := parseVeoOperationName(operationName)
	if err != nil || modelName != spec.Model {
		return "", errors.New("Gemini Veo operation identity does not match the request")
	}
	code := veoModelCodes[modelName]
	aspect := strings.ReplaceAll(spec.AspectRatio, ":", "x")
	checksum := veoTaskIdentityChecksum(modelName, operationID, spec.DurationSeconds, spec.Resolution, aspect)
	taskID := fmt.Sprintf("%s:%s:%s:%d:%s:%s:%s:%s", veoTaskIdentityPrefix, code, operationID,
		spec.DurationSeconds, spec.Resolution, aspect, checksum, veoTaskIdentityRevision)
	if len(taskID) > 191 {
		return "", errors.New("Gemini Veo task identity is too long")
	}
	return taskID, nil
}

func parseVeoTaskID(taskID string) (veoTaskIdentity, error) {
	parts := strings.Split(strings.TrimSpace(taskID), ":")
	if len(parts) != 8 || parts[0] != veoTaskIdentityPrefix || parts[7] != veoTaskIdentityRevision {
		return veoTaskIdentity{}, errors.New("Gemini Veo task identity is invalid")
	}
	modelName, ok := veoCodeModels[parts[1]]
	if !ok || !veoOperationIDPattern.MatchString(parts[2]) {
		return veoTaskIdentity{}, errors.New("Gemini Veo task operation identity is invalid")
	}
	duration, err := strconv.Atoi(parts[3])
	if err != nil {
		return veoTaskIdentity{}, errors.New("Gemini Veo task duration identity is invalid")
	}
	aspect := ""
	switch parts[5] {
	case "16x9":
		aspect = "16:9"
	case "9x16":
		aspect = "9:16"
	default:
		return veoTaskIdentity{}, errors.New("Gemini Veo task aspect identity is invalid")
	}
	if parts[6] != veoTaskIdentityChecksum(modelName, parts[2], duration, parts[4], parts[5]) {
		return veoTaskIdentity{}, errors.New("Gemini Veo task identity checksum is invalid")
	}
	spec, err := resolveVeoTaskSpec(relaycommon.TaskSubmitReq{
		Duration: duration,
		Metadata: map[string]any{"resolution": parts[4], "aspectRatio": aspect},
	}, modelName)
	if err != nil {
		return veoTaskIdentity{}, errors.New("Gemini Veo task specification identity is invalid")
	}
	operationName := "models/" + modelName + "/operations/" + parts[2]
	return veoTaskIdentity{
		veoTaskSpec:   spec,
		OperationID:   parts[2],
		OperationName: operationName,
	}, nil
}

func veoTaskIdentityChecksum(modelName string, operationID string, duration int, resolution string, aspect string) string {
	canonical := fmt.Sprintf("%s\x00%s\x00%d\x00%s\x00%s\x00%s", modelName, operationID, duration, resolution, aspect, veoTaskIdentityRevision)
	digest := sha256.Sum256([]byte(canonical))
	return hex.EncodeToString(digest[:8])
}

func buildVeoOperationURL(baseURL string, operationName string) (string, error) {
	if _, _, err := parseVeoOperationName(operationName); err != nil {
		return "", err
	}
	base, err := url.Parse(strings.TrimRight(strings.TrimSpace(baseURL), "/"))
	if err != nil || base.Scheme == "" || base.Host == "" || base.User != nil || base.RawQuery != "" || base.Fragment != "" {
		return "", errors.New("Gemini base URL is invalid")
	}
	base.Path = strings.TrimSuffix(base.Path, "/v1beta") + "/v1beta/" + operationName
	return base.String(), nil
}

func canonicalVeoFileDownloadURL(raw string) (string, error) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Scheme != "https" || !strings.EqualFold(parsed.Hostname(), "generativelanguage.googleapis.com") ||
		(parsed.Port() != "" && parsed.Port() != "443") || parsed.User != nil || parsed.Fragment != "" || parsed.RawPath != "" ||
		(parsed.RawQuery != "" && parsed.RawQuery != "alt=media") {
		return "", errors.New("Gemini Veo output is not a credential-free Google Files locator")
	}
	const prefix = "/v1beta/files/"
	if !strings.HasPrefix(parsed.Path, prefix) || !strings.HasSuffix(parsed.Path, ":download") {
		return "", errors.New("Gemini Veo output is outside the Files download endpoint")
	}
	fileID := strings.TrimSuffix(strings.TrimPrefix(parsed.Path, prefix), ":download")
	if !veoFileIDPattern.MatchString(fileID) {
		return "", errors.New("Gemini Veo output file identity is invalid")
	}
	return "https://generativelanguage.googleapis.com/v1beta/files/" + fileID + ":download?alt=media", nil
}

func buildVeoOperationEnvelope(raw []byte, taskID string, providerModel string) ([]byte, error) {
	identity, err := parseVeoTaskID(taskID)
	if err != nil {
		return nil, err
	}
	if providerModel != "" && providerModel != identity.Model {
		return nil, errors.New("Gemini Veo polling model drifted from its task identity")
	}
	if err := common.RejectDuplicateJSONKeys(raw); err != nil {
		return nil, errors.New("Gemini Veo operation response contains duplicate fields")
	}
	var operation operationResponse
	if err := common.Unmarshal(raw, &operation); err != nil {
		return nil, errors.New("Gemini Veo operation response is invalid")
	}
	if operation.Name != identity.OperationName {
		return nil, errors.New("Gemini Veo operation response identity is inconsistent")
	}
	filtered := operation.Response.RaiMediaFilteredCount + operation.Response.GenerateVideoResponse.RaiMediaFilteredCount
	if filtered < 0 {
		return nil, errors.New("Gemini Veo filtering evidence is invalid")
	}
	outputs := make([]operationVideo, 0, 2)
	for _, sample := range operation.Response.GenerateVideoResponse.GeneratedSamples {
		outputs = append(outputs, sample.Video)
	}
	for _, video := range operation.Response.GenerateVideoResponse.GeneratedVideos {
		outputs = append(outputs, video.Video)
	}
	hasInline := operation.Response.BytesBase64Encoded != "" || operation.Response.Video != "" || len(operation.Response.Videos) != 0
	for _, output := range outputs {
		if output.BytesBase64Encoded != "" || output.VideoBytes != "" || output.Encoding != "" {
			hasInline = true
		}
	}
	digest := sha256.Sum256(raw)
	envelope := veoOperationEnvelope{
		SchemaVersion:          1,
		Object:                 veoOperationEnvelopeName,
		TaskID:                 taskID,
		OperationName:          identity.OperationName,
		ProviderModel:          identity.Model,
		DurationSeconds:        identity.DurationSeconds,
		Resolution:             identity.Resolution,
		AspectRatio:            identity.AspectRatio,
		ProviderResponseBytes:  len(raw),
		ProviderResponseSHA256: hex.EncodeToString(digest[:]),
	}
	hasError := operation.Error.Code != 0 || strings.TrimSpace(operation.Error.Message) != ""
	switch {
	case !operation.Done:
		if hasError || filtered != 0 || len(outputs) != 0 || hasInline {
			return nil, errors.New("Gemini Veo non-terminal operation contains terminal material")
		}
		envelope.ProviderState = "processing"
	case hasError:
		if filtered != 0 || len(outputs) != 0 || hasInline {
			return nil, errors.New("Gemini Veo failed operation contains conflicting output material")
		}
		envelope.ProviderState = "failed"
		envelope.FailureOwner = "provider"
		envelope.FailureCode = "provider_service_failure"
	case filtered > 0:
		if len(outputs) != 0 || hasInline {
			return nil, errors.New("Gemini Veo filtered operation contains conflicting output material")
		}
		envelope.ProviderState = "failed"
		envelope.FailureOwner = "client"
		envelope.FailureCode = "content_policy_rejected"
	default:
		if len(outputs) != 1 || hasInline {
			return nil, errors.New("Gemini Veo succeeded operation must contain one URI video")
		}
		if outputs[0].MimeType != "" && outputs[0].MimeType != "video/mp4" {
			return nil, errors.New("Gemini Veo output MIME type is unsupported")
		}
		artifactURL, err := canonicalVeoFileDownloadURL(outputs[0].URI)
		if err != nil {
			return nil, err
		}
		envelope.ProviderState = "succeeded"
		envelope.ArtifactURL = artifactURL
	}
	return common.Marshal(envelope)
}

func replaceVeoResponseBody(response *http.Response, body []byte) *http.Response {
	response.Body = io.NopCloser(bytes.NewReader(body))
	response.ContentLength = int64(len(body))
	response.Header = response.Header.Clone()
	response.Header.Set("Content-Type", "application/json")
	response.Header.Set("Content-Length", strconv.Itoa(len(body)))
	return response
}

func validateVeoEnvelopeDigest(envelope veoOperationEnvelope) error {
	digest, err := hex.DecodeString(envelope.ProviderResponseSHA256)
	if err != nil || len(digest) != sha256.Size || envelope.ProviderResponseBytes <= 0 {
		return errors.New("Gemini Veo provider response evidence is invalid")
	}
	return nil
}

// Preserve the old base64 operation identifier for tasks submitted before the
// versioned identity was introduced. It cannot satisfy protected Platform
// proof because it carries no immutable output specification.
func decodeLegacyVeoTaskID(taskID string) (string, error) {
	decoded, err := base64.RawURLEncoding.DecodeString(taskID)
	if err != nil {
		return "", err
	}
	operationName := string(decoded)
	if _, _, err := parseVeoOperationName(operationName); err != nil {
		return "", err
	}
	return operationName, nil
}
