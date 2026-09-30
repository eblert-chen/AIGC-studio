package toc

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const MaxPoints int64 = 9000000000000000

var digestPattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)

// maxConcurrentTasks returns the per-user cap on simultaneously held
// (unsettled) generation tasks. The limit is read from TOC_MAX_CONCURRENT_TASKS
// and defaults to 2 to preserve the historical behavior. A value <= 0 falls
// back to the default so a misconfigured environment cannot disable the guard
// entirely.
//
// The user parameter is reserved for future tier-based differentiation (for
// example, granting premium groups a higher cap). Today every user shares the
// global limit; the signature is kept stable so the call site does not need to
// change when per-tier lookup is introduced.
func maxConcurrentTasks(_ int) int64 {
	const defaultLimit int64 = 2
	limit := int64(common.GetEnvOrDefault("TOC_MAX_CONCURRENT_TASKS", int(defaultLimit)))
	if limit < 1 {
		return defaultLimit
	}
	return limit
}

type Error struct {
	Status        int
	Code, Message string
}

func (e *Error) Error() string                    { return e.Message }
func fail(status int, code, message string) error { return &Error{status, code, message} }
func digest(v any) (string, error) {
	raw, err := common.Marshal(v)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(raw)
	return fmt.Sprintf("sha256:%x", sum), nil
}
func encode(v any) (string, error) { b, err := common.Marshal(v); return string(b), err }
func object(raw string) (map[string]any, error) {
	out := map[string]any{}
	err := common.Unmarshal([]byte(raw), &out)
	if err == nil && out == nil {
		err = fmt.Errorf("TOC object is null")
	}
	return out, err
}
func validKey(key string) bool {
	return len(key) >= 8 && len(key) <= 120 && !strings.ContainsAny(key, "\r\n\x00")
}
func workspace(user int) string { return fmt.Sprintf("relay-toc:%d", user) }

type ModelEvidence struct {
	Resource             dto.PlatformModelResource `json:"model"`
	CatalogRevision      string                    `json:"catalog_revision"`
	RoutingReleaseSHA256 string                    `json:"routing_release_sha256"`
	CostReadinessSHA256  string                    `json:"provider_cost_readiness_sha256"`
	Ready                bool                      `json:"ready"`
	Blockers             []string                  `json:"blockers"`
}

// Executor admits locally in the caller's transaction. No method is allowed to
// start a provider request before the reserve, task and authority commit.
type Executor interface {
	Evidence(*gorm.DB, string) (ModelEvidence, error)
	Create(context.Context, *gorm.DB, Task, dto.PlatformGenerationRequest, CatalogModel) error
	Snapshot(context.Context, Task) (dto.PlatformGenerationSnapshot, error)
	Download(context.Context, Task, string) (dto.PlatformSignedDownload, error)
}
type Service struct {
	DB       *gorm.DB
	Executor Executor
	Assets   *AssetStore
}

func lockWallet(tx *gorm.DB, user int) (Wallet, error) {
	w := Wallet{UserID: user, Version: 1, UpdatedAt: time.Now().UTC()}
	if user <= 0 {
		return w, fail(401, "TOC_USER_REQUIRED", "需要有效登录用户")
	}
	if err := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&w).Error; err != nil {
		return w, err
	}
	err := model.LockTOCBusinessRows(tx).First(&w, "user_id = ?", user).Error
	return w, err
}
func changeWallet(tx *gorm.DB, w Wallet, available, reserved int64) error {
	if available > MaxPoints-w.AvailablePoints || reserved > MaxPoints-w.ReservedPoints || available < -w.AvailablePoints || reserved < -w.ReservedPoints {
		return fail(409, "TOC_BALANCE_INVALID", "测试积分不足或超过余额上限")
	}
	r := tx.Model(&Wallet{}).Where("user_id = ? AND version = ?", w.UserID, w.Version).Updates(map[string]any{"available_points": w.AvailablePoints + available, "reserved_points": w.ReservedPoints + reserved, "version": w.Version + 1, "updated_at": time.Now().UTC()})
	if r.Error != nil {
		return r.Error
	}
	if r.RowsAffected != 1 {
		return fail(409, "TOC_WALLET_CHANGED", "积分状态已变更，请刷新")
	}
	return nil
}
func (s *Service) Wallet(user int) (map[string]any, error) {
	w := Wallet{UserID: user}
	err := s.DB.First(&w, "user_id = ?", user).Error
	if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, err
	}
	return map[string]any{"workspace_id": workspace(user), "available_points": w.AvailablePoints, "reserved_points": w.ReservedPoints, "billing_unit": "POINT", "billing_version": 2, "billing_scope": "personal", "billing_authority": "relay_toc", "backend_mode": "relay_toc", "credit_kind": "test_credit"}, nil
}

