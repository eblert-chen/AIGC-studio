package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/go-redis/redis/v8"
	"github.com/google/uuid"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

const upstreamRevision = "0ab02020603d22e5613bc4cf46bfab06f8567769"
const faultForgedVideoPayload = "synthetic-video-payload"

var requiredAssertions = map[string][]string{
	"provider_post_pre_disconnect":        {"provider_post_not_attempted", "job_retry_safe", "reservation_action_valid"},
	"provider_post_response_loss":         {"single_provider_task", "reconciliation_required", "route_unchanged", "account_slot_retained", "automatic_resubmit_absent"},
	"unknown_reconciliation_created":      {"reconciliation_required_observed", "single_provider_task", "original_route_resumed", "account_slot_retained_until_terminal", "cross_channel_retry_absent"},
	"unknown_reconciliation_not_created":  {"reconciliation_required_observed", "proven_non_creation_recorded", "cross_channel_retry_absent", "account_slot_released", "reservation_action_valid"},
	"db_commit_failure":                   {"external_side_effect_not_duplicated", "state_recoverable", "fencing_preserved"},
	"worker_kill":                         {"lease_recovered", "duplicate_provider_task_absent", "terminal_state_not_regressed"},
	"lease_expiry":                        {"new_owner_acquired", "old_owner_fenced"},
	"stale_token_late_write":              {"late_write_rejected", "terminal_state_not_regressed"},
	"redis_outage":                        {"job_durable_in_postgres", "provider_retry_budget_not_consumed", "recovery_completed"},
	"db_jitter":                           {"database_clock_used", "duplicate_claim_absent", "recovery_completed"},
	"account_pool_cross_mode_shared":      {"single_physical_account_state", "rpm_shared_across_modes", "active_capacity_shared_across_modes", "cooldown_shared_across_modes", "existing_task_polling_unchanged"},
	"poll_timeout":                        {"route_unchanged", "provider_retry_budget_not_consumed", "terminal_state_not_falsified"},
	"artifact_corruption":                 {"artifact_rejected", "success_not_published", "provider_url_not_exposed"},
	"artifact_oversize":                   {"artifact_rejected", "success_not_published", "provider_url_not_exposed"},
	"artifact_mime_mismatch":              {"artifact_rejected", "success_not_published", "provider_url_not_exposed"},
	"artifact_ssrf":                       {"private_or_disallowed_target_blocked", "network_fetch_not_attempted", "success_not_published"},
	"callback_retry_dlq":                  {"signature_verified", "at_least_once_retry_observed", "dead_letter_observed", "single_item_claimed", "stale_delivery_token_fenced"},
	"provider_success_rate_drop_recovery": {"database_clock_lease_used", "stale_monitor_token_fenced", "drop_transition_deduplicated", "recovery_transition_deduplicated", "signed_alert_delivery_observed"},
	"provider_widespread_route_failure":   {"widespread_trigger_deduplicated", "missing_probe_not_recovery", "signed_alert_delivery_observed"},
	"provider_batch_account_invalidation": {"provider_caused_only", "batch_trigger_deduplicated", "signed_alert_delivery_observed"},
	"provider_alert_retry_dlq":            {"at_least_once_retry_observed", "dead_letter_observed", "single_item_claimed", "stale_delivery_token_fenced", "readiness_backlog_reported"},
}

type config struct {
	Port               string
	DatabaseURL        string
	RedisURL           string
	ControlToken       string
	CandidateBaseURL   string
	CandidateDigest    string
	CandidateSource    string
	CandidateUpstream  string
	LiveProviderKey    string
	LiveNativeToken    string
	LiveAdmissionToken string
}

type candidateIdentity struct {
	InstanceID          string `json:"instance_id"`
	UpstreamGitRevision string `json:"upstream_git_revision"`
	SourceGitRevision   string `json:"source_git_revision"`
	ImageDigest         string `json:"image_digest"`
}

type startRequest struct {
	SchemaVersion  int               `json:"schema_version"`
	Scenario       string            `json:"scenario"`
	Target         string            `json:"target"`
	Model          string            `json:"model"`
	Mode           string            `json:"mode"`
	RunNonce       string            `json:"run_nonce"`
	RequestedAtUTC string            `json:"requested_at_utc"`
	Candidate      candidateIdentity `json:"candidate"`
}

type startResponse struct {
	SchemaVersion int               `json:"schema_version"`
	RunID         string            `json:"run_id"`
	RunNonce      string            `json:"run_nonce"`
	Scenario      string            `json:"scenario"`
	Target        string            `json:"target"`
	Model         string            `json:"model"`
	Mode          string            `json:"mode"`
	Candidate     candidateIdentity `json:"candidate"`
	AcceptedAtUTC string            `json:"accepted_at_utc"`
}

type rawEvidence struct {
	ID            string         `json:"id"`
	ObservedAtUTC string         `json:"observed_at_utc"`
	Kind          string         `json:"kind"`
	Action        string         `json:"action"`
	Data          map[string]any `json:"data"`
	SHA256        string         `json:"sha256"`
}

type runResult struct {
	SchemaVersion        int                 `json:"schema_version"`
	RunID                string              `json:"run_id"`
	RunNonce             string              `json:"run_nonce"`
	Scenario             string              `json:"scenario"`
	Target               string              `json:"target"`
	Model                string              `json:"model"`
	Mode                 string              `json:"mode"`
	Candidate            candidateIdentity   `json:"candidate"`
	Status               string              `json:"status"`
	RequestReceivedAtUTC string              `json:"request_received_at_utc"`
	StartedAtUTC         string              `json:"started_at_utc"`
	CompletedAtUTC       string              `json:"completed_at_utc,omitempty"`
	Assertions           map[string]bool     `json:"assertions,omitempty"`
	AssertionEvidence    map[string][]string `json:"assertion_evidence,omitempty"`
	RawEvidence          []rawEvidence       `json:"raw_evidence,omitempty"`
	FailureReason        string              `json:"failure_reason,omitempty"`
	ExecutionScope       string              `json:"execution_scope"`
	TargetScope          string              `json:"target_scope"`
	LiveCandidateFaulted bool                `json:"live_candidate_process_faulted"`
}

type faultProviderEffect struct {
	ID             string    `gorm:"type:varchar(36);primaryKey"`
	JobID          string    `gorm:"type:varchar(36);not null;uniqueIndex"`
	ProviderTaskID string    `gorm:"type:varchar(80);not null"`
	CreatedAt      time.Time `gorm:"not null"`
}

type evidenceBuilder struct {
	entries []rawEvidence
	next    int
}

type server struct {
	config          config
	db              *gorm.DB
	redis           *redis.Client
	mu              sync.Mutex
	runs            map[string]*runResult
	execution       chan struct{}
	bridge          *bridgeController
	liveRuns        map[string]*liveRunResult
	artifactFetches atomic.Int64
}

var liveRequiredAssertions = map[string][]string{
	"live_redis_outage":           {"api_accepted_during_redis_outage", "postgres_outbox_durable", "recovery_submitted_once"},
	"live_provider_response_loss": {"provider_side_effect_observed", "reconciliation_required", "sticky_route_and_slot_retained", "automatic_resubmit_absent"},
	"live_candidate_worker_kill":  {"provider_side_effect_observed", "candidate_process_replaced", "lease_recovered_without_resubmit", "old_worker_token_fenced"},
}

type bridgeEffect struct {
	JobID           string `json:"job_id"`
	RouteID         int64  `json:"route_id"`
	WorkerToken     string `json:"worker_token"`
	SubmissionToken string `json:"submission_token"`
	ProviderTaskID  string `json:"provider_task_id"`
	ObservedAtUTC   string `json:"observed_at_utc"`
}

type bridgeController struct {
	mu      sync.Mutex
	mode    string
	effects map[string][]bridgeEffect
}

type liveRunResult struct {
	SchemaVersion        int                 `json:"schema_version"`
	RunID                string              `json:"run_id"`
	RunNonce             string              `json:"run_nonce"`
	Scenario             string              `json:"scenario"`
	Status               string              `json:"status"`
	ExecutionScope       string              `json:"execution_scope"`
	Candidate            candidateIdentity   `json:"candidate"`
	JobID                string              `json:"job_id,omitempty"`
	RequestReceivedAtUTC string              `json:"request_received_at_utc"`
	StartedAtUTC         string              `json:"started_at_utc"`
	CompletedAtUTC       string              `json:"completed_at_utc,omitempty"`
	Assertions           map[string]bool     `json:"assertions,omitempty"`
	AssertionEvidence    map[string][]string `json:"assertion_evidence,omitempty"`
	RawEvidence          []rawEvidence       `json:"raw_evidence,omitempty"`
	FailureReason        string              `json:"failure_reason,omitempty"`
	resumeAfterKill      chan struct{}       `json:"-"`
}

var evidenceClock struct {
	sync.Mutex
	last time.Time
}

func utcNow() string {
	now := time.Now().UTC()
	evidenceClock.Lock()
	if !now.After(evidenceClock.last) {
		now = evidenceClock.last.Add(time.Nanosecond)
	}
	evidenceClock.last = now
	evidenceClock.Unlock()
	return now.Format(time.RFC3339Nano)
}

func digestText(value string) string {
	sum := sha256.Sum256([]byte(value))
	return "sha256:" + hex.EncodeToString(sum[:])
}

func canonicalJSON(value any) ([]byte, error) {
	serialized, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var normalized any
	if err := json.Unmarshal(serialized, &normalized); err != nil {
		return nil, err
	}
	var write func(*bytes.Buffer, any) error
	marshalPrimitive := func(current any) ([]byte, error) {
		encoded, err := json.Marshal(current)
		if err != nil {
			return nil, err
		}
		encoded = bytes.ReplaceAll(encoded, []byte(`\u003c`), []byte("<"))
		encoded = bytes.ReplaceAll(encoded, []byte(`\u003e`), []byte(">"))
		encoded = bytes.ReplaceAll(encoded, []byte(`\u0026`), []byte("&"))
		return encoded, nil
	}
	write = func(buffer *bytes.Buffer, current any) error {
		switch typed := current.(type) {
		case map[string]any:
			keys := make([]string, 0, len(typed))
			for key := range typed {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			buffer.WriteByte('{')
			for index, key := range keys {
				if index > 0 {
					buffer.WriteByte(',')
				}
				encodedKey, _ := marshalPrimitive(key)
				buffer.Write(encodedKey)
				buffer.WriteByte(':')
				if err := write(buffer, typed[key]); err != nil {
					return err
				}
			}
			buffer.WriteByte('}')
		case []any:
			buffer.WriteByte('[')
			for index, item := range typed {
				if index > 0 {
					buffer.WriteByte(',')
				}
				if err := write(buffer, item); err != nil {
					return err
				}
			}
			buffer.WriteByte(']')
		default:
			encoded, err := marshalPrimitive(typed)
			if err != nil {
				return err
			}
			buffer.Write(encoded)
		}
		return nil
	}
	buffer := &bytes.Buffer{}
	if err := write(buffer, normalized); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}

func (builder *evidenceBuilder) add(kind, action string, data map[string]any) string {
	builder.next++
	entry := rawEvidence{
		ID: fmt.Sprintf("evidence-%03d", builder.next), ObservedAtUTC: utcNow(),
		Kind: kind, Action: action, Data: data,
	}
	core := map[string]any{
		"id": entry.ID, "observed_at_utc": entry.ObservedAtUTC,
		"kind": entry.Kind, "action": entry.Action, "data": entry.Data,
	}
	canonical, err := canonicalJSON(core)
	if err != nil {
		panic(err)
	}
	entry.SHA256 = digestText(string(canonical))
	builder.entries = append(builder.entries, entry)
	return entry.ID
}

func loadConfig() (config, error) {
	loaded := config{
		Port: os.Getenv("PORT"), DatabaseURL: os.Getenv("DATABASE_URL"), RedisURL: os.Getenv("REDIS_URL"),
		ControlToken: os.Getenv("CONTROL_TOKEN"), CandidateBaseURL: os.Getenv("CANDIDATE_BASE_URL"),
		CandidateDigest: os.Getenv("CANDIDATE_IMAGE_DIGEST"),
		CandidateSource: os.Getenv("CANDIDATE_SOURCE_REVISION"), CandidateUpstream: os.Getenv("CANDIDATE_UPSTREAM_REVISION"),
		LiveProviderKey: os.Getenv("LIVE_PROVIDER_KEY"), LiveNativeToken: os.Getenv("LIVE_NATIVE_TOKEN"),
		LiveAdmissionToken: os.Getenv("LIVE_INTERNAL_ADMISSION_TOKEN"),
	}
	if loaded.Port == "" {
		loaded.Port = "8080"
	}
	if loaded.LiveProviderKey == "" {
		loaded.LiveProviderKey = "relay-live-provider-key"
	}
	if loaded.LiveNativeToken == "" {
		loaded.LiveNativeToken = "relay-live-native-token"
	}
	if loaded.LiveAdmissionToken == "" {
		loaded.LiveAdmissionToken = "relay-live-internal-admission-token"
	}
	for name, value := range map[string]string{
		"DATABASE_URL": loaded.DatabaseURL, "REDIS_URL": loaded.RedisURL, "CONTROL_TOKEN": loaded.ControlToken,
		"CANDIDATE_BASE_URL":     loaded.CandidateBaseURL,
		"CANDIDATE_IMAGE_DIGEST": loaded.CandidateDigest, "CANDIDATE_SOURCE_REVISION": loaded.CandidateSource,
		"CANDIDATE_UPSTREAM_REVISION": loaded.CandidateUpstream,
	} {
		if strings.TrimSpace(value) == "" {
			return config{}, fmt.Errorf("%s is required", name)
		}
	}
	if loaded.CandidateUpstream != upstreamRevision {
		return config{}, errors.New("candidate upstream revision mismatch")
	}
	return loaded, nil
}

func openDatabase(dsn string) (*gorm.DB, error) {
	database, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	if err != nil {
		return nil, err
	}
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	model.DB = database
	sqlDB, err := database.DB()
	if err != nil {
		return nil, err
	}
	sqlDB.SetMaxOpenConns(24)
	sqlDB.SetMaxIdleConns(8)
	return database, nil
}

func openRedis(rawURL string) (*redis.Client, error) {
	options, err := redis.ParseURL(rawURL)
	if err != nil {
		return nil, err
	}
	options.MaxRetries = -1
	options.DialTimeout = 500 * time.Millisecond
	options.ReadTimeout = 300 * time.Millisecond
	options.WriteTimeout = 300 * time.Millisecond
	client := redis.NewClient(options)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := client.Ping(ctx).Err(); err != nil {
		return nil, err
	}
	return client, nil
}

func candidateFor(config config) candidateIdentity {
	return candidateIdentity{
		UpstreamGitRevision: config.CandidateUpstream,
		SourceGitRevision:   config.CandidateSource, ImageDigest: config.CandidateDigest,
	}
}

func createGenerationJob(database *gorm.DB, label string) (model.PlatformGenerationJob, model.PlatformGenerationOutbox, error) {
	return createGenerationJobForModel(database, label, "fault.video.v1", "text_to_video", `{}`)
}

func createGenerationJobForModel(database *gorm.DB, label string, modelName string, mode string, requestJSON string) (model.PlatformGenerationJob, model.PlatformGenerationOutbox, error) {
	revision := "sha256:" + strings.Repeat("a", 64)
	job := model.PlatformGenerationJob{
		ID: uuid.NewString(), TenantID: uuid.NewString(), SourceClientID: "fault-platform",
		RequestID: "fault-" + label, IdempotencyKey: "fault-" + label + "-" + uuid.NewString(),
		RequestHash: strings.Repeat("b", 64), RequestJSON: requestJSON, Model: modelName, Mode: mode,
		ExpectedCapabilityRevision: revision, CapabilityRevision: revision,
		Status: model.PlatformGenerationStatusQueued, Progress: 0, OutputsJSON: "[]", ErrorDetailsJSON: "{}",
	}
	_, replayed, conflict, err := model.CreatePlatformGenerationJob(&job)
	if err != nil || replayed || conflict {
		return job, model.PlatformGenerationOutbox{}, fmt.Errorf("create generation job: replayed=%t conflict=%t: %w", replayed, conflict, err)
	}
	var outbox model.PlatformGenerationOutbox
	if err := database.Where("job_id = ? AND topic = ?", job.ID, "generation.submit").First(&outbox).Error; err != nil {
		return job, outbox, err
	}
	return job, outbox, nil
}

func processingUpdates(progress int) map[string]any {
	return map[string]any{"status": model.PlatformGenerationStatusProcessing, "progress": progress, "next_poll_at": time.Now().UTC()}
}

var faultChannelSequence atomic.Int64

func (server *server) createFaultProviderRoute(label string, modelName string, mode string, rpmLimit int, activeLimit int) (*model.PlatformGenerationProviderRoute, error) {
	channelID := int(92000 + faultChannelSequence.Add(1))
	key := "fault-provider-key-" + label + "-" + uuid.NewString()
	channel := model.Channel{
		Id: channelID, Type: 1, Key: key, Status: common.ChannelStatusEnabled,
		Name:        "fault-channel-" + label + "-" + strconv.Itoa(channelID),
		CreatedTime: time.Now().UTC().Unix(), Models: modelName, Group: "default",
		ChannelInfo: model.ChannelInfo{IsMultiKey: false, MultiKeySize: 1},
	}
	if err := server.db.Create(&channel).Error; err != nil {
		return nil, err
	}
	route := &model.PlatformGenerationProviderRoute{
		RouteKey: "fault-route-" + label + "-" + uuid.NewString(), Model: modelName, Mode: mode,
		ProviderName: "fault-provider-" + label, AccountID: "fault-account-" + label,
		ChannelID: channelID, KeyIndex: 0,
		KeyFingerprint: fmt.Sprintf("%x", common.Sha256Raw([]byte(key))),
		ChannelClass:   model.PlatformGenerationChannelClassOfficialProvider,
		UpstreamModel:  modelName, ProductionReady: true, Enabled: true,
		RPMWindowSeconds: 60, RPMLimit: rpmLimit, ActiveLimit: activeLimit,
	}
	if err := model.CreatePlatformGenerationProviderRoute(route); err != nil {
		return nil, err
	}
	return route, nil
}

func (server *server) createFaultRouteForSameAccount(base *model.PlatformGenerationProviderRoute, label string, modelName string, mode string) (*model.PlatformGenerationProviderRoute, error) {
	route := &model.PlatformGenerationProviderRoute{
		RouteKey: "fault-route-" + label + "-" + uuid.NewString(), Model: modelName, Mode: mode,
		ProviderName: base.ProviderName, AccountID: base.AccountID,
		ChannelID: base.ChannelID, KeyIndex: base.KeyIndex, KeyFingerprint: base.KeyFingerprint,
		ChannelClass: base.ChannelClass, UpstreamModel: modelName,
		ProductionReady: true, Enabled: true,
		RPMWindowSeconds: base.RPMWindowSeconds, RPMLimit: base.RPMLimit, ActiveLimit: base.ActiveLimit,
	}
	if err := model.CreatePlatformGenerationProviderRoute(route); err != nil {
		return nil, err
	}
	return route, nil
}

type recordingArtifactStore struct{ puts atomic.Int64 }

func (*recordingArtifactStore) Kind() string      { return "fault_recording_store" }
func (*recordingArtifactStore) BindingID() string { return "fault-recording-binding" }
func (*recordingArtifactStore) Persistent() bool  { return true }
func (store *recordingArtifactStore) Put(_ context.Context, input service.PlatformArtifactPutInput) (service.PlatformStoredArtifact, error) {
	store.puts.Add(1)
	return service.PlatformStoredArtifact{ObjectKey: input.ObjectKey, ContentType: input.ContentType, SizeBytes: input.SizeBytes, SHA256: input.SHA256}, nil
}
func (*recordingArtifactStore) Delete(context.Context, string) error { return nil }
func (*recordingArtifactStore) SignedDownloadURL(context.Context, string, time.Duration) (string, error) {
	return "", errors.New("fault recording store never issues URLs")
}
func (*recordingArtifactStore) IssueSignedDownload(context.Context, string, time.Duration) (service.PlatformIssuedArtifactDownload, error) {
	return service.PlatformIssuedArtifactDownload{}, errors.New("fault recording store never issues downloads")
}
func (*recordingArtifactStore) Healthcheck(context.Context) error { return nil }

func (server *server) handleArtifactOrigin(writer http.ResponseWriter, request *http.Request) {
	server.artifactFetches.Add(1)
	switch request.URL.Query().Get("case") {
	case "mime_mismatch":
		writer.Header().Set("Content-Type", "image/png")
		_, _ = writer.Write(faultPNGFixture())
	case "oversize":
		writer.Header().Set("Content-Type", "video/mp4")
		_, _ = writer.Write(bytes.Repeat([]byte("x"), 64))
	default:
		writer.Header().Set("Content-Type", "video/mp4")
		_, _ = writer.Write([]byte(faultForgedVideoPayload))
	}
}

func faultPNGFixture() []byte {
	picture := image.NewNRGBA(image.Rect(0, 0, 1, 1))
	picture.SetNRGBA(0, 0, color.NRGBA{R: 0x42, G: 0x7a, B: 0xff, A: 0xff})
	var payload bytes.Buffer
	if err := png.Encode(&payload, picture); err != nil {
		panic(err)
	}
	return payload.Bytes()
}

func (server *server) authorize(request *http.Request) bool {
	provided := strings.TrimPrefix(request.Header.Get("Authorization"), "Bearer ")
	expectedDigest := sha256.Sum256([]byte(server.config.ControlToken))
	providedDigest := sha256.Sum256([]byte(provided))
	return subtle.ConstantTimeCompare(expectedDigest[:], providedDigest[:]) == 1 && provided != ""
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	data, err := json.Marshal(value)
	if err != nil {
		http.Error(writer, "encode response", http.StatusInternalServerError)
		return
	}
	writer.Header().Set("Content-Type", "application/json")
	writer.Header().Set("Cache-Control", "no-store")
	writer.WriteHeader(status)
	_, _ = writer.Write(data)
}

func decodeStrictJSON(request *http.Request, value any) error {
	decoder := json.NewDecoder(io.LimitReader(request.Body, 64*1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("request must contain one JSON object")
	}
	return nil
}

func sameCandidate(left, right candidateIdentity) bool {
	return left == right
}

func sameCandidateBuild(left, right candidateIdentity) bool {
	return left.UpstreamGitRevision == right.UpstreamGitRevision &&
		left.SourceGitRevision == right.SourceGitRevision &&
		left.ImageDigest == right.ImageDigest
}

func validCandidateInstance(identity candidateIdentity) bool {
	parsed, err := uuid.Parse(identity.InstanceID)
	return err == nil && parsed.Version() == uuid.Version(4)
}

func (bridge *bridgeController) setMode(mode string) {
	bridge.mu.Lock()
	bridge.mode = mode
	bridge.mu.Unlock()
}

func (bridge *bridgeController) effectsFor(jobID string) []bridgeEffect {
	bridge.mu.Lock()
	defer bridge.mu.Unlock()
	return append([]bridgeEffect(nil), bridge.effects[jobID]...)
}

func (server *server) handleNativeBridge(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		writer.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	if request.Header.Get("Authorization") != "Bearer "+server.config.LiveNativeToken ||
		request.Header.Get(constant.HeaderPlatformGenerationInternalAdmission) != server.config.LiveAdmissionToken {
		writeJSON(writer, http.StatusUnauthorized, map[string]any{"error": "invalid native bridge credential"})
		return
	}
	jobID := request.Header.Get(constant.HeaderPlatformGenerationJobID)
	routeID, routeErr := strconv.ParseInt(request.Header.Get(constant.HeaderPlatformGenerationRouteID), 10, 64)
	workerToken := request.Header.Get(constant.HeaderPlatformGenerationWorkerLeaseToken)
	submissionToken := request.Header.Get(constant.HeaderPlatformGenerationSubmissionToken)
	if routeErr != nil {
		writeJSON(writer, http.StatusBadRequest, map[string]any{"error": "invalid route"})
		return
	}
	if _, err := model.BeginPlatformGenerationRouteSubmission(jobID, routeID, workerToken, submissionToken); err != nil {
		writeJSON(writer, http.StatusConflict, map[string]any{"error": "stale native bridge admission"})
		return
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(request.Body, 1<<20))
	effect := bridgeEffect{
		JobID: jobID, RouteID: routeID, WorkerToken: workerToken, SubmissionToken: submissionToken,
		ProviderTaskID: "live-provider-task-" + uuid.NewString(), ObservedAtUTC: utcNow(),
	}
	server.bridge.mu.Lock()
	mode := server.bridge.mode
	server.bridge.effects[jobID] = append(server.bridge.effects[jobID], effect)
	server.bridge.mu.Unlock()
	writer.Header().Set(constant.HeaderPlatformGenerationProviderStarted, "true")
	switch mode {
	case "response_loss":
		if hijacker, ok := writer.(http.Hijacker); ok {
			connection, _, err := hijacker.Hijack()
			if err == nil {
				_ = connection.Close()
				return
			}
		}
		return
	case "block_after_accept":
		<-request.Context().Done()
		return
	default:
		writeJSON(writer, http.StatusAccepted, map[string]any{"accepted": true, "provider_task_id": effect.ProviderTaskID})
	}
}

func (server *server) seedLiveChannel() error {
	channel := model.Channel{
		Id: 91001, Type: 1, Key: server.config.LiveProviderKey,
		Status: common.ChannelStatusEnabled, Name: "relay-live-native-bridge",
		CreatedTime: time.Now().UTC().Unix(), Models: "fault-upstream-video", Group: "default",
		ChannelInfo: model.ChannelInfo{IsMultiKey: false, MultiKeySize: 1},
	}
	return server.db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Where("id = ?", channel.Id).Delete(&model.Channel{}).Error; err != nil {
			return err
		}
		return tx.Create(&channel).Error
	})
}

