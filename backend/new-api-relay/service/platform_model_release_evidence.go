package service

import (
	"crypto/sha256"
	"fmt"
	"net"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	relaydto "github.com/QuantumNous/new-api/relaykit/dto"
)

const (
	platformModelReleaseEvidenceFreshnessEnvironment = "RELAY_MODEL_RELEASE_TEST_FRESHNESS_MAX_AGE_SECONDS"
	platformModelReleaseEvidenceDefaultFreshness     = 24 * time.Hour
	platformModelReleaseEvidenceMinimumFreshness     = 5 * time.Minute
	platformModelReleaseEvidenceMaximumFreshness     = 7 * 24 * time.Hour
)

// PlatformGenerationRouteTestBinding is computed exclusively from the loaded,
// verified Relay release and the native channel/key snapshot. It is persisted
// in the channel-control operation's canonical intent before the provider call.
// CredentialFingerprintSHA256 is deliberately internal-only.
type PlatformGenerationRouteTestBinding struct {
	PublicModelID               string
	RouteID                     string
	Mode                        string
	ChannelID                   int
	NativeChannelType           int
	KeyIndex                    int
	CredentialFingerprintSHA256 string
	UpstreamModel               string
	CapabilityProfileID         string
	CapabilityProfileRevision   string
	ModelReleaseID              string
	ModelReleaseRevision        string
	CapabilityRevision          string
	RoutingReleaseSHA256        string
	RouteBindingSHA256          string
	TransportRevision           string
	TransportSHA256             string
	ProviderTaskID              string
}

type PlatformModelReleaseEvidence struct {
	PublicModelID                   string                              `json:"public_model_id"`
	CapabilityRevision              string                              `json:"capability_revision"`
	ModelReleaseID                  string                              `json:"model_release_id,omitempty"`
	ModelReleaseRevision            string                              `json:"model_release_revision,omitempty"`
	RoutingReleaseSHA256            string                              `json:"routing_release_sha256"`
	ProviderCostReadinessSHA256     string                              `json:"provider_cost_readiness_sha256"`
	ProviderCostReady               bool                                `json:"provider_cost_ready"`
	ProviderCostRectangleCount      int                                 `json:"provider_cost_rectangle_count"`
	ProviderCostReadyRectangleCount int                                 `json:"provider_cost_ready_rectangle_count"`
	RouteCount                      int                                 `json:"route_count"`
	EnabledRouteCount               int                                 `json:"enabled_route_count"`
	AcceptedRouteCount              int                                 `json:"accepted_route_count"`
	FreshTestCount                  int                                 `json:"fresh_test_count"`
	LatestSuccessfulTestAt          *time.Time                          `json:"latest_successful_test_at,omitempty"`
	Status                          string                              `json:"status"`
	Routes                          []PlatformModelReleaseRouteEvidence `json:"routes"`
}

// PlatformModelReleaseRouteEvidence is intentionally sufficient for a
// Platform operator to select one exact test target, but insufficient to call
// the provider directly. Credential fingerprints, key indexes, account IDs,
// provider URLs and acceptance signatures never cross this boundary.
type PlatformModelReleaseRouteEvidence struct {
	RouteID                         string                                  `json:"route_id"`
	ChannelID                       int                                     `json:"channel_id"`
	UpstreamModel                   string                                  `json:"upstream_model"`
	AdapterProfileID                string                                  `json:"adapter_profile_id"`
	AdapterProfileRevision          string                                  `json:"adapter_profile_revision"`
	Enabled                         bool                                    `json:"enabled"`
	Accepted                        bool                                    `json:"accepted"`
	Fresh                           bool                                    `json:"fresh"`
	LatestSuccessfulTestAt          *time.Time                              `json:"latest_successful_test_at,omitempty"`
	FreshUntil                      *time.Time                              `json:"fresh_until,omitempty"`
	RequiredTestModes               []string                                `json:"required_test_modes"`
	FreshTestModes                  []string                                `json:"fresh_test_modes"`
	ProviderCostReady               bool                                    `json:"provider_cost_ready"`
	ProviderCostRectangleCount      int                                     `json:"provider_cost_rectangle_count"`
	ProviderCostReadyRectangleCount int                                     `json:"provider_cost_ready_rectangle_count"`
	ProviderCostRectangles          []PlatformProviderCostRectangleEvidence `json:"provider_cost_rectangles"`
}

type platformProviderCostReadinessDigestRoute struct {
	RouteID                         string                                  `json:"route_id"`
	ProviderCostReady               bool                                    `json:"provider_cost_ready"`
	ProviderCostRectangleCount      int                                     `json:"provider_cost_rectangle_count"`
	ProviderCostReadyRectangleCount int                                     `json:"provider_cost_ready_rectangle_count"`
	ProviderCostRectangles          []PlatformProviderCostRectangleEvidence `json:"provider_cost_rectangles"`
}

