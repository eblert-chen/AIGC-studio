package service

import (
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
)

const (
	PlatformChannelClassReverseEngineered = "reverse"
	PlatformChannelClassThirdPartyAPI     = "third_party_api"
	PlatformChannelClassOfficial          = "official"
)

// PlatformRelayRouteDeclaration is the secret-free route inventory used to
// derive the public failover-safe model capability. Credentials remain in the
// native new-api channel store and are addressed by channel/key index only.
type PlatformRelayRouteDeclaration struct {
	RouteID      string `json:"route_id"`
	ProviderName string `json:"provider_name"`
	AccountID    string `json:"account_id"`
	ChannelID    int    `json:"channel_id"`
	// NativeChannelType binds the accepted route to the exact new-api adapter
	// implementation selected by the native channel row. It is mandatory for
	// staging/production and verified again against the database during sync.
	NativeChannelType int    `json:"native_channel_type,omitempty"`
	KeyIndex          int    `json:"key_index"`
	KeyFingerprint    string `json:"key_fingerprint"`
	ChannelClass      string `json:"channel_class"`
	UpstreamModel     string `json:"upstream_model"`
	// StagingReady is a separate, explicit real-provider acceptance gate. It
	// does not imply production readiness and ProductionReady does not fill it
	// in implicitly, so moving a route between environments always fails closed.
	StagingReady    bool                               `json:"staging_ready,omitempty"`
	ProductionReady bool                               `json:"production_ready,omitempty"`
	RPMLimit        int                                `json:"rpm_limit"`
	ActiveTaskLimit int                                `json:"active_task_limit"`
	Capabilities    dto.PlatformGenerationCapabilities `json:"capabilities"`
	Acceptance      *PlatformRouteAcceptanceEvidence   `json:"acceptance,omitempty"`
	// CapabilityProfile selects an immutable, code-reviewed adapter contract.
	// It is independent from route/key identity and may only be narrowed by the
	// route capability below. Existing exact Seedream bindings may omit it and
	// are resolved to the sole matching profile for migration compatibility.
	CapabilityProfile string `json:"capability_profile,omitempty"`
	// ModelRelease is the signed identity/capability binding. New reusable
	// profiles require it; the only release-less path is the exact historical
	// Seedream binding retained for migration compatibility.
	ModelRelease *generationrelease.Release `json:"model_release,omitempty"`

	// These values are derived only after signature verification and are never
	// accepted from route JSON. They feed the secret-free audit/provenance view.
	AcceptanceDigest                  string    `json:"-"`
	AcceptanceNotBefore               time.Time `json:"-"`
	AcceptanceNotAfter                time.Time `json:"-"`
	ResolvedCapabilityProfileID       string    `json:"-"`
	ResolvedCapabilityProfileRevision string    `json:"-"`
	CanonicalPublicModel              string    `json:"-"`
	DeprecatedPublicAlias             bool      `json:"-"`
	ResolvedModelReleaseID            string    `json:"-"`
	ResolvedModelReleaseRevision      string    `json:"-"`
	ResolvedModelCapabilityRevision   string    `json:"-"`
}

