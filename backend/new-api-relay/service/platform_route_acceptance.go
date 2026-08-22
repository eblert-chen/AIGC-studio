package service

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/google/uuid"
)

const (
	platformRouteAcceptanceKind               = "relay_route_acceptance"
	platformRouteAcceptanceSchemaVersion      = 1
	platformRouteAcceptanceSignatureDomain    = "ai-video/new-api-relay/route-acceptance/v1\x00"
	platformRouteAcceptanceMaximumLifetime    = 90 * 24 * time.Hour
	platformRouteAcceptanceClockSkewAllowance = 5 * time.Minute
)

var (
	platformRouteAcceptanceKeyIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)
	// This digest is embedded by the release build. The public keys themselves
	// are deployment data, but staging/production accept them only when their
	// canonical set digest matches this immutable build-time trust anchor.
	platformRelayCompiledRouteAcceptanceKeysSHA256 = "unknown"
	platformRouteAcceptanceNow                     = func() time.Time { return time.Now().UTC() }
)

// PlatformRouteAcceptanceManifest is produced and signed by an acceptance
// authority outside the Relay runtime. The Relay contains verification code
// only; no private signing-key setting is supported.
//
// RouteDeclarationSHA256 binds every admission-relevant route field, including
// its normalized capability digest. The duplicated identities below make an
// audit record useful without requiring the original route document.
type PlatformRouteAcceptanceManifest struct {
	SchemaVersion          int      `json:"schema_version"`
	Kind                   string   `json:"kind"`
	AcceptanceID           string   `json:"acceptance_id"`
	KeyID                  string   `json:"key_id"`
	Environment            string   `json:"environment"`
	Model                  string   `json:"model"`
	Modes                  []string `json:"modes"`
	RouteID                string   `json:"route_id"`
	ProviderName           string   `json:"provider_name"`
	AccountID              string   `json:"account_id"`
	ChannelID              int      `json:"channel_id"`
	NativeChannelType      int      `json:"native_channel_type"`
	KeyIndex               int      `json:"key_index"`
	KeyFingerprint         string   `json:"key_fingerprint"`
	ChannelClass           string   `json:"channel_class"`
	UpstreamModel          string   `json:"upstream_model"`
	RPMLimit               int      `json:"rpm_limit"`
	ActiveTaskLimit        int      `json:"active_task_limit"`
	CapabilitySHA256       string   `json:"capability_sha256"`
	RouteDeclarationSHA256 string   `json:"route_declaration_sha256"`
	SourceRevision         string   `json:"source_revision"`
	SourceSnapshotSHA256   string   `json:"source_snapshot_sha256"`
	ImageDigest            string   `json:"image_digest"`
	NotBefore              string   `json:"not_before"`
	NotAfter               string   `json:"not_after"`
}

type PlatformRouteAcceptanceEvidence struct {
	Manifest  PlatformRouteAcceptanceManifest `json:"manifest"`
	Signature string                          `json:"signature"`
}

// PlatformRouteAcceptanceAudit contains only public, secret-free proof
// identities. It is returned by the internal runtime provenance endpoint.
type PlatformRouteAcceptanceAudit struct {
	SchemaVersion       int                                 `json:"schema_version"`
	Kind                string                              `json:"kind"`
	Environment         string                              `json:"environment"`
	TrustKeysSHA256     string                              `json:"trust_keys_sha256"`
	AcceptanceSetSHA256 string                              `json:"acceptance_set_sha256"`
	Routes              []PlatformRouteAcceptanceAuditRoute `json:"routes"`
}

type PlatformRouteAcceptanceAuditRoute struct {
	Model             string   `json:"model"`
	Modes             []string `json:"modes"`
	RouteID           string   `json:"route_id"`
	ChannelID         int      `json:"channel_id"`
	NativeChannelType int      `json:"native_channel_type"`
	AcceptanceID      string   `json:"acceptance_id"`
	KeyID             string   `json:"key_id"`
	EvidenceSHA256    string   `json:"evidence_sha256"`
	NotAfter          string   `json:"not_after"`
}

