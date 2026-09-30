package toc

import (
	"context"
	"fmt"
	"github.com/QuantumNous/new-api/common"
	"os"
	"strings"
	"sync"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"gorm.io/gorm"
)

type RelayExecutor struct{}

func (RelayExecutor) Evidence(db *gorm.DB, slug string) (ModelEvidence, error) {
	result := ModelEvidence{Blockers: []string{}}
	catalog, err := service.GetPlatformRelayModelCatalogWithDB(db)
	if err != nil {
		return result, fail(503, "TOC_RELAY_UNAVAILABLE", "模型目录暂时无法核验")
	}
	for _, resource := range catalog.Data {
		if resource.ID == slug {
			result.Resource = resource
			break
		}
	}
	result.CatalogRevision = catalog.CatalogRevision
	if result.Resource.ID == "" || !result.Resource.ManagedRoute || !result.Resource.CustomerCallable {
		result.Blockers = append(result.Blockers, "managed_route_unpublished")
		return result, nil
	}
	projection, err := service.GetPlatformModelReleaseEvidenceProjectionWithDB(db)
	if err != nil {
		return result, fail(503, "TOC_RELAY_UNAVAILABLE", "模型路由和成本证据暂时无法核验")
	}
	for _, e := range projection.Models {
		if e.PublicModelID != slug {
			continue
		}
		result.RoutingReleaseSHA256 = e.RoutingReleaseSHA256
		result.CostReadinessSHA256 = e.ProviderCostReadinessSHA256
		result.Ready = e.Status == "ready" && e.ProviderCostReady && e.CapabilityRevision == result.Resource.CapabilityRevision
		if !result.Ready {
			result.Blockers = append(result.Blockers, "route_or_cost_evidence_unready")
		}
		return result, nil
	}
	result.Blockers = append(result.Blockers, "release_evidence_missing")
	return result, nil
}
func (RelayExecutor) Create(ctx context.Context, db *gorm.DB, task Task, generation dto.PlatformGenerationRequest, catalog CatalogModel) error {
	contract, revision, err := service.BuildLocalTOCExecutionContractWithDB(db, catalog.PublicModelID, generation.Mode, generation.Output.Resolution)
	if err != nil {
		return err
	}
	// 开发模式下 RELAY_TOC_DEV_BYPASS_EVIDENCE=1 跳过 marker/cost readiness SHA 检查
	bypassEvidence := os.Getenv("RELAY_TOC_DEV_BYPASS_EVIDENCE") == "1"
	if !bypassEvidence && (revision != catalog.CapabilityRevision || contract.RoutingReleaseSHA256 != catalog.RoutingReleaseSHA256 || contract.ProviderCostReadinessSHA256 != catalog.CostReadinessSHA256) {
		return fail(409, "TOC_EVIDENCE_CHANGED", "模型执行证据已变化，请重新审核")
	}
	generation.ExecutionContract = &contract
	generation.ClientReferenceID = &task.ID
	_, err = service.CreateLocalTOCGenerationWithDB(ctx, db, service.LocalTOCGenerationRequest{JobID: task.ID, OwnerUserID: task.UserID, AdmissionID: task.AdmissionID, Generation: generation, IdempotencyKey: task.ID, RequestID: task.ID})
	return err
}
func (RelayExecutor) Snapshot(ctx context.Context, task Task) (dto.PlatformGenerationSnapshot, error) {
	return service.GetLocalTOCGeneration(ctx, task.RelayJobID, task.UserID)
}
func (RelayExecutor) Download(ctx context.Context, task Task, asset string) (dto.PlatformSignedDownload, error) {
	return service.GetLocalTOCGenerationDownload(ctx, task.RelayJobID, asset, task.UserID)
}

