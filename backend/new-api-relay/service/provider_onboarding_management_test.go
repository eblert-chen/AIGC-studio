package service

import (
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func providerOnboardingViewByID(
	t *testing.T,
	catalog ProviderOnboardingCatalogView,
	provider string,
) ProviderOnboardingProviderView {
	t.Helper()
	for _, item := range catalog.Providers {
		if item.ID == provider {
			return item
		}
	}
	require.FailNow(t, "provider was not present in onboarding catalog", provider)
	return ProviderOnboardingProviderView{}
}

func TestProviderOnboardingManagementCatalogAndCredentialRotationAreSecretFree(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	require.NoError(t, model.InstallPlatformChannelControlRevisionGuardWithDB(db))

	catalog, err := ListProviderOnboardingProviders()
	require.NoError(t, err)
	require.Len(t, catalog.Providers, 3)
	assert.Equal(t, "staging", catalog.Environment)
	assert.Equal(t, []string{
		CredentialLateProviderGoogleGemini,
		CredentialLateProviderMiniMax,
		CredentialLateProviderVolcengineArk,
	}, []string{catalog.Providers[0].ID, catalog.Providers[1].ID, catalog.Providers[2].ID})
	for _, provider := range catalog.Providers {
		assert.Equal(t, "primary", provider.AccountID)
		assert.False(t, provider.Credential.Configured)
		assert.Equal(t, "not_configured", provider.LifecycleState)
		assert.False(t, provider.RouteMaterialized)
		assert.False(t, provider.AcceptanceVerified)
		assert.False(t, provider.AcceptanceFresh)
		assert.Nil(t, provider.AcceptanceValidUntil)
		assert.Equal(t, "not_recorded", provider.PlatformPublicationStatus)
		assert.Equal(t, "not_recorded", provider.PlatformPriceStatus)
		assert.Equal(t, "not_recorded", provider.PlatformGrantStatus)
		assert.False(t, provider.CanResume)
		assert.Equal(t, []string{"configure_credential"}, provider.AvailableActions)
		assert.Equal(t, "configure_credential", provider.NextAction)
		assert.Equal(t, "credential_not_configured", provider.BlockingReason)
		assert.Equal(t, "credential_not_configured", provider.ActionBlockerCode)
	}

	initialKey := "google-provider-management-key-one"
	created, changed, err := PutProviderOnboardingCredential(
		CredentialLateProviderGoogleGemini,
		initialKey,
		"configure reviewed Google primary account",
		"",
	)
	require.NoError(t, err)
	assert.True(t, changed)
	assert.Equal(t, model.ProviderOnboardingGoogleChannelID, created.ChannelID)
	assert.Equal(t, "primary", created.AccountID)
	assert.Equal(t, "staged", created.LifecycleState)
	assert.False(t, created.RouteMaterialized)
	assert.False(t, created.AcceptanceVerified)
	assert.Equal(t, []string{"rotate_credential"}, created.AvailableActions)
	assert.Equal(t, "prepare_and_sign_route_acceptance", created.NextAction)
	assert.Equal(t, "route_acceptance_required", created.BlockingReason)
	assert.Equal(t, "route_not_materialized", created.ActionBlockerCode)
	assert.Equal(t, "manually_disabled", created.ChannelStatus)
	assert.Len(t, created.Credential.FingerprintPrefix, 12)
	assert.NotContains(t, created.Credential.FingerprintPrefix, "sha256:")
	assert.NotEmpty(t, created.ControlRevision)

	encoded, err := common.Marshal(created)
	require.NoError(t, err)
	assert.NotContains(t, string(encoded), initialKey)
	assert.NotContains(t, string(encoded), credentialLateProviderFingerprint(initialKey))
	assert.NotContains(t, string(encoded), "credential_set_version")

	var stored struct {
		LegacyKey            string `gorm:"column:key"`
		CredentialSetVersion string
	}
	require.NoError(t, db.Table("channels").Select("key", "credential_set_version").
		Where("id = ?", created.ChannelID).Take(&stored).Error)
	assert.Empty(t, stored.LegacyKey)
	assert.NotEmpty(t, stored.CredentialSetVersion)
	var versions int64
	require.NoError(t, db.Model(&model.ProviderChannelCredentialSetVersion{}).
		Where("channel_id = ?", created.ChannelID).Count(&versions).Error)
	assert.Equal(t, int64(1), versions)

	replayed, changed, err := PutProviderOnboardingCredential(
		CredentialLateProviderGoogleGemini,
		initialKey,
		"retry the same Google credential after lost response",
		"sha256:"+strings.Repeat("0", 64),
	)
	require.NoError(t, err)
	assert.False(t, changed)
	assert.Equal(t, created.ControlRevision, replayed.ControlRevision)
	require.NoError(t, db.Model(&model.ProviderChannelCredentialSetVersion{}).
		Where("channel_id = ?", created.ChannelID).Count(&versions).Error)
	assert.Equal(t, int64(1), versions)

	_, _, err = PutProviderOnboardingCredential(
		CredentialLateProviderGoogleGemini,
		"google-provider-management-key-two",
		"rotate reviewed Google primary account",
		"",
	)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingExpectedRevisionRequired)

	require.NoError(t, db.Create(&model.PlatformGenerationProviderRoute{
		RouteKey: "old-google-primary", Model: "veo-3.1", Mode: "text_to_video",
		ProviderName: CredentialLateProviderGoogleGemini, AccountID: "primary",
		ChannelID: created.ChannelID, KeyIndex: 0,
		KeyFingerprint: credentialLateProviderFingerprint(initialKey), Enabled: true,
	}).Error)
	rotatedKey := "google-provider-management-key-two"
	rotated, changed, err := PutProviderOnboardingCredential(
		CredentialLateProviderGoogleGemini,
		rotatedKey,
		"rotate reviewed Google primary account",
		created.ControlRevision,
	)
	require.NoError(t, err)
	assert.True(t, changed)
	assert.NotEqual(t, created.ControlRevision, rotated.ControlRevision)
	assert.Equal(t, credentialLateProviderFingerprint(rotatedKey)[:12], rotated.Credential.FingerprintPrefix)
	assert.Equal(t, "staged", rotated.LifecycleState)
	var oldRoute model.PlatformGenerationProviderRoute
	require.NoError(t, db.Where("route_key = ?", "old-google-primary").First(&oldRoute).Error)
	assert.False(t, oldRoute.Enabled)
	require.NoError(t, db.Model(&model.ProviderChannelCredentialSetVersion{}).
		Where("channel_id = ?", created.ChannelID).Count(&versions).Error)
	assert.Equal(t, int64(2), versions)

	loaded, err := LoadProviderOnboardingCredentialInput(CredentialLateProviderGoogleGemini)
	require.NoError(t, err)
	assert.Equal(t, rotatedKey, loaded.APIKey)
	assert.Equal(t, "primary", loaded.AccountID)
	assert.Equal(t, model.ProviderOnboardingGoogleChannelID, loaded.ChannelID)
	assert.NotContains(t, loaded.String(), rotatedKey)
}

