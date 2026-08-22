package model

import (
	"context"
	"crypto/x509"
	"database/sql"
	"database/sql/driver"
	"errors"
	"fmt"
	"log"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"

	"github.com/glebarez/sqlite"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"gorm.io/driver/clickhouse"
	"gorm.io/driver/mysql"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
)

var commonGroupCol string
var commonKeyCol string
var commonTrueVal string
var commonFalseVal string

var logKeyCol string
var logGroupCol string

func initCol() {
	// init common column names
	if common.UsingMainDatabase(common.DatabaseTypePostgreSQL) {
		commonGroupCol = `"group"`
		commonKeyCol = `"key"`
		commonTrueVal = "true"
		commonFalseVal = "false"
	} else {
		commonGroupCol = "`group`"
		commonKeyCol = "`key`"
		commonTrueVal = "1"
		commonFalseVal = "0"
	}
	switch common.LogDatabaseType() {
	case common.DatabaseTypePostgreSQL:
		logGroupCol = `"group"`
		logKeyCol = `"key"`
	default:
		logGroupCol = "`group`"
		logKeyCol = "`key`"
	}
}

var DB *gorm.DB

var LOG_DB *gorm.DB

var relayRuntimeDatabaseLifecycleFencing atomic.Bool
var relayRuntimeDatabaseLifecycleHealthy atomic.Bool
var relayRuntimeLifecycleDSNMu sync.RWMutex
var relayRuntimeLifecycleDSN string

// EnableRelayRuntimeDatabaseLifecycleFencing must be called before InitDB by
// long-lived API/edge processes. Offline commands never enable it: they take
// the exclusive side inside their actual mutation transactions instead.
func EnableRelayRuntimeDatabaseLifecycleFencing() {
	relayRuntimeDatabaseLifecycleFencing.Store(true)
	relayRuntimeDatabaseLifecycleHealthy.Store(false)
}

func RelayRuntimeDatabaseLifecycleFencingEnabled() bool {
	return relayRuntimeDatabaseLifecycleFencing.Load()
}

func RelayRuntimeDatabaseLifecycleHealthy() bool {
	return !relayRuntimeDatabaseLifecycleFencing.Load() || relayRuntimeDatabaseLifecycleHealthy.Load()
}

func setRelayRuntimeLifecycleDSNSnapshot(dsn string) error {
	relayRuntimeLifecycleDSNMu.Lock()
	defer relayRuntimeLifecycleDSNMu.Unlock()
	if relayRuntimeLifecycleDSN != "" && relayRuntimeLifecycleDSN != dsn {
		return errors.New("Relay runtime database DSN snapshot changed during process startup")
	}
	relayRuntimeLifecycleDSN = dsn
	return nil
}

func getRelayRuntimeLifecycleDSNSnapshot() string {
	relayRuntimeLifecycleDSNMu.RLock()
	defer relayRuntimeLifecycleDSNMu.RUnlock()
	return relayRuntimeLifecycleDSN
}

func CheckSetup() error {
	protectedSetup := common.IsProductionEnvironment() || RelayDatabaseRoleAttestationRequired()
	setup := GetSetup()
	if setup == nil {
		// No setup record exists, check if we have a root user
		if RootUserExists() {
			if protectedSetup {
				constant.Setup = false
				return fmt.Errorf("protected startup rejects a partial root-only setup state")
			}
			common.SysLog("system is not initialized, but root user exists")
			// Create setup record
			newSetup := Setup{
				Version:       common.Version,
				InitializedAt: time.Now().Unix(),
			}
			err := DB.Create(&newSetup).Error
			if err != nil {
				common.SysLog("failed to create setup record: " + err.Error())
			}
			constant.Setup = true
		} else {
			common.SysLog("system is not initialized and no root user exists")
			constant.Setup = false
		}
	} else {
		// Setup record exists, system is initialized
		common.SysLog("system is already initialized at: " + time.Unix(setup.InitializedAt, 0).String())
		constant.Setup = true
	}
	if protectedSetup && !SetupReady() {
		constant.Setup = false
		return fmt.Errorf("protected startup requires a completed setup record and a root user provisioned out of band")
	}
	return nil
}

func isClickHouseDSN(dsn string) bool {
	return strings.HasPrefix(dsn, "clickhouse://") ||
		strings.HasPrefix(dsn, "tcp://") ||
		strings.HasPrefix(dsn, "http://") ||
		strings.HasPrefix(dsn, "https://")
}

func normalizeClickHouseDSN(dsn string) string {
	parsed, err := url.Parse(dsn)
	if err != nil || parsed.Scheme != "https" {
		return dsn
	}
	query := parsed.Query()
	if _, ok := query["secure"]; !ok {
		query.Set("secure", "true")
		parsed.RawQuery = query.Encode()
	}
	return parsed.String()
}

