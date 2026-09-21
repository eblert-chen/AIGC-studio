package geminiomni

import (
	"bytes"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
)

var fileIDPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$`)

type persistedTaskIdentity struct {
	SchemaVersion     int
	FileID            string
	DurationSeconds   int
	Resolution        string
	AspectRatio       string
	UsageComplete     bool
	TotalInputTokens  int
	TotalOutputTokens int
	OutputVideoTokens int
	OutputTextTokens  int
	ThoughtTokens     int
	TotalCachedTokens int
	TotalTokens       int
}

func buildPersistedTaskID(fileID string, spec requestSpec, usage interactionUsage) (string, error) {
	if !fileIDPattern.MatchString(fileID) {
		return "", errors.New("Gemini file id is invalid")
	}
	if err := validateUsage(usage); err != nil {
		return "", err
	}
	videoTokens, textTokens, err := outputTokensByModality(usage.OutputTokensByModality)
	if err != nil {
		return "", err
	}
	aspect := strings.ReplaceAll(spec.AspectRatio, ":", "x")
	taskID := fmt.Sprintf(
		"omni-file:%s:%d:%s:%s:%d:%d:%d:%d:%d:%d:%d:v2",
		fileID,
		spec.DurationSeconds,
		spec.Resolution,
		aspect,
		usage.TotalInputTokens,
		usage.TotalOutputTokens,
		videoTokens,
		textTokens,
		usage.TotalThoughtTokens,
		usage.TotalCachedTokens,
		usage.TotalTokens,
	)
	if len(taskID) > 191 {
		return "", errors.New("Gemini file task id is too long")
	}
	return taskID, nil
}

func buildPersistedTaskIDWithoutUsage(fileID string, spec requestSpec) (string, error) {
	if !fileIDPattern.MatchString(fileID) {
		return "", errors.New("Gemini file id is invalid")
	}
	aspect := strings.ReplaceAll(spec.AspectRatio, ":", "x")
	taskID := fmt.Sprintf(
		"omni-file:%s:%d:%s:%s:usage-unavailable:v3",
		fileID,
		spec.DurationSeconds,
		spec.Resolution,
		aspect,
	)
	if len(taskID) > 191 {
		return "", errors.New("Gemini file task id is too long")
	}
	return taskID, nil
}

func parsePersistedTaskID(taskID string) (persistedTaskIdentity, error) {
	parts := strings.Split(taskID, ":")
	if (len(parts) != 7 && len(parts) != 10 && len(parts) != 13) || parts[0] != "omni-file" {
		return persistedTaskIdentity{}, errors.New("Gemini Omni task id is invalid")
	}
	identity := persistedTaskIdentity{FileID: parts[1], Resolution: parts[3]}
	if !fileIDPattern.MatchString(identity.FileID) {
		return persistedTaskIdentity{}, errors.New("Gemini Omni file id is invalid")
	}
	var err error
	identity.DurationSeconds, err = parseBoundedInt(parts[2], 3, 10)
	if err != nil {
		return persistedTaskIdentity{}, errors.New("Gemini Omni duration identity is invalid")
	}
	switch identity.Resolution {
	case "360p", "720p", "1080p", "4k":
	default:
		return persistedTaskIdentity{}, errors.New("Gemini Omni resolution identity is invalid")
	}
	switch parts[4] {
	case "16x9":
		identity.AspectRatio = "16:9"
	case "9x16":
		identity.AspectRatio = "9:16"
	default:
		return persistedTaskIdentity{}, errors.New("Gemini Omni aspect ratio identity is invalid")
	}
	switch {
	case len(parts) == 7 && parts[5] == "usage-unavailable" && parts[6] == "v3":
		identity.SchemaVersion = 3
		identity.UsageComplete = false
	case len(parts) == 10 && parts[9] == "v1":
		identity.SchemaVersion = 1
		identity.UsageComplete = true
		identity.TotalInputTokens, err = parseBoundedInt(parts[5], 1, maxOmniTokenCount)
		if err == nil {
			identity.TotalOutputTokens, err = parseBoundedInt(parts[6], 1, maxOmniTokenCount)
		}
		if err != nil {
			return persistedTaskIdentity{}, errors.New("Gemini Omni usage identity is invalid")
		}
		identity.TotalCachedTokens, err = parseBoundedInt(parts[7], 0, maxOmniTokenCount)
		if err == nil {
			identity.TotalTokens, err = parseBoundedInt(parts[8], 1, maxOmniTokenCount)
		}
		if err != nil {
			return persistedTaskIdentity{}, errors.New("Gemini Omni legacy usage identity is invalid")
		}
		if identity.TotalCachedTokens > identity.TotalInputTokens ||
			identity.TotalTokens < identity.TotalInputTokens+identity.TotalOutputTokens {
			return persistedTaskIdentity{}, errors.New("Gemini Omni legacy usage identity is inconsistent")
		}
	case len(parts) == 13 && parts[12] == "v2":
		identity.SchemaVersion = 2
		identity.UsageComplete = true
		identity.TotalInputTokens, err = parseBoundedInt(parts[5], 1, maxOmniTokenCount)
		if err == nil {
			identity.TotalOutputTokens, err = parseBoundedInt(parts[6], 1, maxOmniTokenCount)
		}
		if err != nil {
			return persistedTaskIdentity{}, errors.New("Gemini Omni usage identity is invalid")
		}
		identity.OutputVideoTokens, err = parseBoundedInt(parts[7], 1, maxOmniTokenCount)
		if err == nil {
			identity.OutputTextTokens, err = parseBoundedInt(parts[8], 0, maxOmniTokenCount)
		}
		if err == nil {
			identity.ThoughtTokens, err = parseBoundedInt(parts[9], 0, maxOmniTokenCount)
		}
		if err == nil {
			identity.TotalCachedTokens, err = parseBoundedInt(parts[10], 0, maxOmniTokenCount)
		}
		if err == nil {
			identity.TotalTokens, err = parseBoundedInt(parts[11], 1, maxOmniTokenCount)
		}
		if err != nil {
			return persistedTaskIdentity{}, errors.New("Gemini Omni classified usage identity is invalid")
		}
		if identity.TotalCachedTokens > identity.TotalInputTokens ||
			identity.TotalOutputTokens != identity.OutputVideoTokens+identity.OutputTextTokens ||
			identity.TotalTokens != identity.TotalInputTokens+identity.TotalOutputTokens+identity.ThoughtTokens {
			return persistedTaskIdentity{}, errors.New("Gemini Omni classified usage identity is inconsistent")
		}
	default:
		return persistedTaskIdentity{}, errors.New("Gemini Omni task identity revision is invalid")
	}
	return identity, nil
}

func parseBoundedInt(value string, minimum int, maximum int) (int, error) {
	parsed, err := strconv.ParseInt(value, 10, 32)
	if err != nil || parsed < int64(minimum) || parsed > int64(maximum) {
		return 0, errors.New("integer is outside the supported range")
	}
	return int(parsed), nil
}

func extractFileIDFromOutputURI(raw string) (string, error) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" {
		return "", errors.New("Gemini output URI must be absolute HTTPS")
	}
	query := parsed.Query()
	for key := range query {
		if key != "alt" {
			return "", errors.New("Gemini output URI contains an unexpected query parameter")
		}
	}
	if alt := query.Get("alt"); alt != "" && alt != "media" {
		return "", errors.New("Gemini output URI has an invalid delivery mode")
	}
	const prefix = "/v1beta/files/"
	if !strings.HasPrefix(parsed.EscapedPath(), prefix) {
		return "", errors.New("Gemini output URI is not a Files resource")
	}
	filePart := strings.TrimPrefix(parsed.EscapedPath(), prefix)
	filePart = strings.TrimSuffix(filePart, ":download")
	fileID, err := url.PathUnescape(filePart)
	if err != nil || !fileIDPattern.MatchString(fileID) {
		return "", errors.New("Gemini output URI file id is invalid")
	}
	return fileID, nil
}

func buildFilesURL(baseURL string, fileID string, download bool) (string, error) {
	if !fileIDPattern.MatchString(fileID) {
		return "", errors.New("Gemini file id is invalid")
	}
	base, err := url.Parse(strings.TrimRight(strings.TrimSpace(baseURL), "/"))
	if err != nil || base.Scheme == "" || base.Host == "" || base.User != nil || base.RawQuery != "" || base.Fragment != "" {
		return "", errors.New("Gemini base URL is invalid")
	}
	base.Path = strings.TrimSuffix(base.Path, "/v1beta") + "/v1beta/files/" + fileID
	if download {
		base.Path += ":download"
		base.RawQuery = "alt=media"
	}
	return base.String(), nil
}

func buildFileStatusEnvelope(raw []byte, baseURL string, taskID string, providerModel string) ([]byte, error) {
	identity, err := parsePersistedTaskID(taskID)
	if err != nil {
		return nil, err
	}
	if providerModel != ModelGeminiOmni11Flash {
		return nil, errors.New("Gemini Omni provider model is invalid")
	}
	var file googleFile
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(string(raw)), &file); err != nil {
		return nil, fmt.Errorf("decode Gemini file response failed: %w", err)
	}
	if file.Name != "files/"+identity.FileID {
		return nil, errors.New("Gemini file response identity is inconsistent")
	}
	envelope := fileStatusEnvelope{
		SchemaVersion:     identity.SchemaVersion,
		Object:            fileEnvelopeObject,
		TaskID:            taskID,
		ProviderModel:     providerModel,
		FileID:            identity.FileID,
		State:             file.State,
		DurationSeconds:   identity.DurationSeconds,
		Resolution:        identity.Resolution,
		AspectRatio:       identity.AspectRatio,
		UsageComplete:     identity.UsageComplete,
		TotalCachedTokens: identity.TotalCachedTokens,
		TotalInputTokens:  identity.TotalInputTokens,
		TotalOutputTokens: identity.TotalOutputTokens,
		OutputVideoTokens: identity.OutputVideoTokens,
		OutputTextTokens:  identity.OutputTextTokens,
		ThoughtTokens:     identity.ThoughtTokens,
		TotalTokens:       identity.TotalTokens,
	}
	switch file.State {
	case "PROCESSING":
		if file.Source != "" && file.Source != "GENERATED" {
			return nil, errors.New("Gemini processing file source is invalid")
		}
	case "ACTIVE":
		if file.Source != "GENERATED" || file.MIMEType != "video/mp4" {
			return nil, errors.New("Gemini active file contract is invalid")
		}
		sizeBytes, err := strconv.ParseInt(file.SizeBytes, 10, 64)
		if err != nil || sizeBytes <= 0 {
			return nil, errors.New("Gemini active file size is invalid")
		}
		hash, err := base64.StdEncoding.Strict().DecodeString(file.SHA256Hash)
		if err != nil || len(hash) != 32 {
			return nil, errors.New("Gemini active file digest is invalid")
		}
		duration, err := time.ParseDuration(file.VideoMetadata.VideoDuration)
		if err != nil || duration != time.Duration(identity.DurationSeconds)*time.Second {
			return nil, errors.New("Gemini active file duration is inconsistent")
		}
		artifactURL, err := buildFilesURL(baseURL, identity.FileID, true)
		if err != nil {
			return nil, err
		}
		parsedArtifact, err := url.Parse(artifactURL)
		if err != nil || parsedArtifact.Scheme != "https" {
			return nil, errors.New("Gemini artifact URL is not HTTPS")
		}
		envelope.MIMEType = file.MIMEType
		envelope.SizeBytes = sizeBytes
		envelope.SHA256Hash = file.SHA256Hash
		envelope.VideoDuration = file.VideoMetadata.VideoDuration
		envelope.ArtifactURL = artifactURL
	case "FAILED":
		envelope.ProviderErrorCode = file.Error.Code
	default:
		return nil, errors.New("Gemini file state is unknown")
	}
	return common.Marshal(envelope)
}

func replaceResponseBody(response *http.Response, body []byte) *http.Response {
	response.Body = io.NopCloser(bytes.NewReader(body))
	response.ContentLength = int64(len(body))
	response.Header = response.Header.Clone()
	response.Header.Set("Content-Type", "application/json")
	response.Header.Set("Content-Length", strconv.Itoa(len(body)))
	return response
}