func parsePlatformRelayCapabilities(
	capabilitiesRaw string,
	routesRaw string,
	environment string,
) (map[string]dto.PlatformGenerationCapabilities, map[string][]PlatformRelayRouteDeclaration, error) {
	capabilities := make(map[string]dto.PlatformGenerationCapabilities)
	routes := make(map[string][]PlatformRelayRouteDeclaration)
	environment = strings.ToLower(strings.TrimSpace(environment))
	switch environment {
	case "", "development", "test", "staging", "production":
	default:
		return nil, nil, fmt.Errorf("Relay compatibility environment %q is invalid", environment)
	}
	if err := rejectPlatformRouteAcceptancePrivateMaterial(); err != nil {
		return nil, nil, err
	}
	secureEnvironment := environment == "staging" || environment == "production"

	if routesRaw != "" && capabilitiesRaw != "" {
		return nil, nil, fmt.Errorf("configure either RELAY_COMPAT_MODEL_ROUTES_JSON or RELAY_COMPAT_MODEL_CAPABILITIES_JSON, not both")
	}
	if routesRaw == "" {
		if secureEnvironment {
			return nil, nil, fmt.Errorf("%s requires RELAY_COMPAT_MODEL_ROUTES_JSON", environment)
		}
		if capabilitiesRaw == "" {
			return capabilities, routes, nil
		}
		if err := common.RejectDuplicateJSONKeys([]byte(capabilitiesRaw)); err != nil {
			return nil, nil, fmt.Errorf("invalid Relay compatibility model capabilities: %w", err)
		}
		if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(capabilitiesRaw), &capabilities); err != nil {
			return nil, nil, fmt.Errorf("invalid Relay compatibility model capabilities: %w", err)
		}
		for modelID, capability := range capabilities {
			if err := validatePlatformCapability(modelID, capability); err != nil {
				return nil, nil, err
			}
			capabilities[modelID] = normalizePlatformCapability(capability)
		}
		return capabilities, routes, nil
	}

	if err := common.RejectDuplicateJSONKeys([]byte(routesRaw)); err != nil {
		return nil, nil, fmt.Errorf("invalid Relay compatibility route declarations: %w", err)
	}
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(routesRaw), &routes); err != nil {
		return nil, nil, fmt.Errorf("invalid Relay compatibility route declarations: %w", err)
	}
	routeCount := 0
	for _, declarations := range routes {
		routeCount += len(declarations)
	}
	acceptanceVerifier, err := newPlatformRouteAcceptanceVerifier(environment, secureEnvironment && routeCount > 0)
	if err != nil {
		return nil, nil, err
	}
	for modelID, declarations := range routes {
		if len(declarations) == 0 {
			return nil, nil, fmt.Errorf("Relay model %q has no provider routes", modelID)
		}
		seenRouteIDs := make(map[string]struct{}, len(declarations))
		for i := range declarations {
			declaration := declarations[i]
			if strings.TrimSpace(declaration.RouteID) == "" || len(declaration.RouteID) > 120 {
				return nil, nil, fmt.Errorf("Relay route id for model %q is invalid", modelID)
			}
			if _, duplicate := seenRouteIDs[declaration.RouteID]; duplicate {
				return nil, nil, fmt.Errorf("Relay route %q is declared more than once", declaration.RouteID)
			}
			seenRouteIDs[declaration.RouteID] = struct{}{}
			if declaration.ChannelID <= 0 || declaration.KeyIndex < 0 || declaration.RPMLimit <= 0 || declaration.ActiveTaskLimit <= 0 {
				return nil, nil, fmt.Errorf("Relay route %q has invalid admission limits", declaration.RouteID)
			}
			if strings.TrimSpace(declaration.KeyFingerprint) == "" || strings.TrimSpace(declaration.UpstreamModel) == "" {
				return nil, nil, fmt.Errorf("Relay route %q requires a key fingerprint and upstream model", declaration.RouteID)
			}
			fingerprint, decodeErr := hex.DecodeString(declaration.KeyFingerprint)
			if decodeErr != nil || len(fingerprint) != 32 || strings.ToLower(declaration.KeyFingerprint) != declaration.KeyFingerprint {
				return nil, nil, fmt.Errorf("Relay route %q key fingerprint must be a lowercase SHA-256 digest", declaration.RouteID)
			}
			if strings.TrimSpace(declaration.ProviderName) == "" || len(declaration.ProviderName) > 64 ||
				strings.TrimSpace(declaration.AccountID) == "" || len(declaration.AccountID) > 128 {
				return nil, nil, fmt.Errorf("Relay route %q requires a provider and account identity", declaration.RouteID)
			}
			switch declaration.ChannelClass {
			case PlatformChannelClassReverseEngineered, PlatformChannelClassThirdPartyAPI, PlatformChannelClassOfficial:
			default:
				return nil, nil, fmt.Errorf("Relay route %q has an unknown channel class", declaration.RouteID)
			}
			if secureEnvironment && (platformRelayMockIdentity(declaration.RouteID) ||
				platformRelayMockIdentity(declaration.ProviderName) ||
				platformRelayMockIdentity(declaration.AccountID) ||
				platformRelayMockIdentity(declaration.UpstreamModel)) {
				return nil, nil, fmt.Errorf("Relay route %q uses a mock identity and is forbidden in staging and production", declaration.RouteID)
			}
			if err := resolvePlatformGenerationRouteProfile(modelID, &declaration); err != nil {
				return nil, nil, fmt.Errorf("Relay route %q: %w", declaration.RouteID, err)
			}
			if err := validatePlatformCapability(modelID, declaration.Capabilities); err != nil {
				return nil, nil, fmt.Errorf("Relay route %q: %w", declaration.RouteID, err)
			}
			if err := validatePlatformNativeTaskBridgeCapability(modelID, declaration); err != nil {
				return nil, nil, fmt.Errorf("Relay route %q cannot be executed by the generation bridge: %w", declaration.RouteID, err)
			}
			declaration.Capabilities = normalizePlatformCapability(declaration.Capabilities)
			if secureEnvironment {
				if declaration.ModelRelease != nil {
					if err := declaration.ModelRelease.VerifyAttestation(acceptanceVerifier.publicKeys, acceptanceVerifier.now); err != nil {
						return nil, nil, fmt.Errorf("Relay route %q model release: %w", declaration.RouteID, err)
					}
				}
				if err := acceptanceVerifier.verify(modelID, &declaration); err != nil {
					return nil, nil, err
				}
			}
			declarations[i] = declaration
		}
		sort.Slice(declarations, func(i, j int) bool { return declarations[i].RouteID < declarations[j].RouteID })
		routes[modelID] = declarations
		intersection, err := intersectPlatformCapabilities(modelID, declarations)
		if err != nil {
			return nil, nil, err
		}
		capabilities[modelID] = intersection
	}
	return capabilities, routes, nil
}