// ResolveDatabaseDSN reads either ENV or ENV_FILE, never both. Protected
// deployments use the file form so rendered Compose config, container argv
// and process environment contain only a non-secret absolute path.
func ResolveDatabaseDSN(envName string) (string, error) {
	raw, rawPresent := os.LookupEnv(envName)
	fileName := os.Getenv(envName + "_FILE")
	requireProtectedFile := common.IsProductionEnvironment() ||
		RelayDatabaseRoleAttestationRequired() || RelayDatabaseSecretFilesRequired()
	if rawMode, present := os.LookupEnv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED"); present && strings.TrimSpace(rawMode) != "" {
		requireProtectedFile = requireProtectedFile || !strings.EqualFold(strings.TrimSpace(rawMode), "false")
	}
	if requireProtectedFile && rawPresent {
		return "", fmt.Errorf("%s environment value is forbidden; use %s_FILE", envName, envName)
	}
	if raw != "" && fileName != "" {
		return "", fmt.Errorf("%s and %s_FILE are mutually exclusive", envName, envName)
	}
	if fileName == "" {
		return raw, nil
	}
	if requireProtectedFile {
		valueBytes, err := common.ReadProtectedSecretFile(envName+"_FILE", 16*1024)
		if err != nil {
			return "", fmt.Errorf("%s_FILE is unavailable or invalid", envName)
		}
		defer clear(valueBytes)
		value := string(valueBytes)
		if value == "" || strings.TrimSpace(value) != value || strings.ContainsAny(value, "\x00\r\n") {
			return "", fmt.Errorf("%s_FILE content is invalid", envName)
		}
		return value, nil
	}
	if strings.TrimSpace(fileName) != fileName || strings.ContainsAny(fileName, "\x00\r\n") || !filepath.IsAbs(fileName) {
		return "", fmt.Errorf("%s_FILE path is invalid", envName)
	}
	info, err := os.Lstat(fileName)
	if err != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > 16*1024 {
		return "", fmt.Errorf("%s_FILE is unavailable or invalid", envName)
	}
	valueBytes, err := os.ReadFile(fileName)
	if err != nil {
		return "", fmt.Errorf("%s_FILE could not be read", envName)
	}
	defer clear(valueBytes)
	value := string(valueBytes)
	if value == "" || strings.TrimSpace(value) != value || strings.ContainsAny(value, "\x00\r\n") {
		return "", fmt.Errorf("%s_FILE content is invalid", envName)
	}
	return value, nil
}

// parseRelayPostgresConfig consumes the already receipt-pinned CA bytes rather
// than allowing pgx to reopen sslrootcert by pathname after verification. The
// original DSN remains the source of the exact protected transport policy; only
// the driver-facing copy drops sslrootcert before installing the same in-memory
// trust pool on the parsed connection config.
func parseRelayPostgresConfig(dsn string) (*pgx.ConnConfig, error) {
	if err := ValidateRelayProtectedPostgresClientEnvironment(); err != nil {
		return nil, err
	}
	if !RelayDatabaseTLSAttestationRequired() {
		configuration, err := pgx.ParseConfig(dsn)
		if err != nil {
			return nil, errors.New("PostgreSQL configuration is invalid")
		}
		return configuration, nil
	}
	parsed, err := url.Parse(dsn)
	if err != nil {
		return nil, errors.New("PostgreSQL configuration is invalid")
	}
	query := parsed.Query()
	expectedCAPath := os.Getenv(relayDatabaseCAFileEnvironment)
	if expectedCAPath == "" || len(query["sslrootcert"]) != 1 || query.Get("sslrootcert") != expectedCAPath {
		return nil, errors.New("PostgreSQL TLS root configuration is invalid")
	}
	caBytes, err := common.ReadProtectedSecretFile(relayDatabaseCAFileEnvironment, 256*1024)
	if err != nil {
		return nil, errors.New("PostgreSQL TLS root certificate is unavailable")
	}
	defer clear(caBytes)
	rootCertificates := x509.NewCertPool()
	if !rootCertificates.AppendCertsFromPEM(caBytes) {
		return nil, errors.New("PostgreSQL TLS root certificate is invalid")
	}
	query.Del("sslrootcert")
	parsed.RawQuery = query.Encode()
	configuration, err := pgx.ParseConfig(parsed.String())
	if err != nil || configuration.TLSConfig == nil {
		return nil, errors.New("PostgreSQL configuration is invalid")
	}
	configuration.TLSConfig.RootCAs = rootCertificates
	return configuration, nil
}

