package localvideoconfig_test

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
	"github.com/QuantumNous/new-api/localvideoconfig"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func localOptions() localvideoconfig.Options {
	return localvideoconfig.Options{
		Mode: localvideoconfig.ModeMock, Environment: localvideoconfig.EnvironmentDevelopment,
		Namespace: "isolated-video-contract-test", Now: time.Date(2026, 8, 31, 12, 0, 0, 0, time.UTC),
		CreatedBy: "local-config-unit-test", Reason: "Development fixture; not provider acceptance or production approval",
		RuntimeSeed: []byte("unit-test-runtime-seed-with-at-least-32-bytes"),
		CallbackURL: "http://127.0.0.1:8200/internal/relay-callbacks/new-api-v1",
	}
}

func liveOptions() localvideoconfig.Options {
	options := localOptions()
	options.Mode = localvideoconfig.ModeLive
	options.Keys = localvideoconfig.ProviderKeys{
		Ark: "unit-live-ark-provider-secret-000001", MiniMax: "unit-live-minimax-provider-secret-000002",
	}
	return options
}

func TestBuildUsesExactlyEligibleCompiledModelsAndPerModeContracts(t *testing.T) {
	config, err := localvideoconfig.Build(localOptions())
	require.NoError(t, err)
	require.NoError(t, localvideoconfig.Validate(config))
	expected := map[string]string{
		"seedance-2.5": "doubao-seedance-2-5-260628", "seedance-2.0": "doubao-seedance-2-0-260128",
		"seedance-2.0-fast": "doubao-seedance-2-0-fast-260128", "seedance-2.0-mini": "doubao-seedance-2-0-mini-260615",
		"seedance-1.0-pro": "doubao-seedance-1-0-pro-250528", "seedance-1.0-pro-fast": "doubao-seedance-1-0-pro-fast-251015",
		"minimax-h3": "MiniMax-H3", "minimax-h3-max": "MiniMax-H3-Max",
	}
	require.Len(t, config.Models, 8)
	require.Len(t, config.Routes, 8)
	require.Len(t, config.Channels, 2)
	modeCount := 0
	for _, model := range config.Models {
		assert.Equal(t, expected[model.PublicModelID], model.ProviderModelID)
		assert.NotEmpty(t, model.OfficialSources)
		assert.NotEmpty(t, model.CompatibilityNotes)
		profile, found := generationprofile.Get(model.Profile.ID)
		require.True(t, found)
		assert.Equal(t, profile, model.Profile)
		require.NoError(t, model.Release.Validate(profile))
		assert.Nil(t, model.Release.Attestation, "development preparation cannot attest a provider account")
		assert.Empty(t, model.Release.LegacyPublicAliases)
		assert.Equal(t, model.CatalogSource, model.Release.Audit.SourceRef)
		assert.Equal(t, model.PublicModelID, model.Release.PublicModelID)
		assert.Equal(t, model.ProviderModelID, model.Release.ProviderModelID)
		revision, err := model.Release.CapabilityRevision()
		require.NoError(t, err)
		assert.Equal(t, revision, model.ReleaseCapabilityRevision)
		var compiledCapability dto.PlatformGenerationCapabilities
		if model.ProviderName == "minimax" {
			manifest, found, err := generationprofile.ResolveMiniMaxH3ProviderModel(model.ProviderModelID)
			require.NoError(t, err)
			require.True(t, found)
			assert.True(t, manifest.NewRoutesAllowed)
			compiledCapability = manifest.CompatibleCapability()
		} else {
			manifest, found, err := generationprofile.ResolveSeedanceProviderModel(model.ProviderModelID)
			require.NoError(t, err)
			require.True(t, found)
			assert.True(t, manifest.NewRoutesAllowed)
			require.NoError(t, manifest.ValidateSubmissionAt(localOptions().Now))
			compiledCapability = manifest.CompatibleCapability()
		}
		assert.Equal(t, generationprofile.NormalizeCapability(compiledCapability), model.Capability)
		routes := config.Routes[model.PublicModelID]
		require.Len(t, routes, 1, "all modes must remain in the one capability intersection")
		route := routes[0]
		assert.Equal(t, model.ProviderModelID, route.UpstreamModel)
		assert.Equal(t, model.Profile.NativeChannelType, route.NativeChannelType)
		assert.Equal(t, model.Profile.ID, route.CapabilityProfile)
		assert.Equal(t, model.Capability, route.Capabilities)
		require.NotNil(t, route.ModelRelease)
		assert.Equal(t, model.Release, *route.ModelRelease)
		assert.Positive(t, route.RPMLimit)
		assert.Positive(t, route.ActiveTaskLimit)
		assert.Contains(t, route.ProviderName, "mock-")
		modeCount += len(route.Capabilities.Modes)
	}
	assert.Equal(t, 20, modeCount, "the eight models have twenty executable model/mode routes")
	assert.False(t, config.Summary().ProductionReady)
	assert.Equal(t, "mock_local_only", config.Summary().Status)
	assert.Equal(t, localvideoconfig.ArkBaseURL, config.Channels[0].BaseURL)
	assert.Equal(t, constant.ChannelTypeVolcEngine, config.Channels[0].Type)
	assert.Equal(t, localvideoconfig.MiniMaxChinaBaseURL, config.Channels[1].BaseURL)
	assert.Equal(t, constant.ChannelTypeMiniMax, config.Channels[1].Type)
	assert.NotEqual(t, config.Channels[0].ID, config.Channels[1].ID)
	assert.Equal(t, localvideoconfig.MockArkProviderKey, config.Channels[0].Key)
	assert.Equal(t, localvideoconfig.MockMiniMaxProviderKey, config.Channels[1].Key)
}

