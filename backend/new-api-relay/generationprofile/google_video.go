package generationprofile

import (
	"fmt"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
)

const (
	// GoogleGeminiInteractionsVideoProtocolV1 is the Gemini Developer API
	// v1beta Interactions protocol used by Gemini Omni. It is deliberately
	// distinct from Veo predictLongRunning: an Omni release cannot be routed
	// through the older Veo converter merely because both return MP4 video.
	GoogleGeminiInteractionsVideoProtocolV1 = "google-gemini-interactions-omni-video-v1"
	GoogleGeminiVeoVideoProtocolV1          = "google-gemini-veo-predict-long-running-v1"
	GoogleVertexVeoVideoProtocolV1          = "google-vertex-veo-predict-long-running-v1"

	// GoogleGeminiOmni11FlashBasicVideoV1 intentionally publishes only
	// stateless text/image generation. The frozen public schema cannot bind a
	// previous_interaction_id or distinguish edit from tail-only extension, so
	// those provider features remain outside this executable profile.
	GoogleGeminiOmni11FlashBasicVideoV1 = "google.gemini.omni-1.1-flash.basic-video.v1"
	GoogleGeminiVeo31VideoV1            = "google.gemini.veo-3.1.video.v1"
	GoogleVertexVeo31VideoV1            = "google.vertex.veo-3.1.video.v1"
)

var googleVideoAspectRatios = []string{"16:9", "9:16"}

// BuiltinGoogleVideoProfiles returns implementation ceilings, not route or
// account readiness. A concrete, exact model identity is still required by
// the reviewed catalog and a signed generation release.
func BuiltinGoogleVideoProfiles() []Profile {
	profiles := []Profile{
		buildGoogleGeminiOmni11BasicProfile(),
		buildGoogleVeo31Profile(
			GoogleGeminiVeo31VideoV1,
			GoogleGeminiVeoVideoProtocolV1,
			constant.ChannelTypeGemini,
		),
		buildGoogleVeo31Profile(
			GoogleVertexVeo31VideoV1,
			GoogleVertexVeoVideoProtocolV1,
			constant.ChannelTypeVertexAi,
		),
	}
	for index := range profiles {
		profiles[index].Revision = profileRevision(profiles[index])
	}
	return profiles
}

func buildGoogleGeminiOmni11BasicProfile() Profile {
	durations := integerRange(3, 10)
	return Profile{
		ID:                GoogleGeminiOmni11FlashBasicVideoV1,
		Protocol:          GoogleGeminiInteractionsVideoProtocolV1,
		NativeChannelType: constant.ChannelTypeGemini,
		Capability: dto.PlatformGenerationCapabilities{
			SchemaVersion: 1,
			Modes: map[string]dto.PlatformModeCapability{
				"text_to_video":  googleVideoMode(nil, 0, 0, durations, []string{"360p", "720p", "1080p", "4k"}, 2_000),
				"image_to_video": googleVideoMode([]string{"image"}, 2, 0, durations, []string{"360p", "720p", "1080p", "4k"}, 2_000),
			},
		},
		DurationSemantics: map[string]DurationSemantics{
			"text_to_video":  DurationSemanticsSeconds,
			"image_to_video": DurationSemanticsSeconds,
		},
		Artifacts: videoArtifactContracts("text_to_video", "image_to_video"),
	}
}

func buildGoogleVeo31Profile(id, protocol string, channelType int) Profile {
	// The provider supports 4/6/8 seconds, but 1080p and 4K require eight
	// seconds. Capability v1 cannot express that dependency. Publishing the
	// eight-second rectangular subset keeps every advertised combination valid
	// while preserving all three resolutions supported by the converter.
	durations := []int{8}
	resolutions := []string{"720p", "1080p", "4k"}
	return Profile{
		ID:                id,
		Protocol:          protocol,
		NativeChannelType: channelType,
		Capability: dto.PlatformGenerationCapabilities{
			SchemaVersion: 1,
			Modes: map[string]dto.PlatformModeCapability{
				"text_to_video":  googleVideoMode(nil, 0, 0, durations, resolutions, 512),
				"image_to_video": googleVideoMode([]string{"image"}, 1, 0, durations, resolutions, 512),
			},
		},
		DurationSemantics: map[string]DurationSemantics{
			"text_to_video":  DurationSemanticsSeconds,
			"image_to_video": DurationSemanticsSeconds,
		},
		Artifacts: videoArtifactContracts("text_to_video", "image_to_video"),
	}
}

func googleVideoMode(mediaTypes []string, maxImages, maxVideos int, durations []int, resolutions []string, maxPromptLength int) dto.PlatformModeCapability {
	return dto.PlatformModeCapability{
		InputMediaTypes:      append([]string(nil), mediaTypes...),
		SupportsFace:         false,
		RequiredResourceKeys: []string{},
		Limits: dto.PlatformCapabilityLimits{
			MaxPromptLength: maxPromptLength,
			MaxImages:       maxImages,
			MaxVideos:       maxVideos,
			MaxAudio:        0,
			DurationSeconds: append([]int(nil), durations...),
			AspectRatios:    append([]string(nil), googleVideoAspectRatios...),
			Resolutions:     append([]string(nil), resolutions...),
			OutputCounts:    []int{1},
		},
	}
}

func isGoogleVideoProtocol(protocol string) bool {
	switch protocol {
	case GoogleGeminiInteractionsVideoProtocolV1, GoogleGeminiVeoVideoProtocolV1, GoogleVertexVeoVideoProtocolV1:
		return true
	default:
		return false
	}
}

// Persisted snapshots may narrow an implementation ceiling but cannot turn a
// Veo converter into the Interactions API, add video input, or expose an
// unsupported duration/resolution cross-product.
func validateGoogleVideoProfileContract(profile Profile) error {
	var ceiling Profile
	switch profile.ID {
	case GoogleGeminiOmni11FlashBasicVideoV1:
		ceiling = buildGoogleGeminiOmni11BasicProfile()
	case GoogleGeminiVeo31VideoV1:
		ceiling = buildGoogleVeo31Profile(profile.ID, GoogleGeminiVeoVideoProtocolV1, constant.ChannelTypeGemini)
	case GoogleVertexVeo31VideoV1:
		ceiling = buildGoogleVeo31Profile(profile.ID, GoogleVertexVeoVideoProtocolV1, constant.ChannelTypeVertexAi)
	default:
		return fmt.Errorf("Google video profile has no reviewed implementation contract")
	}
	if profile.Protocol != ceiling.Protocol || profile.NativeChannelType != ceiling.NativeChannelType {
		return fmt.Errorf("Google video profile protocol or native channel expands its implementation contract")
	}
	if err := ceiling.ValidateNarrowing(profile.Capability); err != nil {
		return fmt.Errorf("Google video profile expands its implementation contract: %w", err)
	}
	return nil
}
