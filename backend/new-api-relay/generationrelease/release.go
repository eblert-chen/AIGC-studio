// Package generationrelease owns immutable, auditable bindings between a
// stable public model, a provider model ID and a reusable generation adapter
// profile. Route/account/key state is deliberately absent from this package:
// rotating credentials or adding an equivalent route must not create a new
// model capability revision.
package generationrelease

import (
	"bytes"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
)

const (
	APIVersion                 = "relay.aivideo/v1"
	Kind                       = "generation_model_release"
	Algorithm                  = "Ed25519"
	MaximumAttestationLifetime = 365 * 24 * time.Hour
)

var (
	releaseIDPattern = regexp.MustCompile(`^[a-z][a-z0-9._-]{2,119}$`)
	modelIDPattern   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$`)
	keyIDPattern     = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$`)
)

// Audit records why and by whom a model binding was promoted. It is included
// in the signed payload, so changing the reason or source creates a different
// immutable binding revision.
type Audit struct {
	CreatedAt string `json:"created_at"`
	CreatedBy string `json:"created_by"`
	Reason    string `json:"reason"`
	SourceRef string `json:"source_ref"`
}

// Attestation contains no private material. Signing is expected to happen in
// an offline/release command; the Relay runtime only receives public keys.
type Attestation struct {
	Algorithm string `json:"algorithm"`
	KeyID     string `json:"key_id"`
	SignedAt  string `json:"signed_at"`
	NotAfter  string `json:"not_after"`
	Signature string `json:"signature"`
}

// Release is a versioned model identity/capability binding. AdapterProfileID
// names an immutable code-reviewed protocol profile. Capability may only be a
// subset of that profile. LegacyPublicAliases are product aliases, never
// provider-side IDs.
type Release struct {
	APIVersion             string                             `json:"api_version"`
	Kind                   string                             `json:"kind"`
	ReleaseID              string                             `json:"release_id"`
	PublicModelID          string                             `json:"public_model_id"`
	ProviderModelID        string                             `json:"provider_model_id"`
	AdapterProfileID       string                             `json:"adapter_profile_id"`
	AdapterProfileRevision string                             `json:"adapter_profile_revision"`
	LegacyPublicAliases    []string                           `json:"legacy_public_aliases,omitempty"`
	Capability             dto.PlatformGenerationCapabilities `json:"capability"`
	Audit                  Audit                              `json:"audit"`
	Attestation            *Attestation                       `json:"attestation,omitempty"`
}

type signingRelease struct {
	APIVersion             string                             `json:"api_version"`
	Kind                   string                             `json:"kind"`
	ReleaseID              string                             `json:"release_id"`
	PublicModelID          string                             `json:"public_model_id"`
	ProviderModelID        string                             `json:"provider_model_id"`
	AdapterProfileID       string                             `json:"adapter_profile_id"`
	AdapterProfileRevision string                             `json:"adapter_profile_revision"`
	LegacyPublicAliases    []string                           `json:"legacy_public_aliases,omitempty"`
	Capability             dto.PlatformGenerationCapabilities `json:"capability"`
	Audit                  Audit                              `json:"audit"`
	Algorithm              string                             `json:"algorithm,omitempty"`
	KeyID                  string                             `json:"key_id,omitempty"`
	SignedAt               string                             `json:"signed_at,omitempty"`
	NotAfter               string                             `json:"not_after,omitempty"`
}

// DecodeStrict rejects unknown fields, trailing JSON and malformed release
// documents. Validation against the registered profile happens separately so
// callers can distinguish syntax errors from an unavailable runtime profile.
func DecodeStrict(raw []byte) (Release, error) {
	if err := common.RejectDuplicateJSONKeys(raw); err != nil {
		return Release{}, fmt.Errorf("invalid generation model release: %w", err)
	}
	var release Release
	if err := common.DecodeJsonDisallowUnknownFields(bytes.NewReader(raw), &release); err != nil {
		return Release{}, fmt.Errorf("invalid generation model release: %w", err)
	}
	return release, nil
}

