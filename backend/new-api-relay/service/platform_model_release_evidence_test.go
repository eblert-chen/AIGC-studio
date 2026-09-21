package service

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func resetPlatformModelReleaseEvidenceConfigCache() {
	platformRelayConfigCache.Lock()
	platformRelayConfigCache.snapshot = platformRelayConfigSnapshot{}
	platformRelayConfigCache.lastObserved = time.Time{}
	platformRelayConfigCache.clockFailed = false
	platformRelayConfigCache.Unlock()
}

func setupPlatformModelReleaseEvidenceTest(t *testing.T) (PlatformRelayRouteDeclaration, string) {
	t.Helper()
	originalDB := model.DB
	originalDatabaseType := common.MainDatabaseType()
	database, err := gorm.Open(sqlite.Open("file:model-release-evidence-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"release-evidence-v1","keys":{"release-evidence-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)
	require.NoError(t, database.AutoMigrate(&model.Channel{}, &model.ProviderChannelCredentialSetVersion{}))
	require.NoError(t, model.MigrateProviderChannelCredentialVaultStorage())
	require.NoError(t, model.MigratePlatformChannelControlStorage())
	require.NoError(t, model.MigratePlatformChannelControlStorageV7WithDB(database))

	key := "route-evidence-provider-key"
	channel := model.Channel{
		Type: constant.ChannelTypeVolcEngine, Key: key, Status: common.ChannelStatusEnabled,
		Name: "release evidence channel", Models: constant.PlatformGenerationArkSeedream50Model,
		CreatedTime: time.Now().Unix(),
	}
	require.NoError(t, database.Create(&channel).Error)

	profile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	route := PlatformRelayRouteDeclaration{
		RouteID: "release-evidence-route", ProviderName: "volcengine-ark", AccountID: "ark-account-one",
		ChannelID: channel.Id, NativeChannelType: constant.ChannelTypeVolcEngine,
		KeyIndex: 0, KeyFingerprint: fmt.Sprintf("%x", common.Sha256Raw([]byte(key))),
		ChannelClass: PlatformChannelClassOfficial, UpstreamModel: constant.PlatformGenerationArkSeedream50Model,
		RPMLimit: 10, ActiveTaskLimit: 2, Capabilities: profile.Capability,
		CapabilityProfile: profile.ID,
	}
	routesRaw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{
		constant.PlatformGenerationPublicSeedream50Model: {route},
	})
	require.NoError(t, err)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", string(routesRaw))
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", `{"platform-test":{"tenant_id":"51bdf7c4-93a6-4b7c-a4a1-03f616a10f30","api_key":"api-key","upstream_token":"upstream-token"}}`)
	resetPlatformModelReleaseEvidenceConfigCache()
	t.Cleanup(func() {
		resetPlatformModelReleaseEvidenceConfigCache()
		model.DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	return route, constant.PlatformGenerationPublicSeedream50Model
}

func platformBoundTestRequest(operationID, publicModelID, routeID string) dto.PlatformChannelControlTestRequest {
	return dto.PlatformChannelControlTestRequest{
		OperationID:   operationID,
		TenantID:      "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30",
		Actor:         "platform-owner",
		Reason:        "Verify exact generation release route",
		PublicModelID: publicModelID,
		RouteID:       routeID,
	}
}

func requirePlatformModelReleaseEvidence(
	t *testing.T,
	projection PlatformModelReleaseEvidenceProjection,
	publicModelID string,
) PlatformModelReleaseEvidence {
	t.Helper()
	for _, evidence := range projection.Models {
		if evidence.PublicModelID == publicModelID {
			return evidence
		}
	}
	require.FailNow(t, "model release evidence is missing", publicModelID)
	return PlatformModelReleaseEvidence{}
}

func TestModelReleaseEvidenceUsesOnlyExactDurableSuccessfulReceipt(t *testing.T) {
	route, publicModelID := setupPlatformModelReleaseEvidenceTest(t)

	before, err := GetPlatformModelReleaseEvidenceProjection()
	require.NoError(t, err)
	require.Len(t, before.Models, 12)
	beforeEvidence := requirePlatformModelReleaseEvidence(t, before, publicModelID)
	assert.Equal(t, "blocked", beforeEvidence.Status)
	assert.Zero(t, beforeEvidence.FreshTestCount)
	for _, evidence := range before.Models {
		if evidence.PublicModelID == publicModelID {
			continue
		}
		assert.Equal(t, "blocked", evidence.Status)
		assert.Zero(t, evidence.RouteCount, evidence.PublicModelID+" is candidate metadata, not route evidence")
	}

	failedRequest := platformBoundTestRequest("failed-route-test-0001", publicModelID, route.RouteID)
	_, execute, _, err := BeginPlatformChannelControlTest(route.ChannelID, failedRequest, "failed-route-test-request")
	require.NoError(t, err)
	require.True(t, execute)
	claimed, err := ClaimPlatformChannelControlTestSubmission(failedRequest.TenantID, failedRequest.OperationID)
	require.NoError(t, err)
	require.True(t, claimed)
	_, err = CompletePlatformChannelControlRouteTestProvenNoCreation(
		failedRequest.TenantID, failedRequest.OperationID, 200,
		model.PlatformChannelControlErrorTestValidation,
	)
	require.NoError(t, err)
	// Failed native tests update Channel.TestTime elsewhere in new-api. Even an
	// explicit observational timestamp must not become release evidence.
	require.NoError(t, model.DB.Model(&model.Channel{}).Where("id = ?", route.ChannelID).
		Updates(map[string]any{"test_time": time.Now().Unix(), "response_time": 200}).Error)

	afterFailure, err := GetPlatformModelReleaseEvidenceProjection()
	require.NoError(t, err)
	require.Len(t, afterFailure.Models, 12)
	afterFailureEvidence := requirePlatformModelReleaseEvidence(t, afterFailure, publicModelID)
	assert.Equal(t, "blocked", afterFailureEvidence.Status)
	assert.Zero(t, afterFailureEvidence.FreshTestCount)

	successRequest := platformBoundTestRequest("success-route-test-0001", publicModelID, route.RouteID)
	_, execute, binding, err := BeginPlatformChannelControlTest(route.ChannelID, successRequest, "success-route-test-request")
	require.NoError(t, err)
	require.True(t, execute)
	require.NotNil(t, binding)
	claimed, err = ClaimPlatformChannelControlTestSubmission(successRequest.TenantID, successRequest.OperationID)
	require.NoError(t, err)
	require.True(t, claimed)
	require.NoError(t, RecordPlatformChannelControlTestSubmitted(
		successRequest.TenantID, successRequest.OperationID, "cgt-release-evidence-success",
	))
	_, err = CompletePlatformChannelControlRouteTestSuccess(
		successRequest.TenantID, successRequest.OperationID, 17,
		model.PlatformChannelTestArtifactEvidence{
			SHA256: strings.Repeat("a", 64), SizeBytes: 128, ContentType: "image/png",
		},
	)
	require.NoError(t, err)

	ready, err := GetPlatformModelReleaseEvidenceProjection()
	require.NoError(t, err)
	require.Len(t, ready.Models, 12)
	readyEvidence := requirePlatformModelReleaseEvidence(t, ready, publicModelID)
	assert.Equal(t, "ready", readyEvidence.Status)
	assert.Equal(t, 1, readyEvidence.RouteCount)
	assert.Equal(t, 1, readyEvidence.EnabledRouteCount)
	assert.Equal(t, 1, readyEvidence.AcceptedRouteCount)
	assert.Equal(t, 1, readyEvidence.FreshTestCount)
	assert.NotNil(t, readyEvidence.LatestSuccessfulTestAt)
	require.Len(t, readyEvidence.Routes, 1)
	assert.Equal(t, route.RouteID, readyEvidence.Routes[0].RouteID)
	assert.Equal(t, route.ChannelID, readyEvidence.Routes[0].ChannelID)
	assert.Equal(t, route.UpstreamModel, readyEvidence.Routes[0].UpstreamModel)
	assert.Equal(t, generationprofile.Seedream50TextToImageV1, readyEvidence.Routes[0].AdapterProfileID)
	assert.True(t, readyEvidence.Routes[0].Enabled)
	assert.True(t, readyEvidence.Routes[0].Accepted)
	assert.True(t, readyEvidence.Routes[0].Fresh)
	require.NotNil(t, readyEvidence.Routes[0].LatestSuccessfulTestAt)
	require.NotNil(t, readyEvidence.Routes[0].FreshUntil)
	assert.True(t, ready.GeneratedAt.Before(*readyEvidence.Routes[0].FreshUntil))
	assert.WithinDuration(t, *readyEvidence.LatestSuccessfulTestAt, *readyEvidence.Routes[0].LatestSuccessfulTestAt, time.Second)

	// Any current binding drift invalidates the receipt without changing the
	// public capability revision.
	drifted := *binding
	drifted.CredentialFingerprintSHA256 = fmt.Sprintf("%x", common.Sha256Raw([]byte("rotated-key")))
	var operation model.PlatformChannelControlOperation
	require.NoError(t, model.DB.Where("operation_id = ?", successRequest.OperationID).First(&operation).Error)
	wrongMedia := operation
	wrongMedia.ProviderArtifactContentType = "video/mp4"
	assert.False(t, platformRouteTestReceiptMatches(wrongMedia, *binding),
		"a syntactically valid video receipt must not attest an image profile")
	assert.False(t, platformRouteTestReceiptMatches(operation, drifted))
	assert.Equal(t, binding.CapabilityRevision, readyEvidence.CapabilityRevision)
}

func TestPlatformRouteTestReceiptMatcherUsesImageAndVideoProfileArtifactContracts(t *testing.T) {
	tests := []struct {
		name         string
		profileID    string
		expectedMIME string
		wrongMIME    string
	}{
		{name: "synchronous image", profileID: generationprofile.VolcengineArkImageGenerationV1, expectedMIME: "image/png", wrongMIME: "video/mp4"},
		{name: "asynchronous video regression", profileID: generationprofile.VolcengineArkVideoGenerationV1, expectedMIME: "video/mp4", wrongMIME: "image/png"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			profile, ok := generationprofile.Get(test.profileID)
			require.True(t, ok)
			binding := PlatformGenerationRouteTestBinding{
				PublicModelID:               "public-model",
				RouteID:                     "route-one",
				ChannelID:                   73,
				NativeChannelType:           profile.NativeChannelType,
				UpstreamModel:               "provider-model",
				CapabilityProfileID:         profile.ID,
				CapabilityProfileRevision:   profile.Revision,
				ModelReleaseID:              "release-one",
				ModelReleaseRevision:        "sha256:" + strings.Repeat("1", 64),
				CapabilityRevision:          "sha256:" + strings.Repeat("2", 64),
				RoutingReleaseSHA256:        strings.Repeat("3", 64),
				RouteBindingSHA256:          strings.Repeat("4", 64),
				CredentialFingerprintSHA256: strings.Repeat("5", 64),
				TransportRevision:           "sha256:" + strings.Repeat("6", 64),
				TransportSHA256:             strings.Repeat("7", 64),
			}
			intent, err := common.Marshal(platformRouteTestIntentProjection{
				PublicModelID: binding.PublicModelID, RouteID: binding.RouteID,
				UpstreamModel:             binding.UpstreamModel,
				CapabilityProfileID:       binding.CapabilityProfileID,
				CapabilityProfileRevision: binding.CapabilityProfileRevision,
				ModelReleaseID:            binding.ModelReleaseID, ModelReleaseRevision: binding.ModelReleaseRevision,
				CapabilityRevision:          binding.CapabilityRevision,
				RoutingReleaseSHA256:        binding.RoutingReleaseSHA256,
				RouteBindingSHA256:          binding.RouteBindingSHA256,
				CredentialFingerprintSHA256: binding.CredentialFingerprintSHA256,
				TransportRevision:           binding.TransportRevision, TransportSHA256: binding.TransportSHA256,
			})
			require.NoError(t, err)
			now := time.Now().UTC()
			receipt := model.PlatformChannelControlOperation{
				ChannelID: binding.ChannelID, IntentJSON: string(intent), CompletedAt: &now,
				IntentTransportRevision: binding.TransportRevision,
				IntentTransportSHA256:   binding.TransportSHA256,
				ProviderSubmissionState: model.PlatformChannelTestSubmissionArtifactVerified,
				ProviderArtifactSHA256:  strings.Repeat("a", 64), ProviderArtifactSizeBytes: 128,
				ProviderArtifactContentType: test.expectedMIME,
			}
			assert.True(t, platformRouteTestReceiptMatches(receipt, binding))
			receipt.ProviderArtifactContentType = test.wrongMIME
			assert.False(t, platformRouteTestReceiptMatches(receipt, binding))
		})
	}
}

func TestGoogleRouteTestReceiptsAreModeBound(t *testing.T) {
	profile, ok := generationprofile.Get(generationprofile.GoogleGeminiVeo31VideoV1)
	require.True(t, ok)
	binding := PlatformGenerationRouteTestBinding{
		PublicModelID: "veo-3.1", RouteID: "google-route-one", Mode: "text_to_video",
		ChannelID: 91, NativeChannelType: profile.NativeChannelType,
		UpstreamModel: "veo-3.1-generate-preview", CapabilityProfileID: profile.ID,
		CapabilityProfileRevision: profile.Revision,
		ModelReleaseID:            "google-release-one", ModelReleaseRevision: "sha256:" + strings.Repeat("1", 64),
		CapabilityRevision: "sha256:" + strings.Repeat("2", 64), RoutingReleaseSHA256: "sha256:" + strings.Repeat("3", 64),
		RouteBindingSHA256: "sha256:" + strings.Repeat("4", 64), CredentialFingerprintSHA256: strings.Repeat("5", 64),
		TransportRevision: "sha256:" + strings.Repeat("6", 64), TransportSHA256: "sha256:" + strings.Repeat("7", 64),
	}
	now := time.Now().UTC()
	receiptFor := func(mode string, persistMode bool) model.PlatformChannelControlOperation {
		intent := platformRouteTestIntentProjection{
			PublicModelID: binding.PublicModelID, RouteID: binding.RouteID,
			UpstreamModel: binding.UpstreamModel, CapabilityProfileID: binding.CapabilityProfileID,
			CapabilityProfileRevision: binding.CapabilityProfileRevision,
			ModelReleaseID:            binding.ModelReleaseID, ModelReleaseRevision: binding.ModelReleaseRevision,
			CapabilityRevision: binding.CapabilityRevision, RoutingReleaseSHA256: binding.RoutingReleaseSHA256,
			RouteBindingSHA256:          binding.RouteBindingSHA256,
			CredentialFingerprintSHA256: binding.CredentialFingerprintSHA256,
			TransportRevision:           binding.TransportRevision, TransportSHA256: binding.TransportSHA256,
		}
		if persistMode {
			intent.Mode = mode
		}
		encoded, err := common.Marshal(intent)
		require.NoError(t, err)
		return model.PlatformChannelControlOperation{
			ChannelID: binding.ChannelID, IntentJSON: string(encoded), CompletedAt: &now,
			IntentTransportRevision: binding.TransportRevision, IntentTransportSHA256: binding.TransportSHA256,
			ProviderSubmissionState: model.PlatformChannelTestSubmissionArtifactVerified,
			ProviderArtifactSHA256:  strings.Repeat("a", 64), ProviderArtifactSizeBytes: 128,
			ProviderArtifactContentType: "video/mp4",
		}
	}

	legacyText := receiptFor("", false)
	assert.True(t, platformRouteTestReceiptMatches(legacyText, binding), "legacy mode-less receipt binds only the default text mode")
	imageBinding := binding
	imageBinding.Mode = "image_to_video"
	assert.False(t, platformRouteTestReceiptMatches(legacyText, imageBinding))

	explicitText := receiptFor("text_to_video", true)
	explicitImage := receiptFor("image_to_video", true)
	assert.True(t, platformRouteTestReceiptMatches(explicitText, binding))
	assert.False(t, platformRouteTestReceiptMatches(explicitText, imageBinding))
	assert.True(t, platformRouteTestReceiptMatches(explicitImage, imageBinding))
	assert.False(t, platformRouteTestReceiptMatches(explicitImage, binding))
}

func TestModelReleaseEvidenceFreshnessConfigurationFailsClosed(t *testing.T) {
	t.Setenv(platformModelReleaseEvidenceFreshnessEnvironment, "1")
	_, err := platformModelReleaseTestFreshness()
	require.ErrorContains(t, err, "between")
	t.Setenv(platformModelReleaseEvidenceFreshnessEnvironment, "86400")
	value, err := platformModelReleaseTestFreshness()
	require.NoError(t, err)
	assert.Equal(t, 24*time.Hour, value)
}

func TestModelReleaseEvidenceBlockedLegacyRouteDoesNotFailProjection(t *testing.T) {
	route, publicModelID := setupPlatformModelReleaseEvidenceTest(t)
	require.NoError(t, model.DB.Model(&model.Channel{}).Where("id = ?", route.ChannelID).
		Update("status", common.ChannelStatusManuallyDisabled).Error)

	projection, err := GetPlatformModelReleaseEvidenceProjection()
	require.NoError(t, err, "one disabled historical legacy route must block its model, not fail the whole catalog")
	require.Len(t, projection.Models, 12)
	evidence := requirePlatformModelReleaseEvidence(t, projection, publicModelID)
	assert.Equal(t, "blocked", evidence.Status)
	assert.Equal(t, 1, evidence.RouteCount)
	assert.Equal(t, 0, evidence.EnabledRouteCount)
	assert.Equal(t, 1, evidence.AcceptedRouteCount, "current route validation is independent from native enablement")
	assert.Equal(t, 0, evidence.FreshTestCount, "historical tests on a disabled native route are not eligible evidence")
	assert.Empty(t, evidence.ModelReleaseID, "legacy migration routes truthfully omit a signed model release identity")
	require.Len(t, evidence.Routes, 1)
	assert.False(t, evidence.Routes[0].Enabled)
	assert.True(t, evidence.Routes[0].Accepted)
	assert.False(t, evidence.Routes[0].Fresh)
}

func TestPlatformRouteAcceptedCountsOnlyCurrentSecureAcceptance(t *testing.T) {
	now := platformRouteAcceptanceNow()
	current := PlatformRelayRouteDeclaration{
		Acceptance:         &PlatformRouteAcceptanceEvidence{},
		AcceptanceDigest:   "sha256:" + strings.Repeat("1", 64),
		AcceptanceNotAfter: now.Add(time.Minute),
	}
	historical := current
	historical.AcceptanceNotAfter = now.Add(-time.Second)
	assert.True(t, platformRouteAccepted(current, true))
	assert.False(t, platformRouteAccepted(historical, true))
	assert.True(t, platformRouteAccepted(PlatformRelayRouteDeclaration{}, false), "development validates routes without signed production acceptance")
}

func TestRoutingAndKeyReleaseDriftDoesNotChangeCapabilityRevision(t *testing.T) {
	route, publicModelID := setupPlatformModelReleaseEvidenceTest(t)
	before, err := GetPlatformModelReleaseEvidenceProjection()
	require.NoError(t, err)
	require.Len(t, before.Models, 12)
	beforeEvidence := requirePlatformModelReleaseEvidence(t, before, publicModelID)

	rotatedKey := "route-evidence-rotated-provider-key"
	require.NoError(t, model.RotateChannelCredentialSet(route.ChannelID, rotatedKey))
	route.KeyFingerprint = fmt.Sprintf("%x", common.Sha256Raw([]byte(rotatedKey)))
	routesRaw, err := common.Marshal(map[string][]PlatformRelayRouteDeclaration{
		publicModelID: {route},
	})
	require.NoError(t, err)
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", string(routesRaw))
	resetPlatformModelReleaseEvidenceConfigCache()

	after, err := GetPlatformModelReleaseEvidenceProjection()
	require.NoError(t, err)
	require.Len(t, after.Models, 12)
	afterEvidence := requirePlatformModelReleaseEvidence(t, after, publicModelID)
	assert.Equal(t, beforeEvidence.CapabilityRevision, afterEvidence.CapabilityRevision,
		"credential and routing releases are intentionally independent from the public capability revision")
	assert.NotEqual(t, beforeEvidence.RoutingReleaseSHA256, afterEvidence.RoutingReleaseSHA256)
	assert.Equal(t, "blocked", afterEvidence.Status, "the rotated key requires a new exact successful route test")
	assert.Zero(t, afterEvidence.FreshTestCount)
}
