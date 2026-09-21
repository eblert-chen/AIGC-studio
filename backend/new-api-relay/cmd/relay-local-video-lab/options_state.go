//go:build relay_local_video_lab

package main

import (
	"crypto/rand"
	"encoding/base64"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
)

const labArtifactHost = "video-artifacts.local.test"

type labOptions struct {
	Mode             string
	Namespace        string
	StateDirectory   string
	Listen           string
	PublicBaseURL    string
	RedisURL         string
	CallbackURL      string
	ModelIDs         []string
	ArkKeyFile       string
	MiniMaxKeyFile   string
	FixtureManifest  string
	PaidApprovalFile string
	ContainerLab     bool
	AllowPaidProbe   bool
	Prepare          bool
}

func parseLabOptions(arguments []string, output io.Writer) (labOptions, error) {
	options := labOptions{}
	flags := flag.NewFlagSet("relay-local-video-lab", flag.ContinueOnError)
	flags.SetOutput(output)
	flags.StringVar(&options.Mode, "mode", "mock", "mock or live; mock never contacts a provider")
	flags.StringVar(&options.Namespace, "namespace", "video-lab", "isolated local run name")
	flags.StringVar(&options.StateDirectory, "state-dir", "", "required dedicated absolute directory")
	flags.StringVar(&options.Listen, "listen", "127.0.0.1:3000", "local HTTP listener")
	flags.StringVar(&options.PublicBaseURL, "public-base-url", "", "local Relay URL reachable by Platform")
	flags.StringVar(&options.RedisURL, "redis-url", "redis://127.0.0.1:6379/0", "dedicated local Redis")
	flags.StringVar(&options.CallbackURL, "callback-url", "http://127.0.0.1:3000/lab/callback", "exact development callback target")
	flags.StringVar(&options.ArkKeyFile, "ark-key-file", "", "live-only server-side Ark key file")
	flags.StringVar(&options.MiniMaxKeyFile, "minimax-key-file", "", "live-only server-side MiniMax key file")
	flags.StringVar(&options.FixtureManifest, "fixture-manifest", "", "mock-only JSON mapping output specifications to local real MP4 files")
	flags.StringVar(&options.PaidApprovalFile, "paid-probe-approval", "", "live-only private, state-bound and count-limited provider-create approval")
	flags.BoolVar(&options.ContainerLab, "container-lab", false, "allow the documented isolated Docker service names")
	flags.BoolVar(&options.AllowPaidProbe, "allow-paid-probe", false, "explicitly authorize provider creates in live mode")
	flags.BoolVar(&options.Prepare, "prepare", false, "write private local configuration without starting services")
	modelIDs := flags.String("models", "", "comma-separated public model IDs; empty selects the current reviewed catalog")
	if err := flags.Parse(arguments); err != nil {
		return options, err
	}
	if flags.NArg() != 0 {
		return options, errors.New("unexpected positional arguments")
	}
	if *modelIDs != "" {
		options.ModelIDs = strings.Split(*modelIDs, ",")
	}
	if err := validateLabOptions(options); err != nil {
		return options, err
	}
	if options.PublicBaseURL == "" {
		if options.ContainerLab {
			_, port, _ := net.SplitHostPort(options.Listen)
			options.PublicBaseURL = "http://relay-lab:" + port
		} else {
			options.PublicBaseURL = "http://" + options.Listen
		}
	}
	return options, nil
}

