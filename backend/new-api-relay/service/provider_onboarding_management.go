package service

import (
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

var (
	ErrProviderOnboardingUnknownProvider     = errors.New("provider onboarding provider is not supported")
	ErrProviderOnboardingReasonInvalid       = errors.New("provider onboarding reason is invalid")
	ErrProviderOnboardingCredentialInvalid   = errors.New("provider onboarding credential is invalid")
	ErrProviderOnboardingEnvironmentNotReady = errors.New("provider onboarding environment is not ready")
)

type ProviderOnboardingModelView struct {
	ID          string `json:"id"`
	DisplayName string `json:"display_name"`
}

type ProviderOnboardingCredentialView struct {
	Configured        bool       `json:"configured"`
	FingerprintPrefix string     `json:"fingerprint_prefix,omitempty"`
	KeyCount          int        `json:"key_count"`
	UpdatedAt         *time.Time `json:"updated_at,omitempty"`
}

type ProviderOnboardingProviderView struct {
	ID                         string                           `json:"id"`
	DisplayName                string                           `json:"display_name"`
	Region                     string                           `json:"region,omitempty"`
	ChannelID                  int                              `json:"channel_id"`
	AccountID                  string                           `json:"account_id"`
	BaseURL                    string                           `json:"base_url"`
	Models                     []ProviderOnboardingModelView    `json:"models"`
	Credential                 ProviderOnboardingCredentialView `json:"credential"`
	LifecycleState             string                           `json:"lifecycle_state"`
	ChannelStatus              string                           `json:"channel_status"`
	ControlRevision            string                           `json:"control_revision,omitempty"`
	RouteTestReady             bool                             `json:"route_test_ready"`
	RouteMaterialized          bool                             `json:"route_materialized"`
	RouteDeclarationVerified   bool                             `json:"route_declaration_verified"`
	RouteDeclarationFresh      bool                             `json:"route_declaration_fresh"`
	RouteDeclarationValidUntil *time.Time                       `json:"route_declaration_valid_until"`
	RouteTestVerified          bool                             `json:"route_test_verified"`
	RouteTestFresh             bool                             `json:"route_test_fresh"`
	RouteTestLatestAt          *time.Time                       `json:"route_test_latest_at"`
	AcceptanceVerified         bool                             `json:"acceptance_verified"`
	AcceptanceFresh            bool                             `json:"acceptance_fresh"`
	AcceptanceValidUntil       *time.Time                       `json:"acceptance_valid_until"`
	PlatformPublicationStatus  string                           `json:"platform_publication_status"`
	PlatformPriceStatus        string                           `json:"platform_price_status"`
	PlatformGrantStatus        string                           `json:"platform_grant_status"`
	CanDisable                 bool                             `json:"can_disable"`
	CanResume                  bool                             `json:"can_resume"`
	AvailableActions           []string                         `json:"available_actions"`
	NextAction                 string                           `json:"next_action"`
	BlockingReason             string                           `json:"blocking_reason,omitempty"`
	ActionBlockerCode          string                           `json:"action_blocker_code,omitempty"`
	BlockerCode                string                           `json:"blocker_code,omitempty"`
}

type ProviderOnboardingCatalogView struct {
	Environment string                           `json:"environment"`
	Providers   []ProviderOnboardingProviderView `json:"providers"`
}

type providerOnboardingManagementDefinition struct {
	Spec        model.ProviderOnboardingChannelSpec
	DisplayName string
	Models      []ProviderOnboardingModelView
	Compiled    credentialLateProviderDefinition
}

var providerOnboardingExpectedRoutesForEnable = currentProviderOnboardingExpectedRoutes
var providerOnboardingModelReleaseEvidenceProjection = GetPlatformModelReleaseEvidenceProjection

func currentProviderOnboardingExpectedRoutes(
	definition providerOnboardingManagementDefinition,
) ([]model.ProviderOnboardingExpectedRoute, error) {
	input, err := LoadProviderOnboardingCredentialInput(definition.Spec.Provider)
	if err != nil {
		return nil, err
	}
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return nil, snapshot.err
	}
	environment := ProviderOnboardingRuntimeEnvironment()
	return credentialLateProviderExpectedRoutes(input, definition.Compiled, snapshot.routes, environment)
}

