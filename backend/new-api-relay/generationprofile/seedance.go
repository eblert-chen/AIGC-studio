package generationprofile

import (
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
)

const (
	VolcengineArkVideoProtocolV1 = "volcengine-ark-contents-generations-tasks-v1"

	// VolcengineArkVideoGenerationV1 is the implementation ceiling for the
	// current Ark contents/generations/tasks protocol, including Seedance 2.x
	// multimodal video requests. Concrete provider/public model identities are
	// bound by generationrelease rather than this reusable adapter profile.
	VolcengineArkVideoGenerationV1 = "volcengine.ark.video-generation.v1"

	// VolcengineArkLegacyVideoGenerationV1 preserves the wider 2..12 second
	// duration range implemented by Seedance 1.0. A separate ceiling avoids
	// pretending that the newer 4..15 second contract also accepts 2/3 seconds.
	VolcengineArkLegacyVideoGenerationV1 = "volcengine.ark.video-generation.legacy-v1"

	// New ceilings are separate immutable profiles. In particular, neither
	// adding 4K nor extending duration may rewrite an accepted v1 snapshot.
	VolcengineArkVideoGeneration4KV1           = "volcengine.ark.video-generation.4k-v1"
	VolcengineArkVideoGeneration30SReferenceV1 = "volcengine.ark.video-generation.30s-reference-v1"
)

var seedanceNumericAspectRatios = []string{"16:9", "4:3", "1:1", "3:4", "9:16", "21:9"}

// BuiltinSeedanceProfiles returns defensive code-reviewed profiles for the
// registry in profile.go. It deliberately returns profiles rather than
// maintaining a second registry so Resolve/ResolveSnapshot remain the only
// runtime lookup path.
func BuiltinSeedanceProfiles() []Profile {
	profiles := []Profile{
		buildArkSeedanceMultimodalProfile(),
		buildArkSeedanceLegacyProfile(),
		buildArkSeedance4KProfile(),
		buildArkSeedance30SReferenceProfile(),
	}
	for index := range profiles {
		profiles[index].Revision = profileRevision(profiles[index])
	}
	return profiles
}

func buildArkSeedance4KProfile() Profile {
	profile := buildArkSeedanceMultimodalProfile()
	profile.ID = VolcengineArkVideoGeneration4KV1
	for modeName, mode := range profile.Capability.Modes {
		mode.Limits.Resolutions = []string{"480p", "720p", "1080p", "4k"}
		profile.Capability.Modes[modeName] = mode
	}
	return profile
}

func buildArkSeedance30SReferenceProfile() Profile {
	profile := buildArkSeedanceMultimodalProfile()
	profile.ID = VolcengineArkVideoGeneration30SReferenceV1
	for modeName, mode := range profile.Capability.Modes {
		mode.Limits.DurationSeconds = integerRange(4, 30)
		profile.Capability.Modes[modeName] = mode
	}
	return profile
}

func buildArkSeedanceMultimodalProfile() Profile {
	durations := integerRange(4, 15)
	return Profile{
		ID:                VolcengineArkVideoGenerationV1,
		Protocol:          VolcengineArkVideoProtocolV1,
		NativeChannelType: constant.ChannelTypeVolcEngine,
		Capability: dto.PlatformGenerationCapabilities{
			SchemaVersion: 1,
			Modes: map[string]dto.PlatformModeCapability{
				"text_to_video":  seedanceMode(nil, 0, 0, 0, durations),
				"image_to_video": seedanceMode([]string{"image", "audio"}, 9, 0, 3, durations),
				"video_to_video": seedanceMode([]string{"image", "video", "audio"}, 9, 3, 3, durations),
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

func buildArkSeedanceLegacyProfile() Profile {
	durations := integerRange(2, 12)
	return Profile{
		ID:                VolcengineArkLegacyVideoGenerationV1,
		Protocol:          VolcengineArkVideoProtocolV1,
		NativeChannelType: constant.ChannelTypeVolcEngine,
		Capability: dto.PlatformGenerationCapabilities{
			SchemaVersion: 1,
			Modes: map[string]dto.PlatformModeCapability{
				"text_to_video": seedanceMode(nil, 0, 0, 0, durations),
				// The reusable Ark v1 converter implements first+last-frame input.
				// Concrete provider releases (for example Pro Fast) narrow this
				// implementation ceiling to one image in their reviewed manifest.
				"image_to_video": seedanceMode([]string{"image"}, 2, 0, 0, durations),
			},
		},
		DurationSemantics: map[string]DurationSemantics{
			"text_to_video":  DurationSemanticsSeconds,
			"image_to_video": DurationSemanticsSeconds,
		},
		Artifacts: videoArtifactContracts("text_to_video", "image_to_video"),
	}
}

func seedanceMode(mediaTypes []string, maxImages, maxVideos, maxAudio int, durations []int) dto.PlatformModeCapability {
	return dto.PlatformModeCapability{
		InputMediaTypes:      append([]string(nil), mediaTypes...),
		SupportsFace:         false,
		RequiredResourceKeys: []string{},
		Limits: dto.PlatformCapabilityLimits{
			MaxPromptLength: 2_000,
			MaxImages:       maxImages,
			MaxVideos:       maxVideos,
			MaxAudio:        maxAudio,
			DurationSeconds: append([]int(nil), durations...),
			AspectRatios:    append([]string(nil), seedanceNumericAspectRatios...),
			Resolutions:     []string{"480p", "720p", "1080p"},
			OutputCounts:    []int{1},
		},
	}
}

func videoArtifactContracts(modes ...string) map[string]ArtifactContract {
	contracts := make(map[string]ArtifactContract, len(modes))
	for _, mode := range modes {
		contracts[mode] = ArtifactContract{
			MediaType:   "video",
			ContentType: "video/mp4",
			Count:       1,
		}
	}
	return contracts
}

func integerRange(start, end int) []int {
	values := make([]int, 0, end-start+1)
	for value := start; value <= end; value++ {
		values = append(values, value)
	}
	return values
}
