package service

import (
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type platformGenerationCallbackRoundTripFunc func(*http.Request) (*http.Response, error)

func (function platformGenerationCallbackRoundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func TestSerializeAndSignPlatformGenerationCallbackEventExactly(t *testing.T) {
	clientReferenceID := "platform-task-42"
	revision := "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	event := dto.PlatformGenerationCallbackEvent{
		APIVersion:    dto.PlatformRelayAPIVersion,
		SchemaVersion: dto.PlatformRelaySchemaVersion,
		EventID:       "123e4567-e89b-12d3-a456-426614174000",
		Type:          dto.PlatformGenerationCallbackEventType,
		OccurredAt:    time.Date(2026, time.August, 6, 12, 34, 56, 0, time.UTC),
		Job: dto.PlatformGenerationCallbackEventJob{
			APIVersion:                 dto.PlatformRelayAPIVersion,
			ID:                         "123e4567-e89b-12d3-a456-426614174001",
			ClientReferenceID:          &clientReferenceID,
			Status:                     "processing",
			Progress:                   42,
			Outputs:                    []dto.PlatformGenerationArtifact{},
			Error:                      nil,
			ExpectedCapabilityRevision: revision,
			CapabilityRevision:         revision,
			ReservationAction:          "hold",
		},
	}

	payload, err := SerializePlatformGenerationCallbackEvent(event)
	require.NoError(t, err)
	assert.Equal(t,
		`{"api_version":"v1","schema_version":1,"event_id":"123e4567-e89b-12d3-a456-426614174000","type":"generation.status_changed","occurred_at":"2026-08-06T12:34:56Z","job":{"api_version":"v1","id":"123e4567-e89b-12d3-a456-426614174001","client_reference_id":"platform-task-42","status":"processing","progress":42,"outputs":[],"error":null,"expected_capability_revision":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","capability_revision":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","reservation_action":"hold"}}`,
		string(payload),
	)

	signature, err := SignPlatformGenerationCallback(
		"callback-secret-测试",
		1786003200,
		"123e4567-e89b-12d3-a456-426614174000",
		[]byte(`{"api_version":"v1","schema_version":1}`),
	)
	require.NoError(t, err)
	assert.Equal(t, "v1=8e99979f5a3236701c1d7bd07ee369baed7b8a042d914aa26fa4eff82ff0cb8a", signature)
}

func TestBuildPlatformGenerationCallbackPreservesNullableClientReferenceID(t *testing.T) {
	revision := "sha256:" + strings.Repeat("c", 64)
	now := time.Date(2026, time.August, 6, 12, 34, 56, 0, time.UTC)
	baseRequest := dto.NewPlatformGenerationRequest()
	baseRequest.Model = "video-model"
	baseRequest.ExpectedCapabilityRevision = revision
	baseRequest.Mode = "text_to_video"
	baseRequest.Inputs.Prompt = "hello"

	for _, test := range []struct {
		name      string
		reference *string
		expected  string
	}{
		{name: "null", reference: nil, expected: `"client_reference_id":null`},
		{name: "empty string", reference: stringPointer(""), expected: `"client_reference_id":""`},
	} {
		t.Run(test.name, func(t *testing.T) {
			request := baseRequest
			request.ClientReferenceID = test.reference
			requestJSON, err := common.Marshal(request)
			require.NoError(t, err)
			job := model.PlatformGenerationJob{
				ID:                         uuid.NewString(),
				TenantID:                   uuid.NewString(),
				SourceClientID:             "platform",
				RequestID:                  "callback-nullable-reference",
				RequestJSON:                string(requestJSON),
				ExpectedCapabilityRevision: revision,
				CapabilityRevision:         revision,
				Status:                     model.PlatformGenerationStatusProcessing,
				Progress:                   42,
				CallbackURL:                "https://callbacks.example.test/generations",
				OutputsJSON:                "[]",
				ErrorDetailsJSON:           "{}",
				CreatedAt:                  now,
				UpdatedAt:                  now,
			}
			delivery, eligible, err := BuildPlatformGenerationCallbackDelivery(job)
			require.NoError(t, err)
			require.True(t, eligible)
			require.NotNil(t, delivery)
			assert.Contains(t, delivery.PayloadJSON, test.expected)
		})
	}
}

func stringPointer(value string) *string {
	return &value
}

func TestDispatchPlatformGenerationCallbackSendsExactSignedEnvelopeWithoutFollowingRedirects(t *testing.T) {
	eventID := "123e4567-e89b-12d3-a456-426614174010"
	payload := validPlatformGenerationCallbackPayload(t, eventID)

	received := make(chan *http.Request, 1)
	receivedBody := make(chan []byte, 1)
	endpoint := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		body, _ := io.ReadAll(request.Body)
		received <- request.Clone(request.Context())
		receivedBody <- body
		writer.WriteHeader(http.StatusNoContent)
	}))
	t.Cleanup(endpoint.Close)

	delivery := callbackDeliveryFixture(eventID, endpoint.URL, payload)
	result := dispatchPlatformGenerationCallback(
		context.Background(),
		newPlatformGenerationCallbackHTTPClient(false),
		false,
		time.Unix(1786003200, 0).UTC(),
		delivery,
		"callback-secret",
	)
	require.True(t, result.Delivered)
	assert.Equal(t, http.StatusNoContent, result.ResponseStatus)
	request := <-received
	assert.Equal(t, eventID, request.Header.Get("X-Relay-Event-ID"))
	assert.Equal(t, "1786003200", request.Header.Get("X-Relay-Timestamp"))
	assert.Equal(t, delivery.RequestID, request.Header.Get("X-Request-ID"))
	assert.Equal(t, "application/json", request.Header.Get("Content-Type"))
	assert.Equal(t, payload, <-receivedBody)
	expectedSignature, err := SignPlatformGenerationCallback("callback-secret", 1786003200, eventID, payload)
	require.NoError(t, err)
	assert.Equal(t, expectedSignature, request.Header.Get("X-Relay-Signature"))

	var redirectedRequests atomic.Int32
	redirectTarget := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		redirectedRequests.Add(1)
	}))
	t.Cleanup(redirectTarget.Close)
	redirectSource := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Location", redirectTarget.URL)
		writer.WriteHeader(http.StatusTemporaryRedirect)
	}))
	t.Cleanup(redirectSource.Close)
	delivery = callbackDeliveryFixture(eventID, redirectSource.URL, payload)
	result = dispatchPlatformGenerationCallback(
		context.Background(),
		newPlatformGenerationCallbackHTTPClient(false),
		false,
		time.Unix(1786003200, 0).UTC(),
		delivery,
		"callback-secret",
	)
	assert.False(t, result.Delivered)
	assert.True(t, result.Retryable)
	assert.Equal(t, http.StatusTemporaryRedirect, result.ResponseStatus)
	assert.Equal(t, model.PlatformGenerationCallbackFailureEndpoint, result.Failure)
	assert.Zero(t, redirectedRequests.Load())
}

