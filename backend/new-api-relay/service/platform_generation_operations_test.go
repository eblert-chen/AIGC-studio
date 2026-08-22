package service

import (
	"crypto/hmac"
	"crypto/sha256"
	"fmt"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const (
	platformOperationsTenantOne = "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
	platformOperationsTenantTwo = "58775bb2-b6d2-4ad3-ab03-2f9d10854ba1"
)

func platformOperationsDigest(token string) string {
	return fmt.Sprintf("%x", sha256.Sum256([]byte(token)))
}

func platformOperationsCredential(tenantID string, token string) string {
	return fmt.Sprintf(
		`{"tenant_id":%q,"token_sha256":%q}`,
		tenantID,
		platformOperationsDigest(token),
	)
}

func platformReconciliationApprovalKey(tenantID string, keyID string, secret string) string {
	return fmt.Sprintf(
		`{"tenant_id":%q,"key_id":%q,"secret":%q}`,
		tenantID,
		keyID,
		secret,
	)
}

func configurePlatformOperationsDevelopment(t *testing.T) {
	t.Helper()
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON", "")
}

func TestPlatformGenerationOperationsCredentialsUseStrictArrayContract(t *testing.T) {
	configurePlatformOperationsDevelopment(t)
	token := "operations-token-one-with-at-least-32-bytes"
	valid := platformOperationsCredential(platformOperationsTenantOne, token)
	tests := []struct {
		name    string
		raw     string
		message string
	}{
		{name: "null is not an empty array", raw: `null`, message: "expected a JSON array"},
		{name: "unknown field", raw: `[` + strings.Replace(valid, `}`, `,"scope":"all"}`, 1) + `]`, message: "unknown field"},
		{name: "trailing document", raw: `[` + valid + `] []`, message: "trailing JSON value"},
		{name: "non canonical tenant", raw: `[` + platformOperationsCredential(strings.ToUpper(platformOperationsTenantOne), token) + `]`, message: "credential is invalid"},
		{name: "uppercase digest", raw: `[{"tenant_id":"` + platformOperationsTenantOne + `","token_sha256":"` + strings.ToUpper(platformOperationsDigest(token)) + `"}]`, message: "credential is invalid"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", test.raw)
			err := ValidatePlatformGenerationOperationsConfiguration()
			require.Error(t, err)
			assert.Contains(t, err.Error(), test.message)
		})
	}

	t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", `[]`)
	require.NoError(t, ValidatePlatformGenerationOperationsConfiguration())
}

func TestAuthenticatePlatformGenerationOperationsCredentialIsTenantScoped(t *testing.T) {
	configurePlatformOperationsDevelopment(t)
	firstToken := "operations-token-one-with-at-least-32-bytes"
	secondToken := "operations-token-two-with-at-least-32-bytes"
	t.Setenv(
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		`[`+platformOperationsCredential(platformOperationsTenantOne, firstToken)+`,`+
			platformOperationsCredential(platformOperationsTenantTwo, secondToken)+`]`,
	)

	assert.True(t, AuthenticatePlatformGenerationOperationsCredential(firstToken, platformOperationsTenantOne))
	assert.True(t, AuthenticatePlatformGenerationOperationsCredential(secondToken, platformOperationsTenantTwo))
	assert.False(t, AuthenticatePlatformGenerationOperationsCredential(firstToken, platformOperationsTenantTwo))
	assert.False(t, AuthenticatePlatformGenerationOperationsCredential(secondToken, platformOperationsTenantOne))
	assert.False(t, AuthenticatePlatformGenerationOperationsCredential("well-formed-but-wrong-operations-token-32-bytes", platformOperationsTenantOne))
	assert.False(t, AuthenticatePlatformGenerationOperationsCredential(" "+firstToken, platformOperationsTenantOne))
	assert.False(t, AuthenticatePlatformGenerationOperationsCredential("too-short", platformOperationsTenantOne))
	assert.False(t, AuthenticatePlatformGenerationOperationsCredential(firstToken, "not-a-tenant-id"))
}

