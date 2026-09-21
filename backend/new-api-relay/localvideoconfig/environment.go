package localvideoconfig

import (
	"errors"

	"github.com/QuantumNous/new-api/common"
)

func (c Config) RoutesJSON() (string, error) {
	if err := Validate(c); err != nil {
		return "", err
	}
	encoded, err := common.Marshal(c.Routes)
	if err != nil {
		return "", errors.New("local video routes cannot be encoded")
	}
	return string(encoded), nil
}

// ClientCredentialsJSON contains runtime credentials, despite the public Config
// itself being redacted. It is only for the server process/private environment.
func (c Config) ClientCredentialsJSON() (string, error) {
	if err := Validate(c); err != nil {
		return "", err
	}
	encoded, err := common.Marshal(map[string]map[string]string{c.Principal.ClientID: {
		"tenant_id": c.Principal.TenantID, "api_key": c.Principal.APIKey, "upstream_token": c.Principal.UpstreamToken,
		"callback_url": c.Principal.CallbackURL, "callback_signing_secret": c.Principal.CallbackSigningSecret,
	}})
	if err != nil {
		return "", errors.New("local video client credentials cannot be encoded")
	}
	return string(encoded), nil
}

// RuntimeEnvironment returns secret-bearing values for an isolated development
// process. NEVER print or expose this map to a browser. A CLI may write it to a
// server-owned private file for the Platform launcher to consume. It sets no
// database, storage-directory, TLS, proxy, artifact DNS or provider transport
// override: those remain the isolated entry point's responsibility.
// The caller must refuse production/attested/ambient credentials BEFORE using
// this map; this pure function neither reads nor overwrites process state.
func (c Config) RuntimeEnvironment() (map[string]string, error) {
	if err := Validate(c); err != nil {
		return nil, err
	}
	routes, err := c.RoutesJSON()
	if err != nil {
		return nil, err
	}
	clients, err := c.ClientCredentialsJSON()
	if err != nil {
		return nil, err
	}
	operations, err := common.Marshal([]map[string]string{{"tenant_id": c.Principal.TenantID, "token_sha256": sha256Hex([]byte(c.Principal.OperationsToken))}})
	if err != nil {
		return nil, errors.New("local video operations credentials cannot be encoded")
	}
	approvalKeys, err := common.Marshal([]map[string]string{{"tenant_id": c.Principal.TenantID, "key_id": "local-video-reconciliation-v1", "secret": c.Principal.ReconciliationSecret}})
	if err != nil {
		return nil, errors.New("local video reconciliation credentials cannot be encoded")
	}
	return map[string]string{
		"APP_ENV": EnvironmentDevelopment, "DEPLOYMENT_ENV": EnvironmentDevelopment, "ENVIRONMENT": EnvironmentDevelopment,
		"RELAY_COMPAT_ENABLED": "true", "RELAY_COMPAT_ENVIRONMENT": EnvironmentDevelopment,
		"RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED": "false",
		"RELAY_COMPAT_MODEL_ROUTES_JSON":           routes, "RELAY_COMPAT_MODEL_CAPABILITIES_JSON": "",
		"RELAY_COMPAT_CLIENT_CREDENTIALS_JSON": clients, "RELAY_COMPAT_WORKER_ENABLED": "true",
		"RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN":    c.Principal.InternalAdmissionToken,
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON": string(operations), "RELAY_PLATFORM_CONTROL_TENANT_ID": c.Principal.TenantID,
		"RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON": string(approvalKeys),
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON":         c.Principal.CredentialKeyringJSON,
		"RELAY_COMPAT_DELAY_QUEUE_NAMESPACE":             "local-video-lab-" + c.StateID,
		"RELAY_NATIVE_PAID_COMPAT_ENABLED":               "false", "RELAY_PROVIDER_MONITOR_ENABLED": "false", "CHANNEL_TEST_ENABLED": "false",
		"RELAY_ARTIFACT_STORE": "filesystem", "RELAY_ARTIFACT_SIGNING_SECRET": c.Principal.ArtifactSigningSecret,
		"PLATFORM_LAB_RELAY_CLIENT_ID": c.Principal.ClientID, "PLATFORM_LAB_RELAY_TENANT_ID": c.Principal.TenantID,
		"PLATFORM_LAB_RELAY_API_KEY": c.Principal.APIKey, "PLATFORM_LAB_RELAY_UPSTREAM_TOKEN": c.Principal.UpstreamToken,
		"PLATFORM_LAB_RELAY_INTERNAL_ADMISSION_TOKEN": c.Principal.InternalAdmissionToken,
		"PLATFORM_LAB_RELAY_OPERATIONS_TOKEN":         c.Principal.OperationsToken,
		"PLATFORM_LAB_RELAY_CALLBACK_SIGNING_SECRET":  c.Principal.CallbackSigningSecret,
		"PLATFORM_LAB_RELAY_RECONCILIATION_KEY_ID":    "local-video-reconciliation-v1",
		"PLATFORM_LAB_RELAY_RECONCILIATION_SECRET":    c.Principal.ReconciliationSecret,
		"PLATFORM_LAB_RELAY_MODE":                     c.Mode, "PLATFORM_LAB_RELAY_STATE_ID": c.StateID,
	}, nil
}
