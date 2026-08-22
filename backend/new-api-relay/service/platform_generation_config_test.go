package service

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPlatformRelayCapabilityRevisionMatchesPythonOracle(t *testing.T) {
	capability := platformCapabilityForTest(
		[]int{5},
		[]string{"16:9"},
		[]string{"720p"},
		false,
		1,
	)
	canonical, err := platformRelayCanonicalJSON(capability)
	require.NoError(t, err)

	assert.JSONEq(t, `{"modes":{"text_to_video":{"input_media_types":["image"],"limits":{"aspect_ratios":["16:9"],"duration_seconds":[5],"max_audio":0,"max_images":1,"max_prompt_length":1000,"max_videos":0,"output_counts":[1],"resolutions":["720p"]},"required_resource_keys":[],"supports_face":false}},"schema_version":1}`, string(canonical))
	digest := fmt.Sprintf("sha256:%x", common.Sha256Raw(canonical))
	assert.Equal(t, "sha256:2daca8cae9cd2d8684aa2defc0880d61679ea6385ab700f147af183916ce0e58", digest)
}

func TestPlatformRelayConfigDoesNotTreatAnEmptyInitialCacheAsLoaded(t *testing.T) {
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")

	_, err := GetPlatformRelayModelCatalog()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "RELAY_COMPAT_CLIENT_CREDENTIALS_JSON")
}

func TestPlatformRelayConfigRejectsUnknownOrDowngradedEnvironment(t *testing.T) {
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "prod")
	_, err := GetPlatformRelayModelCatalog()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "must be one of")

	t.Setenv("APP_ENV", "production")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	_, err = GetPlatformRelayModelCatalog()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "APP_ENV=production")

	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	_, err = GetPlatformRelayModelCatalog()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "DEPLOYMENT_ENV=staging")
}

func TestPlatformRelayConfigAllowsImplicitDevelopmentOutsideSecureDeployments(t *testing.T) {
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "")
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")

	_, err := GetPlatformRelayModelCatalog()
	require.NoError(t, err)
}

func TestPlatformGenerationProviderFailurePolicyIsStrictAndBounded(t *testing.T) {
	t.Setenv("RELAY_PROVIDER_FAILURE_THRESHOLD", "3")
	t.Setenv("RELAY_PROVIDER_COOLDOWN_SECONDS", "30")
	threshold, cooldown, err := platformGenerationProviderFailurePolicy()
	require.NoError(t, err)
	assert.Equal(t, 3, threshold)
	assert.Equal(t, 30*time.Second, cooldown)

	t.Setenv("RELAY_PROVIDER_FAILURE_THRESHOLD", "not-a-number")
	_, _, err = platformGenerationProviderFailurePolicy()
	assert.ErrorContains(t, err, "must be an integer")

	t.Setenv("RELAY_PROVIDER_FAILURE_THRESHOLD", "3")
	t.Setenv("RELAY_PROVIDER_COOLDOWN_SECONDS", "86401")
	_, _, err = platformGenerationProviderFailurePolicy()
	assert.ErrorContains(t, err, "between 0 and 86400")
}