func chooseDB(envName string, isLog bool) (*gorm.DB, common.DatabaseType, error) {
	dsn, resolveErr := ResolveDatabaseDSN(envName)
	if resolveErr != nil {
		return nil, "", resolveErr
	}
	if dsn != "" {
		if strings.HasPrefix(dsn, "postgres://") || strings.HasPrefix(dsn, "postgresql://") {
			if err := ValidateRelayPostgresSearchPathDSN(dsn); err != nil {
				return nil, "", err
			}
			if err := ValidateRelayPostgresTransportDSN(dsn); err != nil {
				return nil, "", err
			}
		}
		if isClickHouseDSN(dsn) {
			if !isLog {
				return nil, "", fmt.Errorf("%s does not support ClickHouse; use SQLite, MySQL, or PostgreSQL for the primary database and LOG_SQL_DSN for ClickHouse logs", envName)
			}
			common.SysLog("using ClickHouse as log database")
			db, err := gorm.Open(clickhouse.Open(normalizeClickHouseDSN(dsn)), newGormConfig(false))
			return db, common.DatabaseTypeClickHouse, err
		}
		if strings.HasPrefix(dsn, "postgres://") || strings.HasPrefix(dsn, "postgresql://") {
			// Use PostgreSQL
			common.SysLog("using PostgreSQL as database")
			if !isLog && relayRuntimeDatabaseLifecycleFencing.Load() {
				if err := setRelayRuntimeLifecycleDSNSnapshot(dsn); err != nil {
					return nil, "", err
				}
				connectionConfig, err := parseRelayPostgresConfig(dsn)
				if err != nil {
					return nil, "", errors.New("Relay runtime PostgreSQL configuration is invalid")
				}
				// Extended protocol with QueryExecModeExec keeps bind values out of
				// SQL statement text without relying on a prepared-statement cache.
				// Offline principal provisioning stores raw service tokens and must
				// never interpolate them into server-visible SQL.
				connectionConfig.DefaultQueryExecMode = pgx.QueryExecModeExec
				expectedRole := connectionConfig.User
				lockedPool := stdlib.OpenDB(*connectionConfig,
					stdlib.OptionAfterConnect(func(ctx context.Context, connection *pgx.Conn) (callbackErr error) {
						// pgx stdlib does not close a freshly opened physical connection
						// when OptionAfterConnect returns an error. Close it here so a
						// failed anchor proof cannot leak shared A/B locks outside the
						// database/sql pool's ownership.
						defer func() {
							if callbackErr == nil {
								return
							}
							closeContext, cancelClose := context.WithTimeout(context.Background(), 2*time.Second)
							defer cancelClose()
							_ = connection.Close(closeContext)
						}()
						if !RelayRuntimeDatabaseLifecycleHealthy() {
							return errors.New("Relay runtime database lifecycle is not healthy")
						}
						if _, err := connection.Exec(ctx, `SELECT pg_catalog.pg_advisory_lock_shared($1)`, relayLifecycleAdvisoryLock); err != nil {
							return errors.New("Relay runtime database connection process fence could not be acquired")
						}
						if _, err := connection.Exec(ctx, `SELECT pg_catalog.pg_advisory_lock_shared($1)`, relayLifecycleMutationAdvisoryLock); err != nil {
							return errors.New("Relay runtime database connection mutation fence could not be acquired")
						}
						if !RelayRuntimeDatabaseLifecycleHealthy() {
							return errors.New("Relay runtime database lifecycle was lost while opening a connection")
						}
						return verifyRelayRuntimeDatabaseConnectionSession(ctx, connection, expectedRole)
					}),
					stdlib.OptionResetSession(func(ctx context.Context, connection *pgx.Conn) error {
						if !RelayRuntimeDatabaseLifecycleHealthy() {
							return driver.ErrBadConn
						}
						if err := verifyRelayRuntimeDatabaseConnectionSession(ctx, connection, expectedRole); err != nil {
							return driver.ErrBadConn
						}
						return nil
					}),
				)
				gormConfig := newGormConfig(true)
				// Runtime acquires process lock A before the first physical pool
				// connection may acquire mutation lock B. Automatic ping would
				// invert A -> B and deadlock an in-flight offline mutation.
				gormConfig.DisableAutomaticPing = true
				db, openErr := gorm.Open(postgres.New(postgres.Config{
					Conn:                 lockedPool,
					PreferSimpleProtocol: false,
				}), gormConfig)
				if openErr != nil {
					_ = lockedPool.Close()
				}
				return db, common.DatabaseTypePostgreSQL, openErr
			}
			connectionConfig, err := parseRelayPostgresConfig(dsn)
			if err != nil {
				return nil, "", errors.New("PostgreSQL configuration is invalid")
			}
			connectionConfig.DefaultQueryExecMode = pgx.QueryExecModeExec
			pool := stdlib.OpenDB(*connectionConfig)
			db, openErr := gorm.Open(postgres.New(postgres.Config{
				Conn:                 pool,
				PreferSimpleProtocol: false,
			}), newGormConfig(true))
			if openErr != nil {
				_ = pool.Close()
			}
			return db, common.DatabaseTypePostgreSQL, openErr
		}
		if strings.HasPrefix(dsn, "local") {
			common.SysLog("SQL_DSN not set, using SQLite as database")
			db, err := gorm.Open(sqlite.Open(common.SQLitePath), newGormConfig(true))
			return db, common.DatabaseTypeSQLite, err
		}
		// Use MySQL
		common.SysLog("using MySQL as database")
		// check parseTime
		if !strings.Contains(dsn, "parseTime") {
			if strings.Contains(dsn, "?") {
				dsn += "&parseTime=true"
			} else {
				dsn += "?parseTime=true"
			}
		}
		db, err := gorm.Open(mysql.Open(dsn), newGormConfig(true))
		return db, common.DatabaseTypeMySQL, err
	}
	// Use SQLite
	common.SysLog("SQL_DSN not set, using SQLite as database")
	db, err := gorm.Open(sqlite.Open(common.SQLitePath), newGormConfig(true))
	return db, common.DatabaseTypeSQLite, err
}

