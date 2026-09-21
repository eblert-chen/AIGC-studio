package generationprofile

import (
	"bytes"
	_ "embed"
	"fmt"
	"net/url"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
)

// One compiled, reviewed model directory is shared by provider conversion,
// signed release validation and offline onboarding. It contains no secrets,
// active route state, customer price or fabricated production evidence.
//
//go:embed minimax_h3_models.v1.json
var miniMaxH3CatalogJSON []byte

type miniMaxH3CatalogDocument struct {
	SchemaVersion int                      `json:"schema_version"`
	ReviewedAt    string                   `json:"reviewed_at"`
	Models        []MiniMaxH3ProviderModel `json:"models"`
}

// MiniMaxH3ProviderModel binds an exact official identity to the reviewed
// subset of the public contract, not every capability of the upstream model.
type MiniMaxH3ProviderModel struct {
	ProviderModelID  string                             `json:"provider_model_id"`
	PublicModelID    string                             `json:"public_model_id"`
	DisplayName      string                             `json:"display_name"`
	Lifecycle        string                             `json:"lifecycle"`
	EvidenceStatus   string                             `json:"evidence_status"`
	NewRoutesAllowed bool                               `json:"new_routes_allowed"`
	AdapterProfileID string                             `json:"adapter_profile_id"`
	OfficialSources  []string                           `json:"official_sources"`
	ImageRole        string                             `json:"image_role,omitempty"`
	Notes            []string                           `json:"notes"`
	Capability       dto.PlatformGenerationCapabilities `json:"capability"`
}

var (
	miniMaxH3CatalogOnce sync.Once
	miniMaxH3Catalog     []MiniMaxH3ProviderModel
	miniMaxH3CatalogErr  error
)

// MiniMaxH3ModelCatalog returns independent copies. NewRoutesAllowed means
// eligible to start onboarding; it never bypasses signed route acceptance.
func MiniMaxH3ModelCatalog() ([]MiniMaxH3ProviderModel, error) {
	miniMaxH3CatalogOnce.Do(func() {
		miniMaxH3Catalog, miniMaxH3CatalogErr = decodeMiniMaxH3Catalog(miniMaxH3CatalogJSON)
	})
	if miniMaxH3CatalogErr != nil {
		return nil, miniMaxH3CatalogErr
	}
	result := make([]MiniMaxH3ProviderModel, len(miniMaxH3Catalog))
	for index, model := range miniMaxH3Catalog {
		result[index] = cloneMiniMaxH3ProviderModel(model)
	}
	return result, nil
}

// ResolveMiniMaxH3ProviderModel is exact and case-sensitive. An H3 alias,
// Hailuo model or unreviewed future version cannot inherit an H3 capability.
func ResolveMiniMaxH3ProviderModel(providerModelID string) (MiniMaxH3ProviderModel, bool, error) {
	models, err := MiniMaxH3ModelCatalog()
	if err != nil {
		return MiniMaxH3ProviderModel{}, false, err
	}
	for _, model := range models {
		if model.ProviderModelID == providerModelID {
			return model, true, nil
		}
	}
	return MiniMaxH3ProviderModel{}, false, nil
}

func (model MiniMaxH3ProviderModel) CompatibleCapability() dto.PlatformGenerationCapabilities {
	return cloneMiniMaxH3ProviderModel(model).Capability
}

// ValidateMiniMaxH3ModelBinding prevents a signed identity from widening a
// model through a different profile, especially H3 Max borrowing reference
// generation, 2K or four-second output from the standard H3 implementation.
func ValidateMiniMaxH3ModelBinding(profile Profile, providerModelID string, capability dto.PlatformGenerationCapabilities) error {
	if profile.Protocol != MiniMaxH3VideoProtocolV2 {
		if strings.HasPrefix(strings.ToLower(providerModelID), "minimax-h3") {
			return fmt.Errorf("MiniMax H3 provider model %q requires a reviewed V2 video profile", providerModelID)
		}
		return nil
	}
	model, found, err := ResolveMiniMaxH3ProviderModel(providerModelID)
	if err != nil {
		return err
	}
	if !found {
		return fmt.Errorf("MiniMax H3 provider model %q has no reviewed adapter manifest", providerModelID)
	}
	if profile.ID != model.AdapterProfileID {
		return fmt.Errorf("MiniMax H3 adapter profile does not match provider model %q", providerModelID)
	}
	if err := profile.ValidateImmutableContract(); err != nil {
		return err
	}
	if err := profile.ValidateNarrowing(capability); err != nil {
		return err
	}
	modelCeiling := profile
	modelCeiling.Capability = model.Capability
	if err := modelCeiling.ValidateNarrowing(capability); err != nil {
		return fmt.Errorf("capability exceeds reviewed MiniMax H3 provider model %q: %w", providerModelID, err)
	}
	return nil
}

