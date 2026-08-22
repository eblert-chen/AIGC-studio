package service

import (
	"bytes"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"sync"

	"github.com/QuantumNous/new-api/common"
	"github.com/go-redis/redis/v8"
)

const (
	PlatformRelayAPIRuntimeSecretsFileKind          = "relay_api_runtime_secrets"
	PlatformRelayAPIRuntimeSecretsFileSchemaVersion = 1
)

type PlatformRelayAPIRuntimeClientSecret struct {
	ClientID              string `json:"client_id"`
	TenantID              string `json:"tenant_id"`
	APIKey                string `json:"api_key"`
	CallbackURL           string `json:"callback_url,omitempty"`
	CallbackSigningSecret string `json:"callback_signing_secret,omitempty"`
}

type PlatformRelayAPIRuntimeApplicationSecrets struct {
	SessionSecret string `json:"session_secret"`
	CryptoSecret  string `json:"crypto_secret"`
}

type PlatformRelayAPIRuntimeOBSSecrets struct {
	AccessKeyID     string `json:"access_key_id"`
	SecretAccessKey string `json:"secret_access_key"`
	SecurityToken   string `json:"security_token,omitempty"`
}

type PlatformRelayAPIRuntimeSecretsFile struct {
	Kind                       string                                        `json:"kind"`
	SchemaVersion              int                                           `json:"schema_version"`
	RedisDSN                   string                                        `json:"redis_dsn"`
	Application                PlatformRelayAPIRuntimeApplicationSecrets     `json:"application"`
	Clients                    []PlatformRelayAPIRuntimeClientSecret         `json:"clients"`
	OperationsCredentials      []platformGenerationOperationsCredential      `json:"operations_credentials"`
	ReconciliationApprovalKeys []platformGenerationReconciliationApprovalKey `json:"reconciliation_approval_keys"`
	InternalAdmissionToken     string                                        `json:"internal_admission_token"`
	ArtifactSigningSecret      string                                        `json:"artifact_signing_secret"`
	HuaweiOBS                  PlatformRelayAPIRuntimeOBSSecrets             `json:"huawei_obs"`
	ProviderAlertSigningSecret string                                        `json:"provider_alert_signing_secret"`
	PlatformInternalToken      string                                        `json:"platform_internal_service_token"`
	ChannelCostSigningSecret   string                                        `json:"channel_cost_signing_secret"`
	TelemetrySigningSecret     string                                        `json:"telemetry_signing_secret"`
}

type platformRelayInstalledAPIRuntimeSecrets struct {
	fingerprint                [sha256.Size]byte
	credentials                map[string]PlatformRelayCredential
	operations                 []platformGenerationOperationsCredential
	approvals                  []platformGenerationReconciliationApprovalKey
	internalAdmissionToken     string
	artifactSigningSecret      string
	obs                        PlatformRelayAPIRuntimeOBSSecrets
	providerAlertSigningSecret string
	platformInternalToken      string
	channelCostSigningSecret   string
	telemetrySigningSecret     string
}

var platformRelayAPIRuntimeSecretsState struct {
	sync.RWMutex
	value *platformRelayInstalledAPIRuntimeSecrets
}

func protectedPlatformRelaySecretDiverse(value string) bool {
	distinct := make(map[byte]struct{}, len(value))
	for index := 0; index < len(value); index++ {
		distinct[value[index]] = struct{}{}
	}
	if len(distinct) < 8 {
		return false
	}
	for period := 1; period <= 16 && period*2 <= len(value); period++ {
		periodic := true
		for index := period; index < len(value); index++ {
			if value[index] != value[index%period] {
				periodic = false
				break
			}
		}
		if periodic {
			return false
		}
	}
	return true
}

