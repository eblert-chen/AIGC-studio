package toc

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"image"
	"image/png"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/bytedance/gopkg/util/gopool"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

type fakeExecutor struct {
	db         *gorm.DB
	evidence   ModelEvidence
	failCreate bool
}

func (f *fakeExecutor) Evidence(_ *gorm.DB, _ string) (ModelEvidence, error) { return f.evidence, nil }
func (f *fakeExecutor) Create(_ context.Context, tx *gorm.DB, task Task, generation dto.PlatformGenerationRequest, c CatalogModel) error {
	generation.ExecutionContract = &dto.PlatformGenerationExecutionContract{SchemaVersion: 1, RoutingReleaseSHA256: c.RoutingReleaseSHA256, ProviderCostReadinessSHA256: c.CostReadinessSHA256}
	job := model.PlatformGenerationJob{ID: task.ID, TenantID: model.RelayTOCGenerationTenantID, SourceClientID: model.RelayTOCGenerationClientID, IdempotencyKey: task.ID, RequestJSON: fixtureJSON(generation), RequestHash: task.RequestSHA256, Model: c.PublicModelID, Mode: generation.Mode, ExpectedCapabilityRevision: c.CapabilityRevision, CapabilityRevision: c.CapabilityRevision, Status: "queued", OutputsJSON: "[]", CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	if err := tx.Create(&job).Error; err != nil {
		return err
	}
	a := model.RelayGenerationAuthority{JobID: task.ID, Kind: model.RelayGenerationAuthorityTOC, OwnerUserID: task.UserID, AdmissionID: task.AdmissionID, TenantID: job.TenantID, SourceClientID: job.SourceClientID, BillingPolicyRevision: model.RelayTOCBillingPolicyRevision, CreatedAt: time.Now().UTC()}
	if err := tx.Create(&a).Error; err != nil {
		return err
	}
	if f.failCreate {
		return errors.New("isolated executor persistence failure")
	}
	return ValidateAdmission(tx, &job)
}
func (f *fakeExecutor) Snapshot(_ context.Context, task Task) (dto.PlatformGenerationSnapshot, error) {
	var job model.PlatformGenerationJob
	err := f.db.First(&job, "id = ?", task.ID).Error
	var outputs []dto.PlatformGenerationArtifact
	_ = common.Unmarshal([]byte(job.OutputsJSON), &outputs)
	return dto.PlatformGenerationSnapshot{ID: job.ID, Status: job.Status, Outputs: outputs}, err
}
func (f *fakeExecutor) Download(_ context.Context, _ Task, _ string) (dto.PlatformSignedDownload, error) {
	return dto.PlatformSignedDownload{}, errors.New("fixture does not claim a stored output")
}

func fixture(t *testing.T) (*Service, *fakeExecutor) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:toc-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	sqlDB.SetMaxOpenConns(1)
	t.Cleanup(func() { _ = sqlDB.Close() })
	require.NoError(t, db.AutoMigrate(&model.User{}, &model.UserSession{}, &model.Log{}, &model.PlatformGenerationJob{}, &model.PlatformGenerationRouteAdmission{}, &model.PlatformGenerationReconciliationEvent{}, &model.PlatformProviderTerminalOutcome{}))
	require.NoError(t, model.MigrateRelayTOCSchemaV12(db))
	for id := 1; id <= 3; id++ {
		role := common.RoleCommonUser
		if id == 1 {
			role = common.RoleRootUser
		}
		require.NoError(t, db.Create(&model.User{Id: id, Username: fmt.Sprintf("toc-user-%d", id), DisplayName: fmt.Sprintf("User %d", id), Password: "isolated-no-password", Role: role, Status: common.UserStatusEnabled, Group: "default", AffCode: fmt.Sprintf("toc-aff-%d", id), AuthVersion: 1}).Error)
	}
	sha := func(letter string) string { return "sha256:" + strings.Repeat(letter, 64) }
	caps := dto.PlatformGenerationCapabilities{SchemaVersion: 3, Modes: map[string]dto.PlatformModeCapability{}}
	for _, name := range []string{"text_to_image", "image_to_image"} {
		cap := dto.PlatformModeCapability{InputMediaTypes: []string{}, InputRoles: []string{}, TemporalControls: []string{}, StructuredInputs: []string{}, RequiredResourceKeys: []string{}, Limits: dto.PlatformCapabilityLimits{MaxPromptLength: 1000, AspectRatios: []string{"1:1"}, Resolutions: []string{"2k"}, OutputCounts: []int{1}}}
		if name == "image_to_image" {
			cap.InputMediaTypes = []string{"image"}
			cap.InputRoles = []string{"reference_image"}
			cap.Limits.MaxImages = 1
		}
		caps.Modes[name] = cap
	}
	executor := &fakeExecutor{db: db, evidence: ModelEvidence{Resource: dto.PlatformModelResource{ID: "seedream-5", CapabilityRevision: sha("a"), ManagedRoute: true, CustomerCallable: true, Capabilities: caps}, CatalogRevision: sha("b"), RoutingReleaseSHA256: sha("c"), CostReadinessSHA256: sha("d"), Ready: true, Blockers: []string{}}}
	assets, err := NewAssetStore(t.TempDir(), "http://127.0.0.1:14340", strings.Repeat("s", 40))
	require.NoError(t, err)
	return &Service{DB: db, Executor: executor, Assets: assets}, executor
}
func configureOffer(t *testing.T, s *Service, f *fakeExecutor) (CatalogModel, CreateTaskRequest) {
	t.Helper()
	e := f.evidence
	m, err := s.SaveCatalog(context.Background(), 1, "seedream-5", CatalogRequest{Enabled: true, UnitPricePoints: 4, ExpectedCapabilityRevision: e.Resource.CapabilityRevision, ExpectedCatalogRevision: e.CatalogRevision, ExpectedRoutingSHA256: e.RoutingReleaseSHA256, ExpectedCostSHA256: e.CostReadinessSHA256, IdempotencyKey: uuid.NewString(), Reason: "isolated reviewed test offer"})
	require.NoError(t, err)
	_, err = s.SaveGrant(1, 2, GrantRequest{ModelID: m.ID, Enabled: true, IdempotencyKey: uuid.NewString(), Reason: "isolated personal grant"})
	require.NoError(t, err)
	_, err = s.Credit(1, 2, CreditRequest{AmountPoints: 20, IdempotencyKey: uuid.NewString(), Note: "explicit test credit only"})
	require.NoError(t, err)
	rows, err := s.Models(2, false)
	require.NoError(t, err)
	require.Len(t, rows, 1)
	return m, CreateTaskRequest{ModelID: m.ID, IdempotencyKey: uuid.NewString(), ExpectedCapabilityVersion: m.Version, ExpectedQuoteRevision: rows[0]["quote_revision"].(string), RequestPayload: GenerationPayload{Mode: "text_to_image", Prompt: "isolated image", AspectRatio: "1:1", Resolution: "2k", OutputCount: 1, Assets: []AssetReference{}}}
}
func TestTOCCreditExactReplayAndUserIsolation(t *testing.T) {
	s, _ := fixture(t)
	r := CreditRequest{AmountPoints: 20, IdempotencyKey: uuid.NewString(), Note: "test"}
	first, err := s.Credit(1, 2, r)
	require.NoError(t, err)
	again, err := s.Credit(1, 2, r)
	require.NoError(t, err)
	require.Equal(t, first.ID, again.ID)
	r.AmountPoints = 21
	_, err = s.Credit(1, 2, r)
	require.Error(t, err)
	wallet, err := s.Wallet(2)
	require.NoError(t, err)
	require.Equal(t, int64(20), wallet["available_points"])
	other, _ := s.Wallet(3)
	require.Equal(t, int64(0), other["available_points"])
	require.Error(t, s.DB.Exec("UPDATE toc_ledger_entries SET available_delta_points=99").Error)
	require.Error(t, s.DB.Exec("DELETE FROM toc_ledger_entries").Error)
}
func TestTOCReserveAndExecutorAreAtomicAndReplaySafe(t *testing.T) {
	s, f := fixture(t)
	_, r := configureOffer(t, s, f)
	f.failCreate = true
	_, err := s.CreateTask(context.Background(), 2, r)
	require.Error(t, err)
	var count int64
	require.NoError(t, s.DB.Model(&Task{}).Count(&count).Error)
	require.Zero(t, count)
	require.NoError(t, s.DB.Model(&model.PlatformGenerationJob{}).Count(&count).Error)
	require.Zero(t, count)
	w, _ := s.Wallet(2)
	require.Equal(t, int64(20), w["available_points"])
	require.Equal(t, int64(0), w["reserved_points"])
	f.failCreate = false
	first, err := s.CreateTask(context.Background(), 2, r)
	require.NoError(t, err)
	f.evidence.Ready = false
	again, err := s.CreateTask(context.Background(), 2, r)
	require.NoError(t, err)
	require.Equal(t, first.ID, again.ID)
	r.RequestPayload.Prompt = "different"
	_, err = s.CreateTask(context.Background(), 2, r)
	require.Error(t, err)
	w, _ = s.Wallet(2)
	require.Equal(t, int64(16), w["available_points"])
	require.Equal(t, int64(4), w["reserved_points"])
	_, err = s.Task(context.Background(), 3, first.ID)
	require.Error(t, err)
}
func TestTOCSettlementSuccessExactlyOnce(t *testing.T) {
	s, f := fixture(t)
	_, r := configureOffer(t, s, f)
	task, err := s.CreateTask(context.Background(), 2, r)
	require.NoError(t, err)
	var job model.PlatformGenerationJob
	require.NoError(t, s.DB.First(&job, "id = ?", task.ID).Error)
	job.Status = "succeeded"
	job.OutputsJSON = fixtureJSON([]dto.PlatformGenerationArtifact{{AssetID: uuid.NewString(), MediaType: "image", ContentType: "image/png", SizeBytes: 4, SHA256: strings.Repeat("a", 64)}})
	require.NoError(t, s.DB.Save(&job).Error)
	for i := 0; i < 2; i++ {
		require.NoError(t, s.DB.Transaction(func(tx *gorm.DB) error { return ApplyTerminal(tx, &job) }))
	}
	wallet, _ := s.Wallet(2)
	require.Equal(t, int64(16), wallet["available_points"])
	require.Equal(t, int64(0), wallet["reserved_points"])
	var count int64
	require.NoError(t, s.DB.Model(&LedgerEntry{}).Where("kind = ?", "settle").Count(&count).Error)
	require.Equal(t, int64(1), count)
}
func TestTOCDefiniteFailureReleasesUnknownKeepsHold(t *testing.T) {
	for _, unknown := range []bool{false, true} {
		t.Run(fmt.Sprint(unknown), func(t *testing.T) {
			s, f := fixture(t)
			_, r := configureOffer(t, s, f)
			task, err := s.CreateTask(context.Background(), 2, r)
			require.NoError(t, err)
			var job model.PlatformGenerationJob
			require.NoError(t, s.DB.First(&job, "id = ?", task.ID).Error)
			job.Status = "failed"
			if unknown {
				job.ProviderSubmissionAttempt = 1
				require.NoError(t, s.DB.Create(&model.PlatformGenerationRouteAdmission{JobID: job.ID, RouteID: 1, State: model.PlatformGenerationRouteAdmissionUnknown, SlotHeld: true, SubmissionTokenHash: strings.Repeat("a", 64), Attempt: 1}).Error)
			}
			require.NoError(t, s.DB.Save(&job).Error)
			require.NoError(t, s.DB.Transaction(func(tx *gorm.DB) error { return ApplyTerminal(tx, &job) }))
			w, _ := s.Wallet(2)
			if unknown {
				require.Equal(t, int64(16), w["available_points"])
				require.Equal(t, int64(4), w["reserved_points"])
				view, err := s.Task(context.Background(), 2, task.ID)
				require.NoError(t, err)
				require.Equal(t, "reconciliation_required", view["status"])
			} else {
				require.Equal(t, int64(20), w["available_points"])
				require.Equal(t, int64(0), w["reserved_points"])
			}
		})
	}
}
func TestTOCReconciliationRequiredResetsAfterDurableEvidenceCompletes(t *testing.T) {
	s, f := fixture(t)
	_, r := configureOffer(t, s, f)
	task, err := s.CreateTask(context.Background(), 2, r)
	require.NoError(t, err)
	var job model.PlatformGenerationJob
	require.NoError(t, s.DB.First(&job, "id = ?", task.ID).Error)
	// --- Phase 1: job.Status = failed, admission unknown → first ApplyTerminal
	// returns definite=false → task.status becomes reconciliation_required,
	// settlement_state stays held, wallet unchanged.
	job.Status = model.PlatformGenerationStatusFailed
	job.ProviderSubmissionAttempt = 1
	require.NoError(t, s.DB.Create(&model.PlatformGenerationRouteAdmission{
		JobID:              job.ID,
		RouteID:            1,
		State:              model.PlatformGenerationRouteAdmissionUnknown,
		SlotHeld:           true,
		SubmissionTokenHash: strings.Repeat("a", 64),
		Attempt:            1,
	}).Error)
	require.NoError(t, s.DB.Save(&job).Error)
	require.NoError(t, s.DB.Transaction(func(tx *gorm.DB) error { return ApplyTerminal(tx, &job) }))
	w, _ := s.Wallet(2)
	require.Equal(t, int64(16), w["available_points"], "unknown failure keeps held available")
	require.Equal(t, int64(4), w["reserved_points"], "unknown failure keeps reserved intact")
	view, err := s.Task(context.Background(), 2, task.ID)
	require.NoError(t, err)
	require.Equal(t, "reconciliation_required", view["status"], "Task() surfaces reconciliation_required")
	var before Task
	require.NoError(t, s.DB.First(&before, "id = ?", task.ID).Error)
	require.Equal(t, "held", before.SettlementState, "settlement_state still held")
	// --- Phase 2: Relay reconciliation scanner finishes durable evidence —
	// admission → finished, ProviderFailed outcome recorded.
	admission := model.PlatformGenerationRouteAdmission{}
	require.NoError(t, s.DB.First(&admission, "job_id = ?", job.ID).Error)
	admission.State = model.PlatformGenerationRouteAdmissionFinished
	admission.SlotHeld = false
	require.NoError(t, s.DB.Save(&admission).Error)
	require.NoError(t, s.DB.Create(&model.PlatformProviderTerminalOutcome{
		ID:                uuid.NewString(),
		RouteID:           1,
		RouteKey:          "route-a",
		ProviderName:      "fake-provider",
		ChannelClass:      "official",
		RelayJobID:        job.ID,
		Outcome:           model.PlatformProviderOutcomeFailed,
		FailureOwner:      "provider",
		FailureCode:       "upstream_error",
		OccurredAt:        time.Now().UTC(),
		ExternalReference: "synthetic-failure-outcome",
	}).Error)
	// --- Phase 3: job is now reconciliation_required (Relay scanner would
	// have flagged it), ApplyTerminal must resolve the now-definite failure.
	job.Status = model.PlatformGenerationStatusReconciliationRequired
	require.NoError(t, s.DB.Save(&job).Error)
	require.NoError(t, s.DB.Transaction(func(tx *gorm.DB) error { return ApplyTerminal(tx, &job) }))
	w, _ = s.Wallet(2)
	require.Equal(t, int64(20), w["available_points"], "definite failure releases reserved back to available")
	require.Equal(t, int64(0), w["reserved_points"], "reserved is fully released")
	var after Task
	require.NoError(t, s.DB.First(&after, "id = ?", task.ID).Error)
	require.Equal(t, "released", after.SettlementState)
	require.NotNil(t, after.ActualCostPoints)
	require.Equal(t, int64(0), *after.ActualCostPoints)
	var releaseCount int64
	require.NoError(t, s.DB.Model(&LedgerEntry{}).Where("kind = ? AND task_id = ?", "release", task.ID).Count(&releaseCount).Error)
	require.Equal(t, int64(1), releaseCount, "release ledger entry recorded")
	// --- Phase 4: idempotent replay is safe.
	for i := 0; i < 2; i++ {
		require.NoError(t, s.DB.Transaction(func(tx *gorm.DB) error { return ApplyTerminal(tx, &job) }))
	}
	var totalLedger int64
	require.NoError(t, s.DB.Model(&LedgerEntry{}).Where("task_id = ?", task.ID).Count(&totalLedger).Error)
	require.Equal(t, int64(2), totalLedger, "exactly reserve + release, no duplicate")
	// --- Phase 5: user-facing status normalized to failed (not the raw
	// reconciliation_required marker) after the release settled.
	view, err = s.Task(context.Background(), 2, task.ID)
	require.NoError(t, err)
	require.Equal(t, "failed", view["status"], "released task surfaces normalized failed status")
}
func TestTOCReconciliationRequiredSettlesAfterOutputsRecover(t *testing.T) {
	s, f := fixture(t)
	_, r := configureOffer(t, s, f)
	task, err := s.CreateTask(context.Background(), 2, r)
	require.NoError(t, err)
	var job model.PlatformGenerationJob
	require.NoError(t, s.DB.First(&job, "id = ?", task.ID).Error)
	// --- Phase 1: unknown outcome (e.g. local response lost after provider
	// accepted) → first ApplyTerminal keeps the reservation held and flags
	// reconciliation_required.
	job.Status = model.PlatformGenerationStatusFailed
	job.ProviderSubmissionAttempt = 1
	require.NoError(t, s.DB.Create(&model.PlatformGenerationRouteAdmission{
		JobID:              job.ID,
		RouteID:            1,
		State:              model.PlatformGenerationRouteAdmissionUnknown,
		SlotHeld:           true,
		SubmissionTokenHash: strings.Repeat("b", 64),
		Attempt:            1,
	}).Error)
	require.NoError(t, s.DB.Save(&job).Error)
	require.NoError(t, s.DB.Transaction(func(tx *gorm.DB) error { return ApplyTerminal(tx, &job) }))
	w, _ := s.Wallet(2)
	require.Equal(t, int64(16), w["available_points"], "unknown outcome keeps held available")
	require.Equal(t, int64(4), w["reserved_points"], "unknown outcome keeps reserved intact")
	// --- Phase 2: Relay's reconciliation scanner recovers the complete
	// output set (provider actually delivered). Job stays flagged
	// reconciliation_required but now carries valid OutputsJSON.
	job.Status = model.PlatformGenerationStatusReconciliationRequired
	artifact := dto.PlatformGenerationArtifact{AssetID: "asset-" + uuid.NewString(), ObjectKey: "toc/out.png", MediaType: "image", ContentType: "image/png", SizeBytes: 1024, SHA256: strings.Repeat("c", 64)}
	job.OutputsJSON = fixtureJSON([]dto.PlatformGenerationArtifact{artifact})
	require.NoError(t, s.DB.Save(&job).Error)
	// --- Phase 3: ApplyTerminal must settle from the recovered outputs.
	require.NoError(t, s.DB.Transaction(func(tx *gorm.DB) error { return ApplyTerminal(tx, &job) }))
	w, _ = s.Wallet(2)
	require.Equal(t, int64(16), w["available_points"], "settle does not refund available")
	require.Equal(t, int64(0), w["reserved_points"], "reserved fully consumed")
	var after Task
	require.NoError(t, s.DB.First(&after, "id = ?", task.ID).Error)
	require.Equal(t, "settled", after.SettlementState)
	require.Equal(t, "succeeded", after.Status, "recovered success normalizes user-facing status")
	require.Empty(t, after.FailureReason, "settled task carries no failure reason")
	require.NotNil(t, after.ActualCostPoints)
	require.Equal(t, task.QuotePoints, *after.ActualCostPoints)
	var settleCount int64
	require.NoError(t, s.DB.Model(&LedgerEntry{}).Where("kind = ? AND task_id = ?", "settle", task.ID).Count(&settleCount).Error)
	require.Equal(t, int64(1), settleCount, "settle ledger entry recorded")
	// --- Phase 4: Task() read path surfaces the recovered artifacts and the
	// normalized succeeded status.
	view, err := s.Task(context.Background(), 2, task.ID)
	require.NoError(t, err)
	require.Equal(t, "succeeded", view["status"])
	require.Len(t, view["output_artifacts"], 1)
	require.Equal(t, artifact.AssetID, view["output_artifacts"].([]any)[0].(map[string]any)["asset_id"])
	// --- Phase 5: idempotent replay is safe.
	for i := 0; i < 2; i++ {
		require.NoError(t, s.DB.Transaction(func(tx *gorm.DB) error { return ApplyTerminal(tx, &job) }))
	}
	var totalLedger int64
	require.NoError(t, s.DB.Model(&LedgerEntry{}).Where("task_id = ?", task.ID).Count(&totalLedger).Error)
	require.Equal(t, int64(2), totalLedger, "exactly reserve + settle, no duplicate")
}
func TestTOCQuoteAndLiveRouteAreAuthoritative(t *testing.T) {
	s, f := fixture(t)
	m, r := configureOffer(t, s, f)
	changed := r
	changed.ExpectedQuoteRevision = "sha256:" + strings.Repeat("f", 64)
	_, err := s.CreateTask(context.Background(), 2, changed)
	require.Error(t, err)
	f.evidence.RoutingReleaseSHA256 = "sha256:" + strings.Repeat("e", 64)
	_, err = s.CreateTask(context.Background(), 2, r)
	require.Error(t, err)
	rows, err := s.Models(2, false)
	require.NoError(t, err)
	require.False(t, rows[0]["available"].(bool))
	_, err = s.SaveCatalog(context.Background(), 1, "seedream-5", CatalogRequest{Enabled: false, UnitPricePoints: 4, ExpectedVersion: m.Version, IdempotencyKey: uuid.NewString(), Reason: "disable despite unavailable provider"})
	require.NoError(t, err)
	rows, err = s.Models(2, false)
	require.NoError(t, err)
	require.Empty(t, rows)
}
func TestTOCImageOwnershipLeaseAndIntegrity(t *testing.T) {
	s, f := fixture(t)
	_, r := configureOffer(t, s, f)
	var imageData bytes.Buffer
	require.NoError(t, png.Encode(&imageData, image.NewRGBA(image.Rect(0, 0, 2, 2))))
	asset, err := s.Upload(2, uuid.NewString(), "image.png", "image", "", imageData.Bytes())
	require.NoError(t, err)
	_, err = s.AssetAccess(3, asset.ID)
	require.Error(t, err)
	access, err := s.AssetAccess(2, asset.ID)
	require.NoError(t, err)
	u, err := url.Parse(access["url"].(string))
	require.NoError(t, err)
	data, err := s.ReadSignedAsset(asset.ID, u.Query().Get("expires"), u.Query().Get("signature"))
	require.NoError(t, err)
	require.NotEmpty(t, data)
	_, err = s.ReadSignedAsset(asset.ID, u.Query().Get("expires"), strings.Repeat("0", 64))
	require.Error(t, err)
	r.RequestPayload.Mode = "image_to_image"
	r.RequestPayload.Assets = []AssetReference{{AssetID: asset.ID, Role: "reference_image"}}
	_, err = s.CreateTask(context.Background(), 2, r)
	require.NoError(t, err)
}

