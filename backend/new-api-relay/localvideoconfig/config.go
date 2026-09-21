// Package localvideoconfig prepares development-only video configurations from
// the compiled provider catalogs. It performs no I/O, signs no acceptance, and
// cannot enable a production deployment. The caller owns isolated state,
// credential loading, network isolation, and all runtime/database operations.
package localvideoconfig

import (
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
	"github.com/google/uuid"
)

const (
	ModeMock               = "mock"
	ModeLive               = "live"
	EnvironmentDevelopment = "development"
	ArkBaseURL             = "https://ark.cn-beijing.volces.com"
	MiniMaxChinaBaseURL    = "https://api.minimax.cn"
	MiniMaxGlobalBaseURL   = "https://api.minimax.io"
	MockArkProviderKey     = "local-video-lab-mock-ark-provider-key-never-live"
	MockMiniMaxProviderKey = "local-video-lab-mock-minimax-provider-key-never-live"
	TestRPMLimit           = 10
	TestActiveTaskLimit    = 2
)

var namespacePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

// ProviderKeys must be loaded by the server/CLI, never by a browser. Mock mode
// requires both fields to be empty; live mode cannot accept the mock keys.
type ProviderKeys struct {
	Ark     string `json:"-"`
	MiniMax string `json:"-"`
}

func (ProviderKeys) String() string   { return "ProviderKeys{redacted}" }
func (ProviderKeys) GoString() string { return "ProviderKeys{redacted}" }

type Options struct {
	Mode           string
	Environment    string
	Namespace      string
	Now            time.Time
	CreatedBy      string
	Reason         string
	ModelIDs       []string
	Keys           ProviderKeys `json:"-"`
	RuntimeSeed    []byte       `json:"-"`
	CallbackURL    string
	MiniMaxRegion  string // Empty or "cn" selects China; "global" selects international.
	IsolatedDocker bool   // Allows only the named local Platform callback below.
}

func (Options) String() string   { return "LocalVideoOptions{secrets redacted}" }
func (Options) GoString() string { return "LocalVideoOptions{secrets redacted}" }

type Model struct {
	PublicModelID             string                             `json:"public_model_id"`
	ProviderModelID           string                             `json:"provider_model_id"`
	DisplayName               string                             `json:"display_name"`
	ProviderName              string                             `json:"provider_name"`
	CatalogSource             string                             `json:"catalog_source"`
	OfficialSources           []string                           `json:"official_sources"`
	CompatibilityNotes        []string                           `json:"compatibility_notes"`
	Modes                     []string                           `json:"modes"`
	Profile                   generationprofile.Profile          `json:"profile"`
	Capability                dto.PlatformGenerationCapabilities `json:"capability"`
	CapabilityRevision        string                             `json:"capability_revision"`
	ReleaseCapabilityRevision string                             `json:"release_capability_revision"`
	Release                   generationrelease.Release          `json:"unsigned_model_release"`
}

type Channel struct {
	ID               int      `json:"id"`
	Type             int      `json:"type"`
	Name             string   `json:"name"`
	ProviderName     string   `json:"provider_name"`
	AccountID        string   `json:"account_id"`
	BaseURL          string   `json:"base_url"`
	ModelIDs         []string `json:"model_ids"`
	ProviderModelIDs []string `json:"provider_model_ids"`
	KeyIndex         int      `json:"key_index"`
	KeyFingerprint   string   `json:"key_fingerprint"`
	Key              string   `json:"-"`
}

func (c Channel) String() string {
	return fmt.Sprintf("LocalVideoChannel{id:%d, type:%d, key:redacted}", c.ID, c.Type)
}
func (c Channel) GoString() string { return c.String() }