func platformRelayMockIdentity(value string) bool {
	normalized := strings.ToLower(strings.TrimSpace(value))
	return normalized == "mock" || strings.HasPrefix(normalized, "mock-") ||
		strings.HasPrefix(normalized, "mock_") || strings.HasSuffix(normalized, "-mock") ||
		strings.HasSuffix(normalized, "_mock")
}

func resolvePlatformGenerationRouteProfile(modelID string, declaration *PlatformRelayRouteDeclaration) error {
	if declaration == nil {
		return fmt.Errorf("route declaration is required")
	}
	if declaration.NativeChannelType == constant.ChannelTypeVolcEngine &&
		strings.HasPrefix(strings.TrimSpace(declaration.UpstreamModel), "doubao-seedance-") && declaration.ModelRelease == nil {
		// A release-less legacy declaration must not advertise a model's
		// reusable implementation ceiling without its reviewed restrictions.
		return fmt.Errorf("Ark Seedance routes require a reviewed capability profile and model_release")
	}
	if declaration.NativeChannelType == constant.ChannelTypeMiniMax &&
		strings.HasPrefix(strings.TrimSpace(declaration.UpstreamModel), "MiniMax-H3") && declaration.ModelRelease == nil {
		return fmt.Errorf("MiniMax H3 routes require a reviewed capability profile and model_release")
	}
	if declaration.ModelRelease != nil {
		release := declaration.ModelRelease
		if !release.MatchesPublicModel(modelID) {
			return fmt.Errorf("model release does not bind public model %q", modelID)
		}
		if release.ProviderModelID != declaration.UpstreamModel {
			return fmt.Errorf("model release provider_model_id does not match route upstream_model")
		}
		if declaration.CapabilityProfile != "" && declaration.CapabilityProfile != release.AdapterProfileID {
			return fmt.Errorf("model release adapter_profile_id does not match route capability_profile")
		}
		profile, found, err := generationprofile.Resolve(release.AdapterProfileID, declaration.NativeChannelType, declaration.UpstreamModel)
		if err != nil {
			return err
		}
		if !found {
			return fmt.Errorf("model release adapter profile is unavailable")
		}
		if err := release.Validate(profile); err != nil {
			return err
		}
		if len(declaration.Capabilities.Modes) == 0 {
			declaration.Capabilities = generationprofile.NormalizeCapability(release.Capability)
		}
		if err := release.ValidateRouteNarrowing(profile, declaration.Capabilities); err != nil {
			return err
		}
		bindingRevision, err := release.BindingRevision()
		if err != nil {
			return err
		}
		capabilityRevision, err := release.CapabilityRevision()
		if err != nil {
			return err
		}
		declaration.CapabilityProfile = profile.ID
		declaration.ResolvedCapabilityProfileID = profile.ID
		declaration.ResolvedCapabilityProfileRevision = profile.Revision
		declaration.CanonicalPublicModel = release.PublicModelID
		declaration.DeprecatedPublicAlias = modelID != release.PublicModelID
		declaration.ResolvedModelReleaseID = release.ReleaseID
		declaration.ResolvedModelReleaseRevision = bindingRevision
		declaration.ResolvedModelCapabilityRevision = capabilityRevision
		return nil
	}
	profile, found, err := generationprofile.Resolve(
		declaration.CapabilityProfile,
		declaration.NativeChannelType,
		declaration.UpstreamModel,
	)
	if err != nil {
		return err
	}
	if !found {
		if strings.TrimSpace(declaration.CapabilityProfile) != "" {
			return fmt.Errorf("capability profile is unavailable")
		}
		return nil
	}
	if profile.ID != generationprofile.Seedream50TextToImageV1 {
		return fmt.Errorf("reusable capability profile %q requires a model_release", profile.ID)
	}
	if err := profile.ValidateNarrowing(declaration.Capabilities); err != nil {
		return err
	}
	declaration.CapabilityProfile = profile.ID
	declaration.ResolvedCapabilityProfileID = profile.ID
	declaration.ResolvedCapabilityProfileRevision = profile.Revision
	// The legacy profile describes the already-shipped Seedream protocol ceiling;
	// it is not itself a model identity binding. Keep the one historical alias
	// migration, while allowing an explicitly selected legacy profile to be used
	// by old installations whose public alias was configured independently. New
	// reusable profiles still require a signed model_release above.
	declaration.CanonicalPublicModel = modelID
	if modelID == constant.PlatformGenerationLegacySeedream50LitePublicAlias {
		declaration.CanonicalPublicModel = constant.PlatformGenerationPublicSeedream50Model
		declaration.DeprecatedPublicAlias = true
	}
	return nil
}

