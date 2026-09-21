package middleware

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
)

const (
	SecurityProofScopeProviderCredentialWrite = service.SecurityProofScopeProviderCredentialWrite
	SecurityProofScopeProviderDisable         = service.SecurityProofScopeProviderDisable
	SecurityProofScopeProviderResume          = service.SecurityProofScopeProviderResume
	providerOnboardingProofBodyLimit          = 70 * 1024
)

// SecureVerificationRequired protects channel key disclosure. Other sensitive
// operations validate their narrower proof scopes in their controller.
func SecureVerificationRequired() gin.HandlerFunc {
	return func(c *gin.Context) {
		if !RequireSecurityProof(c, "channel.key.read", []string{"2fa", "passkey"}) {
			return
		}
		c.Set("secure_verified", true)
		c.Next()
	}
}

// ProviderCredentialWriteVerificationRequired requires a session-bound step-up
// proof with a scope that cannot be replayed against the raw-key disclosure
// endpoint. PAT identities intentionally cannot satisfy this middleware.
func ProviderCredentialWriteVerificationRequired() gin.HandlerFunc {
	return providerOnboardingVerificationRequired(SecurityProofScopeProviderCredentialWrite)
}

func ProviderDisableVerificationRequired() gin.HandlerFunc {
	return providerOnboardingVerificationRequired(SecurityProofScopeProviderDisable)
}

func ProviderResumeVerificationRequired() gin.HandlerFunc {
	return providerOnboardingVerificationRequired(SecurityProofScopeProviderResume)
}

type providerOnboardingCredentialProofBody struct {
	APIKey           string `json:"api_key"`
	Reason           string `json:"reason"`
	ExpectedRevision string `json:"expected_revision,omitempty"`
}

type providerOnboardingLifecycleProofBody struct {
	Reason           string `json:"reason"`
	ExpectedRevision string `json:"expected_revision"`
}

func providerOnboardingVerificationRequired(scope string) gin.HandlerFunc {
	return func(c *gin.Context) {
		binding, body, ok := providerOnboardingRequestBinding(c, scope)
		if !ok {
			return
		}
		defer clear(body)
		identity, authenticated := GetSessionAuthIdentity(c)
		if !authenticated {
			securityProofError(c, "SECURITY_PROOF_INVALID", "安全验证状态无效")
			return
		}
		rawProof := strings.TrimSpace(c.GetHeader("X-Security-Proof"))
		if rawProof == "" {
			securityProofError(c, "SECURITY_PROOF_REQUIRED", "需要安全验证")
			return
		}
		if _, err := service.VerifyAndConsumeBoundSecurityProof(
			rawProof,
			identity,
			scope,
			[]string{"2fa", "passkey"},
			binding,
		); err != nil {
			writeSecurityProofVerificationError(c, err)
			return
		}
		c.Set("provider_onboarding_proof_consumed", true)
		c.Set("provider_onboarding_proof_scope", scope)
		c.Next()
	}
}

