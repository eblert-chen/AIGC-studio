package toc

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"github.com/QuantumNous/new-api/common"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

// profileDurationApplies reports whether DurationSeconds represents a real
// provider billing unit for the given mode. Profiles that declare
// DurationSemanticsNone on a mode (images, where duration is just a legacy
// sentinel) are not duration-billed. Missing per-mode annotations fall back
// to "yes, duration applies" so purely-video models keep their per-second
// semantics without requiring a per-release annotation.
func profileDurationApplies(_modelID, mode string) bool {
	for _, p := range generationprofile.AllProfiles() {
		if ds, ok := p.DurationSemantics[mode]; ok && ds == generationprofile.DurationSemanticsNone {
			return false
		}
	}
	return true
}

type AssetReference struct {
	AssetID   string `json:"asset_id"`
	MediaType string `json:"media_type,omitempty"`
	Role      string `json:"role,omitempty"`
}
type GenerationPayload struct {
	Mode            string           `json:"mode"`
	Prompt          string           `json:"prompt"`
	Assets          []AssetReference `json:"assets"`
	AspectRatio     string           `json:"aspect_ratio"`
	Resolution      string           `json:"resolution"`
	OutputCount     int              `json:"output_count"`
	DurationSeconds int              `json:"duration_seconds,omitempty"`
	FaceEnabled     bool             `json:"face_enabled,omitempty"`
}
type CreateTaskRequest struct {
	ModelID                   string            `json:"model_id"`
	IdempotencyKey            string            `json:"idempotency_key"`
	ExpectedCapabilityVersion int64             `json:"expected_capability_version"`
	ExpectedQuoteRevision     string            `json:"expected_quote_revision"`
	RequestPayload            GenerationPayload `json:"request_payload"`
}