// Validate proves identity syntax, immutable profile revision and capability
// narrowing. It performs no signature check; production callers must also
// call VerifyAttestation.
func (release Release) Validate(profile generationprofile.Profile) error {
	if release.APIVersion != APIVersion || release.Kind != Kind {
		return fmt.Errorf("generation model release api_version or kind is unsupported")
	}
	if !releaseIDPattern.MatchString(release.ReleaseID) {
		return fmt.Errorf("generation model release_id is invalid")
	}
	if !validModelID(release.PublicModelID) || !validModelID(release.ProviderModelID) {
		return fmt.Errorf("generation model identities are invalid")
	}
	if release.AdapterProfileID != profile.ID || release.AdapterProfileRevision != profile.Revision {
		return fmt.Errorf("generation model release profile snapshot is unavailable")
	}
	if err := profile.ValidateNarrowing(release.Capability); err != nil {
		return fmt.Errorf("generation model release expands adapter profile: %w", err)
	}
	if err := generationprofile.ValidateSeedanceModelBinding(profile, release.ProviderModelID, release.Capability); err != nil {
		return fmt.Errorf("generation model release violates provider model contract: %w", err)
	}
	if err := generationprofile.ValidateMiniMaxH3ModelBinding(profile, release.ProviderModelID, release.Capability); err != nil {
		return fmt.Errorf("generation model release violates provider model contract: %w", err)
	}
	if err := generationprofile.ValidateGoogleVideoModelBinding(profile, release.ProviderModelID, release.Capability); err != nil {
		return fmt.Errorf("generation model release violates provider model contract: %w", err)
	}
	if err := validateAliases(release.PublicModelID, release.LegacyPublicAliases); err != nil {
		return err
	}
	if err := validateAudit(release.Audit); err != nil {
		return err
	}
	if release.Attestation != nil {
		if err := validateAttestationShape(*release.Attestation); err != nil {
			return err
		}
	}
	return nil
}

func validModelID(value string) bool {
	return value == strings.TrimSpace(value) && utf8.ValidString(value) && modelIDPattern.MatchString(value)
}

func validateAliases(publicModelID string, aliases []string) error {
	seen := map[string]struct{}{publicModelID: {}}
	for _, alias := range aliases {
		if !validModelID(alias) {
			return fmt.Errorf("generation model legacy public alias is invalid")
		}
		if _, duplicate := seen[alias]; duplicate {
			return fmt.Errorf("generation model aliases contain duplicates")
		}
		seen[alias] = struct{}{}
	}
	return nil
}

func validateAudit(audit Audit) error {
	createdAt, err := time.Parse(time.RFC3339, audit.CreatedAt)
	if err != nil || audit.CreatedAt != createdAt.UTC().Format(time.RFC3339) {
		return fmt.Errorf("generation model release audit.created_at must be canonical UTC RFC3339")
	}
	if !validAuditText(audit.CreatedBy, 128) || !validAuditText(audit.Reason, 512) || !validAuditText(audit.SourceRef, 512) {
		return fmt.Errorf("generation model release audit fields are invalid")
	}
	return nil
}

func validAuditText(value string, maximum int) bool {
	return value == strings.TrimSpace(value) && value != "" && utf8.ValidString(value) && utf8.RuneCountInString(value) <= maximum
}

func validateAttestationShape(attestation Attestation) error {
	if attestation.Algorithm != Algorithm || !keyIDPattern.MatchString(attestation.KeyID) {
		return fmt.Errorf("generation model release attestation identity is invalid")
	}
	signedAt, signedErr := time.Parse(time.RFC3339, attestation.SignedAt)
	notAfter, expiryErr := time.Parse(time.RFC3339, attestation.NotAfter)
	if signedErr != nil || expiryErr != nil || attestation.SignedAt != signedAt.UTC().Format(time.RFC3339) ||
		attestation.NotAfter != notAfter.UTC().Format(time.RFC3339) || !notAfter.After(signedAt) ||
		notAfter.Sub(signedAt) > MaximumAttestationLifetime {
		return fmt.Errorf("generation model release attestation window is invalid")
	}
	signature, err := base64.StdEncoding.DecodeString(attestation.Signature)
	if err != nil || len(signature) != ed25519.SignatureSize || base64.StdEncoding.EncodeToString(signature) != attestation.Signature {
		return fmt.Errorf("generation model release attestation signature is invalid")
	}
	return nil
}

