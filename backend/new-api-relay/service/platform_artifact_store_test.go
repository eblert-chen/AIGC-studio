package service

import (
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPlatformArtifactStoreBindingIDIsStableAndDestinationScoped(t *testing.T) {
	root := t.TempDir()
	first, err := NewPlatformFilesystemArtifactStore(
		root,
		"http://relay.example/internal",
		[]byte("0123456789abcdef0123456789abcdef"),
	)
	require.NoError(t, err)
	rotatedSecret, err := NewPlatformFilesystemArtifactStore(
		root,
		"http://relay.example/internal",
		[]byte("abcdef0123456789abcdef0123456789"),
	)
	require.NoError(t, err)
	otherRoot, err := NewPlatformFilesystemArtifactStore(
		t.TempDir(),
		"http://relay.example/internal",
		[]byte("0123456789abcdef0123456789abcdef"),
	)
	require.NoError(t, err)
	assert.Len(t, first.BindingID(), sha256.Size*2)
	assert.Equal(t, first.BindingID(), rotatedSecret.BindingID(), "credential rotation must preserve cleanup ownership")
	assert.NotEqual(t, first.BindingID(), otherRoot.BindingID(), "filesystem roots are distinct cleanup destinations")

	obs := newPlatformHuaweiOBSArtifactStoreWithBinding(nil, "obs.cn-north-4.myhuaweicloud.com", "relay-artifacts", time.Now)
	otherBucket := newPlatformHuaweiOBSArtifactStoreWithBinding(nil, "obs.cn-north-4.myhuaweicloud.com", "relay-artifacts-next", time.Now)
	otherEndpoint := newPlatformHuaweiOBSArtifactStoreWithBinding(nil, "obs.cn-east-3.myhuaweicloud.com", "relay-artifacts", time.Now)
	assert.NotEqual(t, obs.BindingID(), otherBucket.BindingID(), "OBS buckets are distinct cleanup destinations")
	assert.NotEqual(t, obs.BindingID(), otherEndpoint.BindingID(), "OBS endpoints are distinct cleanup destinations")
}

func TestPlatformFilesystemArtifactStorePublishesAndValidatesSignedDownload(t *testing.T) {
	root := t.TempDir()
	now := time.Unix(1_800_000_000, 0)
	store, err := newPlatformFilesystemArtifactStore(
		root,
		"http://relay.example:3000/internal",
		[]byte("0123456789abcdef0123456789abcdef"),
		func() time.Time { return now },
	)
	require.NoError(t, err)
	tenantID := uuid.NewString()
	jobID := uuid.NewString()
	assetID := uuid.NewString()
	objectKey, err := PlatformArtifactObjectKey(tenantID, jobID, assetID)
	require.NoError(t, err)
	payload := []byte("durable artifact bytes")
	digest := sha256.Sum256(payload)
	digestText := fmt.Sprintf("%x", digest)

	stored, err := store.Put(context.Background(), PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     bytes.NewReader(payload),
		ContentType: "video/mp4",
		SizeBytes:   int64(len(payload)),
		SHA256:      digestText,
	})
	require.NoError(t, err)
	assert.Equal(t, objectKey, stored.ObjectKey)
	assert.Equal(t, digestText, stored.SHA256)
	assert.Equal(t, payload, mustReadPlatformArtifactFile(t, filepath.Join(root, filepath.FromSlash(objectKey))))

	signedURL, err := store.SignedDownloadURL(context.Background(), objectKey, 5*time.Minute)
	require.NoError(t, err)
	parsed, err := url.Parse(signedURL)
	require.NoError(t, err)
	assert.Equal(t, "/internal/v1/artifacts/download", parsed.Path)
	assert.Equal(t, objectKey, parsed.Query().Get("key"))
	expires, err := strconv.ParseInt(parsed.Query().Get("expires"), 10, 64)
	require.NoError(t, err)
	assert.Equal(t, now.Unix()+300, expires)

	opened, err := store.OpenSigned(
		context.Background(),
		objectKey,
		expires,
		parsed.Query().Get("signature"),
	)
	require.NoError(t, err)
	actual, err := io.ReadAll(opened.Content)
	require.NoError(t, err)
	require.NoError(t, opened.Content.Close())
	assert.Equal(t, payload, actual)
	assert.Equal(t, "video/mp4", opened.ContentType)
	assert.Equal(t, int64(len(payload)), opened.SizeBytes)

	_, err = store.OpenSigned(context.Background(), objectKey, expires, strings.Repeat("0", sha256.Size*2))
	require.ErrorIs(t, err, ErrPlatformArtifactSignature)
	now = now.Add(6 * time.Minute)
	_, err = store.OpenSigned(context.Background(), objectKey, expires, parsed.Query().Get("signature"))
	require.ErrorIs(t, err, ErrPlatformArtifactSignature)
}

