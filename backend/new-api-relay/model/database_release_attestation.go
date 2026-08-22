package model

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
	"gorm.io/gorm"
)

const relayPostgres16PGAuditExtensionVersion = "16.1"

// RelayDatabaseReleaseIdentity contains only non-secret database-server facts.
// A privileged predecessor records the settings ordinary roles cannot inspect;
// every later database consumer compares the restart/config timestamps and the
// security-relevant catalog fingerprint against its own connection.
type RelayDatabaseReleaseIdentity struct {
	Environment                  string `json:"environment"`
	PostmasterStartTime          string `json:"postmaster_start_time"`
	ConfigLoadTime               string `json:"config_load_time"`
	SystemSemanticSHA256         string `json:"system_semantic_sha256"`
	PGAuditExtensionExact        bool   `json:"pgaudit_extension_exact"`
	PGAuditSharedPreload         bool   `json:"pgaudit_shared_preload"`
	SharedPreloadManifest        string `json:"shared_preload_manifest"`
	SessionPreloadEmpty          bool   `json:"session_preload_empty"`
	PGAuditLogClassCoverage      bool   `json:"pgaudit_log_class_coverage"`
	CredentialLoggingPolicyExact bool   `json:"credential_logging_policy_exact"`
}

type relayDatabaseReleaseSettings struct {
	PostmasterStartTime              time.Time `gorm:"column:postmaster_start_time"`
	ConfigLoadTime                   time.Time `gorm:"column:config_load_time"`
	SharedPreloadLibraries           string    `gorm:"column:shared_preload_libraries"`
	SessionPreloadLibraries          string    `gorm:"column:session_preload_libraries"`
	LocalPreloadLibraries            string    `gorm:"column:local_preload_libraries"`
	PGAuditLog                       string    `gorm:"column:pgaudit_log"`
	PGAuditLogParameter              string    `gorm:"column:pgaudit_log_parameter"`
	LogParameterMaxLength            int       `gorm:"column:log_parameter_max_length"`
	LogParameterMaxLengthOnError     int       `gorm:"column:log_parameter_max_length_on_error"`
	AutoExplainLogParameterMaxLength int       `gorm:"column:auto_explain_log_parameter_max_length"`
}

var relayDatabaseReleaseIdentityState = struct {
	sync.RWMutex
	installed *RelayDatabaseReleaseIdentity
}{}

func relayProtectedDatabaseEnvironment() (string, bool, error) {
	if !RelayDatabaseRoleAttestationRequired() {
		return "", false, nil
	}
	appEnvironment := os.Getenv("APP_ENV")
	deploymentEnvironment := os.Getenv("DEPLOYMENT_ENV")
	if appEnvironment != deploymentEnvironment ||
		(appEnvironment != "staging" && appEnvironment != "production") {
		return "", true, errors.New("Relay protected database environment is not exact")
	}
	if value, present := os.LookupEnv(relayDatabaseTLSAttestationEnvironment); !present || value != "true" {
		return "", true, errors.New("Relay protected database TLS policy is not exact")
	}
	return appEnvironment, true, nil
}

func relayDatabaseSettingTokens(value string) (map[string]struct{}, bool) {
	result := make(map[string]struct{})
	if strings.TrimSpace(value) == "" {
		return result, true
	}
	for _, raw := range strings.Split(value, ",") {
		token := strings.ToLower(strings.TrimSpace(raw))
		if token == "" {
			return nil, false
		}
		if _, duplicate := result[token]; duplicate {
			return nil, false
		}
		result[token] = struct{}{}
	}
	return result, true
}

func relayDatabaseSettingContains(value, expected string) bool {
	tokens, valid := relayDatabaseSettingTokens(value)
	if !valid {
		return false
	}
	_, present := tokens[expected]
	return present
}

func relayDatabaseSharedPreloadManifest(value string) (string, bool) {
	tokens, valid := relayDatabaseSettingTokens(value)
	if !valid {
		return "", false
	}
	ordered := make([]string, 0, len(tokens))
	for _, raw := range strings.Split(value, ",") {
		token := strings.ToLower(strings.TrimSpace(raw))
		for _, character := range token {
			if !((character >= 'a' && character <= 'z') ||
				(character >= '0' && character <= '9') || character == '_') {
				return "", false
			}
		}
		if token != "" {
			ordered = append(ordered, token)
		}
	}
	sort.Strings(ordered)
	return strings.Join(ordered, ","), true
}

