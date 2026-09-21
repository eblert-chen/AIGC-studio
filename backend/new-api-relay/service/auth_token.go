package service

import (
	"crypto/hmac"
	"crypto/sha256"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
)

const (
	AccessTokenTTL             = 15 * time.Minute
	SecurityProofTTL           = 5 * time.Minute
	LoginSessionTTL            = 30 * 24 * time.Hour
	RefreshReplayWindow        = 30 * time.Second
	accessTokenUse             = "access"
	securityProofTokenUse      = "security_proof"
	securityProofReplayPurpose = "security_proof"
	authTokenIssuer            = "new-api"
	authTokenAudience          = "new-api-dashboard"
)

var (
	ErrAuthTokenInvalid = errors.New("authentication token is invalid")
	ErrAuthTokenExpired = errors.New("authentication token has expired")
	ErrProofScope       = errors.New("security proof scope mismatch")
	ErrProofMethod      = errors.New("security proof method mismatch")
	ErrProofBinding     = errors.New("security proof request binding mismatch")
	ErrProofConsumed    = errors.New("security proof has already been consumed")
)

const (
	SecurityProofScopeProviderCredentialWrite = "provider.credential.write"
	SecurityProofScopeProviderDisable         = "provider.lifecycle.disable"
	SecurityProofScopeProviderResume          = "provider.lifecycle.resume"
)

var (
	securityProofProviderPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)
	securityProofDigestPattern   = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
)

// SecurityProofBinding turns a step-up proof into authority for exactly one
// provider-onboarding request. The digest binds the byte-identical JSON body
// without placing credential material in the JWT.
type SecurityProofBinding struct {
	Action           string `json:"action"`
	Provider         string `json:"provider"`
	HTTPMethod       string `json:"http_method"`
	HTTPPath         string `json:"http_path"`
	BodySHA256       string `json:"body_sha256"`
	ExpectedRevision string `json:"expected_revision,omitempty"`
}

func (binding SecurityProofBinding) Validate(scope string) error {
	if binding.Action != scope || !isProviderOnboardingProofScope(scope) ||
		!securityProofProviderPattern.MatchString(binding.Provider) ||
		binding.HTTPMethod != "POST" ||
		!securityProofDigestPattern.MatchString(binding.BodySHA256) ||
		len(binding.ExpectedRevision) > 96 {
		return ErrProofBinding
	}
	suffix := ""
	switch binding.Action {
	case SecurityProofScopeProviderCredentialWrite:
		suffix = "credential"
	case SecurityProofScopeProviderDisable:
		suffix = "disable"
	case SecurityProofScopeProviderResume:
		suffix = "resume"
	default:
		return ErrProofBinding
	}
	expectedPath := "/api/provider-onboarding/" + binding.Provider + "/" + suffix
	if !hmac.Equal([]byte(binding.HTTPPath), []byte(expectedPath)) {
		return ErrProofBinding
	}
	return nil
}

func isProviderOnboardingProofScope(scope string) bool {
	switch scope {
	case SecurityProofScopeProviderCredentialWrite,
		SecurityProofScopeProviderDisable,
		SecurityProofScopeProviderResume:
		return true
	default:
		return false
	}
}

// AuthIdentity is the server-validated identity attached to dashboard requests.
// Role, status and group are deliberately loaded from the user cache instead of JWT claims.
type AuthIdentity struct {
	UserID          int
	SessionID       string
	UserAuthVersion int64
	SessionVersion  int64
}

type authClaims struct {
	TokenUse        string                `json:"token_use"`
	SessionID       string                `json:"sid"`
	UserAuthVersion int64                 `json:"uv"`
	SessionVersion  int64                 `json:"sv"`
	Method          string                `json:"method,omitempty"`
	Scopes          []string              `json:"scopes,omitempty"`
	Binding         *SecurityProofBinding `json:"binding,omitempty"`
	jwt.RegisteredClaims
}

func authSigningKey(purpose string) []byte {
	mac := hmac.New(sha256.New, []byte(common.SessionSecret))
	_, _ = mac.Write([]byte("new-api/auth/" + purpose + "/v1"))
	return mac.Sum(nil)
}

