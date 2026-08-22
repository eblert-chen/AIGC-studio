package service

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const (
	platformGenerationCallbackTimeout   = 10 * time.Second
	platformGenerationCallbackBaseDelay = 5 * time.Second
	platformGenerationCallbackMaxDelay  = time.Hour
	platformGenerationCallbackUserAgent = "ai-video-relay-callback/1.0"
)

type PlatformGenerationCallbackDispatchResult struct {
	Delivered      bool
	Retryable      bool
	ResponseStatus int
	Failure        string
}

// BuildPlatformGenerationCallbackDelivery creates one stable callback event
// for each meaningful generation state. Processing events additionally include
// progress in their UUIDv5 identity so repeated observations are idempotent.
func BuildPlatformGenerationCallbackDelivery(job model.PlatformGenerationJob) (*model.PlatformGenerationCallbackDelivery, bool, error) {
	if strings.TrimSpace(job.CallbackURL) == "" {
		return nil, false, nil
	}
	switch job.Status {
	case model.PlatformGenerationStatusReconciliationRequired,
		model.PlatformGenerationStatusProcessing,
		model.PlatformGenerationStatusSucceeded,
		model.PlatformGenerationStatusFailed,
		model.PlatformGenerationStatusCancelled:
	default:
		return nil, false, nil
	}

	snapshot, err := platformGenerationSnapshot(job)
	if err != nil {
		return nil, false, err
	}
	identity := fmt.Sprintf("relay-callback:%s:%s", job.ID, job.Status)
	if job.Status == model.PlatformGenerationStatusProcessing {
		identity = fmt.Sprintf("%s:%d", identity, job.Progress)
	}
	eventID := uuid.NewSHA1(uuid.NameSpaceURL, []byte(identity)).String()
	event := dto.PlatformGenerationCallbackEvent{
		APIVersion:    dto.PlatformRelayAPIVersion,
		SchemaVersion: dto.PlatformRelaySchemaVersion,
		EventID:       eventID,
		Type:          dto.PlatformGenerationCallbackEventType,
		OccurredAt:    snapshot.UpdatedAt.UTC(),
		Job: dto.PlatformGenerationCallbackEventJob{
			APIVersion:                 dto.PlatformRelayAPIVersion,
			ID:                         snapshot.ID,
			ClientReferenceID:          snapshot.ClientReferenceID,
			Status:                     snapshot.Status,
			Progress:                   snapshot.Progress,
			Outputs:                    snapshot.Outputs,
			Error:                      snapshot.Error,
			ExpectedCapabilityRevision: snapshot.ExpectedCapabilityRevision,
			CapabilityRevision:         snapshot.CapabilityRevision,
			ReservationAction:          snapshot.ReservationAction,
		},
	}
	payload, err := SerializePlatformGenerationCallbackEvent(event)
	if err != nil {
		return nil, false, err
	}
	digest := sha256.Sum256(payload)
	requestID := platformGenerationCallbackRequestID(job.RequestID, eventID)
	return &model.PlatformGenerationCallbackDelivery{
		ID:             eventID,
		TenantID:       job.TenantID,
		SourceClientID: job.SourceClientID,
		JobID:          job.ID,
		CallbackURL:    job.CallbackURL,
		RequestID:      requestID,
		PayloadJSON:    string(payload),
		PayloadSHA256:  fmt.Sprintf("%x", digest),
		MaxAttempts:    model.DefaultPlatformGenerationCallbackMaxAttempts,
	}, true, nil
}

func EnqueuePlatformGenerationCallbackForJob(job model.PlatformGenerationJob) (bool, error) {
	delivery, eligible, err := BuildPlatformGenerationCallbackDelivery(job)
	if err != nil || !eligible {
		return false, err
	}
	return model.CreatePlatformGenerationCallbackDelivery(delivery)
}

// EnqueuePlatformGenerationCallbackForJobTx is the atomic integration point
// for generation status transitions. The caller supplies the transaction that
// persisted the matching job snapshot.
func EnqueuePlatformGenerationCallbackForJobTx(tx *gorm.DB, job model.PlatformGenerationJob) (bool, error) {
	delivery, eligible, err := BuildPlatformGenerationCallbackDelivery(job)
	if err != nil || !eligible {
		return false, err
	}
	return model.CreatePlatformGenerationCallbackDeliveryTx(tx, delivery)
}

func SerializePlatformGenerationCallbackEvent(event dto.PlatformGenerationCallbackEvent) ([]byte, error) {
	if err := event.Validate(); err != nil {
		return nil, err
	}
	return common.Marshal(event)
}

