package service

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/glebarez/sqlite"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func preparePlatformDownloadEdgeTest(t *testing.T) {
	t.Helper()
	previousDB := model.DB
	previousType := common.MainDatabaseType()
	dsn := "file:download-edge-" + uuid.NewString() + "?mode=memory&cache=shared"
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	sqlDB.SetMaxOpenConns(8)
	common.SetMainDatabaseType(common.DatabaseTypeSQLite)
	model.DB = db
	require.NoError(t, model.MigratePlatformProviderMonitorAndCostStorage())
	t.Cleanup(func() {
		model.DB = previousDB
		common.SetMainDatabaseType(previousType)
		require.NoError(t, sqlDB.Close())
	})
}

func testPlatformDownloadEdgeConfig(t *testing.T, upstreamURL, platformURL string) PlatformDownloadEdgeConfig {
	t.Helper()
	parsed, err := url.Parse(upstreamURL)
	require.NoError(t, err)
	seed := bytes.Repeat([]byte{0x8a}, ed25519.SeedSize)
	return PlatformDownloadEdgeConfig{
		ListenAddress:               ":0",
		PublicBaseURL:               "http://127.0.0.1:18888",
		AllowedOBSHosts:             []string{strings.ToLower(parsed.Host)},
		RegistrationToken:           strings.Repeat("a", 32),
		RegistrationSigningSecret:   strings.Repeat("b", 32),
		TicketTokenKey:              bytes.Repeat([]byte{0xc1}, 32),
		SourceEncryptionKey:         bytes.Repeat([]byte{0xd2}, 32),
		PlatformCompletionURL:       platformURL,
		PlatformEdgeCompletionToken: strings.Repeat("e", 32),
		CompletionSigningSecret:     strings.Repeat("f", 32),
		ProofSigningPrivateKey:      ed25519.NewKeyFromSeed(seed),
		ProofKeyID:                  "edge-proof-key-2026-08",
		ProofReadToken:              strings.Repeat("g", 32),
		ProducerSubject:             "relay-download-edge/test",
		TicketTTL:                   5 * time.Minute,
		SourceExpirySafetyMargin:    60 * time.Second,
		RegistrationMaxAge:          5 * time.Minute,
		TransferTimeout:             10 * time.Second,
		TransferCompletionMargin:    5 * time.Second,
		TicketClaimLease:            15 * time.Second,
		DeliveryClaimLease:          30 * time.Second,
		DeliveryCommitMargin:        10 * time.Second,
		DeliveryPollInterval:        time.Second,
		DeliveryMaxAttempts:         3,
		MaxArtifactBytes:            1024 * 1024,
	}
}

func TestPlatformDownloadEdgeRejectsUnsafeLeaseMargins(t *testing.T) {
	valid := testProductionPlatformDownloadEdgeConfig(t)
	require.NoError(t, valid.Validate())

	t.Run("transfer claim cannot expire before commit margin", func(t *testing.T) {
		candidate := valid
		candidate.TicketClaimLease = candidate.TransferTimeout + candidate.TransferCompletionMargin - time.Second
		require.Error(t, candidate.Validate())
	})
	t.Run("delivery claim must cover Platform HTTP timeout and commit margin", func(t *testing.T) {
		candidate := valid
		candidate.DeliveryClaimLease = 15*time.Second + candidate.DeliveryCommitMargin - time.Second
		require.Error(t, candidate.Validate())
	})
}

func testProductionPlatformDownloadEdgeConfig(t *testing.T) PlatformDownloadEdgeConfig {
	t.Helper()
	config := testPlatformDownloadEdgeConfig(
		t,
		"https://artifact-bucket.obs.cn-north-4.myhuaweicloud.com/results/video.mp4?Signature=test",
		"https://platform.internal.example.com/internal/artifact-download-completions/edge-gateway",
	)
	config.Production = true
	config.PublicBaseURL = "https://downloads.example.com"
	return config
}

func platformDownloadEdgeRuntimeTestSecret(label string) string {
	digest := sha256.Sum256([]byte("download-edge-runtime-test:" + label))
	return label + "-" + hex.EncodeToString(digest[:])
}

func platformDownloadEdgeRuntimeTestKey(offset byte) []byte {
	key := make([]byte, ed25519.SeedSize)
	for index := range key {
		key[index] = offset + byte(index)
	}
	return key
}

func platformDownloadEdgeRuntimeTestDocument() PlatformDownloadEdgeRuntimeSecretsFile {
	return PlatformDownloadEdgeRuntimeSecretsFile{
		Kind: PlatformDownloadEdgeRuntimeSecretsKind, SchemaVersion: PlatformDownloadEdgeRuntimeSecretsSchemaVersion,
		RegistrationToken:           platformDownloadEdgeRuntimeTestSecret("registration-token"),
		RegistrationSigningSecret:   platformDownloadEdgeRuntimeTestSecret("registration-signing"),
		TicketTokenKeyBase64:        base64.StdEncoding.EncodeToString(platformDownloadEdgeRuntimeTestKey(0)),
		SourceEncryptionKeyBase64:   base64.StdEncoding.EncodeToString(platformDownloadEdgeRuntimeTestKey(32)),
		PlatformEdgeCompletionToken: platformDownloadEdgeRuntimeTestSecret("edge-completion-token"),
		CompletionSigningSecret:     platformDownloadEdgeRuntimeTestSecret("completion-signing"),
		ProofSigningSeedBase64:      base64.StdEncoding.EncodeToString(platformDownloadEdgeRuntimeTestKey(64)),
		ProofReadToken:              platformDownloadEdgeRuntimeTestSecret("proof-read"),
	}
}