func platformProviderCostReadinessSHA256(routes []PlatformModelReleaseRouteEvidence) (string, error) {
	rows := make([]platformProviderCostReadinessDigestRoute, 0, len(routes))
	for _, route := range routes {
		rows = append(rows, platformProviderCostReadinessDigestRoute{
			RouteID:                         route.RouteID,
			ProviderCostReady:               route.ProviderCostReady,
			ProviderCostRectangleCount:      route.ProviderCostRectangleCount,
			ProviderCostReadyRectangleCount: route.ProviderCostReadyRectangleCount,
			ProviderCostRectangles:          route.ProviderCostRectangles,
		})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].RouteID < rows[j].RouteID })
	canonical, err := platformRelayCanonicalJSON(rows)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("sha256:%x", sha256.Sum256(canonical)), nil
}

type PlatformModelReleaseEvidenceProjection struct {
	SchemaVersion              int                            `json:"schema_version"`
	Object                     string                         `json:"object"`
	GeneratedAt                time.Time                      `json:"generated_at"`
	TestFreshnessMaxAgeSeconds int64                          `json:"test_freshness_max_age_seconds"`
	Models                     []PlatformModelReleaseEvidence `json:"models"`
}

type platformRouteReleaseDigestRow struct {
	RouteID                string `json:"route_id"`
	RouteDeclarationSHA256 string `json:"route_declaration_sha256"`
	AcceptanceSHA256       string `json:"acceptance_sha256,omitempty"`
}

type platformRouteTestIntentProjection struct {
	PublicModelID               string `json:"public_model_id"`
	RouteID                     string `json:"route_id"`
	Mode                        string `json:"mode,omitempty"`
	UpstreamModel               string `json:"upstream_model"`
	CapabilityProfileID         string `json:"capability_profile_id"`
	CapabilityProfileRevision   string `json:"capability_profile_revision"`
	ModelReleaseID              string `json:"model_release_id"`
	ModelReleaseRevision        string `json:"model_release_revision"`
	CapabilityRevision          string `json:"capability_revision"`
	RoutingReleaseSHA256        string `json:"routing_release_sha256"`
	RouteBindingSHA256          string `json:"route_binding_sha256"`
	CredentialFingerprintSHA256 string `json:"credential_fingerprint_sha256"`
	TransportRevision           string `json:"transport_revision"`
	TransportSHA256             string `json:"transport_sha256"`
}

type platformRouteTransportProjection struct {
	BaseURL                string `json:"base_url"`
	ProxyEndpoint          string `json:"proxy_endpoint,omitempty"`
	ProxyHasCredentials    bool   `json:"proxy_has_credentials"`
	HTTPProtocol           string `json:"http_protocol"`
	HTTP2ConnectionShards  int    `json:"http2_connection_shards"`
	ChannelControlRevision string `json:"channel_control_revision"`
}

func platformModelReleaseTestFreshness() (time.Duration, error) {
	raw := strings.TrimSpace(os.Getenv(platformModelReleaseEvidenceFreshnessEnvironment))
	if raw == "" {
		return platformModelReleaseEvidenceDefaultFreshness, nil
	}
	seconds, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("%s must be an integer number of seconds", platformModelReleaseEvidenceFreshnessEnvironment)
	}
	value := time.Duration(seconds) * time.Second
	if value < platformModelReleaseEvidenceMinimumFreshness || value > platformModelReleaseEvidenceMaximumFreshness {
		return 0, fmt.Errorf("%s must be between %d and %d seconds",
			platformModelReleaseEvidenceFreshnessEnvironment,
			int64(platformModelReleaseEvidenceMinimumFreshness/time.Second),
			int64(platformModelReleaseEvidenceMaximumFreshness/time.Second),
		)
	}
	return value, nil
}

func platformModelRoutingReleaseSHA256(
	publicModelID string,
	routes []PlatformRelayRouteDeclaration,
	environment string,
) (string, error) {
	rows := make([]platformRouteReleaseDigestRow, 0, len(routes))
	for _, route := range routes {
		_, _, routeDigest, err := platformRouteAcceptanceBinding(publicModelID, route)
		if err != nil {
			return "", err
		}
		rows = append(rows, platformRouteReleaseDigestRow{
			RouteID:                route.RouteID,
			RouteDeclarationSHA256: routeDigest,
			AcceptanceSHA256:       route.AcceptanceDigest,
		})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].RouteID < rows[j].RouteID })
	if environment == "" {
		environment = "development"
	}
	canonical, err := platformRelayCanonicalJSON(map[string]any{
		"environment": environment,
		"model":       publicModelID,
		"routes":      rows,
	})
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("sha256:%x", sha256.Sum256(canonical)), nil
}

