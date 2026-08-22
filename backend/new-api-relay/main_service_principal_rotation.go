package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
)

const (
	platformRelayPrincipalRotationIsolationCommand = "relay-validate-service-principal-rotation-secret-isolation-v1"
	platformRelayPrincipalRotationCommand          = "relay-rotate-service-principals-v1"
)

const platformRelayPrincipalRotationLifecycleLockTimeout = 10 * time.Second

type platformRelayPrincipalRotationIsolationOutput struct {
	SchemaVersion int    `json:"schema_version"`
	Kind          string `json:"kind"`
	State         string `json:"state"`
}

type platformRelayServicePrincipalRotationOutput struct {
	SchemaVersion       int    `json:"schema_version"`
	Kind                string `json:"kind"`
	CredentialOperation string `json:"credential_operation"`
	IdentitySet         string `json:"identity_set"`
	State               string `json:"state"`
	AttemptID           string `json:"attempt_id"`
	Count               int    `json:"count"`
	RotatedCount        int    `json:"rotated_count"`
}

func rejectPlatformRelayPrincipalRotationEnvironment(names []string, message string) error {
	forbidden := make(map[string]struct{}, len(names))
	for _, name := range names {
		forbidden[strings.ToUpper(name)] = struct{}{}
	}
	for _, entry := range os.Environ() {
		name, _, found := strings.Cut(entry, "=")
		if !found {
			continue
		}
		if _, present := forbidden[strings.ToUpper(name)]; present {
			return errors.New(message)
		}
	}
	return nil
}

func validatePlatformRelayPrincipalRotationNoRawSecrets() error {
	if common.ProtectedRawSecretEnvironmentPresent(os.Environ()) {
		return errors.New("Relay service principal rotation requires file-only secret sources")
	}
	return nil
}

func validatePlatformRelayPrincipalRotationLeastPrivilegeFiles() error {
	if err := rejectPlatformRelayPrincipalRotationEnvironment([]string{
		"RELAY_PROVISION_ROOT_PASSWORD_FILE",
		"RELAY_MIGRATION_DATABASE_PASSWORD_FILE",
		"RELAY_RUNTIME_DATABASE_PASSWORD_FILE",
		"RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE",
		"RELAY_API_RUNTIME_SECRETS_FILE",
		"RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE",
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE",
		"RELAY_DOWNLOAD_EDGE_SQL_DSN_FILE",
		"RELAY_SECRET_ISOLATION_ROLE_ADMIN_SQL_DSN_FILE",
		"RELAY_SECRET_ISOLATION_MIGRATION_SQL_DSN_FILE",
		"RELAY_SECRET_ISOLATION_RUNTIME_SQL_DSN_FILE",
		"RELAY_SECRET_ISOLATION_EDGE_SQL_DSN_FILE",
		"PLATFORM_DATABASE_ROLE_ADMIN_DSN_FILE",
		"PLATFORM_MIGRATION_DATABASE_PASSWORD_FILE",
		"PLATFORM_API_DATABASE_PASSWORD_FILE",
		"PLATFORM_DISPATCHER_DATABASE_PASSWORD_FILE",
		"PLATFORM_RELAY_SYNC_DATABASE_PASSWORD_FILE",
		"PLATFORM_TIMEOUT_WORKER_DATABASE_PASSWORD_FILE",
		"PLATFORM_PUBLISHING_WORKER_DATABASE_PASSWORD_FILE",
		"PLATFORM_DOWNLOAD_GATEWAY_WORKER_DATABASE_PASSWORD_FILE",
		"PLATFORM_PROCESS_RUNTIME_SECRETS_FILE",
		"PLATFORM_MIGRATION_RUNTIME_SECRETS_FILE",
		"PLATFORM_API_RUNTIME_SECRETS_FILE",
		"PLATFORM_DISPATCHER_RUNTIME_SECRETS_FILE",
		"PLATFORM_RELAY_SYNC_RUNTIME_SECRETS_FILE",
		"PLATFORM_TIMEOUT_WORKER_RUNTIME_SECRETS_FILE",
		"PLATFORM_PUBLISHING_WORKER_RUNTIME_SECRETS_FILE",
		"PLATFORM_DOWNLOAD_GATEWAY_WORKER_RUNTIME_SECRETS_FILE",
	}, "Relay service principal rotation received an unrelated secret file"); err != nil {
		return err
	}
	allowedFiles := map[string]struct{}{
		"SQL_DSN_FILE":           {},
		"RELAY_DATABASE_CA_FILE": {},
		service.PlatformRelayCurrentServicePrincipalsFileEnvironment: {},
		"RELAY_SERVICE_PRINCIPALS_FILE":                              {},
		"RELAY_SECRET_ISOLATION_RECEIPT_FILE":                        {},
		service.RelayDatabaseReleaseProofFileEnvironment:             {},
	}
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		upperName := strings.ToUpper(name)
		if strings.HasPrefix(upperName, "RELAY_SECRET_ISOLATION_RECEIPT_") &&
			name != "RELAY_SECRET_ISOLATION_RECEIPT_FILE" {
			return errors.New("Relay service principal rotation received an unrelated receipt source")
		}
		_, allowed := allowedFiles[name]
		if !allowed && strings.HasSuffix(upperName, "_FILE") &&
			(strings.HasPrefix(upperName, "RELAY_") || strings.HasPrefix(upperName, "PLATFORM_") ||
				strings.HasPrefix(upperName, "NEW_API_")) {
			return errors.New("Relay service principal rotation received an unrelated secret file")
		}
	}
	return nil
}

