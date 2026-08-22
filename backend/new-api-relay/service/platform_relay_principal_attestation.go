package service

import (
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/base32"
	"encoding/hex"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

var ErrProtectedPlatformRelayPrincipal = errors.New("protected Platform Relay service principal is not exact")

const protectedPlatformRelayPrincipalMaxClients = 256

const (
	protectedPlatformRelayServicePrincipalPasswordMarker = "!platform-relay-service-principal-v1!"
	protectedPlatformRelayServicePrincipalDisplayName    = "Relay Service"
)

type protectedPlatformRelayExpectedPrincipal struct {
	clientID string
	tenantID string
	key      string
}

// A protected upstream token is deliberately incompatible with new-api's
// optional "-channel" token suffix. The worker may send either the canonical
// stored key or its sk- presentation, but the database key is always exactly
// 48 alphanumeric characters.
var protectedPlatformRelayUpstreamTokenPattern = regexp.MustCompile(`^(?:sk-)?[A-Za-z0-9]{48}$`)

func protectedPlatformRelayTokenKey(value string) (string, bool) {
	if value != strings.TrimSpace(value) || !protectedPlatformRelayUpstreamTokenPattern.MatchString(value) {
		return "", false
	}
	return strings.TrimPrefix(value, "sk-"), true
}

func protectedPlatformRelayTokenPurpose(clientID string, tenantID string) string {
	return "platform-relay:" + clientID + ":" + tenantID
}

func protectedPlatformRelayServicePrincipalUsername(purpose string) string {
	digest := sha256.Sum256([]byte(purpose))
	encoded := strings.ToLower(base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(digest[:10]))
	return "rsvc_" + encoded[:15]
}

func protectedPlatformRelayServicePrincipalAffCode(purpose string) string {
	digest := sha256.Sum256([]byte("aff-code\x00" + purpose))
	encoded := strings.ToLower(base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(digest[:10]))
	return "r" + encoded
}

// BuildProtectedPlatformRelayServicePrincipalUser returns the only accepted
// non-interactive user shape for a protected Platform Relay principal. The
// password marker is intentionally not a password hash, so dashboard login can
// never authenticate it. Provisioning and attestation share this constructor.
func BuildProtectedPlatformRelayServicePrincipalUser(clientID string, tenantID string) (model.User, error) {
	if !platformRelayClientIDPattern.MatchString(clientID) || !platformRelayCanonicalTenantID(tenantID) {
		return model.User{}, ErrProtectedPlatformRelayPrincipal
	}
	purpose := protectedPlatformRelayTokenPurpose(clientID, tenantID)
	return model.User{
		Username:    protectedPlatformRelayServicePrincipalUsername(purpose),
		Password:    protectedPlatformRelayServicePrincipalPasswordMarker,
		DisplayName: protectedPlatformRelayServicePrincipalDisplayName,
		Role:        common.RoleCommonUser,
		Status:      common.UserStatusEnabled,
		Group:       "default",
		AffCode:     protectedPlatformRelayServicePrincipalAffCode(purpose),
		Remark:      model.PlatformRelayServicePrincipalRemark,
		AuthVersion: 1,
	}, nil
}

// ValidateProtectedPlatformRelayServicePrincipals is DB-direct and read-only.
// It does not call ValidateUserToken, populate caches, mutate status, or expose
// token material in an error. Startup, readiness, job admission and the final
// provider boundary all use this same proof.
func ValidateProtectedPlatformRelayServicePrincipals() error {
	if !model.RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	if model.DB == nil {
		return fmt.Errorf("%w: database is unavailable", ErrProtectedPlatformRelayPrincipal)
	}
	snapshot := loadPlatformRelayConfig()
	if snapshot.err != nil {
		return fmt.Errorf("%w: Relay configuration is unavailable", ErrProtectedPlatformRelayPrincipal)
	}
	if len(snapshot.credentials) == 0 || len(snapshot.credentials) > protectedPlatformRelayPrincipalMaxClients {
		return fmt.Errorf("%w: configured principal count is invalid", ErrProtectedPlatformRelayPrincipal)
	}

	expectedByPurpose := make(map[string]protectedPlatformRelayExpectedPrincipal, len(snapshot.credentials))
	credentialDigests := make(map[[sha256.Size]byte]string, len(snapshot.credentials)*4)
	addCredentialDigest := func(owner string, secret string) error {
		if secret == "" {
			return nil
		}
		digest := sha256.Sum256([]byte(secret))
		if existing, duplicate := credentialDigests[digest]; duplicate && existing != owner {
			return fmt.Errorf("%w: protected credentials are not globally isolated", ErrProtectedPlatformRelayPrincipal)
		}
		credentialDigests[digest] = owner
		return nil
	}
	for clientID, credential := range snapshot.credentials {
		key, ok := protectedPlatformRelayTokenKey(credential.UpstreamToken)
		if !ok {
			return fmt.Errorf("%w: upstream token format is invalid", ErrProtectedPlatformRelayPrincipal)
		}
		ownerPrefix := protectedPlatformRelayTokenPurpose(clientID, credential.TenantID)
		if err := addCredentialDigest(ownerPrefix+":api", credential.APIKey); err != nil {
			return err
		}
		// TokenAuth accepts the stored 48-byte key while workers may present
		// either that key or its sk- form. Register both representations so a
		// secret used by any other protocol cannot alias either one.
		if err := addCredentialDigest(ownerPrefix+":upstream", key); err != nil {
			return err
		}
		if err := addCredentialDigest(ownerPrefix+":upstream", "sk-"+key); err != nil {
			return err
		}
		if err := addCredentialDigest(ownerPrefix+":callback", credential.CallbackSigningSecret); err != nil {
			return err
		}
		purpose := protectedPlatformRelayTokenPurpose(clientID, credential.TenantID)
		if _, duplicate := expectedByPurpose[purpose]; duplicate {
			return fmt.Errorf("%w: principal purpose is duplicated", ErrProtectedPlatformRelayPrincipal)
		}
		expectedByPurpose[purpose] = protectedPlatformRelayExpectedPrincipal{clientID: clientID, tenantID: credential.TenantID, key: key}
	}
	if err := addCredentialDigest("internal-admission", PlatformRelayInternalAdmissionToken()); err != nil {
		return err
	}
	operationsCredentials, err := loadPlatformGenerationOperationsCredentials()
	if err != nil {
		return fmt.Errorf("%w: operations credential policy is invalid", ErrProtectedPlatformRelayPrincipal)
	}
	for index, credential := range operationsCredentials {
		digestBytes, decodeErr := hex.DecodeString(credential.TokenSHA256)
		if decodeErr != nil || len(digestBytes) != sha256.Size {
			return fmt.Errorf("%w: operations credential policy is invalid", ErrProtectedPlatformRelayPrincipal)
		}
		var digest [sha256.Size]byte
		copy(digest[:], digestBytes)
		owner := fmt.Sprintf("operations:%d", index)
		if existing, duplicate := credentialDigests[digest]; duplicate && existing != owner {
			return fmt.Errorf("%w: protected credentials are not globally isolated", ErrProtectedPlatformRelayPrincipal)
		}
		credentialDigests[digest] = owner
	}
	approvalKeys, err := loadPlatformGenerationReconciliationApprovalKeys()
	if err != nil {
		return fmt.Errorf("%w: reconciliation credential policy is invalid", ErrProtectedPlatformRelayPrincipal)
	}
	for index, approvalKey := range approvalKeys {
		if err := addCredentialDigest(fmt.Sprintf("reconciliation:%d", index), approvalKey.Secret); err != nil {
			return err
		}
	}

	isolation := sql.LevelSerializable
	if model.DB.Dialector.Name() == "postgres" {
		isolation = sql.LevelRepeatableRead
	}
	return model.DB.Transaction(func(tx *gorm.DB) error {
		return validateProtectedPlatformRelayServicePrincipalsTx(tx, expectedByPurpose)
	}, &sql.TxOptions{Isolation: isolation, ReadOnly: true})
}

func validateProtectedPlatformRelayServicePrincipalsTx(
	tx *gorm.DB,
	expectedByPurpose map[string]protectedPlatformRelayExpectedPrincipal,
) error {
	var tokens []model.Token
	// Purpose names are non-sensitive and are the only lookup values sent to
	// the SQL layer. Raw tokens never appear in SQL parameters or trace output.
	if err := tx.Unscoped().Where("name LIKE ?", model.PlatformRelayServicePrincipalTokenNamePrefix+"%").
		Limit(protectedPlatformRelayPrincipalMaxClients + 1).
		Find(&tokens).Error; err != nil {
		return fmt.Errorf("%w: token rows could not be inspected", ErrProtectedPlatformRelayPrincipal)
	}
	if len(tokens) != len(expectedByPurpose) {
		return fmt.Errorf("%w: token row is missing or ambiguous", ErrProtectedPlatformRelayPrincipal)
	}
	userIDs := make([]int, 0, len(tokens))
	seenUserIDs := make(map[int]struct{}, len(tokens))
	purposeByUserID := make(map[int]string, len(tokens))
	for _, token := range tokens {
		expected, ok := expectedByPurpose[token.Name]
		expectedDigest := sha256.Sum256([]byte(expected.key))
		actualDigest := sha256.Sum256([]byte(token.Key))
		if !ok || subtle.ConstantTimeCompare(expectedDigest[:], actualDigest[:]) != 1 ||
			token.DeletedAt.Valid ||
			token.Status != common.TokenStatusEnabled || token.ExpiredTime != -1 ||
			!token.UnlimitedQuota || token.RemainQuota != 0 || token.UsedQuota != 0 ||
			token.ModelLimitsEnabled || strings.TrimSpace(token.ModelLimits) != "" ||
			token.CrossGroupRetry || strings.TrimSpace(token.AutoGroups) != "" || strings.TrimSpace(token.Group) != "" ||
			(token.AllowIps != nil && strings.TrimSpace(*token.AllowIps) != "") ||
			token.Name != protectedPlatformRelayTokenPurpose(expected.clientID, expected.tenantID) {
			return fmt.Errorf("%w: token policy is invalid", ErrProtectedPlatformRelayPrincipal)
		}
		if _, reused := seenUserIDs[token.UserId]; reused {
			return fmt.Errorf("%w: service user is reused", ErrProtectedPlatformRelayPrincipal)
		}
		seenUserIDs[token.UserId] = struct{}{}
		purposeByUserID[token.UserId] = token.Name
		userIDs = append(userIDs, token.UserId)
	}

	var users []model.User
	if err := tx.Unscoped().Where(
		"remark = ? OR lower(substr(username, 1, ?)) = ?",
		model.PlatformRelayServicePrincipalRemark,
		len(model.PlatformRelayServicePrincipalUsernamePrefix),
		model.PlatformRelayServicePrincipalUsernamePrefix,
	).
		Limit(protectedPlatformRelayPrincipalMaxClients + 1).Find(&users).Error; err != nil {
		return fmt.Errorf("%w: service user rows could not be inspected", ErrProtectedPlatformRelayPrincipal)
	}
	if len(users) != len(userIDs) {
		return fmt.Errorf("%w: service user row is missing", ErrProtectedPlatformRelayPrincipal)
	}
	for _, user := range users {
		purpose, ok := purposeByUserID[user.Id]
		if !ok || user.DeletedAt.Valid ||
			user.Username != protectedPlatformRelayServicePrincipalUsername(purpose) ||
			user.Password != protectedPlatformRelayServicePrincipalPasswordMarker ||
			user.DisplayName != protectedPlatformRelayServicePrincipalDisplayName ||
			user.Status != common.UserStatusEnabled || user.Role != common.RoleCommonUser || user.Group != "default" ||
			user.AccessToken != nil || user.AuthVersion != 1 || user.LastLoginAt != 0 ||
			user.Email != "" || user.GitHubId != "" || user.DiscordId != "" || user.OidcId != "" ||
			user.WeChatId != "" || user.TelegramId != "" || user.LinuxDOId != "" ||
			user.StripeCustomer != "" || user.Setting != "" || user.Remark != model.PlatformRelayServicePrincipalRemark ||
			user.Quota != 0 || user.UsedQuota != 0 || user.RequestCount != 0 ||
			user.AffCode != protectedPlatformRelayServicePrincipalAffCode(purpose) ||
			user.AffCount != 0 || user.AffQuota != 0 || user.AffHistoryQuota != 0 || user.InviterId != 0 {
			return fmt.Errorf("%w: service user policy is invalid", ErrProtectedPlatformRelayPrincipal)
		}
	}
	var dedicatedUserTokens []model.Token
	if err := tx.Unscoped().Select("id, user_id").Where("user_id IN ?", userIDs).
		Limit(protectedPlatformRelayPrincipalMaxClients + 1).
		Find(&dedicatedUserTokens).Error; err != nil {
		return fmt.Errorf("%w: service user token ownership could not be inspected", ErrProtectedPlatformRelayPrincipal)
	}
	if len(dedicatedUserTokens) != len(tokens) {
		return fmt.Errorf("%w: service user is not dedicated", ErrProtectedPlatformRelayPrincipal)
	}
	for _, authenticationModel := range []any{
		&model.UserSession{},
		&model.AuthFlow{},
		&model.ExternalIdentityClaim{},
		&model.PasskeyCredential{},
		&model.UserOAuthBinding{},
		&model.TwoFA{},
		&model.TwoFABackupCode{},
	} {
		var footprintCount int64
		if err := tx.Unscoped().Model(authenticationModel).Where("user_id IN ?", userIDs).Count(&footprintCount).Error; err != nil {
			return fmt.Errorf("%w: service user authentication footprint could not be inspected", ErrProtectedPlatformRelayPrincipal)
		}
		if footprintCount != 0 {
			return fmt.Errorf("%w: service user has an interactive authentication footprint", ErrProtectedPlatformRelayPrincipal)
		}
	}
	return nil
}

// ValidateProtectedPlatformRelayPrincipalForJobID is used by the private
// native-submit admission before it changes the route state. It prevents an
// already-running worker from crossing the provider boundary after a token or
// user is revoked, limited, or rebound.
func ValidateProtectedPlatformRelayPrincipalForJobID(jobID string) error {
	if !model.RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	if err := ValidateProtectedPlatformRelayServicePrincipals(); err != nil {
		return err
	}
	var job model.PlatformGenerationJob
	if err := model.DB.Select("id, source_client_id, tenant_id").Where("id = ?", jobID).First(&job).Error; err != nil {
		return fmt.Errorf("%w: Platform job principal binding is unavailable", ErrProtectedPlatformRelayPrincipal)
	}
	snapshot := loadPlatformRelayConfig()
	credential, ok := snapshot.credentials[job.SourceClientID]
	if !ok || credential.TenantID != job.TenantID {
		return fmt.Errorf("%w: Platform job principal binding is invalid", ErrProtectedPlatformRelayPrincipal)
	}
	return nil
}

// ValidateProtectedPlatformRelayRequestPrincipalForJobID additionally binds
// the actual private native-submit Authorization and TokenAuth context to the
// exact configured service token before BeginRouteSubmission mutates durable
// route state. Suffix/channel token presentations are rejected.
func ValidateProtectedPlatformRelayRequestPrincipalForJobID(
	jobID string,
	tokenID int,
	tokenKey string,
	authorization string,
) error {
	if !model.RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	if err := ValidateProtectedPlatformRelayServicePrincipals(); err != nil {
		return err
	}
	var job model.PlatformGenerationJob
	if err := model.DB.Select("id, source_client_id, tenant_id").Where("id = ?", jobID).First(&job).Error; err != nil {
		return fmt.Errorf("%w: Platform job principal binding is unavailable", ErrProtectedPlatformRelayPrincipal)
	}
	snapshot := loadPlatformRelayConfig()
	credential, ok := snapshot.credentials[job.SourceClientID]
	if !ok || credential.TenantID != job.TenantID {
		return fmt.Errorf("%w: Platform job principal binding is invalid", ErrProtectedPlatformRelayPrincipal)
	}
	expectedKey, ok := protectedPlatformRelayTokenKey(credential.UpstreamToken)
	if !ok || tokenID <= 0 {
		return fmt.Errorf("%w: request principal is invalid", ErrProtectedPlatformRelayPrincipal)
	}
	expectedKeyDigest := sha256.Sum256([]byte(expectedKey))
	actualKeyDigest := sha256.Sum256([]byte(tokenKey))
	expectedAuthorizationDigest := sha256.Sum256([]byte("Bearer " + credential.UpstreamToken))
	actualAuthorizationDigest := sha256.Sum256([]byte(authorization))
	if subtle.ConstantTimeCompare(expectedKeyDigest[:], actualKeyDigest[:]) != 1 ||
		subtle.ConstantTimeCompare(expectedAuthorizationDigest[:], actualAuthorizationDigest[:]) != 1 {
		return fmt.Errorf("%w: request principal is invalid", ErrProtectedPlatformRelayPrincipal)
	}
	var tokens []model.Token
	if err := model.DB.Select("id, key").Where(
		"name = ?",
		protectedPlatformRelayTokenPurpose(job.SourceClientID, job.TenantID),
	).Order("id ASC").Limit(2).Find(&tokens).Error; err != nil {
		return fmt.Errorf("%w: request principal could not be inspected", ErrProtectedPlatformRelayPrincipal)
	}
	if len(tokens) != 1 || tokens[0].Id != tokenID {
		return fmt.Errorf("%w: request principal is ambiguous", ErrProtectedPlatformRelayPrincipal)
	}
	storedKeyDigest := sha256.Sum256([]byte(tokens[0].Key))
	if subtle.ConstantTimeCompare(expectedKeyDigest[:], storedKeyDigest[:]) != 1 {
		return fmt.Errorf("%w: request principal is inconsistent", ErrProtectedPlatformRelayPrincipal)
	}
	return nil
}
