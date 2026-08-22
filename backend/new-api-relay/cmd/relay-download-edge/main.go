package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
)

var resolveDownloadEdgeDatabaseDSN = model.ResolveDatabaseDSN

func main() {
	if err := run(); err != nil {
		log.Printf("relay download edge stopped: %v", err)
		os.Exit(1)
	}
}

func run() error {
	config, err := service.PlatformDownloadEdgeConfigFromEnv()
	if err != nil {
		return err
	}
	protected := config.ProtectedSecurityRequired()
	if err := configureDownloadEdgeDatabase(protected); err != nil {
		return err
	}
	model.EnableRelayRuntimeDatabaseLifecycleFencing()
	if err := model.InitDB(); err != nil {
		return fmt.Errorf("initialize download edge database: %w", err)
	}
	runtimeLifecycleLock, err := model.AcquireRelayRuntimeLifecycleLock(context.Background(), model.DB)
	if err != nil {
		_ = model.CloseDB()
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	var lifecycleLost atomic.Bool
	defer func() {
		if lifecycleLost.Load() {
			runtimeLifecycleLock.Close()
			return
		}
		_ = model.CloseDB()
		_ = model.ReleaseRelayLifecycleLockBounded(runtimeLifecycleLock)
	}()
	var lifecycleFailures <-chan error
	stopLifecycleMonitor := func() {}
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
			lifecycleLost.Store(true)
			forwardedFailures <- failure
			stop()
		}()
	}
	defer stopLifecycleMonitor()
	var schemaStatus model.RelaySchemaStatus
	if protected {
		schemaStatus, err = model.RequireRelaySchemaCurrent(model.DB)
	} else {
		schemaStatus, err = model.RequireRelaySchemaCompatible(model.DB)
	}
	if err != nil {
		return err
	}
	if protected {
		if err := model.VerifyRelayDownloadEdgeDatabaseRole(model.DB, schemaStatus.CurrentVersion); err != nil {
			return err
		}
		if err := service.VerifyPlatformRelayDatabaseReleaseProof(
			model.DB,
			service.PlatformRelaySecretIsolationConsumerEdge,
		); err != nil {
			return err
		}
	}
	gateway, err := service.NewPlatformDownloadEdgeGateway(config)
	if err != nil {
		return err
	}

	gatewayHandler := gateway.Handler()
	httpHandlers := service.NewRelayHTTPHandlerTracker()
	server := &http.Server{
		Addr: config.ListenAddress,
		Handler: http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
			if !httpHandlers.Enter() {
				http.Error(writer, "Relay HTTP admission is draining", http.StatusServiceUnavailable)
				return
			}
			defer httpHandlers.Leave()
			if model.RelayRuntimeDatabaseLifecycleFencingEnabled() && !model.RelayRuntimeDatabaseLifecycleHealthy() && request.URL.Path != "/health/live" {
				http.Error(writer, "Relay database lifecycle is draining", http.StatusServiceUnavailable)
				return
			}
			gatewayHandler.ServeHTTP(writer, request)
		}),
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      config.TransferTimeout + 5*time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    32 * 1024,
	}
	workerDone := make(chan error, 1)
	go func() {
		err := gateway.RunDeliveryWorker(ctx)
		workerDone <- err
	}()
	serverDone := make(chan error, 1)
	go func() {
		log.Printf("relay download edge listening on %s", config.ListenAddress)
		serverDone <- server.ListenAndServe()
	}()

	var runtimeErr error
	var lifecycleLoss error
	workerFinished := false
	select {
	case <-ctx.Done():
	case lifecycleLoss = <-lifecycleFailures:
		runtimeErr = errors.Join(runtimeErr, lifecycleLoss)
	case err := <-workerDone:
		workerFinished = true
		if err != nil && !errors.Is(err, context.Canceled) {
			runtimeErr = fmt.Errorf("download completion worker: %w", err)
		}
	case err := <-serverDone:
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			runtimeErr = fmt.Errorf("download edge HTTP server: %w", err)
		}
	}
	if lifecycleLost.Load() && lifecycleLoss == nil {
		lifecycleLoss = errors.New("Relay download edge lifecycle lock was lost")
		runtimeErr = errors.Join(runtimeErr, lifecycleLoss)
	}
	// Close global HTTP admission before cancelling the delivery worker on both
	// normal SIGTERM and lifecycle loss. No new transfer may begin while the
	// worker or an already accepted handler is draining.
	httpHandlers.BeginDrain()
	stop()
	drainOutcome, drainErr := service.DrainRelayHTTPAndWorkersWithLifecycleEscalation(
		20*time.Second,
		15*time.Second,
		10*time.Second,
		8*time.Second,
		lifecycleLoss,
		lifecycleFailures,
		func() bool {
			return lifecycleLost.Load() ||
				(model.RelayRuntimeDatabaseLifecycleFencingEnabled() && !model.RelayRuntimeDatabaseLifecycleHealthy())
		},
		httpHandlers,
		server.Shutdown,
		server.Close,
		func(ctx context.Context) error {
			if workerFinished {
				return nil
			}
			return waitForDownloadEdgeWorker(ctx, workerDone)
		},
	)
	if drainOutcome.LifecycleLoss != nil {
		lifecycleLost.Store(true)
		runtimeErr = errors.Join(runtimeErr, drainOutcome.LifecycleLoss)
		if drainErr != nil && !errors.Is(drainErr, context.Canceled) {
			runtimeErr = errors.Join(runtimeErr, drainErr)
		}
		lossContext, cancelLoss := context.WithDeadline(context.Background(), drainOutcome.LifecycleLossDeadline)
		poolClosed := make(chan error, 1)
		go func() { poolClosed <- model.CloseDB() }()
		select {
		case closeErr := <-poolClosed:
			if closeErr != nil {
				runtimeErr = errors.Join(runtimeErr, closeErr)
			}
		case <-lossContext.Done():
			runtimeErr = errors.Join(runtimeErr, errors.New("download edge database pool did not close before the lifecycle-loss deadline"))
		}
		cancelLoss()
		return runtimeErr
	}
	return errors.Join(runtimeErr, drainErr)
}

