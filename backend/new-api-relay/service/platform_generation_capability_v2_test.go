package service

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPlatformCapabilityV2AcceptsConditionalFaceResources(t *testing.T) {
	capability := platformCapabilityForTest(
		[]int{5},
		[]string{"16:9"},
		[]string{"720p"},
		true,
		1,
	)
	capability.SchemaVersion = 2
	mode := capability.Modes["text_to_video"]
	mode.ConditionalRequiredResourceKeys = map[string][]string{
		"face_enabled": {"face.library"},
	}
	capability.Modes["text_to_video"] = mode

	require.NoError(t, validatePlatformCapability("video-model", capability))
}

func TestPlatformCapabilityV1RejectsConditionalResources(t *testing.T) {
	capability := platformCapabilityForTest(
		[]int{5},
		[]string{"16:9"},
		[]string{"720p"},
		true,
		1,
	)
	mode := capability.Modes["text_to_video"]
	mode.ConditionalRequiredResourceKeys = map[string][]string{
		"face_enabled": {"face.library"},
	}
	capability.Modes["text_to_video"] = mode

	require.ErrorContains(
		t,
		validatePlatformCapability("video-model", capability),
		"schema v1",
	)
}

func TestPlatformCapabilityV2IntersectsConditionalResourcesFailoverSafely(t *testing.T) {
	first := platformCapabilityForTest(
		[]int{5},
		[]string{"16:9"},
		[]string{"720p"},
		true,
		1,
	)
	first.SchemaVersion = 2
	firstMode := first.Modes["text_to_video"]
	firstMode.ConditionalRequiredResourceKeys = map[string][]string{
		"face_enabled": {"face.library"},
	}
	first.Modes["text_to_video"] = firstMode

	second := platformCapabilityForTest(
		[]int{5},
		[]string{"16:9"},
		[]string{"720p"},
		true,
		1,
	)
	second.SchemaVersion = 2
	secondMode := second.Modes["text_to_video"]
	secondMode.ConditionalRequiredResourceKeys = map[string][]string{
		"face_enabled": {"feature.identity", "face.library"},
	}
	second.Modes["text_to_video"] = secondMode

	capability, err := intersectPlatformCapabilities(
		"video-model",
		[]PlatformRelayRouteDeclaration{
			{RouteID: "route-a", Capabilities: first},
			{RouteID: "route-b", Capabilities: second},
		},
	)
	require.NoError(t, err)
	require.Equal(t, 2, capability.SchemaVersion)
	require.Equal(
		t,
		[]string{"face.library", "feature.identity"},
		capability.Modes["text_to_video"].ConditionalRequiredResourceKeys["face_enabled"],
	)
}

func TestPlatformCapabilityIntersectionRejectsMixedSchemaVersions(t *testing.T) {
	first := platformCapabilityForTest(
		[]int{5},
		[]string{"16:9"},
		[]string{"720p"},
		false,
		1,
	)
	second := platformCapabilityForTest(
		[]int{5},
		[]string{"16:9"},
		[]string{"720p"},
		false,
		1,
	)
	second.SchemaVersion = 2

	_, err := intersectPlatformCapabilities(
		"video-model",
		[]PlatformRelayRouteDeclaration{
			{RouteID: "route-a", Capabilities: first},
			{RouteID: "route-b", Capabilities: second},
		},
	)
	require.ErrorContains(t, err, "different capability schema versions")
}
