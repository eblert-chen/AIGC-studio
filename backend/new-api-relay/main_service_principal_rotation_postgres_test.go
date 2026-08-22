package main

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

const platformRelayPrincipalRotationLifecycleAdvisoryLock int64 = 0x41564944524f4f4c

// This optional PostgreSQL gate proves that a mistakenly live runtime cannot
// leave the maintenance job waiting forever. A held shared gate A makes the
// real rotation helper return its fixed, secret-free error within the command
// deadline, before schema checks or principal DML can run.
func TestPlatformRelayPrincipalRotationLifecycleLockPostgresTimesOutWithoutWrites(t *testing.T) {
	adminDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_ROTATION_ADMIN_DSN"))
	runtimeDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_ROTATION_RUNTIME_DSN"))
	if adminDSN == "" && runtimeDSN == "" {
		t.Skip("set both TEST_POSTGRES_ROTATION_*_DSN values for the disposable PostgreSQL lifecycle gate")
	}
	require.NotEmpty(t, adminDSN)
	require.NotEmpty(t, runtimeDSN)

	admin, err := gorm.Open(postgres.Open(adminDSN), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Silent),
	})
	require.NoError(t, err)
	adminSQL, err := admin.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = adminSQL.Close() })
	runtimeDB, err := gorm.Open(postgres.Open(runtimeDSN), &gorm.Config{
		Logger: logger.Default.LogMode(logger.Silent),
	})
	require.NoError(t, err)
	runtimeSQL, err := runtimeDB.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = runtimeSQL.Close() })

	originalDB := model.DB
	originalDatabaseType := common.MainDatabaseType()
	model.DB = runtimeDB
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	t.Cleanup(func() {
		model.DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "true")
	require.NoError(t, os.Unsetenv("SQL_DSN"))
	_, runtimeDSNFile := writePlatformRelayRootProvisionProtectedFile(
		t,
		"principal-rotation-lifecycle-timeout-runtime-dsn",
		runtimeDSN,
	)
	t.Setenv("SQL_DSN_FILE", runtimeDSNFile)

	type databaseFingerprint struct {
		Digest string
		Count  int64
	}
	fingerprint := func() databaseFingerprint {
		var result databaseFingerprint
		require.NoError(t, admin.Raw(`
SELECT md5(COALESCE(string_agg(to_jsonb(token_row)::text, '|' ORDER BY token_row.id), '')) AS digest,
       count(*) AS count
  FROM public.tokens token_row`).Scan(&result).Error)
		return result
	}
	before := fingerprint()

	holder, err := adminSQL.Conn(context.Background())
	require.NoError(t, err)
	holderOpen := true
	t.Cleanup(func() {
		if holderOpen {
			_, _ = holder.ExecContext(
				context.Background(),
				`SELECT pg_catalog.pg_advisory_unlock_shared($1)`,
				platformRelayPrincipalRotationLifecycleAdvisoryLock,
			)
			_ = holder.Close()
		}
	})
	_, err = holder.ExecContext(
		context.Background(),
		`SELECT pg_catalog.pg_advisory_lock_shared($1)`,
		platformRelayPrincipalRotationLifecycleAdvisoryLock,
	)
	require.NoError(t, err)

	started := time.Now()
	lock, lockErr := acquirePlatformRelayPrincipalRotationLifecycleLock()
	elapsed := time.Since(started)
	if lock != nil {
		lock.Close()
	}
	require.EqualError(t, lockErr, "Relay service principal rotation lifecycle gate is unavailable")
	require.GreaterOrEqual(t, elapsed, platformRelayPrincipalRotationLifecycleLockTimeout-time.Second)
	require.LessOrEqual(t, elapsed, platformRelayPrincipalRotationLifecycleLockTimeout+3*time.Second)
	require.Equal(t, before, fingerprint(), "lifecycle timeout must perform zero principal database writes")

	_, err = holder.ExecContext(
		context.Background(),
		`SELECT pg_catalog.pg_advisory_unlock_shared($1)`,
		platformRelayPrincipalRotationLifecycleAdvisoryLock,
	)
	require.NoError(t, err)
	require.NoError(t, holder.Close())
	holderOpen = false
}
