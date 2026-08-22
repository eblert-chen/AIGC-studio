package service

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"fmt"
	"net"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/google/uuid"
)

const PlatformRelayUpstreamGitRevision = "0ab02020603d22e5613bc4cf46bfab06f8567769"

var (
	platformRelaySourceRevisionPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
	platformRelayImageDigestPattern    = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	platformRelaySnapshotDigestPattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	platformRelayClientIDPattern       = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
	platformRelayProcessInstanceID     = uuid.NewString()
	// Docker builds replace these values with -X linker inputs derived from
	// the frozen source snapshot. Production never trusts environment-only
	// source provenance.
	platformRelayCompiledUpstreamRevision  = "unknown"
	platformRelayCompiledSourceRevision    = "unknown"
	platformRelayCompiledSnapshotSHA256    = "unknown"
	platformRelayCompiledSnapshotFileCount = "unknown"
)

func platformRelayCanonicalTenantID(value string) bool {
	parsed, err := uuid.Parse(value)
	return err == nil && parsed != uuid.Nil && parsed.String() == value
}

type PlatformRelayBuildProvenance struct {
	InstanceID                     string `json:"instance_id"`
	UpstreamGitRevision            string `json:"upstream_git_revision"`
	SourceGitRevision              string `json:"source_git_revision"`
	SourceSnapshotSHA256           string `json:"source_snapshot_sha256"`
	SourceSnapshotFiles            int    `json:"source_snapshot_file_count"`
	ImageDigest                    string `json:"image_digest"`
	RouteAcceptanceTrustKeysSHA256 string `json:"route_acceptance_trust_keys_sha256"`
}

// PlatformRelayCompiledBuildIdentity is the environment-independent identity
// embedded into release binaries by the Docker build. It is intentionally
// safe to print from an offline container: it contains no deployment values,
// credentials, process identifiers, or network-derived state.
type PlatformRelayCompiledBuildIdentity struct {
	SchemaVersion                  int    `json:"schema_version"`
	Kind                           string `json:"kind"`
	UpstreamGitRevision            string `json:"upstream_git_revision"`
	SourceRevision                 string `json:"source_revision"`
	SourceSnapshotSHA256           string `json:"source_snapshot_sha256"`
	SourceSnapshotFileCount        int    `json:"source_snapshot_file_count"`
	RouteAcceptanceTrustKeysSHA256 string `json:"route_acceptance_trust_keys_sha256"`
}

func GetPlatformRelayCompiledBuildIdentity() PlatformRelayCompiledBuildIdentity {
	fileCount, _ := strconv.Atoi(platformRelayCompiledSnapshotFileCount)
	return PlatformRelayCompiledBuildIdentity{
		SchemaVersion:                  1,
		Kind:                           "relay_compiled_build_identity",
		UpstreamGitRevision:            strings.ToLower(strings.TrimSpace(platformRelayCompiledUpstreamRevision)),
		SourceRevision:                 strings.ToLower(strings.TrimSpace(platformRelayCompiledSourceRevision)),
		SourceSnapshotSHA256:           strings.ToLower(strings.TrimSpace(platformRelayCompiledSnapshotSHA256)),
		SourceSnapshotFileCount:        fileCount,
		RouteAcceptanceTrustKeysSHA256: strings.ToLower(strings.TrimSpace(platformRelayCompiledRouteAcceptanceKeysSHA256)),
	}
}