func contains[T comparable](values []T, value T) bool {
	for _, item := range values {
		if item == value {
			return true
		}
	}
	return false
}
func strictJSON(raw []byte, target any) error {
	if err := common.RejectDuplicateJSONKeys(raw); err != nil {
		return err
	}
	return common.DecodeJsonDisallowUnknownFields(bytes.NewReader(raw), target)
}
func (s *Service) buildGeneration(tx *gorm.DB, user int, m CatalogModel, p GenerationPayload) (dto.PlatformGenerationRequest, error) {
	var caps dto.PlatformGenerationCapabilities
	if err := common.Unmarshal([]byte(m.CapabilitiesJSON), &caps); err != nil {
		return dto.PlatformGenerationRequest{}, err
	}
	cap, ok := caps.Modes[p.Mode]
	if !ok || caps.SchemaVersion != 3 {
		return dto.PlatformGenerationRequest{}, fail(422, "TOC_REQUEST_UNSUPPORTED", "输入不符合当前已授权模型的能力限制")
	}
	// 图像模式: text_to_image / image_to_image；视频模式: text_to_video / image_to_video / video_to_video
	videoMode := p.Mode == "text_to_video" || p.Mode == "image_to_video" || p.Mode == "video_to_video"
	if strings.TrimSpace(p.Prompt) == "" || utf8.RuneCountInString(p.Prompt) > cap.Limits.MaxPromptLength || !contains(cap.Limits.OutputCounts, p.OutputCount) || !contains(cap.Limits.AspectRatios, p.AspectRatio) || !contains(cap.Limits.Resolutions, p.Resolution) {
		return dto.PlatformGenerationRequest{}, fail(422, "TOC_REQUEST_UNSUPPORTED", "输入不符合当前已授权模型的能力限制")
	}
	if p.FaceEnabled {
		return dto.PlatformGenerationRequest{}, fail(422, "TOC_REQUEST_UNSUPPORTED", "人脸识别暂不可用")
	}
	// Duration 校验: 视频模型要求在 capability 声明的可选值里；图像模型必须为 0
	if videoMode {
		if !contains(cap.Limits.DurationSeconds, p.DurationSeconds) {
			return dto.PlatformGenerationRequest{}, fail(422, "TOC_REQUEST_UNSUPPORTED", "视频时长超出当前已授权模型的可选值")
		}
	} else if p.DurationSeconds != 0 {
		return dto.PlatformGenerationRequest{}, fail(422, "TOC_REQUEST_UNSUPPORTED", "图像模型不支持视频时长参数")
	}
	// Assets 校验: 图像模式沿用原逻辑；视频模式简化处理（仅 image_to_video 接受参考图）
	if videoMode {
		switch p.Mode {
		case "text_to_video":
			if len(p.Assets) > 0 {
				return dto.PlatformGenerationRequest{}, fail(422, "TOC_ASSET_COUNT_INVALID", "文生视频模式不接受参考素材")
			}
		case "image_to_video":
			if len(p.Assets) == 0 || len(p.Assets) > cap.Limits.MaxImages {
				return dto.PlatformGenerationRequest{}, fail(422, "TOC_ASSET_COUNT_INVALID", "图生视频模式需要 1-"+fmt.Sprint(cap.Limits.MaxImages)+" 张参考图")
			}
		case "video_to_video":
			// video_to_video 需要视频 asset，TOC 暂不开放
			return dto.PlatformGenerationRequest{}, fail(422, "TOC_REQUEST_UNSUPPORTED", "视频到视频模式暂不支持")
		}
	} else {
		if len(p.Assets) > cap.Limits.MaxImages || (p.Mode == "image_to_image" && len(p.Assets) == 0) || (p.Mode == "text_to_image" && len(p.Assets) > 0) {
			return dto.PlatformGenerationRequest{}, fail(422, "TOC_ASSET_COUNT_INVALID", "参考图数量不符合所选模式")
		}
	}
	r := dto.PlatformGenerationRequest{Model: m.PublicModelID, ExpectedCapabilityRevision: m.CapabilityRevision, Mode: p.Mode, Inputs: dto.PlatformGenerationInputs{Prompt: p.Prompt, Assets: []dto.PlatformGenerationAssetInput{}}, Output: dto.PlatformGenerationOutputOptions{DurationSeconds: p.DurationSeconds, AspectRatio: p.AspectRatio, Resolution: p.Resolution, Count: p.OutputCount}, Metadata: map[string]any{}}
	seen := map[string]bool{}
	for _, ref := range p.Assets {
		if seen[ref.AssetID] || ref.AssetID == "" || (ref.MediaType != "" && ref.MediaType != "image") || (ref.Role != "" && ref.Role != "reference_image") || !contains(cap.InputMediaTypes, "image") || !contains(cap.InputRoles, "reference_image") {
			return r, fail(422, "TOC_ASSET_ROLE_INVALID", "参考图角色不受支持")
		}
		seen[ref.AssetID] = true
		var asset Asset
		if err := tx.First(&asset, "id = ? AND user_id = ? AND deleted = ?", ref.AssetID, user, false).Error; err != nil {
			return r, fail(404, "TOC_ASSET_NOT_FOUND", "参考素材不存在或不属于当前用户")
		}
		if s.Assets == nil {
			return r, fail(503, "TOC_ASSETS_UNAVAILABLE", "素材存储尚未配置")
		}
		url, err := s.Assets.InputURL(asset, time.Now().UTC().Add(30*time.Minute))
		if err != nil {
			return r, err
		}
		role := "reference_image"
		r.Inputs.Assets = append(r.Inputs.Assets, dto.PlatformGenerationAssetInput{URL: url, MediaType: "image", Role: &role})
	}
	return r, nil
}
func (s *Service) CreateTask(ctx context.Context, user int, r CreateTaskRequest) (Task, error) {
	var result Task
	if !validKey(r.IdempotencyKey) || r.ExpectedCapabilityVersion <= 0 || !digestPattern.MatchString(r.ExpectedQuoteRevision) {
		return result, fail(422, "TOC_QUOTE_REQUIRED", "必须提交当前能力版本、报价版本与幂等标识")
	}
	sha, err := digest(r)
	if err != nil {
		return result, err
	}
	err = s.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		w, err := lockWallet(tx, user)
		if err != nil {
			return err
		}
		err = tx.First(&result, "user_id = ? AND idempotency_key = ?", user, r.IdempotencyKey).Error
		if err == nil {
			if result.RequestSHA256 != sha {
				return fail(409, "IDEMPOTENCY_KEY_REUSED", "同一请求标识对应不同生成内容")
			}
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		var m CatalogModel
		if err = model.LockTOCBusinessRows(tx).First(&m, "id = ?", r.ModelID).Error; err != nil {
			return fail(404, "TOC_MODEL_NOT_FOUND", "模型不存在")
		}
		g, _, err := s.available(tx, user, m)
		if err != nil {
			return err
		}
		quote, err := quoteRevision(m, g)
		if err != nil {
			return err
		}
		if m.Version != r.ExpectedCapabilityVersion || quote != r.ExpectedQuoteRevision {
			return fail(409, "QUOTE_REVISION_MISMATCH", "模型能力或价格已变更，请刷新后重新提交")
		}
		generation, err := s.buildGeneration(tx, user, m, r.RequestPayload)
		if err != nil {
			return err
		}
		// 判断定价模式: 优先看 profile 注册的 DurationSemantics 标注（None=per_item），
		// 避免把 profile 里仅作 legacy sentinel 的 duration_seconds 数组误判为 per_second。
		// 只有无 profile 或 profile 没标注时才退回到数组长度推断。
		var capsForPricing dto.PlatformGenerationCapabilities
		if err := common.Unmarshal([]byte(m.CapabilitiesJSON), &capsForPricing); err != nil {
			return err
		}
		capForPricing, _ := capsForPricing.Modes[r.RequestPayload.Mode]
		pricingMode := "per_item"
		durationSec := int64(0)
		hasDurationCap := len(capForPricing.Limits.DurationSeconds) > 0
		if hasDurationCap && profileDurationApplies(m.PublicModelID, r.RequestPayload.Mode) {
			pricingMode = "per_second"
			durationSec = int64(r.RequestPayload.DurationSeconds)
		}
		var active int64
		if err = tx.Model(&Task{}).Where("user_id = ? AND settlement_state = ?", user, "held").Count(&active).Error; err != nil {
			return err
		}
		if active >= maxConcurrentTasks(user) {
			return fail(409, "TOC_CONCURRENCY_LIMIT", "请先等待当前任务完成或核对未知任务状态")
		}
		var cost int64
		if pricingMode == "per_second" {
			cost = m.UnitPricePoints * durationSec * int64(r.RequestPayload.OutputCount)
		} else {
			cost = m.UnitPricePoints * int64(r.RequestPayload.OutputCount)
		}
		if cost <= 0 || cost > MaxPoints {
			return fail(422, "TOC_QUOTE_INVALID", "计算出的积分无效")
		}
		if err = changeWallet(tx, w, -cost, cost); err != nil {
			return err
		}
		now := time.Now().UTC()
		taskID := uuid.NewString()
		requestJSON, err := encode(r.RequestPayload)
		if err != nil {
			return err
		}
		pricingJSON, err := encode(map[string]any{
			"schema_version":            2,
			"billing_unit":              "POINT",
			"billing_version":           2,
			"charge_policy":             "FIXED_QUOTE_ON_SUCCESS",
			"price_version_id":          m.ID,
			"mode":                      pricingMode,
			"unit_price_points":         m.UnitPricePoints,
			"duration_seconds":          durationSec,
			"quantity":                  r.RequestPayload.OutputCount,
			"quote_points":              cost,
			"quote_revision":            r.ExpectedQuoteRevision,
			"candidate_revision":        m.CapabilityRevision,
			"routing_release_sha256":    m.RoutingReleaseSHA256,
			"provider_cost_readiness_sha256": m.CostReadinessSHA256,
			"credit_kind":               "test_credit",
		})
		if err != nil {
			return err
		}
		result = Task{ID: taskID, UserID: user, IdempotencyKey: r.IdempotencyKey, RequestSHA256: sha, ModelID: m.ID, AdmissionID: uuid.NewString(), RelayJobID: taskID, Status: "queued", RequestJSON: requestJSON, CapabilitiesJSON: m.CapabilitiesJSON, PricingJSON: pricingJSON, QuotePoints: cost, ReservedPoints: cost, SettlementState: "held", CreatedAt: now, UpdatedAt: now}
		if err = tx.Create(&result).Error; err != nil {
			return err
		}
		entry := LedgerEntry{ID: uuid.NewString(), UserID: user, IdempotencyKey: "reserve:" + taskID, RequestSHA256: sha, Kind: "reserve", AvailableDeltaPoints: -cost, ReservedDeltaPoints: cost, TaskID: taskID, ActorUserID: user, Note: "生成任务测试积分预占", CreatedAt: now}
		if err = tx.Create(&entry).Error; err != nil {
			return err
		}
		// Includes the durable job, execution contract and explicit local owner.
		// Any failure rolls back the task, wallet and ledger together.
		return s.Executor.Create(ctx, tx, result, generation, m)
	})
	return result, err
}

