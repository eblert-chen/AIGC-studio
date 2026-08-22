package main

import (
	"bytes"
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/controller"
	"github.com/QuantumNous/new-api/i18n"
	"github.com/QuantumNous/new-api/logger"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/oauth"
	perfmetrics "github.com/QuantumNous/new-api/pkg/perf_metrics"
	"github.com/QuantumNous/new-api/relay"
	kitutil "github.com/QuantumNous/new-api/relaykit/relayconvert/kitutil"
	"github.com/QuantumNous/new-api/router"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/service/authz"
	_ "github.com/QuantumNous/new-api/setting/performance_setting"
	"github.com/QuantumNous/new-api/setting/ratio_setting"

	"github.com/bytedance/gopkg/util/gopool"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/joho/godotenv"

	_ "net/http/pprof"
)

//go:embed web/dist
var buildFS embed.FS

//go:embed web/dist/index.html
var indexPage []byte

var platformRelayRuntimeLifecycleLock *model.RelayLifecycleLock
var platformRelayRuntimeLifecycleFailures <-chan error
var stopPlatformRelayRuntimeLifecycleMonitor = func() {}
var platformRelayRuntimeLifecycleLost atomic.Bool
var readPlatformRelayDatabasePasswordSecretFile = common.ReadProtectedSecretFile
var verifyPlatformRelaySecretIsolationReceipt = service.VerifyPlatformRelaySecretIsolationReceipt
var verifyPlatformRelayRootSecretIsolationReceipt = service.VerifyPlatformRelayRootSecretIsolationReceipt

