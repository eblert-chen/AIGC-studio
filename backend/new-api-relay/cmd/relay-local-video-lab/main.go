//go:build relay_local_video_lab

// relay-local-video-lab is a separate development executable. It is excluded
// from ordinary builds and never participates in a production candidate image.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/localvideoconfig"
	"github.com/QuantumNous/new-api/relay"
	"github.com/QuantumNous/new-api/service"
	"github.com/gin-gonic/gin"
	"github.com/go-redis/redis/v8"
)

func main() {
	log.SetOutput(os.Stderr)
	gin.DefaultWriter, gin.DefaultErrorWriter = os.Stderr, os.Stderr
	options, err := parseLabOptions(os.Args[1:], os.Stderr)
	if errors.Is(err, flag.ErrHelp) {
		return
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if err := runLab(ctx, options, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, "local video lab:", err)
		os.Exit(1)
	}
}

type labProbeRequest struct {
	ChannelID int                                   `json:"channel_id"`
	Method    string                                `json:"method"`
	Path      string                                `json:"path"`
	RequestID string                                `json:"request_id"`
	Body      dto.PlatformChannelControlTestRequest `json:"body"`
}

type labSummary struct {
	localvideoconfig.Summary
	Kind             string            `json:"kind"`
	PublicBaseURL    string            `json:"public_base_url"`
	PaidCreateLocked bool              `json:"paid_create_locked"`
	ProbeRequests    []labProbeRequest `json:"probe_requests"`
	ConfigFile       string            `json:"private_runtime_config_file"`
}

func buildLabSummary(config localvideoconfig.Config, options labOptions) labSummary {
	summary := labSummary{
		Summary: config.Summary(), Kind: "new-api-local-video-lab", PublicBaseURL: options.PublicBaseURL,
		PaidCreateLocked: options.Mode == "live",
		ConfigFile:       filepath.Join(options.StateDirectory, "runtime-environment.json"),
		ProbeRequests:    make([]labProbeRequest, 0, len(config.Models)),
	}
	for index, item := range config.Models {
		route := config.Routes[item.PublicModelID][0]
		operationID := fmt.Sprintf("lab-probe-%s-%d", config.StateID, index)
		summary.ProbeRequests = append(summary.ProbeRequests, labProbeRequest{
			ChannelID: route.ChannelID, Method: http.MethodPost,
			Path:      fmt.Sprintf("/internal/platform-generation-operations/channels/%d/test", route.ChannelID),
			RequestID: fmt.Sprintf("lab-probe-%d-%.48s", index, config.StateID),
			Body: dto.PlatformChannelControlTestRequest{
				OperationID: operationID, TenantID: config.Principal.TenantID, Actor: "local-video-lab",
				Reason:        "Explicit isolated " + config.Mode + " video route verification",
				PublicModelID: item.PublicModelID, RouteID: route.RouteID,
			},
		})
	}
	return summary
}