type platformRouteAcceptanceBoundDeclaration struct {
	SchemaVersion     int      `json:"schema_version"`
	Model             string   `json:"model"`
	Modes             []string `json:"modes"`
	RouteID           string   `json:"route_id"`
	ProviderName      string   `json:"provider_name"`
	AccountID         string   `json:"account_id"`
	ChannelID         int      `json:"channel_id"`
	NativeChannelType int      `json:"native_channel_type"`
	KeyIndex          int      `json:"key_index"`
	KeyFingerprint    string   `json:"key_fingerprint"`
	ChannelClass      string   `json:"channel_class"`
	UpstreamModel     string   `json:"upstream_model"`
	RPMLimit          int      `json:"rpm_limit"`
	ActiveTaskLimit   int      `json:"active_task_limit"`
	CapabilitySHA256  string   `json:"capability_sha256"`
}

type platformRouteAcceptanceVerifier struct {
	environment     string
	publicKeys      map[string]ed25519.PublicKey
	trustKeysSHA256 string
	provenance      PlatformRelayBuildProvenance
	now             time.Time
	seenIDs         map[string]string
}

func rejectPlatformRouteAcceptancePrivateMaterial() error {
	// These names are intentionally unsupported. Rejecting them also catches an
	// operator accidentally mounting release-authority material into Relay.
	for _, variable := range []string{
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY",
	} {
		if strings.TrimSpace(os.Getenv(variable)) != "" {
			return fmt.Errorf("%s must never be present in the Relay runtime", variable)
		}
	}
	return nil
}

func loadPlatformRouteAcceptanceTrustStore(required bool) (map[string]ed25519.PublicKey, string, error) {
	if err := rejectPlatformRouteAcceptancePrivateMaterial(); err != nil {
		return nil, "", err
	}
	raw := strings.TrimSpace(os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON"))
	if raw == "" {
		if required {
			return nil, "", fmt.Errorf("RELAY_COMPAT_ROUTE_ACCEPTANCE_PUBLIC_KEYS_JSON is required for signed route acceptance")
		}
		return map[string]ed25519.PublicKey{}, "", nil
	}
	encoded := make(map[string]string)
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(raw), &encoded); err != nil {
		return nil, "", fmt.Errorf("invalid route acceptance public-key set: %w", err)
	}
	if len(encoded) == 0 {
		return nil, "", fmt.Errorf("route acceptance public-key set must not be empty")
	}
	keys := make(map[string]ed25519.PublicKey, len(encoded))
	for keyID, encodedKey := range encoded {
		if !platformRouteAcceptanceKeyIDPattern.MatchString(keyID) {
			return nil, "", fmt.Errorf("route acceptance public-key id %q is invalid", keyID)
		}
		decoded, err := base64.StdEncoding.DecodeString(encodedKey)
		if err != nil || len(decoded) != ed25519.PublicKeySize || base64.StdEncoding.EncodeToString(decoded) != encodedKey {
			return nil, "", fmt.Errorf("route acceptance public key %q must be canonical base64 Ed25519 public-key bytes", keyID)
		}
		keys[keyID] = ed25519.PublicKey(append([]byte(nil), decoded...))
	}
	canonical, err := platformRelayCanonicalJSON(encoded)
	if err != nil {
		return nil, "", fmt.Errorf("canonicalize route acceptance public-key set: %w", err)
	}
	digest := fmt.Sprintf("sha256:%x", sha256.Sum256(canonical))
	return keys, digest, nil
}

