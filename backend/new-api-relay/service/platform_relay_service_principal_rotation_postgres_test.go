package service

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/alicebob/miniredis/v2"
	"github.com/go-redis/redis/v8"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	gormlogger "gorm.io/gorm/logger"
)

const (
	platformRelayRotationPostgresTenantID        = "4f88d0a4-d950-4b42-936e-6adf03aaf62d"
	platformRelayRotationPostgresRuntimeRole     = "relay_runtime"
	platformRelayRotationPostgresLifecycleClass  = int64(0x41564944)
	platformRelayRotationPostgresProcessObject   = int64(0x524f4f4c)
	platformRelayRotationPostgresMutationObject  = int64(0x524f4f4d)
	platformRelayRotationPostgresPrincipalObject = int64(0x5350524e)
	platformRelayRotationPostgresKillMarker      = int64(0x41564944524f544d)
	platformRelayRotationPostgresKillBarrier     = int64(0x41564944524f544b)
)

var platformRelayRotationPostgresClientIDs = []string{
	"lifecycle-platform-api",
	"lifecycle-platform-dispatcher",
	"lifecycle-platform-relay-sync",
	"lifecycle-platform-timeout",
}

type platformRelayRotationPostgresSynchronizedBuffer struct {
	mu     sync.Mutex
	buffer bytes.Buffer
}

func (buffer *platformRelayRotationPostgresSynchronizedBuffer) Write(value []byte) (int, error) {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	return buffer.buffer.Write(value)
}

func (buffer *platformRelayRotationPostgresSynchronizedBuffer) String() string {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	return buffer.buffer.String()
}

func platformRelayRotationPostgresToken(domain, clientID string) string {
	digest := sha256.Sum256([]byte(domain + "\x00" + clientID))
	return "sk-" + hex.EncodeToString(digest[:])[:48]
}

func platformRelayRotationPostgresInputs(domain string) []PlatformRelayServicePrincipalProvisionInput {
	inputs := make([]PlatformRelayServicePrincipalProvisionInput, 0, len(platformRelayRotationPostgresClientIDs))
	for _, clientID := range platformRelayRotationPostgresClientIDs {
		inputs = append(inputs, PlatformRelayServicePrincipalProvisionInput{
			ClientID:      clientID,
			TenantID:      platformRelayRotationPostgresTenantID,
			UpstreamToken: platformRelayRotationPostgresToken(domain, clientID),
		})
	}
	return inputs
}

func platformRelayRotationPostgresWriteProtectedDSN(t *testing.T, dsn string) string {
	t.Helper()
	sourceDirectory := ""
	readOnlyDirectory := ""
	if runtime.GOOS == "linux" {
		sourceDirectory = strings.TrimSpace(os.Getenv("TEST_PROTECTED_SECRET_SOURCE_DIR"))
		readOnlyDirectory = strings.TrimSpace(os.Getenv("TEST_PROTECTED_SECRET_READONLY_DIR"))
		require.NotEmpty(t, sourceDirectory)
		require.NotEmpty(t, readOnlyDirectory)
	} else {
		sourceDirectory = t.TempDir()
		readOnlyDirectory = sourceDirectory
	}
	require.True(t, filepath.IsAbs(sourceDirectory))
	require.True(t, filepath.IsAbs(readOnlyDirectory))
	name := fmt.Sprintf("rotation-runtime-sql-dsn-%d", os.Getpid())
	sourcePath := filepath.Join(sourceDirectory, name)
	readOnlyPath := filepath.Join(readOnlyDirectory, name)
	require.NoError(t, os.WriteFile(sourcePath, []byte(dsn), 0o400))
	require.NoError(t, os.Chmod(sourcePath, 0o400))
	sourceInfo, err := os.Stat(sourcePath)
	require.NoError(t, err)
	readOnlyInfo, err := os.Stat(readOnlyPath)
	require.NoError(t, err)
	require.True(t, os.SameFile(sourceInfo, readOnlyInfo))
	t.Cleanup(func() {
		_ = os.Chmod(sourcePath, 0o600)
		_ = os.Remove(sourcePath)
	})
	return readOnlyPath
}

