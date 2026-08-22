package service

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/go-redis/redis/v8"
)

const (
	PlatformRelaySecretIsolationReceiptKind          = "relay_secret_isolation_commitment"
	PlatformRelaySecretIsolationReceiptSchemaVersion = 2
	PlatformRelaySecretIsolationConsumerCount        = 14

	PlatformRelaySecretIsolationConsumerPre                           = "pre"
	PlatformRelaySecretIsolationConsumerMigrate                       = "migrate"
	PlatformRelaySecretIsolationConsumerPost                          = "post"
	PlatformRelaySecretIsolationConsumerPrincipal                     = "principal"
	PlatformRelaySecretIsolationConsumerAPI                           = "api"
	PlatformRelaySecretIsolationConsumerEdge                          = "edge"
	PlatformRelaySecretIsolationConsumerPlatformDBRolePre             = "platform-db-role-pre"
	PlatformRelaySecretIsolationConsumerPlatformMigration             = "platform-migration"
	PlatformRelaySecretIsolationConsumerPlatformAPI                   = "platform-api"
	PlatformRelaySecretIsolationConsumerPlatformDispatcher            = "platform-dispatcher"
	PlatformRelaySecretIsolationConsumerPlatformRelaySync             = "platform-relay-sync"
	PlatformRelaySecretIsolationConsumerPlatformTimeoutWorker         = "platform-timeout-worker"
	PlatformRelaySecretIsolationConsumerPlatformPublishingWorker      = "platform-publishing-worker"
	PlatformRelaySecretIsolationConsumerPlatformDownloadGatewayWorker = "platform-download-gateway-registration-worker"

	platformRelaySecretIsolationReceiptDirectoryEnvironmentPrefix = "RELAY_SECRET_ISOLATION_RECEIPT_"
	platformRelaySecretIsolationReceiptFileEnvironment            = "RELAY_SECRET_ISOLATION_RECEIPT_FILE"
	platformRelaySecretIsolationCommitDirectoryEnvironment        = "RELAY_SECRET_ISOLATION_COMMIT_DIRECTORY"
	platformRelaySecretIsolationCommitFileEnvironment             = "RELAY_SECRET_ISOLATION_COMMIT_FILE"
	platformRelaySecretIsolationGenerationEnvironment             = "RELAY_SECRET_ISOLATION_GENERATION"
	platformRelaySecretIsolationReceiptMaxBytes                   = 1024 * 1024
	platformRelaySecretIsolationCommitKind                        = "relay_secret_isolation_commit"
	platformRelaySecretIsolationCommitSchemaVersion               = 2
	platformRelaySecretIsolationGenerationPreRoot                 = "pre-root"
	platformRelaySecretIsolationGenerationRootProofPresent        = "root-proof-present"

	platformRelaySecretIsolationRoleAdminDSNEnvironment         = "RELAY_SECRET_ISOLATION_ROLE_ADMIN_SQL_DSN_FILE"
	platformRelaySecretIsolationMigrationDSNEnvironment         = "RELAY_SECRET_ISOLATION_MIGRATION_SQL_DSN_FILE"
	platformRelaySecretIsolationRuntimeDSNEnvironment           = "RELAY_SECRET_ISOLATION_RUNTIME_SQL_DSN_FILE"
	platformRelaySecretIsolationEdgeDSNEnvironment              = "RELAY_SECRET_ISOLATION_EDGE_SQL_DSN_FILE"
	platformRelaySecretIsolationPlatformRoleAdminDSNEnvironment = "PLATFORM_DATABASE_ROLE_ADMIN_DSN_FILE"
	platformRelaySecretIsolationPlatformOriginEnvironment       = "PLATFORM_PUBLIC_BASE_URL"
	platformRelaySecretIsolationRelayOriginEnvironment          = "NEW_API_RELAY_PUBLIC_BASE_URL"
	platformRelaySecretIsolationEdgeOriginEnvironment           = "DOWNLOAD_GATEWAY_PUBLIC_BASE_URL"
	platformRelaySecretIsolationRelayContractEnvironment        = "PLATFORM_NEW_API_RELAY_CONTRACT_REVISION"
	platformRelaySecretIsolationRelayDatabaseCAEnvironment      = "RELAY_DATABASE_CA_FILE"
	platformRelaySecretIsolationPlatformDatabaseCAEnvironment   = "PLATFORM_DATABASE_CA_FILE"
	PlatformRelayRedisTLSCAFileEnvironment                      = "RELAY_REDIS_TLS_CA_FILE"
)

var platformRelaySecretIsolationConsumers = []string{
	PlatformRelaySecretIsolationConsumerAPI,
	PlatformRelaySecretIsolationConsumerEdge,
	PlatformRelaySecretIsolationConsumerMigrate,
	PlatformRelaySecretIsolationConsumerPlatformDBRolePre,
	PlatformRelaySecretIsolationConsumerPlatformAPI,
	PlatformRelaySecretIsolationConsumerPlatformDispatcher,
	PlatformRelaySecretIsolationConsumerPlatformDownloadGatewayWorker,
	PlatformRelaySecretIsolationConsumerPlatformMigration,
	PlatformRelaySecretIsolationConsumerPlatformPublishingWorker,
	PlatformRelaySecretIsolationConsumerPlatformRelaySync,
	PlatformRelaySecretIsolationConsumerPlatformTimeoutWorker,
	PlatformRelaySecretIsolationConsumerPost,
	PlatformRelaySecretIsolationConsumerPre,
	PlatformRelaySecretIsolationConsumerPrincipal,
}

var readPlatformRelaySecretIsolationProtectedFile = common.ReadProtectedSecretFile

type platformRelaySecretIsolationRelease struct {
	ImageDigest                    string `json:"image_digest"`
	SourceRevision                 string `json:"source_revision"`
	SourceSnapshotSHA256           string `json:"source_snapshot_sha256"`
	SourceSnapshotFileCount        int    `json:"source_snapshot_file_count"`
	UpstreamRevision               string `json:"upstream_revision"`
	RouteAcceptanceTrustKeysSHA256 string `json:"route_acceptance_trust_keys_sha256"`
	PlatformImage                  string `json:"platform_image"`
	PlatformSourceRevision         string `json:"platform_source_revision"`
	PlatformSourceSnapshotSHA256   string `json:"platform_source_snapshot_sha256"`
	PlatformOrigin                 string `json:"platform_origin"`
	RelayOrigin                    string `json:"relay_origin"`
	EdgeOrigin                     string `json:"edge_origin"`
	RelayContractRevision          string `json:"relay_contract_revision"`
}

type platformRelaySecretIsolationCommitment struct {
	ID     string `json:"id"`
	SHA256 string `json:"sha256"`
}

type platformRelaySecretIsolationReceipt struct {
	SchemaVersion int                                      `json:"schema_version"`
	Kind          string                                   `json:"kind"`
	RunID         string                                   `json:"run_id"`
	Consumer      string                                   `json:"consumer"`
	Release       platformRelaySecretIsolationRelease      `json:"release"`
	Files         []platformRelaySecretIsolationCommitment `json:"files"`
	Semantics     []platformRelaySecretIsolationCommitment `json:"semantics"`
}

type platformRelaySecretIsolationCommitMarker struct {
	SchemaVersion int                                      `json:"schema_version"`
	Kind          string                                   `json:"kind"`
	RunID         string                                   `json:"run_id"`
	Generation    string                                   `json:"generation"`
	RootProofID   string                                   `json:"root_proof_id"`
	Release       platformRelaySecretIsolationRelease      `json:"release"`
	Receipts      []platformRelaySecretIsolationCommitment `json:"receipts"`
}

var platformRelaySecretIsolationAfterReceiptCommit func(int) error

type platformRelaySecretIsolationVerifiedContext struct {
	receipt platformRelaySecretIsolationReceipt
	marker  platformRelaySecretIsolationCommitMarker
}

var platformRelaySecretIsolationVerifiedContexts = struct {
	sync.RWMutex
	byKey map[string]platformRelaySecretIsolationVerifiedContext
}{byKey: make(map[string]platformRelaySecretIsolationVerifiedContext)}

func platformRelaySecretIsolationVerifiedContextKey(consumer string) (string, error) {
	receiptPath := os.Getenv(platformRelaySecretIsolationReceiptFileEnvironment)
	commitPath := os.Getenv(platformRelaySecretIsolationCommitFileEnvironment)
	if consumer == "" || receiptPath == "" || commitPath == "" ||
		receiptPath != strings.TrimSpace(receiptPath) || commitPath != strings.TrimSpace(commitPath) ||
		!filepath.IsAbs(receiptPath) || !filepath.IsAbs(commitPath) ||
		filepath.Clean(receiptPath) != receiptPath || filepath.Clean(commitPath) != commitPath {
		return "", errors.New("Relay secret isolation verified context is invalid")
	}
	return consumer + "\x00" + receiptPath + "\x00" + commitPath, nil
}

func platformRelaySecretIsolationRememberVerifiedContext(
	consumer string,
	receipt platformRelaySecretIsolationReceipt,
	marker platformRelaySecretIsolationCommitMarker,
) error {
	key, err := platformRelaySecretIsolationVerifiedContextKey(consumer)
	if err != nil {
		return err
	}
	receipt.Files = append([]platformRelaySecretIsolationCommitment(nil), receipt.Files...)
	receipt.Semantics = append([]platformRelaySecretIsolationCommitment(nil), receipt.Semantics...)
	marker.Receipts = append([]platformRelaySecretIsolationCommitment(nil), marker.Receipts...)
	candidate := platformRelaySecretIsolationVerifiedContext{receipt: receipt, marker: marker}
	platformRelaySecretIsolationVerifiedContexts.Lock()
	defer platformRelaySecretIsolationVerifiedContexts.Unlock()
	if existing, present := platformRelaySecretIsolationVerifiedContexts.byKey[key]; present {
		existingReceipt, existingReceiptErr := platformRelaySecretIsolationCanonicalReceipt(existing.receipt)
		candidateReceipt, candidateReceiptErr := platformRelaySecretIsolationCanonicalReceipt(candidate.receipt)
		existingMarker, existingMarkerErr := platformRelaySecretIsolationCanonicalCommitMarker(existing.marker)
		candidateMarker, candidateMarkerErr := platformRelaySecretIsolationCanonicalCommitMarker(candidate.marker)
		defer clear(existingReceipt)
		defer clear(candidateReceipt)
		defer clear(existingMarker)
		defer clear(candidateMarker)
		if existingReceiptErr != nil || candidateReceiptErr != nil || existingMarkerErr != nil || candidateMarkerErr != nil ||
			len(existingReceipt) != len(candidateReceipt) ||
			subtle.ConstantTimeCompare(existingReceipt, candidateReceipt) != 1 ||
			len(existingMarker) != len(candidateMarker) ||
			subtle.ConstantTimeCompare(existingMarker, candidateMarker) != 1 {
			return errors.New("Relay secret isolation verified context is immutable")
		}
		return nil
	}
	platformRelaySecretIsolationVerifiedContexts.byKey[key] = candidate
	return nil
}