// Route has the exact public shape accepted by RELAY_COMPAT_MODEL_ROUTES_JSON.
// One declaration contains every selected mode for a model; Relay's existing
// SyncPlatformGenerationProviderRoutes expands it into per-mode admission rows.
// Splitting a model into disjoint route capabilities would produce an empty
// failover-safe capability intersection, so this package never does that.
type Route struct {
	RouteID           string                             `json:"route_id"`
	ProviderName      string                             `json:"provider_name"`
	AccountID         string                             `json:"account_id"`
	ChannelID         int                                `json:"channel_id"`
	NativeChannelType int                                `json:"native_channel_type"`
	KeyIndex          int                                `json:"key_index"`
	KeyFingerprint    string                             `json:"key_fingerprint"`
	ChannelClass      string                             `json:"channel_class"`
	UpstreamModel     string                             `json:"upstream_model"`
	RPMLimit          int                                `json:"rpm_limit"`
	ActiveTaskLimit   int                                `json:"active_task_limit"`
	Capabilities      dto.PlatformGenerationCapabilities `json:"capabilities"`
	CapabilityProfile string                             `json:"capability_profile"`
	ModelRelease      *generationrelease.Release         `json:"model_release"`
}

type Principal struct {
	ClientID               string `json:"client_id"`
	TenantID               string `json:"tenant_id"`
	UserName               string `json:"user_name"`
	CallbackURL            string `json:"callback_url"`
	APIKey                 string `json:"-"`
	UpstreamToken          string `json:"-"`
	InternalAdmissionToken string `json:"-"`
	OperationsToken        string `json:"-"`
	CallbackSigningSecret  string `json:"-"`
	ArtifactSigningSecret  string `json:"-"`
	CredentialKeyringJSON  string `json:"-"`
	ReconciliationSecret   string `json:"-"`
}

func (p Principal) String() string {
	return "LocalVideoPrincipal{client:" + p.ClientID + ", secrets:redacted}"
}
func (p Principal) GoString() string { return p.String() }

type Config struct {
	SchemaVersion int                `json:"schema_version"`
	Mode          string             `json:"mode"`
	Environment   string             `json:"environment"`
	StateID       string             `json:"state_id"`
	Status        string             `json:"status"`
	Models        []Model            `json:"models"`
	Channels      []Channel          `json:"channels"`
	Routes        map[string][]Route `json:"routes"`
	Principal     Principal          `json:"principal"`
	options       Options
}

func (c Config) String() string {
	return "LocalVideoConfig{mode:" + c.Mode + ", state:" + c.StateID + ", secrets:redacted}"
}
func (c Config) GoString() string { return c.String() }

type SummaryModel struct {
	PublicModelID          string   `json:"public_model_id"`
	ProviderModelID        string   `json:"provider_model_id"`
	DisplayName            string   `json:"display_name"`
	AdapterProfileID       string   `json:"adapter_profile_id"`
	AdapterProfileRevision string   `json:"adapter_profile_revision"`
	CapabilityRevision     string   `json:"capability_revision"`
	Modes                  []string `json:"modes"`
}

type Summary struct {
	SchemaVersion   int            `json:"schema_version"`
	Mode            string         `json:"mode"`
	Environment     string         `json:"environment"`
	StateID         string         `json:"state_id"`
	Status          string         `json:"status"`
	ProductionReady bool           `json:"production_ready"`
	Models          []SummaryModel `json:"models"`
}

// Summary is the only intended health/status/browser projection. It contains
// no provider keys, native tokens, account identities, or key fingerprints.
func (c Config) Summary() Summary {
	summary := Summary{SchemaVersion: c.SchemaVersion, Mode: c.Mode, Environment: c.Environment, StateID: c.StateID, Status: c.Status, Models: make([]SummaryModel, 0, len(c.Models))}
	for _, model := range c.Models {
		summary.Models = append(summary.Models, SummaryModel{
			PublicModelID: model.PublicModelID, ProviderModelID: model.ProviderModelID, DisplayName: model.DisplayName,
			AdapterProfileID: model.Profile.ID, AdapterProfileRevision: model.Profile.Revision,
			CapabilityRevision: model.CapabilityRevision, Modes: append([]string(nil), model.Modes...),
		})
	}
	return summary
}