func TestPreparedRoutesAreAcceptedByTheRealRelayCatalogParser(t *testing.T) {
	dockerOptions := localOptions()
	dockerOptions.IsolatedDocker = true
	dockerOptions.CallbackURL = "http://platform-lab:8000/internal/relay-callbacks/new-api-v1"
	for _, options := range []localvideoconfig.Options{localOptions(), liveOptions(), dockerOptions} {
		name := options.Mode
		if options.IsolatedDocker {
			name += "-isolated-docker"
		}
		t.Run(name, func(t *testing.T) {
			config, err := localvideoconfig.Build(options)
			require.NoError(t, err)
			environment, err := config.RuntimeEnvironment()
			require.NoError(t, err)
			for key, value := range environment {
				t.Setenv(key, value)
			}
			t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON", "")
			t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY", "")
			t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON", "")
			t.Setenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY", "")
			catalog, err := service.GetPlatformRelayModelCatalog()
			require.NoError(t, err)
			require.Len(t, catalog.Data, 11)
			resources := make(map[string]dto.PlatformModelResource, len(catalog.Data))
			for _, resource := range catalog.Data {
				resources[resource.ID] = resource
			}
			for _, configured := range config.Models {
				resource, exists := resources[configured.PublicModelID]
				require.True(t, exists)
				assert.Equal(t, configured.Capability, resource.Capabilities)
				assert.Equal(t, configured.CapabilityRevision, resource.CapabilityRevision)
				for _, mode := range configured.Modes {
					routes, err := service.GetPlatformRelayRoutes(configured.PublicModelID, mode)
					require.NoError(t, err)
					require.Len(t, routes, 1)
					assert.False(t, routes[0].StagingReady)
					assert.False(t, routes[0].ProductionReady)
					assert.Nil(t, routes[0].Acceptance)
				}
			}
			require.NoError(t, service.ValidatePlatformGenerationOperationsConfiguration())
		})
	}
}

func TestPreparedConfigurationCannotBecomeProductionAuthority(t *testing.T) {
	config, err := localvideoconfig.Build(localOptions())
	require.NoError(t, err)
	environment, err := config.RuntimeEnvironment()
	require.NoError(t, err)
	for key, value := range environment {
		t.Setenv(key, value)
	}
	for _, secureSignal := range []string{"APP_ENV", "DEPLOYMENT_ENV", "ENVIRONMENT"} {
		t.Run("ambient-"+secureSignal, func(t *testing.T) {
			t.Setenv(secureSignal, "production")
			_, err := service.GetPlatformRelayModelCatalog()
			require.Error(t, err, "a development declaration cannot downgrade a secure process")
		})
	}
	t.Run("unsigned-releases-stay-untrusted", func(t *testing.T) {
		for _, key := range []string{"APP_ENV", "DEPLOYMENT_ENV", "ENVIRONMENT", "RELAY_COMPAT_ENVIRONMENT"} {
			t.Setenv(key, "production")
		}
		_, err := service.GetPlatformRelayModelCatalog()
		require.Error(t, err, "changing environment labels cannot promote unsigned local declarations")
	})
	encoded, err := common.Marshal(config)
	require.NoError(t, err)
	var publicProjection localvideoconfig.Config
	require.NoError(t, common.Unmarshal(encoded, &publicProjection))
	assert.Error(t, localvideoconfig.Validate(publicProjection), "public JSON must never reconstitute the private prepared authority")
}

