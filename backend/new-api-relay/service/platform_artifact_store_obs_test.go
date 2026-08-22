package service

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type fakePlatformHuaweiOBSClient struct {
	putBucket        string
	putObjectKey     string
	putContent       []byte
	putContentType   string
	putSizeBytes     int64
	putSHA256        string
	putMetadata      map[string]string
	putVersionID     string
	putCancel        context.CancelFunc
	head             platformHuaweiOBSHead
	headErr          error
	headVersionID    string
	deleteBucket     string
	deleteObjectKey  string
	deleteVersionID  string
	deleteErr        error
	versioningStatus string
	versioningErr    error
	signedURL        string
	signedTTL        int
	closed           bool
}

func (client *fakePlatformHuaweiOBSClient) PutObject(
	_ context.Context,
	bucket string,
	objectKey string,
	content io.Reader,
	contentType string,
	sizeBytes int64,
	sha256Digest string,
	metadata map[string]string,
) (string, error) {
	client.putBucket = bucket
	client.putObjectKey = objectKey
	client.putContentType = contentType
	client.putSizeBytes = sizeBytes
	client.putSHA256 = sha256Digest
	client.putMetadata = metadata
	if client.putCancel != nil {
		buffer := make([]byte, 1)
		read, err := content.Read(buffer)
		client.putContent = append(client.putContent, buffer[:read]...)
		if err != nil {
			return client.putVersionID, err
		}
		client.putCancel()
		_, err = content.Read(buffer)
		return client.putVersionID, err
	}
	data, err := io.ReadAll(content)
	client.putContent = data
	return client.putVersionID, err
}

func (client *fakePlatformHuaweiOBSClient) HeadObject(
	_ context.Context,
	_ string,
	_ string,
	versionID string,
) (platformHuaweiOBSHead, error) {
	client.headVersionID = versionID
	return client.head, client.headErr
}

func (client *fakePlatformHuaweiOBSClient) DeleteObject(
	_ context.Context,
	bucket string,
	objectKey string,
	versionID string,
) error {
	client.deleteBucket = bucket
	client.deleteObjectKey = objectKey
	client.deleteVersionID = versionID
	return client.deleteErr
}

func (client *fakePlatformHuaweiOBSClient) BucketVersioningStatus(
	context.Context,
	string,
) (string, error) {
	return client.versioningStatus, client.versioningErr
}

func (client *fakePlatformHuaweiOBSClient) SignedGetURL(
	_ context.Context,
	_ string,
	_ string,
	expiresSeconds int,
) (string, error) {
	client.signedTTL = expiresSeconds
	return client.signedURL, nil
}

func (client *fakePlatformHuaweiOBSClient) Healthcheck(context.Context, string) error {
	return nil
}

func (client *fakePlatformHuaweiOBSClient) Close() {
	client.closed = true
}

func TestPlatformHuaweiOBSArtifactStoreUploadsThenVerifiesHEAD(t *testing.T) {
	payload := []byte("obs artifact payload")
	digest := sha256.Sum256(payload)
	digestText := fmt.Sprintf("%x", digest)
	client := &fakePlatformHuaweiOBSClient{
		putVersionID: "obs-version-1",
		head: platformHuaweiOBSHead{
			ContentType:   "video/mp4",
			ContentLength: int64(len(payload)),
			Metadata: map[string]string{
				"x-obs-meta-sha256":     digestText,
				"x-obs-meta-size-bytes": fmt.Sprintf("%d", len(payload)),
			},
		},
		signedURL: "https://private-artifacts.obs.example/output?signature=test",
	}
	store := newPlatformHuaweiOBSArtifactStoreWithClient(client, "private-artifacts")
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)

	stored, err := store.Put(context.Background(), PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     bytes.NewReader(payload),
		ContentType: "video/mp4",
		SizeBytes:   int64(len(payload)),
		SHA256:      digestText,
	})
	require.NoError(t, err)
	assert.Equal(t, objectKey, stored.ObjectKey)
	assert.Equal(t, "obs-version-1", stored.VersionID)
	assert.Equal(t, "private-artifacts", client.putBucket)
	assert.Equal(t, objectKey, client.putObjectKey)
	assert.Equal(t, payload, client.putContent)
	assert.Equal(t, "video/mp4", client.putContentType)
	assert.Equal(t, int64(len(payload)), client.putSizeBytes)
	assert.Equal(t, digestText, client.putSHA256)
	assert.Equal(t, digestText, client.putMetadata["sha256"])
	assert.Equal(t, fmt.Sprintf("%d", len(payload)), client.putMetadata["size-bytes"])
	assert.Equal(t, "obs-version-1", client.headVersionID)

	signedURL, err := store.SignedDownloadURL(context.Background(), objectKey, 5*time.Minute)
	require.NoError(t, err)
	assert.Equal(t, client.signedURL, signedURL)
	assert.Equal(t, 300, client.signedTTL)
	require.NoError(t, store.Healthcheck(context.Background()))
	store.Close()
	assert.True(t, client.closed)
}