func TestTOCRealRelayAuthOriginAndOwnershipRoutes(t *testing.T) {
	s, _ := fixture(t)
	oldDB, oldLog, oldSecret := model.DB, model.LOG_DB, common.SessionSecret
	oldRedis := common.RedisEnabled
	common.RedisEnabled = false
	oldMain, oldLogType := common.MainDatabaseType(), common.LogDatabaseType()
	model.DB = s.DB
	model.LOG_DB = s.DB
	common.SessionSecret = strings.Repeat("z", 40)
	common.SetDatabaseTypes(common.DatabaseTypeSQLite, common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		require.Eventually(t, func() bool { return gopool.WorkerCount() == 0 }, 3*time.Second, time.Millisecond)
		common.RedisEnabled = oldRedis
		model.DB = oldDB
		model.LOG_DB = oldLog
		common.SessionSecret = oldSecret
		common.SetDatabaseTypes(oldMain, oldLogType)
	})
	t.Setenv("RELAY_RUNTIME_PROFILE", "toc")
	t.Setenv("TOC_CLIENT_ORIGIN", "http://127.0.0.1:14340")
	t.Setenv("TOC_API_PUBLIC_BASE_URL", "http://127.0.0.1:18340")
	token := func(user int) string {
		session := model.UserSession{SID: uuid.NewString(), UserID: user, Version: 1, UserAuthVersion: 1, Status: model.UserSessionStatusActive, RefreshHash: uuid.NewString(), LoginMethod: "password", LastActiveAt: time.Now().Unix(), ExpiresAt: time.Now().Add(time.Hour).Unix()}
		require.NoError(t, model.CreateUserSession(&session))
		value, _, err := service.IssueAccessToken(service.AuthIdentity{UserID: user, SessionID: session.SID, UserAuthVersion: 1, SessionVersion: 1})
		require.NoError(t, err)
		return value
	}
	root, user := token(1), token(2)
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	RegisterRoutes(engine, s)
	call := func(method, path, bearer, origin, body string) int {
		request := httptest.NewRequest(method, path, strings.NewReader(body))
		if bearer != "" {
			request.Header.Set("Authorization", "Bearer "+bearer)
		}
		if origin != "" {
			request.Header.Set("Origin", origin)
		}
		request.Header.Set("Content-Type", "application/json")
		response := httptest.NewRecorder()
		engine.ServeHTTP(response, request)
		return response.Code
	}
	require.Equal(t, http.StatusUnauthorized, call("GET", "/api/v1/personal/wallet", "", "", ""))
	profile := httptest.NewRecorder()
	engine.ServeHTTP(profile, httptest.NewRequest(http.MethodGet, "/profile", nil))
	require.Equal(t, http.StatusSeeOther, profile.Code)
	require.Equal(t, "http://127.0.0.1:14340/creation", profile.Header().Get("Location"))
	rootPage := httptest.NewRecorder()
	engine.ServeHTTP(rootPage, httptest.NewRequest(http.MethodGet, "/", nil))
	require.Equal(t, http.StatusSeeOther, rootPage.Code)
	require.Equal(t, "http://127.0.0.1:14340/creation", rootPage.Header().Get("Location"))
	require.Equal(t, http.StatusForbidden, call("GET", "/api/v1/toc-admin/catalog", user, "", ""))
	require.Equal(t, http.StatusOK, call("GET", "/api/v1/session/surfaces", user, "", ""))
	credit := fixtureJSON(CreditRequest{AmountPoints: 10, IdempotencyKey: uuid.NewString(), Note: "root audited fixture"})
	require.Equal(t, 403, call("POST", "/api/v1/toc-admin/users/2/test-credit", root, "https://evil.invalid", credit))
	require.Equal(t, 403, call("POST", "/api/v1/toc-admin/users/2/test-credit", root, "", credit))
	require.Equal(t, 200, call("POST", "/api/v1/toc-admin/users/2/test-credit", root, "http://127.0.0.1:18340", credit))
	require.Equal(t, 404, call("GET", "/api/v1/personal/tasks/"+uuid.NewString(), user, "", ""))
}