func ProviderOnboardingRuntimeEnvironment() string {
	environment := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_ENVIRONMENT")))
	switch environment {
	case "development", "staging", "production":
		return environment
	default:
		return "unknown"
	}
}

func providerOnboardingManagementDefinitions() ([]providerOnboardingManagementDefinition, error) {
	compiled, err := credentialLateProviderDefinitions()
	if err != nil {
		return nil, err
	}
	type fixedDefinition struct {
		provider    string
		displayName string
		region      string
		channelID   int
	}
	fixed := []fixedDefinition{
		{
			provider: CredentialLateProviderGoogleGemini, displayName: "Google Gemini API",
			channelID: model.ProviderOnboardingGoogleChannelID,
		},
		{
			provider: CredentialLateProviderMiniMax, displayName: "MiniMax（中国）", region: "cn",
			channelID: model.ProviderOnboardingMiniMaxChannelID,
		},
		{
			provider: CredentialLateProviderVolcengineArk, displayName: "火山引擎 Ark",
			channelID: model.ProviderOnboardingVolcengineChannelID,
		},
	}
	result := make([]providerOnboardingManagementDefinition, 0, len(fixed))
	for _, item := range fixed {
		definition, ok := compiled[item.provider]
		if !ok || len(definition.Models) == 0 {
			return nil, ErrProviderOnboardingUnknownProvider
		}
		modelIDs := make([]string, 0, len(definition.Models))
		for publicModelID := range definition.Models {
			modelIDs = append(modelIDs, publicModelID)
		}
		sort.Strings(modelIDs)
		models := make([]ProviderOnboardingModelView, 0, len(modelIDs))
		for _, modelID := range modelIDs {
			models = append(models, ProviderOnboardingModelView{ID: modelID, DisplayName: modelID})
		}
		baseURL := definition.BaseURL
		if item.provider == CredentialLateProviderMiniMax {
			baseURL = "https://api.minimax.cn"
		}
		result = append(result, providerOnboardingManagementDefinition{
			Spec: model.ProviderOnboardingChannelSpec{
				Provider: item.provider, Region: item.region, AccountID: "primary",
				ChannelID: item.channelID, ChannelType: definition.ChannelType,
				BaseURL: baseURL, PublicModels: modelIDs,
			},
			DisplayName: item.displayName,
			Models:      models,
			Compiled:    definition,
		})
	}
	return result, nil
}

func providerOnboardingManagementDefinitionFor(
	provider string,
) (providerOnboardingManagementDefinition, error) {
	definitions, err := providerOnboardingManagementDefinitions()
	if err != nil {
		return providerOnboardingManagementDefinition{}, err
	}
	for _, definition := range definitions {
		if definition.Spec.Provider == provider {
			return definition, nil
		}
	}
	return providerOnboardingManagementDefinition{}, ErrProviderOnboardingUnknownProvider
}

func providerOnboardingBaseView(
	definition providerOnboardingManagementDefinition,
) ProviderOnboardingProviderView {
	return ProviderOnboardingProviderView{
		ID: definition.Spec.Provider, DisplayName: definition.DisplayName,
		Region: definition.Spec.Region, ChannelID: definition.Spec.ChannelID,
		AccountID: definition.Spec.AccountID, BaseURL: definition.Spec.BaseURL,
		Models:         append([]ProviderOnboardingModelView(nil), definition.Models...),
		Credential:     ProviderOnboardingCredentialView{Configured: false, KeyCount: 0},
		LifecycleState: "not_configured", ChannelStatus: "not_configured",
		PlatformPublicationStatus: "not_recorded",
		PlatformPriceStatus:       "not_recorded",
		PlatformGrantStatus:       "not_recorded",
		AvailableActions:          []string{"configure_credential"},
		NextAction:                "configure_credential",
		BlockingReason:            "credential_not_configured",
		ActionBlockerCode:         "credential_not_configured",
	}
}