func newPlatformRouteAcceptanceVerifier(environment string, required bool) (*platformRouteAcceptanceVerifier, error) {
	if err := rejectPlatformRouteAcceptancePrivateMaterial(); err != nil {
		return nil, err
	}
	if environment != "staging" && environment != "production" {
		return nil, nil
	}
	keys, digest, err := loadPlatformRouteAcceptanceTrustStore(required)
	if err != nil {
		return nil, err
	}
	if !required {
		return &platformRouteAcceptanceVerifier{
			environment: environment, publicKeys: keys, trustKeysSHA256: digest,
			provenance: GetPlatformRelayBuildProvenance(), now: platformRouteAcceptanceNow(), seenIDs: make(map[string]string),
		}, nil
	}
	compiledDigest := strings.ToLower(strings.TrimSpace(platformRelayCompiledRouteAcceptanceKeysSHA256))
	if !platformRelaySnapshotDigestPattern.MatchString(compiledDigest) || compiledDigest == "sha256:"+strings.Repeat("0", 64) {
		return nil, fmt.Errorf("staging and production Relay require a build-time route acceptance trust-key digest")
	}
	if digest != compiledDigest {
		return nil, fmt.Errorf("route acceptance public-key set does not match the build-time trust anchor")
	}
	provenance := GetPlatformRelayBuildProvenance()
	if !platformRelaySourceRevisionPattern.MatchString(provenance.SourceGitRevision) ||
		!platformRelaySnapshotDigestPattern.MatchString(provenance.SourceSnapshotSHA256) ||
		!platformRelayImageDigestPattern.MatchString(provenance.ImageDigest) ||
		provenance.ImageDigest == "sha256:"+strings.Repeat("0", 64) {
		return nil, fmt.Errorf("signed route acceptance requires valid source and image provenance")
	}
	return &platformRouteAcceptanceVerifier{
		environment:     environment,
		publicKeys:      keys,
		trustKeysSHA256: digest,
		provenance:      provenance,
		now:             platformRouteAcceptanceNow(),
		seenIDs:         make(map[string]string),
	}, nil
}

func platformRouteAcceptanceBinding(modelID string, declaration PlatformRelayRouteDeclaration) (platformRouteAcceptanceBoundDeclaration, string, string, error) {
	capabilityCanonical, err := platformRelayCanonicalJSON(declaration.Capabilities)
	if err != nil {
		return platformRouteAcceptanceBoundDeclaration{}, "", "", err
	}
	capabilityDigest := fmt.Sprintf("sha256:%x", sha256.Sum256(capabilityCanonical))
	modes := make([]string, 0, len(declaration.Capabilities.Modes))
	for mode := range declaration.Capabilities.Modes {
		modes = append(modes, mode)
	}
	sort.Strings(modes)
	bound := platformRouteAcceptanceBoundDeclaration{
		SchemaVersion:     platformRouteAcceptanceSchemaVersion,
		Model:             modelID,
		Modes:             modes,
		RouteID:           declaration.RouteID,
		ProviderName:      declaration.ProviderName,
		AccountID:         declaration.AccountID,
		ChannelID:         declaration.ChannelID,
		NativeChannelType: declaration.NativeChannelType,
		KeyIndex:          declaration.KeyIndex,
		KeyFingerprint:    declaration.KeyFingerprint,
		ChannelClass:      declaration.ChannelClass,
		UpstreamModel:     declaration.UpstreamModel,
		RPMLimit:          declaration.RPMLimit,
		ActiveTaskLimit:   declaration.ActiveTaskLimit,
		CapabilitySHA256:  capabilityDigest,
	}
	canonical, err := platformRelayCanonicalJSON(bound)
	if err != nil {
		return platformRouteAcceptanceBoundDeclaration{}, "", "", err
	}
	return bound, capabilityDigest, fmt.Sprintf("sha256:%x", sha256.Sum256(canonical)), nil
}

