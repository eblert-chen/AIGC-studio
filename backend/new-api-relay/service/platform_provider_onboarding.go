package service

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const (
	CredentialLateProviderSchemaVersion   = 1
	CredentialLateProviderPhaseStage      = "stage"
	CredentialLateProviderPhasePlanRoutes = "plan-routes"
	CredentialLateProviderPhaseFinalize   = "finalize"

	CredentialLateProviderVolcengineArk = "volcengine-ark"
	CredentialLateProviderMiniMax       = "minimax"
	CredentialLateProviderGoogleGemini  = "google-gemini-api"

	credentialLateProviderChannelTag = "platform-provider-onboarding-v1"
	credentialLateProviderMarkerKey  = "credential_late_provider"
)

// CredentialLateProviderInput is decoded only from an owner-only protected
// file by the standalone onboarding command. String deliberately redacts it so
// an error formatter cannot disclose the upstream credential.
type CredentialLateProviderInput struct {
	SchemaVersion  int      `json:"schema_version"`
	Provider       string   `json:"provider"`
	Region         string   `json:"region,omitempty"`
	AccountID      string   `json:"account_id"`
	ChannelID      int      `json:"channel_id"`
	APIKey         string   `json:"api_key"`
	PublicModelIDs []string `json:"public_model_ids"`
}

func (CredentialLateProviderInput) String() string   { return "CredentialLateProviderInput{redacted}" }
func (CredentialLateProviderInput) GoString() string { return "CredentialLateProviderInput{redacted}" }

type CredentialLateProviderResult struct {
	ChannelID       int      `json:"channel_id"`
	Provider        string   `json:"provider"`
	AccountID       string   `json:"account_id"`
	PublicModelIDs  []string `json:"public_model_ids"`
	RouteIDs        []string `json:"route_ids"`
	ChannelCreated  bool     `json:"channel_created"`
	RouteTestReady  bool     `json:"route_test_ready"`
	NativeAbilities string   `json:"native_abilities"`
}

// CredentialLateProviderRoutePlanOptions contains operator-owned route policy
// and audit metadata. Adapter profile IDs, revisions, provider model IDs and
// capabilities are deliberately absent: those facts come only from the exact
// reviewed catalogs compiled into this Relay image.
type CredentialLateProviderRoutePlanOptions struct {
	CurrentRoutesJSON []byte
	ReplaceAllRoutes  bool
	CreatedAt         string
	CreatedBy         string
	Reason            string
	RPMLimit          int
	ActiveTaskLimit   int
}

// CredentialLateProviderFinalizeOptions makes a destructive full-inventory
// replacement explicit. It is development-only; protected environments keep
// the existing preserve-all-routes and signed-acceptance requirements.
type CredentialLateProviderFinalizeOptions struct {
	ReplaceAllRoutes bool
}

type credentialLateProviderModel struct {
	ProviderModelID   string
	PublicModelID     string
	NativeChannelType int
	AdapterProfileID  string
	Capability        dto.PlatformGenerationCapabilities
	ReleaseSourceRef  string
	RequiresRelease   bool
}

type credentialLateProviderDefinition struct {
	ChannelType int
	BaseURL     string
	Models      map[string]credentialLateProviderModel
}

type credentialLateProviderMarker struct {
	SchemaVersion int      `json:"schema_version"`
	Provider      string   `json:"provider"`
	Region        string   `json:"region,omitempty"`
	AccountID     string   `json:"account_id"`
	ChannelID     int      `json:"channel_id"`
	PublicModels  []string `json:"public_model_ids"`
	State         string   `json:"state"`
}

var parseCredentialLateProviderRoutes = parsePlatformRelayCapabilities

func credentialLateProviderDefinitions() (map[string]credentialLateProviderDefinition, error) {
	candidates, err := generationprofile.ReviewedAcceptanceCandidates()
	if err != nil {
		return nil, err
	}
	definitions := map[string]credentialLateProviderDefinition{
		CredentialLateProviderVolcengineArk: {
			ChannelType: constant.ChannelTypeVolcEngine,
			BaseURL:     constant.ChannelBaseURLs[constant.ChannelTypeVolcEngine],
			Models:      map[string]credentialLateProviderModel{},
		},
		CredentialLateProviderMiniMax: {
			ChannelType: constant.ChannelTypeMiniMax,
			BaseURL:     "https://api.minimax.cn",
			Models:      map[string]credentialLateProviderModel{},
		},
		CredentialLateProviderGoogleGemini: {
			ChannelType: constant.ChannelTypeGemini,
			BaseURL:     constant.ChannelBaseURLs[constant.ChannelTypeGemini],
			Models:      map[string]credentialLateProviderModel{},
		},
	}
	for _, candidate := range candidates {
		provider := ""
		releaseSourceRef := ""
		switch candidate.NativeChannelType {
		case constant.ChannelTypeVolcEngine:
			provider = CredentialLateProviderVolcengineArk
			releaseSourceRef = "generationprofile/seedance_models.v1.json"
		case constant.ChannelTypeMiniMax:
			provider = CredentialLateProviderMiniMax
			releaseSourceRef = "generationprofile/minimax_h3_models.v1.json"
		case constant.ChannelTypeGemini:
			provider = CredentialLateProviderGoogleGemini
			releaseSourceRef = "generationprofile/google_video_models.v1.json"
		}
		if provider == "" {
			return nil, fmt.Errorf("reviewed provider candidate %q uses an unsupported native channel", candidate.PublicModelID)
		}
		definition := definitions[provider]
		definition.Models[candidate.PublicModelID] = credentialLateProviderModel{
			ProviderModelID: candidate.ProviderModelID, PublicModelID: candidate.PublicModelID,
			NativeChannelType: candidate.NativeChannelType, AdapterProfileID: candidate.AdapterProfileID,
			Capability: candidate.Capability, ReleaseSourceRef: releaseSourceRef, RequiresRelease: true,
		}
		definitions[provider] = definition
	}
	// Seedream 5 is the exact historical production binding. It is not an
	// acceptance candidate, but a new Ark account still has to be able to bind
	// this already-reviewed model without inventing a Lite provider identity.
	seedreamProfile, ok := generationprofile.Get(generationprofile.Seedream50TextToImageV1)
	if !ok {
		return nil, errors.New("historical Seedream adapter profile is unavailable")
	}
	volcengine := definitions[CredentialLateProviderVolcengineArk]
	volcengine.Models[constant.PlatformGenerationPublicSeedream50Model] = credentialLateProviderModel{
		ProviderModelID:   constant.PlatformGenerationArkSeedream50Model,
		PublicModelID:     constant.PlatformGenerationPublicSeedream50Model,
		NativeChannelType: constant.ChannelTypeVolcEngine,
		AdapterProfileID:  generationprofile.Seedream50TextToImageV1,
		Capability:        seedreamProfile.Capability,
	}
	definitions[CredentialLateProviderVolcengineArk] = volcengine
	return definitions, nil
}