func main() {
	if platformRelayOfflineCommandRequested(os.Args) {
		configurePlatformRelayOfflineCommandWriters()
	}
	if handled, err := handlePlatformRelayOfflineCommand(os.Args, os.Stdout); handled {
		if err != nil {
			if platformRelayLocalDatabaseRoleRehearsalEnabled() {
				fmt.Fprintln(os.Stderr, "Relay local database-role rehearsal failed:", err)
			} else {
				fmt.Fprintln(os.Stderr, "Relay offline command failed")
			}
			os.Exit(1)
		}
		return
	}
	startTime := time.Now()
	kitutil.SetLogging(common.SysLog, func(message string) {
		logger.LogError(nil, message)
	})
	kitutil.SetSystemErrorLogging(common.SysError)

	err := InitResources()
	if err != nil {
		common.FatalLog("failed to initialize resources: " + err.Error())
		return
	}

	common.SysLog("New API " + common.Version + " started")
	if os.Getenv("GIN_MODE") != "debug" {
		gin.SetMode(gin.ReleaseMode)
	}
	if common.DebugEnabled {
		common.SysLog("running in debug mode")
	}

	kitutil.Debug.Store(common.DebugEnabled)

	lifecycleLost := false
	defer func() {
		if !lifecycleLost {
			err := model.CloseDB()
			if err != nil {
				common.SysError("failed to close database: " + err.Error())
			}
		}
		if lifecycleLost {
			closePlatformRelayRuntimeLifecycleLock()
		} else if err := releasePlatformRelayRuntimeLifecycleLock(); err != nil {
			common.SysError("failed to release Relay runtime lifecycle lock: " + err.Error())
		}
	}()
	defer stopPlatformRelayRuntimeLifecycleMonitor()

	if common.RedisEnabled {
		// for compatibility with old versions
		common.MemoryCacheEnabled = true
	}
	if common.MemoryCacheEnabled {
		common.SysLog("memory cache enabled")
		common.SysLog(fmt.Sprintf("sync frequency: %d seconds", common.SyncFrequency))

		// Add panic recovery and retry for InitChannelCache
		func() {
			defer func() {
				if r := recover(); r != nil {
					common.SysLog(fmt.Sprintf("InitChannelCache panic: %v, retrying once", r))
					// Retry once
					_, _, fixErr := model.FixAbility()
					if fixErr != nil {
						common.FatalLog(fmt.Sprintf("InitChannelCache failed: %s", fixErr.Error()))
					}
				}
			}()
			model.InitChannelCache()
		}()

		go model.SyncChannelCache(common.SyncFrequency)
	}

	// Warm pricing after channel cache initialization so Advanced Custom
	// endpoint inference can read cached route settings on first request.
	model.GetPricing()
	if err := service.ValidatePlatformArtifactCleanupMaintenanceConfiguration(); err != nil {
		common.FatalLog("invalid platform artifact cleanup maintenance configuration: " + err.Error())
		return
	}
	if service.PlatformRelayCompatEnabled() {
		if err := service.ValidatePlatformGenerationWorkerConfiguration(); err != nil {
			common.FatalLog("invalid platform generation worker configuration: " + err.Error())
			return
		}
		if err := service.SyncPlatformGenerationProviderRoutes(); err != nil {
			common.FatalLog("failed to synchronize platform generation provider routes: " + err.Error())
			return
		}
		if err := service.ValidatePlatformProviderRuntimeConfiguration(); err != nil {
			common.FatalLog("invalid platform provider runtime configuration: " + err.Error())
			return
		}
	}
	// 热更新配置
	go model.SyncOptions(common.SyncFrequency)

	// 周期性重载授权策略，保证多节点/多 master 部署下权限变更能传播到每个实例
	go authz.StartPolicySync(common.SyncFrequency)

	// 数据看板
	go model.UpdateQuotaData()

	if os.Getenv("CHANNEL_UPDATE_FREQUENCY") != "" {
		frequency, err := strconv.Atoi(os.Getenv("CHANNEL_UPDATE_FREQUENCY"))
		if err != nil {
			common.FatalLog("failed to parse CHANNEL_UPDATE_FREQUENCY: " + err.Error())
		}
		go controller.AutomaticallyUpdateChannels(frequency)
	}

	// Codex refresh rotates an upstream credential before persisting its
	// replacement. Protected deployments disable/reject it until that boundary
	// has durable unknown-outcome reconciliation.
	codexRefreshEnabled, codexRefreshErr := service.CodexCredentialAutoRefreshEnabled()
	if codexRefreshErr != nil {
		common.FatalLog("invalid Codex credential refresh configuration: " + codexRefreshErr.Error())
		return
	}
	if err := service.ValidateCodexCredentialAutoRefreshLifecycle(
		codexRefreshEnabled, model.RelayDatabaseRoleAttestationRequired(),
	); err != nil {
		common.FatalLog("unsafe Codex credential refresh lifecycle: " + err.Error())
		return
	}
	if codexRefreshEnabled {
		service.StartCodexCredentialAutoRefreshTask()
	}

	// Subscription quota reset task (daily/weekly/monthly/custom)
	service.StartSubscriptionQuotaResetTask()

	// Report this process as a system instance so the System Info page can show
	// all currently alive nodes in multi-instance deployments.
	service.StartSystemInstanceReporter()

	// Wire task polling adaptor factory (breaks service -> relay import cycle).
	// Must run before the system task runner starts: the async_task_poll handler
	// calls service.RunTaskPollingOnce, which needs this factory set.
	service.GetTaskAdaptorFunc = func(platform constant.TaskPlatform) service.TaskPollingAdaptor {
		a := relay.GetTaskAdaptor(platform)
		if a == nil {
			return nil
		}
		return a
	}

	// Register the periodic channel test, upstream model update, and async task
	// polling (Midjourney / Suno / video) jobs as scheduled system tasks
	// (DB-lease dedup across masters + run history), then start the runner that
	// schedules and executes them. Master-only execution and the UpdateTask
	// switch are enforced inside the runner and each handler's Enabled().
	controller.RegisterScheduledSystemTasks()
	service.StartSystemTaskRunner()

	if os.Getenv("BATCH_UPDATE_ENABLED") == "true" {
		common.BatchUpdateEnabled = true
		common.SysLog("batch update enabled with interval " + strconv.Itoa(common.BatchUpdateInterval) + "s")
		model.InitBatchUpdater()
	}

	if os.Getenv("ENABLE_PPROF") == "true" {
		gopool.Go(func() {
			log.Println(http.ListenAndServe("0.0.0.0:8005", nil))
		})
		go common.Monitor()
		common.SysLog("pprof enabled")
	}

	err = common.StartPyroScope()
	if err != nil {
		common.SysError(fmt.Sprintf("start pyroscope error : %v", err))
	}

	// Initialize HTTP server
	server := gin.New()
	httpHandlers := service.NewRelayHTTPHandlerTracker()
	var httpAdmissionReady atomic.Bool
	server.Use(func(c *gin.Context) {
		if !httpHandlers.Enter() {
			c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{"error": "Relay HTTP admission is draining"})
			return
		}
		defer httpHandlers.Leave()
		if !httpAdmissionReady.Load() && c.Request.URL.Path != "/health/live" {
			c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{"error": "Relay HTTP admission is starting or draining"})
			return
		}
		if model.RelayRuntimeDatabaseLifecycleFencingEnabled() &&
			!model.RelayRuntimeDatabaseLifecycleHealthy() && c.Request.URL.Path != "/health/live" {
			c.AbortWithStatusJSON(http.StatusServiceUnavailable, gin.H{"error": "Relay database lifecycle is draining"})
			return
		}
		c.Next()
	})
	if err := middleware.ConfigureTrustedProxies(server); err != nil {
		common.FatalLog("failed to configure trusted proxies: " + err.Error())
		return
	}
	server.Use(gin.CustomRecovery(func(c *gin.Context, err any) {
		common.SysLog(fmt.Sprintf("panic detected: %v", err))
		c.JSON(http.StatusInternalServerError, gin.H{
			"error": gin.H{
				"message": fmt.Sprintf("Panic detected, error: %v. Please submit a issue here: https://github.com/Calcium-Ion/new-api", err),
				"type":    "new_api_panic",
			},
		})
	}))
	// This will cause SSE not to work!!!
	//server.Use(gzip.Gzip(gzip.DefaultCompression))
	server.Use(middleware.RequestId())
	server.Use(middleware.Version())
	server.Use(middleware.I18n())
	middleware.SetUpLogger(server)
	InjectUmamiAnalytics()
	InjectGoogleAnalytics()

	// 设置路由
	router.SetRouter(server, router.WebAssets{
		BuildFS:   buildFS,
		IndexPage: indexPage,
	})
	var port = os.Getenv("PORT")
	if port == "" {
		port = strconv.Itoa(*common.Port)
	}

	srv := &http.Server{
		Addr:    ":" + port,
		Handler: server,
	}
	if model.RelayRuntimeDatabaseLifecycleFencingEnabled() && !model.RelayRuntimeDatabaseLifecycleHealthy() {
		common.FatalLog("Relay runtime lifecycle was lost before HTTP admission opened")
		return
	}

	go func() {
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			common.FatalLog("failed to start HTTP server: " + err.Error())
		}
	}()

	time.Sleep(100 * time.Millisecond)
	if model.RelayRuntimeDatabaseLifecycleFencingEnabled() && !model.RelayRuntimeDatabaseLifecycleHealthy() {
		_ = drainPlatformRelayStartupFailure(httpHandlers, srv)
		common.FatalLog("Relay runtime lifecycle was lost before generation workers started")
		return
	}
	if err := service.StartPlatformGenerationWorkers(); err != nil {
		_ = drainPlatformRelayStartupFailure(httpHandlers, srv)
		common.FatalLog("platform generation workers failed to start: " + err.Error())
		return
	}
	if model.RelayRuntimeDatabaseLifecycleFencingEnabled() && !model.RelayRuntimeDatabaseLifecycleHealthy() {
		_ = drainPlatformRelayStartupFailure(httpHandlers, srv)
		common.FatalLog("Relay runtime lifecycle was lost while generation workers started")
		return
	}
	if err := service.StartPlatformProviderRuntimeWorkers(); err != nil {
		_ = drainPlatformRelayStartupFailure(httpHandlers, srv)
		common.FatalLog("platform provider runtime workers failed to start: " + err.Error())
		return
	}
	if model.RelayRuntimeDatabaseLifecycleFencingEnabled() && !model.RelayRuntimeDatabaseLifecycleHealthy() {
		_ = drainPlatformRelayStartupFailure(httpHandlers, srv)
		common.FatalLog("Relay runtime lifecycle was lost while provider workers started")
		return
	}
	// Open non-liveness HTTP admission only after every database side-effect
	// worker is running and the lifecycle anchor is still healthy. This closes
	// the listener-startup window where an old runtime could otherwise accept a
	// request before its fencing supervision was complete.
	httpAdmissionReady.Store(true)

	common.LogStartupSuccess(startTime, port)

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(quit)
	var lifecycleLoss error
	select {
	case sig := <-quit:
		common.SysLog(fmt.Sprintf("received signal: %v, shutting down...", sig))
	case failure, open := <-platformRelayRuntimeLifecycleFailures:
		if open {
			lifecycleLoss = failure
		}
	}
	// A signal and anchor failure can become ready simultaneously. Always
	// reconcile the server-owned loss bit and the database lifecycle state after
	// select so SIGTERM can never accidentally choose the long normal-drain path
	// after lock A was already lost. A closed failure channel alone is not loss.
	if platformRelayRuntimeLifecycleLost.Load() ||
		(model.RelayRuntimeDatabaseLifecycleFencingEnabled() && !model.RelayRuntimeDatabaseLifecycleHealthy()) {
		if lifecycleLoss == nil {
			lifecycleLoss = errors.New("Relay runtime lifecycle lock was lost")
		}
	}
	httpAdmissionReady.Store(false)

	// SSE streams may run for minutes; give them time to finish before forced exit
	shutdownTimeout := time.Duration(common.GetEnvOrDefault("SHUTDOWN_TIMEOUT_SECONDS", 120)) * time.Second
	drainOutcome, drainErr := service.DrainRelayHTTPAndWorkersWithLifecycleEscalation(
		shutdownTimeout+10*time.Second,
		shutdownTimeout,
		10*time.Second,
		8*time.Second,
		lifecycleLoss,
		platformRelayRuntimeLifecycleFailures,
		func() bool {
			return platformRelayRuntimeLifecycleLost.Load() ||
				(model.RelayRuntimeDatabaseLifecycleFencingEnabled() && !model.RelayRuntimeDatabaseLifecycleHealthy())
		},
		httpHandlers,
		srv.Shutdown,
		srv.Close,
		stopPlatformRelayDatabaseWorkers,
	)
	if drainOutcome.LifecycleLoss != nil {
		lifecycleLost = true
		common.SysError("Relay runtime lifecycle lock was lost; forcing database drain")
		if drainErr != nil {
			common.SysError(fmt.Sprintf("Relay HTTP handlers or database workers required forced lifecycle-loss drain: %v", drainErr))
		}
		lossContext, cancelLoss := context.WithDeadline(context.Background(), drainOutcome.LifecycleLossDeadline)
		poolClosed := make(chan error, 1)
		go func() { poolClosed <- model.CloseDB() }()
		select {
		case closeErr := <-poolClosed:
			if closeErr != nil {
				common.SysError("failed to close Relay database after lifecycle loss: " + closeErr.Error())
			}
		case <-lossContext.Done():
			common.SysError("Relay database pool did not close before the lifecycle-loss hard deadline")
		}
		cancelLoss()
		stopPlatformRelayRuntimeLifecycleMonitor()
		closePlatformRelayRuntimeLifecycleLock()
		common.FatalLog("Relay runtime exited after lifecycle lock loss")
		return
	}
	if drainErr != nil {
		common.SysError(fmt.Sprintf("Relay HTTP handlers or database workers required forced normal shutdown: %v", drainErr))
	}
	// 内存中的看板数据保存入库，避免重启丢失未落库数据 (issue #5679)
	if common.DataExportEnabled {
		model.SaveQuotaDataCache()
	}
	common.SysLog("server exited")
}

func platformRelayOfflineCommandRequested(args []string) bool {
	if len(args) < 2 {
		return false
	}
	switch args[1] {
	case "relay-build-identity",
		"relay-validate-secret-isolation",
		"relay-validate-root-secret-isolation-v1",
		"relay-provision-root",
		"relay-provision-service-principals",
		"relay-provision-edge-login",
		"relay-provision-database-roles",
		"relay-schema-status",
		"relay-migrate",
		platformRelayPrincipalRotationIsolationCommand,
		platformRelayPrincipalRotationCommand:
		return true
	default:
		return false
	}
}