func GetPlatformRelayBuildProvenance() PlatformRelayBuildProvenance {
	sourceRevision := platformRelayCompiledSourceRevision
	snapshotSHA256 := platformRelayCompiledSnapshotSHA256
	fileCountRaw := platformRelayCompiledSnapshotFileCount
	upstreamRevision := platformRelayCompiledUpstreamRevision
	if sourceRevision == "unknown" {
		sourceRevision = strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_REVISION"))
	}
	if snapshotSHA256 == "unknown" {
		snapshotSHA256 = strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256"))
	}
	if fileCountRaw == "unknown" {
		fileCountRaw = strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT"))
	}
	if upstreamRevision == "unknown" {
		upstreamRevision = PlatformRelayUpstreamGitRevision
	}
	fileCount, _ := strconv.Atoi(fileCountRaw)
	return PlatformRelayBuildProvenance{
		InstanceID:                     platformRelayProcessInstanceID,
		UpstreamGitRevision:            strings.ToLower(strings.TrimSpace(upstreamRevision)),
		SourceGitRevision:              strings.ToLower(strings.TrimSpace(sourceRevision)),
		SourceSnapshotSHA256:           strings.ToLower(strings.TrimSpace(snapshotSHA256)),
		SourceSnapshotFiles:            fileCount,
		ImageDigest:                    strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_IMAGE_DIGEST"))),
		RouteAcceptanceTrustKeysSHA256: strings.ToLower(strings.TrimSpace(platformRelayCompiledRouteAcceptanceKeysSHA256)),
	}
}

func ValidatePlatformRelayBuildProvenance(production bool) error {
	if !production {
		return nil
	}
	provenance := GetPlatformRelayBuildProvenance()
	compiledFileCount, compiledFileCountErr := strconv.Atoi(platformRelayCompiledSnapshotFileCount)
	if platformRelayCompiledUpstreamRevision != PlatformRelayUpstreamGitRevision ||
		!platformRelaySourceRevisionPattern.MatchString(platformRelayCompiledSourceRevision) ||
		!platformRelaySnapshotDigestPattern.MatchString(platformRelayCompiledSnapshotSHA256) ||
		compiledFileCountErr != nil || compiledFileCount < 1 {
		return fmt.Errorf("production Relay requires build-time frozen source provenance")
	}
	if !platformRelaySnapshotDigestPattern.MatchString(platformRelayCompiledRouteAcceptanceKeysSHA256) ||
		platformRelayCompiledRouteAcceptanceKeysSHA256 == "sha256:"+strings.Repeat("0", 64) {
		return fmt.Errorf("production Relay requires a build-time route acceptance trust-key digest")
	}
	environmentFileCount, environmentFileCountErr := strconv.Atoi(strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT")))
	if strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_REVISION"))) != platformRelayCompiledSourceRevision ||
		strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256"))) != platformRelayCompiledSnapshotSHA256 ||
		environmentFileCountErr != nil || environmentFileCount != compiledFileCount {
		return fmt.Errorf("production Relay deployment provenance does not match the compiled source snapshot")
	}
	if !platformRelaySourceRevisionPattern.MatchString(provenance.SourceGitRevision) ||
		provenance.SourceGitRevision == strings.Repeat("0", 40) ||
		provenance.SourceGitRevision == provenance.UpstreamGitRevision {
		return fmt.Errorf("production Relay requires the committed extension fork revision in RELAY_COMPAT_SOURCE_REVISION")
	}
	if !platformRelayImageDigestPattern.MatchString(provenance.ImageDigest) ||
		provenance.ImageDigest == "sha256:"+strings.Repeat("0", 64) {
		return fmt.Errorf("production Relay requires an immutable non-placeholder RELAY_COMPAT_IMAGE_DIGEST")
	}
	if !platformRelaySnapshotDigestPattern.MatchString(provenance.SourceSnapshotSHA256) ||
		provenance.SourceSnapshotSHA256 == "sha256:"+strings.Repeat("0", 64) || provenance.SourceSnapshotFiles < 1 {
		return fmt.Errorf("production Relay requires a frozen source snapshot digest and file count")
	}
	_, trustKeysDigest, err := loadPlatformRouteAcceptanceTrustStore(true)
	if err != nil {
		return err
	}
	if trustKeysDigest != platformRelayCompiledRouteAcceptanceKeysSHA256 {
		return fmt.Errorf("production Relay route acceptance trust keys do not match the compiled release")
	}
	return nil
}

func AuthenticatePlatformRelayRuntimeIdentity(provided string) bool {
	expected := PlatformRelayInternalAdmissionToken()
	providedDigest := sha256.Sum256([]byte(provided))
	expectedDigest := sha256.Sum256([]byte(expected))
	return expected != "" && subtle.ConstantTimeCompare(providedDigest[:], expectedDigest[:]) == 1
}

type PlatformRelayCredential struct {
	TenantID              string `json:"tenant_id"`
	APIKey                string `json:"api_key"`
	UpstreamToken         string `json:"upstream_token"`
	CallbackURL           string `json:"callback_url,omitempty"`
	CallbackSigningSecret string `json:"callback_signing_secret,omitempty"`
}

type PlatformRelayPrincipal struct {
	ClientID       string
	TenantID       string
	UpstreamToken  string
	CallbackURL    string
	CallbackSecret string
}

type platformRelayConfigSnapshot struct {
	loaded                       bool
	credentialsRaw               string
	capabilitiesRaw              string
	routesRaw                    string
	environmentRaw               string
	deploymentRaw                string
	acceptancePublicKeysRaw      string
	acceptancePrivateMaterialRaw string
	acceptanceProvenanceRaw      string
	acceptanceExpiresAt          time.Time
	acceptanceAudit              PlatformRouteAcceptanceAudit
	credentials                  map[string]PlatformRelayCredential
	catalog                      dto.PlatformModelCatalog
	models                       map[string]dto.PlatformModelResource
	routes                       map[string][]PlatformRelayRouteDeclaration
	err                          error
}

var platformRelayConfigCache struct {
	sync.Mutex
	snapshot platformRelayConfigSnapshot
}

func PlatformRelayCompatEnabled() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("RELAY_COMPAT_ENABLED")), "true")
}

