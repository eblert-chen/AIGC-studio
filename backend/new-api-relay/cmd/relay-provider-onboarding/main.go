// relay-provider-onboarding is a one-shot, credential-late provisioning tool.
// It is intentionally separate from Relay startup: missing credentials cannot
// create a channel, and no candidate is made callable merely by being compiled
// into the model directory.
package main

import (
	"bytes"
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
)

const (
	providerOnboardingCredentialEnvironment  = "RELAY_PROVIDER_ONBOARDING_FILE"
	providerOnboardingMaximumCredentialBytes = int64(128 * 1024)
	providerOnboardingMaximumRoutesBytes     = int64(32 * 1024 * 1024)
	providerOnboardingPhaseCheckCredential   = "check-credential"
)

type onboardingOptions struct {
	environment      string
	phase            string
	managedProvider  string
	routesPath       string
	currentRoutes    string
	replaceAllRoutes bool
	createdAt        string
	createdBy        string
	reason           string
	rpmLimit         int
	activeTaskLimit  int
}

var readProviderOnboardingCredential = common.ReadProtectedSecretFile
var loadManagedProviderOnboardingCredential = service.LoadProviderOnboardingCredentialInput

func main() {
	if err := run(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintln(os.Stderr, "provider onboarding failed:", err)
		os.Exit(1)
	}
}

func run(args []string, output io.Writer, diagnostics io.Writer) error {
	options := onboardingOptions{}
	flags := flag.NewFlagSet("relay-provider-onboarding", flag.ContinueOnError)
	flags.SetOutput(diagnostics)
	flags.StringVar(&options.environment, "environment", "", "required exact target: development, staging, or production")
	flags.StringVar(&options.phase, "phase", service.CredentialLateProviderPhaseFinalize, "exact phase: check-credential, stage, plan-routes, or finalize")
	flags.StringVar(&options.managedProvider, "managed-provider", "", "load the write-only credential managed by New API: google-gemini-api, minimax, or volcengine-ark")
	flags.StringVar(&options.routesPath, "signed-routes", "", "finalize-only absolute complete signed route-inventory JSON path")
	flags.StringVar(&options.currentRoutes, "current-routes", "", "plan-routes-only absolute complete unsigned development route-inventory JSON path")
	flags.BoolVar(&options.replaceAllRoutes, "replace-all-routes", false, "explicitly replace the complete development route inventory")
	flags.StringVar(&options.createdAt, "created-at", "", "plan-routes-only canonical UTC RFC3339 audit timestamp")
	flags.StringVar(&options.createdBy, "created-by", "", "plan-routes-only operator identity")
	flags.StringVar(&options.reason, "reason", "", "plan-routes-only audit reason")
	flags.IntVar(&options.rpmLimit, "rpm-limit", 0, "plan-routes-only positive account RPM limit")
	flags.IntVar(&options.activeTaskLimit, "active-task-limit", 0, "plan-routes-only positive account active-task limit")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("positional arguments are not accepted")
	}
	if options.environment != "development" && options.environment != "staging" && options.environment != "production" {
		return errors.New("--environment must be development, staging, or production")
	}
	if options.phase != providerOnboardingPhaseCheckCredential &&
		options.phase != service.CredentialLateProviderPhaseStage &&
		options.phase != service.CredentialLateProviderPhasePlanRoutes &&
		options.phase != service.CredentialLateProviderPhaseFinalize {
		return errors.New("--phase must be check-credential, stage, plan-routes, or finalize")
	}
	if options.managedProvider != "" &&
		options.managedProvider != "google-gemini-api" &&
		options.managedProvider != "minimax" &&
		options.managedProvider != "volcengine-ark" {
		return errors.New("--managed-provider must be google-gemini-api, minimax, or volcengine-ark")
	}
	legacyCredentialConfigured := strings.TrimSpace(os.Getenv(providerOnboardingCredentialEnvironment)) != ""
	if options.managedProvider != "" && legacyCredentialConfigured {
		return errors.New("--managed-provider and RELAY_PROVIDER_ONBOARDING_FILE are mutually exclusive")
	}
	planFlagsSet := options.currentRoutes != "" || options.createdAt != "" || options.createdBy != "" ||
		options.reason != "" || options.rpmLimit != 0 || options.activeTaskLimit != 0
	if (options.phase == providerOnboardingPhaseCheckCredential || options.phase == service.CredentialLateProviderPhaseStage) &&
		(options.routesPath != "" || planFlagsSet || options.replaceAllRoutes) {
		return errors.New("route inventory options are not accepted during credential check or stage")
	}
	if options.phase == service.CredentialLateProviderPhaseFinalize && planFlagsSet {
		return errors.New("route planning options are not accepted during finalize")
	}
	if options.phase == service.CredentialLateProviderPhasePlanRoutes {
		if options.environment != "development" {
			return errors.New("plan-routes is restricted to development")
		}
		if options.routesPath != "" {
			return errors.New("--signed-routes is not accepted during plan-routes")
		}
		if (options.currentRoutes != "") == options.replaceAllRoutes {
			return errors.New("plan-routes requires exactly one of --current-routes or --replace-all-routes")
		}
	}
	if options.replaceAllRoutes && options.environment != "development" {
		return errors.New("--replace-all-routes is restricted to development")
	}
	var routes []byte
	var err error
	if options.phase == service.CredentialLateProviderPhaseFinalize {
		routes, err = readStableOnboardingFile(options.routesPath, providerOnboardingMaximumRoutesBytes)
		if err != nil {
			return fmt.Errorf("read complete signed route inventory: %w", err)
		}
	} else if options.phase == service.CredentialLateProviderPhasePlanRoutes && options.currentRoutes != "" {
		routes, err = readStableOnboardingFile(options.currentRoutes, providerOnboardingMaximumRoutesBytes)
		if err != nil {
			return fmt.Errorf("read complete current development route inventory: %w", err)
		}
	}
	var input service.CredentialLateProviderInput
	if options.managedProvider == "" {
		credentialJSON, err := readProviderOnboardingCredential(providerOnboardingCredentialEnvironment, providerOnboardingMaximumCredentialBytes)
		if err != nil {
			return errors.New("protected provider credential is unavailable")
		}
		defer clear(credentialJSON)
		if err := common.RejectDuplicateJSONKeys(credentialJSON); err != nil {
			return errors.New("protected provider credential JSON is ambiguous")
		}
		if err := common.DecodeJsonDisallowUnknownFields(bytes.NewReader(credentialJSON), &input); err != nil {
			return errors.New("protected provider credential JSON is invalid")
		}
	}

	common.InitEnv()
	if err := model.InitDB(); err != nil {
		return errors.New("Relay database initialization failed")
	}
	defer model.CloseDB()
	lock, err := model.AcquireRelayRuntimeLifecycleLock(context.Background(), model.DB)
	if err != nil {
		return errors.New("Relay lifecycle lock is unavailable")
	}
	defer model.ReleaseRelayLifecycleLockBounded(lock)
	if _, err := model.RequireRelaySchemaCurrent(model.DB); err != nil {
		return errors.New("Relay schema is not current")
	}
	if err := model.VerifyRelayRuntimeDatabaseRole(model.DB); err != nil {
		return errors.New("Relay runtime database role is not attested")
	}
	if options.managedProvider != "" {
		input, err = loadManagedProviderOnboardingCredential(options.managedProvider)
		if err != nil {
			return errors.New("managed provider credential is unavailable")
		}
		defer func() { input.APIKey = "" }()
	}
	var result any
	if options.phase == providerOnboardingPhaseCheckCredential {
		result = struct {
			Provider            string   `json:"provider"`
			AccountID           string   `json:"account_id"`
			ChannelID           int      `json:"channel_id"`
			PublicModelIDs      []string `json:"public_model_ids"`
			CredentialAvailable bool     `json:"credential_available"`
		}{
			Provider: input.Provider, AccountID: input.AccountID, ChannelID: input.ChannelID,
			PublicModelIDs: input.PublicModelIDs, CredentialAvailable: true,
		}
	} else if options.phase == service.CredentialLateProviderPhaseStage {
		result, err = service.StageCredentialLateProvider(input, options.environment)
	} else if options.phase == service.CredentialLateProviderPhasePlanRoutes {
		result, err = service.PlanDevelopmentCredentialLateProviderRoutes(input, service.CredentialLateProviderRoutePlanOptions{
			CurrentRoutesJSON: routes, ReplaceAllRoutes: options.replaceAllRoutes,
			CreatedAt: options.createdAt, CreatedBy: options.createdBy, Reason: options.reason,
			RPMLimit: options.rpmLimit, ActiveTaskLimit: options.activeTaskLimit,
		})
	} else {
		result, err = service.FinalizeCredentialLateProviderWithOptions(
			input,
			routes,
			options.environment,
			service.CredentialLateProviderFinalizeOptions{ReplaceAllRoutes: options.replaceAllRoutes},
		)
	}
	if err != nil {
		return err
	}
	encoded, err := common.Marshal(result)
	if err != nil {
		return errors.New("encode secret-free onboarding result")
	}
	encoded = append(encoded, '\n')
	written, err := output.Write(encoded)
	if err != nil {
		return err
	}
	if written != len(encoded) {
		return io.ErrShortWrite
	}
	return nil
}