func normalizeCredentialLateProviderInput(input CredentialLateProviderInput) (CredentialLateProviderInput, credentialLateProviderDefinition, error) {
	definitions, err := credentialLateProviderDefinitions()
	if err != nil {
		return CredentialLateProviderInput{}, credentialLateProviderDefinition{}, err
	}
	definition, ok := definitions[input.Provider]
	if input.SchemaVersion != CredentialLateProviderSchemaVersion || !ok || input.ChannelID <= 0 || input.ChannelID > 1_000_000_000 {
		return CredentialLateProviderInput{}, credentialLateProviderDefinition{}, errors.New("credential-late provider identity is invalid")
	}
	if input.Provider == CredentialLateProviderMiniMax {
		switch input.Region {
		case "cn":
			definition.BaseURL = "https://api.minimax.cn"
		case "global":
			definition.BaseURL = "https://api.minimax.io"
		default:
			return CredentialLateProviderInput{}, credentialLateProviderDefinition{}, errors.New("MiniMax region must be cn or global")
		}
	} else if input.Region != "" {
		return CredentialLateProviderInput{}, credentialLateProviderDefinition{}, errors.New("provider region is not supported")
	}
	if input.AccountID == "" || strings.TrimSpace(input.AccountID) != input.AccountID || len(input.AccountID) > 128 ||
		strings.ContainsAny(input.AccountID, "\x00\r\n\t") || platformRelayMockIdentity(input.AccountID) {
		return CredentialLateProviderInput{}, credentialLateProviderDefinition{}, errors.New("credential-late provider account_id is invalid")
	}
	if len(input.APIKey) < 8 || len(input.APIKey) > 64*1024 || strings.TrimSpace(input.APIKey) != input.APIKey ||
		!utf8.ValidString(input.APIKey) || strings.ContainsAny(input.APIKey, "\x00\r\n\t") {
		return CredentialLateProviderInput{}, credentialLateProviderDefinition{}, errors.New("credential-late provider API key is missing or invalid")
	}
	lowerKey := strings.ToLower(input.APIKey)
	for _, forbidden := range []string{"replace-with", "placeholder", "example-key", "mock-", "test-key", "changeme"} {
		if strings.Contains(lowerKey, forbidden) {
			return CredentialLateProviderInput{}, credentialLateProviderDefinition{}, errors.New("credential-late provider API key is a placeholder")
		}
	}
	if len(input.PublicModelIDs) == 0 {
		return CredentialLateProviderInput{}, credentialLateProviderDefinition{}, errors.New("at least one reviewed public model is required")
	}
	seen := make(map[string]struct{}, len(input.PublicModelIDs))
	models := append([]string(nil), input.PublicModelIDs...)
	for _, publicModelID := range models {
		if _, exists := definition.Models[publicModelID]; !exists {
			return CredentialLateProviderInput{}, credentialLateProviderDefinition{}, fmt.Errorf("public model %q is not reviewed for provider %q", publicModelID, input.Provider)
		}
		if _, duplicate := seen[publicModelID]; duplicate {
			return CredentialLateProviderInput{}, credentialLateProviderDefinition{}, fmt.Errorf("public model %q is duplicated", publicModelID)
		}
		seen[publicModelID] = struct{}{}
	}
	sort.Strings(models)
	input.PublicModelIDs = models
	return input, definition, nil
}

func validateCredentialLateProviderEnvironment(environment string) error {
	runtimeEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_ENVIRONMENT")))
	appEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	deploymentEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("DEPLOYMENT_ENV")))
	switch environment {
	case "staging", "production":
		if runtimeEnvironment != environment || appEnvironment != environment || deploymentEnvironment != environment ||
			!PlatformRelayProductionSecurityEnabled() {
			return fmt.Errorf("credential-late provider target %q does not match the protected Relay runtime", environment)
		}
	case "development":
		if runtimeEnvironment != "development" || appEnvironment != "development" || deploymentEnvironment != "development" ||
			PlatformRelayProductionSecurityEnabled() {
			return errors.New("development provider onboarding requires an exact non-protected development Relay runtime")
		}
		for _, variable := range []string{
			"RELAY_DATABASE_TLS_ATTESTATION_REQUIRED",
			"RELAY_DATABASE_SECRET_FILES_REQUIRED",
			"RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED",
		} {
			if value, present := os.LookupEnv(variable); present && strings.EqualFold(strings.TrimSpace(value), "true") {
				return errors.New("development provider onboarding is forbidden in a protected Relay runtime")
			}
		}
		for _, variable := range []string{
			"RELAY_SECRET_ISOLATION_GENERATION",
			"RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE",
			"RELAY_SECRET_ISOLATION_RECEIPT_FILE",
			"RELAY_SECRET_ISOLATION_COMMIT_FILE",
		} {
			if strings.TrimSpace(os.Getenv(variable)) != "" {
				return errors.New("development provider onboarding is forbidden in a protected Relay runtime")
			}
		}
	default:
		return errors.New("credential-late provider target must be development, staging, or production")
	}
	return nil
}