// SignPlatformGenerationCallback signs the raw bytes that will be sent. The
// receiver must verify timestamp + "." + event_id + "." + raw_body exactly.
func SignPlatformGenerationCallback(secret string, timestamp int64, eventID string, body []byte) (string, error) {
	if secret == "" || timestamp <= 0 || len(body) == 0 {
		return "", fmt.Errorf("generation callback signing input is incomplete")
	}
	if _, err := uuid.Parse(eventID); err != nil {
		return "", fmt.Errorf("generation callback event id is invalid")
	}
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(strconv.FormatInt(timestamp, 10)))
	_, _ = mac.Write([]byte("."))
	_, _ = mac.Write([]byte(eventID))
	_, _ = mac.Write([]byte("."))
	_, _ = mac.Write(body)
	return fmt.Sprintf("v1=%x", mac.Sum(nil)), nil
}

func DispatchPlatformGenerationCallback(
	ctx context.Context,
	delivery model.PlatformGenerationCallbackDelivery,
	secret string,
) PlatformGenerationCallbackDispatchResult {
	production := PlatformRelayProductionSecurityEnabled()
	client := newPlatformGenerationCallbackHTTPClient(production)
	return dispatchPlatformGenerationCallback(ctx, client, production, time.Now().UTC(), delivery, secret)
}

// DeliverPlatformGenerationCallbackClaim dispatches and then applies the
// claim-token-fenced database transition. It processes only the single item
// represented by claim; callers retain control over worker scheduling.
func DeliverPlatformGenerationCallbackClaim(
	ctx context.Context,
	claim model.PlatformGenerationCallbackClaim,
	principal PlatformRelayPrincipal,
) (string, bool, error) {
	result := PlatformGenerationCallbackDispatchResult{
		Retryable: true,
		Failure:   model.PlatformGenerationCallbackFailureConfiguration,
	}
	if principal.ClientID == claim.Delivery.SourceClientID &&
		principal.TenantID == claim.Delivery.TenantID &&
		principal.CallbackURL == claim.Delivery.CallbackURL &&
		principal.CallbackSecret != "" {
		result = DispatchPlatformGenerationCallback(ctx, claim.Delivery, principal.CallbackSecret)
	}
	if result.Delivered {
		won, err := model.CompletePlatformGenerationCallbackDelivery(
			claim.Delivery.ID,
			claim.Token,
			result.ResponseStatus,
		)
		return model.PlatformGenerationCallbackDelivered, won, err
	}
	if !result.Retryable {
		won, err := model.DeadLetterPlatformGenerationCallbackDelivery(
			claim.Delivery.ID,
			claim.Token,
			result.Failure,
			result.ResponseStatus,
		)
		return model.PlatformGenerationCallbackDeadLetter, won, err
	}
	return model.ReleasePlatformGenerationCallbackDelivery(
		claim.Delivery.ID,
		claim.Token,
		PlatformGenerationCallbackRetryDelay(claim.Delivery.Attempts),
		result.Failure,
		result.ResponseStatus,
	)
}

func PlatformGenerationCallbackRetryDelay(attempt int) time.Duration {
	if attempt < 1 {
		attempt = 1
	}
	delay := platformGenerationCallbackBaseDelay
	for current := 1; current < attempt && delay < platformGenerationCallbackMaxDelay; current++ {
		if delay > platformGenerationCallbackMaxDelay/2 {
			return platformGenerationCallbackMaxDelay
		}
		delay *= 2
	}
	if delay > platformGenerationCallbackMaxDelay {
		return platformGenerationCallbackMaxDelay
	}
	return delay
}