// BuildPlatformRouteAcceptanceManifest is the secret-free half of the offline
// signing flow. It deliberately accepts no private key. The standalone signer
// validates its private-key file boundary, calls this function, and signs the
// returned canonical payload outside every Relay runtime image.
func BuildPlatformRouteAcceptanceManifest(
	modelID string,
	declaration *PlatformRelayRouteDeclaration,
	acceptanceID string,
	keyID string,
	environment string,
	provenance PlatformRelayBuildProvenance,
	notBefore time.Time,
	notAfter time.Time,
) (PlatformRouteAcceptanceManifest, error) {
	if declaration == nil {
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("route declaration is required")
	}
	if environment != "staging" && environment != "production" {
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("acceptance environment must be staging or production")
	}
	parsedAcceptanceID, err := uuid.Parse(acceptanceID)
	if err != nil || parsedAcceptanceID.String() != acceptanceID {
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("acceptance_id is invalid")
	}
	if !platformRouteAcceptanceKeyIDPattern.MatchString(keyID) {
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("acceptance key_id is invalid")
	}
	if declaration.StagingReady || declaration.ProductionReady {
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("readiness booleans cannot be signed as acceptance evidence")
	}
	if strings.TrimSpace(modelID) == "" || len(modelID) > 128 ||
		strings.TrimSpace(declaration.RouteID) == "" || len(declaration.RouteID) > 120 ||
		strings.TrimSpace(declaration.ProviderName) == "" || len(declaration.ProviderName) > 64 ||
		strings.TrimSpace(declaration.AccountID) == "" || len(declaration.AccountID) > 128 ||
		declaration.ChannelID <= 0 || declaration.KeyIndex < 0 || declaration.RPMLimit <= 0 || declaration.ActiveTaskLimit <= 0 ||
		declaration.NativeChannelType <= constant.ChannelTypeUnknown || declaration.NativeChannelType >= constant.ChannelTypeDummy ||
		strings.TrimSpace(declaration.UpstreamModel) == "" {
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("route declaration identity or admission limits are invalid")
	}
	if platformRelayMockIdentity(declaration.RouteID) || platformRelayMockIdentity(declaration.ProviderName) ||
		platformRelayMockIdentity(declaration.AccountID) || platformRelayMockIdentity(declaration.UpstreamModel) {
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("mock route identities cannot receive secure acceptance evidence")
	}
	fingerprint, err := hex.DecodeString(declaration.KeyFingerprint)
	if err != nil || len(fingerprint) != 32 || strings.ToLower(declaration.KeyFingerprint) != declaration.KeyFingerprint {
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("route key fingerprint must be a lowercase SHA-256 digest")
	}
	switch declaration.ChannelClass {
	case PlatformChannelClassReverseEngineered, PlatformChannelClassThirdPartyAPI, PlatformChannelClassOfficial:
	default:
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("route channel class is invalid")
	}
	if err := validatePlatformCapability(modelID, declaration.Capabilities); err != nil {
		return PlatformRouteAcceptanceManifest{}, err
	}
	if err := validatePlatformNativeTaskBridgeCapability(modelID, declaration.Capabilities); err != nil {
		return PlatformRouteAcceptanceManifest{}, err
	}
	declaration.Capabilities = normalizePlatformCapability(declaration.Capabilities)
	bound, capabilityDigest, routeDigest, err := platformRouteAcceptanceBinding(modelID, *declaration)
	if err != nil {
		return PlatformRouteAcceptanceManifest{}, err
	}
	if !platformRelaySourceRevisionPattern.MatchString(provenance.SourceGitRevision) ||
		!platformRelaySnapshotDigestPattern.MatchString(provenance.SourceSnapshotSHA256) ||
		!platformRelayImageDigestPattern.MatchString(provenance.ImageDigest) ||
		provenance.ImageDigest == "sha256:"+strings.Repeat("0", 64) {
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("source and image provenance are invalid")
	}
	notBefore = notBefore.UTC().Truncate(time.Second)
	notAfter = notAfter.UTC().Truncate(time.Second)
	if !notAfter.After(notBefore) || notAfter.Sub(notBefore) > platformRouteAcceptanceMaximumLifetime {
		return PlatformRouteAcceptanceManifest{}, fmt.Errorf("acceptance validity window is invalid")
	}
	return PlatformRouteAcceptanceManifest{
		SchemaVersion:          platformRouteAcceptanceSchemaVersion,
		Kind:                   platformRouteAcceptanceKind,
		AcceptanceID:           acceptanceID,
		KeyID:                  keyID,
		Environment:            environment,
		Model:                  bound.Model,
		Modes:                  append([]string(nil), bound.Modes...),
		RouteID:                bound.RouteID,
		ProviderName:           bound.ProviderName,
		AccountID:              bound.AccountID,
		ChannelID:              bound.ChannelID,
		NativeChannelType:      bound.NativeChannelType,
		KeyIndex:               bound.KeyIndex,
		KeyFingerprint:         bound.KeyFingerprint,
		ChannelClass:           bound.ChannelClass,
		UpstreamModel:          bound.UpstreamModel,
		RPMLimit:               bound.RPMLimit,
		ActiveTaskLimit:        bound.ActiveTaskLimit,
		CapabilitySHA256:       capabilityDigest,
		RouteDeclarationSHA256: routeDigest,
		SourceRevision:         strings.ToLower(strings.TrimSpace(provenance.SourceGitRevision)),
		SourceSnapshotSHA256:   strings.ToLower(strings.TrimSpace(provenance.SourceSnapshotSHA256)),
		ImageDigest:            strings.ToLower(strings.TrimSpace(provenance.ImageDigest)),
		NotBefore:              notBefore.Format(time.RFC3339),
		NotAfter:               notAfter.Format(time.RFC3339),
	}, nil
}