func normalizePlatformRouteBaseURL(channel *model.Channel) (string, error) {
	if channel == nil {
		return "", fmt.Errorf("native channel transport is unavailable")
	}
	raw := strings.TrimSpace(channel.GetBaseURL())
	if channel.Type == constant.ChannelTypeVertexAi && raw == "" {
		// An empty Vertex base URL is not ambient: the adapter deterministically
		// derives the official regional aiplatform.googleapis.com host from the
		// pinned credential/project and API-version settings.
		return "https://aiplatform.googleapis.com", nil
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed == nil || parsed.Hostname() == "" || parsed.User != nil ||
		parsed.RawQuery != "" || parsed.ForceQuery || parsed.Fragment != "" {
		return "", fmt.Errorf("native channel base URL is invalid")
	}
	parsed.Scheme = strings.ToLower(parsed.Scheme)
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return "", fmt.Errorf("native channel base URL must use HTTP or HTTPS")
	}
	parsed.Host = strings.ToLower(parsed.Host)
	parsed.Path = strings.TrimSuffix(parsed.EscapedPath(), "/")
	parsed.RawPath = ""
	parsed.RawQuery = ""
	parsed.Fragment = ""
	if PlatformRelayProductionSecurityEnabled() && channel.Type == constant.ChannelTypeGemini {
		// Gemini API-key routes are credential-bearing official-provider routes.
		// A custom host/path/port would turn an accepted Google route into a key
		// exfiltration endpoint without changing its model or channel identity.
		if parsed.Scheme != "https" || !strings.EqualFold(parsed.Hostname(), "generativelanguage.googleapis.com") ||
			parsed.Port() != "" || parsed.Path != "" {
			return "", fmt.Errorf("official Gemini route requires the approved HTTPS endpoint")
		}
		return "https://generativelanguage.googleapis.com", nil
	}
	if PlatformRelayProductionSecurityEnabled() && channel.Type == constant.ChannelTypeVertexAi {
		host := strings.ToLower(parsed.Hostname())
		officialHost := host == "aiplatform.googleapis.com" ||
			(strings.HasSuffix(host, "-aiplatform.googleapis.com") && len(strings.TrimSuffix(host, "-aiplatform.googleapis.com")) > 0)
		if parsed.Scheme != "https" || !officialHost || parsed.Port() != "" || parsed.Path != "" {
			return "", fmt.Errorf("official Vertex AI route requires an approved Google HTTPS endpoint")
		}
		return "https://" + host, nil
	}
	if channel.Type == constant.ChannelTypeVolcEngine {
		// The code-reviewed VolcEngine profiles are official-provider profiles.
		// A custom port, host, path, plaintext scheme or userinfo could exfiltrate
		// the provider credential and can never be accepted as an equivalent route.
		if parsed.Scheme != "https" || !strings.EqualFold(parsed.Hostname(), "ark.cn-beijing.volces.com") ||
			parsed.Port() != "" || parsed.Path != "" {
			return "", fmt.Errorf("official VolcEngine route requires the approved HTTPS endpoint")
		}
		return "https://ark.cn-beijing.volces.com", nil
	}
	if PlatformRelayProductionSecurityEnabled() && parsed.Scheme != "https" {
		return "", fmt.Errorf("production route transport requires HTTPS")
	}
	return parsed.String(), nil
}

func resolvePlatformRouteTransportBinding(channel *model.Channel) (string, string, error) {
	baseURL, err := normalizePlatformRouteBaseURL(channel)
	if err != nil {
		return "", "", err
	}
	settings := relaydto.ChannelSettings{}
	if channel.Setting != nil && strings.TrimSpace(*channel.Setting) != "" {
		if err := common.Unmarshal([]byte(*channel.Setting), &settings); err != nil {
			return "", "", fmt.Errorf("native channel transport settings are invalid")
		}
	}
	if err := settings.ValidateHTTPTransport(); err != nil {
		return "", "", fmt.Errorf("native channel transport settings are invalid")
	}
	proxyURL, err := common.ParseProxyURLStrict(settings.Proxy)
	if err != nil {
		return "", "", fmt.Errorf("native channel proxy is invalid")
	}
	proxyEndpoint := ""
	proxyHasCredentials := false
	if proxyURL != nil {
		port := proxyURL.Port()
		if port == "" {
			switch strings.ToLower(proxyURL.Scheme) {
			case "http":
				port = "80"
			case "https":
				port = "443"
			}
		}
		proxyEndpoint = strings.ToLower(proxyURL.Scheme) + "://" + net.JoinHostPort(strings.ToLower(proxyURL.Hostname()), port)
		proxyHasCredentials = proxyURL.User != nil
	}
	protocol := strings.ToLower(strings.TrimSpace(settings.HTTPProtocol))
	if protocol == "" {
		protocol = relaydto.HTTPProtocolAuto
	}
	shards := settings.HTTP2ConnectionShards
	if shards == 0 {
		shards = 1
	}
	revision := model.PlatformChannelControlRevision(*channel)
	canonical, err := platformRelayCanonicalJSON(platformRouteTransportProjection{
		BaseURL: baseURL, ProxyEndpoint: proxyEndpoint, ProxyHasCredentials: proxyHasCredentials,
		HTTPProtocol: protocol, HTTP2ConnectionShards: shards, ChannelControlRevision: revision,
	})
	if err != nil {
		return "", "", err
	}
	return revision, fmt.Sprintf("sha256:%x", sha256.Sum256(canonical)), nil
}