func verifyRelayRuntimeDatabaseConnectionSession(ctx context.Context, connection *pgx.Conn, expectedRole string) error {
	var parameterLength string
	var errorParameterLength string
	var autoExplainParameterLength sql.NullString
	var pgAuditParameter sql.NullString
	if err := connection.QueryRow(ctx, `SELECT
  current_setting('log_parameter_max_length'),
  current_setting('log_parameter_max_length_on_error'),
  current_setting('auto_explain.log_parameter_max_length', true),
  current_setting('pgaudit.log_parameter', true)
`).Scan(&parameterLength, &errorParameterLength, &autoExplainParameterLength, &pgAuditParameter); err != nil ||
		parameterLength != "0" || errorParameterLength != "0" {
		return errors.New("Relay runtime database parameter logging policy is not exact")
	}
	if !autoExplainParameterLength.Valid || strings.TrimSpace(autoExplainParameterLength.String) != "0" {
		return errors.New("Relay runtime database auto-explain parameter logging must be disabled")
	}
	if !pgAuditParameter.Valid || strings.ToLower(strings.TrimSpace(pgAuditParameter.String)) != "off" {
		return errors.New("Relay runtime database audit parameter logging must be disabled")
	}
	anchorPID, anchorStart, anchorEpoch, anchorAvailable := getRelayRuntimeAnchorIdentity()
	if !anchorAvailable {
		return errors.New("Relay runtime database anchor identity is unavailable")
	}
	var anchorExact bool
	if err := connection.QueryRow(ctx, `SELECT EXISTS (
  SELECT 1
    FROM pg_catalog.pg_stat_activity activity
    JOIN pg_catalog.pg_locks process_lock ON process_lock.pid = activity.pid
    JOIN pg_catalog.pg_locks epoch_lock ON epoch_lock.pid = activity.pid
   WHERE activity.pid = $1
     AND activity.backend_start = $2
     AND activity.datid = (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database())
     AND process_lock.locktype = 'advisory'
     AND process_lock.classid = (($3::bigint >> 32) & 4294967295)::oid
     AND process_lock.objid = ($3::bigint & 4294967295)::oid
     AND process_lock.objsubid = 1
     AND process_lock.mode = 'ShareLock'
     AND process_lock.granted
     AND epoch_lock.locktype = 'advisory'
     AND epoch_lock.classid = (($4::bigint >> 32) & 4294967295)::oid
     AND epoch_lock.objid = ($4::bigint & 4294967295)::oid
     AND epoch_lock.objsubid = 1
     AND epoch_lock.mode = 'ExclusiveLock'
     AND epoch_lock.granted
)`, anchorPID, anchorStart, relayLifecycleAdvisoryLock, anchorEpoch).Scan(&anchorExact); err != nil || !anchorExact {
		return errors.New("Relay runtime database anchor identity is not exact")
	}
	var exact bool
	if err := connection.QueryRow(ctx, `SELECT session_user = $1
  AND current_user = $1
  AND current_setting('search_path') = 'public'
  AND current_schema() = 'public'
  AND current_schemas(true) = ARRAY['pg_catalog', 'public']::name[]
  AND current_setting('row_security') = 'on'
  AND current_setting('session_replication_role') = 'origin'
  AND (SELECT count(*)
         FROM pg_catalog.pg_locks
        WHERE pid = pg_catalog.pg_backend_pid()
          AND locktype = 'advisory'
          AND classid = (($2::bigint >> 32) & 4294967295)::oid
          AND objid IN (($2::bigint & 4294967295)::oid, ($3::bigint & 4294967295)::oid)
          AND objsubid = 1
          AND mode = 'ShareLock'
          AND granted) = 2`, expectedRole, relayLifecycleAdvisoryLock, relayLifecycleMutationAdvisoryLock).Scan(&exact); err != nil || !exact {
		return errors.New("Relay runtime database connection session contract is not exact")
	}
	return verifyRelayInstalledDatabaseReleaseIdentityConnection(ctx, connection)
}

func InitDB() (err error) {
	db, dbType, err := chooseDB("SQL_DSN", false)
	if err == nil {
		common.SetMainDatabaseType(dbType)
		if os.Getenv("LOG_SQL_DSN") == "" {
			common.SetLogDatabaseType(dbType)
		}
		initCol()
		if common.DebugEnabled {
			db = db.Debug()
		}
		DB = db
		// MySQL charset/collation startup check: ensure Chinese-capable charset
		if common.UsingMainDatabase(common.DatabaseTypeMySQL) {
			if err := checkMySQLChineseSupport(DB); err != nil {
				panic(err)
			}
		}
		sqlDB, err := DB.DB()
		if err != nil {
			return err
		}
		sqlDB.SetMaxIdleConns(common.GetEnvOrDefault("SQL_MAX_IDLE_CONNS", 100))
		sqlDB.SetMaxOpenConns(common.GetEnvOrDefault("SQL_MAX_OPEN_CONNS", 1000))
		sqlDB.SetConnMaxLifetime(time.Second * time.Duration(common.GetEnvOrDefault("SQL_MAX_LIFETIME", 60)))
		// Runtime initialization is deliberately connection-only. Schema and
		// data migrations are owned exclusively by the relay-migrate one-shot,
		// regardless of NODE_TYPE.
		return nil
	} else {
		common.FatalLog(err)
	}
	return err
}