func platformRelaySecretIsolationCurrentVerifiedContext(
	consumer string,
) (platformRelaySecretIsolationVerifiedContext, error) {
	key, err := platformRelaySecretIsolationVerifiedContextKey(consumer)
	if err != nil {
		return platformRelaySecretIsolationVerifiedContext{}, err
	}
	platformRelaySecretIsolationVerifiedContexts.RLock()
	context, present := platformRelaySecretIsolationVerifiedContexts.byKey[key]
	platformRelaySecretIsolationVerifiedContexts.RUnlock()
	if !present {
		return platformRelaySecretIsolationVerifiedContext{}, errors.New("Relay secret isolation verified context is unavailable")
	}
	context.receipt.Files = append([]platformRelaySecretIsolationCommitment(nil), context.receipt.Files...)
	context.receipt.Semantics = append([]platformRelaySecretIsolationCommitment(nil), context.receipt.Semantics...)
	context.marker.Receipts = append([]platformRelaySecretIsolationCommitment(nil), context.marker.Receipts...)
	return context, nil
}

type platformRelaySecretIsolationRepresentation struct {
	id               string
	digest           [sha256.Size]byte
	equivalenceGroup string
	equivalenceCount int
}

type platformRelaySecretIsolationFile struct {
	id              string
	environment     string
	raw             []byte
	representations []platformRelaySecretIsolationRepresentation
}

var platformRelaySecretIsolationCommitmentIDPattern = regexp.MustCompile(`^[a-z0-9][A-Za-z0-9._:-]{0,511}$`)
var platformRelaySecretIsolationDatabaseNamePattern = regexp.MustCompile(`^[a-z][a-z0-9_-]{0,62}$`)
var platformRelaySecretIsolationRunIDPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

func platformRelaySecretIsolationDigest(id string, value []byte, equivalenceGroup string) platformRelaySecretIsolationRepresentation {
	return platformRelaySecretIsolationRepresentation{
		id: id, digest: sha256.Sum256(value), equivalenceGroup: equivalenceGroup,
		equivalenceCount: func() int {
			if equivalenceGroup != "" {
				return 2
			}
			return 0
		}(),
	}
}