func configureLab(options labOptions, state *labState) (localvideoconfig.Config, map[string]string, []labMockModel, error) {
	keys := localvideoconfig.ProviderKeys{}
	if options.Mode == "live" {
		for _, source := range []struct {
			path        string
			destination *string
		}{{options.ArkKeyFile, &keys.Ark}, {options.MiniMaxKeyFile, &keys.MiniMax}} {
			if source.path == "" {
				continue
			}
			if !filepath.IsAbs(source.path) {
				return localvideoconfig.Config{}, nil, nil, errors.New("live provider key files must be absolute server-side paths")
			}
			payload, err := readLabPrivateFile(source.path, 4096)
			if err != nil {
				return localvideoconfig.Config{}, nil, nil, err
			}
			*source.destination = strings.TrimSpace(string(payload))
		}
	}
	config, err := localvideoconfig.Build(localvideoconfig.Options{
		Mode: options.Mode, Environment: "development", Namespace: options.Namespace,
		Now: state.Manifest.CreatedAt, CreatedBy: "local-video-lab", Reason: "Explicit isolated local video integration",
		ModelIDs: options.ModelIDs, Keys: keys, RuntimeSeed: state.Seed, CallbackURL: options.CallbackURL, IsolatedDocker: options.ContainerLab,
	})
	if err != nil {
		return config, nil, nil, err
	}
	if state.Manifest.StateID != "" && state.Manifest.StateID != config.StateID {
		return config, nil, nil, errors.New("provider keys, models or profiles changed; a separate state directory is required")
	}
	state.Manifest.StateID = config.StateID
	if err := writeLabJSON(filepath.Join(state.Directory, "lab-state.json"), state.Manifest, false); err != nil {
		return config, nil, nil, err
	}
	environment, err := config.RuntimeEnvironment()
	if err != nil {
		return config, nil, nil, err
	}
	_, port, _ := net.SplitHostPort(options.Listen)
	environment["RELAY_COMPAT_INTERNAL_BASE_URL"] = "http://127.0.0.1:" + port
	environment["RELAY_ARTIFACT_FILESYSTEM_ROOT"] = filepath.Join(state.Directory, "artifacts")
	environment["RELAY_ARTIFACT_PUBLIC_BASE_URL"] = strings.TrimRight(options.PublicBaseURL, "/")
	environment["RELAY_ARTIFACT_SPOOL_DIRECTORY"] = filepath.Join(state.Directory, "spool")
	environment["RELAY_ARTIFACT_MAX_BYTES"] = "67108864"
	environment["RELAY_COMPAT_DELAY_QUEUE_RECOVERY_SECONDS"] = "1"
	environment["RELAY_NATIVE_PAID_COMPAT_ENABLED"] = "false"
	environment["RELAY_DATABASE_TLS_ATTESTATION_REQUIRED"] = "false"
	environment["RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED"] = "false"
	environment["PLATFORM_LAB_RELAY_BASE_URL"] = options.PublicBaseURL
	environment["PLATFORM_LAB_RELAY_CALLBACK_URL"] = config.Principal.CallbackURL
	models := make([]labMockModel, 0, len(config.Models))
	for _, channel := range config.Channels {
		base, err := url.Parse(channel.BaseURL)
		if err != nil || base.Scheme != "https" || base.RawQuery != "" || base.User != nil || base.Fragment != "" {
			return config, nil, nil, errors.New("local provider origin must be exact HTTPS")
		}
		family := "ark"
		if channel.Type == constant.ChannelTypeMiniMax {
			family = "minimax"
		}
		for _, id := range channel.ProviderModelIDs {
			models = append(models, labMockModel{ID: id, Family: family, Host: base.Hostname(), Key: channel.Key})
		}
	}
	if options.Mode == "mock" {
		if err := prepareLabMockTLS(state.Directory, models); err != nil {
			return config, nil, nil, err
		}
		environment["SSL_CERT_FILE"] = filepath.Join(state.Directory, "mock-ca.pem")
	}
	if err := configureLabArtifactStorage(options, state, config, environment); err != nil {
		return config, nil, nil, err
	}
	return config, environment, models, nil
}