// Build is deterministic for fixed inputs. Persist the namespace, review time,
// runtime seed, and selected models in a server-owned manifest for restarts.
// A changed key, mode, transport, capability, profile, or review produces a new
// state identity; callers must never reuse another identity's DB or receipts.
func Build(options Options) (Config, error) {
	if options.Environment != EnvironmentDevelopment {
		return Config{}, errors.New("local video configuration requires the exact development environment")
	}
	if options.Mode != ModeMock && options.Mode != ModeLive {
		return Config{}, errors.New("local video mode must be exactly mock or live")
	}
	if !namespacePattern.MatchString(options.Namespace) || options.Now.IsZero() || len(options.RuntimeSeed) < 32 || len(options.RuntimeSeed) > 128 {
		return Config{}, errors.New("local video namespace, review time, or runtime seed is invalid")
	}
	if options.MiniMaxRegion != "" && options.MiniMaxRegion != "cn" && options.MiniMaxRegion != "global" {
		return Config{}, errors.New("local video MiniMax region must be cn or global")
	}
	if err := validateCallbackURL(options.CallbackURL, options.IsolatedDocker); err != nil {
		return Config{}, err
	}
	models, err := availableModels(options.Now)
	if err != nil {
		return Config{}, err
	}
	selected := make(map[string]bool, len(options.ModelIDs))
	for _, id := range options.ModelIDs {
		if selected[id] {
			return Config{}, errors.New("local video model selection contains duplicates")
		}
		selected[id] = true
	}
	if len(selected) > 0 {
		filtered := make([]Model, 0, len(selected))
		for _, model := range models {
			if selected[model.PublicModelID] {
				filtered = append(filtered, model)
				delete(selected, model.PublicModelID)
			}
		}
		if len(selected) != 0 {
			return Config{}, errors.New("requested model is unavailable for new local video routes")
		}
		models = filtered
	}
	if len(models) == 0 {
		return Config{}, errors.New("no reviewed models are eligible for new local video routes")
	}
	providerKeys := map[string]string{"volcengine": options.Keys.Ark, "minimax": options.Keys.MiniMax}
	if options.Mode == ModeMock {
		if options.Keys.Ark != "" || options.Keys.MiniMax != "" {
			return Config{}, errors.New("mock mode must not receive provider credentials")
		}
		providerKeys["volcengine"], providerKeys["minimax"] = MockArkProviderKey, MockMiniMaxProviderKey
	}
	providers := make(map[string]bool)
	for _, model := range models {
		providers[model.ProviderName] = true
	}
	for _, provider := range []string{"volcengine", "minimax"} {
		if options.Mode == ModeLive && (providers[provider] || providerKeys[provider] != "") && !validLiveProviderKey(providerKeys[provider]) {
			return Config{}, fmt.Errorf("live %s requires a non-placeholder server-loaded provider key", provider)
		}
	}
	options.ModelIDs = append([]string(nil), options.ModelIDs...)
	options.RuntimeSeed = append([]byte(nil), options.RuntimeSeed...)
	options.Now = options.Now.UTC().Truncate(time.Second)
	miniMaxURL := MiniMaxChinaBaseURL
	if options.MiniMaxRegion == "global" {
		miniMaxURL = MiniMaxGlobalBaseURL
	}
	fingerprints := make(map[string]string)
	for provider := range providers {
		fingerprints[provider] = sha256Hex([]byte(providerKeys[provider]))
	}
	stateInput, err := common.Marshal(struct {
		Version     int               `json:"version"`
		Mode        string            `json:"mode"`
		Namespace   string            `json:"namespace"`
		CreatedAt   string            `json:"created_at"`
		CreatedBy   string            `json:"created_by"`
		Reason      string            `json:"reason"`
		CallbackURL string            `json:"callback_url"`
		MiniMaxURL  string            `json:"minimax_url"`
		SeedSHA256  string            `json:"seed_sha256"`
		Keys        map[string]string `json:"key_fingerprints"`
		Models      []Model           `json:"models"`
	}{1, options.Mode, options.Namespace, options.Now.Format(time.RFC3339), options.CreatedBy, options.Reason,
		options.CallbackURL, miniMaxURL, sha256Hex(options.RuntimeSeed), fingerprints, models})
	if err != nil {
		return Config{}, errors.New("local video state identity cannot be encoded")
	}
	stateDigest := sha256Hex(stateInput)
	config := Config{SchemaVersion: 1, Mode: options.Mode, Environment: EnvironmentDevelopment,
		StateID: options.Mode + "-" + stateDigest, Status: "mock_local_only", Models: models,
		Channels: make([]Channel, 0, len(providers)), Routes: make(map[string][]Route), options: options}
	if options.Mode == ModeLive {
		config.Status = "live_provider_acceptance_required"
	}
	for _, provider := range []string{"volcengine", "minimax"} {
		if !providers[provider] {
			continue
		}
		providerName := provider
		if options.Mode == ModeMock {
			providerName = "mock-" + provider
		}
		identity := sha256.Sum256([]byte(config.StateID + ":channel:" + provider))
		channelID := int(binary.BigEndian.Uint32(identity[:4])%500000000)*2 + 1
		channelType, baseURL := constant.ChannelTypeVolcEngine, ArkBaseURL
		if provider == "minimax" {
			channelID++
			channelType, baseURL = constant.ChannelTypeMiniMax, miniMaxURL
		}
		channel := Channel{ID: channelID, Type: channelType, Name: "Local video " + options.Mode + " " + provider,
			ProviderName: providerName, AccountID: "local-" + options.Mode + "-" + provider + "-" + stateDigest[:24],
			BaseURL: baseURL, ModelIDs: []string{}, ProviderModelIDs: []string{}, KeyFingerprint: fingerprints[provider], Key: providerKeys[provider]}
		for index := range config.Models {
			model := &config.Models[index]
			if model.ProviderName != provider {
				continue
			}
			model.Release = generationrelease.Release{
				APIVersion: generationrelease.APIVersion, Kind: generationrelease.Kind,
				ReleaseID:     "local-" + options.Mode + "-" + model.PublicModelID + "-" + stateDigest[:24],
				PublicModelID: model.PublicModelID, ProviderModelID: model.ProviderModelID,
				AdapterProfileID: model.Profile.ID, AdapterProfileRevision: model.Profile.Revision,
				Capability: generationprofile.NormalizeCapability(model.Capability),
				Audit: generationrelease.Audit{CreatedAt: options.Now.Format(time.RFC3339), CreatedBy: options.CreatedBy,
					Reason: options.Reason, SourceRef: model.CatalogSource},
			}
			if err := model.Release.Validate(model.Profile); err != nil {
				return Config{}, errors.New("local video model release audit or binding is invalid")
			}
			releaseCopy := model.Release
			releaseCopy.Capability = generationprofile.NormalizeCapability(model.Capability)
			config.Routes[model.PublicModelID] = []Route{{
				RouteID: "local-" + options.Mode + "-" + model.PublicModelID + "-" + stateDigest[:24], ProviderName: providerName,
				AccountID: channel.AccountID, ChannelID: channel.ID, NativeChannelType: channel.Type,
				KeyIndex: 0, KeyFingerprint: channel.KeyFingerprint, ChannelClass: "official", UpstreamModel: model.ProviderModelID,
				RPMLimit: TestRPMLimit, ActiveTaskLimit: TestActiveTaskLimit,
				Capabilities: generationprofile.NormalizeCapability(model.Capability), CapabilityProfile: model.Profile.ID, ModelRelease: &releaseCopy,
			}}
			channel.ModelIDs = append(channel.ModelIDs, model.PublicModelID)
			channel.ProviderModelIDs = append(channel.ProviderModelIDs, model.ProviderModelID)
		}
		config.Channels = append(config.Channels, channel)
	}
	derive := func(label string) []byte {
		mac := hmac.New(sha256.New, options.RuntimeSeed)
		_, _ = mac.Write([]byte("local-video-lab:v1:" + config.StateID + ":" + label))
		return mac.Sum(nil)
	}
	keyID := "local-video-" + stateDigest[:12]
	keyring, err := common.Marshal(struct {
		SchemaVersion int               `json:"schema_version"`
		ActiveKeyID   string            `json:"active_key_id"`
		Keys          map[string]string `json:"keys"`
	}{1, keyID, map[string]string{keyID: base64.StdEncoding.EncodeToString(derive("provider-keyring"))}})
	if err != nil {
		return Config{}, errors.New("local video credential keyring cannot be encoded")
	}
	config.Principal = Principal{
		ClientID: "local-video-lab", TenantID: uuid.NewSHA1(uuid.NameSpaceURL, []byte("local-video-lab:"+config.StateID)).String(),
		UserName: "local-video-" + stateDigest[:16], CallbackURL: options.CallbackURL,
		APIKey: hex.EncodeToString(derive("platform-api-key")), UpstreamToken: "sk-" + hex.EncodeToString(derive("native-token"))[:48],
		InternalAdmissionToken: hex.EncodeToString(derive("internal-admission")), OperationsToken: hex.EncodeToString(derive("operations")),
		CallbackSigningSecret: hex.EncodeToString(derive("callback")), ArtifactSigningSecret: hex.EncodeToString(derive("artifact")),
		CredentialKeyringJSON: string(keyring), ReconciliationSecret: hex.EncodeToString(derive("reconciliation")),
	}
	return config, nil
}