func TestProviderOnboardingCredentialRotationFencesActiveTasks(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	require.NoError(t, db.AutoMigrate(&model.PlatformGenerationRouteAdmission{}))

	initialKey := "minimax-provider-management-key-one"
	created, _, err := PutProviderOnboardingCredential(
		CredentialLateProviderMiniMax,
		initialKey,
		"configure reviewed MiniMax primary account",
		"",
	)
	require.NoError(t, err)
	route := model.PlatformGenerationProviderRoute{
		RouteKey: "held-minimax-primary", Model: "minimax-h3", Mode: "text_to_video",
		ProviderName: CredentialLateProviderMiniMax, AccountID: "primary",
		ChannelID: created.ChannelID, KeyIndex: 0,
		KeyFingerprint: credentialLateProviderFingerprint(initialKey), Enabled: true,
	}
	require.NoError(t, db.Create(&route).Error)
	require.NoError(t, db.Create(&model.PlatformGenerationRouteAdmission{
		JobID: "11111111-1111-4111-8111-111111111111", RouteID: route.ID,
		SubmissionTokenHash: strings.Repeat("a", 64), State: model.PlatformGenerationRouteAdmissionHeld,
		SlotHeld: true, Attempt: 1,
	}).Error)

	_, _, err = PutProviderOnboardingCredential(
		CredentialLateProviderMiniMax,
		"minimax-provider-management-key-two",
		"rotate reviewed MiniMax primary account",
		created.ControlRevision,
	)
	assert.ErrorIs(t, err, model.ErrPlatformGenerationChannelInUse)
	current, err := LoadProviderOnboardingCredentialInput(CredentialLateProviderMiniMax)
	require.NoError(t, err)
	assert.Equal(t, initialKey, current.APIKey)
	var versions int64
	require.NoError(t, db.Model(&model.ProviderChannelCredentialSetVersion{}).
		Where("channel_id = ?", created.ChannelID).Count(&versions).Error)
	assert.Equal(t, int64(1), versions)
}