// ValidatePlatformGenerationRouteTestTransport is called immediately before
// credential hydration and again before provider I/O. It compares only opaque
// digests/revisions; BaseURL and proxy components never enter a receipt.
func ValidatePlatformGenerationRouteTestTransport(
	channel *model.Channel,
	binding *PlatformGenerationRouteTestBinding,
) error {
	if channel == nil || binding == nil || channel.Id != binding.ChannelID ||
		channel.Type != binding.NativeChannelType {
		return fmt.Errorf("route test transport identity is unavailable")
	}
	revision, digest, err := resolvePlatformRouteTransportBinding(channel)
	if err != nil {
		return err
	}
	if revision != binding.TransportRevision || digest != binding.TransportSHA256 {
		return fmt.Errorf("route test transport binding changed")
	}
	return nil
}

// PlatformGenerationRuntimeTransportBinding is the secret-free transport
// proof copied into the private native-submit fence and then into Task recovery
// evidence. It is intentionally the exact same revision/digest pair used by
// paid route acceptance.
type PlatformGenerationRuntimeTransportBinding struct {
	Revision string
	SHA256   string
}

func platformGenerationGoogleVideoRoute(route model.PlatformGenerationProviderRoute) bool {
	profile, ok := generationprofile.Get(route.CapabilityProfileID)
	if !ok || profile.Revision != route.CapabilityProfileRevision ||
		profile.NativeChannelType != route.AcceptedChannelType {
		return false
	}
	switch profile.Protocol {
	case generationprofile.GoogleGeminiInteractionsVideoProtocolV1,
		generationprofile.GoogleGeminiVeoVideoProtocolV1,
		generationprofile.GoogleVertexVeoVideoProtocolV1:
		return true
	default:
		return false
	}
}

// ResolvePlatformGenerationRuntimeTransport proves that this exact Google
// route/mode has a fresh artifact-verified acceptance receipt for the current
// BaseURL/proxy/HTTP policy. The route already owns an active admission while
// this runs, so Channel mutation is fenced until the task becomes terminal.
// Non-Google profiles deliberately retain their existing runtime contract.
func ResolvePlatformGenerationRuntimeTransport(
	route model.PlatformGenerationProviderRoute,
) (PlatformGenerationRuntimeTransportBinding, bool, error) {
	if !platformGenerationGoogleVideoRoute(route) {
		return PlatformGenerationRuntimeTransportBinding{}, false, nil
	}
	binding, err := ResolvePlatformGenerationRouteTestBindingForMode(
		route.ChannelID,
		route.Model,
		route.RouteKey,
		route.Mode,
	)
	if err != nil {
		return PlatformGenerationRuntimeTransportBinding{}, true, err
	}
	if binding.NativeChannelType != route.AcceptedChannelType ||
		binding.KeyIndex != route.KeyIndex ||
		binding.CredentialFingerprintSHA256 != route.KeyFingerprint ||
		binding.UpstreamModel != route.UpstreamModel ||
		binding.CapabilityProfileID != route.CapabilityProfileID ||
		binding.CapabilityProfileRevision != route.CapabilityProfileRevision ||
		binding.ModelReleaseID != route.ModelReleaseID ||
		binding.ModelReleaseRevision != route.ModelReleaseRevision ||
		binding.CapabilityRevision != route.ModelReleaseCapabilityRevision {
		return PlatformGenerationRuntimeTransportBinding{}, true, fmt.Errorf("Google runtime route release binding changed")
	}
	freshness, err := platformModelReleaseTestFreshness()
	if err != nil {
		return PlatformGenerationRuntimeTransportBinding{}, true, err
	}
	receipts, err := model.ListSuccessfulPlatformChannelTestsSince(time.Now().UTC().Add(-freshness))
	if err != nil {
		return PlatformGenerationRuntimeTransportBinding{}, true, err
	}
	fresh := false
	for _, receipt := range receipts {
		if platformRouteTestReceiptMatches(receipt, *binding) {
			fresh = true
			break
		}
	}
	if !fresh {
		return PlatformGenerationRuntimeTransportBinding{}, true, fmt.Errorf("Google runtime route transport lacks fresh acceptance evidence")
	}
	return PlatformGenerationRuntimeTransportBinding{
		Revision: binding.TransportRevision,
		SHA256:   binding.TransportSHA256,
	}, true, nil
}

