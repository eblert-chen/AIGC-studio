package service

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const (
	PlatformDownloadEdgeRegistrationPath                    = "/internal/v1/download-tickets"
	platformDownloadEdgeProofPrefix                         = "/internal/v1/download-completions/"
	platformDownloadEdgePublicPrefix                        = "/downloads/"
	platformDownloadEdgeRegistrationDomain                  = "download-edge-registration.v1\n"
	platformDownloadEdgeCompletionDomain                    = "download-completion.v1\n"
	platformDownloadEdgeProofDomain                         = "relay-download-completion-proof.v1\n"
	platformDownloadEdgeUserAgent                           = "ai-video-relay-download-edge/1.0"
	platformDownloadEdgeMaxRegistrationBody                 = 64 * 1024
	platformDownloadEdgeMaxPlatformBody                     = 64 * 1024
	PlatformDownloadEdgeRuntimeSecretsKind                  = "relay_download_edge_runtime_secrets"
	PlatformDownloadEdgeRuntimeSecretsSchemaVersion         = 1
	platformDownloadEdgeRuntimeSecretsFileEnvironment       = "RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE"
	platformDownloadEdgeRuntimeSecretsFileMaxBytes    int64 = 64 * 1024

	// These values exist only so the opt-in local Compose profile can boot. A
	// production process must reject them even though their lengths are valid.
	platformDownloadEdgeDevelopmentTicketKey = "ticket-token-key-32-byte-value01"
	platformDownloadEdgeDevelopmentSourceKey = "source-encryption-key-32-byte-02"
	platformDownloadEdgeDevelopmentProofSeed = "proof-private-seed-32-bytes-key3"
)

var (
	platformDownloadEdgeBucketPattern                = regexp.MustCompile(`^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$`)
	platformDownloadEdgeDigestPattern                = regexp.MustCompile(`^[0-9a-f]{64}$`)
	readPlatformDownloadEdgeRuntimeSecretFile        = common.ReadProtectedSecretFile
	verifyPlatformDownloadEdgeSecretIsolationReceipt = VerifyPlatformRelaySecretIsolationReceipt
	platformDownloadEdgeForbiddenSecretEnvironments  = []string{
		"SQL_DSN",
		"SQL_DSN_FILE",
		"LOG_SQL_DSN",
		"LOG_SQL_DSN_FILE",
		"RELAY_DOWNLOAD_EDGE_SQL_DSN",
		"INTERNAL_SERVICE_TOKEN",
		"DOWNLOAD_EDGE_COMPLETION_SERVICE_TOKEN",
		"RELAY_API_RUNTIME_SECRETS_FILE",
		"RELAY_SERVICE_PRINCIPALS_FILE",
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE",
		"RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON",
		"RELAY_PROVISION_ROOT_PASSWORD_FILE",
		"RELAY_MIGRATION_DATABASE_PASSWORD_FILE",
		"RELAY_RUNTIME_DATABASE_PASSWORD_FILE",
		"RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE",
		"REDIS_CONN_STRING",
		"SESSION_SECRET",
		"CRYPTO_SECRET",
		"RELAY_COMPAT_CLIENT_CREDENTIALS_JSON",
		"RELAY_COMPAT_OPERATIONS_CREDENTIALS_JSON",
		"RELAY_COMPAT_RECONCILIATION_APPROVAL_KEYS_JSON",
		"RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEY",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_PRIVATE_KEYS_JSON",
		"RELAY_COMPAT_ROUTE_ACCEPTANCE_SIGNING_KEY",
		"RELAY_ARTIFACT_SIGNING_SECRET",
		"HUAWEI_OBS_ACCESS_KEY_ID",
		"HUAWEI_OBS_SECRET_ACCESS_KEY",
		"HUAWEI_OBS_SECURITY_TOKEN",
		"RELAY_PROVIDER_ALERT_SIGNING_SECRET",
		"RELAY_PLATFORM_INTERNAL_SERVICE_TOKEN",
		"RELAY_PLATFORM_CHANNEL_COST_SIGNING_SECRET",
		"RELAY_TELEMETRY_SIGNING_SECRET",
		"DOWNLOAD_GATEWAY_SERVICE_TOKEN",
		"DOWNLOAD_GATEWAY_REGISTRATION_SIGNING_SECRET",
		"DOWNLOAD_COMPLETION_EDGE_GATEWAY_SIGNING_SECRET",
		"RELAY_DOWNLOAD_EDGE_REGISTRATION_TOKEN",
		"RELAY_DOWNLOAD_EDGE_REGISTRATION_SIGNING_SECRET",
		"RELAY_DOWNLOAD_EDGE_TICKET_TOKEN_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_SOURCE_ENCRYPTION_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_PLATFORM_INTERNAL_TOKEN",
		"RELAY_DOWNLOAD_EDGE_COMPLETION_SIGNING_SECRET",
		"RELAY_DOWNLOAD_EDGE_PROOF_PRIVATE_KEY_BASE64",
		"RELAY_DOWNLOAD_EDGE_PROOF_READ_TOKEN",
	}
)

type PlatformDownloadEdgeOBSBinding struct {
	Bucket    string  `json:"bucket"`
	ObjectKey string  `json:"object_key"`
	VersionID *string `json:"version_id"`
}

type PlatformDownloadTicketRegistrationRequest struct {
	APIVersion        string                         `json:"api_version"`
	SchemaVersion     int                            `json:"schema_version"`
	DownloadRecordID  string                         `json:"download_record_id"`
	CompanyID         string                         `json:"company_id"`
	TaskID            string                         `json:"task_id"`
	AssetID           string                         `json:"asset_id"`
	ExpectedSizeBytes int64                          `json:"expected_size_bytes"`
	ArtifactSHA256    string                         `json:"artifact_sha256"`
	SourceURL         string                         `json:"source_url"`
	SourceExpiresAt   string                         `json:"source_expires_at"`
	OBSBinding        PlatformDownloadEdgeOBSBinding `json:"obs_binding"`
	IssuanceRequestID string                         `json:"issuance_request_id"`
	TransferReference string                         `json:"transfer_reference"`
}

type PlatformDownloadTicketRegistrationResponse struct {
	APIVersion        string    `json:"api_version"`
	SchemaVersion     int       `json:"schema_version"`
	DownloadRecordID  string    `json:"download_record_id"`
	CompanyID         string    `json:"company_id"`
	TaskID            string    `json:"task_id"`
	AssetID           string    `json:"asset_id"`
	IssuanceRequestID string    `json:"issuance_request_id"`
	TransferReference string    `json:"transfer_reference"`
	GatewayTicketID   string    `json:"gateway_ticket_id"`
	OneTime           bool      `json:"one_time"`
	TicketURL         string    `json:"ticket_url"`
	IssuedAt          time.Time `json:"issued_at"`
	ExpiresAt         time.Time `json:"expires_at"`
	ExpiresSeconds    int       `json:"expires_seconds"`
}

type PlatformDownloadTicketCommittedExpiredResponse struct {
	APIVersion                string    `json:"api_version"`
	SchemaVersion             int       `json:"schema_version"`
	Outcome                   string    `json:"outcome"`
	RegistrationRequestID     string    `json:"registration_request_id"`
	RegistrationPayloadSHA256 string    `json:"registration_payload_sha256"`
	GatewayTicketID           string    `json:"gateway_ticket_id"`
	DownloadRecordID          string    `json:"download_record_id"`
	CompanyID                 string    `json:"company_id"`
	TaskID                    string    `json:"task_id"`
	AssetID                   string    `json:"asset_id"`
	IssuanceRequestID         string    `json:"issuance_request_id"`
	TransferReference         string    `json:"transfer_reference"`
	IssuedAt                  time.Time `json:"issued_at"`
	ExpiresAt                 time.Time `json:"expires_at"`
}

// PlatformDownloadCompletionProofPayload is serialized as a struct (not a
// map), persisted byte-for-byte, and signed with the domain prefix. Field order
// is part of schema v1 and must not be changed in place.
type PlatformDownloadCompletionProofPayload struct {
	SchemaVersion          int    `json:"schema_version"`
	Kind                   string `json:"kind"`
	CompletionID           string `json:"completion_id"`
	DownloadRecordID       string `json:"download_record_id"`
	CompanyID              string `json:"company_id"`
	TaskID                 string `json:"task_id"`
	AssetID                string `json:"asset_id"`
	Source                 string `json:"source"`
	SignedEventID          string `json:"signed_event_id"`
	SignedPayloadSHA256    string `json:"signed_payload_sha256"`
	IssuanceRequestID      string `json:"issuance_request_id"`
	TransferReference      string `json:"transfer_reference"`
	GatewayRequestID       string `json:"gateway_request_id"`
	OBSBucket              string `json:"obs_bucket"`
	OBSObjectKey           string `json:"obs_object_key"`
	OBSVersionID           string `json:"obs_version_id,omitempty"`
	HTTPStatus             int    `json:"http_status"`
	TransferScope          string `json:"transfer_scope"`
	BytesSent              int64  `json:"bytes_sent"`
	ExpectedSizeBytes      int64  `json:"expected_size_bytes"`
	ArtifactSHA256         string `json:"artifact_sha256"`
	CompletedAtUTC         string `json:"completed_at_utc"`
	PlatformDeliveredAtUTC string `json:"platform_delivered_at_utc"`
	ProducerSubject        string `json:"producer_subject"`
	ProducedAtUTC          string `json:"produced_at_utc"`
	Nonce                  string `json:"nonce"`
}