func TestProviderOnboardingCredentialRotationFencesUnresolvedRouteTest(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	require.NoError(t, db.AutoMigrate(&model.PlatformChannelControlOperation{}))
	initialKey := "google-provider-management-key-one"
	created, _, err := PutProviderOnboardingCredential(
		CredentialLateProviderGoogleGemini,
		initialKey,
		"configure reviewed Google route-test account",
		"",
	)
	require.NoError(t, err)
	require.NoError(t, db.Create(&model.PlatformChannelControlOperation{
		ID: uuid.NewString(), TenantID: uuid.NewString(), OperationID: "provider-route-test-pending-0001",
		ChannelID: created.ChannelID, Kind: model.PlatformChannelControlOperationKindTest,
		State: model.PlatformChannelControlOperationPending, RequestID: "provider-route-test-request",
		Actor: "provider-admin", Reason: "verify credential rotation barrier",
		IntentSHA256: strings.Repeat("a", 64), IntentJSON: `{}`,
		IntentPublicModelID: "veo-3.1", IntentRouteID: "provider-route-test-veo",
		ProviderSubmissionState: model.PlatformChannelTestSubmissionUnknown,
		CreatedAt:               time.Now().UTC(),
	}).Error)

	_, _, err = PutProviderOnboardingCredential(
		CredentialLateProviderGoogleGemini,
		"google-provider-management-key-two",
		"rotate reviewed Google route-test account",
		created.ControlRevision,
	)
	assert.ErrorIs(t, err, model.ErrPlatformGenerationChannelInUse)
	current, err := LoadProviderOnboardingCredentialInput(CredentialLateProviderGoogleGemini)
	require.NoError(t, err)
	assert.Equal(t, initialKey, current.APIKey)
}

