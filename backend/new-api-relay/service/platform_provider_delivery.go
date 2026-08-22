package service

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const (
	platformRelayExternalDeliveryTimeout   = 10 * time.Second
	platformRelayExternalDeliveryBaseDelay = 5 * time.Second
	platformRelayExternalDeliveryMaxDelay  = time.Hour
	platformProviderAlertUserAgent         = "ai-video-relay-provider-monitor/1.0"
	platformChannelCostUserAgent           = "ai-video-relay-channel-cost/1.0"
)

type PlatformProviderAlertSinkConfig struct {
	URL           string
	SigningSecret string
	Production    bool
}

func (config PlatformProviderAlertSinkConfig) Validate() error {
	if err := validatePlatformProviderAlertTarget(config.URL, config.Production); err != nil {
		return err
	}
	if config.SigningSecret == "" || (config.Production && len([]byte(config.SigningSecret)) < 32) {
		return fmt.Errorf("provider alert signing secret is invalid")
	}
	return nil
}

type PlatformChannelCostSinkConfig struct {
	URL                  string
	InternalServiceToken string
	SigningSecret        string
	Production           bool
}

func (config PlatformChannelCostSinkConfig) Validate() error {
	if err := validatePlatformChannelCostTarget(config.URL, config.Production); err != nil {
		return err
	}
	if config.InternalServiceToken == "" || config.SigningSecret == "" {
		return fmt.Errorf("channel cost delivery credentials are required")
	}
	if config.Production && (len([]byte(config.InternalServiceToken)) < 32 || len([]byte(config.SigningSecret)) < 32) {
		return fmt.Errorf("channel cost delivery credentials are too short")
	}
	if config.Production && (platformRelaySecretIsPlaceholder(config.InternalServiceToken) || platformRelaySecretIsPlaceholder(config.SigningSecret)) {
		return fmt.Errorf("channel cost delivery credentials contain a placeholder")
	}
	if config.Production && config.InternalServiceToken == config.SigningSecret {
		return fmt.Errorf("channel cost authentication and signing secrets must be independent")
	}
	return nil
}

type PlatformRelayExternalDispatchResult struct {
	Delivered      bool
	Retryable      bool
	ResponseStatus int
	Failure        string
}

func EnqueuePlatformChannelCost(input dto.PlatformChannelCostInput) (bool, error) {
	if err := input.Validate(); err != nil {
		return false, err
	}
	payload, err := common.Marshal(input.Payload())
	if err != nil {
		return false, err
	}
	digest := sha256.Sum256(payload)
	event := model.PlatformChannelCostEvent{
		ID:                   input.EventID,
		AmountCents:          input.AmountCents,
		IdempotencyKey:       input.IdempotencyKey,
		ChannelKey:           input.ChannelKey,
		ChannelType:          input.ChannelType,
		OccurredAt:           input.OccurredAt.UTC(),
		ExternalReference:    input.ExternalReference,
		CompanyID:            input.CompanyID,
		TaskID:               input.TaskID,
		RelayJobID:           input.RelayJobID,
		Note:                 input.Note,
		EvidenceSource:       input.EvidenceSource,
		EvidenceReference:    input.EvidenceReference,
		SourceDocumentSHA256: input.SourceDocumentSHA256,
		PayloadJSON:          string(payload),
		PayloadSHA256:        fmt.Sprintf("%x", digest),
	}
	return model.CreatePlatformChannelCostEvent(&event)
}

// SignPlatformRelayExternalEvent signs timestamp + "." + event_id + "." +
// raw_body. The exact persisted bytes are reused for every delivery attempt.
func SignPlatformRelayExternalEvent(secret string, timestamp int64, eventID string, body []byte) (string, error) {
	if secret == "" || timestamp <= 0 || len(body) == 0 {
		return "", fmt.Errorf("Relay external event signing input is incomplete")
	}
	if parsed, err := uuid.Parse(eventID); err != nil || parsed.String() != eventID {
		return "", fmt.Errorf("Relay external event id is invalid")
	}
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(strconv.FormatInt(timestamp, 10)))
	_, _ = mac.Write([]byte("."))
	_, _ = mac.Write([]byte(eventID))
	_, _ = mac.Write([]byte("."))
	_, _ = mac.Write(body)
	return fmt.Sprintf("v1=%x", mac.Sum(nil)), nil
}

func DeliverPlatformProviderAlertClaim(
	ctx context.Context,
	claim model.PlatformRelayDeliveryClaim,
	config PlatformProviderAlertSinkConfig,
) (string, bool, error) {
	result := PlatformRelayExternalDispatchResult{
		Retryable: true,
		Failure:   model.PlatformRelayDeliveryFailureConfiguration,
	}
	if claim.Delivery.EventKind != model.PlatformRelayDeliveryKindProviderAlert {
		result.Retryable = false
	} else if config.Validate() == nil {
		event, err := model.GetPlatformProviderAlertEvent(claim.Delivery.EventID)
		if err == nil {
			client := newPlatformRelayExternalHTTPClient(config.Production, true)
			result = dispatchPlatformProviderAlert(ctx, client, time.Now().UTC(), claim.Delivery, *event, config)
		} else if err != nil && errors.Is(err, gorm.ErrRecordNotFound) {
			result.Retryable = false
			result.Failure = model.PlatformRelayDeliveryFailurePayload
		}
	}
	return applyPlatformRelayExternalDispatchResult(claim, result)
}

