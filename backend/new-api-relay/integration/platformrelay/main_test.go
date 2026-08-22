//go:build integration

package platformrelay_test

import (
	"context"
	"database/sql"
	"fmt"
	"net/url"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/go-redis/redis/v8"
	"github.com/google/uuid"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

var (
	integrationDB             *gorm.DB
	integrationSQLDB          *sql.DB
	integrationRedisOptions   *redis.Options
	integrationRedisNamespace string
	integrationSchema         string
)

var integrationIdentifierPattern = regexp.MustCompile(`^[a-z][a-z0-9_]{0,39}$`)

func TestMain(m *testing.M) {
	_ = os.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")
	_ = os.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"integration-v1","keys":{"integration-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)
	postgresDSN := strings.TrimSpace(os.Getenv("TEST_POSTGRES_DSN"))
	redisURL := strings.TrimSpace(os.Getenv("TEST_REDIS_URL"))
	if postgresDSN == "" || redisURL == "" {
		fmt.Fprintln(os.Stderr, "integration tests require TEST_POSTGRES_DSN and TEST_REDIS_URL")
		os.Exit(2)
	}

	suffix := strings.ReplaceAll(uuid.NewString(), "-", "")[:12]
	schemaPrefix := integrationIdentifier("TEST_RELAY_SCHEMA", "relay_it")
	namespacePrefix := integrationIdentifier("TEST_REDIS_NAMESPACE", "relay_it")
	integrationSchema = schemaPrefix + "_" + suffix
	integrationRedisNamespace = namespacePrefix + "-" + suffix

	adminDB := mustOpenPostgres(postgresDSN)
	if err := adminDB.Exec(fmt.Sprintf(`CREATE SCHEMA %q`, integrationSchema)).Error; err != nil {
		integrationFatal("create isolated PostgreSQL schema", err)
	}

	scopedDSN, err := postgresDSNWithSearchPath(postgresDSN, integrationSchema)
	if err != nil {
		integrationFatal("scope PostgreSQL DSN", err)
	}
	integrationDB = mustOpenPostgres(scopedDSN)
	integrationSQLDB, err = integrationDB.DB()
	if err != nil {
		integrationFatal("open PostgreSQL connection pool", err)
	}
	integrationSQLDB.SetMaxOpenConns(32)
	integrationSQLDB.SetMaxIdleConns(32)
	integrationSQLDB.SetConnMaxLifetime(5 * time.Minute)
	if err := integrationSQLDB.PingContext(context.Background()); err != nil {
		integrationFatal("ping PostgreSQL", err)
	}

	integrationRedisOptions, err = redis.ParseURL(redisURL)
	if err != nil {
		integrationFatal("parse Redis URL", err)
	}
	integrationRedisOptions.MaxRetries = -1
	integrationRedisOptions.PoolSize = 32
	redisClient := redis.NewClient(integrationRedisOptions)
	if err := redisClient.Ping(context.Background()).Err(); err != nil {
		integrationFatal("ping Redis", err)
	}

	model.DB = integrationDB
	model.LOG_DB = integrationDB
	common.SetDatabaseTypes(common.DatabaseTypePostgreSQL, common.DatabaseTypePostgreSQL)
	common.RedisEnabled = true
	common.RDB = redisClient
	common.BatchUpdateEnabled = false

	if err := integrationDB.AutoMigrate(
		&model.Channel{},
		&model.ProviderChannelCredentialSetVersion{},
		&model.ProviderCredentialVersion{},
		&model.Task{},
		&model.Ability{},
		&model.PlatformGenerationJob{},
		&model.PlatformGenerationOutbox{},
		&model.PlatformArtifactUploadIntent{},
		&model.PlatformGenerationProviderAccountState{},
		&model.PlatformGenerationProviderRoute{},
		&model.PlatformGenerationRouteAdmission{},
		&model.PlatformGenerationReconciliationEvent{},
		&model.PlatformGenerationCallbackDelivery{},
	); err != nil {
		integrationFatal("migrate Relay integration schema", err)
	}
	if err := model.MigratePlatformGenerationReconciliationStorage(); err != nil {
		integrationFatal("migrate reconciliation receipt storage", err)
	}
	if err := model.MigrateProviderCredentialVaultStorage(); err != nil {
		integrationFatal("migrate provider credential vault storage", err)
	}
	if err := model.MigrateProviderChannelCredentialVaultStorage(); err != nil {
		integrationFatal("migrate provider channel credential vault storage", err)
	}
	if err := model.MigratePlatformGenerationCallbackOperationsStorage(); err != nil {
		integrationFatal("migrate callback redrive receipt storage", err)
	}
	if err := model.MigratePlatformChannelControlStorage(); err != nil {
		integrationFatal("migrate channel control receipt storage", err)
	}

	exitCode := m.Run()
	deleteRedisNamespace(context.Background(), redisClient, integrationRedisNamespace)
	_ = redisClient.Close()
	_ = integrationSQLDB.Close()
	if sqlDB, openErr := adminDB.DB(); openErr == nil {
		_ = adminDB.Exec(fmt.Sprintf(`DROP SCHEMA IF EXISTS %q CASCADE`, integrationSchema)).Error
		_ = sqlDB.Close()
	}
	os.Exit(exitCode)
}

func integrationIdentifier(envName string, fallback string) string {
	value := strings.ToLower(strings.TrimSpace(os.Getenv(envName)))
	if value == "" {
		value = fallback
	}
	if !integrationIdentifierPattern.MatchString(value) {
		integrationFatal(envName+" must match "+integrationIdentifierPattern.String(), nil)
	}
	return value
}

func integrationFatal(message string, err error) {
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", message, err)
	} else {
		fmt.Fprintln(os.Stderr, message)
	}
	os.Exit(2)
}

func mustOpenPostgres(dsn string) *gorm.DB {
	db, err := gorm.Open(postgres.New(postgres.Config{
		DSN:                  dsn,
		PreferSimpleProtocol: true,
	}), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		integrationFatal("open PostgreSQL", err)
	}
	return db
}

func postgresDSNWithSearchPath(dsn string, schema string) (string, error) {
	parsed, err := url.Parse(dsn)
	if err != nil {
		return "", err
	}
	if parsed.Scheme != "postgres" && parsed.Scheme != "postgresql" {
		return "", fmt.Errorf("TEST_POSTGRES_DSN must be a postgres URL")
	}
	query := parsed.Query()
	query.Set("search_path", schema)
	parsed.RawQuery = query.Encode()
	return parsed.String(), nil
}

func resetIntegrationState(t *testing.T) {
	t.Helper()
	requireNoError(t, integrationDB.Exec(`
		TRUNCATE TABLE
			platform_generation_callback_deliveries,
			tasks,
			platform_artifact_upload_intents,
			platform_generation_route_admissions,
			platform_generation_provider_routes,
			platform_generation_provider_account_states,
			platform_generation_outboxes,
			platform_generation_jobs,
			channels
		RESTART IDENTITY CASCADE
	`).Error)
}

func newRedisClient(t *testing.T) *redis.Client {
	t.Helper()
	options := *integrationRedisOptions
	client := redis.NewClient(&options)
	requireNoError(t, client.Ping(context.Background()).Err())
	t.Cleanup(func() { _ = client.Close() })
	return client
}

func testRedisNamespace(t *testing.T) string {
	t.Helper()
	name := strings.ToLower(t.Name())
	name = strings.NewReplacer("/", "-", "_", "-", " ", "-").Replace(name)
	if len(name) > 40 {
		name = name[:40]
	}
	namespace := integrationRedisNamespace + "-" + name
	t.Cleanup(func() {
		deleteRedisNamespace(context.Background(), common.RDB, namespace)
	})
	return namespace
}

func deleteRedisNamespace(ctx context.Context, client *redis.Client, namespace string) {
	if client == nil || namespace == "" {
		return
	}
	var cursor uint64
	for {
		keys, next, err := client.Scan(ctx, cursor, "*"+namespace+"*", 128).Result()
		if err != nil {
			return
		}
		if len(keys) > 0 {
			_ = client.Del(ctx, keys...).Err()
		}
		cursor = next
		if cursor == 0 {
			return
		}
	}
}

func requireNoError(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}