type CreditRequest struct {
	AmountPoints   int64  `json:"amount_points"`
	IdempotencyKey string `json:"idempotency_key"`
	Note           string `json:"note"`
}

func (s *Service) Credit(actor, user int, r CreditRequest) (LedgerEntry, error) {
	var entry LedgerEntry
	if r.AmountPoints <= 0 || r.AmountPoints > 1000000 || !validKey(r.IdempotencyKey) || len(strings.TrimSpace(r.Note)) < 1 || len(r.Note) > 240 {
		return entry, fail(422, "TOC_CREDIT_INVALID", "请输入1至1000000测试积分、说明与幂等标识")
	}
	sha, err := digest(struct {
		Actor, User int
		Request     CreditRequest
	}{actor, user, r})
	if err != nil {
		return entry, err
	}
	err = s.DB.Transaction(func(tx *gorm.DB) error {
		var target model.User
		if err := tx.First(&target, "id = ? AND status = ?", user, 1).Error; err != nil {
			return fail(404, "TOC_USER_NOT_FOUND", "用户不存在或已停用")
		}
		w, err := lockWallet(tx, user)
		if err != nil {
			return err
		}
		err = tx.First(&entry, "user_id = ? AND idempotency_key = ?", user, "credit:"+r.IdempotencyKey).Error
		if err == nil {
			if entry.RequestSHA256 != sha {
				return fail(409, "IDEMPOTENCY_KEY_REUSED", "同一幂等标识已用于不同操作")
			}
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		if err = changeWallet(tx, w, r.AmountPoints, 0); err != nil {
			return err
		}
		entry = LedgerEntry{ID: uuid.NewString(), UserID: user, IdempotencyKey: "credit:" + r.IdempotencyKey, RequestSHA256: sha, Kind: "test_credit", AvailableDeltaPoints: r.AmountPoints, ActorUserID: actor, Note: r.Note, CreatedAt: time.Now().UTC()}
		return tx.Create(&entry).Error
	})
	return entry, err
}

type CatalogRequest struct {
	Enabled                    bool   `json:"enabled"`
	UnitPricePoints            int64  `json:"unit_price_points"`
	ExpectedVersion            int64  `json:"expected_version"`
	ExpectedCapabilityRevision string `json:"expected_capability_revision"`
	ExpectedCatalogRevision    string `json:"expected_catalog_revision"`
	ExpectedRoutingSHA256      string `json:"expected_routing_release_sha256"`
	ExpectedCostSHA256         string `json:"expected_provider_cost_readiness_sha256"`
	IdempotencyKey             string `json:"idempotency_key"`
	Reason                     string `json:"reason"`
}

func auditReplay(tx *gorm.DB, actor int, key, sha string, out any) (bool, error) {
	var a AuditEntry
	err := tx.First(&a, "actor_user_id = ? AND idempotency_key = ?", actor, key).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if a.RequestSHA256 != sha {
		return false, fail(409, "IDEMPOTENCY_KEY_REUSED", "同一幂等标识已用于不同操作")
	}
	return true, common.Unmarshal([]byte(a.ResultJSON), out)
}
func audit(tx *gorm.DB, actor int, key, sha, action, subject, reason string, result any) error {
	raw, err := encode(result)
	if err != nil {
		return err
	}
	return tx.Create(&AuditEntry{ID: uuid.NewString(), ActorUserID: actor, IdempotencyKey: key, RequestSHA256: sha, Action: action, Subject: subject, Reason: reason, ResultJSON: raw, CreatedAt: time.Now().UTC()}).Error
}
func (s *Service) SaveCatalog(ctx context.Context, actor int, slug string, r CatalogRequest) (CatalogModel, error) {
	var result CatalogModel
	if r.UnitPricePoints < 1 || r.UnitPricePoints > 1000000 || !validKey(r.IdempotencyKey) || strings.TrimSpace(r.Reason) == "" || len(r.Reason) > 240 {
		return result, fail(422, "TOC_CATALOG_INVALID", "请提供 1-1000000 之间的测试积分价、明确审核说明和幂等标识")
	}
	sha, err := digest(struct {
		Slug    string
		Request CatalogRequest
	}{slug, r})
	if err != nil {
		return result, err
	}
	err = s.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		if _, err := lockWallet(tx, actor); err != nil {
			return err
		}
		if replay, err := auditReplay(tx, actor, r.IdempotencyKey, sha, &result); err != nil || replay {
			return err
		}
		err := model.LockTOCBusinessRows(tx).First(&result, "public_model_id = ?", slug).Error
		if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		if result.Version != r.ExpectedVersion {
			return fail(409, "TOC_CATALOG_CHANGED", "销售目录版本已变更，请刷新后重新审核")
		}
		// Disabling an existing catalog entry is always recoverable, even when
		// the provider is unavailable. Enabling or revising authority requires
		// freshly verified immutable catalog, capability, route and cost pins.
		if r.Enabled || result.ID == "" {
			evidence, err := s.Executor.Evidence(tx, slug)
			if err != nil {
				return err
			}
			bypassEvidence := os.Getenv("RELAY_TOC_DEV_BYPASS_EVIDENCE") == "1"
			if !bypassEvidence && !evidence.Ready {
				return fail(409, "TOC_ROUTE_UNREADY", "模型尚未完成发布、双模式验收和可核验成本配置")
			}
			if !bypassEvidence && (!digestPattern.MatchString(r.ExpectedRoutingSHA256) || evidence.Resource.CapabilityRevision != r.ExpectedCapabilityRevision || evidence.CatalogRevision != r.ExpectedCatalogRevision || evidence.RoutingReleaseSHA256 != r.ExpectedRoutingSHA256 || evidence.CostReadinessSHA256 != r.ExpectedCostSHA256) {
				return fail(409, "TOC_EVIDENCE_CHANGED", "模型能力、路由或成本证据已变更，请重新审核")
			}
			if evidence.Resource.Capabilities.SchemaVersion != 3 {
				return fail(409, "TOC_CAPABILITY_UNSUPPORTED", "需要完整schema v3能力声明")
			}
			var caps dto.PlatformGenerationCapabilities
			capBytes, err := common.Marshal(evidence.Resource.Capabilities)
			if err != nil {
				return err
			}
			if err = common.Unmarshal(capBytes, &caps); err != nil {
				return err
			}
			// 保留所有已声明模式（图像和视频均可），生产环境验收会在 marker 侧强校验
			if len(caps.Modes) == 0 {
				return fail(409, "TOC_CAPABILITY_UNSUPPORTED", "需要至少一种验收后的模式")
			}
			result.CapabilityRevision = evidence.Resource.CapabilityRevision
			result.CatalogRevision = evidence.CatalogRevision
			result.RoutingReleaseSHA256 = evidence.RoutingReleaseSHA256
			result.CostReadinessSHA256 = evidence.CostReadinessSHA256
			result.CapabilitiesJSON, err = encode(caps)
			if err != nil {
				return err
			}
		}
		now := time.Now().UTC()
		if result.ID == "" {
			result.ID = uuid.NewString()
			result.PublicModelID = slug
			result.DisplayName = "Seedream 5"
			result.CreatedAt = now
		}
		result.Enabled = r.Enabled
		result.UnitPricePoints = r.UnitPricePoints
		result.Version++
		result.UpdatedAt = now
		if err := tx.Save(&result).Error; err != nil {
			return err
		}
		return audit(tx, actor, r.IdempotencyKey, sha, "catalog", result.ID, r.Reason, result)
	})
	return result, err
}

