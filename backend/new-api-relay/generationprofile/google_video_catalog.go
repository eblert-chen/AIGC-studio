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
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
)

// This reviewed directory records exact Google model IDs for each API
// surface. Gemini Developer API preview IDs and Vertex GA IDs are not aliases,
// even when they share one stable public product identity.
//
//go:embed google_video_models.v1.json
var googleVideoCatalogJSON []byte

type googleVideoCatalogDocument struct {
	SchemaVersion int                        `json:"schema_version"`
	ReviewedAt    string                     `json:"reviewed_at"`
	Models        []GoogleVideoProviderModel `json:"models"`
}

type GoogleVideoProviderModel struct {
	ProviderModelID    string                             `json:"provider_model_id"`
	PublicModelID      string                             `json:"public_model_id"`
	DisplayName        string                             `json:"display_name"`
	ProductFamily      string                             `json:"product_family"`
	AccessSurface      string                             `json:"access_surface"`
	NativeChannelType  int                                `json:"native_channel_type"`
	Lifecycle          string                             `json:"lifecycle"`
	EvidenceStatus     string                             `json:"evidence_status"`
	NewRoutesAllowed   bool                               `json:"new_routes_allowed"`
	AdapterProfileID   string                             `json:"adapter_profile_id"`
	OfficialSources    []string                           `json:"official_sources"`
	ProviderSemantics  []string                           `json:"provider_semantics"`
	PublishedSemantics []string                           `json:"published_semantics"`
	BlockedSemantics   []string                           `json:"blocked_semantics"`
	GeneratesAudio     bool                               `json:"generates_audio"`
	Notes              []string                           `json:"notes"`
	Capability         dto.PlatformGenerationCapabilities `json:"capability"`
}

var (
	googleVideoCatalogOnce sync.Once
	googleVideoCatalog     []GoogleVideoProviderModel
	googleVideoCatalogErr  error
)

func GoogleVideoModelCatalog() ([]GoogleVideoProviderModel, error) {
	googleVideoCatalogOnce.Do(func() {
		googleVideoCatalog, googleVideoCatalogErr = decodeGoogleVideoCatalog(googleVideoCatalogJSON)
	})
	if googleVideoCatalogErr != nil {
		return nil, googleVideoCatalogErr
	}
	result := make([]GoogleVideoProviderModel, len(googleVideoCatalog))
	for index, model := range googleVideoCatalog {
		result[index] = cloneGoogleVideoProviderModel(model)
	}
	return result, nil
}

// ResolveGoogleVideoProviderModel is exact and channel-aware. In particular,
// a Vertex GA identifier cannot silently replace a Gemini API preview model.
func ResolveGoogleVideoProviderModel(nativeChannelType int, providerModelID string) (GoogleVideoProviderModel, bool, error) {
	models, err := GoogleVideoModelCatalog()
	if err != nil {
		return GoogleVideoProviderModel{}, false, err
	}
	for _, model := range models {
		if model.NativeChannelType == nativeChannelType && model.ProviderModelID == providerModelID {
			return model, true, nil
		}
	}
	return GoogleVideoProviderModel{}, false, nil
}

func (model GoogleVideoProviderModel) CompatibleCapability() dto.PlatformGenerationCapabilities {
	return cloneGoogleVideoProviderModel(model).Capability
}

func ValidateGoogleVideoModelBinding(profile Profile, providerModelID string, capability dto.PlatformGenerationCapabilities) error {
	if !isGoogleVideoProtocol(profile.Protocol) {
		if strings.HasPrefix(providerModelID, "gemini-omni-") || strings.HasPrefix(providerModelID, "veo-") {
			return fmt.Errorf("Google video provider model %q requires a reviewed Google video profile", providerModelID)
		}
		return nil
	}
	model, found, err := ResolveGoogleVideoProviderModel(profile.NativeChannelType, providerModelID)
	if err != nil {
		return err
	}
	if !found {
		return fmt.Errorf("Google video provider model %q has no reviewed manifest for native channel %d", providerModelID, profile.NativeChannelType)
	}
	if model.Lifecycle != "acceptance_candidate" || model.AdapterProfileID != profile.ID {
		return fmt.Errorf("Google video adapter profile does not match provider model %q", providerModelID)
	}
	if err := profile.ValidateNarrowing(capability); err != nil {
		return err
	}
	modelCeiling := profile
	modelCeiling.ID = model.ProviderModelID
	modelCeiling.Capability = model.Capability
	if err := modelCeiling.ValidateNarrowing(capability); err != nil {
		return fmt.Errorf("capability exceeds reviewed Google video model %q: %w", providerModelID, err)
	}
	return nil
}