func relayDatabasePGAuditLogCoverage(value string) bool {
	for _, character := range value {
		if character > 0x7f {
			return false
		}
	}
	tokens := strings.Split(value, ",")
	if len(tokens) == 0 {
		return false
	}
	allClasses := map[string]struct{}{
		"read": {}, "write": {}, "function": {}, "role": {},
		"ddl": {}, "misc": {}, "misc_set": {},
	}
	covered := make(map[string]struct{}, len(allClasses))
	for _, raw := range tokens {
		token := strings.ToLower(strings.TrimSpace(raw))
		if token == "" {
			return false
		}
		remove := strings.HasPrefix(token, "-")
		name := strings.TrimPrefix(token, "-")
		if name == "none" {
			if remove {
				return false
			}
			clear(covered)
			continue
		}
		classes := make(map[string]struct{})
		if name == "all" {
			for class := range allClasses {
				classes[class] = struct{}{}
			}
		} else if _, valid := allClasses[name]; valid {
			classes[name] = struct{}{}
		} else {
			return false
		}
		for class := range classes {
			if remove {
				delete(covered, class)
			} else {
				covered[class] = struct{}{}
			}
		}
	}
	for _, required := range []string{"ddl", "role", "write"} {
		if _, present := covered[required]; !present {
			return false
		}
	}
	return true
}

func inspectRelayDatabaseReleaseSettings(db *gorm.DB, includePrivilegedSettings bool) (relayDatabaseReleaseSettings, error) {
	var settings relayDatabaseReleaseSettings
	if db == nil || db.Dialector.Name() != "postgres" {
		return settings, errors.New("Relay database release identity requires PostgreSQL")
	}
	query := `SELECT pg_catalog.pg_postmaster_start_time() AS postmaster_start_time,
       pg_catalog.pg_conf_load_time() AS config_load_time,
       ''::text AS shared_preload_libraries,
       ''::text AS session_preload_libraries,
       ''::text AS local_preload_libraries,
       COALESCE(pg_catalog.current_setting('pgaudit.log', true), '') AS pgaudit_log,
       COALESCE(pg_catalog.current_setting('pgaudit.log_parameter', true), '') AS pgaudit_log_parameter,
       pg_catalog.current_setting('log_parameter_max_length')::integer AS log_parameter_max_length,
       pg_catalog.current_setting('log_parameter_max_length_on_error')::integer AS log_parameter_max_length_on_error,
       COALESCE(NULLIF(pg_catalog.current_setting('auto_explain.log_parameter_max_length', true), ''), '-1')::integer
         AS auto_explain_log_parameter_max_length`
	if includePrivilegedSettings {
		query = `SELECT pg_catalog.pg_postmaster_start_time() AS postmaster_start_time,
       pg_catalog.pg_conf_load_time() AS config_load_time,
       pg_catalog.current_setting('shared_preload_libraries') AS shared_preload_libraries,
       pg_catalog.current_setting('session_preload_libraries') AS session_preload_libraries,
       pg_catalog.current_setting('local_preload_libraries') AS local_preload_libraries,
       COALESCE(pg_catalog.current_setting('pgaudit.log', true), '') AS pgaudit_log,
       COALESCE(pg_catalog.current_setting('pgaudit.log_parameter', true), '') AS pgaudit_log_parameter,
       pg_catalog.current_setting('log_parameter_max_length')::integer AS log_parameter_max_length,
       pg_catalog.current_setting('log_parameter_max_length_on_error')::integer AS log_parameter_max_length_on_error,
       COALESCE(NULLIF(pg_catalog.current_setting('auto_explain.log_parameter_max_length', true), ''), '-1')::integer
         AS auto_explain_log_parameter_max_length`
	}
	if err := db.Raw(query).Scan(&settings).Error; err != nil ||
		settings.PostmasterStartTime.IsZero() || settings.ConfigLoadTime.IsZero() {
		return relayDatabaseReleaseSettings{}, errors.New("Relay database release settings could not be inspected")
	}
	return settings, nil
}

func relayPGAuditExtensionExact(db *gorm.DB) (bool, error) {
	var exact bool
	if err := db.Raw(`SELECT count(*) = 1
  FROM pg_catalog.pg_extension extension
  JOIN pg_catalog.pg_namespace namespace ON namespace.oid = extension.extnamespace
  JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = extension.extowner
 WHERE extension.extname = 'pgaudit'
   AND extension.extversion = ?
   AND namespace.nspname = 'pg_catalog'
   AND owner_role.rolname = 'postgres'`, relayPostgres16PGAuditExtensionVersion).Scan(&exact).Error; err != nil {
		return false, errors.New("Relay pgAudit extension identity could not be inspected")
	}
	return exact, nil
}