// tocOutputsRecovered reports whether the job's OutputsJSON carries a
// complete, valid artifact set matching the task's requested output count.
// Shared by the direct succeeded path and the reconciliation path, where
// Relay's scanner may have recovered outputs after a local response loss.
func tocOutputsRecovered(job *model.PlatformGenerationJob, task Task) bool {
	var artifacts []dto.PlatformGenerationArtifact
	var payload GenerationPayload
	if common.Unmarshal([]byte(job.OutputsJSON), &artifacts) != nil || common.Unmarshal([]byte(task.RequestJSON), &payload) != nil || payload.OutputCount <= 0 || len(artifacts) != payload.OutputCount {
		return false
	}
	for _, artifact := range artifacts {
		if artifact.AssetID == "" || (artifact.MediaType != "image" && artifact.MediaType != "video") || artifact.SizeBytes <= 0 || len(artifact.SHA256) != 64 {
			return false
		}
	}
	return true
}

// ApplyTerminal is safe to replay within the exact generation state transition
// transaction. Unknown submission and transfer states retain the reservation.
func ApplyTerminal(tx *gorm.DB, job *model.PlatformGenerationJob) error {
	if tx == nil || job == nil {
		return fmt.Errorf("TOC terminal transaction missing")
	}
	var task Task
	err := model.LockTOCBusinessRows(tx).First(&task, "relay_job_id = ?", job.ID).Error
	if err != nil {
		return err
	}
	var authority model.RelayGenerationAuthority
	if err = tx.First(&authority, "job_id = ? AND kind = ? AND owner_user_id = ? AND admission_id = ?", job.ID, "relay_toc", task.UserID, task.AdmissionID).Error; err != nil {
		return fmt.Errorf("TOC owner authority mismatch")
	}
	state := ""
	kind := ""
	available := int64(0)
	// User-facing terminal status written back to the task row. Normally the
	// job status passes through unchanged; the reconciliation path normalizes
	// to succeeded/failed once the outcome is proven.
	terminalStatus := job.Status
	switch job.Status {
	case model.PlatformGenerationStatusSucceeded:
		if !tocOutputsRecovered(job, task) {
			return fmt.Errorf("TOC successful output evidence incomplete")
		}
		state = "settled"
		kind = "settle"
	case model.PlatformGenerationStatusFailed, model.PlatformGenerationStatusCancelled:
		definite, err := service.IsLocalTOCDefiniteFailureWithDB(tx, job)
		if err != nil {
			return err
		}
		if !definite {
			return tx.Model(&Task{}).Where("id = ? AND settlement_state = ?", task.ID, "held").Updates(map[string]any{"status": "reconciliation_required", "failure_reason": "TOC_RESULT_UNCERTAIN", "updated_at": time.Now().UTC()}).Error
		}
		state = "released"
		kind = "release"
		available = task.QuotePoints
	case model.PlatformGenerationStatusReconciliationRequired:
		// The durable evidence may have been completed by Relay's
		// reconciliation scanner since the job was flagged. Check success
		// first: if the scanner recovered a complete output set (e.g. the
		// local response was lost but the provider actually delivered), the
		// reservation settles. Otherwise re-run the definite-failure proof;
		// release only when it resolves, keep holding otherwise.
		if tocOutputsRecovered(job, task) {
			state = "settled"
			kind = "settle"
			terminalStatus = model.PlatformGenerationStatusSucceeded
		} else {
			definite, err := service.IsLocalTOCDefiniteFailureWithDB(tx, job)
			if err != nil {
				return err
			}
			if !definite {
				return tx.Model(&Task{}).Where("id = ? AND settlement_state = ?", task.ID, "held").Updates(map[string]any{"status": job.Status, "failure_reason": "TOC_RESULT_UNCERTAIN", "updated_at": time.Now().UTC()}).Error
			}
			state = "released"
			kind = "release"
			available = task.QuotePoints
			terminalStatus = model.PlatformGenerationStatusFailed
		}
	default:
		return tx.Model(&Task{}).Where("id = ? AND settlement_state = ?", task.ID, "held").Updates(map[string]any{"status": job.Status, "updated_at": time.Now().UTC()}).Error
	}
	if task.SettlementState != "held" {
		if task.SettlementState != state {
			return fmt.Errorf("TOC terminal outcome changed")
		}
		return nil
	}
	w, err := lockWallet(tx, task.UserID)
	if err != nil {
		return err
	}
	if task.ReservedPoints != task.QuotePoints {
		return fmt.Errorf("TOC reservation integrity failure")
	}
	if err = changeWallet(tx, w, available, -task.QuotePoints); err != nil {
		return err
	}
	terminalSHA, err := digest(struct {
		TaskID, State string
		Quote         int64
	}{task.ID, state, task.QuotePoints})
	if err != nil {
		return err
	}
	entry := LedgerEntry{ID: uuid.NewString(), UserID: task.UserID, IdempotencyKey: "terminal:" + task.ID, RequestSHA256: terminalSHA, Kind: kind, AvailableDeltaPoints: available, ReservedDeltaPoints: -task.QuotePoints, TaskID: task.ID, ActorUserID: task.UserID, Note: "生成任务测试积分终态核对", CreatedAt: time.Now().UTC()}
	if err = tx.Create(&entry).Error; err != nil {
		return err
	}
	actual := int64(0)
	if state == "settled" {
		actual = task.QuotePoints
	}
	// A settled task succeeded: drop any historical error snapshot so the
	// user-facing row does not carry a failure reason next to status=succeeded.
	failureReason := ""
	if state == "released" {
		failureReason = job.ErrorCode
	}
	return tx.Model(&Task{}).Where("id = ? AND settlement_state = ?", task.ID, "held").Updates(map[string]any{"status": terminalStatus, "settlement_state": state, "reserved_points": 0, "actual_cost_points": actual, "failure_reason": failureReason, "updated_at": time.Now().UTC()}).Error
}
func (s *Service) Task(ctx context.Context, user int, id string) (map[string]any, error) {
	var task Task
	if err := s.DB.First(&task, "id = ? AND user_id = ?", id, user).Error; err != nil {
		return nil, fail(404, "TOC_TASK_NOT_FOUND", "任务不存在")
	}
	snapshot, err := s.Executor.Snapshot(ctx, task)
	if err != nil {
		return nil, err
	}
	// Recover a previously committed terminal job if a process crashed before
	// response delivery; never query providers or resubmit from this read path.
	// Also covers reconciliation_required — the durable evidence may have been
	// completed by Relay's reconciliation scanner since the last visit.
	if snapshot.Status == model.PlatformGenerationStatusSucceeded || snapshot.Status == model.PlatformGenerationStatusFailed || snapshot.Status == model.PlatformGenerationStatusCancelled || snapshot.Status == model.PlatformGenerationStatusReconciliationRequired {
		err = s.DB.Transaction(func(tx *gorm.DB) error {
			var job model.PlatformGenerationJob
			if err := tx.First(&job, "id = ?", task.RelayJobID).Error; err != nil {
				return err
			}
			return ApplyTerminal(tx, &job)
		})
		if err != nil {
			return nil, err
		}
		if err = s.DB.First(&task, "id = ?", id).Error; err != nil {
			return nil, err
		}
	}
	outputs := []any{}
	if snapshot.Status == model.PlatformGenerationStatusSucceeded || task.SettlementState == "settled" {
		for _, artifact := range snapshot.Outputs {
			outputs = append(outputs, map[string]any{"asset_id": artifact.AssetID, "media_type": artifact.MediaType, "content_type": artifact.ContentType, "size_bytes": artifact.SizeBytes, "sha256": artifact.SHA256})
		}
	}
	status := snapshot.Status
	if status == "processing" || status == "submitting" || status == "transferring" {
		status = "running"
	}
	if task.SettlementState == "held" && (snapshot.Status == model.PlatformGenerationStatusFailed || snapshot.Status == model.PlatformGenerationStatusCancelled) {
		status = "reconciliation_required"
	}
	// After a terminal settlement the task row carries the user-facing
	// outcome; the reconciliation path normalizes it to succeeded/failed
	// while the job row keeps the raw reconciliation_required marker.
	if task.SettlementState == "settled" || task.SettlementState == "released" {
		status = task.Status
	}
	requestPayload, err := object(task.RequestJSON)
	if err != nil {
		return nil, err
	}
	pricing, err := object(task.PricingJSON)
	if err != nil {
		return nil, err
	}
	capabilities, err := object(task.CapabilitiesJSON)
	if err != nil {
		return nil, err
	}
	return map[string]any{"id": task.ID, "idempotency_key": task.IdempotencyKey, "workspace_id": workspace(user), "user_id": fmt.Sprint(user), "model_id": task.ModelID, "status": status, "request_payload": requestPayload, "quote_points": task.QuotePoints, "pricing_snapshot": pricing, "capability_snapshot": capabilities, "reserved_points": task.ReservedPoints, "actual_cost_points": task.ActualCostPoints, "relay_job_id": task.RelayJobID, "output_artifacts": outputs, "failure_reason": task.FailureReason, "relay_error_snapshot": snapshot.Error, "created_at": task.CreatedAt, "updated_at": task.UpdatedAt, "billing_unit": "POINT", "billing_version": 2, "billing_scope": "personal", "billing_authority": "relay_toc", "backend_mode": "relay_toc", "credit_kind": "test_credit"}, nil
}