func decodeMiniMaxH3Catalog(raw []byte) ([]MiniMaxH3ProviderModel, error) {
	if err := common.RejectDuplicateJSONKeys(raw); err != nil {
		return nil, fmt.Errorf("decode MiniMax H3 provider manifest: %w", err)
	}
	var document miniMaxH3CatalogDocument
	if err := common.DecodeJsonDisallowUnknownFields(bytes.NewReader(raw), &document); err != nil {
		return nil, fmt.Errorf("decode MiniMax H3 provider manifest: %w", err)
	}
	date, err := time.Parse("2006-01-02", document.ReviewedAt)
	if document.SchemaVersion != 1 || len(document.Models) == 0 || err != nil || date.Format("2006-01-02") != document.ReviewedAt {
		return nil, fmt.Errorf("MiniMax H3 provider manifest schema or review date is invalid")
	}
	seenProvider := make(map[string]struct{}, len(document.Models))
	seenPublic := make(map[string]struct{}, len(document.Models))
	for _, model := range document.Models {
		if _, duplicate := seenProvider[model.ProviderModelID]; duplicate {
			return nil, fmt.Errorf("MiniMax H3 provider manifest contains duplicate model %q", model.ProviderModelID)
		}
		if _, duplicate := seenPublic[model.PublicModelID]; duplicate {
			return nil, fmt.Errorf("MiniMax H3 provider manifest contains duplicate public model %q", model.PublicModelID)
		}
		if err := validateMiniMaxH3CatalogModel(model); err != nil {
			return nil, err
		}
		seenProvider[model.ProviderModelID] = struct{}{}
		seenPublic[model.PublicModelID] = struct{}{}
	}
	return document.Models, nil
}

func validateMiniMaxH3CatalogModel(model MiniMaxH3ProviderModel) error {
	var expectedPublicID, expectedProfileID, expectedImageRole string
	switch model.ProviderModelID {
	case "MiniMax-H3":
		expectedPublicID, expectedProfileID, expectedImageRole = "minimax-h3", MiniMaxH3ReferenceVideoGenerationV1, "reference_image"
	case "MiniMax-H3-Max":
		expectedPublicID, expectedProfileID = "minimax-h3-max", MiniMaxH3MaxTextToVideoGenerationV1
	default:
		return fmt.Errorf("MiniMax H3 provider model %q is not reviewed", model.ProviderModelID)
	}
	if model.PublicModelID != expectedPublicID || model.AdapterProfileID != expectedProfileID || model.ImageRole != expectedImageRole {
		return fmt.Errorf("MiniMax H3 provider model identity or role policy is inconsistent")
	}
	if model.DisplayName == "" || strings.TrimSpace(model.DisplayName) != model.DisplayName ||
		!utf8.ValidString(model.DisplayName) || utf8.RuneCountInString(model.DisplayName) > 128 || strings.ContainsAny(model.DisplayName, "\r\n\t") {
		return fmt.Errorf("MiniMax H3 provider model display name is invalid")
	}
	if model.Lifecycle != "acceptance_candidate" || model.EvidenceStatus != "route_acceptance_required" || !model.NewRoutesAllowed {
		return fmt.Errorf("MiniMax H3 provider model cannot claim unverified production evidence")
	}
	if len(model.OfficialSources) == 0 || len(model.Notes) == 0 {
		return fmt.Errorf("MiniMax H3 provider model has no official provenance or compatibility notes")
	}
	for _, source := range model.OfficialSources {
		parsed, err := url.Parse(source)
		if err != nil || parsed.Scheme != "https" || parsed.User != nil || parsed.Port() != "" || parsed.RawQuery != "" || source != strings.TrimSpace(source) {
			return fmt.Errorf("MiniMax H3 provider model has invalid official provenance")
		}
		docsSource := (parsed.Host == "platform.minimaxi.com" || parsed.Host == "platform.minimax.io") && strings.HasPrefix(parsed.Path, "/docs/")
		codeSource := parsed.Host == "github.com" && (parsed.Path == "/MiniMax-AI/MiniMax-H3" || strings.HasPrefix(parsed.Path, "/MiniMax-AI/MiniMax-H3/"))
		if !docsSource && !codeSource {
			return fmt.Errorf("MiniMax H3 provider model has invalid official provenance")
		}
	}
	for _, note := range model.Notes {
		if note == "" || strings.TrimSpace(note) != note || !utf8.ValidString(note) {
			return fmt.Errorf("MiniMax H3 provider model compatibility note is invalid")
		}
	}
	profile, ok := Get(model.AdapterProfileID)
	if !ok || profile.Protocol != MiniMaxH3VideoProtocolV2 {
		return fmt.Errorf("MiniMax H3 provider model has no registered V2 profile")
	}
	if _, ok := model.Capability.Modes["text_to_video"]; !ok {
		return fmt.Errorf("MiniMax H3 provider model requires a text-only acceptance capability")
	}
	if err := profile.ValidateNarrowing(model.Capability); err != nil {
		return fmt.Errorf("MiniMax H3 provider model expands its profile: %w", err)
	}
	return nil
}

func cloneMiniMaxH3ProviderModel(model MiniMaxH3ProviderModel) MiniMaxH3ProviderModel {
	serialized, err := common.Marshal(model)
	if err != nil {
		panic(err) // Reviewed model records contain only JSON-safe values.
	}
	var cloned MiniMaxH3ProviderModel
	if err := common.Unmarshal(serialized, &cloned); err != nil {
		panic(err)
	}
	return cloned
}