func TestPlatformFilesystemArtifactStoreRejectsConflictAndStoredCorruption(t *testing.T) {
	root := t.TempDir()
	now := time.Unix(1_800_000_000, 0)
	store, err := newPlatformFilesystemArtifactStore(
		root,
		"http://relay.example",
		[]byte("0123456789abcdef0123456789abcdef"),
		func() time.Time { return now },
	)
	require.NoError(t, err)
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)
	original := []byte("first immutable content")
	originalDigest := sha256.Sum256(original)
	_, err = store.Put(context.Background(), PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     bytes.NewReader(original),
		ContentType: "image/png",
		SizeBytes:   int64(len(original)),
		SHA256:      fmt.Sprintf("%x", originalDigest),
	})
	require.NoError(t, err)

	conflict := []byte("different content")
	conflictDigest := sha256.Sum256(conflict)
	_, err = store.Put(context.Background(), PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     bytes.NewReader(conflict),
		ContentType: "image/png",
		SizeBytes:   int64(len(conflict)),
		SHA256:      fmt.Sprintf("%x", conflictDigest),
	})
	require.ErrorIs(t, err, ErrPlatformArtifactStore)
	assert.Equal(t, original, mustReadPlatformArtifactFile(t, filepath.Join(root, filepath.FromSlash(objectKey))))

	signedURL, err := store.SignedDownloadURL(context.Background(), objectKey, 5*time.Minute)
	require.NoError(t, err)
	parsed, err := url.Parse(signedURL)
	require.NoError(t, err)
	artifactPath := filepath.Join(root, filepath.FromSlash(objectKey))
	require.NoError(t, os.WriteFile(artifactPath, []byte("corrupted"), 0o600))
	expires, err := strconv.ParseInt(parsed.Query().Get("expires"), 10, 64)
	require.NoError(t, err)
	_, err = store.OpenSigned(context.Background(), objectKey, expires, parsed.Query().Get("signature"))
	require.ErrorIs(t, err, ErrPlatformArtifactIntegrity)
}

func TestPlatformFilesystemArtifactStoreDeletesUnpublishedObjectIdempotently(t *testing.T) {
	root := t.TempDir()
	store, err := NewPlatformFilesystemArtifactStore(
		root,
		"http://relay.example",
		[]byte("0123456789abcdef0123456789abcdef"),
	)
	require.NoError(t, err)
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)
	payload := []byte("unpublished artifact")
	digest := sha256.Sum256(payload)
	_, err = store.Put(context.Background(), PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     bytes.NewReader(payload),
		ContentType: "video/mp4",
		SizeBytes:   int64(len(payload)),
		SHA256:      fmt.Sprintf("%x", digest),
	})
	require.NoError(t, err)

	require.NoError(t, store.Delete(context.Background(), objectKey))
	require.NoError(t, store.Delete(context.Background(), objectKey))
	_, err = store.SignedDownloadURL(context.Background(), objectKey, time.Minute)
	require.ErrorIs(t, err, ErrPlatformArtifactNotFound)
	_, contentErr := os.Stat(filepath.Join(root, filepath.FromSlash(objectKey)))
	assert.True(t, os.IsNotExist(contentErr))
	_, metadataErr := os.Stat(filepath.Join(root, filepath.FromSlash(objectKey)+".meta.json"))
	assert.True(t, os.IsNotExist(metadataErr))
}

func TestPlatformArtifactObjectKeyRejectsNonCanonicalOrEscapingIdentifiers(t *testing.T) {
	valid := uuid.NewString()
	tests := []string{
		"outputs/../" + valid + "/" + valid,
		"outputs/" + valid + "/" + valid + "/" + valid + "/extra",
		"outputs/" + valid + "/" + valid + "/" + strings.ToUpper(valid),
		"outputs\\" + valid + "\\" + valid + "\\" + valid,
	}
	for _, objectKey := range tests {
		err := ValidatePlatformArtifactObjectKey(objectKey)
		assert.ErrorIs(t, err, ErrPlatformArtifactStore, objectKey)
	}
}

func TestPlatformArtifactStoreProductionFailsClosedWithoutOBS(t *testing.T) {
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	t.Setenv("RELAY_ARTIFACT_STORE", "filesystem")
	store, err := NewPlatformArtifactStoreFromEnvironment()
	require.ErrorIs(t, err, ErrPlatformArtifactConfiguration)
	assert.Nil(t, store)

	t.Setenv("RELAY_ARTIFACT_STORE", "")
	store, err = NewPlatformArtifactStoreFromEnvironment()
	require.ErrorIs(t, err, ErrPlatformArtifactConfiguration)
	assert.Nil(t, store)
}

func TestPlatformArtifactDownloadResponseRequiresStorageBindingInProduction(t *testing.T) {
	issued := PlatformIssuedArtifactDownload{URL: "https://relay.example/v1/artifacts/download?signature=test"}
	objectKey, err := PlatformArtifactObjectKey(uuid.NewString(), uuid.NewString(), uuid.NewString())
	require.NoError(t, err)

	response, err := platformGenerationArtifactDownloadResponse(issued, objectKey, 300, false)
	require.NoError(t, err)
	assert.Nil(t, response.StorageBinding)
	assert.Equal(t, issued.URL, response.URL)

	_, err = platformGenerationArtifactDownloadResponse(issued, objectKey, 300, true)
	require.ErrorIs(t, err, ErrPlatformArtifactConfiguration)
}

func mustReadPlatformArtifactFile(t *testing.T, path string) []byte {
	t.Helper()
	content, err := os.ReadFile(path)
	require.NoError(t, err)
	return content
}