func TestLocalConfigurationRejectsMixedModesUnsafeInputsAndUnavailableModels(t *testing.T) {
	tests := map[string]func(*localvideoconfig.Options){
		"production":                        func(o *localvideoconfig.Options) { o.Environment = "production" },
		"staging":                           func(o *localvideoconfig.Options) { o.Environment = "staging" },
		"implicit environment":              func(o *localvideoconfig.Options) { o.Environment = "" },
		"unknown mode":                      func(o *localvideoconfig.Options) { o.Mode = "auto" },
		"case folded mode":                  func(o *localvideoconfig.Options) { o.Mode = "Mock" },
		"mock receives live key":            func(o *localvideoconfig.Options) { o.Keys.Ark = liveOptions().Keys.Ark },
		"mock receives fake key explicitly": func(o *localvideoconfig.Options) { o.Keys.MiniMax = localvideoconfig.MockMiniMaxProviderKey },
		"missing seed":                      func(o *localvideoconfig.Options) { o.RuntimeSeed = nil },
		"short seed":                        func(o *localvideoconfig.Options) { o.RuntimeSeed = make([]byte, 31) },
		"missing review time":               func(o *localvideoconfig.Options) { o.Now = time.Time{} },
		"missing reviewer":                  func(o *localvideoconfig.Options) { o.CreatedBy = "" },
		"missing reason":                    func(o *localvideoconfig.Options) { o.Reason = "" },
		"directory traversal":               func(o *localvideoconfig.Options) { o.Namespace = "../real-canary" },
		"unknown model":                     func(o *localvideoconfig.Options) { o.ModelIDs = []string{"minimax-h3-next"} },
		"provider id is not public alias":   func(o *localvideoconfig.Options) { o.ModelIDs = []string{"MiniMax-H3"} },
		"legacy Hailuo is not H3":           func(o *localvideoconfig.Options) { o.ModelIDs = []string{"MiniMax-Hailuo-2.3"} },
		"duplicate model":                   func(o *localvideoconfig.Options) { o.ModelIDs = []string{"minimax-h3", "minimax-h3"} },
		"retired model":                     func(o *localvideoconfig.Options) { o.ModelIDs = []string{"seedance-1.0-lite-t2v"} },
		"deprecated existing route only":    func(o *localvideoconfig.Options) { o.ModelIDs = []string{"seedance-1.5-pro"} },
		"unverified version":                func(o *localvideoconfig.Options) { o.ModelIDs = []string{"seedance-1.0-pro-fast-250610-unverified"} },
		"unsupported MiniMax region":        func(o *localvideoconfig.Options) { o.MiniMaxRegion = "other" },
		"external plaintext callback":       func(o *localvideoconfig.Options) { o.CallbackURL = "http://platform.example/callback" },
		"callback userinfo":                 func(o *localvideoconfig.Options) { o.CallbackURL = "https://user:password@platform.example/callback" },
		"callback query":                    func(o *localvideoconfig.Options) { o.CallbackURL += "?token=unsafe" },
		"overlong callback":                 func(o *localvideoconfig.Options) { o.CallbackURL += strings.Repeat("a", 2048) },
		"implicit container callback": func(o *localvideoconfig.Options) {
			o.CallbackURL = "http://platform-lab:8000/internal/relay-callbacks/new-api-v1"
		},
		"container arbitrary host": func(o *localvideoconfig.Options) {
			o.IsolatedDocker = true
			o.CallbackURL = "http://other-lab:8000/internal/relay-callbacks/new-api-v1"
		},
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			options := localOptions()
			mutate(&options)
			config, err := localvideoconfig.Build(options)
			require.Error(t, err)
			assert.Empty(t, config.StateID, "invalid input cannot produce a partial usable configuration")
			assert.Empty(t, config.Routes)
			assert.NotContains(t, err.Error(), liveOptions().Keys.Ark)
		})
	}
}

