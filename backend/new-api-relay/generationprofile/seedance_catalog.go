package generationprofile

import (
	"bytes"
	_ "embed"
	"fmt"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
)

// This is the single reviewed provider-model matrix shared by release
// validation, the adapter and offline onboarding tools. It is not evidence of
// account access, a price, a successful paid request or route readiness.
//
//go:embed seedance_models.v1.json
var seedanceCatalogJSON []byte

type seedanceCatalogDocument struct {
	SchemaVersion int                     `json:"schema_version"`
	ReviewedAt    string                  `json:"reviewed_at"`
	Models        []SeedanceProviderModel `json:"models"`
}

// SeedanceProviderModel binds one exact official model version to the subset
// expressible by the frozen public generation contract. Historical records
// deliberately have no executable capability. PublicModelID is a proposed
// stable product name, not a provider alias or a routing decision.
type SeedanceProviderModel struct {
	ProviderModelID             string                             `json:"provider_model_id"`
	PublicModelID               string                             `json:"public_model_id"`
	DisplayName                 string                             `json:"display_name"`
	Lifecycle                   string                             `json:"lifecycle"`
	EvidenceStatus              string                             `json:"evidence_status"`
	NewRoutesAllowed            bool                               `json:"new_routes_allowed"`
	EOMAt                       string                             `json:"eom_at,omitempty"`
	EOSAt                       string                             `json:"eos_at,omitempty"`
	ReplacementProviderModelID  string                             `json:"replacement_provider_model_id,omitempty"`
	AdapterProfileID            string                             `json:"adapter_profile_id,omitempty"`
	CompatibleAdapterProfileIDs []string                           `json:"compatible_adapter_profile_ids,omitempty"`
	OfficialSources             []string                           `json:"official_sources"`
	ImageRole                   string                             `json:"image_role,omitempty"`
	OmniReferenceTaskType       string                             `json:"omni_reference_task_type,omitempty"`
	GeneratesAudio              bool                               `json:"generates_audio,omitempty"`
	Notes                       []string                           `json:"notes,omitempty"`
	Capability                  dto.PlatformGenerationCapabilities `json:"capability,omitempty"`
}

var (
	seedanceCatalogOnce sync.Once
	seedanceCatalog     []SeedanceProviderModel
	seedanceCatalogErr  error
)

// SeedanceModelCatalog returns an independent copy, including historical
// lifecycle evidence. Callers must use NewRoutesAllowed for new onboarding;
// deprecated models are only for already enabled accounts/routes until EOS.
func SeedanceModelCatalog() ([]SeedanceProviderModel, error) {
	seedanceCatalogOnce.Do(func() {
		seedanceCatalog, seedanceCatalogErr = decodeSeedanceCatalog(seedanceCatalogJSON)
	})
	if seedanceCatalogErr != nil {
		return nil, seedanceCatalogErr
	}
	result := make([]SeedanceProviderModel, len(seedanceCatalog))
	for index, model := range seedanceCatalog {
		result[index] = cloneSeedanceProviderModel(model)
	}
	return result, nil
}

// ResolveSeedanceProviderModel requires an exact versioned provider identity.
// It never treats a retired/unverified version as an alias for its successor.
func ResolveSeedanceProviderModel(providerModelID string) (SeedanceProviderModel, bool, error) {
	models, err := SeedanceModelCatalog()
	if err != nil {
		return SeedanceProviderModel{}, false, err
	}
	for _, model := range models {
		if model.ProviderModelID == providerModelID {
			return model, true, nil
		}
	}
	return SeedanceProviderModel{}, false, nil
}

// CompatibleCapability is a defensive copy, not the raw/full provider API.
// For example reference-based V2V does not claim video editing or extension.
func (model SeedanceProviderModel) CompatibleCapability() dto.PlatformGenerationCapabilities {
	return cloneSeedanceProviderModel(model).Capability
}

func (model SeedanceProviderModel) AcceptsProfile(profileID string) bool {
	return profileID == model.AdapterProfileID || containsString(model.CompatibleAdapterProfileIDs, profileID)
}

// ValidateSubmissionAt applies lifecycle only to a new provider submission.
// Polling/recovering an existing provider task must not use this check.
func (model SeedanceProviderModel) ValidateSubmissionAt(now time.Time) error {
	if model.Lifecycle != "acceptance_candidate" && model.Lifecycle != "deprecated" {
		return fmt.Errorf("Seedance provider model %q is not eligible for a new route or submission", model.ProviderModelID)
	}
	if model.EOSAt != "" {
		eos, err := time.Parse(time.RFC3339, model.EOSAt)
		if err != nil || !now.Before(eos) {
			return fmt.Errorf("Seedance provider model %q has reached end of service (%s)", model.ProviderModelID, model.EOSAt)
		}
	}
	return nil
}