func configurePlatformRelayOfflineCommandWriters() {
	common.LogWriterMu.Lock()
	gin.DefaultWriter = os.Stderr
	gin.DefaultErrorWriter = os.Stderr
	common.LogWriterMu.Unlock()
	log.SetOutput(os.Stderr)
	model.SetGormLogWriterForOfflineCommand(os.Stderr)
}

func stopPlatformRelayDatabaseWorkers(ctx context.Context) error {
	if ctx == nil {
		return errors.New("platform Relay database worker stop context is required")
	}
	results := make(chan error, 3)
	go func() { results <- service.StopPlatformGenerationWorkers(ctx) }()
	go func() { results <- service.StopPlatformProviderRuntimeWorkers(ctx) }()
	go func() { results <- service.StopSystemTaskRunner(ctx) }()
	var combined error
	for range 3 {
		select {
		case err := <-results:
			combined = errors.Join(combined, err)
		case <-ctx.Done():
			return errors.Join(combined, ctx.Err())
		}
	}
	return combined
}

func validatePlatformRelayBatchUpdateLifecycle(protected bool) error {
	if !protected {
		return nil
	}
	if os.Getenv("BATCH_UPDATE_ENABLED") != "false" {
		return errors.New("protected Relay requires BATCH_UPDATE_ENABLED=false until the batch writer has a fenced flush lifecycle")
	}
	return nil
}

func validatePlatformRelayNativeCompatibilityLifecycle(protected bool) error {
	if !protected {
		return nil
	}
	if os.Getenv("RELAY_NATIVE_PAID_COMPAT_ENABLED") != "false" {
		return errors.New("protected Relay requires RELAY_NATIVE_PAID_COMPAT_ENABLED=false until native billing and unknown outcomes are durable")
	}
	return nil
}

const platformGenerationStartupShutdownTimeout = 5 * time.Second

func drainPlatformRelayStartupFailure(
	httpHandlers *service.RelayHTTPHandlerTracker,
	server *http.Server,
) error {
	if httpHandlers == nil || server == nil {
		return errors.New("platform Relay startup drain is invalid")
	}
	startupContext, cancelStartup := context.WithTimeout(context.Background(), platformGenerationStartupShutdownTimeout)
	defer cancelStartup()
	return service.DrainRelayHTTPAndWorkers(
		startupContext,
		3*time.Second,
		httpHandlers,
		server.Shutdown,
		server.Close,
		stopPlatformRelayDatabaseWorkers,
	)
}

func startPlatformGenerationWorkersFailClosed(
	start func() error,
	shutdownServer func(context.Context) error,
	shutdownTimeout time.Duration,
) error {
	if start == nil || shutdownServer == nil || shutdownTimeout <= 0 {
		return errors.New("platform generation worker startup lifecycle is invalid")
	}
	if err := start(); err != nil {
		shutdownContext, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
		defer cancel()
		return errors.Join(err, shutdownServer(shutdownContext))
	}
	return nil
}

func handlePlatformRelayOfflineCommand(args []string, output io.Writer) (bool, error) {
	if len(args) < 2 {
		return false, nil
	}
	switch args[1] {
	case "relay-build-identity":
		if len(args) != 2 {
			return true, errors.New("relay-build-identity does not accept arguments")
		}
		payload, err := json.Marshal(service.GetPlatformRelayCompiledBuildIdentity())
		if err != nil {
			return true, err
		}
		_, err = fmt.Fprintln(output, string(payload))
		return true, err
	case "relay-validate-secret-isolation":
		if len(args) != 2 {
			return true, errors.New("relay-validate-secret-isolation does not accept arguments")
		}
		return true, runPlatformRelaySecretIsolation(output)
	case "relay-validate-root-secret-isolation-v1":
		if len(args) != 2 {
			return true, errors.New("relay-validate-root-secret-isolation-v1 does not accept arguments")
		}
		return true, runPlatformRelayRootSecretIsolation(output)
	case "relay-provision-root":
		if len(args) != 2 {
			return true, errors.New("relay-provision-root does not accept arguments")
		}
		return true, runPlatformRelayRootProvision(output)
	case "relay-provision-service-principals":
		if len(args) != 2 {
			return true, errors.New("relay-provision-service-principals does not accept arguments")
		}
		return true, runPlatformRelayServicePrincipalProvision(output)
	case "relay-provision-edge-login":
		if len(args) != 2 {
			return true, errors.New("relay-provision-edge-login does not accept arguments")
		}
		return true, runPlatformRelayEdgeLoginProvision(output)
	case "relay-provision-database-roles":
		if len(args) != 2 {
			return true, errors.New("relay-provision-database-roles does not accept arguments")
		}
		return true, runPlatformRelayDatabaseRoleProvision(output)
	case "relay-schema-status":
		if len(args) != 2 {
			return true, errors.New("relay-schema-status does not accept arguments")
		}
		return true, runPlatformRelaySchemaCommand(output, "")
	case "relay-migrate":
		resumeAttemptID := ""
		if len(args) == 4 && args[2] == "--resume" {
			parsed, err := uuid.Parse(args[3])
			if err != nil || parsed.String() != args[3] {
				return true, errors.New("relay-migrate resume attempt id is invalid")
			}
			resumeAttemptID = args[3]
		} else if len(args) != 2 {
			return true, errors.New("relay-migrate accepts only --resume <attempt-id>")
		}
		return true, runPlatformRelayMigrationCommand(output, resumeAttemptID)
	case platformRelayPrincipalRotationIsolationCommand:
		if len(args) != 2 {
			return true, errors.New(platformRelayPrincipalRotationIsolationCommand + " does not accept arguments")
		}
		return true, runPlatformRelayPrincipalRotationSecretIsolation(output)
	case platformRelayPrincipalRotationCommand:
		if len(args) != 2 {
			return true, errors.New(platformRelayPrincipalRotationCommand + " does not accept arguments")
		}
		return true, runPlatformRelayServicePrincipalRotation(output)
	default:
		return false, nil
	}
}

type platformRelayDatabaseRoleProvisionOutput struct {
	SchemaVersion int    `json:"schema_version"`
	Kind          string `json:"kind"`
	State         string `json:"state"`
}

type platformRelaySecretIsolationOutput struct {
	SchemaVersion int    `json:"schema_version"`
	Kind          string `json:"kind"`
	State         string `json:"state"`
	Consumers     int    `json:"consumers"`
}

func runPlatformRelaySecretIsolation(output io.Writer) error {
	if err := validatePlatformRelayProtectedBootstrapEnvironment("relay-validate-secret-isolation"); err != nil {
		return err
	}
	if !strings.EqualFold(strings.TrimSpace(os.Getenv("NODE_TYPE")), "master") {
		return errors.New("relay-validate-secret-isolation requires NODE_TYPE=master")
	}
	if err := validatePlatformRelayOfflineBuildProvenance(); err != nil {
		return err
	}
	if err := service.ValidateAndCommitPlatformRelaySecretIsolation(); err != nil {
		return err
	}
	return json.NewEncoder(output).Encode(platformRelaySecretIsolationOutput{
		SchemaVersion: 1,
		Kind:          "relay_secret_isolation",
		State:         "validated",
		Consumers:     service.PlatformRelaySecretIsolationConsumerCount,
	})
}

func verifyPlatformRelaySecretIsolationForProtectedConsumer(consumer string) error {
	if platformRelayLocalDatabaseRoleRehearsalEnabled() {
		return nil
	}
	environment := os.Getenv("APP_ENV")
	if !model.RelayDatabaseRoleAttestationRequired() && environment != "staging" && environment != "production" {
		return nil
	}
	if err := verifyPlatformRelaySecretIsolationReceipt(consumer); err != nil {
		return errors.New("Relay secret isolation commitment is unavailable or invalid")
	}
	return nil
}