func setPlatformDownloadEdgeRuntimeSecretsDocument(t *testing.T, document PlatformDownloadEdgeRuntimeSecretsFile) {
	t.Helper()
	raw, err := json.Marshal(document)
	require.NoError(t, err)
	previousReader := readPlatformDownloadEdgeRuntimeSecretFile
	readPlatformDownloadEdgeRuntimeSecretFile = func(environment string, maximumBytes int64) ([]byte, error) {
		require.Equal(t, platformDownloadEdgeRuntimeSecretsFileEnvironment, environment)
		require.Equal(t, platformDownloadEdgeRuntimeSecretsFileMaxBytes, maximumBytes)
		return append([]byte(nil), raw...), nil
	}
	t.Cleanup(func() {
		readPlatformDownloadEdgeRuntimeSecretFile = previousReader
		clear(raw)
	})
}

func unsetPlatformDownloadEdgeForbiddenSecretEnvironments(t *testing.T) {
	t.Helper()
	for _, environment := range platformDownloadEdgeForbiddenSecretEnvironments {
		value, present := os.LookupEnv(environment)
		require.NoError(t, os.Unsetenv(environment))
		environment := environment
		t.Cleanup(func() {
			if present {
				require.NoError(t, os.Setenv(environment, value))
				return
			}
			require.NoError(t, os.Unsetenv(environment))
		})
	}
}

func setPlatformDownloadEdgeEnvironment(t *testing.T, environment string) {
	t.Helper()
	previousVerifier := verifyPlatformDownloadEdgeSecretIsolationReceipt
	verifyPlatformDownloadEdgeSecretIsolationReceipt = func(consumer string) error {
		require.Equal(t, PlatformRelaySecretIsolationConsumerEdge, consumer)
		return nil
	}
	t.Cleanup(func() { verifyPlatformDownloadEdgeSecretIsolationReceipt = previousVerifier })
	t.Setenv("APP_ENV", environment)
	t.Setenv("DEPLOYMENT_ENV", environment)
	t.Setenv("DEBUG", "false")
	if environment == "development" {
		t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	} else {
		unsetPlatformDownloadEdgeForbiddenSecretEnvironments(t)
		t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
		t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "true")
		t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "true")
		t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "true")
		t.Setenv(platformDownloadEdgeRuntimeSecretsFileEnvironment, "/run/secrets/relay-download-edge-runtime-secrets.json")
		setPlatformDownloadEdgeRuntimeSecretsDocument(t, platformDownloadEdgeRuntimeTestDocument())
	}
	t.Setenv("RELAY_DOWNLOAD_EDGE_ENVIRONMENT", environment)
	t.Setenv("RELAY_DOWNLOAD_EDGE_LISTEN_ADDRESS", ":8080")
	t.Setenv("RELAY_DOWNLOAD_EDGE_PUBLIC_BASE_URL", "https://downloads.example.com")
	t.Setenv("RELAY_DOWNLOAD_EDGE_ALLOWED_OBS_HOSTS", "artifact-bucket.obs.cn-north-4.myhuaweicloud.com")
	if environment == "development" {
		t.Setenv("RELAY_DOWNLOAD_EDGE_REGISTRATION_TOKEN", strings.Repeat("a", 32))
		t.Setenv("RELAY_DOWNLOAD_EDGE_REGISTRATION_SIGNING_SECRET", strings.Repeat("b", 32))
		t.Setenv("RELAY_DOWNLOAD_EDGE_TICKET_TOKEN_KEY_BASE64", base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xc1}, 32)))
		t.Setenv("RELAY_DOWNLOAD_EDGE_SOURCE_ENCRYPTION_KEY_BASE64", base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xd2}, 32)))
		t.Setenv("RELAY_DOWNLOAD_EDGE_PLATFORM_INTERNAL_TOKEN", strings.Repeat("e", 32))
		t.Setenv("RELAY_DOWNLOAD_EDGE_COMPLETION_SIGNING_SECRET", strings.Repeat("f", 32))
		t.Setenv("RELAY_DOWNLOAD_EDGE_PROOF_PRIVATE_KEY_BASE64", base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0x8a}, ed25519.SeedSize)))
		t.Setenv("RELAY_DOWNLOAD_EDGE_PROOF_READ_TOKEN", strings.Repeat("g", 32))
	}
	t.Setenv("RELAY_DOWNLOAD_EDGE_PLATFORM_COMPLETION_URL", "https://platform.internal.example.com/internal/artifact-download-completions/edge-gateway")
	t.Setenv("RELAY_DOWNLOAD_EDGE_PROOF_KEY_ID", "edge-proof-key-2026-08")
	t.Setenv("RELAY_DOWNLOAD_EDGE_PRODUCER_SUBJECT", "relay-download-edge/test")
}