func ParsePlatformRelayAPIRuntimeSecretsFile(raw []byte) (PlatformRelayAPIRuntimeSecretsFile, error) {
	var document PlatformRelayAPIRuntimeSecretsFile
	invalid := func() (PlatformRelayAPIRuntimeSecretsFile, error) {
		return PlatformRelayAPIRuntimeSecretsFile{}, errors.New("Relay API runtime secret file is invalid")
	}
	if len(raw) == 0 || !json.Valid(raw) || rejectPlatformRelayDuplicateJSONKeys(raw) != nil {
		return invalid()
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&document) != nil || requirePlatformRelayJSONEOF(decoder) != nil ||
		document.Kind != PlatformRelayAPIRuntimeSecretsFileKind ||
		document.SchemaVersion != PlatformRelayAPIRuntimeSecretsFileSchemaVersion ||
		len(document.Clients) == 0 || len(document.Clients) > protectedPlatformRelayPrincipalMaxClients {
		return invalid()
	}
	redisURL, err := url.Parse(document.RedisDSN)
	redisOptions, redisErr := redis.ParseURL(document.RedisDSN)
	if err != nil || redisErr != nil || redisURL.Scheme != "rediss" || redisURL.Host == "" ||
		redisURL.Opaque != "" || redisURL.Fragment != "" || redisURL.RawFragment != "" || redisURL.RawQuery != "" ||
		redisOptions.Password == "" || document.RedisDSN != strings.TrimSpace(document.RedisDSN) ||
		strings.IndexFunc(document.RedisDSN, func(value rune) bool { return value < 0x20 || value == 0x7f }) >= 0 {
		return invalid()
	}

	secretDigests := make(map[[sha256.Size]byte]struct{})
	addSecret := func(value string, minimum int, optional bool) bool {
		if value == "" && optional {
			return true
		}
		if value != strings.TrimSpace(value) || len([]byte(value)) < minimum ||
			strings.IndexFunc(value, func(character rune) bool { return character < 0x20 || character == 0x7f }) >= 0 ||
			platformRelaySecretIsPlaceholder(value) || !protectedPlatformRelaySecretDiverse(value) {
			return false
		}
		digest := sha256.Sum256([]byte(value))
		if _, duplicate := secretDigests[digest]; duplicate {
			return false
		}
		secretDigests[digest] = struct{}{}
		return true
	}
	for _, value := range []string{
		redisOptions.Password,
		document.Application.SessionSecret,
		document.Application.CryptoSecret,
		document.InternalAdmissionToken,
		document.ArtifactSigningSecret,
		document.HuaweiOBS.SecretAccessKey,
		document.ProviderAlertSigningSecret,
		document.PlatformInternalToken,
		document.ChannelCostSigningSecret,
		document.TelemetrySigningSecret,
	} {
		if !addSecret(value, 32, false) {
			return invalid()
		}
	}
	if !addSecret(document.HuaweiOBS.AccessKeyID, 16, false) ||
		!addSecret(document.HuaweiOBS.SecurityToken, 16, true) {
		return invalid()
	}

	previousClientID := ""
	seenTenants := make(map[string]struct{}, len(document.Clients))
	for _, client := range document.Clients {
		if !platformRelayClientIDPattern.MatchString(client.ClientID) || client.ClientID <= previousClientID ||
			!platformRelayCanonicalTenantID(client.TenantID) ||
			(client.CallbackURL == "") != (client.CallbackSigningSecret == "") ||
			!addSecret(client.APIKey, 32, false) ||
			!addSecret(client.CallbackSigningSecret, 32, client.CallbackURL == "") {
			return invalid()
		}
		seenTenants[client.TenantID] = struct{}{}
		previousClientID = client.ClientID
	}
	if len(document.OperationsCredentials) == 0 || len(document.ReconciliationApprovalKeys) == 0 {
		return invalid()
	}
	operationsTenants := make(map[string]struct{}, len(document.OperationsCredentials))
	previousOperationIdentity := ""
	for _, credential := range document.OperationsCredentials {
		digestBytes, digestErr := hex.DecodeString(credential.TokenSHA256)
		identity := credential.TenantID + "\x00" + credential.TokenSHA256
		if !platformRelayCanonicalTenantID(credential.TenantID) || digestErr != nil ||
			len(digestBytes) != sha256.Size || strings.ToLower(credential.TokenSHA256) != credential.TokenSHA256 ||
			identity <= previousOperationIdentity {
			return invalid()
		}
		var digest [sha256.Size]byte
		copy(digest[:], digestBytes)
		if _, duplicate := secretDigests[digest]; duplicate {
			return invalid()
		}
		secretDigests[digest] = struct{}{}
		operationsTenants[credential.TenantID] = struct{}{}
		previousOperationIdentity = identity
	}
	approvalTenants := make(map[string]struct{}, len(document.ReconciliationApprovalKeys))
	previousApprovalIdentity := ""
	for _, approval := range document.ReconciliationApprovalKeys {
		identity := approval.TenantID + "\x00" + approval.KeyID
		if !platformRelayCanonicalTenantID(approval.TenantID) ||
			!platformGenerationReconciliationApprovalKeyIDPattern.MatchString(approval.KeyID) ||
			identity <= previousApprovalIdentity || !addSecret(approval.Secret, 32, false) {
			return invalid()
		}
		if _, exists := operationsTenants[approval.TenantID]; !exists {
			return invalid()
		}
		approvalTenants[approval.TenantID] = struct{}{}
		previousApprovalIdentity = identity
	}
	if len(operationsTenants) != len(seenTenants) || len(approvalTenants) != len(seenTenants) {
		return invalid()
	}
	for tenantID := range seenTenants {
		if _, ok := operationsTenants[tenantID]; !ok {
			return invalid()
		}
		if _, ok := approvalTenants[tenantID]; !ok {
			return invalid()
		}
	}
	return document, nil
}

