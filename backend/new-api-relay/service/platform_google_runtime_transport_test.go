package service

import (
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/require"
)

func googleRuntimeTransportTestChannel(baseURL string, setting string) *model.Channel {
	return &model.Channel{
		Id: 24, Type: constant.ChannelTypeGemini, BaseURL: &baseURL, Setting: &setting,
		Key: "google-runtime-secret", Name: "google-runtime-route", Models: "veo-3.1-generate-preview",
	}
}

func TestPlatformGoogleRuntimeTransportRejectsBaseURLAndProxyDrift(t *testing.T) {
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	channel := googleRuntimeTransportTestChannel("https://generativelanguage.googleapis.com", `{}`)
	revision, digest, err := resolvePlatformRouteTransportBinding(channel)
	require.NoError(t, err)
	expected := PlatformGenerationRuntimeTransportBinding{Revision: revision, SHA256: digest}
	require.NoError(t, ValidatePlatformGenerationRuntimeTransport(channel, expected))

	driftedBase := *channel
	baseURL := "https://provider-drift.example"
	driftedBase.BaseURL = &baseURL
	require.ErrorContains(t, ValidatePlatformGenerationRuntimeTransport(&driftedBase, expected), "changed")

	driftedProxy := *channel
	setting := `{"proxy":"http://proxy.example:3128"}`
	driftedProxy.Setting = &setting
	require.ErrorContains(t, ValidatePlatformGenerationRuntimeTransport(&driftedProxy, expected), "changed")
}

func TestPlatformGoogleProductionTransportRequiresExactOfficialEndpoint(t *testing.T) {
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	for _, raw := range []string{
		"https://credential-sink.example",
		"https://generativelanguage.googleapis.com.evil.example",
		"https://generativelanguage.googleapis.com/custom",
		"http://generativelanguage.googleapis.com",
		"https://generativelanguage.googleapis.com:8443",
	} {
		channel := googleRuntimeTransportTestChannel(raw, `{}`)
		_, _, err := resolvePlatformRouteTransportBinding(channel)
		require.ErrorContains(t, err, "official Gemini route", raw)
	}
	channel := googleRuntimeTransportTestChannel("https://GENeRATIVElanguage.googleapis.com/", `{}`)
	_, digest, err := resolvePlatformRouteTransportBinding(channel)
	require.NoError(t, err)
	require.True(t, strings.HasPrefix(digest, "sha256:"))
}

func TestPlatformVertexProductionTransportUsesOnlyOfficialDynamicOrRegionalEndpoint(t *testing.T) {
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	setting := `{}`
	dynamic := &model.Channel{Id: 41, Type: constant.ChannelTypeVertexAi, Setting: &setting}
	baseURL, _, err := resolvePlatformRouteTransportBinding(dynamic)
	require.NoError(t, err)
	require.NotEmpty(t, baseURL)

	regionalURL := "https://us-central1-aiplatform.googleapis.com"
	regional := &model.Channel{Id: 42, Type: constant.ChannelTypeVertexAi, BaseURL: &regionalURL, Setting: &setting}
	_, _, err = resolvePlatformRouteTransportBinding(regional)
	require.NoError(t, err)

	maliciousURL := "https://us-central1-aiplatform.googleapis.com.evil.example"
	malicious := &model.Channel{Id: 43, Type: constant.ChannelTypeVertexAi, BaseURL: &maliciousURL, Setting: &setting}
	_, _, err = resolvePlatformRouteTransportBinding(malicious)
	require.ErrorContains(t, err, "official Vertex AI route")
}
