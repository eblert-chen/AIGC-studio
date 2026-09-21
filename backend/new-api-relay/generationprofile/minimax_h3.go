package generationprofile

import (
	"fmt"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
)

const (
	MiniMaxH3VideoProtocolV2 = "minimax-video-generation-v2"

	// These are new immutable profiles, not revisions of the legacy Hailuo
	// protocol. The fixed-ratio public contract cannot express H3 first/last
	// frames: the provider ignores their ratio and uses the input image ratio.
	MiniMaxH3ReferenceVideoGenerationV1 = "minimax.video-generation.h3-reference-v1"
	MiniMaxH3MaxTextToVideoGenerationV1 = "minimax.video-generation.h3-max-t2v-v1"
)

var miniMaxH3NumericAspectRatios = []string{"16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}

// BuiltinMiniMaxH3Profiles exposes only the subset implemented by the V2
// converter and the existing single-MP4 artifact contract. A compiled profile
// is not evidence that an account, route, price or paid generation is ready.
func BuiltinMiniMaxH3Profiles() []Profile {
	profiles := []Profile{buildMiniMaxH3ReferenceProfile(), buildMiniMaxH3MaxProfile()}
	for index := range profiles {
		profiles[index].Revision = profileRevision(profiles[index])
	}
	return profiles
}

func buildMiniMaxH3ReferenceProfile() Profile {
	durations := integerRange(4, 15)
	resolutions := []string{"768p", "2k"}
	return Profile{
		ID:                MiniMaxH3ReferenceVideoGenerationV1,
		Protocol:          MiniMaxH3VideoProtocolV2,
		NativeChannelType: constant.ChannelTypeMiniMax,
		Capability: dto.PlatformGenerationCapabilities{
			SchemaVersion: 1,
			Modes: map[string]dto.PlatformModeCapability{
				"text_to_video":  miniMaxH3Mode(nil, 0, 0, 0, durations, resolutions),
				"image_to_video": miniMaxH3Mode([]string{"image", "audio"}, 9, 0, 3, durations, resolutions),
				// H3 permits at most 12 mixed files, not 9+3+3 together. The
				// public schema has no cross-media total, so 6+3+3 is the safe
				// rectangular subset; unsupported combinations are not trimmed.
				"video_to_video": miniMaxH3Mode([]string{"image", "video", "audio"}, 6, 3, 3, durations, resolutions),
			},
		},
		DurationSemantics: map[string]DurationSemantics{
			"text_to_video":  DurationSemanticsSeconds,
			"image_to_video": DurationSemanticsSeconds,
			"video_to_video": DurationSemanticsSeconds,
		},
		Artifacts: videoArtifactContracts("text_to_video", "image_to_video", "video_to_video"),
	}
}

func buildMiniMaxH3MaxProfile() Profile {
	return Profile{
		ID:                MiniMaxH3MaxTextToVideoGenerationV1,
		Protocol:          MiniMaxH3VideoProtocolV2,
		NativeChannelType: constant.ChannelTypeMiniMax,
		Capability: dto.PlatformGenerationCapabilities{
			SchemaVersion: 1,
			Modes: map[string]dto.PlatformModeCapability{
				"text_to_video": miniMaxH3Mode(nil, 0, 0, 0, integerRange(5, 15), []string{"480p", "768p"}),
			},
		},
		DurationSemantics: map[string]DurationSemantics{"text_to_video": DurationSemanticsSeconds},
		Artifacts:         videoArtifactContracts("text_to_video"),
	}
}

func miniMaxH3Mode(mediaTypes []string, maxImages, maxVideos, maxAudio int, durations []int, resolutions []string) dto.PlatformModeCapability {
	return dto.PlatformModeCapability{
		InputMediaTypes:      append([]string(nil), mediaTypes...),
		SupportsFace:         false,
		RequiredResourceKeys: []string{},
		Limits: dto.PlatformCapabilityLimits{
			MaxPromptLength: 7_000,
			MaxImages:       maxImages,
			MaxVideos:       maxVideos,
			MaxAudio:        maxAudio,
			DurationSeconds: append([]int(nil), durations...),
			AspectRatios:    append([]string(nil), miniMaxH3NumericAspectRatios...),
			Resolutions:     append([]string(nil), resolutions...),
			OutputCounts:    []int{1},
		},
	}
}

// Persisted snapshots must not turn an H3 Max task into reference generation,
// introduce adaptive ratio or expand the 12-file implementation ceiling.
func validateMiniMaxH3ProfileContract(profile Profile) error {
	var ceiling Profile
	switch profile.ID {
	case MiniMaxH3ReferenceVideoGenerationV1:
		ceiling = buildMiniMaxH3ReferenceProfile()
	case MiniMaxH3MaxTextToVideoGenerationV1:
		ceiling = buildMiniMaxH3MaxProfile()
	default:
		return fmt.Errorf("MiniMax H3 profile has no reviewed implementation contract")
	}
	if err := ceiling.ValidateNarrowing(profile.Capability); err != nil {
		return fmt.Errorf("MiniMax H3 profile expands its implementation contract: %w", err)
	}
	return nil
}