func waitForDownloadEdgeWorker(ctx context.Context, done <-chan error) error {
	if ctx == nil || done == nil {
		return errors.New("download edge worker drain is invalid")
	}
	select {
	case err := <-done:
		return err
	case <-ctx.Done():
		return errors.Join(errors.New("download edge worker did not stop before the drain deadline"), ctx.Err())
	}
}

func configureDownloadEdgeDatabase(production bool) error {
	protectedDeployment := production || model.RelayDatabaseRoleAttestationRequired()
	if protectedDeployment {
		if _, present := os.LookupEnv("RELAY_DOWNLOAD_EDGE_SQL_DSN"); present {
			return fmt.Errorf("protected download edge forbids raw RELAY_DOWNLOAD_EDGE_SQL_DSN")
		}
		if os.Getenv("RELAY_DOWNLOAD_EDGE_SQL_DSN_FILE") == "" {
			return fmt.Errorf("protected download edge requires RELAY_DOWNLOAD_EDGE_SQL_DSN_FILE")
		}
		if _, present := os.LookupEnv("SQL_DSN"); present {
			return fmt.Errorf("protected download edge must not inherit SQL_DSN")
		}
		if _, present := os.LookupEnv("SQL_DSN_FILE"); present {
			return fmt.Errorf("protected download edge must not inherit SQL_DSN_FILE")
		}
		for _, required := range []string{
			"RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED",
			"RELAY_DATABASE_TLS_ATTESTATION_REQUIRED",
			"RELAY_DATABASE_SECRET_FILES_REQUIRED",
			"RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED",
		} {
			if os.Getenv(required) != "true" {
				return fmt.Errorf("protected download edge database policy is incomplete")
			}
		}
	}
	rawDedicatedDSN, resolveErr := resolveDownloadEdgeDatabaseDSN("RELAY_DOWNLOAD_EDGE_SQL_DSN")
	if resolveErr != nil {
		return fmt.Errorf("download edge database DSN source is invalid")
	}
	dsn := strings.TrimSpace(rawDedicatedDSN)
	if rawDedicatedDSN != dsn || strings.ContainsAny(rawDedicatedDSN, "\r\n\x00") {
		return fmt.Errorf("download edge database DSN is not normalized")
	}
	if protectedDeployment {
		if dsn == "" {
			return fmt.Errorf("protected download edge requires a dedicated database DSN file")
		}
		if downloadEdgeDSNIsPlaceholder(dsn) {
			return fmt.Errorf("protected download edge database DSN contains a placeholder")
		}
	} else if dsn == "" {
		genericDSN, genericErr := resolveDownloadEdgeDatabaseDSN("SQL_DSN")
		if genericErr != nil {
			return fmt.Errorf("download edge database DSN source is invalid")
		}
		dsn = strings.TrimSpace(genericDSN)
	}
	if dsn == "" {
		return fmt.Errorf("download edge database DSN is required")
	}
	if protectedDeployment {
		parsed, err := url.Parse(dsn)
		if err != nil || (parsed.Scheme != "postgres" && parsed.Scheme != "postgresql") || parsed.User == nil {
			return fmt.Errorf("protected download edge requires PostgreSQL")
		}
		username := strings.ToLower(parsed.User.Username())
		if username != "relay_download_edge" {
			return fmt.Errorf("protected download edge requires a dedicated runtime database role")
		}
	}
	if err := model.ValidateRelayPostgresTransportDSN(dsn); err != nil {
		return fmt.Errorf("download edge database TLS configuration is invalid")
	}
	if err := model.ValidateRelayPostgresSearchPathDSN(dsn); err != nil {
		return fmt.Errorf("download edge database search path is invalid")
	}
	if dedicatedFile := os.Getenv("RELAY_DOWNLOAD_EDGE_SQL_DSN_FILE"); dedicatedFile != "" {
		if err := os.Setenv("SQL_DSN_FILE", dedicatedFile); err != nil {
			return fmt.Errorf("configure download edge database file: %w", err)
		}
	} else if err := os.Setenv("SQL_DSN", dsn); err != nil {
		return fmt.Errorf("configure download edge database: %w", err)
	}
	// The main new-api service owns migrations. The public edge connects with a
	// dedicated DML-only role and must never attempt AutoMigrate or other DDL.
	common.IsMasterNode = false
	return nil
}

func downloadEdgeDSNIsPlaceholder(dsn string) bool {
	normalized := strings.NewReplacer("-", "", "_", "", ".", "", " ", "").Replace(strings.ToLower(dsn))
	for _, marker := range []string{
		"changeme", "replaceme", "replacewith", "placeholder", "developmentonly", "exampleonly",
		"localnewapipostgrespassword",
	} {
		if strings.Contains(normalized, marker) {
			return true
		}
	}
	return false
}