// ValidateSeedanceModelBinding runs before a model/route advertises a
// capability. The reusable adapter ceiling alone is insufficient: e.g. Mini
// cannot inherit 1080p and Pro Fast cannot inherit a second input frame.
// Unrelated image/video protocols remain provider-model agnostic.
// Deprecated bindings remain readable; onboarding must separately honor
// NewRoutesAllowed, and new provider submissions enforce EOS.
func ValidateSeedanceModelBinding(profile Profile, providerModelID string, capability dto.PlatformGenerationCapabilities) error {
	if profile.Protocol != VolcengineArkVideoProtocolV1 {
		if strings.HasPrefix(providerModelID, "doubao-seedance-") {
			return fmt.Errorf("Seedance provider model %q requires a reviewed Ark video profile", providerModelID)
		}
		return nil
	}
	model, found, err := ResolveSeedanceProviderModel(providerModelID)
	if err != nil {
		return err
	}
	if !found {
		return fmt.Errorf("Seedance provider model %q has no reviewed adapter manifest", providerModelID)
	}
	if model.Lifecycle != "acceptance_candidate" && model.Lifecycle != "deprecated" {
		return fmt.Errorf("Seedance provider model %q has no executable reviewed capability", providerModelID)
	}
	if !model.AcceptsProfile(profile.ID) {
		return fmt.Errorf("Seedance adapter profile does not match provider model %q", providerModelID)
	}
	if err := profile.ValidateNarrowing(capability); err != nil {
		return err
	}
	modelCeiling := profile
	modelCeiling.ID = model.ProviderModelID
	modelCeiling.Capability = model.Capability
	if err := modelCeiling.ValidateNarrowing(capability); err != nil {
		return fmt.Errorf("capability exceeds reviewed Seedance provider model %q: %w", providerModelID, err)
	}
	return nil
}

func decodeSeedanceCatalog(raw []byte) ([]SeedanceProviderModel, error) {
	if err := common.RejectDuplicateJSONKeys(raw); err != nil {
		return nil, fmt.Errorf("decode Seedance provider manifest: %w", err)
	}
	var document seedanceCatalogDocument
	if err := common.DecodeJsonDisallowUnknownFields(bytes.NewReader(raw), &document); err != nil {
		return nil, fmt.Errorf("decode Seedance provider manifest: %w", err)
	}
	date, err := time.Parse("2006-01-02", document.ReviewedAt)
	if document.SchemaVersion != 1 || len(document.Models) == 0 || err != nil || date.Format("2006-01-02") != document.ReviewedAt {
		return nil, fmt.Errorf("Seedance provider manifest schema or review date is invalid")
	}
	seen := make(map[string]struct{}, len(document.Models))
	seenPublic := make(map[string]struct{}, len(document.Models))
	for _, model := range document.Models {
		if err := validateSeedanceCatalogModel(model); err != nil {
			return nil, err
		}
		if _, duplicate := seen[model.ProviderModelID]; duplicate {
			return nil, fmt.Errorf("Seedance provider manifest contains duplicate model %q", model.ProviderModelID)
		}
		seen[model.ProviderModelID] = struct{}{}
		if _, duplicate := seenPublic[model.PublicModelID]; duplicate {
			return nil, fmt.Errorf("Seedance provider manifest contains duplicate public model %q", model.PublicModelID)
		}
		seenPublic[model.PublicModelID] = struct{}{}
	}
	return document.Models, nil
}

