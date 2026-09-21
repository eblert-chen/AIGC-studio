package doubao

import (
	"fmt"
	"net/url"
	"sort"
	"strings"
	"time"

	platformdto "github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/samber/lo"
)

func seedanceProviderModelIDs() []string {
	manifests, err := generationprofile.SeedanceModelCatalog()
	if err != nil {
		panic(err)
	}
	result := make([]string, 0, len(manifests))
	for _, manifest := range manifests {
		if manifest.NewRoutesAllowed {
			result = append(result, manifest.ProviderModelID)
		}
	}
	sort.Strings(result)
	return result
}

func resolveSeedanceProviderManifest(req *relaycommon.TaskSubmitReq, info *relaycommon.RelayInfo, now time.Time) (generationprofile.SeedanceProviderModel, error) {
	if req == nil || info == nil || !isPinnedPlatformRoute(info) {
		return generationprofile.SeedanceProviderModel{}, fmt.Errorf("Seedance request requires a pinned Platform route")
	}
	providerModelID := strings.TrimSpace(info.UpstreamModelName)
	manifest, ok, err := generationprofile.ResolveSeedanceProviderModel(providerModelID)
	if err != nil {
		return generationprofile.SeedanceProviderModel{}, err
	}
	if !ok {
		return generationprofile.SeedanceProviderModel{}, fmt.Errorf("Seedance provider model %q has no reviewed adapter manifest", providerModelID)
	}
	if err := manifest.ValidateSubmissionAt(now); err != nil {
		return generationprofile.SeedanceProviderModel{}, err
	}
	profile, found, err := generationprofile.ResolveSnapshot(req.Metadata)
	if err != nil {
		return generationprofile.SeedanceProviderModel{}, err
	}
	if found && (!manifest.AcceptsProfile(profile.ID) || profile.NativeChannelType != info.ChannelType) {
		return generationprofile.SeedanceProviderModel{}, fmt.Errorf("Seedance adapter profile snapshot does not match the pinned provider route")
	}
	return manifest, nil
}

func convertPinnedSeedanceRequest(req *relaycommon.TaskSubmitReq, info *relaycommon.RelayInfo) (*requestPayload, error) {
	return convertPinnedSeedanceRequestAt(req, info, time.Now())
}