func IssueAccessToken(identity AuthIdentity) (string, int64, error) {
	if identity.UserID <= 0 || identity.SessionID == "" || identity.UserAuthVersion <= 0 || identity.SessionVersion <= 0 {
		return "", 0, ErrAuthTokenInvalid
	}
	now := time.Now()
	expiresAt := now.Add(AccessTokenTTL)
	claims := authClaims{
		TokenUse:        accessTokenUse,
		SessionID:       identity.SessionID,
		UserAuthVersion: identity.UserAuthVersion,
		SessionVersion:  identity.SessionVersion,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    authTokenIssuer,
			Subject:   strconv.Itoa(identity.UserID),
			Audience:  jwt.ClaimStrings{authTokenAudience},
			ExpiresAt: jwt.NewNumericDate(expiresAt),
			NotBefore: jwt.NewNumericDate(now.Add(-5 * time.Second)),
			IssuedAt:  jwt.NewNumericDate(now),
			ID:        uuid.NewString(),
		},
	}
	signed, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(authSigningKey(accessTokenUse))
	return signed, expiresAt.Unix(), err
}

func ParseAccessToken(raw string) (AuthIdentity, error) {
	claims, err := parseAuthClaims(raw, accessTokenUse, authSigningKey(accessTokenUse))
	if err != nil {
		return AuthIdentity{}, err
	}
	userID, err := strconv.Atoi(claims.Subject)
	if err != nil || userID <= 0 || claims.SessionID == "" || claims.UserAuthVersion <= 0 || claims.SessionVersion <= 0 {
		return AuthIdentity{}, ErrAuthTokenInvalid
	}
	return AuthIdentity{
		UserID:          userID,
		SessionID:       claims.SessionID,
		UserAuthVersion: claims.UserAuthVersion,
		SessionVersion:  claims.SessionVersion,
	}, nil
}

// ParseDashboardAccessToken distinguishes new-api dashboard JWTs from opaque
// credentials. A token carrying the dashboard issuer, audience and a known
// token use is always treated as internal, even when its signature, lifetime
// or requested purpose is invalid, so it can never fall through to PAT or
// relay-token authentication.
func ParseDashboardAccessToken(raw string) (identity AuthIdentity, internal bool, err error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return AuthIdentity{}, false, nil
	}
	claims := &authClaims{}
	parsed, _, parseErr := jwt.NewParser().ParseUnverified(raw, claims)
	if parseErr != nil || parsed == nil {
		return AuthIdentity{}, false, nil
	}
	audienceMatches := false
	for _, audience := range claims.Audience {
		if audience == authTokenAudience {
			audienceMatches = true
			break
		}
	}
	knownTokenUse := claims.TokenUse == accessTokenUse || claims.TokenUse == securityProofTokenUse
	if claims.Issuer != authTokenIssuer || !audienceMatches || !knownTokenUse {
		return AuthIdentity{}, false, nil
	}
	identity, err = ParseAccessToken(raw)
	return identity, true, err
}

func IssueSecurityProof(identity AuthIdentity, method string, scopes []string) (string, int64, error) {
	return issueSecurityProof(identity, method, scopes, nil)
}

// IssueBoundSecurityProof issues a provider-onboarding proof that cannot be
// replayed for another provider, action, path, method, revision, or body.
func IssueBoundSecurityProof(identity AuthIdentity, method string, scope string, binding SecurityProofBinding) (string, int64, error) {
	if err := binding.Validate(scope); err != nil {
		return "", 0, err
	}
	return issueSecurityProof(identity, method, []string{scope}, &binding)
}

func issueSecurityProof(identity AuthIdentity, method string, scopes []string, binding *SecurityProofBinding) (string, int64, error) {
	method = strings.TrimSpace(method)
	if identity.UserID <= 0 || identity.SessionID == "" || identity.UserAuthVersion <= 0 || identity.SessionVersion <= 0 || method == "" || len(scopes) == 0 {
		return "", 0, ErrAuthTokenInvalid
	}
	now := time.Now()
	expiresAt := now.Add(SecurityProofTTL)
	claims := authClaims{
		TokenUse:        securityProofTokenUse,
		SessionID:       identity.SessionID,
		UserAuthVersion: identity.UserAuthVersion,
		SessionVersion:  identity.SessionVersion,
		Method:          method,
		Scopes:          append([]string(nil), scopes...),
		Binding:         binding,
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    authTokenIssuer,
			Subject:   strconv.Itoa(identity.UserID),
			Audience:  jwt.ClaimStrings{authTokenAudience},
			ExpiresAt: jwt.NewNumericDate(expiresAt),
			NotBefore: jwt.NewNumericDate(now.Add(-5 * time.Second)),
			IssuedAt:  jwt.NewNumericDate(now),
			ID:        uuid.NewString(),
		},
	}
	signed, err := jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(authSigningKey(securityProofTokenUse))
	return signed, expiresAt.Unix(), err
}

