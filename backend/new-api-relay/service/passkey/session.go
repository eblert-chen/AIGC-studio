package passkey

import (
	"encoding/json"
	"errors"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"

	webauthn "github.com/go-webauthn/webauthn/webauthn"
)

var errSessionNotFound = errors.New("Passkey 会话不存在或已过期")

const passkeyFlowTTL = 5 * time.Minute

type flowPayload struct {
	SessionData webauthn.SessionData `json:"session_data"`
	Scope       string               `json:"scope,omitempty"`
	Binding     json.RawMessage      `json:"binding,omitempty"`
}

func CreateSessionDataFlow(purpose string, userID int, sessionID, scope string, data *webauthn.SessionData) (string, int64, error) {
	return CreateSessionDataFlowWithBinding(purpose, userID, sessionID, scope, nil, data)
}

func CreateSessionDataFlowWithBinding(purpose string, userID int, sessionID, scope string, binding any, data *webauthn.SessionData) (string, int64, error) {
	if data == nil {
		return "", 0, errors.New("Passkey 会话数据不能为空")
	}
	var bindingJSON json.RawMessage
	if binding != nil {
		encoded, err := common.Marshal(binding)
		if err != nil {
			return "", 0, err
		}
		bindingJSON = encoded
	}
	payload, err := common.Marshal(flowPayload{SessionData: *data, Scope: scope, Binding: bindingJSON})
	if err != nil {
		return "", 0, err
	}
	expiresAt := time.Now().Add(passkeyFlowTTL)
	token, _, err := model.CreateAuthFlow(model.AuthFlowCreate{
		Purpose:   purpose,
		UserId:    userID,
		SessionId: sessionID,
		Payload:   string(payload),
		ExpiresAt: expiresAt,
	})
	if err != nil {
		return "", 0, err
	}
	return token, expiresAt.Unix(), nil
}

func PopSessionDataFlow(token, purpose string, userID int, sessionID string) (*webauthn.SessionData, string, error) {
	sessionData, scope, _, err := PopSessionDataFlowWithBinding(token, purpose, userID, sessionID)
	return sessionData, scope, err
}

func PopSessionDataFlowWithBinding(token, purpose string, userID int, sessionID string) (*webauthn.SessionData, string, json.RawMessage, error) {
	flow, err := model.ConsumeAuthFlow(token, model.AuthFlowMatch{
		Purpose:   purpose,
		UserId:    userID,
		SessionId: sessionID,
	})
	if err != nil {
		if errors.Is(err, model.ErrAuthFlowInvalid) || errors.Is(err, model.ErrAuthFlowExpired) || errors.Is(err, model.ErrAuthFlowConsumed) {
			return nil, "", nil, errSessionNotFound
		}
		return nil, "", nil, err
	}
	var payload flowPayload
	if err := common.UnmarshalJsonStr(flow.Payload, &payload); err != nil {
		return nil, "", nil, err
	}
	return &payload.SessionData, payload.Scope, append(json.RawMessage(nil), payload.Binding...), nil
}