type PlatformDownloadCompletionProofSignature struct {
	SchemaVersion   int    `json:"schema_version"`
	Algorithm       string `json:"algorithm"`
	KeyID           string `json:"key_id"`
	PayloadSHA256   string `json:"payload_sha256"`
	SignatureBase64 string `json:"signature_base64"`
}

type PlatformDownloadEdgeConfig struct {
	Production bool
	// Protected extends the production transport and secret policy to secure
	// staging and any deployment that requires database-role attestation.
	// Production remains separate so callers can report the actual environment.
	Protected                   bool
	ListenAddress               string
	PublicBaseURL               string
	AllowedOBSHosts             []string
	RegistrationToken           string
	RegistrationSigningSecret   string
	TicketTokenKey              []byte
	SourceEncryptionKey         []byte
	PlatformCompletionURL       string
	PlatformEdgeCompletionToken string
	CompletionSigningSecret     string
	ProofSigningPrivateKey      ed25519.PrivateKey
	ProofKeyID                  string
	ProofReadToken              string
	ProducerSubject             string
	TicketTTL                   time.Duration
	SourceExpirySafetyMargin    time.Duration
	RegistrationMaxAge          time.Duration
	TransferTimeout             time.Duration
	TransferCompletionMargin    time.Duration
	TicketClaimLease            time.Duration
	DeliveryClaimLease          time.Duration
	DeliveryCommitMargin        time.Duration
	DeliveryPollInterval        time.Duration
	DeliveryMaxAttempts         int
	MaxArtifactBytes            int64
}

type PlatformDownloadEdgeRuntimeSecretsFile struct {
	Kind                        string `json:"kind"`
	SchemaVersion               int    `json:"schema_version"`
	RegistrationToken           string `json:"registration_token"`
	RegistrationSigningSecret   string `json:"registration_signing_secret"`
	TicketTokenKeyBase64        string `json:"ticket_token_key_base64"`
	SourceEncryptionKeyBase64   string `json:"source_encryption_key_base64"`
	PlatformEdgeCompletionToken string `json:"platform_edge_completion_token"`
	CompletionSigningSecret     string `json:"completion_signing_secret"`
	ProofSigningSeedBase64      string `json:"proof_signing_seed_base64"`
	ProofReadToken              string `json:"proof_read_token"`
}

func (config PlatformDownloadEdgeConfig) ProtectedSecurityRequired() bool {
	return config.Production || config.Protected
}

func (config PlatformDownloadEdgeConfig) Validate() error {
	protected := config.ProtectedSecurityRequired()
	if strings.TrimSpace(config.ListenAddress) == "" {
		return fmt.Errorf("download edge listen address is required")
	}
	publicURL, err := url.Parse(config.PublicBaseURL)
	if err != nil || publicURL.User != nil || publicURL.RawQuery != "" || publicURL.Fragment != "" || publicURL.Host == "" ||
		(publicURL.Path != "" && publicURL.Path != "/") || !platformDownloadEdgeSchemeAllowed(publicURL, protected) {
		return fmt.Errorf("download edge public base URL is invalid")
	}
	completionURL, err := url.Parse(config.PlatformCompletionURL)
	if err != nil || completionURL.User != nil || completionURL.RawQuery != "" || completionURL.Fragment != "" || completionURL.Host == "" ||
		completionURL.Path != "/internal/artifact-download-completions/edge-gateway" || !platformDownloadEdgeSchemeAllowed(completionURL, protected) {
		return fmt.Errorf("download edge Platform completion URL is invalid")
	}
	if len(config.AllowedOBSHosts) == 0 {
		return fmt.Errorf("download edge OBS host allowlist is required")
	}
	seenHosts := map[string]struct{}{}
	for _, host := range config.AllowedOBSHosts {
		if host == "" || host != strings.ToLower(strings.TrimSpace(host)) || strings.ContainsAny(host, "/?#@") {
			return fmt.Errorf("download edge OBS host allowlist is invalid")
		}
		if protected && !platformDownloadEdgeOfficialOBSHost(host) {
			return fmt.Errorf("protected download edge OBS host must be an official Huawei OBS host")
		}
		if _, exists := seenHosts[host]; exists {
			return fmt.Errorf("download edge OBS host allowlist contains duplicates")
		}
		seenHosts[host] = struct{}{}
	}
	if len(config.SourceEncryptionKey) != 32 || len(config.TicketTokenKey) != 32 || len(config.ProofSigningPrivateKey) != ed25519.PrivateKeySize {
		return fmt.Errorf("download edge cryptographic keys are invalid")
	}
	secretValues := [][]byte{
		[]byte(config.RegistrationToken), []byte(config.RegistrationSigningSecret), config.TicketTokenKey,
		config.SourceEncryptionKey, []byte(config.PlatformEdgeCompletionToken),
		[]byte(config.CompletionSigningSecret), config.ProofSigningPrivateKey.Seed(), []byte(config.ProofReadToken),
	}
	for _, value := range secretValues {
		if len(value) < 32 || (protected && (platformRelaySecretIsPlaceholder(string(value)) || platformDownloadEdgeDevelopmentCredential(value))) {
			return fmt.Errorf("download edge credentials must be non-placeholder values of at least 32 bytes")
		}
	}
	for left := range secretValues {
		for right := left + 1; right < len(secretValues); right++ {
			if hmac.Equal(secretValues[left], secretValues[right]) {
				return fmt.Errorf("download edge credentials must be independent")
			}
		}
	}
	if config.ProofKeyID == "" || len(config.ProofKeyID) > 120 || strings.TrimSpace(config.ProofKeyID) != config.ProofKeyID ||
		config.ProducerSubject == "" || len(config.ProducerSubject) > 160 || strings.TrimSpace(config.ProducerSubject) != config.ProducerSubject {
		return fmt.Errorf("download edge proof identity is invalid")
	}
	if config.TicketTTL < 30*time.Second || config.TicketTTL > time.Hour ||
		config.SourceExpirySafetyMargin < 30*time.Second || config.SourceExpirySafetyMargin > 15*time.Minute ||
		config.RegistrationMaxAge < 30*time.Second || config.RegistrationMaxAge > 15*time.Minute ||
		config.TransferTimeout < time.Second || config.TransferTimeout > 30*time.Minute ||
		config.TransferCompletionMargin < time.Second || config.TransferCompletionMargin > 5*time.Minute ||
		config.TicketClaimLease < config.TransferTimeout+config.TransferCompletionMargin || config.TicketClaimLease > 35*time.Minute ||
		config.DeliveryCommitMargin < time.Second || config.DeliveryCommitMargin > 5*time.Minute ||
		config.DeliveryClaimLease < 15*time.Second+config.DeliveryCommitMargin || config.DeliveryClaimLease > 10*time.Minute ||
		config.DeliveryPollInterval < 100*time.Millisecond || config.DeliveryPollInterval > time.Minute ||
		config.DeliveryMaxAttempts < 1 || config.DeliveryMaxAttempts > 100 ||
		config.MaxArtifactBytes < 1 || config.MaxArtifactBytes > 5*1024*1024*1024 {
		return fmt.Errorf("download edge timing or size limits are invalid")
	}
	return nil
}

func platformDownloadEdgeDevelopmentCredential(value []byte) bool {
	for _, known := range [][]byte{
		[]byte(platformDownloadEdgeDevelopmentTicketKey),
		[]byte(platformDownloadEdgeDevelopmentSourceKey),
		[]byte(platformDownloadEdgeDevelopmentProofSeed),
		[]byte("local-download-gateway-service-token-change-me-01"),
		[]byte("local-download-gateway-registration-signing-change-me-02"),
		[]byte("local-internal-service-token-change-me-04"),
		[]byte("local-edge-download-signing-secret-change-me"),
		[]byte("local-download-proof-read-token-change-me-03"),
	} {
		if hmac.Equal(value, known) {
			return true
		}
	}
	return false
}

type PlatformDownloadEdgeGateway struct {
	config         PlatformDownloadEdgeConfig
	handler        http.Handler
	upstreamClient *http.Client
	platformClient *http.Client
	clock          func() time.Time
}

func NewPlatformDownloadEdgeGateway(config PlatformDownloadEdgeConfig) (*PlatformDownloadEdgeGateway, error) {
	if err := config.Validate(); err != nil {
		return nil, err
	}
	gateway := &PlatformDownloadEdgeGateway{
		config:         config,
		upstreamClient: newPlatformDownloadEdgeHTTPClient(config.TransferTimeout),
		platformClient: newPlatformDownloadEdgeHTTPClient(15 * time.Second),
		clock:          func() time.Time { return time.Now().UTC() },
	}
	mux := http.NewServeMux()
	mux.HandleFunc(PlatformDownloadEdgeRegistrationPath, gateway.handleRegistration)
	mux.HandleFunc(platformDownloadEdgePublicPrefix, gateway.handleDownload)
	mux.HandleFunc(platformDownloadEdgeProofPrefix, gateway.handleProofRead)
	mux.HandleFunc("/health/live", gateway.handleLive)
	mux.HandleFunc("/health/ready", gateway.handleReady)
	gateway.handler = securityHeaders(mux)
	return gateway, nil
}

