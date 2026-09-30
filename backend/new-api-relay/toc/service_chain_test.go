package toc

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

// 端到端链路验证：CreateTask → 供应商 mock 成功 → ApplyTerminal settle → Task() 查产物
// 验证从用户提交到产物可下载的完整链路是否连通（不花真钱，全部 mock）
func TestTOCArtifactChainEndToEnd(t *testing.T) {
	t.Setenv("RELAY_TOC_DEV_BYPASS_EVIDENCE", "1")
	db, err := gorm.Open(sqlite.Open("file:toc-chain-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	sqlDB.SetMaxOpenConns(1)
	t.Cleanup(func() { _ = sqlDB.Close() })

	// 建表
	require.NoError(t, db.AutoMigrate(&model.User{}, &model.UserSession{}, &model.Log{}, &model.PlatformGenerationJob{}, &model.PlatformGenerationRouteAdmission{}, &model.PlatformGenerationReconciliationEvent{}, &model.PlatformProviderTerminalOutcome{}))
	require.NoError(t, model.MigrateRelayTOCSchemaV12(db))

	// 创建用户
	require.NoError(t, db.Create(&model.User{Id: 2, Username: "toc-test-user", DisplayName: "Test User", Password: "no", Role: common.RoleCommonUser, Status: common.UserStatusEnabled, Group: "default", AuthVersion: 1}).Error)

	// 准备 seedream-5 能力（per_item 图像模式，两个都要有）
	sha := func(letter string) string { return "sha256:" + strings.Repeat(letter, 64) }
	caps := dto.PlatformGenerationCapabilities{
		SchemaVersion: 3,
		Modes: map[string]dto.PlatformModeCapability{
			"text_to_image": {
				InputMediaTypes: []string{}, InputRoles: []string{}, TemporalControls: []string{},
				StructuredInputs: []string{}, RequiredResourceKeys: []string{},
				Limits: dto.PlatformCapabilityLimits{MaxPromptLength: 1000, AspectRatios: []string{"1:1"}, Resolutions: []string{"2k"}, OutputCounts: []int{1}},
			},
			"image_to_image": {
				InputMediaTypes: []string{"image"}, InputRoles: []string{"reference_image"}, TemporalControls: []string{},
				StructuredInputs: []string{}, RequiredResourceKeys: []string{},
				Limits: dto.PlatformCapabilityLimits{MaxPromptLength: 1000, MaxImages: 1, AspectRatios: []string{"1:1"}, Resolutions: []string{"2k"}, OutputCounts: []int{1}},
			},
		},
	}
	evidence := ModelEvidence{
		Resource:             dto.PlatformModelResource{ID: "seedream-5", CapabilityRevision: sha("a"), ManagedRoute: true, CustomerCallable: true, Capabilities: caps},
		CatalogRevision:      sha("b"),
		RoutingReleaseSHA256: sha("c"),
		CostReadinessSHA256:  sha("d"),
		Ready:                true,
		Blockers:             []string{},
	}
	executor := &fakeExecutor{db: db, evidence: evidence}

	tmpDir := t.TempDir()
	assets, err := NewAssetStore(tmpDir, "http://127.0.0.1:14340", strings.Repeat("s", 40))
	require.NoError(t, err)
	s := &Service{DB: db, Executor: executor, Assets: assets}

	// Step 1: SaveCatalog 上架模型
	m, err := s.SaveCatalog(context.Background(), 1, "seedream-5", CatalogRequest{
		Enabled: true, UnitPricePoints: 4,
		ExpectedCapabilityRevision: evidence.Resource.CapabilityRevision,
		ExpectedCatalogRevision:    evidence.CatalogRevision,
		ExpectedRoutingSHA256:      evidence.RoutingReleaseSHA256,
		ExpectedCostSHA256:         evidence.CostReadinessSHA256,
		IdempotencyKey: uuid.NewString(), Reason: "e2e chain test",
	})
	require.NoError(t, err, "Step 1 SaveCatalog 失败")

	// Step 2: SaveGrant 授权
	_, err = s.SaveGrant(1, 2, GrantRequest{ModelID: m.ID, Enabled: true, IdempotencyKey: uuid.NewString(), Reason: "e2e grant"})
	require.NoError(t, err, "Step 2 SaveGrant 失败")

	// Step 3: Credit 充积分
	_, err = s.Credit(1, 2, CreditRequest{AmountPoints: 20, IdempotencyKey: uuid.NewString(), Note: "e2e credit"})
	require.NoError(t, err, "Step 3 Credit 失败")

	// Step 4: 拿 quote_revision，构造 CreateTask 请求
	models, err := s.Models(2, false)
	require.NoError(t, err)
	quoteRevision := models[0]["quote_revision"].(string)

	req := CreateTaskRequest{
		ModelID: m.ID, IdempotencyKey: uuid.NewString(),
		ExpectedCapabilityVersion: m.Version, ExpectedQuoteRevision: quoteRevision,
		RequestPayload: GenerationPayload{Mode: "text_to_image", Prompt: "一只可爱的猫", AspectRatio: "1:1", Resolution: "2k", OutputCount: 1},
	}

	// Step 5: CreateTask → reserve 积分 + 创建 job
	task, err := s.CreateTask(context.Background(), 2, req)
	require.NoError(t, err, "Step 5 CreateTask 失败")
	t.Logf("✓ CreateTask 成功: task=%s quote=%d reserved=%d state=%s", task.ID, task.QuotePoints, task.ReservedPoints, task.SettlementState)

	// 验证 reserve 成功
	wallet, _ := s.Wallet(2)
	require.Equal(t, int64(16), wallet["available_points"], "reserve 后 available 应为 16")
	require.Equal(t, int64(4), wallet["reserved_points"], "reserve 后 reserved 应为 4")

	var reserveCount int64
	require.NoError(t, db.Model(&LedgerEntry{}).Where("kind = ?", "reserve").Count(&reserveCount).Error)
	require.Equal(t, int64(1), reserveCount, "应有 1 条 reserve 分录")

	// Step 6: mock 供应商成功返回产物
	var job model.PlatformGenerationJob
	require.NoError(t, db.First(&job, "id = ?", task.ID).Error)
	artifactID := uuid.NewString()
	artifactSHA := strings.Repeat("9", 64)
	job.Status = "succeeded"
	job.OutputsJSON = fmt.Sprintf(`[{"asset_id":"%s","media_type":"image","content_type":"image/png","size_bytes":1024,"sha256":"%s"}]`, artifactID, artifactSHA)
	require.NoError(t, db.Save(&job).Error)
	t.Logf("✓ mock 供应商回调: job.Status=succeeded, artifact.asset_id=%s", artifactID)

	// Step 7: ApplyTerminal settle
	require.NoError(t, db.Transaction(func(tx *gorm.DB) error {
		return ApplyTerminal(tx, &job)
	}))
	t.Logf("✓ ApplyTerminal settle 完成")

	// 验证 settle 成功
	wallet, _ = s.Wallet(2)
	require.Equal(t, int64(16), wallet["available_points"], "settle 后 available 不变（扣 reserved）")
	require.Equal(t, int64(0), wallet["reserved_points"], "settle 后 reserved 应为 0")

	var settleCount int64
	require.NoError(t, db.Model(&LedgerEntry{}).Where("kind = ?", "settle").Count(&settleCount).Error)
	require.Equal(t, int64(1), settleCount, "应有 1 条 settle 分录")

	// 任务终态验证
	var finalTask model.TOCTask
	require.NoError(t, db.First(&finalTask, "id = ?", task.ID).Error)
	require.Equal(t, "settled", finalTask.SettlementState)
	require.NotNil(t, finalTask.ActualCostPoints)
	require.Equal(t, int64(4), *finalTask.ActualCostPoints)

	// Step 8: Task() 查询 → 验证 output_artifacts 返回给前端
	result, err := s.Task(context.Background(), 2, task.ID)
	require.NoError(t, err, "Step 8 Task() 查询失败")

	artifacts, ok := result["output_artifacts"].([]any)
	require.True(t, ok, "output_artifacts 应为数组")
	require.Len(t, artifacts, 1, "应有 1 个 artifact")

	firstArtifact := artifacts[0].(map[string]any)
	require.Equal(t, artifactID, firstArtifact["asset_id"], "artifact asset_id 应匹配 mock")
	require.Equal(t, "image", firstArtifact["media_type"])
	require.NotEmpty(t, firstArtifact["sha256"])
	t.Logf("✓ Task() 返回产物: asset_id=%s, media_type=%s", firstArtifact["asset_id"], firstArtifact["media_type"])

	// 验证 pricing_snapshot 里有 mode 和 quote
	pricing, _ := result["pricing_snapshot"].(map[string]any)
	require.Equal(t, "per_item", pricing["mode"], "per_item 定价模式")
	require.Equal(t, float64(4), pricing["quote_points"], "quote_points=4")
	t.Logf("✓ pricing_snapshot: mode=%s, quote=%v", pricing["mode"], pricing["quote_points"])

	t.Log("\n===== 端到端链路验证全部通过 =====")
	t.Log("CreateTask → reserve → mock 供应商成功 → settle → Task() 查产物 ✓")
}

// 视频模型端到端验证（per_second 定价）
func TestTOCVideoPerSecondChain(t *testing.T) {
	t.Setenv("RELAY_TOC_DEV_BYPASS_EVIDENCE", "1")
	db, err := gorm.Open(sqlite.Open("file:toc-video-"+uuid.NewString()+"?mode=memory&cache=shared"), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	sqlDB.SetMaxOpenConns(1)
	t.Cleanup(func() { _ = sqlDB.Close() })

	require.NoError(t, db.AutoMigrate(&model.User{}, &model.UserSession{}, &model.Log{}, &model.PlatformGenerationJob{}, &model.PlatformGenerationRouteAdmission{}, &model.PlatformGenerationReconciliationEvent{}, &model.PlatformProviderTerminalOutcome{}))
	require.NoError(t, model.MigrateRelayTOCSchemaV12(db))

	require.NoError(t, db.Create(&model.User{Id: 2, Username: "toc-video-user", DisplayName: "Video User", Password: "no", Role: common.RoleCommonUser, Status: common.UserStatusEnabled, Group: "default", AuthVersion: 1}).Error)

	sha := func(letter string) string { return "sha256:" + strings.Repeat(letter, 64) }
	// Seedance 2.0: text_to_video, duration_seconds=[4,5,6]
	caps := dto.PlatformGenerationCapabilities{
		SchemaVersion: 3,
		Modes: map[string]dto.PlatformModeCapability{
			"text_to_video": {
				InputMediaTypes: []string{}, InputRoles: []string{}, TemporalControls: []string{},
				StructuredInputs: []string{}, RequiredResourceKeys: []string{},
				Limits: dto.PlatformCapabilityLimits{
					MaxPromptLength: 2000, AspectRatios: []string{"16:9"}, Resolutions: []string{"720p"},
					OutputCounts:    []int{1},
					DurationSeconds: []int{4, 5, 6}, // 关键: 有 DurationSeconds → per_second 定价
				},
			},
		},
	}
	evidence := ModelEvidence{
		Resource: dto.PlatformModelResource{ID: "seedance-2.0", CapabilityRevision: sha("v"), ManagedRoute: true, CustomerCallable: true, Capabilities: caps},
		CatalogRevision:      sha("w"),
		RoutingReleaseSHA256: sha("x"),
		CostReadinessSHA256:  sha("y"),
		Ready:                true,
		Blockers:             []string{},
	}
	executor := &fakeExecutor{db: db, evidence: evidence}
	assets, err := NewAssetStore(t.TempDir(), "http://127.0.0.1:14340", strings.Repeat("s", 40))
	require.NoError(t, err)
	s := &Service{DB: db, Executor: executor, Assets: assets}

	m, err := s.SaveCatalog(context.Background(), 1, "seedance-2.0", CatalogRequest{
		Enabled: true, UnitPricePoints: 10, // 10 pts/秒
		ExpectedCapabilityRevision: evidence.Resource.CapabilityRevision,
		ExpectedCatalogRevision:    evidence.CatalogRevision,
		ExpectedRoutingSHA256:      evidence.RoutingReleaseSHA256,
		ExpectedCostSHA256:         evidence.CostReadinessSHA256,
		IdempotencyKey: uuid.NewString(), Reason: "video e2e test",
	})
	require.NoError(t, err)

	_, err = s.SaveGrant(1, 2, GrantRequest{ModelID: m.ID, Enabled: true, IdempotencyKey: uuid.NewString(), Reason: "video grant"})
	require.NoError(t, err)

	_, err = s.Credit(1, 2, CreditRequest{AmountPoints: 100, IdempotencyKey: uuid.NewString(), Note: "video credit"})
	require.NoError(t, err)

	models, err := s.Models(2, false)
	require.NoError(t, err)
	quoteRevision := models[0]["quote_revision"].(string)

	// 请求: 5 秒视频 → cost = 10 pts/秒 × 5 秒 × 1 = 50 pts
	req := CreateTaskRequest{
		ModelID: m.ID, IdempotencyKey: uuid.NewString(),
		ExpectedCapabilityVersion: m.Version, ExpectedQuoteRevision: quoteRevision,
		RequestPayload: GenerationPayload{
			Mode: "text_to_video", Prompt: "一只猫在草地上奔跑",
			AspectRatio: "16:9", Resolution: "720p", OutputCount: 1,
			DurationSeconds: 5,
		},
	}

	task, err := s.CreateTask(context.Background(), 2, req)
	require.NoError(t, err, "视频 CreateTask 失败")

	// 验证 per_second cost 计算: 10 × 5 × 1 = 50
	require.Equal(t, int64(50), task.QuotePoints, "per_second cost 应为 50")
	require.Equal(t, int64(50), task.ReservedPoints)

	wallet, _ := s.Wallet(2)
	require.Equal(t, int64(50), wallet["available_points"])
	require.Equal(t, int64(50), wallet["reserved_points"])
	t.Logf("✓ 视频 reserve: quote=%d reserved=%d (per_second: 10pts/秒×5秒×1)", task.QuotePoints, task.ReservedPoints)

	// mock 供应商成功
	var job model.PlatformGenerationJob
	require.NoError(t, db.First(&job, "id = ?", task.ID).Error)
	artifactID := uuid.NewString()
	job.Status = "succeeded"
	job.OutputsJSON = fmt.Sprintf(`[{"asset_id":"%s","media_type":"video","content_type":"video/mp4","size_bytes":2048,"sha256":"%s"}]`, artifactID, strings.Repeat("5", 64))
	require.NoError(t, db.Save(&job).Error)
	require.NoError(t, db.Transaction(func(tx *gorm.DB) error { return ApplyTerminal(tx, &job) }))

	wallet, _ = s.Wallet(2)
	require.Equal(t, int64(50), wallet["available_points"], "settle 后 available")
	require.Equal(t, int64(0), wallet["reserved_points"], "settle 后 reserved=0")

	// 验证 Task() 返回
	result, err := s.Task(context.Background(), 2, task.ID)
	require.NoError(t, err)
	pricing, _ := result["pricing_snapshot"].(map[string]any)
	require.Equal(t, "per_second", pricing["mode"], "应为 per_second 定价")
	require.Equal(t, float64(5), pricing["duration_seconds"], "duration=5")
	require.Equal(t, float64(50), pricing["quote_points"], "quote=50")

	t.Logf("✓ 视频 settle 完成: mode=%s, duration=%v, quote=%v", pricing["mode"], pricing["duration_seconds"], pricing["quote_points"])
	t.Log("\n===== 视频 per_second 端到端链路验证通过 =====")
}