func TestLiveKeysAreExactServerInputsAndMockNeverPromotes(t *testing.T) {
	for name, mutate := range map[string]func(*localvideoconfig.Options){
		"missing Ark key":     func(o *localvideoconfig.Options) { o.Keys.Ark = "" },
		"missing MiniMax key": func(o *localvideoconfig.Options) { o.Keys.MiniMax = "" },
		"mock Ark key":        func(o *localvideoconfig.Options) { o.Keys.Ark = localvideoconfig.MockArkProviderKey },
		"mock MiniMax key":    func(o *localvideoconfig.Options) { o.Keys.MiniMax = localvideoconfig.MockMiniMaxProviderKey },
		"whitespace key":      func(o *localvideoconfig.Options) { o.Keys.Ark += " " },
		"control byte key":    func(o *localvideoconfig.Options) { o.Keys.Ark += "\x00" },
		"placeholder key":     func(o *localvideoconfig.Options) { o.Keys.Ark = "replace-with-real-ark-key" },
		"unselected mock provider key": func(o *localvideoconfig.Options) {
			o.ModelIDs = []string{"minimax-h3-max"}
			o.Keys.Ark = localvideoconfig.MockArkProviderKey
		},
	} {
		t.Run(name, func(t *testing.T) {
			options := liveOptions()
			mutate(&options)
			_, err := localvideoconfig.Build(options)
			require.Error(t, err)
			assert.NotContains(t, err.Error(), "unit-live-")
		})
	}
	options := liveOptions()
	options.ModelIDs = []string{"minimax-h3-max"}
	options.Keys.Ark = ""
	options.MiniMaxRegion = "global"
	options.IsolatedDocker = true
	options.CallbackURL = "http://platform-lab:8000/internal/relay-callbacks/new-api-v1"
	config, err := localvideoconfig.Build(options)
	require.NoError(t, err)
	require.Len(t, config.Channels, 1)
	assert.Equal(t, localvideoconfig.MiniMaxGlobalBaseURL, config.Channels[0].BaseURL)
	assert.Equal(t, options.Keys.MiniMax, config.Channels[0].Key)
	digest := sha256.Sum256([]byte(options.Keys.MiniMax))
	assert.Equal(t, hex.EncodeToString(digest[:]), config.Channels[0].KeyFingerprint)
	assert.Equal(t, "live_provider_acceptance_required", config.Status)
	assert.False(t, config.Summary().ProductionReady)
	assert.Equal(t, []string{"text_to_video"}, config.Models[0].Modes)
}

func TestStateIdentityIsStableButCannotReuseAnotherCredentialOrModeReceipt(t *testing.T) {
	options := liveOptions()
	config, err := localvideoconfig.Build(options)
	require.NoError(t, err)
	repeated, err := localvideoconfig.Build(options)
	require.NoError(t, err)
	assert.Equal(t, config.StateID, repeated.StateID)
	assert.Equal(t, config.Principal, repeated.Principal)
	firstRoutes, err := config.RoutesJSON()
	require.NoError(t, err)
	repeatedRoutes, err := repeated.RoutesJSON()
	require.NoError(t, err)
	assert.Equal(t, firstRoutes, repeatedRoutes)
	for name, mutate := range map[string]func(*localvideoconfig.Options){
		"Ark key rotation":     func(o *localvideoconfig.Options) { o.Keys.Ark += "-rotated" },
		"MiniMax key rotation": func(o *localvideoconfig.Options) { o.Keys.MiniMax += "-rotated" },
		"mock mode": func(o *localvideoconfig.Options) {
			o.Mode = localvideoconfig.ModeMock
			o.Keys = localvideoconfig.ProviderKeys{}
		},
		"runtime seed": func(o *localvideoconfig.Options) {
			o.RuntimeSeed = []byte("another-unit-test-runtime-seed-at-least-32-bytes")
		},
		"namespace":       func(o *localvideoconfig.Options) { o.Namespace += "-second" },
		"review":          func(o *localvideoconfig.Options) { o.Now = o.Now.Add(time.Second) },
		"provider region": func(o *localvideoconfig.Options) { o.MiniMaxRegion = "global" },
	} {
		t.Run(name, func(t *testing.T) {
			changedOptions := liveOptions()
			mutate(&changedOptions)
			changed, err := localvideoconfig.Build(changedOptions)
			require.NoError(t, err)
			assert.NotEqual(t, config.StateID, changed.StateID)
			assert.NotEqual(t, config.Principal.TenantID, changed.Principal.TenantID)
			assert.NotEqual(t, config.Principal.APIKey, changed.Principal.APIKey)
			assert.NotEqual(t, config.Routes["minimax-h3"][0].RouteID, changed.Routes["minimax-h3"][0].RouteID)
			assert.NotEqual(t, config.Channels[0].AccountID, changed.Channels[0].AccountID)
			for index := range config.Models {
				assert.Equal(t, config.Models[index].CapabilityRevision, changed.Models[index].CapabilityRevision, "credential changes cannot invent a capability revision")
			}
		})
	}
}