// PlatformRelayProductionSecurityEnabled is true for both staging and
// production deployments. Staging exercises real-provider migration gates and
// must not silently fall back to development transport or secret policy.
func PlatformRelayProductionSecurityEnabled() bool {
	environment := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_ENVIRONMENT")))
	if environment == "staging" || environment == "production" {
		return true
	}
	raw, present := os.LookupEnv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED")
	if !present || strings.TrimSpace(raw) == "" {
		return false
	}
	required, err := strconv.ParseBool(strings.TrimSpace(raw))
	return err != nil || required
}

func validatePlatformRelayEnvironment(environment string) error {
	switch environment {
	case "", "development", "test", "staging", "production":
	default:
		return fmt.Errorf("RELAY_COMPAT_ENVIRONMENT must be one of development, test, staging, or production")
	}
	for _, variable := range []string{"APP_ENV", "DEPLOYMENT_ENV", "ENVIRONMENT"} {
		outer := strings.ToLower(strings.TrimSpace(os.Getenv(variable)))
		if outer == "production" && environment != "production" {
			return fmt.Errorf("%s=production requires RELAY_COMPAT_ENVIRONMENT=production", variable)
		}
		if outer == "staging" && environment != "staging" && environment != "production" {
			return fmt.Errorf("%s=staging requires staging or production Relay security policy", variable)
		}
	}
	rawAttestation, attestationPresent := os.LookupEnv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED")
	attestationRequired := false
	if attestationPresent && strings.TrimSpace(rawAttestation) != "" {
		parsed, err := strconv.ParseBool(strings.TrimSpace(rawAttestation))
		if err != nil {
			return fmt.Errorf("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED must be a boolean")
		}
		attestationRequired = parsed
	}
	if attestationRequired {
		appEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
		deploymentEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("DEPLOYMENT_ENV")))
		outerEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("ENVIRONMENT")))
		if appEnvironment != deploymentEnvironment || appEnvironment != environment ||
			(appEnvironment != "staging" && appEnvironment != "production") ||
			(outerEnvironment != "" && outerEnvironment != appEnvironment) {
			return fmt.Errorf("role-attested Relay environments must match exactly")
		}
	}
	return nil
}