func credentialLateProviderFingerprint(key string) string {
	digest := sha256.Sum256([]byte(key))
	return hex.EncodeToString(digest[:])
}

func validateCredentialLateProviderRoutes(
	input CredentialLateProviderInput,
	definition credentialLateProviderDefinition,
	routes map[string][]PlatformRelayRouteDeclaration,
	requireSignedAcceptance bool,
) ([]string, error) {
	wanted := make(map[string]credentialLateProviderModel, len(input.PublicModelIDs))
	for _, publicModelID := range input.PublicModelIDs {
		wanted[publicModelID] = definition.Models[publicModelID]
	}
	fingerprint := credentialLateProviderFingerprint(input.APIKey)
	seen := make(map[string]bool, len(wanted))
	routeIDs := make([]string, 0, len(wanted))
	for publicModelID, declarations := range routes {
		for _, declaration := range declarations {
			if declaration.ChannelID != input.ChannelID {
				continue
			}
			if platformRelayMockIdentity(declaration.RouteID) {
				return nil, fmt.Errorf("provider onboarding route %q uses a mock identity", declaration.RouteID)
			}
			expected, ok := wanted[publicModelID]
			if !ok || seen[publicModelID] {
				return nil, fmt.Errorf("signed route set uses onboarding channel %d outside its exact reviewed model set", input.ChannelID)
			}
			if declaration.ProviderName != input.Provider || declaration.AccountID != input.AccountID ||
				declaration.NativeChannelType != definition.ChannelType || declaration.KeyIndex != 0 ||
				declaration.KeyFingerprint != fingerprint || declaration.ChannelClass != PlatformChannelClassOfficial ||
				declaration.UpstreamModel != expected.ProviderModelID ||
				declaration.ResolvedCapabilityProfileID != expected.AdapterProfileID {
				return nil, fmt.Errorf("signed route %q does not match its exact credential-late provider binding", declaration.RouteID)
			}
			// Route releases may narrow values inside a reviewed mode (for example
			// duration or resolution), but onboarding must never silently delete an
			// entire reviewed execution mode. Otherwise the declaration becomes its
			// own incomplete source of truth and the later route-count check cannot
			// detect the omission.
			if declaration.Capabilities.SchemaVersion != expected.Capability.SchemaVersion ||
				len(declaration.Capabilities.Modes) != len(expected.Capability.Modes) {
				return nil, fmt.Errorf("signed route %q does not contain the complete reviewed mode set", declaration.RouteID)
			}
			for mode := range expected.Capability.Modes {
				if _, present := declaration.Capabilities.Modes[mode]; !present {
					return nil, fmt.Errorf("signed route %q does not contain the complete reviewed mode set", declaration.RouteID)
				}
			}
			if requireSignedAcceptance {
				if declaration.Acceptance == nil || declaration.AcceptanceDigest == "" {
					return nil, fmt.Errorf("signed route %q lacks verified acceptance", declaration.RouteID)
				}
			} else if declaration.Acceptance != nil || declaration.AcceptanceDigest != "" {
				return nil, fmt.Errorf("development route %q must not carry unverified acceptance material", declaration.RouteID)
			}
			seen[publicModelID] = true
			routeIDs = append(routeIDs, declaration.RouteID)
		}
	}
	for publicModelID := range wanted {
		if !seen[publicModelID] {
			return nil, fmt.Errorf("signed route set is missing onboarding model %q", publicModelID)
		}
	}
	sort.Strings(routeIDs)
	return routeIDs, nil
}

func validateCredentialLateProviderAcceptanceFresh(
	routes map[string][]PlatformRelayRouteDeclaration,
	environment string,
) error {
	if environment == "development" {
		return nil
	}
	if environment != "staging" && environment != "production" {
		return errors.New("provider onboarding runtime environment is invalid")
	}
	now := platformRouteAcceptanceNow()
	for _, declarations := range routes {
		for _, declaration := range declarations {
			correctEnvironment := (environment == "staging" && declaration.StagingReady && !declaration.ProductionReady) ||
				(environment == "production" && !declaration.StagingReady && declaration.ProductionReady)
			if declaration.Acceptance == nil || declaration.AcceptanceDigest == "" ||
				declaration.AcceptanceNotBefore.IsZero() || declaration.AcceptanceNotAfter.IsZero() ||
				!correctEnvironment || now.Before(declaration.AcceptanceNotBefore) ||
				!now.Before(declaration.AcceptanceNotAfter) {
				return fmt.Errorf("signed route %q acceptance expired or changed before finalization", declaration.RouteID)
			}
		}
	}
	return nil
}

