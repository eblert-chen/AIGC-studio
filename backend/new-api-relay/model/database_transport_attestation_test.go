package model

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net/url"
	"path/filepath"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
)

func relayModelTestRedisTLSCA(t *testing.T) common.ProtectedRelayRedisTLSCA {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	require.NoError(t, err)
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Relay Redis test CA"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, publicKey, privateKey)
	require.NoError(t, err)
	raw := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	defer clear(raw)
	value, err := common.ParseProtectedRelayRedisTLSCA(raw)
	require.NoError(t, err)
	return value
}

func TestValidateRelayPostgresTransportDSNRequiresVerifyFullWhenExplicitlyAttested(t *testing.T) {
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")

	require.Error(t, ValidateRelayPostgresTransportDSN("postgresql://relay:password@postgres.invalid:5432/relay?sslmode=require"))
	require.Error(t, ValidateRelayPostgresTransportDSN("postgresql://relay:password@postgres.invalid:5432/relay?sslmode=verify-ca"))
	require.Error(t, ValidateRelayPostgresTransportDSN("postgresql://relay:password@postgres.invalid:5432/relay?sslmode=verify-full&sslmode=require"))
	require.Error(t, ValidateRelayPostgresTransportDSN("postgresql://relay:password@postgres.invalid:5432/relay?sslmode=verify-full&service=rogue"))
	require.Error(t, ValidateRelayPostgresTransportDSN("postgresql://relay:password@postgres.invalid:5432/relay?sslmode=verify-full&servicefile=%2Ftmp%2Frogue"))
	rootPath := filepath.Join(t.TempDir(), "root-ca.pem")
	t.Setenv(relayDatabaseCAFileEnvironment, rootPath)
	require.NoError(t, ValidateRelayPostgresTransportDSN("postgresql://relay:password@postgres.invalid:5432/relay?sslmode=verify-full&sslrootcert="+url.QueryEscape(rootPath)))
	require.Error(t, ValidateRelayPostgresTransportDSN("postgresql://relay:password@postgres.invalid:5432/relay?sslmode=verify-full&sslrootcert=relative.pem"))
	require.Error(t, ValidateRelayPostgresTransportDSN("postgresql://relay:password@postgres.invalid:5432/relay?sslmode=verify-full"))
}

func TestParseRelayPostgresConfigUsesReceiptPinnedCARatherThanReopeningPath(t *testing.T) {
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	caPath := filepath.Join(t.TempDir(), "root-ca.pem")
	t.Setenv(relayDatabaseCAFileEnvironment, caPath)
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	require.NoError(t, err)
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Pinned Relay test CA"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, publicKey, privateKey)
	require.NoError(t, err)
	caBytes := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	require.NoError(t, common.InstallProtectedSecretFileSnapshots([]common.ProtectedSecretFileSnapshot{{
		Environment: relayDatabaseCAFileEnvironment, Value: caBytes,
	}}))
	clear(caBytes)

	dsn := "postgresql://relay:password@postgres.invalid:5432/relay?sslmode=verify-full&sslrootcert=" + url.QueryEscape(caPath)
	configuration, err := parseRelayPostgresConfig(dsn)
	require.NoError(t, err)
	require.NotNil(t, configuration.TLSConfig)
	require.NotEmpty(t, configuration.TLSConfig.RootCAs.Subjects())
}

func TestChooseDBRejectsRawProtectedPostgresBeforeNetworkAccess(t *testing.T) {
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("SQL_DSN", "postgresql://relay:password@unreachable.invalid:5432/relay?sslmode=require&search_path=public")
	t.Setenv("SQL_DSN_FILE", "")

	db, _, err := chooseDB("SQL_DSN", false)
	require.ErrorContains(t, err, "environment value is forbidden")
	require.Nil(t, db)
}

func TestValidateRelayPostgresSearchPathDSNRejectsHostileOverrides(t *testing.T) {
	// This is the deliberately non-TLS local ACL rehearsal. Protected staging
	// is covered separately and always requires verify-full plus a committed CA.
	t.Setenv("APP_ENV", "test")
	t.Setenv("DEPLOYMENT_ENV", "test")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_MIGRATION_DATABASE_ROLE", "relay_schema_migrator")
	t.Setenv("RELAY_SCHEMA_OWNER_DATABASE_ROLE", "relay_schema_owner")

	require.NoError(t, ValidateRelayPostgresSearchPathDSN("postgresql://relay:password@postgres.invalid:5432/relay?search_path=public"))
	require.Error(t, ValidateRelayPostgresSearchPathDSN("postgresql://relay:password@postgres.invalid:5432/relay?search_path=public,pg_catalog"))
	require.Error(t, ValidateRelayPostgresSearchPathDSN("postgresql://relay:password@postgres.invalid:5432/relay?search_path=public&options=-c%20search_path%3Drogue"))
	require.Error(t, ValidateRelayPostgresSearchPathDSN("postgresql://relay:password@postgres.invalid:5432/relay?search_path=public&search_path=rogue"))
	require.Error(t, ValidateRelayPostgresSearchPathDSN("postgresql://relay:password@postgres.invalid:5432/relay?search_path=public&options=-c%20role%3Drelay_schema_owner"))
	require.Error(t, ValidateRelayPostgresSearchPathDSN("postgresql://relay:password@postgres.invalid:5432/relay?search_path=public&service=rogue"))
	require.NoError(t, ValidateRelayPostgresSearchPathDSN("postgresql://relay_schema_migrator:password@postgres.invalid:5432/relay?search_path=public&options=-c%20role%3Drelay_schema_owner"))
	require.Error(t, ValidateRelayPostgresSearchPathDSN("postgresql://relay_schema_migrator:password@postgres.invalid:5432/relay?search_path=public"))
	require.Error(t, ValidateRelayPostgresSearchPathDSN("postgresql://relay_schema_migrator:password@postgres.invalid:5432/relay?search_path=public&options=-c%20role%3Drelay_schema_owner&options=-c%20statement_timeout%3D0"))
	require.Error(t, ValidateRelayPostgresSearchPathDSN("postgresql://relay:password@postgres.invalid:5432/relay"))

	base := "postgresql://relay:userinfo-password@postgres.invalid:5432/relay?search_path=public"
	for _, hostile := range []string{
		"password=query-override",
		"passfile=%2Frun%2Fsecrets%2Frogue",
		"sslpassword=query-secret",
		"sslkey=%2Frun%2Fsecrets%2Frogue-key",
		"host=rogue.invalid",
		"user=rogue",
		"application_name=unapproved",
		"unknown=value",
		"password=",
	} {
		t.Run(hostile, func(t *testing.T) {
			require.Error(t, ValidateRelayPostgresSearchPathDSN(base+"&"+hostile))
		})
	}
}

func TestValidateRelayProtectedPostgresDSNRejectsAmbientPGConfiguration(t *testing.T) {
	for _, environment := range []string{"PGPASSWORD", "pgpassfile", "PgService", "PGSERVICEFILE", "PGOPTIONS", "PGSSLKEY", "PGSSLCERT", "PGSSLROOTCERT", "PGHOST", "PGUSER"} {
		t.Run(environment, func(t *testing.T) {
			t.Setenv("APP_ENV", "staging")
			t.Setenv("DEPLOYMENT_ENV", "staging")
			t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
			t.Setenv(environment, "")
			require.EqualError(t,
				ValidateRelayProtectedPostgresDSN("postgresql://relay:password@postgres.invalid:5432/relay?search_path=public"),
				"Relay protected database client environment is forbidden",
			)
		})
	}
}