// ValidatePlatformGenerationRuntimeTransport compares a live Channel snapshot
// with the accepted binding persisted before provider submission. It performs
// no credential reads and is safe to call before every polling request.
func ValidatePlatformGenerationRuntimeTransport(
	channel *model.Channel,
	expected PlatformGenerationRuntimeTransportBinding,
) error {
	if channel == nil || !platformRelaySnapshotDigestPattern.MatchString(expected.Revision) ||
		!platformRelaySnapshotDigestPattern.MatchString(expected.SHA256) {
		return fmt.Errorf("Google runtime transport binding is unavailable")
	}
	revision, digest, err := resolvePlatformRouteTransportBinding(channel)
	if err != nil {
		return err
	}
	if revision != expected.Revision || digest != expected.SHA256 {
		return fmt.Errorf("Google runtime transport binding changed")
	}
	return nil
}

func platformRouteNativeBindingReady(route PlatformRelayRouteDeclaration) (*model.Channel, error) {
	channel, err := model.GetChannelById(route.ChannelID, false)
	if err != nil {
		return nil, err
	}
	if channel.Status != common.ChannelStatusEnabled {
		return nil, fmt.Errorf("native channel is not enabled")
	}
	if err := platformRouteAcceptanceMatchesNativeChannel(route, channel.Type); err != nil {
		return nil, err
	}
	if route.KeyIndex < 0 {
		return nil, fmt.Errorf("route key index is invalid")
	}
	// Validate the official endpoint and the complete transport binding before
	// reading any provider credential from the encrypted key set.
	if _, _, err := resolvePlatformRouteTransportBinding(channel); err != nil {
		return nil, err
	}
	channel, err = model.GetChannelById(route.ChannelID, true)
	if err != nil {
		return nil, err
	}
	if _, _, err := resolvePlatformRouteTransportBinding(channel); err != nil {
		return nil, err
	}
	if status, exists := channel.ChannelInfo.MultiKeyStatusList[route.KeyIndex]; exists && status != common.ChannelStatusEnabled {
		return nil, fmt.Errorf("native channel key is not enabled")
	}
	key, err := channel.GetKeyAt(route.KeyIndex)
	if err != nil {
		return nil, err
	}
	fingerprint := fmt.Sprintf("%x", common.Sha256Raw([]byte(key)))
	if fingerprint != route.KeyFingerprint {
		return nil, fmt.Errorf("native channel credential release does not match route")
	}
	return channel, nil
}

// ResolvePlatformGenerationRouteTestBinding rejects caller-supplied model
// strings unless they identify one exact current route. The provider model,
// profile/release revisions and credential fingerprint are all server-derived.
func ResolvePlatformGenerationRouteTestBinding(
	channelID int,
	publicModelID string,
	routeID string,
) (*PlatformGenerationRouteTestBinding, error) {
	return ResolvePlatformGenerationRouteTestBindingForMode(channelID, publicModelID, routeID, "")
}

// ResolvePlatformGenerationRouteTestBindingForMode binds one provider test to
// one exact advertised mode. An omitted mode is accepted only as the legacy
// default so already-issued text/image receipts remain replayable; multi-mode
// release qualification always calls this function with an explicit mode.
func ResolvePlatformGenerationRouteTestBindingForMode(
	channelID int,
	publicModelID string,
	routeID string,
	mode string,
) (*PlatformGenerationRouteTestBinding, error) {
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return nil, snapshot.err
	}
	publicModelID = strings.TrimSpace(publicModelID)
	routeID = strings.TrimSpace(routeID)
	if publicModelID == "" || routeID == "" || channelID <= 0 {
		return nil, fmt.Errorf("route-bound channel test requires public_model_id and route_id")
	}
	resource, exists := snapshot.models[publicModelID]
	if !exists {
		return nil, fmt.Errorf("public model release is unavailable")
	}
	var declaration *PlatformRelayRouteDeclaration
	for i := range snapshot.routes[publicModelID] {
		candidate := &snapshot.routes[publicModelID][i]
		if candidate.RouteID == routeID && candidate.ChannelID == channelID {
			declaration = candidate
			break
		}
	}
	if declaration == nil {
		return nil, fmt.Errorf("route is not bound to the selected public model and channel")
	}
	testModes, err := platformRouteAcceptanceTestModes(*declaration)
	if err != nil {
		return nil, err
	}
	mode = strings.TrimSpace(mode)
	if mode == "" {
		if len(testModes) == 0 {
			return nil, fmt.Errorf("route adapter profile has no safe acceptance mode")
		}
		mode = testModes[0]
	}
	if !containsPlatformRouteTestMode(testModes, mode) {
		return nil, fmt.Errorf("route does not expose a self-contained acceptance probe for mode %q", mode)
	}
	channel, err := platformRouteNativeBindingReady(*declaration)
	if err != nil {
		return nil, err
	}
	transportRevision, transportSHA256, err := resolvePlatformRouteTransportBinding(channel)
	if err != nil {
		return nil, err
	}
	_, _, routeBinding, err := platformRouteAcceptanceBinding(publicModelID, *declaration)
	if err != nil {
		return nil, err
	}
	routingRelease, err := platformModelRoutingReleaseSHA256(publicModelID, snapshot.routes[publicModelID], snapshot.environmentRaw)
	if err != nil {
		return nil, err
	}
	return &PlatformGenerationRouteTestBinding{
		PublicModelID:               publicModelID,
		RouteID:                     declaration.RouteID,
		Mode:                        mode,
		ChannelID:                   declaration.ChannelID,
		NativeChannelType:           declaration.NativeChannelType,
		KeyIndex:                    declaration.KeyIndex,
		CredentialFingerprintSHA256: declaration.KeyFingerprint,
		UpstreamModel:               declaration.UpstreamModel,
		CapabilityProfileID:         declaration.ResolvedCapabilityProfileID,
		CapabilityProfileRevision:   declaration.ResolvedCapabilityProfileRevision,
		ModelReleaseID:              declaration.ResolvedModelReleaseID,
		ModelReleaseRevision:        declaration.ResolvedModelReleaseRevision,
		CapabilityRevision:          resource.CapabilityRevision,
		RoutingReleaseSHA256:        routingRelease,
		RouteBindingSHA256:          routeBinding,
		TransportRevision:           transportRevision,
		TransportSHA256:             transportSHA256,
	}, nil
}