func dispatchPlatformGenerationCallback(
	ctx context.Context,
	client *http.Client,
	production bool,
	now time.Time,
	delivery model.PlatformGenerationCallbackDelivery,
	secret string,
) PlatformGenerationCallbackDispatchResult {
	if client == nil || secret == "" || strings.TrimSpace(delivery.RequestID) == "" {
		return PlatformGenerationCallbackDispatchResult{
			Retryable: true,
			Failure:   model.PlatformGenerationCallbackFailureConfiguration,
		}
	}
	if err := validatePlatformGenerationCallbackTarget(delivery.CallbackURL, production); err != nil {
		return PlatformGenerationCallbackDispatchResult{
			Retryable: false,
			Failure:   model.PlatformGenerationCallbackFailureTarget,
		}
	}
	payload := []byte(delivery.PayloadJSON)
	payloadDigest := sha256.Sum256(payload)
	if fmt.Sprintf("%x", payloadDigest) != delivery.PayloadSHA256 {
		return PlatformGenerationCallbackDispatchResult{
			Retryable: false,
			Failure:   model.PlatformGenerationCallbackFailurePayload,
		}
	}
	var event dto.PlatformGenerationCallbackEvent
	if err := common.Unmarshal(payload, &event); err != nil || event.Validate() != nil || event.EventID != delivery.ID {
		return PlatformGenerationCallbackDispatchResult{
			Retryable: false,
			Failure:   model.PlatformGenerationCallbackFailurePayload,
		}
	}

	timestamp := now.Unix()
	signature, err := SignPlatformGenerationCallback(secret, timestamp, delivery.ID, payload)
	if err != nil {
		return PlatformGenerationCallbackDispatchResult{
			Retryable: false,
			Failure:   model.PlatformGenerationCallbackFailurePayload,
		}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, delivery.CallbackURL, bytes.NewReader(payload))
	if err != nil {
		return PlatformGenerationCallbackDispatchResult{
			Retryable: false,
			Failure:   model.PlatformGenerationCallbackFailureTarget,
		}
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", platformGenerationCallbackUserAgent)
	req.Header.Set("X-Relay-Event-ID", delivery.ID)
	req.Header.Set("X-Relay-Timestamp", strconv.FormatInt(timestamp, 10))
	req.Header.Set("X-Request-ID", delivery.RequestID)
	req.Header.Set("X-Relay-Signature", signature)

	response, err := client.Do(req)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		return PlatformGenerationCallbackDispatchResult{
			Retryable: true,
			Failure:   model.PlatformGenerationCallbackFailureTransport,
		}
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
	if response.StatusCode >= 200 && response.StatusCode < 300 {
		return PlatformGenerationCallbackDispatchResult{
			Delivered:      true,
			ResponseStatus: response.StatusCode,
		}
	}
	return PlatformGenerationCallbackDispatchResult{
		Retryable:      true,
		ResponseStatus: response.StatusCode,
		Failure:        model.PlatformGenerationCallbackFailureEndpoint,
	}
}

func newPlatformGenerationCallbackHTTPClient(production bool) *http.Client {
	var transport *http.Transport
	if defaultTransport, ok := http.DefaultTransport.(*http.Transport); ok && defaultTransport != nil {
		transport = defaultTransport.Clone()
	} else {
		transport = &http.Transport{}
	}
	// Callback destinations are tenant-configured. Never inherit an ambient
	// proxy because that bypasses destination-address validation.
	transport.Proxy = nil
	if production {
		networkDialer := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
		publicOnly := platformGenerationPublicCallbackProtection()
		protectedDialer := &protectedFetchDialer{
			resolver:    net.DefaultResolver,
			dialContext: networkDialer.DialContext,
			getProtection: func() (*common.SSRFProtection, bool, error) {
				return publicOnly, true, nil
			},
		}
		transport.DialContext = protectedDialer.DialContext
	}
	return &http.Client{
		Transport: transport,
		Timeout:   platformGenerationCallbackTimeout,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

func validatePlatformGenerationCallbackTarget(rawURL string, production bool) error {
	parsed, err := url.ParseRequestURI(rawURL)
	if err != nil || parsed == nil || !parsed.IsAbs() || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" {
		return fmt.Errorf("generation callback target rejected")
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return fmt.Errorf("generation callback target rejected")
	}
	if !production {
		return nil
	}
	if parsed.Scheme != "https" {
		return fmt.Errorf("generation callback target rejected")
	}
	host := parsed.Hostname()
	if host == "" || strings.Contains(host, "%") {
		return fmt.Errorf("generation callback target rejected")
	}
	port := 443
	if parsed.Port() != "" {
		port, err = strconv.Atoi(parsed.Port())
		if err != nil {
			return fmt.Errorf("generation callback target rejected")
		}
	}
	if err := platformGenerationPublicCallbackProtection().ValidateNetworkTarget(host, port); err != nil {
		return fmt.Errorf("generation callback target rejected")
	}
	return nil
}

func platformGenerationPublicCallbackProtection() *common.SSRFProtection {
	return &common.SSRFProtection{
		AllowPrivateIp:         false,
		DomainFilterMode:       false,
		DomainList:             nil,
		IpFilterMode:           false,
		IpList:                 nil,
		AllowedPorts:           nil,
		ApplyIPFilterForDomain: true,
	}
}

func platformGenerationCallbackRequestID(requestID string, eventID string) string {
	if len(requestID) >= 1 && len(requestID) <= 80 && strings.TrimSpace(requestID) == requestID {
		valid := true
		for _, character := range requestID {
			if character < 0x20 || character == 0x7f {
				valid = false
				break
			}
		}
		if valid {
			return requestID
		}
	}
	return "relay-callback-" + eventID
}