func TestValidationRefusesCapabilityWideningAndPreparedIdentityMutation(t *testing.T) {
	for name, mutate := range map[string]func(*localvideoconfig.Config){
		"environment":    func(c *localvideoconfig.Config) { c.Environment = "production" },
		"mode":           func(c *localvideoconfig.Config) { c.Mode = localvideoconfig.ModeLive },
		"state identity": func(c *localvideoconfig.Config) { c.StateID = "mock-other-state" },
		"provider model": func(c *localvideoconfig.Config) { c.Routes["minimax-h3"][0].UpstreamModel = "MiniMax-H3-Max" },
		"profile": func(c *localvideoconfig.Config) {
			c.Routes["minimax-h3-max"][0].CapabilityProfile = generationprofile.MiniMaxH3ReferenceVideoGenerationV1
		},
		"resolution widening": func(c *localvideoconfig.Config) {
			mode := c.Routes["minimax-h3-max"][0].Capabilities.Modes["text_to_video"]
			mode.Limits.Resolutions = append(mode.Limits.Resolutions, "2k")
			c.Routes["minimax-h3-max"][0].Capabilities.Modes["text_to_video"] = mode
		},
		"unsigned release replaced": func(c *localvideoconfig.Config) {
			c.Routes["minimax-h3"][0].ModelRelease.Attestation = &generationrelease.Attestation{Algorithm: "Ed25519"}
		},
		"channel credential":   func(c *localvideoconfig.Config) { c.Channels[0].Key = "replaced-key-that-must-not-be-reused" },
		"channel endpoint":     func(c *localvideoconfig.Config) { c.Channels[0].BaseURL = "https://unrelated.example" },
		"principal credential": func(c *localvideoconfig.Config) { c.Principal.APIKey = "different-principal-credential" },
	} {
		t.Run(name, func(t *testing.T) {
			config, err := localvideoconfig.Build(localOptions())
			require.NoError(t, err)
			mutate(&config)
			assert.Error(t, localvideoconfig.Validate(config))
			environment, err := config.RuntimeEnvironment()
			require.Error(t, err)
			assert.Nil(t, environment, "modified data must not become runtime environment")
		})
	}
	assert.Error(t, localvideoconfig.Validate(localvideoconfig.Config{}))
}