func InstallPlatformRelayAPIRuntimeSecrets(
	principals []PlatformRelayServicePrincipalProvisionInput,
	document PlatformRelayAPIRuntimeSecretsFile,
	redisTLSCA common.ProtectedRelayRedisTLSCA,
) error {
	principalDocument := PlatformRelayServicePrincipalsFile{
		Kind: PlatformRelayServicePrincipalsFileKind, SchemaVersion: PlatformRelayServicePrincipalsFileSchemaVersion,
		Principals: principals,
	}
	principalRaw, err := json.Marshal(principalDocument)
	if err != nil {
		return errors.New("Relay API runtime secret installation is invalid")
	}
	validatedPrincipals, err := ParsePlatformRelayServicePrincipalsFile(principalRaw)
	clear(principalRaw)
	if err != nil {
		return errors.New("Relay API runtime secret installation is invalid")
	}
	runtimeRaw, err := json.Marshal(document)
	if err != nil {
		return errors.New("Relay API runtime secret installation is invalid")
	}
	validatedDocument, err := ParsePlatformRelayAPIRuntimeSecretsFile(runtimeRaw)
	clear(runtimeRaw)
	if err != nil || len(validatedPrincipals) != len(validatedDocument.Clients) {
		return errors.New("Relay API runtime secret installation is invalid")
	}
	document = validatedDocument
	principals = validatedPrincipals

	secretDigests := make(map[[sha256.Size]byte]struct{})
	addDigest := func(value string) bool {
		digest := sha256.Sum256([]byte(value))
		if _, duplicate := secretDigests[digest]; duplicate {
			return false
		}
		secretDigests[digest] = struct{}{}
		return true
	}
	redisOptions, err := redis.ParseURL(document.RedisDSN)
	if err != nil {
		return errors.New("Relay API runtime secret installation is invalid")
	}
	for _, value := range []string{
		redisOptions.Password, document.Application.SessionSecret, document.Application.CryptoSecret,
		document.InternalAdmissionToken, document.ArtifactSigningSecret, document.HuaweiOBS.AccessKeyID,
		document.HuaweiOBS.SecretAccessKey, document.ProviderAlertSigningSecret, document.PlatformInternalToken,
		document.ChannelCostSigningSecret, document.TelemetrySigningSecret,
	} {
		if !addDigest(value) {
			return errors.New("Relay API runtime secrets must be independent")
		}
	}
	if document.HuaweiOBS.SecurityToken != "" && !addDigest(document.HuaweiOBS.SecurityToken) {
		return errors.New("Relay API runtime secrets must be independent")
	}
	for _, client := range document.Clients {
		if !addDigest(client.APIKey) ||
			(client.CallbackSigningSecret != "" && !addDigest(client.CallbackSigningSecret)) {
			return errors.New("Relay API runtime secrets must be independent")
		}
	}
	for _, credential := range document.OperationsCredentials {
		digestBytes, _ := hex.DecodeString(credential.TokenSHA256)
		var digest [sha256.Size]byte
		copy(digest[:], digestBytes)
		if _, duplicate := secretDigests[digest]; duplicate {
			return errors.New("Relay API runtime secrets must be independent")
		}
		secretDigests[digest] = struct{}{}
	}
	for _, approval := range document.ReconciliationApprovalKeys {
		if !addDigest(approval.Secret) {
			return errors.New("Relay API runtime secrets must be independent")
		}
	}

	credentials := make(map[string]PlatformRelayCredential, len(principals))
	for index, principal := range principals {
		client := document.Clients[index]
		if principal.ClientID != client.ClientID || principal.TenantID != client.TenantID {
			return errors.New("Relay API runtime secret identities do not match")
		}
		bareToken := strings.TrimPrefix(principal.UpstreamToken, "sk-")
		if !addDigest(principal.UpstreamToken) || !addDigest(bareToken) {
			return errors.New("Relay API runtime secrets must be independent")
		}
		credentials[client.ClientID] = PlatformRelayCredential{
			TenantID: client.TenantID, APIKey: client.APIKey, UpstreamToken: principal.UpstreamToken,
			CallbackURL: client.CallbackURL, CallbackSigningSecret: client.CallbackSigningSecret,
		}
	}
	canonical, err := json.Marshal(struct {
		Principals []PlatformRelayServicePrincipalProvisionInput `json:"principals"`
		Runtime    PlatformRelayAPIRuntimeSecretsFile            `json:"runtime"`
	}{Principals: principals, Runtime: document})
	if err != nil {
		return errors.New("Relay API runtime secret file is invalid")
	}
	redisTLSCAFingerprint := redisTLSCA.Fingerprint()
	fingerprintHash := sha256.New()
	_, _ = fingerprintHash.Write(canonical)
	_, _ = fingerprintHash.Write(redisTLSCAFingerprint[:])
	var fingerprint [sha256.Size]byte
	copy(fingerprint[:], fingerprintHash.Sum(nil))
	clear(canonical)
	value := &platformRelayInstalledAPIRuntimeSecrets{
		fingerprint: fingerprint, credentials: credentials,
		operations:             append([]platformGenerationOperationsCredential(nil), document.OperationsCredentials...),
		approvals:              append([]platformGenerationReconciliationApprovalKey(nil), document.ReconciliationApprovalKeys...),
		internalAdmissionToken: document.InternalAdmissionToken,
		artifactSigningSecret:  document.ArtifactSigningSecret, obs: document.HuaweiOBS,
		providerAlertSigningSecret: document.ProviderAlertSigningSecret,
		platformInternalToken:      document.PlatformInternalToken,
		channelCostSigningSecret:   document.ChannelCostSigningSecret,
		telemetrySigningSecret:     document.TelemetrySigningSecret,
	}
	digestList := make([][sha256.Size]byte, 0, len(secretDigests))
	for digest := range secretDigests {
		digestList = append(digestList, digest)
	}
	platformRelayAPIRuntimeSecretsState.Lock()
	defer platformRelayAPIRuntimeSecretsState.Unlock()
	if existing := platformRelayAPIRuntimeSecretsState.value; existing != nil {
		if subtle.ConstantTimeCompare(existing.fingerprint[:], value.fingerprint[:]) != 1 {
			return errors.New("Relay API runtime secrets are immutable")
		}
		if err := common.ConfigureProtectedRelayRuntimeSecrets(
			document.RedisDSN, document.Application.SessionSecret, document.Application.CryptoSecret,
			redisTLSCA,
			digestList...,
		); err != nil {
			return errors.New("Relay API runtime secrets are unavailable")
		}
		return nil
	}
	if err := common.ConfigureProtectedRelayRuntimeSecrets(
		document.RedisDSN, document.Application.SessionSecret, document.Application.CryptoSecret,
		redisTLSCA,
		digestList...,
	); err != nil {
		return errors.New("Relay API runtime secrets are unavailable")
	}
	platformRelayAPIRuntimeSecretsState.value = value
	platformRelayConfigCache.Lock()
	platformRelayConfigCache.snapshot = platformRelayConfigSnapshot{}
	platformRelayConfigCache.Unlock()
	return nil
}