func availableModels(now time.Time) ([]Model, error) {
	ark, err := generationprofile.SeedanceModelCatalog()
	if err != nil {
		return nil, errors.New("compiled Seedance catalog is unavailable")
	}
	miniMax, err := generationprofile.MiniMaxH3ModelCatalog()
	if err != nil {
		return nil, errors.New("compiled MiniMax H3 catalog is unavailable")
	}
	models := make([]Model, 0, len(ark)+len(miniMax))
	for _, manifest := range ark {
		if !manifest.NewRoutesAllowed || manifest.ValidateSubmissionAt(now) != nil {
			continue
		}
		models = append(models, Model{PublicModelID: manifest.PublicModelID, ProviderModelID: manifest.ProviderModelID,
			DisplayName: manifest.DisplayName, ProviderName: "volcengine", CatalogSource: "generationprofile/seedance_models.v1.json",
			OfficialSources: append([]string(nil), manifest.OfficialSources...), CompatibilityNotes: append([]string(nil), manifest.Notes...),
			Profile: generationprofile.Profile{ID: manifest.AdapterProfileID}, Capability: manifest.CompatibleCapability()})
	}
	for _, manifest := range miniMax {
		if !manifest.NewRoutesAllowed {
			continue
		}
		models = append(models, Model{PublicModelID: manifest.PublicModelID, ProviderModelID: manifest.ProviderModelID,
			DisplayName: manifest.DisplayName, ProviderName: "minimax", CatalogSource: "generationprofile/minimax_h3_models.v1.json",
			OfficialSources: append([]string(nil), manifest.OfficialSources...), CompatibilityNotes: append([]string(nil), manifest.Notes...),
			Profile: generationprofile.Profile{ID: manifest.AdapterProfileID}, Capability: manifest.CompatibleCapability()})
	}
	for index := range models {
		model := &models[index]
		profile, found, err := generationprofile.Resolve(model.Profile.ID, map[string]int{"volcengine": constant.ChannelTypeVolcEngine, "minimax": constant.ChannelTypeMiniMax}[model.ProviderName], model.ProviderModelID)
		if err != nil || !found || profile.ValidateNarrowing(model.Capability) != nil {
			return nil, errors.New("compiled local video model/profile binding is invalid")
		}
		model.Profile = profile
		model.Capability = generationprofile.NormalizeCapability(model.Capability)
		if len(model.CompatibilityNotes) == 0 {
			model.CompatibilityNotes = []string{"Only the compiled immutable capability subset is exposed; provider features absent from that contract remain unavailable."}
		}
		model.Modes = make([]string, 0, len(model.Capability.Modes))
		for mode := range model.Capability.Modes {
			model.Modes = append(model.Modes, mode)
		}
		sort.Strings(model.Modes)
		revision, err := (generationrelease.Release{Capability: model.Capability}).CapabilityRevision()
		if err != nil {
			return nil, errors.New("compiled local video capability revision is invalid")
		}
		model.ReleaseCapabilityRevision = revision
		model.CapabilityRevision, err = publicCapabilityRevision(model.Capability)
		if err != nil {
			return nil, errors.New("compiled local video public capability revision is invalid")
		}
	}
	sort.Slice(models, func(i, j int) bool { return models[i].PublicModelID < models[j].PublicModelID })
	return models, nil
}

