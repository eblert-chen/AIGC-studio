package service

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
)

const (
	platformProcessSecretKind              = "platform_process_runtime_secrets"
	platformProcessSecretSchemaVersion     = 1
	platformProcessSecretMaximumBytes      = 256 * 1024
	platformProcessSecretMaximumLength     = 16 * 1024
	platformProcessDatabaseNameEnvironment = "PLATFORM_DATABASE_NAME"
)

type platformProcessSecretRoleContract struct {
	role                string
	fileID              string
	prefix              string
	environment         string
	databaseUser        string
	passwordFileID      string
	passwordEnvironment string
	fields              []string
}

var platformProcessSecretRoleContracts = []platformProcessSecretRoleContract{
	{
		role: "migration", fileID: "platform_migration_runtime", prefix: "platform.migration",
		environment: "PLATFORM_MIGRATION_RUNTIME_SECRETS_FILE", databaseUser: "platform_migration",
		passwordFileID: "platform_migration_password", passwordEnvironment: "PLATFORM_MIGRATION_DATABASE_PASSWORD_FILE",
		fields: []string{"database_url"},
	},
	{
		role: "platform-api", fileID: "platform_api_runtime", prefix: "platform.api",
		environment: "PLATFORM_API_RUNTIME_SECRETS_FILE", databaseUser: "platform_api",
		passwordFileID: "platform_api_password", passwordEnvironment: "PLATFORM_API_DATABASE_PASSWORD_FILE",
		fields: []string{
			"database_url", "relay_backends", "relay_tenant_id",
			"relay_operations_token", "relay_reconciliation_approval_key_id", "relay_reconciliation_approval_secret",
			"relay_callback_signing_secrets", "internal_service_token",
			"download_edge_completion_service_token", "channel_cost_signing_secret", "relay_telemetry_signing_secret",
			"provider_alert_signing_secret", "provider_alert_forward_signing_secret",
			"download_completion_edge_gateway_signing_secret", "download_completion_obs_access_log_signing_secret",
			"download_gateway_service_token", "download_gateway_registration_signing_secret",
			"download_gateway_attempt_encryption_key_base64", "jwt_signing_secret", "huawei_obs_access_key_id",
			"huawei_obs_secret_access_key", "huawei_obs_security_token", "publishing_plugin_credentials",
		},
	},
	{
		role: "dispatcher", fileID: "platform_dispatcher_runtime", prefix: "platform.dispatcher",
		environment: "PLATFORM_DISPATCHER_RUNTIME_SECRETS_FILE", databaseUser: "platform_dispatcher",
		passwordFileID: "platform_dispatcher_password", passwordEnvironment: "PLATFORM_DISPATCHER_DATABASE_PASSWORD_FILE",
		fields: []string{
			"database_url", "relay_backends",
			"huawei_obs_access_key_id", "huawei_obs_secret_access_key", "huawei_obs_security_token",
		},
	},
	{
		role: "relay-sync", fileID: "platform_relay_sync_runtime", prefix: "platform.relay_sync",
		environment: "PLATFORM_RELAY_SYNC_RUNTIME_SECRETS_FILE", databaseUser: "platform_relay_sync",
		passwordFileID: "platform_relay_sync_password", passwordEnvironment: "PLATFORM_RELAY_SYNC_DATABASE_PASSWORD_FILE",
		fields: []string{"database_url", "relay_backends"},
	},
	{
		role: "timeout-worker", fileID: "platform_timeout_worker_runtime", prefix: "platform.timeout_worker",
		environment: "PLATFORM_TIMEOUT_WORKER_RUNTIME_SECRETS_FILE", databaseUser: "platform_timeout_worker",
		passwordFileID: "platform_timeout_worker_password", passwordEnvironment: "PLATFORM_TIMEOUT_WORKER_DATABASE_PASSWORD_FILE",
		fields: []string{"database_url", "relay_backends"},
	},
	{
		role: "publishing-worker", fileID: "platform_publishing_worker_runtime", prefix: "platform.publishing_worker",
		environment: "PLATFORM_PUBLISHING_WORKER_RUNTIME_SECRETS_FILE", databaseUser: "platform_publishing_worker",
		passwordFileID: "platform_publishing_worker_password", passwordEnvironment: "PLATFORM_PUBLISHING_WORKER_DATABASE_PASSWORD_FILE",
		fields: []string{"database_url", "publishing_plugin_credentials"},
	},
	{
		role: "download-gateway-registration-worker", fileID: "platform_download_gateway_worker_runtime",
		prefix: "platform.download_gateway_worker", environment: "PLATFORM_DOWNLOAD_GATEWAY_WORKER_RUNTIME_SECRETS_FILE",
		databaseUser:   "platform_download_gateway_worker",
		passwordFileID: "platform_download_gateway_worker_password", passwordEnvironment: "PLATFORM_DOWNLOAD_GATEWAY_WORKER_DATABASE_PASSWORD_FILE",
		fields: []string{
			"database_url", "download_gateway_service_token", "download_gateway_registration_signing_secret",
			"download_gateway_attempt_encryption_key_base64",
		},
	},
}

