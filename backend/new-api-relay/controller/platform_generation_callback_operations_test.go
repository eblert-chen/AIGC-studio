package controller

import (
	"crypto/sha256"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestPlatformGenerationCallbackDeadLetterOperationsContract(t *testing.T) {
	originalDB := model.DB
	originalDatabaseType := common.MainDatabaseType()
	dsn := "file:callback-operations-" + uuid.NewString() + "?mode=memory&cache=shared&_pragma=busy_timeout(5000)"
	database, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		model.DB = originalDB
		common.SetMainDatabaseType(originalDatabaseType)
	})
	require.NoError(t, database.AutoMigrate(&model.PlatformGenerationCallbackDelivery{}))
	require.NoError(t, model.MigratePlatformGenerationCallbackOperationsStorage())

	tenantID := "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
	otherTenantID := "58775bb2-b6d2-4ad3-ab03-2f9d10854ba1"
	operationsToken := "tenant-scoped-callback-operations-token"
	otherOperationsToken := "other-tenant-callback-operations-token"
	digest := sha256.Sum256([]byte(operationsToken))
	otherDigest := sha256.Sum256([]byte(otherOperationsToken))
	t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"token_sha256":"%x"},{"tenant_id":%q,"token_sha256":"%x"}]`,
		tenantID,
		digest,
		otherTenantID,
		otherDigest,
	))
	// Operations config validation requires an independent approval key for
	// every operations tenant even though callback redrive itself does not use
	// the unknown-submission approval signature.
	t.Setenv("RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"key_id":"approval-v1","secret":"tenant-approval-secret-at-least-32-bytes"},{"tenant_id":%q,"key_id":"approval-v1","secret":"other-approval-secret-at-least-32-bytes"}]`,
		tenantID,
		otherTenantID,
	))

	deadLetter := callbackOperationsDeliveryFixture(tenantID, model.PlatformGenerationCallbackDeadLetter)
	pending := callbackOperationsDeliveryFixture(tenantID, model.PlatformGenerationCallbackPending)
	otherTenantDeadLetter := callbackOperationsDeliveryFixture(otherTenantID, model.PlatformGenerationCallbackDeadLetter)
	for _, delivery := range []*model.PlatformGenerationCallbackDelivery{deadLetter, pending, otherTenantDeadLetter} {
		require.NoError(t, database.Create(delivery).Error)
	}

	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(middleware.PlatformRelayRequestID())
	router.GET("/callbacks", ListPlatformGenerationCallbackDeliveries)
	router.GET("/callbacks/:event_id", GetPlatformGenerationCallbackDelivery)
	router.POST("/callbacks/:event_id/redrive", RedrivePlatformGenerationCallbackDelivery)
	router.GET("/callbacks/:event_id/redrive-result", GetPlatformGenerationCallbackRedriveResult)

	listRequest := httptest.NewRequest(http.MethodGet, "/callbacks?tenant_id="+tenantID, nil)
	listRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	listResponse := httptest.NewRecorder()
	router.ServeHTTP(listResponse, listRequest)
	require.Equal(t, http.StatusOK, listResponse.Code, listResponse.Body.String())
	var page dto.PlatformGenerationCallbackDeliveryPage
	require.NoError(t, common.Unmarshal(listResponse.Body.Bytes(), &page))
	require.Len(t, page.Data, 1)
	assert.EqualValues(t, 1, page.Total)
	assert.Equal(t, deadLetter.ID, page.Data[0].EventID)
	assert.Equal(t, model.PlatformGenerationCallbackDeadLetter, page.Data[0].State)
	assert.Equal(t, strings.Repeat("a", 64), page.Data[0].PayloadSHA256)
	assert.NotEmpty(t, page.Data[0].CallbackURLSHA256)
	assert.NotContains(t, listResponse.Body.String(), "callback-secret")
	assert.NotContains(t, listResponse.Body.String(), "callbacks.example.test")

	invalidStateRequest := httptest.NewRequest(http.MethodGet, "/callbacks?tenant_id="+tenantID+"&state=unknown", nil)
	invalidStateRequest.Header.Set("X-Relay-Operations-Token", operationsToken)
	invalidStateResponse := httptest.NewRecorder()
	router.ServeHTTP(invalidStateResponse, invalidStateRequest)
	assert.Equal(t, http.StatusUnprocessableEntity, invalidStateResponse.Code)

	crossTenant := httptest.NewRequest(http.MethodGet, "/callbacks/"+otherTenantDeadLetter.ID+"?tenant_id="+otherTenantID, nil)
	crossTenant.Header.Set("X-Relay-Operations-Token", operationsToken)
	crossTenantResponse := httptest.NewRecorder()
	router.ServeHTTP(crossTenantResponse, crossTenant)
	assert.Equal(t, http.StatusUnauthorized, crossTenantResponse.Code)

	authorizedOtherTenant := httptest.NewRequest(
		http.MethodGet,
		"/callbacks/"+deadLetter.ID+"?tenant_id="+otherTenantID,
		nil,
	)
	authorizedOtherTenant.Header.Set("X-Relay-Operations-Token", otherOperationsToken)
	authorizedOtherTenantResponse := httptest.NewRecorder()
	router.ServeHTTP(authorizedOtherTenantResponse, authorizedOtherTenant)
	assert.Equal(t, http.StatusNotFound, authorizedOtherTenantResponse.Code)
	assert.NotContains(t, authorizedOtherTenantResponse.Body.String(), deadLetter.ID)

	redriveBody := fmt.Sprintf(
		`{"operation_id":"callback-redrive-operation-0001","tenant_id":%q,"actor":"platform-owner-1","reason":"Destination incident is resolved"}`,
		tenantID,
	)
	redrive := httptest.NewRequest(http.MethodPost, "/callbacks/"+deadLetter.ID+"/redrive", strings.NewReader(redriveBody))
	redrive.Header.Set("X-Relay-Operations-Token", operationsToken)
	redrive.Header.Set("X-Request-ID", "callback-redrive-request-1")
	redriveResponse := httptest.NewRecorder()
	router.ServeHTTP(redriveResponse, redrive)
	require.Equal(t, http.StatusOK, redriveResponse.Code, redriveResponse.Body.String())
	assert.Equal(t, "false", redriveResponse.Header().Get("X-Idempotent-Replay"))
	redriveEventID := redriveResponse.Header().Get("X-Callback-Redrive-Event-ID")
	assert.NotEmpty(t, redriveEventID)
	var result dto.PlatformGenerationCallbackRedriveResult
	require.NoError(t, common.Unmarshal(redriveResponse.Body.Bytes(), &result))
	assert.Equal(t, deadLetter.ID, result.DeliveryEventID)
	assert.Equal(t, model.PlatformGenerationCallbackPending, result.CurrentState)
	assert.Equal(t, "callback-redrive-request-1", result.Evidence.RequestID)
	assert.Equal(t, "platform-owner-1", result.Evidence.Actor)
	assert.Equal(t, "Destination incident is resolved", result.Evidence.Reason)
	assert.Equal(t, model.PlatformGenerationCallbackDeadLetter, result.Evidence.PreviousState)
	assert.Equal(t, 8, result.Evidence.PreviousAttempts)
	assert.Equal(t, 503, result.Evidence.PreviousResponseStatus)
	assert.Equal(t, model.PlatformGenerationCallbackFailureEndpoint, result.Evidence.PreviousLastError)
	assert.Equal(t, strings.Repeat("a", 64), result.Evidence.PayloadSHA256)
	assert.Regexp(t, `^[0-9a-f]{64}$`, result.Evidence.ReceiptSHA256)

	var rearmed model.PlatformGenerationCallbackDelivery
	require.NoError(t, database.First(&rearmed, "id = ?", deadLetter.ID).Error)
	assert.Equal(t, model.PlatformGenerationCallbackPending, rearmed.State)
	assert.Zero(t, rearmed.Attempts)
	assert.Zero(t, rearmed.ResponseStatus)
	assert.Empty(t, rearmed.LastError)
	assert.Nil(t, rearmed.DeadLetteredAt)
	assert.Equal(t, deadLetter.ID, rearmed.ID)
	assert.Equal(t, deadLetter.PayloadJSON, rearmed.PayloadJSON)
	assert.Equal(t, deadLetter.PayloadSHA256, rearmed.PayloadSHA256)
	assert.Equal(t, deadLetter.CallbackURL, rearmed.CallbackURL)
	assert.Equal(t, deadLetter.RequestID, rearmed.RequestID)

	replay := httptest.NewRequest(http.MethodPost, "/callbacks/"+deadLetter.ID+"/redrive", strings.NewReader(redriveBody))
	replay.Header.Set("X-Relay-Operations-Token", operationsToken)
	replay.Header.Set("X-Request-ID", "callback-redrive-request-2")
	replayResponse := httptest.NewRecorder()
	router.ServeHTTP(replayResponse, replay)
	require.Equal(t, http.StatusOK, replayResponse.Code, replayResponse.Body.String())
	assert.Equal(t, "true", replayResponse.Header().Get("X-Idempotent-Replay"))
	assert.Equal(t, redriveEventID, replayResponse.Header().Get("X-Callback-Redrive-Event-ID"))

	conflictBody := strings.Replace(redriveBody, "Destination incident is resolved", "Different operator evidence", 1)
	conflict := httptest.NewRequest(http.MethodPost, "/callbacks/"+deadLetter.ID+"/redrive", strings.NewReader(conflictBody))
	conflict.Header.Set("X-Relay-Operations-Token", operationsToken)
	conflict.Header.Set("X-Request-ID", "callback-redrive-request-3")
	conflictResponse := httptest.NewRecorder()
	router.ServeHTTP(conflictResponse, conflict)
	assert.Equal(t, http.StatusConflict, conflictResponse.Code)

	pendingBody := strings.Replace(redriveBody, "callback-redrive-operation-0001", "callback-redrive-operation-0002", 1)
	pendingRedrive := httptest.NewRequest(http.MethodPost, "/callbacks/"+pending.ID+"/redrive", strings.NewReader(pendingBody))
	pendingRedrive.Header.Set("X-Relay-Operations-Token", operationsToken)
	pendingRedrive.Header.Set("X-Request-ID", "callback-redrive-request-4")
	pendingResponse := httptest.NewRecorder()
	router.ServeHTTP(pendingResponse, pendingRedrive)
	assert.Equal(t, http.StatusConflict, pendingResponse.Code)

	lookup := httptest.NewRequest(
		http.MethodGet,
		"/callbacks/"+deadLetter.ID+"/redrive-result?tenant_id="+tenantID+"&operation_id=callback-redrive-operation-0001",
		nil,
	)
	lookup.Header.Set("X-Relay-Operations-Token", operationsToken)
	lookupResponse := httptest.NewRecorder()
	router.ServeHTTP(lookupResponse, lookup)
	require.Equal(t, http.StatusOK, lookupResponse.Code, lookupResponse.Body.String())
	var lookupResult dto.PlatformGenerationCallbackRedriveResult
	require.NoError(t, common.Unmarshal(lookupResponse.Body.Bytes(), &lookupResult))
	assert.Equal(t, redriveEventID, lookupResult.Evidence.EventID)
	assert.Equal(t, "callback-redrive-request-1", lookupResult.Evidence.RequestID)
	assert.Equal(t, model.PlatformGenerationCallbackPending, lookupResult.CurrentState)

	detail := httptest.NewRequest(http.MethodGet, "/callbacks/"+deadLetter.ID+"?tenant_id="+tenantID, nil)
	detail.Header.Set("X-Relay-Operations-Token", operationsToken)
	detailResponse := httptest.NewRecorder()
	router.ServeHTTP(detailResponse, detail)
	require.Equal(t, http.StatusOK, detailResponse.Code, detailResponse.Body.String())
	var detailItem dto.PlatformGenerationCallbackDeliveryItem
	require.NoError(t, common.Unmarshal(detailResponse.Body.Bytes(), &detailItem))
	require.Len(t, detailItem.Redrives, 1)
	assert.Equal(t, redriveEventID, detailItem.Redrives[0].EventID)

	var eventCount int64
	require.NoError(t, database.Model(&model.PlatformGenerationCallbackRedriveEvent{}).Count(&eventCount).Error)
	assert.EqualValues(t, 1, eventCount)
	assert.Error(t, database.Exec(
		"UPDATE platform_generation_callback_redrive_events SET reason = ? WHERE id = ?",
		"tampered",
		redriveEventID,
	).Error)
	assert.Error(t, database.Exec(
		"DELETE FROM platform_generation_callback_redrive_events WHERE id = ?",
		redriveEventID,
	).Error)
}