func credentialLateProviderExpectedRoutes(
	input CredentialLateProviderInput,
	definition credentialLateProviderDefinition,
	routes map[string][]PlatformRelayRouteDeclaration,
	environment string,
) ([]model.ProviderOnboardingExpectedRoute, error) {
	secure := environment == "staging" || environment == "production"
	if _, err := validateCredentialLateProviderRoutes(input, definition, routes, secure); err != nil {
		return nil, err
	}
	now := platformRouteAcceptanceNow()
	expected := make([]model.ProviderOnboardingExpectedRoute, 0)
	for publicModelID, declarations := range routes {
		for _, declaration := range declarations {
			if declaration.ChannelID != input.ChannelID {
				continue
			}
			switch environment {
			case "development":
				if declaration.StagingReady || declaration.ProductionReady {
					return nil, fmt.Errorf("development route %q carries a protected-environment readiness flag", declaration.RouteID)
				}
			case "staging":
				if !declaration.StagingReady || declaration.ProductionReady || declaration.AcceptanceNotBefore.IsZero() ||
					declaration.AcceptanceNotAfter.IsZero() || now.Before(declaration.AcceptanceNotBefore) ||
					!now.Before(declaration.AcceptanceNotAfter) {
					return nil, fmt.Errorf("staging route %q acceptance is missing, stale, or bound to another environment", declaration.RouteID)
				}
			case "production":
				if declaration.StagingReady || !declaration.ProductionReady || declaration.AcceptanceNotBefore.IsZero() ||
					declaration.AcceptanceNotAfter.IsZero() || now.Before(declaration.AcceptanceNotBefore) ||
					!now.Before(declaration.AcceptanceNotAfter) {
					return nil, fmt.Errorf("production route %q acceptance is missing, stale, or bound to another environment", declaration.RouteID)
				}
			default:
				return nil, errors.New("provider onboarding runtime environment is invalid")
			}
			modes := make([]string, 0, len(declaration.Capabilities.Modes))
			for mode := range declaration.Capabilities.Modes {
				modes = append(modes, mode)
			}
			sort.Strings(modes)
			if len(modes) == 0 {
				return nil, fmt.Errorf("provider onboarding route %q has no executable modes", declaration.RouteID)
			}
			for _, mode := range modes {
				expected = append(expected, model.ProviderOnboardingExpectedRoute{
					RouteKey: declaration.RouteID, Model: publicModelID, Mode: mode,
					ProviderName: declaration.ProviderName, AccountID: declaration.AccountID,
					ChannelID: declaration.ChannelID, AcceptedChannelType: declaration.NativeChannelType,
					KeyIndex: declaration.KeyIndex, KeyFingerprint: declaration.KeyFingerprint,
					ChannelClass: declaration.ChannelClass, UpstreamModel: declaration.UpstreamModel,
					CapabilityProfileID:            declaration.ResolvedCapabilityProfileID,
					CapabilityProfileRevision:      declaration.ResolvedCapabilityProfileRevision,
					ModelReleaseID:                 declaration.ResolvedModelReleaseID,
					ModelReleaseRevision:           declaration.ResolvedModelReleaseRevision,
					ModelReleaseCapabilityRevision: declaration.ResolvedModelCapabilityRevision,
					StagingReady:                   declaration.StagingReady, ProductionReady: declaration.ProductionReady,
					RPMWindowSeconds: 60, RPMLimit: declaration.RPMLimit, ActiveLimit: declaration.ActiveTaskLimit,
					AcceptanceDigest:    declaration.AcceptanceDigest,
					AcceptanceNotBefore: declaration.AcceptanceNotBefore,
					AcceptanceNotAfter:  declaration.AcceptanceNotAfter,
				})
			}
		}
	}
	sort.Slice(expected, func(i, j int) bool {
		if expected[i].RouteKey == expected[j].RouteKey {
			return expected[i].Mode < expected[j].Mode
		}
		return expected[i].RouteKey < expected[j].RouteKey
	})
	return expected, nil
}

// validateCredentialLateProviderPreservesExistingRoutes makes "complete
// inventory" enforceable rather than documentary. A one-provider input must
// never turn into an accidental global route-disable operation.
func validateCredentialLateProviderPreservesExistingRoutes(
	routes map[string][]PlatformRelayRouteDeclaration,
) error {
	return validateCredentialLateProviderPreservesExistingRoutesWithDB(model.DB, routes)
}

func validateCredentialLateProviderPreservesExistingRoutesWithDB(
	db *gorm.DB,
	routes map[string][]PlatformRelayRouteDeclaration,
) error {
	if db == nil {
		return errors.New("provider route inventory database is unavailable")
	}
	type desiredIdentity struct {
		modelID     string
		declaration PlatformRelayRouteDeclaration
	}
	desired := make(map[string]desiredIdentity)
	for modelID, declarations := range routes {
		for _, declaration := range declarations {
			for mode := range declaration.Capabilities.Modes {
				desired[declaration.RouteID+"\x00"+mode] = desiredIdentity{modelID: modelID, declaration: declaration}
			}
		}
	}
	var existing []model.PlatformGenerationProviderRoute
	if err := db.Where("enabled = ?", true).Find(&existing).Error; err != nil {
		return err
	}
	for _, route := range existing {
		candidate, ok := desired[route.RouteKey+"\x00"+route.Mode]
		if !ok {
			return fmt.Errorf("complete signed route inventory would omit enabled route %q mode %q", route.RouteKey, route.Mode)
		}
		declaration := candidate.declaration
		if candidate.modelID != route.Model || declaration.ChannelID != route.ChannelID ||
			declaration.KeyIndex != route.KeyIndex || declaration.KeyFingerprint != route.KeyFingerprint ||
			declaration.ProviderName != route.ProviderName || declaration.AccountID != route.AccountID ||
			declaration.ChannelClass != route.ChannelClass || declaration.UpstreamModel != route.UpstreamModel ||
			(declaration.NativeChannelType != constant.ChannelTypeUnknown && route.AcceptedChannelType != constant.ChannelTypeUnknown &&
				declaration.NativeChannelType != route.AcceptedChannelType) {
			return fmt.Errorf("complete signed route inventory changes enabled route %q mode %q identity", route.RouteKey, route.Mode)
		}
	}
	return nil
}

