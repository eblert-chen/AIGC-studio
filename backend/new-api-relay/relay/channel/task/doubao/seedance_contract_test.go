package doubao

import (
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func seedanceContractRelayInfo(providerModelID string) *relaycommon.RelayInfo {
	return &relaycommon.RelayInfo{
		OriginModelName: "video.seedance.acceptance",
		ChannelMeta: &relaycommon.ChannelMeta{
			ChannelType:       constant.ChannelTypeVolcEngine,
			UpstreamModelName: providerModelID,
		},
		TaskRelayInfo: &relaycommon.TaskRelayInfo{PinnedProviderRoute: true},
	}
}

func validSeedanceContractRequest(mode string) relaycommon.TaskSubmitReq {
	return relaycommon.TaskSubmitReq{
		Prompt:   "a continuous studio camera move",
		Duration: 5,
		Metadata: map[string]any{
			"platform_generation_mode": mode,
			"resolution":               "720p",
			"aspectRatio":              "16:9",
			"sampleCount":              1,
			"face_enabled":             false,
		},
	}
}

func TestSeedanceManifestPublishesOnlyCurrentAcceptanceCandidates(t *testing.T) {
	manifests, err := generationprofile.SeedanceModelCatalog()
	require.NoError(t, err)
	assert.Equal(t, []string{
		"doubao-seedance-1-0-pro-250528",
		"doubao-seedance-1-0-pro-fast-251015",
		"doubao-seedance-2-0-260128",
		"doubao-seedance-2-0-fast-260128",
		"doubao-seedance-2-0-mini-260615",
		"doubao-seedance-2-5-260628",
	}, seedanceProviderModelIDs())

	for _, manifest := range manifests {
		assert.NotEmpty(t, manifest.OfficialSources, manifest.ProviderModelID)
		if manifest.NewRoutesAllowed {
			assert.Equal(t, "acceptance_candidate", manifest.Lifecycle)
			assert.Equal(t, "route_acceptance_required", manifest.EvidenceStatus)
		}
	}
}

func TestSeedanceRetiredProviderModelCannotCreateANewRoute(t *testing.T) {
	request := validSeedanceContractRequest("text_to_video")
	_, err := convertPinnedSeedanceRequest(&request, seedanceContractRelayInfo("doubao-seedance-1-0-lite-t2v-250428"))
	require.ErrorContains(t, err, "not eligible for a new route")
}

func TestSeedanceUnverifiedProviderModelCannotCreateANewRoute(t *testing.T) {
	request := validSeedanceContractRequest("text_to_video")
	_, err := convertPinnedSeedanceRequest(&request, seedanceContractRelayInfo("doubao-seedance-future-unreviewed"))
	require.ErrorContains(t, err, "has no reviewed adapter manifest")
}

func TestSeedance15MapsTwoImagesToFirstAndLastFrames(t *testing.T) {
	request := validSeedanceContractRequest("image_to_video")
	request.Images = []string{"https://assets.example/first.png", "https://assets.example/last.png"}
	payload, err := convertPinnedSeedanceRequestAt(&request, seedanceContractRelayInfo("doubao-seedance-1-5-pro-251215"), time.Date(2026, 8, 31, 0, 0, 0, 0, time.UTC))
	require.NoError(t, err)
	require.Len(t, payload.Content, 3)
	require.Equal(t, "first_frame", payload.Content[1].Role)
	require.Equal(t, "last_frame", payload.Content[2].Role)
}

func TestSeedanceAllCurrentModelModesPreserveTheirReviewedInputRoles(t *testing.T) {
	models, err := generationprofile.SeedanceModelCatalog()
	require.NoError(t, err)
	for _, model := range models {
		for modeName, mode := range model.Capability.Modes {
			t.Run(model.PublicModelID+"/"+modeName, func(t *testing.T) {
				request := validSeedanceContractRequest(modeName)
				profile, ok := generationprofile.Get(model.AdapterProfileID)
				require.True(t, ok)
				request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, profile)
				if modeName == "image_to_video" {
					request.Images = []string{"https://assets.example/first.png"}
				}
				if modeName == "video_to_video" {
					request.Videos = []string{"https://assets.example/reference.mp4"}
				}
				if mode.Limits.MaxAudio > 0 {
					request.Audios = []string{"https://assets.example/reference.mp3"}
				}
				payload, err := convertPinnedSeedanceRequestAt(&request, seedanceContractRelayInfo(model.ProviderModelID), time.Date(2026, 8, 31, 0, 0, 0, 0, time.UTC))
				require.NoError(t, err)
				assert.Equal(t, model.ProviderModelID, payload.Model)
				assert.Equal(t, request.Prompt, payload.Content[0].Text)
				if len(request.Images) > 0 {
					expectedRole := model.ImageRole
					if expectedRole == "first_last_frame" {
						expectedRole = "first_frame"
					}
					assert.Equal(t, expectedRole, payload.Content[1].Role)
				}
				if len(request.Videos) > 0 {
					assert.Equal(t, "reference_video", payload.Content[1].Role)
				}
				if len(request.Audios) > 0 {
					assert.Equal(t, "reference_audio", payload.Content[len(payload.Content)-1].Role)
				}
				if model.OmniReferenceTaskType != "" && modeName != "text_to_video" {
					require.NotNil(t, payload.OmniReferenceTaskType)
					assert.Equal(t, "reference", *payload.OmniReferenceTaskType)
				} else {
					assert.Nil(t, payload.OmniReferenceTaskType, "pure text generation does not declare a multimodal reference task")
				}
			})
		}
	}
}