func fixtureJSON(v any) string {
	raw, err := common.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(raw)
}

func TestTOCConcurrentIdenticalSubmitReservesOnce(t *testing.T) {
	s, f := fixture(t)
	_, request := configureOffer(t, s, f)
	var wg sync.WaitGroup
	results := make(chan Task, 2)
	errs := make(chan error, 2)
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			task, err := s.CreateTask(context.Background(), 2, request)
			results <- task
			errs <- err
		}()
	}
	wg.Wait()
	close(results)
	close(errs)
	for err := range errs {
		require.NoError(t, err)
	}
	id := ""
	for task := range results {
		if id != "" {
			require.Equal(t, id, task.ID)
		}
		id = task.ID
	}
	wallet, err := s.Wallet(2)
	require.NoError(t, err)
	require.Equal(t, int64(16), wallet["available_points"])
	require.Equal(t, int64(4), wallet["reserved_points"])
}
func TestTOCDatabaseTaskPinsAndNullSettlementRejected(t *testing.T) {
	s, f := fixture(t)
	_, request := configureOffer(t, s, f)
	task, err := s.CreateTask(context.Background(), 2, request)
	require.NoError(t, err)
	for _, sql := range []string{"UPDATE toc_tasks SET user_id=3 WHERE id=?", "UPDATE toc_tasks SET quote_points=8,reserved_points=8 WHERE id=?", "UPDATE toc_tasks SET settlement_state='settled',status='succeeded',reserved_points=0,actual_cost_points=NULL WHERE id=?"} {
		require.Error(t, s.DB.Exec(sql, task.ID).Error)
	}
	var stored Task
	require.NoError(t, s.DB.First(&stored, "id = ?", task.ID).Error)
	require.Equal(t, 2, stored.UserID)
	require.Equal(t, "held", stored.SettlementState)
	_, err = digest(make(chan int))
	require.Error(t, err)
	_, err = encode(make(chan int))
	require.Error(t, err)
	require.Error(t, strictJSON([]byte(`{"amount_points":1,"amount_points":2}`), &CreditRequest{}))
}
func TestTOCGrantRevocationBlocksQueuedNativeAdmission(t *testing.T) {
	s, f := fixture(t)
	m, r := configureOffer(t, s, f)
	task, err := s.CreateTask(context.Background(), 2, r)
	require.NoError(t, err)
	_, err = s.SaveGrant(1, 2, GrantRequest{ModelID: m.ID, Enabled: false, ExpectedVersion: 1, IdempotencyKey: uuid.NewString(), Reason: "explicit revoke"})
	require.NoError(t, err)
	var job model.PlatformGenerationJob
	require.NoError(t, s.DB.First(&job, "id = ?", task.ID).Error)
	require.Error(t, ValidateAdmission(s.DB, &job))
	wallet, _ := s.Wallet(2)
	require.Equal(t, int64(4), wallet["reserved_points"])
}
func TestTOCMediaPublicAndExecutionOriginsRemainSeparate(t *testing.T) {
	s, _ := fixture(t)
	require.NoError(t, s.Assets.SetRelayBase("https://assets.relay-toc.local"))
	var raw bytes.Buffer
	require.NoError(t, png.Encode(&raw, image.NewRGBA(image.Rect(0, 0, 2, 2))))
	asset, err := s.Upload(2, uuid.NewString(), "ref.png", "image", "", raw.Bytes())
	require.NoError(t, err)
	public, err := s.AssetAccess(2, asset.ID)
	require.NoError(t, err)
	require.Contains(t, public["url"], "http://127.0.0.1:14340/api/v1/toc-media/")
	input, err := s.Assets.InputURL(asset, time.Now().Add(time.Minute))
	require.NoError(t, err)
	require.Contains(t, input, "https://assets.relay-toc.local/api/v1/toc-media/")
	require.Error(t, s.Assets.SetRelayBase("http://127.0.0.1:14340"))
	require.Error(t, s.Assets.SetRelayBase("https://assets.relay-toc.local/forward?url=https://elsewhere"))
}