// validatePlatformNativeTaskBridgeCapability prevents route declarations from
// advertising fields that the current native task bridge cannot preserve.
// Adapter metadata is not treated as proof that a public contract field was
// applied by the provider.
func validatePlatformNativeTaskBridgeCapability(modelID string, declaration PlatformRelayRouteDeclaration) error {
	if declaration.ResolvedCapabilityProfileID == "" {
		if err := resolvePlatformGenerationRouteProfile(modelID, &declaration); err != nil {
			return err
		}
	}
	capability := declaration.Capabilities
	if declaration.ResolvedCapabilityProfileID != "" {
		profile, ok := generationprofile.Get(declaration.ResolvedCapabilityProfileID)
		if !ok || profile.Revision != declaration.ResolvedCapabilityProfileRevision {
			return fmt.Errorf("model %q capability profile snapshot is unavailable", modelID)
		}
		if err := profile.ValidateNarrowing(capability); err != nil {
			return fmt.Errorf("model %q exceeds adapter capability profile: %w", modelID, err)
		}
		return nil
	}
	for modeName, mode := range capability.Modes {
		if modeName == "text_to_image" {
			return fmt.Errorf("model %q mode %q requires a registered generation capability profile", modelID, modeName)
		}
		if mode.SupportsFace {
			return fmt.Errorf("model %q mode %q cannot advertise face controls until an adapter applies them explicitly", modelID, modeName)
		}
		if mode.Limits.MaxAudio != 0 || containsString(mode.InputMediaTypes, "audio") {
			return fmt.Errorf("model %q mode %q cannot advertise audio input", modelID, modeName)
		}
		if mode.Limits.MaxVideos > 1 {
			return fmt.Errorf("model %q mode %q cannot advertise more than one video input", modelID, modeName)
		}
		if len(mode.Limits.OutputCounts) != 1 || mode.Limits.OutputCounts[0] != 1 {
			return fmt.Errorf("model %q mode %q must advertise exactly one output", modelID, modeName)
		}
	}
	return nil
}