func runPlatformRelayPrincipalRotationSecretIsolation(output io.Writer) error {
	if err := validatePlatformRelayProtectedBootstrapEnvironment(platformRelayPrincipalRotationIsolationCommand); err != nil {
		return err
	}
	if !strings.EqualFold(strings.TrimSpace(os.Getenv("NODE_TYPE")), "master") {
		return errors.New("Relay service principal rotation isolation requires NODE_TYPE=master")
	}
	if err := validatePlatformRelayPrincipalRotationNoRawSecrets(); err != nil {
		return err
	}
	if err := validatePlatformRelayOfflineBuildProvenance(); err != nil {
		return err
	}
	if err := service.ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation(); err != nil {
		return err
	}
	return json.NewEncoder(output).Encode(platformRelayPrincipalRotationIsolationOutput{
		SchemaVersion: service.PlatformRelayServicePrincipalRotationSchemaVersion,
		Kind:          "relay_service_principal_rotation_secret_isolation",
		State:         "validated",
	})
}

func clearPlatformRelayPrincipalRotationInputs(inputs *service.PlatformRelayServicePrincipalRotationInputs) {
	if inputs == nil {
		return
	}
	for index := range inputs.Current {
		inputs.Current[index].UpstreamToken = ""
	}
	for index := range inputs.Desired {
		inputs.Desired[index].UpstreamToken = ""
	}
	inputs.AttemptID = ""
	inputs.Current = nil
	inputs.Desired = nil
}

func acquirePlatformRelayPrincipalRotationLifecycleLock() (*model.RelayLifecycleLock, error) {
	ctx, cancel := context.WithTimeout(
		context.Background(),
		platformRelayPrincipalRotationLifecycleLockTimeout,
	)
	defer cancel()
	lock, err := model.AcquireRelayLifecycleLock(ctx, model.DB)
	if err != nil {
		return nil, errors.New("Relay service principal rotation lifecycle gate is unavailable")
	}
	return lock, nil
}

func writePlatformRelayServicePrincipalRotationOutput(
	output io.Writer,
	result service.PlatformRelayServicePrincipalRotationResult,
) error {
	return json.NewEncoder(output).Encode(platformRelayServicePrincipalRotationOutput{
		SchemaVersion:       service.PlatformRelayServicePrincipalRotationSchemaVersion,
		Kind:                "relay_service_principal_rotation",
		CredentialOperation: "token_rotation_only",
		IdentitySet:         "immutable",
		State:               result.State,
		AttemptID:           result.AttemptID,
		Count:               result.Count,
		RotatedCount:        result.RotatedCount,
	})
}

func runPlatformRelayServicePrincipalRotation(output io.Writer) error {
	if err := validatePlatformRelayProtectedBootstrapEnvironment(platformRelayPrincipalRotationCommand); err != nil {
		return err
	}
	if err := validatePlatformRelayPrincipalRotationNoRawSecrets(); err != nil {
		return err
	}
	if err := validatePlatformRelayPrincipalRotationLeastPrivilegeFiles(); err != nil {
		return err
	}
	inputs, err := service.VerifyPlatformRelayPrincipalRotationSecretIsolationReceipt()
	if err != nil {
		return errors.New("Relay service principal rotation isolation commitment is unavailable or invalid")
	}
	defer clearPlatformRelayPrincipalRotationInputs(&inputs)
	if err := validatePlatformRelaySchemaCommandEnvironment(platformRelayPrincipalRotationCommand); err != nil {
		return err
	}
	common.IsMasterNode = true
	if err := model.InitDB(); err != nil {
		return errors.New("Relay service principal rotation database could not be opened")
	}
	sqlDB, err := model.DB.DB()
	if err != nil {
		_ = model.CloseDB()
		return errors.New("Relay service principal rotation database handle is unavailable")
	}
	databaseOpen := true
	defer func() {
		if databaseOpen {
			_ = sqlDB.Close()
		}
	}()
	if common.MainDatabaseType() != common.DatabaseTypePostgreSQL {
		return errors.New("Relay service principal rotation requires PostgreSQL")
	}
	lifecycleLock, err := acquirePlatformRelayPrincipalRotationLifecycleLock()
	if err != nil {
		return err
	}
	lockOpen := true
	defer func() {
		if lockOpen {
			lifecycleLock.Close()
		}
	}()
	if _, err := model.RequireRelaySchemaCurrent(model.DB); err != nil {
		return errors.New("Relay service principal rotation requires the current Relay schema")
	}
	if err := model.VerifyRelayRuntimeDatabaseRole(model.DB); err != nil {
		return err
	}
	if err := service.VerifyPlatformRelayPrincipalRotationDatabaseReleaseProof(
		model.DB,
		inputs.AttemptID,
	); err != nil {
		return err
	}
	result, err := service.RotateProtectedPlatformRelayServicePrincipals(
		inputs.AttemptID, inputs.Current, inputs.Desired,
	)
	if err != nil {
		return err
	}
	if err := model.ReleaseRelayLifecycleLockBounded(lifecycleLock); err != nil {
		return err
	}
	lockOpen = false
	if err := sqlDB.Close(); err != nil {
		return errors.New("Relay service principal rotation database could not be closed cleanly")
	}
	databaseOpen = false
	return writePlatformRelayServicePrincipalRotationOutput(output, result)
}