func platformRouteAcceptanceSignaturePayload(manifest PlatformRouteAcceptanceManifest) ([]byte, error) {
	canonical, err := platformRelayCanonicalJSON(manifest)
	if err != nil {
		return nil, err
	}
	return append([]byte(platformRouteAcceptanceSignatureDomain), canonical...), nil
}

func PlatformRouteAcceptanceSignaturePayload(manifest PlatformRouteAcceptanceManifest) ([]byte, error) {
	return platformRouteAcceptanceSignaturePayload(manifest)
}

func parsePlatformRouteAcceptanceTimestamp(name string, value string) (time.Time, error) {
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil || parsed.Location() != time.UTC || parsed.Format(time.RFC3339) != value {
		return time.Time{}, fmt.Errorf("route acceptance %s must be canonical UTC RFC3339", name)
	}
	return parsed, nil
}

func (verifier *platformRouteAcceptanceVerifier) verify(
	modelID string,
	declaration *PlatformRelayRouteDeclaration,
) error {
	if verifier == nil {
		return nil
	}
	if declaration.Acceptance == nil {
		return fmt.Errorf("Relay route %q requires signed acceptance evidence", declaration.RouteID)
	}
	if declaration.StagingReady || declaration.ProductionReady {
		return fmt.Errorf("Relay route %q readiness must be derived from signed acceptance evidence, not staging_ready or production_ready", declaration.RouteID)
	}
	if declaration.NativeChannelType <= constant.ChannelTypeUnknown || declaration.NativeChannelType >= constant.ChannelTypeDummy {
		return fmt.Errorf("Relay route %q requires an exact native_channel_type", declaration.RouteID)
	}
	manifest := declaration.Acceptance.Manifest
	if manifest.SchemaVersion != platformRouteAcceptanceSchemaVersion || manifest.Kind != platformRouteAcceptanceKind {
		return fmt.Errorf("Relay route %q acceptance manifest kind or schema is invalid", declaration.RouteID)
	}
	parsedAcceptanceID, err := uuid.Parse(manifest.AcceptanceID)
	if err != nil || parsedAcceptanceID.String() != manifest.AcceptanceID {
		return fmt.Errorf("Relay route %q acceptance_id is invalid", declaration.RouteID)
	}
	if !platformRouteAcceptanceKeyIDPattern.MatchString(manifest.KeyID) {
		return fmt.Errorf("Relay route %q acceptance key_id is invalid", declaration.RouteID)
	}
	bound, capabilityDigest, routeDigest, err := platformRouteAcceptanceBinding(modelID, *declaration)
	if err != nil {
		return fmt.Errorf("Relay route %q acceptance binding: %w", declaration.RouteID, err)
	}
	if manifest.Environment != verifier.environment || manifest.Model != bound.Model ||
		!equalPlatformRouteAcceptanceStrings(manifest.Modes, bound.Modes) || manifest.RouteID != bound.RouteID ||
		manifest.ProviderName != bound.ProviderName || manifest.AccountID != bound.AccountID ||
		manifest.ChannelID != bound.ChannelID || manifest.NativeChannelType != bound.NativeChannelType ||
		manifest.KeyIndex != bound.KeyIndex || manifest.KeyFingerprint != bound.KeyFingerprint ||
		manifest.ChannelClass != bound.ChannelClass || manifest.UpstreamModel != bound.UpstreamModel ||
		manifest.RPMLimit != bound.RPMLimit || manifest.ActiveTaskLimit != bound.ActiveTaskLimit ||
		manifest.CapabilitySHA256 != capabilityDigest || manifest.RouteDeclarationSHA256 != routeDigest {
		return fmt.Errorf("Relay route %q acceptance manifest does not match the exact route and capability declaration", declaration.RouteID)
	}
	if manifest.SourceRevision != verifier.provenance.SourceGitRevision ||
		manifest.SourceSnapshotSHA256 != verifier.provenance.SourceSnapshotSHA256 ||
		manifest.ImageDigest != verifier.provenance.ImageDigest {
		return fmt.Errorf("Relay route %q acceptance manifest does not match the running source and image", declaration.RouteID)
	}
	notBefore, err := parsePlatformRouteAcceptanceTimestamp("not_before", manifest.NotBefore)
	if err != nil {
		return fmt.Errorf("Relay route %q: %w", declaration.RouteID, err)
	}
	notAfter, err := parsePlatformRouteAcceptanceTimestamp("not_after", manifest.NotAfter)
	if err != nil {
		return fmt.Errorf("Relay route %q: %w", declaration.RouteID, err)
	}
	if !notAfter.After(notBefore) || notAfter.Sub(notBefore) > platformRouteAcceptanceMaximumLifetime {
		return fmt.Errorf("Relay route %q acceptance validity window is invalid", declaration.RouteID)
	}
	if verifier.now.Add(platformRouteAcceptanceClockSkewAllowance).Before(notBefore) {
		return fmt.Errorf("Relay route %q acceptance evidence is not active yet", declaration.RouteID)
	}
	if !verifier.now.Before(notAfter) {
		return fmt.Errorf("Relay route %q acceptance evidence is expired and cannot be replayed", declaration.RouteID)
	}
	publicKey, ok := verifier.publicKeys[manifest.KeyID]
	if !ok {
		return fmt.Errorf("Relay route %q acceptance key is not trusted", declaration.RouteID)
	}
	signature, err := base64.StdEncoding.DecodeString(declaration.Acceptance.Signature)
	if err != nil || len(signature) != ed25519.SignatureSize || base64.StdEncoding.EncodeToString(signature) != declaration.Acceptance.Signature {
		return fmt.Errorf("Relay route %q acceptance signature is invalid", declaration.RouteID)
	}
	payload, err := platformRouteAcceptanceSignaturePayload(manifest)
	if err != nil {
		return fmt.Errorf("Relay route %q acceptance payload: %w", declaration.RouteID, err)
	}
	if !ed25519.Verify(publicKey, payload, signature) {
		return fmt.Errorf("Relay route %q acceptance signature verification failed", declaration.RouteID)
	}
	evidenceCanonical, err := platformRelayCanonicalJSON(declaration.Acceptance)
	if err != nil {
		return fmt.Errorf("Relay route %q acceptance digest: %w", declaration.RouteID, err)
	}
	evidenceDigest := fmt.Sprintf("sha256:%x", sha256.Sum256(evidenceCanonical))
	if previous, duplicate := verifier.seenIDs[manifest.AcceptanceID]; duplicate && previous != evidenceDigest {
		return fmt.Errorf("Relay route %q reuses acceptance_id %q for different evidence", declaration.RouteID, manifest.AcceptanceID)
	}
	if _, duplicate := verifier.seenIDs[manifest.AcceptanceID]; duplicate {
		return fmt.Errorf("Relay route %q replays acceptance_id %q", declaration.RouteID, manifest.AcceptanceID)
	}
	verifier.seenIDs[manifest.AcceptanceID] = evidenceDigest
	declaration.AcceptanceDigest = evidenceDigest
	declaration.AcceptanceNotAfter = notAfter
	declaration.StagingReady = verifier.environment == "staging"
	declaration.ProductionReady = verifier.environment == "production"
	return nil
}