func applyProviderOnboardingRouteDeclarationEvidence(
	view *ProviderOnboardingProviderView,
	expected []model.ProviderOnboardingExpectedRoute,
) {
	if view == nil || len(expected) == 0 {
		return
	}
	now := platformRouteAcceptanceNow()
	verified := true
	fresh := true
	var validUntil time.Time
	for _, route := range expected {
		if route.AcceptanceDigest == "" || route.AcceptanceNotBefore.IsZero() || route.AcceptanceNotAfter.IsZero() {
			verified = false
			fresh = false
			break
		}
		if now.Before(route.AcceptanceNotBefore) || !now.Before(route.AcceptanceNotAfter) {
			fresh = false
		}
		if validUntil.IsZero() || route.AcceptanceNotAfter.Before(validUntil) {
			validUntil = route.AcceptanceNotAfter.UTC()
		}
	}
	view.RouteDeclarationVerified = verified
	view.RouteDeclarationFresh = verified && fresh
	if verified && !validUntil.IsZero() {
		until := validUntil
		view.RouteDeclarationValidUntil = &until
	}
}

// applyProviderOnboardingRouteTestEvidence projects only durable provider
// artifact receipts that the model-release evidence service has rebound to the
// current route/profile/release/key/transport tuple. Signed route declaration
// material is intentionally insufficient here.
func applyProviderOnboardingRouteTestEvidence(
	view *ProviderOnboardingProviderView,
	expected []model.ProviderOnboardingExpectedRoute,
	projection PlatformModelReleaseEvidenceProjection,
) {
	if view == nil || len(expected) == 0 {
		return
	}
	type expectedRoute struct {
		modelID         string
		routeID         string
		channelID       int
		upstreamModel   string
		profileID       string
		profileRevision string
	}
	unique := make(map[string]expectedRoute)
	for _, route := range expected {
		key := route.Model + "\x00" + route.RouteKey
		unique[key] = expectedRoute{
			modelID: route.Model, routeID: route.RouteKey, channelID: route.ChannelID,
			upstreamModel: route.UpstreamModel, profileID: route.CapabilityProfileID,
			profileRevision: route.CapabilityProfileRevision,
		}
	}
	models := make(map[string]PlatformModelReleaseEvidence, len(projection.Models))
	for _, evidence := range projection.Models {
		models[evidence.PublicModelID] = evidence
	}
	var latest time.Time
	var validUntil time.Time
	for _, expectedRoute := range unique {
		modelEvidence, ok := models[expectedRoute.modelID]
		if !ok {
			return
		}
		matched := false
		for _, routeEvidence := range modelEvidence.Routes {
			if routeEvidence.RouteID != expectedRoute.routeID ||
				routeEvidence.ChannelID != expectedRoute.channelID ||
				routeEvidence.UpstreamModel != expectedRoute.upstreamModel ||
				routeEvidence.AdapterProfileID != expectedRoute.profileID ||
				routeEvidence.AdapterProfileRevision != expectedRoute.profileRevision {
				continue
			}
			if !routeEvidence.Fresh || routeEvidence.LatestSuccessfulTestAt == nil ||
				routeEvidence.FreshUntil == nil {
				return
			}
			when := routeEvidence.LatestSuccessfulTestAt.UTC()
			until := routeEvidence.FreshUntil.UTC()
			if !projection.GeneratedAt.Before(until) {
				return
			}
			if latest.IsZero() || when.After(latest) {
				latest = when
			}
			if validUntil.IsZero() || until.Before(validUntil) {
				validUntil = until
			}
			matched = true
			break
		}
		if !matched {
			return
		}
	}
	if len(unique) == 0 || latest.IsZero() || validUntil.IsZero() {
		return
	}
	view.RouteTestVerified = true
	view.RouteTestFresh = true
	view.RouteTestLatestAt = &latest
	// Backwards-compatible acceptance fields now mean durable route-test
	// acceptance only; declaration validity is exposed separately above.
	view.AcceptanceVerified = true
	view.AcceptanceFresh = true
	view.AcceptanceValidUntil = &validUntil
}

