package service

import (
	"fmt"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
)

// platformGenerationProfileForModelMode returns a pre-assignment profile only
// when every eligible route happens to share it. Heterogeneous provider
// profiles are valid: public admission uses the failover-safe capability
// intersection and the executable profile is selected with the durable route.
func platformGenerationProfileForModelMode(modelID string, mode string) (generationprofile.Profile, bool, error) {
	routes, err := GetPlatformRelayRoutes(modelID, mode)
	if err != nil {
		return generationprofile.Profile{}, false, err
	}
	return platformGenerationProfileForRoutes(routes)
}

func platformGenerationProfileForRoutes(routes []PlatformRelayRouteDeclaration) (generationprofile.Profile, bool, error) {
	profileID := ""
	profileRevision := ""
	profileBindingSeen := false
	for _, route := range routes {
		routeProfileID := route.ResolvedCapabilityProfileID
		routeProfileRevision := route.ResolvedCapabilityProfileRevision
		if !profileBindingSeen {
			profileID = routeProfileID
			profileRevision = routeProfileRevision
			profileBindingSeen = true
			continue
		}
		if profileID != routeProfileID || profileRevision != routeProfileRevision {
			// A public alias may safely intersect heterogeneous provider adapter
			// profiles. There is intentionally no pre-assignment profile in that
			// case; the selected route supplies the executable snapshot.
			return generationprofile.Profile{}, false, nil
		}
	}
	if profileID == "" {
		return generationprofile.Profile{}, false, nil
	}
	profile, ok := generationprofile.Get(profileID)
	if !ok || profile.Revision != profileRevision {
		return generationprofile.Profile{}, false, fmt.Errorf("generation capability profile is unavailable")
	}
	return profile, true, nil
}

func platformGenerationProfileForProviderRoute(route model.PlatformGenerationProviderRoute) (generationprofile.Profile, bool, error) {
	routes, err := GetPlatformRelayRoutes(route.Model, route.Mode)
	if err != nil {
		return generationprofile.Profile{}, false, err
	}
	for _, declaration := range routes {
		if declaration.RouteID != route.RouteKey || declaration.ChannelID != route.ChannelID ||
			declaration.KeyIndex != route.KeyIndex || declaration.KeyFingerprint != route.KeyFingerprint ||
			declaration.UpstreamModel != route.UpstreamModel {
			continue
		}
		if declaration.ResolvedCapabilityProfileID == "" {
			return generationprofile.Profile{}, false, nil
		}
		profile, ok := generationprofile.Get(declaration.ResolvedCapabilityProfileID)
		if !ok || profile.Revision != declaration.ResolvedCapabilityProfileRevision {
			return generationprofile.Profile{}, false, fmt.Errorf("generation provider route capability profile is unavailable")
		}
		return profile, true, nil
	}
	return generationprofile.Profile{}, false, fmt.Errorf("generation provider route declaration is unavailable")
}

// platformGenerationProfileForStoredRequest resolves a request being built by
// the current fenced submission worker. Durable consumers must use
// platformGenerationProfileForStoredJob so the snapshot is also anchored to
// native-task recovery evidence.
func platformGenerationProfileForStoredRequest(request dto.PlatformGenerationRequest) (generationprofile.Profile, bool, error) {
	profile, found, err := generationprofile.ResolveSnapshot(request.Metadata)
	if err != nil || found {
		return profile, found, err
	}
	return platformGenerationProfileForModelMode(request.Model, request.Mode)
}

// platformGenerationProfileForStoredJob anchors the request snapshot to the
// immutable profile binding on the exact durable route assignment. The
// snapshot digest alone is not treated as authenticity evidence.
func platformGenerationProfileForStoredJob(
	job model.PlatformGenerationJob,
	request dto.PlatformGenerationRequest,
) (generationprofile.Profile, bool, error) {
	profile, found, err := generationprofile.ResolveSnapshot(request.Metadata)
	if err != nil {
		return generationprofile.Profile{}, false, err
	}
	if found {
		if err := model.ValidatePlatformGenerationRequestSnapshotBinding(job); err != nil {
			return generationprofile.Profile{}, false, err
		}
		return profile, true, nil
	}
	return platformGenerationProfileForStoredRequest(request)
}