func intersectPlatformCapabilities(modelID string, routes []PlatformRelayRouteDeclaration) (dto.PlatformGenerationCapabilities, error) {
	first := routes[0].Capabilities
	result := dto.PlatformGenerationCapabilities{SchemaVersion: first.SchemaVersion, Modes: make(map[string]dto.PlatformModeCapability)}
	modesByName := make(map[string][]dto.PlatformModeCapability)
	for _, route := range routes {
		if route.Capabilities.SchemaVersion != first.SchemaVersion {
			return dto.PlatformGenerationCapabilities{}, fmt.Errorf("Relay routes for model %q use different capability schema versions", modelID)
		}
		for modeName, mode := range route.Capabilities.Modes {
			modesByName[modeName] = append(modesByName[modeName], mode)
		}
	}
	// Routing is selected by model + mode. A route that does not implement a
	// mode can never receive that mode and therefore must not erase it from the
	// public catalog. Intersect only the routes eligible for the same mode,
	// while taking the mode union across the shared public model alias.
	for modeName, modes := range modesByName {
		intersection := modes[0]
		for _, mode := range modes[1:] {
			intersection.SupportsFace = intersection.SupportsFace && mode.SupportsFace
			intersection.InputMediaTypes = intersectStrings(intersection.InputMediaTypes, mode.InputMediaTypes)
			intersection.RequiredResourceKeys = unionStrings(intersection.RequiredResourceKeys, mode.RequiredResourceKeys)
			intersection.ConditionalRequiredResourceKeys = unionConditionalResourceKeys(
				intersection.ConditionalRequiredResourceKeys,
				mode.ConditionalRequiredResourceKeys,
			)
			intersection.Limits.MaxPromptLength = minPlatformCapabilityLimit(intersection.Limits.MaxPromptLength, mode.Limits.MaxPromptLength)
			intersection.Limits.MaxImages = minPlatformCapabilityLimit(intersection.Limits.MaxImages, mode.Limits.MaxImages)
			intersection.Limits.MaxVideos = minPlatformCapabilityLimit(intersection.Limits.MaxVideos, mode.Limits.MaxVideos)
			intersection.Limits.MaxAudio = minPlatformCapabilityLimit(intersection.Limits.MaxAudio, mode.Limits.MaxAudio)
			intersection.Limits.DurationSeconds = intersectInts(intersection.Limits.DurationSeconds, mode.Limits.DurationSeconds)
			intersection.Limits.AspectRatios = intersectStrings(intersection.Limits.AspectRatios, mode.Limits.AspectRatios)
			intersection.Limits.Resolutions = intersectStrings(intersection.Limits.Resolutions, mode.Limits.Resolutions)
			intersection.Limits.OutputCounts = intersectInts(intersection.Limits.OutputCounts, mode.Limits.OutputCounts)
		}
		if !intersection.SupportsFace && intersection.ConditionalRequiredResourceKeys != nil {
			delete(intersection.ConditionalRequiredResourceKeys, "face_enabled")
			if len(intersection.ConditionalRequiredResourceKeys) == 0 {
				intersection.ConditionalRequiredResourceKeys = nil
			}
		}
		if (platformCapabilityDurationApplies(modeName) && len(intersection.Limits.DurationSeconds) == 0) || len(intersection.Limits.AspectRatios) == 0 ||
			len(intersection.Limits.Resolutions) == 0 || len(intersection.Limits.OutputCounts) == 0 {
			return dto.PlatformGenerationCapabilities{}, fmt.Errorf("Relay routes for model %q have an empty safe intersection in mode %q", modelID, modeName)
		}
		result.Modes[modeName] = normalizePlatformModeCapability(intersection)
	}
	if len(result.Modes) == 0 {
		return dto.PlatformGenerationCapabilities{}, fmt.Errorf("Relay routes for model %q do not share a failover-safe mode", modelID)
	}
	return result, nil
}