func providerOnboardingProviderView(
	definition providerOnboardingManagementDefinition,
	snapshot model.ProviderOnboardingChannelSnapshot,
) (ProviderOnboardingProviderView, error) {
	view := providerOnboardingBaseView(definition)
	view.Credential = ProviderOnboardingCredentialView{
		Configured:        snapshot.Credential.Configured,
		FingerprintPrefix: snapshot.Credential.FingerprintPrefix,
		KeyCount:          snapshot.Credential.KeyCount,
		UpdatedAt:         snapshot.Credential.UpdatedAt,
	}
	view.ChannelStatus = model.PlatformChannelControlStatusName(snapshot.Channel.Status)
	view.ControlRevision = model.PlatformChannelControlRevision(snapshot.Channel)
	view.RouteMaterialized = false
	if snapshot.Marker.State == model.ProviderOnboardingStateRouteReady {
		expectedRoutes, expectedErr := providerOnboardingExpectedRoutesForEnable(definition)
		if expectedErr == nil {
			applyProviderOnboardingRouteDeclarationEvidence(&view, expectedRoutes)
			view.RouteMaterialized, expectedErr = model.ProviderOnboardingCurrentRoutesReady(definition.Spec, expectedRoutes)
			if expectedErr == nil && view.RouteMaterialized {
				projection, projectionErr := providerOnboardingModelReleaseEvidenceProjection()
				if projectionErr != nil {
					expectedErr = projectionErr
				} else {
					applyProviderOnboardingRouteTestEvidence(&view, expectedRoutes, projection)
				}
			}
		}
		if expectedErr != nil {
			view.BlockerCode = "route_test_evidence_unavailable"
		} else if !view.RouteMaterialized {
			view.BlockerCode = "route_evidence_stale"
		}
	}
	view.RouteTestReady = view.RouteMaterialized
	switch snapshot.Marker.State {
	case model.ProviderOnboardingStateStaged:
		view.LifecycleState = "staged"
		if snapshot.Channel.Status != common.ChannelStatusManuallyDisabled {
			view.LifecycleState = "blocked"
			view.BlockerCode = "managed_status_mismatch"
		}
	case model.ProviderOnboardingStateRouteReady:
		switch snapshot.Channel.Status {
		case common.ChannelStatusEnabled:
			view.LifecycleState = "route_test_ready"
		case common.ChannelStatusManuallyDisabled:
			view.LifecycleState = "disabled"
		default:
			view.LifecycleState = "blocked"
			view.BlockerCode = "managed_status_mismatch"
		}
	default:
		view.LifecycleState = "blocked"
		view.BlockerCode = "managed_marker_invalid"
	}
	var enabledAbilities int64
	if err := model.DB.Model(&model.Ability{}).
		Where("channel_id = ? AND enabled = ?", definition.Spec.ChannelID, true).
		Count(&enabledAbilities).Error; err != nil {
		return ProviderOnboardingProviderView{}, err
	}
	if enabledAbilities != 0 {
		view.LifecycleState = "blocked"
		view.BlockerCode = "native_ability_enabled"
	}
	// Emergency disable is deliberately independent from route/acceptance
	// health. A stale or compromised route is exactly when an operator must be
	// able to take a managed account offline. The mutation itself locks the exact
	// channel identity and forces every native ability off.
	view.CanDisable = view.ControlRevision != "" &&
		snapshot.Channel.Status != common.ChannelStatusManuallyDisabled
	view.CanResume = view.BlockerCode == "" && view.ControlRevision != "" &&
		view.RouteMaterialized && snapshot.Channel.Status == common.ChannelStatusManuallyDisabled
	switch {
	case view.CanDisable || view.CanResume:
		view.ActionBlockerCode = ""
	case view.BlockerCode != "":
		view.ActionBlockerCode = view.BlockerCode
	case view.ControlRevision == "":
		view.ActionBlockerCode = "control_revision_missing"
	case !view.RouteMaterialized:
		view.ActionBlockerCode = "route_not_materialized"
	case !view.CanDisable && !view.CanResume:
		view.ActionBlockerCode = "lifecycle_action_unavailable"
	default:
		view.ActionBlockerCode = ""
	}
	view.AvailableActions = []string{"rotate_credential"}
	if view.CanDisable {
		view.AvailableActions = append(view.AvailableActions, "disable")
	}
	if view.CanResume {
		view.AvailableActions = append(view.AvailableActions, "resume")
	}
	switch {
	case view.CanDisable && view.BlockerCode != "":
		view.NextAction = "disable"
		view.BlockingReason = view.BlockerCode
	case snapshot.Marker.State == model.ProviderOnboardingStateStaged:
		view.NextAction = "prepare_and_sign_route_acceptance"
		view.BlockingReason = "route_acceptance_required"
	case view.BlockerCode != "":
		view.NextAction = "refresh_route_acceptance"
		view.BlockingReason = view.BlockerCode
	case view.CanResume:
		view.NextAction = "resume"
		view.BlockingReason = ""
	case view.RouteMaterialized && !view.RouteTestFresh:
		view.NextAction = "run_route_test"
		view.BlockingReason = "route_test_required"
	case view.RouteMaterialized:
		// Relay has no authoritative Platform pricing/grant/publication source.
		// Keep the cross-service stage explicit instead of treating a route test
		// as customer publication or inventing an in-process long-running job.
		view.NextAction = "complete_platform_publication"
		view.BlockingReason = "platform_publication_not_recorded"
	default:
		view.NextAction = "prepare_and_sign_route_acceptance"
		view.BlockingReason = "route_acceptance_required"
	}
	return view, nil
}

