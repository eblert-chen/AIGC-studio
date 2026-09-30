package toc

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/bytedance/gopkg/util/gopool"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestTOCArtworkHTTPPreservesSettledCostAndModelAfterRepricing(t *testing.T) {
	s, executor := fixture(t)
	catalog, request := configureOffer(t, s, executor)
	settle := func(user int, input CreateTaskRequest) Task {
		t.Helper()
		task, err := s.CreateTask(context.Background(), user, input)
		require.NoError(t, err)
		var job model.PlatformGenerationJob
		require.NoError(t, s.DB.First(&job, "id = ?", task.ID).Error)
		// This test owns the HTTP projection and real TOC settlement boundary;
		// fixture executor output metadata is not supplier acceptance evidence.
		job.Status = model.PlatformGenerationStatusSucceeded
		job.OutputsJSON = fixtureJSON([]dto.PlatformGenerationArtifact{{AssetID: uuid.NewString(), MediaType: "image", ContentType: "image/png", SizeBytes: 4, SHA256: strings.Repeat("a", 64)}})
		require.NoError(t, s.DB.Save(&job).Error)
		require.NoError(t, s.DB.Transaction(func(tx *gorm.DB) error { return ApplyTerminal(tx, &job) }))
		return task
	}
	settled := settle(2, request)
	request.IdempotencyKey = uuid.NewString()
	unknown, err := s.CreateTask(context.Background(), 2, request)
	require.NoError(t, err)
	require.NoError(t, s.DB.Model(&model.PlatformGenerationJob{}).Where("id = ?", unknown.ID).Update("status", model.PlatformGenerationStatusReconciliationRequired).Error)
	_, err = s.SaveGrant(1, 3, GrantRequest{ModelID: catalog.ID, Enabled: true, IdempotencyKey: uuid.NewString(), Reason: "isolated second artwork owner"})
	require.NoError(t, err)
	_, err = s.Credit(1, 3, CreditRequest{AmountPoints: 20, IdempotencyKey: uuid.NewString(), Note: "isolated artwork owner"})
	require.NoError(t, err)
	models, err := s.Models(3, false)
	require.NoError(t, err)
	request.IdempotencyKey = uuid.NewString()
	request.ExpectedQuoteRevision = models[0]["quote_revision"].(string)
	other := settle(3, request)
	evidence := executor.evidence
	updated, err := s.SaveCatalog(context.Background(), 1, catalog.PublicModelID, CatalogRequest{
		Enabled: true, UnitPricePoints: 9, ExpectedVersion: catalog.Version,
		ExpectedCapabilityRevision: evidence.Resource.CapabilityRevision, ExpectedCatalogRevision: evidence.CatalogRevision,
		ExpectedRoutingSHA256: evidence.RoutingReleaseSHA256, ExpectedCostSHA256: evidence.CostReadinessSHA256,
		IdempotencyKey: uuid.NewString(), Reason: "new price cannot rewrite historical settlement",
	})
	require.NoError(t, err)
	require.EqualValues(t, 9, updated.UnitPricePoints)

	previousDB, previousLog, previousSecret := model.DB, model.LOG_DB, common.SessionSecret
	previousRate, previousRedis := common.GlobalApiRateLimitEnable, common.RedisEnabled
	previousMain, previousLogType := common.MainDatabaseType(), common.LogDatabaseType()
	model.DB, model.LOG_DB, common.SessionSecret = s.DB, s.DB, strings.Repeat("artwork-session-secret-", 2)
	common.GlobalApiRateLimitEnable, common.RedisEnabled = false, false
	common.SetDatabaseTypes(common.DatabaseTypeSQLite, common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		require.Eventually(t, func() bool { return gopool.WorkerCount() == 0 }, 3*time.Second, time.Millisecond)
		model.DB, model.LOG_DB, common.SessionSecret = previousDB, previousLog, previousSecret
		common.GlobalApiRateLimitEnable, common.RedisEnabled = previousRate, previousRedis
		common.SetDatabaseTypes(previousMain, previousLogType)
	})
	t.Setenv("RELAY_RUNTIME_PROFILE", "toc")
	tokens := map[int]string{}
	for _, userID := range []int{2, 3} {
		session := model.UserSession{SID: uuid.NewString(), UserID: userID, Version: 1, UserAuthVersion: 1, Status: model.UserSessionStatusActive, RefreshHash: uuid.NewString(), LoginMethod: "password", LastActiveAt: time.Now().Unix(), ExpiresAt: time.Now().Add(time.Hour).Unix()}
		require.NoError(t, model.CreateUserSession(&session))
		token, _, err := service.IssueAccessToken(service.AuthIdentity{UserID: userID, SessionID: session.SID, UserAuthVersion: 1, SessionVersion: 1})
		require.NoError(t, err)
		tokens[userID] = token
	}
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	RegisterRoutes(engine, s)
	server := httptest.NewServer(engine)
	t.Cleanup(server.Close)
	read := func(user int) (int, []map[string]any) {
		t.Helper()
		request, err := http.NewRequest(http.MethodGet, server.URL+"/api/v1/personal/artworks", nil)
		require.NoError(t, err)
		if user > 0 {
			request.Header.Set("Authorization", "Bearer "+tokens[user])
		}
		response, err := server.Client().Do(request)
		require.NoError(t, err)
		raw, err := io.ReadAll(response.Body)
		require.NoError(t, response.Body.Close())
		require.NoError(t, err)
		var result struct {
			Items []map[string]any `json:"items"`
		}
		require.NoError(t, common.Unmarshal(raw, &result))
		return response.StatusCode, result.Items
	}
	status, items := read(2)
	require.Equal(t, http.StatusOK, status)
	require.Len(t, items, 1, "the unresolved held task and the other user's work must both be excluded")
	item := items[0]
	require.Equal(t, settled.ID, item["task_id"])
	require.Equal(t, catalog.ID, item["model_id"])
	require.Equal(t, catalog.DisplayName, item["model_display_name"])
	require.EqualValues(t, 4, item["actual_cost_points"], "the current nine POINT price must never replace the settled four POINT charge")
	require.EqualValues(t, 4, item["quote_points"])
	require.EqualValues(t, 0, item["reserved_points"])
	require.Equal(t, "relay_toc", item["billing_authority"])
	require.Equal(t, "POINT", item["billing_unit"])
	status, items = read(3)
	require.Equal(t, http.StatusOK, status)
	require.Len(t, items, 1)
	require.Equal(t, other.ID, items[0]["task_id"])
	status, _ = read(0)
	require.Equal(t, http.StatusUnauthorized, status)
}