// platformRelaySecretIsolationDatabaseEndpoint returns the physical endpoint
// identity independently of each image's mounted CA pathname. It is used both
// to bind all roles within one database and to reject an accidental Relay /
// Platform collapse onto the same database even when their sslrootcert paths
// differ.
func platformRelaySecretIsolationDatabaseEndpoint(parsed *url.URL, databaseOverride string) (string, error) {
	if parsed == nil || parsed.Hostname() == "" || parsed.Port() == "" || parsed.RawPath != "" {
		return "", errors.New("Relay secret isolation database target is invalid")
	}
	host := strings.ToLower(strings.TrimSuffix(parsed.Hostname(), "."))
	if host == "" || parsed.Hostname() != host || strings.ContainsAny(host, "\x00\r\n") {
		return "", errors.New("Relay secret isolation database target is invalid")
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil || port < 1 || port > 65535 || net.JoinHostPort(host, strconv.Itoa(port)) != parsed.Host {
		return "", errors.New("Relay secret isolation database target is invalid")
	}
	database := strings.TrimPrefix(parsed.Path, "/")
	if databaseOverride != "" {
		database = databaseOverride
	}
	if parsed.Path == "" || strings.Count(parsed.Path, "/") != 1 ||
		!platformRelaySecretIsolationDatabaseNamePattern.MatchString(database) {
		return "", errors.New("Relay secret isolation database target is invalid")
	}
	return fmt.Sprintf(
		"postgres-endpoint-v1\nhost=%s\nport=%d\ndatabase=%s",
		host, port, database,
	), nil
}

// platformRelaySecretIsolationDatabaseTarget adds each domain's exact TLS
// policy to the physical endpoint identity. Role-specific query parameters
// (search_path/options) remain deliberately excluded.
func platformRelaySecretIsolationDatabaseTarget(parsed *url.URL, databaseOverride string) (string, error) {
	endpoint, err := platformRelaySecretIsolationDatabaseEndpoint(parsed, databaseOverride)
	if err != nil {
		return "", err
	}
	endpointFields, found := strings.CutPrefix(endpoint, "postgres-endpoint-v1\n")
	if !found || endpointFields == "" {
		return "", errors.New("Relay secret isolation database target is invalid")
	}
	query, err := url.ParseQuery(parsed.RawQuery)
	if err != nil || len(query["sslmode"]) != 1 || query.Get("sslmode") == "" || len(query["sslrootcert"]) > 1 {
		return "", errors.New("Relay secret isolation database target is invalid")
	}
	rootCertificate := "system"
	if len(query["sslrootcert"]) == 1 {
		rootCertificate = query["sslrootcert"][0]
	}
	if strings.ContainsAny(rootCertificate, "\x00\r\n") {
		return "", errors.New("Relay secret isolation database target is invalid")
	}
	return fmt.Sprintf("postgres-target-v1\n%s\nsslmode=%s\nsslrootcert=%s",
		endpointFields, query.Get("sslmode"), rootCertificate), nil
}

func platformRelaySecretIsolationReleaseIdentity() (platformRelaySecretIsolationRelease, error) {
	compiled := GetPlatformRelayCompiledBuildIdentity()
	imageDigest := os.Getenv("RELAY_COMPAT_IMAGE_DIGEST")
	sourceRevision := os.Getenv("RELAY_COMPAT_SOURCE_REVISION")
	snapshotSHA256 := os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256")
	fileCountRaw := os.Getenv("RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT")
	fileCount, fileCountErr := strconv.Atoi(fileCountRaw)
	upstreamRevision := os.Getenv("RELAY_COMPAT_UPSTREAM_REVISION")
	trustKeysSHA256 := os.Getenv("RELAY_COMPAT_ROUTE_ACCEPTANCE_TRUST_KEYS_SHA256")
	platformImage := os.Getenv("PLATFORM_IMAGE")
	platformSourceRevision := os.Getenv("PLATFORM_SOURCE_REVISION")
	platformSourceSnapshotSHA256 := os.Getenv("PLATFORM_SOURCE_SNAPSHOT_SHA256")
	platformOrigin := os.Getenv(platformRelaySecretIsolationPlatformOriginEnvironment)
	relayOrigin := os.Getenv(platformRelaySecretIsolationRelayOriginEnvironment)
	edgeOrigin := os.Getenv(platformRelaySecretIsolationEdgeOriginEnvironment)
	relayContractRevision := os.Getenv(platformRelaySecretIsolationRelayContractEnvironment)
	if compiled.UpstreamGitRevision != PlatformRelayUpstreamGitRevision ||
		!platformRelaySourceRevisionPattern.MatchString(compiled.SourceRevision) ||
		compiled.SourceRevision == strings.Repeat("0", 40) ||
		compiled.SourceRevision == compiled.UpstreamGitRevision ||
		!platformRelaySnapshotDigestPattern.MatchString(compiled.SourceSnapshotSHA256) ||
		compiled.SourceSnapshotFileCount < 1 ||
		!platformRelaySnapshotDigestPattern.MatchString(compiled.RouteAcceptanceTrustKeysSHA256) ||
		compiled.RouteAcceptanceTrustKeysSHA256 == "sha256:"+strings.Repeat("0", 64) ||
		!platformRelayImageDigestPattern.MatchString(imageDigest) || imageDigest == "sha256:"+strings.Repeat("0", 64) ||
		sourceRevision != compiled.SourceRevision || snapshotSHA256 != compiled.SourceSnapshotSHA256 ||
		fileCountErr != nil || fileCountRaw != strconv.Itoa(fileCount) || fileCount != compiled.SourceSnapshotFileCount ||
		upstreamRevision != compiled.UpstreamGitRevision || trustKeysSHA256 != compiled.RouteAcceptanceTrustKeysSHA256 ||
		!platformProcessImagePattern.MatchString(platformImage) ||
		strings.HasSuffix(platformImage, "@sha256:"+strings.Repeat("0", 64)) ||
		!platformRelaySourceRevisionPattern.MatchString(platformSourceRevision) ||
		platformSourceRevision == strings.Repeat("0", 40) ||
		!platformRelaySnapshotDigestPattern.MatchString(platformSourceSnapshotSHA256) ||
		platformSourceSnapshotSHA256 == "sha256:"+strings.Repeat("0", 64) ||
		!platformRelaySecretIsolationCanonicalOrigin(platformOrigin) ||
		!platformRelaySecretIsolationCanonicalOrigin(relayOrigin) ||
		!platformRelaySecretIsolationCanonicalOrigin(edgeOrigin) ||
		!platformProcessContractPattern.MatchString(relayContractRevision) {
		return platformRelaySecretIsolationRelease{}, errors.New("Relay secret isolation release identity is invalid")
	}
	return platformRelaySecretIsolationRelease{
		ImageDigest: imageDigest, SourceRevision: sourceRevision,
		SourceSnapshotSHA256: snapshotSHA256, SourceSnapshotFileCount: fileCount,
		UpstreamRevision:               compiled.UpstreamGitRevision,
		RouteAcceptanceTrustKeysSHA256: compiled.RouteAcceptanceTrustKeysSHA256,
		PlatformImage:                  platformImage, PlatformSourceRevision: platformSourceRevision,
		PlatformSourceSnapshotSHA256: platformSourceSnapshotSHA256,
		PlatformOrigin:               platformOrigin, RelayOrigin: relayOrigin, EdgeOrigin: edgeOrigin,
		RelayContractRevision: relayContractRevision,
	}, nil
}

func platformRelaySecretIsolationReleaseShapeValid(release platformRelaySecretIsolationRelease) bool {
	return platformRelayImageDigestPattern.MatchString(release.ImageDigest) &&
		release.ImageDigest != "sha256:"+strings.Repeat("0", 64) &&
		platformRelaySourceRevisionPattern.MatchString(release.SourceRevision) &&
		release.SourceRevision != strings.Repeat("0", 40) &&
		platformRelaySnapshotDigestPattern.MatchString(release.SourceSnapshotSHA256) &&
		release.SourceSnapshotSHA256 != "sha256:"+strings.Repeat("0", 64) &&
		release.SourceSnapshotFileCount > 0 &&
		platformRelaySourceRevisionPattern.MatchString(release.UpstreamRevision) &&
		release.UpstreamRevision != strings.Repeat("0", 40) &&
		release.SourceRevision != release.UpstreamRevision &&
		platformRelaySnapshotDigestPattern.MatchString(release.RouteAcceptanceTrustKeysSHA256) &&
		release.RouteAcceptanceTrustKeysSHA256 != "sha256:"+strings.Repeat("0", 64) &&
		platformProcessImagePattern.MatchString(release.PlatformImage) &&
		!strings.HasSuffix(release.PlatformImage, "@sha256:"+strings.Repeat("0", 64)) &&
		platformRelaySourceRevisionPattern.MatchString(release.PlatformSourceRevision) &&
		release.PlatformSourceRevision != strings.Repeat("0", 40) &&
		platformRelaySnapshotDigestPattern.MatchString(release.PlatformSourceSnapshotSHA256) &&
		release.PlatformSourceSnapshotSHA256 != "sha256:"+strings.Repeat("0", 64) &&
		platformRelaySecretIsolationCanonicalOrigin(release.PlatformOrigin) &&
		platformRelaySecretIsolationCanonicalOrigin(release.RelayOrigin) &&
		platformRelaySecretIsolationCanonicalOrigin(release.EdgeOrigin) &&
		platformProcessContractPattern.MatchString(release.RelayContractRevision)
}

func platformRelaySecretIsolationCanonicalOrigin(value string) bool {
	if value == "" || value != strings.TrimSpace(value) || len(value) > 2048 || strings.ContainsAny(value, "\x00\r\n") {
		return false
	}
	for index := range len(value) {
		if value[index] > 0x7f {
			return false
		}
	}
	parsed, err := url.Parse(value)
	if err != nil || parsed.Scheme != "https" || parsed.User != nil || parsed.Opaque != "" ||
		parsed.RawQuery != "" || parsed.Fragment != "" || parsed.Path != "" || parsed.RawPath != "" ||
		parsed.Hostname() == "" || parsed.Port() != "" {
		return false
	}
	host := strings.ToLower(strings.TrimSuffix(parsed.Hostname(), "."))
	return host != "" && parsed.Hostname() == host && parsed.Host == host
}

func platformRelaySecretIsolationReadFile(environment string, maximumBytes int64) ([]byte, error) {
	value, err := readPlatformRelaySecretIsolationProtectedFile(environment, maximumBytes)
	if err != nil || len(value) == 0 {
		clear(value)
		return nil, errors.New("Relay secret isolation source is unavailable or invalid")
	}
	return value, nil
}

func platformRelaySecretIsolationPrincipalFile(raw []byte) (platformRelaySecretIsolationFile, error) {
	principals, err := ParsePlatformRelayServicePrincipalsFile(raw)
	if err != nil {
		return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation principal source is invalid")
	}
	file := platformRelaySecretIsolationFile{id: "service_principals", raw: raw}
	for index, principal := range principals {
		bare := strings.TrimPrefix(principal.UpstreamToken, "sk-")
		file.representations = append(file.representations,
			platformRelaySecretIsolationDigest(fmt.Sprintf("principal.%03d.upstream.canonical", index), []byte(principal.UpstreamToken), ""),
			platformRelaySecretIsolationDigest(fmt.Sprintf("principal.%03d.upstream.bare", index), []byte(bare), ""),
		)
	}
	return file, nil
}

func platformRelaySecretIsolationAPIFile(raw []byte) (platformRelaySecretIsolationFile, error) {
	document, err := ParsePlatformRelayAPIRuntimeSecretsFile(raw)
	if err != nil {
		return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation API source is invalid")
	}
	options, err := redis.ParseURL(document.RedisDSN)
	if err != nil || options.Password == "" {
		return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation API source is invalid")
	}
	file := platformRelaySecretIsolationFile{id: "api_runtime", raw: raw}
	add := func(id string, value string) {
		file.representations = append(file.representations, platformRelaySecretIsolationDigest(id, []byte(value), ""))
	}
	for _, item := range []struct{ id, value string }{
		{"api.redis.password", options.Password},
		{"api.application.session", document.Application.SessionSecret},
		{"api.application.crypto", document.Application.CryptoSecret},
		{"api.internal_admission", document.InternalAdmissionToken},
		{"api.artifact_signing", document.ArtifactSigningSecret},
		{"api.obs.access_key", document.HuaweiOBS.AccessKeyID},
		{"api.obs.secret_key", document.HuaweiOBS.SecretAccessKey},
		{"api.provider_alert", document.ProviderAlertSigningSecret},
		{"api.platform_internal", document.PlatformInternalToken},
		{"api.channel_cost", document.ChannelCostSigningSecret},
		{"api.telemetry", document.TelemetrySigningSecret},
	} {
		add(item.id, item.value)
	}
	if document.HuaweiOBS.SecurityToken != "" {
		add("api.obs.security_token", document.HuaweiOBS.SecurityToken)
	}
	for index, client := range document.Clients {
		add(fmt.Sprintf("api.client.%03d.api_key", index), client.APIKey)
		if client.CallbackSigningSecret != "" {
			add(fmt.Sprintf("api.client.%03d.callback", index), client.CallbackSigningSecret)
			expectedCallback := os.Getenv(platformRelaySecretIsolationPlatformOriginEnvironment) +
				"/internal/relay-callbacks/new-api-v1"
			if client.CallbackURL != expectedCallback {
				return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation API source is invalid")
			}
			file.representations = append(file.representations,
				platformRelaySecretIsolationDigest(fmt.Sprintf("api.client.%03d.callback_url", index), []byte(client.CallbackURL), ""))
		}
	}
	for index, credential := range document.OperationsCredentials {
		digestBytes, decodeErr := hex.DecodeString(credential.TokenSHA256)
		if decodeErr != nil || len(digestBytes) != sha256.Size {
			clear(digestBytes)
			return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation API source is invalid")
		}
		var digest [sha256.Size]byte
		copy(digest[:], digestBytes)
		clear(digestBytes)
		file.representations = append(file.representations, platformRelaySecretIsolationRepresentation{
			id: fmt.Sprintf("api.operations.%03d.token", index), digest: digest,
		})
	}
	for index, approval := range document.ReconciliationApprovalKeys {
		add(fmt.Sprintf("api.approval.%03d.secret", index), approval.Secret)
	}
	for _, target := range []struct {
		id, environment, path string
	}{
		{"api.provider_alert_url", "RELAY_PROVIDER_ALERT_WEBHOOK_URL", "/internal/relay/provider-alerts"},
		{"api.channel_cost_url", "RELAY_PLATFORM_CHANNEL_COST_URL", "/internal/channel-costs"},
		{"api.task_stage_url", "RELAY_PLATFORM_TASK_STAGE_URL", "/internal/relay/task-stages"},
		{"api.operations_snapshot_url", "RELAY_PLATFORM_OPERATIONS_SNAPSHOT_URL", "/internal/relay/operations-snapshots"},
	} {
		value := os.Getenv(target.environment)
		if value != os.Getenv(platformRelaySecretIsolationPlatformOriginEnvironment)+target.path {
			return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation API target is invalid")
		}
		file.representations = append(file.representations,
			platformRelaySecretIsolationDigest(target.id, []byte(value), ""))
	}
	return file, nil
}

func platformRelaySecretIsolationEdgeFile(raw []byte) (platformRelaySecretIsolationFile, error) {
	document, err := ParsePlatformDownloadEdgeRuntimeSecretsFile(raw)
	if err != nil {
		return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation edge source is invalid")
	}
	file := platformRelaySecretIsolationFile{id: "edge_runtime", raw: raw}
	add := func(id string, value []byte) {
		file.representations = append(file.representations, platformRelaySecretIsolationDigest(id, value, ""))
	}
	for _, item := range []struct{ id, value string }{
		{"edge.registration_token", document.RegistrationToken},
		{"edge.registration_signing", document.RegistrationSigningSecret},
		{"edge.platform_completion_token", document.PlatformEdgeCompletionToken},
		{"edge.completion_signing", document.CompletionSigningSecret},
		{"edge.proof_read_token", document.ProofReadToken},
	} {
		add(item.id, []byte(item.value))
	}
	edgeOrigin := os.Getenv(platformRelaySecretIsolationEdgeOriginEnvironment)
	platformOrigin := os.Getenv(platformRelaySecretIsolationPlatformOriginEnvironment)
	publicBaseURL := os.Getenv("RELAY_DOWNLOAD_EDGE_PUBLIC_BASE_URL")
	completionURL := os.Getenv("RELAY_DOWNLOAD_EDGE_PLATFORM_COMPLETION_URL")
	if publicBaseURL != edgeOrigin ||
		completionURL != platformOrigin+"/internal/artifact-download-completions/edge-gateway" {
		return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation edge target is invalid")
	}
	file.representations = append(file.representations,
		platformRelaySecretIsolationDigest("edge.public_base_url", []byte(publicBaseURL), ""),
		platformRelaySecretIsolationDigest("edge.platform_completion_url", []byte(completionURL), ""),
	)
	for _, item := range []struct{ id, value string }{
		{"edge.ticket", document.TicketTokenKeyBase64},
		{"edge.source", document.SourceEncryptionKeyBase64},
		{"edge.proof_seed", document.ProofSigningSeedBase64},
	} {
		decoded, decodeErr := base64.StdEncoding.Strict().DecodeString(item.value)
		if decodeErr != nil || len(decoded) != 32 || base64.StdEncoding.EncodeToString(decoded) != item.value {
			clear(decoded)
			return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation edge source is invalid")
		}
		add(item.id+".encoded", []byte(item.value))
		add(item.id+".decoded", decoded)
		clear(decoded)
	}
	return file, nil
}

func platformRelaySecretIsolationKEKFile(raw []byte) (platformRelaySecretIsolationFile, error) {
	values, err := model.ProviderCredentialKeyringIsolationRepresentations(raw)
	if err != nil {
		return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation provider keyring source is invalid")
	}
	file := platformRelaySecretIsolationFile{id: "provider_kek", raw: raw}
	for index := range values {
		file.representations = append(file.representations,
			platformRelaySecretIsolationDigest("provider."+values[index].ID, values[index].Value, ""))
		clear(values[index].Value)
	}
	return file, nil
}

func platformRelaySecretIsolationCAFile(id string, raw []byte) (platformRelaySecretIsolationFile, error) {
	if len(raw) == 0 || len(raw) > 256*1024 || bytes.Contains(raw, []byte("\r")) || raw[len(raw)-1] != '\n' {
		return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation database CA source is invalid")
	}
	remaining := raw
	canonical := make([]byte, 0, len(raw))
	defer clear(canonical)
	count := 0
	for len(remaining) > 0 {
		if !bytes.HasPrefix(remaining, []byte("-----BEGIN CERTIFICATE-----\n")) {
			return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation database CA source is invalid")
		}
		block, rest := pem.Decode(remaining)
		if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 {
			return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation database CA source is invalid")
		}
		if _, err := x509.ParseCertificate(block.Bytes); err != nil {
			return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation database CA source is invalid")
		}
		canonical = append(canonical, pem.EncodeToMemory(block)...)
		count++
		remaining = rest
	}
	if count == 0 || !bytes.Equal(canonical, raw) {
		return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation database CA source is invalid")
	}
	return platformRelaySecretIsolationFile{id: id, raw: raw}, nil
}

func platformRelaySecretIsolationDSNFile(id string, raw []byte, equivalenceGroup string) (platformRelaySecretIsolationFile, []byte, error) {
	value := string(raw)
	parsed, err := url.Parse(value)
	if err != nil || parsed.User == nil || (parsed.Scheme != "postgres" && parsed.Scheme != "postgresql") ||
		value != strings.TrimSpace(value) || strings.ContainsAny(value, "\x00\r\n") ||
		model.ValidateRelayProtectedPostgresDSN(value) != nil {
		return platformRelaySecretIsolationFile{}, nil, errors.New("Relay secret isolation database source is invalid")
	}
	if model.RelayDatabaseTLSAttestationRequired() {
		query := parsed.Query()
		expectedCAPath := os.Getenv(platformRelaySecretIsolationRelayDatabaseCAEnvironment)
		if expectedCAPath == "" || len(query["sslrootcert"]) != 1 || query.Get("sslrootcert") != expectedCAPath {
			return platformRelaySecretIsolationFile{}, nil, errors.New("Relay secret isolation database source is invalid")
		}
	}
	password, present := parsed.User.Password()
	passwordBytes := []byte(password)
	if !present || model.ValidateRelayDatabasePassword(passwordBytes) != nil {
		clear(passwordBytes)
		return platformRelaySecretIsolationFile{}, nil, errors.New("Relay secret isolation database source is invalid")
	}
	target, err := platformRelaySecretIsolationDatabaseTarget(parsed, "")
	if err != nil {
		clear(passwordBytes)
		return platformRelaySecretIsolationFile{}, nil, errors.New("Relay secret isolation database source is invalid")
	}
	endpoint, err := platformRelaySecretIsolationDatabaseEndpoint(parsed, "")
	if err != nil {
		clear(passwordBytes)
		return platformRelaySecretIsolationFile{}, nil, errors.New("Relay secret isolation database source is invalid")
	}
	return platformRelaySecretIsolationFile{
		id: id, raw: raw,
		representations: []platformRelaySecretIsolationRepresentation{
			platformRelaySecretIsolationDigest("database."+id+".password", passwordBytes, equivalenceGroup),
			platformRelaySecretIsolationDigest("database."+id+".target", []byte(target), ""),
			platformRelaySecretIsolationDigest("database."+id+".endpoint", []byte(endpoint), ""),
		},
	}, passwordBytes, nil
}

func platformRelaySecretIsolationPasswordFile(id string, raw []byte, equivalenceGroup string) (platformRelaySecretIsolationFile, error) {
	if model.ValidateRelayDatabasePassword(raw) != nil {
		return platformRelaySecretIsolationFile{}, errors.New("Relay secret isolation database password source is invalid")
	}
	return platformRelaySecretIsolationFile{
		id: id, raw: raw,
		representations: []platformRelaySecretIsolationRepresentation{
			platformRelaySecretIsolationDigest("database."+id+".password_file", raw, equivalenceGroup),
		},
	}, nil
}

func platformRelaySecretIsolationReadValidatorSources() ([]platformRelaySecretIsolationFile, error) {
	type source struct {
		environment string
		maximum     int64
	}
	sources := map[string]source{
		"principals":          {"RELAY_SERVICE_PRINCIPALS_FILE", 512 * 1024},
		"api":                 {"RELAY_API_RUNTIME_SECRETS_FILE", 1024 * 1024},
		"edge":                {"RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE", 64 * 1024},
		"kek":                 {"RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", 64 * 1024},
		"relay_ca":            {platformRelaySecretIsolationRelayDatabaseCAEnvironment, 256 * 1024},
		"redis_tls_ca":        {PlatformRelayRedisTLSCAFileEnvironment, 256 * 1024},
		"platform_ca":         {platformRelaySecretIsolationPlatformDatabaseCAEnvironment, 256 * 1024},
		"role_admin":          {platformRelaySecretIsolationRoleAdminDSNEnvironment, 16 * 1024},
		"migration":           {platformRelaySecretIsolationMigrationDSNEnvironment, 16 * 1024},
		"runtime":             {platformRelaySecretIsolationRuntimeDSNEnvironment, 16 * 1024},
		"edge_dsn":            {platformRelaySecretIsolationEdgeDSNEnvironment, 16 * 1024},
		"migration_password":  {"RELAY_MIGRATION_DATABASE_PASSWORD_FILE", 128},
		"runtime_password":    {"RELAY_RUNTIME_DATABASE_PASSWORD_FILE", 128},
		"edge_password":       {"RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE", 128},
		"platform:role_admin": {platformRelaySecretIsolationPlatformRoleAdminDSNEnvironment, 16 * 1024},
	}
	for _, contract := range platformProcessSecretRoleContracts {
		sources["platform:"+contract.role] = source{contract.environment, platformProcessSecretMaximumBytes}
		sources["platform-password:"+contract.role] = source{contract.passwordEnvironment, platformProcessSecretMaximumLength}
	}
	raw := make(map[string][]byte, len(sources))
	clearRaw := func() {
		for _, value := range raw {
			clear(value)
		}
	}
	for id, item := range sources {
		value, err := platformRelaySecretIsolationReadFile(item.environment, item.maximum)
		if err != nil {
			clearRaw()
			return nil, err
		}
		raw[id] = value
	}
	files := make([]platformRelaySecretIsolationFile, 0, len(sources))
	appendFile := func(file platformRelaySecretIsolationFile, err error) bool {
		if err != nil {
			return false
		}
		files = append(files, file)
		return true
	}
	if file, err := platformRelaySecretIsolationPrincipalFile(raw["principals"]); !appendFile(file, err) {
		clearRaw()
		return nil, errors.New("Relay secret isolation inputs are invalid")
	}
	if file, err := platformRelaySecretIsolationAPIFile(raw["api"]); !appendFile(file, err) {
		clearRaw()
		return nil, errors.New("Relay secret isolation inputs are invalid")
	}
	if file, err := platformRelaySecretIsolationEdgeFile(raw["edge"]); !appendFile(file, err) {
		clearRaw()
		return nil, errors.New("Relay secret isolation inputs are invalid")
	}
	if file, err := platformRelaySecretIsolationKEKFile(raw["kek"]); !appendFile(file, err) {
		clearRaw()
		return nil, errors.New("Relay secret isolation inputs are invalid")
	}
	if file, err := platformRelaySecretIsolationCAFile("relay_database_ca", raw["relay_ca"]); !appendFile(file, err) {
		clearRaw()
		return nil, errors.New("Relay secret isolation inputs are invalid")
	}
	if file, err := platformRelaySecretIsolationCAFile("redis_tls_ca", raw["redis_tls_ca"]); !appendFile(file, err) {
		clearRaw()
		return nil, errors.New("Relay secret isolation inputs are invalid")
	}
	if file, err := platformRelaySecretIsolationCAFile("platform_database_ca", raw["platform_ca"]); !appendFile(file, err) {
		clearRaw()
		return nil, errors.New("Relay and Platform secret isolation inputs are invalid")
	}
	for _, contract := range platformProcessSecretRoleContracts {
		file, _, parseErr := platformProcessSecretParse(raw["platform:"+contract.role], contract.role)
		if !appendFile(file, parseErr) {
			clearRaw()
			return nil, errors.New("Relay and Platform secret isolation inputs are invalid")
		}
	}
	if file, parseErr := platformProcessSecretRoleAdminDSNFile(raw["platform:role_admin"]); !appendFile(file, parseErr) {
		clearRaw()
		return nil, errors.New("Relay and Platform secret isolation inputs are invalid")
	}
	for _, contract := range platformProcessSecretRoleContracts {
		file, parseErr := platformProcessSecretPasswordFile(raw["platform-password:"+contract.role], contract)
		if !appendFile(file, parseErr) {
			clearRaw()
			return nil, errors.New("Relay and Platform secret isolation inputs are invalid")
		}
	}
	roleAdmin, roleAdminPassword, err := platformRelaySecretIsolationDSNFile("role_admin_dsn", raw["role_admin"], "")
	if err != nil {
		clearRaw()
		return nil, err
	}
	defer clear(roleAdminPassword)
	files = append(files, roleAdmin)
	migration, migrationDSNPassword, err := platformRelaySecretIsolationDSNFile("migration_dsn", raw["migration"], "migration_database_password")
	if err != nil {
		clearRaw()
		return nil, err
	}
	defer clear(migrationDSNPassword)
	files = append(files, migration)
	runtimeFile, runtimeDSNPassword, err := platformRelaySecretIsolationDSNFile("runtime_dsn", raw["runtime"], "runtime_database_password")
	if err != nil {
		clearRaw()
		return nil, err
	}
	defer clear(runtimeDSNPassword)
	files = append(files, runtimeFile)
	edgeDSN, edgeDSNPassword, err := platformRelaySecretIsolationDSNFile("edge_dsn", raw["edge_dsn"], "edge_database_password")
	if err != nil {
		clearRaw()
		return nil, err
	}
	defer clear(edgeDSNPassword)
	files = append(files, edgeDSN)
	for _, item := range []struct{ id, rawID, group string }{
		{"migration_password", "migration_password", "migration_database_password"},
		{"runtime_password", "runtime_password", "runtime_database_password"},
		{"edge_password", "edge_password", "edge_database_password"},
	} {
		file, fileErr := platformRelaySecretIsolationPasswordFile(item.id, raw[item.rawID], item.group)
		if fileErr != nil {
			clearRaw()
			return nil, fileErr
		}
		files = append(files, file)
	}
	for _, pair := range []struct{ dsn, password []byte }{
		{migrationDSNPassword, raw["migration_password"]},
		{runtimeDSNPassword, raw["runtime_password"]},
		{edgeDSNPassword, raw["edge_password"]},
	} {
		if len(pair.dsn) != len(pair.password) || subtle.ConstantTimeCompare(pair.dsn, pair.password) != 1 {
			clearRaw()
			return nil, errors.New("Relay secret isolation database password binding is invalid")
		}
	}
	return files, nil
}

func platformRelaySecretIsolationClearFiles(files []platformRelaySecretIsolationFile) {
	for index := range files {
		clear(files[index].raw)
	}
}

func platformRelaySecretIsolationValidateRepresentations(files []platformRelaySecretIsolationFile) error {
	type seenRepresentation struct {
		group string
	}
	type seenGroup struct {
		digest   [sha256.Size]byte
		count    int
		expected int
	}
	seen := make(map[[sha256.Size]byte]seenRepresentation)
	groups := make(map[string]seenGroup)
	seenIDs := make(map[string]struct{})
	for _, file := range files {
		for _, representation := range file.representations {
			if representation.id == "" {
				return errors.New("Relay secret isolation representation is invalid")
			}
			if _, duplicateID := seenIDs[representation.id]; duplicateID {
				return errors.New("Relay secret isolation representation identity is duplicated")
			}
			seenIDs[representation.id] = struct{}{}
			if representation.equivalenceGroup == "" {
				if representation.equivalenceCount != 0 {
					return errors.New("Relay secret isolation representation is invalid")
				}
			} else {
				if representation.equivalenceCount < 2 {
					return errors.New("Relay secret isolation representation is invalid")
				}
				group, exists := groups[representation.equivalenceGroup]
				if exists {
					if group.digest != representation.digest || group.expected != representation.equivalenceCount {
						return errors.New("Relay secret isolation intentional binding does not match")
					}
					group.count++
					groups[representation.equivalenceGroup] = group
				} else {
					groups[representation.equivalenceGroup] = seenGroup{
						digest: representation.digest, count: 1, expected: representation.equivalenceCount,
					}
				}
			}
			if previous, duplicate := seen[representation.digest]; duplicate {
				if previous.group == "" || representation.equivalenceGroup == "" ||
					previous.group != representation.equivalenceGroup {
					return errors.New("Relay secret isolation found cross-domain secret reuse")
				}
				continue
			}
			seen[representation.digest] = seenRepresentation{group: representation.equivalenceGroup}
		}
	}
	for _, value := range groups {
		if value.count != value.expected {
			return errors.New("Relay secret isolation intentional binding is incomplete")
		}
	}
	return nil
}

func platformRelaySecretIsolationReceiptForConsumer(
	consumer string,
	release platformRelaySecretIsolationRelease,
	files []platformRelaySecretIsolationFile,
) (platformRelaySecretIsolationReceipt, error) {
	wanted := map[string]map[string]struct{}{
		PlatformRelaySecretIsolationConsumerPre: {
			"role_admin_dsn": {}, "migration_password": {}, "runtime_password": {}, "edge_password": {},
			"relay_database_ca": {},
		},
		PlatformRelaySecretIsolationConsumerMigrate: {"migration_dsn": {}, "provider_kek": {}, "relay_database_ca": {}},
		PlatformRelaySecretIsolationConsumerPost:    {"role_admin_dsn": {}, "relay_database_ca": {}},
		PlatformRelaySecretIsolationConsumerPrincipal: {
			"runtime_dsn": {}, "service_principals": {}, "relay_database_ca": {},
		},
		PlatformRelaySecretIsolationConsumerAPI: {
			"runtime_dsn": {}, "service_principals": {}, "api_runtime": {}, "provider_kek": {}, "relay_database_ca": {},
			"redis_tls_ca": {},
		},
		PlatformRelaySecretIsolationConsumerEdge: {"edge_dsn": {}, "edge_runtime": {}, "relay_database_ca": {}},
		PlatformRelaySecretIsolationConsumerPlatformDBRolePre: {
			"platform_role_admin_dsn":                   {},
			"platform_database_ca":                      {},
			"platform_migration_password":               {},
			"platform_api_password":                     {},
			"platform_dispatcher_password":              {},
			"platform_relay_sync_password":              {},
			"platform_timeout_worker_password":          {},
			"platform_publishing_worker_password":       {},
			"platform_download_gateway_worker_password": {},
		},
		PlatformRelaySecretIsolationConsumerPlatformMigration: {
			"platform_migration_runtime": {}, "platform_database_ca": {},
		},
		PlatformRelaySecretIsolationConsumerPlatformAPI: {
			"platform_api_runtime": {}, "platform_database_ca": {},
		},
		PlatformRelaySecretIsolationConsumerPlatformDispatcher: {
			"platform_dispatcher_runtime": {}, "platform_database_ca": {},
		},
		PlatformRelaySecretIsolationConsumerPlatformRelaySync: {
			"platform_relay_sync_runtime": {}, "platform_database_ca": {},
		},
		PlatformRelaySecretIsolationConsumerPlatformTimeoutWorker: {
			"platform_timeout_worker_runtime": {}, "platform_database_ca": {},
		},
		PlatformRelaySecretIsolationConsumerPlatformPublishingWorker: {
			"platform_publishing_worker_runtime": {}, "platform_database_ca": {},
		},
		PlatformRelaySecretIsolationConsumerPlatformDownloadGatewayWorker: {
			"platform_download_gateway_worker_runtime": {}, "platform_database_ca": {},
		},
	}
	selected, ok := wanted[consumer]
	if !ok {
		return platformRelaySecretIsolationReceipt{}, errors.New("Relay secret isolation consumer is invalid")
	}
	receipt := platformRelaySecretIsolationReceipt{
		SchemaVersion: PlatformRelaySecretIsolationReceiptSchemaVersion,
		Kind:          PlatformRelaySecretIsolationReceiptKind,
		Consumer:      consumer,
		Release:       release,
	}
	seenFileIDs := make(map[string]struct{}, len(selected))
	seenSemanticIDs := make(map[string]struct{})
	for _, file := range files {
		if _, include := selected[file.id]; !include {
			continue
		}
		if !platformRelaySecretIsolationCommitmentIDPattern.MatchString(file.id) {
			return platformRelaySecretIsolationReceipt{}, errors.New("Relay secret isolation commitment identifier is invalid")
		}
		if _, duplicate := seenFileIDs[file.id]; duplicate {
			return platformRelaySecretIsolationReceipt{}, errors.New("Relay secret isolation commitment identifier is duplicated")
		}
		seenFileIDs[file.id] = struct{}{}
		digest := sha256.Sum256(file.raw)
		receipt.Files = append(receipt.Files, platformRelaySecretIsolationCommitment{
			ID: file.id, SHA256: hex.EncodeToString(digest[:]),
		})
		for _, representation := range file.representations {
			if !platformRelaySecretIsolationCommitmentIDPattern.MatchString(representation.id) {
				return platformRelaySecretIsolationReceipt{}, errors.New("Relay secret isolation commitment identifier is invalid")
			}
			if _, duplicate := seenSemanticIDs[representation.id]; duplicate {
				return platformRelaySecretIsolationReceipt{}, errors.New("Relay secret isolation commitment identifier is duplicated")
			}
			seenSemanticIDs[representation.id] = struct{}{}
			receipt.Semantics = append(receipt.Semantics, platformRelaySecretIsolationCommitment{
				ID: representation.id, SHA256: hex.EncodeToString(representation.digest[:]),
			})
		}
	}
	sort.Slice(receipt.Files, func(i, j int) bool { return receipt.Files[i].ID < receipt.Files[j].ID })
	sort.Slice(receipt.Semantics, func(i, j int) bool { return receipt.Semantics[i].ID < receipt.Semantics[j].ID })
	if len(receipt.Files) != len(selected) {
		return platformRelaySecretIsolationReceipt{}, errors.New("Relay secret isolation consumer source set is incomplete")
	}
	return receipt, nil
}

func platformRelaySecretIsolationReceiptDirectoryEnvironment(consumer string) string {
	return platformRelaySecretIsolationReceiptDirectoryEnvironmentPrefix +
		strings.ToUpper(strings.ReplaceAll(consumer, "-", "_")) + "_DIRECTORY"
}

// platformRelaySecretIsolationRevokeOrdinaryReceipts invalidates a completed
// ordinary generation while the permanent root-proof lock is held. Root proof
// creation changes the allowed secret set, so a pre-root marker must become
// unusable before proof.json can appear. Removing both the shared marker and
// all per-consumer receipts also leaves an unambiguous fail-closed state after
// an interrupted bootstrap.
func platformRelaySecretIsolationRevokeOrdinaryReceipts() error {
	openedDirectories := make([]*platformRelaySecretIsolationReceiptDirectory, 0, len(platformRelaySecretIsolationConsumers)+1)
	defer func() {
		for _, directory := range openedDirectories {
			_ = directory.close()
		}
	}()
	openDirectory := func(path string) (*platformRelaySecretIsolationReceiptDirectory, error) {
		if path == "" || !filepath.IsAbs(path) || filepath.Clean(path) != path {
			return nil, errors.New("Relay secret isolation receipt directory is invalid")
		}
		opened, err := platformRelaySecretIsolationOpenReceiptDirectory(path)
		if err != nil {
			return nil, errors.New("Relay secret isolation receipt directory is invalid")
		}
		for _, existing := range openedDirectories {
			if os.SameFile(existing.info, opened.info) {
				_ = opened.close()
				return nil, errors.New("Relay secret isolation receipt directories must be distinct")
			}
		}
		openedDirectories = append(openedDirectories, opened)
		return opened, nil
	}

	commitDirectory, err := openDirectory(strings.TrimSpace(os.Getenv(platformRelaySecretIsolationCommitDirectoryEnvironment)))
	if err != nil {
		return err
	}
	receiptDirectories := make([]*platformRelaySecretIsolationReceiptDirectory, 0, len(platformRelaySecretIsolationConsumers))
	for _, consumer := range platformRelaySecretIsolationConsumers {
		directory, err := openDirectory(strings.TrimSpace(os.Getenv(
			platformRelaySecretIsolationReceiptDirectoryEnvironment(consumer),
		)))
		if err != nil {
			return err
		}
		receiptDirectories = append(receiptDirectories, directory)
	}
	if err := commitDirectory.remove("receipt.json"); err != nil && !errors.Is(err, os.ErrNotExist) {
		return errors.New("Relay secret isolation commit marker could not be revoked")
	}
	if err := commitDirectory.sync(); err != nil {
		return errors.New("Relay secret isolation commit directory could not be synchronized")
	}
	for _, directory := range receiptDirectories {
		if err := directory.remove("receipt.json"); err != nil && !errors.Is(err, os.ErrNotExist) {
			return errors.New("Relay secret isolation receipt could not be revoked")
		}
		if err := directory.sync(); err != nil {
			return errors.New("Relay secret isolation receipt directory could not be synchronized")
		}
	}
	return nil
}

func platformRelaySecretIsolationGenerationForValidation(
	_ platformRelaySecretIsolationRelease,
) (string, string, *platformRelaySecretIsolationFile, error) {
	generation := os.Getenv(platformRelaySecretIsolationGenerationEnvironment)
	proofPath := os.Getenv(PlatformRelayRootProofFileEnvironment)
	if proofPath == "" || proofPath != strings.TrimSpace(proofPath) ||
		!filepath.IsAbs(proofPath) || filepath.Clean(proofPath) != proofPath {
		return "", "", nil, errors.New("Relay root isolation proof path is invalid")
	}
	switch generation {
	case platformRelaySecretIsolationGenerationPreRoot:
		if raw, readErr := platformRelaySecretIsolationReadFile(
			PlatformRelayRootProofFileEnvironment,
			platformRelayRootProofMaximumBytes,
		); readErr == nil {
			clear(raw)
			return "", "", nil, errors.New("Relay secret isolation cannot downgrade an existing root proof")
		}
		if !platformRelayRootIsolationProofSourceAbsent(proofPath) {
			return "", "", nil, errors.New("Relay root isolation proof is unavailable or invalid")
		}
		return generation, "", nil, nil
	case platformRelaySecretIsolationGenerationRootProofPresent:
		proof, err := platformRelayRootIsolationReadPermanentProof()
		if err != nil {
			return "", "", nil, err
		}
		digest, err := hex.DecodeString(proof.RootPasswordSHA256)
		if err != nil || len(digest) != sha256.Size {
			clear(digest)
			return "", "", nil, errors.New("Relay root isolation proof is invalid")
		}
		var rootDigest [sha256.Size]byte
		copy(rootDigest[:], digest)
		clear(digest)
		file := &platformRelaySecretIsolationFile{
			id: "root_forbidden_proof",
			representations: []platformRelaySecretIsolationRepresentation{{
				id: "root.password", digest: rootDigest,
			}},
		}
		return generation, proof.ProofID, file, nil
	default:
		return "", "", nil, errors.New("Relay secret isolation generation is invalid")
	}
}

func platformRelaySecretIsolationConsumerAcceptsGeneration(consumer, generation, proofID string) bool {
	switch generation {
	case platformRelaySecretIsolationGenerationRootProofPresent:
		return platformRelaySecretIsolationRunIDPattern.MatchString(proofID)
	case platformRelaySecretIsolationGenerationPreRoot:
		if proofID != "" {
			return false
		}
		switch consumer {
		case PlatformRelaySecretIsolationConsumerPre,
			PlatformRelaySecretIsolationConsumerMigrate,
			PlatformRelaySecretIsolationConsumerPost:
			return true
		default:
			return false
		}
	default:
		return false
	}
}

// ValidateAndCommitPlatformRelaySecretIsolation is a network-free offline
// release gate. It reads every protected secret source once, rejects any
// cross-domain representation collision, removes stale receipts, then commits
// one least-privilege receipt per consumer on the dedicated receipt volume.
func ValidateAndCommitPlatformRelaySecretIsolation() error {
	proofLock, err := platformRelayRootIsolationAcquireProofStateLock(
		os.Getenv(PlatformRelayRootProofFileEnvironment),
	)
	if err != nil {
		return err
	}
	defer proofLock.release()

	release, err := platformRelaySecretIsolationReleaseIdentity()
	if err != nil {
		return err
	}
	generation, rootProofID, rootProofFile, err := platformRelaySecretIsolationGenerationForValidation(release)
	if err != nil {
		return err
	}
	directories := make(map[string]*platformRelaySecretIsolationReceiptDirectory, len(platformRelaySecretIsolationConsumers))
	openedDirectories := make([]*platformRelaySecretIsolationReceiptDirectory, 0, len(platformRelaySecretIsolationConsumers)+1)
	defer func() {
		for _, directory := range openedDirectories {
			_ = directory.close()
		}
	}()
	// Open and pin every directory before removing or writing anything. Distinct
	// path strings are insufficient because the same named volume can be mounted
	// at multiple aliases; identity is the opened directory's device/inode.
	openDirectory := func(directory string) (*platformRelaySecretIsolationReceiptDirectory, error) {
		if !filepath.IsAbs(directory) || filepath.Clean(directory) != directory {
			return nil, errors.New("Relay secret isolation receipt directory is invalid")
		}
		opened, openErr := platformRelaySecretIsolationOpenReceiptDirectory(directory)
		if openErr != nil {
			return nil, errors.New("Relay secret isolation receipt directory is invalid")
		}
		for _, existing := range openedDirectories {
			if os.SameFile(existing.info, opened.info) {
				_ = opened.close()
				return nil, errors.New("Relay secret isolation receipt directories must be distinct")
			}
		}
		openedDirectories = append(openedDirectories, opened)
		return opened, nil
	}
	commitDirectoryPath := strings.TrimSpace(os.Getenv(platformRelaySecretIsolationCommitDirectoryEnvironment))
	commitDirectory, err := openDirectory(commitDirectoryPath)
	if err != nil {
		return err
	}
	for _, consumer := range platformRelaySecretIsolationConsumers {
		directory := strings.TrimSpace(os.Getenv(platformRelaySecretIsolationReceiptDirectoryEnvironment(consumer)))
		opened, openErr := openDirectory(directory)
		if openErr != nil {
			return openErr
		}
		directories[consumer] = opened
	}
	// Revoke the shared commit marker first. Until a new marker is atomically
	// committed after all fourteen receipts, no complete-looking partial set can
	// be consumed after a kill or power loss.
	if removeErr := commitDirectory.remove("receipt.json"); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
		return errors.New("Relay secret isolation stale commit marker could not be removed")
	}
	if syncErr := commitDirectory.sync(); syncErr != nil {
		return errors.New("Relay secret isolation commit directory could not be synchronized")
	}
	// Remove every prior consumer receipt before reading current sources.
	for _, consumer := range platformRelaySecretIsolationConsumers {
		directory := directories[consumer]
		if removeErr := directory.remove("receipt.json"); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
			return errors.New("Relay secret isolation stale receipt could not be removed")
		}
		if syncErr := directory.sync(); syncErr != nil {
			return errors.New("Relay secret isolation receipt directory could not be synchronized")
		}
	}
	files, err := platformRelaySecretIsolationReadValidatorSources()
	if err != nil {
		return err
	}
	defer platformRelaySecretIsolationClearFiles(files)
	if rootProofFile != nil {
		files = append(files, *rootProofFile)
	}
	if err := platformRelaySecretIsolationBindPlatformContracts(files); err != nil {
		return err
	}
	if err := platformRelaySecretIsolationValidateRepresentations(files); err != nil {
		return err
	}
	var runBytes [32]byte
	if _, err := io.ReadFull(rand.Reader, runBytes[:]); err != nil {
		return errors.New("Relay secret isolation run identity could not be generated")
	}
	runID := hex.EncodeToString(runBytes[:])
	clear(runBytes[:])
	prepared := make(map[string]platformRelaySecretIsolationReceipt, len(platformRelaySecretIsolationConsumers))
	marker := platformRelaySecretIsolationCommitMarker{
		SchemaVersion: platformRelaySecretIsolationCommitSchemaVersion,
		Kind:          platformRelaySecretIsolationCommitKind,
		RunID:         runID,
		Generation:    generation,
		RootProofID:   rootProofID,
		Release:       release,
		Receipts:      make([]platformRelaySecretIsolationCommitment, 0, len(platformRelaySecretIsolationConsumers)),
	}
	for _, consumer := range platformRelaySecretIsolationConsumers {
		receipt, receiptErr := platformRelaySecretIsolationReceiptForConsumer(consumer, release, files)
		if receiptErr != nil {
			return receiptErr
		}
		receipt.RunID = runID
		encoded, encodeErr := platformRelaySecretIsolationCanonicalReceipt(receipt)
		if encodeErr != nil {
			return errors.New("Relay secret isolation receipt could not be encoded")
		}
		digest := sha256.Sum256(encoded)
		clear(encoded)
		prepared[consumer] = receipt
		marker.Receipts = append(marker.Receipts, platformRelaySecretIsolationCommitment{
			ID: consumer, SHA256: hex.EncodeToString(digest[:]),
		})
	}
	sort.Slice(marker.Receipts, func(i, j int) bool { return marker.Receipts[i].ID < marker.Receipts[j].ID })
	committed := make([]string, 0, len(platformRelaySecretIsolationConsumers))
	removeCommitted := func() {
		for _, consumer := range committed {
			directory := directories[consumer]
			_ = directory.remove("receipt.json")
			_ = directory.sync()
		}
	}
	for _, consumer := range platformRelaySecretIsolationConsumers {
		if writeErr := directories[consumer].writeReceipt(prepared[consumer]); writeErr != nil {
			removeCommitted()
			return writeErr
		}
		committed = append(committed, consumer)
		// This seam deliberately returns without cleanup to model SIGKILL after N
		// durable per-consumer renames. The absent commit marker must make every
		// such receipt unusable.
		if platformRelaySecretIsolationAfterReceiptCommit != nil {
			if interruptErr := platformRelaySecretIsolationAfterReceiptCommit(len(committed)); interruptErr != nil {
				return interruptErr
			}
		}
	}
	if writeErr := commitDirectory.writeReceipt(marker); writeErr != nil {
		removeCommitted()
		return writeErr
	}
	return nil
}