func TestTOCProviderCostReceiptExactOwnerPinsAndReplay(t *testing.T) {
	s, f := fixture(t)
	_, request := configureOffer(t, s, f)
	task, err := s.CreateTask(context.Background(), 2, request)
	require.NoError(t, err)
	require.NoError(t, s.DB.AutoMigrate(&model.PlatformGenerationProviderRoute{}))
	channel, member, _ := seedTOCCostVault(t, s.DB, 21)
	route := model.PlatformGenerationProviderRoute{ID: 12, RouteKey: "route-a", Model: "seedream-5", Mode: "text_to_image", ProviderName: "volcengine-ark", AccountID: "primary", ChannelID: 21, KeyFingerprint: member.PrivateData.PinnedKeyFingerprint, ChannelClass: "official", UpstreamModel: "seedream-5", CapabilityProfileSnapshot: "{}"}
	require.NoError(t, s.DB.Create(&route).Error)
	credential := channel.CredentialSetVersion
	costSHA := "sha256:" + strings.Repeat("e", 64)
	contract := dto.PlatformGenerationExecutionContract{SchemaVersion: 1, RoutingReleaseSHA256: f.evidence.RoutingReleaseSHA256, ProviderCostReadinessSHA256: f.evidence.CostReadinessSHA256, Routes: []dto.PlatformGenerationExecutionRoute{{RouteID: route.RouteKey, ChannelID: route.ChannelID, ProviderAccountID: route.AccountID, ProviderCredentialSetVersion: credential, RouteBindingSHA256: "sha256:" + strings.Repeat("f", 64), Mode: route.Mode, Resolution: "2k", CostKind: "contract_rate", CostID: uuid.NewString(), CostSHA256: costSHA}}}
	contractSHA, err := contract.Digest()
	require.NoError(t, err)
	var job model.PlatformGenerationJob
	require.NoError(t, s.DB.First(&job, "id = ?", task.ID).Error)
	var generation dto.PlatformGenerationRequest
	require.NoError(t, common.Unmarshal([]byte(job.RequestJSON), &generation))
	generation.ExecutionContract = &contract
	job.RequestJSON = fixtureJSON(generation)
	job.ProviderRouteID = route.ID
	job.ProviderChannelID = route.ChannelID
	require.NoError(t, s.DB.Save(&job).Error)
	input := dto.PlatformChannelCostInput{SchemaVersion: 2, EventID: uuid.NewString(), AmountCents: 22, IdempotencyKey: uuid.NewString(), ChannelKey: route.RouteKey, ChannelType: "official", RouteID: route.ID, OccurredAt: time.Now().UTC(), ExternalReference: "isolated-cost-evidence", PersonalWorkspaceID: workspace(2), TaskID: task.ID, RelayJobID: task.ID, EvidenceSource: "contract_rate", EvidenceReference: "isolated synthetic contract", SourceDocumentSHA256: strings.Repeat("b", 64), ExecutionContractSHA256: contractSHA, ProviderCostRevisionSHA256: costSHA, PlatformProviderRouteIdentity: dto.PlatformProviderRouteIdentity{IdentityStatus: "bound", ProviderName: route.ProviderName, ProviderAccountID: route.AccountID, ProviderChannelID: route.ChannelID, ProviderRouteID: route.ID, ProviderKeyFingerprint: route.KeyFingerprint, ProviderCredentialVersion: member.PrivateData.ProviderCredentialVersion, RouteKey: route.RouteKey, RoutingReleaseSHA256: contract.RoutingReleaseSHA256}}
	require.NoError(t, input.Validate())
	raw := fixtureJSON(input.Payload())
	hash := sha256.Sum256([]byte(raw))
	event := model.PlatformChannelCostEvent{ID: input.EventID, AmountCents: input.AmountCents, IdempotencyKey: input.IdempotencyKey, ChannelKey: input.ChannelKey, ChannelType: input.ChannelType, OccurredAt: input.OccurredAt, ExternalReference: input.ExternalReference, PersonalWorkspaceID: input.PersonalWorkspaceID, TaskID: task.ID, RelayJobID: task.ID, EvidenceSource: input.EvidenceSource, EvidenceReference: input.EvidenceReference, SourceDocumentSHA256: input.SourceDocumentSHA256, PayloadJSON: raw, PayloadSHA256: hex.EncodeToString(hash[:])}
	for i := 0; i < 2; i++ {
		require.NoError(t, s.DB.Transaction(func(tx *gorm.DB) error { return RecordProviderCost(tx, task.ID, event) }))
	}
	var count int64
	require.NoError(t, s.DB.Model(&CostReceipt{}).Count(&count).Error)
	require.Equal(t, int64(1), count)
	bad := event
	bad.PersonalWorkspaceID = workspace(3)
	require.Error(t, RecordProviderCost(s.DB, task.ID, bad))
	bad = event
	bad.PayloadJSON = raw + " "
	require.Error(t, RecordProviderCost(s.DB, task.ID, bad))
	require.Error(t, s.DB.Exec("DELETE FROM toc_provider_cost_receipts").Error)
	wallet, _ := s.Wallet(2)
	require.Equal(t, int64(4), wallet["reserved_points"])
}

