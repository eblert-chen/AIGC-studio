package service

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/google/uuid"
)

type platformGenerationOperationsCredential struct {
	TenantID    string `json:"tenant_id"`
	TokenSHA256 string `json:"token_sha256"`
}

type platformGenerationReconciliationApprovalKey struct {
	TenantID string `json:"tenant_id"`
	KeyID    string `json:"key_id"`
	Secret   string `json:"secret"`
}

var platformGenerationReconciliationApprovalKeyIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$`)

func loadPlatformGenerationOperationsCredentials() ([]platformGenerationOperationsCredential, error) {
	installedCredentials, installed := platformRelayInstalledOperationsCredentials()
	raw := strings.TrimSpace(os.Getenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON"))
	if installed {
		raw = ""
	}
	if raw == "" {
		if installed {
			// Continue through the shared exact validation below.
		} else if PlatformRelayProductionSecurityEnabled() {
			return nil, fmt.Errorf("production operations credentials are required")
		} else {
			return []platformGenerationOperationsCredential{}, nil
		}
	}
	credentials := installedCredentials
	if !installed {
		if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(raw), &credentials); err != nil {
			return nil, fmt.Errorf("invalid Relay operations credentials: %w", err)
		}
	}
	if credentials == nil {
		return nil, fmt.Errorf("invalid Relay operations credentials: expected a JSON array")
	}
	seen := make(map[string]struct{}, len(credentials))
	digestTenants := make(map[string]string, len(credentials))
	for _, credential := range credentials {
		parsed, err := uuid.Parse(credential.TenantID)
		digest, digestErr := hex.DecodeString(credential.TokenSHA256)
		if err != nil || parsed.String() != credential.TenantID || digestErr != nil || len(digest) != sha256.Size ||
			strings.ToLower(credential.TokenSHA256) != credential.TokenSHA256 {
			return nil, fmt.Errorf("Relay operations credential is invalid")
		}
		identity := credential.TenantID + "\x00" + credential.TokenSHA256
		if _, duplicate := seen[identity]; duplicate {
			return nil, fmt.Errorf("Relay operations credential is duplicated")
		}
		seen[identity] = struct{}{}
		if ownerTenantID, reused := digestTenants[credential.TokenSHA256]; reused && ownerTenantID != credential.TenantID {
			return nil, fmt.Errorf("Relay operations credential must not be shared across tenants")
		}
		digestTenants[credential.TokenSHA256] = credential.TenantID
	}
	if PlatformRelayProductionSecurityEnabled() {
		if len(credentials) == 0 {
			return nil, fmt.Errorf("production operations credentials are required")
		}
		snapshot := loadPlatformRelayConfig()
		if snapshot.err != nil {
			return nil, snapshot.err
		}
		covered := make(map[string]bool, len(credentials))
		for _, credential := range credentials {
			covered[credential.TenantID] = true
		}
		for _, credential := range snapshot.credentials {
			if !covered[credential.TenantID] {
				return nil, fmt.Errorf("every production Relay tenant requires a separate operations credential")
			}
		}
	}
	return credentials, nil
}

func ValidatePlatformGenerationOperationsConfiguration() error {
	if _, err := loadPlatformGenerationOperationsCredentials(); err != nil {
		return err
	}
	if _, err := loadPlatformGenerationReconciliationApprovalKeys(); err != nil {
		return err
	}
	if strings.TrimSpace(os.Getenv("RELAY_PLATFORM_CONTROL_TENANT_ID")) != "" || PlatformRelayProductionSecurityEnabled() {
		return ValidatePlatformChannelControlConfiguration()
	}
	return nil
}

// PlatformChannelControlTenantID returns the one operations tenant permitted
// to administer Relay-global channels. This is deliberately independent from
// the tenant_id supplied by a request and fails closed when not configured.
func PlatformChannelControlTenantID() (string, error) {
	tenantID := strings.TrimSpace(os.Getenv("RELAY_PLATFORM_CONTROL_TENANT_ID"))
	parsed, err := uuid.Parse(tenantID)
	if err != nil || parsed.String() != tenantID {
		return "", fmt.Errorf("RELAY_PLATFORM_CONTROL_TENANT_ID must be a canonical UUID")
	}
	credentials, err := loadPlatformGenerationOperationsCredentials()
	if err != nil {
		return "", err
	}
	for _, credential := range credentials {
		if credential.TenantID == tenantID {
			return tenantID, nil
		}
	}
	return "", fmt.Errorf("RELAY_PLATFORM_CONTROL_TENANT_ID must match an existing Relay operations credential tenant")
}

func ValidatePlatformChannelControlConfiguration() error {
	_, err := PlatformChannelControlTenantID()
	return err
}

func IsPlatformChannelControlTenant(tenantID string) bool {
	configured, err := PlatformChannelControlTenantID()
	return err == nil && subtle.ConstantTimeCompare([]byte(configured), []byte(tenantID)) == 1
}

func AuthenticatePlatformGenerationOperationsCredential(token string, tenantID string) bool {
	if len([]byte(token)) < 32 || strings.TrimSpace(token) != token {
		return false
	}
	parsed, err := uuid.Parse(tenantID)
	if err != nil || parsed.String() != tenantID {
		return false
	}
	credentials, err := loadPlatformGenerationOperationsCredentials()
	if err != nil || len(credentials) == 0 {
		return false
	}
	digest := fmt.Sprintf("%x", sha256.Sum256([]byte(token)))
	authorized := 0
	for _, credential := range credentials {
		tenantMatch := subtle.ConstantTimeCompare([]byte(credential.TenantID), []byte(tenantID))
		digestMatch := subtle.ConstantTimeCompare([]byte(credential.TokenSHA256), []byte(digest))
		authorized |= tenantMatch & digestMatch
	}
	return authorized == 1
}

func loadPlatformGenerationReconciliationApprovalKeys() ([]platformGenerationReconciliationApprovalKey, error) {
	installedKeys, installed := platformRelayInstalledApprovalKeys()
	raw := strings.TrimSpace(os.Getenv("RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON"))
	if installed {
		raw = ""
	}
	if raw == "" {
		if installed {
			// Continue through the shared exact validation below.
		} else if PlatformRelayProductionSecurityEnabled() {
			return nil, fmt.Errorf("production reconciliation approval keys are required")
		} else {
			operationsCredentials, err := loadPlatformGenerationOperationsCredentials()
			if err != nil {
				return nil, err
			}
			if len(operationsCredentials) > 0 {
				return nil, fmt.Errorf("every Relay operations tenant requires a reconciliation approval key")
			}
			return []platformGenerationReconciliationApprovalKey{}, nil
		}
	}
	keys := installedKeys
	if !installed {
		if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(raw), &keys); err != nil {
			return nil, fmt.Errorf("invalid reconciliation approval keys: %w", err)
		}
	}
	if keys == nil {
		return nil, fmt.Errorf("invalid reconciliation approval keys: expected a JSON array")
	}
	operationsCredentials, err := loadPlatformGenerationOperationsCredentials()
	if err != nil {
		return nil, err
	}
	operationsDigests := make(map[string]struct{}, len(operationsCredentials))
	operationsTenants := make(map[string]struct{}, len(operationsCredentials))
	for _, credential := range operationsCredentials {
		operationsDigests[credential.TokenSHA256] = struct{}{}
		operationsTenants[credential.TenantID] = struct{}{}
	}
	seen := make(map[string]struct{}, len(keys))
	secretTenants := make(map[string]string, len(keys))
	covered := make(map[string]bool, len(keys))
	for _, key := range keys {
		parsed, parseErr := uuid.Parse(key.TenantID)
		secretDigest := fmt.Sprintf("%x", sha256.Sum256([]byte(key.Secret)))
		if parseErr != nil || parsed.String() != key.TenantID ||
			!platformGenerationReconciliationApprovalKeyIDPattern.MatchString(key.KeyID) ||
			len([]byte(key.Secret)) < 32 || strings.TrimSpace(key.Secret) != key.Secret {
			return nil, fmt.Errorf("reconciliation approval key is invalid")
		}
		if PlatformRelayProductionSecurityEnabled() && platformRelaySecretIsPlaceholder(key.Secret) {
			return nil, fmt.Errorf("production reconciliation approval key is a placeholder")
		}
		identity := key.TenantID + "\x00" + key.KeyID
		if _, duplicate := seen[identity]; duplicate {
			return nil, fmt.Errorf("reconciliation approval key is duplicated")
		}
		seen[identity] = struct{}{}
		if ownerTenantID, reused := secretTenants[secretDigest]; reused && ownerTenantID != key.TenantID {
			return nil, fmt.Errorf("reconciliation approval key must not be shared across tenants")
		}
		if _, reusesOperationsToken := operationsDigests[secretDigest]; reusesOperationsToken {
			return nil, fmt.Errorf("reconciliation approval key must be independent from operations credentials")
		}
		if _, hasOperationsCredential := operationsTenants[key.TenantID]; !hasOperationsCredential {
			return nil, fmt.Errorf("reconciliation approval key has no matching operations tenant")
		}
		secretTenants[secretDigest] = key.TenantID
		covered[key.TenantID] = true
	}
	for tenantID := range operationsTenants {
		if !covered[tenantID] {
			return nil, fmt.Errorf("every Relay operations tenant requires a reconciliation approval key")
		}
	}
	if PlatformRelayProductionSecurityEnabled() {
		if len(keys) == 0 {
			return nil, fmt.Errorf("production reconciliation approval keys are required")
		}
		snapshot := loadPlatformRelayConfig()
		if snapshot.err != nil {
			return nil, snapshot.err
		}
		for _, credential := range snapshot.credentials {
			if !covered[credential.TenantID] {
				return nil, fmt.Errorf("every production Relay tenant requires a reconciliation approval key")
			}
		}
	}
	return keys, nil
}

func appendPlatformGenerationApprovalField(buffer *bytes.Buffer, value string) {
	buffer.WriteString(strconv.Itoa(len([]byte(value))))
	buffer.WriteByte(':')
	buffer.WriteString(value)
}

func platformGenerationReconciliationApprovalPayload(
	tenantID string,
	jobID string,
	request dto.PlatformGenerationReconciliationRequest,
) []byte {
	var buffer bytes.Buffer
	buffer.WriteString("platform-generation-reconciliation-approval-v1\x00")
	for _, value := range []string{
		tenantID,
		jobID,
		request.OperationID,
		request.Outcome,
		request.UpstreamTaskID,
		strconv.FormatInt(request.ExpectedRouteID, 10),
		strconv.Itoa(request.ExpectedSubmissionAttempt),
		request.ExpectedReconciliationToken,
		request.VerificationReference,
		request.ApprovedBy,
		request.ApprovalReason,
		request.ApprovalKeyID,
	} {
		appendPlatformGenerationApprovalField(&buffer, value)
	}
	return buffer.Bytes()
}

func VerifyPlatformGenerationReconciliationApproval(
	tenantID string,
	jobID string,
	request dto.PlatformGenerationReconciliationRequest,
) (bool, error) {
	if !strings.HasPrefix(request.ApprovalSignature, "hmac-sha256:") {
		return false, nil
	}
	provided, err := hex.DecodeString(strings.TrimPrefix(request.ApprovalSignature, "hmac-sha256:"))
	if err != nil || len(provided) != sha256.Size {
		return false, nil
	}
	keys, err := loadPlatformGenerationReconciliationApprovalKeys()
	if err != nil {
		return false, err
	}
	payload := platformGenerationReconciliationApprovalPayload(tenantID, jobID, request)
	authorized := 0
	for _, key := range keys {
		mac := hmac.New(sha256.New, []byte(key.Secret))
		_, _ = mac.Write(payload)
		expected := mac.Sum(nil)
		tenantMatch := subtle.ConstantTimeCompare([]byte(key.TenantID), []byte(tenantID))
		keyMatch := subtle.ConstantTimeCompare([]byte(key.KeyID), []byte(request.ApprovalKeyID))
		signatureMatch := subtle.ConstantTimeCompare(expected, provided)
		authorized |= tenantMatch & keyMatch & signatureMatch
	}
	return authorized == 1, nil
}