func providerOnboardingRequestBinding(c *gin.Context, scope string) (service.SecurityProofBinding, []byte, bool) {
	if c.Request == nil || c.Request.Body == nil {
		securityProofError(c, "SECURITY_PROOF_REQUEST_MISMATCH", "安全验证与当前请求不匹配")
		return service.SecurityProofBinding{}, nil, false
	}
	raw, err := io.ReadAll(io.LimitReader(c.Request.Body, providerOnboardingProofBodyLimit+1))
	_ = c.Request.Body.Close()
	if err != nil || len(raw) == 0 || len(raw) > providerOnboardingProofBodyLimit || common.RejectDuplicateJSONKeys(raw) != nil {
		clear(raw)
		securityProofError(c, "SECURITY_PROOF_REQUEST_MISMATCH", "安全验证与当前请求不匹配")
		return service.SecurityProofBinding{}, nil, false
	}
	c.Request.Body = io.NopCloser(bytes.NewReader(raw))

	expectedRevision := ""
	switch scope {
	case SecurityProofScopeProviderCredentialWrite:
		var body providerOnboardingCredentialProofBody
		if common.DecodeJsonDisallowUnknownFields(bytes.NewReader(raw), &body) != nil {
			clear(raw)
			securityProofError(c, "SECURITY_PROOF_REQUEST_MISMATCH", "安全验证与当前请求不匹配")
			return service.SecurityProofBinding{}, nil, false
		}
		expectedRevision = body.ExpectedRevision
	case SecurityProofScopeProviderDisable, SecurityProofScopeProviderResume:
		var body providerOnboardingLifecycleProofBody
		if common.DecodeJsonDisallowUnknownFields(bytes.NewReader(raw), &body) != nil {
			clear(raw)
			securityProofError(c, "SECURITY_PROOF_REQUEST_MISMATCH", "安全验证与当前请求不匹配")
			return service.SecurityProofBinding{}, nil, false
		}
		expectedRevision = body.ExpectedRevision
	default:
		clear(raw)
		securityProofError(c, "SECURITY_PROOF_SCOPE_MISMATCH", "安全验证范围不匹配")
		return service.SecurityProofBinding{}, nil, false
	}
	digest := sha256.Sum256(raw)
	binding := service.SecurityProofBinding{
		Action: scope, Provider: c.Param("provider"),
		HTTPMethod: c.Request.Method, HTTPPath: c.Request.URL.Path,
		BodySHA256:       "sha256:" + hex.EncodeToString(digest[:]),
		ExpectedRevision: expectedRevision,
	}
	if err := binding.Validate(scope); err != nil {
		clear(raw)
		securityProofError(c, "SECURITY_PROOF_REQUEST_MISMATCH", "安全验证与当前请求不匹配")
		return service.SecurityProofBinding{}, nil, false
	}
	return binding, raw, true
}

// RequireSecurityProof validates a proof against the authenticated dashboard
// session and writes the shared proof error contract on failure.
func RequireSecurityProof(c *gin.Context, requiredScope string, allowedMethods []string) bool {
	identity, ok := GetSessionAuthIdentity(c)
	if !ok {
		securityProofError(c, "SECURITY_PROOF_INVALID", "安全验证状态无效")
		return false
	}
	raw := strings.TrimSpace(c.GetHeader("X-Security-Proof"))
	if raw == "" {
		securityProofError(c, "SECURITY_PROOF_REQUIRED", "需要安全验证")
		return false
	}
	if _, err := service.VerifySecurityProof(raw, identity, requiredScope, allowedMethods); err != nil {
		writeSecurityProofVerificationError(c, err)
		return false
	}
	return true
}

func writeSecurityProofVerificationError(c *gin.Context, err error) {
	switch {
	case errors.Is(err, service.ErrAuthTokenExpired):
		securityProofError(c, "SECURITY_PROOF_EXPIRED", "安全验证已过期")
	case errors.Is(err, service.ErrProofConsumed), errors.Is(err, model.ErrAuthFlowConsumed):
		securityProofError(c, "SECURITY_PROOF_CONSUMED", "安全验证已使用，请重新验证")
	case errors.Is(err, service.ErrProofBinding):
		securityProofError(c, "SECURITY_PROOF_REQUEST_MISMATCH", "安全验证与当前请求不匹配")
	case errors.Is(err, service.ErrProofScope):
		securityProofError(c, "SECURITY_PROOF_SCOPE_MISMATCH", "安全验证范围不匹配")
	case errors.Is(err, service.ErrProofMethod):
		securityProofError(c, "SECURITY_PROOF_METHOD_MISMATCH", "安全验证方式不匹配")
	default:
		securityProofError(c, "SECURITY_PROOF_INVALID", "安全验证状态无效")
	}
}

func securityProofError(c *gin.Context, code, message string) {
	c.JSON(http.StatusForbidden, gin.H{
		"success": false,
		"message": message,
		"code":    code,
	})
	c.Abort()
}
