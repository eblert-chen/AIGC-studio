package model

import (
	"os"
	"testing"

	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func TestRelayDatabaseReleaseIdentityProductionCandidate(t *testing.T) {
	dsn := os.Getenv("TEST_RELAY_DATABASE_RELEASE_DSN")
	if dsn == "" {
		t.Skip("set TEST_RELAY_DATABASE_RELEASE_DSN to verify the PostgreSQL 16 production release identity")
	}
	t.Setenv("APP_ENV", "production")
	t.Setenv("DEPLOYMENT_ENV", "production")
	t.Setenv(relayDatabaseRoleAttestationEnvironment, "true")
	t.Setenv(relayDatabaseTLSAttestationEnvironment, "true")
	db, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = sqlDB.Close() })

	identity, err := InspectRelayDatabaseReleaseIdentity(db)
	require.NoError(t, err)
	require.Equal(t, "production", identity.Environment)
	require.NotEmpty(t, identity.PostmasterStartTime)
	require.NotEmpty(t, identity.ConfigLoadTime)
	require.Equal(t, relayPostgres16DebianSystemSemanticSHA256, identity.SystemSemanticSHA256)
	require.True(t, identity.PGAuditExtensionExact)
	require.True(t, identity.PGAuditSharedPreload)
	require.Equal(t, "auto_explain,pgaudit", identity.SharedPreloadManifest)
	require.True(t, identity.SessionPreloadEmpty)
	require.True(t, identity.PGAuditLogClassCoverage)
	require.True(t, identity.CredentialLoggingPolicyExact)
}

func TestRelayDatabasePGAuditLogCoverageUsesOrderedClassSemantics(t *testing.T) {
	for _, test := range []struct {
		value string
		valid bool
	}{
		{value: "ddl,role,write", valid: true},
		{value: "all,-read", valid: true},
		{value: "none,ddl,role,write", valid: true},
		{value: "ddl,role,write,-write", valid: false},
		{value: "ddl,role,write,none", valid: false},
		{value: "all,-write", valid: false},
		{value: "-none,ddl,role,write", valid: false},
		{value: "ddl,role,unknown,write", valid: false},
		{value: "ddl,role,write,", valid: false},
		{value: "ddl,role,write,写", valid: false},
	} {
		t.Run(test.value, func(t *testing.T) {
			require.Equal(t, test.valid, relayDatabasePGAuditLogCoverage(test.value))
		})
	}
}

func TestRelayDatabaseSharedPreloadManifestIsExactAndUnambiguous(t *testing.T) {
	for _, test := range []struct {
		value    string
		expected string
		valid    bool
	}{
		{value: "", expected: "", valid: true},
		{value: "pgaudit", expected: "pgaudit", valid: true},
		{value: "pgaudit, auto_explain", expected: "auto_explain,pgaudit", valid: true},
		{value: "pgaudit,pgaudit", valid: false},
		{value: "'pgaudit'", valid: false},
		{value: `"pgaudit"`, valid: false},
		{value: "pgaudit,../library", valid: false},
		{value: "pgaudit,库", valid: false},
	} {
		t.Run(test.value, func(t *testing.T) {
			actual, valid := relayDatabaseSharedPreloadManifest(test.value)
			require.Equal(t, test.valid, valid)
			if valid {
				require.Equal(t, test.expected, actual)
			}
		})
	}
}

func TestRelayProtectedDatabaseEnvironmentRequiresExactTLSFlag(t *testing.T) {
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv(relayDatabaseRoleAttestationEnvironment, "true")
	for _, value := range []string{"", "false", "TRUE", " true "} {
		t.Run(value, func(t *testing.T) {
			if value == "" {
				t.Setenv(relayDatabaseTLSAttestationEnvironment, "")
			} else {
				t.Setenv(relayDatabaseTLSAttestationEnvironment, value)
			}
			_, protected, err := relayProtectedDatabaseEnvironment()
			require.True(t, protected)
			require.Error(t, err)
		})
	}
	t.Setenv(relayDatabaseTLSAttestationEnvironment, "true")
	environment, protected, err := relayProtectedDatabaseEnvironment()
	require.NoError(t, err)
	require.True(t, protected)
	require.Equal(t, "staging", environment)
}