func loadPlatformRelayConfig() platformRelayConfigSnapshot {
	credentialsRaw := strings.TrimSpace(os.Getenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON"))
	installedCredentials, installedCredentialFingerprint, installedRuntimeSecrets := platformRelayAPIRuntimeCredentials()
	if installedRuntimeSecrets {
		credentialsRaw = "file-sha256:" + hex.EncodeToString(installedCredentialFingerprint[:])
	}
	capabilitiesRaw := strings.TrimSpace(os.Getenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON"))
	routesRaw := strings.TrimSpace(os.Getenv("RELAY_COMPAT_MODEL_ROUTES_JSON"))
	environmentRaw := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_ENVIRONMENT")))
	deploymentRaw := strings.Join([]string{os.Getenv("APP_ENV"), os.Getenv("DEPLOYMENT_ENV"), os.Getenv("ENVIRONMENT")}, "\x00")
	acceptancePublicKeysRaw := strings.TrimSpace(os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON"))
	acceptancePrivateMaterialRaw := strings.Join([]string{
		os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY"),
		os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON"),
		os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY"),
	}, "\x00")
	acceptanceProvenanceRaw := strings.Join([]string{
		os.Getenv("RELAY_COMPAT_SOURCE_REVISION"),
		os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256"),
		os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT"),
		os.Getenv("RELAY_COMPAT_IMAGE_DIGEST"),
	}, "\x00")
	now := platformRouteAcceptanceNow()

	platformRelayConfigCache.Lock()
	defer platformRelayConfigCache.Unlock()
	if platformRelayConfigCache.snapshot.loaded &&
		platformRelayConfigCache.snapshot.credentialsRaw == credentialsRaw &&
		platformRelayConfigCache.snapshot.capabilitiesRaw == capabilitiesRaw &&
		platformRelayConfigCache.snapshot.routesRaw == routesRaw &&
		platformRelayConfigCache.snapshot.environmentRaw == environmentRaw &&
		platformRelayConfigCache.snapshot.deploymentRaw == deploymentRaw &&
		platformRelayConfigCache.snapshot.acceptancePublicKeysRaw == acceptancePublicKeysRaw &&
		platformRelayConfigCache.snapshot.acceptancePrivateMaterialRaw == acceptancePrivateMaterialRaw &&
		platformRelayConfigCache.snapshot.acceptanceProvenanceRaw == acceptanceProvenanceRaw &&
		(platformRelayConfigCache.snapshot.acceptanceExpiresAt.IsZero() || now.Before(platformRelayConfigCache.snapshot.acceptanceExpiresAt)) {
		return platformRelayConfigCache.snapshot
	}

	snapshot := platformRelayConfigSnapshot{
		loaded:                       true,
		credentialsRaw:               credentialsRaw,
		capabilitiesRaw:              capabilitiesRaw,
		routesRaw:                    routesRaw,
		environmentRaw:               environmentRaw,
		deploymentRaw:                deploymentRaw,
		acceptancePublicKeysRaw:      acceptancePublicKeysRaw,
		acceptancePrivateMaterialRaw: acceptancePrivateMaterialRaw,
		acceptanceProvenanceRaw:      acceptanceProvenanceRaw,
		credentials:                  make(map[string]PlatformRelayCredential),
		models:                       make(map[string]dto.PlatformModelResource),
		routes:                       make(map[string][]PlatformRelayRouteDeclaration),
	}
	if err := validatePlatformRelayEnvironment(environmentRaw); err != nil {
		snapshot.err = err
		platformRelayConfigCache.snapshot = snapshot
		return snapshot
	}
	if credentialsRaw == "" {
		snapshot.err = fmt.Errorf("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON is required")
		platformRelayConfigCache.snapshot = snapshot
		return snapshot
	}
	if installedRuntimeSecrets {
		for clientID, credential := range installedCredentials {
			snapshot.credentials[clientID] = credential
		}
	} else {
		if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(credentialsRaw), &snapshot.credentials); err != nil {
			snapshot.err = fmt.Errorf("invalid Relay compatibility credentials: %w", err)
			platformRelayConfigCache.snapshot = snapshot
			return snapshot
		}
	}
	for clientID, credential := range snapshot.credentials {
		if !platformRelayClientIDPattern.MatchString(clientID) {
			snapshot.err = fmt.Errorf("Relay compatibility client id is invalid")
			break
		}
		if !platformRelayCanonicalTenantID(credential.TenantID) {
			snapshot.err = fmt.Errorf("Relay compatibility tenant id is invalid")
			break
		}
		if credential.APIKey == "" || credential.UpstreamToken == "" {
			snapshot.err = fmt.Errorf("Relay compatibility credentials require api_key and upstream_token")
			break
		}
		if (credential.CallbackURL == "") != (credential.CallbackSigningSecret == "") {
			snapshot.err = fmt.Errorf("Relay compatibility callback URL and secret must be configured together")
			break
		}
		production := environmentRaw == "production" || environmentRaw == "staging"
		if production && (len([]byte(credential.APIKey)) < 32 || len([]byte(credential.UpstreamToken)) < 32) {
			snapshot.err = fmt.Errorf("production Relay compatibility credentials must contain at least 32 bytes")
			break
		}
		if production {
			if _, ok := protectedPlatformRelayTokenKey(credential.UpstreamToken); !ok {
				snapshot.err = fmt.Errorf("production Relay compatibility upstream token format is invalid")
				break
			}
		}
		if credential.CallbackURL != "" {
			normalizedURL, err := normalizePlatformCallbackURL(credential.CallbackURL, production)
			if err != nil {
				snapshot.err = fmt.Errorf("Relay compatibility callback URL is invalid: %w", err)
				break
			}
			if len([]byte(credential.CallbackSigningSecret)) < 32 {
				snapshot.err = fmt.Errorf("Relay compatibility callback signing secret must contain at least 32 bytes")
				break
			}
			if production && platformRelaySecretIsPlaceholder(credential.CallbackSigningSecret) {
				snapshot.err = fmt.Errorf("Relay compatibility callback signing secret is a placeholder")
				break
			}
			credential.CallbackURL = normalizedURL
			snapshot.credentials[clientID] = credential
		}
	}
	if snapshot.err != nil {
		platformRelayConfigCache.snapshot = snapshot
		return snapshot
	}

	capabilities, routes, err := parsePlatformRelayCapabilities(capabilitiesRaw, routesRaw, environmentRaw)
	if err != nil {
		snapshot.err = err
		platformRelayConfigCache.snapshot = snapshot
		return snapshot
	}
	snapshot.routes = routes
	acceptanceAudit, acceptanceExpiresAt, err := buildPlatformRouteAcceptanceAudit(environmentRaw, routes)
	if err != nil {
		snapshot.err = fmt.Errorf("build Relay route acceptance audit: %w", err)
		platformRelayConfigCache.snapshot = snapshot
		return snapshot
	}
	snapshot.acceptanceAudit = acceptanceAudit
	snapshot.acceptanceExpiresAt = acceptanceExpiresAt
	modelIDs := make([]string, 0, len(capabilities))
	for modelID := range capabilities {
		modelIDs = append(modelIDs, modelID)
	}
	sort.Strings(modelIDs)
	resources := make([]dto.PlatformModelResource, 0, len(modelIDs))
	for _, modelID := range modelIDs {
		capability := capabilities[modelID]
		if err := validatePlatformCapability(modelID, capability); err != nil {
			snapshot.err = err
			platformRelayConfigCache.snapshot = snapshot
			return snapshot
		}
		serialized, err := platformRelayCanonicalJSON(capability)
		if err != nil {
			snapshot.err = err
			platformRelayConfigCache.snapshot = snapshot
			return snapshot
		}
		digest := sha256.Sum256(serialized)
		resource := dto.PlatformModelResource{
			APIVersion:         dto.PlatformRelayAPIVersion,
			SchemaVersion:      dto.PlatformRelaySchemaVersion,
			ID:                 modelID,
			Object:             "model",
			CapabilityRevision: fmt.Sprintf("sha256:%x", digest),
			Capabilities:       capability,
		}
		resources = append(resources, resource)
		snapshot.models[modelID] = resource
	}
	revisionInput := make([]map[string]string, 0, len(resources))
	for _, resource := range resources {
		revisionInput = append(revisionInput, map[string]string{
			"id":       resource.ID,
			"revision": resource.CapabilityRevision,
		})
	}
	serialized, err := platformRelayCanonicalJSON(revisionInput)
	if err != nil {
		snapshot.err = err
		platformRelayConfigCache.snapshot = snapshot
		return snapshot
	}
	digest := sha256.Sum256(serialized)
	snapshot.catalog = dto.PlatformModelCatalog{
		APIVersion:      dto.PlatformRelayAPIVersion,
		SchemaVersion:   dto.PlatformRelaySchemaVersion,
		Object:          "list",
		Data:            resources,
		CatalogRevision: fmt.Sprintf("sha256:%x", digest),
	}
	platformRelayConfigCache.snapshot = snapshot
	return snapshot
}

// platformRelayCanonicalJSON matches the frozen Python Relay revision
// algorithm: object keys are ordered recursively and no insignificant
// whitespace is emitted. Marshaling structs directly is not sufficient
// because encoding/json preserves struct field order rather than sorting it.
func platformRelayCanonicalJSON(value any) ([]byte, error) {
	serialized, err := common.Marshal(value)
	if err != nil {
		return nil, err
	}
	var canonical any
	if err := common.Unmarshal(serialized, &canonical); err != nil {
		return nil, err
	}
	return common.Marshal(canonical)
}

func AuthenticatePlatformRelayClient(clientID string, apiKey string) (*PlatformRelayPrincipal, error) {
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return nil, snapshot.err
	}
	credential, ok := snapshot.credentials[clientID]
	expectedAPIKey := credential.APIKey
	if !ok {
		expectedAPIKey = "invalid-platform-relay-api-key"
	}
	providedDigest := sha256.Sum256([]byte(apiKey))
	expectedDigest := sha256.Sum256([]byte(expectedAPIKey))
	if subtle.ConstantTimeCompare(providedDigest[:], expectedDigest[:]) != 1 || !ok {
		return nil, fmt.Errorf("invalid Relay client credentials")
	}
	if err := ValidateProtectedPlatformRelayServicePrincipals(); err != nil {
		return nil, err
	}
	return &PlatformRelayPrincipal{
		ClientID:       clientID,
		TenantID:       credential.TenantID,
		UpstreamToken:  credential.UpstreamToken,
		CallbackURL:    credential.CallbackURL,
		CallbackSecret: credential.CallbackSigningSecret,
	}, nil
}

// GetPlatformRelayPrincipalForJob resolves only an already authenticated,
// persisted client/tenant binding. It never accepts a tenant from a request.
func GetPlatformRelayPrincipalForJob(clientID string, tenantID string) (*PlatformRelayPrincipal, error) {
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return nil, snapshot.err
	}
	credential, ok := snapshot.credentials[clientID]
	if !ok || credential.TenantID != tenantID {
		return nil, fmt.Errorf("stored Relay client binding is no longer configured")
	}
	if err := ValidateProtectedPlatformRelayServicePrincipals(); err != nil {
		return nil, err
	}
	return &PlatformRelayPrincipal{
		ClientID:       clientID,
		TenantID:       credential.TenantID,
		UpstreamToken:  credential.UpstreamToken,
		CallbackURL:    credential.CallbackURL,
		CallbackSecret: credential.CallbackSigningSecret,
	}, nil
}