func TestPlatformDownloadEdgeProtectedStagingUsesProductionSecurityPolicy(t *testing.T) {
	setPlatformDownloadEdgeEnvironment(t, "staging")
	config, err := PlatformDownloadEdgeConfigFromEnv()
	require.NoError(t, err)
	require.False(t, config.Production)
	require.True(t, config.Protected)

	t.Run("public HTTP is rejected", func(t *testing.T) {
		setPlatformDownloadEdgeEnvironment(t, "staging")
		t.Setenv("RELAY_DOWNLOAD_EDGE_PUBLIC_BASE_URL", "http://127.0.0.1:8080")
		_, err := PlatformDownloadEdgeConfigFromEnv()
		require.Error(t, err)
	})
	t.Run("non-official OBS host is rejected", func(t *testing.T) {
		setPlatformDownloadEdgeEnvironment(t, "staging")
		t.Setenv("RELAY_DOWNLOAD_EDGE_ALLOWED_OBS_HOSTS", "objects.example.com")
		_, err := PlatformDownloadEdgeConfigFromEnv()
		require.Error(t, err)
	})
	t.Run("placeholder credential is rejected", func(t *testing.T) {
		setPlatformDownloadEdgeEnvironment(t, "staging")
		document := platformDownloadEdgeRuntimeTestDocument()
		document.RegistrationToken = strings.Repeat("replace-with-secret-", 2)
		setPlatformDownloadEdgeRuntimeSecretsDocument(t, document)
		_, err := PlatformDownloadEdgeConfigFromEnv()
		require.Error(t, err)
	})
}

func TestParsePlatformDownloadEdgeRuntimeSecretsAcceptsBinaryControlBytes(t *testing.T) {
	document := platformDownloadEdgeRuntimeTestDocument()
	raw, err := json.Marshal(document)
	require.NoError(t, err)
	defer clear(raw)
	parsed, err := ParsePlatformDownloadEdgeRuntimeSecretsFile(raw)
	require.NoError(t, err)
	ticketKey, sourceKey, privateKey, err := platformDownloadEdgeRuntimeSecretsFromDocument(parsed)
	require.NoError(t, err)
	require.Contains(t, ticketKey, byte(0))
	require.Equal(t, platformDownloadEdgeRuntimeTestKey(32), sourceKey)
	require.Equal(t, platformDownloadEdgeRuntimeTestKey(64), privateKey.Seed())
}

func TestParsePlatformDownloadEdgeRuntimeSecretsRejectsNoncanonicalAndCrossRepresentationKeys(t *testing.T) {
	t.Run("64-byte private key is not a seed", func(t *testing.T) {
		document := platformDownloadEdgeRuntimeTestDocument()
		document.ProofSigningSeedBase64 = base64.StdEncoding.EncodeToString(
			ed25519.NewKeyFromSeed(platformDownloadEdgeRuntimeTestKey(64)),
		)
		raw, err := json.Marshal(document)
		require.NoError(t, err)
		defer clear(raw)
		_, err = ParsePlatformDownloadEdgeRuntimeSecretsFile(raw)
		require.EqualError(t, err, "download edge runtime secret file is invalid")
	})

	t.Run("decoded key cannot reuse a text secret", func(t *testing.T) {
		document := platformDownloadEdgeRuntimeTestDocument()
		document.RegistrationToken = "0123456789abcdefghijklmnopqrstuv"
		document.TicketTokenKeyBase64 = base64.StdEncoding.EncodeToString([]byte(document.RegistrationToken))
		raw, err := json.Marshal(document)
		require.NoError(t, err)
		defer clear(raw)
		_, err = ParsePlatformDownloadEdgeRuntimeSecretsFile(raw)
		require.EqualError(t, err, "download edge runtime secret file is invalid")
	})
}

func TestPlatformDownloadEdgeProtectedEnvironmentRejectsUnrelatedAndLegacySecretSources(t *testing.T) {
	for _, environment := range platformDownloadEdgeForbiddenSecretEnvironments {
		t.Run(environment, func(t *testing.T) {
			setPlatformDownloadEdgeEnvironment(t, "staging")
			t.Setenv(environment, "")
			_, err := PlatformDownloadEdgeConfigFromEnv()
			require.EqualError(t, err, "protected download edge received a legacy raw secret environment variable")
		})
	}
	t.Run("case-folded raw secret", func(t *testing.T) {
		setPlatformDownloadEdgeEnvironment(t, "staging")
		t.Setenv("relay_artifact_signing_secret", "")
		_, err := PlatformDownloadEdgeConfigFromEnv()
		require.EqualError(t, err, "protected download edge received a legacy raw secret environment variable")
	})
}