// PlanDevelopmentCredentialLateProviderRoutes creates the exact route JSON
// consumed by FinalizeCredentialLateProviderWithOptions. It is intentionally
// bound to a staged development channel: no provider request, signing,
// acceptance, route write or channel activation occurs here.
func PlanDevelopmentCredentialLateProviderRoutes(
	input CredentialLateProviderInput,
	options CredentialLateProviderRoutePlanOptions,
) (map[string][]PlatformRelayRouteDeclaration, error) {
	if err := validateCredentialLateProviderEnvironment("development"); err != nil {
		return nil, err
	}
	input, definition, err := normalizeCredentialLateProviderInput(input)
	if err != nil {
		return nil, err
	}
	if options.ReplaceAllRoutes && len(options.CurrentRoutesJSON) != 0 {
		return nil, errors.New("development route planning accepts either current routes or explicit full replacement, not both")
	}
	if !options.ReplaceAllRoutes && len(options.CurrentRoutesJSON) == 0 {
		return nil, errors.New("development route planning requires a complete current inventory or explicit full replacement")
	}
	createdAt, err := time.Parse(time.RFC3339, options.CreatedAt)
	if err != nil || options.CreatedAt != createdAt.UTC().Format(time.RFC3339) {
		return nil, errors.New("development route plan created_at must be canonical UTC RFC3339")
	}
	if options.RPMLimit <= 0 || options.RPMLimit > 1_000_000 ||
		options.ActiveTaskLimit <= 0 || options.ActiveTaskLimit > 1_000_000 {
		return nil, errors.New("development route plan admission limits are invalid")
	}

	channel, state, err := loadCredentialLateProviderChannel(input, definition)
	if err != nil {
		return nil, err
	}
	if state != "staged" || channel.Status != common.ChannelStatusManuallyDisabled {
		return nil, errors.New("development route planning requires the exact manually-disabled staged channel")
	}
	if err := verifyCredentialLateProviderAbilitiesDisabled(input.ChannelID); err != nil {
		return nil, err
	}

	routes := make(map[string][]PlatformRelayRouteDeclaration)
	if len(options.CurrentRoutesJSON) != 0 {
		_, parsed, parseErr := parseCredentialLateProviderRoutes("", string(options.CurrentRoutesJSON), "development")
		if parseErr != nil {
			return nil, fmt.Errorf("current development route inventory is not accepted: %w", parseErr)
		}
		for modelID, declarations := range parsed {
			preserved := make([]PlatformRelayRouteDeclaration, 0, len(declarations))
			for _, declaration := range declarations {
				if declaration.Acceptance != nil || declaration.StagingReady || declaration.ProductionReady ||
					(declaration.ModelRelease != nil && declaration.ModelRelease.Attestation != nil) {
					return nil, fmt.Errorf("current development route %q is not an unsigned development declaration", declaration.RouteID)
				}
				if declaration.ChannelID != input.ChannelID {
					preserved = append(preserved, declaration)
				}
			}
			if len(preserved) != 0 {
				routes[modelID] = preserved
			}
		}
		if err := validateCredentialLateProviderPreservesExistingRoutes(parsed); err != nil {
			return nil, fmt.Errorf("current development route inventory is incomplete: %w", err)
		}
	}

	fingerprint := credentialLateProviderFingerprint(input.APIKey)
	for _, publicModelID := range input.PublicModelIDs {
		modelFact := definition.Models[publicModelID]
		profile, found := generationprofile.Get(modelFact.AdapterProfileID)
		if !found || profile.NativeChannelType != modelFact.NativeChannelType {
			return nil, fmt.Errorf("reviewed model %q adapter profile is unavailable", publicModelID)
		}
		capability := generationprofile.NormalizeCapability(modelFact.Capability)
		if err := profile.ValidateNarrowing(capability); err != nil {
			return nil, fmt.Errorf("reviewed model %q capability is invalid: %w", publicModelID, err)
		}
		identity, marshalErr := common.Marshal(struct {
			Provider       string `json:"provider"`
			AccountID      string `json:"account_id"`
			ChannelID      int    `json:"channel_id"`
			PublicModelID  string `json:"public_model_id"`
			ProviderModel  string `json:"provider_model_id"`
			KeyFingerprint string `json:"key_fingerprint"`
		}{
			Provider: input.Provider, AccountID: input.AccountID, ChannelID: input.ChannelID,
			PublicModelID: publicModelID, ProviderModel: modelFact.ProviderModelID, KeyFingerprint: fingerprint,
		})
		if marshalErr != nil {
			return nil, errors.New("encode development route identity")
		}
		routeDigest := sha256.Sum256(identity)
		declaration := PlatformRelayRouteDeclaration{
			RouteID:      fmt.Sprintf("provider-onboarding-%d-%s", input.ChannelID, hex.EncodeToString(routeDigest[:])),
			ProviderName: input.Provider, AccountID: input.AccountID, ChannelID: input.ChannelID,
			NativeChannelType: definition.ChannelType, KeyIndex: 0, KeyFingerprint: fingerprint,
			ChannelClass: PlatformChannelClassOfficial, UpstreamModel: modelFact.ProviderModelID,
			RPMLimit: options.RPMLimit, ActiveTaskLimit: options.ActiveTaskLimit,
			Capabilities: capability, CapabilityProfile: profile.ID,
		}
		if modelFact.RequiresRelease {
			release := generationrelease.Release{
				APIVersion: generationrelease.APIVersion, Kind: generationrelease.Kind,
				ReleaseID:     "development." + strings.ReplaceAll(input.Provider, "-", ".") + "." + publicModelID + "." + createdAt.Format("20060102t150405z"),
				PublicModelID: publicModelID, ProviderModelID: modelFact.ProviderModelID,
				AdapterProfileID: profile.ID, AdapterProfileRevision: profile.Revision,
				Capability: capability,
				Audit: generationrelease.Audit{
					CreatedAt: options.CreatedAt, CreatedBy: options.CreatedBy, Reason: options.Reason,
					SourceRef: modelFact.ReleaseSourceRef,
				},
			}
			if err := release.Validate(profile); err != nil {
				return nil, fmt.Errorf("reviewed model %q cannot form a development release: %w", publicModelID, err)
			}
			declaration.ModelRelease = &release
		}
		routes[publicModelID] = append(routes[publicModelID], declaration)
	}

	encoded, err := common.Marshal(routes)
	if err != nil {
		return nil, errors.New("encode complete development route inventory")
	}
	_, validated, err := parseCredentialLateProviderRoutes("", string(encoded), "development")
	if err != nil {
		return nil, fmt.Errorf("generated development route inventory is invalid: %w", err)
	}
	if _, err := validateCredentialLateProviderRoutes(input, definition, validated, false); err != nil {
		return nil, err
	}
	if !options.ReplaceAllRoutes {
		if err := validateCredentialLateProviderPreservesExistingRoutes(validated); err != nil {
			return nil, err
		}
	}
	return validated, nil
}