func (gateway *PlatformDownloadEdgeGateway) Handler() http.Handler { return gateway.handler }

func (gateway *PlatformDownloadEdgeGateway) handleRegistration(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		writer.Header().Set("Allow", http.MethodPost)
		writePlatformDownloadEdgeError(writer, http.StatusMethodNotAllowed, "method_not_allowed")
		return
	}
	if !strings.HasPrefix(strings.ToLower(request.Header.Get("Content-Type")), "application/json") {
		writePlatformDownloadEdgeError(writer, http.StatusUnsupportedMediaType, "json_required")
		return
	}
	if request.ContentLength > platformDownloadEdgeMaxRegistrationBody {
		writePlatformDownloadEdgeError(writer, http.StatusRequestEntityTooLarge, "request_too_large")
		return
	}
	raw, err := io.ReadAll(io.LimitReader(request.Body, platformDownloadEdgeMaxRegistrationBody+1))
	if err != nil || len(raw) == 0 || len(raw) > platformDownloadEdgeMaxRegistrationBody {
		writePlatformDownloadEdgeError(writer, http.StatusBadRequest, "invalid_request")
		return
	}
	registrationRequestID, err := gateway.verifyRegistration(request.Header, raw)
	if err != nil {
		writePlatformDownloadEdgeError(writer, http.StatusUnauthorized, "registration_unauthorized")
		return
	}
	var input PlatformDownloadTicketRegistrationRequest
	if err := decodePlatformDownloadEdgeStrictJSON(raw, &input); err != nil {
		writePlatformDownloadEdgeError(writer, http.StatusUnprocessableEntity, "registration_invalid")
		return
	}
	payloadDigest := sha256.Sum256(raw)
	payloadSHA256 := hex.EncodeToString(payloadDigest[:])
	existing, expired, replayErr := model.GetPlatformDownloadEdgeTicketForReplay(registrationRequestID, payloadSHA256)
	if replayErr == nil {
		if expired {
			gateway.writeCommittedExpiredRegistration(writer, *existing)
		} else {
			gateway.writeRegistrationTicket(writer, *existing)
		}
		return
	}
	if errors.Is(replayErr, model.ErrPlatformDownloadEdgeTicketCollision) {
		writePlatformDownloadEdgeError(writer, http.StatusConflict, "registration_conflict")
		return
	}
	if !errors.Is(replayErr, gorm.ErrRecordNotFound) {
		writePlatformDownloadEdgeError(writer, http.StatusInternalServerError, "registration_failed")
		return
	}
	if gateway.validateRegistrationInput(&input) != nil {
		writePlatformDownloadEdgeError(writer, http.StatusUnprocessableEntity, "registration_invalid")
		return
	}
	sourceExpiresAt, _ := parsePlatformDownloadEdgeSourceExpiresAt(input.SourceExpiresAt)
	sourceDigest := sha256.Sum256([]byte(input.SourceURL))
	ticketID := uuid.NewString()
	token := gateway.deriveTicketToken(ticketID, payloadSHA256)
	tokenDigest := sha256.Sum256([]byte(token))
	ciphertext, nonce, err := encryptPlatformDownloadEdgeSourceURL(
		gateway.config.SourceEncryptionKey, ticketID, input.DownloadRecordID,
		hex.EncodeToString(sourceDigest[:]), input.SourceURL,
	)
	if err != nil {
		writePlatformDownloadEdgeError(writer, http.StatusInternalServerError, "registration_failed")
		return
	}
	versionID := ""
	if input.OBSBinding.VersionID != nil {
		versionID = *input.OBSBinding.VersionID
	}
	ticket := model.PlatformDownloadEdgeTicket{
		ID: ticketID, TokenSHA256: hex.EncodeToString(tokenDigest[:]),
		RegistrationRequestID:     registrationRequestID,
		RegistrationPayloadSHA256: payloadSHA256,
		DownloadRecordID:          input.DownloadRecordID, CompanyID: input.CompanyID,
		TaskID: input.TaskID, AssetID: input.AssetID, ExpectedSizeBytes: input.ExpectedSizeBytes,
		ArtifactSHA256: input.ArtifactSHA256, OBSBucket: input.OBSBinding.Bucket,
		OBSObjectKey: input.OBSBinding.ObjectKey, OBSVersionID: versionID,
		IssuanceRequestID: input.IssuanceRequestID, TransferReference: input.TransferReference,
		SourceURLSHA256: hex.EncodeToString(sourceDigest[:]), SourceExpiresAt: sourceExpiresAt,
		SourceURLCiphertext: ciphertext,
		SourceURLNonce:      nonce,
	}
	_, err = model.CreatePlatformDownloadEdgeTicket(
		&ticket, gateway.config.TicketTTL, gateway.config.SourceExpirySafetyMargin,
	)
	if err != nil {
		status := http.StatusInternalServerError
		code := "registration_failed"
		if errors.Is(err, model.ErrPlatformDownloadEdgeTicketCollision) {
			status, code = http.StatusConflict, "registration_conflict"
		} else if errors.Is(err, model.ErrPlatformDownloadEdgeSourceLifetime) {
			status, code = http.StatusUnprocessableEntity, "registration_source_lifetime_insufficient"
		}
		writePlatformDownloadEdgeError(writer, status, code)
		return
	}
	gateway.writeRegistrationTicket(writer, ticket)
}

func (gateway *PlatformDownloadEdgeGateway) writeRegistrationTicket(writer http.ResponseWriter, ticket model.PlatformDownloadEdgeTicket) {
	// A duplicate request loads the original ticket. Re-derive its token from
	// the stable id and payload digest; no raw bearer token is persisted.
	token := gateway.deriveTicketToken(ticket.ID, ticket.RegistrationPayloadSHA256)
	response := PlatformDownloadTicketRegistrationResponse{
		APIVersion: "v1", SchemaVersion: 1,
		DownloadRecordID: ticket.DownloadRecordID, CompanyID: ticket.CompanyID,
		TaskID: ticket.TaskID, AssetID: ticket.AssetID,
		IssuanceRequestID: ticket.IssuanceRequestID, TransferReference: ticket.TransferReference,
		GatewayTicketID: ticket.ID, OneTime: true,
		TicketURL: strings.TrimRight(gateway.config.PublicBaseURL, "/") + platformDownloadEdgePublicPrefix + token,
		IssuedAt:  ticket.IssuedAt.UTC(), ExpiresAt: ticket.ExpiresAt.UTC(),
		ExpiresSeconds: int(ticket.ExpiresAt.Sub(ticket.IssuedAt) / time.Second),
	}
	writer.Header().Set("Cache-Control", "no-store")
	writer.Header().Set("Location", response.TicketURL)
	writePlatformDownloadEdgeJSON(writer, http.StatusCreated, response)
}

func (gateway *PlatformDownloadEdgeGateway) writeCommittedExpiredRegistration(writer http.ResponseWriter, ticket model.PlatformDownloadEdgeTicket) {
	response := PlatformDownloadTicketCommittedExpiredResponse{
		APIVersion: "v1", SchemaVersion: 1, Outcome: "committed_expired",
		RegistrationRequestID:     ticket.RegistrationRequestID,
		RegistrationPayloadSHA256: ticket.RegistrationPayloadSHA256,
		GatewayTicketID:           ticket.ID, DownloadRecordID: ticket.DownloadRecordID,
		CompanyID: ticket.CompanyID, TaskID: ticket.TaskID, AssetID: ticket.AssetID,
		IssuanceRequestID: ticket.IssuanceRequestID, TransferReference: ticket.TransferReference,
		IssuedAt: ticket.IssuedAt.UTC(), ExpiresAt: ticket.ExpiresAt.UTC(),
	}
	writer.Header().Set("Cache-Control", "no-store")
	writePlatformDownloadEdgeJSON(writer, http.StatusGone, response)
}

