package model

import (
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"gorm.io/gorm"
)

const relayDatabaseTLSAttestationEnvironment = "RELAY_DATABASE_TLS_ATTESTATION_REQUIRED"
const relayDatabaseSecretFilesEnvironment = "RELAY_DATABASE_SECRET_FILES_REQUIRED"
const relayDatabaseCAFileEnvironment = "RELAY_DATABASE_CA_FILE"

// RelayDatabaseTLSAttestationRequired is separate from role attestation so a
// local PG16 rehearsal can exercise exact ACLs without pretending to be a
// managed TLS endpoint. Production always requires both.
func RelayDatabaseTLSAttestationRequired() bool {
	if common.IsProductionEnvironment() {
		return true
	}
	appEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	deploymentEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("DEPLOYMENT_ENV")))
	if RelayDatabaseRoleAttestationRequired() && appEnvironment == deploymentEnvironment &&
		(appEnvironment == "staging" || appEnvironment == "production") {
		return true
	}
	raw, present := os.LookupEnv(relayDatabaseTLSAttestationEnvironment)
	if !present || strings.TrimSpace(raw) == "" {
		return false
	}
	required, err := strconv.ParseBool(strings.TrimSpace(raw))
	return err != nil || required
}

func RelayDatabaseSecretFilesRequired() bool {
	if common.IsProductionEnvironment() || RelayDatabaseRoleAttestationRequired() {
		return true
	}
	raw, present := os.LookupEnv(relayDatabaseSecretFilesEnvironment)
	if !present || strings.TrimSpace(raw) == "" {
		return false
	}
	required, err := strconv.ParseBool(strings.TrimSpace(raw))
	return err != nil || required
}

// ValidateRelayProtectedPostgresClientEnvironment forbids libpq/pgx ambient
// defaults in protected processes. A URI can pin its apparent user, host,
// password, and TLS mode while ParseConfig still consumes PGSERVICE,
// PGPASSFILE, PGOPTIONS, client-certificate paths, or another PG* override.
// Protected callers therefore use the committed DSN and CA snapshot as their
// only connection configuration source.
func ValidateRelayProtectedPostgresClientEnvironment() error {
	if !RelayDatabaseRoleAttestationRequired() && !RelayDatabaseTLSAttestationRequired() {
		return nil
	}
	for _, entry := range os.Environ() {
		name, _, present := strings.Cut(entry, "=")
		if present && strings.HasPrefix(strings.ToUpper(name), "PG") {
			return errors.New("Relay protected database client environment is forbidden")
		}
	}
	return nil
}