func credentialLateProviderMarkerJSON(input CredentialLateProviderInput, state string) (string, error) {
	marker := credentialLateProviderMarker{
		SchemaVersion: CredentialLateProviderSchemaVersion, Provider: input.Provider, Region: input.Region,
		AccountID: input.AccountID, ChannelID: input.ChannelID,
		PublicModels: append([]string(nil), input.PublicModelIDs...), State: state,
	}
	encoded, err := common.Marshal(map[string]any{credentialLateProviderMarkerKey: marker})
	return string(encoded), err
}

func exactCredentialLateProviderChannel(
	channel *model.Channel,
	input CredentialLateProviderInput,
	definition credentialLateProviderDefinition,
) (string, error) {
	if channel == nil || channel.Id != input.ChannelID || channel.Type != definition.ChannelType || channel.Tag == nil ||
		*channel.Tag != credentialLateProviderChannelTag || channel.BaseURL == nil || *channel.BaseURL != definition.BaseURL ||
		channel.Group != "default" || channel.Models != strings.Join(input.PublicModelIDs, ",") ||
		credentialLateProviderFingerprint(channel.Key) != credentialLateProviderFingerprint(input.APIKey) {
		return "", errors.New("channel id is already owned by an existing or different administrator configuration")
	}
	var markerMap map[string]credentialLateProviderMarker
	if err := common.Unmarshal([]byte(channel.OtherInfo), &markerMap); err != nil {
		return "", errors.New("managed onboarding channel marker is invalid")
	}
	marker, ok := markerMap[credentialLateProviderMarkerKey]
	if !ok || marker.SchemaVersion != CredentialLateProviderSchemaVersion || marker.Provider != input.Provider ||
		marker.Region != input.Region || marker.AccountID != input.AccountID || marker.ChannelID != input.ChannelID ||
		strings.Join(marker.PublicModels, ",") != strings.Join(input.PublicModelIDs, ",") ||
		(marker.State != "staged" && marker.State != "route_test_ready") {
		return "", errors.New("managed onboarding channel marker changed")
	}
	return marker.State, nil
}

func stageCredentialLateProviderChannel(
	input CredentialLateProviderInput,
	definition credentialLateProviderDefinition,
) (*model.Channel, bool, string, error) {
	existing, err := model.GetChannelById(input.ChannelID, true)
	if err == nil {
		state, exactErr := exactCredentialLateProviderChannel(existing, input, definition)
		return existing, false, state, exactErr
	}
	if !errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, false, "", err
	}
	marker, err := credentialLateProviderMarkerJSON(input, "staged")
	if err != nil {
		return nil, false, "", err
	}
	tag := credentialLateProviderChannelTag
	baseURL := definition.BaseURL
	channel := &model.Channel{
		Id: input.ChannelID, Type: definition.ChannelType, Key: input.APIKey,
		Status:  common.ChannelStatusManuallyDisabled,
		Name:    "Provider acceptance / " + input.Provider + " / " + input.AccountID,
		BaseURL: &baseURL, Models: strings.Join(input.PublicModelIDs, ","), Group: "default",
		Tag: &tag, OtherInfo: marker, CreatedTime: common.GetTimestamp(),
	}
	err = model.DB.Transaction(func(tx *gorm.DB) error {
		if err := tx.Create(channel).Error; err != nil {
			return err
		}
		return channel.AddAbilities(tx)
	})
	if err != nil {
		return nil, false, "", err
	}
	return channel, true, "staged", nil
}

func loadCredentialLateProviderChannel(
	input CredentialLateProviderInput,
	definition credentialLateProviderDefinition,
) (*model.Channel, string, error) {
	channel, err := model.GetChannelById(input.ChannelID, true)
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, "", errors.New("credential-late provider channel must be staged before finalization")
		}
		return nil, "", err
	}
	state, err := exactCredentialLateProviderChannel(channel, input, definition)
	if err != nil {
		return nil, "", err
	}
	return channel, state, nil
}

func verifyCredentialLateProviderAbilitiesDisabled(channelID int) error {
	return verifyCredentialLateProviderAbilitiesDisabledWithDB(model.DB, channelID)
}

func verifyCredentialLateProviderAbilitiesDisabledWithDB(db *gorm.DB, channelID int) error {
	if db == nil {
		return errors.New("provider onboarding ability database is unavailable")
	}
	var enabled int64
	if err := db.Model(&model.Ability{}).Where("channel_id = ? AND enabled = ?", channelID, true).Count(&enabled).Error; err != nil {
		return err
	}
	if enabled != 0 {
		return errors.New("managed onboarding channel native abilities were enabled outside Platform publication")
	}
	return nil
}