func TestPlatformDownloadEdgeRoleAttestedDevelopmentUsesProtectedPolicy(t *testing.T) {
	setPlatformDownloadEdgeEnvironment(t, "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	t.Setenv("RELAY_DOWNLOAD_EDGE_PLATFORM_COMPLETION_URL", "http://127.0.0.1:8081/internal/artifact-download-completions/edge-gateway")
	_, err := PlatformDownloadEdgeConfigFromEnv()
	require.Error(t, err)
}

func TestPlatformDownloadEdgeEnvironmentMustBeCanonical(t *testing.T) {
	setPlatformDownloadEdgeEnvironment(t, "Staging")
	_, err := PlatformDownloadEdgeConfigFromEnv()
	require.Error(t, err)
}

func TestPlatformDownloadEdgeProtectedEnvironmentRejectsDatabasePolicyDowngrade(t *testing.T) {
	for _, environment := range []string{
		"RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED",
		"RELAY_DATABASE_TLS_ATTESTATION_REQUIRED",
		"RELAY_DATABASE_SECRET_FILES_REQUIRED",
		"RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED",
	} {
		t.Run(environment, func(t *testing.T) {
			setPlatformDownloadEdgeEnvironment(t, "staging")
			t.Setenv(environment, "false")
			_, err := PlatformDownloadEdgeConfigFromEnv()
			require.EqualError(t, err, "protected download edge environment is invalid")
		})
	}
}

func TestPlatformDownloadEdgeProductionRejectsDefaultPlaceholderAndReusedCredentials(t *testing.T) {
	valid := testProductionPlatformDownloadEdgeConfig(t)
	require.NoError(t, valid.Validate())
	placeholder := strings.Repeat("replace-with-secret-", 2)
	placeholderKey := []byte(("replace-with-key-" + strings.Repeat("x", 32))[:32])

	mutations := []struct {
		name   string
		mutate func(*PlatformDownloadEdgeConfig)
	}{
		{name: "registration token placeholder", mutate: func(config *PlatformDownloadEdgeConfig) { config.RegistrationToken = placeholder }},
		{name: "registration signing placeholder", mutate: func(config *PlatformDownloadEdgeConfig) { config.RegistrationSigningSecret = placeholder }},
		{name: "ticket key placeholder", mutate: func(config *PlatformDownloadEdgeConfig) {
			config.TicketTokenKey = append([]byte(nil), placeholderKey...)
		}},
		{name: "source key placeholder", mutate: func(config *PlatformDownloadEdgeConfig) {
			config.SourceEncryptionKey = append([]byte(nil), placeholderKey...)
		}},
		{name: "Platform token placeholder", mutate: func(config *PlatformDownloadEdgeConfig) { config.PlatformEdgeCompletionToken = placeholder }},
		{name: "completion signing placeholder", mutate: func(config *PlatformDownloadEdgeConfig) { config.CompletionSigningSecret = placeholder }},
		{name: "proof seed placeholder", mutate: func(config *PlatformDownloadEdgeConfig) {
			config.ProofSigningPrivateKey = ed25519.NewKeyFromSeed(append([]byte(nil), placeholderKey...))
		}},
		{name: "proof read placeholder", mutate: func(config *PlatformDownloadEdgeConfig) { config.ProofReadToken = placeholder }},
		{name: "development ticket key", mutate: func(config *PlatformDownloadEdgeConfig) {
			config.TicketTokenKey = []byte(platformDownloadEdgeDevelopmentTicketKey)
		}},
		{name: "development source key", mutate: func(config *PlatformDownloadEdgeConfig) {
			config.SourceEncryptionKey = []byte(platformDownloadEdgeDevelopmentSourceKey)
		}},
		{name: "development proof seed", mutate: func(config *PlatformDownloadEdgeConfig) {
			config.ProofSigningPrivateKey = ed25519.NewKeyFromSeed([]byte(platformDownloadEdgeDevelopmentProofSeed))
		}},
		{name: "ticket and source key reused", mutate: func(config *PlatformDownloadEdgeConfig) {
			config.SourceEncryptionKey = append([]byte(nil), config.TicketTokenKey...)
		}},
		{name: "ticket and proof seed reused", mutate: func(config *PlatformDownloadEdgeConfig) {
			config.ProofSigningPrivateKey = ed25519.NewKeyFromSeed(append([]byte(nil), config.TicketTokenKey...))
		}},
	}
	for _, item := range mutations {
		t.Run(item.name, func(t *testing.T) {
			candidate := valid
			item.mutate(&candidate)
			require.Error(t, candidate.Validate())
		})
	}
}

func registerPlatformDownloadEdgeTicket(
	t *testing.T,
	gateway *PlatformDownloadEdgeGateway,
	config PlatformDownloadEdgeConfig,
	sourceURL string,
	payload []byte,
	registrationRequestID string,
) PlatformDownloadTicketRegistrationResponse {
	t.Helper()
	request := httptest.NewRequest(http.MethodPost, PlatformDownloadEdgeRegistrationPath, bytes.NewReader(payload))
	timestamp := strconvFormatInt(gateway.clock().Unix())
	signature, err := SignPlatformDownloadEdgeRegistration(
		config.RegistrationSigningSecret, timestamp, registrationRequestID, payload,
	)
	require.NoError(t, err)
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("X-Download-Gateway-Token", config.RegistrationToken)
	request.Header.Set("X-Download-Gateway-Timestamp", timestamp)
	request.Header.Set("X-Download-Gateway-Request-ID", registrationRequestID)
	request.Header.Set("X-Download-Gateway-Signature", signature)
	response := httptest.NewRecorder()
	gateway.Handler().ServeHTTP(response, request)
	require.Equal(t, http.StatusCreated, response.Code, response.Body.String())
	var output PlatformDownloadTicketRegistrationResponse
	require.NoError(t, json.Unmarshal(response.Body.Bytes(), &output))
	require.True(t, output.OneTime)
	require.Equal(t, sourceURL != "", output.TicketURL != "")
	return output
}

func testPlatformDownloadEdgeRegistrationPayload(t *testing.T, sourceURL string, artifact []byte) ([]byte, PlatformDownloadTicketRegistrationRequest) {
	t.Helper()
	digest := sha256.Sum256(artifact)
	version := "obs-version-1"
	input := PlatformDownloadTicketRegistrationRequest{
		APIVersion: "v1", SchemaVersion: 1,
		DownloadRecordID: uuid.NewString(), CompanyID: uuid.NewString(), TaskID: uuid.NewString(),
		AssetID: uuid.NewString(), ExpectedSizeBytes: int64(len(artifact)),
		ArtifactSHA256: hex.EncodeToString(digest[:]), SourceURL: sourceURL,
		SourceExpiresAt:   time.Now().UTC().Add(10 * time.Minute).Format(time.RFC3339Nano),
		OBSBinding:        PlatformDownloadEdgeOBSBinding{Bucket: "artifact-bucket", ObjectKey: "results/video.mp4", VersionID: &version},
		IssuanceRequestID: "platform-download-issuance-" + uuid.NewString(), TransferReference: uuid.NewString(),
	}
	raw, err := json.Marshal(input)
	require.NoError(t, err)
	return raw, input
}

func TestPlatformDownloadEdgeFullTransferCallbackAndDetachedProof(t *testing.T) {
	preparePlatformDownloadEdgeTest(t)
	artifact := []byte("complete-artifact-body")
	var upstreamRequests atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		upstreamRequests.Add(1)
		assert.Equal(t, "/artifact-bucket/results/video.mp4", request.URL.Path)
		assert.Equal(t, "secret-query", request.URL.Query().Get("signature"))
		writer.Header().Set("Content-Type", "video/mp4")
		writer.Header().Set("Content-Length", fmt.Sprint(len(artifact)))
		_, _ = writer.Write(artifact)
	}))
	defer upstream.Close()

	completionID := uuid.NewString()
	var callbackRequests atomic.Int32
	platform := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		callbackRequests.Add(1)
		raw, err := io.ReadAll(request.Body)
		require.NoError(t, err)
		var payload platformDownloadCompletionPayloadForTest
		require.NoError(t, json.Unmarshal(raw, &payload))
		eventID := request.Header.Get("X-Download-Event-ID")
		timestamp := request.Header.Get("X-Download-Timestamp")
		timestampValue, err := strconvParseInt(timestamp)
		require.NoError(t, err)
		require.Equal(t, signPlatformDownloadCompletion(strings.Repeat("f", 32), timestampValue, eventID, raw), request.Header.Get("X-Download-Signature"))
		digest := sha256.Sum256(raw)
		now := time.Now().UTC()
		writePlatformDownloadEdgeJSON(writer, http.StatusCreated, map[string]any{
			"id": completionID, "download_record_id": payload.DownloadRecordID,
			"external_event_id": eventID, "source": "edge_gateway", "bytes_sent": payload.BytesSent,
			"completed_at": payload.CompletedAt, "verification_version": 1,
			"artifact_sha256": payload.ArtifactSHA256, "expected_size_bytes": payload.ExpectedSizeBytes,
			"http_status": 200, "transfer_scope": "full_body",
			"source_evidence": map[string]string{
				"gateway_request_id":         payload.GatewayRequestID,
				"gateway_transfer_reference": payload.GatewayTransferReference,
			},
			"signed_event_id": eventID, "signed_event_timestamp": time.Unix(timestampValue, 0).UTC(),
			"signed_payload_sha256": hex.EncodeToString(digest[:]), "verified_at": now, "created_at": now,
		})
	}))
	defer platform.Close()

	config := testPlatformDownloadEdgeConfig(t, upstream.URL, platform.URL+"/internal/artifact-download-completions/edge-gateway")
	gateway, err := NewPlatformDownloadEdgeGateway(config)
	require.NoError(t, err)
	raw, input := testPlatformDownloadEdgeRegistrationPayload(
		t, upstream.URL+"/artifact-bucket/results/video.mp4?signature=secret-query", artifact,
	)
	registrationID := uuid.NewString()
	registration := registerPlatformDownloadEdgeTicket(t, gateway, config, input.SourceURL, raw, registrationID)

	// The exact replay returns the same high-entropy URL without persisting the
	// bearer token. A changed payload under the same request id is rejected.
	replayed := registerPlatformDownloadEdgeTicket(t, gateway, config, input.SourceURL, raw, registrationID)
	assert.Equal(t, registration.TicketURL, replayed.TicketURL)
	var ticketCount int64
	require.NoError(t, model.DB.Model(&model.PlatformDownloadEdgeTicket{}).Count(&ticketCount).Error)
	assert.Equal(t, int64(1), ticketCount)
	var storedTicket model.PlatformDownloadEdgeTicket
	require.NoError(t, model.DB.First(&storedTicket).Error)
	assert.NotContains(t, string(storedTicket.SourceURLCiphertext), input.SourceURL)
	assert.NotContains(t, string(storedTicket.SourceURLCiphertext), "secret-query")
	assert.NotContains(t, fmt.Sprintf("%+v", storedTicket), strings.TrimPrefix(registration.TicketURL, config.PublicBaseURL+platformDownloadEdgePublicPrefix))

	ticketURL, err := url.Parse(registration.TicketURL)
	require.NoError(t, err)
	download := httptest.NewRequest(http.MethodGet, ticketURL.Path, nil)
	downloadResponse := httptest.NewRecorder()
	gateway.Handler().ServeHTTP(downloadResponse, download)
	require.Equal(t, http.StatusOK, downloadResponse.Code)
	assert.Equal(t, artifact, downloadResponse.Body.Bytes())
	assert.Equal(t, int32(1), upstreamRequests.Load())

	var event model.PlatformDownloadCompletionEvent
	require.NoError(t, model.DB.First(&event).Error)
	assert.Equal(t, input.DownloadRecordID, event.DownloadRecordID)
	assert.Equal(t, input.OBSBinding.ObjectKey, event.OBSObjectKey)
	assert.Equal(t, input.IssuanceRequestID, event.GatewayRequestID)
	assert.NotContains(t, event.PayloadJSON, "secret-query")
	var pending model.PlatformRelayExternalDelivery
	require.NoError(t, model.DB.Where("event_kind = ?", model.PlatformRelayDeliveryKindDownloadCompletion).First(&pending).Error)
	assert.Equal(t, model.PlatformRelayDeliveryPending, pending.State)

	didWork, err := gateway.RunDeliveryOnce(context.Background())
	require.NoError(t, err)
	assert.True(t, didWork)
	assert.Equal(t, int32(1), callbackRequests.Load())
	var proof model.PlatformDownloadCompletionProof
	require.NoError(t, model.DB.First(&proof).Error)
	assert.Equal(t, completionID, proof.CompletionID)
	signature, err := base64.StdEncoding.DecodeString(proof.SignatureBase64)
	require.NoError(t, err)
	assert.True(t, ed25519.Verify(
		config.ProofSigningPrivateKey.Public().(ed25519.PublicKey),
		append([]byte(platformDownloadEdgeProofDomain), []byte(proof.PayloadJSON)...), signature,
	))
	assert.NotContains(t, proof.PayloadJSON, "secret-query")
	assert.NotContains(t, proof.PayloadJSON, input.SourceURL)

	proofRequest := httptest.NewRequest(http.MethodGet, platformDownloadEdgeProofPrefix+event.ID+"/proof", nil)
	proofRequest.Header.Set("X-Download-Proof-Read-Token", config.ProofReadToken)
	proofResponse := httptest.NewRecorder()
	gateway.Handler().ServeHTTP(proofResponse, proofRequest)
	require.Equal(t, http.StatusOK, proofResponse.Code)
	assert.Equal(t, proof.PayloadJSON, proofResponse.Body.String())

	signatureRequest := httptest.NewRequest(http.MethodGet, platformDownloadEdgeProofPrefix+event.ID+"/proof/signature", nil)
	signatureRequest.Header.Set("X-Download-Proof-Read-Token", config.ProofReadToken)
	signatureResponse := httptest.NewRecorder()
	gateway.Handler().ServeHTTP(signatureResponse, signatureRequest)
	require.Equal(t, http.StatusOK, signatureResponse.Code)
	var signatureEnvelope PlatformDownloadCompletionProofSignature
	require.NoError(t, json.Unmarshal(signatureResponse.Body.Bytes(), &signatureEnvelope))
	assert.Equal(t, "sha256:"+proof.PayloadSHA256, signatureEnvelope.PayloadSHA256)
	assert.Equal(t, proof.SignatureBase64, signatureEnvelope.SignatureBase64)

	// ORM hooks and database triggers independently reject evidence mutation.
	assert.Error(t, model.DB.Model(&event).Update("artifact_sha256", strings.Repeat("0", 64)).Error)
	assert.Error(t, model.DB.Exec("DELETE FROM platform_download_completion_events WHERE id = ?", event.ID).Error)
	assert.Error(t, model.DB.Exec("UPDATE platform_download_completion_proofs SET key_id = 'tampered' WHERE event_id = ?", event.ID).Error)
}