func TestProductionCallbackPolicyRejectsHTTPAndNonPublicTargetsBeforeTransport(t *testing.T) {
	payload := validPlatformGenerationCallbackPayload(t, "123e4567-e89b-12d3-a456-426614174020")
	var requests atomic.Int32
	client := &http.Client{Transport: platformGenerationCallbackRoundTripFunc(func(*http.Request) (*http.Response, error) {
		requests.Add(1)
		return nil, fmt.Errorf("transport must not be reached")
	})}

	for _, target := range []string{
		"http://callbacks.example.com/status",
		"https://127.0.0.1/status",
		"https://[::1]/status",
	} {
		delivery := callbackDeliveryFixture("123e4567-e89b-12d3-a456-426614174020", target, payload)
		result := dispatchPlatformGenerationCallback(
			context.Background(),
			client,
			true,
			time.Unix(1786003200, 0).UTC(),
			delivery,
			"callback-secret",
		)
		assert.False(t, result.Delivered)
		assert.False(t, result.Retryable)
		assert.Equal(t, model.PlatformGenerationCallbackFailureTarget, result.Failure)
	}
	assert.Zero(t, requests.Load())
}

func validPlatformGenerationCallbackPayload(t *testing.T, eventID string) []byte {
	t.Helper()
	revision := "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	event := dto.PlatformGenerationCallbackEvent{
		APIVersion:    dto.PlatformRelayAPIVersion,
		SchemaVersion: dto.PlatformRelaySchemaVersion,
		EventID:       eventID,
		Type:          dto.PlatformGenerationCallbackEventType,
		OccurredAt:    time.Date(2026, time.August, 6, 12, 34, 56, 0, time.UTC),
		Job: dto.PlatformGenerationCallbackEventJob{
			APIVersion:                 dto.PlatformRelayAPIVersion,
			ID:                         "123e4567-e89b-12d3-a456-426614174099",
			ClientReferenceID:          nil,
			Status:                     "processing",
			Progress:                   25,
			Outputs:                    []dto.PlatformGenerationArtifact{},
			Error:                      nil,
			ExpectedCapabilityRevision: revision,
			CapabilityRevision:         revision,
			ReservationAction:          "hold",
		},
	}
	payload, err := SerializePlatformGenerationCallbackEvent(event)
	require.NoError(t, err)
	return payload
}

func callbackDeliveryFixture(eventID string, callbackURL string, payload []byte) model.PlatformGenerationCallbackDelivery {
	digest := sha256.Sum256(payload)
	return model.PlatformGenerationCallbackDelivery{
		ID:            eventID,
		CallbackURL:   callbackURL,
		RequestID:     "relay-request-42",
		PayloadJSON:   string(payload),
		PayloadSHA256: fmt.Sprintf("%x", digest),
	}
}
