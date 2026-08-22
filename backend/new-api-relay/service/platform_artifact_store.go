package service

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
)

const (
	PlatformArtifactFilesystemKind = "filesystem"
	PlatformArtifactHuaweiOBSKind  = "huawei_obs"
	PlatformArtifactDownloadPath   = "/v1/artifacts/download"
	platformArtifactMaxSignedTTL   = time.Hour
	platformArtifactCleanupTimeout = 30 * time.Second
)

var (
	ErrPlatformArtifactConfiguration = errors.New("platform artifact storage configuration is invalid")
	ErrPlatformArtifactStore         = errors.New("platform artifact storage failed")
	ErrPlatformArtifactNotFound      = errors.New("platform artifact does not exist")
	ErrPlatformArtifactSignature     = errors.New("platform artifact signature is invalid")
)

var platformArtifactContentTypePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$`)

type PlatformArtifactPutInput struct {
	ObjectKey   string
	Content     io.Reader
	ContentType string
	SizeBytes   int64
	SHA256      string
}

type PlatformStoredArtifact struct {
	ObjectKey   string
	ContentType string
	SizeBytes   int64
	SHA256      string
	VersionID   string
}

type PlatformOpenedArtifact struct {
	Content     io.ReadCloser
	ContentType string
	SizeBytes   int64
	SHA256      string
}

type PlatformArtifactStorageBinding struct {
	Provider     string
	EndpointHost string
	Bucket       string
	ObjectKey    string
	IssuedAt     time.Time
	ExpiresAt    time.Time
	URLSHA256    string
}

type PlatformIssuedArtifactDownload struct {
	URL            string
	StorageBinding *PlatformArtifactStorageBinding
}

type PlatformArtifactStore interface {
	Kind() string
	BindingID() string
	Persistent() bool
	Put(ctx context.Context, input PlatformArtifactPutInput) (PlatformStoredArtifact, error)
	Delete(ctx context.Context, objectKey string) error
	IssueSignedDownload(ctx context.Context, objectKey string, ttl time.Duration) (PlatformIssuedArtifactDownload, error)
	Healthcheck(ctx context.Context) error
}

// PlatformArtifactVersionedStore is implemented only by stores that can
// permanently remove one acknowledged object version. Cleanup uses this when
// Put returned a version identifier; a bare key delete is never substituted.
type PlatformArtifactVersionedStore interface {
	DeleteVersion(ctx context.Context, objectKey string, versionID string) error
}

type PlatformArtifactTransferRequest struct {
	SourceURL         string
	TenantID          string
	JobID             string
	AssetID           string
	MediaType         string
	ExpectedSizeBytes *int64
	ExpectedSHA256    string
}

type platformFilesystemArtifactMetadata struct {
	ContentType string `json:"content_type"`
	SizeBytes   int64  `json:"size_bytes"`
	SHA256      string `json:"sha256"`
}

type PlatformFilesystemArtifactStore struct {
	root          string
	publicBaseURL *url.URL
	signingSecret []byte
	clock         func() time.Time
}

func NewPlatformFilesystemArtifactStore(
	root string,
	publicBaseURL string,
	signingSecret []byte,
) (*PlatformFilesystemArtifactStore, error) {
	return newPlatformFilesystemArtifactStore(root, publicBaseURL, signingSecret, time.Now)
}

func newPlatformFilesystemArtifactStore(
	root string,
	publicBaseURL string,
	signingSecret []byte,
	clock func() time.Time,
) (*PlatformFilesystemArtifactStore, error) {
	if !filepath.IsAbs(root) {
		return nil, fmt.Errorf("%w: filesystem root must be absolute", ErrPlatformArtifactConfiguration)
	}
	if err := os.MkdirAll(root, 0o700); err != nil {
		return nil, fmt.Errorf("%w: filesystem root could not be created", ErrPlatformArtifactConfiguration)
	}
	rootInfo, err := os.Stat(root)
	if err != nil || !rootInfo.IsDir() {
		return nil, fmt.Errorf("%w: filesystem root must be a directory", ErrPlatformArtifactConfiguration)
	}
	resolvedRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return nil, fmt.Errorf("%w: filesystem root could not be resolved", ErrPlatformArtifactConfiguration)
	}
	resolvedRoot, err = filepath.Abs(resolvedRoot)
	if err != nil {
		return nil, fmt.Errorf("%w: filesystem root could not be resolved", ErrPlatformArtifactConfiguration)
	}
	parsedBase, err := url.Parse(publicBaseURL)
	if err != nil || parsedBase.Hostname() == "" || parsedBase.User != nil || parsedBase.RawQuery != "" || parsedBase.Fragment != "" {
		return nil, fmt.Errorf("%w: public base URL must be credential-free HTTP(S)", ErrPlatformArtifactConfiguration)
	}
	if parsedBase.Scheme != "http" && parsedBase.Scheme != "https" {
		return nil, fmt.Errorf("%w: public base URL must be credential-free HTTP(S)", ErrPlatformArtifactConfiguration)
	}
	if len(signingSecret) < 32 {
		return nil, fmt.Errorf("%w: signing secret must contain at least 32 bytes", ErrPlatformArtifactConfiguration)
	}
	if clock == nil {
		return nil, fmt.Errorf("%w: clock is required", ErrPlatformArtifactConfiguration)
	}
	secretCopy := append([]byte(nil), signingSecret...)
	return &PlatformFilesystemArtifactStore{
		root:          resolvedRoot,
		publicBaseURL: parsedBase,
		signingSecret: secretCopy,
		clock:         clock,
	}, nil
}

func (store *PlatformFilesystemArtifactStore) Kind() string {
	return PlatformArtifactFilesystemKind
}

func (store *PlatformFilesystemArtifactStore) BindingID() string {
	return platformArtifactStoreBindingID(store.Kind(), store.root)
}

func (store *PlatformFilesystemArtifactStore) Persistent() bool {
	return true
}

// platformArtifactStoreBindingID identifies the immutable, non-secret
// destination of an artifact store. Credentials and signing secrets are
// deliberately excluded so they can rotate without orphaning cleanup intents.
func platformArtifactStoreBindingID(kind string, bindingParts ...string) string {
	canonical := append([]string{kind}, bindingParts...)
	digest := sha256.Sum256([]byte(strings.Join(canonical, "\x00")))
	return hex.EncodeToString(digest[:])
}

func PlatformArtifactObjectKey(tenantID string, jobID string, assetID string) (string, error) {
	objectKey := strings.Join([]string{"outputs", tenantID, jobID, assetID}, "/")
	if err := ValidatePlatformArtifactObjectKey(objectKey); err != nil {
		return "", err
	}
	return objectKey, nil
}

func ValidatePlatformArtifactObjectKey(objectKey string) error {
	parts := strings.Split(objectKey, "/")
	if len(parts) != 4 || parts[0] != "outputs" || len(objectKey) > 160 || strings.Contains(objectKey, `\`) {
		return fmt.Errorf("%w: object key is outside the outputs namespace", ErrPlatformArtifactStore)
	}
	for _, part := range parts[1:] {
		parsed, err := uuid.Parse(part)
		if err != nil || parsed.String() != part {
			return fmt.Errorf("%w: object key is outside the outputs namespace", ErrPlatformArtifactStore)
		}
	}
	return nil
}

func (store *PlatformFilesystemArtifactStore) Put(
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
	artifactPath, metadataPath, err := store.paths(input.ObjectKey, true)
	if err != nil {
		return PlatformStoredArtifact{}, err
	}

	temporaryContent, err := os.CreateTemp(filepath.Dir(artifactPath), ".artifact-*.content")
	if err != nil {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: temporary object could not be created", ErrPlatformArtifactStore)
	}
	temporaryContentPath := temporaryContent.Name()
	defer os.Remove(temporaryContentPath)
	if err := temporaryContent.Chmod(0o600); err != nil {
		_ = temporaryContent.Close()
		return PlatformStoredArtifact{}, fmt.Errorf("%w: temporary object permissions could not be set", ErrPlatformArtifactStore)
	}
	sizeBytes, digest, copyErr := copyPlatformArtifactWithIntegrity(ctx, temporaryContent, input.Content)
	if copyErr == nil {
		copyErr = temporaryContent.Sync()
	}
	closeErr := temporaryContent.Close()
	if copyErr != nil {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: object could not be written", ErrPlatformArtifactStore)
	}
	if closeErr != nil {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: object could not be closed", ErrPlatformArtifactStore)
	}
	if sizeBytes != input.SizeBytes || digest != input.SHA256 {
		return PlatformStoredArtifact{}, ErrPlatformArtifactIntegrity
	}
	if err := os.Link(temporaryContentPath, artifactPath); err != nil {
		if !os.IsExist(err) {
			return PlatformStoredArtifact{}, fmt.Errorf("%w: object could not be published", ErrPlatformArtifactStore)
		}
		if err := store.validateExistingArtifact(ctx, artifactPath, input.SizeBytes, input.SHA256); err != nil {
			return PlatformStoredArtifact{}, err
		}
	}

	metadata := platformFilesystemArtifactMetadata{
		ContentType: input.ContentType,
		SizeBytes:   input.SizeBytes,
		SHA256:      input.SHA256,
	}
	metadataBytes, err := common.Marshal(metadata)
	if err != nil {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: metadata could not be encoded", ErrPlatformArtifactStore)
	}
	temporaryMetadata, err := os.CreateTemp(filepath.Dir(metadataPath), ".artifact-*.metadata")
	if err != nil {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: temporary metadata could not be created", ErrPlatformArtifactStore)
	}
	temporaryMetadataPath := temporaryMetadata.Name()
	defer os.Remove(temporaryMetadataPath)
	if err := temporaryMetadata.Chmod(0o600); err != nil {
		_ = temporaryMetadata.Close()
		return PlatformStoredArtifact{}, fmt.Errorf("%w: temporary metadata permissions could not be set", ErrPlatformArtifactStore)
	}
	_, writeErr := temporaryMetadata.Write(metadataBytes)
	if writeErr == nil {
		writeErr = temporaryMetadata.Sync()
	}
	metadataCloseErr := temporaryMetadata.Close()
	if writeErr != nil || metadataCloseErr != nil {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: metadata could not be written", ErrPlatformArtifactStore)
	}
	if err := os.Link(temporaryMetadataPath, metadataPath); err != nil {
		if !os.IsExist(err) {
			return PlatformStoredArtifact{}, fmt.Errorf("%w: metadata could not be published", ErrPlatformArtifactStore)
		}
		existingBytes, readErr := os.ReadFile(metadataPath)
		if readErr != nil {
			return PlatformStoredArtifact{}, fmt.Errorf("%w: existing metadata could not be read", ErrPlatformArtifactStore)
		}
		var existing platformFilesystemArtifactMetadata
		if common.Unmarshal(existingBytes, &existing) != nil || existing != metadata {
			return PlatformStoredArtifact{}, fmt.Errorf("%w: existing artifact metadata conflicts", ErrPlatformArtifactStore)
		}
	}
	return PlatformStoredArtifact{
		ObjectKey:   input.ObjectKey,
		ContentType: input.ContentType,
		SizeBytes:   input.SizeBytes,
		SHA256:      input.SHA256,
	}, nil
}

// Delete removes an unpublished artifact idempotently. Metadata is removed
// first so no new signed download can be issued while the content cleanup is
// still in progress. The caller must only use this for an object key that has
// not been committed to a generation job.
func (store *PlatformFilesystemArtifactStore) Delete(ctx context.Context, objectKey string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	artifactPath, metadataPath, err := store.paths(objectKey, false)
	if errors.Is(err, ErrPlatformArtifactNotFound) {
		return nil
	}
	if err != nil {
		return err
	}
	var firstErr error
	for _, path := range []string{metadataPath, artifactPath} {
		if err := ctx.Err(); err != nil {
			return err
		}
		if err := os.Remove(path); err != nil && !os.IsNotExist(err) && firstErr == nil {
			firstErr = err
		}
	}
	if firstErr != nil {
		return fmt.Errorf("%w: unpublished filesystem object could not be removed", ErrPlatformArtifactStore)
	}
	return nil
}

func (store *PlatformFilesystemArtifactStore) SignedDownloadURL(
	ctx context.Context,
	objectKey string,
	ttl time.Duration,
) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if ttl < time.Second || ttl > platformArtifactMaxSignedTTL {
		return "", fmt.Errorf("%w: signed URL expiry is outside the allowed range", ErrPlatformArtifactStore)
	}
	artifactPath, metadataPath, err := store.paths(objectKey, false)
	if err != nil {
		return "", err
	}
	if !platformArtifactRegularFileExists(artifactPath) || !platformArtifactRegularFileExists(metadataPath) {
		return "", ErrPlatformArtifactNotFound
	}
	expires := store.clock().Unix() + int64(ttl/time.Second)
	signedURL := *store.publicBaseURL
	signedURL.Path = strings.TrimRight(signedURL.Path, "/") + PlatformArtifactDownloadPath
	query := signedURL.Query()
	query.Set("key", objectKey)
	query.Set("expires", strconv.FormatInt(expires, 10))
	query.Set("signature", store.signature(objectKey, expires))
	signedURL.RawQuery = query.Encode()
	return signedURL.String(), nil
}

func (store *PlatformFilesystemArtifactStore) IssueSignedDownload(
	ctx context.Context,
	objectKey string,
	ttl time.Duration,
) (PlatformIssuedArtifactDownload, error) {
	signedURL, err := store.SignedDownloadURL(ctx, objectKey, ttl)
	if err != nil {
		return PlatformIssuedArtifactDownload{}, err
	}
	return PlatformIssuedArtifactDownload{URL: signedURL}, nil
}

func (store *PlatformFilesystemArtifactStore) OpenSigned(
	ctx context.Context,
	objectKey string,
	expires int64,
	signature string,
) (*PlatformOpenedArtifact, error) {
	if err := ValidatePlatformArtifactObjectKey(objectKey); err != nil {
		return nil, ErrPlatformArtifactSignature
	}
	if len(signature) != sha256.Size*2 {
		return nil, ErrPlatformArtifactSignature
	}
	if _, err := hex.DecodeString(signature); err != nil {
		return nil, ErrPlatformArtifactSignature
	}
	now := store.clock().Unix()
	if expires <= now || expires-now > int64(platformArtifactMaxSignedTTL/time.Second) {
		return nil, ErrPlatformArtifactSignature
	}
	expectedSignature, err := hex.DecodeString(store.signature(objectKey, expires))
	if err != nil {
		return nil, ErrPlatformArtifactSignature
	}
	providedSignature, err := hex.DecodeString(signature)
	if err != nil || !hmac.Equal(expectedSignature, providedSignature) {
		return nil, ErrPlatformArtifactSignature
	}
	artifactPath, metadataPath, err := store.paths(objectKey, false)
	if err != nil {
		if errors.Is(err, ErrPlatformArtifactNotFound) {
			return nil, err
		}
		return nil, fmt.Errorf("%w: artifact path is invalid", ErrPlatformArtifactStore)
	}
	metadataBytes, err := os.ReadFile(metadataPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, ErrPlatformArtifactNotFound
		}
		return nil, fmt.Errorf("%w: metadata could not be read", ErrPlatformArtifactStore)
	}
	var metadata platformFilesystemArtifactMetadata
	if common.Unmarshal(metadataBytes, &metadata) != nil || validatePlatformArtifactMetadata(metadata.ContentType, metadata.SizeBytes, metadata.SHA256) != nil {
		return nil, fmt.Errorf("%w: artifact metadata is invalid", ErrPlatformArtifactStore)
	}
	content, err := os.Open(artifactPath)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, ErrPlatformArtifactNotFound
		}
		return nil, fmt.Errorf("%w: artifact could not be opened", ErrPlatformArtifactStore)
	}
	sizeBytes, digest, err := readPlatformArtifactIntegrity(ctx, content)
	if err != nil {
		_ = content.Close()
		return nil, err
	}
	if sizeBytes != metadata.SizeBytes || digest != metadata.SHA256 {
		_ = content.Close()
		return nil, fmt.Errorf("%w: stored artifact did not match metadata", ErrPlatformArtifactIntegrity)
	}
	return &PlatformOpenedArtifact{
		Content:     content,
		ContentType: metadata.ContentType,
		SizeBytes:   metadata.SizeBytes,
		SHA256:      metadata.SHA256,
	}, nil
}

func (store *PlatformFilesystemArtifactStore) Healthcheck(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(store.root, ".health-*")
	if err != nil {
		return fmt.Errorf("%w: filesystem root is not writable", ErrPlatformArtifactStore)
	}
	temporaryPath := temporary.Name()
	if closeErr := temporary.Close(); closeErr != nil {
		_ = os.Remove(temporaryPath)
		return fmt.Errorf("%w: filesystem health file could not be closed", ErrPlatformArtifactStore)
	}
	if err := os.Remove(temporaryPath); err != nil {
		return fmt.Errorf("%w: filesystem health file could not be removed", ErrPlatformArtifactStore)
	}
	return nil
}

func (store *PlatformFilesystemArtifactStore) signature(objectKey string, expires int64) string {
	message := []byte("v1\n" + objectKey + "\n" + strconv.FormatInt(expires, 10))
	digest := hmac.New(sha256.New, store.signingSecret)
	_, _ = digest.Write(message)
	return hex.EncodeToString(digest.Sum(nil))
}

func (store *PlatformFilesystemArtifactStore) paths(
	objectKey string,
	createParents bool,
) (string, string, error) {
	if err := ValidatePlatformArtifactObjectKey(objectKey); err != nil {
		return "", "", err
	}
	parts := strings.Split(objectKey, "/")
	current := store.root
	for _, part := range parts[:len(parts)-1] {
		candidate := filepath.Join(current, part)
		info, err := os.Lstat(candidate)
		if os.IsNotExist(err) && createParents {
			if mkdirErr := os.Mkdir(candidate, 0o700); mkdirErr != nil && !os.IsExist(mkdirErr) {
				return "", "", fmt.Errorf("%w: artifact directory could not be created", ErrPlatformArtifactStore)
			}
			info, err = os.Lstat(candidate)
		}
		if os.IsNotExist(err) {
			return "", "", ErrPlatformArtifactNotFound
		}
		if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.IsDir() {
			return "", "", fmt.Errorf("%w: artifact storage path is invalid", ErrPlatformArtifactStore)
		}
		resolved, err := filepath.EvalSymlinks(candidate)
		if err != nil || !platformArtifactPathWithinRoot(store.root, resolved) {
			return "", "", fmt.Errorf("%w: artifact storage path escaped its root", ErrPlatformArtifactStore)
		}
		current = candidate
	}
	artifactPath := filepath.Join(current, parts[len(parts)-1])
	metadataPath := artifactPath + ".meta.json"
	for _, candidate := range []string{artifactPath, metadataPath} {
		info, err := os.Lstat(candidate)
		if err == nil && info.Mode()&os.ModeSymlink != 0 {
			return "", "", fmt.Errorf("%w: symbolic links are forbidden", ErrPlatformArtifactStore)
		}
		if err != nil && !os.IsNotExist(err) {
			return "", "", fmt.Errorf("%w: artifact storage path is invalid", ErrPlatformArtifactStore)
		}
	}
	return artifactPath, metadataPath, nil
}

func (store *PlatformFilesystemArtifactStore) validateExistingArtifact(
	ctx context.Context,
	artifactPath string,
	sizeBytes int64,
	digest string,
) error {
	info, err := os.Lstat(artifactPath)
	if err != nil || info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return fmt.Errorf("%w: existing artifact target is invalid", ErrPlatformArtifactStore)
	}
	content, err := os.Open(artifactPath)
	if err != nil {
		return fmt.Errorf("%w: existing artifact could not be read", ErrPlatformArtifactStore)
	}
	defer content.Close()
	actualSize, actualDigest, err := readPlatformArtifactIntegrity(ctx, content)
	if err != nil {
		return err
	}
	if actualSize != sizeBytes || actualDigest != digest {
		return fmt.Errorf("%w: existing artifact conflicts with requested content", ErrPlatformArtifactStore)
	}
	return nil
}

func TransferPlatformProviderArtifact(
	ctx context.Context,
	downloader *PlatformArtifactDownloader,
	store PlatformArtifactStore,
	request PlatformArtifactTransferRequest,
) (PlatformStoredArtifact, error) {
	if downloader == nil || store == nil {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: artifact transfer dependencies are unavailable", ErrPlatformArtifactConfiguration)
	}
	objectKey, err := PlatformArtifactObjectKey(request.TenantID, request.JobID, request.AssetID)
	if err != nil {
		return PlatformStoredArtifact{}, err
	}
	downloaded, err := downloader.Download(ctx, request.SourceURL, PlatformArtifactDownloadExpectation{
		SizeBytes: request.ExpectedSizeBytes,
		SHA256:    request.ExpectedSHA256,
	})
	if err != nil {
		return PlatformStoredArtifact{}, err
	}
	defer downloaded.Close()
	if request.MediaType != "image" && request.MediaType != "video" {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: artifact media type is invalid", ErrPlatformArtifactDownload)
	}
	if !strings.HasPrefix(downloaded.ContentType, request.MediaType+"/") {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: provider MIME type did not match media type", ErrPlatformArtifactDownload)
	}
	stored, err := store.Put(ctx, PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     downloaded.Content,
		ContentType: downloaded.ContentType,
		SizeBytes:   downloaded.SizeBytes,
		SHA256:      downloaded.SHA256,
	})
	if err != nil {
		return PlatformStoredArtifact{}, err
	}
	if stored.ObjectKey != objectKey || stored.SizeBytes != downloaded.SizeBytes || stored.SHA256 != downloaded.SHA256 {
		return PlatformStoredArtifact{}, fmt.Errorf("%w: durable storage metadata did not match", ErrPlatformArtifactIntegrity)
	}
	return stored, nil
}

// cleanupUnpublishedPlatformArtifact deliberately uses a fresh bounded
// context. Transfer lease loss normally cancels the upload context, but that
// cancellation must not prevent removal of the token-scoped object that can
// no longer be published. Cleanup failure never masks the primary fenced or
// transfer error and is surfaced only through secret-free operational logs.
func cleanupUnpublishedPlatformArtifact(store PlatformArtifactStore, objectKey string) {
	cleanupUnpublishedPlatformArtifactVersion(store, objectKey, "")
}

func cleanupUnpublishedPlatformArtifactVersion(
	store PlatformArtifactStore,
	objectKey string,
	versionID string,
) {
	if store == nil || objectKey == "" {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), platformArtifactCleanupTimeout)
	defer cancel()
	var err error
	if versionID != "" {
		versionedStore, ok := store.(PlatformArtifactVersionedStore)
		if !ok {
			err = fmt.Errorf("%w: artifact store cannot delete the acknowledged object version", ErrPlatformArtifactStore)
		} else {
			err = versionedStore.DeleteVersion(ctx, objectKey, versionID)
		}
	} else {
		err = store.Delete(ctx, objectKey)
	}
	if err != nil {
		common.SysError("unpublished platform artifact cleanup failed for " + store.Kind() + ": " + err.Error())
	}
}

func validatePlatformArtifactMetadata(contentType string, sizeBytes int64, digest string) error {
	if len(contentType) > 127 || !platformArtifactContentTypePattern.MatchString(contentType) {
		return fmt.Errorf("%w: artifact content type is invalid", ErrPlatformArtifactStore)
	}
	if sizeBytes < 0 {
		return fmt.Errorf("%w: artifact size is invalid", ErrPlatformArtifactStore)
	}
	normalized, err := normalizePlatformArtifactSHA256(digest)
	if err != nil || normalized != digest {
		return fmt.Errorf("%w: artifact digest is invalid", ErrPlatformArtifactStore)
	}
	return nil
}

func copyPlatformArtifactWithIntegrity(
	ctx context.Context,
	destination io.Writer,
	source io.Reader,
) (int64, string, error) {
	digest := sha256.New()
	destinationWithDigest := io.MultiWriter(destination, digest)
	buffer := make([]byte, platformArtifactCopyBufferSize)
	var sizeBytes int64
	for {
		if err := ctx.Err(); err != nil {
			return 0, "", err
		}
		readBytes, readErr := source.Read(buffer)
		if readBytes > 0 {
			written, writeErr := destinationWithDigest.Write(buffer[:readBytes])
			sizeBytes += int64(written)
			if writeErr != nil {
				return 0, "", writeErr
			}
			if written != readBytes {
				return 0, "", io.ErrShortWrite
			}
		}
		if errors.Is(readErr, io.EOF) {
			return sizeBytes, hex.EncodeToString(digest.Sum(nil)), nil
		}
		if readErr != nil {
			return 0, "", readErr
		}
	}
}

func readPlatformArtifactIntegrity(ctx context.Context, content io.ReadSeeker) (int64, string, error) {
	if _, err := content.Seek(0, io.SeekStart); err != nil {
		return 0, "", fmt.Errorf("%w: artifact could not be rewound", ErrPlatformArtifactStore)
	}
	sizeBytes, digest, err := copyPlatformArtifactWithIntegrity(ctx, io.Discard, content)
	if err != nil {
		return 0, "", fmt.Errorf("%w: artifact integrity could not be read", ErrPlatformArtifactStore)
	}
	if _, err := content.Seek(0, io.SeekStart); err != nil {
		return 0, "", fmt.Errorf("%w: artifact could not be rewound", ErrPlatformArtifactStore)
	}
	return sizeBytes, digest, nil
}

func platformArtifactRegularFileExists(path string) bool {
	info, err := os.Lstat(path)
	return err == nil && info.Mode()&os.ModeSymlink == 0 && info.Mode().IsRegular()
}

func platformArtifactPathWithinRoot(root string, candidate string) bool {
	relative, err := filepath.Rel(root, candidate)
	if err != nil {
		return false
	}
	return relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) && !filepath.IsAbs(relative)
}