func InitLogDB() (err error) {
	if os.Getenv("LOG_SQL_DSN") == "" {
		LOG_DB = DB
		common.SetLogDatabaseType(common.MainDatabaseType())
		initCol()
		return
	}
	db, dbType, err := chooseDB("LOG_SQL_DSN", true)
	if err == nil {
		common.SetLogDatabaseType(dbType)
		initCol()
		if common.DebugEnabled {
			db = db.Debug()
		}
		LOG_DB = db
		// If log DB is MySQL, also ensure Chinese-capable charset
		if common.UsingLogDatabase(common.DatabaseTypeMySQL) {
			if err := checkMySQLChineseSupport(LOG_DB); err != nil {
				panic(err)
			}
		}
		sqlDB, err := LOG_DB.DB()
		if err != nil {
			return err
		}
		sqlDB.SetMaxIdleConns(common.GetEnvOrDefault("SQL_MAX_IDLE_CONNS", 100))
		sqlDB.SetMaxOpenConns(common.GetEnvOrDefault("SQL_MAX_OPEN_CONNS", 1000))
		sqlDB.SetConnMaxLifetime(time.Second * time.Duration(common.GetEnvOrDefault("SQL_MAX_LIFETIME", 60)))
		// A separate log database is also opened without DDL. relay-migrate owns
		// its schema before any long-lived process is admitted.
		return nil
	} else {
		common.FatalLog(err)
	}
	return err
}

func migrateLOGDB() error {
	if common.UsingLogDatabase(common.DatabaseTypeClickHouse) {
		return migrateClickHouseLogDB()
	}
	return LOG_DB.AutoMigrate(&Log{})
}

func migrateClickHouseLogDB() error {
	ttlDays := clickHouseLogTTLDays()
	if err := LOG_DB.Exec(clickHouseLogCreateTableSQL(ttlDays)).Error; err != nil {
		return err
	}
	return syncClickHouseLogTTL(ttlDays)
}

func clickHouseLogTTLDays() int {
	ttlDays := common.GetEnvOrDefault("LOG_SQL_CLICKHOUSE_TTL_DAYS", 0)
	if ttlDays < 0 {
		return 0
	}
	return ttlDays
}

func clickHouseLogTTLExpression(ttlDays int) string {
	if ttlDays <= 0 {
		return ""
	}
	return fmt.Sprintf("toDateTime(created_at) + INTERVAL %d DAY DELETE", ttlDays)
}

func clickHouseLogTTLClause(ttlDays int) string {
	expression := clickHouseLogTTLExpression(ttlDays)
	if expression == "" {
		return ""
	}
	return "\nTTL " + expression
}

func clickHouseLogCreateTableSQL(ttlDays int) string {
	return fmt.Sprintf(`
CREATE TABLE IF NOT EXISTS logs (
	id Int64 DEFAULT 0,
	user_id Int32 DEFAULT 0,
	created_at Int64 DEFAULT 0,
	type Int32 DEFAULT 0,
	content String DEFAULT '',
	username String DEFAULT '',
	token_name String DEFAULT '',
	model_name String DEFAULT '',
	quota Int32 DEFAULT 0,
	prompt_tokens Int32 DEFAULT 0,
	completion_tokens Int32 DEFAULT 0,
	use_time Int32 DEFAULT 0,
	is_stream UInt8 DEFAULT 0,
	channel_id Int32 DEFAULT 0,
	token_id Int32 DEFAULT 0,
	`+"`group`"+` String DEFAULT '',
	ip String DEFAULT '',
	request_id String DEFAULT '',
	upstream_request_id String DEFAULT '',
	other String DEFAULT ''
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(toDateTime(created_at))
ORDER BY (created_at, request_id)%s`, clickHouseLogTTLClause(ttlDays))
}

func syncClickHouseLogTTL(ttlDays int) error {
	expression := clickHouseLogTTLExpression(ttlDays)
	if expression != "" {
		return LOG_DB.Exec("ALTER TABLE logs MODIFY TTL " + expression).Error
	}

	hasTTL, err := clickHouseLogTableHasTTL()
	if err != nil {
		return err
	}
	if !hasTTL {
		return nil
	}
	return LOG_DB.Exec("ALTER TABLE logs REMOVE TTL").Error
}

func clickHouseLogTableHasTTL() (bool, error) {
	var createTableSQL string
	if err := LOG_DB.Raw("SHOW CREATE TABLE logs").Scan(&createTableSQL).Error; err != nil {
		return false, err
	}
	return clickHouseCreateTableHasTTL(createTableSQL), nil
}