func TestPlatformHuaweiOBSArtifactStoreIssuesStrictStorageBinding(t *testing.T) {
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)
	now := time.Date(2027, time.January, 2, 3, 4, 5, 987000000, time.FixedZone("unexpected", 8*60*60))
	signedURL := "https://private-artifacts.obs.cn-north-4.myhuaweicloud.com/" + objectKey + "?AccessKeyId=test&Signature=secret"
	client := &fakePlatformHuaweiOBSClient{signedURL: signedURL}
	store := newPlatformHuaweiOBSArtifactStoreWithBinding(
		client,
		"obs.cn-north-4.myhuaweicloud.com",
		"private-artifacts",
		func() time.Time { return now },
	)

	issued, err := store.IssueSignedDownload(context.Background(), objectKey, 5*time.Minute)
	require.NoError(t, err)
	require.NotNil(t, issued.StorageBinding)
	assert.Equal(t, signedURL, issued.URL)
	assert.Equal(t, PlatformArtifactHuaweiOBSKind, issued.StorageBinding.Provider)
	assert.Equal(t, "obs.cn-north-4.myhuaweicloud.com", issued.StorageBinding.EndpointHost)
	assert.Equal(t, "private-artifacts", issued.StorageBinding.Bucket)
	assert.Equal(t, objectKey, issued.StorageBinding.ObjectKey)
	assert.Equal(t, time.Date(2027, time.January, 1, 19, 4, 5, 0, time.UTC), issued.StorageBinding.IssuedAt)
	assert.Equal(t, time.Date(2027, time.January, 1, 19, 9, 5, 0, time.UTC), issued.StorageBinding.ExpiresAt)
	digest := sha256.Sum256([]byte(signedURL))
	assert.Equal(t, hex.EncodeToString(digest[:]), issued.StorageBinding.URLSHA256)

	response, err := platformGenerationArtifactDownloadResponse(issued, objectKey, 300, true)
	require.NoError(t, err)
	require.NotNil(t, response.StorageBinding)
	assert.Equal(t, "2027-01-01T19:04:05+00:00", response.StorageBinding.IssuedAt)
	assert.Equal(t, "2027-01-01T19:09:05+00:00", response.StorageBinding.ExpiresAt)
	assert.Regexp(t, `^[0-9a-f]{64}$`, response.StorageBinding.URLSHA256)
	serialized, err := json.Marshal(response)
	require.NoError(t, err)
	assert.Contains(t, string(serialized), `"storage_binding":{"provider":"huawei_obs","endpoint_host":"obs.cn-north-4.myhuaweicloud.com"`)
}

func TestPlatformHuaweiOBSArtifactStoreRejectsSignedURLOutsideBinding(t *testing.T) {
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)
	tests := map[string]string{
		"different host":   "https://attacker.example/" + objectKey + "?Signature=test",
		"different object": "https://private-artifacts.obs.example/outputs/00000000-0000-4000-8000-000000000001/00000000-0000-4000-8000-000000000002/00000000-0000-4000-8000-000000000003?Signature=test",
		"explicit port":    "https://private-artifacts.obs.example:443/" + objectKey + "?Signature=test",
	}
	for name, signedURL := range tests {
		t.Run(name, func(t *testing.T) {
			store := newPlatformHuaweiOBSArtifactStoreWithBinding(
				&fakePlatformHuaweiOBSClient{signedURL: signedURL},
				"obs.example",
				"private-artifacts",
				time.Now,
			)
			_, err := store.IssueSignedDownload(context.Background(), objectKey, time.Minute)
			require.ErrorIs(t, err, ErrPlatformArtifactStore)
		})
	}
}