func TestPlatformDownloadEdgeSourceExpiryCapsNewTicketAndExactReplayWinsFreshness(t *testing.T) {
	preparePlatformDownloadEdgeTest(t)
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.WriteHeader(http.StatusNotFound)
	}))
	defer upstream.Close()
	config := testPlatformDownloadEdgeConfig(
		t, upstream.URL, "http://127.0.0.1/internal/artifact-download-completions/edge-gateway",
	)
	gateway, err := NewPlatformDownloadEdgeGateway(config)
	require.NoError(t, err)
	artifact := []byte("source-expiry-artifact")

	raw, input := testPlatformDownloadEdgeRegistrationPayload(t, upstream.URL+"/results/video.mp4?sig=test", artifact)
	sourceExpiry, err := time.Parse(time.RFC3339Nano, input.SourceExpiresAt)
	require.NoError(t, err)
	input.SourceExpiresAt = time.Now().UTC().Add(3 * time.Minute).Format(time.RFC3339Nano)
	raw, err = json.Marshal(input)
	require.NoError(t, err)
	registration := registerPlatformDownloadEdgeTicket(t, gateway, config, input.SourceURL, raw, uuid.NewString())
	sourceExpiry, err = time.Parse(time.RFC3339Nano, input.SourceExpiresAt)
	require.NoError(t, err)
	assert.Less(t, registration.ExpiresSeconds, int(config.TicketTTL/time.Second))
	assert.GreaterOrEqual(t, registration.ExpiresSeconds, 30)
	assert.False(t, registration.ExpiresAt.After(sourceExpiry.Add(-config.SourceExpirySafetyMargin)))

	serve := func(rawBody []byte, registrationID string) *httptest.ResponseRecorder {
		t.Helper()
		request := httptest.NewRequest(http.MethodPost, PlatformDownloadEdgeRegistrationPath, bytes.NewReader(rawBody))
		timestamp := strconvFormatInt(gateway.clock().Unix())
		signature, signErr := SignPlatformDownloadEdgeRegistration(config.RegistrationSigningSecret, timestamp, registrationID, rawBody)
		require.NoError(t, signErr)
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("X-Download-Gateway-Token", config.RegistrationToken)
		request.Header.Set("X-Download-Gateway-Timestamp", timestamp)
		request.Header.Set("X-Download-Gateway-Request-ID", registrationID)
		request.Header.Set("X-Download-Gateway-Signature", signature)
		response := httptest.NewRecorder()
		gateway.Handler().ServeHTTP(response, request)
		return response
	}

	insufficient := input
	insufficient.DownloadRecordID = uuid.NewString()
	insufficient.TransferReference = uuid.NewString()
	insufficient.SourceExpiresAt = time.Now().UTC().Add(80 * time.Second).Format(time.RFC3339Nano)
	insufficientRaw, err := json.Marshal(insufficient)
	require.NoError(t, err)
	response := serve(insufficientRaw, uuid.NewString())
	assert.Equal(t, http.StatusUnprocessableEntity, response.Code)
	assert.Contains(t, response.Body.String(), "registration_source_lifetime_insufficient")

	replayInput := insufficient
	replayInput.DownloadRecordID = uuid.NewString()
	replayInput.TransferReference = uuid.NewString()
	replayInput.SourceExpiresAt = time.Now().UTC().Add(-time.Minute).Format(time.RFC3339Nano)
	replayRaw, err := json.Marshal(replayInput)
	require.NoError(t, err)
	replayDigest := sha256.Sum256(replayRaw)
	replayPayloadSHA := hex.EncodeToString(replayDigest[:])
	replayRegistrationID := uuid.NewString()
	replayTicketID := uuid.NewString()
	replayToken := gateway.deriveTicketToken(replayTicketID, replayPayloadSHA)
	replayTokenDigest := sha256.Sum256([]byte(replayToken))
	sourceDigest := sha256.Sum256([]byte(replayInput.SourceURL))
	past := time.Now().UTC().Add(-time.Minute).Truncate(time.Microsecond)
	replayTicket := model.PlatformDownloadEdgeTicket{
		ID: replayTicketID, TokenSHA256: hex.EncodeToString(replayTokenDigest[:]),
		RegistrationRequestID: replayRegistrationID, RegistrationPayloadSHA256: replayPayloadSHA,
		DownloadRecordID: replayInput.DownloadRecordID, CompanyID: replayInput.CompanyID,
		TaskID: replayInput.TaskID, AssetID: replayInput.AssetID,
		ExpectedSizeBytes: replayInput.ExpectedSizeBytes, ArtifactSHA256: replayInput.ArtifactSHA256,
		OBSBucket: replayInput.OBSBinding.Bucket, OBSObjectKey: replayInput.OBSBinding.ObjectKey,
		OBSVersionID: *replayInput.OBSBinding.VersionID, IssuanceRequestID: replayInput.IssuanceRequestID,
		TransferReference: replayInput.TransferReference, SourceURLSHA256: hex.EncodeToString(sourceDigest[:]),
		SourceExpiresAt: past, SourceURLCiphertext: []byte("ciphertext-at-least-sixteen-bytes"),
		SourceURLNonce: []byte("123456789012"), State: model.PlatformDownloadEdgeTicketExpired,
		IssuedAt: past.Add(-time.Minute), ExpiresAt: past, CreatedAt: past.Add(-time.Minute), UpdatedAt: past,
	}
	require.NoError(t, model.DB.Create(&replayTicket).Error)
	response = serve(replayRaw, replayRegistrationID)
	assert.Equal(t, http.StatusGone, response.Code, response.Body.String())
	assert.Empty(t, response.Header().Get("Location"))
	assert.NotContains(t, response.Body.String(), replayToken)
	var terminal PlatformDownloadTicketCommittedExpiredResponse
	require.NoError(t, json.Unmarshal(response.Body.Bytes(), &terminal))
	assert.Equal(t, "committed_expired", terminal.Outcome)
	assert.Equal(t, replayRegistrationID, terminal.RegistrationRequestID)
	assert.Equal(t, replayPayloadSHA, terminal.RegistrationPayloadSHA256)
	assert.Equal(t, replayTicketID, terminal.GatewayTicketID)
	assert.Equal(t, replayInput.DownloadRecordID, terminal.DownloadRecordID)
	assert.Equal(t, replayInput.IssuanceRequestID, terminal.IssuanceRequestID)
}

