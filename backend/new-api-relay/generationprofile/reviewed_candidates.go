package generationprofile

import (
	"fmt"
	"sort"
	"strings"

	"github.com/QuantumNous/new-api/dto"
)

// ReviewedAcceptanceCandidate is a compiled, code-reviewed model declaration.
// It is discovery metadata only: it does not prove that a provider account,
// credential, native channel, accepted route, price or customer grant exists.
type ReviewedAcceptanceCandidate struct {
	ProviderModelID   string
	PublicModelID     string
	DisplayName       string
	NativeChannelType int
	AdapterProfileID  string
	Capability        dto.PlatformGenerationCapabilities
}

// ReviewedAcceptanceCandidates returns only exact current declarations that
// may begin route onboarding. Historical, deprecated and unverified records
// are deliberately excluded. Every returned capability is rechecked against
// its immutable adapter profile and exact provider-model binding.
func ReviewedAcceptanceCandidates() ([]ReviewedAcceptanceCandidate, error) {
	candidates := make([]ReviewedAcceptanceCandidate, 0)

	seedanceModels, err := SeedanceModelCatalog()
	if err != nil {
		return nil, err
	}
	for _, providerModel := range seedanceModels {
		if providerModel.Lifecycle != "acceptance_candidate" || !providerModel.NewRoutesAllowed {
			continue
		}
		profile, ok := Get(providerModel.AdapterProfileID)
		if !ok {
			return nil, fmt.Errorf("reviewed candidate %q has no adapter profile", providerModel.PublicModelID)
		}
		capability := providerModel.CompatibleCapability()
		if err := ValidateSeedanceModelBinding(profile, providerModel.ProviderModelID, capability); err != nil {
			return nil, fmt.Errorf("reviewed candidate %q is invalid: %w", providerModel.PublicModelID, err)
		}
		candidates = append(candidates, ReviewedAcceptanceCandidate{
			ProviderModelID:   providerModel.ProviderModelID,
			PublicModelID:     providerModel.PublicModelID,
			DisplayName:       providerModel.DisplayName,
			NativeChannelType: profile.NativeChannelType,
			AdapterProfileID:  profile.ID,
			Capability:        capability,
		})
	}

	miniMaxModels, err := MiniMaxH3ModelCatalog()
	if err != nil {
		return nil, err
	}
	for _, providerModel := range miniMaxModels {
		if providerModel.Lifecycle != "acceptance_candidate" || !providerModel.NewRoutesAllowed {
			continue
		}
		profile, ok := Get(providerModel.AdapterProfileID)
		if !ok {
			return nil, fmt.Errorf("reviewed candidate %q has no adapter profile", providerModel.PublicModelID)
		}
		capability := providerModel.CompatibleCapability()
		if err := ValidateMiniMaxH3ModelBinding(profile, providerModel.ProviderModelID, capability); err != nil {
			return nil, fmt.Errorf("reviewed candidate %q is invalid: %w", providerModel.PublicModelID, err)
		}
		candidates = append(candidates, ReviewedAcceptanceCandidate{
			ProviderModelID:   providerModel.ProviderModelID,
			PublicModelID:     providerModel.PublicModelID,
			DisplayName:       providerModel.DisplayName,
			NativeChannelType: profile.NativeChannelType,
			AdapterProfileID:  profile.ID,
			Capability:        capability,
		})
	}

	googleModels, err := GoogleVideoModelCatalog()
	if err != nil {
		return nil, err
	}
	for _, providerModel := range googleModels {
		if providerModel.Lifecycle != "acceptance_candidate" || !providerModel.NewRoutesAllowed {
			continue
		}
		profile, ok := Get(providerModel.AdapterProfileID)
		if !ok {
			return nil, fmt.Errorf("reviewed candidate %q has no adapter profile", providerModel.PublicModelID)
		}
		capability := providerModel.CompatibleCapability()
		if profile.NativeChannelType != providerModel.NativeChannelType {
			return nil, fmt.Errorf("reviewed candidate %q has an inconsistent native channel", providerModel.PublicModelID)
		}
		if err := ValidateGoogleVideoModelBinding(profile, providerModel.ProviderModelID, capability); err != nil {
			return nil, fmt.Errorf("reviewed candidate %q is invalid: %w", providerModel.PublicModelID, err)
		}
		candidates = append(candidates, ReviewedAcceptanceCandidate{
			ProviderModelID:   providerModel.ProviderModelID,
			PublicModelID:     providerModel.PublicModelID,
			DisplayName:       providerModel.DisplayName,
			NativeChannelType: profile.NativeChannelType,
			AdapterProfileID:  profile.ID,
			Capability:        capability,
		})
	}

	seen := make(map[string]struct{}, len(candidates))
	for _, candidate := range candidates {
		if candidate.PublicModelID == "" || strings.TrimSpace(candidate.PublicModelID) != candidate.PublicModelID ||
			candidate.ProviderModelID == "" || strings.TrimSpace(candidate.ProviderModelID) != candidate.ProviderModelID ||
			candidate.DisplayName == "" || strings.TrimSpace(candidate.DisplayName) != candidate.DisplayName ||
			candidate.AdapterProfileID == "" || candidate.NativeChannelType <= 0 ||
			candidate.Capability.SchemaVersion == 0 || len(candidate.Capability.Modes) == 0 {
			return nil, fmt.Errorf("reviewed acceptance candidate identity or capability is incomplete")
		}
		if _, duplicate := seen[candidate.PublicModelID]; duplicate {
			return nil, fmt.Errorf("reviewed acceptance candidate public model %q is duplicated", candidate.PublicModelID)
		}
		seen[candidate.PublicModelID] = struct{}{}
		profile, ok := Get(candidate.AdapterProfileID)
		if !ok || profile.NativeChannelType != candidate.NativeChannelType {
			return nil, fmt.Errorf("reviewed candidate %q adapter profile is unavailable", candidate.PublicModelID)
		}
		if err := profile.ValidateImmutableContract(); err != nil {
			return nil, fmt.Errorf("reviewed candidate %q adapter profile is invalid: %w", candidate.PublicModelID, err)
		}
		if err := profile.ValidateNarrowing(candidate.Capability); err != nil {
			return nil, fmt.Errorf("reviewed candidate %q capability is invalid: %w", candidate.PublicModelID, err)
		}
	}
	sort.Slice(candidates, func(left, right int) bool {
		return candidates[left].PublicModelID < candidates[right].PublicModelID
	})
	return candidates, nil
}
