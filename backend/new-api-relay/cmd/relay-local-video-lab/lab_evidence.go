//go:build relay_local_video_lab

package main

import (
	"context"
	"database/sql"
	"encoding/hex"
	"errors"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/localvideoconfig"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

// Native quota is the legacy wallet's raw accounting unit, not CNY or provider
// cost. These state-scoped readings are private operations evidence only.
type labNativeBillingEvidence struct {
	UserQuota           int   `json:"user_quota"`
	UserUsedQuota       int   `json:"user_used_quota"`
	UserRequestCount    int   `json:"user_request_count"`
	TokenRemainQuota    int   `json:"token_remain_quota"`
	TokenUsedQuota      int   `json:"token_used_quota"`
	TokenUnlimitedQuota bool  `json:"token_unlimited_quota"`
	NativeConsumeLogs   int64 `json:"native_consume_logs"`
	NativeRefundLogs    int64 `json:"native_refund_logs"`
}

func readLabNativeBillingEvidence(ctx context.Context, state *labState, config localvideoconfig.Config) (labNativeBillingEvidence, error) {
	var evidence labNativeBillingEvidence
	invalid := errors.New("local native billing evidence binding is unavailable")
	if state == nil || state.Manifest.StateID != config.StateID || config.Environment != "development" ||
		(config.Mode != "mock" && config.Mode != "live") || model.DB == nil || model.LOG_DB != model.DB {
		return evidence, invalid
	}
	digest := strings.TrimPrefix(config.StateID, config.Mode+"-")
	decoded, err := hex.DecodeString(digest)
	if err != nil || len(decoded) != 32 || hex.EncodeToString(decoded) != digest || config.Principal.UserName != "local-video-"+digest[:16] {
		return evidence, invalid
	}
	key := strings.TrimPrefix(config.Principal.UpstreamToken, "sk-")
	decoded, err = hex.DecodeString(key)
	if err != nil || len(decoded) != 24 || hex.EncodeToString(decoded) != key || config.Principal.UpstreamToken != "sk-"+key {
		return evidence, invalid
	}
	// The isolated initializer binds LOG_DB to this exact DB. Reading through one
	// transaction avoids cross-snapshot balances and never invokes cache, wallet,
	// quota mutation or log helpers. Secret columns and log bodies are not read.
	err = model.DB.WithContext(ctx).Transaction(func(tx *gorm.DB) error {
		var user model.User
		if err := tx.Select("id", "quota", "used_quota", "request_count").
			Where("username = ? AND role = ? AND status = ?", config.Principal.UserName, common.RoleCommonUser, common.UserStatusEnabled).
			First(&user).Error; err != nil {
			return invalid
		}
		var token model.Token
		if err := tx.Select("remain_quota", "used_quota", "unlimited_quota").
			Where("key = ? AND user_id = ? AND name = ? AND status = ?", key, user.Id, "local-video-platform-external", common.TokenStatusEnabled).
			First(&token).Error; err != nil {
			return invalid
		}
		evidence.UserQuota, evidence.UserUsedQuota, evidence.UserRequestCount = user.Quota, user.UsedQuota, user.RequestCount
		evidence.TokenRemainQuota, evidence.TokenUsedQuota, evidence.TokenUnlimitedQuota = token.RemainQuota, token.UsedQuota, token.UnlimitedQuota
		// Refund logs need not carry a token ID. The exact state-bound native user
		// is therefore the scope; other users' charges cannot affect this reading.
		if err := tx.Model(&model.Log{}).Where("user_id = ? AND type = ?", user.Id, model.LogTypeConsume).Count(&evidence.NativeConsumeLogs).Error; err != nil {
			return invalid
		}
		if err := tx.Model(&model.Log{}).Where("user_id = ? AND type = ?", user.Id, model.LogTypeRefund).Count(&evidence.NativeRefundLogs).Error; err != nil {
			return invalid
		}
		return nil
	}, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return labNativeBillingEvidence{}, invalid
	}
	return evidence, nil
}