func loadProviderOnboardingProviderView(
	definition providerOnboardingManagementDefinition,
) (ProviderOnboardingProviderView, error) {
	snapshot, err := model.LoadProviderOnboardingChannel(definition.Spec)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return providerOnboardingBaseView(definition), nil
	}
	if errors.Is(err, model.ErrProviderOnboardingChannelConflict) {
		view := providerOnboardingBaseView(definition)
		view.LifecycleState = "blocked"
		view.ChannelStatus = "unknown"
		view.BlockerCode = "channel_conflict"
		return view, nil
	}
	if err != nil {
		return ProviderOnboardingProviderView{}, err
	}
	return providerOnboardingProviderView(definition, snapshot)
}

func ListProviderOnboardingProviders() (ProviderOnboardingCatalogView, error) {
	result := ProviderOnboardingCatalogView{Environment: ProviderOnboardingRuntimeEnvironment()}
	definitions, err := providerOnboardingManagementDefinitions()
	if err != nil {
		return result, err
	}
	result.Providers = make([]ProviderOnboardingProviderView, 0, len(definitions))
	for _, definition := range definitions {
		view, err := loadProviderOnboardingProviderView(definition)
		if err != nil {
			return ProviderOnboardingCatalogView{}, err
		}
		result.Providers = append(result.Providers, view)
	}
	return result, nil
}

func ValidateProviderOnboardingReason(reason string) error {
	if len(reason) < 8 || len(reason) > 500 || !utf8.ValidString(reason) ||
		strings.TrimSpace(reason) != reason || strings.ContainsAny(reason, "\x00\r\n\t") {
		return ErrProviderOnboardingReasonInvalid
	}
	return nil
}