func TestPlatformHuaweiOBSArtifactStoreRejectsMismatchedHEAD(t *testing.T) {
	payload := []byte("obs artifact payload")
	digest := sha256.Sum256(payload)
	digestText := fmt.Sprintf("%x", digest)
	client := &fakePlatformHuaweiOBSClient{
		head: platformHuaweiOBSHead{
			ContentType:   "video/mp4",
			ContentLength: int64(len(payload) + 1),
			Metadata: map[string]string{
				"sha256":     digestText,
				"size-bytes": fmt.Sprintf("%d", len(payload)),
			},
		},
	}
	store := newPlatformHuaweiOBSArtifactStoreWithClient(client, "private-artifacts")
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)
	stored, err := store.Put(context.Background(), PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     bytes.NewReader(payload),
		ContentType: "video/mp4",
		SizeBytes:   int64(len(payload)),
		SHA256:      digestText,
	})
	require.ErrorIs(t, err, ErrPlatformArtifactIntegrity)
	assert.Equal(t, PlatformStoredArtifact{}, stored)
	assert.Equal(t, "private-artifacts", client.deleteBucket)
	assert.Equal(t, objectKey, client.deleteObjectKey)
}

func TestPlatformHuaweiOBSArtifactStoreCancelsStreamingUploadAndCleansObject(t *testing.T) {
	payload := []byte("obs artifact payload")
	digest := sha256.Sum256(payload)
	ctx, cancel := context.WithCancel(context.Background())
	client := &fakePlatformHuaweiOBSClient{putCancel: cancel}
	store := newPlatformHuaweiOBSArtifactStoreWithClient(client, "private-artifacts")
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)

	_, err = store.Put(ctx, PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     bytes.NewReader(payload),
		ContentType: "video/mp4",
		SizeBytes:   int64(len(payload)),
		SHA256:      fmt.Sprintf("%x", digest),
	})
	require.ErrorIs(t, err, context.Canceled)
	assert.Equal(t, payload[:1], client.putContent)
	assert.Equal(t, "private-artifacts", client.deleteBucket)
	assert.Equal(t, objectKey, client.deleteObjectKey)
}

func TestPlatformHuaweiOBSArtifactStoreDeleteIsValidatedAndIdempotent(t *testing.T) {
	client := &fakePlatformHuaweiOBSClient{}
	store := newPlatformHuaweiOBSArtifactStoreWithClient(client, "private-artifacts")
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)

	require.NoError(t, store.Delete(context.Background(), objectKey))
	assert.Equal(t, "private-artifacts", client.deleteBucket)
	assert.Equal(t, objectKey, client.deleteObjectKey)
	assert.Empty(t, client.deleteVersionID)
	require.NoError(t, store.DeleteVersion(context.Background(), objectKey, "obs-version-2"))
	assert.Equal(t, "obs-version-2", client.deleteVersionID)
	require.ErrorIs(t, store.Delete(context.Background(), "../outside"), ErrPlatformArtifactStore)
}

func TestPlatformHuaweiOBSArtifactStoreRefusesKeyOnlyDeleteForVersionedBucket(t *testing.T) {
	client := &fakePlatformHuaweiOBSClient{versioningStatus: "Enabled"}
	store := newPlatformHuaweiOBSArtifactStoreWithClient(client, "private-artifacts")
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)

	err = store.Delete(context.Background(), objectKey)
	require.ErrorIs(t, err, ErrPlatformArtifactConfiguration)
	assert.Empty(t, client.deleteObjectKey, "a key-only delete would only create a delete marker")
}