func containsPlatformRouteTestMode(modes []string, expected string) bool {
	for _, mode := range modes {
		if mode == expected {
			return true
		}
	}
	return false
}

// platformRouteAcceptanceTestModes returns only executable provider probes.
// Google video supports a self-contained inline image fixture, so every
// advertised Google mode must be covered. Other protocols retain their
// existing zero-input mode until an attested public-media fixture exists.
func platformRouteAcceptanceTestModes(route PlatformRelayRouteDeclaration) ([]string, error) {
	profile, ok := generationprofile.Get(route.ResolvedCapabilityProfileID)
	if !ok || profile.Revision != route.ResolvedCapabilityProfileRevision ||
		profile.NativeChannelType != route.NativeChannelType {
		return nil, fmt.Errorf("route adapter profile release is unavailable")
	}
	executable := profile.AcceptanceTestModes()
	result := make([]string, 0, len(executable))
	for _, mode := range executable {
		if _, advertised := route.Capabilities.Modes[mode]; advertised {
			result = append(result, mode)
		}
	}
	switch profile.Protocol {
	case generationprofile.GoogleGeminiInteractionsVideoProtocolV1,
		generationprofile.GoogleGeminiVeoVideoProtocolV1,
		generationprofile.GoogleVertexVeoVideoProtocolV1:
		if len(result) != len(route.Capabilities.Modes) {
			return nil, fmt.Errorf("Google route has an advertised mode without a self-contained acceptance probe")
		}
	}
	if len(result) == 0 {
		return nil, fmt.Errorf("route adapter profile has no safe acceptance mode")
	}
	return result, nil
}

func platformRouteTestReceiptMatches(
	receipt model.PlatformChannelControlOperation,
	binding PlatformGenerationRouteTestBinding,
) bool {
	if receipt.ChannelID != binding.ChannelID || receipt.CompletedAt == nil {
		return false
	}
	var persisted platformRouteTestIntentProjection
	if err := common.Unmarshal([]byte(receipt.IntentJSON), &persisted); err != nil {
		return false
	}
	profile, ok := generationprofile.Get(binding.CapabilityProfileID)
	if !ok || profile.Revision != binding.CapabilityProfileRevision ||
		profile.NativeChannelType != binding.NativeChannelType {
		return false
	}
	mode := binding.Mode
	if mode == "" {
		mode, ok = profile.AcceptanceTestMode()
	}
	if mode == "" {
		return false
	}
	artifact, ok := profile.Artifact(mode)
	if !ok || artifact.Count != 1 ||
		(artifact.ContentType != "image/png" && artifact.ContentType != "video/mp4") {
		return false
	}
	persistedMode := persisted.Mode
	if persistedMode == "" {
		legacyMode, legacyOK := profile.AcceptanceTestMode()
		if !legacyOK || mode != legacyMode {
			return false
		}
	} else if persistedMode != mode {
		return false
	}
	return persisted.PublicModelID == binding.PublicModelID &&
		persisted.RouteID == binding.RouteID &&
		persisted.UpstreamModel == binding.UpstreamModel &&
		persisted.CapabilityProfileID == binding.CapabilityProfileID &&
		persisted.CapabilityProfileRevision == binding.CapabilityProfileRevision &&
		persisted.ModelReleaseID == binding.ModelReleaseID &&
		persisted.ModelReleaseRevision == binding.ModelReleaseRevision &&
		persisted.CapabilityRevision == binding.CapabilityRevision &&
		persisted.RoutingReleaseSHA256 == binding.RoutingReleaseSHA256 &&
		persisted.RouteBindingSHA256 == binding.RouteBindingSHA256 &&
		persisted.CredentialFingerprintSHA256 == binding.CredentialFingerprintSHA256 &&
		persisted.TransportRevision == binding.TransportRevision &&
		persisted.TransportSHA256 == binding.TransportSHA256 &&
		receipt.IntentTransportRevision == binding.TransportRevision &&
		receipt.IntentTransportSHA256 == binding.TransportSHA256 &&
		receipt.ProviderSubmissionState == model.PlatformChannelTestSubmissionArtifactVerified &&
		len(receipt.ProviderArtifactSHA256) == sha256.Size*2 &&
		receipt.ProviderArtifactSizeBytes > 0 &&
		receipt.ProviderArtifactContentType == artifact.ContentType
}

