package common

import "strings"

// ProtectedPlatformRawSecretEnvironmentNamesV1 mirrors the Platform loader's
// RAW_PLATFORM_SECRET_ENVIRONMENTS contract. Keep this explicit manifest in
// sync with backend/platform/platform_api/process_secrets.py; both sides reject
// names case-insensitively and reject present-even-empty values.
func ProtectedPlatformRawSecretEnvironmentNamesV1() []string {
	return []string{
		"DATABASE_URL",
		"PLATFORM_DATABASE_URL",
		"PLATFORM_DATABASE_ROLE_ADMIN_DSN",
		"PLATFORM_MIGRATION_DATABASE_PASSWORD",
		"PLATFORM_API_DATABASE_PASSWORD",
		"PLATFORM_DISPATCHER_DATABASE_PASSWORD",
		"PLATFORM_RELAY_SYNC_DATABASE_PASSWORD",
		"PLATFORM_TIMEOUT_WORKER_DATABASE_PASSWORD",
		"PLATFORM_PUBLISHING_WORKER_DATABASE_PASSWORD",
		"PLATFORM_DOWNLOAD_GATEWAY_WORKER_DATABASE_PASSWORD",
		"HTTP_PROXY",
		"HTTPS_PROXY",
		"ALL_PROXY",
		"NO_PROXY",
		"SSL_CERT_FILE",
		"SSL_CERT_DIR",
		"REQUESTS_CA_BUNDLE",
		"CURL_CA_BUNDLE",
		"RELAY_BACKENDS",
		"RELAY_BASE_URL",
		"RELAY_CLIENT_ID",
		"RELAY_API_KEY",
		"RELAY_LEGACY_COMPATIBILITY_ENABLED",
		"RELAY_ALLOW_LEGACY_ARTIFACT_DOWNLOAD_RESPONSE",
		"RELAY_TENANT_ID",
		"RELAY_OPERATIONS_TOKEN",
		"RELAY_RECONCILIATION_APPROVAL_KEY_ID",
		"RELAY_RECONCILIATION_APPROVAL_SECRET",
		"RELAY_CALLBACK_SIGNING_SECRET",
		"RELAY_CALLBACK_SIGNING_SECRETS",
		"INTERNAL_SERVICE_TOKEN",
		"DOWNLOAD_EDGE_COMPLETION_SERVICE_TOKEN",
		"CHANNEL_COST_SIGNING_SECRET",
		"RELAY_TELEMETRY_SIGNING_SECRET",
		"PROVIDER_ALERT_SIGNING_SECRET",
		"PROVIDER_ALERT_FORWARD_SIGNING_SECRET",
		"DOWNLOAD_COMPLETION_EDGE_GATEWAY_SIGNING_SECRET",
		"DOWNLOAD_COMPLETION_OBS_ACCESS_LOG_SIGNING_SECRET",
		"DOWNLOAD_GATEWAY_SERVICE_TOKEN",
		"DOWNLOAD_GATEWAY_REGISTRATION_SIGNING_SECRET",
		"DOWNLOAD_GATEWAY_ATTEMPT_ENCRYPTION_KEY_BASE64",
		"JWT_SIGNING_SECRET",
		"INPUT_ASSET_SIGNING_SECRET",
		"HUAWEI_OBS_ACCESS_KEY_ID",
		"HUAWEI_OBS_SECRET_ACCESS_KEY",
		"HUAWEI_OBS_SECURITY_TOKEN",
		"PUBLISHING_PLUGIN_CREDENTIALS",
		"BOOTSTRAP_TOKEN",
	}
}

// ProtectedRelayRawSecretEnvironmentNamesV1 is the Relay-side
// present-even-empty legacy/raw credential manifest used by the rotation job.
// It includes the current upstream aliases as well as the protected deployment
// compatibility names so an accidental injection cannot remain visible via
// argv, /proc, or container inspection while an offline command reports green.
func ProtectedRelayRawSecretEnvironmentNamesV1() []string {
	return []string{
		"SQL_DSN",
		"LOG_SQL_DSN",
		"LOG_SQL_DSN_FILE",
		"REDIS_CONN_STRING",
		"SESSION_SECRET",
		"CRYPTO_SECRET",
		"JWT_SECRET",
		"TOKEN_SECRET",
		"PAYLOAD_SECRET",
		"RELAY_CURRENT_SERVICE_PRINCIPALS",
		"RELAY_CURRENT_SERVICE_PRINCIPALS_JSON",
		"RELAY_SERVICE_PRINCIPALS",
		"RELAY_SERVICE_PRINCIPALS_JSON",
		"RELAY_COMPAT_CLIENT_CREDENTIALS_JSON",
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		"RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON",
		"RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN",
		"RELAY_COMPAT_INTERNAL_BASE_URL",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY",
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON",
		"RELAY_ARTIFACT_SIGNING_SECRET",
		"RELAY_PROVIDER_ALERT_SIGNING_SECRET",
		"RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN",
		"RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET",
		"RELAY_TELEMETRY_SIGNING_SECRET",
		"RELAY_DOWNLOAD_EDGE_REGISTRATION_TOKEN",
		"RELAY_DOWNLOAD_EDGE_REGISTRATION_SIGNING_SECRET",
		"RELAY_DOWNLOAD_EDGE_TICKET_TOKEN_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_SOURCE_ENCRYPTION_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_PLATFORM_INTERNAL_TOKEN",
		"RELAY_DOWNLOAD_EDGE_COMPLETION_SIGNING_SECRET",
		"RELAY_DOWNLOAD_EDGE_PROOF_PRIVATE_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_PROOF_READ_TOKEN",
		"RELAY_PROVISION_ROOT_PASSWORD",
		"NEW_API_RELAY_ROOT_PASSWORD",
		"RELAY_MIGRATION_DATABASE_PASSWORD",
		"NEW_API_RELAY_MIGRATION_DATABASE_PASSWORD",
		"RELAY_RUNTIME_DATABASE_PASSWORD",
		"NEW_API_RELAY_RUNTIME_DATABASE_PASSWORD",
		"RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD",
		"RELAY_DOWNLOAD_EDGE_POSTGRES_PASSWORD",
		"RELAY_DOWNLOAD_EDGE_SQL_DSN",
		"NEW_API_RELAY_HUAWEI_OBS_ACCESS_KEY_ID",
		"NEW_API_RELAY_HUAWEI_OBS_SECRET_ACCESS_KEY",
	}
}

// ProtectedRawSecretEnvironmentPresent performs ASCII case-folded name
// matching over the process environment. Values are intentionally ignored:
// present-even-empty is forbidden for every manifest entry.
func ProtectedRawSecretEnvironmentPresent(environment []string) bool {
	forbidden := make(map[string]struct{})
	for _, names := range [][]string{
		ProtectedPlatformRawSecretEnvironmentNamesV1(),
		ProtectedRelayRawSecretEnvironmentNamesV1(),
	} {
		for _, name := range names {
			forbidden[strings.ToUpper(name)] = struct{}{}
		}
	}
	for _, entry := range environment {
		name, _, found := strings.Cut(entry, "=")
		if !found {
			continue
		}
		upperName := strings.ToUpper(name)
		// libpq accepts credentials, TLS client material, service-file paths,
		// connection targets, and command-line options through a broad and
		// extensible PG* environment namespace. A prefix rule fails closed for
		// current and future libpq variables instead of chasing an enumeration.
		if strings.HasPrefix(upperName, "PG") {
			return true
		}
		if _, blocked := forbidden[upperName]; blocked {
			return true
		}
	}
	return false
}