func DeliverPlatformChannelCostClaim(
	ctx context.Context,
	claim model.PlatformRelayDeliveryClaim,
	config PlatformChannelCostSinkConfig,
) (string, bool, error) {
	result := PlatformRelayExternalDispatchResult{
		Retryable: true,
		Failure:   model.PlatformRelayDeliveryFailureConfiguration,
	}
	if claim.Delivery.EventKind != model.PlatformRelayDeliveryKindChannelCost {
		result.Retryable = false
	} else if config.Validate() == nil {
		event, err := model.GetPlatformChannelCostEvent(claim.Delivery.EventID)
		if err == nil {
			client := newPlatformRelayExternalHTTPClient(config.Production, false)
			result = dispatchPlatformChannelCost(ctx, client, time.Now().UTC(), claim.Delivery, *event, config)
		} else if err != nil && errors.Is(err, gorm.ErrRecordNotFound) {
			result.Retryable = false
			result.Failure = model.PlatformRelayDeliveryFailurePayload
		}
	}
	return applyPlatformRelayExternalDispatchResult(claim, result)
}

func PlatformRelayExternalDeliveryRetryDelay(attempt int) time.Duration {
	if attempt < 1 {
		attempt = 1
	}
	delay := platformRelayExternalDeliveryBaseDelay
	for current := 1; current < attempt && delay < platformRelayExternalDeliveryMaxDelay; current++ {
		if delay > platformRelayExternalDeliveryMaxDelay/2 {
			return platformRelayExternalDeliveryMaxDelay
		}
		delay *= 2
	}
	if delay > platformRelayExternalDeliveryMaxDelay {
		return platformRelayExternalDeliveryMaxDelay
	}
	return delay
}

func dispatchPlatformProviderAlert(
	ctx context.Context,
	client *http.Client,
	now time.Time,
	delivery model.PlatformRelayExternalDelivery,
	event model.PlatformProviderAlertEvent,
	config PlatformProviderAlertSinkConfig,
) PlatformRelayExternalDispatchResult {
	if client == nil || config.Validate() != nil || delivery.EventID != event.ID {
		return PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailureConfiguration, Retryable: true}
	}
	payload := []byte(event.PayloadJSON)
	if !platformRelayExternalPayloadDigestMatches(payload, event.PayloadSHA256) {
		return PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailurePayload}
	}
	var envelope dto.PlatformProviderAlertEvent
	if err := common.Unmarshal(payload, &envelope); err != nil ||
		envelope.SchemaVersion != 1 || envelope.EventID != event.ID || envelope.OccurredAt.IsZero() ||
		envelope.Type != "provider_monitor."+event.IncidentKind+"."+event.IncidentState ||
		envelope.Incident.ProviderName != event.ProviderName || envelope.Incident.Kind != event.IncidentKind ||
		envelope.Incident.State != event.IncidentState || envelope.Incident.Generation != event.Generation {
		return PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailurePayload}
	}
	return sendPlatformRelayExternalEvent(
		ctx,
		client,
		now,
		config.URL,
		config.SigningSecret,
		"",
		platformProviderAlertUserAgent,
		delivery,
		payload,
	)
}

func dispatchPlatformChannelCost(
	ctx context.Context,
	client *http.Client,
	now time.Time,
	delivery model.PlatformRelayExternalDelivery,
	event model.PlatformChannelCostEvent,
	config PlatformChannelCostSinkConfig,
) PlatformRelayExternalDispatchResult {
	if client == nil || config.Validate() != nil || delivery.EventID != event.ID {
		return PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailureConfiguration, Retryable: true}
	}
	payload := []byte(event.PayloadJSON)
	if !platformRelayExternalPayloadDigestMatches(payload, event.PayloadSHA256) {
		return PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailurePayload}
	}
	expectedPayload := dto.PlatformChannelCostPayload{
		AmountCents:          event.AmountCents,
		IdempotencyKey:       event.IdempotencyKey,
		ChannelKey:           event.ChannelKey,
		ChannelType:          event.ChannelType,
		OccurredAt:           event.OccurredAt.UTC(),
		ExternalReference:    event.ExternalReference,
		CompanyID:            event.CompanyID,
		TaskID:               event.TaskID,
		RelayJobID:           event.RelayJobID,
		Note:                 event.Note,
		EvidenceSource:       event.EvidenceSource,
		EvidenceReference:    event.EvidenceReference,
		SourceDocumentSHA256: event.SourceDocumentSHA256,
	}
	expectedJSON, err := common.Marshal(expectedPayload)
	if err != nil || !bytes.Equal(expectedJSON, payload) {
		return PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailurePayload}
	}
	return sendPlatformRelayExternalEvent(
		ctx,
		client,
		now,
		config.URL,
		config.SigningSecret,
		config.InternalServiceToken,
		platformChannelCostUserAgent,
		delivery,
		payload,
	)
}