func TestPlatformDownloadEdgeRejectsRangeRedirectTruncationAndDigestMismatch(t *testing.T) {
	tests := []struct {
		name          string
		requestRange  bool
		upstream      func(http.ResponseWriter, *http.Request)
		expectedState string
		expectedCalls int32
	}{
		{name: "range rejected before claim", requestRange: true, expectedState: model.PlatformDownloadEdgeTicketPending, expectedCalls: 0,
			upstream: func(writer http.ResponseWriter, _ *http.Request) { _, _ = writer.Write([]byte("valid-body")) }},
		{name: "redirect is not followed", expectedState: model.PlatformDownloadEdgeTicketFailed, expectedCalls: 1,
			upstream: func(writer http.ResponseWriter, request *http.Request) {
				http.Redirect(writer, request, "/other", http.StatusFound)
			}},
		{name: "truncated body", expectedState: model.PlatformDownloadEdgeTicketFailed, expectedCalls: 1,
			upstream: func(writer http.ResponseWriter, _ *http.Request) { _, _ = writer.Write([]byte("short")) }},
		{name: "digest mismatch", expectedState: model.PlatformDownloadEdgeTicketFailed, expectedCalls: 1,
			upstream: func(writer http.ResponseWriter, _ *http.Request) { _, _ = writer.Write([]byte("wrong-body")) }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			preparePlatformDownloadEdgeTest(t)
			artifact := []byte("valid-body")
			var calls atomic.Int32
			upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
				calls.Add(1)
				test.upstream(writer, request)
			}))
			defer upstream.Close()
			platform := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				t.Fatal("completion callback must not be sent for a failed transfer")
			}))
			defer platform.Close()
			config := testPlatformDownloadEdgeConfig(t, upstream.URL, platform.URL+"/internal/artifact-download-completions/edge-gateway")
			gateway, err := NewPlatformDownloadEdgeGateway(config)
			require.NoError(t, err)
			raw, input := testPlatformDownloadEdgeRegistrationPayload(
				t, upstream.URL+"/artifact-bucket/results/video.mp4?signature=hidden", artifact,
			)
			registration := registerPlatformDownloadEdgeTicket(t, gateway, config, input.SourceURL, raw, uuid.NewString())
			ticketURL, err := url.Parse(registration.TicketURL)
			require.NoError(t, err)
			request := httptest.NewRequest(http.MethodGet, ticketURL.Path, nil)
			if test.requestRange {
				request.Header.Set("Range", "bytes=0-3")
			}
			response := httptest.NewRecorder()
			gateway.Handler().ServeHTTP(response, request)
			if test.requestRange {
				assert.Equal(t, http.StatusBadRequest, response.Code)
			}
			assert.Equal(t, test.expectedCalls, calls.Load())
			var ticket model.PlatformDownloadEdgeTicket
			require.NoError(t, model.DB.First(&ticket).Error)
			assert.Equal(t, test.expectedState, ticket.State)
			var events int64
			require.NoError(t, model.DB.Model(&model.PlatformDownloadCompletionEvent{}).Count(&events).Error)
			assert.Zero(t, events)
			var deliveries int64
			require.NoError(t, model.DB.Model(&model.PlatformRelayExternalDelivery{}).
				Where("event_kind = ?", model.PlatformRelayDeliveryKindDownloadCompletion).Count(&deliveries).Error)
			assert.Zero(t, deliveries)
		})
	}
}