func platformRelayRotationPostgresWaitForRelationLock(
	t *testing.T,
	admin *gorm.DB,
	mode string,
	granted bool,
) int {
	t.Helper()
	pid := 0
	require.Eventually(t, func() bool {
		pid = 0
		err := admin.Raw(`SELECT pid
  FROM pg_catalog.pg_locks
 WHERE locktype = 'relation'
   AND relation = 'public.tokens'::regclass
   AND mode = ? AND granted = ?
 ORDER BY pid LIMIT 1`, mode, granted).Scan(&pid).Error
		return err == nil && pid > 0
	}, 10*time.Second, 25*time.Millisecond, "expected token relation lock was not observable")
	return pid
}

func platformRelayRotationPostgresAssertNoSecret(
	t *testing.T,
	diagnostic string,
	secrets ...string,
) {
	t.Helper()
	for _, secret := range secrets {
		if secret == "" {
			continue
		}
		require.False(t, strings.Contains(diagnostic, secret), "application log contained a secret canary")
	}
}

// TestProtectedPlatformRelayServicePrincipalRotationPostgresBarrier is run
// only against the disposable PostgreSQL 16 reference left by the fresh
// catalog gate. The root-row barrier makes the rotation hold B, the principal
// advisory lock, and SHARE ROW EXCLUSIVE on tokens while an ordinary protected
// insert queues behind it. That queued insert must converge to the generic
// conflict sentinel after commit, with no unique-error detail and no mixed
// principal state.
func TestProtectedPlatformRelayServicePrincipalRotationPostgresBarrier(t *testing.T) {
	adminDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_ROTATION_ADMIN_DSN"))
	runtimeDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_ROTATION_RUNTIME_DSN"))
	if adminDSN == "" && runtimeDSN == "" {
		t.Skip("set both TEST_POSTGRES_ROTATION_*_DSN values for the disposable PostgreSQL barrier gate")
	}
	require.NotEmpty(t, adminDSN)
	require.NotEmpty(t, runtimeDSN)
	parsedRuntime, err := url.Parse(runtimeDSN)
	require.NoError(t, err)
	runtimePassword, hasRuntimePassword := parsedRuntime.User.Password()
	require.True(t, hasRuntimePassword)
	require.NotEmpty(t, runtimePassword)

	admin, err := gorm.Open(postgres.Open(adminDSN), &gorm.Config{
		Logger: gormlogger.Default.LogMode(gormlogger.Silent),
	})
	require.NoError(t, err)
	adminSQL, err := admin.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = adminSQL.Close() })

	var applicationLog platformRelayRotationPostgresSynchronizedBuffer
	protectedLogger := gormlogger.New(
		log.New(&applicationLog, "", 0),
		gormlogger.Config{
			SlowThreshold:             time.Second,
			LogLevel:                  gormlogger.Info,
			IgnoreRecordNotFoundError: true,
			ParameterizedQueries:      true,
			Colorful:                  false,
		},
	)
	runtimeDB, err := gorm.Open(postgres.Open(runtimeDSN), &gorm.Config{Logger: protectedLogger})
	require.NoError(t, err)
	runtimeSQL, err := runtimeDB.DB()
	require.NoError(t, err)
	runtimeSQL.SetMaxOpenConns(4)
	runtimeSQL.SetMaxIdleConns(4)
	t.Cleanup(func() { _ = runtimeSQL.Close() })

	originalDB := model.DB
	originalDatabaseType := common.MainDatabaseType()
	model.DB = runtimeDB
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	t.Cleanup(func() {
		model.DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	t.Setenv("APP_ENV", "staging")
	t.Setenv("DEPLOYMENT_ENV", "staging")
	t.Setenv("NODE_TYPE", "master")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "true")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "true")
	t.Setenv("RELAY_RUNTIME_DATABASE_ROLE", platformRelayRotationPostgresRuntimeRole)
	require.NoError(t, os.Unsetenv("SQL_DSN"))
	t.Setenv("SQL_DSN_FILE", platformRelayRotationPostgresWriteProtectedDSN(t, runtimeDSN))

	current := make([]PlatformRelayServicePrincipalProvisionInput, len(platformRelayRotationPostgresClientIDs))
	// The fresh-schema fixture hashes "relay-schema-lifecycle\0" plus the
	// label "upstream-token-<client>". Construct that exact input explicitly.
	for index, clientID := range platformRelayRotationPostgresClientIDs {
		digest := sha256.Sum256([]byte("relay-schema-lifecycle\x00upstream-token-" + clientID))
		current[index] = PlatformRelayServicePrincipalProvisionInput{
			ClientID:      clientID,
			TenantID:      platformRelayRotationPostgresTenantID,
			UpstreamToken: "sk-" + hex.EncodeToString(digest[:])[:48],
		}
	}
	desired := platformRelayRotationPostgresInputs("relay-principal-rotation-pg-barrier-v1")
	attemptID := strings.Repeat("c", 64)
	expectedCurrent, err := buildProtectedPlatformRelayExpectedPrincipals(current)
	require.NoError(t, err)
	expectedDesired, err := buildProtectedPlatformRelayExpectedPrincipals(desired)
	require.NoError(t, err)
	require.NoError(t, validateProtectedPlatformRelayServicePrincipalsTx(runtimeDB, expectedCurrent),
		"disposable PostgreSQL fixture did not contain the exact current principal set")

	previousRedisEnabled := common.RedisEnabled
	previousRedis := common.RDB
	previousCryptoSecret := common.CryptoSecret
	cacheServer := miniredis.RunT(t)
	common.RedisEnabled = true
	common.RDB = redis.NewClient(&redis.Options{Addr: cacheServer.Addr()})
	common.CryptoSecret = "rotation-postgres-stale-cache-hmac-key"
	t.Cleanup(func() {
		_ = common.RDB.Close()
		common.RedisEnabled = previousRedisEnabled
		common.RDB = previousRedis
		common.CryptoSecret = previousCryptoSecret
	})
	oldCachedKey := strings.TrimPrefix(current[0].UpstreamToken, "sk-")
	var oldCachedToken model.Token
	require.NoError(t, runtimeDB.Where("key = ?", oldCachedKey).First(&oldCachedToken).Error)
	oldCachedToken.Clean()
	require.NoError(t, common.RedisHSetObj(
		"token:"+common.GenerateHMAC(oldCachedKey), &oldCachedToken, time.Hour,
	))

	var runtimeVerifier string
	require.NoError(t, admin.Raw(
		`SELECT rolpassword FROM pg_catalog.pg_authid WHERE rolname = ?`,
		platformRelayRotationPostgresRuntimeRole,
	).Scan(&runtimeVerifier).Error)
	require.NotEmpty(t, runtimeVerifier)

	lifecycleLock, err := model.AcquireRelayLifecycleLock(context.Background(), runtimeDB)
	require.NoError(t, err)
	lifecycleReleased := false
	t.Cleanup(func() {
		if !lifecycleReleased {
			lifecycleLock.Close()
		}
	})
	var processLockCount int64
	require.NoError(t, admin.Raw(`SELECT count(*)
  FROM pg_catalog.pg_locks
 WHERE locktype = 'advisory'
   AND classid = ? AND objid = ? AND objsubid = 1
   AND mode = 'ExclusiveLock' AND granted`,
		platformRelayRotationPostgresLifecycleClass,
		platformRelayRotationPostgresProcessObject,
	).Scan(&processLockCount).Error)
	require.Equal(t, int64(1), processLockCount, "exclusive lifecycle gate A was not observable")

	rootBarrier := admin.Begin()
	require.NoError(t, rootBarrier.Error)
	rootBarrierOpen := true
	t.Cleanup(func() {
		if rootBarrierOpen {
			_ = rootBarrier.Rollback().Error
		}
	})
	var rootID int
	require.NoError(t, rootBarrier.Raw(
		`SELECT id FROM public.users WHERE role = ? ORDER BY id LIMIT 1 FOR UPDATE`,
		common.RoleRootUser,
	).Scan(&rootID).Error)
	require.Positive(t, rootID)

	type rotationOutcome struct {
		result PlatformRelayServicePrincipalRotationResult
		err    error
	}
	rotationDone := make(chan rotationOutcome, 1)
	go func() {
		result, rotationErr := RotateProtectedPlatformRelayServicePrincipals(attemptID, current, desired)
		rotationDone <- rotationOutcome{result: result, err: rotationErr}
	}()

	rotationPID := platformRelayRotationPostgresWaitForRelationLock(
		t, admin, "ShareRowExclusiveLock", true,
	)
	var rotationAdvisoryCount int64
	require.NoError(t, admin.Raw(`SELECT count(*)
  FROM pg_catalog.pg_locks
 WHERE pid = ? AND locktype = 'advisory'
   AND classid = ? AND objid IN (?, ?) AND objsubid = 1
   AND mode = 'ExclusiveLock' AND granted`,
		rotationPID,
		platformRelayRotationPostgresLifecycleClass,
		platformRelayRotationPostgresMutationObject,
		platformRelayRotationPostgresPrincipalObject,
	).Scan(&rotationAdvisoryCount).Error)
	require.Equal(t, int64(2), rotationAdvisoryCount,
		"rotation backend did not hold transaction gate B and the principal advisory lock before row locks")

	competing := &model.Token{
		UserId:         rootID,
		Key:            strings.TrimPrefix(desired[0].UpstreamToken, "sk-"),
		Status:         common.TokenStatusEnabled,
		Name:           "rotation-collision-probe",
		CreatedTime:    time.Now().Unix(),
		ExpiredTime:    -1,
		UnlimitedQuota: true,
	}
	insertDone := make(chan error, 1)
	go func() { insertDone <- competing.Insert() }()
	insertPID := platformRelayRotationPostgresWaitForRelationLock(
		t, admin, "RowExclusiveLock", false,
	)
	require.NotEqual(t, rotationPID, insertPID)
	select {
	case <-insertDone:
		t.Fatal("ordinary protected token insert passed the rotation table-lock barrier")
	default:
	}

	require.NoError(t, rootBarrier.Commit().Error)
	rootBarrierOpen = false
	var rotated rotationOutcome
	select {
	case rotated = <-rotationDone:
	case <-time.After(15 * time.Second):
		t.Fatal("rotation did not finish after the root-row barrier was released")
	}
	require.NoError(t, rotated.err)
	require.Equal(t, PlatformRelayServicePrincipalRotationStateRotated, rotated.result.State)
	require.Equal(t, len(desired), rotated.result.Count)
	require.Equal(t, len(desired), rotated.result.RotatedCount)
	require.True(t, cacheServer.Exists("token:"+common.GenerateHMAC(oldCachedKey)),
		"rotation must not depend on best-effort stale cache deletion")
	_, err = model.GetTokenByKey(oldCachedKey, false)
	require.ErrorIs(t, err, gorm.ErrRecordNotFound)
	_, err = model.ValidateUserToken(oldCachedKey)
	require.ErrorIs(t, err, model.ErrTokenInvalid)
	newCachedKey := strings.TrimPrefix(desired[0].UpstreamToken, "sk-")
	currentToken, err := model.ValidateUserToken(newCachedKey)
	require.NoError(t, err)
	require.Equal(t, oldCachedToken.Id, currentToken.Id)

	var insertErr error
	select {
	case insertErr = <-insertDone:
	case <-time.After(15 * time.Second):
		t.Fatal("queued protected token insert did not converge after rotation commit")
	}
	require.ErrorIs(t, insertErr, model.ErrTokenKeyConflict)
	for _, input := range append(append([]PlatformRelayServicePrincipalProvisionInput(nil), current...), desired...) {
		bare := strings.TrimPrefix(input.UpstreamToken, "sk-")
		require.False(t, strings.Contains(insertErr.Error(), input.UpstreamToken), "conflict error contained a secret canary")
		require.False(t, strings.Contains(insertErr.Error(), bare), "conflict error contained a secret canary")
	}

	replay, err := RotateProtectedPlatformRelayServicePrincipals(attemptID, current, desired)
	require.NoError(t, err)
	require.Equal(t, PlatformRelayServicePrincipalRotationStateReplayed, replay.State)
	require.Equal(t, rotated.result.AttemptID, replay.AttemptID)
	require.NoError(t, validateProtectedPlatformRelayServicePrincipalsTx(runtimeDB, expectedDesired),
		"rotation postcondition was not the exact desired principal set")
	var collisionProbeCount int64
	require.NoError(t, admin.Unscoped().Model(&model.Token{}).
		Where("name = ?", competing.Name).Count(&collisionProbeCount).Error)
	require.Zero(t, collisionProbeCount)
	for _, input := range current {
		var oldKeyCount int64
		require.NoError(t, admin.Unscoped().Model(&model.Token{}).
			Where("key = ?", strings.TrimPrefix(input.UpstreamToken, "sk-")).
			Count(&oldKeyCount).Error)
		require.Zero(t, oldKeyCount, "an old principal token survived the atomic rotation")
	}

	// Prove process death cannot expose a mixed set. The trigger records a
	// transaction-scoped advisory marker after the first sorted token update and
	// blocks the second update behind a session lock. Terminating that exact
	// backend must roll the first update back with the rest of the transaction.
	killTarget := platformRelayRotationPostgresInputs("relay-principal-rotation-pg-kill-v1")
	expectedKillTarget, err := buildProtectedPlatformRelayExpectedPrincipals(killTarget)
	require.NoError(t, err)
	purposes := make([]string, 0, len(desired))
	for _, input := range desired {
		purposes = append(purposes, protectedPlatformRelayTokenPurpose(input.ClientID, input.TenantID))
	}
	sort.Strings(purposes)
	require.GreaterOrEqual(t, len(purposes), 2)
	quoteLiteral := func(value string) string {
		var quoted string
		require.NoError(t, admin.Raw(`SELECT pg_catalog.quote_literal(?)`, value).Scan(&quoted).Error)
		return quoted
	}
	const killFunction = "platform_relay_rotation_kill_barrier_v1"
	const killTrigger = "platform_relay_rotation_kill_barrier_v1"
	functionSQL := fmt.Sprintf(`
CREATE OR REPLACE FUNCTION public.%s() RETURNS trigger
LANGUAGE plpgsql AS $rotation_kill$
BEGIN
  IF OLD.name = %s THEN
    PERFORM pg_catalog.pg_advisory_xact_lock(%d);
  ELSIF OLD.name = %s THEN
    PERFORM pg_catalog.pg_advisory_xact_lock(%d);
  END IF;
  RETURN NEW;
END
$rotation_kill$`, killFunction, quoteLiteral(purposes[0]), platformRelayRotationPostgresKillMarker,
		quoteLiteral(purposes[1]), platformRelayRotationPostgresKillBarrier)
	require.NoError(t, admin.Exec(functionSQL).Error)
	require.NoError(t, admin.Exec(fmt.Sprintf(`
CREATE TRIGGER %s BEFORE UPDATE OF key ON public.tokens
FOR EACH ROW EXECUTE FUNCTION public.%s()`, killTrigger, killFunction)).Error)
	triggerOpen := true
	t.Cleanup(func() {
		if triggerOpen {
			_ = admin.Exec("DROP TRIGGER IF EXISTS " + killTrigger + " ON public.tokens").Error
			_ = admin.Exec("DROP FUNCTION IF EXISTS public." + killFunction + "()").Error
		}
	})

	killBarrierHolder, err := adminSQL.Conn(context.Background())
	require.NoError(t, err)
	killBarrierHolderOpen := true
	t.Cleanup(func() {
		if killBarrierHolderOpen {
			_, _ = killBarrierHolder.ExecContext(
				context.Background(), `SELECT pg_catalog.pg_advisory_unlock($1)`,
				platformRelayRotationPostgresKillBarrier,
			)
			_ = killBarrierHolder.Close()
		}
	})
	_, err = killBarrierHolder.ExecContext(
		context.Background(), `SELECT pg_catalog.pg_advisory_lock($1)`,
		platformRelayRotationPostgresKillBarrier,
	)
	require.NoError(t, err)

	killDone := make(chan error, 1)
	go func() {
		_, rotationErr := RotateProtectedPlatformRelayServicePrincipals(
			strings.Repeat("e", 64), desired, killTarget,
		)
		killDone <- rotationErr
	}()
	killPID := 0
	require.Eventually(t, func() bool {
		killPID = 0
		err := admin.Raw(`SELECT pid
  FROM pg_catalog.pg_locks
 WHERE locktype = 'advisory'
   AND classid = ? AND objid = ? AND objsubid = 1
   AND mode = 'ExclusiveLock' AND NOT granted
 ORDER BY pid LIMIT 1`,
			platformRelayRotationPostgresKillBarrier>>32,
			platformRelayRotationPostgresKillBarrier&0xffffffff,
		).Scan(&killPID).Error
		return err == nil && killPID > 0
	}, 10*time.Second, 25*time.Millisecond, "kill barrier did not observe the second token update")
	var firstUpdateMarkerCount int64
	require.NoError(t, admin.Raw(`SELECT count(*)
  FROM pg_catalog.pg_locks
 WHERE pid = ? AND locktype = 'advisory'
   AND classid = ? AND objid = ? AND objsubid = 1
   AND mode = 'ExclusiveLock' AND granted`,
		killPID,
		platformRelayRotationPostgresKillMarker>>32,
		platformRelayRotationPostgresKillMarker&0xffffffff,
	).Scan(&firstUpdateMarkerCount).Error)
	require.Equal(t, int64(1), firstUpdateMarkerCount,
		"rotation backend reached the second update without proving the first update ran")
	var terminated bool
	require.NoError(t, admin.Raw(`SELECT pg_catalog.pg_terminate_backend(?)`, killPID).Scan(&terminated).Error)
	require.True(t, terminated)
	select {
	case killErr := <-killDone:
		require.ErrorIs(t, killErr, ErrProtectedPlatformRelayPrincipalRotationConflict)
	case <-time.After(15 * time.Second):
		t.Fatal("terminated rotation backend did not return")
	}
	require.NoError(t, validateProtectedPlatformRelayServicePrincipalsTx(runtimeDB, expectedDesired),
		"backend termination exposed a mixed principal state")
	for _, principal := range expectedKillTarget {
		var targetKeyCount int64
		require.NoError(t, admin.Unscoped().Model(&model.Token{}).
			Where("key = ?", principal.key).Count(&targetKeyCount).Error)
		require.Zero(t, targetKeyCount, "a killed transaction left a target token key behind")
	}
	_, err = killBarrierHolder.ExecContext(
		context.Background(), `SELECT pg_catalog.pg_advisory_unlock($1)`,
		platformRelayRotationPostgresKillBarrier,
	)
	require.NoError(t, err)
	require.NoError(t, killBarrierHolder.Close())
	killBarrierHolderOpen = false
	require.NoError(t, admin.Exec("DROP TRIGGER "+killTrigger+" ON public.tokens").Error)
	require.NoError(t, admin.Exec("DROP FUNCTION public."+killFunction+"()").Error)
	triggerOpen = false

	require.NoError(t, model.ReleaseRelayLifecycleLockBounded(lifecycleLock))
	lifecycleReleased = true

	applicationDiagnostic := applicationLog.String()
	allCredentialInputs := append(append(append(
		[]PlatformRelayServicePrincipalProvisionInput(nil), current...), desired...), killTarget...)
	for _, input := range allCredentialInputs {
		bare := strings.TrimPrefix(input.UpstreamToken, "sk-")
		canonicalDigest := sha256.Sum256([]byte(input.UpstreamToken))
		bareDigest := sha256.Sum256([]byte(bare))
		platformRelayRotationPostgresAssertNoSecret(
			t,
			applicationDiagnostic,
			input.UpstreamToken,
			bare,
			hex.EncodeToString(canonicalDigest[:]),
			hex.EncodeToString(bareDigest[:]),
		)
	}
	platformRelayRotationPostgresAssertNoSecret(
		t,
		applicationDiagnostic,
		runtimePassword,
		runtimeVerifier,
		runtimeDSN,
	)
	require.False(t, errors.Is(rotated.err, model.ErrTokenKeyConflict))
}