func validLiveProviderKey(key string) bool {
	if len(key) < 16 || len(key) > 16384 || !utf8.ValidString(key) || strings.TrimSpace(key) != key {
		return false
	}
	for _, r := range key {
		if unicode.IsControl(r) || unicode.IsSpace(r) {
			return false
		}
	}
	lower := strings.ToLower(key)
	return !strings.HasPrefix(lower, "local-video-lab-mock-") && !strings.Contains(lower, "change-me") &&
		!strings.Contains(lower, "changeme") && !strings.Contains(lower, "replace-with") && !strings.Contains(lower, "placeholder")
}

func validateCallbackURL(raw string, isolatedDocker bool) error {
	parsed, err := url.Parse(raw)
	if err != nil || len(raw) > 2048 || raw != strings.TrimSpace(raw) || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" || parsed.RawQuery != "" || parsed.ForceQuery {
		return errors.New("local video callback URL is invalid")
	}
	if parsed.Scheme == "https" {
		return nil
	}
	if isolatedDocker && raw == "http://platform-lab:8000/internal/relay-callbacks/new-api-v1" {
		return nil
	}
	address := net.ParseIP(parsed.Hostname())
	if parsed.Scheme == "http" && (parsed.Hostname() == "localhost" || (address != nil && address.IsLoopback())) {
		return nil
	}
	return errors.New("local video callback requires HTTPS, loopback HTTP, or the explicit isolated Docker callback")
}