type credentialLateProviderFinalizeFence struct {
	CredentialSetVersion string
	KeySetFingerprint    string
	ControlRevision      int64
	Status               int
	OtherInfo            string
}

func credentialLateProviderFenceWithDB(db *gorm.DB, channel *model.Channel) (credentialLateProviderFinalizeFence, error) {
	if db == nil || channel == nil || strings.TrimSpace(channel.CredentialSetVersion) == "" || channel.ControlRevision < 0 {
		return credentialLateProviderFinalizeFence{}, errors.New("provider onboarding credential/control fence is unavailable")
	}
	var credential model.ProviderChannelCredentialSetVersion
	if err := db.Session(&gorm.Session{NewDB: true, SkipHooks: true}).
		Where("credential_set_version = ?", channel.CredentialSetVersion).Take(&credential).Error; err != nil ||
		credential.ChannelID != channel.Id || len(credential.KeySetFingerprint) != sha256.Size*2 ||
		credential.KeyCount != 1 || subtle.ConstantTimeCompare(
		[]byte(credential.KeySetFingerprint), []byte(credentialLateProviderFingerprint(channel.Key)),
	) != 1 {
		return credentialLateProviderFinalizeFence{}, errors.New("provider onboarding credential/control fence is unavailable")
	}
	return credentialLateProviderFinalizeFence{
		CredentialSetVersion: channel.CredentialSetVersion,
		KeySetFingerprint:    credential.KeySetFingerprint,
		ControlRevision:      channel.ControlRevision,
		Status:               channel.Status,
		OtherInfo:            channel.OtherInfo,
	}, nil
}

func credentialLateProviderFenceMatches(
	channel *model.Channel,
	fence credentialLateProviderFinalizeFence,
) bool {
	return channel != nil && channel.CredentialSetVersion == fence.CredentialSetVersion &&
		subtle.ConstantTimeCompare([]byte(credentialLateProviderFingerprint(channel.Key)), []byte(fence.KeySetFingerprint)) == 1 &&
		channel.ControlRevision == fence.ControlRevision && channel.Status == fence.Status &&
		channel.OtherInfo == fence.OtherInfo
}

func activateCredentialLateProviderChannelWithDB(
	tx *gorm.DB,
	input CredentialLateProviderInput,
	current model.Channel,
	fence credentialLateProviderFinalizeFence,
) error {
	marker, err := credentialLateProviderMarkerJSON(input, "route_test_ready")
	if err != nil {
		return err
	}
	if tx == nil || current.Status != common.ChannelStatusManuallyDisabled ||
		!credentialLateProviderFenceMatches(&current, fence) {
		return errors.New("staged onboarding channel activation lost its credential/control fence")
	}
	result := tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).Model(&model.Channel{}).
		Where("id = ? AND status = ? AND control_revision = ? AND credential_set_version = ? AND other_info = ?",
			input.ChannelID, common.ChannelStatusManuallyDisabled, fence.ControlRevision,
			fence.CredentialSetVersion, fence.OtherInfo).
		Updates(map[string]any{"status": common.ChannelStatusEnabled, "other_info": marker})
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("staged onboarding channel activation lost its credential/control fence")
	}
	// Deliberately do not enable native abilities. The channel is reachable
	// only through the exact route-bound acceptance test until Platform has
	// published the fresh release evidence through its normal workflow.
	return tx.Model(&model.Ability{}).Where("channel_id = ?", input.ChannelID).
		Update("enabled", false).Error
}

func finalizeCredentialLateProviderWithFence(
	input CredentialLateProviderInput,
	definition credentialLateProviderDefinition,
	routes map[string][]PlatformRelayRouteDeclaration,
	state string,
	fence credentialLateProviderFinalizeFence,
	preserveExisting bool,
	environment string,
) error {
	return model.DB.Transaction(func(tx *gorm.DB) error {
		var current model.Channel
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).
			Where("id = ?", input.ChannelID).First(&current).Error; err != nil {
			return err
		}
		if !credentialLateProviderFenceMatches(&current, fence) {
			return errors.New("provider onboarding credential/control state changed before finalization")
		}
		currentState, err := exactCredentialLateProviderChannel(&current, input, definition)
		if err != nil {
			return err
		}
		if currentState != state {
			return errors.New("provider onboarding credential/control state changed before finalization")
		}
		currentFence, err := credentialLateProviderFenceWithDB(tx, &current)
		if err != nil || currentFence != fence {
			return errors.New("provider onboarding credential/control state changed before finalization")
		}
		if err := verifyCredentialLateProviderAbilitiesDisabledWithDB(tx, input.ChannelID); err != nil {
			return err
		}
		if preserveExisting {
			if err := validateCredentialLateProviderPreservesExistingRoutesWithDB(tx, routes); err != nil {
				return err
			}
		}
		// Acceptance can expire after parsing while the channel/fence checks are
		// in progress. Re-evaluate its signed window immediately before the route
		// inventory and channel activation commit atomically.
		if err := validateCredentialLateProviderAcceptanceFresh(routes, environment); err != nil {
			return err
		}
		if err := syncPlatformGenerationProviderRouteDeclarationsWithDB(tx, routes); err != nil {
			return fmt.Errorf("materialize accepted provider routes: %w", err)
		}
		if state == "staged" {
			return activateCredentialLateProviderChannelWithDB(tx, input, current, fence)
		}
		if current.Status != common.ChannelStatusEnabled {
			return errors.New("administrator disabled the managed provider channel; onboarding will not override that decision")
		}
		return nil
	})
}