func relayDatabaseCredentialLoggingPolicyExact(settings relayDatabaseReleaseSettings) bool {
	return strings.EqualFold(settings.PGAuditLogParameter, "off") &&
		settings.LogParameterMaxLength == 0 &&
		settings.LogParameterMaxLengthOnError == 0 &&
		settings.AutoExplainLogParameterMaxLength == 0
}

func relayDatabaseReleaseIdentity(db *gorm.DB, includePrivilegedSettings bool) (RelayDatabaseReleaseIdentity, error) {
	environment, protected, err := relayProtectedDatabaseEnvironment()
	if err != nil || !protected {
		if err != nil {
			return RelayDatabaseReleaseIdentity{}, err
		}
		return RelayDatabaseReleaseIdentity{}, errors.New("Relay database release identity requires protected mode")
	}
	semanticSHA256, err := relayPostgres16SystemSemanticFingerprint(db)
	if err != nil {
		return RelayDatabaseReleaseIdentity{}, err
	}
	if environment == "production" && semanticSHA256 != relayPostgres16DebianSystemSemanticSHA256 {
		return RelayDatabaseReleaseIdentity{}, errors.New("Relay production database system baseline is not qualified")
	}
	if environment == "staging" && semanticSHA256 != relayPostgres16AlpineSystemSemanticSHA256 &&
		semanticSHA256 != relayPostgres16DebianSystemSemanticSHA256 {
		return RelayDatabaseReleaseIdentity{}, errors.New("Relay staging database system baseline is not qualified")
	}
	if err := verifyRelayAllowedExtensionMembers(db); err != nil {
		return RelayDatabaseReleaseIdentity{}, err
	}
	pgauditExact, err := relayPGAuditExtensionExact(db)
	if err != nil {
		return RelayDatabaseReleaseIdentity{}, err
	}
	auditRequired := semanticSHA256 == relayPostgres16DebianSystemSemanticSHA256
	if pgauditExact != auditRequired {
		return RelayDatabaseReleaseIdentity{}, errors.New("Relay pgAudit extension identity is not exact")
	}
	settings, err := inspectRelayDatabaseReleaseSettings(db, includePrivilegedSettings)
	if err != nil {
		return RelayDatabaseReleaseIdentity{}, err
	}
	identity := RelayDatabaseReleaseIdentity{
		Environment:                  environment,
		PostmasterStartTime:          settings.PostmasterStartTime.UTC().Format(time.RFC3339Nano),
		ConfigLoadTime:               settings.ConfigLoadTime.UTC().Format(time.RFC3339Nano),
		SystemSemanticSHA256:         semanticSHA256,
		PGAuditExtensionExact:        pgauditExact,
		PGAuditLogClassCoverage:      relayDatabasePGAuditLogCoverage(settings.PGAuditLog),
		CredentialLoggingPolicyExact: relayDatabaseCredentialLoggingPolicyExact(settings),
	}
	if includePrivilegedSettings {
		manifest, validManifest := relayDatabaseSharedPreloadManifest(settings.SharedPreloadLibraries)
		if !validManifest {
			return RelayDatabaseReleaseIdentity{}, errors.New("Relay database shared preload manifest is invalid")
		}
		identity.SharedPreloadManifest = manifest
		identity.PGAuditSharedPreload = relayDatabaseSettingContains(manifest, "pgaudit")
		identity.SessionPreloadEmpty = strings.TrimSpace(settings.SessionPreloadLibraries) == "" &&
			strings.TrimSpace(settings.LocalPreloadLibraries) == ""
	}
	if includePrivilegedSettings {
		expectedManifest := ""
		if auditRequired {
			expectedManifest = "auto_explain,pgaudit"
		}
		if !identity.SessionPreloadEmpty || identity.PGAuditSharedPreload != auditRequired ||
			identity.SharedPreloadManifest != expectedManifest {
			return RelayDatabaseReleaseIdentity{}, errors.New("Relay database preload policy is not exact")
		}
		if auditRequired && (!identity.PGAuditLogClassCoverage || !identity.CredentialLoggingPolicyExact) {
			return RelayDatabaseReleaseIdentity{}, errors.New("Relay production database audit policy is not exact")
		}
	}
	return identity, nil
}