func ValidateAdmission(tx *gorm.DB, job *model.PlatformGenerationJob) error {
	if tx == nil || job == nil {
		return fmt.Errorf("TOC admission unavailable")
	}
	authority, err := model.LoadRelayGenerationAuthorityWithDB(tx, *job)
	if err != nil || authority == nil {
		return fmt.Errorf("TOC authority unavailable")
	}
	var task Task
	if err = tx.First(&task, "id = ? AND relay_job_id = ? AND user_id = ? AND admission_id = ?", job.ID, job.ID, authority.OwnerUserID, authority.AdmissionID).Error; err != nil {
		return err
	}
	if task.SettlementState != "held" || task.ReservedPoints != task.QuotePoints || task.QuotePoints <= 0 {
		return fmt.Errorf("TOC reservation is not active")
	}
	var entry LedgerEntry
	if err = tx.First(&entry, "user_id = ? AND task_id = ? AND kind = ?", task.UserID, task.ID, "reserve").Error; err != nil {
		return err
	}
	if entry.RequestSHA256 != task.RequestSHA256 || entry.ReservedDeltaPoints != task.QuotePoints || entry.AvailableDeltaPoints != -task.QuotePoints {
		return fmt.Errorf("TOC reservation receipt mismatch")
	}
	var wallet Wallet
	if err = tx.First(&wallet, "user_id = ?", task.UserID).Error; err != nil {
		return err
	}
	if wallet.ReservedPoints < task.QuotePoints {
		return fmt.Errorf("TOC wallet does not cover reservation")
	}
	var request dto.PlatformGenerationRequest
	if err = common.Unmarshal([]byte(job.RequestJSON), &request); err != nil {
		return err
	}
	if request.ExecutionContract == nil {
		return fmt.Errorf("TOC exact execution contract missing")
	}
	var payload GenerationPayload
	if err = common.Unmarshal([]byte(task.RequestJSON), &payload); err != nil {
		return err
	}
	if request.Model != job.Model || request.Mode != payload.Mode || request.Inputs.Prompt != payload.Prompt || request.Output.Count != payload.OutputCount || request.Output.AspectRatio != payload.AspectRatio || request.Output.Resolution != payload.Resolution || request.Output.DurationSeconds != payload.DurationSeconds || request.Output.FaceEnabled != payload.FaceEnabled || len(request.Inputs.Assets) != len(payload.Assets) {
		return fmt.Errorf("TOC execution does not match reserved request")
	}
	price, err := object(task.PricingJSON)
	if err != nil {
		return err
	}
	bypassEvidence := os.Getenv("RELAY_TOC_DEV_BYPASS_EVIDENCE") == "1"
	if !bypassEvidence && (price["candidate_revision"] != job.ExpectedCapabilityRevision || price["routing_release_sha256"] != request.ExecutionContract.RoutingReleaseSHA256 || price["provider_cost_readiness_sha256"] != request.ExecutionContract.ProviderCostReadinessSHA256) {
		return fmt.Errorf("TOC approved release differs from execution")
	}
	var catalog CatalogModel
	if err = tx.First(&catalog, "id = ? AND enabled = ?", task.ModelID, true).Error; err != nil {
		return fmt.Errorf("TOC sales model disabled")
	}
	var grant Grant
	if err = tx.First(&grant, "user_id = ? AND model_id = ? AND enabled = ?", task.UserID, task.ModelID, true).Error; err != nil {
		return fmt.Errorf("TOC model grant disabled")
	}
	if !bypassEvidence && (catalog.CapabilityRevision != job.ExpectedCapabilityRevision || catalog.RoutingReleaseSHA256 != request.ExecutionContract.RoutingReleaseSHA256 || catalog.CostReadinessSHA256 != request.ExecutionContract.ProviderCostReadinessSHA256) {
		return fmt.Errorf("TOC sales authority changed")
	}
	unit, ok := price["unit_price_points"].(float64)
	if !ok || unit < 1 || unit > 1000000 || unit != float64(int64(unit)) || int64(payload.OutputCount) <= 0 || int64(payload.OutputCount) > MaxPoints/int64(unit) {
		return fmt.Errorf("TOC reservation quote invalid")
	}
	// 按 mode 计算期望 cost（per_second 或 per_item）
	mode, _ := price["mode"].(string)
	var expectedCost int64
	if mode == "per_second" {
		duration, _ := price["duration_seconds"].(float64)
		expectedCost = int64(unit) * int64(duration) * int64(payload.OutputCount)
	} else {
		expectedCost = int64(unit) * int64(payload.OutputCount)
	}
	if task.QuotePoints != expectedCost {
		return fmt.Errorf("TOC reservation quote does not match execution quantity")
	}
	return nil
}

func VerifySchema(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("TOC database missing")
	}
	status, err := model.GetRelaySchemaStatus(db)
	if err != nil || !status.Current || status.CurrentVersion < 12 {
		return fmt.Errorf("TOC requires the verified v12 schema")
	}
	for _, row := range model.RelayTOCSchemaV12Models() {
		if !db.Migrator().HasTable(row) {
			return fmt.Errorf("TOC table missing")
		}
	}
	return nil
}

var configured struct {
	sync.RWMutex
	service *Service
}

func ConfiguredService() *Service {
	configured.RLock()
	defer configured.RUnlock()
	return configured.service
}
func Configure(db *gorm.DB) error {
	if os.Getenv("RELAY_RUNTIME_PROFILE") != "toc" {
		return nil
	}
	if os.Getenv("RELAY_PLATFORM_CONNECTOR_ENABLED") != "false" {
		return fmt.Errorf("TOC first release requires a disabled Platform connector")
	}
	if err := VerifySchema(db); err != nil {
		return err
	}
	public := os.Getenv("TOC_CLIENT_ORIGIN")
	if public == "" {
		public = os.Getenv("TOC_PUBLIC_BASE_URL")
	}
	assets, err := NewAssetStore(os.Getenv("TOC_ASSET_ROOT"), public, os.Getenv("TOC_ASSET_SIGNING_SECRET"))
	if err != nil {
		return err
	}
	if base := os.Getenv("TOC_ASSET_RELAY_BASE_URL"); base != "" {
		if err = assets.SetRelayBase(base); err != nil {
			return err
		}
	}
	client := strings.TrimRight(os.Getenv("TOC_CLIENT_ORIGIN"), "/")
	if client == "" {
		return fmt.Errorf("TOC client origin required")
	}
	// Validate the browser origin with the same strict origin-only URL contract.
	if _, err = NewAssetStore(os.Getenv("TOC_ASSET_ROOT"), client, os.Getenv("TOC_ASSET_SIGNING_SECRET")); err != nil {
		return err
	}
	configured.Lock()
	defer configured.Unlock()
	if configured.service != nil {
		return fmt.Errorf("TOC already configured")
	}
	if err = service.RegisterRelayTOCLocalCostRecorder(RecordProviderCost); err != nil {
		return err
	}
	if err = service.RegisterRelayTOCCostReadiness(VerifySchema); err != nil {
		return err
	}
	service.RegisterLocalTOCTerminalHook(ApplyTerminal)
	service.RegisterLocalTOCAdmissionHook(ValidateAdmission)
	configured.service = &Service{DB: db, Executor: RelayExecutor{}, Assets: assets}
	return nil
}
