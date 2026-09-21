package controller

import (
	"bytes"
	"context"
	"encoding/base64"
	"image/png"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	relayservice "github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

func TestGoogleGeminiVideoProfilesHavePaidRouteAcceptancePlans(t *testing.T) {
	tests := []struct {
		name       string
		profileID  string
		model      string
		duration   int
		resolution string
	}{
		{
			name:       "omni interactions",
			profileID:  generationprofile.GoogleGeminiOmni11FlashBasicVideoV1,
			model:      "gemini-omni-1.1-flash",
			duration:   4,
			resolution: "720p",
		},
		{
			name:       "veo predict long running",
			profileID:  generationprofile.GoogleGeminiVeo31VideoV1,
			model:      "veo-3.1-generate-preview",
			duration:   8,
			resolution: "720p",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			profile, ok := generationprofile.Get(test.profileID)
			require.True(t, ok)
			plan, err := resolvePlatformGenerationRouteTestPlan(&relayservice.PlatformGenerationRouteTestBinding{
				NativeChannelType:         constant.ChannelTypeGemini,
				UpstreamModel:             test.model,
				CapabilityProfileID:       profile.ID,
				CapabilityProfileRevision: profile.Revision,
			})
			require.NoError(t, err)
			require.True(t, plan.Asynchronous)
			require.Equal(t, "text_to_video", plan.Mode)
			require.Equal(t, test.duration, plan.Duration)
			require.Equal(t, test.resolution, plan.Resolution)
			require.Equal(t, "16:9", plan.AspectRatio)
			require.Equal(t, "video/mp4", plan.Artifact.ContentType)
		})
	}
}

func TestGoogleVideoRouteAcceptanceBindsEveryAdvertisedMode(t *testing.T) {
	tests := []struct {
		profileID   string
		channelType int
	}{
		{generationprofile.GoogleGeminiOmni11FlashBasicVideoV1, constant.ChannelTypeGemini},
		{generationprofile.GoogleGeminiVeo31VideoV1, constant.ChannelTypeGemini},
		{generationprofile.GoogleVertexVeo31VideoV1, constant.ChannelTypeVertexAi},
	}
	for _, test := range tests {
		t.Run(test.profileID, func(t *testing.T) {
			profile, ok := generationprofile.Get(test.profileID)
			require.True(t, ok)
			require.Equal(t, []string{"text_to_video", "image_to_video"}, profile.AcceptanceTestModes())
			for _, mode := range profile.AcceptanceTestModes() {
				plan, err := resolvePlatformGenerationRouteTestPlan(&relayservice.PlatformGenerationRouteTestBinding{
					Mode: mode, NativeChannelType: test.channelType,
					CapabilityProfileID: profile.ID, CapabilityProfileRevision: profile.Revision,
				})
				require.NoError(t, err)
				require.Equal(t, mode, plan.Mode)
				if mode == "text_to_video" {
					require.Empty(t, plan.Images)
					continue
				}
				require.Len(t, plan.Images, 1)
				const prefix = "data:image/png;base64,"
				require.True(t, strings.HasPrefix(plan.Images[0], prefix))
				payload, err := base64.StdEncoding.Strict().DecodeString(strings.TrimPrefix(plan.Images[0], prefix))
				require.NoError(t, err)
				image, err := png.Decode(bytes.NewReader(payload))
				require.NoError(t, err)
				require.Equal(t, 512, image.Bounds().Dx())
				require.Equal(t, 512, image.Bounds().Dy())
			}
		})
	}
}

func TestGoogleGeminiRouteAcceptanceUsesTransientAuthorizedArtifactVerifier(t *testing.T) {
	profile, ok := generationprofile.Get(generationprofile.GoogleGeminiOmni11FlashBasicVideoV1)
	require.True(t, ok)
	binding := &relayservice.PlatformGenerationRouteTestBinding{
		ChannelID:                 81,
		NativeChannelType:         constant.ChannelTypeGemini,
		UpstreamModel:             "gemini-omni-1.1-flash",
		CapabilityProfileID:       profile.ID,
		CapabilityProfileRevision: profile.Revision,
	}
	plan, err := resolvePlatformGenerationRouteTestPlan(binding)
	require.NoError(t, err)
	providerTaskID := "omni-file:video01:4:720p:16x9:10:20:0:30:v1"
	providerURL := "https://generativelanguage.googleapis.com/v1beta/files/video01:download?alt=media"
	result := &relaycommon.TaskInfo{
		Status:   string(model.TaskStatusSuccess),
		Progress: "100%",
		TaskID:   providerTaskID,
		Url:      providerURL,
		ProviderResultProof: &relaycommon.ProviderTaskResultProof{
			SchemaVersion: 1, Protocol: profile.Protocol, TaskID: providerTaskID,
			Model: binding.UpstreamModel, ProviderStatus: "succeeded",
			Resolution: plan.Resolution, DurationSeconds: plan.Duration,
			AspectRatio: plan.AspectRatio, OutputCount: 1, MediaType: "video",
		},
	}

	originalGoogle := verifyPlatformGoogleGenerationRouteTestArtifact
	originalGeneric := verifyPlatformGenerationRouteTestArtifact
	verifyPlatformGoogleGenerationRouteTestArtifact = func(
		ctx context.Context,
		locator string,
		artifact generationprofile.ArtifactContract,
		apiKey string,
	) (model.PlatformChannelTestArtifactEvidence, error) {
		require.NotNil(t, ctx)
		require.Equal(t, providerURL, locator)
		require.Equal(t, plan.Artifact, artifact)
		require.Equal(t, "route-google-key", apiKey)
		return model.PlatformChannelTestArtifactEvidence{
			SHA256: strings.Repeat("a", 64), SizeBytes: 128, ContentType: "video/mp4",
		}, nil
	}
	verifyPlatformGenerationRouteTestArtifact = func(
		context.Context,
		string,
		generationprofile.ArtifactContract,
	) (model.PlatformChannelTestArtifactEvidence, error) {
		t.Fatal("Google Files artifact must not use the anonymous verifier")
		return model.PlatformChannelTestArtifactEvidence{}, nil
	}
	t.Cleanup(func() {
		verifyPlatformGoogleGenerationRouteTestArtifact = originalGoogle
		verifyPlatformGenerationRouteTestArtifact = originalGeneric
	})

	evidence := model.PlatformChannelTestArtifactEvidence{}
	err = verifyPlatformGenerationRouteTerminalArtifact(
		context.Background(),
		result,
		providerTaskID,
		binding,
		plan,
		&model.Channel{Id: 81, Type: constant.ChannelTypeGemini, Key: "route-google-key"},
		&evidence,
	)
	require.NoError(t, err)
	require.Equal(t, "video/mp4", evidence.ContentType)
	require.Equal(t, strings.Repeat("a", 64), evidence.SHA256)
}