func sendPlatformRelayExternalEvent(
	ctx context.Context,
	client *http.Client,
	now time.Time,
	targetURL string,
	signingSecret string,
	internalServiceToken string,
	userAgent string,
	delivery model.PlatformRelayExternalDelivery,
	payload []byte,
) PlatformRelayExternalDispatchResult {
	timestamp := now.Unix()
	signature, err := SignPlatformRelayExternalEvent(signingSecret, timestamp, delivery.EventID, payload)
	if err != nil {
		return PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailurePayload}
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, targetURL, bytes.NewReader(payload))
	if err != nil {
		return PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailureTarget}
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("User-Agent", userAgent)
	request.Header.Set("X-Relay-Event-ID", delivery.EventID)
	request.Header.Set("X-Relay-Timestamp", strconv.FormatInt(timestamp, 10))
	request.Header.Set("X-Relay-Signature", signature)
	request.Header.Set("X-Request-ID", delivery.RequestID)
	if internalServiceToken != "" {
		request.Header.Set("X-Internal-Service-Token", internalServiceToken)
	}

	response, err := client.Do(request)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		return PlatformRelayExternalDispatchResult{Failure: model.PlatformRelayDeliveryFailureTransport, Retryable: true}
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
	if response.StatusCode >= 200 && response.StatusCode < 300 {
		return PlatformRelayExternalDispatchResult{Delivered: true, ResponseStatus: response.StatusCode}
	}
	result := PlatformRelayExternalDispatchResult{
		ResponseStatus: response.StatusCode,
		Failure:        model.PlatformRelayDeliveryFailureEndpoint,
	}
	if response.StatusCode == http.StatusConflict {
		result.Failure = model.PlatformRelayDeliveryFailureConflict
		result.Retryable = true
		return result
	}
	result.Retryable = response.StatusCode == http.StatusRequestTimeout || response.StatusCode == http.StatusTooEarly ||
		response.StatusCode == http.StatusTooManyRequests || response.StatusCode >= 500
	return result
}

func applyPlatformRelayExternalDispatchResult(
	claim model.PlatformRelayDeliveryClaim,
	result PlatformRelayExternalDispatchResult,
) (string, bool, error) {
	if result.Delivered {
		won, err := model.CompletePlatformRelayExternalDelivery(
			claim.Delivery.EventKind,
			claim.Delivery.EventID,
			claim.Token,
			result.ResponseStatus,
		)
		return model.PlatformRelayDeliveryDelivered, won, err
	}
	if !result.Retryable {
		won, err := model.DeadLetterPlatformRelayExternalDelivery(
			claim.Delivery.EventKind,
			claim.Delivery.EventID,
			claim.Token,
			result.Failure,
			result.ResponseStatus,
		)
		return model.PlatformRelayDeliveryDeadLetter, won, err
	}
	return model.ReleasePlatformRelayExternalDelivery(
		claim.Delivery.EventKind,
		claim.Delivery.EventID,
		claim.Token,
		PlatformRelayExternalDeliveryRetryDelay(claim.Delivery.Attempts),
		result.Failure,
		result.ResponseStatus,
	)
}

func newPlatformRelayExternalHTTPClient(production bool, publicOnly bool) *http.Client {
	if publicOnly {
		return newPlatformGenerationCallbackHTTPClient(production)
	}
	var transport *http.Transport
	if defaultTransport, ok := http.DefaultTransport.(*http.Transport); ok && defaultTransport != nil {
		transport = defaultTransport.Clone()
	} else {
		transport = &http.Transport{}
	}
	transport.Proxy = nil
	return &http.Client{
		Transport: transport,
		Timeout:   platformRelayExternalDeliveryTimeout,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func validatePlatformProviderAlertTarget(rawURL string, production bool) error {
	parsed, err := url.ParseRequestURI(rawURL)
	if err != nil || parsed.RawQuery != "" {
		return fmt.Errorf("provider alert target rejected")
	}
	if err := validatePlatformGenerationCallbackTarget(rawURL, production); err != nil {
		return fmt.Errorf("provider alert target rejected")
	}
	return nil
}

func validatePlatformChannelCostTarget(rawURL string, production bool) error {
	parsed, err := url.ParseRequestURI(rawURL)
	if err != nil || parsed == nil || !parsed.IsAbs() || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return fmt.Errorf("channel cost target rejected")
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return fmt.Errorf("channel cost target rejected")
	}
	if production && parsed.Scheme != "https" {
		return fmt.Errorf("channel cost target rejected")
	}
	if parsed.EscapedPath() != "/internal/channel-costs" {
		return fmt.Errorf("channel cost target path is invalid")
	}
	return nil
}

func platformRelayExternalPayloadDigestMatches(payload []byte, expected string) bool {
	if len(payload) == 0 || len(expected) != 64 {
		return false
	}
	digest := sha256.Sum256(payload)
	return hmac.Equal([]byte(fmt.Sprintf("%x", digest)), []byte(expected))
}