func platformRouteAccepted(route PlatformRelayRouteDeclaration, secure bool) bool {
	if !secure {
		return true
	}
	return route.Acceptance != nil && route.AcceptanceDigest != "" &&
		!route.AcceptanceNotAfter.IsZero() && platformRouteAcceptanceNow().Before(route.AcceptanceNotAfter)
}

// GetPlatformModelReleaseEvidenceProjection returns a secret-free model-level
// view. A route is fresh only when a durable successful channel-control
// receipt exactly matches every current route/profile/release/key revision.
func GetPlatformModelReleaseEvidenceProjection() (PlatformModelReleaseEvidenceProjection, error) {
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return PlatformModelReleaseEvidenceProjection{}, snapshot.err
	}
	freshness, err := platformModelReleaseTestFreshness()
	if err != nil {
		return PlatformModelReleaseEvidenceProjection{}, err
	}
	now := time.Now().UTC()
	receipts, err := model.ListSuccessfulPlatformChannelTestsSince(now.Add(-freshness))
	if err != nil {
		return PlatformModelReleaseEvidenceProjection{}, err
	}
	secure := snapshot.environmentRaw == "staging" || snapshot.environmentRaw == "production"
	providerCostSnapshot := currentPlatformProviderCostReadinessSnapshot()
	models := make([]PlatformModelReleaseEvidence, 0, len(snapshot.catalog.Data))
	for _, resource := range snapshot.catalog.Data {
		routes := snapshot.routes[resource.ID]
		routingRelease, err := platformModelRoutingReleaseSHA256(resource.ID, routes, snapshot.environmentRaw)
		if err != nil {
			return PlatformModelReleaseEvidenceProjection{}, err
		}
		evidence := PlatformModelReleaseEvidence{
			PublicModelID:        resource.ID,
			CapabilityRevision:   resource.CapabilityRevision,
			RoutingReleaseSHA256: routingRelease,
			RouteCount:           len(routes),
			Status:               "blocked",
			Routes:               make([]PlatformModelReleaseRouteEvidence, 0, len(routes)),
		}
		releaseIdentityInitialized := false
		releaseIdentityConsistent := true
		freshRoutes := make(map[string]struct{})
		for _, route := range routes {
			routeEvidence := PlatformModelReleaseRouteEvidence{
				RouteID:                route.RouteID,
				ChannelID:              route.ChannelID,
				UpstreamModel:          route.UpstreamModel,
				AdapterProfileID:       route.ResolvedCapabilityProfileID,
				AdapterProfileRevision: route.ResolvedCapabilityProfileRevision,
				RequiredTestModes:      []string{},
				FreshTestModes:         []string{},
				ProviderCostRectangles: []PlatformProviderCostRectangleEvidence{},
			}
			providerCost, err := platformProviderCostReadinessForRoute(
				providerCostSnapshot,
				route,
				now,
			)
			if err != nil {
				return PlatformModelReleaseEvidenceProjection{}, err
			}
			routeEvidence.ProviderCostReady = providerCost.Ready
			routeEvidence.ProviderCostRectangleCount = providerCost.RectangleCount
			routeEvidence.ProviderCostReadyRectangleCount = providerCost.ReadyCount
			routeEvidence.ProviderCostRectangles = providerCost.Rectangles
			evidence.ProviderCostRectangleCount += providerCost.RectangleCount
			evidence.ProviderCostReadyRectangleCount += providerCost.ReadyCount
			testModes, testModesErr := platformRouteAcceptanceTestModes(route)
			if testModesErr == nil {
				routeEvidence.RequiredTestModes = append([]string(nil), testModes...)
			}
			if !releaseIdentityInitialized {
				evidence.ModelReleaseID = route.ResolvedModelReleaseID
				evidence.ModelReleaseRevision = route.ResolvedModelReleaseRevision
				releaseIdentityInitialized = true
			} else if evidence.ModelReleaseID != route.ResolvedModelReleaseID || evidence.ModelReleaseRevision != route.ResolvedModelReleaseRevision {
				// Heterogeneous release sets are not representable by this v1
				// projection and therefore remain fail-closed.
				evidence.ModelReleaseID = ""
				evidence.ModelReleaseRevision = ""
				releaseIdentityConsistent = false
			}
			if platformRouteAccepted(route, secure) {
				evidence.AcceptedRouteCount++
				routeEvidence.Accepted = true
			}
			channel, err := platformRouteNativeBindingReady(route)
			if err != nil {
				evidence.Routes = append(evidence.Routes, routeEvidence)
				continue
			}
			transportRevision, transportSHA256, err := resolvePlatformRouteTransportBinding(channel)
			if err != nil {
				evidence.Routes = append(evidence.Routes, routeEvidence)
				continue
			}
			evidence.EnabledRouteCount++
			routeEvidence.Enabled = true
			_, _, routeBinding, err := platformRouteAcceptanceBinding(resource.ID, route)
			if err != nil {
				return PlatformModelReleaseEvidenceProjection{}, err
			}
			if testModesErr != nil {
				evidence.Routes = append(evidence.Routes, routeEvidence)
				continue
			}
			routeFresh := true
			for _, mode := range testModes {
				binding := PlatformGenerationRouteTestBinding{
					PublicModelID: resource.ID, RouteID: route.RouteID, Mode: mode, ChannelID: route.ChannelID,
					NativeChannelType: route.NativeChannelType, KeyIndex: route.KeyIndex,
					CredentialFingerprintSHA256: route.KeyFingerprint, UpstreamModel: route.UpstreamModel,
					CapabilityProfileID: route.ResolvedCapabilityProfileID, CapabilityProfileRevision: route.ResolvedCapabilityProfileRevision,
					ModelReleaseID: route.ResolvedModelReleaseID, ModelReleaseRevision: route.ResolvedModelReleaseRevision,
					CapabilityRevision: resource.CapabilityRevision, RoutingReleaseSHA256: routingRelease,
					RouteBindingSHA256: routeBinding, TransportRevision: transportRevision,
					TransportSHA256: transportSHA256,
				}
				modeFresh := false
				for _, receipt := range receipts {
					if !platformRouteTestReceiptMatches(receipt, binding) {
						continue
					}
					when := receipt.CompletedAt.UTC()
					freshUntil := when.Add(freshness)
					if !now.Before(freshUntil) {
						continue
					}
					modeFresh = true
					if evidence.LatestSuccessfulTestAt == nil || when.After(*evidence.LatestSuccessfulTestAt) {
						evidence.LatestSuccessfulTestAt = &when
					}
					if routeEvidence.LatestSuccessfulTestAt == nil || when.After(*routeEvidence.LatestSuccessfulTestAt) {
						routeEvidence.LatestSuccessfulTestAt = &when
					}
					if routeEvidence.FreshUntil == nil || freshUntil.Before(*routeEvidence.FreshUntil) {
						routeEvidence.FreshUntil = &freshUntil
					}
					break
				}
				if !modeFresh {
					routeFresh = false
				} else {
					routeEvidence.FreshTestModes = append(routeEvidence.FreshTestModes, mode)
				}
			}
			if routeFresh {
				freshRoutes[route.RouteID] = struct{}{}
				routeEvidence.Fresh = true
			}
			evidence.Routes = append(evidence.Routes, routeEvidence)
		}
		evidence.FreshTestCount = len(freshRoutes)
		evidence.ProviderCostReady = evidence.RouteCount > 0 &&
			evidence.ProviderCostRectangleCount > 0 &&
			evidence.ProviderCostReadyRectangleCount == evidence.ProviderCostRectangleCount
		evidence.ProviderCostReadinessSHA256, err = platformProviderCostReadinessSHA256(evidence.Routes)
		if err != nil {
			return PlatformModelReleaseEvidenceProjection{}, err
		}
		if releaseIdentityConsistent && evidence.RouteCount > 0 && evidence.EnabledRouteCount == evidence.RouteCount &&
			evidence.AcceptedRouteCount == evidence.RouteCount && evidence.FreshTestCount == evidence.RouteCount {
			evidence.Status = "ready"
		}
		models = append(models, evidence)
	}
	return PlatformModelReleaseEvidenceProjection{
		SchemaVersion:              1,
		Object:                     "relay.model_release_evidence",
		GeneratedAt:                now,
		TestFreshnessMaxAgeSeconds: int64(freshness / time.Second),
		Models:                     models,
	}, nil
}