func platformRelaySecretIsolationReadConsumerSources(consumer string) ([]platformRelaySecretIsolationFile, error) {
	read := func(environment string, maximum int64) ([]byte, error) {
		return platformRelaySecretIsolationReadFile(environment, maximum)
	}
	files := make([]platformRelaySecretIsolationFile, 0, 4)
	appendParsed := func(environment string, raw []byte, file platformRelaySecretIsolationFile, err error) error {
		if err != nil {
			clear(raw)
			return err
		}
		file.environment = environment
		files = append(files, file)
		return nil
	}
	readDSN := func(environment, id string) error {
		raw, err := read(environment, 16*1024)
		if err != nil {
			return err
		}
		file, password, err := platformRelaySecretIsolationDSNFile(id, raw, "")
		clear(password)
		return appendParsed(environment, raw, file, err)
	}
	readPassword := func(environment, id string) error {
		raw, err := read(environment, 128)
		if err != nil {
			return err
		}
		file, err := platformRelaySecretIsolationPasswordFile(id, raw, "")
		return appendParsed(environment, raw, file, err)
	}
	readPrincipals := func() error {
		raw, err := read("RELAY_SERVICE_PRINCIPALS_FILE", 512*1024)
		if err != nil {
			return err
		}
		file, err := platformRelaySecretIsolationPrincipalFile(raw)
		return appendParsed("RELAY_SERVICE_PRINCIPALS_FILE", raw, file, err)
	}
	readAPI := func() error {
		raw, err := read("RELAY_API_RUNTIME_SECRETS_FILE", 1024*1024)
		if err != nil {
			return err
		}
		file, err := platformRelaySecretIsolationAPIFile(raw)
		return appendParsed("RELAY_API_RUNTIME_SECRETS_FILE", raw, file, err)
	}
	readEdge := func() error {
		raw, err := read("RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE", 64*1024)
		if err != nil {
			return err
		}
		file, err := platformRelaySecretIsolationEdgeFile(raw)
		return appendParsed("RELAY_DOWNLOAD_EDGE_RUNTIME_SECRETS_FILE", raw, file, err)
	}
	readKEK := func() error {
		raw, err := read("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", 64*1024)
		if err != nil {
			return err
		}
		file, err := platformRelaySecretIsolationKEKFile(raw)
		return appendParsed("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", raw, file, err)
	}
	readCA := func(environment, id string) error {
		raw, err := read(environment, 256*1024)
		if err != nil {
			return err
		}
		file, err := platformRelaySecretIsolationCAFile(id, raw)
		return appendParsed(environment, raw, file, err)
	}
	readPlatformRuntime := func(contract platformProcessSecretRoleContract) error {
		raw, err := read(contract.environment, platformProcessSecretMaximumBytes)
		if err != nil {
			return err
		}
		file, _, err := platformProcessSecretParse(raw, contract.role)
		return appendParsed(contract.environment, raw, file, err)
	}
	readPlatformRoleAdmin := func() error {
		raw, err := read(platformRelaySecretIsolationPlatformRoleAdminDSNEnvironment, 16*1024)
		if err != nil {
			return err
		}
		file, err := platformProcessSecretRoleAdminDSNFile(raw)
		return appendParsed(platformRelaySecretIsolationPlatformRoleAdminDSNEnvironment, raw, file, err)
	}
	readPlatformPassword := func(contract platformProcessSecretRoleContract) error {
		raw, err := read(contract.passwordEnvironment, platformProcessSecretMaximumLength)
		if err != nil {
			return err
		}
		file, err := platformProcessSecretPasswordFile(raw, contract)
		return appendParsed(contract.passwordEnvironment, raw, file, err)
	}
	fail := func(err error) ([]platformRelaySecretIsolationFile, error) {
		platformRelaySecretIsolationClearFiles(files)
		return nil, err
	}
	switch consumer {
	case PlatformRelaySecretIsolationConsumerPre:
		if err := readCA(platformRelaySecretIsolationRelayDatabaseCAEnvironment, "relay_database_ca"); err != nil {
			return fail(err)
		}
		if err := readDSN("SQL_DSN_FILE", "role_admin_dsn"); err != nil {
			return fail(err)
		}
		if err := readPassword("RELAY_MIGRATION_DATABASE_PASSWORD_FILE", "migration_password"); err != nil {
			return fail(err)
		}
		if err := readPassword("RELAY_RUNTIME_DATABASE_PASSWORD_FILE", "runtime_password"); err != nil {
			return fail(err)
		}
		if err := readPassword("RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE", "edge_password"); err != nil {
			return fail(err)
		}
	case PlatformRelaySecretIsolationConsumerMigrate:
		if err := readCA(platformRelaySecretIsolationRelayDatabaseCAEnvironment, "relay_database_ca"); err != nil {
			return fail(err)
		}
		if err := readDSN("SQL_DSN_FILE", "migration_dsn"); err != nil {
			return fail(err)
		}
		if err := readKEK(); err != nil {
			return fail(err)
		}
	case PlatformRelaySecretIsolationConsumerPost:
		if err := readCA(platformRelaySecretIsolationRelayDatabaseCAEnvironment, "relay_database_ca"); err != nil {
			return fail(err)
		}
		if err := readDSN("SQL_DSN_FILE", "role_admin_dsn"); err != nil {
			return fail(err)
		}
	case PlatformRelaySecretIsolationConsumerPrincipal:
		if err := readCA(platformRelaySecretIsolationRelayDatabaseCAEnvironment, "relay_database_ca"); err != nil {
			return fail(err)
		}
		if err := readDSN("SQL_DSN_FILE", "runtime_dsn"); err != nil {
			return fail(err)
		}
		if err := readPrincipals(); err != nil {
			return fail(err)
		}
	case PlatformRelaySecretIsolationConsumerAPI:
		if err := readCA(platformRelaySecretIsolationRelayDatabaseCAEnvironment, "relay_database_ca"); err != nil {
			return fail(err)
		}
		if err := readCA(PlatformRelayRedisTLSCAFileEnvironment, "redis_tls_ca"); err != nil {
			return fail(err)
		}
		if err := readDSN("SQL_DSN_FILE", "runtime_dsn"); err != nil {
			return fail(err)
		}
		if err := readPrincipals(); err != nil {
			return fail(err)
		}
		if err := readAPI(); err != nil {
			return fail(err)
		}
		if err := readKEK(); err != nil {
			return fail(err)
		}
	case PlatformRelaySecretIsolationConsumerEdge:
		if err := readCA(platformRelaySecretIsolationRelayDatabaseCAEnvironment, "relay_database_ca"); err != nil {
			return fail(err)
		}
		if err := readDSN("RELAY_DOWNLOAD_EDGE_SQL_DSN_FILE", "edge_dsn"); err != nil {
			return fail(err)
		}
		if err := readEdge(); err != nil {
			return fail(err)
		}
	case PlatformRelaySecretIsolationConsumerPlatformDBRolePre:
		if err := readCA(platformRelaySecretIsolationPlatformDatabaseCAEnvironment, "platform_database_ca"); err != nil {
			return fail(err)
		}
		if err := readPlatformRoleAdmin(); err != nil {
			return fail(err)
		}
		for _, contract := range platformProcessSecretRoleContracts {
			if err := readPlatformPassword(contract); err != nil {
				return fail(err)
			}
		}
	default:
		role := ""
		switch consumer {
		case PlatformRelaySecretIsolationConsumerPlatformMigration:
			role = "migration"
		case PlatformRelaySecretIsolationConsumerPlatformAPI:
			role = "platform-api"
		case PlatformRelaySecretIsolationConsumerPlatformDispatcher:
			role = "dispatcher"
		case PlatformRelaySecretIsolationConsumerPlatformRelaySync:
			role = "relay-sync"
		case PlatformRelaySecretIsolationConsumerPlatformTimeoutWorker:
			role = "timeout-worker"
		case PlatformRelaySecretIsolationConsumerPlatformPublishingWorker:
			role = "publishing-worker"
		case PlatformRelaySecretIsolationConsumerPlatformDownloadGatewayWorker:
			role = "download-gateway-registration-worker"
		}
		contract, ok := platformProcessSecretContract(role)
		if !ok {
			return nil, errors.New("Relay secret isolation consumer is invalid")
		}
		if err := readCA(platformRelaySecretIsolationPlatformDatabaseCAEnvironment, "platform_database_ca"); err != nil {
			return fail(err)
		}
		if err := readPlatformRuntime(contract); err != nil {
			return fail(err)
		}
	}
	return files, nil
}