// SigningPayload returns deterministic JSON. It binds both the model release
// and the attestation window/key identity, but never includes the signature.
func (release Release) SigningPayload(attestation Attestation) ([]byte, error) {
	copy := release
	copy.Attestation = nil
	copy.LegacyPublicAliases = sortedUniqueStrings(copy.LegacyPublicAliases)
	copy.Capability = generationprofile.NormalizeCapability(copy.Capability)
	payload := signingRelease{
		APIVersion:             copy.APIVersion,
		Kind:                   copy.Kind,
		ReleaseID:              copy.ReleaseID,
		PublicModelID:          copy.PublicModelID,
		ProviderModelID:        copy.ProviderModelID,
		AdapterProfileID:       copy.AdapterProfileID,
		AdapterProfileRevision: copy.AdapterProfileRevision,
		LegacyPublicAliases:    copy.LegacyPublicAliases,
		Capability:             copy.Capability,
		Audit:                  copy.Audit,
		Algorithm:              attestation.Algorithm,
		KeyID:                  attestation.KeyID,
		SignedAt:               attestation.SignedAt,
		NotAfter:               attestation.NotAfter,
	}
	return common.Marshal(payload)
}

// VerifyAttestation fails closed on missing, unknown, expired, not-yet-valid,
// or invalid signatures. Public keys are raw Ed25519 public keys keyed by ID.
func (release Release) VerifyAttestation(publicKeys map[string]ed25519.PublicKey, now time.Time) error {
	if release.Attestation == nil {
		return fmt.Errorf("generation model release requires a signed attestation")
	}
	attestation := *release.Attestation
	if err := validateAttestationShape(attestation); err != nil {
		return err
	}
	publicKey, ok := publicKeys[attestation.KeyID]
	if !ok || len(publicKey) != ed25519.PublicKeySize {
		return fmt.Errorf("generation model release attestation key is unavailable")
	}
	signedAt, _ := time.Parse(time.RFC3339, attestation.SignedAt)
	notAfter, _ := time.Parse(time.RFC3339, attestation.NotAfter)
	now = now.UTC()
	if now.Before(signedAt) || !now.Before(notAfter) {
		return fmt.Errorf("generation model release attestation is outside its validity window")
	}
	payload, err := release.SigningPayload(attestation)
	if err != nil {
		return err
	}
	signature, _ := base64.StdEncoding.DecodeString(attestation.Signature)
	if !ed25519.Verify(publicKey, payload, signature) {
		return fmt.Errorf("generation model release attestation signature verification failed")
	}
	return nil
}

// CapabilityRevision changes only when the public capability changes. It does
// not include provider model, profile, release audit, channel, route, account,
// key fingerprint, priority, weight, limits or acceptance evidence.
func (release Release) CapabilityRevision() (string, error) {
	normalized := generationprofile.NormalizeCapability(release.Capability)
	serialized, err := common.Marshal(normalized)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(serialized)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

// BindingRevision identifies the immutable identity/profile/capability/audit
// release. It intentionally excludes Attestation bytes and all route state.
func (release Release) BindingRevision() (string, error) {
	copy := release
	copy.Attestation = nil
	payload, err := copy.SigningPayload(Attestation{})
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(payload)
	return "sha256:" + hex.EncodeToString(digest[:]), nil
}

func (release Release) MatchesPublicModel(modelID string) bool {
	if modelID == release.PublicModelID {
		return true
	}
	for _, alias := range release.LegacyPublicAliases {
		if modelID == alias {
			return true
		}
	}
	return false
}

// ValidateRouteNarrowing proves a route remains within both the signed model
// release and its underlying implementation profile.
func (release Release) ValidateRouteNarrowing(profile generationprofile.Profile, candidate dto.PlatformGenerationCapabilities) error {
	if err := release.Validate(profile); err != nil {
		return err
	}
	releaseCeiling := profile
	releaseCeiling.ID = release.ReleaseID
	releaseCeiling.Capability = release.Capability
	if err := releaseCeiling.ValidateNarrowing(candidate); err != nil {
		return fmt.Errorf("route capability expands generation model release: %w", err)
	}
	return nil
}

func sortedUniqueStrings(values []string) []string {
	set := make(map[string]struct{}, len(values))
	for _, value := range values {
		set[value] = struct{}{}
	}
	result := make([]string, 0, len(set))
	for value := range set {
		result = append(result, value)
	}
	sort.Strings(result)
	return result
}