var (
	platformProcessBackendIDPattern        = regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`)
	platformProcessClientIDPattern         = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$`)
	platformProcessContractPattern         = regexp.MustCompile(`^[a-z][a-z0-9._-]{0,79}$`)
	platformProcessApprovalKeyIDPattern    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$`)
	platformProcessPluginSpecPattern       = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$`)
	platformProcessPluginCredentialPattern = regexp.MustCompile(`^[A-Z][A-Z0-9_]{0,79}$`)
	platformProcessImagePattern            = regexp.MustCompile(`^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$`)
	platformProcessDatabasePasswordPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{32,128}$`)
)

type platformProcessSecretEnvelope struct {
	Kind          string          `json:"kind"`
	SchemaVersion int             `json:"schema_version"`
	ProcessRole   string          `json:"process_role"`
	Secrets       json.RawMessage `json:"secrets"`
}

type platformProcessRelayBackend struct {
	BaseURL          string `json:"base_url"`
	ClientID         string `json:"client_id"`
	APIKey           string `json:"api_key"`
	ContractRevision string `json:"contract_revision"`
}

type platformProcessSecretValues struct {
	DatabaseURL                                 string                                  `json:"database_url"`
	RelayBackends                               map[string]platformProcessRelayBackend  `json:"relay_backends"`
	RelayTenantID                               string                                  `json:"relay_tenant_id"`
	RelayOperationsToken                        string                                  `json:"relay_operations_token"`
	RelayReconciliationApprovalKeyID            string                                  `json:"relay_reconciliation_approval_key_id"`
	RelayReconciliationApprovalSecret           string                                  `json:"relay_reconciliation_approval_secret"`
	RelayCallbackSigningSecrets                 map[string]string                       `json:"relay_callback_signing_secrets"`
	InternalServiceToken                        string                                  `json:"internal_service_token"`
	DownloadEdgeCompletionServiceToken          string                                  `json:"download_edge_completion_service_token"`
	ChannelCostSigningSecret                    string                                  `json:"channel_cost_signing_secret"`
	RelayTelemetrySigningSecret                 string                                  `json:"relay_telemetry_signing_secret"`
	ProviderAlertSigningSecret                  string                                  `json:"provider_alert_signing_secret"`
	ProviderAlertForwardSigningSecret           string                                  `json:"provider_alert_forward_signing_secret"`
	DownloadCompletionEdgeGatewaySigningSecret  string                                  `json:"download_completion_edge_gateway_signing_secret"`
	DownloadCompletionOBSAccessLogSigningSecret string                                  `json:"download_completion_obs_access_log_signing_secret"`
	DownloadGatewayServiceToken                 string                                  `json:"download_gateway_service_token"`
	DownloadGatewayRegistrationSigningSecret    string                                  `json:"download_gateway_registration_signing_secret"`
	DownloadGatewayAttemptEncryptionKeyBase64   string                                  `json:"download_gateway_attempt_encryption_key_base64"`
	JWTSigningSecret                            string                                  `json:"jwt_signing_secret"`
	HuaweiOBSAccessKeyID                        string                                  `json:"huawei_obs_access_key_id"`
	HuaweiOBSSecretAccessKey                    string                                  `json:"huawei_obs_secret_access_key"`
	HuaweiOBSSecurityToken                      *string                                 `json:"huawei_obs_security_token"`
	PublishingPluginCredentials                 map[string]map[string]map[string]string `json:"publishing_plugin_credentials"`
}

type platformProcessSecretDocument struct {
	contract platformProcessSecretRoleContract
	values   platformProcessSecretValues
}

func platformProcessSecretContract(role string) (platformProcessSecretRoleContract, bool) {
	for _, contract := range platformProcessSecretRoleContracts {
		if contract.role == role {
			return contract, true
		}
	}
	return platformProcessSecretRoleContract{}, false
}

func platformProcessSecretDecode(raw []byte, destination any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(destination) != nil || requirePlatformRelayJSONEOF(decoder) != nil {
		return errors.New("Platform process runtime secret file is invalid")
	}
	return nil
}

func platformProcessSecretExactKeys(raw []byte, expected []string) bool {
	var values map[string]json.RawMessage
	if platformProcessSecretDecode(raw, &values) != nil || len(values) != len(expected) {
		return false
	}
	for _, key := range expected {
		if _, ok := values[key]; !ok {
			return false
		}
	}
	return true
}

func platformProcessSecretClean(value string, maximum int) bool {
	return value != "" && len(value) <= maximum && value == strings.TrimSpace(value) &&
		strings.IndexFunc(value, func(character rune) bool { return character < 0x20 || character == 0x7f }) < 0
}

func platformProcessSecretValid(value string) bool {
	if !platformProcessSecretClean(value, platformProcessSecretMaximumLength) || len([]byte(value)) < 32 ||
		platformProcessSecretIsPlaceholder(value) {
		return false
	}
	return protectedPlatformRelaySecretDiverse(value)
}

func platformProcessDatabasePasswordValid(value string) bool {
	return platformProcessDatabasePasswordPattern.MatchString(value) && platformProcessSecretValid(value)
}

func platformProcessSecretIsPlaceholder(value string) bool {
	lower := strings.ToLower(value)
	for _, prefix := range []string{
		"changeme", "change-me", "change_me", "default", "example", "placeholder",
		"replacewith", "replace-with", "replace_with", "secret", "test", "todo", "token", "your-", "your_",
	} {
		if strings.HasPrefix(lower, prefix) {
			return true
		}
	}
	return false
}

func platformProcessSecretIdentityValid(value string, maximum int) bool {
	return platformProcessSecretClean(value, maximum) &&
		strings.IndexFunc(value, unicode.IsSpace) < 0 && !platformProcessSecretIsPlaceholder(value)
}

func platformProcessSecretDatabasePassword(value, expectedUser string) ([]byte, string, bool) {
	parsed, err := url.Parse(value)
	query := parsed.Query()
	expectedCAPath := os.Getenv(platformRelaySecretIsolationPlatformDatabaseCAEnvironment)
	if err != nil || parsed.Scheme != "postgresql+psycopg" || parsed.Opaque != "" || parsed.User == nil ||
		parsed.User.Username() != expectedUser || parsed.Hostname() == "" || parsed.Port() == "" ||
		parsed.Path == "" || parsed.Path == "/" || parsed.Fragment != "" ||
		expectedCAPath == "" || !filepath.IsAbs(expectedCAPath) || filepath.Clean(expectedCAPath) != expectedCAPath ||
		len(query) != 2 || len(query["sslmode"]) != 1 || query.Get("sslmode") != "verify-full" ||
		len(query["sslrootcert"]) != 1 || query.Get("sslrootcert") != expectedCAPath ||
		parsed.RawQuery != query.Encode() ||
		!platformProcessSecretClean(value, 8192) {
		return nil, "", false
	}
	password, present := parsed.User.Password()
	if !present || !platformProcessDatabasePasswordValid(password) {
		return nil, "", false
	}
	target, targetErr := platformRelaySecretIsolationDatabaseTarget(parsed, "")
	if targetErr != nil {
		return nil, "", false
	}
	return []byte(password), target, true
}

func platformProcessSecretRoleAdminDSNFile(raw []byte) (platformRelaySecretIsolationFile, error) {
	value := string(raw)
	parsed, err := url.Parse(value)
	password, _, valid := platformProcessSecretDatabasePassword(value, "platform_role_admin")
	if err != nil || parsed.Path != "/postgres" || !valid {
		clear(password)
		return platformRelaySecretIsolationFile{}, errors.New("Platform role-admin database source is invalid")
	}
	targetDatabase := strings.TrimSpace(os.Getenv(platformProcessDatabaseNameEnvironment))
	if targetDatabase == "" || targetDatabase != os.Getenv(platformProcessDatabaseNameEnvironment) {
		clear(password)
		return platformRelaySecretIsolationFile{}, errors.New("Platform role-admin database source is invalid")
	}
	target, targetErr := platformRelaySecretIsolationDatabaseTarget(parsed, targetDatabase)
	endpoint, endpointErr := platformRelaySecretIsolationDatabaseEndpoint(parsed, targetDatabase)
	if targetErr != nil || endpointErr != nil {
		clear(password)
		return platformRelaySecretIsolationFile{}, errors.New("Platform role-admin database source is invalid")
	}
	file := platformRelaySecretIsolationFile{
		id: "platform_role_admin_dsn", raw: raw,
		representations: []platformRelaySecretIsolationRepresentation{
			platformRelaySecretIsolationDigest("platform.role_admin.database.password", password, ""),
			platformRelaySecretIsolationDigest("platform.role_admin.database.target", []byte(target), ""),
			platformRelaySecretIsolationDigest("platform.role_admin.database.endpoint", []byte(endpoint), ""),
		},
	}
	clear(password)
	return file, nil
}

func platformProcessSecretPasswordFile(
	raw []byte,
	contract platformProcessSecretRoleContract,
) (platformRelaySecretIsolationFile, error) {
	if contract.passwordFileID == "" || contract.passwordEnvironment == "" || !utf8.Valid(raw) ||
		!platformProcessDatabasePasswordValid(string(raw)) {
		return platformRelaySecretIsolationFile{}, errors.New("Platform database password source is invalid")
	}
	return platformRelaySecretIsolationFile{
		id: contract.passwordFileID, raw: raw,
		representations: []platformRelaySecretIsolationRepresentation{
			platformRelaySecretIsolationDigest(contract.prefix+".database.password_file", raw, ""),
		},
	}, nil
}

func platformProcessSecretHTTPSRoot(value string) bool {
	parsed, err := url.Parse(value)
	return err == nil && platformProcessSecretClean(value, 2048) && parsed.Scheme == "https" && parsed.Host != "" &&
		parsed.User == nil && parsed.Opaque == "" && parsed.RawQuery == "" && parsed.Fragment == ""
}

func platformProcessSecretAddPluginRepresentations(
	file *platformRelaySecretIsolationFile,
	prefix string,
	role string,
	plugins map[string]map[string]map[string]string,
) bool {
	expectedSections := map[string]struct{}{"adapters": {}}
	if role == "publishing-worker" {
		expectedSections["media_resolvers"] = struct{}{}
	}
	if len(plugins) != len(expectedSections) {
		return false
	}
	sections := make([]string, 0, len(plugins))
	for section := range plugins {
		if _, ok := expectedSections[section]; !ok {
			return false
		}
		sections = append(sections, section)
	}
	sort.Strings(sections)
	for _, section := range sections {
		entries := plugins[section]
		if len(entries) > 32 {
			return false
		}
		specs := make([]string, 0, len(entries))
		for spec := range entries {
			if len(spec) > 256 || !platformProcessPluginSpecPattern.MatchString(spec) {
				return false
			}
			specs = append(specs, spec)
		}
		sort.Strings(specs)
		for _, spec := range specs {
			credentials := entries[spec]
			if len(credentials) > 64 {
				return false
			}
			names := make([]string, 0, len(credentials))
			for name := range credentials {
				if !platformProcessPluginCredentialPattern.MatchString(name) {
					return false
				}
				names = append(names, name)
			}
			sort.Strings(names)
			for _, name := range names {
				value := credentials[name]
				if !platformProcessSecretValid(value) {
					return false
				}
				file.representations = append(file.representations,
					platformRelaySecretIsolationDigest(prefix+".publishing_plugin."+section+"."+spec+"."+name, []byte(value), ""))
			}
		}
	}
	return true
}

func platformProcessSecretParse(raw []byte, expectedRole string) (platformRelaySecretIsolationFile, platformProcessSecretDocument, error) {
	invalid := func() (platformRelaySecretIsolationFile, platformProcessSecretDocument, error) {
		return platformRelaySecretIsolationFile{}, platformProcessSecretDocument{}, errors.New("Platform process runtime secret file is invalid")
	}
	contract, ok := platformProcessSecretContract(expectedRole)
	if !ok || len(raw) == 0 || len(raw) > platformProcessSecretMaximumBytes || !utf8.Valid(raw) ||
		raw[0] != '{' || raw[len(raw)-1] != '}' || common.RejectDuplicateJSONKeys(raw) != nil ||
		!platformProcessSecretExactKeys(raw, []string{"kind", "schema_version", "process_role", "secrets"}) {
		return invalid()
	}
	var envelope platformProcessSecretEnvelope
	if platformProcessSecretDecode(raw, &envelope) != nil || envelope.Kind != platformProcessSecretKind ||
		envelope.SchemaVersion != platformProcessSecretSchemaVersion || envelope.ProcessRole != expectedRole ||
		!platformProcessSecretExactKeys(envelope.Secrets, contract.fields) {
		return invalid()
	}
	var values platformProcessSecretValues
	if platformProcessSecretDecode(envelope.Secrets, &values) != nil {
		return invalid()
	}
	file := platformRelaySecretIsolationFile{id: contract.fileID, raw: raw}
	add := func(id, value string) bool {
		if !platformProcessSecretValid(value) {
			return false
		}
		file.representations = append(file.representations,
			platformRelaySecretIsolationDigest(contract.prefix+"."+id, []byte(value), ""))
		return true
	}
	databasePassword, databaseTarget, validDatabase := platformProcessSecretDatabasePassword(values.DatabaseURL, contract.databaseUser)
	if !validDatabase {
		clear(databasePassword)
		return invalid()
	}
	databaseURL, parseDatabaseErr := url.Parse(values.DatabaseURL)
	databaseEndpoint, endpointErr := platformRelaySecretIsolationDatabaseEndpoint(databaseURL, "")
	if parseDatabaseErr != nil || endpointErr != nil {
		clear(databasePassword)
		return invalid()
	}
	file.representations = append(file.representations,
		platformRelaySecretIsolationDigest(contract.prefix+".database.password", databasePassword, ""),
		platformRelaySecretIsolationDigest(contract.prefix+".database.target", []byte(databaseTarget), ""),
		platformRelaySecretIsolationDigest(contract.prefix+".database.endpoint", []byte(databaseEndpoint), ""))
	clear(databasePassword)

	relayRole := expectedRole == "platform-api" || expectedRole == "dispatcher" ||
		expectedRole == "relay-sync" || expectedRole == "timeout-worker"
	if relayRole && values.RelayBackends == nil {
		return invalid()
	}
	if values.RelayBackends != nil {
		if len(values.RelayBackends) != 1 {
			return invalid()
		}
		backendIDs := make([]string, 0, len(values.RelayBackends))
		for backendID := range values.RelayBackends {
			if !platformProcessBackendIDPattern.MatchString(backendID) {
				return invalid()
			}
			backendIDs = append(backendIDs, backendID)
		}
		sort.Strings(backendIDs)
		for _, backendID := range backendIDs {
			backend := values.RelayBackends[backendID]
			if backendID != "new-api-v1" || !platformProcessSecretHTTPSRoot(backend.BaseURL) ||
				!platformProcessClientIDPattern.MatchString(backend.ClientID) ||
				platformRelaySecretIsPlaceholder(backend.ClientID) || !platformProcessContractPattern.MatchString(backend.ContractRevision) ||
				!add("relay_backend."+backendID+".api_key", backend.APIKey) {
				return invalid()
			}
			file.representations = append(file.representations,
				platformRelaySecretIsolationDigest(contract.prefix+".relay_backend."+backendID+".base_url", []byte(backend.BaseURL), ""),
				platformRelaySecretIsolationDigest(contract.prefix+".relay_backend."+backendID+".contract_revision", []byte(backend.ContractRevision), ""),
			)
		}
	}

	if expectedRole == "platform-api" {
		parsedTenant, tenantErr := uuid.Parse(values.RelayTenantID)
		if tenantErr != nil || parsedTenant == uuid.Nil || parsedTenant.String() != values.RelayTenantID ||
			!platformProcessApprovalKeyIDPattern.MatchString(values.RelayReconciliationApprovalKeyID) ||
			!add("relay_operations_token", values.RelayOperationsToken) ||
			!add("relay_reconciliation_approval_secret", values.RelayReconciliationApprovalSecret) ||
			len(values.RelayCallbackSigningSecrets) == 0 || len(values.RelayCallbackSigningSecrets) > 32 {
			return invalid()
		}
		callbackIDs := make([]string, 0, len(values.RelayCallbackSigningSecrets))
		for backendID := range values.RelayCallbackSigningSecrets {
			if !platformProcessBackendIDPattern.MatchString(backendID) {
				return invalid()
			}
			callbackIDs = append(callbackIDs, backendID)
		}
		sort.Strings(callbackIDs)
		for _, backendID := range callbackIDs {
			if !add("callback."+backendID, values.RelayCallbackSigningSecrets[backendID]) {
				return invalid()
			}
		}
		for _, item := range []struct{ id, value string }{
			{"internal_service_token", values.InternalServiceToken},
			{"download_edge_completion_service_token", values.DownloadEdgeCompletionServiceToken},
			{"channel_cost_signing_secret", values.ChannelCostSigningSecret},
			{"relay_telemetry_signing_secret", values.RelayTelemetrySigningSecret},
			{"provider_alert_signing_secret", values.ProviderAlertSigningSecret},
			{"provider_alert_forward_signing_secret", values.ProviderAlertForwardSigningSecret},
			{"download_completion_edge_gateway_signing_secret", values.DownloadCompletionEdgeGatewaySigningSecret},
			{"download_completion_obs_access_log_signing_secret", values.DownloadCompletionOBSAccessLogSigningSecret},
			{"download_gateway_service_token", values.DownloadGatewayServiceToken},
			{"download_gateway_registration_signing_secret", values.DownloadGatewayRegistrationSigningSecret},
			{"jwt_signing_secret", values.JWTSigningSecret},
		} {
			if !add(item.id, item.value) {
				return invalid()
			}
		}
	}

	if expectedRole == "platform-api" || expectedRole == "dispatcher" {
		if !platformProcessSecretIdentityValid(values.HuaweiOBSAccessKeyID, 128) ||
			!add("obs.secret_access_key", values.HuaweiOBSSecretAccessKey) {
			return invalid()
		}
		file.representations = append(file.representations,
			platformRelaySecretIsolationDigest(contract.prefix+".obs.access_key_id", []byte(values.HuaweiOBSAccessKeyID), ""))
		if values.HuaweiOBSSecurityToken != nil && !add("obs.security_token", *values.HuaweiOBSSecurityToken) {
			return invalid()
		}
	}

	if expectedRole == "platform-api" || expectedRole == "download-gateway-registration-worker" {
		registrationURL := os.Getenv("DOWNLOAD_GATEWAY_REGISTRATION_URL")
		if registrationURL != os.Getenv(platformRelaySecretIsolationEdgeOriginEnvironment)+"/internal/v1/download-tickets" {
			return invalid()
		}
		file.representations = append(file.representations,
			platformRelaySecretIsolationDigest(contract.prefix+".download_gateway_registration_url", []byte(registrationURL), ""))
		if expectedRole == "download-gateway-registration-worker" &&
			(!add("download_gateway_service_token", values.DownloadGatewayServiceToken) ||
				!add("download_gateway_registration_signing_secret", values.DownloadGatewayRegistrationSigningSecret)) {
			return invalid()
		}
		decoded, decodeErr := base64.StdEncoding.Strict().DecodeString(values.DownloadGatewayAttemptEncryptionKeyBase64)
		distinct := make(map[byte]struct{}, len(decoded))
		for _, value := range decoded {
			distinct[value] = struct{}{}
		}
		knownSequence := make([]byte, 32)
		for index := range knownSequence {
			knownSequence[index] = byte(index)
		}
		if decodeErr != nil || len(decoded) != 32 || len(distinct) < 16 ||
			base64.StdEncoding.EncodeToString(decoded) != values.DownloadGatewayAttemptEncryptionKeyBase64 ||
			bytes.Equal(decoded, make([]byte, 32)) || bytes.Equal(decoded, knownSequence) {
			clear(decoded)
			clear(knownSequence)
			return invalid()
		}
		clear(knownSequence)
		file.representations = append(file.representations,
			platformRelaySecretIsolationDigest(contract.prefix+".download_gateway_attempt_encryption_key.encoded", []byte(values.DownloadGatewayAttemptEncryptionKeyBase64), ""),
			platformRelaySecretIsolationDigest(contract.prefix+".download_gateway_attempt_encryption_key.decoded", decoded, ""),
		)
		clear(decoded)
	}

	if expectedRole == "platform-api" || expectedRole == "publishing-worker" {
		if !platformProcessSecretAddPluginRepresentations(&file, contract.prefix, expectedRole, values.PublishingPluginCredentials) {
			return invalid()
		}
	}
	return file, platformProcessSecretDocument{contract: contract, values: values}, nil
}

func platformRelaySecretIsolationFileByID(files []platformRelaySecretIsolationFile, id string) ([]byte, bool) {
	for index := range files {
		if files[index].id == id {
			return files[index].raw, true
		}
	}
	return nil, false
}

func platformRelaySecretIsolationBindGroup(
	files []platformRelaySecretIsolationFile,
	group string,
	expectedCount int,
	members ...string,
) bool {
	if group == "" || expectedCount < 2 || len(members) != expectedCount {
		return false
	}
	for _, member := range members {
		found := false
		for fileIndex := range files {
			for representationIndex := range files[fileIndex].representations {
				representation := &files[fileIndex].representations[representationIndex]
				if representation.id != member {
					continue
				}
				if found || representation.equivalenceGroup != "" {
					return false
				}
				representation.equivalenceGroup = group
				representation.equivalenceCount = expectedCount
				found = true
			}
		}
		if !found {
			return false
		}
	}
	return true
}

// platformRelaySecretIsolationBindPlatformContracts proves the intentional
// equality surface between the seven Platform process bundles and Relay A/B/C.
// Everything not assigned to one exact-count group remains globally unique.
func platformRelaySecretIsolationBindPlatformContracts(files []platformRelaySecretIsolationFile) error {
	invalid := func() error { return errors.New("Relay and Platform secret binding is invalid") }
	if !platformRelaySecretIsolationBindGroup(
		files,
		"relay.database.target",
		4,
		"database.role_admin_dsn.target",
		"database.migration_dsn.target",
		"database.runtime_dsn.target",
		"database.edge_dsn.target",
	) {
		return invalid()
	}
	if !platformRelaySecretIsolationBindGroup(
		files,
		"relay.database.endpoint",
		4,
		"database.role_admin_dsn.endpoint",
		"database.migration_dsn.endpoint",
		"database.runtime_dsn.endpoint",
		"database.edge_dsn.endpoint",
	) {
		return invalid()
	}
	platformDatabaseTargets := []string{"platform.role_admin.database.target"}
	platformDatabaseEndpoints := []string{"platform.role_admin.database.endpoint"}
	for _, contract := range platformProcessSecretRoleContracts {
		platformDatabaseTargets = append(platformDatabaseTargets, contract.prefix+".database.target")
		platformDatabaseEndpoints = append(platformDatabaseEndpoints, contract.prefix+".database.endpoint")
	}
	if !platformRelaySecretIsolationBindGroup(
		files,
		"platform.database.target",
		len(platformDatabaseTargets),
		platformDatabaseTargets...,
	) {
		return invalid()
	}
	if !platformRelaySecretIsolationBindGroup(
		files,
		"platform.database.endpoint",
		len(platformDatabaseEndpoints),
		platformDatabaseEndpoints...,
	) {
		return invalid()
	}
	for _, contract := range platformProcessSecretRoleContracts {
		if !platformRelaySecretIsolationBindGroup(
			files,
			"platform.database."+contract.role+".password",
			2,
			contract.prefix+".database.password",
			contract.prefix+".database.password_file",
		) {
			return invalid()
		}
	}
	principalRaw, ok := platformRelaySecretIsolationFileByID(files, "service_principals")
	if !ok {
		return invalid()
	}
	principals, err := ParsePlatformRelayServicePrincipalsFile(principalRaw)
	if err != nil {
		return invalid()
	}
	apiRaw, ok := platformRelaySecretIsolationFileByID(files, "api_runtime")
	if !ok {
		return invalid()
	}
	relayAPI, err := ParsePlatformRelayAPIRuntimeSecretsFile(apiRaw)
	if err != nil || len(principals) != len(relayAPI.Clients) {
		return invalid()
	}
	for index := range principals {
		if principals[index].ClientID != relayAPI.Clients[index].ClientID ||
			principals[index].TenantID != relayAPI.Clients[index].TenantID {
			return invalid()
		}
	}
	edgeRaw, ok := platformRelaySecretIsolationFileByID(files, "edge_runtime")
	if !ok {
		return invalid()
	}
	relayEdge, err := ParsePlatformDownloadEdgeRuntimeSecretsFile(edgeRaw)
	if err != nil {
		return invalid()
	}

	platformDocuments := make(map[string]platformProcessSecretDocument, len(platformProcessSecretRoleContracts))
	for _, contract := range platformProcessSecretRoleContracts {
		raw, exists := platformRelaySecretIsolationFileByID(files, contract.fileID)
		if !exists {
			return invalid()
		}
		_, document, parseErr := platformProcessSecretParse(raw, contract.role)
		if parseErr != nil {
			return invalid()
		}
		platformDocuments[contract.role] = document
	}
	platformAPI := platformDocuments["platform-api"].values
	platformPublishingWorker := platformDocuments["publishing-worker"].values
	apiPublishingAdapters, apiAdaptersPresent := platformAPI.PublishingPluginCredentials["adapters"]
	workerPublishingAdapters, workerAdaptersPresent := platformPublishingWorker.PublishingPluginCredentials["adapters"]
	if !apiAdaptersPresent || !workerAdaptersPresent || len(apiPublishingAdapters) != len(workerPublishingAdapters) {
		return invalid()
	}
	for spec, apiCredentials := range apiPublishingAdapters {
		workerCredentials, present := workerPublishingAdapters[spec]
		if !present || len(apiCredentials) != len(workerCredentials) {
			return invalid()
		}
		for name := range apiCredentials {
			if _, present := workerCredentials[name]; !present || !platformRelaySecretIsolationBindGroup(
				files,
				"platform.publishing_plugin.adapters."+spec+"."+name,
				2,
				"platform.api.publishing_plugin.adapters."+spec+"."+name,
				"platform.publishing_worker.publishing_plugin.adapters."+spec+"."+name,
			) {
				return invalid()
			}
		}
	}

	relayClientIndex := make(map[string]int, len(relayAPI.Clients))
	for index, client := range relayAPI.Clients {
		if _, duplicate := relayClientIndex[client.ClientID]; duplicate {
			return invalid()
		}
		relayClientIndex[client.ClientID] = index
	}
	mappedRelayClientIDs := make(map[string]struct{})
	mappedRoles := []struct {
		role   string
		prefix string
	}{
		{"platform-api", "platform.api"},
		{"dispatcher", "platform.dispatcher"},
		{"relay-sync", "platform.relay_sync"},
		{"timeout-worker", "platform.timeout_worker"},
	}
	relayBackendURLMembers := make([]string, 0, len(mappedRoles))
	relayBackendRevisionMembers := make([]string, 0, len(mappedRoles))
	for _, item := range mappedRoles {
		values := platformDocuments[item.role].values
		backend, exists := values.RelayBackends["new-api-v1"]
		if !exists || backend.BaseURL != os.Getenv(platformRelaySecretIsolationRelayOriginEnvironment) ||
			backend.ContractRevision != os.Getenv(platformRelaySecretIsolationRelayContractEnvironment) {
			return invalid()
		}
		relayBackendURLMembers = append(relayBackendURLMembers, item.prefix+".relay_backend.new-api-v1.base_url")
		relayBackendRevisionMembers = append(relayBackendRevisionMembers, item.prefix+".relay_backend.new-api-v1.contract_revision")
		if _, duplicate := mappedRelayClientIDs[backend.ClientID]; duplicate {
			return invalid()
		}
		mappedRelayClientIDs[backend.ClientID] = struct{}{}
		index, exists := relayClientIndex[backend.ClientID]
		if !exists || relayAPI.Clients[index].TenantID != platformAPI.RelayTenantID {
			return invalid()
		}
		if item.role == "dispatcher" {
			if relayAPI.Clients[index].CallbackSigningSecret == "" {
				return invalid()
			}
		} else if relayAPI.Clients[index].CallbackSigningSecret != "" {
			return invalid()
		}
		if !platformRelaySecretIsolationBindGroup(files, "platform.new_api."+item.role+".api_key", 2,
			item.prefix+".relay_backend.new-api-v1.api_key", "api.client."+formatPlatformRelayIsolationIndex(index)+".api_key") {
			return invalid()
		}
	}
	if !platformRelaySecretIsolationBindGroup(
		files, "platform.new_api.base_url", len(relayBackendURLMembers), relayBackendURLMembers...,
	) || !platformRelaySecretIsolationBindGroup(
		files, "platform.new_api.contract_revision", len(relayBackendRevisionMembers), relayBackendRevisionMembers...,
	) {
		return invalid()
	}

	operationDigest := sha256.Sum256([]byte(platformAPI.RelayOperationsToken))
	operationIndex := -1
	for index, credential := range relayAPI.OperationsCredentials {
		if credential.TenantID == platformAPI.RelayTenantID && credential.TokenSHA256 == hex.EncodeToString(operationDigest[:]) {
			if operationIndex >= 0 {
				return invalid()
			}
			operationIndex = index
		}
	}
	if operationIndex < 0 || !platformRelaySecretIsolationBindGroup(files, "platform.operations_token", 2,
		"platform.api.relay_operations_token", "api.operations."+formatPlatformRelayIsolationIndex(operationIndex)+".token") {
		return invalid()
	}
	approvalIndex := -1
	for index, approval := range relayAPI.ReconciliationApprovalKeys {
		if approval.TenantID == platformAPI.RelayTenantID && approval.KeyID == platformAPI.RelayReconciliationApprovalKeyID {
			if approvalIndex >= 0 {
				return invalid()
			}
			approvalIndex = index
		}
	}
	if approvalIndex < 0 || !platformRelaySecretIsolationBindGroup(files, "platform.reconciliation_approval", 2,
		"platform.api.relay_reconciliation_approval_secret", "api.approval."+formatPlatformRelayIsolationIndex(approvalIndex)+".secret") {
		return invalid()
	}
	dispatcherBackend := platformDocuments["dispatcher"].values.RelayBackends["new-api-v1"]
	dispatcherIndex := relayClientIndex[dispatcherBackend.ClientID]
	if _, exists := platformAPI.RelayCallbackSigningSecrets["new-api-v1"]; !exists ||
		!platformRelaySecretIsolationBindGroup(files, "platform.callback.new_api", 2,
			"platform.api.callback.new-api-v1", "api.client."+formatPlatformRelayIsolationIndex(dispatcherIndex)+".callback") {
		return invalid()
	}

	for _, binding := range []struct {
		group string
		left  string
		right string
	}{
		{"platform.internal_service", "platform.api.internal_service_token", "api.platform_internal"},
		{"platform.channel_cost", "platform.api.channel_cost_signing_secret", "api.channel_cost"},
		{"platform.telemetry", "platform.api.relay_telemetry_signing_secret", "api.telemetry"},
		{"platform.provider_alert", "platform.api.provider_alert_signing_secret", "api.provider_alert"},
		{"platform.edge_completion_token", "platform.api.download_edge_completion_service_token", "edge.platform_completion_token"},
		{"platform.edge_completion_signing", "platform.api.download_completion_edge_gateway_signing_secret", "edge.completion_signing"},
		{"platform.gateway_attempt_key.encoded", "platform.api.download_gateway_attempt_encryption_key.encoded", "platform.download_gateway_worker.download_gateway_attempt_encryption_key.encoded"},
		{"platform.gateway_attempt_key.decoded", "platform.api.download_gateway_attempt_encryption_key.decoded", "platform.download_gateway_worker.download_gateway_attempt_encryption_key.decoded"},
	} {
		if !platformRelaySecretIsolationBindGroup(files, binding.group, 2, binding.left, binding.right) {
			return invalid()
		}
	}
	if !platformRelaySecretIsolationBindGroup(files, "platform.download_gateway.registration_url", 2,
		"platform.api.download_gateway_registration_url",
		"platform.download_gateway_worker.download_gateway_registration_url",
	) {
		return invalid()
	}
	for _, binding := range []struct {
		group   string
		members []string
	}{
		{"platform.gateway.registration_token", []string{
			"edge.registration_token", "platform.api.download_gateway_service_token",
			"platform.download_gateway_worker.download_gateway_service_token",
		}},
		{"platform.gateway.registration_signing", []string{
			"edge.registration_signing", "platform.api.download_gateway_registration_signing_secret",
			"platform.download_gateway_worker.download_gateway_registration_signing_secret",
		}},
	} {
		if !platformRelaySecretIsolationBindGroup(files, binding.group, 3, binding.members...) {
			return invalid()
		}
	}
	_ = relayEdge // The parsed C document is deliberately part of the exact binding proof.
	return nil
}

func formatPlatformRelayIsolationIndex(index int) string {
	return fmt.Sprintf("%03d", index)
}