func runPlatformRelayDatabaseRoleProvision(output io.Writer) error {
	if err := validatePlatformRelaySchemaCommandEnvironment("relay-provision-database-roles"); err != nil {
		return err
	}
	localRehearsal := platformRelayLocalDatabaseRoleRehearsalEnabled()
	var databaseReleaseWriter *service.PlatformRelayDatabaseReleaseProofWriter
	if !localRehearsal {
		var err error
		databaseReleaseWriter, err = service.PreparePlatformRelayDatabaseReleaseProof()
		if err != nil {
			return err
		}
		defer databaseReleaseWriter.Close()
	}
	if err := verifyPlatformRelaySecretIsolationForProtectedConsumer(service.PlatformRelaySecretIsolationConsumerPre); err != nil {
		return err
	}
	for _, forbidden := range []string{
		"RELAY_MIGRATION_DATABASE_PASSWORD",
		"RELAY_RUNTIME_DATABASE_PASSWORD",
		"RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD",
		"NEW_API_RELAY_MIGRATION_DATABASE_PASSWORD",
		"NEW_API_RELAY_RUNTIME_DATABASE_PASSWORD",
		"RELAY_DOWNLOAD_EDGE_POSTGRES_PASSWORD",
	} {
		if _, present := os.LookupEnv(forbidden); present {
			return errors.New("Relay database role passwords must use password files")
		}
	}
	migrationPassword, runtimePassword, edgePassword, err := loadPlatformRelayDatabaseRoleProvisionPasswords()
	if err != nil {
		return err
	}
	defer clear(migrationPassword)
	defer clear(runtimePassword)
	defer clear(edgePassword)
	roleAdminDSN, err := model.ResolveDatabaseDSN("SQL_DSN")
	if err != nil {
		return errors.New("Relay role-admin database credential could not be inspected")
	}
	parsedRoleAdminDSN, err := url.Parse(roleAdminDSN)
	if err != nil || parsedRoleAdminDSN.User == nil {
		return errors.New("Relay role-admin database credential is invalid")
	}
	roleAdminPassword, present := parsedRoleAdminDSN.User.Password()
	if !present {
		return errors.New("Relay role-admin database password is required")
	}
	roleAdminPasswordBytes := []byte(roleAdminPassword)
	defer clear(roleAdminPasswordBytes)
	if err := model.ValidateDistinctRelayDatabasePasswords(
		roleAdminPasswordBytes, migrationPassword, runtimePassword, edgePassword,
	); err != nil {
		return errors.New("Relay database role passwords are invalid or not distinct")
	}
	common.IsMasterNode = true
	if err := model.InitDB(); err != nil {
		return errors.New("Relay role-admin database could not be opened")
	}
	defer func() { _ = model.CloseDB() }()
	if err := model.ProvisionRelayDatabaseRoles(model.DB, migrationPassword, runtimePassword, edgePassword); err != nil {
		return err
	}
	if databaseReleaseWriter != nil {
		if err := databaseReleaseWriter.Commit(model.DB); err != nil {
			return err
		}
	}
	return json.NewEncoder(output).Encode(platformRelayDatabaseRoleProvisionOutput{
		SchemaVersion: 1,
		Kind:          "relay_database_role_provision",
		State:         "provisioned",
	})
}

func loadPlatformRelayDatabaseRoleProvisionPasswords() ([]byte, []byte, []byte, error) {
	migrationPassword, err := readPlatformRelayDatabasePasswordFile("RELAY_MIGRATION_DATABASE_PASSWORD_FILE")
	if err != nil {
		return nil, nil, nil, err
	}
	runtimePassword, err := readPlatformRelayDatabasePasswordFile("RELAY_RUNTIME_DATABASE_PASSWORD_FILE")
	if err != nil {
		clear(migrationPassword)
		return nil, nil, nil, err
	}
	edgePassword, err := readPlatformRelayDatabasePasswordFile("RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE")
	if err != nil {
		clear(migrationPassword)
		clear(runtimePassword)
		return nil, nil, nil, err
	}
	return migrationPassword, runtimePassword, edgePassword, nil
}

func readPlatformRelayDatabasePasswordFile(environment string) ([]byte, error) {
	password, err := readPlatformRelayDatabasePasswordSecretFile(environment, 128)
	if err != nil {
		return nil, fmt.Errorf("%s is unavailable or invalid", environment)
	}
	if err := model.ValidateRelayDatabasePassword(password); err != nil {
		clear(password)
		return nil, fmt.Errorf("%s content is invalid", environment)
	}
	return password, nil
}

type platformRelayEdgeLoginProvisionOutput struct {
	SchemaVersion int    `json:"schema_version"`
	Kind          string `json:"kind"`
	State         string `json:"state"`
}

func runPlatformRelayEdgeLoginProvision(output io.Writer) error {
	if err := validatePlatformRelaySchemaCommandEnvironment("relay-provision-edge-login"); err != nil {
		return err
	}
	if err := verifyPlatformRelaySecretIsolationForProtectedConsumer(service.PlatformRelaySecretIsolationConsumerPost); err != nil {
		return err
	}
	common.IsMasterNode = true
	if err := model.InitDB(); err != nil {
		return errors.New("Relay download edge role-admin database could not be opened")
	}
	if !platformRelayLocalDatabaseRoleRehearsalEnabled() {
		if err := service.VerifyPlatformRelayDatabaseReleaseProof(
			model.DB,
			service.PlatformRelaySecretIsolationConsumerPost,
		); err != nil {
			_ = model.CloseDB()
			return err
		}
	}
	sqlDB, err := model.DB.DB()
	if err != nil {
		_ = model.CloseDB()
		return errors.New("Relay download edge role-admin database handle is unavailable")
	}
	databaseOpen := true
	defer func() {
		if databaseOpen {
			_ = sqlDB.Close()
		}
	}()
	lock, err := model.AcquireRelayLifecycleLock(context.Background(), model.DB)
	if err != nil {
		return err
	}
	lockOpen := true
	defer func() {
		if lockOpen {
			lock.Close()
		}
	}()
	if err := model.FinalizeRelayDownloadEdgeDatabaseRole(model.DB); err != nil {
		return err
	}
	if err := sqlDB.Close(); err != nil {
		return errors.New("Relay download edge role-admin database could not be closed cleanly")
	}
	databaseOpen = false
	if err := model.ReleaseRelayLifecycleLockBounded(lock); err != nil {
		return err
	}
	lockOpen = false
	return json.NewEncoder(output).Encode(platformRelayEdgeLoginProvisionOutput{
		SchemaVersion: 1,
		Kind:          "relay_download_edge_login_provision",
		State:         "attached",
	})
}

type platformRelaySchemaStatusOutput struct {
	SchemaVersion int                     `json:"schema_version"`
	Kind          string                  `json:"kind"`
	Status        model.RelaySchemaStatus `json:"status"`
}

var (
	platformRelayOfflineSourceRevisionPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
	platformRelayOfflineSnapshotSHA256Pattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
)

func runPlatformRelayMigrationCommand(output io.Writer, resumeAttemptID string) error {
	if err := validatePlatformRelaySchemaCommandEnvironment("relay-migrate"); err != nil {
		return err
	}
	if err := verifyPlatformRelaySecretIsolationForProtectedConsumer(service.PlatformRelaySecretIsolationConsumerMigrate); err != nil {
		return err
	}
	common.IsMasterNode = true
	if err := model.InitDB(); err != nil {
		return errors.New("Relay migration database could not be opened")
	}
	defer func() { _ = model.CloseDB() }()
	if !platformRelayLocalDatabaseRoleRehearsalEnabled() {
		if err := service.VerifyPlatformRelayDatabaseReleaseProof(
			model.DB,
			service.PlatformRelaySecretIsolationConsumerMigrate,
		); err != nil {
			return err
		}
	}
	result, err := model.RunRelaySchemaMigrations(context.Background(), resumeAttemptID)
	if encodeErr := json.NewEncoder(output).Encode(result); encodeErr != nil {
		return encodeErr
	}
	if err != nil {
		return err
	}
	return nil
}