// InspectRelayDatabaseReleaseIdentity is called only by the privileged role
// predecessor after its complete role/surface transaction succeeds.
func InspectRelayDatabaseReleaseIdentity(db *gorm.DB) (RelayDatabaseReleaseIdentity, error) {
	return relayDatabaseReleaseIdentity(db, true)
}

func installRelayDatabaseReleaseIdentity(identity RelayDatabaseReleaseIdentity) error {
	relayDatabaseReleaseIdentityState.Lock()
	defer relayDatabaseReleaseIdentityState.Unlock()
	if relayDatabaseReleaseIdentityState.installed != nil {
		if *relayDatabaseReleaseIdentityState.installed != identity {
			return errors.New("Relay database release identity is immutable")
		}
		return nil
	}
	installed := identity
	relayDatabaseReleaseIdentityState.installed = &installed
	return nil
}

func relayInstalledDatabaseReleaseIdentity() (RelayDatabaseReleaseIdentity, bool) {
	relayDatabaseReleaseIdentityState.RLock()
	defer relayDatabaseReleaseIdentityState.RUnlock()
	if relayDatabaseReleaseIdentityState.installed == nil {
		return RelayDatabaseReleaseIdentity{}, false
	}
	return *relayDatabaseReleaseIdentityState.installed, true
}

func relayPostgres16SystemSemanticFingerprintPGX(
	ctx context.Context,
	connection *pgx.Conn,
) (string, error) {
	rows, err := connection.Query(ctx, relayPostgres16SystemSemanticSQL)
	if err != nil {
		return "", errors.New("Relay PostgreSQL 16 system semantic baseline could not be inspected")
	}
	defer rows.Close()
	var canonical strings.Builder
	writeField := func(value string) {
		canonical.WriteString(strconv.Itoa(len(value)))
		canonical.WriteByte(':')
		canonical.WriteString(value)
	}
	for rows.Next() {
		var object relaySchemaCatalogObject
		if err := rows.Scan(&object.Kind, &object.Identity, &object.Definition); err != nil {
			return "", errors.New("Relay PostgreSQL 16 system semantic baseline could not be inspected")
		}
		writeField(object.Kind)
		writeField(object.Identity)
		writeField(object.Definition)
	}
	if rows.Err() != nil {
		return "", errors.New("Relay PostgreSQL 16 system semantic baseline could not be inspected")
	}
	digest := sha256.Sum256([]byte(canonical.String()))
	return fmt.Sprintf("sha256:%x", digest[:]), nil
}

func relayPGAuditExtensionExactPGX(ctx context.Context, connection *pgx.Conn) (bool, error) {
	var exact bool
	if err := connection.QueryRow(ctx, `SELECT count(*) = 1
  FROM pg_catalog.pg_extension extension
  JOIN pg_catalog.pg_namespace namespace ON namespace.oid = extension.extnamespace
  JOIN pg_catalog.pg_roles owner_role ON owner_role.oid = extension.extowner
 WHERE extension.extname = 'pgaudit'
   AND extension.extversion = $1
   AND namespace.nspname = 'pg_catalog'
   AND owner_role.rolname = 'postgres'`, relayPostgres16PGAuditExtensionVersion).Scan(&exact); err != nil {
		return false, errors.New("Relay pgAudit extension identity could not be inspected")
	}
	return exact, nil
}