func validateSeedanceCatalogModel(model SeedanceProviderModel) error {
	if !strings.HasPrefix(model.ProviderModelID, "doubao-seedance-") || !profileIDPattern.MatchString(model.ProviderModelID) ||
		!profileIDPattern.MatchString(model.PublicModelID) || model.DisplayName == "" || strings.TrimSpace(model.DisplayName) != model.DisplayName {
		return fmt.Errorf("Seedance provider model identity is invalid")
	}
	if len(model.OfficialSources) == 0 {
		return fmt.Errorf("Seedance provider model %q has no official provenance", model.ProviderModelID)
	}
	for _, source := range model.OfficialSources {
		parsed, err := url.Parse(source)
		if err != nil || parsed.Scheme != "https" || parsed.User != nil ||
			(!strings.HasSuffix(parsed.Hostname(), ".volcengine.com") && parsed.Hostname() != "volcengine.com") ||
			!strings.HasPrefix(parsed.Path, "/docs/82379/") {
			return fmt.Errorf("Seedance provider model %q has invalid official Ark provenance", model.ProviderModelID)
		}
	}
	for _, value := range []string{model.EOMAt, model.EOSAt} {
		if value == "" {
			continue
		}
		parsed, err := time.Parse(time.RFC3339, value)
		if err != nil || parsed.UTC().Format(time.RFC3339) != value {
			return fmt.Errorf("Seedance provider model %q has invalid lifecycle dates", model.ProviderModelID)
		}
	}
	if model.EOMAt != "" && model.EOSAt != "" && model.EOMAt >= model.EOSAt {
		return fmt.Errorf("Seedance provider model %q lifecycle dates are reversed", model.ProviderModelID)
	}
	switch model.Lifecycle {
	case "acceptance_candidate":
		if !model.NewRoutesAllowed || model.EvidenceStatus != "route_acceptance_required" {
			return fmt.Errorf("Seedance current model evidence is inconsistent")
		}
	case "deprecated":
		if model.NewRoutesAllowed || model.EvidenceStatus != "existing_routes_only" || model.EOMAt == "" || model.EOSAt == "" {
			return fmt.Errorf("Seedance deprecated model evidence is inconsistent")
		}
	case "retired", "unverified":
		if model.NewRoutesAllowed || model.AdapterProfileID != "" || len(model.Capability.Modes) > 0 || model.Capability.SchemaVersion != 0 ||
			(model.Lifecycle == "retired" && model.EvidenceStatus != "retired_no_new_routes") ||
			(model.Lifecycle == "unverified" && model.EvidenceStatus != "official_model_id_unverified") {
			return fmt.Errorf("Seedance historical model cannot declare executable capability")
		}
		return nil
	default:
		return fmt.Errorf("Seedance provider model %q has an invalid lifecycle", model.ProviderModelID)
	}
	profile, ok := Get(model.AdapterProfileID)
	if !ok || profile.Protocol != VolcengineArkVideoProtocolV1 {
		return fmt.Errorf("Seedance provider model %q has no registered video profile", model.ProviderModelID)
	}
	if err := profile.ValidateNarrowing(model.Capability); err != nil {
		return fmt.Errorf("Seedance provider model %q expands its profile: %w", model.ProviderModelID, err)
	}
	seenProfiles := map[string]struct{}{model.AdapterProfileID: {}}
	for _, profileID := range model.CompatibleAdapterProfileIDs {
		compatible, ok := Get(profileID)
		_, duplicate := seenProfiles[profileID]
		if !ok || compatible.Protocol != VolcengineArkVideoProtocolV1 || duplicate {
			return fmt.Errorf("Seedance provider model %q has an invalid compatible profile", model.ProviderModelID)
		}
		seenProfiles[profileID] = struct{}{}
	}
	switch model.ImageRole {
	case "reference_image", "first_frame", "first_last_frame":
	default:
		return fmt.Errorf("Seedance provider model %q has invalid image role policy", model.ProviderModelID)
	}
	if model.OmniReferenceTaskType != "" && (model.OmniReferenceTaskType != "reference" || model.ImageRole != "reference_image") {
		return fmt.Errorf("Seedance provider model %q must use reference generation semantics", model.ProviderModelID)
	}
	for modeName, mode := range model.Capability.Modes {
		if modeName == "text_to_video" && (mode.Limits.MaxImages != 0 || mode.Limits.MaxVideos != 0 || mode.Limits.MaxAudio != 0) {
			return fmt.Errorf("Seedance text_to_video capability cannot accept assets")
		}
		if (model.ImageRole == "first_frame" && mode.Limits.MaxImages > 1) || (model.ImageRole == "first_last_frame" && mode.Limits.MaxImages > 2) {
			return fmt.Errorf("Seedance provider model image cardinality exceeds its role policy")
		}
	}
	return nil
}

func cloneSeedanceProviderModel(model SeedanceProviderModel) SeedanceProviderModel {
	serialized, err := common.Marshal(model)
	if err != nil {
		panic(err) // This reviewed value contains only JSON-safe contract fields.
	}
	var cloned SeedanceProviderModel
	if err := common.Unmarshal(serialized, &cloned); err != nil {
		panic(err)
	}
	return cloned
}