func runPlatformRelaySchemaCommand(output io.Writer, resumeAttemptID string) error {
	if resumeAttemptID != "" {
		return errors.New("relay-schema-status does not support resume")
	}
	if err := validatePlatformRelaySchemaCommandEnvironment("relay-schema-status"); err != nil {
		return err
	}
	if err := verifyPlatformRelaySecretIsolationForProtectedConsumer(service.PlatformRelaySecretIsolationConsumerMigrate); err != nil {
		return err
	}
	common.IsMasterNode = false
	if err := model.InitDB(); err != nil {
		return errors.New("Relay schema status database could not be opened")
	}
	defer func() { _ = model.CloseDB() }()
	if !platformRelayLocalDatabaseRoleRehearsalEnabled() {
		if err := service.VerifyPlatformRelayDatabaseReleaseProof(
			model.DB,
			service.PlatformRelaySecretIsolationConsumerMigrate,
		); err != nil {
			return err
		}
	}
	if err := model.VerifyRelayMigrationDatabaseRole(model.DB); err != nil {
		return err
	}
	status, err := model.GetRelaySchemaStatus(model.DB)
	if err != nil {
		return err
	}
	return json.NewEncoder(output).Encode(platformRelaySchemaStatusOutput{
		SchemaVersion: 1,
		Kind:          "relay_schema_status",
		Status:        status,
	})
}

func validatePlatformRelaySchemaCommandEnvironment(command string) error {
	if model.RelayDatabaseRoleAttestationRequired() {
		if !platformRelayLocalDatabaseRoleRehearsalEnabled() {
			appEnvironment := os.Getenv("APP_ENV")
			deploymentEnvironment := os.Getenv("DEPLOYMENT_ENV")
			if appEnvironment != deploymentEnvironment ||
				(appEnvironment != "staging" && appEnvironment != "production") {
				return fmt.Errorf("%s requires exact attested staging or production environments", command)
			}
			if err := validatePlatformRelayProtectedTLSMode(command); err != nil {
				return err
			}
		}
	}
	logDSN, err := model.ResolveDatabaseDSN("LOG_SQL_DSN")
	if err != nil {
		return fmt.Errorf("%s log database configuration is invalid", command)
	}
	if strings.TrimSpace(logDSN) != "" {
		return fmt.Errorf("%s does not support a separate LOG_SQL_DSN", command)
	}
	if !model.RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	if err := validatePlatformRelayOfflineBuildProvenance(); err != nil {
		return err
	}
	if !strings.EqualFold(strings.TrimSpace(os.Getenv("NODE_TYPE")), "master") {
		return fmt.Errorf("%s requires NODE_TYPE=master", command)
	}
	return validatePlatformRelayPostgresDSN(command)
}

// platformRelayLocalDatabaseRoleRehearsalEnabled permits the repository's
// loopback-only development Compose profile to exercise the exact PostgreSQL
// role topology without pretending that Docker Desktop provides a managed TLS
// endpoint or production secret-isolation receipts.  The opt-in is deliberately
// unusable in staging or production; those environments continue through the
// protected TLS, release-proof, and secret-isolation gates above.
func platformRelayLocalDatabaseRoleRehearsalEnabled() bool {
	return os.Getenv("RELAY_LOCAL_DATABASE_ROLE_REHEARSAL") == "true" &&
		os.Getenv("APP_ENV") == "development" &&
		os.Getenv("DEPLOYMENT_ENV") == "development" &&
		model.RelayDatabaseRoleAttestationRequired() &&
		!model.RelayDatabaseTLSAttestationRequired()
}

func validatePlatformRelayOfflineBuildProvenance() error {
	identity := service.GetPlatformRelayCompiledBuildIdentity()
	environmentRevision := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_REVISION")))
	environmentSnapshot := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256")))
	environmentFiles, filesErr := strconv.Atoi(strings.TrimSpace(os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT")))
	if !platformRelayOfflineSourceRevisionPattern.MatchString(identity.SourceRevision) ||
		identity.SourceRevision == strings.Repeat("0", 40) ||
		!platformRelayOfflineSnapshotSHA256Pattern.MatchString(identity.SourceSnapshotSHA256) ||
		identity.SourceSnapshotSHA256 == "sha256:"+strings.Repeat("0", 64) ||
		identity.SourceSnapshotFileCount < 1 || filesErr != nil ||
		environmentRevision != identity.SourceRevision || environmentSnapshot != identity.SourceSnapshotSHA256 ||
		environmentFiles != identity.SourceSnapshotFileCount {
		return errors.New("Relay offline schema command provenance does not match the compiled image")
	}
	return nil
}

const (
	platformRelayRootUsernameEnvironment          = service.PlatformRelayRootUsernameEnvironment
	platformRelayRootPasswordFileEnvironment      = service.PlatformRelayRootPasswordFileEnvironment
	platformRelayRootPasswordFileMaxBytes         = 4096
	platformRelayServicePrincipalsFileEnvironment = "RELAY_SERVICE_PRINCIPALS_FILE"
	platformRelayServicePrincipalsFileMaxBytes    = 512 * 1024
	platformRelayAPIRuntimeSecretsFileEnvironment = "RELAY_API_RUNTIME_SECRETS_FILE"
	platformRelayAPIRuntimeSecretsFileMaxBytes    = 1024 * 1024
)

type platformRelayRootProvisionInput struct {
	Username string
	Password string
}

type platformRelayRootProvisionOutput struct {
	SchemaVersion int    `json:"schema_version"`
	Kind          string `json:"kind"`
	State         string `json:"state"`
	Username      string `json:"username"`
}

type platformRelayServicePrincipalProvisionOutput struct {
	SchemaVersion int    `json:"schema_version"`
	Kind          string `json:"kind"`
	State         string `json:"state"`
	Count         int    `json:"count"`
}

func runPlatformRelayServicePrincipalProvision(output io.Writer) error {
	if err := validatePlatformRelayProtectedBootstrapEnvironment("relay-provision-service-principals"); err != nil {
		return err
	}
	for _, forbidden := range []string{
		"SQL_DSN",
		"LOG_SQL_DSN",
		"LOG_SQL_DSN_FILE",
		"RELAY_COMPAT_CLIENT_CREDENTIALS_JSON",
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		"RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON",
		"RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN",
		"RELAY_COMPAT_INTERNAL_BASE_URL",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY",
		"REDIS_CONN_STRING",
		"SESSION_SECRET",
		"CRYPTO_SECRET",
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON",
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE",
		"RELAY_ARTIFACT_SIGNING_SECRET",
		"HUAWEI_OBS_ACCESS_KEY_ID",
		"HUAWEI_OBS_SECRET_ACCESS_KEY",
		"HUAWEI_OBS_SECURITY_TOKEN",
		"RELAY_PROVIDER_ALERT_SIGNING_SECRET",
		"RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN",
		"RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET",
		"RELAY_TELEMETRY_SIGNING_SECRET",
		"RELAY_PROVISION_ROOT_PASSWORD_FILE",
		"RELAY_MIGRATION_DATABASE_PASSWORD_FILE",
		"RELAY_RUNTIME_DATABASE_PASSWORD_FILE",
		"RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE",
		"RELAY_API_RUNTIME_SECRETS_FILE",
		"RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE",
		"RELAY_DOWNLOAD_EDGE_REGISTRATION_TOKEN",
		"RELAY_DOWNLOAD_EDGE_REGISTRATION_SIGNING_SECRET",
		"RELAY_DOWNLOAD_EDGE_TICKET_TOKEN_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_SOURCE_ENCRYPTION_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_PLATFORM_INTERNAL_TOKEN",
		"RELAY_DOWNLOAD_EDGE_COMPLETION_SIGNING_SECRET",
		"RELAY_DOWNLOAD_EDGE_PROOF_PRIVATE_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_PROOF_READ_TOKEN",
	} {
		if _, present := os.LookupEnv(forbidden); present {
			return errors.New("Relay service principal provisioner received an unrelated secret source")
		}
	}
	if err := verifyPlatformRelaySecretIsolationForProtectedConsumer(service.PlatformRelaySecretIsolationConsumerPrincipal); err != nil {
		return err
	}
	raw, err := readPlatformRelayOwnerOnlyReadOnlySecretFile(
		platformRelayServicePrincipalsFileEnvironment,
		platformRelayServicePrincipalsFileMaxBytes,
	)
	if err != nil {
		return err
	}
	defer clear(raw)
	inputs, err := service.ParsePlatformRelayServicePrincipalsFile(raw)
	if err != nil {
		return err
	}
	if err := validatePlatformRelaySchemaCommandEnvironment("relay-provision-service-principals"); err != nil {
		return err
	}
	common.IsMasterNode = true
	if err := model.InitDB(); err != nil {
		return errors.New("Relay service principal database could not be opened")
	}
	sqlDB, err := model.DB.DB()
	if err != nil {
		return errors.New("Relay service principal database handle is unavailable")
	}
	databaseOpen := true
	defer func() {
		if databaseOpen {
			_ = sqlDB.Close()
		}
	}()
	if common.MainDatabaseType() != common.DatabaseTypePostgreSQL {
		return errors.New("Relay service principal provisioning requires PostgreSQL")
	}
	lifecycleLock, err := model.AcquireRelayLifecycleLock(context.Background(), model.DB)
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
		return errors.New("Relay service principal provisioning requires the current Relay schema")
	}
	if err := model.VerifyRelayRuntimeDatabaseRole(model.DB); err != nil {
		return err
	}
	if err := service.VerifyPlatformRelayDatabaseReleaseProof(
		model.DB,
		service.PlatformRelaySecretIsolationConsumerPrincipal,
	); err != nil {
		return err
	}
	result, err := service.ProvisionProtectedPlatformRelayServicePrincipals(inputs)
	if err != nil {
		return err
	}
	if err := model.ReleaseRelayLifecycleLockBounded(lifecycleLock); err != nil {
		return err
	}
	lockOpen = false
	if err := sqlDB.Close(); err != nil {
		return errors.New("Relay service principal database could not be closed cleanly")
	}
	databaseOpen = false
	state := "unchanged"
	if result.Created {
		state = "created"
	}
	return json.NewEncoder(output).Encode(platformRelayServicePrincipalProvisionOutput{
		SchemaVersion: 1,
		Kind:          "relay_service_principal_provision",
		State:         state,
		Count:         result.Count,
	})
}

func runPlatformRelayRootProvision(output io.Writer) error {
	if err := validatePlatformRelayRootProvisionExecutionEnvironment(); err != nil {
		return err
	}
	isolated, err := verifyPlatformRelayRootSecretIsolationReceipt()
	if err != nil {
		return errors.New("Relay root isolation commitment is unavailable or invalid")
	}
	input := platformRelayRootProvisionInput{Username: isolated.Username, Password: isolated.Password}
	if err := validatePlatformRelayOfflineBuildProvenance(); err != nil {
		return err
	}
	if err := validatePlatformRelayRootProvisionDatabase(); err != nil {
		return err
	}
	common.IsMasterNode = true
	if err := model.InitDB(); err != nil {
		return errors.New("production root database could not be opened")
	}
	sqlDB, err := model.DB.DB()
	if err != nil {
		return errors.New("production root database handle is unavailable")
	}
	databaseOpen := true
	defer func() {
		if databaseOpen {
			_ = sqlDB.Close()
		}
	}()
	if common.MainDatabaseType() != common.DatabaseTypePostgreSQL {
		return errors.New("production root provisioning requires PostgreSQL")
	}
	lifecycleLock, err := model.AcquireRelayLifecycleLock(context.Background(), model.DB)
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
		return errors.New("production root provisioning requires the current Relay schema")
	}
	if err := model.VerifyRelayRuntimeDatabaseRole(model.DB); err != nil {
		return err
	}
	if err := service.VerifyPlatformRelayRootDatabaseReleaseProof(model.DB); err != nil {
		return err
	}
	result, provisionErr := model.ProvisionProductionRoot(input.Username, input.Password, common.Version)
	if provisionErr != nil {
		return provisionErr
	}
	if err := model.ReleaseRelayLifecycleLockBounded(lifecycleLock); err != nil {
		return err
	}
	lockOpen = false
	if err := sqlDB.Close(); err != nil {
		return errors.New("production root database could not be closed cleanly")
	}
	databaseOpen = false
	state := "unchanged"
	if result.RootCreated {
		state = "created"
	}
	return json.NewEncoder(output).Encode(platformRelayRootProvisionOutput{
		SchemaVersion: 1,
		Kind:          "relay_root_provision",
		State:         state,
		Username:      result.Username,
	})
}