func TestPlatformDownloadEdgeClaimIsOneTimeAndStaleFenceCannotCreateEvidence(t *testing.T) {
	preparePlatformDownloadEdgeTest(t)
	artifact := []byte("valid-body")
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) { _, _ = writer.Write(artifact) }))
	defer upstream.Close()
	platform := httptest.NewServer(http.NotFoundHandler())
	defer platform.Close()
	config := testPlatformDownloadEdgeConfig(t, upstream.URL, platform.URL+"/internal/artifact-download-completions/edge-gateway")
	gateway, err := NewPlatformDownloadEdgeGateway(config)
	require.NoError(t, err)
	raw, input := testPlatformDownloadEdgeRegistrationPayload(
		t, upstream.URL+"/artifact-bucket/results/video.mp4?signature=hidden", artifact,
	)
	registration := registerPlatformDownloadEdgeTicket(t, gateway, config, input.SourceURL, raw, uuid.NewString())
	parsed, err := url.Parse(registration.TicketURL)
	require.NoError(t, err)
	token := strings.TrimPrefix(parsed.Path, platformDownloadEdgePublicPrefix)
	tokenDigest := sha256.Sum256([]byte(token))
	claim, err := model.ClaimPlatformDownloadEdgeTicket(hex.EncodeToString(tokenDigest[:]), time.Second)
	require.NoError(t, err)
	_, err = model.ClaimPlatformDownloadEdgeTicket(hex.EncodeToString(tokenDigest[:]), time.Second)
	assert.ErrorIs(t, err, model.ErrPlatformDownloadEdgeTicketUnavailable)
	require.NoError(t, model.DB.Model(&model.PlatformDownloadEdgeTicket{}).
		Where("id = ?", claim.Ticket.ID).Update("claim_expires_at", time.Unix(1, 0).UTC()).Error)
	_, err = model.FinalizePlatformDownloadEdgeTransfer(claim.Ticket.ID, claim.Token, uuid.NewString(), 3)
	assert.ErrorIs(t, err, model.ErrPlatformDownloadEdgeClaimLost)
	var events int64
	require.NoError(t, model.DB.Model(&model.PlatformDownloadCompletionEvent{}).Count(&events).Error)
	assert.Zero(t, events)
}

