package main

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"strings"

	"github.com/QuantumNous/new-api/service"
)

type platformRelayRootSecretIsolationOutput struct {
	SchemaVersion int    `json:"schema_version"`
	Kind          string `json:"kind"`
	State         string `json:"state"`
}

func validatePlatformRelayRootProvisionExecutionEnvironment() error {
	if err := validatePlatformRelayProtectedBootstrapEnvironment("relay-provision-root"); err != nil {
		return err
	}
	if os.Getenv("NODE_TYPE") != "master" {
		return errors.New("relay-provision-root requires NODE_TYPE=master")
	}
	allowedFiles := map[string]struct{}{
		"SQL_DSN_FILE":           {},
		"RELAY_DATABASE_CA_FILE": {},
		service.PlatformRelayRootPasswordFileEnvironment: {},
		service.PlatformRelayRootProofFileEnvironment:    {},
		service.RelayDatabaseReleaseProofFileEnvironment: {},
		"RELAY_SECRET_ISOLATION_RECEIPT_FILE":            {},
	}
	for _, entry := range os.Environ() {
		name, _, found := strings.Cut(entry, "=")
		if !found {
			continue
		}
		upperName := strings.ToUpper(name)
		if strings.HasPrefix(upperName, "RELAY_SECRET_ISOLATION_RECEIPT_") &&
			upperName != "RELAY_SECRET_ISOLATION_RECEIPT_FILE" {
			return errors.New("Relay root provisioner received an unrelated receipt source")
		}
		if _, allowed := allowedFiles[upperName]; allowed {
			continue
		}
		if strings.HasSuffix(upperName, "_FILE") &&
			(strings.HasPrefix(upperName, "RELAY_") ||
				strings.HasPrefix(upperName, "PLATFORM_") ||
				strings.HasPrefix(upperName, "NEW_API_") ||
				strings.HasPrefix(upperName, "SQL_") ||
				strings.HasPrefix(upperName, "LOG_")) {
			return errors.New("Relay root provisioner received an unrelated secret file")
		}
	}
	return nil
}

func runPlatformRelayRootSecretIsolation(output io.Writer) error {
	if err := validatePlatformRelayProtectedBootstrapEnvironment("relay-validate-root-secret-isolation-v1"); err != nil {
		return err
	}
	if os.Getenv("NODE_TYPE") != "master" {
		return errors.New("relay-validate-root-secret-isolation-v1 requires NODE_TYPE=master")
	}
	if err := validatePlatformRelayOfflineBuildProvenance(); err != nil {
		return err
	}
	if err := service.ValidateAndCommitPlatformRelayRootSecretIsolation(); err != nil {
		return err
	}
	return json.NewEncoder(output).Encode(platformRelayRootSecretIsolationOutput{
		SchemaVersion: 1,
		Kind:          "relay_root_secret_isolation",
		State:         "validated",
	})
}