func TestPlatformRelayProductionBuildProvenanceBindsTheExtensionForkAndImage(t *testing.T) {
	_ = newPlatformRouteAcceptanceFixture(t)
	previousUpstream := platformRelayCompiledUpstreamRevision
	previousSource := platformRelayCompiledSourceRevision
	previousSnapshot := platformRelayCompiledSnapshotSHA256
	previousCount := platformRelayCompiledSnapshotFileCount
	t.Cleanup(func() {
		platformRelayCompiledUpstreamRevision = previousUpstream
		platformRelayCompiledSourceRevision = previousSource
		platformRelayCompiledSnapshotSHA256 = previousSnapshot
		platformRelayCompiledSnapshotFileCount = previousCount
	})
	platformRelayCompiledUpstreamRevision = PlatformRelayUpstreamGitRevision
	platformRelayCompiledSourceRevision = strings.Repeat("b", 40)
	platformRelayCompiledSnapshotSHA256 = "sha256:" + strings.Repeat("d", 64)
	platformRelayCompiledSnapshotFileCount = "123"
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", platformRelayCompiledSourceRevision)
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", platformRelayCompiledSnapshotSHA256)
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT", platformRelayCompiledSnapshotFileCount)
	t.Setenv("RELAY_COMPAT_IMAGE_DIGEST", "sha256:"+strings.Repeat("c", 64))
	require.NoError(t, ValidatePlatformRelayBuildProvenance(true))

	platformRelayCompiledSourceRevision = PlatformRelayUpstreamGitRevision
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", platformRelayCompiledSourceRevision)
	assert.ErrorContains(t, ValidatePlatformRelayBuildProvenance(true), "extension fork revision")

	platformRelayCompiledSourceRevision = strings.Repeat("b", 40)
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", platformRelayCompiledSourceRevision)
	t.Setenv("RELAY_COMPAT_IMAGE_DIGEST", "sha256:"+strings.Repeat("0", 64))
	assert.ErrorContains(t, ValidatePlatformRelayBuildProvenance(true), "non-placeholder")

	t.Setenv("RELAY_COMPAT_IMAGE_DIGEST", "sha256:"+strings.Repeat("c", 64))
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", "sha256:"+strings.Repeat("e", 64))
	assert.ErrorContains(t, ValidatePlatformRelayBuildProvenance(true), "does not match")

	platformRelayCompiledSnapshotSHA256 = "unknown"
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", "sha256:"+strings.Repeat("e", 64))
	assert.ErrorContains(t, ValidatePlatformRelayBuildProvenance(true), "build-time")
}

func TestPlatformRelayProcessInstanceIDIsStableAndNonSensitiveUUID(t *testing.T) {
	first := GetPlatformRelayBuildProvenance().InstanceID
	second := GetPlatformRelayBuildProvenance().InstanceID
	require.Equal(t, first, second)
	parsed, err := uuid.Parse(first)
	require.NoError(t, err)
	require.Equal(t, uuid.Version(4), parsed.Version())
}

func TestPlatformRelayCompiledBuildIdentityNeverUsesDeploymentEnvironment(t *testing.T) {
	previousUpstream := platformRelayCompiledUpstreamRevision
	previousSource := platformRelayCompiledSourceRevision
	previousSnapshot := platformRelayCompiledSnapshotSHA256
	previousCount := platformRelayCompiledSnapshotFileCount
	t.Cleanup(func() {
		platformRelayCompiledUpstreamRevision = previousUpstream
		platformRelayCompiledSourceRevision = previousSource
		platformRelayCompiledSnapshotSHA256 = previousSnapshot
		platformRelayCompiledSnapshotFileCount = previousCount
	})
	platformRelayCompiledUpstreamRevision = PlatformRelayUpstreamGitRevision
	platformRelayCompiledSourceRevision = strings.Repeat("a", 40)
	platformRelayCompiledSnapshotSHA256 = "sha256:" + strings.Repeat("b", 64)
	platformRelayCompiledSnapshotFileCount = "321"
	t.Setenv("RELAY_COMPAT_SOURCE_REVISION", strings.Repeat("c", 40))
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256", "sha256:"+strings.Repeat("d", 64))
	t.Setenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT", "999")
	t.Setenv("RELAY_COMPAT_IMAGE_DIGEST", "sha256:"+strings.Repeat("e", 64))

	identity := GetPlatformRelayCompiledBuildIdentity()
	require.Equal(t, 1, identity.SchemaVersion)
	require.Equal(t, "relay_compiled_build_identity", identity.Kind)
	require.Equal(t, PlatformRelayUpstreamGitRevision, identity.UpstreamGitRevision)
	require.Equal(t, strings.Repeat("a", 40), identity.SourceRevision)
	require.Equal(t, "sha256:"+strings.Repeat("b", 64), identity.SourceSnapshotSHA256)
	require.Equal(t, 321, identity.SourceSnapshotFileCount)
	require.Equal(t, platformRelayCompiledRouteAcceptanceKeysSHA256, identity.RouteAcceptanceTrustKeysSHA256)
}