func TestPlatformDownloadEdgeVerifiedWriterDetectsDownstreamFailure(t *testing.T) {
	expected := errors.New("client disconnected")
	destination := &failingPlatformDownloadEdgeWriter{remaining: 4, failure: expected}
	verified := &platformDownloadEdgeVerifiedWriter{destination: destination, digest: sha256.New()}
	written, err := verified.Write([]byte("abcdefgh"))
	assert.Equal(t, 4, written)
	assert.ErrorIs(t, err, expected)
	assert.Equal(t, int64(4), verified.bytes)
	digest := sha256.Sum256([]byte("abcd"))
	assert.Equal(t, hex.EncodeToString(digest[:]), hex.EncodeToString(verified.digest.Sum(nil)))
}

type failingPlatformDownloadEdgeWriter struct {
	remaining int
	failure   error
}

func (writer *failingPlatformDownloadEdgeWriter) Write(payload []byte) (int, error) {
	if writer.remaining <= 0 {
		return 0, writer.failure
	}
	written := len(payload)
	if written > writer.remaining {
		written = writer.remaining
	}
	writer.remaining -= written
	return written, writer.failure
}

type platformDownloadCompletionPayloadForTest struct {
	DownloadRecordID         string    `json:"download_record_id"`
	BytesSent                int64     `json:"bytes_sent"`
	CompletedAt              time.Time `json:"completed_at"`
	ArtifactSHA256           string    `json:"artifact_sha256"`
	ExpectedSizeBytes        int64     `json:"expected_size_bytes"`
	GatewayRequestID         string    `json:"gateway_request_id"`
	GatewayTransferReference string    `json:"gateway_transfer_reference"`
}

func strconvFormatInt(value int64) string { return fmt.Sprintf("%d", value) }

func strconvParseInt(value string) (int64, error) {
	var parsed int64
	_, err := fmt.Sscanf(value, "%d", &parsed)
	return parsed, err
}

func verifyRegistrationSignatureForTest(secret string, request *http.Request, raw []byte) bool {
	expected, err := SignPlatformDownloadEdgeRegistration(
		secret,
		request.Header.Get("X-Download-Gateway-Timestamp"),
		request.Header.Get("X-Download-Gateway-Request-ID"),
		raw,
	)
	return err == nil && hmac.Equal([]byte(expected), []byte(request.Header.Get("X-Download-Gateway-Signature")))
}