func validateLabOptions(options labOptions) error {
	if options.Mode != "mock" && options.Mode != "live" {
		return errors.New("mode must be mock or live")
	}
	if options.StateDirectory == "" || !filepath.IsAbs(options.StateDirectory) {
		return errors.New("a dedicated absolute --state-dir is required")
	}
	if !regexp.MustCompile(`^[a-z][a-z0-9-]{0,39}$`).MatchString(options.Namespace) {
		return errors.New("namespace must contain lower-case letters, digits and hyphens")
	}
	host, port, err := net.SplitHostPort(options.Listen)
	portNumber, portErr := strconv.Atoi(port)
	if err != nil || portErr != nil || portNumber < 1 || portNumber > 65535 || strconv.Itoa(portNumber) != port {
		return errors.New("listen must have an explicit nonzero port")
	}
	if !labLoopbackHost(host) && !(options.ContainerLab && host == "0.0.0.0") {
		return errors.New("non-loopback listen requires --container-lab and 0.0.0.0")
	}
	redisURL, err := url.Parse(options.RedisURL)
	if err != nil || redisURL.Scheme != "redis" || redisURL.User != nil || redisURL.RawQuery != "" || redisURL.Fragment != "" ||
		(!labLoopbackHost(redisURL.Hostname()) && !(options.ContainerLab && redisURL.Hostname() == "redis-lab")) {
		return errors.New("Redis must be an explicit local Redis URL")
	}
	if options.Mode == "mock" && (options.ArkKeyFile != "" || options.MiniMaxKeyFile != "" || options.AllowPaidProbe || options.PaidApprovalFile != "") {
		return errors.New("mock mode rejects provider key files and paid authorization")
	}
	if options.Mode == "live" && options.FixtureManifest != "" {
		return errors.New("live mode rejects mock media fixtures")
	}
	if options.AllowPaidProbe != (options.PaidApprovalFile != "") {
		return errors.New("live paid creates require both --allow-paid-probe and --paid-probe-approval")
	}
	if options.PublicBaseURL != "" {
		parsed, err := url.Parse(options.PublicBaseURL)
		if err != nil || parsed.Scheme != "http" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" ||
			(parsed.Path != "" && parsed.Path != "/") ||
			(!labLoopbackHost(parsed.Hostname()) && !(options.ContainerLab && parsed.Hostname() == "relay-lab")) {
			return errors.New("public-base-url must identify this local Relay")
		}
	}
	return nil
}

func labLoopbackHost(host string) bool {
	address := net.ParseIP(host)
	return host == "localhost" || (address != nil && address.IsLoopback())
}

func rejectProtectedLabEnvironment() error {
	for _, name := range []string{"APP_ENV", "ENVIRONMENT", "DEPLOYMENT_ENV", "RELAY_COMPAT_ENVIRONMENT"} {
		value := strings.ToLower(strings.TrimSpace(os.Getenv(name)))
		if value == "staging" || value == "production" {
			return errors.New("local video lab refuses a staging or production environment")
		}
	}
	for _, name := range []string{
		"SQL_DSN", "SQL_DSN_FILE", "LOG_SQL_DSN", "LOG_SQL_DSN_FILE",
		"RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "RELAY_DATABASE_TLS_ATTESTATION_REQUIRED",
		"RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "RELAY_DATABASE_RELEASE_IDENTITY_FILE",
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "RELAY_COMPAT_RUNTIME_CONFIG_FILE",
	} {
		value := strings.TrimSpace(os.Getenv(name))
		if value != "" && value != "false" {
			return fmt.Errorf("local video lab rejects inherited %s", name)
		}
	}
	if common.TLSInsecureSkipVerify {
		return errors.New("local video lab refuses TLS verification bypass")
	}
	return nil
}

type labStateManifest struct {
	SchemaVersion int       `json:"schema_version"`
	Kind          string    `json:"kind"`
	Mode          string    `json:"mode"`
	Namespace     string    `json:"namespace"`
	CreatedAt     time.Time `json:"created_at"`
	StateID       string    `json:"state_id"`
	CallbackURL   string    `json:"callback_url"`
}

type labState struct {
	Directory string
	Manifest  labStateManifest
	Seed      []byte
	lockPath  string
}