func TestRuntimePrincipalProjectionIsExactAndSummaryNeverContainsSecrets(t *testing.T) {
	options := liveOptions()
	config, err := localvideoconfig.Build(options)
	require.NoError(t, err)
	environment, err := config.RuntimeEnvironment()
	require.NoError(t, err)
	assert.Equal(t, config.Principal.APIKey, environment["PLATFORM_LAB_RELAY_API_KEY"])
	assert.Equal(t, config.Principal.InternalAdmissionToken, environment["RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN"])
	assert.Equal(t, config.Principal.CallbackSigningSecret, environment["PLATFORM_LAB_RELAY_CALLBACK_SIGNING_SECRET"])
	assert.Equal(t, config.Principal.OperationsToken, environment["PLATFORM_LAB_RELAY_OPERATIONS_TOKEN"])
	assert.Equal(t, config.StateID, environment["PLATFORM_LAB_RELAY_STATE_ID"])
	assert.Equal(t, "false", environment["RELAY_NATIVE_PAID_COMPAT_ENABLED"])
	assert.Equal(t, "false", environment["CHANNEL_TEST_ENABLED"], "startup must not trigger periodic paid probes")
	assert.Regexp(t, `^sk-[a-f0-9]{48}$`, config.Principal.UpstreamToken)
	var credentials map[string]map[string]string
	require.NoError(t, common.Unmarshal([]byte(environment["RELAY_COMPAT_CLIENT_CREDENTIALS_JSON"]), &credentials))
	require.Len(t, credentials, 1)
	assert.Equal(t, config.Principal.APIKey, credentials[config.Principal.ClientID]["api_key"])
	assert.Equal(t, config.Principal.UpstreamToken, credentials[config.Principal.ClientID]["upstream_token"])
	assert.Equal(t, config.Principal.TenantID, credentials[config.Principal.ClientID]["tenant_id"])
	assert.Equal(t, config.Principal.CallbackURL, credentials[config.Principal.ClientID]["callback_url"])
	var operations []map[string]string
	require.NoError(t, common.Unmarshal([]byte(environment["RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON"]), &operations))
	require.Len(t, operations, 1)
	operationDigest := sha256.Sum256([]byte(config.Principal.OperationsToken))
	assert.Equal(t, hex.EncodeToString(operationDigest[:]), operations[0]["token_sha256"])
	secretCorpus := []string{options.Keys.Ark, options.Keys.MiniMax, config.Principal.APIKey,
		config.Principal.UpstreamToken, config.Principal.InternalAdmissionToken, config.Principal.OperationsToken,
		config.Principal.CallbackSigningSecret, config.Principal.ArtifactSigningSecret, config.Principal.ReconciliationSecret,
		config.Principal.CredentialKeyringJSON, string(options.RuntimeSeed)}
	for _, value := range []any{config, config.Summary(), config.Principal, config.Channels, options, options.Keys} {
		encoded, err := common.Marshal(value)
		require.NoError(t, err)
		for _, secret := range secretCorpus {
			assert.NotContains(t, string(encoded), secret)
			assert.NotContains(t, fmt.Sprintf("%+v", value), secret)
			assert.NotContains(t, fmt.Sprintf("%#v", value), secret)
		}
	}
	encodedSummary, err := common.Marshal(config.Summary())
	require.NoError(t, err)
	for _, channel := range config.Channels {
		assert.NotContains(t, string(encodedSummary), channel.KeyFingerprint)
		assert.NotContains(t, string(encodedSummary), channel.AccountID)
	}
	for _, value := range environment {
		assert.NotContains(t, value, options.Keys.Ark, "provider keys stay in native credential loading, not orchestration output")
		assert.NotContains(t, value, options.Keys.MiniMax)
	}
}

func TestSelectionsLifecycleAndCallerOwnedInputsRemainIsolated(t *testing.T) {
	for _, at := range []time.Time{localOptions().Now, time.Date(2026, 9, 21, 6, 0, 0, 0, time.UTC)} {
		options := localOptions()
		options.Now, options.ModelIDs = at, []string{"seedance-1.5-pro"}
		_, err := localvideoconfig.Build(options)
		require.Error(t, err, "EOM and EOS must never be bypassed for a new local route")
	}
	options := localOptions()
	options.ModelIDs = []string{"minimax-h3-max", "seedance-2.0-mini"}
	config, err := localvideoconfig.Build(options)
	require.NoError(t, err)
	require.Len(t, config.Models, 2)
	options.ModelIDs[0] = "not-a-model"
	options.RuntimeSeed[0] ^= 1
	require.NoError(t, localvideoconfig.Validate(config), "Build must snapshot caller-owned seed and selection slices")
	independent, err := localvideoconfig.Build(localOptions())
	require.NoError(t, err)
	capability := config.Models[0].Capability.Modes["text_to_video"]
	capability.Limits.Resolutions = []string{"invalid"}
	config.Models[0].Capability.Modes["text_to_video"] = capability
	require.NoError(t, localvideoconfig.Validate(independent), "mutating one prepared instance cannot modify compiled data or another instance")
	assert.False(t, strings.Contains(independent.StateID, ".."))
}