func (server *server) handleLiveSetup(writer http.ResponseWriter, request *http.Request) {
	if !server.authorize(request) {
		writeJSON(writer, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	if err := server.seedLiveChannel(); err != nil {
		writeJSON(writer, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(writer, http.StatusOK, map[string]any{
		"seeded": true, "channel_id": 91001, "key_fingerprint": strings.TrimPrefix(digestText(server.config.LiveProviderKey), "sha256:"),
	})
}

func (server *server) candidateRequest(method, path string, body any, headers map[string]string) (int, http.Header, []byte, error) {
	var reader io.Reader
	if body != nil {
		serialized, err := json.Marshal(body)
		if err != nil {
			return 0, nil, nil, err
		}
		reader = bytes.NewReader(serialized)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(server.config.CandidateBaseURL, "/")+path, reader)
	if err != nil {
		return 0, nil, nil, err
	}
	request.Header.Set("X-Client-ID", "fault-platform")
	request.Header.Set("X-API-Key", "fault-platform-api-key")
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	for key, value := range headers {
		request.Header.Set(key, value)
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return 0, nil, nil, err
	}
	defer response.Body.Close()
	data, readErr := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	return response.StatusCode, response.Header.Clone(), data, readErr
}

func (server *server) liveCapabilityRevision() (string, error) {
	status, _, body, err := server.candidateRequest(http.MethodGet, "/v1/models", nil, nil)
	if err != nil {
		return "", err
	}
	if status != http.StatusOK {
		return "", fmt.Errorf("candidate model catalog returned HTTP %d: %s", status, string(body))
	}
	var catalog struct {
		Data []struct {
			ID                 string `json:"id"`
			CapabilityRevision string `json:"capability_revision"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &catalog); err != nil {
		return "", err
	}
	for _, entry := range catalog.Data {
		if entry.ID == "fault.video.v1" {
			return entry.CapabilityRevision, nil
		}
	}
	return "", errors.New("live candidate did not publish fault.video.v1")
}

func (server *server) submitLiveGeneration(label string) (string, int, []byte, error) {
	revision, err := server.liveCapabilityRevision()
	if err != nil {
		return "", 0, nil, err
	}
	body := map[string]any{
		"model": "fault.video.v1", "expected_capability_revision": revision, "mode": "text_to_video",
		"inputs": map[string]any{"prompt": "live fault acceptance " + label},
		"output": map[string]any{
			"duration_seconds": 5, "aspect_ratio": "16:9", "resolution": "720p", "count": 1, "face_enabled": false,
		},
		"metadata": map[string]any{"live_fault_scenario": label},
	}
	status, _, responseBody, err := server.candidateRequest(http.MethodPost, "/v1/generations", body, map[string]string{
		"Idempotency-Key": "live-" + label + "-" + uuid.NewString(), "X-Request-ID": "live-" + label,
	})
	if err != nil {
		return "", status, responseBody, err
	}
	var accepted struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(responseBody, &accepted); err != nil {
		return "", status, responseBody, err
	}
	return accepted.ID, status, responseBody, nil
}

func waitUntil(timeout time.Duration, predicate func() (bool, error)) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		ready, err := predicate()
		if err != nil {
			return err
		}
		if ready {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return errors.New("condition timed out")
}

func (server *server) handleLiveStart(writer http.ResponseWriter, request *http.Request) {
	if !server.authorize(request) {
		writeJSON(writer, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	var body struct {
		SchemaVersion int               `json:"schema_version"`
		Scenario      string            `json:"scenario"`
		RunNonce      string            `json:"run_nonce"`
		Candidate     candidateIdentity `json:"candidate"`
	}
	if err := decodeStrictJSON(request, &body); err != nil {
		writeJSON(writer, http.StatusBadRequest, map[string]any{"error": "invalid request"})
		return
	}
	if body.SchemaVersion != 1 || !validCandidateInstance(body.Candidate) || !sameCandidateBuild(body.Candidate, candidateFor(server.config)) {
		writeJSON(writer, http.StatusUnprocessableEntity, map[string]any{"error": "candidate binding rejected"})
		return
	}
	if _, err := uuid.Parse(body.RunNonce); err != nil {
		writeJSON(writer, http.StatusUnprocessableEntity, map[string]any{"error": "invalid nonce"})
		return
	}
	if _, ok := liveRequiredAssertions[body.Scenario]; !ok {
		writeJSON(writer, http.StatusUnprocessableEntity, map[string]any{"error": "unsupported live scenario"})
		return
	}
	select {
	case server.execution <- struct{}{}:
	default:
		writeJSON(writer, http.StatusConflict, map[string]any{"error": "another fault run is active"})
		return
	}
	now := utcNow()
	run := &liveRunResult{
		SchemaVersion: 1, RunID: uuid.NewString(), RunNonce: body.RunNonce, Scenario: body.Scenario,
		Status: "RUNNING", ExecutionScope: "live_candidate_api_and_generation_worker",
		Candidate: body.Candidate, RequestReceivedAtUTC: now, StartedAtUTC: now,
		resumeAfterKill: make(chan struct{}, 1),
	}
	server.mu.Lock()
	server.liveRuns[run.RunID] = run
	server.mu.Unlock()
	go func() { defer func() { <-server.execution }(); server.executeLive(run) }()
	writeJSON(writer, http.StatusAccepted, map[string]any{
		"schema_version": 1, "run_id": run.RunID, "run_nonce": run.RunNonce,
		"scenario": run.Scenario, "candidate": run.Candidate, "accepted_at_utc": now,
	})
}

func (server *server) handleLiveResult(writer http.ResponseWriter, request *http.Request, runID string) {
	if !server.authorize(request) {
		writeJSON(writer, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	server.mu.Lock()
	run, ok := server.liveRuns[runID]
	if !ok {
		server.mu.Unlock()
		writeJSON(writer, http.StatusNotFound, map[string]any{"error": "run not found"})
		return
	}
	data, _ := json.Marshal(run)
	server.mu.Unlock()
	copyRun := &liveRunResult{}
	_ = json.Unmarshal(data, copyRun)
	writeJSON(writer, http.StatusOK, copyRun)
}

func (server *server) handleLiveResumeAfterKill(writer http.ResponseWriter, request *http.Request, runID string) {
	if !server.authorize(request) {
		writeJSON(writer, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	server.mu.Lock()
	run, ok := server.liveRuns[runID]
	if !ok || run.Status != "AWAITING_CANDIDATE_KILL" {
		server.mu.Unlock()
		writeJSON(writer, http.StatusConflict, map[string]any{"error": "run is not awaiting a candidate kill"})
		return
	}
	jobID := run.JobID
	resume := run.resumeAfterKill
	server.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	healthRequest, _ := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(server.config.CandidateBaseURL, "/")+"/health/live", nil)
	healthResponse, healthErr := http.DefaultClient.Do(healthRequest)
	cancel()
	if healthResponse != nil {
		_ = healthResponse.Body.Close()
	}
	if healthErr == nil {
		writeJSON(writer, http.StatusConflict, map[string]any{"error": "candidate is still reachable; refusing lease expiry injection"})
		return
	}
	var outbox model.PlatformGenerationOutbox
	if err := server.db.Where("job_id = ?", jobID).First(&outbox).Error; err != nil {
		writeJSON(writer, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	err := server.db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec("UPDATE platform_generation_jobs SET submission_lease_expires_at = clock_timestamp() - interval '1 second' WHERE id = ? AND status = ?", jobID, model.PlatformGenerationStatusSubmitting).Error; err != nil {
			return err
		}
		return tx.Exec("UPDATE platform_generation_outboxes SET claim_expires_at = clock_timestamp() - interval '1 second' WHERE id = ? AND state = ?", outbox.ID, model.PlatformGenerationOutboxClaimed).Error
	})
	if err == nil {
		err = server.redis.FlushDB(context.Background()).Err()
	}
	if err != nil {
		writeJSON(writer, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	select {
	case resume <- struct{}{}:
	default:
	}
	writeJSON(writer, http.StatusOK, map[string]any{
		"expired_with_database_clock": true, "redis_derived_schedule_cleared": true,
		"job_id": jobID, "outbox_id": outbox.ID,
	})
}

func (server *server) executeLive(run *liveRunResult) {
	builder := &evidenceBuilder{}
	assertions := make(map[string]bool)
	mapping := make(map[string][]string)
	for _, name := range liveRequiredAssertions[run.Scenario] {
		assertions[name] = false
		mapping[name] = []string{}
	}
	attestationID, err := server.candidateAttestation(builder, run.Candidate)
	if err == nil {
		switch run.Scenario {
		case "live_redis_outage":
			err = server.liveRedisOutage(run, builder, attestationID, assertions, mapping)
		case "live_provider_response_loss":
			err = server.liveProviderResponseLoss(run, builder, attestationID, assertions, mapping)
		case "live_candidate_worker_kill":
			err = server.liveCandidateWorkerKill(run, builder, attestationID, assertions, mapping)
		}
	}
	status := "PASS"
	for _, value := range assertions {
		if !value {
			status = "FAIL"
			break
		}
	}
	if err != nil {
		status = "FAIL"
		builder.add("execution_error", "live fault scenario failed", map[string]any{"error": err.Error()})
	}
	server.mu.Lock()
	run.Status = status
	run.CompletedAtUTC = utcNow()
	run.Assertions = assertions
	run.AssertionEvidence = mapping
	run.RawEvidence = builder.entries
	if err != nil {
		run.FailureReason = err.Error()
	}
	server.mu.Unlock()
}

func liveBind(assertions map[string]bool, mapping map[string][]string, name string, passed bool, evidenceIDs ...string) {
	assertions[name] = passed
	if passed {
		mapping[name] = append([]string(nil), evidenceIDs...)
	}
}

func (server *server) generationState(jobID string) (model.PlatformGenerationJob, model.PlatformGenerationOutbox, *model.PlatformGenerationRouteAdmission, *model.PlatformGenerationProviderRoute, error) {
	var job model.PlatformGenerationJob
	var outbox model.PlatformGenerationOutbox
	if err := server.db.First(&job, "id = ?", jobID).Error; err != nil {
		return job, outbox, nil, nil, err
	}
	if err := server.db.Where("job_id = ?", jobID).First(&outbox).Error; err != nil {
		return job, outbox, nil, nil, err
	}
	admission, route, err := model.GetPlatformGenerationProviderRouteAssignment(jobID)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return job, outbox, nil, nil, nil
	}
	return job, outbox, admission, route, err
}

func (server *server) liveRedisOutage(run *liveRunResult, builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	server.bridge.setMode("normal_ack")
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	_, err := server.redis.Do(ctx, "CLIENT", "PAUSE", "1500", "ALL").Result()
	cancel()
	if err != nil {
		return err
	}
	jobID, apiStatus, apiBody, err := server.submitLiveGeneration("redis-outage")
	if err != nil {
		return err
	}
	run.JobID = jobID
	job, outbox, _, _, err := server.generationState(jobID)
	if err != nil {
		return err
	}
	acceptedID := builder.add("live_candidate_api", "candidate accepted generation while Redis was paused", map[string]any{
		"http_status": apiStatus, "response_sha256": digestText(string(apiBody)), "job_id": jobID,
		"redis_pause_milliseconds": 1500,
	})
	durableID := builder.add("live_postgres", "accepted job and outbox remained durable during Redis outage", map[string]any{
		"job_status": job.Status, "outbox_state": outbox.State, "outbox_attempts": outbox.Attempts,
		"provider_submission_attempt": job.ProviderSubmissionAttempt,
	})
	if err := waitUntil(15*time.Second, func() (bool, error) {
		var current model.PlatformGenerationJob
		if err := server.db.First(&current, "id = ?", jobID).Error; err != nil {
			return false, err
		}
		return current.Status == model.PlatformGenerationStatusReconciliationRequired && len(server.bridge.effectsFor(jobID)) >= 1, nil
	}); err != nil {
		return err
	}
	time.Sleep(1200 * time.Millisecond)
	finalJob, finalOutbox, admission, route, err := server.generationState(jobID)
	if err != nil {
		return err
	}
	effects := server.bridge.effectsFor(jobID)
	recoveryData := map[string]any{
		"job_id": jobID, "outbox_id": finalOutbox.ID,
		"effect_count": len(effects), "final_job_status": finalJob.Status, "final_outbox_state": finalOutbox.State,
		"provider_submission_attempt": finalJob.ProviderSubmissionAttempt,
		"route_admission_state": func() any {
			if admission == nil {
				return nil
			}
			return admission.State
		}(),
		"route_slot_held": func() any {
			if admission == nil {
				return nil
			}
			return admission.SlotHeld
		}(),
		"route_admission_id": func() any {
			if admission == nil {
				return nil
			}
			return admission.ID
		}(),
		"route_id": func() any {
			if route == nil {
				return nil
			}
			return route.ID
		}(),
	}
	tokenMatches := false
	if len(effects) == 1 && admission != nil {
		recoveryData["worker_lease_token_sha256"] = digestText(effects[0].WorkerToken)
		recoveryData["provider_submission_token_sha256"] = digestText(effects[0].SubmissionToken)
		recoveryData["persisted_submission_token_hash"] = admission.SubmissionTokenHash
		tokenMatches = strings.TrimPrefix(digestText(effects[0].SubmissionToken), "sha256:") == admission.SubmissionTokenHash
		recoveryData["submission_token_hash_matches"] = tokenMatches
	}
	recoveryID := builder.add("live_candidate_worker", "candidate worker recovered Redis schedule and submitted once", recoveryData)
	liveBind(assertions, mapping, "api_accepted_during_redis_outage", apiStatus == http.StatusAccepted && jobID != "", attestationID, acceptedID)
	liveBind(assertions, mapping, "postgres_outbox_durable", job.Status == model.PlatformGenerationStatusQueued && outbox.State == model.PlatformGenerationOutboxPending && job.ProviderSubmissionAttempt == 0, attestationID, durableID)
	liveBind(assertions, mapping, "recovery_submitted_once", len(effects) == 1 && tokenMatches && finalJob.Status == model.PlatformGenerationStatusReconciliationRequired && finalOutbox.State == model.PlatformGenerationOutboxCompleted && finalJob.ProviderSubmissionAttempt == 1, attestationID, recoveryID)
	return nil
}

func (server *server) liveProviderResponseLoss(run *liveRunResult, builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	server.bridge.setMode("response_loss")
	jobID, apiStatus, apiBody, err := server.submitLiveGeneration("provider-response-loss")
	if err != nil {
		return err
	}
	run.JobID = jobID
	if err := waitUntil(15*time.Second, func() (bool, error) {
		var current model.PlatformGenerationJob
		if err := server.db.First(&current, "id = ?", jobID).Error; err != nil {
			return false, err
		}
		return current.Status == model.PlatformGenerationStatusReconciliationRequired && len(server.bridge.effectsFor(jobID)) >= 1, nil
	}); err != nil {
		return err
	}
	job, outbox, admission, route, err := server.generationState(jobID)
	if err != nil {
		return err
	}
	var heldAdmissionCount int64
	if err := server.db.Model(&model.PlatformGenerationRouteAdmission{}).
		Where("route_id = ? AND slot_held = ?", route.ID, true).
		Count(&heldAdmissionCount).Error; err != nil {
		return err
	}
	effectsBefore := server.bridge.effectsFor(jobID)
	tokenMatches := len(effectsBefore) == 1 && strings.TrimPrefix(digestText(effectsBefore[0].SubmissionToken), "sha256:") == admission.SubmissionTokenHash
	stateID := builder.add("live_native_bridge", "native bridge accepted provider side effect then dropped the response", map[string]any{
		"api_status": apiStatus, "api_response_sha256": digestText(string(apiBody)), "job_id": jobID,
		"outbox_id": outbox.ID, "route_admission_id": admission.ID,
		"provider_effect_count": len(effectsBefore), "job_status": job.Status, "outbox_state": outbox.State,
		"route_id": route.ID, "route_admission_state": admission.State, "route_slot_held": admission.SlotHeld,
		"route_active_count": route.ActiveCount, "held_admission_count": heldAdmissionCount,
		"submission_attempt": admission.Attempt, "persisted_submission_token_hash": admission.SubmissionTokenHash,
		"worker_lease_token_sha256": func() any {
			if len(effectsBefore) != 1 {
				return nil
			}
			return digestText(effectsBefore[0].WorkerToken)
		}(),
		"provider_submission_token_sha256": func() any {
			if len(effectsBefore) != 1 {
				return nil
			}
			return digestText(effectsBefore[0].SubmissionToken)
		}(),
		"submission_token_hash_matches": tokenMatches,
	})
	time.Sleep(2200 * time.Millisecond)
	effectsAfter := server.bridge.effectsFor(jobID)
	noRetryID := builder.add("live_candidate_observation", "candidate reconciliation loop did not resubmit unknown work", map[string]any{
		"provider_effect_count_before": len(effectsBefore), "provider_effect_count_after": len(effectsAfter),
		"route_id": route.ID, "channel_id": route.ChannelID,
	})
	liveBind(assertions, mapping, "provider_side_effect_observed", len(effectsBefore) == 1, attestationID, stateID)
	liveBind(assertions, mapping, "reconciliation_required", job.Status == model.PlatformGenerationStatusReconciliationRequired && outbox.State == model.PlatformGenerationOutboxCompleted, attestationID, stateID)
	liveBind(assertions, mapping, "sticky_route_and_slot_retained", tokenMatches && admission.State == model.PlatformGenerationRouteAdmissionUnknown && admission.SlotHeld && heldAdmissionCount >= 1 && int64(route.ActiveCount) == heldAdmissionCount && job.ProviderRouteID == route.ID, attestationID, stateID)
	liveBind(assertions, mapping, "automatic_resubmit_absent", len(effectsBefore) == 1 && len(effectsAfter) == 1, attestationID, noRetryID)
	return nil
}

func (server *server) liveCandidateWorkerKill(run *liveRunResult, builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	server.bridge.setMode("block_after_accept")
	jobID, apiStatus, apiBody, err := server.submitLiveGeneration("candidate-worker-kill")
	if err != nil {
		return err
	}
	run.JobID = jobID
	if err := waitUntil(15*time.Second, func() (bool, error) { return len(server.bridge.effectsFor(jobID)) == 1, nil }); err != nil {
		return err
	}
	effect := server.bridge.effectsFor(jobID)[0]
	acceptedID := builder.add("live_candidate_worker", "candidate worker reached provider side effect and blocked before committing", map[string]any{
		"api_status": apiStatus, "api_response_sha256": digestText(string(apiBody)), "job_id": jobID,
		"provider_task_id": effect.ProviderTaskID, "old_worker_token_sha256": digestText(effect.WorkerToken),
		"route_id": effect.RouteID,
	})
	server.mu.Lock()
	run.Status = "AWAITING_CANDIDATE_KILL"
	run.RawEvidence = append([]rawEvidence(nil), builder.entries...)
	server.mu.Unlock()
	select {
	case <-run.resumeAfterKill:
	case <-time.After(60 * time.Second):
		return errors.New("candidate kill was not confirmed before timeout")
	}
	if err := waitUntil(30*time.Second, func() (bool, error) {
		status, _, _, healthErr := server.candidateRequest(http.MethodGet, "/health/live", nil, nil)
		return healthErr == nil && status == http.StatusOK, nil
	}); err != nil {
		return fmt.Errorf("replacement candidate did not become live: %w", err)
	}
	if err := waitUntil(20*time.Second, func() (bool, error) {
		var current model.PlatformGenerationJob
		if err := server.db.First(&current, "id = ?", jobID).Error; err != nil {
			return false, err
		}
		return current.Status == model.PlatformGenerationStatusReconciliationRequired, nil
	}); err != nil {
		return err
	}
	job, outbox, admission, route, err := server.generationState(jobID)
	if err != nil {
		return err
	}
	effects := server.bridge.effectsFor(jobID)
	staleResult := server.db.Model(&model.PlatformGenerationJob{}).Where(
		"id = ? AND status = ? AND submission_lease_token = ? AND submission_lease_expires_at > clock_timestamp()",
		jobID, model.PlatformGenerationStatusSubmitting, effect.WorkerToken,
	).Update("progress", 1)
	if staleResult.Error != nil {
		return staleResult.Error
	}
	recoveryID := builder.add("live_candidate_replacement", "replacement candidate recovered expired job without a second provider call", map[string]any{
		"job_id": jobID, "outbox_id": outbox.ID, "route_admission_id": admission.ID,
		"job_status": job.Status, "outbox_state": outbox.State, "outbox_attempts": outbox.Attempts,
		"provider_effect_count": len(effects), "route_id": route.ID, "route_admission_state": admission.State,
		"route_slot_held": admission.SlotHeld, "route_active_count": route.ActiveCount,
		"old_worker_token_sha256":          digestText(effect.WorkerToken),
		"provider_submission_token_sha256": digestText(effect.SubmissionToken),
		"persisted_submission_token_hash":  admission.SubmissionTokenHash,
		"submission_token_hash_matches":    strings.TrimPrefix(digestText(effect.SubmissionToken), "sha256:") == admission.SubmissionTokenHash,
		"stale_old_worker_rows_affected":   staleResult.RowsAffected,
	})
	liveBind(assertions, mapping, "provider_side_effect_observed", len(effects) == 1, attestationID, acceptedID)
	liveBind(assertions, mapping, "candidate_process_replaced", job.Status == model.PlatformGenerationStatusReconciliationRequired, attestationID, recoveryID)
	liveBind(assertions, mapping, "lease_recovered_without_resubmit", outbox.Attempts >= 2 && len(effects) == 1 && admission.State == model.PlatformGenerationRouteAdmissionUnknown && admission.SlotHeld, attestationID, acceptedID, recoveryID)
	liveBind(assertions, mapping, "old_worker_token_fenced", staleResult.RowsAffected == 0, attestationID, recoveryID)
	return nil
}

func (server *server) handleStart(writer http.ResponseWriter, request *http.Request) {
	if !server.authorize(request) {
		writeJSON(writer, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	var body startRequest
	if err := decodeStrictJSON(request, &body); err != nil {
		writeJSON(writer, http.StatusBadRequest, map[string]any{"error": "invalid request"})
		return
	}
	expectedCandidate := candidateFor(server.config)
	requestedAt, timeErr := time.Parse(time.RFC3339Nano, body.RequestedAtUTC)
	_, nonceErr := uuid.Parse(body.RunNonce)
	_, supported := requiredAssertions[body.Scenario]
	if body.SchemaVersion != 1 || body.Target != "new_api_candidate" || strings.TrimSpace(body.Model) == "" || strings.TrimSpace(body.Mode) == "" ||
		timeErr != nil || time.Since(requestedAt).Abs() > 5*time.Minute || nonceErr != nil ||
		!validCandidateInstance(body.Candidate) || !sameCandidateBuild(body.Candidate, expectedCandidate) {
		writeJSON(writer, http.StatusUnprocessableEntity, map[string]any{"error": "run binding rejected"})
		return
	}
	if !supported {
		writeJSON(writer, http.StatusUnprocessableEntity, map[string]any{"error": "scenario has no executable fault implementation"})
		return
	}
	select {
	case server.execution <- struct{}{}:
	default:
		writeJSON(writer, http.StatusConflict, map[string]any{"error": "another fault run is active"})
		return
	}
	acceptedAt := utcNow()
	runID := uuid.NewString()
	result := &runResult{
		SchemaVersion: 1, RunID: runID, RunNonce: body.RunNonce, Scenario: body.Scenario,
		Target: body.Target, Model: body.Model, Mode: body.Mode, Candidate: body.Candidate,
		Status: "RUNNING", RequestReceivedAtUTC: acceptedAt, StartedAtUTC: acceptedAt,
		ExecutionScope:       "package_level_production_model_service_code_with_real_postgresql_redis",
		TargetScope:          "production_model_service_packages_with_real_postgresql_redis",
		LiveCandidateFaulted: false,
	}
	server.mu.Lock()
	server.runs[runID] = result
	server.mu.Unlock()
	go func() {
		defer func() { <-server.execution }()
		server.execute(result)
	}()
	writeJSON(writer, http.StatusAccepted, startResponse{
		SchemaVersion: 1, RunID: runID, RunNonce: body.RunNonce, Scenario: body.Scenario,
		Target: body.Target, Model: body.Model, Mode: body.Mode, Candidate: body.Candidate,
		AcceptedAtUTC: acceptedAt,
	})
}

func (server *server) handleResult(writer http.ResponseWriter, request *http.Request) {
	if !server.authorize(request) {
		writeJSON(writer, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	runID := strings.TrimPrefix(request.URL.Path, "/v1/relay-fault-injections/")
	server.mu.Lock()
	result, ok := server.runs[runID]
	if ok {
		data, _ := json.Marshal(result)
		copyResult := &runResult{}
		_ = json.Unmarshal(data, copyResult)
		server.mu.Unlock()
		writeJSON(writer, http.StatusOK, copyResult)
		return
	}
	server.mu.Unlock()
	writeJSON(writer, http.StatusNotFound, map[string]any{"error": "run not found"})
}

func (server *server) readCandidateIdentity() (candidateIdentity, int, []byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	request, _ := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(server.config.CandidateBaseURL, "/")+"/health/ready", nil)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return candidateIdentity{}, 0, nil, fmt.Errorf("candidate readiness unavailable: %w", err)
	}
	defer response.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(response.Body, 256*1024))
	actual := candidateIdentity{
		InstanceID:          response.Header.Get("X-Relay-Instance-ID"),
		UpstreamGitRevision: response.Header.Get("X-Relay-Upstream-Revision"),
		SourceGitRevision:   response.Header.Get("X-Relay-Source-Revision"),
		ImageDigest:         response.Header.Get("X-Relay-Image-Digest"),
	}
	return actual, response.StatusCode, body, nil
}

func (server *server) candidateAttestation(builder *evidenceBuilder, expected candidateIdentity) (string, error) {
	actual, status, body, err := server.readCandidateIdentity()
	if err != nil {
		return "", err
	}
	evidenceID := builder.add("candidate_build_attestation", "attest the exact candidate build bound to this fault run", map[string]any{
		"http_status": status, "body_sha256": digestText(string(body)),
		"instance_id": actual.InstanceID, "upstream_git_revision": actual.UpstreamGitRevision,
		"source_git_revision": actual.SourceGitRevision, "image_digest": actual.ImageDigest,
		"attestation_only": true,
	})
	if status != http.StatusOK || !validCandidateInstance(actual) || !sameCandidate(actual, expected) {
		return evidenceID, errors.New("candidate live provenance does not match the bound instance/build")
	}
	return evidenceID, nil
}

func (server *server) handleCandidateProvenance(writer http.ResponseWriter, request *http.Request) {
	if !server.authorize(request) {
		writeJSON(writer, http.StatusUnauthorized, map[string]any{"error": "unauthorized"})
		return
	}
	actual, status, body, err := server.readCandidateIdentity()
	if err != nil || status != http.StatusOK || !validCandidateInstance(actual) || !sameCandidateBuild(actual, candidateFor(server.config)) {
		writeJSON(writer, http.StatusServiceUnavailable, map[string]any{
			"error": "candidate provenance unavailable", "readiness_status": status,
			"readiness_body_sha256": digestText(string(body)),
		})
		return
	}
	writeJSON(writer, http.StatusOK, map[string]any{
		"candidate": actual, "readiness_status": status, "readiness_body_sha256": digestText(string(body)),
	})
}

func (server *server) execute(result *runResult) {
	builder := &evidenceBuilder{}
	assertions := make(map[string]bool)
	mapping := make(map[string][]string)
	for _, name := range requiredAssertions[result.Scenario] {
		assertions[name] = false
		mapping[name] = []string{}
	}
	attestationID, err := server.candidateAttestation(builder, result.Candidate)
	if err == nil {
		switch result.Scenario {
		case "provider_post_pre_disconnect":
			err = server.scenarioProviderPostPreDisconnect(builder, attestationID, assertions, mapping)
		case "provider_post_response_loss":
			err = server.scenarioProviderPostResponseLoss(builder, attestationID, assertions, mapping)
		case "unknown_reconciliation_created":
			err = server.scenarioUnknownReconciliation(builder, attestationID, assertions, mapping, true)
		case "unknown_reconciliation_not_created":
			err = server.scenarioUnknownReconciliation(builder, attestationID, assertions, mapping, false)
		case "lease_expiry":
			err = server.scenarioLeaseExpiry(builder, attestationID, assertions, mapping, false)
		case "stale_token_late_write":
			err = server.scenarioLeaseExpiry(builder, attestationID, assertions, mapping, true)
		case "worker_kill":
			err = server.scenarioWorkerKill(builder, attestationID, assertions, mapping)
		case "redis_outage":
			err = server.scenarioRedisOutage(builder, attestationID, assertions, mapping)
		case "db_jitter":
			err = server.scenarioDBJitter(builder, attestationID, assertions, mapping)
		case "db_commit_failure":
			err = server.scenarioDBCommitFailure(builder, attestationID, assertions, mapping)
		case "account_pool_cross_mode_shared":
			err = server.scenarioAccountPoolCrossModeShared(builder, attestationID, assertions, mapping)
		case "poll_timeout":
			err = server.scenarioPollTimeout(builder, attestationID, assertions, mapping)
		case "artifact_corruption", "artifact_oversize", "artifact_mime_mismatch", "artifact_ssrf":
			err = server.scenarioArtifactRejection(result.Scenario, builder, attestationID, assertions, mapping)
		case "callback_retry_dlq":
			err = server.scenarioCallbackRetryDLQ(builder, attestationID, assertions, mapping)
		case "provider_success_rate_drop_recovery":
			err = server.scenarioProviderSuccessRateDropRecovery(builder, attestationID, assertions, mapping)
		case "provider_widespread_route_failure":
			err = server.scenarioProviderWidespreadFailure(builder, attestationID, assertions, mapping)
		case "provider_batch_account_invalidation":
			err = server.scenarioProviderBatchInvalidation(builder, attestationID, assertions, mapping)
		case "provider_alert_retry_dlq":
			err = server.scenarioProviderAlertRetryDLQ(builder, attestationID, assertions, mapping)
		default:
			err = errors.New("scenario has no executable implementation")
		}
	}
	status := "PASS"
	for _, value := range assertions {
		if !value {
			status = "FAIL"
			break
		}
	}
	if err != nil {
		status = "FAIL"
		builder.add("execution_error", "fault scenario failed", map[string]any{"error": err.Error()})
	}
	server.mu.Lock()
	result.Status = status
	result.CompletedAtUTC = utcNow()
	result.Assertions = assertions
	result.AssertionEvidence = mapping
	result.RawEvidence = builder.entries
	if err != nil {
		result.FailureReason = err.Error()
	}
	server.mu.Unlock()
}

func bindAssertion(assertions map[string]bool, mapping map[string][]string, name string, passed bool, attestationID string, evidenceIDs ...string) {
	assertions[name] = passed
	if passed {
		ids := []string{attestationID}
		ids = append(ids, evidenceIDs...)
		mapping[name] = ids
	}
}

func (server *server) scenarioProviderPostPreDisconnect(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	modelName := "fault.pre-disconnect." + strings.ReplaceAll(uuid.NewString(), "-", "")
	route, err := server.createFaultProviderRoute("pre-disconnect", modelName, "text_to_video", 10, 2)
	if err != nil {
		return err
	}
	job, outbox, err := createGenerationJobForModel(server.db, "pre-disconnect", modelName, "text_to_video", `{}`)
	if err != nil {
		return err
	}
	claim, err := model.ClaimPlatformGenerationSubmission(5*time.Second, outbox.ID)
	if err != nil {
		return err
	}
	routeClaim, err := model.ClaimPlatformGenerationProviderRoute(job.ID, modelName, "text_to_video")
	if err != nil {
		return err
	}
	request, _ := http.NewRequest(http.MethodPost, "http://127.0.0.1:1/provider-submit", strings.NewReader(`{"fault":"pre_disconnect"}`))
	client := &http.Client{Timeout: 500 * time.Millisecond}
	response, callErr := client.Do(request)
	if response != nil {
		_ = response.Body.Close()
	}
	if callErr == nil {
		return errors.New("pre-disconnect injection unexpectedly reached an HTTP endpoint")
	}
	var effectCount int64
	if err := server.db.Model(&faultProviderEffect{}).Where("job_id = ?", job.ID).Count(&effectCount).Error; err != nil {
		return err
	}
	networkID := builder.add("provider_transport_fault", "connection failed before a provider HTTP request could be accepted", map[string]any{
		"job_id": job.ID, "route_id": route.ID, "network_error": callErr.Error(), "provider_effect_count": effectCount,
		"submission_worker_token_sha256": digestText(claim.Token), "route_submission_token_sha256": digestText(routeClaim.SubmissionToken),
	})
	releasedRoute, err := model.ReleasePlatformGenerationProviderRoute(job.ID, routeClaim.SubmissionToken)
	if err != nil {
		return err
	}
	releasedJob, err := model.ReleasePlatformGenerationSubmission(*claim, time.Second, "provider connection failed before POST")
	if err != nil {
		return err
	}
	var persistedJob model.PlatformGenerationJob
	var persistedOutbox model.PlatformGenerationOutbox
	if err := server.db.First(&persistedJob, "id = ?", job.ID).Error; err != nil {
		return err
	}
	if err := server.db.First(&persistedOutbox, outbox.ID).Error; err != nil {
		return err
	}
	admission, persistedRoute, err := model.GetPlatformGenerationProviderRouteAssignment(job.ID)
	if err != nil {
		return err
	}
	stateID := builder.add("postgres_retry_state", "proven pre-POST failure released only the route and durably requeued the job", map[string]any{
		"job_id": job.ID, "outbox_id": outbox.ID, "job_status": persistedJob.Status,
		"outbox_state": persistedOutbox.State, "provider_submission_attempt": persistedJob.ProviderSubmissionAttempt,
		"route_release_won": releasedRoute, "job_release_won": releasedJob,
		"admission_state": admission.State, "slot_held": admission.SlotHeld, "account_active_count": persistedRoute.ActiveCount,
		"reservation_action": "hold",
	})
	bindAssertion(assertions, mapping, "provider_post_not_attempted", effectCount == 0 && callErr != nil, attestationID, networkID)
	bindAssertion(assertions, mapping, "job_retry_safe", releasedRoute && releasedJob && persistedJob.Status == model.PlatformGenerationStatusQueued && persistedOutbox.State == model.PlatformGenerationOutboxPending && persistedJob.ProviderSubmissionAttempt == 0, attestationID, stateID)
	bindAssertion(assertions, mapping, "reservation_action_valid", persistedJob.Status == model.PlatformGenerationStatusQueued && admission.State == model.PlatformGenerationRouteAdmissionReleased && !admission.SlotHeld && persistedRoute.ActiveCount == 0, attestationID, stateID)
	return nil
}

func (server *server) prepareUnknownGeneration(label string, stageRecovery bool) (model.PlatformGenerationJob, model.PlatformGenerationOutbox, *model.PlatformGenerationRouteClaim, string, error) {
	modelName := "fault." + label + "." + strings.ReplaceAll(uuid.NewString(), "-", "")
	route, err := server.createFaultProviderRoute(label, modelName, "text_to_video", 20, 3)
	if err != nil {
		return model.PlatformGenerationJob{}, model.PlatformGenerationOutbox{}, nil, "", err
	}
	job, outbox, err := createGenerationJobForModel(server.db, label, modelName, "text_to_video", `{}`)
	if err != nil {
		return job, outbox, nil, "", err
	}
	claim, err := model.ClaimPlatformGenerationSubmission(10*time.Second, outbox.ID)
	if err != nil {
		return job, outbox, nil, "", err
	}
	routeClaim, err := model.ClaimPlatformGenerationProviderRoute(job.ID, modelName, "text_to_video")
	if err != nil {
		return job, outbox, nil, "", err
	}
	if _, err := model.BeginPlatformGenerationRouteSubmission(job.ID, route.ID, claim.Token, routeClaim.SubmissionToken); err != nil {
		return job, outbox, nil, "", err
	}
	nativeTaskID, err := model.PlatformGenerationNativeTaskID(job.ID)
	if err != nil {
		return job, outbox, nil, "", err
	}
	if stageRecovery {
		var channel model.Channel
		if err := server.db.First(&channel, route.ChannelID).Error; err != nil {
			return job, outbox, nil, "", err
		}
		pinnedKey, err := channel.GetKeyAt(route.KeyIndex)
		if err != nil {
			return job, outbox, nil, "", err
		}
		keyIndex := route.KeyIndex
		template := &model.Task{
			TaskID: nativeTaskID, ChannelId: route.ChannelID, Platform: constant.TaskPlatform("fault-reconciliation"),
			UserId: 42, Group: "service", Action: constant.TaskActionTextGenerate, SubmitTime: time.Now().UTC().Unix(),
			Properties:  model.Properties{UpstreamModelName: route.UpstreamModel, OriginModelName: route.Model},
			PrivateData: model.TaskPrivateData{PinnedKeyIndex: &keyIndex, PinnedKeyFingerprint: route.KeyFingerprint, Key: pinnedKey},
		}
		if err := model.StagePlatformGenerationNativeTaskRecovery(job.ID, claim.Token, template); err != nil {
			return job, outbox, nil, "", err
		}
	}
	marked, err := model.MarkPlatformGenerationRouteSubmissionUnknown(job.ID, routeClaim.SubmissionToken)
	if err != nil || !marked {
		return job, outbox, nil, "", fmt.Errorf("mark submission unknown: won=%t: %w", marked, err)
	}
	won, err := model.CompletePlatformGenerationSubmission(*claim, map[string]any{
		"native_task_id": nativeTaskID, "provider_route_id": route.ID, "provider_channel_id": route.ChannelID,
		"provider_key_index": route.KeyIndex, "provider_submission_attempt": routeClaim.Attempt,
		"status":        model.PlatformGenerationStatusReconciliationRequired,
		"error_code":    model.PlatformGenerationErrorSubmissionReconciliationRequired,
		"error_message": "fault injection: provider submission outcome unknown", "error_retryable": true,
	})
	if err != nil || !won {
		return job, outbox, nil, "", fmt.Errorf("complete unknown submission: won=%t: %w", won, err)
	}
	if err := server.db.First(&job, "id = ?", job.ID).Error; err != nil {
		return job, outbox, nil, "", err
	}
	return job, outbox, routeClaim, nativeTaskID, nil
}

func (server *server) scenarioProviderPostResponseLoss(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	modelName := "fault.response-loss." + strings.ReplaceAll(uuid.NewString(), "-", "")
	route, err := server.createFaultProviderRoute("response-loss", modelName, "text_to_video", 20, 3)
	if err != nil {
		return err
	}
	job, outbox, err := createGenerationJobForModel(server.db, "response-loss", modelName, "text_to_video", `{}`)
	if err != nil {
		return err
	}
	claim, err := model.ClaimPlatformGenerationSubmission(10*time.Second, outbox.ID)
	if err != nil {
		return err
	}
	routeClaim, err := model.ClaimPlatformGenerationProviderRoute(job.ID, modelName, "text_to_video")
	if err != nil {
		return err
	}
	if _, err := model.BeginPlatformGenerationRouteSubmission(job.ID, route.ID, claim.Token, routeClaim.SubmissionToken); err != nil {
		return err
	}
	var accepted atomic.Int32
	endpoint := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		_, _ = io.Copy(io.Discard, request.Body)
		effect := faultProviderEffect{ID: uuid.NewString(), JobID: job.ID, ProviderTaskID: "provider-task-" + uuid.NewString(), CreatedAt: time.Now().UTC()}
		if server.db.Create(&effect).Error == nil {
			accepted.Add(1)
		}
		if hijacker, ok := writer.(http.Hijacker); ok {
			connection, _, hijackErr := hijacker.Hijack()
			if hijackErr == nil {
				_ = connection.Close()
				return
			}
		}
	}))
	defer endpoint.Close()
	request, _ := http.NewRequest(http.MethodPost, endpoint.URL, strings.NewReader(`{"generation":"start"}`))
	response, callErr := (&http.Client{Timeout: 2 * time.Second}).Do(request)
	if response != nil {
		_ = response.Body.Close()
	}
	if callErr == nil || accepted.Load() != 1 {
		return errors.New("response-loss injection did not accept exactly one provider side effect")
	}
	marked, err := model.MarkPlatformGenerationRouteSubmissionUnknown(job.ID, routeClaim.SubmissionToken)
	if err != nil || !marked {
		return fmt.Errorf("mark response loss unknown: won=%t: %w", marked, err)
	}
	nativeTaskID, _ := model.PlatformGenerationNativeTaskID(job.ID)
	won, err := model.CompletePlatformGenerationSubmission(*claim, map[string]any{
		"native_task_id": nativeTaskID, "provider_route_id": route.ID, "provider_channel_id": route.ChannelID,
		"provider_key_index": route.KeyIndex, "provider_submission_attempt": routeClaim.Attempt,
		"status":        model.PlatformGenerationStatusReconciliationRequired,
		"error_code":    model.PlatformGenerationErrorSubmissionReconciliationRequired,
		"error_message": "provider accepted then response was lost", "error_retryable": true,
	})
	if err != nil || !won {
		return fmt.Errorf("persist response loss: won=%t: %w", won, err)
	}
	var persisted model.PlatformGenerationJob
	if err := server.db.First(&persisted, "id = ?", job.ID).Error; err != nil {
		return err
	}
	admission, persistedRoute, err := model.GetPlatformGenerationProviderRouteAssignment(job.ID)
	if err != nil {
		return err
	}
	_, retryErr := model.ClaimPlatformGenerationProviderRoute(job.ID, modelName, "text_to_video")
	var effectCount int64
	if err := server.db.Model(&faultProviderEffect{}).Where("job_id = ?", job.ID).Count(&effectCount).Error; err != nil {
		return err
	}
	transportID := builder.add("provider_response_loss", "provider stored one task then closed the transport before returning a response", map[string]any{
		"job_id": job.ID, "route_id": route.ID, "transport_error": callErr.Error(), "provider_effect_count": effectCount,
	})
	stateID := builder.add("postgres_unknown_submission", "response loss durably retained the original route and account slot", map[string]any{
		"job_status": persisted.Status, "provider_submission_attempt": persisted.ProviderSubmissionAttempt,
		"job_route_id": persisted.ProviderRouteID, "admission_route_id": admission.RouteID,
		"admission_state": admission.State, "slot_held": admission.SlotHeld, "account_active_count": persistedRoute.ActiveCount,
		"automatic_route_claim_error": fmt.Sprint(retryErr), "provider_effect_count_after_retry_probe": effectCount,
	})
	bindAssertion(assertions, mapping, "single_provider_task", effectCount == 1, attestationID, transportID, stateID)
	bindAssertion(assertions, mapping, "reconciliation_required", persisted.Status == model.PlatformGenerationStatusReconciliationRequired, attestationID, stateID)
	bindAssertion(assertions, mapping, "route_unchanged", persisted.ProviderRouteID == route.ID && admission.RouteID == route.ID, attestationID, stateID)
	bindAssertion(assertions, mapping, "account_slot_retained", admission.State == model.PlatformGenerationRouteAdmissionUnknown && admission.SlotHeld && persistedRoute.ActiveCount == 1, attestationID, stateID)
	bindAssertion(assertions, mapping, "automatic_resubmit_absent", errors.Is(retryErr, model.ErrPlatformGenerationRouteAdmissionUnknown) && effectCount == 1, attestationID, stateID)
	return nil
}

func (server *server) scenarioUnknownReconciliation(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string, created bool) error {
	label := "unknown-not-created"
	if created {
		label = "unknown-created"
	}
	job, _, routeClaim, nativeTaskID, err := server.prepareUnknownGeneration(label, created)
	if err != nil {
		return err
	}
	if created {
		effect := faultProviderEffect{ID: uuid.NewString(), JobID: job.ID, ProviderTaskID: "provider-confirmed-" + uuid.NewString(), CreatedAt: time.Now().UTC()}
		if err := server.db.Create(&effect).Error; err != nil {
			return err
		}
	}
	beforeAdmission, beforeRoute, err := model.GetPlatformGenerationProviderRouteAssignment(job.ID)
	if err != nil {
		return err
	}
	beforeID := builder.add("unknown_submission", "durable reconciliation-required state preceded the definitive provider answer", map[string]any{
		"job_id": job.ID, "job_status": job.Status, "route_id": beforeRoute.ID, "submission_attempt": routeClaim.Attempt,
		"admission_state": beforeAdmission.State, "slot_held": beforeAdmission.SlotHeld, "account_active_count": beforeRoute.ActiveCount,
	})
	outcome := "not_created"
	upstreamTaskID := ""
	if created {
		outcome = "created"
		upstreamTaskID = "provider-task-definitive-" + uuid.NewString()
	}
	reconciliation, err := service.GetPlatformGenerationUnknownSubmission(job.TenantID, job.ID)
	if err != nil {
		return err
	}
	resolved, receipt, _, err := service.ResolvePlatformGenerationUnknownSubmission(
		job.TenantID,
		job.ID,
		dto.PlatformGenerationReconciliationRequest{
			OperationID:                 "fault-reconcile-" + uuid.NewString(),
			TenantID:                    job.TenantID,
			Outcome:                     outcome,
			UpstreamTaskID:              upstreamTaskID,
			ExpectedRouteID:             beforeRoute.ID,
			ExpectedSubmissionAttempt:   routeClaim.Attempt,
			ExpectedReconciliationToken: reconciliation.ReconciliationToken,
			VerificationReference:       "fault-provider-console-" + label,
			ApprovedBy:                  "fault-harness-operator",
			ApprovalReason:              "Fault harness verified the definitive provider outcome",
			ApprovalKeyID:               "fault-harness-approval-v1",
			ApprovalSignature:           "hmac-sha256:" + strings.Repeat("a", 64),
		},
		"fault-request-"+uuid.NewString(),
	)
	if err != nil {
		return err
	}
	var resolvedJob model.PlatformGenerationJob
	if err := server.db.First(&resolvedJob, "id = ?", job.ID).Error; err != nil {
		return err
	}
	afterAdmission, afterRoute, err := model.GetPlatformGenerationProviderRouteAssignment(job.ID)
	if err != nil {
		return err
	}
	var effectCount int64
	if err := server.db.Model(&faultProviderEffect{}).Where("job_id = ?", job.ID).Count(&effectCount).Error; err != nil {
		return err
	}
	var admissionCount int64
	if err := server.db.Model(&model.PlatformGenerationRouteAdmission{}).Where("job_id = ?", job.ID).Count(&admissionCount).Error; err != nil {
		return err
	}
	resolvedID := builder.add("manual_reconciliation", "production reconciliation resolved the exact original route without cross-channel submission", map[string]any{
		"outcome": outcome, "resolved_status": resolved.Status, "resolved_route_id": resolvedJob.ProviderRouteID,
		"reconciliation_event_id": receipt.EventID, "operation_id": receipt.OperationID,
		"original_route_id": beforeRoute.ID, "admission_route_id": afterAdmission.RouteID,
		"admission_state": afterAdmission.State, "slot_held": afterAdmission.SlotHeld,
		"account_active_count": afterRoute.ActiveCount, "provider_effect_count": effectCount,
		"admission_row_count": admissionCount, "native_task_id": nativeTaskID,
		"reservation_action": resolved.ReservationAction,
	})
	bindAssertion(assertions, mapping, "reconciliation_required_observed", job.Status == model.PlatformGenerationStatusReconciliationRequired && beforeAdmission.State == model.PlatformGenerationRouteAdmissionUnknown && beforeAdmission.SlotHeld, attestationID, beforeID)
	if created {
		retainedBeforeTerminal := resolved.Status == model.PlatformGenerationStatusProcessing && afterAdmission.SlotHeld && afterRoute.ActiveCount == 1
		pollToken := uuid.NewString()
		if err := server.db.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
			"poll_lease_token": pollToken, "poll_lease_expires_at": gorm.Expr("clock_timestamp() + interval '30 seconds'"),
		}).Error; err != nil {
			return err
		}
		terminalWon, err := model.CompletePlatformGenerationTerminal(job.ID, pollToken, model.PlatformGenerationStatusProcessing, map[string]any{"status": model.PlatformGenerationStatusFailed, "progress": 100})
		if err != nil {
			return err
		}
		terminalAdmission, terminalRoute, err := model.GetPlatformGenerationProviderRouteAssignment(job.ID)
		if err != nil {
			return err
		}
		terminalID := builder.add("provider_terminal_fence", "the sticky account slot remained held until a fenced terminal transition", map[string]any{
			"retained_before_terminal": retainedBeforeTerminal, "terminal_transition_won": terminalWon,
			"terminal_admission_state": terminalAdmission.State, "terminal_slot_held": terminalAdmission.SlotHeld,
			"terminal_account_active_count": terminalRoute.ActiveCount,
		})
		bindAssertion(assertions, mapping, "single_provider_task", effectCount == 1, attestationID, resolvedID)
		bindAssertion(assertions, mapping, "original_route_resumed", resolvedJob.ProviderRouteID == beforeRoute.ID && afterAdmission.RouteID == beforeRoute.ID && resolvedJob.UpstreamTaskID == upstreamTaskID, attestationID, resolvedID)
		bindAssertion(assertions, mapping, "account_slot_retained_until_terminal", retainedBeforeTerminal && terminalWon && !terminalAdmission.SlotHeld && terminalRoute.ActiveCount == 0, attestationID, resolvedID, terminalID)
		bindAssertion(assertions, mapping, "cross_channel_retry_absent", effectCount == 1 && admissionCount == 1, attestationID, resolvedID)
	} else {
		errorCode := ""
		if resolved.Error != nil {
			errorCode = resolved.Error.Code
		}
		bindAssertion(assertions, mapping, "proven_non_creation_recorded", resolved.Status == model.PlatformGenerationStatusFailed && errorCode == model.PlatformGenerationErrorSubmissionConfirmedNotCreated, attestationID, resolvedID)
		bindAssertion(assertions, mapping, "cross_channel_retry_absent", effectCount == 0 && admissionCount == 1 && afterAdmission.RouteID == beforeRoute.ID, attestationID, resolvedID)
		bindAssertion(assertions, mapping, "account_slot_released", afterAdmission.State == model.PlatformGenerationRouteAdmissionReleased && !afterAdmission.SlotHeld && afterRoute.ActiveCount == 0, attestationID, resolvedID)
		bindAssertion(assertions, mapping, "reservation_action_valid", resolved.Status == model.PlatformGenerationStatusFailed && errorCode == model.PlatformGenerationErrorSubmissionConfirmedNotCreated && resolved.ReservationAction == "release", attestationID, resolvedID)
	}
	return nil
}

func (server *server) scenarioLeaseExpiry(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string, staleScenario bool) error {
	job, outbox, err := createGenerationJob(server.db, "lease")
	if err != nil {
		return err
	}
	var recoveryTemplate *model.Task
	if staleScenario {
		route, err := server.createFaultProviderRoute("stale-stage", job.Model, job.Mode, 10, 1)
		if err != nil {
			return err
		}
		routeClaim, err := model.ClaimPlatformGenerationProviderRoute(job.ID, job.Model, job.Mode)
		if err != nil {
			return fmt.Errorf("claim stale-stage route: %w", err)
		}
		first, err := model.ClaimPlatformGenerationSubmission(time.Second, outbox.ID)
		if err != nil {
			return fmt.Errorf("first lease claim: %w", err)
		}
		if _, err := model.BeginPlatformGenerationRouteSubmission(job.ID, route.ID, first.Token, routeClaim.SubmissionToken); err != nil {
			return fmt.Errorf("begin stale-stage route submission: %w", err)
		}
		var channel model.Channel
		if err := server.db.First(&channel, route.ChannelID).Error; err != nil {
			return err
		}
		pinnedKey, err := channel.GetKeyAt(route.KeyIndex)
		if err != nil {
			return err
		}
		nativeTaskID, err := model.PlatformGenerationNativeTaskID(job.ID)
		if err != nil {
			return err
		}
		keyIndex := route.KeyIndex
		recoveryTemplate = &model.Task{
			TaskID: nativeTaskID, ChannelId: route.ChannelID, Platform: constant.TaskPlatform("fault-stale-stage"),
			UserId: 42, Group: "service", Action: constant.TaskActionTextGenerate, SubmitTime: time.Now().UTC().Unix(),
			Properties: model.Properties{UpstreamModelName: route.UpstreamModel, OriginModelName: route.Model},
			PrivateData: model.TaskPrivateData{
				PinnedKeyIndex: &keyIndex, PinnedKeyFingerprint: route.KeyFingerprint, Key: pinnedKey,
			},
		}
		firstID := builder.add("postgres_lease", "first worker claimed generation outbox and consumed the one-shot route token", map[string]any{
			"job_id": job.ID, "outbox_id": outbox.ID, "token_sha256": digestText(first.Token),
			"lease_expires_at_utc": first.Job.SubmissionLeaseExpiresAt.UTC().Format(time.RFC3339Nano),
			"route_id":             route.ID, "route_attempt": routeClaim.Attempt,
		})
		time.Sleep(2100 * time.Millisecond)
		second, err := model.ClaimPlatformGenerationSubmission(3*time.Second, outbox.ID)
		if err != nil {
			return fmt.Errorf("replacement lease claim: %w", err)
		}
		secondID := builder.add("postgres_lease", "replacement worker reclaimed expired lease", map[string]any{
			"job_id": job.ID, "outbox_id": outbox.ID, "first_token_sha256": digestText(first.Token),
			"second_token_sha256": digestText(second.Token), "tokens_distinct": first.Token != second.Token,
		})
		staleStageErr := model.StagePlatformGenerationNativeTaskRecovery(job.ID, first.Token, recoveryTemplate)
		var afterStaleStage model.PlatformGenerationJob
		if err := server.db.First(&afterStaleStage, "id = ?", job.ID).Error; err != nil {
			return err
		}
		staleRecoveryAbsent := afterStaleStage.NativeTaskRecoveryJSON == "" && afterStaleStage.NativeTaskID == ""
		currentStageErr := model.StagePlatformGenerationNativeTaskRecovery(job.ID, second.Token, recoveryTemplate)
		stageFenceID := builder.add("postgres_stage_fence", "expired submitter could not stage provider recovery identity after lease takeover", map[string]any{
			"stale_stage_rejected":    staleStageErr != nil,
			"stale_recovery_absent":   staleRecoveryAbsent,
			"current_stage_succeeded": currentStageErr == nil,
		})
		staleWon, err := model.CompletePlatformGenerationSubmission(*first, map[string]any{
			"status": model.PlatformGenerationStatusFailed, "progress": 0, "error_code": "STALE_WORKER",
		})
		if err != nil {
			return fmt.Errorf("stale completion: %w", err)
		}
		currentWon, err := model.CompletePlatformGenerationSubmission(*second, processingUpdates(25))
		if err != nil {
			return fmt.Errorf("current completion: %w", err)
		}
		var persisted model.PlatformGenerationJob
		if err := server.db.First(&persisted, "id = ?", job.ID).Error; err != nil {
			return err
		}
		fenceID := builder.add("postgres_fence", "late stale write and current owner completion", map[string]any{
			"stale_rows_won": staleWon, "current_rows_won": currentWon,
			"final_status": persisted.Status, "final_progress": persisted.Progress,
		})
		bindAssertion(assertions, mapping, "late_write_rejected", !staleWon && staleStageErr != nil && staleRecoveryAbsent && currentStageErr == nil, attestationID, firstID, secondID, stageFenceID, fenceID)
		bindAssertion(assertions, mapping, "terminal_state_not_regressed", currentWon && persisted.Status == model.PlatformGenerationStatusProcessing && persisted.Progress == 25, attestationID, fenceID)
		return nil
	}
	first, err := model.ClaimPlatformGenerationSubmission(time.Second, outbox.ID)
	if err != nil {
		return fmt.Errorf("first lease claim: %w", err)
	}
	firstID := builder.add("postgres_lease", "first worker claimed generation outbox", map[string]any{
		"job_id": job.ID, "outbox_id": outbox.ID, "token_sha256": digestText(first.Token),
		"lease_expires_at_utc": first.Job.SubmissionLeaseExpiresAt.UTC().Format(time.RFC3339Nano),
	})
	time.Sleep(2100 * time.Millisecond)
	second, err := model.ClaimPlatformGenerationSubmission(3*time.Second, outbox.ID)
	if err != nil {
		return fmt.Errorf("replacement lease claim: %w", err)
	}
	secondID := builder.add("postgres_lease", "replacement worker reclaimed expired lease", map[string]any{
		"job_id": job.ID, "outbox_id": outbox.ID, "first_token_sha256": digestText(first.Token),
		"second_token_sha256": digestText(second.Token), "tokens_distinct": first.Token != second.Token,
	})
	staleWon, err := model.CompletePlatformGenerationSubmission(*first, map[string]any{
		"status": model.PlatformGenerationStatusFailed, "progress": 0, "error_code": "STALE_WORKER",
	})
	if err != nil {
		return fmt.Errorf("stale completion: %w", err)
	}
	currentWon, err := model.CompletePlatformGenerationSubmission(*second, processingUpdates(25))
	if err != nil {
		return fmt.Errorf("current completion: %w", err)
	}
	var persisted model.PlatformGenerationJob
	if err := server.db.First(&persisted, "id = ?", job.ID).Error; err != nil {
		return err
	}
	fenceID := builder.add("postgres_fence", "late stale write and current owner completion", map[string]any{
		"stale_rows_won": staleWon, "current_rows_won": currentWon,
		"final_status": persisted.Status, "final_progress": persisted.Progress,
	})
	bindAssertion(assertions, mapping, "new_owner_acquired", first.Token != second.Token, attestationID, firstID, secondID)
	bindAssertion(assertions, mapping, "old_owner_fenced", !staleWon && currentWon && persisted.Status == model.PlatformGenerationStatusProcessing, attestationID, fenceID)
	return nil
}

type childClaimMessage struct {
	PID        int    `json:"pid"`
	JobID      string `json:"job_id"`
	OutboxID   int64  `json:"outbox_id"`
	Token      string `json:"token"`
	ProviderID string `json:"provider_task_id"`
}

func runClaimWorker(outboxID int64) error {
	loaded, err := loadConfig()
	if err != nil {
		return err
	}
	database, err := openDatabase(loaded.DatabaseURL)
	if err != nil {
		return err
	}
	claim, err := model.ClaimPlatformGenerationSubmission(time.Second, outboxID)
	if err != nil {
		return err
	}
	effect := faultProviderEffect{
		ID: uuid.NewString(), JobID: claim.Job.ID,
		ProviderTaskID: "provider-task-" + uuid.NewString(), CreatedAt: time.Now().UTC(),
	}
	if err := database.Create(&effect).Error; err != nil {
		return err
	}
	message := childClaimMessage{
		PID: os.Getpid(), JobID: claim.Job.ID, OutboxID: claim.OutboxID,
		Token: claim.Token, ProviderID: effect.ProviderTaskID,
	}
	if err := json.NewEncoder(os.Stdout).Encode(message); err != nil {
		return err
	}
	select {}
}

func (server *server) scenarioWorkerKill(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	job, outbox, err := createGenerationJob(server.db, "worker-kill")
	if err != nil {
		return err
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	command := exec.Command(executable, "--claim-worker", strconv.FormatInt(outbox.ID, 10))
	command.Env = os.Environ()
	stdout, err := command.StdoutPipe()
	if err != nil {
		return err
	}
	stderr := &bytes.Buffer{}
	command.Stderr = stderr
	if err := command.Start(); err != nil {
		return err
	}
	reader := bufio.NewReader(stdout)
	line, err := reader.ReadBytes('\n')
	if err != nil {
		_ = command.Process.Kill()
		return fmt.Errorf("worker did not claim: %w (%s)", err, stderr.String())
	}
	message := childClaimMessage{}
	if err := json.Unmarshal(line, &message); err != nil {
		_ = command.Process.Kill()
		return fmt.Errorf("decode worker claim: %w", err)
	}
	claimedID := builder.add("worker_process", "child worker claimed and made one provider side effect", map[string]any{
		"pid": message.PID, "job_id": message.JobID, "outbox_id": message.OutboxID,
		"claim_token_sha256": digestText(message.Token), "provider_task_id": message.ProviderID,
	})
	if err := command.Process.Kill(); err != nil {
		return fmt.Errorf("kill claimed worker: %w", err)
	}
	waitErr := command.Wait()
	killedID := builder.add("worker_process", "operating system killed the active worker", map[string]any{
		"pid": message.PID, "process_state": command.ProcessState.String(), "wait_error": fmt.Sprint(waitErr),
	})
	time.Sleep(2100 * time.Millisecond)
	replacement, err := model.ClaimPlatformGenerationSubmission(3*time.Second, outbox.ID)
	if err != nil {
		return fmt.Errorf("reclaim killed worker lease: %w", err)
	}
	var effectCount int64
	if err := server.db.Model(&faultProviderEffect{}).Where("job_id = ?", job.ID).Count(&effectCount).Error; err != nil {
		return err
	}
	recoveredID := builder.add("postgres_recovery", "replacement worker reconciled the existing provider task", map[string]any{
		"job_id": job.ID, "old_token_sha256": digestText(message.Token),
		"new_token_sha256": digestText(replacement.Token), "provider_effect_count": effectCount,
	})
	currentWon, err := model.CompletePlatformGenerationSubmission(*replacement, processingUpdates(40))
	if err != nil {
		return err
	}
	staleClaim := model.PlatformGenerationClaim{Job: job, OutboxID: outbox.ID, Token: message.Token}
	staleWon, err := model.CompletePlatformGenerationSubmission(staleClaim, map[string]any{
		"status": model.PlatformGenerationStatusFailed, "progress": 0, "error_code": "KILLED_WORKER_LATE_WRITE",
	})
	if err != nil {
		return err
	}
	var persisted model.PlatformGenerationJob
	if err := server.db.First(&persisted, "id = ?", job.ID).Error; err != nil {
		return err
	}
	terminalID := builder.add("postgres_fence", "current owner completed and killed owner could not regress state", map[string]any{
		"current_rows_won": currentWon, "stale_rows_won": staleWon,
		"final_status": persisted.Status, "final_progress": persisted.Progress,
	})
	bindAssertion(assertions, mapping, "lease_recovered", replacement.Token != "" && replacement.Token != message.Token, attestationID, claimedID, killedID, recoveredID)
	bindAssertion(assertions, mapping, "duplicate_provider_task_absent", effectCount == 1, attestationID, claimedID, recoveredID)
	bindAssertion(assertions, mapping, "terminal_state_not_regressed", currentWon && !staleWon && persisted.Status == model.PlatformGenerationStatusProcessing && persisted.Progress == 40, attestationID, terminalID)
	return nil
}

func (server *server) scenarioRedisOutage(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	job, outbox, err := createGenerationJob(server.db, "redis-outage")
	if err != nil {
		return err
	}
	// Earlier scenarios may intentionally leave retry-safe outboxes pending.
	// Make this scenario's row deterministically first without deleting or
	// mutating unrelated durable work.
	if err := server.db.Model(&model.PlatformGenerationOutbox{}).Where("id = ?", outbox.ID).
		Update("available_at", gorm.Expr("clock_timestamp() - interval '1 day'")).Error; err != nil {
		return err
	}
	namespace := "fault-redis-" + uuid.NewString()
	queue, err := service.NewPlatformGenerationDelayQueue(service.PlatformGenerationDelayQueueConfig{
		Redis: server.redis, Database: server.db, Namespace: namespace,
		DispatchLease: 5 * time.Second, RecoveryInterval: 100 * time.Millisecond, RecoveryBatch: 32,
	})
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	_, pauseErr := server.redis.Do(ctx, "CLIENT", "PAUSE", "1500", "ALL").Result()
	cancel()
	if pauseErr != nil {
		return fmt.Errorf("pause Redis: %w", pauseErr)
	}
	outageCtx, outageCancel := context.WithTimeout(context.Background(), 350*time.Millisecond)
	_, outageErr := queue.AcquireDispatch(outageCtx)
	outageCancel()
	if outageErr == nil {
		return errors.New("Redis pause did not interrupt delayed-queue acquisition")
	}
	var duringOutbox model.PlatformGenerationOutbox
	var duringJob model.PlatformGenerationJob
	if err := server.db.First(&duringOutbox, outbox.ID).Error; err != nil {
		return err
	}
	if err := server.db.First(&duringJob, "id = ?", job.ID).Error; err != nil {
		return err
	}
	outageID := builder.add("redis_outage", "real Redis CLIENT PAUSE interrupted scheduling", map[string]any{
		"pause_milliseconds": 1500, "queue_error": outageErr.Error(),
		"postgres_outbox_state": duringOutbox.State, "postgres_job_status": duringJob.Status,
		"provider_submission_attempt": duringJob.ProviderSubmissionAttempt,
	})
	time.Sleep(1700 * time.Millisecond)
	recovered, err := queue.RecoverFromDatabase(context.Background())
	if err != nil {
		return fmt.Errorf("recover queue projection: %w", err)
	}
	dispatch, err := queue.AcquireDispatch(context.Background())
	if err != nil {
		return fmt.Errorf("acquire recovered dispatch: %w", err)
	}
	claim, err := model.ClaimPlatformGenerationSubmission(3*time.Second, dispatch.OutboxID)
	if err != nil {
		return fmt.Errorf("claim recovered Postgres outbox: %w", err)
	}
	currentWon, err := model.CompletePlatformGenerationSubmission(*claim, processingUpdates(30))
	if err != nil {
		return err
	}
	if err := queue.FinalizeDispatch(context.Background(), *dispatch); err != nil {
		return err
	}
	var finalJob model.PlatformGenerationJob
	if err := server.db.First(&finalJob, "id = ?", job.ID).Error; err != nil {
		return err
	}
	recoveryID := builder.add("redis_recovery", "PostgreSQL outbox rebuilt Redis schedule after recovery", map[string]any{
		"rows_reprojected": recovered, "redis_outbox_id": dispatch.OutboxID,
		"postgres_claim_won": currentWon, "final_job_status": finalJob.Status,
		"provider_submission_attempt": finalJob.ProviderSubmissionAttempt,
	})
	bindAssertion(assertions, mapping, "job_durable_in_postgres", duringOutbox.State == model.PlatformGenerationOutboxPending && duringJob.Status == model.PlatformGenerationStatusQueued, attestationID, outageID)
	bindAssertion(assertions, mapping, "provider_retry_budget_not_consumed", duringJob.ProviderSubmissionAttempt == 0 && finalJob.ProviderSubmissionAttempt == 0, attestationID, outageID, recoveryID)
	bindAssertion(assertions, mapping, "recovery_completed", recovered >= 1 && dispatch.OutboxID == outbox.ID && currentWon && finalJob.Status == model.PlatformGenerationStatusProcessing, attestationID, recoveryID)
	return nil
}

func (server *server) scenarioDBJitter(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	job, outbox, err := createGenerationJob(server.db, "db-jitter")
	if err != nil {
		return err
	}
	var dbClock time.Time
	if err := server.db.Transaction(func(tx *gorm.DB) error {
		var clockErr error
		dbClock, clockErr = model.GetDBTimeTx(tx)
		return clockErr
	}); err != nil {
		return err
	}
	const workers = 8
	type claimResult struct {
		Claim   *model.PlatformGenerationClaim
		Error   error
		Elapsed time.Duration
	}
	start := make(chan struct{})
	results := make(chan claimResult, workers)
	var wait sync.WaitGroup
	for index := 0; index < workers; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			began := time.Now()
			jitterErr := server.db.Exec("SELECT pg_sleep(0.05 + random() * 0.15)").Error
			if jitterErr != nil {
				results <- claimResult{Error: jitterErr, Elapsed: time.Since(began)}
				return
			}
			claim, claimErr := model.ClaimPlatformGenerationSubmission(3*time.Second, outbox.ID)
			results <- claimResult{Claim: claim, Error: claimErr, Elapsed: time.Since(began)}
		}()
	}
	close(start)
	wait.Wait()
	close(results)
	claims := make([]*model.PlatformGenerationClaim, 0, 1)
	notFound := 0
	elapsedMS := make([]int64, 0, workers)
	for result := range results {
		elapsedMS = append(elapsedMS, result.Elapsed.Milliseconds())
		switch {
		case result.Error == nil && result.Claim != nil:
			claims = append(claims, result.Claim)
		case errors.Is(result.Error, gorm.ErrRecordNotFound):
			notFound++
		default:
			return fmt.Errorf("concurrent jitter claim: %w", result.Error)
		}
	}
	if len(claims) != 1 {
		return fmt.Errorf("database jitter produced %d claims", len(claims))
	}
	claim := claims[0]
	clockDelta := claim.Job.SubmissionLeaseExpiresAt.Sub(dbClock)
	jitterID := builder.add("postgres_jitter", "concurrent workers claimed through real pg_sleep jitter", map[string]any{
		"worker_count": workers, "successful_claims": len(claims), "not_found_claims": notFound,
		"worker_elapsed_milliseconds": elapsedMS, "database_clock_utc": dbClock.UTC().Format(time.RFC3339Nano),
		"lease_expires_at_utc":                      claim.Job.SubmissionLeaseExpiresAt.UTC().Format(time.RFC3339Nano),
		"lease_minus_sampled_db_clock_milliseconds": clockDelta.Milliseconds(),
	})
	won, err := model.CompletePlatformGenerationSubmission(*claim, processingUpdates(35))
	if err != nil {
		return err
	}
	var persisted model.PlatformGenerationJob
	if err := server.db.First(&persisted, "id = ?", job.ID).Error; err != nil {
		return err
	}
	recoveryID := builder.add("postgres_recovery", "single jitter winner completed the durable job", map[string]any{
		"completion_won": won, "final_status": persisted.Status, "final_progress": persisted.Progress,
	})
	bindAssertion(assertions, mapping, "database_clock_used", clockDelta >= 2*time.Second && clockDelta <= 4*time.Second, attestationID, jitterID)
	bindAssertion(assertions, mapping, "duplicate_claim_absent", len(claims) == 1 && notFound == workers-1, attestationID, jitterID)
	bindAssertion(assertions, mapping, "recovery_completed", won && persisted.Status == model.PlatformGenerationStatusProcessing && persisted.Progress == 35, attestationID, recoveryID)
	return nil
}

func installDeferredCommitFault(database *gorm.DB) error {
	statements := []string{
		`CREATE TABLE IF NOT EXISTS relay_fault_commit_targets (job_id varchar(36) PRIMARY KEY, armed boolean NOT NULL DEFAULT true)`,
		`CREATE OR REPLACE FUNCTION relay_fault_fail_generation_commit() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM relay_fault_commit_targets WHERE job_id = NEW.id AND armed = true) THEN
    RAISE EXCEPTION 'relay fault injected deferred commit failure for %', NEW.id USING ERRCODE = '40001';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql`,
		`DROP TRIGGER IF EXISTS relay_fault_generation_commit_failure ON platform_generation_jobs`,
		`CREATE CONSTRAINT TRIGGER relay_fault_generation_commit_failure
AFTER UPDATE ON platform_generation_jobs
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION relay_fault_fail_generation_commit()`,
	}
	for _, statement := range statements {
		if err := database.Exec(statement).Error; err != nil {
			return err
		}
	}
	return nil
}

func (server *server) scenarioDBCommitFailure(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	if err := installDeferredCommitFault(server.db); err != nil {
		return err
	}
	job, outbox, err := createGenerationJob(server.db, "commit-failure")
	if err != nil {
		return err
	}
	claim, err := model.ClaimPlatformGenerationSubmission(time.Second, outbox.ID)
	if err != nil {
		return err
	}
	effect := faultProviderEffect{
		ID: uuid.NewString(), JobID: job.ID, ProviderTaskID: "provider-task-" + uuid.NewString(), CreatedAt: time.Now().UTC(),
	}
	if err := server.db.Create(&effect).Error; err != nil {
		return err
	}
	if err := server.db.Exec(
		"INSERT INTO relay_fault_commit_targets (job_id, armed) VALUES (?, true) ON CONFLICT (job_id) DO UPDATE SET armed = true",
		job.ID,
	).Error; err != nil {
		return err
	}
	effectID := builder.add("provider_side_effect", "provider accepted exactly one task before database commit", map[string]any{
		"job_id": job.ID, "provider_task_id": effect.ProviderTaskID, "claim_token_sha256": digestText(claim.Token),
	})
	commitWon, commitErr := model.CompletePlatformGenerationSubmission(*claim, processingUpdates(45))
	if commitErr == nil {
		return errors.New("deferred database commit fault did not fire")
	}
	var afterFailure model.PlatformGenerationJob
	if err := server.db.First(&afterFailure, "id = ?", job.ID).Error; err != nil {
		return err
	}
	commitID := builder.add("postgres_commit_failure", "deferred constraint failed the production completion transaction at commit", map[string]any{
		"completion_returned_won": commitWon, "commit_error": commitErr.Error(),
		"persisted_status_after_rollback": afterFailure.Status,
		"persisted_lease_token_sha256":    digestText(afterFailure.SubmissionLeaseToken),
	})
	if err := server.db.Exec("DELETE FROM relay_fault_commit_targets WHERE job_id = ?", job.ID).Error; err != nil {
		return err
	}
	time.Sleep(2100 * time.Millisecond)
	replacement, err := model.ClaimPlatformGenerationSubmission(3*time.Second, outbox.ID)
	if err != nil {
		return fmt.Errorf("claim after commit rollback: %w", err)
	}
	var effectCount int64
	if err := server.db.Model(&faultProviderEffect{}).Where("job_id = ?", job.ID).Count(&effectCount).Error; err != nil {
		return err
	}
	currentWon, err := model.CompletePlatformGenerationSubmission(*replacement, processingUpdates(55))
	if err != nil {
		return err
	}
	staleWon, err := model.CompletePlatformGenerationSubmission(*claim, map[string]any{
		"status": model.PlatformGenerationStatusFailed, "progress": 0, "error_code": "STALE_AFTER_COMMIT_FAILURE",
	})
	if err != nil {
		return err
	}
	var finalJob model.PlatformGenerationJob
	if err := server.db.First(&finalJob, "id = ?", job.ID).Error; err != nil {
		return err
	}
	recoveryID := builder.add("postgres_reconciliation", "replacement lease reconciled the one provider side effect", map[string]any{
		"provider_effect_count": effectCount, "old_token_sha256": digestText(claim.Token),
		"replacement_token_sha256": digestText(replacement.Token), "current_completion_won": currentWon,
		"stale_completion_won": staleWon, "final_status": finalJob.Status, "final_progress": finalJob.Progress,
	})
	bindAssertion(assertions, mapping, "external_side_effect_not_duplicated", effectCount == 1, attestationID, effectID, commitID, recoveryID)
	bindAssertion(assertions, mapping, "state_recoverable", commitErr != nil && afterFailure.Status == model.PlatformGenerationStatusSubmitting && currentWon && finalJob.Status == model.PlatformGenerationStatusProcessing, attestationID, commitID, recoveryID)
	bindAssertion(assertions, mapping, "fencing_preserved", replacement.Token != claim.Token && !staleWon && finalJob.Progress == 55, attestationID, recoveryID)
	return nil
}

func processingJobForRoute(jobID string, route *model.PlatformGenerationProviderRoute, pollToken string) model.PlatformGenerationJob {
	revision := "sha256:" + strings.Repeat("e", 64)
	return model.PlatformGenerationJob{
		ID: jobID, TenantID: uuid.NewString(), SourceClientID: "fault-platform",
		RequestID: "fault-processing-" + jobID, IdempotencyKey: "fault-processing-" + jobID,
		RequestHash: strings.Repeat("f", 64), RequestJSON: `{}`, Model: route.Model, Mode: route.Mode,
		ExpectedCapabilityRevision: revision, CapabilityRevision: revision,
		Status: model.PlatformGenerationStatusProcessing, Progress: 20,
		ProviderRouteID: route.ID, ProviderChannelID: route.ChannelID, ProviderKeyIndex: route.KeyIndex,
		ProviderSubmissionAttempt: 1, PollLeaseToken: pollToken,
		PollLeaseExpiresAt: time.Now().UTC().Add(time.Minute), NextPollAt: time.Now().UTC().Add(-time.Second),
		OutputsJSON: "[]", ErrorDetailsJSON: "{}",
	}
}

func (server *server) scenarioAccountPoolCrossModeShared(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	modelName := "fault.shared-account." + strings.ReplaceAll(uuid.NewString(), "-", "")
	firstRoute, err := server.createFaultProviderRoute("shared-account", modelName, "text_to_video", 10, 2)
	if err != nil {
		return err
	}
	secondRoute, err := server.createFaultRouteForSameAccount(firstRoute, "shared-account-second", modelName, "image_to_video")
	if err != nil {
		return err
	}
	firstJobID := uuid.NewString()
	secondJobID := uuid.NewString()
	firstClaim, err := model.ClaimPlatformGenerationProviderRoute(firstJobID, modelName, "text_to_video")
	if err != nil {
		return err
	}
	secondClaim, err := model.ClaimPlatformGenerationProviderRoute(secondJobID, modelName, "image_to_video")
	if err != nil {
		return err
	}
	_, busyErr := model.ClaimPlatformGenerationProviderRoute(uuid.NewString(), modelName, "image_to_video")
	firstPollToken := uuid.NewString()
	secondPollToken := uuid.NewString()
	if err := server.db.Create(&[]model.PlatformGenerationJob{
		processingJobForRoute(firstJobID, firstRoute, firstPollToken),
		processingJobForRoute(secondJobID, secondRoute, secondPollToken),
	}).Error; err != nil {
		return err
	}
	var firstBefore, secondBefore model.PlatformGenerationProviderRoute
	var sharedState model.PlatformGenerationProviderAccountState
	if err := server.db.First(&firstBefore, firstRoute.ID).Error; err != nil {
		return err
	}
	if err := server.db.First(&secondBefore, secondRoute.ID).Error; err != nil {
		return err
	}
	if err := server.db.First(&sharedState, firstRoute.AccountStateID).Error; err != nil {
		return err
	}
	sharedID := builder.add("postgres_account_pool", "two modes consumed one physical account state and one active-capacity counter", map[string]any{
		"first_route_id": firstRoute.ID, "second_route_id": secondRoute.ID,
		"first_account_state_id": firstRoute.AccountStateID, "second_account_state_id": secondRoute.AccountStateID,
		"shared_rpm_window_count": sharedState.RPMWindowCount, "shared_active_count": sharedState.ActiveCount,
		"first_route_mirror_rpm": firstBefore.RPMWindowCount, "second_route_mirror_rpm": secondBefore.RPMWindowCount,
		"third_claim_error": fmt.Sprint(busyErr), "first_submission_token_sha256": digestText(firstClaim.SubmissionToken),
		"second_submission_token_sha256": digestText(secondClaim.SubmissionToken),
	})
	failureWon, err := model.CompletePlatformGenerationTerminalWithOutcomePolicy(
		firstJobID, firstPollToken, model.PlatformGenerationStatusProcessing,
		map[string]any{"status": model.PlatformGenerationStatusFailed, "progress": 100},
		&model.PlatformProviderTerminalOutcome{
			ID: uuid.NewString(), RouteID: firstRoute.ID, RelayJobID: firstJobID,
			Outcome: model.PlatformProviderOutcomeFailed, FailureOwner: model.PlatformProviderFailureOwnerProvider,
			FailureCode: "fault_account_invalidated", OccurredAt: time.Now().UTC(), ExternalReference: "fault-provider-task-" + firstJobID,
		},
		1, 5*time.Minute,
	)
	if err != nil {
		return err
	}
	var firstCooling, secondCooling model.PlatformGenerationProviderRoute
	if err := server.db.First(&firstCooling, firstRoute.ID).Error; err != nil {
		return err
	}
	if err := server.db.First(&secondCooling, secondRoute.ID).Error; err != nil {
		return err
	}
	_, coolingErr := model.ClaimPlatformGenerationProviderRoute(uuid.NewString(), modelName, "text_to_video")
	existingWon, err := model.CompletePlatformGenerationTerminalWithOutcome(
		secondJobID, secondPollToken, model.PlatformGenerationStatusProcessing,
		map[string]any{"status": model.PlatformGenerationStatusSucceeded, "progress": 100},
		&model.PlatformProviderTerminalOutcome{
			ID: uuid.NewString(), RouteID: secondRoute.ID, RelayJobID: secondJobID,
			Outcome: model.PlatformProviderOutcomeSucceeded, FailureOwner: model.PlatformProviderFailureOwnerNone,
			OccurredAt: time.Now().UTC(), ExternalReference: "fault-provider-task-" + secondJobID,
		},
	)
	if err != nil {
		return err
	}
	var finalSecondJob model.PlatformGenerationJob
	if err := server.db.First(&finalSecondJob, "id = ?", secondJobID).Error; err != nil {
		return err
	}
	cooldownID := builder.add("postgres_account_cooldown", "provider failure cooled the shared account while an existing task still completed on its pinned route", map[string]any{
		"failure_terminal_won": failureWon, "first_cooling_until": firstCooling.CoolingUntil,
		"second_cooling_until": secondCooling.CoolingUntil, "new_claim_error": fmt.Sprint(coolingErr),
		"existing_terminal_won": existingWon, "existing_final_status": finalSecondJob.Status,
	})
	bindAssertion(assertions, mapping, "single_physical_account_state", firstRoute.AccountStateID > 0 && firstRoute.AccountStateID == secondRoute.AccountStateID, attestationID, sharedID)
	bindAssertion(assertions, mapping, "rpm_shared_across_modes", sharedState.RPMWindowCount == 2 && firstBefore.RPMWindowCount == 2 && secondBefore.RPMWindowCount == 2, attestationID, sharedID)
	bindAssertion(assertions, mapping, "active_capacity_shared_across_modes", sharedState.ActiveCount == 2 && errors.Is(busyErr, model.ErrPlatformGenerationProviderRouteBusy), attestationID, sharedID)
	bindAssertion(assertions, mapping, "cooldown_shared_across_modes", failureWon && firstCooling.CoolingUntil != nil && secondCooling.CoolingUntil != nil && firstCooling.CoolingUntil.Equal(*secondCooling.CoolingUntil) && errors.Is(coolingErr, model.ErrPlatformGenerationProviderRouteUnavailable), attestationID, cooldownID)
	bindAssertion(assertions, mapping, "existing_task_polling_unchanged", existingWon && finalSecondJob.Status == model.PlatformGenerationStatusSucceeded, attestationID, cooldownID)
	return nil
}

func (server *server) scenarioPollTimeout(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	modelName := "fault.poll-timeout." + strings.ReplaceAll(uuid.NewString(), "-", "")
	route, err := server.createFaultProviderRoute("poll-timeout", modelName, "text_to_video", 10, 2)
	if err != nil {
		return err
	}
	jobID := uuid.NewString()
	if _, err := model.ClaimPlatformGenerationProviderRoute(jobID, modelName, "text_to_video"); err != nil {
		return err
	}
	pollToken := uuid.NewString()
	job := processingJobForRoute(jobID, route, pollToken)
	if err := server.db.Create(&job).Error; err != nil {
		return err
	}
	endpoint := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		select {
		case <-request.Context().Done():
			return
		case <-time.After(2 * time.Second):
			writer.WriteHeader(http.StatusOK)
		}
	}))
	defer endpoint.Close()
	request, _ := http.NewRequest(http.MethodGet, endpoint.URL, nil)
	response, timeoutErr := (&http.Client{Timeout: 100 * time.Millisecond}).Do(request)
	if response != nil {
		_ = response.Body.Close()
	}
	if timeoutErr == nil {
		return errors.New("poll timeout injection did not time out")
	}
	if err := model.ReleasePlatformGenerationPoll(job.ID, pollToken, 2*time.Second); err != nil {
		return err
	}
	var persisted model.PlatformGenerationJob
	if err := server.db.First(&persisted, "id = ?", job.ID).Error; err != nil {
		return err
	}
	admission, persistedRoute, err := model.GetPlatformGenerationProviderRouteAssignment(job.ID)
	if err != nil {
		return err
	}
	evidenceID := builder.add("provider_poll_timeout", "an actual HTTP timeout released only the poll lease for a local retry", map[string]any{
		"job_id": job.ID, "timeout_error": timeoutErr.Error(), "route_id_before": route.ID,
		"route_id_after": admission.RouteID, "account_active_count": persistedRoute.ActiveCount,
		"job_status": persisted.Status, "provider_submission_attempt": persisted.ProviderSubmissionAttempt,
		"poll_lease_cleared": persisted.PollLeaseToken == "", "next_poll_at_utc": persisted.NextPollAt.UTC().Format(time.RFC3339Nano),
	})
	bindAssertion(assertions, mapping, "route_unchanged", admission.RouteID == route.ID && admission.SlotHeld && persistedRoute.ActiveCount == 1, attestationID, evidenceID)
	bindAssertion(assertions, mapping, "provider_retry_budget_not_consumed", persisted.ProviderSubmissionAttempt == 1, attestationID, evidenceID)
	bindAssertion(assertions, mapping, "terminal_state_not_falsified", persisted.Status == model.PlatformGenerationStatusProcessing && persisted.Progress == 20 && persisted.PollLeaseToken == "", attestationID, evidenceID)
	return nil
}

func (server *server) scenarioArtifactRejection(scenario string, builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	caseName := strings.TrimPrefix(scenario, "artifact_")
	maxBytes := int64(1024)
	if scenario == "artifact_oversize" {
		maxBytes = 8
	}
	downloader, err := service.NewPlatformArtifactDownloader(service.PlatformArtifactDownloadConfig{
		Production: false, MaxBytes: maxBytes, Timeout: 3 * time.Second,
	})
	if err != nil {
		return err
	}
	store := &recordingArtifactStore{}
	job, _, err := createGenerationJob(server.db, caseName)
	if err != nil {
		return err
	}
	sourceURL := "http://artifact-origin:8080/__fault/artifact?case=" + caseName
	if scenario == "artifact_ssrf" {
		sourceURL = "http://127.0.0.1:8080/__fault/artifact?case=ssrf"
	}
	if err := server.db.Model(&model.PlatformGenerationJob{}).Where("id = ?", job.ID).Updates(map[string]any{
		"status": model.PlatformGenerationStatusTransferring, "progress": 95,
		"temporary_result_json": `{"private_provider_url_present":true}`,
	}).Error; err != nil {
		return err
	}
	expectedSHA := ""
	if scenario == "artifact_corruption" {
		// Match the forged body's digest so the production validator must reach
		// media-signature inspection instead of stopping at metadata mismatch.
		expectedSHA = digestText(faultForgedVideoPayload)
	}
	fetchesBefore := server.artifactFetches.Load()
	_, transferErr := service.TransferPlatformProviderArtifact(context.Background(), downloader, store, service.PlatformArtifactTransferRequest{
		SourceURL: sourceURL, TenantID: job.TenantID, JobID: job.ID, AssetID: uuid.NewString(),
		MediaType: "video", ExpectedSHA256: expectedSHA,
	})
	fetchesAfter := server.artifactFetches.Load()
	if transferErr == nil {
		return errors.New("artifact fault injection was accepted")
	}
	var persisted model.PlatformGenerationJob
	if err := server.db.First(&persisted, "id = ?", job.ID).Error; err != nil {
		return err
	}
	rejectedID := builder.add("artifact_transfer_rejection", "production artifact transfer rejected the injected provider artifact before durable publication", map[string]any{
		"scenario": scenario, "source_url_sha256": digestText(sourceURL), "transfer_error": transferErr.Error(),
		"network_fetch_delta": fetchesAfter - fetchesBefore, "artifact_store_put_count": store.puts.Load(),
		"job_status": persisted.Status, "outputs_json": persisted.OutputsJSON,
		"public_upstream_result_url":      persisted.UpstreamResultURL,
		"forged_video_signature_rejected": scenario == "artifact_corruption" && strings.Contains(transferErr.Error(), "no supported media signature"),
		"valid_png_rejected_as_video":     scenario == "artifact_mime_mismatch" && strings.Contains(transferErr.Error(), "MIME type did not match media type"),
	})
	if scenario == "artifact_ssrf" {
		blocked := errors.Is(transferErr, service.ErrPlatformArtifactSecurity)
		bindAssertion(assertions, mapping, "private_or_disallowed_target_blocked", blocked, attestationID, rejectedID)
		bindAssertion(assertions, mapping, "network_fetch_not_attempted", fetchesAfter == fetchesBefore, attestationID, rejectedID)
		bindAssertion(assertions, mapping, "success_not_published", persisted.Status == model.PlatformGenerationStatusTransferring && persisted.OutputsJSON == "[]" && store.puts.Load() == 0, attestationID, rejectedID)
		return nil
	}
	rejected := false
	switch scenario {
	case "artifact_corruption":
		rejected = errors.Is(transferErr, service.ErrPlatformArtifactIntegrity) && strings.Contains(transferErr.Error(), "no supported media signature")
	case "artifact_oversize":
		rejected = errors.Is(transferErr, service.ErrPlatformArtifactTooLarge)
	case "artifact_mime_mismatch":
		rejected = errors.Is(transferErr, service.ErrPlatformArtifactDownload) && strings.Contains(transferErr.Error(), "MIME type did not match media type")
	}
	bindAssertion(assertions, mapping, "artifact_rejected", rejected && fetchesAfter == fetchesBefore+1, attestationID, rejectedID)
	bindAssertion(assertions, mapping, "success_not_published", persisted.Status == model.PlatformGenerationStatusTransferring && persisted.OutputsJSON == "[]" && store.puts.Load() == 0, attestationID, rejectedID)
	bindAssertion(assertions, mapping, "provider_url_not_exposed", persisted.UpstreamResultURL == "" && !strings.Contains(persisted.OutputsJSON, "artifact-origin"), attestationID, rejectedID)
	return nil
}

type callbackObservation struct {
	EventID        string
	Timestamp      string
	Signature      string
	SignatureValid bool
	BodySHA256     string
}

func (server *server) scenarioCallbackRetryDLQ(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	secret := "relay-fault-callback-secret-at-least-32-bytes"
	var requestCount atomic.Int32
	observations := make([]callbackObservation, 0, 2)
	var observationsMu sync.Mutex
	endpoint := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		body, _ := io.ReadAll(request.Body)
		timestamp := request.Header.Get("X-Relay-Timestamp")
		eventID := request.Header.Get("X-Relay-Event-ID")
		timestampValue, parseErr := strconv.ParseInt(timestamp, 10, 64)
		expected := ""
		if parseErr == nil {
			expected, _ = service.SignPlatformGenerationCallback(secret, timestampValue, eventID, body)
		}
		observation := callbackObservation{
			EventID: eventID, Timestamp: timestamp, Signature: request.Header.Get("X-Relay-Signature"),
			SignatureValid: expected != "" && subtle.ConstantTimeCompare([]byte(expected), []byte(request.Header.Get("X-Relay-Signature"))) == 1,
			BodySHA256:     digestText(string(body)),
		}
		observationsMu.Lock()
		observations = append(observations, observation)
		observationsMu.Unlock()
		requestCount.Add(1)
		writer.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer endpoint.Close()

	eventID := uuid.NewString()
	jobID := uuid.NewString()
	revision := "sha256:" + strings.Repeat("c", 64)
	event := dto.PlatformGenerationCallbackEvent{
		APIVersion: dto.PlatformRelayAPIVersion, SchemaVersion: dto.PlatformRelaySchemaVersion,
		EventID: eventID, Type: dto.PlatformGenerationCallbackEventType, OccurredAt: time.Now().UTC(),
		Job: dto.PlatformGenerationCallbackEventJob{
			APIVersion: dto.PlatformRelayAPIVersion, ID: jobID, Status: model.PlatformGenerationStatusProcessing,
			Progress: 50, Outputs: []dto.PlatformGenerationArtifact{}, Error: nil,
			ExpectedCapabilityRevision: revision, CapabilityRevision: revision, ReservationAction: "hold",
		},
	}
	payload, err := service.SerializePlatformGenerationCallbackEvent(event)
	if err != nil {
		return err
	}
	payloadDigest := sha256.Sum256(payload)
	tenantID := uuid.NewString()
	delivery := model.PlatformGenerationCallbackDelivery{
		ID: eventID, TenantID: tenantID, SourceClientID: "fault-platform", JobID: jobID,
		CallbackURL: endpoint.URL, RequestID: "fault-callback-" + eventID,
		PayloadJSON: string(payload), PayloadSHA256: hex.EncodeToString(payloadDigest[:]), MaxAttempts: 2,
	}
	created, err := model.CreatePlatformGenerationCallbackDelivery(&delivery)
	if err != nil || !created {
		return fmt.Errorf("create callback delivery: created=%t: %w", created, err)
	}
	principal := service.PlatformRelayPrincipal{
		ClientID: "fault-platform", TenantID: tenantID, CallbackURL: endpoint.URL, CallbackSecret: secret,
	}
	first, err := model.ClaimPlatformGenerationCallbackDelivery(3 * time.Second)
	if err != nil {
		return err
	}
	countsAfterFirst, err := model.GetPlatformGenerationCallbackCounts()
	if err != nil {
		return err
	}
	firstState, firstWon, err := service.DeliverPlatformGenerationCallbackClaim(context.Background(), *first, principal)
	if err != nil {
		return err
	}
	staleWon, err := model.DeadLetterPlatformGenerationCallbackDelivery(
		first.Delivery.ID, first.Token, model.PlatformGenerationCallbackFailureGeneric, http.StatusServiceUnavailable,
	)
	if err != nil {
		return err
	}
	if err := server.db.Model(&model.PlatformGenerationCallbackDelivery{}).Where("id = ?", eventID).
		Update("available_at", time.Now().UTC().Add(-time.Second)).Error; err != nil {
		return err
	}
	second, err := model.ClaimPlatformGenerationCallbackDelivery(3 * time.Second)
	if err != nil {
		return err
	}
	countsAfterSecond, err := model.GetPlatformGenerationCallbackCounts()
	if err != nil {
		return err
	}
	secondState, secondWon, err := service.DeliverPlatformGenerationCallbackClaim(context.Background(), *second, principal)
	if err != nil {
		return err
	}
	var persisted model.PlatformGenerationCallbackDelivery
	if err := server.db.First(&persisted, "id = ?", eventID).Error; err != nil {
		return err
	}
	observationsMu.Lock()
	observedCopy := append([]callbackObservation(nil), observations...)
	observationsMu.Unlock()
	serializedObservations := make([]map[string]any, 0, len(observedCopy))
	allSignaturesValid := len(observedCopy) == 2
	for _, observation := range observedCopy {
		allSignaturesValid = allSignaturesValid && observation.SignatureValid
		serializedObservations = append(serializedObservations, map[string]any{
			"event_id": observation.EventID, "timestamp": observation.Timestamp,
			"signature_sha256": digestText(observation.Signature), "signature_valid": observation.SignatureValid,
			"body_sha256": observation.BodySHA256,
		})
	}
	deliveryID := builder.add("callback_delivery", "signed callback endpoint returned 503 twice", map[string]any{
		"requests": requestCount.Load(), "observations": serializedObservations,
		"first_state": firstState, "first_transition_won": firstWon,
		"second_state": secondState, "second_transition_won": secondWon,
	})
	claimID := builder.add("callback_claim", "each worker claim owned exactly the one callback item", map[string]any{
		"first_claimed_count": countsAfterFirst.Claimed, "second_claimed_count": countsAfterSecond.Claimed,
		"first_token_sha256": digestText(first.Token), "second_token_sha256": digestText(second.Token),
		"tokens_distinct": first.Token != second.Token,
	})
	dlqID := builder.add("callback_dead_letter", "retry budget exhausted into durable dead letter", map[string]any{
		"state": persisted.State, "attempts": persisted.Attempts, "max_attempts": persisted.MaxAttempts,
		"response_status": persisted.ResponseStatus, "last_error": persisted.LastError,
		"dead_lettered_at_utc": func() any {
			if persisted.DeadLetteredAt == nil {
				return nil
			}
			return persisted.DeadLetteredAt.UTC().Format(time.RFC3339Nano)
		}(),
		"stale_first_token_transition_won": staleWon,
	})
	bindAssertion(assertions, mapping, "signature_verified", allSignaturesValid, attestationID, deliveryID)
	bindAssertion(assertions, mapping, "at_least_once_retry_observed", requestCount.Load() == 2 && firstState == model.PlatformGenerationCallbackPending && firstWon, attestationID, deliveryID)
	bindAssertion(assertions, mapping, "dead_letter_observed", persisted.State == model.PlatformGenerationCallbackDeadLetter && persisted.Attempts == 2 && secondState == model.PlatformGenerationCallbackDeadLetter && secondWon, attestationID, dlqID)
	bindAssertion(assertions, mapping, "single_item_claimed", countsAfterFirst.Claimed == 1 && countsAfterSecond.Claimed == 1, attestationID, claimID)
	bindAssertion(assertions, mapping, "stale_delivery_token_fenced", !staleWon && first.Token != second.Token, attestationID, claimID, dlqID)
	return nil
}

func (server *server) acquireFreshMonitorLease(label string) (*model.PlatformProviderMonitorLeaseClaim, *model.PlatformProviderMonitorLeaseClaim, time.Time, error) {
	if err := server.db.Exec("UPDATE platform_provider_monitor_leases SET expires_at = clock_timestamp() - interval '1 second'").Error; err != nil {
		return nil, nil, time.Time{}, err
	}
	stale, err := model.ClaimPlatformProviderMonitorLease("fault-stale-"+label, 5*time.Second)
	if err != nil {
		return nil, nil, time.Time{}, err
	}
	if err := server.db.Exec("UPDATE platform_provider_monitor_leases SET expires_at = clock_timestamp() - interval '1 second'").Error; err != nil {
		return nil, nil, time.Time{}, err
	}
	fresh, err := model.ClaimPlatformProviderMonitorLease("fault-current-"+label, 30*time.Second)
	if err != nil {
		return nil, nil, time.Time{}, err
	}
	var databaseTime time.Time
	if err := server.db.Transaction(func(tx *gorm.DB) error {
		var clockErr error
		databaseTime, clockErr = model.GetDBTimeTx(tx)
		return clockErr
	}); err != nil {
		return nil, nil, time.Time{}, err
	}
	return stale, fresh, databaseTime, nil
}

func decisionsForProvider(decisions []model.PlatformProviderIncidentDecision, providerName string, kind string) []model.PlatformProviderIncidentDecision {
	filtered := make([]model.PlatformProviderIncidentDecision, 0, 1)
	for _, decision := range decisions {
		if decision.ProviderName == providerName && decision.Kind == kind {
			filtered = append(filtered, decision)
		}
	}
	return filtered
}

type providerAlertObservation struct {
	EventID         string `json:"event_id"`
	SignatureSHA256 string `json:"signature_sha256"`
	SignatureValid  bool   `json:"signature_valid"`
	BodySHA256      string `json:"body_sha256"`
}

func (server *server) deliverProviderAlerts(providerName string, expected int) ([]providerAlertObservation, error) {
	secret := "fault-provider-alert-signing-secret-at-least-32-bytes"
	observations := make([]providerAlertObservation, 0, expected)
	var observationsMu sync.Mutex
	endpoint := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		body, _ := io.ReadAll(request.Body)
		timestamp, parseErr := strconv.ParseInt(request.Header.Get("X-Relay-Timestamp"), 10, 64)
		eventID := request.Header.Get("X-Relay-Event-ID")
		expectedSignature := ""
		if parseErr == nil {
			expectedSignature, _ = service.SignPlatformRelayExternalEvent(secret, timestamp, eventID, body)
		}
		actualSignature := request.Header.Get("X-Relay-Signature")
		observationsMu.Lock()
		observations = append(observations, providerAlertObservation{
			EventID: eventID, SignatureSHA256: digestText(actualSignature),
			SignatureValid: expectedSignature != "" && subtle.ConstantTimeCompare([]byte(expectedSignature), []byte(actualSignature)) == 1,
			BodySHA256:     digestText(string(body)),
		})
		observationsMu.Unlock()
		writer.WriteHeader(http.StatusNoContent)
	}))
	defer endpoint.Close()
	for index := 0; index < expected; index++ {
		claim, err := model.ClaimPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindProviderAlert, 10*time.Second)
		if err != nil {
			return observations, err
		}
		event, err := model.GetPlatformProviderAlertEvent(claim.Delivery.EventID)
		if err != nil {
			return observations, err
		}
		if event.ProviderName != providerName {
			return observations, fmt.Errorf("claimed provider alert for %s instead of %s", event.ProviderName, providerName)
		}
		state, won, err := service.DeliverPlatformProviderAlertClaim(context.Background(), *claim, service.PlatformProviderAlertSinkConfig{
			URL: endpoint.URL, SigningSecret: secret, Production: false,
		})
		if err != nil || !won || state != model.PlatformRelayDeliveryDelivered {
			return observations, fmt.Errorf("deliver provider alert: state=%s won=%t: %w", state, won, err)
		}
	}
	return observations, nil
}

func allProviderAlertSignaturesValid(observations []providerAlertObservation, expected int) bool {
	if len(observations) != expected {
		return false
	}
	for _, observation := range observations {
		if !observation.SignatureValid {
			return false
		}
	}
	return true
}

func (server *server) scenarioProviderSuccessRateDropRecovery(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	providerName := "fault-success-rate-" + strings.ReplaceAll(uuid.NewString(), "-", "")[:12]
	modelName := "fault.monitor.success." + strings.ReplaceAll(uuid.NewString(), "-", "")
	route, err := server.createFaultProviderRoute(providerName, modelName, "text_to_video", 100, 10)
	if err != nil {
		return err
	}
	// Keep the route's provider identity scenario-specific even though the helper label is also unique.
	providerName = route.ProviderName
	for index := 0; index < 4; index++ {
		outcome := model.PlatformProviderOutcomeFailed
		owner := model.PlatformProviderFailureOwnerProvider
		failureCode := "provider_timeout"
		if index == 0 {
			outcome = model.PlatformProviderOutcomeSucceeded
			owner = model.PlatformProviderFailureOwnerNone
			failureCode = ""
		}
		created, err := model.CreatePlatformProviderTerminalOutcome(&model.PlatformProviderTerminalOutcome{
			ID: uuid.NewString(), RouteID: route.ID, RelayJobID: uuid.NewString(), Outcome: outcome,
			FailureOwner: owner, FailureCode: failureCode, OccurredAt: time.Now().UTC(),
			ExternalReference: fmt.Sprintf("fault-success-rate-initial-%d-%s", index, uuid.NewString()),
		})
		if err != nil || !created {
			return fmt.Errorf("record success-rate outcome: created=%t: %w", created, err)
		}
	}
	policy := service.DefaultPlatformProviderMonitorPolicy()
	policy.SuccessRateMinimumSamples = 4
	policy.SuccessRateTriggerBasisPoints = 8000
	policy.SuccessRateRecoveryBasisPoints = 8500
	snapshot, err := model.GetPlatformProviderMonitorEvaluationSnapshot(policy.OutcomeLookback)
	if err != nil {
		return err
	}
	dropDecisions, err := service.EvaluatePlatformProviderMonitorSnapshot(snapshot, policy)
	if err != nil {
		return err
	}
	dropDecisions = decisionsForProvider(dropDecisions, providerName, model.PlatformProviderIncidentSuccessRateDrop)
	if len(dropDecisions) != 1 || !dropDecisions[0].DesiredActive {
		return errors.New("success-rate drop was not detected")
	}
	stale, fresh, databaseTime, err := server.acquireFreshMonitorLease("success-rate")
	if err != nil {
		return err
	}
	staleErr := model.ApplyPlatformProviderIncidentDecisions(stale.Token, append([]model.PlatformProviderIncidentDecision(nil), dropDecisions...))
	if err := model.ApplyPlatformProviderIncidentDecisions(fresh.Token, append([]model.PlatformProviderIncidentDecision(nil), dropDecisions...)); err != nil {
		return err
	}
	if err := model.ApplyPlatformProviderIncidentDecisions(fresh.Token, append([]model.PlatformProviderIncidentDecision(nil), dropDecisions...)); err != nil {
		return err
	}
	var triggeredCount int64
	if err := server.db.Model(&model.PlatformProviderAlertEvent{}).Where("provider_name = ? AND incident_kind = ? AND incident_state = ?", providerName, model.PlatformProviderIncidentSuccessRateDrop, model.PlatformProviderIncidentTriggered).Count(&triggeredCount).Error; err != nil {
		return err
	}
	for index := 0; index < 20; index++ {
		created, err := model.CreatePlatformProviderTerminalOutcome(&model.PlatformProviderTerminalOutcome{
			ID: uuid.NewString(), RouteID: route.ID, RelayJobID: uuid.NewString(), Outcome: model.PlatformProviderOutcomeSucceeded,
			FailureOwner: model.PlatformProviderFailureOwnerNone, OccurredAt: time.Now().UTC(),
			ExternalReference: fmt.Sprintf("fault-success-rate-recovery-%d-%s", index, uuid.NewString()),
		})
		if err != nil || !created {
			return fmt.Errorf("record recovery outcome: created=%t: %w", created, err)
		}
	}
	snapshot, err = model.GetPlatformProviderMonitorEvaluationSnapshot(policy.OutcomeLookback)
	if err != nil {
		return err
	}
	recoveryDecisions, err := service.EvaluatePlatformProviderMonitorSnapshot(snapshot, policy)
	if err != nil {
		return err
	}
	recoveryDecisions = decisionsForProvider(recoveryDecisions, providerName, model.PlatformProviderIncidentSuccessRateDrop)
	if len(recoveryDecisions) != 1 || recoveryDecisions[0].DesiredActive {
		return errors.New("success-rate recovery was not detected")
	}
	if err := model.ApplyPlatformProviderIncidentDecisions(fresh.Token, append([]model.PlatformProviderIncidentDecision(nil), recoveryDecisions...)); err != nil {
		return err
	}
	if err := model.ApplyPlatformProviderIncidentDecisions(fresh.Token, append([]model.PlatformProviderIncidentDecision(nil), recoveryDecisions...)); err != nil {
		return err
	}
	var recoveredCount int64
	if err := server.db.Model(&model.PlatformProviderAlertEvent{}).Where("provider_name = ? AND incident_kind = ? AND incident_state = ?", providerName, model.PlatformProviderIncidentSuccessRateDrop, model.PlatformProviderIncidentRecovered).Count(&recoveredCount).Error; err != nil {
		return err
	}
	completed, err := model.CompletePlatformProviderMonitorCycle(fresh.Token, "")
	if err != nil {
		return err
	}
	observations, err := server.deliverProviderAlerts(providerName, 2)
	if err != nil {
		return err
	}
	leaseID := builder.add("provider_monitor_lease", "database-clock lease replacement fenced an expired monitor owner", map[string]any{
		"database_time_utc": databaseTime.UTC().Format(time.RFC3339Nano), "fresh_expires_at_utc": fresh.ExpiresAt.UTC().Format(time.RFC3339Nano),
		"lease_seconds_from_database_sample": fresh.ExpiresAt.Sub(databaseTime).Seconds(),
		"stale_token_sha256":                 digestText(stale.Token), "fresh_token_sha256": digestText(fresh.Token),
		"stale_apply_error": fmt.Sprint(staleErr), "monitor_cycle_completed": completed,
	})
	transitionID := builder.add("provider_monitor_transitions", "append-only outcomes triggered and recovered one deduplicated success-rate incident", map[string]any{
		"drop_decision": dropDecisions[0], "recovery_decision": recoveryDecisions[0],
		"triggered_alert_count": triggeredCount, "recovered_alert_count": recoveredCount,
		"signed_delivery_observations": observations,
	})
	bindAssertion(assertions, mapping, "database_clock_lease_used", fresh.ExpiresAt.After(databaseTime) && fresh.ExpiresAt.Sub(databaseTime) <= 31*time.Second, attestationID, leaseID)
	bindAssertion(assertions, mapping, "stale_monitor_token_fenced", errors.Is(staleErr, model.ErrPlatformProviderMonitorLeaseLost), attestationID, leaseID)
	bindAssertion(assertions, mapping, "drop_transition_deduplicated", triggeredCount == 1, attestationID, transitionID)
	bindAssertion(assertions, mapping, "recovery_transition_deduplicated", recoveredCount == 1, attestationID, transitionID)
	bindAssertion(assertions, mapping, "signed_alert_delivery_observed", allProviderAlertSignaturesValid(observations, 2), attestationID, transitionID)
	return nil
}

func (server *server) scenarioProviderWidespreadFailure(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	providerName := "fault-widespread-" + strings.ReplaceAll(uuid.NewString(), "-", "")[:12]
	policy := service.DefaultPlatformProviderMonitorPolicy()
	policy.WidespreadMinimumRoutes = 3
	policy.WidespreadMinimumAffectedRoutes = 2
	snapshot := model.PlatformProviderMonitorEvaluationSnapshot{DatabaseTime: time.Now().UTC(), Health: []model.PlatformProviderRouteHealth{
		{RouteID: 81001, ProviderName: providerName, Status: model.PlatformProviderRouteHealthFailed},
		{RouteID: 81002, ProviderName: providerName, Status: model.PlatformProviderRouteHealthFailed},
		{RouteID: 81003, ProviderName: providerName, Status: model.PlatformProviderRouteHealthHealthy},
	}}
	decisions, err := service.EvaluatePlatformProviderMonitorSnapshot(snapshot, policy)
	if err != nil {
		return err
	}
	trigger := decisionsForProvider(decisions, providerName, model.PlatformProviderIncidentWidespreadFailure)
	if len(trigger) != 1 || !trigger[0].DesiredActive {
		return errors.New("widespread route failure was not detected")
	}
	_, fresh, _, err := server.acquireFreshMonitorLease("widespread")
	if err != nil {
		return err
	}
	if err := model.ApplyPlatformProviderIncidentDecisions(fresh.Token, append([]model.PlatformProviderIncidentDecision(nil), trigger...)); err != nil {
		return err
	}
	if err := model.ApplyPlatformProviderIncidentDecisions(fresh.Token, append([]model.PlatformProviderIncidentDecision(nil), trigger...)); err != nil {
		return err
	}
	missingSnapshot := model.PlatformProviderMonitorEvaluationSnapshot{DatabaseTime: time.Now().UTC(), Incidents: []model.PlatformProviderIncident{{
		ProviderName: providerName, Kind: model.PlatformProviderIncidentWidespreadFailure, Active: true,
	}}}
	missingDecisions, err := service.EvaluatePlatformProviderMonitorSnapshot(missingSnapshot, policy)
	if err != nil {
		return err
	}
	missing := decisionsForProvider(missingDecisions, providerName, model.PlatformProviderIncidentWidespreadFailure)
	if len(missing) != 1 {
		return errors.New("missing-probe evaluation omitted active incident")
	}
	if err := model.ApplyPlatformProviderIncidentDecisions(fresh.Token, append([]model.PlatformProviderIncidentDecision(nil), missing...)); err != nil {
		return err
	}
	var alertCount int64
	if err := server.db.Model(&model.PlatformProviderAlertEvent{}).Where("provider_name = ? AND incident_kind = ?", providerName, model.PlatformProviderIncidentWidespreadFailure).Count(&alertCount).Error; err != nil {
		return err
	}
	observations, err := server.deliverProviderAlerts(providerName, 1)
	if err != nil {
		return err
	}
	evidenceID := builder.add("provider_monitor_widespread", "two failed routes triggered one alert and absence of probes did not manufacture recovery", map[string]any{
		"trigger_decision": trigger[0], "missing_probe_decision": missing[0], "alert_event_count": alertCount,
		"signed_delivery_observations": observations,
	})
	bindAssertion(assertions, mapping, "widespread_trigger_deduplicated", alertCount == 1, attestationID, evidenceID)
	bindAssertion(assertions, mapping, "missing_probe_not_recovery", missing[0].DesiredActive && missing[0].ReasonCode == "no_explicit_route_recovery", attestationID, evidenceID)
	bindAssertion(assertions, mapping, "signed_alert_delivery_observed", allProviderAlertSignaturesValid(observations, 1), attestationID, evidenceID)
	return nil
}

func (server *server) scenarioProviderBatchInvalidation(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	providerName := "fault-batch-" + strings.ReplaceAll(uuid.NewString(), "-", "")[:12]
	policy := service.DefaultPlatformProviderMonitorPolicy()
	policy.BatchInvalidationMinimumRoutes = 2
	snapshot := model.PlatformProviderMonitorEvaluationSnapshot{DatabaseTime: time.Now().UTC(), Health: []model.PlatformProviderRouteHealth{
		{RouteID: 82001, ProviderName: providerName, Status: model.PlatformProviderRouteHealthInvalidated, FailureProviderCaused: true},
		{RouteID: 82002, ProviderName: providerName, Status: model.PlatformProviderRouteHealthInvalidated, FailureProviderCaused: true},
		{RouteID: 82003, ProviderName: providerName, Status: model.PlatformProviderRouteHealthInvalidated, FailureProviderCaused: false},
	}}
	decisions, err := service.EvaluatePlatformProviderMonitorSnapshot(snapshot, policy)
	if err != nil {
		return err
	}
	trigger := decisionsForProvider(decisions, providerName, model.PlatformProviderIncidentBatchInvalidation)
	if len(trigger) != 1 || !trigger[0].DesiredActive {
		return errors.New("provider-caused batch invalidation was not detected")
	}
	clientOnlySnapshot := model.PlatformProviderMonitorEvaluationSnapshot{DatabaseTime: time.Now().UTC(), Health: []model.PlatformProviderRouteHealth{
		{RouteID: 82011, ProviderName: providerName + "-client", Status: model.PlatformProviderRouteHealthInvalidated, FailureProviderCaused: false},
		{RouteID: 82012, ProviderName: providerName + "-client", Status: model.PlatformProviderRouteHealthInvalidated, FailureProviderCaused: false},
	}}
	clientDecisions, err := service.EvaluatePlatformProviderMonitorSnapshot(clientOnlySnapshot, policy)
	if err != nil {
		return err
	}
	clientOnly := decisionsForProvider(clientDecisions, providerName+"-client", model.PlatformProviderIncidentBatchInvalidation)
	_, fresh, _, err := server.acquireFreshMonitorLease("batch")
	if err != nil {
		return err
	}
	if err := model.ApplyPlatformProviderIncidentDecisions(fresh.Token, append([]model.PlatformProviderIncidentDecision(nil), trigger...)); err != nil {
		return err
	}
	if err := model.ApplyPlatformProviderIncidentDecisions(fresh.Token, append([]model.PlatformProviderIncidentDecision(nil), trigger...)); err != nil {
		return err
	}
	var alertCount int64
	if err := server.db.Model(&model.PlatformProviderAlertEvent{}).Where("provider_name = ? AND incident_kind = ?", providerName, model.PlatformProviderIncidentBatchInvalidation).Count(&alertCount).Error; err != nil {
		return err
	}
	observations, err := server.deliverProviderAlerts(providerName, 1)
	if err != nil {
		return err
	}
	evidenceID := builder.add("provider_monitor_batch_invalidation", "only provider-caused invalidations formed one deduplicated batch incident", map[string]any{
		"trigger_decision": trigger[0], "client_only_decisions": clientOnly,
		"alert_event_count": alertCount, "signed_delivery_observations": observations,
	})
	bindAssertion(assertions, mapping, "provider_caused_only", trigger[0].AffectedRoutes == 2 && (len(clientOnly) == 0 || !clientOnly[0].DesiredActive), attestationID, evidenceID)
	bindAssertion(assertions, mapping, "batch_trigger_deduplicated", alertCount == 1, attestationID, evidenceID)
	bindAssertion(assertions, mapping, "signed_alert_delivery_observed", allProviderAlertSignaturesValid(observations, 1), attestationID, evidenceID)
	return nil
}

func (server *server) scenarioProviderAlertRetryDLQ(builder *evidenceBuilder, attestationID string, assertions map[string]bool, mapping map[string][]string) error {
	providerName := "fault-alert-dlq-" + strings.ReplaceAll(uuid.NewString(), "-", "")[:12]
	_, fresh, _, err := server.acquireFreshMonitorLease("alert-dlq")
	if err != nil {
		return err
	}
	decision := model.PlatformProviderIncidentDecision{
		ProviderName: providerName, Kind: model.PlatformProviderIncidentSuccessRateDrop, DesiredActive: true,
		ReasonCode: "success_rate_below_threshold", SampleSize: 20, SuccessCount: 5, SuccessRateBasisPoints: 2500,
	}
	if err := model.ApplyPlatformProviderIncidentDecisions(fresh.Token, []model.PlatformProviderIncidentDecision{decision}); err != nil {
		return err
	}
	var event model.PlatformProviderAlertEvent
	if err := server.db.Where("provider_name = ? AND incident_kind = ?", providerName, model.PlatformProviderIncidentSuccessRateDrop).First(&event).Error; err != nil {
		return err
	}
	if err := server.db.Model(&model.PlatformRelayExternalDelivery{}).Where("event_kind = ? AND event_id = ?", model.PlatformRelayDeliveryKindProviderAlert, event.ID).Update("max_attempts", 2).Error; err != nil {
		return err
	}
	secret := "fault-provider-alert-dlq-secret-at-least-32-bytes"
	var requests atomic.Int32
	var signaturesValid atomic.Bool
	signaturesValid.Store(true)
	endpoint := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		body, _ := io.ReadAll(request.Body)
		timestamp, parseErr := strconv.ParseInt(request.Header.Get("X-Relay-Timestamp"), 10, 64)
		expected := ""
		if parseErr == nil {
			expected, _ = service.SignPlatformRelayExternalEvent(secret, timestamp, request.Header.Get("X-Relay-Event-ID"), body)
		}
		if expected == "" || subtle.ConstantTimeCompare([]byte(expected), []byte(request.Header.Get("X-Relay-Signature"))) != 1 {
			signaturesValid.Store(false)
		}
		requests.Add(1)
		writer.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer endpoint.Close()
	config := service.PlatformProviderAlertSinkConfig{URL: endpoint.URL, SigningSecret: secret, Production: false}
	first, err := model.ClaimPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindProviderAlert, 10*time.Second)
	if err != nil {
		return err
	}
	countsAfterFirst, err := model.GetPlatformRelayDeliveryCounts(model.PlatformRelayDeliveryKindProviderAlert)
	if err != nil {
		return err
	}
	firstState, firstWon, err := service.DeliverPlatformProviderAlertClaim(context.Background(), *first, config)
	if err != nil {
		return err
	}
	staleWon, err := model.DeadLetterPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindProviderAlert, first.Delivery.EventID, first.Token, model.PlatformRelayDeliveryFailureGeneric, http.StatusServiceUnavailable)
	if err != nil {
		return err
	}
	if err := server.db.Model(&model.PlatformRelayExternalDelivery{}).Where("event_kind = ? AND event_id = ?", model.PlatformRelayDeliveryKindProviderAlert, event.ID).
		Update("available_at", gorm.Expr("clock_timestamp() - interval '1 second'")).Error; err != nil {
		return err
	}
	second, err := model.ClaimPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindProviderAlert, 10*time.Second)
	if err != nil {
		return err
	}
	countsAfterSecond, err := model.GetPlatformRelayDeliveryCounts(model.PlatformRelayDeliveryKindProviderAlert)
	if err != nil {
		return err
	}
	secondState, secondWon, err := service.DeliverPlatformProviderAlertClaim(context.Background(), *second, config)
	if err != nil {
		return err
	}
	var persisted model.PlatformRelayExternalDelivery
	if err := server.db.Where("event_kind = ? AND event_id = ?", model.PlatformRelayDeliveryKindProviderAlert, event.ID).First(&persisted).Error; err != nil {
		return err
	}
	completed, err := model.CompletePlatformProviderMonitorCycle(fresh.Token, "")
	if err != nil {
		return err
	}
	readiness, err := service.GetPlatformProviderMonitorReadiness(true, time.Minute)
	if err != nil {
		return err
	}
	deliveryID := builder.add("provider_alert_delivery", "signed provider alert endpoint returned 503 twice under two distinct fenced claims", map[string]any{
		"event_id": event.ID, "request_count": requests.Load(), "signatures_valid": signaturesValid.Load(),
		"first_state": firstState, "first_transition_won": firstWon, "second_state": secondState, "second_transition_won": secondWon,
		"first_token_sha256": digestText(first.Token), "second_token_sha256": digestText(second.Token),
		"stale_first_token_transition_won": staleWon,
		"claimed_after_first":              countsAfterFirst.Claimed, "claimed_after_second": countsAfterSecond.Claimed,
	})
	dlqID := builder.add("provider_alert_dead_letter", "retry budget exhausted into durable provider-alert dead letter and readiness exposed the backlog", map[string]any{
		"state": persisted.State, "attempts": persisted.Attempts, "max_attempts": persisted.MaxAttempts,
		"dead_lettered_at_utc": func() any {
			if persisted.DeadLetteredAt == nil {
				return nil
			}
			return persisted.DeadLetteredAt.UTC().Format(time.RFC3339Nano)
		}(),
		"monitor_cycle_completed": completed, "readiness_fresh": readiness.Fresh,
		"readiness_alert_dead_letter": readiness.AlertDeadLetter, "readiness_degraded": readiness.Degraded,
	})
	bindAssertion(assertions, mapping, "at_least_once_retry_observed", requests.Load() == 2 && signaturesValid.Load() && firstState == model.PlatformRelayDeliveryPending && firstWon, attestationID, deliveryID)
	bindAssertion(assertions, mapping, "dead_letter_observed", persisted.State == model.PlatformRelayDeliveryDeadLetter && persisted.Attempts == 2 && secondState == model.PlatformRelayDeliveryDeadLetter && secondWon, attestationID, dlqID)
	bindAssertion(assertions, mapping, "single_item_claimed", countsAfterFirst.Claimed == 1 && countsAfterSecond.Claimed == 1, attestationID, deliveryID)
	bindAssertion(assertions, mapping, "stale_delivery_token_fenced", !staleWon && first.Token != second.Token, attestationID, deliveryID)
	bindAssertion(assertions, mapping, "readiness_backlog_reported", completed && readiness.Fresh && readiness.AlertDeadLetter >= 1 && readiness.Degraded, attestationID, dlqID)
	return nil
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "--healthcheck" {
		response, err := http.Get("http://127.0.0.1:" + func() string {
			if os.Getenv("PORT") == "" {
				return "8080"
			}
			return os.Getenv("PORT")
		}() + "/health")
		if err != nil || response.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		_ = response.Body.Close()
		return
	}
	if len(os.Args) == 3 && os.Args[1] == "--claim-worker" {
		outboxID, err := strconv.ParseInt(os.Args[2], 10, 64)
		if err != nil || outboxID <= 0 {
			fmt.Fprintln(os.Stderr, "invalid outbox id")
			os.Exit(2)
		}
		if err := runClaimWorker(outboxID); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	loaded, err := loadConfig()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	database, err := openDatabase(loaded.DatabaseURL)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	migrationModels := []any{
		&model.Channel{}, &model.Task{},
		&model.PlatformGenerationJob{}, &model.PlatformGenerationOutbox{},
		&model.PlatformGenerationProviderAccountState{}, &model.PlatformGenerationProviderRoute{},
		&model.PlatformGenerationRouteAdmission{}, &model.PlatformGenerationCallbackDelivery{},
		&faultProviderEffect{},
	}
	migrationModels = append(migrationModels, model.PlatformProviderMonitorAndCostModels()...)
	if err := database.AutoMigrate(migrationModels...); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	redisClient, err := openRedis(loaded.RedisURL)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer redisClient.Close()
	control := &server{
		config: loaded, db: database, redis: redisClient,
		runs: make(map[string]*runResult), execution: make(chan struct{}, 1),
		bridge:   &bridgeController{mode: "normal_ack", effects: make(map[string][]bridgeEffect)},
		liveRuns: make(map[string]*liveRunResult),
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet {
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		writeJSON(writer, http.StatusOK, map[string]any{"state": "healthy", "candidate": candidateFor(loaded)})
	})
	mux.HandleFunc("/v1/relay-fault-injections", func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		control.handleStart(writer, request)
	})
	mux.HandleFunc("/v1/candidate-provenance", func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet {
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		control.handleCandidateProvenance(writer, request)
	})
	mux.HandleFunc("/v1/relay-fault-injections/", func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet {
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		control.handleResult(writer, request)
	})
	mux.HandleFunc("/internal/platform-generations/native-submit", control.handleNativeBridge)
	mux.HandleFunc("/__fault/artifact", control.handleArtifactOrigin)
	mux.HandleFunc("/v1/live-setup", func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		control.handleLiveSetup(writer, request)
	})
	mux.HandleFunc("/v1/live-fault-runs", func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		control.handleLiveStart(writer, request)
	})
	mux.HandleFunc("/v1/live-fault-runs/", func(writer http.ResponseWriter, request *http.Request) {
		suffix := strings.TrimPrefix(request.URL.Path, "/v1/live-fault-runs/")
		if strings.HasSuffix(suffix, "/resume-after-kill") {
			if request.Method != http.MethodPost {
				writer.WriteHeader(http.StatusMethodNotAllowed)
				return
			}
			control.handleLiveResumeAfterKill(writer, request, strings.TrimSuffix(suffix, "/resume-after-kill"))
			return
		}
		if request.Method != http.MethodGet {
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		control.handleLiveResult(writer, request, suffix)
	})
	httpServer := &http.Server{Addr: ":" + loaded.Port, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	if err := httpServer.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