func TestNormalizePlatformCallbackURLSeparatesDevelopmentAndProductionPolicy(t *testing.T) {
	developmentURL, err := normalizePlatformCallbackURL("http://platform-api:8000/internal/relay-callbacks", false)
	require.NoError(t, err)
	assert.Equal(t, "http://platform-api:8000/internal/relay-callbacks", developmentURL)

	_, err = normalizePlatformCallbackURL("http://platform-api:8000/internal/relay-callbacks", true)
	require.Error(t, err)
	_, err = normalizePlatformCallbackURL("https://127.0.0.1/internal/relay-callbacks", true)
	require.Error(t, err)
	_, err = normalizePlatformCallbackURL("https://relay.example.com/internal/relay-callbacks?token=secret", true)
	require.Error(t, err)

	productionURL, err := normalizePlatformCallbackURL("https://Relay.Example.com:443/internal/relay-callbacks", true)
	require.NoError(t, err)
	assert.Equal(t, "https://relay.example.com/internal/relay-callbacks", productionURL)
}

func TestPlatformRelayConfigRejectsShortCallbackSecret(t *testing.T) {
	credentials := `{"customer-platform":{"tenant_id":"00000000-0000-4000-8000-000000000001","api_key":"client-key","upstream_token":"native-token","callback_url":"http://platform-api:8000/internal/relay-callbacks","callback_signing_secret":"short"}}`
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", credentials)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")

	_, err := GetPlatformRelayModelCatalog()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "at least 32 bytes")
}

func TestAuthenticatePlatformRelayClientAcceptsConfiguredSecret(t *testing.T) {
	secret := strings.Repeat("k", 32)
	credentials := `{"customer-platform":{"tenant_id":"00000000-0000-4000-8000-000000000001","api_key":"` + secret + `","upstream_token":"native-token"}}`
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", credentials)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")

	principal, err := AuthenticatePlatformRelayClient("customer-platform", secret)
	require.NoError(t, err)
	assert.Equal(t, "00000000-0000-4000-8000-000000000001", principal.TenantID)

	_, err = AuthenticatePlatformRelayClient("unknown-client", secret)
	require.Error(t, err)
}

func TestPlatformRelayConfigRejectsNilTenantIdentity(t *testing.T) {
	secret := strings.Repeat("k", 32)
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{"customer-platform":{"tenant_id":"00000000-0000-0000-0000-000000000000","api_key":"`+secret+`","upstream_token":"native-token"}}`)
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")

	_, err := AuthenticatePlatformRelayClient("customer-platform", secret)
	require.Error(t, err)
}

func TestPlatformGenerationPromptLimitCountsUnicodeCharacters(t *testing.T) {
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{"platform":{"tenant_id":"00000000-0000-4000-8000-000000000001","api_key":"client-key","upstream_token":"native-token"}}`)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", "")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", `{"unicode-model":{"schema_version":1,"modes":{"text_to_video":{"input_media_types":[],"supports_face":false,"required_resource_keys":[],"limits":{"max_prompt_length":2,"max_images":0,"max_videos":0,"max_audio":0,"duration_seconds":[5],"aspect_ratios":["16:9"],"resolutions":["720p"],"output_counts":[1]}}}}}`)

	resource, ok, err := GetPlatformRelayModel("unicode-model")
	require.NoError(t, err)
	require.True(t, ok)
	request := dto.NewPlatformGenerationRequest()
	request.Model = resource.ID
	request.Mode = "text_to_video"
	request.ExpectedCapabilityRevision = resource.CapabilityRevision
	request.Inputs.Prompt = "你好"
	request.Output.DurationSeconds = 5
	request.Output.AspectRatio = "16:9"
	request.Output.Resolution = "720p"
	request.Output.Count = 1
	require.NoError(t, ValidatePlatformGenerationCapability(request))

	request.Inputs.Prompt = "你好啊"
	require.EqualError(t, ValidatePlatformGenerationCapability(request), "REQUEST_NOT_SUPPORTED_BY_MODEL")
}
