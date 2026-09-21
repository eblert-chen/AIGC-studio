package service

import (
	"fmt"
	"sort"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

// SyncPlatformGenerationProviderRoutes validates every declared native
// channel/key binding and materializes one admission row per model mode.
func SyncPlatformGenerationProviderRoutes() error {
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return snapshot.err
	}
	return syncPlatformGenerationProviderRouteDeclarations(snapshot.routes)
}

// syncPlatformGenerationProviderRouteDeclarations is shared by normal Relay
// startup and the credential-late provider onboarding boundary. Callers must
// pass declarations returned by parsePlatformRelayCapabilities: raw or merely
// well-shaped JSON is never sufficient for a database route.
func syncPlatformGenerationProviderRouteDeclarations(
	routes map[string][]PlatformRelayRouteDeclaration,
) error {
	return model.DB.Transaction(func(tx *gorm.DB) error {
		return syncPlatformGenerationProviderRouteDeclarationsWithDB(tx, routes)
	})
}

func syncPlatformGenerationProviderRouteDeclarationsWithDB(
	tx *gorm.DB,
	routes map[string][]PlatformRelayRouteDeclaration,
) error {
	if tx == nil {
		return fmt.Errorf("Relay route transaction is required")
	}
	modelIDs := make([]string, 0, len(routes))
	for modelID := range routes {
		modelIDs = append(modelIDs, modelID)
	}
	sort.Strings(modelIDs)
	desired := make([]model.PlatformGenerationProviderRoute, 0)
	for _, modelID := range modelIDs {
		declarations := routes[modelID]
		for _, declaration := range declarations {
			var channel model.Channel
			if err := tx.Session(&gorm.Session{NewDB: true}).
				Where("id = ?", declaration.ChannelID).First(&channel).Error; err != nil {
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
			profileSnapshot := ""
			if declaration.ResolvedCapabilityProfileID != "" {
				profile, ok := generationprofile.Get(declaration.ResolvedCapabilityProfileID)
				if !ok || profile.Revision != declaration.ResolvedCapabilityProfileRevision {
					return fmt.Errorf("Relay route %q capability profile snapshot is unavailable", declaration.RouteID)
				}
				serializedProfile, err := common.Marshal(profile)
				if err != nil {
					return fmt.Errorf("Relay route %q capability profile snapshot cannot be serialized: %w", declaration.RouteID, err)
				}
				profileSnapshot = string(serializedProfile)
			}
			modeNames := make([]string, 0, len(declaration.Capabilities.Modes))
			for modeName := range declaration.Capabilities.Modes {
				modeNames = append(modeNames, modeName)
			}
			sort.Strings(modeNames)
			for _, modeName := range modeNames {
				desired = append(desired, model.PlatformGenerationProviderRoute{
					RouteKey:                       declaration.RouteID,
					Model:                          modelID,
					Mode:                           modeName,
					ProviderName:                   declaration.ProviderName,
					AccountID:                      declaration.AccountID,
					ChannelID:                      declaration.ChannelID,
					AcceptedChannelType:            acceptedChannelType,
					KeyIndex:                       declaration.KeyIndex,
					KeyFingerprint:                 declaration.KeyFingerprint,
					ChannelClass:                   declaration.ChannelClass,
					UpstreamModel:                  declaration.UpstreamModel,
					CapabilityProfileID:            declaration.ResolvedCapabilityProfileID,
					CapabilityProfileRevision:      declaration.ResolvedCapabilityProfileRevision,
					CapabilityProfileSnapshot:      profileSnapshot,
					ModelReleaseID:                 declaration.ResolvedModelReleaseID,
					ModelReleaseRevision:           declaration.ResolvedModelReleaseRevision,
					ModelReleaseCapabilityRevision: declaration.ResolvedModelCapabilityRevision,
					StagingReady:                   declaration.StagingReady,
					ProductionReady:                declaration.ProductionReady,
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
	return model.SyncPlatformGenerationProviderRoutesWithDB(tx, desired)
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
