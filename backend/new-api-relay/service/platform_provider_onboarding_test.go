package service

import (
	"fmt"
	"os"
	"path/filepath"
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

func setupCredentialLateProviderTest(t *testing.T) *gorm.DB {
	t.Helper()
	previousDB := model.DB
	previousType := common.MainDatabaseType()
	db, err := gorm.Open(sqlite.Open("file:provider-onboarding-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	model.DB = db
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "staging")
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	keyringJSON := []byte(`{"schema_version":1,"active_key_id":"provider-onboarding-v1","keys":{"provider-onboarding-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)
	keyringPath := filepath.Join(t.TempDir(), "provider-keyring.json")
	require.NoError(t, os.WriteFile(keyringPath, keyringJSON, 0o600))
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", keyringPath)
	previousInline, inlinePresent := os.LookupEnv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON")
	require.NoError(t, os.Unsetenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON"))
	t.Cleanup(func() {
		if inlinePresent {
			require.NoError(t, os.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", previousInline))
		} else {
			require.NoError(t, os.Unsetenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON"))
		}
	})
	require.NoError(t, common.InstallProtectedSecretFileSnapshots([]common.ProtectedSecretFileSnapshot{{
		Environment: "RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE",
		Value:       keyringJSON,
	}}))
	require.NoError(t, db.AutoMigrate(
		&model.Channel{}, &model.ProviderChannelCredentialSetVersion{}, &model.Ability{},
		&model.PlatformGenerationProviderRoute{}, &model.PlatformGenerationProviderAccountState{},
	))
	t.Cleanup(func() {
		model.DB = previousDB
		common.SetMainDatabaseType(previousType)
	})
	return db
}

func acceptedCredentialLateProviderRoute(t *testing.T, input CredentialLateProviderInput) PlatformRelayRouteDeclaration {
	t.Helper()
	definitions, err := credentialLateProviderDefinitions()
	require.NoError(t, err)
	definition := definitions[input.Provider]
	providerModel := definition.Models[input.PublicModelIDs[0]]
	profile, ok := generationprofile.Get(providerModel.AdapterProfileID)
	require.True(t, ok)
	return PlatformRelayRouteDeclaration{
		RouteID: "accepted-" + input.PublicModelIDs[0], ProviderName: input.Provider, AccountID: input.AccountID,
		ChannelID: input.ChannelID, NativeChannelType: definition.ChannelType, KeyIndex: 0,
		KeyFingerprint: credentialLateProviderFingerprint(input.APIKey), ChannelClass: PlatformChannelClassOfficial,
		UpstreamModel: providerModel.ProviderModelID, RPMLimit: 2, ActiveTaskLimit: 1,
		Capabilities: profile.Capability, CapabilityProfile: profile.ID,
		Acceptance:                  &PlatformRouteAcceptanceEvidence{Signature: "verified-by-parser-fixture"},
		AcceptanceDigest:            "sha256:" + fmt.Sprintf("%064x", 1),
		AcceptanceNotBefore:         time.Now().UTC().Add(-time.Minute),
		AcceptanceNotAfter:          time.Now().UTC().Add(time.Hour),
		StagingReady:                true,
		ResolvedCapabilityProfileID: profile.ID, ResolvedCapabilityProfileRevision: profile.Revision,
		ResolvedModelReleaseID:          "accepted-release-" + input.PublicModelIDs[0],
		ResolvedModelReleaseRevision:    "sha256:" + fmt.Sprintf("%064x", 2),
		ResolvedModelCapabilityRevision: "sha256:" + fmt.Sprintf("%064x", 3),
	}
}

func TestCredentialLateProviderExpectedRoutesBindEveryModeAndFreshEnvironment(t *testing.T) {
	setupCredentialLateProviderTest(t)
	input := CredentialLateProviderInput{SchemaVersion: 1, Provider: CredentialLateProviderMiniMax, Region: "cn", AccountID: "minimax-account", ChannelID: 35008, APIKey: "real-shaped-minimax-key", PublicModelIDs: []string{"minimax-h3"}}
	definitions, err := credentialLateProviderDefinitions()
	require.NoError(t, err)
	route := acceptedCredentialLateProviderRoute(t, input)
	routes := map[string][]PlatformRelayRouteDeclaration{"minimax-h3": {route}}
	expected, err := credentialLateProviderExpectedRoutes(input, definitions[input.Provider], routes, "staging")
	require.NoError(t, err)
	assert.Len(t, expected, len(route.Capabilities.Modes))
	for _, item := range expected {
		assert.Equal(t, route.RouteID, item.RouteKey)
		assert.Equal(t, route.ResolvedCapabilityProfileRevision, item.CapabilityProfileRevision)
		assert.Equal(t, route.ResolvedModelReleaseRevision, item.ModelReleaseRevision)
		assert.True(t, item.StagingReady)
		assert.False(t, item.ProductionReady)
	}

	route.AcceptanceNotAfter = platformRouteAcceptanceNow().Add(-time.Second)
	_, err = credentialLateProviderExpectedRoutes(input, definitions[input.Provider], map[string][]PlatformRelayRouteDeclaration{"minimax-h3": {route}}, "staging")
	require.ErrorContains(t, err, "stale")
	route.AcceptanceNotAfter = platformRouteAcceptanceNow().Add(time.Hour)
	route.StagingReady = false
	_, err = credentialLateProviderExpectedRoutes(input, definitions[input.Provider], map[string][]PlatformRelayRouteDeclaration{"minimax-h3": {route}}, "staging")
	require.ErrorContains(t, err, "another environment")
}

func TestCredentialLateProviderRejectsSignedRouteMissingReviewedMode(t *testing.T) {
	setupCredentialLateProviderTest(t)
	input := CredentialLateProviderInput{
		SchemaVersion: 1, Provider: CredentialLateProviderMiniMax, Region: "cn",
		AccountID: "minimax-account", ChannelID: 35018,
		APIKey: "real-shaped-minimax-completeness-key", PublicModelIDs: []string{"minimax-h3"},
	}
	definitions, err := credentialLateProviderDefinitions()
	require.NoError(t, err)
	route := acceptedCredentialLateProviderRoute(t, input)
	require.Greater(t, len(route.Capabilities.Modes), 1)
	narrowed := make(map[string]dto.PlatformModeCapability, len(route.Capabilities.Modes)-1)
	skipped := false
	for mode, capability := range route.Capabilities.Modes {
		if !skipped {
			skipped = true
			continue
		}
		narrowed[mode] = capability
	}
	route.Capabilities.Modes = narrowed
	_, err = validateCredentialLateProviderRoutes(
		input, definitions[input.Provider],
		map[string][]PlatformRelayRouteDeclaration{"minimax-h3": {route}}, true,
	)
	require.ErrorContains(t, err, "complete reviewed mode set")
}

func TestCredentialLateProviderFinalizeRejectsCredentialFenceRotationWithoutRouteWrites(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	input := CredentialLateProviderInput{SchemaVersion: 1, Provider: CredentialLateProviderMiniMax, Region: "cn", AccountID: "minimax-account", ChannelID: 35009, APIKey: "real-shaped-minimax-key-a", PublicModelIDs: []string{"minimax-h3"}}
	_, err := StageCredentialLateProvider(input, "staging")
	require.NoError(t, err)
	definitions, err := credentialLateProviderDefinitions()
	require.NoError(t, err)
	channel, state, err := loadCredentialLateProviderChannel(input, definitions[input.Provider])
	require.NoError(t, err)
	assert.Equal(t, "staged", state)
	fence, err := credentialLateProviderFenceWithDB(db, channel)
	require.NoError(t, err)
	route := acceptedCredentialLateProviderRoute(t, input)

	require.NoError(t, model.RotateChannelCredentialSet(input.ChannelID, "real-shaped-minimax-key-b"))
	err = finalizeCredentialLateProviderWithFence(
		input, definitions[input.Provider], map[string][]PlatformRelayRouteDeclaration{"minimax-h3": {route}},
		state, fence, false, "staging",
	)
	require.ErrorContains(t, err, "changed before finalization")
	var routes int64
	require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).Count(&routes).Error)
	assert.Zero(t, routes)
	var stored model.Channel
	require.NoError(t, db.Where("id = ?", input.ChannelID).First(&stored).Error)
	assert.Equal(t, common.ChannelStatusManuallyDisabled, stored.Status)
	assert.Equal(t, "real-shaped-minimax-key-b", stored.Key)
}

func TestCredentialLateProviderFinalizeRechecksAcceptanceAtCommit(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	input := CredentialLateProviderInput{
		SchemaVersion: 1, Provider: CredentialLateProviderMiniMax, Region: "cn",
		AccountID: "minimax-account", ChannelID: 35019,
		APIKey: "real-shaped-minimax-expiry-key", PublicModelIDs: []string{"minimax-h3"},
	}
	_, err := StageCredentialLateProvider(input, "staging")
	require.NoError(t, err)
	definitions, err := credentialLateProviderDefinitions()
	require.NoError(t, err)
	channel, state, err := loadCredentialLateProviderChannel(input, definitions[input.Provider])
	require.NoError(t, err)
	fence, err := credentialLateProviderFenceWithDB(db, channel)
	require.NoError(t, err)
	route := acceptedCredentialLateProviderRoute(t, input)

	previousNow := platformRouteAcceptanceNow
	platformRouteAcceptanceNow = func() time.Time { return route.AcceptanceNotAfter }
	t.Cleanup(func() { platformRouteAcceptanceNow = previousNow })
	err = finalizeCredentialLateProviderWithFence(
		input, definitions[input.Provider], map[string][]PlatformRelayRouteDeclaration{"minimax-h3": {route}},
		state, fence, false, "staging",
	)
	require.ErrorContains(t, err, "expired or changed before finalization")
	var routeCount int64
	require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).Count(&routeCount).Error)
	assert.Zero(t, routeCount)
	var persisted model.Channel
	require.NoError(t, db.First(&persisted, input.ChannelID).Error)
	assert.Equal(t, common.ChannelStatusManuallyDisabled, persisted.Status)
}

func useCredentialLateProviderDevelopmentRuntime(t *testing.T) {
	t.Helper()
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "false")
	t.Setenv("RELAY_SECRET_ISOLATION_GENERATION", "")
	t.Setenv("RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE", "")
	t.Setenv("RELAY_SECRET_ISOLATION_RECEIPT_FILE", "")
	t.Setenv("RELAY_SECRET_ISOLATION_COMMIT_FILE", "")
}

func TestCredentialLateProviderDefinitionsCoverExactFamiliesAndSeedream(t *testing.T) {
	definitions, err := credentialLateProviderDefinitions()
	require.NoError(t, err)
	assert.Equal(t, constant.ChannelTypeVolcEngine, definitions[CredentialLateProviderVolcengineArk].ChannelType)
	assert.Equal(t, constant.ChannelTypeMiniMax, definitions[CredentialLateProviderMiniMax].ChannelType)
	assert.Equal(t, constant.ChannelTypeGemini, definitions[CredentialLateProviderGoogleGemini].ChannelType)
	for provider, modelIDs := range map[string][]string{
		CredentialLateProviderVolcengineArk: {"seedream-5", "seedance-2.5"},
		CredentialLateProviderMiniMax:       {"minimax-h3", "minimax-h3-max"},
		CredentialLateProviderGoogleGemini:  {"gemini-omni-1.1-flash", "veo-3.1", "veo-3.1-fast"},
	} {
		for _, modelID := range modelIDs {
			assert.Contains(t, definitions[provider].Models, modelID)
		}
	}
	assert.NotContains(t, definitions[CredentialLateProviderVolcengineArk].Models, "seedance-1.5-pro")
}

func TestDevelopmentRoutePlanUsesCompiledFactsPreservesInventoryAndNeverEmitsKey(t *testing.T) {
	setupCredentialLateProviderTest(t)
	useCredentialLateProviderDevelopmentRuntime(t)
	input := CredentialLateProviderInput{
		SchemaVersion: 1, Provider: CredentialLateProviderGoogleGemini,
		AccountID: "google-development-plan", ChannelID: 24010,
		APIKey: "real-shaped-google-development-plan-key", PublicModelIDs: []string{"veo-3.1"},
	}
	_, err := StageCredentialLateProvider(input, "development")
	require.NoError(t, err)

	legacyProfile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	require.True(t, ok)
	current := map[string][]PlatformRelayRouteDeclaration{
		constant.PlatformGenerationPublicSeedream50Model: {{
			RouteID: "preserved-seedream-route", ProviderName: CredentialLateProviderVolcengineArk,
			AccountID: "existing-ark-account", ChannelID: 45010, NativeChannelType: constant.ChannelTypeVolcEngine,
			KeyIndex: 0, KeyFingerprint: fmt.Sprintf("%064x", 45010), ChannelClass: PlatformChannelClassOfficial,
			UpstreamModel: constant.PlatformGenerationArkSeedream50Model,
			RPMLimit:      3, ActiveTaskLimit: 2, Capabilities: legacyProfile.Capability,
			CapabilityProfile: legacyProfile.ID,
		}},
	}
	currentJSON, err := common.Marshal(current)
	require.NoError(t, err)
	options := CredentialLateProviderRoutePlanOptions{
		CurrentRoutesJSON: currentJSON, CreatedAt: "2026-09-02T08:30:00Z",
		CreatedBy: "development-operator", Reason: "prepare exact development acceptance routes",
		RPMLimit: 2, ActiveTaskLimit: 1,
	}

	first, err := PlanDevelopmentCredentialLateProviderRoutes(input, options)
	require.NoError(t, err)
	second, err := PlanDevelopmentCredentialLateProviderRoutes(input, options)
	require.NoError(t, err)
	firstJSON, err := common.Marshal(first)
	require.NoError(t, err)
	secondJSON, err := common.Marshal(second)
	require.NoError(t, err)
	assert.Equal(t, firstJSON, secondJSON, "identical reviewed facts and policy must produce identical inventory bytes")
	assert.NotContains(t, string(firstJSON), input.APIKey)

	require.Len(t, first[constant.PlatformGenerationPublicSeedream50Model], 1)
	assert.Equal(t, "preserved-seedream-route", first[constant.PlatformGenerationPublicSeedream50Model][0].RouteID)
	require.Len(t, first["veo-3.1"], 1)
	planned := first["veo-3.1"][0]
	definitions, err := credentialLateProviderDefinitions()
	require.NoError(t, err)
	modelFact := definitions[CredentialLateProviderGoogleGemini].Models["veo-3.1"]
	profile, ok := generationprofile.Get(modelFact.AdapterProfileID)
	require.True(t, ok)
	assert.Equal(t, modelFact.ProviderModelID, planned.UpstreamModel)
	assert.Equal(t, profile.ID, planned.CapabilityProfile)
	assert.Equal(t, generationprofile.NormalizeCapability(modelFact.Capability), planned.Capabilities)
	assert.Equal(t, credentialLateProviderFingerprint(input.APIKey), planned.KeyFingerprint)
	assert.Nil(t, planned.Acceptance)
	require.NotNil(t, planned.ModelRelease)
	assert.Nil(t, planned.ModelRelease.Attestation)
	assert.Equal(t, profile.ID, planned.ModelRelease.AdapterProfileID)
	assert.Equal(t, profile.Revision, planned.ModelRelease.AdapterProfileRevision)
	assert.Equal(t, generationprofile.NormalizeCapability(modelFact.Capability), planned.ModelRelease.Capability)
}

func TestDevelopmentRoutePlanExplicitReplacementIsConsumedOnlyWithExplicitFinalize(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	useCredentialLateProviderDevelopmentRuntime(t)
	input := CredentialLateProviderInput{
		SchemaVersion: 1, Provider: CredentialLateProviderMiniMax, Region: "global",
		AccountID: "minimax-development-plan", ChannelID: 35010,
		APIKey: "real-shaped-minimax-development-plan-key", PublicModelIDs: []string{"minimax-h3"},
	}
	_, err := StageCredentialLateProvider(input, "development")
	require.NoError(t, err)

	plan, err := PlanDevelopmentCredentialLateProviderRoutes(input, CredentialLateProviderRoutePlanOptions{
		ReplaceAllRoutes: true, CreatedAt: "2026-09-02T08:31:00Z",
		CreatedBy: "development-operator", Reason: "replace the complete isolated development route inventory",
		RPMLimit: 1, ActiveTaskLimit: 1,
	})
	require.NoError(t, err)
	planJSON, err := common.Marshal(plan)
	require.NoError(t, err)
	result, err := FinalizeCredentialLateProviderWithOptions(
		input,
		planJSON,
		"development",
		CredentialLateProviderFinalizeOptions{ReplaceAllRoutes: true},
	)
	require.NoError(t, err)
	assert.True(t, result.RouteTestReady)
	assert.Equal(t, []string{plan["minimax-h3"][0].RouteID}, result.RouteIDs)
	var routeRows int64
	require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).Where("channel_id = ?", input.ChannelID).Count(&routeRows).Error)
	assert.Equal(t, int64(len(plan["minimax-h3"][0].Capabilities.Modes)), routeRows)
}

func TestDevelopmentRoutePlanRequiresCurrentInventoryOrExplicitReplacement(t *testing.T) {
	setupCredentialLateProviderTest(t)
	useCredentialLateProviderDevelopmentRuntime(t)
	input := CredentialLateProviderInput{
		SchemaVersion: 1, Provider: CredentialLateProviderGoogleGemini,
		AccountID: "google-development-policy", ChannelID: 24011,
		APIKey: "real-shaped-google-development-policy-key", PublicModelIDs: []string{"veo-3.1-fast"},
	}
	_, err := StageCredentialLateProvider(input, "development")
	require.NoError(t, err)
	_, err = PlanDevelopmentCredentialLateProviderRoutes(input, CredentialLateProviderRoutePlanOptions{
		CreatedAt: "2026-09-02T08:32:00Z", CreatedBy: "development-operator",
		Reason: "missing route inventory must fail closed", RPMLimit: 1, ActiveTaskLimit: 1,
	})
	require.ErrorContains(t, err, "complete current inventory or explicit full replacement")
}

func TestProtectedFinalizeCannotOptOutOfExistingRoutePreservation(t *testing.T) {
	setupCredentialLateProviderTest(t)
	input := CredentialLateProviderInput{
		SchemaVersion: 1, Provider: CredentialLateProviderGoogleGemini,
		AccountID: "google-protected-replacement", ChannelID: 24012,
		APIKey: "real-shaped-google-protected-replacement-key", PublicModelIDs: []string{"veo-3.1"},
	}
	_, err := FinalizeCredentialLateProviderWithOptions(
		input,
		[]byte(`{}`),
		"staging",
		CredentialLateProviderFinalizeOptions{ReplaceAllRoutes: true},
	)
	require.ErrorContains(t, err, "restricted to development")
}

func TestCredentialLateProviderMissingOrUnsignedInputMakesNoDatabaseWrites(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	input := CredentialLateProviderInput{
		SchemaVersion: 1, Provider: CredentialLateProviderGoogleGemini, AccountID: "google-account-one",
		ChannelID: 24001, PublicModelIDs: []string{"veo-3.1"},
	}
	_, err := MaterializeCredentialLateProvider(input, []byte(`{}`), "staging")
	require.ErrorContains(t, err, "API key")
	input.APIKey = "real-shaped-google-provider-key"
	_, err = MaterializeCredentialLateProvider(input, []byte(`{"veo-3.1":[]}`), "staging")
	require.Error(t, err, "unsigned route JSON must fail the real secure parser")
	var channels, abilities, routes int64
	require.NoError(t, db.Model(&model.Channel{}).Count(&channels).Error)
	require.NoError(t, db.Model(&model.Ability{}).Count(&abilities).Error)
	require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).Count(&routes).Error)
	assert.Zero(t, channels)
	assert.Zero(t, abilities)
	assert.Zero(t, routes)
}

func TestCredentialLateProviderStagesThenFinalizesExactDisabledAbilityAndIsIdempotent(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	originalParser := parseCredentialLateProviderRoutes
	t.Cleanup(func() { parseCredentialLateProviderRoutes = originalParser })
	completeRoutes := make(map[string][]PlatformRelayRouteDeclaration)

	for _, input := range []CredentialLateProviderInput{
		{SchemaVersion: 1, Provider: CredentialLateProviderVolcengineArk, AccountID: "ark-account-one", ChannelID: 45001, APIKey: "real-shaped-ark-provider-key", PublicModelIDs: []string{"seedance-2.5"}},
		{SchemaVersion: 1, Provider: CredentialLateProviderMiniMax, Region: "global", AccountID: "minimax-account-one", ChannelID: 35001, APIKey: "real-shaped-minimax-provider-key", PublicModelIDs: []string{"minimax-h3"}},
		{SchemaVersion: 1, Provider: CredentialLateProviderGoogleGemini, AccountID: "google-account-one", ChannelID: 24001, APIKey: "real-shaped-google-provider-key", PublicModelIDs: []string{"veo-3.1"}},
	} {
		staged, err := StageCredentialLateProvider(input, "staging")
		require.NoError(t, err)
		assert.True(t, staged.ChannelCreated)
		assert.False(t, staged.RouteTestReady)
		assert.Empty(t, staged.RouteIDs)
		assert.Equal(t, "disabled_until_platform_publication", staged.NativeAbilities)
		var stagedChannel model.Channel
		require.NoError(t, db.Omit("key", "credential_set_version").First(&stagedChannel, "id = ?", input.ChannelID).Error)
		assert.Equal(t, common.ChannelStatusManuallyDisabled, stagedChannel.Status)
		var stagedAbility model.Ability
		require.NoError(t, db.First(&stagedAbility, "channel_id = ?", input.ChannelID).Error)
		assert.False(t, stagedAbility.Enabled)
		var stagedRouteRows int64
		require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).Where("channel_id = ?", input.ChannelID).Count(&stagedRouteRows).Error)
		assert.Zero(t, stagedRouteRows)

		replayedStage, err := StageCredentialLateProvider(input, "staging")
		require.NoError(t, err)
		assert.False(t, replayedStage.ChannelCreated)
		assert.False(t, replayedStage.RouteTestReady)

		route := acceptedCredentialLateProviderRoute(t, input)
		completeRoutes[input.PublicModelIDs[0]] = []PlatformRelayRouteDeclaration{route}
		parseCredentialLateProviderRoutes = func(string, string, string) (map[string]dto.PlatformGenerationCapabilities, map[string][]PlatformRelayRouteDeclaration, error) {
			return map[string]dto.PlatformGenerationCapabilities{input.PublicModelIDs[0]: route.Capabilities}, completeRoutes, nil
		}
		result, err := MaterializeCredentialLateProvider(input, []byte(`{"signed":"fixture"}`), "staging")
		require.NoError(t, err)
		assert.False(t, result.ChannelCreated)
		assert.True(t, result.RouteTestReady)
		assert.Equal(t, "disabled_until_platform_publication", result.NativeAbilities)

		var channel model.Channel
		require.NoError(t, db.Omit("key", "credential_set_version").First(&channel, "id = ?", input.ChannelID).Error)
		assert.Equal(t, common.ChannelStatusEnabled, channel.Status)
		var ability model.Ability
		require.NoError(t, db.First(&ability, "channel_id = ?", input.ChannelID).Error)
		assert.False(t, ability.Enabled)
		var routeRows int64
		require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).Where("channel_id = ?", input.ChannelID).Count(&routeRows).Error)
		assert.Equal(t, int64(len(route.Capabilities.Modes)), routeRows)

		replayed, err := MaterializeCredentialLateProvider(input, []byte(`{"signed":"fixture"}`), "staging")
		require.NoError(t, err)
		assert.False(t, replayed.ChannelCreated)
		assert.True(t, replayed.RouteTestReady)
		resumedStage, err := StageCredentialLateProvider(input, "staging")
		require.NoError(t, err)
		assert.False(t, resumedStage.ChannelCreated)
		assert.True(t, resumedStage.RouteTestReady)
		assert.Empty(t, resumedStage.RouteIDs)
		var abilityCount int64
		require.NoError(t, db.Model(&model.Ability{}).Where("channel_id = ?", input.ChannelID).Count(&abilityCount).Error)
		assert.Equal(t, int64(1), abilityCount)

	}
}

func TestCredentialLateProviderStageNeverClaimsAdministratorChannel(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	input := CredentialLateProviderInput{SchemaVersion: 1, Provider: CredentialLateProviderMiniMax, Region: "cn", AccountID: "minimax-account", ChannelID: 35002, APIKey: "real-shaped-minimax-key", PublicModelIDs: []string{"minimax-h3"}}
	baseURL := "https://api.minimax.cn"
	require.NoError(t, db.Create(&model.Channel{Id: input.ChannelID, Type: constant.ChannelTypeMiniMax, Key: input.APIKey, Name: "administrator channel", BaseURL: &baseURL, Models: "minimax-h3", Group: "default", Status: common.ChannelStatusEnabled}).Error)

	_, err := StageCredentialLateProvider(input, "staging")
	require.ErrorContains(t, err, "administrator configuration")
	var persisted model.Channel
	require.NoError(t, db.Omit("key", "credential_set_version").First(&persisted, "id = ?", input.ChannelID).Error)
	assert.Equal(t, "administrator channel", persisted.Name)
	assert.Equal(t, common.ChannelStatusEnabled, persisted.Status)
	var routes int64
	require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).Count(&routes).Error)
	assert.Zero(t, routes)
}

func TestCredentialLateProviderRejectsPartialInventoryThatWouldDisableExistingRoute(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	require.NoError(t, db.Create(&model.PlatformGenerationProviderRoute{
		RouteKey: "existing-route", Model: "existing-model", Mode: "text_to_video", ProviderName: "existing-provider",
		AccountID: "existing-account", ChannelID: 99, AcceptedChannelType: constant.ChannelTypeVolcEngine,
		KeyIndex: 0, KeyFingerprint: fmt.Sprintf("%064x", 99), ChannelClass: PlatformChannelClassOfficial,
		UpstreamModel: "existing-upstream", Enabled: true, RPMWindowSeconds: 60, RPMLimit: 1, ActiveLimit: 1,
	}).Error)
	input := CredentialLateProviderInput{SchemaVersion: 1, Provider: CredentialLateProviderGoogleGemini, AccountID: "google-account", ChannelID: 24002, APIKey: "real-shaped-google-key", PublicModelIDs: []string{"veo-3.1"}}
	route := acceptedCredentialLateProviderRoute(t, input)
	originalParser := parseCredentialLateProviderRoutes
	t.Cleanup(func() { parseCredentialLateProviderRoutes = originalParser })
	parseCredentialLateProviderRoutes = func(string, string, string) (map[string]dto.PlatformGenerationCapabilities, map[string][]PlatformRelayRouteDeclaration, error) {
		return nil, map[string][]PlatformRelayRouteDeclaration{"veo-3.1": {route}}, nil
	}
	_, err := MaterializeCredentialLateProvider(input, []byte(`{"signed":"fixture"}`), "staging")
	require.ErrorContains(t, err, "would omit enabled route")
	var channels int64
	require.NoError(t, db.Model(&model.Channel{}).Count(&channels).Error)
	assert.Zero(t, channels)
}

func TestCredentialLateProviderFinalizeRequiresMatchingStage(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	input := CredentialLateProviderInput{SchemaVersion: 1, Provider: CredentialLateProviderGoogleGemini, AccountID: "google-account", ChannelID: 24003, APIKey: "real-shaped-google-key", PublicModelIDs: []string{"veo-3.1"}}
	route := acceptedCredentialLateProviderRoute(t, input)
	originalParser := parseCredentialLateProviderRoutes
	t.Cleanup(func() { parseCredentialLateProviderRoutes = originalParser })
	parseCredentialLateProviderRoutes = func(string, string, string) (map[string]dto.PlatformGenerationCapabilities, map[string][]PlatformRelayRouteDeclaration, error) {
		return nil, map[string][]PlatformRelayRouteDeclaration{"veo-3.1": {route}}, nil
	}

	_, err := FinalizeCredentialLateProvider(input, []byte(`{"signed":"fixture"}`), "staging")
	require.ErrorContains(t, err, "must be staged before finalization")
	var channels, abilities, routes int64
	require.NoError(t, db.Model(&model.Channel{}).Count(&channels).Error)
	require.NoError(t, db.Model(&model.Ability{}).Count(&abilities).Error)
	require.NoError(t, db.Model(&model.PlatformGenerationProviderRoute{}).Count(&routes).Error)
	assert.Zero(t, channels)
	assert.Zero(t, abilities)
	assert.Zero(t, routes)
}

func TestCredentialLateProviderDevelopmentAllowsOnlyUnsignedRouteTestInventory(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "false")
	t.Setenv("RELAY_SECRET_ISOLATION_GENERATION", "")
	t.Setenv("RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE", "")
	t.Setenv("RELAY_SECRET_ISOLATION_RECEIPT_FILE", "")
	t.Setenv("RELAY_SECRET_ISOLATION_COMMIT_FILE", "")
	input := CredentialLateProviderInput{SchemaVersion: 1, Provider: CredentialLateProviderGoogleGemini, AccountID: "google-development-account", ChannelID: 24004, APIKey: "real-shaped-google-development-key", PublicModelIDs: []string{"veo-3.1"}}
	staged, err := StageCredentialLateProvider(input, "development")
	require.NoError(t, err)
	assert.True(t, staged.ChannelCreated)
	assert.False(t, staged.RouteTestReady)

	route := acceptedCredentialLateProviderRoute(t, input)
	route.Acceptance = nil
	route.AcceptanceDigest = ""
	route.AcceptanceNotBefore = time.Time{}
	route.AcceptanceNotAfter = time.Time{}
	route.StagingReady = false
	originalParser := parseCredentialLateProviderRoutes
	t.Cleanup(func() { parseCredentialLateProviderRoutes = originalParser })
	parseCredentialLateProviderRoutes = func(string, string, string) (map[string]dto.PlatformGenerationCapabilities, map[string][]PlatformRelayRouteDeclaration, error) {
		return nil, map[string][]PlatformRelayRouteDeclaration{"veo-3.1": {route}}, nil
	}
	finalized, err := FinalizeCredentialLateProvider(input, []byte(`{"reviewed":"development-only"}`), "development")
	require.NoError(t, err)
	assert.True(t, finalized.RouteTestReady)
	assert.Equal(t, "disabled_until_platform_publication", finalized.NativeAbilities)
	var ability model.Ability
	require.NoError(t, db.First(&ability, "channel_id = ?", input.ChannelID).Error)
	assert.False(t, ability.Enabled)

	route.Acceptance = &PlatformRouteAcceptanceEvidence{Signature: "not-verified-in-development"}
	parseCredentialLateProviderRoutes = func(string, string, string) (map[string]dto.PlatformGenerationCapabilities, map[string][]PlatformRelayRouteDeclaration, error) {
		return nil, map[string][]PlatformRelayRouteDeclaration{"veo-3.1": {route}}, nil
	}
	_, err = FinalizeCredentialLateProvider(input, []byte(`{"reviewed":"ambiguous"}`), "development")
	require.ErrorContains(t, err, "must not carry unverified acceptance material")
}

func TestCredentialLateProviderDevelopmentRejectsProtectedRuntime(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	input := CredentialLateProviderInput{SchemaVersion: 1, Provider: CredentialLateProviderMiniMax, Region: "cn", AccountID: "minimax-development-account", ChannelID: 35004, APIKey: "real-shaped-minimax-development-key", PublicModelIDs: []string{"minimax-h3"}}

	_, err := StageCredentialLateProvider(input, "development")
	require.ErrorContains(t, err, "non-protected development Relay runtime")
	var channels int64
	require.NoError(t, db.Model(&model.Channel{}).Count(&channels).Error)
	assert.Zero(t, channels)
}

func TestCredentialLateProviderNeverClaimsAdministratorChannel(t *testing.T) {
	db := setupCredentialLateProviderTest(t)
	input := CredentialLateProviderInput{SchemaVersion: 1, Provider: CredentialLateProviderMiniMax, Region: "cn", AccountID: "minimax-account", ChannelID: 35002, APIKey: "real-shaped-minimax-key", PublicModelIDs: []string{"minimax-h3"}}
	baseURL := "https://api.minimax.cn"
	require.NoError(t, db.Create(&model.Channel{Id: input.ChannelID, Type: constant.ChannelTypeMiniMax, Key: input.APIKey, Name: "administrator channel", BaseURL: &baseURL, Models: "minimax-h3", Group: "default", Status: common.ChannelStatusEnabled}).Error)
	originalParser := parseCredentialLateProviderRoutes
	t.Cleanup(func() { parseCredentialLateProviderRoutes = originalParser })
	route := acceptedCredentialLateProviderRoute(t, input)
	parseCredentialLateProviderRoutes = func(string, string, string) (map[string]dto.PlatformGenerationCapabilities, map[string][]PlatformRelayRouteDeclaration, error) {
		return nil, map[string][]PlatformRelayRouteDeclaration{"minimax-h3": {route}}, nil
	}
	_, err := MaterializeCredentialLateProvider(input, []byte(`{"signed":"fixture"}`), "staging")
	require.ErrorContains(t, err, "administrator configuration")
	var persisted model.Channel
	require.NoError(t, db.Omit("key", "credential_set_version").First(&persisted, "id = ?", input.ChannelID).Error)
	assert.Equal(t, "administrator channel", persisted.Name)
	assert.Equal(t, common.ChannelStatusEnabled, persisted.Status)
}