func loadPlatformRelayRootProvisionInput() (platformRelayRootProvisionInput, error) {
	var input platformRelayRootProvisionInput
	if err := validatePlatformRelayProtectedBootstrapEnvironment("relay-provision-root"); err != nil {
		return input, err
	}
	if !strings.EqualFold(strings.TrimSpace(os.Getenv("NODE_TYPE")), "master") {
		return input, errors.New("relay-provision-root requires NODE_TYPE=master")
	}
	for _, forbidden := range []string{"RELAY_PROVISION_ROOT_PASSWORD", "NEW_API_RELAY_ROOT_PASSWORD"} {
		if _, present := os.LookupEnv(forbidden); present {
			return input, errors.New("production root password environment variables are forbidden; use the password file")
		}
	}
	input.Username = os.Getenv(platformRelayRootUsernameEnvironment)
	passwordBytes, err := readPlatformRelayOwnerOnlyReadOnlySecretFile(
		platformRelayRootPasswordFileEnvironment,
		platformRelayRootPasswordFileMaxBytes,
	)
	if err != nil {
		return input, err
	}
	defer clear(passwordBytes)
	input.Password = string(passwordBytes)
	if err := model.ValidateProductionRootCredentials(input.Username, input.Password); err != nil {
		return platformRelayRootProvisionInput{}, err
	}
	return input, nil
}

func validatePlatformRelayProtectedBootstrapEnvironment(command string) error {
	appEnvironment := os.Getenv("APP_ENV")
	deploymentEnvironment := os.Getenv("DEPLOYMENT_ENV")
	if appEnvironment != deploymentEnvironment ||
		(appEnvironment != "staging" && appEnvironment != "production") ||
		!model.RelayDatabaseRoleAttestationRequired() {
		return fmt.Errorf("%s requires an attested staging or production environment", command)
	}
	return validatePlatformRelayProtectedTLSMode(command)
}

func validatePlatformRelayProtectedTLSMode(command string) error {
	value, present := os.LookupEnv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED")
	if !present || value != "true" || !model.RelayDatabaseTLSAttestationRequired() {
		return fmt.Errorf("%s requires exact protected PostgreSQL TLS attestation", command)
	}
	if common.ProtectedRawSecretEnvironmentPresent(os.Environ()) {
		return fmt.Errorf("%s received a forbidden raw secret environment variable", command)
	}
	return model.ValidateRelayProtectedPostgresClientEnvironment()
}

func readPlatformRelayOwnerOnlyReadOnlySecretFile(environment string, maximumBytes int64) ([]byte, error) {
	return common.ReadProtectedSecretFile(environment, maximumBytes)
}

func validatePlatformRelayProtectedRuntimeEnvironment() (bool, error) {
	appEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	deploymentEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("DEPLOYMENT_ENV")))
	compatEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_COMPAT_ENVIRONMENT")))
	outerEnvironment := strings.ToLower(strings.TrimSpace(os.Getenv("ENVIRONMENT")))
	roleAttestationRaw, roleAttestationPresent := os.LookupEnv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED")
	roleAttestation, roleAttestationErr := strconv.ParseBool(strings.TrimSpace(roleAttestationRaw))
	if roleAttestationPresent && strings.TrimSpace(roleAttestationRaw) != "" && roleAttestationErr != nil {
		return true, errors.New("protected Relay runtime environment is inconsistent")
	}
	roleProtected := appEnvironment == "production" ||
		(roleAttestationPresent && strings.TrimSpace(roleAttestationRaw) != "" &&
			roleAttestationErr == nil && roleAttestation)
	protectedRequested := roleProtected || appEnvironment == "staging" || deploymentEnvironment == "staging" ||
		deploymentEnvironment == "production" || compatEnvironment == "staging" || compatEnvironment == "production" ||
		outerEnvironment == "staging" || outerEnvironment == "production"
	if !protectedRequested {
		return false, nil
	}
	if !roleProtected || appEnvironment != deploymentEnvironment || appEnvironment != compatEnvironment ||
		(appEnvironment != "staging" && appEnvironment != "production") ||
		(outerEnvironment != "" && outerEnvironment != appEnvironment) {
		return true, errors.New("protected Relay runtime environment is inconsistent")
	}
	// RELAY_COMPAT_* are retained as deployment-compatible variable names, but
	// in a protected release they select the only admitted Platform generation
	// data plane: this process' fenced native new-api implementation.  Reject a
	// missing, disabled, or non-canonical value before reading any mounted secret
	// so a configuration drift cannot start an ordinary upstream new-api server
	// beside the production Platform Relay contract.
	if raw, present := os.LookupEnv("RELAY_COMPAT_ENABLED"); !present || raw != "true" {
		return true, errors.New("protected Relay runtime requires RELAY_COMPAT_ENABLED=true")
	}
	if raw, present := os.LookupEnv("RELAY_COMPAT_WORKER_ENABLED"); !present || raw != "true" {
		return true, errors.New("protected Relay runtime requires RELAY_COMPAT_WORKER_ENABLED=true")
	}
	if err := validatePlatformRelayProtectedTLSMode("protected Relay runtime"); err != nil {
		return true, err
	}
	return true, nil
}