func platformRelayAPIRuntimeCredentials() (map[string]PlatformRelayCredential, [sha256.Size]byte, bool) {
	platformRelayAPIRuntimeSecretsState.RLock()
	value := platformRelayAPIRuntimeSecretsState.value
	if value == nil {
		platformRelayAPIRuntimeSecretsState.RUnlock()
		return nil, [sha256.Size]byte{}, false
	}
	credentials := make(map[string]PlatformRelayCredential, len(value.credentials))
	for clientID, credential := range value.credentials {
		credentials[clientID] = credential
	}
	fingerprint := value.fingerprint
	platformRelayAPIRuntimeSecretsState.RUnlock()
	return credentials, fingerprint, true
}

func platformRelayAPIRuntimeSecret(name string) (string, bool) {
	platformRelayAPIRuntimeSecretsState.RLock()
	value := platformRelayAPIRuntimeSecretsState.value
	if value == nil {
		platformRelayAPIRuntimeSecretsState.RUnlock()
		return "", false
	}
	var secret string
	switch name {
	case "RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN":
		secret = value.internalAdmissionToken
	case "RELAY_ARTIFACT_SIGNING_SECRET":
		secret = value.artifactSigningSecret
	case "HUAWEI_OBS_ACCESS_KEY_ID":
		secret = value.obs.AccessKeyID
	case "HUAWEI_OBS_SECRET_ACCESS_KEY":
		secret = value.obs.SecretAccessKey
	case "HUAWEI_OBS_SECURITY_TOKEN":
		secret = value.obs.SecurityToken
	case "RELAY_PROVIDER_ALERT_SIGNING_SECRET":
		secret = value.providerAlertSigningSecret
	case "RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN":
		secret = value.platformInternalToken
	case "RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET":
		secret = value.channelCostSigningSecret
	case "RELAY_TELEMETRY_SIGNING_SECRET":
		secret = value.telemetrySigningSecret
	}
	platformRelayAPIRuntimeSecretsState.RUnlock()
	return secret, true
}

