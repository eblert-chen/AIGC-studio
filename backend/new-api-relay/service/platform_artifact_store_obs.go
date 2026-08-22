package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/huaweicloud/huaweicloud-sdk-go-obs/obs"
)

var (
	platformHuaweiOBSBucketPattern       = regexp.MustCompile(`^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$`)
	platformHuaweiOBSEndpointHostPattern = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$`)
)

const (
	platformHuaweiOBSConnectTimeoutSeconds = 10
	platformHuaweiOBSSocketTimeoutSeconds  = 60
	platformHuaweiOBSHeaderTimeoutSeconds  = 30
	platformHuaweiOBSMaxRetryCount         = 2
)

type PlatformHuaweiOBSConfig struct {
	AccessKeyID     string
	SecretAccessKey string
	SecurityToken   string
	Endpoint        string
	Bucket          string
}

type platformHuaweiOBSHead struct {
	ContentType   string
	ContentLength int64
	Metadata      map[string]string
}

type platformHuaweiOBSAPI interface {
	PutObject(
		ctx context.Context,
		bucket string,
		objectKey string,
		content io.Reader,
		contentType string,
		sizeBytes int64,
		sha256Digest string,
		metadata map[string]string,
	) (string, error)
	HeadObject(ctx context.Context, bucket string, objectKey string, versionID string) (platformHuaweiOBSHead, error)
	DeleteObject(ctx context.Context, bucket string, objectKey string, versionID string) error
	BucketVersioningStatus(ctx context.Context, bucket string) (string, error)
	SignedGetURL(ctx context.Context, bucket string, objectKey string, expiresSeconds int) (string, error)
	Healthcheck(ctx context.Context, bucket string) error
	Close()
}

type platformHuaweiOBSSDKClient struct {
	client *obs.ObsClient
}

type PlatformHuaweiOBSArtifactStore struct {
	client       platformHuaweiOBSAPI
	endpointHost string
	bucket       string
	clock        func() time.Time
}

func NewPlatformHuaweiOBSArtifactStore(config PlatformHuaweiOBSConfig) (*PlatformHuaweiOBSArtifactStore, error) {
	if err := validatePlatformHuaweiOBSConfig(config); err != nil {
		return nil, err
	}
	endpointHost, err := platformHuaweiOBSEndpointHost(config.Endpoint)
	if err != nil {
		return nil, err
	}
	if PlatformRelayProductionSecurityEnabled() && !platformHuaweiOBSOfficialEndpointHost(endpointHost) {
		return nil, fmt.Errorf("%w: production requires an official Huawei OBS endpoint", ErrPlatformArtifactConfiguration)
	}
	canonicalEndpoint := "https://" + endpointHost
	var client *obs.ObsClient
	if config.SecurityToken == "" {
		client, err = obs.New(
			config.AccessKeyID,
			config.SecretAccessKey,
			canonicalEndpoint,
			obs.WithSslVerify(true),
			obs.WithConnectTimeout(platformHuaweiOBSConnectTimeoutSeconds),
			obs.WithSocketTimeout(platformHuaweiOBSSocketTimeoutSeconds),
			obs.WithHeaderTimeout(platformHuaweiOBSHeaderTimeoutSeconds),
			obs.WithMaxRetryCount(platformHuaweiOBSMaxRetryCount),
			obs.WithProxyFromEnv(false),
		)
	} else {
		client, err = obs.New(
			config.AccessKeyID,
			config.SecretAccessKey,
			canonicalEndpoint,
			obs.WithSecurityToken(config.SecurityToken),
			obs.WithSslVerify(true),
			obs.WithConnectTimeout(platformHuaweiOBSConnectTimeoutSeconds),
			obs.WithSocketTimeout(platformHuaweiOBSSocketTimeoutSeconds),
			obs.WithHeaderTimeout(platformHuaweiOBSHeaderTimeoutSeconds),
			obs.WithMaxRetryCount(platformHuaweiOBSMaxRetryCount),
			obs.WithProxyFromEnv(false),
		)
	}
	if err != nil {
		return nil, fmt.Errorf("%w: Huawei OBS client could not be initialized", ErrPlatformArtifactConfiguration)
	}
	store := newPlatformHuaweiOBSArtifactStoreWithBinding(
		&platformHuaweiOBSSDKClient{client: client},
		endpointHost,
		config.Bucket,
		time.Now,
	)
	if PlatformRelayProductionSecurityEnabled() {
		ctx, cancel := context.WithTimeout(context.Background(), platformArtifactCleanupTimeout)
		validationErr := store.validatePermanentDeleteSafety(ctx)
		cancel()
		if validationErr != nil {
			store.Close()
			return nil, validationErr
		}
	}
	return store, nil
}

func newPlatformHuaweiOBSArtifactStoreWithClient(
	client platformHuaweiOBSAPI,
	bucket string,
) *PlatformHuaweiOBSArtifactStore {
	return newPlatformHuaweiOBSArtifactStoreWithBinding(client, "", bucket, time.Now)
}

func newPlatformHuaweiOBSArtifactStoreWithBinding(
	client platformHuaweiOBSAPI,
	endpointHost string,
	bucket string,
	clock func() time.Time,
) *PlatformHuaweiOBSArtifactStore {
	return &PlatformHuaweiOBSArtifactStore{
		client:       client,
		endpointHost: endpointHost,
		bucket:       bucket,
		clock:        clock,
	}
}

func (store *PlatformHuaweiOBSArtifactStore) Kind() string {
	return PlatformArtifactHuaweiOBSKind
}

func (store *PlatformHuaweiOBSArtifactStore) BindingID() string {
	return platformArtifactStoreBindingID(store.Kind(), store.endpointHost, store.bucket)
}

func (store *PlatformHuaweiOBSArtifactStore) Persistent() bool {
	return true
}

func (store *PlatformHuaweiOBSArtifactStore) Put(
	ctx context.Context,
	input PlatformArtifactPutInput,
) (PlatformStoredArtifact, error) {
	if input.Content == nil {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: artifact content is required", ErrPlatformArtifactStore)
	}
	if err := ValidatePlatformArtifactObjectKey(input.ObjectKey); err != nil {
		return PlatformStoredArtifact{}, err
	}
	if err := validatePlatformArtifactMetadata(input.ContentType, input.SizeBytes, input.SHA256); err != nil {
		return PlatformStoredArtifact{}, err
	}
	content, ok := input.Content.(io.ReadSeeker)
	if !ok {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: Huawei OBS requires replayable artifact content", ErrPlatformArtifactStore)
	}
	if _, err := content.Seek(0, io.SeekStart); err != nil {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: artifact content could not be rewound", ErrPlatformArtifactStore)
	}
	actualSize, actualDigest, err := readPlatformArtifactIntegrity(ctx, content)
	if err != nil {
		return PlatformStoredArtifact{}, err
	}
	if actualSize != input.SizeBytes || actualDigest != input.SHA256 {
		return PlatformStoredArtifact{}, ErrPlatformArtifactIntegrity
	}
	if err := ctx.Err(); err != nil {
		return PlatformStoredArtifact{}, err
	}
	if PlatformRelayProductionSecurityEnabled() {
		if err := store.validatePermanentDeleteSafety(ctx); err != nil {
			return PlatformStoredArtifact{}, err
		}
	}
	metadata := map[string]string{
		"sha256":     input.SHA256,
		"size-bytes": strconv.FormatInt(input.SizeBytes, 10),
	}
	contextContent := &platformContextReadSeeker{ctx: ctx, content: content}
	versionID, putErr := store.client.PutObject(
		ctx,
		store.bucket,
		input.ObjectKey,
		contextContent,
		input.ContentType,
		input.SizeBytes,
		input.SHA256,
		metadata,
	)
	if putErr != nil {
		// A transport or cancellation error can be ambiguous after OBS has
		// accepted bytes. This token-scoped key has not been published, so a
		// fresh-context idempotent delete is always safe.
		cleanupUnpublishedPlatformArtifactVersion(store, input.ObjectKey, versionID)
		return PlatformStoredArtifact{}, fmt.Errorf("%w: Huawei OBS upload request failed: %w", ErrPlatformArtifactStore, putErr)
	}
	if !platformHuaweiOBSVersionIDValid(versionID) {
		cleanupUnpublishedPlatformArtifactVersion(store, input.ObjectKey, versionID)
		return PlatformStoredArtifact{}, fmt.Errorf("%w: Huawei OBS returned an invalid object version", ErrPlatformArtifactStore)
	}
	head, err := store.client.HeadObject(ctx, store.bucket, input.ObjectKey, versionID)
	if err != nil {
		cleanupUnpublishedPlatformArtifactVersion(store, input.ObjectKey, versionID)
		return PlatformStoredArtifact{}, fmt.Errorf("%w: Huawei OBS object verification failed: %w", ErrPlatformArtifactStore, err)
	}
	storedSHA256 := platformHuaweiOBSMetadataValue(head.Metadata, "sha256")
	storedSize := platformHuaweiOBSMetadataValue(head.Metadata, "size-bytes")
	if head.ContentLength != input.SizeBytes ||
		head.ContentType != input.ContentType ||
		storedSHA256 != input.SHA256 ||
		storedSize != strconv.FormatInt(input.SizeBytes, 10) {
		cleanupUnpublishedPlatformArtifactVersion(store, input.ObjectKey, versionID)
		return PlatformStoredArtifact{}, fmt.Errorf("%w: Huawei OBS HEAD metadata did not match uploaded content", ErrPlatformArtifactIntegrity)
	}
	return PlatformStoredArtifact{
		ObjectKey:   input.ObjectKey,
		ContentType: input.ContentType,
		SizeBytes:   input.SizeBytes,
		SHA256:      input.SHA256,
		VersionID:   versionID,
	}, nil
}

func (store *PlatformHuaweiOBSArtifactStore) Delete(ctx context.Context, objectKey string) error {
	if err := ValidatePlatformArtifactObjectKey(objectKey); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	status, err := store.client.BucketVersioningStatus(ctx, store.bucket)
	if err != nil {
		return fmt.Errorf("%w: Huawei OBS bucket versioning status could not be verified: %w", ErrPlatformArtifactConfiguration, err)
	}
	if status != "" {
		return fmt.Errorf("%w: refusing a key-only delete for a versioned Huawei OBS bucket", ErrPlatformArtifactConfiguration)
	}
	if err := store.client.DeleteObject(ctx, store.bucket, objectKey, ""); err != nil {
		return fmt.Errorf("%w: Huawei OBS object delete failed: %w", ErrPlatformArtifactStore, err)
	}
	return nil
}

func (store *PlatformHuaweiOBSArtifactStore) DeleteVersion(
	ctx context.Context,
	objectKey string,
	versionID string,
) error {
	if err := ValidatePlatformArtifactObjectKey(objectKey); err != nil {
		return err
	}
	if versionID == "" || !platformHuaweiOBSVersionIDValid(versionID) {
		return fmt.Errorf("%w: Huawei OBS object version is invalid", ErrPlatformArtifactStore)
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := store.client.DeleteObject(ctx, store.bucket, objectKey, versionID); err != nil {
		return fmt.Errorf("%w: Huawei OBS object version delete failed: %w", ErrPlatformArtifactStore, err)
	}
	return nil
}

func (store *PlatformHuaweiOBSArtifactStore) SignedDownloadURL(
	ctx context.Context,
	objectKey string,
	ttl time.Duration,
) (string, error) {
	if err := ValidatePlatformArtifactObjectKey(objectKey); err != nil {
		return "", err
	}
	if ttl < time.Second || ttl > platformArtifactMaxSignedTTL {
		return "", fmt.Errorf("%w: signed URL expiry is outside the allowed range", ErrPlatformArtifactStore)
	}
	expiresSeconds := int(ttl / time.Second)
	signedURL, err := store.client.SignedGetURL(ctx, store.bucket, objectKey, expiresSeconds)
	if err != nil {
		return "", fmt.Errorf("%w: Huawei OBS could not create a signed URL", ErrPlatformArtifactStore)
	}
	parsed, err := url.Parse(signedURL)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil {
		return "", fmt.Errorf("%w: Huawei OBS returned an invalid signed URL", ErrPlatformArtifactStore)
	}
	return signedURL, nil
}

func (store *PlatformHuaweiOBSArtifactStore) IssueSignedDownload(
	ctx context.Context,
	objectKey string,
	ttl time.Duration,
) (PlatformIssuedArtifactDownload, error) {
	if store.clock == nil ||
		!platformHuaweiOBSEndpointHostPattern.MatchString(store.endpointHost) ||
		!platformHuaweiOBSBucketPattern.MatchString(store.bucket) {
		return PlatformIssuedArtifactDownload{}, fmt.Errorf("%w: Huawei OBS storage binding is incomplete", ErrPlatformArtifactConfiguration)
	}
	if ttl%time.Second != 0 {
		return PlatformIssuedArtifactDownload{}, fmt.Errorf("%w: signed URL expiry must use whole seconds", ErrPlatformArtifactStore)
	}
	issuedAt := store.clock().UTC().Truncate(time.Second)
	signedURL, err := store.SignedDownloadURL(ctx, objectKey, ttl)
	if err != nil {
		return PlatformIssuedArtifactDownload{}, err
	}
	if err := validatePlatformHuaweiOBSSignedDownloadURL(signedURL, store.endpointHost, store.bucket, objectKey); err != nil {
		return PlatformIssuedArtifactDownload{}, err
	}
	expiresAt := issuedAt.Add(ttl)
	urlDigest := sha256.Sum256([]byte(signedURL))
	return PlatformIssuedArtifactDownload{
		URL: signedURL,
		StorageBinding: &PlatformArtifactStorageBinding{
			Provider:     PlatformArtifactHuaweiOBSKind,
			EndpointHost: store.endpointHost,
			Bucket:       store.bucket,
			ObjectKey:    objectKey,
			IssuedAt:     issuedAt,
			ExpiresAt:    expiresAt,
			URLSHA256:    hex.EncodeToString(urlDigest[:]),
		},
	}, nil
}

func (store *PlatformHuaweiOBSArtifactStore) Healthcheck(ctx context.Context) error {
	if err := store.client.Healthcheck(ctx, store.bucket); err != nil {
		return fmt.Errorf("%w: Huawei OBS healthcheck failed", ErrPlatformArtifactStore)
	}
	if PlatformRelayProductionSecurityEnabled() {
		return store.validatePermanentDeleteSafety(ctx)
	}
	return nil
}

func (store *PlatformHuaweiOBSArtifactStore) validatePermanentDeleteSafety(ctx context.Context) error {
	status, err := store.client.BucketVersioningStatus(ctx, store.bucket)
	if err != nil {
		return fmt.Errorf("%w: Huawei OBS bucket versioning status could not be verified", ErrPlatformArtifactConfiguration)
	}
	switch status {
	case "":
		return nil
	case string(obs.VersioningStatusEnabled), string(obs.VersioningStatusSuspended):
		// Even though acknowledged Put responses include VersionId, a process
		// can die after OBS commits and before that identifier is persisted.
		// Therefore production accepts only buckets that have never enabled
		// versioning; otherwise a key-only cleanup could create a delete marker
		// while retaining the orphaned object version.
		return fmt.Errorf("%w: production artifact cleanup requires Huawei OBS bucket versioning to be unconfigured", ErrPlatformArtifactConfiguration)
	default:
		return fmt.Errorf("%w: Huawei OBS returned an unknown bucket versioning status", ErrPlatformArtifactConfiguration)
	}
}

func (store *PlatformHuaweiOBSArtifactStore) Close() {
	if store != nil && store.client != nil {
		store.client.Close()
	}
}

func (client *platformHuaweiOBSSDKClient) PutObject(
	ctx context.Context,
	bucket string,
	objectKey string,
	content io.Reader,
	contentType string,
	sizeBytes int64,
	sha256Digest string,
	metadata map[string]string,
) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	output, err := client.client.PutObject(&obs.PutObjectInput{
		PutObjectBasicInput: obs.PutObjectBasicInput{
			ObjectOperationInput: obs.ObjectOperationInput{
				Bucket:   bucket,
				Key:      objectKey,
				ACL:      obs.AclPrivate,
				Metadata: metadata,
			},
			HttpHeader:    obs.HttpHeader{ContentType: contentType},
			ContentSHA256: sha256Digest,
			ContentLength: sizeBytes,
		},
		Body: content,
	})
	if err != nil {
		return "", err
	}
	if output == nil || output.StatusCode < 200 || output.StatusCode >= 300 {
		return "", fmt.Errorf("Huawei OBS upload returned a non-success status")
	}
	return output.VersionId, ctx.Err()
}

func (client *platformHuaweiOBSSDKClient) HeadObject(
	ctx context.Context,
	bucket string,
	objectKey string,
	versionID string,
) (platformHuaweiOBSHead, error) {
	if err := ctx.Err(); err != nil {
		return platformHuaweiOBSHead{}, err
	}
	output, err := client.client.GetObjectMetadata(&obs.GetObjectMetadataInput{
		Bucket:    bucket,
		Key:       objectKey,
		VersionId: versionID,
	})
	if err != nil {
		return platformHuaweiOBSHead{}, err
	}
	if output == nil || output.StatusCode < 200 || output.StatusCode >= 300 {
		return platformHuaweiOBSHead{}, fmt.Errorf("Huawei OBS HEAD returned a non-success status")
	}
	return platformHuaweiOBSHead{
		ContentType:   output.ContentType,
		ContentLength: output.ContentLength,
		Metadata:      output.Metadata,
	}, ctx.Err()
}

func (client *platformHuaweiOBSSDKClient) DeleteObject(
	ctx context.Context,
	bucket string,
	objectKey string,
	versionID string,
) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	output, err := client.client.DeleteObject(&obs.DeleteObjectInput{
		Bucket:    bucket,
		Key:       objectKey,
		VersionId: versionID,
	})
	if err != nil {
		return err
	}
	if output == nil || output.StatusCode < 200 || output.StatusCode >= 300 {
		return fmt.Errorf("Huawei OBS delete returned a non-success status")
	}
	return ctx.Err()
}

func (client *platformHuaweiOBSSDKClient) BucketVersioningStatus(
	ctx context.Context,
	bucket string,
) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	output, err := client.client.GetBucketVersioning(bucket)
	if err != nil {
		return "", err
	}
	if output == nil || output.StatusCode < 200 || output.StatusCode >= 300 {
		return "", fmt.Errorf("Huawei OBS bucket versioning returned a non-success status")
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	return string(output.Status), nil
}

func (client *platformHuaweiOBSSDKClient) SignedGetURL(
	ctx context.Context,
	bucket string,
	objectKey string,
	expiresSeconds int,
) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	output, err := client.client.CreateSignedUrl(&obs.CreateSignedUrlInput{
		Method:  obs.HttpMethodGet,
		Bucket:  bucket,
		Key:     objectKey,
		Expires: expiresSeconds,
	})
	if err != nil {
		return "", err
	}
	if output == nil || output.SignedUrl == "" {
		return "", fmt.Errorf("Huawei OBS returned an empty signed URL")
	}
	return output.SignedUrl, ctx.Err()
}

func (client *platformHuaweiOBSSDKClient) Healthcheck(ctx context.Context, bucket string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	output, err := client.client.GetBucketMetadata(&obs.GetBucketMetadataInput{Bucket: bucket})
	if err != nil {
		return err
	}
	if output == nil || output.StatusCode < 200 || output.StatusCode >= 300 {
		return fmt.Errorf("Huawei OBS bucket metadata returned a non-success status")
	}
	return ctx.Err()
}

func (client *platformHuaweiOBSSDKClient) Close() {
	if client != nil && client.client != nil {
		client.client.Close()
		client.client = nil
	}
}

// platformContextReadSeeker makes SDK body reads observe the transfer lease
// context. The Huawei SDK does not accept a request context directly, but it
// does stream this reader and may seek it for retries.
type platformContextReadSeeker struct {
	ctx     context.Context
	content io.ReadSeeker
}

func (reader *platformContextReadSeeker) Read(buffer []byte) (int, error) {
	if err := reader.ctx.Err(); err != nil {
		return 0, err
	}
	read, err := reader.content.Read(buffer)
	if contextErr := reader.ctx.Err(); contextErr != nil {
		return read, contextErr
	}
	return read, err
}

func (reader *platformContextReadSeeker) Seek(offset int64, whence int) (int64, error) {
	if err := reader.ctx.Err(); err != nil {
		return 0, err
	}
	position, err := reader.content.Seek(offset, whence)
	if contextErr := reader.ctx.Err(); contextErr != nil {
		return position, contextErr
	}
	return position, err
}

func validatePlatformHuaweiOBSConfig(config PlatformHuaweiOBSConfig) error {
	if !platformHuaweiOBSCredentialValid(config.AccessKeyID, true) ||
		!platformHuaweiOBSCredentialValid(config.SecretAccessKey, true) ||
		!platformHuaweiOBSCredentialValid(config.SecurityToken, false) ||
		strings.TrimSpace(config.Endpoint) == "" ||
		strings.TrimSpace(config.Bucket) == "" {
		return fmt.Errorf("%w: Huawei OBS configuration is incomplete", ErrPlatformArtifactConfiguration)
	}
	if _, err := platformHuaweiOBSEndpointHost(config.Endpoint); err != nil {
		return err
	}
	if !platformHuaweiOBSBucketPattern.MatchString(config.Bucket) {
		return fmt.Errorf("%w: Huawei OBS bucket name is invalid", ErrPlatformArtifactConfiguration)
	}
	return nil
}

func platformHuaweiOBSCredentialValid(value string, required bool) bool {
	if value == "" {
		return !required
	}
	return value == strings.TrimSpace(value) && !strings.ContainsAny(value, "\r\n\x00\t ")
}

func platformHuaweiOBSOfficialEndpointHost(host string) bool {
	host = strings.ToLower(strings.TrimSuffix(host, "."))
	return strings.HasPrefix(host, "obs.") &&
		(strings.HasSuffix(host, ".myhuaweicloud.com") || strings.HasSuffix(host, ".myhuaweicloud.cn"))
}

func platformHuaweiOBSEndpointHost(rawEndpoint string) (string, error) {
	if rawEndpoint != strings.TrimSpace(rawEndpoint) {
		return "", fmt.Errorf("%w: Huawei OBS endpoint must be credential-free HTTPS without a port", ErrPlatformArtifactConfiguration)
	}
	endpoint, err := url.Parse(rawEndpoint)
	if err != nil || endpoint.Scheme != "https" || endpoint.Hostname() == "" || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" || (endpoint.Path != "" && endpoint.Path != "/") || endpoint.Port() != "" {
		return "", fmt.Errorf("%w: Huawei OBS endpoint must be credential-free HTTPS without a port", ErrPlatformArtifactConfiguration)
	}
	host := strings.ToLower(strings.TrimSuffix(endpoint.Hostname(), "."))
	if len(host) > 253 || !platformHuaweiOBSEndpointHostPattern.MatchString(host) {
		return "", fmt.Errorf("%w: Huawei OBS endpoint host is invalid", ErrPlatformArtifactConfiguration)
	}
	return host, nil
}

func validatePlatformHuaweiOBSSignedDownloadURL(
	rawURL string,
	endpointHost string,
	bucket string,
	objectKey string,
) error {
	if rawURL == "" || rawURL != strings.TrimSpace(rawURL) {
		return fmt.Errorf("%w: Huawei OBS returned an invalid signed URL", ErrPlatformArtifactStore)
	}
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil || parsed.Port() != "" || parsed.Fragment != "" || parsed.RawQuery == "" {
		return fmt.Errorf("%w: Huawei OBS returned an invalid signed URL", ErrPlatformArtifactStore)
	}
	signedHost := strings.ToLower(parsed.Hostname())
	if signedHost != parsed.Hostname() || strings.HasSuffix(signedHost, ".") {
		return fmt.Errorf("%w: Huawei OBS returned a non-canonical signed URL host", ErrPlatformArtifactStore)
	}
	expectedVirtualHost := bucket + "." + endpointHost
	expectedPath := "/" + objectKey
	pathMatches := false
	switch signedHost {
	case expectedVirtualHost:
		pathMatches = parsed.Path == expectedPath
	case endpointHost:
		pathMatches = parsed.Path == expectedPath || parsed.Path == "/"+bucket+expectedPath
	default:
		return fmt.Errorf("%w: Huawei OBS signed URL host does not match storage configuration", ErrPlatformArtifactStore)
	}
	if !pathMatches {
		return fmt.Errorf("%w: Huawei OBS signed URL path does not match the requested object", ErrPlatformArtifactStore)
	}
	return nil
}

func platformHuaweiOBSMetadataValue(metadata map[string]string, name string) string {
	for key, value := range metadata {
		normalizedKey := strings.ToLower(strings.TrimSpace(key))
		normalizedKey = strings.TrimPrefix(normalizedKey, "x-obs-meta-")
		normalizedKey = strings.TrimPrefix(normalizedKey, "x-amz-meta-")
		if normalizedKey == name {
			return value
		}
	}
	return ""
}

func platformHuaweiOBSVersionIDValid(value string) bool {
	return len(value) <= 256 && value == strings.TrimSpace(value) &&
		!strings.ContainsAny(value, "\r\n\x00\t ")
}

func NewPlatformArtifactStoreFromEnvironment() (PlatformArtifactStore, error) {
	production := PlatformRelayProductionSecurityEnabled()
	kind := strings.ToLower(strings.TrimSpace(os.Getenv("RELAY_ARTIFACT_STORE")))
	if production && kind != PlatformArtifactHuaweiOBSKind {
		return nil, fmt.Errorf("%w: production requires RELAY_ARTIFACT_STORE=huawei_obs", ErrPlatformArtifactConfiguration)
	}
	switch kind {
	case PlatformArtifactFilesystemKind:
		if production {
			return nil, fmt.Errorf("%w: filesystem storage is development-only", ErrPlatformArtifactConfiguration)
		}
		return NewPlatformFilesystemArtifactStore(
			strings.TrimSpace(os.Getenv("RELAY_ARTIFACT_FILESYSTEM_ROOT")),
			strings.TrimSpace(os.Getenv("RELAY_ARTIFACT_PUBLIC_BASE_URL")),
			[]byte(platformRelayRuntimeSecretOrEnvironment("RELAY_ARTIFACT_SIGNING_SECRET")),
		)
	case PlatformArtifactHuaweiOBSKind:
		return NewPlatformHuaweiOBSArtifactStore(PlatformHuaweiOBSConfig{
			AccessKeyID:     platformRelayRuntimeSecretOrEnvironment("HUAWEI_OBS_ACCESS_KEY_ID"),
			SecretAccessKey: platformRelayRuntimeSecretOrEnvironment("HUAWEI_OBS_SECRET_ACCESS_KEY"),
			SecurityToken:   platformRelayRuntimeSecretOrEnvironment("HUAWEI_OBS_SECURITY_TOKEN"),
			Endpoint:        strings.TrimSpace(os.Getenv("HUAWEI_OBS_ENDPOINT")),
			Bucket:          strings.TrimSpace(os.Getenv("HUAWEI_OBS_BUCKET")),
		})
	default:
		return nil, fmt.Errorf("%w: RELAY_ARTIFACT_STORE must be filesystem or huawei_obs", ErrPlatformArtifactConfiguration)
	}
}