func TestProviderOnboardingResumeRequiresCurrentAcceptedRouteAndKeepsNativeAbilitiesDisabled(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	key := "volcengine-provider-management-key-one"
	created, _, err := PutProviderOnboardingCredential(
		CredentialLateProviderVolcengineArk,
		key,
		"configure reviewed Volcengine primary account",
		"",
	)
	require.NoError(t, err)
	_, _, err = SetProviderOnboardingEnabled(
		CredentialLateProviderVolcengineArk,
		"resume reviewed Volcengine primary account",
		created.ControlRevision,
		true,
	)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingRouteNotReady)

	definition, err := providerOnboardingManagementDefinitionFor(CredentialLateProviderVolcengineArk)
	require.NoError(t, err)
	expectedRoute := model.ProviderOnboardingExpectedRoute{
		RouteKey: "ready-volcengine-primary", Model: definition.Spec.PublicModels[0], Mode: "text_to_video",
		ProviderName: CredentialLateProviderVolcengineArk, AccountID: "primary",
		ChannelID: created.ChannelID, AcceptedChannelType: definition.Spec.ChannelType, KeyIndex: 0,
		KeyFingerprint: credentialLateProviderFingerprint(key), ChannelClass: PlatformChannelClassOfficial,
		UpstreamModel: "seedance-test-provider-model", CapabilityProfileID: "seedance-test-profile",
		CapabilityProfileRevision: strings.Repeat("a", 64), ModelReleaseID: "seedance-test-release",
		ModelReleaseRevision: strings.Repeat("b", 64), ModelReleaseCapabilityRevision: strings.Repeat("c", 64),
		StagingReady: true, RPMWindowSeconds: 60, RPMLimit: 2, ActiveLimit: 1,
		AcceptanceDigest:    "sha256:" + strings.Repeat("d", 64),
		AcceptanceNotBefore: time.Now().UTC().Add(-time.Minute),
		AcceptanceNotAfter:  time.Now().UTC().Add(time.Hour),
	}
	originalExpectedRoutes := providerOnboardingExpectedRoutesForEnable
	providerOnboardingExpectedRoutesForEnable = func(providerOnboardingManagementDefinition) ([]model.ProviderOnboardingExpectedRoute, error) {
		return []model.ProviderOnboardingExpectedRoute{expectedRoute}, nil
	}
	originalEvidenceProjection := providerOnboardingModelReleaseEvidenceProjection
	providerOnboardingModelReleaseEvidenceProjection = func() (PlatformModelReleaseEvidenceProjection, error) {
		return PlatformModelReleaseEvidenceProjection{
			SchemaVersion: 1, Object: "relay.model_release_evidence", GeneratedAt: time.Now().UTC(),
			TestFreshnessMaxAgeSeconds: 3600,
		}, nil
	}
	t.Cleanup(func() {
		providerOnboardingExpectedRoutesForEnable = originalExpectedRoutes
		providerOnboardingModelReleaseEvidenceProjection = originalEvidenceProjection
	})
	input := CredentialLateProviderInput{
		SchemaVersion: CredentialLateProviderSchemaVersion,
		Provider:      definition.Spec.Provider, Region: definition.Spec.Region,
		AccountID: definition.Spec.AccountID, ChannelID: definition.Spec.ChannelID,
		APIKey: key, PublicModelIDs: append([]string(nil), definition.Spec.PublicModels...),
	}
	readyMarker, err := credentialLateProviderMarkerJSON(input, model.ProviderOnboardingStateRouteReady)
	require.NoError(t, err)
	require.NoError(t, db.Model(&model.Channel{}).Where("id = ?", created.ChannelID).
		Update("other_info", readyMarker).Error)
	accountState := model.PlatformGenerationProviderAccountState{
		ChannelID: created.ChannelID, KeyIndex: 0, KeyFingerprint: credentialLateProviderFingerprint(key),
		RPMWindowSeconds: 60, RPMLimit: 2, ActiveLimit: 1,
	}
	require.NoError(t, db.Create(&accountState).Error)
	require.NoError(t, db.Create(&model.PlatformGenerationProviderRoute{
		RouteKey: "ready-volcengine-primary", Model: definition.Spec.PublicModels[0], Mode: "text_to_video",
		ProviderName: CredentialLateProviderVolcengineArk, AccountID: "primary",
		ChannelID: created.ChannelID, AcceptedChannelType: expectedRoute.AcceptedChannelType, KeyIndex: 0,
		KeyFingerprint: credentialLateProviderFingerprint(key), ChannelClass: expectedRoute.ChannelClass,
		UpstreamModel: expectedRoute.UpstreamModel, CapabilityProfileID: expectedRoute.CapabilityProfileID,
		CapabilityProfileRevision: expectedRoute.CapabilityProfileRevision,
		ModelReleaseID:            expectedRoute.ModelReleaseID, ModelReleaseRevision: expectedRoute.ModelReleaseRevision,
		ModelReleaseCapabilityRevision: expectedRoute.ModelReleaseCapabilityRevision,
		StagingReady:                   true, Enabled: true, AccountStateID: accountState.ID,
		RPMWindowSeconds: 60, RPMLimit: 2, ActiveLimit: 1,
	}).Error)
	declarationOnlyCatalog, err := ListProviderOnboardingProviders()
	require.NoError(t, err)
	declarationOnly := providerOnboardingViewByID(t, declarationOnlyCatalog, CredentialLateProviderVolcengineArk)
	assert.True(t, declarationOnly.RouteDeclarationVerified)
	assert.True(t, declarationOnly.RouteDeclarationFresh)
	require.NotNil(t, declarationOnly.RouteDeclarationValidUntil)
	assert.WithinDuration(t, expectedRoute.AcceptanceNotAfter, *declarationOnly.RouteDeclarationValidUntil, time.Second)
	assert.False(t, declarationOnly.RouteTestVerified)
	assert.False(t, declarationOnly.RouteTestFresh)
	assert.Nil(t, declarationOnly.RouteTestLatestAt)
	assert.False(t, declarationOnly.AcceptanceVerified, "a signed route declaration is not a provider route test")
	assert.False(t, declarationOnly.AcceptanceFresh)
	assert.Nil(t, declarationOnly.AcceptanceValidUntil)

	completedAt := time.Now().UTC().Add(-time.Minute)
	freshUntil := completedAt.Add(time.Hour)
	providerOnboardingModelReleaseEvidenceProjection = func() (PlatformModelReleaseEvidenceProjection, error) {
		return PlatformModelReleaseEvidenceProjection{
			SchemaVersion: 1, Object: "relay.model_release_evidence", GeneratedAt: time.Now().UTC(),
			TestFreshnessMaxAgeSeconds: 3600,
			Models: []PlatformModelReleaseEvidence{{
				PublicModelID: expectedRoute.Model,
				Routes: []PlatformModelReleaseRouteEvidence{{
					RouteID: expectedRoute.RouteKey, ChannelID: expectedRoute.ChannelID,
					UpstreamModel:          expectedRoute.UpstreamModel,
					AdapterProfileID:       expectedRoute.CapabilityProfileID,
					AdapterProfileRevision: expectedRoute.CapabilityProfileRevision,
					Fresh:                  true, LatestSuccessfulTestAt: &completedAt, FreshUntil: &freshUntil,
				}},
			}},
		}, nil
	}
	catalog, err := ListProviderOnboardingProviders()
	require.NoError(t, err)
	ready := providerOnboardingViewByID(t, catalog, CredentialLateProviderVolcengineArk)
	assert.Equal(t, "disabled", ready.LifecycleState)
	assert.True(t, ready.RouteTestReady)
	assert.True(t, ready.RouteMaterialized)
	assert.True(t, ready.AcceptanceVerified)
	assert.True(t, ready.AcceptanceFresh)
	require.NotNil(t, ready.AcceptanceValidUntil)
	assert.WithinDuration(t, freshUntil, *ready.AcceptanceValidUntil, time.Second)
	assert.True(t, ready.RouteTestVerified)
	assert.True(t, ready.RouteTestFresh)
	require.NotNil(t, ready.RouteTestLatestAt)
	assert.WithinDuration(t, completedAt, *ready.RouteTestLatestAt, time.Second)
	assert.Equal(t, "not_recorded", ready.PlatformPublicationStatus)
	assert.True(t, ready.CanResume)
	assert.False(t, ready.CanDisable)
	assert.Contains(t, ready.AvailableActions, "resume")
	assert.Equal(t, "resume", ready.NextAction)
	assert.Empty(t, ready.BlockingReason)

	resumed, changed, err := SetProviderOnboardingEnabled(
		CredentialLateProviderVolcengineArk,
		"resume reviewed Volcengine primary account",
		ready.ControlRevision,
		true,
	)
	require.NoError(t, err)
	assert.True(t, changed)
	assert.Equal(t, "route_test_ready", resumed.LifecycleState)
	assert.Equal(t, "enabled", resumed.ChannelStatus)
	assert.True(t, resumed.CanDisable)
	assert.False(t, resumed.CanResume)
	assert.Contains(t, resumed.AvailableActions, "disable")
	assert.Equal(t, "complete_platform_publication", resumed.NextAction)
	assert.Equal(t, "platform_publication_not_recorded", resumed.BlockingReason)
	var enabledAbilities int64
	require.NoError(t, db.Model(&model.Ability{}).
		Where("channel_id = ? AND enabled = ?", created.ChannelID, true).
		Count(&enabledAbilities).Error)
	assert.Zero(t, enabledAbilities)

	// An already-enabled replay is still a live proof check, not a blind
	// idempotent success. Profile drift and an extra enabled mode both close it.
	replayed, changed, err := SetProviderOnboardingEnabled(
		CredentialLateProviderVolcengineArk,
		"recheck reviewed Volcengine primary account",
		resumed.ControlRevision,
		true,
	)
	require.NoError(t, err)
	assert.False(t, changed)
	assert.Equal(t, resumed.ControlRevision, replayed.ControlRevision)
	require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).
		Where("route_key = ? AND mode = ?", expectedRoute.RouteKey, expectedRoute.Mode).
		Update("capability_profile_revision", strings.Repeat("d", 64)).Error)
	staleCatalog, err := ListProviderOnboardingProviders()
	require.NoError(t, err)
	staleView := providerOnboardingViewByID(t, staleCatalog, CredentialLateProviderVolcengineArk)
	assert.Equal(t, "route_evidence_stale", staleView.BlockerCode)
	assert.True(t, staleView.CanDisable, "route drift must never hide emergency disable")
	assert.Empty(t, staleView.ActionBlockerCode)
	assert.Equal(t, "disable", staleView.NextAction)
	assert.Equal(t, "route_evidence_stale", staleView.BlockingReason)
	_, _, err = SetProviderOnboardingEnabled(
		CredentialLateProviderVolcengineArk,
		"reject a stale Volcengine profile binding",
		resumed.ControlRevision,
		true,
	)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingRouteNotReady)
	require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).
		Where("route_key = ? AND mode = ?", expectedRoute.RouteKey, expectedRoute.Mode).
		Update("capability_profile_revision", expectedRoute.CapabilityProfileRevision).Error)
	require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).
		Where("route_key = ? AND mode = ?", expectedRoute.RouteKey, expectedRoute.Mode).
		Update("rpm_limit", expectedRoute.RPMLimit+1).Error)
	_, _, err = SetProviderOnboardingEnabled(
		CredentialLateProviderVolcengineArk,
		"reject drifted signed route admission policy",
		resumed.ControlRevision,
		true,
	)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingRouteNotReady)
	require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).
		Where("route_key = ? AND mode = ?", expectedRoute.RouteKey, expectedRoute.Mode).
		Update("rpm_limit", expectedRoute.RPMLimit).Error)
	require.NoError(t, db.Model(&model.PlatformGenerationProviderAccountState{}).
		Where("id = ?", accountState.ID).Update("active_limit", expectedRoute.ActiveLimit+1).Error)
	_, _, err = SetProviderOnboardingEnabled(
		CredentialLateProviderVolcengineArk,
		"reject drifted physical account admission policy",
		resumed.ControlRevision,
		true,
	)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingRouteNotReady)
	require.NoError(t, db.Model(&model.PlatformGenerationProviderAccountState{}).
		Where("id = ?", accountState.ID).Update("active_limit", expectedRoute.ActiveLimit).Error)
	extra := model.PlatformGenerationProviderRoute{
		RouteKey: expectedRoute.RouteKey, Model: expectedRoute.Model, Mode: "image_to_video",
		ProviderName: expectedRoute.ProviderName, AccountID: expectedRoute.AccountID,
		ChannelID: expectedRoute.ChannelID, AcceptedChannelType: expectedRoute.AcceptedChannelType,
		KeyIndex: 0, KeyFingerprint: expectedRoute.KeyFingerprint, ChannelClass: expectedRoute.ChannelClass,
		UpstreamModel: expectedRoute.UpstreamModel, CapabilityProfileID: expectedRoute.CapabilityProfileID,
		CapabilityProfileRevision: expectedRoute.CapabilityProfileRevision, ModelReleaseID: expectedRoute.ModelReleaseID,
		ModelReleaseRevision:           expectedRoute.ModelReleaseRevision,
		ModelReleaseCapabilityRevision: expectedRoute.ModelReleaseCapabilityRevision,
		StagingReady:                   true, Enabled: true,
	}
	require.NoError(t, db.Create(&extra).Error)
	_, _, err = SetProviderOnboardingEnabled(
		CredentialLateProviderVolcengineArk,
		"reject an unreviewed extra Volcengine mode",
		resumed.ControlRevision,
		true,
	)
	assert.ErrorIs(t, err, model.ErrProviderOnboardingRouteNotReady)
	require.NoError(t, db.Delete(&extra).Error)

	disabled, changed, err := SetProviderOnboardingEnabled(
		CredentialLateProviderVolcengineArk,
		"disable reviewed Volcengine primary account",
		resumed.ControlRevision,
		false,
	)
	require.NoError(t, err)
	assert.True(t, changed)
	assert.Equal(t, "disabled", disabled.LifecycleState)
	assert.Equal(t, "manually_disabled", disabled.ChannelStatus)
}