func GetPlatformRelayModelCatalog() (dto.PlatformModelCatalog, error) {
	snapshot := loadPlatformRelayConfig()
	return snapshot.catalog, snapshot.err
}

func GetPlatformRelayModel(modelID string) (dto.PlatformModelResource, bool, error) {
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return dto.PlatformModelResource{}, false, snapshot.err
	}
	resource, ok := snapshot.models[modelID]
	return resource, ok, nil
}

func GetPlatformRelayRoutes(modelID string, mode string) ([]PlatformRelayRouteDeclaration, error) {
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return nil, snapshot.err
	}
	configured := snapshot.routes[modelID]
	routes := make([]PlatformRelayRouteDeclaration, 0, len(configured))
	for _, route := range configured {
		if _, ok := route.Capabilities.Modes[mode]; ok {
			routes = append(routes, route)
		}
	}
	return routes, nil
}

func GetPlatformRouteAcceptanceAudit() (PlatformRouteAcceptanceAudit, error) {
	snapshot := loadPlatformRelayConfig()
	return snapshot.acceptanceAudit, snapshot.err
}

func ValidatePlatformGenerationCapability(request dto.PlatformGenerationRequest) error {
	resource, ok, err := GetPlatformRelayModel(request.Model)
	if err != nil {
		return err
	}
	if !ok {
		return fmt.Errorf("MODEL_CAPABILITY_UNAVAILABLE")
	}
	if resource.CapabilityRevision != request.ExpectedCapabilityRevision {
		return fmt.Errorf("CAPABILITY_REVISION_MISMATCH")
	}
	mode, ok := resource.Capabilities.Modes[request.Mode]
	if !ok {
		return fmt.Errorf("MODE_NOT_SUPPORTED_BY_MODEL")
	}
	if utf8.RuneCountInString(request.Inputs.Prompt) > mode.Limits.MaxPromptLength ||
		!containsInt(mode.Limits.DurationSeconds, request.Output.DurationSeconds) ||
		!containsString(mode.Limits.AspectRatios, request.Output.AspectRatio) ||
		!containsString(mode.Limits.Resolutions, request.Output.Resolution) ||
		!containsInt(mode.Limits.OutputCounts, request.Output.Count) ||
		(request.Output.FaceEnabled && !mode.SupportsFace) {
		return fmt.Errorf("REQUEST_NOT_SUPPORTED_BY_MODEL")
	}
	counts := map[string]int{"image": 0, "video": 0, "audio": 0}
	for _, asset := range request.Inputs.Assets {
		if !containsString(mode.InputMediaTypes, asset.MediaType) {
			return fmt.Errorf("REQUEST_NOT_SUPPORTED_BY_MODEL")
		}
		counts[asset.MediaType]++
	}
	if counts["image"] > mode.Limits.MaxImages || counts["video"] > mode.Limits.MaxVideos || counts["audio"] > mode.Limits.MaxAudio {
		return fmt.Errorf("REQUEST_NOT_SUPPORTED_BY_MODEL")
	}
	return nil
}