func readStableOnboardingFile(path string, maximumBytes int64) ([]byte, error) {
	if !filepath.IsAbs(path) || filepath.Clean(path) != path || strings.ContainsAny(path, "\x00\r\n") || maximumBytes < 1 {
		return nil, errors.New("path is invalid")
	}
	before, err := os.Lstat(path)
	if err != nil || !before.Mode().IsRegular() || before.Mode()&os.ModeSymlink != 0 || before.Size() < 1 || before.Size() > maximumBytes {
		return nil, errors.New("file is unavailable or invalid")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, errors.New("file could not be opened")
	}
	defer file.Close()
	opened, err := file.Stat()
	if err != nil || !os.SameFile(before, opened) {
		return nil, errors.New("file identity changed while opening")
	}
	contents, err := io.ReadAll(io.LimitReader(file, maximumBytes+1))
	if err != nil || len(contents) == 0 || int64(len(contents)) != opened.Size() || int64(len(contents)) > maximumBytes {
		clear(contents)
		return nil, errors.New("file contents are invalid")
	}
	after, err := os.Lstat(path)
	if err != nil || !after.Mode().IsRegular() || after.Mode()&os.ModeSymlink != 0 || !os.SameFile(opened, after) ||
		after.Size() != opened.Size() || !after.ModTime().Equal(opened.ModTime()) {
		clear(contents)
		return nil, errors.New("file identity changed while reading")
	}
	return contents, nil
}