func sha256Hex(value []byte) string {
	digest := sha256.Sum256(value)
	return hex.EncodeToString(digest[:])
}

// Public discovery follows Relay's frozen Python-compatible canonical JSON
// contract (recursive key ordering), not generationrelease's internal struct
// revision. The integration test checks against the actual Relay parser;
// keeping this pure avoids importing runtime service initialization here.
func publicCapabilityRevision(capability dto.PlatformGenerationCapabilities) (string, error) {
	encoded, err := common.Marshal(capability)
	if err != nil {
		return "", err
	}
	var canonical any
	if err := common.Unmarshal(encoded, &canonical); err != nil {
		return "", err
	}
	encoded, err = common.Marshal(canonical)
	if err != nil {
		return "", err
	}
	return "sha256:" + sha256Hex(encoded), nil
}

// Validate detects changes to a prepared configuration, including changes that
// remain within an adapter ceiling but no longer match its compiled model.
// Only Build-produced configurations are valid; public JSON is not authority.
func Validate(config Config) error {
	expected, err := Build(config.options)
	if err != nil {
		return errors.New("local video configuration is not a valid prepared configuration")
	}
	actualJSON, err := common.Marshal(config)
	if err != nil {
		return errors.New("local video configuration is invalid")
	}
	expectedJSON, err := common.Marshal(expected)
	if err != nil || string(actualJSON) != string(expectedJSON) {
		return errors.New("local video configuration differs from its exact prepared binding")
	}
	for index := range config.Channels {
		if subtle.ConstantTimeCompare([]byte(config.Channels[index].Key), []byte(expected.Channels[index].Key)) != 1 {
			return errors.New("local video provider credential differs from its prepared binding")
		}
	}
	if config.Principal != expected.Principal {
		return errors.New("local video principal differs from its prepared binding")
	}
	return nil
}