func validatePlatformCapability(modelID string, capability dto.PlatformGenerationCapabilities) error {
	if strings.TrimSpace(modelID) == "" || len(modelID) > 128 || capability.SchemaVersion != 1 || len(capability.Modes) == 0 {
		return fmt.Errorf("Relay capability for model %q is invalid", modelID)
	}
	for modeName, mode := range capability.Modes {
		switch modeName {
		case "text_to_image", "text_to_video", "image_to_video", "video_to_video":
		default:
			return fmt.Errorf("Relay capability for model %q contains an unknown mode", modelID)
		}
		limits := mode.Limits
		if limits.MaxPromptLength < 1 || limits.MaxPromptLength > 10_000 ||
			limits.MaxImages < 0 || limits.MaxImages > 15 || limits.MaxVideos < 0 || limits.MaxVideos > 15 || limits.MaxAudio < 0 || limits.MaxAudio > 15 ||
			limits.MaxImages+limits.MaxVideos+limits.MaxAudio > 15 ||
			len(limits.DurationSeconds) == 0 || len(limits.AspectRatios) == 0 ||
			len(limits.Resolutions) == 0 || len(limits.OutputCounts) == 0 {
			return fmt.Errorf("Relay capability limits for model %q are invalid", modelID)
		}
		mediaTypes := make(map[string]struct{}, len(mode.InputMediaTypes))
		for _, mediaType := range mode.InputMediaTypes {
			switch mediaType {
			case "image", "video", "audio":
			default:
				return fmt.Errorf("Relay capability for model %q contains an unknown input media type", modelID)
			}
			if _, duplicate := mediaTypes[mediaType]; duplicate {
				return fmt.Errorf("Relay capability for model %q contains duplicate input media types", modelID)
			}
			mediaTypes[mediaType] = struct{}{}
		}
		if (limits.MaxImages > 0) != containsString(mode.InputMediaTypes, "image") ||
			(limits.MaxVideos > 0) != containsString(mode.InputMediaTypes, "video") ||
			(limits.MaxAudio > 0) != containsString(mode.InputMediaTypes, "audio") {
			return fmt.Errorf("Relay capability media limits for model %q are internally inconsistent", modelID)
		}
		if modeName == "image_to_video" && limits.MaxImages < 1 {
			return fmt.Errorf("Relay image_to_video capability for model %q requires image input", modelID)
		}
		if modeName == "video_to_video" && limits.MaxVideos < 1 {
			return fmt.Errorf("Relay video_to_video capability for model %q requires video input", modelID)
		}
		for _, duration := range limits.DurationSeconds {
			if duration < 1 || duration > 3600 {
				return fmt.Errorf("Relay capability durations for model %q are invalid", modelID)
			}
		}
		aspectPattern := regexp.MustCompile(`^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$`)
		for _, aspectRatio := range limits.AspectRatios {
			if !aspectPattern.MatchString(aspectRatio) || len(aspectRatio) > 16 {
				return fmt.Errorf("Relay capability aspect ratios for model %q are invalid", modelID)
			}
		}
		resolutionPattern := regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$`)
		for _, resolution := range limits.Resolutions {
			if !resolutionPattern.MatchString(resolution) {
				return fmt.Errorf("Relay capability resolutions for model %q are invalid", modelID)
			}
		}
		for _, count := range limits.OutputCounts {
			if count < 1 || count > 16 {
				return fmt.Errorf("Relay capability output counts for model %q are invalid", modelID)
			}
		}
		resourceKeyPattern := regexp.MustCompile(`^[a-z][a-z0-9._-]{0,127}$`)
		for _, resourceKey := range mode.RequiredResourceKeys {
			if !resourceKeyPattern.MatchString(resourceKey) {
				return fmt.Errorf("Relay capability resource keys for model %q are invalid", modelID)
			}
		}
	}
	return nil
}

func containsString(values []string, expected string) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}

func containsInt(values []int, expected int) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}

func normalizePlatformCallbackURL(value string, production bool) (string, error) {
	if value == "" || value != strings.TrimSpace(value) || len(value) > 2048 {
		return "", fmt.Errorf("callback URL is invalid")
	}
	parsed, err := url.Parse(value)
	if err != nil || parsed.Hostname() == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", fmt.Errorf("callback URL must not contain credentials, query, or fragment")
	}
	scheme := strings.ToLower(parsed.Scheme)
	if scheme != "http" && scheme != "https" {
		return "", fmt.Errorf("callback URL scheme is invalid")
	}
	hostname := strings.ToLower(strings.TrimSuffix(parsed.Hostname(), "."))
	port := parsed.Port()
	if production {
		if scheme != "https" || (port != "" && port != "443") {
			return "", fmt.Errorf("production callbacks require HTTPS port 443")
		}
		if hostname == "localhost" || strings.HasSuffix(hostname, ".localhost") || strings.HasSuffix(hostname, ".local") || strings.HasSuffix(hostname, ".internal") {
			return "", fmt.Errorf("production callback hostname is not public")
		}
		if address := net.ParseIP(hostname); address != nil && !platformRelayPublicIP(address) {
			return "", fmt.Errorf("production callback address is not public")
		}
	}
	host := hostname
	if strings.Contains(hostname, ":") {
		host = "[" + hostname + "]"
	}
	defaultPort := (scheme == "https" && port == "443") || (scheme == "http" && port == "80")
	if port != "" && !defaultPort {
		host += ":" + port
	}
	parsed.Scheme = scheme
	parsed.Host = host
	if parsed.Path == "" {
		parsed.Path = "/"
	}
	return parsed.String(), nil
}

func platformRelayPublicIP(address net.IP) bool {
	return address.IsGlobalUnicast() && !address.IsPrivate() && !address.IsLoopback() && !address.IsLinkLocalUnicast() && !address.IsLinkLocalMulticast()
}

func platformRelaySecretIsPlaceholder(secret string) bool {
	normalized := strings.NewReplacer("-", "", "_", "", ".", "", " ", "").Replace(strings.ToLower(secret))
	for _, token := range []string{"changeme", "replaceme", "replacewith", "placeholder", "developmentonly", "exampleonly"} {
		if strings.Contains(normalized, token) {
			return true
		}
	}
	return false
}