func PutProviderOnboardingCredential(
	provider string,
	apiKey string,
	reason string,
	expectedRevision string,
) (ProviderOnboardingProviderView, bool, error) {
	definition, err := providerOnboardingManagementDefinitionFor(provider)
	if err != nil {
		return ProviderOnboardingProviderView{}, false, err
	}
	if err := ValidateProviderOnboardingReason(reason); err != nil {
		return ProviderOnboardingProviderView{}, false, err
	}
	environment := ProviderOnboardingRuntimeEnvironment()
	if err := validateCredentialLateProviderEnvironment(environment); err != nil {
		return ProviderOnboardingProviderView{}, false, fmt.Errorf("%w: %v", ErrProviderOnboardingEnvironmentNotReady, err)
	}
	input := CredentialLateProviderInput{
		SchemaVersion: CredentialLateProviderSchemaVersion,
		Provider:      definition.Spec.Provider, Region: definition.Spec.Region,
		AccountID: definition.Spec.AccountID, ChannelID: definition.Spec.ChannelID,
		APIKey: apiKey, PublicModelIDs: append([]string(nil), definition.Spec.PublicModels...),
	}
	if _, _, err := normalizeCredentialLateProviderInput(input); err != nil {
		return ProviderOnboardingProviderView{}, false, ErrProviderOnboardingCredentialInvalid
	}
	snapshot, changed, err := model.PutProviderOnboardingCredential(
		definition.Spec, apiKey, expectedRevision,
	)
	if err != nil {
		return ProviderOnboardingProviderView{}, false, err
	}
	view, err := providerOnboardingProviderView(definition, snapshot)
	return view, changed, err
}

func SetProviderOnboardingEnabled(
	provider string,
	reason string,
	expectedRevision string,
	enabled bool,
) (ProviderOnboardingProviderView, bool, error) {
	definition, err := providerOnboardingManagementDefinitionFor(provider)
	if err != nil {
		return ProviderOnboardingProviderView{}, false, err
	}
	if err := ValidateProviderOnboardingReason(reason); err != nil {
		return ProviderOnboardingProviderView{}, false, err
	}
	if enabled {
		environment := ProviderOnboardingRuntimeEnvironment()
		if err := validateCredentialLateProviderEnvironment(environment); err != nil {
			return ProviderOnboardingProviderView{}, false, fmt.Errorf("%w: %v", ErrProviderOnboardingEnvironmentNotReady, err)
		}
	}
	var expectedRoutes []model.ProviderOnboardingExpectedRoute
	if enabled {
		expectedRoutes, err = providerOnboardingExpectedRoutesForEnable(definition)
		if err != nil {
			return ProviderOnboardingProviderView{}, false, fmt.Errorf("%w: %v", model.ErrProviderOnboardingRouteNotReady, err)
		}
	}
	snapshot, changed, err := model.SetProviderOnboardingChannelEnabled(
		definition.Spec, expectedRevision, enabled, expectedRoutes,
	)
	if err != nil {
		return ProviderOnboardingProviderView{}, false, err
	}
	view, err := providerOnboardingProviderView(definition, snapshot)
	return view, changed, err
}

// LoadProviderOnboardingCredentialInput is a trusted-process bridge for
// operator CLI adapters. It is intentionally absent from every HTTP response:
// the returned CredentialLateProviderInput redacts String/GoString and may be
// used only for the existing stage/plan/finalize service calls.
func LoadProviderOnboardingCredentialInput(provider string) (CredentialLateProviderInput, error) {
	definition, err := providerOnboardingManagementDefinitionFor(provider)
	if err != nil {
		return CredentialLateProviderInput{}, err
	}
	channel, err := model.GetChannelById(definition.Spec.ChannelID, true)
	if err != nil {
		return CredentialLateProviderInput{}, err
	}
	input := CredentialLateProviderInput{
		SchemaVersion: CredentialLateProviderSchemaVersion,
		Provider:      definition.Spec.Provider, Region: definition.Spec.Region,
		AccountID: definition.Spec.AccountID, ChannelID: definition.Spec.ChannelID,
		APIKey: channel.Key, PublicModelIDs: append([]string(nil), definition.Spec.PublicModels...),
	}
	input, compiled, err := normalizeCredentialLateProviderInput(input)
	if err != nil {
		return CredentialLateProviderInput{}, err
	}
	if _, err := exactCredentialLateProviderChannel(channel, input, compiled); err != nil {
		return CredentialLateProviderInput{}, err
	}
	return input, nil
}