func decodeGoogleVideoCatalog(raw []byte) ([]GoogleVideoProviderModel, error) {
	if err := common.RejectDuplicateJSONKeys(raw); err != nil {
		return nil, fmt.Errorf("decode Google video provider manifest: %w", err)
	}
	var document googleVideoCatalogDocument
	if err := common.DecodeJsonDisallowUnknownFields(bytes.NewReader(raw), &document); err != nil {
		return nil, fmt.Errorf("decode Google video provider manifest: %w", err)
	}
	date, err := time.Parse("2006-01-02", document.ReviewedAt)
	if document.SchemaVersion != 1 || len(document.Models) == 0 || err != nil || date.Format("2006-01-02") != document.ReviewedAt {
		return nil, fmt.Errorf("Google video provider manifest schema or review date is invalid")
	}
	seenProvider := make(map[string]struct{}, len(document.Models))
	seenPublicSurface := make(map[string]struct{}, len(document.Models))
	for _, model := range document.Models {
		providerKey := fmt.Sprintf("%d\x00%s", model.NativeChannelType, model.ProviderModelID)
		publicKey := fmt.Sprintf("%d\x00%s", model.NativeChannelType, model.PublicModelID)
		if _, duplicate := seenProvider[providerKey]; duplicate {
			return nil, fmt.Errorf("Google video provider manifest contains duplicate model %q", model.ProviderModelID)
		}
		if _, duplicate := seenPublicSurface[publicKey]; duplicate {
			return nil, fmt.Errorf("Google video provider manifest contains duplicate public model %q on one API surface", model.PublicModelID)
		}
		if err := validateGoogleVideoCatalogModel(model); err != nil {
			return nil, err
		}
		seenProvider[providerKey] = struct{}{}
		seenPublicSurface[publicKey] = struct{}{}
	}
	return document.Models, nil
}

type googleVideoIdentity struct {
	publicID, family, surface, profileID string
	channelType                          int
	generatesAudio                       bool
}

func reviewedGoogleVideoIdentity(channelType int, providerID string) (googleVideoIdentity, bool) {
	identities := map[string]googleVideoIdentity{
		fmt.Sprintf("%d\x00gemini-omni-1.1-flash", constant.ChannelTypeGemini): {
			"gemini-omni-1.1-flash", "omni", "gemini_api", GoogleGeminiOmni11FlashBasicVideoV1, constant.ChannelTypeGemini, true,
		},
		fmt.Sprintf("%d\x00veo-3.1-generate-preview", constant.ChannelTypeGemini): {
			"veo-3.1", "veo", "gemini_api", GoogleGeminiVeo31VideoV1, constant.ChannelTypeGemini, true,
		},
		fmt.Sprintf("%d\x00veo-3.1-fast-generate-preview", constant.ChannelTypeGemini): {
			"veo-3.1-fast", "veo", "gemini_api", GoogleGeminiVeo31VideoV1, constant.ChannelTypeGemini, true,
		},
	}
	identity, ok := identities[fmt.Sprintf("%d\x00%s", channelType, providerID)]
	return identity, ok
}