func (gateway *PlatformDownloadEdgeGateway) handleDownload(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		writer.Header().Set("Allow", http.MethodGet)
		writePlatformDownloadEdgeError(writer, http.StatusMethodNotAllowed, "method_not_allowed")
		return
	}
	if request.URL.RawQuery != "" || request.Header.Get("Range") != "" || request.Header.Get("If-Range") != "" {
		writePlatformDownloadEdgeError(writer, http.StatusBadRequest, "full_body_only")
		return
	}
	token := strings.TrimPrefix(request.URL.Path, platformDownloadEdgePublicPrefix)
	if strings.Contains(token, "/") || !platformDownloadEdgeCanonicalToken(token) {
		writePlatformDownloadEdgeError(writer, http.StatusNotFound, "ticket_not_found")
		return
	}
	tokenDigest := sha256.Sum256([]byte(token))
	claim, err := model.ClaimPlatformDownloadEdgeTicket(hex.EncodeToString(tokenDigest[:]), gateway.config.TicketClaimLease)
	if err != nil {
		writePlatformDownloadEdgeError(writer, http.StatusNotFound, "ticket_not_found")
		return
	}
	fail := func(code string) {
		_, _ = model.FailPlatformDownloadEdgeTicket(claim.Ticket.ID, claim.Token, code)
	}
	sourceURL, err := decryptPlatformDownloadEdgeSourceURL(
		gateway.config.SourceEncryptionKey, claim.Ticket.ID, claim.Ticket.DownloadRecordID,
		claim.Ticket.SourceURLSHA256, claim.Ticket.SourceURLNonce, claim.Ticket.SourceURLCiphertext,
	)
	if err != nil {
		fail("source_decryption_failed")
		writePlatformDownloadEdgeError(writer, http.StatusBadGateway, "download_unavailable")
		return
	}
	if err := gateway.validateSourceURL(sourceURL, claim.Ticket.OBSBucket, claim.Ticket.OBSObjectKey); err != nil {
		fail("source_binding_invalid")
		writePlatformDownloadEdgeError(writer, http.StatusBadGateway, "download_unavailable")
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), gateway.config.TransferTimeout)
	defer cancel()
	upstreamRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, sourceURL, nil)
	if err != nil {
		fail("source_request_invalid")
		writePlatformDownloadEdgeError(writer, http.StatusBadGateway, "download_unavailable")
		return
	}
	upstreamRequest.Header.Set("Accept-Encoding", "identity")
	upstreamRequest.Header.Set("User-Agent", platformDownloadEdgeUserAgent)
	response, err := gateway.upstreamClient.Do(upstreamRequest)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		fail("source_transport_failed")
		writePlatformDownloadEdgeError(writer, http.StatusBadGateway, "download_unavailable")
		return
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK || response.Header.Get("Content-Range") != "" ||
		(response.ContentLength >= 0 && response.ContentLength != claim.Ticket.ExpectedSizeBytes) {
		fail("source_response_invalid")
		writePlatformDownloadEdgeError(writer, http.StatusBadGateway, "download_unavailable")
		return
	}
	writer.Header().Set("Content-Length", strconv.FormatInt(claim.Ticket.ExpectedSizeBytes, 10))
	writer.Header().Set("Content-Type", platformDownloadEdgeSafeContentType(response.Header.Get("Content-Type")))
	writer.Header().Set("Content-Disposition", "attachment")
	writer.Header().Set("Accept-Ranges", "none")
	writer.Header().Set("Cache-Control", "private, no-store")
	writer.Header().Set("X-Request-ID", claim.Ticket.GatewayRequestID)
	writer.Header().Set("X-Transfer-Reference", claim.Ticket.TransferReference)
	writer.WriteHeader(http.StatusOK)
	verified := &platformDownloadEdgeVerifiedWriter{destination: writer, digest: sha256.New()}
	written, copyErr := io.Copy(verified, io.LimitReader(response.Body, claim.Ticket.ExpectedSizeBytes))
	var extra [1]byte
	extraCount, tailErr := response.Body.Read(extra[:])
	if tailErr == nil && extraCount == 0 {
		tailErr = io.ErrNoProgress
	}
	if copyErr != nil || written != claim.Ticket.ExpectedSizeBytes || verified.bytes != claim.Ticket.ExpectedSizeBytes ||
		extraCount != 0 || !errors.Is(tailErr, io.EOF) || hex.EncodeToString(verified.digest.Sum(nil)) != claim.Ticket.ArtifactSHA256 {
		fail("transfer_integrity_failed")
		return
	}
	if _, err := model.FinalizePlatformDownloadEdgeTransfer(
		claim.Ticket.ID, claim.Token, uuid.NewString(), gateway.config.DeliveryMaxAttempts,
	); err != nil {
		fail("transfer_fence_lost")
		return
	}
}

func (gateway *PlatformDownloadEdgeGateway) handleProofRead(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		writer.Header().Set("Allow", http.MethodGet)
		writePlatformDownloadEdgeError(writer, http.StatusMethodNotAllowed, "method_not_allowed")
		return
	}
	if !platformDownloadEdgeSecretEqual(request.Header.Get("X-Download-Proof-Read-Token"), gateway.config.ProofReadToken) {
		writePlatformDownloadEdgeError(writer, http.StatusUnauthorized, "proof_unauthorized")
		return
	}
	remainder := strings.TrimPrefix(request.URL.Path, platformDownloadEdgeProofPrefix)
	parts := strings.Split(remainder, "/")
	if len(parts) < 2 || len(parts) > 3 || !platformDownloadEdgeCanonicalUUID(parts[0]) || parts[1] != "proof" || (len(parts) == 3 && parts[2] != "signature") {
		writePlatformDownloadEdgeError(writer, http.StatusNotFound, "proof_not_found")
		return
	}
	proof, err := model.GetPlatformDownloadCompletionProof(parts[0])
	if err != nil {
		writePlatformDownloadEdgeError(writer, http.StatusNotFound, "proof_not_found")
		return
	}
	writer.Header().Set("Cache-Control", "no-store")
	if len(parts) == 2 {
		writer.Header().Set("Content-Type", "application/json")
		writer.Header().Set("Content-SHA256", proof.PayloadSHA256)
		writer.Header().Set("ETag", `"sha256:`+proof.PayloadSHA256+`"`)
		writer.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(writer, proof.PayloadJSON)
		return
	}
	writePlatformDownloadEdgeJSON(writer, http.StatusOK, PlatformDownloadCompletionProofSignature{
		SchemaVersion: 1, Algorithm: "Ed25519", KeyID: proof.KeyID,
		PayloadSHA256: "sha256:" + proof.PayloadSHA256, SignatureBase64: proof.SignatureBase64,
	})
}

func (gateway *PlatformDownloadEdgeGateway) handleLive(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		writePlatformDownloadEdgeError(writer, http.StatusMethodNotAllowed, "method_not_allowed")
		return
	}
	writePlatformDownloadEdgeJSON(writer, http.StatusOK, map[string]any{"status": "ok"})
}

func (gateway *PlatformDownloadEdgeGateway) handleReady(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		writePlatformDownloadEdgeError(writer, http.StatusMethodNotAllowed, "method_not_allowed")
		return
	}
	if model.DB == nil {
		writePlatformDownloadEdgeError(writer, http.StatusServiceUnavailable, "database_unavailable")
		return
	}
	sqlDB, err := model.DB.DB()
	if err != nil || sqlDB.PingContext(request.Context()) != nil {
		writePlatformDownloadEdgeError(writer, http.StatusServiceUnavailable, "database_unavailable")
		return
	}
	var schemaStatus model.RelaySchemaStatus
	if gateway.config.ProtectedSecurityRequired() {
		schemaStatus, err = model.RequireRelaySchemaCurrent(model.DB)
	} else {
		schemaStatus, err = model.RequireRelaySchemaCompatible(model.DB)
	}
	if err != nil {
		writePlatformDownloadEdgeError(writer, http.StatusServiceUnavailable, "schema_unavailable")
		return
	}
	if gateway.config.ProtectedSecurityRequired() {
		if err := model.VerifyRelayDownloadEdgeDatabaseRole(model.DB, schemaStatus.CurrentVersion); err != nil {
			writePlatformDownloadEdgeError(writer, http.StatusServiceUnavailable, "database_role_unavailable")
			return
		}
	}
	counts, err := model.GetPlatformRelayDeliveryCounts(model.PlatformRelayDeliveryKindDownloadCompletion)
	if err != nil {
		writePlatformDownloadEdgeError(writer, http.StatusServiceUnavailable, "outbox_unavailable")
		return
	}
	writePlatformDownloadEdgeJSON(writer, http.StatusOK, map[string]any{
		"status": "ready", "outbox_pending": counts.Pending,
		"outbox_claimed": counts.Claimed, "outbox_dead_letter": counts.DeadLetter,
	})
}

func (gateway *PlatformDownloadEdgeGateway) verifyRegistration(headers http.Header, raw []byte) (string, error) {
	if !platformDownloadEdgeSecretEqual(headers.Get("X-Download-Gateway-Token"), gateway.config.RegistrationToken) {
		return "", errors.New("registration token")
	}
	timestampText := headers.Get("X-Download-Gateway-Timestamp")
	timestamp, err := strconv.ParseInt(timestampText, 10, 64)
	if err != nil || strconv.FormatInt(timestamp, 10) != timestampText ||
		gateway.clock().Sub(time.Unix(timestamp, 0).UTC()).Abs() > gateway.config.RegistrationMaxAge {
		return "", errors.New("registration timestamp")
	}
	requestID := headers.Get("X-Download-Gateway-Request-ID")
	if !platformDownloadEdgeCanonicalUUID(requestID) {
		return "", errors.New("registration request id")
	}
	supplied := headers.Get("X-Download-Gateway-Signature")
	if !strings.HasPrefix(supplied, "sha256=") || len(supplied) != 71 {
		return "", errors.New("registration signature")
	}
	provided, err := hex.DecodeString(strings.TrimPrefix(supplied, "sha256="))
	if err != nil {
		return "", errors.New("registration signature")
	}
	mac := hmac.New(sha256.New, []byte(gateway.config.RegistrationSigningSecret))
	_, _ = io.WriteString(mac, platformDownloadEdgeRegistrationDomain)
	_, _ = io.WriteString(mac, http.MethodPost+"\n"+PlatformDownloadEdgeRegistrationPath+"\n")
	_, _ = io.WriteString(mac, timestampText+"\n"+requestID+"\n")
	_, _ = mac.Write(raw)
	if !hmac.Equal(provided, mac.Sum(nil)) {
		return "", errors.New("registration signature")
	}
	return requestID, nil
}