func TestPlatformChannelControlTenantMustMatchAnOperationsCredential(t *testing.T) {
	configurePlatformOperationsDevelopment(t)
	token := "operations-token-one-with-at-least-32-bytes"
	t.Setenv(
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		`[`+platformOperationsCredential(platformOperationsTenantOne, token)+`]`,
	)

	t.Setenv("RELAY_PLATFORM_CONTROL_TENANT_ID", "")
	require.ErrorContains(t, ValidatePlatformChannelControlConfiguration(), "canonical UUID")
	t.Setenv("RELAY_PLATFORM_CONTROL_TENANT_ID", platformOperationsTenantTwo)
	require.ErrorContains(t, ValidatePlatformChannelControlConfiguration(), "must match")
	t.Setenv("RELAY_PLATFORM_CONTROL_TENANT_ID", platformOperationsTenantOne)
	require.NoError(t, ValidatePlatformChannelControlConfiguration())
	assert.True(t, IsPlatformChannelControlTenant(platformOperationsTenantOne))
	assert.False(t, IsPlatformChannelControlTenant(platformOperationsTenantTwo))
}

func TestPlatformGenerationOperationsCredentialCannotBeSharedAcrossTenants(t *testing.T) {
	configurePlatformOperationsDevelopment(t)
	sharedToken := "shared-operations-token-with-at-least-32-bytes"
	t.Setenv(
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		`[`+platformOperationsCredential(platformOperationsTenantOne, sharedToken)+`,`+
			platformOperationsCredential(platformOperationsTenantTwo, sharedToken)+`]`,
	)

	err := ValidatePlatformGenerationOperationsConfiguration()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "must not be shared across tenants")
}

func TestPlatformGenerationOperationsCredentialAllowsSameTenantRotation(t *testing.T) {
	configurePlatformOperationsDevelopment(t)
	oldToken := "old-operations-token-with-at-least-32-bytes"
	newToken := "new-operations-token-with-at-least-32-bytes"
	t.Setenv(
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		`[`+platformOperationsCredential(platformOperationsTenantOne, oldToken)+`,`+
			platformOperationsCredential(platformOperationsTenantOne, newToken)+`]`,
	)
	t.Setenv(
		"RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON",
		`[`+platformReconciliationApprovalKey(platformOperationsTenantOne, "platform-approval-v1", "approval-secret-one-with-at-least-32-bytes")+`]`,
	)

	require.NoError(t, ValidatePlatformGenerationOperationsConfiguration())
	assert.True(t, AuthenticatePlatformGenerationOperationsCredential(oldToken, platformOperationsTenantOne))
	assert.True(t, AuthenticatePlatformGenerationOperationsCredential(newToken, platformOperationsTenantOne))
}

