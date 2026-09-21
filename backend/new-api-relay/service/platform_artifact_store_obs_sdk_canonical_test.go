package service

import (
	"context"
	"crypto/hmac"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"net/url"
	"testing"
	"time"

	"github.com/huaweicloud/huaweicloud-sdk-go-obs/obs"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPlatformHuaweiOBSSDKCanonicalSignedURLPreservesRealSignatureAndBinding(t *testing.T) {
	// SDK signing is entirely local; these are synthetic credentials and a .test
	// endpoint. No request, object write, metadata lookup, or paid API is invoked.
	const endpoint = "lab-objects.local.test"
	const bucket = "local-sdk-oracle"
	const accessKey = "LOCALORACLEACCESSKEY123"
	const secretKey = "local-sdk-oracle-secret-that-is-not-a-provider-credential"
	const objectKey = "outputs/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222/33333333-3333-4333-8333-333333333333"
	sdk, err := obs.New(accessKey, secretKey, "https://"+endpoint, obs.WithSslVerify(true), obs.WithProxyFromEnv(false))
	require.NoError(t, err)
	defer sdk.Close()
	raw, err := sdk.CreateSignedUrl(&obs.CreateSignedUrlInput{Method: obs.HttpMethodGet, Bucket: bucket, Key: objectKey, Expires: 300})
	require.NoError(t, err)
	parsedRaw, err := url.Parse(raw.SignedUrl)
	require.NoError(t, err)
	assert.Equal(t, "443", parsedRaw.Port(), "the real installed SDK emits this default port")
	canonical, err := normalizePlatformHuaweiOBSSDKSignedURL(raw.SignedUrl)
	require.NoError(t, err)
	parsed, err := url.Parse(canonical)
	require.NoError(t, err)
	assert.Empty(t, parsed.Port())
	assert.Equal(t, bucket+"."+endpoint, parsed.Host)
	assert.Equal(t, "/"+objectKey, parsed.Path)
	assert.Equal(t, parsedRaw.RawQuery, parsed.RawQuery, "canonicalization must not rewrite any signed query byte")
	expires := parsed.Query().Get("Expires")
	mac := hmac.New(sha1.New, []byte(secretKey))
	_, _ = mac.Write([]byte("GET\n\n\n" + expires + "\n/" + bucket + "/" + objectKey))
	assert.Equal(t, base64.StdEncoding.EncodeToString(mac.Sum(nil)), parsed.Query().Get("Signature"))
	assert.Equal(t, accessKey, parsed.Query().Get("AWSAccessKeyId"))

	store := newPlatformHuaweiOBSArtifactStoreWithBinding(&platformHuaweiOBSSDKClient{client: sdk}, endpoint, bucket, time.Now)
	download, err := store.IssueSignedDownload(context.Background(), objectKey, 300*time.Second)
	require.NoError(t, err)
	require.NotNil(t, download.StorageBinding)
	boundURL, err := url.Parse(download.URL)
	require.NoError(t, err)
	assert.Empty(t, boundURL.Port())
	hash := sha256.Sum256([]byte(download.URL))
	assert.Equal(t, hex.EncodeToString(hash[:]), download.StorageBinding.URLSHA256)
	assert.Equal(t, objectKey, download.StorageBinding.ObjectKey)
	assert.Equal(t, endpoint, download.StorageBinding.EndpointHost)
	assert.Equal(t, bucket, download.StorageBinding.Bucket)
	assert.Equal(t, 300*time.Second, download.StorageBinding.ExpiresAt.Sub(download.StorageBinding.IssuedAt))
	response, err := platformGenerationArtifactDownloadResponse(download, objectKey, 300, true)
	require.NoError(t, err, "the existing strict binding response accepts the SDK's canonical spelling")
	assert.Equal(t, download.URL, response.URL)
	assert.NotContains(t, response.URL, ":443")
}

func TestPlatformHuaweiOBSSDKCanonicalizationDoesNotRelaxStorageValidation(t *testing.T) {
	const endpoint = "lab-objects.local.test"
	const bucket = "local-sdk-oracle"
	const objectKey = "outputs/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222/33333333-3333-4333-8333-333333333333"
	base := "https://" + bucket + "." + endpoint
	for name, raw := range map[string]string{
		"non-default port": base + ":8443/" + objectKey + "?Signature=test",
		"HTTP":             "http://" + bucket + "." + endpoint + ":443/" + objectKey + "?Signature=test",
		"userinfo":         "https://user:password@" + bucket + "." + endpoint + ":443/" + objectKey + "?Signature=test",
		"fragment":         base + ":443/" + objectKey + "?Signature=test#fragment",
		"empty query":      base + ":443/" + objectKey,
	} {
		t.Run(name, func(t *testing.T) {
			_, err := normalizePlatformHuaweiOBSSDKSignedURL(raw)
			require.ErrorIs(t, err, ErrPlatformArtifactStore)
		})
	}
	for name, raw := range map[string]string{
		"wrong host":   "https://other.example:443/" + objectKey + "?Signature=test",
		"wrong object": base + ":443/outputs/another-object?Signature=test",
		"wrong bucket": "https://other-bucket." + endpoint + ":443/" + objectKey + "?Signature=test",
	} {
		t.Run(name, func(t *testing.T) {
			canonical, err := normalizePlatformHuaweiOBSSDKSignedURL(raw)
			require.NoError(t, err)
			require.ErrorIs(t, validatePlatformHuaweiOBSSignedDownloadURL(canonical, endpoint, bucket, objectKey), ErrPlatformArtifactStore)
		})
	}
	// Keep the existing strict fake/client contract: only the actual SDK adapter
	// receives this canonicalization. A generic explicit-port response is rejected.
	assert.ErrorIs(t, validatePlatformHuaweiOBSSignedDownloadURL(base+":443/"+objectKey+"?Signature=test", endpoint, bucket, objectKey), ErrPlatformArtifactStore)
}
