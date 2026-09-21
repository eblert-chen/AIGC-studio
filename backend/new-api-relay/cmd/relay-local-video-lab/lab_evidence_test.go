//go:build relay_local_video_lab

package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestLabEvidenceIsPrivateStateBoundAndReadOnly(t *testing.T) {
	config := labTestConfig(t, "mock")
	directory := labTestDatabase(t, config)
	state := &labState{Directory: directory, Manifest: labStateManifest{StateID: config.StateID}}
	options := labOptions{Mode: "mock", PublicBaseURL: "http://127.0.0.1:3000", StateDirectory: directory}
	var user model.User
	require.NoError(t, model.DB.Where("username = ?", config.Principal.UserName).First(&user).Error)
	var token model.Token
	require.NoError(t, model.DB.Where("user_id = ?", user.Id).First(&token).Error)
	other := model.User{Username: "unrelated-user", Password: "not-a-login", AffCode: "unrelated-user", Quota: 9000, UsedQuota: 8000, RequestCount: 7000}
	require.NoError(t, model.DB.Create(&other).Error)
	for _, logType := range []int{model.LogTypeConsume, model.LogTypeRefund} {
		// A misleading token ID is insufficient: the native user ID must match.
		require.NoError(t, model.DB.Create(&model.Log{UserId: other.Id, TokenId: token.Id, Type: logType, Content: "unrelated-private-content"}).Error)
	}
	initial, err := readLabNativeBillingEvidence(context.Background(), state, config)
	require.NoError(t, err)
	assert.Equal(t, labNativeBillingEvidence{TokenUnlimitedQuota: true}, initial)

	// Positive counter values ensure the endpoint reports actual persisted state,
	// not a hard-coded 'zero billing' assertion. Provider cost events are separate.
	require.NoError(t, model.DB.Model(&model.User{}).Where("id = ?", user.Id).Updates(map[string]any{"quota": 123, "used_quota": 4, "request_count": 5}).Error)
	require.NoError(t, model.DB.Model(&model.Token{}).Where("id = ?", token.Id).Updates(map[string]any{"remain_quota": 67, "used_quota": 8}).Error)
	require.NoError(t, model.DB.Create(&model.Log{UserId: user.Id, TokenId: token.Id, Type: model.LogTypeConsume, Content: "private-native-consume-body"}).Error)
	require.NoError(t, model.DB.Create(&model.Log{UserId: user.Id, Type: model.LogTypeRefund, Content: "private-native-refund-body"}).Error)
	require.NoError(t, model.DB.Create(&model.Log{UserId: user.Id, Type: model.LogTypeManage, Content: "not-a-wallet-entry"}).Error)
	expected := labNativeBillingEvidence{UserQuota: 123, UserUsedQuota: 4, UserRequestCount: 5, TokenRemainQuota: 67, TokenUsedQuota: 8, TokenUnlimitedQuota: true, NativeConsumeLogs: 1, NativeRefundLogs: 1}
	var beforeChanges int64
	require.NoError(t, model.DB.Raw("SELECT total_changes()").Scan(&beforeChanges).Error)
	engine, err := buildLabRouter(options, state, config, buildLabSummary(config, options), nil, nil)
	require.NoError(t, err)
	for _, authorized := range []bool{false, true, true} {
		request := httptest.NewRequest(http.MethodGet, "/lab/evidence", nil)
		if authorized {
			request.Header.Set("X-Relay-Operations-Token", config.Principal.OperationsToken)
		}
		response := httptest.NewRecorder()
		engine.ServeHTTP(response, request)
		assert.Equal(t, "no-store", response.Header().Get("Cache-Control"))
		if !authorized {
			assert.Equal(t, http.StatusUnauthorized, response.Code)
			assert.NotContains(t, response.Body.String(), "native_billing")
			continue
		}
		require.Equal(t, http.StatusOK, response.Code, response.Body.String())
		var body struct {
			NativeBilling labNativeBillingEvidence `json:"native_billing"`
		}
		require.NoError(t, common.Unmarshal(response.Body.Bytes(), &body))
		assert.Equal(t, expected, body.NativeBilling)
		for _, private := range []string{config.Principal.UserName, config.Principal.UpstreamToken, token.Key, "private-native-consume-body", "private-native-refund-body", "unrelated-private-content", "user_id", "token_id"} {
			assert.NotContains(t, response.Body.String(), private)
		}
	}
	var afterChanges int64
	require.NoError(t, model.DB.Raw("SELECT total_changes()").Scan(&afterChanges).Error)
	assert.Equal(t, beforeChanges, afterChanges, "evidence reads cannot mutate any SQLite row")
	public := httptest.NewRecorder()
	engine.ServeHTTP(public, httptest.NewRequest(http.MethodGet, "/lab/summary", nil))
	require.Equal(t, http.StatusOK, public.Code)
	assert.NotContains(t, public.Body.String(), "native_billing")
	assert.NotContains(t, public.Body.String(), "user_quota")
}

func TestLabNativeBillingEvidenceRejectsMissingOrCrossStateBindings(t *testing.T) {
	config := labTestConfig(t, "mock")
	directory := labTestDatabase(t, config)
	state := &labState{Directory: directory, Manifest: labStateManifest{StateID: config.StateID}}
	for _, mutation := range []string{"state", "username", "token", "environment", "mode"} {
		t.Run(mutation, func(t *testing.T) {
			changed := config
			switch mutation {
			case "state":
				changed.StateID = "mock-" + strings.Repeat("a", 64)
			case "username":
				changed.Principal.UserName = "unrelated-user"
			case "token":
				changed.Principal.UpstreamToken = "sk-" + strings.Repeat("0", 48)
			case "environment":
				changed.Environment = "production"
			case "mode":
				changed.Mode = "other"
			}
			evidence, err := readLabNativeBillingEvidence(context.Background(), state, changed)
			require.Error(t, err)
			assert.Equal(t, labNativeBillingEvidence{}, evidence)
			assert.NotContains(t, err.Error(), changed.Principal.UpstreamToken)
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := readLabNativeBillingEvidence(ctx, state, config)
	require.Error(t, err)
	// A token that exists but belongs to another native user cannot establish
	// this state's identity, even if its key remains unchanged.
	require.NoError(t, model.DB.Model(&model.Token{}).Where("key = ?", strings.TrimPrefix(config.Principal.UpstreamToken, "sk-")).Update("user_id", 999999).Error)
	_, err = readLabNativeBillingEvidence(context.Background(), state, config)
	require.Error(t, err)
	engine, err := buildLabRouter(labOptions{Mode: "mock"}, state, config, buildLabSummary(config, labOptions{Mode: "mock"}), nil, nil)
	require.NoError(t, err)
	request := httptest.NewRequest(http.MethodGet, "/lab/evidence", nil)
	request.Header.Set("X-Relay-Operations-Token", config.Principal.OperationsToken)
	response := httptest.NewRecorder()
	engine.ServeHTTP(response, request)
	assert.Equal(t, http.StatusServiceUnavailable, response.Code)
	assert.NotContains(t, response.Body.String(), config.Principal.UpstreamToken)
}