func clickHouseCreateTableHasTTL(createTableSQL string) bool {
	upperSQL := strings.ToUpper(createTableSQL)
	return strings.Contains(upperSQL, "\nTTL ") || strings.Contains(upperSQL, " TTL ")
}

type sqliteColumnDef struct {
	Name string
	DDL  string
}

func ensureSubscriptionPlanTableSQLite() error {
	return ensureSubscriptionPlanTableSQLiteWithDB(DB)
}

func ensureSubscriptionPlanTableSQLiteWithDB(db *gorm.DB) error {
	if db == nil || db.Dialector.Name() != "sqlite" {
		return nil
	}
	tableName := "subscription_plans"
	if !db.Migrator().HasTable(tableName) {
		createSQL := `CREATE TABLE ` + "`" + tableName + "`" + ` (
` + "`id`" + ` integer,
` + "`title`" + ` varchar(128) NOT NULL,
` + "`subtitle`" + ` varchar(255) DEFAULT '',
` + "`price_amount`" + ` decimal(10,6) NOT NULL,
` + "`currency`" + ` varchar(8) NOT NULL DEFAULT 'USD',
` + "`duration_unit`" + ` varchar(16) NOT NULL DEFAULT 'month',
` + "`duration_value`" + ` integer NOT NULL DEFAULT 1,
` + "`custom_seconds`" + ` bigint NOT NULL DEFAULT 0,
` + "`enabled`" + ` numeric DEFAULT 1,
` + "`sort_order`" + ` integer DEFAULT 0,
` + "`allow_balance_pay`" + ` numeric DEFAULT 1,
` + "`allow_wallet_overflow`" + ` numeric DEFAULT 1,
` + "`stripe_price_id`" + ` varchar(128) DEFAULT '',
` + "`creem_product_id`" + ` varchar(128) DEFAULT '',
` + "`waffo_pancake_product_id`" + ` varchar(128) DEFAULT '',
` + "`max_purchase_per_user`" + ` integer DEFAULT 0,
` + "`upgrade_group`" + ` varchar(64) DEFAULT '',
` + "`downgrade_group`" + ` varchar(64) DEFAULT '',
` + "`total_amount`" + ` bigint NOT NULL DEFAULT 0,
` + "`quota_reset_period`" + ` varchar(16) DEFAULT 'never',
` + "`quota_reset_custom_seconds`" + ` bigint DEFAULT 0,
` + "`created_at`" + ` bigint,
` + "`updated_at`" + ` bigint,
PRIMARY KEY (` + "`id`" + `)
)`
		return db.Exec(createSQL).Error
	}
	var cols []struct {
		Name string `gorm:"column:name"`
	}
	if err := db.Raw("PRAGMA table_info(`" + tableName + "`)").Scan(&cols).Error; err != nil {
		return err
	}
	existing := make(map[string]struct{}, len(cols))
	for _, c := range cols {
		existing[c.Name] = struct{}{}
	}
	required := []sqliteColumnDef{
		{Name: "title", DDL: "`title` varchar(128) NOT NULL"},
		{Name: "subtitle", DDL: "`subtitle` varchar(255) DEFAULT ''"},
		{Name: "price_amount", DDL: "`price_amount` decimal(10,6) NOT NULL"},
		{Name: "currency", DDL: "`currency` varchar(8) NOT NULL DEFAULT 'USD'"},
		{Name: "duration_unit", DDL: "`duration_unit` varchar(16) NOT NULL DEFAULT 'month'"},
		{Name: "duration_value", DDL: "`duration_value` integer NOT NULL DEFAULT 1"},
		{Name: "custom_seconds", DDL: "`custom_seconds` bigint NOT NULL DEFAULT 0"},
		{Name: "enabled", DDL: "`enabled` numeric DEFAULT 1"},
		{Name: "sort_order", DDL: "`sort_order` integer DEFAULT 0"},
		{Name: "allow_balance_pay", DDL: "`allow_balance_pay` numeric DEFAULT 1"},
		{Name: "allow_wallet_overflow", DDL: "`allow_wallet_overflow` numeric DEFAULT 1"},
		{Name: "stripe_price_id", DDL: "`stripe_price_id` varchar(128) DEFAULT ''"},
		{Name: "creem_product_id", DDL: "`creem_product_id` varchar(128) DEFAULT ''"},
		{Name: "waffo_pancake_product_id", DDL: "`waffo_pancake_product_id` varchar(128) DEFAULT ''"},
		{Name: "max_purchase_per_user", DDL: "`max_purchase_per_user` integer DEFAULT 0"},
		{Name: "upgrade_group", DDL: "`upgrade_group` varchar(64) DEFAULT ''"},
		{Name: "downgrade_group", DDL: "`downgrade_group` varchar(64) DEFAULT ''"},
		{Name: "total_amount", DDL: "`total_amount` bigint NOT NULL DEFAULT 0"},
		{Name: "quota_reset_period", DDL: "`quota_reset_period` varchar(16) DEFAULT 'never'"},
		{Name: "quota_reset_custom_seconds", DDL: "`quota_reset_custom_seconds` bigint DEFAULT 0"},
		{Name: "created_at", DDL: "`created_at` bigint"},
		{Name: "updated_at", DDL: "`updated_at` bigint"},
	}
	for _, col := range required {
		if _, ok := existing[col.Name]; ok {
			continue
		}
		if err := db.Exec("ALTER TABLE `" + tableName + "` ADD COLUMN " + col.DDL).Error; err != nil {
			return err
		}
	}
	return nil
}