func runLab(ctx context.Context, options labOptions, output io.Writer) error {
	if err := rejectProtectedLabEnvironment(); err != nil {
		return err
	}
	state, err := openLabState(options)
	if err != nil {
		return err
	}
	defer state.close()
	config, environment, models, err := configureLab(options, state)
	if err != nil {
		return err
	}
	summary := buildLabSummary(config, options)
	if err := writeLabJSON(filepath.Join(state.Directory, "runtime-environment.json"), environment, false); err != nil {
		return err
	}
	if err := writeLabJSON(filepath.Join(state.Directory, "summary.json"), summary, false); err != nil {
		return err
	}
	if err := writeLabJSON(filepath.Join(state.Directory, "manifest.json"), config, false); err != nil {
		return err
	}
	if options.Prepare {
		payload, err := common.Marshal(summary)
		if err != nil {
			return err
		}
		_, err = fmt.Fprintln(output, string(payload))
		return err
	}
	if options.Mode == "live" && (os.Getenv("SSL_CERT_FILE") != "" || os.Getenv("SSL_CERT_DIR") != "") {
		return errors.New("live lab refuses inherited mock or custom TLS roots")
	}
	// Install the mock CA before any outbound TLS code can initialize SystemCertPool.
	for key, value := range environment {
		if strings.HasPrefix(key, "PLATFORM_LAB_") {
			continue
		}
		if err := os.Setenv(key, value); err != nil {
			return errors.New("could not install local runtime configuration")
		}
	}
	for _, directory := range []string{"artifacts", "spool"} {
		if err := os.MkdirAll(filepath.Join(state.Directory, directory), 0700); err != nil {
			return errors.New("could not create private artifact directories")
		}
	}
	if err := service.InitHttpClient(); err != nil {
		return err
	}
	var mock *labMockProvider
	var tlsServer *http.Server
	if options.Mode == "mock" {
		fixtures, err := loadLabVideoFixtures(options.FixtureManifest)
		if err != nil {
			return err
		}
		mock, err = newLabMockProvider(filepath.Join(state.Directory, "mock-tasks"), models, fixtures, state.Seed)
		if err != nil {
			return err
		}
		inputCA, err := readLabPrivateFile(filepath.Join(state.Directory, "object-store", "certs", "ca.pem"), 64*1024)
		if err != nil {
			return err
		}
		mock.inputClient, err = newLabMockInputClient(inputCA)
		if err != nil {
			return err
		}
		defer mock.inputClient.CloseIdleConnections()
		tlsAddress := "127.0.0.1:443"
		if options.ContainerLab {
			tlsAddress = "0.0.0.0:443"
		}
		listener, err := net.Listen("tcp", tlsAddress)
		if err != nil {
			return errors.New("could not bind the isolated mock HTTPS port 443")
		}
		tlsServer = &http.Server{Handler: mock, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 10 * time.Second}
		defer tlsServer.Close()
		go func() {
			_ = tlsServer.ServeTLS(listener, filepath.Join(state.Directory, "mock-certificate.pem"), filepath.Join(state.Directory, "mock-private-key.pem"))
		}()
		if err := installLabMockTransport(models, state.Directory, environment["RELAY_COMPAT_INTERNAL_BASE_URL"]); err != nil {
			return err
		}
	}
	paid, err := loadLabPaidAuthorization(options, config, state.Directory)
	if err != nil {
		return err
	}
	if options.Mode == "live" {
		if err := installLabLiveGuard(models, environment["RELAY_COMPAT_INTERNAL_BASE_URL"], paid); err != nil {
			return err
		}
	}
	cleanupDB, err := initializeLabDatabase(state.Directory, config)
	if err != nil {
		return err
	}
	defer cleanupDB()
	redisOptions, err := redis.ParseURL(options.RedisURL)
	if err != nil {
		return errors.New("local Redis URL is invalid")
	}
	redisOptions.MaxRetries = -1
	redisClient := redis.NewClient(redisOptions)
	defer redisClient.Close()
	if err := redisClient.Ping(ctx).Err(); err != nil {
		return errors.New("isolated local Redis is unavailable")
	}
	common.RDB, common.RedisEnabled = redisClient, true
	initializeLabNativePollingDefaults()
	service.GetTaskAdaptorFunc = func(platform constant.TaskPlatform) service.TaskPollingAdaptor {
		adapter := relay.GetTaskAdaptor(platform)
		if adapter == nil {
			return nil
		}
		return adapter
	}
	if err := service.SyncPlatformGenerationProviderRoutes(); err != nil {
		return fmt.Errorf("local provider route validation: %w", err)
	}
	if err := service.ValidatePlatformGenerationWorkerConfiguration(); err != nil {
		return fmt.Errorf("local worker configuration: %w", err)
	}
	engine, err := buildLabRouter(options, state, config, summary, mock, paid)
	if err != nil {
		return err
	}
	listener, err := net.Listen("tcp", options.Listen)
	if err != nil {
		return errors.New("could not bind the explicit local HTTP listener")
	}
	server := &http.Server{Handler: engine, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second}
	serverErrors := make(chan error, 1)
	go func() { serverErrors <- server.Serve(listener) }()
	defer server.Close()
	if err := service.StartPlatformGenerationWorkers(); err != nil {
		return err
	}
	defer func() {
		stopCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		_ = service.StopPlatformGenerationWorkers(stopCtx)
	}()
	pollCtx, cancelPoll := context.WithCancel(ctx)
	pollDone := make(chan struct{})
	go runLabNativePolling(pollCtx, pollDone)
	defer func() { cancelPoll(); <-pollDone }()
	fmt.Fprintln(os.Stderr, "new-api local video lab running:", options.Mode, options.Listen, "state", config.StateID)
	select {
	case <-ctx.Done():
		stopCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		return server.Shutdown(stopCtx)
	case err := <-serverErrors:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return errors.New("local Relay HTTP server stopped")
	}
}

func initializeLabNativePollingDefaults() {
	// This independent entry intentionally does not run common.InitEnv, which
	// loads unrelated production globals and flags. A zero TaskQueryLimit makes
	// the real polling scan silently return no tasks, so install its two bounded
	// production defaults explicitly before any workers begin. Ambient settings
	// cannot disable the isolated lab's native polling or timeout sweep.
	constant.TaskQueryLimit = 1000
	constant.TaskTimeoutMinutes = 1440
}

func runLabNativePolling(ctx context.Context, done chan<- struct{}) {
	defer close(done)
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			service.RunTaskPollingOnce(ctx, nil)
			// This discovers real provider terminal facts. With no contract rate,
			// the normal worker records incomplete reconciliation rather than zero.
			_, _ = service.RunPlatformChannelCostReconciliationOnce(ctx, 30*time.Second)
		}
	}
}