func TestSeedance25ReferenceGenerationPinsTaskTypeAndMP4(t *testing.T) {
	request := validSeedanceContractRequest("video_to_video")
	request.Duration = 30
	request.Videos = []string{"https://assets.example/reference.mp4"}
	request.Images = []string{"https://assets.example/reference.png"}
	request.Metadata["omni_reference_task_type"] = "edit"
	request.Metadata["output_format"] = "mov"
	request.Metadata["model"] = "unreviewed-model"
	payload, err := convertPinnedSeedanceRequest(&request, seedanceContractRelayInfo("doubao-seedance-2-5-260628"))
	require.NoError(t, err)
	raw, err := common.Marshal(payload)
	require.NoError(t, err)
	var wire map[string]any
	require.NoError(t, common.Unmarshal(raw, &wire))
	assert.Equal(t, "reference", wire["omni_reference_task_type"])
	assert.Equal(t, "mp4", wire["output_format"])
	assert.Equal(t, "doubao-seedance-2-5-260628", wire["model"])
	assert.Equal(t, float64(30), wire["duration"])
	assert.Equal(t, "reference_image", payload.Content[1].Role)
	assert.Equal(t, "reference_video", payload.Content[2].Role)
}

func TestSeedanceRejectsOptionsOutsideEachModelCompatibleSubset(t *testing.T) {
	for _, test := range []struct {
		name, id, mode string
		change         func(*relaycommon.TaskSubmitReq)
		message        string
	}{
		{"mini 1080p", "doubao-seedance-2-0-mini-260615", "text_to_video", func(r *relaycommon.TaskSubmitReq) { r.Metadata["resolution"] = "1080p" }, "output options"},
		{"fast 4k", "doubao-seedance-2-0-fast-260128", "text_to_video", func(r *relaycommon.TaskSubmitReq) { r.Metadata["resolution"] = "4k" }, "output options"},
		{"2.5 4k", "doubao-seedance-2-5-260628", "text_to_video", func(r *relaycommon.TaskSubmitReq) { r.Metadata["resolution"] = "4k" }, "output options"},
		{"2.5 31 seconds", "doubao-seedance-2-5-260628", "text_to_video", func(r *relaycommon.TaskSubmitReq) { r.Duration = 31 }, "duration"},
		{"adaptive ratio", "doubao-seedance-2-5-260628", "text_to_video", func(r *relaycommon.TaskSubmitReq) { r.Metadata["aspectRatio"] = "adaptive" }, "output options"},
		{"audio only", "doubao-seedance-2-5-260628", "text_to_video", func(r *relaycommon.TaskSubmitReq) { r.Audios = []string{"https://assets.example/a.mp3"} }, "does not accept audio"},
		{"ten images", "doubao-seedance-2-5-260628", "image_to_video", func(r *relaycommon.TaskSubmitReq) {
			for i := 0; i < 10; i++ {
				r.Images = append(r.Images, "https://assets.example/i.png")
			}
		}, "at most 9 image"},
		{"1.5 V2V", "doubao-seedance-1-5-pro-251215", "video_to_video", func(r *relaycommon.TaskSubmitReq) { r.Videos = []string{"https://assets.example/v.mp4"} }, "does not support generation mode"},
		{"1.5 duration", "doubao-seedance-1-5-pro-251215", "text_to_video", func(r *relaycommon.TaskSubmitReq) { r.Duration = 13 }, "duration"},
		{"legacy fast last frame", "doubao-seedance-1-0-pro-fast-251015", "image_to_video", func(r *relaycommon.TaskSubmitReq) {
			r.Images = []string{"https://assets.example/first.png", "https://assets.example/last.png"}
		}, "at most 1 image"},
		{"unverified old fast", "doubao-seedance-1-0-pro-fast-250610", "text_to_video", func(r *relaycommon.TaskSubmitReq) {}, "not eligible"},
	} {
		t.Run(test.name, func(t *testing.T) {
			request := validSeedanceContractRequest(test.mode)
			test.change(&request)
			_, err := convertPinnedSeedanceRequestAt(&request, seedanceContractRelayInfo(test.id), time.Date(2026, 8, 31, 0, 0, 0, 0, time.UTC))
			assert.ErrorContains(t, err, test.message)
		})
	}
}