func normalizePlatformCapability(capability dto.PlatformGenerationCapabilities) dto.PlatformGenerationCapabilities {
	normalized := dto.PlatformGenerationCapabilities{SchemaVersion: capability.SchemaVersion, Modes: make(map[string]dto.PlatformModeCapability, len(capability.Modes))}
	for modeName, mode := range capability.Modes {
		normalized.Modes[modeName] = normalizePlatformModeCapability(mode)
	}
	return normalized
}

func normalizePlatformModeCapability(mode dto.PlatformModeCapability) dto.PlatformModeCapability {
	mode.InputMediaTypes = sortedUniqueStrings(mode.InputMediaTypes)
	mode.RequiredResourceKeys = sortedUniqueStrings(mode.RequiredResourceKeys)
	if mode.ConditionalRequiredResourceKeys != nil {
		normalized := make(map[string][]string, len(mode.ConditionalRequiredResourceKeys))
		for condition, keys := range mode.ConditionalRequiredResourceKeys {
			normalized[condition] = sortedUniqueStrings(keys)
		}
		mode.ConditionalRequiredResourceKeys = normalized
	}
	mode.Limits.DurationSeconds = sortedUniqueInts(mode.Limits.DurationSeconds)
	mode.Limits.AspectRatios = sortedUniqueStrings(mode.Limits.AspectRatios)
	mode.Limits.Resolutions = sortedUniqueStrings(mode.Limits.Resolutions)
	mode.Limits.OutputCounts = sortedUniqueInts(mode.Limits.OutputCounts)
	return mode
}

func unionConditionalResourceKeys(left map[string][]string, right map[string][]string) map[string][]string {
	if left == nil && right == nil {
		return nil
	}
	result := make(map[string][]string, len(left)+len(right))
	for condition, keys := range left {
		result[condition] = sortedUniqueStrings(keys)
	}
	for condition, keys := range right {
		result[condition] = unionStrings(result[condition], keys)
	}
	return result
}

func intersectStrings(left []string, right []string) []string {
	rightSet := make(map[string]struct{}, len(right))
	for _, value := range right {
		rightSet[value] = struct{}{}
	}
	result := make([]string, 0)
	for _, value := range left {
		if _, ok := rightSet[value]; ok {
			result = append(result, value)
		}
	}
	return sortedUniqueStrings(result)
}

func unionStrings(left []string, right []string) []string {
	return sortedUniqueStrings(append(append([]string(nil), left...), right...))
}

func intersectInts(left []int, right []int) []int {
	rightSet := make(map[int]struct{}, len(right))
	for _, value := range right {
		rightSet[value] = struct{}{}
	}
	result := make([]int, 0)
	for _, value := range left {
		if _, ok := rightSet[value]; ok {
			result = append(result, value)
		}
	}
	return sortedUniqueInts(result)
}

func sortedUniqueStrings(values []string) []string {
	set := make(map[string]struct{}, len(values))
	for _, value := range values {
		set[value] = struct{}{}
	}
	result := make([]string, 0, len(set))
	for value := range set {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}

func sortedUniqueInts(values []int) []int {
	set := make(map[int]struct{}, len(values))
	for _, value := range values {
		set[value] = struct{}{}
	}
	result := make([]int, 0, len(set))
	for value := range set {
		result = append(result, value)
	}
	sort.Ints(result)
	return result
}

func minPlatformCapabilityLimit(left int, right int) int {
	if left < right {
		return left
	}
	return right
}