func validateGoogleVideoCatalogModel(model GoogleVideoProviderModel) error {
	expected, reviewed := reviewedGoogleVideoIdentity(model.NativeChannelType, model.ProviderModelID)
	if !reviewed || model.PublicModelID != expected.publicID || model.ProductFamily != expected.family ||
		model.AccessSurface != expected.surface || model.AdapterProfileID != expected.profileID ||
		model.NativeChannelType != expected.channelType || model.GeneratesAudio != expected.generatesAudio {
		return fmt.Errorf("Google video provider model identity or API surface is not reviewed")
	}
	if !profileIDPattern.MatchString(model.ProviderModelID) || !profileIDPattern.MatchString(model.PublicModelID) ||
		model.DisplayName == "" || strings.TrimSpace(model.DisplayName) != model.DisplayName || !utf8.ValidString(model.DisplayName) ||
		utf8.RuneCountInString(model.DisplayName) > 128 || strings.ContainsAny(model.DisplayName, "\r\n\t") {
		return fmt.Errorf("Google video provider model identity is invalid")
	}
	if model.Lifecycle != "acceptance_candidate" || model.EvidenceStatus != "route_acceptance_required" || !model.NewRoutesAllowed {
		return fmt.Errorf("Google video provider model cannot claim unverified production evidence")
	}
	if len(model.OfficialSources) == 0 || len(model.Notes) == 0 {
		return fmt.Errorf("Google video provider model has no official provenance or compatibility notes")
	}
	for _, source := range model.OfficialSources {
		parsed, err := url.Parse(source)
		if err != nil || parsed.Scheme != "https" || parsed.User != nil || parsed.Port() != "" || parsed.RawQuery != "" || source != strings.TrimSpace(source) {
			return fmt.Errorf("Google video provider model has invalid official provenance")
		}
		developerDocs := parsed.Host == "ai.google.dev" && strings.HasPrefix(parsed.Path, "/gemini-api/docs/")
		cloudDocs := (parsed.Host == "cloud.google.com" || parsed.Host == "docs.cloud.google.com") &&
			(strings.HasPrefix(parsed.Path, "/vertex-ai/") || strings.HasPrefix(parsed.Path, "/gemini-enterprise-agent-platform/"))
		if !developerDocs && !cloudDocs {
			return fmt.Errorf("Google video provider model has invalid official provenance")
		}
	}
	for _, note := range model.Notes {
		if note == "" || strings.TrimSpace(note) != note || !utf8.ValidString(note) {
			return fmt.Errorf("Google video provider model compatibility note is invalid")
		}
	}
	if err := validateGoogleSemantics(model); err != nil {
		return err
	}
	profile, ok := Get(model.AdapterProfileID)
	if !ok || !isGoogleVideoProtocol(profile.Protocol) || profile.NativeChannelType != model.NativeChannelType {
		return fmt.Errorf("Google video provider model has no registered API-surface profile")
	}
	if err := profile.ValidateNarrowing(model.Capability); err != nil {
		return fmt.Errorf("Google video provider model expands its profile: %w", err)
	}
	return nil
}

func validateGoogleSemantics(model GoogleVideoProviderModel) error {
	allowed := map[string]struct{}{
		"text_to_video": {}, "image_to_video": {}, "reference_to_video": {},
		"interpolation": {}, "edit": {}, "extend": {},
	}
	provider := make(map[string]struct{}, len(model.ProviderSemantics))
	for _, semantic := range model.ProviderSemantics {
		if _, ok := allowed[semantic]; !ok {
			return fmt.Errorf("Google video provider model has an unknown provider semantic")
		}
		if _, duplicate := provider[semantic]; duplicate {
			return fmt.Errorf("Google video provider model has duplicate provider semantics")
		}
		provider[semantic] = struct{}{}
	}
	published := make(map[string]struct{}, len(model.PublishedSemantics))
	for _, semantic := range model.PublishedSemantics {
		if _, ok := provider[semantic]; !ok {
			return fmt.Errorf("Google video provider model publishes an unsupported semantic")
		}
		if _, duplicate := published[semantic]; duplicate {
			return fmt.Errorf("Google video provider model has duplicate published semantics")
		}
		published[semantic] = struct{}{}
		if _, modeExists := model.Capability.Modes[semantic]; !modeExists {
			return fmt.Errorf("Google video published semantic has no public capability mode")
		}
	}
	blocked := make(map[string]struct{}, len(model.BlockedSemantics))
	for _, semantic := range model.BlockedSemantics {
		if _, ok := provider[semantic]; !ok {
			return fmt.Errorf("Google video provider model blocks an unknown semantic")
		}
		if _, duplicate := published[semantic]; duplicate {
			return fmt.Errorf("Google video provider semantic cannot be both published and blocked")
		}
		if _, duplicate := blocked[semantic]; duplicate {
			return fmt.Errorf("Google video provider model has duplicate blocked semantics")
		}
		blocked[semantic] = struct{}{}
	}
	if len(provider) == 0 || len(published) == 0 || len(published)+len(blocked) != len(provider) {
		return fmt.Errorf("Google video provider semantic inventory is incomplete")
	}
	if _, ok := published["text_to_video"]; !ok {
		return fmt.Errorf("Google video model requires a text-only acceptance semantic")
	}
	for modeName := range model.Capability.Modes {
		if _, ok := published[modeName]; !ok {
			return fmt.Errorf("Google video capability mode is not a published semantic")
		}
	}
	return nil
}

func cloneGoogleVideoProviderModel(model GoogleVideoProviderModel) GoogleVideoProviderModel {
	serialized, err := common.Marshal(model)
	if err != nil {
		panic(err)
	}
	var cloned GoogleVideoProviderModel
	if err := common.Unmarshal(serialized, &cloned); err != nil {
		panic(err)
	}
	return cloned
}