// StageCredentialLateProvider creates only the exact reviewed, encrypted
// native channel and its disabled abilities. It never parses or writes route
// declarations and never enables the channel. This breaks the real paid-test
// bootstrap cycle without exposing a callable provider route before signed
// acceptance exists.
func StageCredentialLateProvider(
	input CredentialLateProviderInput,
	environment string,
) (CredentialLateProviderResult, error) {
	result := CredentialLateProviderResult{NativeAbilities: "disabled_until_platform_publication"}
	input, definition, err := normalizeCredentialLateProviderInput(input)
	if err != nil {
		return result, err
	}
	result.ChannelID, result.Provider, result.AccountID = input.ChannelID, input.Provider, input.AccountID
	result.PublicModelIDs = append([]string(nil), input.PublicModelIDs...)
	if err := validateCredentialLateProviderEnvironment(environment); err != nil {
		return result, err
	}
	channel, created, state, err := stageCredentialLateProviderChannel(input, definition)
	if err != nil {
		return result, err
	}
	result.ChannelCreated = created
	switch state {
	case "staged":
		if channel.Status != common.ChannelStatusManuallyDisabled {
			return result, errors.New("staged onboarding channel status changed")
		}
	case "route_test_ready":
		if channel.Status != common.ChannelStatusEnabled {
			return result, errors.New("administrator disabled the managed provider channel; onboarding will not override that decision")
		}
		// A byte-identical replay after finalization is a recovery read, not a
		// second activation.  This makes the complete onboarding workflow
		// resumable after an ambiguous paid test or a later Platform failure.
		result.RouteTestReady = true
	default:
		return result, errors.New("managed onboarding channel marker changed")
	}
	if err := verifyCredentialLateProviderAbilitiesDisabled(input.ChannelID); err != nil {
		return result, err
	}
	return result, nil
}

// FinalizeCredentialLateProvider validates a protected credential against the
// complete signed runtime route inventory, requires the exact staged channel,
// materializes the full route inventory, then exposes that channel to
// route-bound acceptance testing only. It never creates a price, customer
// grant, Platform publication or enabled native ability.
func FinalizeCredentialLateProvider(
	input CredentialLateProviderInput,
	signedRoutesJSON []byte,
	environment string,
) (CredentialLateProviderResult, error) {
	return FinalizeCredentialLateProviderWithOptions(
		input,
		signedRoutesJSON,
		environment,
		CredentialLateProviderFinalizeOptions{},
	)
}

// FinalizeCredentialLateProviderWithOptions keeps full replacement an exact,
// development-only operator choice. Staging and production always retain both
// signed route acceptance and non-target route preservation.
func FinalizeCredentialLateProviderWithOptions(
	input CredentialLateProviderInput,
	signedRoutesJSON []byte,
	environment string,
	options CredentialLateProviderFinalizeOptions,
) (CredentialLateProviderResult, error) {
	result := CredentialLateProviderResult{NativeAbilities: "disabled_until_platform_publication"}
	input, definition, err := normalizeCredentialLateProviderInput(input)
	if err != nil {
		return result, err
	}
	result.ChannelID, result.Provider, result.AccountID = input.ChannelID, input.Provider, input.AccountID
	result.PublicModelIDs = append([]string(nil), input.PublicModelIDs...)
	if err := validateCredentialLateProviderEnvironment(environment); err != nil {
		return result, err
	}
	if options.ReplaceAllRoutes && environment != "development" {
		return result, errors.New("explicit full route replacement is restricted to development")
	}
	if len(signedRoutesJSON) == 0 || len(signedRoutesJSON) > 32*1024*1024 {
		return result, errors.New("signed route inventory is missing or too large")
	}
	_, routes, err := parseCredentialLateProviderRoutes("", string(signedRoutesJSON), environment)
	if err != nil {
		return result, fmt.Errorf("signed route inventory is not accepted: %w", err)
	}
	result.RouteIDs, err = validateCredentialLateProviderRoutes(input, definition, routes, environment != "development")
	if err != nil {
		return result, err
	}
	if !options.ReplaceAllRoutes {
		if err := validateCredentialLateProviderPreservesExistingRoutes(routes); err != nil {
			return result, err
		}
	}
	channel, state, err := loadCredentialLateProviderChannel(input, definition)
	if err != nil {
		return result, err
	}
	fence, err := credentialLateProviderFenceWithDB(model.DB, channel)
	if err != nil {
		return result, err
	}
	if err := verifyCredentialLateProviderAbilitiesDisabled(input.ChannelID); err != nil {
		return result, err
	}
	switch state {
	case "staged":
		if channel.Status != common.ChannelStatusManuallyDisabled {
			return result, errors.New("staged onboarding channel status changed before finalization")
		}
	case "route_test_ready":
		if channel.Status != common.ChannelStatusEnabled {
			return result, errors.New("administrator disabled the managed provider channel; onboarding will not override that decision")
		}
	default:
		return result, errors.New("managed onboarding channel marker changed")
	}
	if err := finalizeCredentialLateProviderWithFence(
		input, definition, routes, state, fence, !options.ReplaceAllRoutes, environment,
	); err != nil {
		return result, err
	}
	result.RouteTestReady = true
	return result, nil
}

// MaterializeCredentialLateProvider preserves the original public entry as a
// finalize-only alias. New callers should use the explicit stage/finalize
// methods so an unsigned route inventory can never be mistaken for staging.
func MaterializeCredentialLateProvider(
	input CredentialLateProviderInput,
	signedRoutesJSON []byte,
	environment string,
) (CredentialLateProviderResult, error) {
	return FinalizeCredentialLateProvider(input, signedRoutesJSON, environment)
}