func platformRelaySecretIsolationParseReceipt(raw []byte) (platformRelaySecretIsolationReceipt, error) {
	var receipt platformRelaySecretIsolationReceipt
	if len(raw) == 0 || common.RejectDuplicateJSONKeys(raw) != nil {
		return receipt, errors.New("Relay secret isolation receipt is invalid")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&receipt) != nil {
		return platformRelaySecretIsolationReceipt{}, errors.New("Relay secret isolation receipt is invalid")
	}
	var trailing any
	if decoder.Decode(&trailing) != io.EOF ||
		receipt.SchemaVersion != PlatformRelaySecretIsolationReceiptSchemaVersion ||
		receipt.Kind != PlatformRelaySecretIsolationReceiptKind ||
		!platformRelaySecretIsolationRunIDPattern.MatchString(receipt.RunID) {
		return platformRelaySecretIsolationReceipt{}, errors.New("Relay secret isolation receipt is invalid")
	}
	return receipt, nil
}

func platformRelaySecretIsolationParseCommitMarker(raw []byte) (platformRelaySecretIsolationCommitMarker, error) {
	var marker platformRelaySecretIsolationCommitMarker
	if len(raw) == 0 || common.RejectDuplicateJSONKeys(raw) != nil {
		return marker, errors.New("Relay secret isolation commit marker is invalid")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&marker) != nil {
		return platformRelaySecretIsolationCommitMarker{}, errors.New("Relay secret isolation commit marker is invalid")
	}
	var trailing any
	if decoder.Decode(&trailing) != io.EOF ||
		marker.SchemaVersion != platformRelaySecretIsolationCommitSchemaVersion ||
		marker.Kind != platformRelaySecretIsolationCommitKind ||
		!platformRelaySecretIsolationRunIDPattern.MatchString(marker.RunID) ||
		(marker.Generation != platformRelaySecretIsolationGenerationPreRoot &&
			marker.Generation != platformRelaySecretIsolationGenerationRootProofPresent) ||
		(marker.Generation == platformRelaySecretIsolationGenerationPreRoot && marker.RootProofID != "") ||
		(marker.Generation == platformRelaySecretIsolationGenerationRootProofPresent &&
			!platformRelaySecretIsolationRunIDPattern.MatchString(marker.RootProofID)) ||
		len(marker.Receipts) != PlatformRelaySecretIsolationConsumerCount {
		return platformRelaySecretIsolationCommitMarker{}, errors.New("Relay secret isolation commit marker is invalid")
	}
	expectedConsumers := make(map[string]struct{}, len(platformRelaySecretIsolationConsumers))
	for _, consumer := range platformRelaySecretIsolationConsumers {
		expectedConsumers[consumer] = struct{}{}
	}
	previous := ""
	for _, commitment := range marker.Receipts {
		if _, ok := expectedConsumers[commitment.ID]; !ok || commitment.ID <= previous ||
			len(commitment.SHA256) != sha256.Size*2 {
			return platformRelaySecretIsolationCommitMarker{}, errors.New("Relay secret isolation commit marker is invalid")
		}
		decoded, err := hex.DecodeString(commitment.SHA256)
		if err != nil || len(decoded) != sha256.Size || commitment.SHA256 != strings.ToLower(commitment.SHA256) {
			clear(decoded)
			return platformRelaySecretIsolationCommitMarker{}, errors.New("Relay secret isolation commit marker is invalid")
		}
		clear(decoded)
		delete(expectedConsumers, commitment.ID)
		previous = commitment.ID
	}
	if len(expectedConsumers) != 0 {
		return platformRelaySecretIsolationCommitMarker{}, errors.New("Relay secret isolation commit marker is invalid")
	}
	return marker, nil
}