func TestProductionOperationsCredentialsCoverEveryRelayTenant(t *testing.T) {
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	t.Setenv("APP_ENV", "")
	t.Setenv("DEPLOYMENT_ENV", "")
	t.Setenv("ENVIRONMENT", "")
	t.Setenv("RELAY_COMPAT_MODEL_CAPABILITIES_JSON", "")
	t.Setenv("RELAY_COMPAT_MODEL_ROUTES_JSON", `{}`)
	t.Setenv("RELAY_PLATFORM_CONTROL_TENANT_ID", platformOperationsTenantOne)
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", fmt.Sprintf(`{
		"platform-one":{"tenant_id":%q,"api_key":%q,"upstream_token":%q},
		"platform-two":{"tenant_id":%q,"api_key":%q,"upstream_token":%q}
	}`,
		platformOperationsTenantOne, strings.Repeat("a", 32), platformRelayRuntimeSecretsTestToken("operations-tenant-one"),
		platformOperationsTenantTwo, strings.Repeat("c", 32), platformRelayRuntimeSecretsTestToken("operations-tenant-two"),
	))
	firstToken := "operations-token-one-with-at-least-32-bytes"
	secondToken := "operations-token-two-with-at-least-32-bytes"
	firstApprovalSecret := "approval-secret-one-with-at-least-32-bytes"
	secondApprovalSecret := "approval-secret-two-with-at-least-32-bytes"
	t.Setenv(
		"RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON",
		`[`+platformReconciliationApprovalKey(platformOperationsTenantOne, "platform-approval-v1", firstApprovalSecret)+`,`+
			platformReconciliationApprovalKey(platformOperationsTenantTwo, "platform-approval-v1", secondApprovalSecret)+`]`,
	)

	t.Setenv(
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		`[`+platformOperationsCredential(platformOperationsTenantOne, firstToken)+`]`,
	)
	err := ValidatePlatformGenerationOperationsConfiguration()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "every production Relay tenant")

	t.Setenv(
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		`[`+platformOperationsCredential(platformOperationsTenantOne, firstToken)+`,`+
			platformOperationsCredential(platformOperationsTenantTwo, secondToken)+`]`,
	)
	require.NoError(t, ValidatePlatformGenerationOperationsConfiguration())
}

func TestPlatformGenerationReconciliationApprovalRequiresIndependentTenantKey(t *testing.T) {
	configurePlatformOperationsDevelopment(t)
	operationsToken := "operations-token-one-with-at-least-32-bytes"
	approvalSecret := "approval-secret-one-with-at-least-32-bytes"
	t.Setenv(
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		`[`+platformOperationsCredential(platformOperationsTenantOne, operationsToken)+`]`,
	)
	err := ValidatePlatformGenerationOperationsConfiguration()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "requires a reconciliation approval key")

	t.Setenv(
		"RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON",
		`[`+platformReconciliationApprovalKey(platformOperationsTenantOne, "platform-approval-v1", operationsToken)+`]`,
	)
	err = ValidatePlatformGenerationOperationsConfiguration()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "independent from operations credentials")

	t.Setenv(
		"RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON",
		`[`+platformReconciliationApprovalKey(platformOperationsTenantOne, "platform-approval-v1", approvalSecret)+`]`,
	)
	require.NoError(t, ValidatePlatformGenerationOperationsConfiguration())
}

func TestVerifyPlatformGenerationReconciliationApprovalBindsCanonicalProof(t *testing.T) {
	configurePlatformOperationsDevelopment(t)
	operationsToken := "operations-token-one-with-at-least-32-bytes"
	approvalSecret := "approval-secret-one-with-at-least-32-bytes"
	t.Setenv(
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		`[`+platformOperationsCredential(platformOperationsTenantOne, operationsToken)+`]`,
	)
	t.Setenv(
		"RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON",
		`[`+platformReconciliationApprovalKey(platformOperationsTenantOne, "platform-approval-v1", approvalSecret)+`]`,
	)
	jobID := "6b4f72d2-4d64-4ef9-adf2-7ead3e125b4f"
	request := dto.PlatformGenerationReconciliationRequest{
		OperationID:                 "reconcile-operation-0001",
		TenantID:                    platformOperationsTenantOne,
		Outcome:                     "not_created",
		ExpectedRouteID:             19,
		ExpectedSubmissionAttempt:   2,
		ExpectedReconciliationToken: "sha256:" + strings.Repeat("a", 64),
		VerificationReference:       "provider-console-case-42",
		ApprovedBy:                  "platform-admin-1",
		ApprovalReason:              "Provider console proves absence",
		ApprovalKeyID:               "platform-approval-v1",
	}
	mac := hmac.New(sha256.New, []byte(approvalSecret))
	_, _ = mac.Write(platformGenerationReconciliationApprovalPayload(
		platformOperationsTenantOne,
		jobID,
		request,
	))
	request.ApprovalSignature = fmt.Sprintf("hmac-sha256:%x", mac.Sum(nil))

	authorized, err := VerifyPlatformGenerationReconciliationApproval(
		platformOperationsTenantOne,
		jobID,
		request,
	)
	require.NoError(t, err)
	assert.True(t, authorized)

	forgedActor := request
	forgedActor.ApprovedBy = "operations-token-caller"
	authorized, err = VerifyPlatformGenerationReconciliationApproval(
		platformOperationsTenantOne,
		jobID,
		forgedActor,
	)
	require.NoError(t, err)
	assert.False(t, authorized)

	forgedReason := request
	forgedReason.ApprovalReason = "Bypass Platform approval"
	authorized, err = VerifyPlatformGenerationReconciliationApproval(
		platformOperationsTenantOne,
		jobID,
		forgedReason,
	)
	require.NoError(t, err)
	assert.False(t, authorized)
}