// migrateTokenModelLimitsToText migrates model_limits column from varchar(1024) to text
// This is safe to run multiple times - it checks the column type first
func migrateTokenModelLimitsToText() error {
	return migrateTokenModelLimitsToTextWithDB(DB)
}

func migrateTokenModelLimitsToTextWithDB(db *gorm.DB) error {
	// SQLite uses type affinity, so TEXT and VARCHAR are effectively the same — no migration needed
	if db == nil || db.Dialector.Name() == "sqlite" {
		return nil
	}

	tableName := "tokens"
	columnName := "model_limits"

	if !db.Migrator().HasTable(tableName) {
		return nil
	}

	if !db.Migrator().HasColumn(&Token{}, columnName) {
		return nil
	}

	var alterSQL string
	if db.Dialector.Name() == "postgres" {
		var dataType string
		if err := db.Raw(`SELECT data_type FROM information_schema.columns
			WHERE table_schema = current_schema() AND table_name = ? AND column_name = ?`,
			tableName, columnName).Scan(&dataType).Error; err != nil {
			common.SysLog(fmt.Sprintf("Warning: failed to query metadata for %s.%s: %v", tableName, columnName, err))
		} else if dataType == "text" {
			return nil
		}
		alterSQL = fmt.Sprintf(`ALTER TABLE %s ALTER COLUMN %s TYPE text`, tableName, columnName)
	} else if db.Dialector.Name() == "mysql" {
		var columnType string
		if err := db.Raw(`SELECT COLUMN_TYPE FROM information_schema.columns
				WHERE table_schema = DATABASE() AND table_name = ? AND column_name = ?`,
			tableName, columnName).Scan(&columnType).Error; err != nil {
			common.SysLog(fmt.Sprintf("Warning: failed to query metadata for %s.%s: %v", tableName, columnName, err))
		} else if strings.ToLower(columnType) == "text" {
			return nil
		}
		alterSQL = fmt.Sprintf("ALTER TABLE %s MODIFY COLUMN %s text", tableName, columnName)
	} else {
		return nil
	}

	if alterSQL != "" {
		if err := db.Exec(alterSQL).Error; err != nil {
			return fmt.Errorf("failed to migrate %s.%s to text: %w", tableName, columnName, err)
		}
		common.SysLog(fmt.Sprintf("Successfully migrated %s.%s to text", tableName, columnName))
	}
	return nil
}

// migrateSubscriptionPlanPriceAmount migrates price_amount column from float/double to decimal(10,6)
// This is safe to run multiple times - it checks the column type first
func migrateSubscriptionPlanPriceAmount() error {
	return migrateSubscriptionPlanPriceAmountWithDB(DB)
}

func migrateSubscriptionPlanPriceAmountWithDB(db *gorm.DB) error {
	// SQLite doesn't support ALTER COLUMN, and its type affinity handles this automatically
	// Skip early to avoid GORM parsing the existing table DDL which may cause issues
	if db == nil || db.Dialector.Name() == "sqlite" {
		return nil
	}

	tableName := "subscription_plans"
	columnName := "price_amount"

	// Check if table exists first
	if !db.Migrator().HasTable(tableName) {
		return nil
	}

	// Check if column exists
	if !db.Migrator().HasColumn(&SubscriptionPlan{}, columnName) {
		return nil
	}

	var alterSQL string
	if db.Dialector.Name() == "postgres" {
		// PostgreSQL: Check if already decimal/numeric
		var dataType string
		if err := db.Raw(`SELECT data_type FROM information_schema.columns
			WHERE table_schema = current_schema() AND table_name = ? AND column_name = ?`,
			tableName, columnName).Scan(&dataType).Error; err != nil {
			common.SysLog(fmt.Sprintf("Warning: failed to query metadata for %s.%s: %v", tableName, columnName, err))
		} else if dataType == "numeric" {
			return nil // Already decimal/numeric
		}
		alterSQL = fmt.Sprintf(`ALTER TABLE %s ALTER COLUMN %s TYPE decimal(10,6) USING %s::decimal(10,6)`,
			tableName, columnName, columnName)
	} else if db.Dialector.Name() == "mysql" {
		// MySQL: Check if already decimal
		var columnType string
		if err := db.Raw(`SELECT COLUMN_TYPE FROM information_schema.columns
				WHERE table_schema = DATABASE() AND table_name = ? AND column_name = ?`,
			tableName, columnName).Scan(&columnType).Error; err != nil {
			common.SysLog(fmt.Sprintf("Warning: failed to query metadata for %s.%s: %v", tableName, columnName, err))
		} else if strings.HasPrefix(strings.ToLower(columnType), "decimal") {
			return nil // Already decimal
		}
		alterSQL = fmt.Sprintf("ALTER TABLE %s MODIFY COLUMN %s decimal(10,6) NOT NULL DEFAULT 0",
			tableName, columnName)
	} else {
		return nil
	}

	if alterSQL != "" {
		if err := db.Exec(alterSQL).Error; err != nil {
			return fmt.Errorf("failed to migrate %s.%s to decimal: %w", tableName, columnName, err)
		}
		common.SysLog(fmt.Sprintf("Successfully migrated %s.%s to decimal(10,6)", tableName, columnName))
	}
	return nil
}