func TestSeedance20FourKRequiresNewImmutableProfile(t *testing.T) {
	request := validSeedanceContractRequest("text_to_video")
	request.Metadata["resolution"] = "4k"
	oldProfile, ok := generationprofile.Get(generationprofile.VolcengineArkVideoGenerationV1)
	require.True(t, ok)
	request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, oldProfile)
	_, err := convertPinnedSeedanceRequest(&request, seedanceContractRelayInfo("doubao-seedance-2-0-260128"))
	assert.ErrorContains(t, err, "exceeds pinned profile")
	newProfile, ok := generationprofile.Get(generationprofile.VolcengineArkVideoGeneration4KV1)
	require.True(t, ok)
	request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, newProfile)
	payload, err := convertPinnedSeedanceRequest(&request, seedanceContractRelayInfo("doubao-seedance-2-0-260128"))
	require.NoError(t, err)
	assert.Equal(t, "4k", payload.Resolution)
	assert.Nil(t, payload.OmniReferenceTaskType, "older API variants must not receive the 2.5-only task-type field")
}

func TestSeedance15CannotSubmitAtOrAfterEOS(t *testing.T) {
	request := validSeedanceContractRequest("text_to_video")
	_, err := convertPinnedSeedanceRequestAt(&request, seedanceContractRelayInfo("doubao-seedance-1-5-pro-251215"), time.Date(2026, 9, 21, 6, 0, 0, 0, time.UTC))
	assert.ErrorContains(t, err, "end of service")
}

func TestSeedanceRequestRejectsNonHTTPSProviderFetches(t *testing.T) {
	request := validSeedanceContractRequest("image_to_video")
	request.Images = []string{"http://assets.example/first.png"}
	_, err := convertPinnedSeedanceRequest(&request, seedanceContractRelayInfo("doubao-seedance-2-0-260128"))
	require.ErrorContains(t, err, "absolute HTTPS URL")
}

func TestSeedancePinnedRequestRejectsMismatchedProfileSnapshot(t *testing.T) {
	request := validSeedanceContractRequest("text_to_video")
	legacy, ok := generationprofile.Get(generationprofile.VolcengineArkLegacyVideoGenerationV1)
	require.True(t, ok)
	request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, legacy)
	_, err := convertPinnedSeedanceRequest(&request, seedanceContractRelayInfo("doubao-seedance-2-0-260128"))
	require.ErrorContains(t, err, "does not match the pinned provider route")
}