func SignPlatformDownloadEdgeRegistration(secret, timestamp, requestID string, raw []byte) (string, error) {
	if len(secret) < 32 || !platformDownloadEdgeCanonicalUUID(requestID) {
		return "", fmt.Errorf("download edge registration signing input is invalid")
	}
	if parsed, err := strconv.ParseInt(timestamp, 10, 64); err != nil || strconv.FormatInt(parsed, 10) != timestamp {
		return "", fmt.Errorf("download edge registration timestamp is invalid")
	}
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = io.WriteString(mac, platformDownloadEdgeRegistrationDomain)
	_, _ = io.WriteString(mac, http.MethodPost+"\n"+PlatformDownloadEdgeRegistrationPath+"\n")
	_, _ = io.WriteString(mac, timestamp+"\n"+requestID+"\n")
	_, _ = mac.Write(raw)
	return "sha256=" + hex.EncodeToString(mac.Sum(nil)), nil
}

func (gateway *PlatformDownloadEdgeGateway) validateRegistrationInput(input *PlatformDownloadTicketRegistrationRequest) error {
	if input == nil || input.APIVersion != "v1" || input.SchemaVersion != 1 ||
		!platformDownloadEdgeCanonicalUUID(input.DownloadRecordID) || !platformDownloadEdgeCanonicalUUID(input.CompanyID) ||
		!platformDownloadEdgeCanonicalUUID(input.TaskID) || !platformDownloadEdgeCanonicalUUID(input.TransferReference) ||
		input.AssetID == "" || len(input.AssetID) > 160 || strings.TrimSpace(input.AssetID) != input.AssetID ||
		input.ExpectedSizeBytes <= 0 || input.ExpectedSizeBytes > gateway.config.MaxArtifactBytes ||
		!platformDownloadEdgeDigestPattern.MatchString(input.ArtifactSHA256) ||
		input.IssuanceRequestID == "" || len(input.IssuanceRequestID) > 160 || strings.TrimSpace(input.IssuanceRequestID) != input.IssuanceRequestID {
		return errors.New("registration identity")
	}
	if !platformDownloadEdgeBucketPattern.MatchString(input.OBSBinding.Bucket) ||
		input.OBSBinding.ObjectKey == "" || len(input.OBSBinding.ObjectKey) > 1024 || platformDownloadEdgeHasControl(input.OBSBinding.ObjectKey) {
		return errors.New("OBS binding")
	}
	if input.OBSBinding.VersionID != nil && (*input.OBSBinding.VersionID == "" || len(*input.OBSBinding.VersionID) > 256 || platformDownloadEdgeHasControl(*input.OBSBinding.VersionID)) {
		return errors.New("OBS version")
	}
	if _, err := parsePlatformDownloadEdgeSourceExpiresAt(input.SourceExpiresAt); err != nil {
		return err
	}
	return gateway.validateSourceURL(input.SourceURL, input.OBSBinding.Bucket, input.OBSBinding.ObjectKey)
}

func parsePlatformDownloadEdgeSourceExpiresAt(raw string) (time.Time, error) {
	if raw == "" || raw != strings.TrimSpace(raw) || !strings.HasSuffix(raw, "Z") || strings.ContainsAny(raw, "\r\n\x00") {
		return time.Time{}, errors.New("source expiry")
	}
	parsed, err := time.Parse(time.RFC3339Nano, raw)
	if err != nil {
		return time.Time{}, errors.New("source expiry")
	}
	_, offset := parsed.Zone()
	if offset != 0 {
		return time.Time{}, errors.New("source expiry")
	}
	return parsed.UTC().Truncate(time.Microsecond), nil
}

func (gateway *PlatformDownloadEdgeGateway) validateSourceURL(raw, bucket, objectKey string) error {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.User != nil || parsed.Host == "" || parsed.Fragment != "" || parsed.RawQuery == "" {
		if !(gateway.config.ProtectedSecurityRequired() == false && err == nil && parsed.Scheme == "http" && platformDownloadEdgeLoopback(parsed.Hostname())) {
			return errors.New("source URL")
		}
	}
	host := strings.ToLower(parsed.Host)
	allowed := false
	for _, candidate := range gateway.config.AllowedOBSHosts {
		if host == candidate {
			allowed = true
			break
		}
	}
	if !allowed {
		return errors.New("source host")
	}
	decodedPath, err := url.PathUnescape(strings.TrimPrefix(parsed.EscapedPath(), "/"))
	if err != nil || (decodedPath != objectKey && decodedPath != bucket+"/"+objectKey) {
		return errors.New("source object binding")
	}
	return nil
}

func (gateway *PlatformDownloadEdgeGateway) deriveTicketToken(ticketID, payloadSHA string) string {
	mac := hmac.New(sha256.New, gateway.config.TicketTokenKey)
	_, _ = io.WriteString(mac, "download-edge-ticket.v1\n"+ticketID+"\n"+payloadSHA)
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

type platformDownloadCompletionResponse struct {
	ID                   string            `json:"id"`
	DownloadRecordID     string            `json:"download_record_id"`
	ExternalEventID      string            `json:"external_event_id"`
	Source               string            `json:"source"`
	BytesSent            int64             `json:"bytes_sent"`
	CompletedAt          time.Time         `json:"completed_at"`
	VerificationVersion  int               `json:"verification_version"`
	ArtifactSHA256       string            `json:"artifact_sha256"`
	ExpectedSizeBytes    int64             `json:"expected_size_bytes"`
	HTTPStatus           int               `json:"http_status"`
	TransferScope        string            `json:"transfer_scope"`
	SourceEvidence       map[string]string `json:"source_evidence"`
	SignedEventID        string            `json:"signed_event_id"`
	SignedEventTimestamp time.Time         `json:"signed_event_timestamp"`
	SignedPayloadSHA256  string            `json:"signed_payload_sha256"`
	VerifiedAt           time.Time         `json:"verified_at"`
	CreatedAt            time.Time         `json:"created_at"`
}

func (gateway *PlatformDownloadEdgeGateway) RunDeliveryOnce(ctx context.Context) (bool, error) {
	claim, err := model.ClaimPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindDownloadCompletion, gateway.config.DeliveryClaimLease)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return true, gateway.deliverCompletionClaim(ctx, *claim)
}