func equalPlatformRouteAcceptanceStrings(left []string, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}
	return true
}

func platformRouteAcceptanceMatchesNativeChannel(declaration PlatformRelayRouteDeclaration, actualType int) error {
	if declaration.NativeChannelType > constant.ChannelTypeUnknown && declaration.NativeChannelType != actualType {
		return fmt.Errorf(
			"Relay route %q native channel type %d does not match accepted adapter type %d",
			declaration.RouteID,
			actualType,
			declaration.NativeChannelType,
		)
	}
	return nil
}

func buildPlatformRouteAcceptanceAudit(
	environment string,
	routes map[string][]PlatformRelayRouteDeclaration,
) (PlatformRouteAcceptanceAudit, time.Time, error) {
	if environment == "" {
		environment = "development"
	}
	modelIDs := make([]string, 0, len(routes))
	for modelID := range routes {
		modelIDs = append(modelIDs, modelID)
	}
	sort.Strings(modelIDs)
	auditRoutes := make([]PlatformRouteAcceptanceAuditRoute, 0)
	var earliestExpiry time.Time
	for _, modelID := range modelIDs {
		for _, route := range routes[modelID] {
			if route.Acceptance == nil || route.AcceptanceDigest == "" {
				continue
			}
			manifest := route.Acceptance.Manifest
			auditRoutes = append(auditRoutes, PlatformRouteAcceptanceAuditRoute{
				Model:             modelID,
				Modes:             append([]string(nil), manifest.Modes...),
				RouteID:           route.RouteID,
				ChannelID:         route.ChannelID,
				NativeChannelType: route.NativeChannelType,
				AcceptanceID:      manifest.AcceptanceID,
				KeyID:             manifest.KeyID,
				EvidenceSHA256:    route.AcceptanceDigest,
				NotAfter:          manifest.NotAfter,
			})
			if earliestExpiry.IsZero() || route.AcceptanceNotAfter.Before(earliestExpiry) {
				earliestExpiry = route.AcceptanceNotAfter
			}
		}
	}
	trustDigest := strings.ToLower(strings.TrimSpace(platformRelayCompiledRouteAcceptanceKeysSHA256))
	if !platformRelaySnapshotDigestPattern.MatchString(trustDigest) {
		trustDigest = ""
	}
	canonical, err := platformRelayCanonicalJSON(map[string]any{
		"environment":       environment,
		"trust_keys_sha256": trustDigest,
		"routes":            auditRoutes,
	})
	if err != nil {
		return PlatformRouteAcceptanceAudit{}, time.Time{}, err
	}
	return PlatformRouteAcceptanceAudit{
		SchemaVersion:       platformRouteAcceptanceSchemaVersion,
		Kind:                "relay_route_acceptance_audit",
		Environment:         environment,
		TrustKeysSHA256:     trustDigest,
		AcceptanceSetSHA256: fmt.Sprintf("sha256:%x", sha256.Sum256(canonical)),
		Routes:              auditRoutes,
	}, earliestExpiry, nil
}