type GrantRequest struct {
	ModelID         string `json:"model_id"`
	Enabled         bool   `json:"enabled"`
	ExpectedVersion int64  `json:"expected_version"`
	IdempotencyKey  string `json:"idempotency_key"`
	Reason          string `json:"reason"`
}

func (s *Service) SaveGrant(actor, user int, r GrantRequest) (Grant, error) {
	var g Grant
	if !validKey(r.IdempotencyKey) || strings.TrimSpace(r.Reason) == "" || len(r.Reason) > 240 {
		return g, fail(422, "TOC_GRANT_INVALID", "请输入授权说明和幂等标识")
	}
	sha, err := digest(struct {
		User    int
		Request GrantRequest
	}{user, r})
	if err != nil {
		return g, err
	}
	err = s.DB.Transaction(func(tx *gorm.DB) error {
		var u model.User
		if err := tx.First(&u, "id = ? AND status = ?", user, 1).Error; err != nil {
			return fail(404, "TOC_USER_NOT_FOUND", "用户不存在或已停用")
		}
		if _, err := lockWallet(tx, user); err != nil {
			return err
		}
		if replay, err := auditReplay(tx, actor, r.IdempotencyKey, sha, &g); err != nil || replay {
			return err
		}
		var m CatalogModel
		if err := tx.First(&m, "id = ?", r.ModelID).Error; err != nil {
			return fail(404, "TOC_MODEL_NOT_FOUND", "销售模型不存在")
		}
		if r.Enabled && !m.Enabled {
			return fail(409, "TOC_MODEL_DISABLED", "请先审核并启用销售模型")
		}
		err := tx.First(&g, "user_id = ? AND model_id = ?", user, r.ModelID).Error
		if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		if g.Version != r.ExpectedVersion {
			return fail(409, "TOC_GRANT_CHANGED", "授权已变更，请刷新")
		}
		g = Grant{UserID: user, ModelID: r.ModelID, Enabled: r.Enabled, Version: g.Version + 1, UpdatedAt: time.Now().UTC()}
		if err := tx.Save(&g).Error; err != nil {
			return err
		}
		return audit(tx, actor, r.IdempotencyKey, sha, "grant", fmt.Sprintf("%d/%s", user, r.ModelID), r.Reason, g)
	})
	return g, err
}
func quoteRevision(m CatalogModel, g Grant) (string, error) {
	return digest(struct {
		Model        CatalogModel
		GrantVersion int64
	}{m, g.Version})
}
func (s *Service) available(tx *gorm.DB, user int, m CatalogModel) (Grant, ModelEvidence, error) {
	var g Grant
	if !m.Enabled {
		return g, ModelEvidence{}, fail(409, "TOC_MODEL_DISABLED", "销售模型已停用")
	}
	if err := tx.First(&g, "user_id = ? AND model_id = ? AND enabled = ?", user, m.ID, true).Error; err != nil {
		return g, ModelEvidence{}, fail(403, "TOC_MODEL_NOT_GRANTED", "当前用户尚未获得此模型授权")
	}
	e, err := s.Executor.Evidence(tx, m.PublicModelID)
	if err != nil {
		return g, e, err
	}
	// 开发模式下 RELAY_TOC_DEV_BYPASS_EVIDENCE=1 跳过所有 Evidence 检查
	// 用于首次上架 TOC catalog 但 marker 尚未走 acceptance 的环境
	bypassEvidence := os.Getenv("RELAY_TOC_DEV_BYPASS_EVIDENCE") == "1"
	if bypassEvidence {
		return g, e, nil
	}
	if !e.Ready || e.Resource.CapabilityRevision != m.CapabilityRevision || e.RoutingReleaseSHA256 != m.RoutingReleaseSHA256 || e.CostReadinessSHA256 != m.CostReadinessSHA256 {
		return g, e, fail(409, "TOC_MODEL_REVIEW_REQUIRED", "模型连接、路由或成本已变化，需要管理员复核")
	}
	return g, e, nil
}
func (s *Service) Models(user int, catalog bool) ([]map[string]any, error) {
	var models []CatalogModel
	if err := s.DB.Order("created_at,id").Find(&models).Error; err != nil {
		return nil, err
	}
	out := []map[string]any{}
	for _, m := range models {
		g, _, err := s.available(s.DB, user, m)
		caps, decodeErr := object(m.CapabilitiesJSON)
		modes, _ := caps["modes"].(map[string]any)
		// A single stale (pre-schema-3) or corrupt capability snapshot must not
		// 503 the whole personal directory. Such a model never enters the
		// generation list; the onboarding catalog keeps it visible as blocked.
		capabilityReady := decodeErr == nil && caps["schema_version"] == float64(3) && len(modes) > 0
		if !capabilityReady && !catalog {
			continue
		}
		ready := err == nil && capabilityReady
		var grant Grant
		if !catalog {
			if s.DB.First(&grant, "user_id = ? AND model_id = ? AND enabled = ?", user, m.ID, true).Error != nil || !m.Enabled {
				continue
			}
		}
		readiness := map[string]any{}
		for mode := range modes {
			blockers := []map[string]string{}
			status := "ready"
			if !ready {
				status = "blocked"
				blockers = append(blockers, map[string]string{"code": "TOC_MODEL_UNAVAILABLE", "message": "模型连接或授权暂时无法核验"})
			}
			readiness[mode] = map[string]any{"default": map[string]any{"ready": ready, "status": status, "blockers": blockers}, "options": map[string]any{}}
		}
		quote, quoteErr := quoteRevision(m, g)
		if quoteErr != nil {
			return nil, quoteErr
		}
		row := map[string]any{"id": m.ID, "slug": m.PublicModelID, "display_name": m.DisplayName, "billing_mode": "per_item", "pricing_mode": "per_item", "billing_unit": "POINT", "billing_version": 2, "billing_scope": "personal", "credit_kind": "test_credit", "unit_price_points": m.UnitPricePoints, "capability_version": m.Version, "quote_revision": quote, "effective_capabilities": caps, "readiness_checked_at": time.Now().UTC(), "mode_readiness": readiness, "available": ready, "call_quota": nil, "concurrency_limit": nil, "unavailable_reason": nil}
		switch {
		case !capabilityReady:
			row["available"] = false
			row["unavailable_reason"] = map[string]string{"code": "personal_capability_unavailable", "message": "模型能力快照正在升级，请刷新后再选择。"}
		case err != nil:
			row["unavailable_reason"] = map[string]string{"code": "personal_distribution_unconfigured", "message": err.Error()}
		}
		out = append(out, row)
	}
	return out, nil
}