func openLabState(options labOptions) (_ *labState, returnedErr error) {
	directory := filepath.Clean(options.StateDirectory)
	current, _ := os.Getwd()
	userHome, _ := os.UserHomeDir()
	if directory == filepath.VolumeName(directory)+string(filepath.Separator) || directory == current || directory == userHome {
		return nil, errors.New("state directory cannot be a filesystem, workspace or home root")
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		return nil, errors.New("could not create dedicated lab state directory")
	}
	info, err := os.Lstat(directory)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("state directory must be a real directory, not a symlink")
	}
	resolved, err := filepath.EvalSymlinks(directory)
	if err != nil || !strings.EqualFold(filepath.Clean(resolved), directory) {
		return nil, errors.New("state directory must not traverse symlinks")
	}
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, errors.New("could not inspect lab state directory")
	}
	manifestPath := filepath.Join(directory, "lab-state.json")
	_, manifestErr := os.Stat(manifestPath)
	if os.IsNotExist(manifestErr) && len(entries) != 0 {
		return nil, errors.New("unmarked nonempty state directory is not a lab workspace")
	}
	state := &labState{Directory: directory, lockPath: filepath.Join(directory, "lab.lock")}
	lock, err := os.OpenFile(state.lockPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return nil, errors.New("state directory is already locked; verify its owner before removing a stale lab.lock")
	}
	_ = lock.Close()
	defer func() {
		if returnedErr != nil {
			state.close()
		}
	}()
	if os.IsNotExist(manifestErr) {
		state.Manifest = labStateManifest{
			SchemaVersion: 1, Kind: "new-api-local-video-lab", Mode: options.Mode,
			Namespace: options.Namespace, CreatedAt: time.Now().UTC().Truncate(time.Second), CallbackURL: options.CallbackURL,
		}
		state.Seed = make([]byte, 32)
		if _, err := rand.Read(state.Seed); err != nil {
			return nil, errors.New("could not generate the lab runtime seed")
		}
		if err := writeLabJSON(manifestPath, state.Manifest, true); err != nil {
			return nil, err
		}
		if err := writeLabPrivateFile(filepath.Join(directory, "runtime-seed"), []byte(base64.StdEncoding.EncodeToString(state.Seed)), true); err != nil {
			return nil, err
		}
		return state, nil
	}
	manifestBytes, err := readLabPrivateFile(manifestPath, 16*1024)
	if err != nil || common.Unmarshal(manifestBytes, &state.Manifest) != nil || state.Manifest.SchemaVersion != 1 || state.Manifest.Kind != "new-api-local-video-lab" {
		return nil, errors.New("existing state is not a valid local video lab manifest")
	}
	if state.Manifest.Mode != options.Mode || state.Manifest.Namespace != options.Namespace || state.Manifest.CallbackURL != options.CallbackURL {
		return nil, errors.New("mode, namespace or callback changed; use a separate lab state directory")
	}
	seedBytes, err := readLabPrivateFile(filepath.Join(directory, "runtime-seed"), 256)
	if err != nil {
		return nil, err
	}
	state.Seed, err = base64.StdEncoding.DecodeString(string(seedBytes))
	if err != nil || len(state.Seed) != 32 || state.Manifest.CreatedAt.IsZero() {
		return nil, errors.New("lab runtime seed or creation time is invalid")
	}
	return state, nil
}

func (state *labState) close() {
	if state != nil && state.lockPath != "" {
		_ = os.Remove(state.lockPath)
		state.lockPath = ""
	}
}

func writeLabJSON(path string, value any, createOnly bool) error {
	payload, err := common.Marshal(value)
	if err != nil {
		return errors.New("could not serialize lab state")
	}
	return writeLabPrivateFile(path, payload, createOnly)
}

func writeLabPrivateFile(path string, payload []byte, createOnly bool) error {
	if createOnly {
		file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return errors.New("could not create private lab state file")
		}
		defer file.Close()
		if _, err := file.Write(payload); err != nil {
			return errors.New("could not write private lab state file")
		}
		return file.Sync()
	}
	if info, err := os.Lstat(path); err == nil && (!info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0) {
		return errors.New("refusing a non-regular lab state file")
	}
	// Replace one already-owned file atomically. In particular a restart cannot
	// observe an empty paid-create counter between truncation and its rewrite.
	file, err := os.CreateTemp(filepath.Dir(path), ".lab-state-")
	if err != nil {
		return errors.New("could not create private lab state file")
	}
	temporaryPath := file.Name()
	defer os.Remove(temporaryPath)
	defer file.Close()
	if err := file.Chmod(0600); err != nil {
		return errors.New("could not restrict lab state file permissions")
	}
	if _, err := file.Write(payload); err != nil {
		return errors.New("could not write private lab state file")
	}
	if err := file.Sync(); err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return errors.New("could not atomically replace private lab state file")
	}
	return nil
}

func readLabPrivateFile(path string, limit int64) ([]byte, error) {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Size() < 1 || info.Size() > limit {
		return nil, errors.New("private lab file is missing, oversized or unsafe")
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, errors.New("private lab file could not be read")
	}
	return payload, nil
}