func closeDB(db *gorm.DB) error {
	if db == nil {
		return nil
	}
	sqlDB, err := db.DB()
	if err != nil {
		return err
	}
	err = sqlDB.Close()
	return err
}

func CloseDB() error {
	if DB == nil && LOG_DB == nil {
		return nil
	}
	if LOG_DB != DB {
		err := closeDB(LOG_DB)
		if err != nil {
			return err
		}
	}
	return closeDB(DB)
}

// checkMySQLChineseSupport ensures the MySQL connection and current schema
// default charset/collation can store Chinese characters. It allows common
// Chinese-capable charsets (utf8mb4, utf8, gbk, big5, gb18030) and panics otherwise.
func checkMySQLChineseSupport(db *gorm.DB) error {
	// 仅检测：当前库默认字符集/排序规则 + 各表的排序规则（隐含字符集）

	// Read current schema defaults
	var schemaCharset, schemaCollation string
	err := db.Raw("SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = DATABASE()").Row().Scan(&schemaCharset, &schemaCollation)
	if err != nil {
		return fmt.Errorf("读取当前库默认字符集/排序规则失败 / Failed to read schema default charset/collation: %v", err)
	}

	toLower := func(s string) string { return strings.ToLower(s) }
	// Allowed charsets that can store Chinese text
	allowedCharsets := map[string]string{
		"utf8mb4": "utf8mb4_",
		"utf8":    "utf8_",
		"gbk":     "gbk_",
		"big5":    "big5_",
		"gb18030": "gb18030_",
	}
	isChineseCapable := func(cs, cl string) bool {
		csLower := toLower(cs)
		clLower := toLower(cl)
		if prefix, ok := allowedCharsets[csLower]; ok {
			if clLower == "" {
				return true
			}
			return strings.HasPrefix(clLower, prefix)
		}
		// 如果仅提供了排序规则，尝试按排序规则前缀判断
		for _, prefix := range allowedCharsets {
			if strings.HasPrefix(clLower, prefix) {
				return true
			}
		}
		return false
	}

	// 1) 当前库默认值必须支持中文
	if !isChineseCapable(schemaCharset, schemaCollation) {
		return fmt.Errorf("当前库默认字符集/排序规则不支持中文：schema(%s/%s)。请将库设置为 utf8mb4/utf8/gbk/big5/gb18030 / Schema default charset/collation is not Chinese-capable: schema(%s/%s). Please set to utf8mb4/utf8/gbk/big5/gb18030",
			schemaCharset, schemaCollation, schemaCharset, schemaCollation)
	}

	// 2) 所有物理表的排序规则（隐含字符集）必须支持中文
	type tableInfo struct {
		Name      string
		Collation *string
	}
	var tables []tableInfo
	if err := db.Raw("SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'").Scan(&tables).Error; err != nil {
		return fmt.Errorf("读取表排序规则失败 / Failed to read table collations: %v", err)
	}

	var badTables []string
	for _, t := range tables {
		// NULL 或空表示继承库默认设置，已在上面校验库默认，视为通过
		if t.Collation == nil || *t.Collation == "" {
			continue
		}
		cl := *t.Collation
		// 仅凭排序规则判断是否中文可用
		ok := false
		lower := strings.ToLower(cl)
		for _, prefix := range allowedCharsets {
			if strings.HasPrefix(lower, prefix) {
				ok = true
				break
			}
		}
		if !ok {
			badTables = append(badTables, fmt.Sprintf("%s(%s)", t.Name, cl))
		}
	}

	if len(badTables) > 0 {
		// 限制输出数量以避免日志过长
		maxShow := 20
		shown := badTables
		if len(shown) > maxShow {
			shown = shown[:maxShow]
		}
		return fmt.Errorf(
			"存在不支持中文的表，请修复其排序规则/字符集。示例（最多展示 %d 项）：%v / Found tables not Chinese-capable. Please fix their collation/charset. Examples (showing up to %d): %v",
			maxShow, shown, maxShow, shown,
		)
	}
	return nil
}

var (
	lastPingTime time.Time
	pingMutex    sync.Mutex
)

func PingDB() error {
	pingMutex.Lock()
	defer pingMutex.Unlock()

	if time.Since(lastPingTime) < time.Second*10 {
		return nil
	}

	sqlDB, err := DB.DB()
	if err != nil {
		log.Printf("Error getting sql.DB from GORM: %v", err)
		return err
	}

	err = sqlDB.Ping()
	if err != nil {
		log.Printf("Error pinging DB: %v", err)
		return err
	}

	lastPingTime = time.Now()
	common.SysLog("Database pinged successfully")
	return nil
}