func installPlatformRelayProtectedRuntimeSecrets() (bool, error) {
	protected, err := validatePlatformRelayProtectedRuntimeEnvironment()
	if err != nil || !protected {
		return protected, err
	}
	if !strings.EqualFold(strings.TrimSpace(os.Getenv("DEBUG")), "false") ||
		!strings.EqualFold(strings.TrimSpace(os.Getenv("DIFY_DEBUG")), "false") ||
		strings.ToLower(strings.TrimSpace(os.Getenv("GIN_MODE"))) != "release" {
		return true, errors.New("protected Relay runtime requires non-debug logging")
	}
	for _, forbidden := range []string{
		"SQL_DSN",
		"LOG_SQL_DSN",
		"LOG_SQL_DSN_FILE",
		"REDIS_CONN_STRING",
		"SESSION_SECRET",
		"CRYPTO_SECRET",
		"RELAY_COMPAT_CLIENT_CREDENTIALS_JSON",
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		"RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON",
		"RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN",
		"RELAY_COMPAT_INTERNAL_BASE_URL",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY",
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON",
		"RELAY_ARTIFACT_SIGNING_SECRET",
		"HUAWEI_OBS_ACCESS_KEY_ID",
		"HUAWEI_OBS_SECRET_ACCESS_KEY",
		"HUAWEI_OBS_SECURITY_TOKEN",
		"RELAY_PROVIDER_ALERT_SIGNING_SECRET",
		"RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN",
		"RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET",
		"RELAY_TELEMETRY_SIGNING_SECRET",
		"RELAY_PROVISION_ROOT_PASSWORD_FILE",
		"RELAY_MIGRATION_DATABASE_PASSWORD_FILE",
		"RELAY_RUNTIME_DATABASE_PASSWORD_FILE",
		"RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE",
		"RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE",
		"RELAY_DOWNLOAD_EDGE_REGISTRATION_TOKEN",
		"RELAY_DOWNLOAD_EDGE_REGISTRATION_SIGNING_SECRET",
		"RELAY_DOWNLOAD_EDGE_TICKET_TOKEN_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_SOURCE_ENCRYPTION_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_PLATFORM_INTERNAL_TOKEN",
		"RELAY_DOWNLOAD_EDGE_COMPLETION_SIGNING_SECRET",
		"RELAY_DOWNLOAD_EDGE_PROOF_PRIVATE_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_PROOF_READ_TOKEN",
	} {
		if _, present := os.LookupEnv(forbidden); present {
			return true, errors.New("protected Relay runtime received a legacy raw secret environment variable")
		}
	}
	if err := verifyPlatformRelaySecretIsolationReceipt(service.PlatformRelaySecretIsolationConsumerAPI); err != nil {
		return true, errors.New("Relay secret isolation commitment is unavailable or invalid")
	}
	principalRaw, err := readPlatformRelayOwnerOnlyReadOnlySecretFile(
		platformRelayServicePrincipalsFileEnvironment,
		platformRelayServicePrincipalsFileMaxBytes,
	)
	if err != nil {
		return true, err
	}
	defer clear(principalRaw)
	principals, err := service.ParsePlatformRelayServicePrincipalsFile(principalRaw)
	if err != nil {
		return true, err
	}
	runtimeRaw, err := readPlatformRelayOwnerOnlyReadOnlySecretFile(
		platformRelayAPIRuntimeSecretsFileEnvironment,
		platformRelayAPIRuntimeSecretsFileMaxBytes,
	)
	if err != nil {
		return true, err
	}
	defer clear(runtimeRaw)
	document, err := service.ParsePlatformRelayAPIRuntimeSecretsFile(runtimeRaw)
	if err != nil {
		return true, err
	}
	redisTLSCARaw, err := readPlatformRelayOwnerOnlyReadOnlySecretFile(
		service.PlatformRelayRedisTLSCAFileEnvironment,
		common.ProtectedRelayRedisTLSCAMaximumBytes,
	)
	if err != nil {
		return true, err
	}
	defer clear(redisTLSCARaw)
	redisTLSCA, err := common.ParseProtectedRelayRedisTLSCA(redisTLSCARaw)
	if err != nil {
		return true, err
	}
	if err := service.InstallPlatformRelayAPIRuntimeSecrets(principals, document, redisTLSCA); err != nil {
		return true, err
	}
	return true, nil
}

func validatePlatformRelayRootProvisionDatabase() error {
	return validatePlatformRelayPostgresDSN("relay-provision-root")
}

func validatePlatformRelayPostgresDSN(command string) error {
	rawDSN, resolveErr := model.ResolveDatabaseDSN("SQL_DSN")
	if resolveErr != nil {
		return fmt.Errorf("%s requires a valid PostgreSQL SQL_DSN source", command)
	}
	if rawDSN == "" || strings.TrimSpace(rawDSN) != rawDSN || strings.ContainsAny(rawDSN, "\x00\r\n") {
		return fmt.Errorf("%s requires a normalized PostgreSQL SQL_DSN", command)
	}
	parsed, err := url.Parse(rawDSN)
	if err != nil || (parsed.Scheme != "postgres" && parsed.Scheme != "postgresql") ||
		parsed.Host == "" || parsed.User == nil || parsed.User.Username() == "" ||
		parsed.Path == "" || parsed.Path == "/" || parsed.Fragment != "" {
		return fmt.Errorf("%s requires a PostgreSQL SQL_DSN", command)
	}
	if err := model.ValidateRelayPostgresTransportDSN(rawDSN); err != nil {
		return fmt.Errorf("%s requires an attested PostgreSQL TLS DSN", command)
	}
	if err := model.ValidateRelayPostgresSearchPathDSN(rawDSN); err != nil {
		return fmt.Errorf("%s requires an attested PostgreSQL search path", command)
	}
	return nil
}

func InjectUmamiAnalytics() {
	analyticsInjectBuilder := &strings.Builder{}
	if os.Getenv("UMAMI_WEBSITE_ID") != "" {
		umamiSiteID := os.Getenv("UMAMI_WEBSITE_ID")
		umamiScriptURL := os.Getenv("UMAMI_SCRIPT_URL")
		if umamiScriptURL == "" {
			umamiScriptURL = "https://analytics.umami.is/script.js"
		}
		analyticsInjectBuilder.WriteString("<script defer src=\"")
		analyticsInjectBuilder.WriteString(umamiScriptURL)
		analyticsInjectBuilder.WriteString("\" data-website-id=\"")
		analyticsInjectBuilder.WriteString(umamiSiteID)
		analyticsInjectBuilder.WriteString("\"></script>")
	}
	analyticsInjectBuilder.WriteString("<!--Umami QuantumNous-->\n")
	analyticsInject := []byte(analyticsInjectBuilder.String())
	placeholder := []byte("<!--umami-->\n")
	indexPage = bytes.ReplaceAll(indexPage, placeholder, analyticsInject)
}