func VerifySecurityProof(raw string, identity AuthIdentity, requiredScope string, allowedMethods []string) (string, error) {
	claims, err := verifySecurityProofClaims(raw, identity, requiredScope, allowedMethods)
	if err != nil {
		return "", err
	}
	return claims.Method, nil
}

// VerifyAndConsumeBoundSecurityProof first compares every request binding
// field and only then atomically claims the signed JWT ID in auth_flows. A
// mismatch or expired proof is never consumed; a valid proof is one-shot even
// across concurrent Relay processes.
func VerifyAndConsumeBoundSecurityProof(raw string, identity AuthIdentity, requiredScope string, allowedMethods []string, expected SecurityProofBinding) (string, error) {
	if err := expected.Validate(requiredScope); err != nil {
		return "", err
	}
	claims, err := verifySecurityProofClaims(raw, identity, requiredScope, allowedMethods)
	if err != nil {
		return "", err
	}
	if claims.Binding == nil || !securityProofBindingEqual(*claims.Binding, expected) {
		return "", ErrProofBinding
	}
	if claims.ExpiresAt == nil {
		return "", ErrAuthTokenInvalid
	}
	if err := model.ClaimExternalAuthAssertion(
		securityProofReplayPurpose,
		claims.ID,
		claims.ExpiresAt.Time,
	); err != nil {
		if errors.Is(err, model.ErrAuthFlowConsumed) {
			return "", ErrProofConsumed
		}
		return "", fmt.Errorf("claim security proof: %w", err)
	}
	return claims.Method, nil
}

func verifySecurityProofClaims(raw string, identity AuthIdentity, requiredScope string, allowedMethods []string) (*authClaims, error) {
	claims, err := parseAuthClaims(raw, securityProofTokenUse, authSigningKey(securityProofTokenUse))
	if err != nil {
		return nil, err
	}
	userID, err := strconv.Atoi(claims.Subject)
	if err != nil || userID != identity.UserID || claims.SessionID != identity.SessionID || claims.UserAuthVersion != identity.UserAuthVersion || claims.SessionVersion != identity.SessionVersion {
		return nil, ErrAuthTokenInvalid
	}
	methodAllowed := len(allowedMethods) == 0
	for _, method := range allowedMethods {
		if hmac.Equal([]byte(claims.Method), []byte(method)) {
			methodAllowed = true
			break
		}
	}
	if !methodAllowed {
		return nil, ErrProofMethod
	}
	if requiredScope != "" {
		found := false
		for _, scope := range claims.Scopes {
			if hmac.Equal([]byte(scope), []byte(requiredScope)) {
				found = true
				break
			}
		}
		if !found {
			return nil, ErrProofScope
		}
	}
	return claims, nil
}

func securityProofBindingEqual(left, right SecurityProofBinding) bool {
	return hmac.Equal([]byte(left.Action), []byte(right.Action)) &&
		hmac.Equal([]byte(left.Provider), []byte(right.Provider)) &&
		hmac.Equal([]byte(left.HTTPMethod), []byte(right.HTTPMethod)) &&
		hmac.Equal([]byte(left.HTTPPath), []byte(right.HTTPPath)) &&
		hmac.Equal([]byte(left.BodySHA256), []byte(right.BodySHA256)) &&
		hmac.Equal([]byte(left.ExpectedRevision), []byte(right.ExpectedRevision))
}

func parseAuthClaims(raw, expectedUse string, key []byte) (*authClaims, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, ErrAuthTokenInvalid
	}
	claims := &authClaims{}
	parsed, err := jwt.ParseWithClaims(raw, claims, func(token *jwt.Token) (any, error) {
		if token.Method.Alg() != jwt.SigningMethodHS256.Alg() {
			return nil, fmt.Errorf("%w: unexpected signing method", ErrAuthTokenInvalid)
		}
		return key, nil
	}, jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}), jwt.WithIssuer(authTokenIssuer), jwt.WithAudience(authTokenAudience), jwt.WithExpirationRequired(), jwt.WithIssuedAt(), jwt.WithLeeway(5*time.Second))
	if err != nil {
		if errors.Is(err, jwt.ErrTokenExpired) {
			return nil, ErrAuthTokenExpired
		}
		return nil, fmt.Errorf("%w: %v", ErrAuthTokenInvalid, err)
	}
	if !parsed.Valid || claims.TokenUse != expectedUse || claims.ID == "" || claims.IssuedAt == nil || claims.NotBefore == nil {
		return nil, ErrAuthTokenInvalid
	}
	return claims, nil
}