func TestPlatformHuaweiOBSProductionHealthFailsClosedForVersionedOrUnknownBucket(t *testing.T) {
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	for name, client := range map[string]*fakePlatformHuaweiOBSClient{
		"enabled":    {versioningStatus: "Enabled"},
		"suspended":  {versioningStatus: "Suspended"},
		"unknown":    {versioningStatus: "Unexpected"},
		"unreadable": {versioningErr: errors.New("versioning permission denied")},
	} {
		t.Run(name, func(t *testing.T) {
			store := newPlatformHuaweiOBSArtifactStoreWithClient(client, "private-artifacts")
			err := store.Healthcheck(context.Background())
			require.ErrorIs(t, err, ErrPlatformArtifactConfiguration)
		})
	}
}

func TestPlatformHuaweiOBSArtifactStoreRejectsContentBeforeUpload(t *testing.T) {
	payload := []byte("obs artifact payload")
	claimed := sha256.Sum256([]byte("same length payload!!"))
	client := &fakePlatformHuaweiOBSClient{}
	store := newPlatformHuaweiOBSArtifactStoreWithClient(client, "private-artifacts")
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)
	_, err = store.Put(context.Background(), PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     bytes.NewReader(payload),
		ContentType: "video/mp4",
		SizeBytes:   int64(len(payload)),
		SHA256:      fmt.Sprintf("%x", claimed),
	})
	require.ErrorIs(t, err, ErrPlatformArtifactIntegrity)
	assert.Empty(t, client.putObjectKey)
}

func TestPlatformHuaweiOBSConfigurationRequiresPrivateHTTPSBucket(t *testing.T) {
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	base := PlatformHuaweiOBSConfig{
		AccessKeyID:     "access-key",
		SecretAccessKey: "secret-key",
		Endpoint:        "https://obs.cn-north-4.myhuaweicloud.com",
		Bucket:          "private-artifacts",
	}
	require.NoError(t, validatePlatformHuaweiOBSConfig(base))

	invalidEndpoint := base
	invalidEndpoint.Endpoint = "http://obs.example"
	require.ErrorIs(t, validatePlatformHuaweiOBSConfig(invalidEndpoint), ErrPlatformArtifactConfiguration)
	invalidEndpoint.Endpoint = "https://obs.example:443"
	require.ErrorIs(t, validatePlatformHuaweiOBSConfig(invalidEndpoint), ErrPlatformArtifactConfiguration)
	invalidBucket := base
	invalidBucket.Bucket = "Invalid_Bucket"
	require.ErrorIs(t, validatePlatformHuaweiOBSConfig(invalidBucket), ErrPlatformArtifactConfiguration)
	invalidCredential := base
	invalidCredential.SecretAccessKey = " secret-key"
	require.ErrorIs(t, validatePlatformHuaweiOBSConfig(invalidCredential), ErrPlatformArtifactConfiguration)
	invalidCredential = base
	invalidCredential.SecurityToken = "temporary token"
	require.ErrorIs(t, validatePlatformHuaweiOBSConfig(invalidCredential), ErrPlatformArtifactConfiguration)

	endpointHost, err := platformHuaweiOBSEndpointHost("https://OBS.CN-NORTH-4.MYHUAWEICLOUD.COM./")
	require.NoError(t, err)
	assert.Equal(t, "obs.cn-north-4.myhuaweicloud.com", endpointHost)

	store, err := NewPlatformHuaweiOBSArtifactStore(PlatformHuaweiOBSConfig{
		AccessKeyID:     "access-key",
		SecretAccessKey: "secret-key",
		Endpoint:        "https://OBS.CN-NORTH-4.MYHUAWEICLOUD.COM./",
		Bucket:          "private-artifacts",
	})
	require.NoError(t, err)
	t.Cleanup(store.Close)
	assert.Equal(t, "obs.cn-north-4.myhuaweicloud.com", store.endpointHost)
	assert.Equal(t, "private-artifacts", store.bucket)
}

func TestPlatformHuaweiOBSProductionRejectsNonHuaweiEndpointBeforeClientInitialization(t *testing.T) {
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	store, err := NewPlatformHuaweiOBSArtifactStore(PlatformHuaweiOBSConfig{
		AccessKeyID:     "access-key",
		SecretAccessKey: "secret-key",
		Endpoint:        "https://obs.attacker.example",
		Bucket:          "private-artifacts",
	})
	require.Nil(t, store)
	require.ErrorIs(t, err, ErrPlatformArtifactConfiguration)
}