func platformRelaySecretIsolationCanonicalReceipt(receipt platformRelaySecretIsolationReceipt) ([]byte, error) {
	return json.Marshal(receipt)
}

func platformRelaySecretIsolationCanonicalCommitMarker(marker platformRelaySecretIsolationCommitMarker) ([]byte, error) {
	return json.Marshal(marker)
}

// VerifyPlatformRelaySecretIsolationReceipt binds one consumer's currently
// mounted source bytes and semantic representations to the validator's current
// release receipt. It must run before that consumer opens a database, logger,
// Redis client, HTTP client, or long-lived listener.
func VerifyPlatformRelaySecretIsolationReceipt(consumer string) error {
	release, err := platformRelaySecretIsolationReleaseIdentity()
	if err != nil {
		return err
	}
	raw, err := platformRelaySecretIsolationReadFile(
		platformRelaySecretIsolationReceiptFileEnvironment,
		platformRelaySecretIsolationReceiptMaxBytes,
	)
	if err != nil {
		return errors.New("Relay secret isolation receipt is unavailable")
	}
	defer clear(raw)
	actual, err := platformRelaySecretIsolationParseReceipt(raw)
	if err != nil || actual.Consumer != consumer {
		return errors.New("Relay secret isolation receipt is invalid")
	}
	markerRaw, err := platformRelaySecretIsolationReadFile(
		platformRelaySecretIsolationCommitFileEnvironment,
		platformRelaySecretIsolationReceiptMaxBytes,
	)
	if err != nil {
		return errors.New("Relay secret isolation commit marker is unavailable")
	}
	defer clear(markerRaw)
	marker, err := platformRelaySecretIsolationParseCommitMarker(markerRaw)
	if err != nil || marker.RunID != actual.RunID || marker.Release != release || actual.Release != release {
		return errors.New("Relay secret isolation commit marker is invalid")
	}
	if !platformRelaySecretIsolationConsumerAcceptsGeneration(consumer, marker.Generation, marker.RootProofID) {
		return errors.New("Relay secret isolation generation is not valid for this consumer")
	}
	canonicalMarker, markerEncodeErr := platformRelaySecretIsolationCanonicalCommitMarker(marker)
	if markerEncodeErr != nil || len(canonicalMarker) != len(markerRaw) ||
		subtle.ConstantTimeCompare(canonicalMarker, markerRaw) != 1 {
		clear(canonicalMarker)
		return errors.New("Relay secret isolation commit marker is invalid")
	}
	clear(canonicalMarker)
	canonicalActual, actualEncodeErr := platformRelaySecretIsolationCanonicalReceipt(actual)
	if actualEncodeErr != nil || len(canonicalActual) != len(raw) ||
		subtle.ConstantTimeCompare(canonicalActual, raw) != 1 {
		clear(canonicalActual)
		return errors.New("Relay secret isolation receipt is invalid")
	}
	receiptDigest := sha256.Sum256(canonicalActual)
	clear(canonicalActual)
	markerReceiptDigest := ""
	for _, commitment := range marker.Receipts {
		if commitment.ID == consumer {
			markerReceiptDigest = commitment.SHA256
			break
		}
	}
	if markerReceiptDigest != hex.EncodeToString(receiptDigest[:]) {
		return errors.New("Relay secret isolation receipt is not committed")
	}
	files, err := platformRelaySecretIsolationReadConsumerSources(consumer)
	if err != nil {
		return err
	}
	defer platformRelaySecretIsolationClearFiles(files)
	expected, err := platformRelaySecretIsolationReceiptForConsumer(consumer, release, files)
	if err != nil {
		return err
	}
	expected.RunID = actual.RunID
	expectedRaw, expectedErr := platformRelaySecretIsolationCanonicalReceipt(expected)
	actualRaw, actualErr := platformRelaySecretIsolationCanonicalReceipt(actual)
	if expectedErr != nil || actualErr != nil || len(expectedRaw) != len(actualRaw) ||
		subtle.ConstantTimeCompare(expectedRaw, actualRaw) != 1 {
		clear(expectedRaw)
		clear(actualRaw)
		return errors.New("Relay secret isolation receipt does not match mounted sources")
	}
	clear(expectedRaw)
	clear(actualRaw)
	// Pin the receipt and shared marker alongside the source files. Database
	// release attestation is intentionally performed after the connection is
	// opened; it must still bind to the exact marker/receipt bytes verified here
	// instead of reopening host-controlled bind mounts later in startup.
	snapshots := make([]common.ProtectedSecretFileSnapshot, 0, len(files)+2)
	snapshots = append(snapshots,
		common.ProtectedSecretFileSnapshot{
			Environment: platformRelaySecretIsolationReceiptFileEnvironment,
			Value:       raw,
		},
		common.ProtectedSecretFileSnapshot{
			Environment: platformRelaySecretIsolationCommitFileEnvironment,
			Value:       markerRaw,
		},
	)
	for index := range files {
		if files[index].environment == "" || len(files[index].raw) == 0 {
			return errors.New("Relay secret isolation source snapshot is invalid")
		}
		snapshots = append(snapshots, common.ProtectedSecretFileSnapshot{
			Environment: files[index].environment,
			Value:       files[index].raw,
		})
	}
	if err := common.InstallProtectedSecretFileSnapshots(snapshots); err != nil {
		return errors.New("Relay secret isolation source snapshot could not be installed")
	}
	if err := platformRelaySecretIsolationRememberVerifiedContext(consumer, actual, marker); err != nil {
		return errors.New("Relay secret isolation verified context could not be installed")
	}
	return nil
}