// verifyRelayInstalledDatabaseReleaseIdentityConnection is called by both the
// physical-connection hook and ResetSession. Startup is the only phase allowed
// to have no installed proof; listeners and workers start only after the first
// GORM-level proof verification installs the immutable expected identity.
func verifyRelayInstalledDatabaseReleaseIdentityConnection(ctx context.Context, connection *pgx.Conn) error {
	expected, installed := relayInstalledDatabaseReleaseIdentity()
	if !installed {
		return nil
	}
	if connection == nil {
		return errors.New("Relay database release connection is invalid")
	}
	var postmasterStartTime time.Time
	var configLoadTime time.Time
	var localPreloadLibraries string
	var pgauditLog string
	var pgauditLogParameter string
	var logParameterMaxLength int
	var logParameterMaxLengthOnError int
	var autoExplainLogParameterMaxLength int
	if err := connection.QueryRow(ctx, `SELECT pg_catalog.pg_postmaster_start_time(),
       pg_catalog.pg_conf_load_time(),
       COALESCE(pg_catalog.current_setting('local_preload_libraries', true), ''),
       COALESCE(pg_catalog.current_setting('pgaudit.log', true), ''),
       COALESCE(pg_catalog.current_setting('pgaudit.log_parameter', true), ''),
       pg_catalog.current_setting('log_parameter_max_length')::integer,
       pg_catalog.current_setting('log_parameter_max_length_on_error')::integer,
       COALESCE(NULLIF(pg_catalog.current_setting('auto_explain.log_parameter_max_length', true), ''), '-1')::integer`).Scan(
		&postmasterStartTime,
		&configLoadTime,
		&localPreloadLibraries,
		&pgauditLog,
		&pgauditLogParameter,
		&logParameterMaxLength,
		&logParameterMaxLengthOnError,
		&autoExplainLogParameterMaxLength,
	); err != nil {
		return errors.New("Relay database release settings could not be inspected")
	}
	semanticSHA256, err := relayPostgres16SystemSemanticFingerprintPGX(ctx, connection)
	if err != nil {
		return err
	}
	pgauditExact, err := relayPGAuditExtensionExactPGX(ctx, connection)
	if err != nil {
		return err
	}
	environment, protected, err := relayProtectedDatabaseEnvironment()
	if err != nil || !protected {
		return errors.New("Relay protected database environment is not exact")
	}
	auditRequired := semanticSHA256 == relayPostgres16DebianSystemSemanticSHA256
	expectedManifest := ""
	if auditRequired {
		expectedManifest = "auto_explain,pgaudit"
	}
	credentialPolicyExact := strings.EqualFold(pgauditLogParameter, "off") &&
		logParameterMaxLength == 0 && logParameterMaxLengthOnError == 0 &&
		autoExplainLogParameterMaxLength == 0
	if expected.Environment != environment ||
		expected.PostmasterStartTime != postmasterStartTime.UTC().Format(time.RFC3339Nano) ||
		expected.ConfigLoadTime != configLoadTime.UTC().Format(time.RFC3339Nano) ||
		expected.SystemSemanticSHA256 != semanticSHA256 ||
		expected.PGAuditExtensionExact != pgauditExact ||
		expected.PGAuditSharedPreload != auditRequired ||
		expected.SharedPreloadManifest != expectedManifest ||
		!expected.SessionPreloadEmpty || strings.TrimSpace(localPreloadLibraries) != "" {
		return errors.New("Relay database release proof does not match the physical connection")
	}
	if auditRequired && (!expected.PGAuditLogClassCoverage || !expected.CredentialLoggingPolicyExact ||
		!relayDatabasePGAuditLogCoverage(pgauditLog) || !credentialPolicyExact) {
		return errors.New("Relay production database audit proof is not exact on the physical connection")
	}
	return nil
}

// VerifyRelayDatabaseReleaseIdentity verifies the server facts visible to the
// current protected role against a predecessor proof. Shared/session preload
// settings are deliberately taken from the proof and bound to restart/config
// timestamps because ordinary roles are not granted pg_read_all_settings.
func VerifyRelayDatabaseReleaseIdentity(db *gorm.DB, expected RelayDatabaseReleaseIdentity) error {
	actual, err := relayDatabaseReleaseIdentity(db, false)
	if err != nil {
		return err
	}
	auditRequired := actual.SystemSemanticSHA256 == relayPostgres16DebianSystemSemanticSHA256
	expectedManifest := ""
	if auditRequired {
		expectedManifest = "auto_explain,pgaudit"
	}
	if expected.Environment != actual.Environment ||
		expected.PostmasterStartTime != actual.PostmasterStartTime ||
		expected.ConfigLoadTime != actual.ConfigLoadTime ||
		expected.SystemSemanticSHA256 != actual.SystemSemanticSHA256 ||
		expected.PGAuditExtensionExact != actual.PGAuditExtensionExact ||
		!expected.SessionPreloadEmpty || expected.PGAuditSharedPreload != auditRequired ||
		expected.SharedPreloadManifest != expectedManifest {
		return errors.New("Relay database release proof does not match the connected server")
	}
	if auditRequired && (!expected.PGAuditLogClassCoverage || !expected.CredentialLoggingPolicyExact ||
		!actual.PGAuditLogClassCoverage || !actual.CredentialLoggingPolicyExact) {
		return errors.New("Relay production database audit proof is not exact")
	}
	return installRelayDatabaseReleaseIdentity(expected)
}