func InjectGoogleAnalytics() {
	analyticsInjectBuilder := &strings.Builder{}
	if os.Getenv("GOOGLE_ANALYTICS_ID") != "" {
		gaID := os.Getenv("GOOGLE_ANALYTICS_ID")
		// Google Analytics 4 (gtag.js)
		analyticsInjectBuilder.WriteString("<script async src=\"https://www.googletagmanager.com/gtag/js?id=")
		analyticsInjectBuilder.WriteString(gaID)
		analyticsInjectBuilder.WriteString("\"></script>")
		analyticsInjectBuilder.WriteString("<script>")
		analyticsInjectBuilder.WriteString("window.dataLayer = window.dataLayer || [];")
		analyticsInjectBuilder.WriteString("function gtag(){dataLayer.push(arguments);}")
		analyticsInjectBuilder.WriteString("gtag('js', new Date());")
		analyticsInjectBuilder.WriteString("gtag('config', '")
		analyticsInjectBuilder.WriteString(gaID)
		analyticsInjectBuilder.WriteString("');")
		analyticsInjectBuilder.WriteString("</script>")
	}
	analyticsInjectBuilder.WriteString("<!--Google Analytics QuantumNous-->\n")
	analyticsInject := []byte(analyticsInjectBuilder.String())
	placeholder := []byte("<!--Google Analytics-->\n")
	indexPage = bytes.ReplaceAll(indexPage, placeholder, analyticsInject)
}

func InitResources() (err error) {
	platformRelayRuntimeLifecycleLost.Store(false)
	protectedRuntime, err := installPlatformRelayProtectedRuntimeSecrets()
	if err != nil {
		return err
	}
	// Protected runtimes never parse a working-directory .env file. The typed,
	// owner-only bind mounts above are the only accepted secret source and are
	// installed before logging, database, Redis, or outbound HTTP initialization.
	if !protectedRuntime {
		err = godotenv.Load(".env")
		if err != nil {
			if common.DebugEnabled {
				common.SysLog("No .env file found, using default environment variables. If needed, please create a .env file and set the relevant variables.")
			}
		}
	}

	// 加载环境变量
	common.InitEnv()
	model.EnableRelayRuntimeDatabaseLifecycleFencing()

	logger.SetupLogger()

	// Initialize model settings
	ratio_setting.InitRatioSettings()

	if err := service.InitHttpClient(); err != nil {
		return err
	}

	service.InitTokenEncoders()

	// Initialize SQL Database
	err = model.InitDB()
	if err != nil {
		common.FatalLog("failed to initialize database: " + err.Error())
		return err
	}
	if model.RelayDatabaseRoleAttestationRequired() && strings.TrimSpace(os.Getenv("LOG_SQL_DSN")) != "" {
		return errors.New("production Relay runtime does not support a separate LOG_SQL_DSN")
	}
	runtimeLifecycleLock, err := model.AcquireRelayRuntimeLifecycleLock(context.Background(), model.DB)
	if err != nil {
		return err
	}
	lockTransferred := false
	stopLifecycleMonitor := func() {}
	defer func() {
		if !lockTransferred {
			stopLifecycleMonitor()
			_ = model.CloseDB()
			runtimeLifecycleLock.Close()
		}
	}()
	var lifecycleFailures <-chan error
	if model.RelayLifecycleLockRequiresMonitoring(runtimeLifecycleLock) {
		rawFailures, stopMonitor, monitorErr := model.MonitorRelayLifecycleLock(
			context.Background(), runtimeLifecycleLock, time.Second,
		)
		if monitorErr != nil {
			return monitorErr
		}
		stopLifecycleMonitor = stopMonitor
		forwardedFailures := make(chan error, 1)
		lifecycleFailures = forwardedFailures
		go func() {
			failure, open := <-rawFailures
			if !open {
				return
			}
			platformRelayRuntimeLifecycleLost.Store(true)
			// This callback is active from the first synchronous anchor proof,
			// including the remainder of InitResources. It closes admission and
			// wakes main without waiting for its later signal select. The main loss
			// path cancels and joins durable workers before closing the pool; closing
			// here would race their fenced finalization writes.
			service.BeginPlatformGenerationWorkerDrain()
			forwardedFailures <- failure
		}()
	}
	if model.RelayDatabaseRoleAttestationRequired() {
		if _, err = model.RequireRelaySchemaCurrent(model.DB); err != nil {
			return err
		}
	} else if _, err = model.RequireRelaySchemaCompatible(model.DB); err != nil {
		return err
	}
	if err = model.VerifyRelayRuntimeDatabaseRole(model.DB); err != nil {
		return err
	}
	protected := model.RelayDatabaseRoleAttestationRequired()
	if protected {
		if err = service.VerifyPlatformRelayDatabaseReleaseProof(
			model.DB,
			service.PlatformRelaySecretIsolationConsumerAPI,
		); err != nil {
			return err
		}
	}
	if err = model.CheckSetup(); err != nil {
		return fmt.Errorf("invalid protected setup state: %w", err)
	}
	// Protected lifecycle and billing proofs run after schema/role/setup
	// attestation but before authz seeding, option/cache initialization, Redis,
	// OAuth loading, monitors, cleanup workers, or any HTTP listener. A drifted
	// service principal therefore exits through the ordinary InitResources error
	// path, whose deferred cleanup closes the pool and releases lifecycle locks.
	if protected {
		// The provider KEK is part of the protected runtime's immutable secret
		// boundary even when new admission is temporarily disabled. Validate the
		// owner-only read-only file and its A+B secret isolation before any
		// background task, listener, or provider code can observe the process.
		if err = model.ValidateProviderCredentialVaultRuntime(true); err != nil {
			return fmt.Errorf("unsafe protected provider credential vault: %w", err)
		}
	}
	if err = validatePlatformRelayBatchUpdateLifecycle(protected); err != nil {
		return err
	}
	if err = validatePlatformRelayNativeCompatibilityLifecycle(protected); err != nil {
		return err
	}
	if err = service.ValidateProtectedPlatformNativeBillingState(); err != nil {
		return fmt.Errorf("unsafe protected native task billing lifecycle: %w", err)
	}
	if err = service.ValidateProtectedPlatformRelayServicePrincipals(); err != nil {
		return fmt.Errorf("unsafe protected Platform Relay service principal: %w", err)
	}
	if err = authz.Init(model.DB); err != nil {
		return fmt.Errorf("initialize authorization: %w", err)
	}

	// Initialize options only after the independent migration job has made the
	// schema compatible. Long-lived runtime startup performs no schema/data
	// migration.
	model.InitOptionMap()

	// 清理旧的磁盘缓存文件
	common.CleanupOldCacheFiles()

	// Initialize SQL Database
	err = model.InitLogDB()
	if err != nil {
		return err
	}

	// Initialize Redis
	err = common.InitRedisClient()
	if err != nil {
		return err
	}

	perfmetrics.Init()

	// 启动系统监控
	common.StartSystemMonitor()

	// Initialize i18n
	err = i18n.Init()
	if err != nil {
		common.SysError("failed to initialize i18n: " + err.Error())
		// Don't return error, i18n is not critical
	} else {
		common.SysLog("i18n initialized with languages: " + strings.Join(i18n.SupportedLanguages(), ", "))
	}
	// Register user language loader for lazy loading
	i18n.SetUserLangLoader(model.GetUserLanguage)

	// Load custom OAuth providers from database
	err = oauth.LoadCustomProviders()
	if err != nil {
		common.SysError("failed to load custom OAuth providers: " + err.Error())
		// Don't return error, custom OAuth is not critical
	}

	service.StartAuthArtifactCleanup()
	if lifecycleFailures != nil {
		select {
		case failure := <-lifecycleFailures:
			return failure
		default:
		}
	}
	platformRelayRuntimeLifecycleLock = runtimeLifecycleLock
	platformRelayRuntimeLifecycleFailures = lifecycleFailures
	stopPlatformRelayRuntimeLifecycleMonitor = stopLifecycleMonitor
	lockTransferred = true

	return nil
}

func releasePlatformRelayRuntimeLifecycleLock() error {
	if platformRelayRuntimeLifecycleLock == nil {
		return nil
	}
	lock := platformRelayRuntimeLifecycleLock
	platformRelayRuntimeLifecycleLock = nil
	return model.ReleaseRelayLifecycleLockBounded(lock)
}

func closePlatformRelayRuntimeLifecycleLock() {
	if platformRelayRuntimeLifecycleLock == nil {
		return
	}
	lock := platformRelayRuntimeLifecycleLock
	platformRelayRuntimeLifecycleLock = nil
	lock.Close()
}