func TestPlatformGenerationCallbackRedriveRequiresOriginalRequestIDAndClosedBody(t *testing.T) {
	tenantID := "51bdf7c4-93a6-4b7c-a4a1-03f616a10f30"
	operationsToken := "tenant-scoped-callback-operations-token"
	digest := sha256.Sum256([]byte(operationsToken))
	t.Setenv("RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"token_sha256":"%x"}]`,
		tenantID,
		digest,
	))
	t.Setenv("RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON", fmt.Sprintf(
		`[{"tenant_id":%q,"key_id":"approval-v1","secret":"tenant-approval-secret-at-least-32-bytes"}]`,
		tenantID,
	))

	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(middleware.PlatformRelayRequestID())
	router.POST("/callbacks/:event_id/redrive", RedrivePlatformGenerationCallbackDelivery)
	validBody := fmt.Sprintf(
		`{"operation_id":"callback-redrive-validation-0001","tenant_id":%q,"actor":"platform-owner-1","reason":"Destination incident is resolved"}`,
		tenantID,
	)

	missingRequestID := httptest.NewRequest(http.MethodPost, "/callbacks/"+uuid.NewString()+"/redrive", strings.NewReader(validBody))
	missingRequestID.Header.Set("X-Relay-Operations-Token", operationsToken)
	missingResponse := httptest.NewRecorder()
	router.ServeHTTP(missingResponse, missingRequestID)
	assert.Equal(t, http.StatusUnprocessableEntity, missingResponse.Code)

	extraFieldBody := strings.TrimSuffix(validBody, "}") + `,"force":true}`
	extraField := httptest.NewRequest(http.MethodPost, "/callbacks/"+uuid.NewString()+"/redrive", strings.NewReader(extraFieldBody))
	extraField.Header.Set("X-Relay-Operations-Token", operationsToken)
	extraField.Header.Set("X-Request-ID", "callback-redrive-validation-request")
	extraFieldResponse := httptest.NewRecorder()
	router.ServeHTTP(extraFieldResponse, extraField)
	assert.Equal(t, http.StatusUnprocessableEntity, extraFieldResponse.Code)
}

func callbackOperationsDeliveryFixture(tenantID string, state string) *model.PlatformGenerationCallbackDelivery {
	now := time.Now().UTC().Truncate(time.Second)
	delivery := &model.PlatformGenerationCallbackDelivery{
		ID:             uuid.NewString(),
		TenantID:       tenantID,
		SourceClientID: "customer-platform",
		JobID:          uuid.NewString(),
		CallbackURL:    "https://callbacks.example.test/internal/relay?callback-secret=hidden",
		RequestID:      "original-callback-request",
		PayloadJSON:    `{"api_version":"v1","immutable":true}`,
		PayloadSHA256:  strings.Repeat("a", 64),
		State:          state,
		Attempts:       8,
		MaxAttempts:    8,
		AvailableAt:    now,
		ResponseStatus: 503,
		LastError:      model.PlatformGenerationCallbackFailureEndpoint,
		CreatedAt:      now.Add(-time.Hour),
		UpdatedAt:      now,
	}
	if state == model.PlatformGenerationCallbackDeadLetter {
		delivery.DeadLetteredAt = &now
	}
	return delivery
}