// ValidateRelayProtectedPostgresDSN applies one role-aware query contract to
// every protected PostgreSQL consumer and to the offline secret-isolation
// validator. URI query parameters can override userinfo and TLS settings in
// PostgreSQL drivers, so an allowlist is required rather than checking only the
// two expected keys independently.
func ValidateRelayProtectedPostgresDSN(dsn string) error {
	roleProtected := RelayDatabaseRoleAttestationRequired()
	tlsProtected := RelayDatabaseTLSAttestationRequired()
	if !roleProtected && !tlsProtected {
		return nil
	}
	if err := ValidateRelayProtectedPostgresClientEnvironment(); err != nil {
		return err
	}
	parsed, err := url.Parse(dsn)
	if err != nil || (parsed.Scheme != "postgres" && parsed.Scheme != "postgresql") ||
		parsed.User == nil || parsed.User.Username() == "" || parsed.Host == "" ||
		parsed.Fragment != "" || parsed.RawFragment != "" || parsed.ForceQuery {
		return errors.New("Relay protected database DSN is invalid")
	}
	if _, present := parsed.User.Password(); !present {
		return errors.New("Relay protected database DSN requires a userinfo password")
	}
	query, err := url.ParseQuery(parsed.RawQuery)
	if err != nil {
		return errors.New("Relay protected database DSN query is invalid")
	}
	for key, values := range query {
		if len(values) != 1 || values[0] == "" {
			return errors.New("Relay protected database DSN query is invalid")
		}
		switch key {
		case "sslmode", "search_path", "options":
		case "sslrootcert":
			path := values[0]
			if !tlsProtected || strings.TrimSpace(path) != path || strings.ContainsAny(path, "\x00\r\n") ||
				!filepath.IsAbs(path) || filepath.Clean(path) != path {
				return errors.New("Relay protected database DSN TLS root path is invalid")
			}
		default:
			return errors.New("Relay protected database DSN query parameter is forbidden")
		}
	}
	sslModes := query["sslmode"]
	if tlsProtected {
		if len(sslModes) != 1 || sslModes[0] != "verify-full" {
			return errors.New("Relay protected database DSN requires sslmode=verify-full")
		}
		rootCertificates := query["sslrootcert"]
		expectedRootCertificate := os.Getenv(relayDatabaseCAFileEnvironment)
		if expectedRootCertificate == "" || strings.TrimSpace(expectedRootCertificate) != expectedRootCertificate ||
			strings.ContainsAny(expectedRootCertificate, "\x00\r\n") || !filepath.IsAbs(expectedRootCertificate) ||
			filepath.Clean(expectedRootCertificate) != expectedRootCertificate || len(rootCertificates) != 1 ||
			rootCertificates[0] != expectedRootCertificate {
			return errors.New("Relay protected database DSN requires the committed TLS root certificate")
		}
	} else if len(sslModes) > 1 {
		return errors.New("Relay protected database DSN sslmode is ambiguous")
	}
	searchPaths := query["search_path"]
	if roleProtected {
		if len(searchPaths) != 1 || searchPaths[0] != "public" {
			return errors.New("Relay protected database DSN requires exact search_path=public")
		}
	} else if len(searchPaths) == 1 && searchPaths[0] != "public" {
		return errors.New("Relay protected database DSN search_path is invalid")
	}
	options := query["options"]
	migrationRole := strings.TrimSpace(os.Getenv(relayMigrationDatabaseRoleEnvironment))
	ownerRole := strings.TrimSpace(os.Getenv(relaySchemaOwnerRoleEnvironment))
	if roleProtected && parsed.User.Username() == migrationRole && migrationRole != "" {
		expectedOption := "-c role=" + ownerRole
		if ownerRole == "" || len(options) != 1 || options[0] != expectedOption {
			return errors.New("Relay migration database DSN must assume the configured schema owner role")
		}
	} else if len(options) != 0 {
		return errors.New("Relay protected database DSN options are forbidden for this role")
	}
	return nil
}

func ValidateRelayPostgresTransportDSN(dsn string) error {
	if !RelayDatabaseTLSAttestationRequired() {
		return nil
	}
	return ValidateRelayProtectedPostgresDSN(dsn)
}

func ValidateRelayPostgresSearchPathDSN(dsn string) error {
	if !RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	return ValidateRelayProtectedPostgresDSN(dsn)
}

func VerifyRelayDatabaseTLS(db *gorm.DB) error {
	if !RelayDatabaseTLSAttestationRequired() {
		return nil
	}
	if db == nil || db.Dialector.Name() != "postgres" {
		return errors.New("Relay protected database transport requires PostgreSQL")
	}
	var identity struct {
		SessionUser string `gorm:"column:session_user"`
		CurrentUser string `gorm:"column:current_user"`
	}
	if err := db.Raw(`SELECT session_user AS session_user, current_user AS current_user`).Scan(&identity).Error; err != nil ||
		identity.SessionUser == "" || identity.CurrentUser == "" {
		return errors.New("Relay protected database transport is not TLS-attested")
	}
	verify := func(query *gorm.DB) error {
		var encrypted bool
		result := query.Raw(`SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid = pg_catalog.pg_backend_pid()`).Scan(&encrypted)
		if result.Error != nil || result.RowsAffected != 1 || !encrypted {
			return errors.New("Relay protected database transport is not TLS-attested")
		}
		return nil
	}
	if identity.SessionUser == identity.CurrentUser {
		return verify(db)
	}
	// The migration DSN starts every session with SET ROLE owner. PostgreSQL's
	// statistics visibility treats that owner as distinct from session_user and
	// can hide the current pg_stat_ssl row. Inspect the same physical session as
	// session_user inside a short transaction; SET LOCAL restores the pinned
	// owner automatically at transaction end and never leaks elevated state to
	// another pooled statement.
	if err := db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec(`SET LOCAL ROLE NONE`).Error; err != nil {
			return errors.New("Relay protected database transport identity could not be inspected")
		}
		return verify(tx)
	}); err != nil {
		return errors.New("Relay protected database transport is not TLS-attested")
	}
	return nil
}