func TestTOCStaleCapabilityModelDoesNotBreakDirectory(t *testing.T) {
	s, f := fixture(t)
	e := f.evidence
	saveModel := func(publicID string, price int64) CatalogModel {
		m, err := s.SaveCatalog(context.Background(), 1, publicID, CatalogRequest{
			Enabled:                    true,
			UnitPricePoints:           price,
			ExpectedCapabilityRevision: e.Resource.CapabilityRevision,
			ExpectedCatalogRevision:    e.CatalogRevision,
			ExpectedRoutingSHA256:      e.RoutingReleaseSHA256,
			ExpectedCostSHA256:         e.CostReadinessSHA256,
			IdempotencyKey:            uuid.NewString(),
			Reason:                     "mixed schema directory test",
		})
		require.NoError(t, err)
		_, err = s.SaveGrant(1, 2, GrantRequest{ModelID: m.ID, Enabled: true, IdempotencyKey: uuid.NewString(), Reason: "personal grant"})
		require.NoError(t, err)
		return m
	}
	good := saveModel("good-video", 10)
	bad := saveModel("stale-image", 4)
	staleJSON := `{"schema_version":1,"modes":{"text_to_image":{"input_media_types":[],"required_resource_keys":[],"supports_face":false,"limits":{"aspect_ratios":["1:1"],"resolutions":["2048x2048"],"duration_seconds":[1],"max_prompt_length":1000,"max_images":0,"max_videos":0,"max_audio":0,"output_counts":[1]}}}}`
	require.NoError(t, s.DB.Exec("UPDATE toc_catalog_models SET capabilities_json=? WHERE id=?", staleJSON, bad.ID).Error)

	// Generation list: the stale model is skipped, the healthy model stays available.
	rows, err := s.Models(2, false)
	require.NoError(t, err)
	require.Len(t, rows, 1)
	require.Equal(t, good.PublicModelID, rows[0]["slug"])
	require.True(t, rows[0]["available"].(bool))

	// Onboarding catalog: the stale model is retained but explicitly blocked,
	// and the whole endpoint no longer fails (previously a hard 503).
	catalog, err := s.Models(2, true)
	require.NoError(t, err)
	require.Len(t, catalog, 2)
	bySlug := map[string]map[string]any{}
	for _, row := range catalog {
		bySlug[row["slug"].(string)] = row
	}
	require.True(t, bySlug["good-video"]["available"].(bool))
	stale := bySlug["stale-image"]
	require.False(t, stale["available"].(bool))
	reason := stale["unavailable_reason"].(map[string]string)
	require.Equal(t, "personal_capability_unavailable", reason["code"])
}