func (gateway *PlatformDownloadEdgeGateway) deliverCompletionClaim(ctx context.Context, claim model.PlatformRelayDeliveryClaim) error {
	event, err := model.GetPlatformDownloadCompletionEvent(claim.Delivery.EventID)
	if err != nil {
		return gateway.applyDownloadDeliveryFailure(claim, false, model.PlatformRelayDeliveryFailurePayload, 0)
	}
	payload := []byte(event.PayloadJSON)
	digest := sha256.Sum256(payload)
	if hex.EncodeToString(digest[:]) != event.PayloadSHA256 {
		return gateway.applyDownloadDeliveryFailure(claim, false, model.PlatformRelayDeliveryFailurePayload, 0)
	}
	timestamp := gateway.clock().Unix()
	signature := signPlatformDownloadCompletion(gateway.config.CompletionSigningSecret, timestamp, event.ID, payload)
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, gateway.config.PlatformCompletionURL, bytes.NewReader(payload))
	if err != nil {
		return gateway.applyDownloadDeliveryFailure(claim, false, model.PlatformRelayDeliveryFailureTarget, 0)
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("User-Agent", platformDownloadEdgeUserAgent)
	request.Header.Set("X-Internal-Service-Token", gateway.config.PlatformEdgeCompletionToken)
	request.Header.Set("X-Download-Event-ID", event.ID)
	request.Header.Set("X-Download-Timestamp", strconv.FormatInt(timestamp, 10))
	request.Header.Set("X-Download-Signature", signature)
	request.Header.Set("X-Request-ID", claim.Delivery.RequestID)
	response, err := gateway.platformClient.Do(request)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		return gateway.applyDownloadDeliveryFailure(claim, true, model.PlatformRelayDeliveryFailureTransport, 0)
	}
	defer response.Body.Close()
	raw, readErr := io.ReadAll(io.LimitReader(response.Body, platformDownloadEdgeMaxPlatformBody+1))
	if readErr != nil || len(raw) > platformDownloadEdgeMaxPlatformBody {
		return gateway.applyDownloadDeliveryFailure(claim, true, model.PlatformRelayDeliveryFailureEndpoint, response.StatusCode)
	}
	if response.StatusCode != http.StatusCreated {
		retryable := response.StatusCode == http.StatusConflict || response.StatusCode == http.StatusRequestTimeout ||
			response.StatusCode == http.StatusTooEarly || response.StatusCode == http.StatusTooManyRequests || response.StatusCode >= 500
		failure := model.PlatformRelayDeliveryFailureEndpoint
		if response.StatusCode == http.StatusConflict {
			failure = model.PlatformRelayDeliveryFailureConflict
		}
		return gateway.applyDownloadDeliveryFailure(claim, retryable, failure, response.StatusCode)
	}
	var receipt platformDownloadCompletionResponse
	if decodePlatformDownloadEdgeStrictJSON(raw, &receipt) != nil || !platformDownloadCompletionResponseMatches(receipt, *event) {
		// The Platform may have committed even if an intermediary damaged its
		// response. Retrying the same signed event is safe and preserves proof.
		return gateway.applyDownloadDeliveryFailure(claim, true, model.PlatformRelayDeliveryFailureEndpoint, response.StatusCode)
	}
	won, err := model.CompletePlatformDownloadCompletionDelivery(
		event.ID, claim.Token, response.StatusCode,
		func(deliveredAt time.Time) (*model.PlatformDownloadCompletionProof, error) {
			proofPayload := PlatformDownloadCompletionProofPayload{
				SchemaVersion: 1, Kind: "relay.download_completion.edge_gateway.proof",
				CompletionID: receipt.ID, DownloadRecordID: event.DownloadRecordID,
				CompanyID: event.CompanyID, TaskID: event.TaskID, AssetID: event.AssetID,
				Source: "edge_gateway", SignedEventID: receipt.SignedEventID,
				SignedPayloadSHA256: receipt.SignedPayloadSHA256,
				IssuanceRequestID:   event.IssuanceRequestID, TransferReference: event.TransferReference,
				GatewayRequestID: event.GatewayRequestID, OBSBucket: event.OBSBucket,
				OBSObjectKey: event.OBSObjectKey, OBSVersionID: event.OBSVersionID,
				HTTPStatus: 200, TransferScope: "full_body", BytesSent: event.BytesSent,
				ExpectedSizeBytes: event.ExpectedSizeBytes, ArtifactSHA256: event.ArtifactSHA256,
				CompletedAtUTC:         event.CompletedAt.UTC().Format(time.RFC3339Nano),
				PlatformDeliveredAtUTC: deliveredAt.UTC().Format(time.RFC3339Nano),
				ProducerSubject:        gateway.config.ProducerSubject,
				ProducedAtUTC:          deliveredAt.UTC().Format(time.RFC3339Nano), Nonce: uuid.NewString(),
			}
			rawProof, err := json.Marshal(proofPayload)
			if err != nil {
				return nil, err
			}
			proofDigest := sha256.Sum256(rawProof)
			signingInput := append([]byte(platformDownloadEdgeProofDomain), rawProof...)
			signature := ed25519.Sign(gateway.config.ProofSigningPrivateKey, signingInput)
			return &model.PlatformDownloadCompletionProof{
				EventID: event.ID, CompletionID: receipt.ID, KeyID: gateway.config.ProofKeyID,
				PayloadJSON: string(rawProof), PayloadSHA256: hex.EncodeToString(proofDigest[:]),
				SignatureBase64: base64.StdEncoding.EncodeToString(signature), ProducedAt: deliveredAt.UTC(),
			}, nil
		},
	)
	if err != nil {
		return err
	}
	if !won {
		return model.ErrPlatformDownloadEdgeClaimLost
	}
	return nil
}

func (gateway *PlatformDownloadEdgeGateway) applyDownloadDeliveryFailure(
	claim model.PlatformRelayDeliveryClaim, retryable bool, failure string, status int,
) error {
	if !retryable {
		_, err := model.DeadLetterPlatformRelayExternalDelivery(
			model.PlatformRelayDeliveryKindDownloadCompletion, claim.Delivery.EventID, claim.Token, failure, status,
		)
		return err
	}
	_, _, err := model.ReleasePlatformRelayExternalDelivery(
		model.PlatformRelayDeliveryKindDownloadCompletion, claim.Delivery.EventID, claim.Token,
		PlatformRelayExternalDeliveryRetryDelay(claim.Delivery.Attempts), failure, status,
	)
	return err
}

func (gateway *PlatformDownloadEdgeGateway) RunDeliveryWorker(ctx context.Context) error {
	ticker := time.NewTicker(gateway.config.DeliveryPollInterval)
	defer ticker.Stop()
	for {
		for {
			didWork, err := gateway.RunDeliveryOnce(ctx)
			if err != nil {
				break
			}
			if !didWork {
				break
			}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

func platformDownloadCompletionResponseMatches(receipt platformDownloadCompletionResponse, event model.PlatformDownloadCompletionEvent) bool {
	return platformDownloadEdgeCanonicalUUID(receipt.ID) && receipt.DownloadRecordID == event.DownloadRecordID &&
		receipt.ExternalEventID == event.ID && receipt.Source == "edge_gateway" && receipt.BytesSent == event.BytesSent &&
		receipt.VerificationVersion == 1 && receipt.ArtifactSHA256 == event.ArtifactSHA256 &&
		receipt.ExpectedSizeBytes == event.ExpectedSizeBytes && receipt.HTTPStatus == 200 && receipt.TransferScope == "full_body" &&
		receipt.SignedEventID == event.ID && receipt.SignedPayloadSHA256 == event.PayloadSHA256 &&
		!receipt.CompletedAt.IsZero() && receipt.CompletedAt.UTC().Equal(event.CompletedAt.UTC()) &&
		!receipt.SignedEventTimestamp.IsZero() && !receipt.VerifiedAt.IsZero() && !receipt.CreatedAt.IsZero() &&
		len(receipt.SourceEvidence) == 2 && receipt.SourceEvidence["gateway_request_id"] == event.GatewayRequestID &&
		receipt.SourceEvidence["gateway_transfer_reference"] == event.TransferReference
}

func signPlatformDownloadCompletion(secret string, timestamp int64, eventID string, raw []byte) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = io.WriteString(mac, platformDownloadEdgeCompletionDomain+"edge_gateway\n")
	_, _ = io.WriteString(mac, strconv.FormatInt(timestamp, 10)+"\n"+eventID+"\n")
	_, _ = mac.Write(raw)
	return "v1=" + hex.EncodeToString(mac.Sum(nil))
}

type platformDownloadEdgeVerifiedWriter struct {
	destination io.Writer
	digest      hash.Hash
	bytes       int64
}

func (writer *platformDownloadEdgeVerifiedWriter) Write(payload []byte) (int, error) {
	written, err := writer.destination.Write(payload)
	if written > 0 {
		_, _ = writer.digest.Write(payload[:written])
		writer.bytes += int64(written)
	}
	if err == nil && written != len(payload) {
		err = io.ErrShortWrite
	}
	return written, err
}

func encryptPlatformDownloadEdgeSourceURL(key []byte, ticketID, recordID, digest, sourceURL string) ([]byte, []byte, error) {
	aead, err := platformDownloadEdgeAEAD(key)
	if err != nil {
		return nil, nil, err
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, nil, err
	}
	associated := []byte("download-edge-source.v1\n" + ticketID + "\n" + recordID + "\n" + digest)
	return aead.Seal(nil, nonce, []byte(sourceURL), associated), nonce, nil
}

func decryptPlatformDownloadEdgeSourceURL(key []byte, ticketID, recordID, digest string, nonce, ciphertext []byte) (string, error) {
	aead, err := platformDownloadEdgeAEAD(key)
	if err != nil {
		return "", err
	}
	associated := []byte("download-edge-source.v1\n" + ticketID + "\n" + recordID + "\n" + digest)
	plaintext, err := aead.Open(nil, nonce, ciphertext, associated)
	if err != nil {
		return "", err
	}
	actual := sha256.Sum256(plaintext)
	if hex.EncodeToString(actual[:]) != digest {
		return "", errors.New("source URL digest")
	}
	return string(plaintext), nil
}

func platformDownloadEdgeAEAD(key []byte) (cipher.AEAD, error) {
	if len(key) != 32 {
		return nil, errors.New("AES-256 key required")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

func decodePlatformDownloadEdgeStrictJSON(raw []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	if err := rejectPlatformDownloadEdgeDuplicateJSON(decoder); err != nil {
		return err
	}
	decoder = json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("trailing JSON value")
	}
	return nil
}

func rejectPlatformDownloadEdgeDuplicateJSON(decoder *json.Decoder) error {
	if err := scanPlatformDownloadEdgeJSONValue(decoder); err != nil {
		return err
	}
	if _, err := decoder.Token(); !errors.Is(err, io.EOF) {
		return errors.New("trailing JSON token")
	}
	return nil
}

func scanPlatformDownloadEdgeJSONValue(decoder *json.Decoder) error {
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	delimiter, ok := token.(json.Delim)
	if !ok {
		return nil
	}
	switch delimiter {
	case '{':
		seen := map[string]struct{}{}
		for decoder.More() {
			keyToken, err := decoder.Token()
			if err != nil {
				return err
			}
			key, ok := keyToken.(string)
			if !ok {
				return errors.New("JSON object key")
			}
			if _, exists := seen[key]; exists {
				return errors.New("duplicate JSON key")
			}
			seen[key] = struct{}{}
			if err := scanPlatformDownloadEdgeJSONValue(decoder); err != nil {
				return err
			}
		}
		closing, err := decoder.Token()
		if err != nil || closing != json.Delim('}') {
			return errors.New("JSON object closing delimiter")
		}
	case '[':
		for decoder.More() {
			if err := scanPlatformDownloadEdgeJSONValue(decoder); err != nil {
				return err
			}
		}
		closing, err := decoder.Token()
		if err != nil || closing != json.Delim(']') {
			return errors.New("JSON array closing delimiter")
		}
	default:
		return errors.New("unexpected JSON delimiter")
	}
	return nil
}

func newPlatformDownloadEdgeHTTPClient(timeout time.Duration) *http.Client {
	return &http.Client{
		Timeout:       timeout,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse },
		Transport: &http.Transport{
			Proxy: nil, DisableCompression: true, ForceAttemptHTTP2: true,
			DialContext:         (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
			TLSClientConfig:     &tls.Config{MinVersion: tls.VersionTLS12},
			TLSHandshakeTimeout: 10 * time.Second, ResponseHeaderTimeout: 30 * time.Second,
			IdleConnTimeout: 90 * time.Second,
		},
	}
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("X-Content-Type-Options", "nosniff")
		writer.Header().Set("Referrer-Policy", "no-referrer")
		writer.Header().Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
		next.ServeHTTP(writer, request)
	})
}

func writePlatformDownloadEdgeError(writer http.ResponseWriter, status int, code string) {
	writePlatformDownloadEdgeJSON(writer, status, map[string]string{"error": code})
}

func writePlatformDownloadEdgeJSON(writer http.ResponseWriter, status int, payload any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.Header().Set("Cache-Control", "no-store")
	writer.WriteHeader(status)
	encoder := json.NewEncoder(writer)
	encoder.SetEscapeHTML(true)
	_ = encoder.Encode(payload)
}

func platformDownloadEdgeCanonicalUUID(value string) bool {
	parsed, err := uuid.Parse(value)
	return err == nil && parsed.String() == value
}

func platformDownloadEdgeCanonicalToken(token string) bool {
	decoded, err := base64.RawURLEncoding.DecodeString(token)
	return err == nil && len(decoded) == sha256.Size && base64.RawURLEncoding.EncodeToString(decoded) == token
}

func platformDownloadEdgeSecretEqual(left, right string) bool {
	if len(left) != len(right) || len(right) == 0 {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(left), []byte(right)) == 1
}

func platformDownloadEdgeHasControl(value string) bool {
	for _, character := range value {
		if character < 0x20 || character == 0x7f {
			return true
		}
	}
	return false
}

func platformDownloadEdgeSafeContentType(value string) string {
	value = strings.TrimSpace(value)
	if value == "" || len(value) > 200 || platformDownloadEdgeHasControl(value) {
		return "application/octet-stream"
	}
	return value
}

func platformDownloadEdgeSchemeAllowed(parsed *url.URL, production bool) bool {
	if parsed == nil {
		return false
	}
	if parsed.Scheme == "https" {
		return true
	}
	return !production && parsed.Scheme == "http" && platformDownloadEdgeLoopback(parsed.Hostname())
}

func platformDownloadEdgeLoopback(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func platformDownloadEdgeOfficialOBSHost(hostport string) bool {
	host := hostport
	if parsedHost, _, err := net.SplitHostPort(hostport); err == nil {
		host = parsedHost
	}
	host = strings.TrimSuffix(strings.ToLower(host), ".")
	return strings.HasSuffix(host, ".myhuaweicloud.com") || strings.HasSuffix(host, ".myhuaweicloud.cn")
}

func parsePlatformDownloadEdgeDuration(name string, fallback int) (time.Duration, error) {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		value = strconv.Itoa(fallback)
	}
	seconds, err := strconv.Atoi(value)
	if err != nil || seconds <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer", name)
	}
	return time.Duration(seconds) * time.Second, nil
}

