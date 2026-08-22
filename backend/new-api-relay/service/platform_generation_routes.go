package service

import (
	"fmt"
	"sort"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
)

// SyncPlatformGenerationProviderRoutes validates every declared native
// channel/key binding and materializes one admission row per model mode.
func SyncPlatformGenerationProviderRoutes() error {
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return snapshot.err
	}
	modelIDs := make([]string, 0, len(snapshot.routes))
	for modelID := range snapshot.routes {
		modelIDs = append(modelIDs, modelID)
	}
	sort.Strings(modelIDs)
	desired := make([]model.PlatformGenerationProviderRoute, 0)
	for _, modelID := range modelIDs {
		declarations := snapshot.routes[modelID]
		for _, declaration := range declarations {
			channel, err := model.GetChannelById(declaration.ChannelID, true)
			if err != nil {
				return fmt.Errorf("Relay route %q channel %d is unavailable: %w", declaration.RouteID, declaration.ChannelID, err)
			}
			if PlatformRelayProductionSecurityEnabled() &&
				(channel.Type <= constant.ChannelTypeUnknown || channel.Type >= constant.ChannelTypeDummy) {
				return fmt.Errorf("Relay route %q does not reference a production channel adapter", declaration.RouteID)
			}
			if err := platformRouteAcceptanceMatchesNativeChannel(declaration, channel.Type); err != nil {
				return err
			}
			acceptedChannelType := declaration.NativeChannelType
			if acceptedChannelType == constant.ChannelTypeUnknown {
				// Development declarations may omit the acceptance field for
				// compatibility. Persist the adapter observed under the same startup
				// validation instead of leaving an admission-bypass sentinel.
				acceptedChannelType = channel.Type
			}
			key, err := channel.GetKeyAt(declaration.KeyIndex)
			if err != nil {
				return fmt.Errorf("Relay route %q key index is invalid: %w", declaration.RouteID, err)
			}
			fingerprint := fmt.Sprintf("%x", common.Sha256Raw([]byte(key)))
			if fingerprint != declaration.KeyFingerprint {
				return fmt.Errorf("Relay route %q key fingerprint does not match channel %d key %d", declaration.RouteID, declaration.ChannelID, declaration.KeyIndex)
			}
			modeNames := make([]string, 0, len(declaration.Capabilities.Modes))
			for modeName := range declaration.Capabilities.Modes {
				modeNames = append(modeNames, modeName)
			}
			sort.Strings(modeNames)
			for _, modeName := range modeNames {
				desired = append(desired, model.PlatformGenerationProviderRoute{
					RouteKey:            declaration.RouteID,
					Model:               modelID,
					Mode:                modeName,
					ProviderName:        declaration.ProviderName,
					AccountID:           declaration.AccountID,
					ChannelID:           declaration.ChannelID,
					AcceptedChannelType: acceptedChannelType,
					KeyIndex:            declaration.KeyIndex,
					KeyFingerprint:      declaration.KeyFingerprint,
					ChannelClass:        declaration.ChannelClass,
					UpstreamModel:       declaration.UpstreamModel,
					StagingReady:        declaration.StagingReady,
					ProductionReady:     declaration.ProductionReady,
					// Enabled means the route declaration is still active. Native
					// channel/key availability is checked transactionally for every
					// new admission, so a temporary disable does not require a
					// restart and never affects polling for existing tasks.
					Enabled:          true,
					RPMWindowSeconds: 60,
					RPMLimit:         declaration.RPMLimit,
					ActiveLimit:      declaration.ActiveTaskLimit,
				})
			}
		}
	}
	return model.SyncPlatformGenerationProviderRoutes(desired)
}

// platformGenerationProviderRouteReadiness keeps the staging acceptance gate
// independent from the production release gate. Development and test retain
// their existing behavior; invalid environments fail closed even if startup
// validation was bypassed by a direct worker invocation.
func platformGenerationProviderRouteReadiness(
	route model.PlatformGenerationProviderRoute,
	environment string,
) (bool, string) {
	switch environment {
	case "staging":
		if !route.StagingReady {
			return false, "route_not_staging_ready"
		}
	case "production":
		if !route.ProductionReady {
			return false, "route_not_production_ready"
		}
	case "", "development", "test":
	default:
		return false, "route_environment_invalid"
	}
	return true, ""
}