func convertPinnedSeedanceRequestAt(req *relaycommon.TaskSubmitReq, info *relaycommon.RelayInfo, now time.Time) (*requestPayload, error) {
	manifest, err := resolveSeedanceProviderManifest(req, info, now)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(req.Prompt) == "" || len([]rune(req.Prompt)) > 2_000 {
		return nil, fmt.Errorf("Seedance prompt is required and must not exceed 2000 characters")
	}
	if arkPromptControlPattern.MatchString(req.Prompt) {
		return nil, fmt.Errorf("prompt must not override Relay-controlled Ark video options")
	}
	mode, _ := req.Metadata["platform_generation_mode"].(string)
	mode = strings.TrimSpace(mode)
	modeContract, ok := manifest.Capability.Modes[mode]
	if !ok {
		return nil, fmt.Errorf("Seedance provider model %q does not support generation mode %q", manifest.ProviderModelID, mode)
	}

	images, err := normalizedHTTPSMedia(req.Images, "image")
	if err != nil {
		return nil, err
	}
	videos := append([]string(nil), req.Videos...)
	if len(videos) == 0 && strings.TrimSpace(req.InputReference) != "" {
		videos = append(videos, req.InputReference)
	}
	videos, err = normalizedHTTPSMedia(videos, "video")
	if err != nil {
		return nil, err
	}
	audios, err := normalizedHTTPSMedia(req.Audios, "audio")
	if err != nil {
		return nil, err
	}
	minImages, minVideos := 0, 0
	if mode == "image_to_video" {
		minImages = 1
	}
	if mode == "video_to_video" {
		minVideos = 1
	}
	if err := validateSeedanceMediaCount("image", len(images), minImages, modeContract.Limits.MaxImages); err != nil {
		return nil, err
	}
	if err := validateSeedanceMediaCount("video", len(videos), minVideos, modeContract.Limits.MaxVideos); err != nil {
		return nil, err
	}
	if err := validateSeedanceMediaCount("audio", len(audios), 0, modeContract.Limits.MaxAudio); err != nil {
		return nil, err
	}

	duration := req.Duration
	if duration <= 0 {
		duration, _ = exactMetadataInt(req.Metadata["durationSeconds"])
	}
	if !containsExactInt(modeContract.Limits.DurationSeconds, duration) {
		return nil, fmt.Errorf("Seedance duration %d is not supported by provider model %q", duration, manifest.ProviderModelID)
	}
	resolution, _ := req.Metadata["resolution"].(string)
	resolution = strings.ToLower(strings.TrimSpace(resolution))
	ratio, _ := req.Metadata["aspectRatio"].(string)
	ratio = strings.TrimSpace(ratio)
	if !containsExactString(modeContract.Limits.Resolutions, resolution) || !containsExactString(modeContract.Limits.AspectRatios, ratio) {
		return nil, fmt.Errorf("Seedance output options exceed the reviewed provider manifest")
	}
	if sampleCount, ok := exactMetadataInt(req.Metadata["sampleCount"]); !ok || sampleCount != 1 {
		return nil, fmt.Errorf("Seedance video generation requires exactly one output")
	}
	if faceEnabled, ok := req.Metadata["face_enabled"].(bool); !ok || faceEnabled {
		return nil, fmt.Errorf("Seedance video generation requires face controls to be explicitly disabled")
	}
	profile, found, err := generationprofile.ResolveSnapshot(req.Metadata)
	if err != nil {
		return nil, err
	}
	if found {
		// A compatible older profile remains a ceiling, not permission to
		// inherit newly reviewed features such as 4K from the model catalog.
		assets := make([]platformdto.PlatformGenerationAssetInput, 0, len(images)+len(videos)+len(audios))
		for mediaType, urls := range map[string][]string{"image": images, "video": videos, "audio": audios} {
			for _, mediaURL := range urls {
				assets = append(assets, platformdto.PlatformGenerationAssetInput{URL: mediaURL, MediaType: mediaType})
			}
		}
		if err := profile.ValidateRequest(platformdto.PlatformGenerationRequest{
			Mode:   mode,
			Inputs: platformdto.PlatformGenerationInputs{Prompt: req.Prompt, Assets: assets},
			Output: platformdto.PlatformGenerationOutputOptions{DurationSeconds: duration, AspectRatio: ratio, Resolution: resolution, Count: 1},
		}); err != nil {
			return nil, fmt.Errorf("Seedance request exceeds pinned profile: %w", err)
		}
	}

	content := []ContentItem{{Type: "text", Text: req.Prompt}}
	for index, mediaURL := range images {
		role := manifest.ImageRole
		if manifest.ImageRole == "first_last_frame" {
			if index == 0 {
				role = "first_frame"
			} else {
				role = "last_frame"
			}
		}
		content = append(content, ContentItem{Type: "image_url", ImageURL: &MediaURL{URL: mediaURL}, Role: role})
	}
	for _, mediaURL := range videos {
		content = append(content, ContentItem{Type: "video_url", VideoURL: &MediaURL{URL: mediaURL}, Role: "reference_video"})
	}
	for _, mediaURL := range audios {
		content = append(content, ContentItem{Type: "audio_url", AudioURL: &MediaURL{URL: mediaURL}, Role: "reference_audio"})
	}
	payload := &requestPayload{
		Model:      manifest.ProviderModelID,
		Content:    content,
		Resolution: resolution,
		Ratio:      ratio,
		Duration:   lo.ToPtr(dto.IntValue(duration)),
	}
	if manifest.OmniReferenceTaskType != "" {
		// Explicit reference semantics prevent Ark 2.5 auto task inference
		// from turning a public V2V reference into editing or extension.
		if mode == "image_to_video" || mode == "video_to_video" {
			payload.OmniReferenceTaskType = lo.ToPtr(manifest.OmniReferenceTaskType)
		}
		payload.OutputFormat = lo.ToPtr("mp4")
	}
	return payload, nil
}

func validateSeedanceMediaCount(mediaType string, count, minimum, maximum int) error {
	if maximum == 0 && count > 0 {
		return fmt.Errorf("Seedance mode does not accept %s input", mediaType)
	}
	if count < minimum {
		return fmt.Errorf("Seedance mode requires at least %d %s input(s)", minimum, mediaType)
	}
	if count > maximum {
		return fmt.Errorf("Seedance mode accepts at most %d %s input(s)", maximum, mediaType)
	}
	return nil
}

func normalizedHTTPSMedia(values []string, mediaType string) ([]string, error) {
	result := make([]string, 0, len(values))
	for _, value := range values {
		normalized := strings.TrimSpace(value)
		parsed, err := url.Parse(normalized)
		if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" {
			return nil, fmt.Errorf("Seedance %s input must be an absolute HTTPS URL", mediaType)
		}
		result = append(result, normalized)
	}
	return result, nil
}

func containsExactInt(values []int, expected int) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}