func decodePlatformDownloadEdgeKey(name string, expected int) ([]byte, error) {
	encoded := os.Getenv(name)
	if encoded == "" || encoded != strings.TrimSpace(encoded) || strings.ContainsAny(encoded, "\r\n\x00") {
		return nil, fmt.Errorf("%s is invalid", name)
	}
	decoded, err := base64.StdEncoding.Strict().DecodeString(encoded)
	if err != nil || len(decoded) != expected {
		return nil, fmt.Errorf("%s is invalid", name)
	}
	return decoded, nil
}

func decodeCanonicalPlatformDownloadEdgeKey(value string, expected int) ([]byte, error) {
	if value == "" || value != strings.TrimSpace(value) || platformDownloadEdgeHasControl(value) {
		return nil, errors.New("download edge runtime secret file is invalid")
	}
	decoded, err := base64.StdEncoding.Strict().DecodeString(value)
	if err != nil || len(decoded) != expected || base64.StdEncoding.EncodeToString(decoded) != value {
		clear(decoded)
		return nil, errors.New("download edge runtime secret file is invalid")
	}
	return decoded, nil
}

func ParsePlatformDownloadEdgeRuntimeSecretsFile(raw []byte) (PlatformDownloadEdgeRuntimeSecretsFile, error) {
	invalid := func() (PlatformDownloadEdgeRuntimeSecretsFile, error) {
		return PlatformDownloadEdgeRuntimeSecretsFile{}, errors.New("download edge runtime secret file is invalid")
	}
	if len(raw) == 0 || !json.Valid(raw) || rejectPlatformRelayDuplicateJSONKeys(raw) != nil {
		return invalid()
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	var document PlatformDownloadEdgeRuntimeSecretsFile
	if decoder.Decode(&document) != nil || requirePlatformRelayJSONEOF(decoder) != nil ||
		document.Kind != PlatformDownloadEdgeRuntimeSecretsKind ||
		document.SchemaVersion != PlatformDownloadEdgeRuntimeSecretsSchemaVersion {
		return invalid()
	}
	representations := make(map[[sha256.Size]byte]struct{})
	addRepresentation := func(value []byte, minimum int, text bool) bool {
		if len(value) < minimum || !protectedPlatformRelaySecretDiverse(string(value)) ||
			(text && (platformDownloadEdgeHasControl(string(value)) || platformRelaySecretIsPlaceholder(string(value)))) {
			return false
		}
		digest := sha256.Sum256(value)
		if _, duplicate := representations[digest]; duplicate {
			return false
		}
		representations[digest] = struct{}{}
		return true
	}
	for _, value := range []string{
		document.RegistrationToken,
		document.RegistrationSigningSecret,
		document.PlatformEdgeCompletionToken,
		document.CompletionSigningSecret,
		document.ProofReadToken,
	} {
		if value != strings.TrimSpace(value) || !addRepresentation([]byte(value), 32, true) {
			return invalid()
		}
	}
	for _, encoded := range []string{
		document.TicketTokenKeyBase64,
		document.SourceEncryptionKeyBase64,
		document.ProofSigningSeedBase64,
	} {
		decoded, err := decodeCanonicalPlatformDownloadEdgeKey(encoded, ed25519.SeedSize)
		if err != nil || !addRepresentation([]byte(encoded), 32, true) || !addRepresentation(decoded, ed25519.SeedSize, false) {
			clear(decoded)
			return invalid()
		}
		clear(decoded)
	}
	return document, nil
}

func platformDownloadEdgeRuntimeSecretsFromDocument(
	document PlatformDownloadEdgeRuntimeSecretsFile,
) (ticketKey []byte, sourceKey []byte, proofKey ed25519.PrivateKey, err error) {
	ticketKey, err = decodeCanonicalPlatformDownloadEdgeKey(document.TicketTokenKeyBase64, aes.BlockSize*2)
	if err != nil {
		return nil, nil, nil, err
	}
	sourceKey, err = decodeCanonicalPlatformDownloadEdgeKey(document.SourceEncryptionKeyBase64, aes.BlockSize*2)
	if err != nil {
		clear(ticketKey)
		return nil, nil, nil, err
	}
	seed, err := decodeCanonicalPlatformDownloadEdgeKey(document.ProofSigningSeedBase64, ed25519.SeedSize)
	if err != nil {
		clear(ticketKey)
		clear(sourceKey)
		return nil, nil, nil, err
	}
	proofKey = ed25519.NewKeyFromSeed(seed)
	clear(seed)
	return ticketKey, sourceKey, proofKey, nil
}

func PlatformDownloadEdgeConfigFromEnv() (PlatformDownloadEdgeConfig, error) {
	environment := os.Getenv("RELAY_DOWNLOAD_EDGE_ENVIRONMENT")
	if environment != strings.TrimSpace(environment) || environment != strings.ToLower(environment) {
		return PlatformDownloadEdgeConfig{}, errors.New("download edge environment is invalid")
	}
	production := false
	protected := false
	switch environment {
	case "development":
		if model.RelayDatabaseRoleAttestationRequired() {
			return PlatformDownloadEdgeConfig{}, errors.New("role-attested download edge requires staging or production")
		}
	case "staging":
		protected = true
	case "production":
		production = true
		protected = true
	default:
		return PlatformDownloadEdgeConfig{}, errors.New("download edge environment is invalid")
	}
	var runtimeSecrets *PlatformDownloadEdgeRuntimeSecretsFile
	if protected {
		if os.Getenv("APP_ENV") != environment || os.Getenv("DEPLOYMENT_ENV") != environment ||
			os.Getenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED") != "true" ||
			os.Getenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED") != "true" ||
			os.Getenv("RELAY_DATABASE_SECRET_FILES_REQUIRED") != "true" ||
			os.Getenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED") != "true" ||
			os.Getenv("DEBUG") != "false" {
			return PlatformDownloadEdgeConfig{}, errors.New("protected download edge environment is invalid")
		}
		if common.ProtectedRawSecretEnvironmentPresent(os.Environ()) {
			return PlatformDownloadEdgeConfig{}, errors.New("protected download edge received a legacy raw secret environment variable")
		}
		for _, forbidden := range platformDownloadEdgeForbiddenSecretEnvironments {
			if _, present := os.LookupEnv(forbidden); present {
				return PlatformDownloadEdgeConfig{}, errors.New("protected download edge received a legacy raw secret environment variable")
			}
		}
		if verifyErr := verifyPlatformDownloadEdgeSecretIsolationReceipt(PlatformRelaySecretIsolationConsumerEdge); verifyErr != nil {
			return PlatformDownloadEdgeConfig{}, errors.New("download edge secret isolation commitment is unavailable or invalid")
		}
		raw, readErr := readPlatformDownloadEdgeRuntimeSecretFile(
			platformDownloadEdgeRuntimeSecretsFileEnvironment,
			platformDownloadEdgeRuntimeSecretsFileMaxBytes,
		)
		if readErr != nil {
			return PlatformDownloadEdgeConfig{}, readErr
		}
		defer clear(raw)
		document, parseErr := ParsePlatformDownloadEdgeRuntimeSecretsFile(raw)
		if parseErr != nil {
			return PlatformDownloadEdgeConfig{}, parseErr
		}
		runtimeSecrets = &document
	}
	ticketTTL, err := parsePlatformDownloadEdgeDuration("RELAY_DOWNLOAD_EDGE_TICKET_TTL_SECONDS", 300)
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	sourceExpiryMargin, err := parsePlatformDownloadEdgeDuration("RELAY_DOWNLOAD_EDGE_SOURCE_EXPIRY_MARGIN_SECONDS", 60)
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	maxAge, err := parsePlatformDownloadEdgeDuration("RELAY_DOWNLOAD_EDGE_REGISTRATION_MAX_AGE_SECONDS", 300)
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	transferTimeout, err := parsePlatformDownloadEdgeDuration("RELAY_DOWNLOAD_EDGE_TRANSFER_TIMEOUT_SECONDS", 300)
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	transferCompletionMargin, err := parsePlatformDownloadEdgeDuration("RELAY_DOWNLOAD_EDGE_TRANSFER_COMPLETION_MARGIN_SECONDS", 10)
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	claimLease, err := parsePlatformDownloadEdgeDuration("RELAY_DOWNLOAD_EDGE_TICKET_CLAIM_LEASE_SECONDS", 330)
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	deliveryLease, err := parsePlatformDownloadEdgeDuration("RELAY_DOWNLOAD_EDGE_DELIVERY_CLAIM_LEASE_SECONDS", 60)
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	deliveryCommitMargin, err := parsePlatformDownloadEdgeDuration("RELAY_DOWNLOAD_EDGE_DELIVERY_COMMIT_MARGIN_SECONDS", 10)
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	poll, err := parsePlatformDownloadEdgeDuration("RELAY_DOWNLOAD_EDGE_DELIVERY_POLL_SECONDS", 1)
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	var tokenKey []byte
	var encryptionKey []byte
	var privateKey ed25519.PrivateKey
	if runtimeSecrets != nil {
		tokenKey, encryptionKey, privateKey, err = platformDownloadEdgeRuntimeSecretsFromDocument(*runtimeSecrets)
		if err != nil {
			return PlatformDownloadEdgeConfig{}, err
		}
	} else {
		tokenKey, err = decodePlatformDownloadEdgeKey("RELAY_DOWNLOAD_EDGE_TICKET_TOKEN_KEY_BASE64", 32)
		if err != nil {
			return PlatformDownloadEdgeConfig{}, err
		}
		encryptionKey, err = decodePlatformDownloadEdgeKey("RELAY_DOWNLOAD_EDGE_SOURCE_ENCRYPTION_KEY_BASE64", 32)
		if err != nil {
			return PlatformDownloadEdgeConfig{}, err
		}
		privateEncoded := os.Getenv("RELAY_DOWNLOAD_EDGE_PROOF_PRIVATE_KEY_BASE64")
		if privateEncoded == "" || privateEncoded != strings.TrimSpace(privateEncoded) || strings.ContainsAny(privateEncoded, "\r\n\x00") {
			return PlatformDownloadEdgeConfig{}, fmt.Errorf("download edge proof private key is invalid")
		}
		privateRaw, decodeErr := base64.StdEncoding.Strict().DecodeString(privateEncoded)
		if decodeErr != nil {
			return PlatformDownloadEdgeConfig{}, fmt.Errorf("download edge proof private key is invalid")
		}
		switch len(privateRaw) {
		case ed25519.SeedSize:
			privateKey = ed25519.NewKeyFromSeed(privateRaw)
		case ed25519.PrivateKeySize:
			privateKey = ed25519.PrivateKey(privateRaw)
		default:
			return PlatformDownloadEdgeConfig{}, fmt.Errorf("download edge proof private key is invalid")
		}
	}
	maxAttempts, err := strconv.Atoi(strings.TrimSpace(os.Getenv("RELAY_DOWNLOAD_EDGE_DELIVERY_MAX_ATTEMPTS")))
	if strings.TrimSpace(os.Getenv("RELAY_DOWNLOAD_EDGE_DELIVERY_MAX_ATTEMPTS")) == "" {
		maxAttempts = 8
		err = nil
	}
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	maxBytes, err := strconv.ParseInt(strings.TrimSpace(os.Getenv("RELAY_DOWNLOAD_EDGE_MAX_ARTIFACT_BYTES")), 10, 64)
	if strings.TrimSpace(os.Getenv("RELAY_DOWNLOAD_EDGE_MAX_ARTIFACT_BYTES")) == "" {
		maxBytes = 5 * 1024 * 1024 * 1024
		err = nil
	}
	if err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	registrationToken := os.Getenv("RELAY_DOWNLOAD_EDGE_REGISTRATION_TOKEN")
	registrationSigningSecret := os.Getenv("RELAY_DOWNLOAD_EDGE_REGISTRATION_SIGNING_SECRET")
	platformCompletionToken := os.Getenv("RELAY_DOWNLOAD_EDGE_PLATFORM_INTERNAL_TOKEN")
	completionSigningSecret := os.Getenv("RELAY_DOWNLOAD_EDGE_COMPLETION_SIGNING_SECRET")
	proofReadToken := os.Getenv("RELAY_DOWNLOAD_EDGE_PROOF_READ_TOKEN")
	if runtimeSecrets != nil {
		registrationToken = runtimeSecrets.RegistrationToken
		registrationSigningSecret = runtimeSecrets.RegistrationSigningSecret
		platformCompletionToken = runtimeSecrets.PlatformEdgeCompletionToken
		completionSigningSecret = runtimeSecrets.CompletionSigningSecret
		proofReadToken = runtimeSecrets.ProofReadToken
	}
	config := PlatformDownloadEdgeConfig{
		Production: production, Protected: protected, ListenAddress: strings.TrimSpace(os.Getenv("RELAY_DOWNLOAD_EDGE_LISTEN_ADDRESS")),
		PublicBaseURL:             strings.TrimSpace(os.Getenv("RELAY_DOWNLOAD_EDGE_PUBLIC_BASE_URL")),
		AllowedOBSHosts:           strings.Split(strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_DOWNLOAD_EDGE_ALLOWED_OBS_HOSTS"))), ","),
		RegistrationToken:         registrationToken,
		RegistrationSigningSecret: registrationSigningSecret,
		TicketTokenKey:            tokenKey, SourceEncryptionKey: encryptionKey,
		PlatformCompletionURL:       strings.TrimSpace(os.Getenv("RELAY_DOWNLOAD_EDGE_PLATFORM_COMPLETION_URL")),
		PlatformEdgeCompletionToken: platformCompletionToken,
		CompletionSigningSecret:     completionSigningSecret,
		ProofSigningPrivateKey:      privateKey, ProofKeyID: strings.TrimSpace(os.Getenv("RELAY_DOWNLOAD_EDGE_PROOF_KEY_ID")),
		ProofReadToken:  proofReadToken,
		ProducerSubject: strings.TrimSpace(os.Getenv("RELAY_DOWNLOAD_EDGE_PRODUCER_SUBJECT")),
		TicketTTL:       ticketTTL, SourceExpirySafetyMargin: sourceExpiryMargin,
		RegistrationMaxAge: maxAge, TransferTimeout: transferTimeout, TransferCompletionMargin: transferCompletionMargin,
		TicketClaimLease: claimLease, DeliveryClaimLease: deliveryLease, DeliveryCommitMargin: deliveryCommitMargin, DeliveryPollInterval: poll,
		DeliveryMaxAttempts: maxAttempts, MaxArtifactBytes: maxBytes,
	}
	if config.ListenAddress == "" {
		config.ListenAddress = ":8080"
	}
	if err := config.Validate(); err != nil {
		return PlatformDownloadEdgeConfig{}, err
	}
	return config, nil
}