func platformRelayRuntimeSecretOrEnvironment(name string) string {
	if secret, installed := platformRelayAPIRuntimeSecret(name); installed {
		return secret
	}
	return strings.TrimSpace(common.GetEnvOrDefaultString(name, ""))
}

func PlatformRelayInternalAdmissionToken() string {
	return platformRelayRuntimeSecretOrEnvironment("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN")
}

func platformRelayInstalledOperationsCredentials() ([]platformGenerationOperationsCredential, bool) {
	platformRelayAPIRuntimeSecretsState.RLock()
	value := platformRelayAPIRuntimeSecretsState.value
	if value == nil {
		platformRelayAPIRuntimeSecretsState.RUnlock()
		return nil, false
	}
	credentials := append([]platformGenerationOperationsCredential(nil), value.operations...)
	platformRelayAPIRuntimeSecretsState.RUnlock()
	return credentials, true
}

func platformRelayInstalledApprovalKeys() ([]platformGenerationReconciliationApprovalKey, bool) {
	platformRelayAPIRuntimeSecretsState.RLock()
	value := platformRelayAPIRuntimeSecretsState.value
	if value == nil {
		platformRelayAPIRuntimeSecretsState.RUnlock()
		return nil, false
	}
	keys := append([]platformGenerationReconciliationApprovalKey(nil), value.approvals...)
	platformRelayAPIRuntimeSecretsState.RUnlock()
	return keys, true
}

func platformRelayAPIRuntimeSecretsFingerprint() [sha256.Size]byte {
	platformRelayAPIRuntimeSecretsState.RLock()
	defer platformRelayAPIRuntimeSecretsState.RUnlock()
	if platformRelayAPIRuntimeSecretsState.value == nil {
		return [sha256.Size]byte{}
	}
	return platformRelayAPIRuntimeSecretsState.value.fingerprint
}
