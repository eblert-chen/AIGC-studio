//go:build integration && obs_live

package platformobs_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/service"
	"github.com/google/uuid"
	"github.com/huaweicloud/huaweicloud-sdk-go-obs/obs"
	"github.com/stretchr/testify/require"
)

var (
	obsLiveSourceSnapshotSHA1Pattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
	obsLivePrefixedSHA256Pattern     = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	obsLiveDigestPattern             = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

const (
	obsLiveSourceRevisionAttestation = "candidate-image-label-compiled-binary-and-host-snapshot-bound-v2"
	obsLiveHostRunnerAttestation     = "host-image-label-compiled-binary-and-frozen-snapshot-verification-v2"
)

type obsLiveCleanupEvidence struct {
	Strategy            string `json:"strategy"`
	DeleteStatus        int    `json:"delete_status"`
	PostDeleteStatus    int    `json:"post_delete_status"`
	CompletedAtUTC      string `json:"completed_at_utc"`
	ExactObjectVerified bool   `json:"exact_object_verified"`
}

type obsLiveEvidence struct {
	SchemaVersion                 int                    `json:"schema_version"`
	Kind                          string                 `json:"kind"`
	Status                        string                 `json:"status"`
	EvidenceID                    string                 `json:"evidence_id"`
	SourceRevision                string                 `json:"source_revision"`
	SourceRevisionAttestation     string                 `json:"source_revision_attestation"`
	SourceSnapshotSHA256          string                 `json:"source_snapshot_sha256"`
	SourceSnapshotFileCount       int                    `json:"source_snapshot_file_count"`
	HarnessSourceSnapshotSHA256   string                 `json:"harness_source_snapshot_sha256"`
	HarnessSourceSnapshotFiles    int                    `json:"harness_source_snapshot_file_count"`
	ImageSourceLabelsVerified     bool                   `json:"image_source_labels_verified"`
	ImageCompiledIdentityVerified bool                   `json:"image_compiled_identity_verified"`
	ContainerImageDigest          string                 `json:"container_image_digest"`
	EndpointHost                  string                 `json:"endpoint_host"`
	BucketSHA256                  string                 `json:"bucket_sha256"`
	ObjectKey                     string                 `json:"object_key"`
	SizeBytes                     int64                  `json:"size_bytes"`
	ArtifactSHA256                string                 `json:"artifact_sha256"`
	AnonymousGetStatus            int                    `json:"anonymous_get_status"`
	SignedGetStatus               int                    `json:"signed_get_status"`
	StartedAtUTC                  string                 `json:"started_at_utc"`
	PutCompletedAtUTC             string                 `json:"put_completed_at_utc"`
	AnonymousProbeCompletedAtUTC  string                 `json:"anonymous_probe_completed_at_utc"`
	SignedTransferCompletedAtUTC  string                 `json:"signed_transfer_completed_at_utc"`
	Cleanup                       obsLiveCleanupEvidence `json:"cleanup"`
	EvidenceCreatedAtUTC          string                 `json:"evidence_created_at_utc"`
	Verified                      bool                   `json:"verified"`
}

func validateOBSLiveProvenance(sourceRevision, sourceSnapshotSHA256 string, sourceSnapshotFileCount int, harnessSnapshotSHA256 string, harnessSnapshotFileCount int, imageDigest string) error {
	if !obsLiveSourceSnapshotSHA1Pattern.MatchString(sourceRevision) {
		return errors.New("OBS live source revision must be the 40-character lowercase SHA-1 of the frozen Relay source snapshot")
	}
	if !obsLivePrefixedSHA256Pattern.MatchString(sourceSnapshotSHA256) {
		return errors.New("OBS live source snapshot SHA-256 must use immutable sha256:<64-lowercase-hex> form")
	}
	if sourceSnapshotFileCount < 1 {
		return errors.New("OBS live source snapshot file count must be positive")
	}
	if !obsLivePrefixedSHA256Pattern.MatchString(harnessSnapshotSHA256) || harnessSnapshotFileCount < 1 {
		return errors.New("OBS live harness snapshot must have an immutable SHA-256 and positive file count")
	}
	if !obsLivePrefixedSHA256Pattern.MatchString(imageDigest) {
		return errors.New("OBS live image digest must be an immutable sha256 digest")
	}
	return nil
}

func obsLiveSensitiveVariants(value string) []string {
	if value == "" {
		return nil
	}
	variants := []string{value, url.QueryEscape(value)}
	variants = append(variants,
		base64.StdEncoding.EncodeToString([]byte(value)),
		base64.RawStdEncoding.EncodeToString([]byte(value)),
		base64.URLEncoding.EncodeToString([]byte(value)),
		base64.RawURLEncoding.EncodeToString([]byte(value)),
	)
	if escaped, err := json.Marshal(value); err == nil {
		variants = append(variants, string(escaped))
	}
	return variants
}

func scanOBSLiveEvidence(payload []byte, config service.PlatformHuaweiOBSConfig, signedURL string) error {
	text := string(payload)
	for _, secret := range []string{config.AccessKeyID, config.SecretAccessKey, config.SecurityToken, signedURL} {
		if len(secret) < 8 {
			continue
		}
		for _, variant := range obsLiveSensitiveVariants(secret) {
			if variant != "" && strings.Contains(text, variant) {
				return errors.New("OBS live evidence contains credential or signed URL material")
			}
		}
	}
	if config.Bucket != "" && strings.Contains(text, `"`+config.Bucket+`"`) {
		return errors.New("OBS live evidence contains the raw bucket name")
	}
	lower := strings.ToLower(text)
	for _, marker := range []string{"accesskeyid=", "signature=", "x-amz-credential", "x-amz-signature", "x-obs-credential", "x-obs-signature", "securitytoken=", "authorization:"} {
		if strings.Contains(lower, marker) {
			return errors.New("OBS live evidence contains a signed request marker")
		}
	}
	return nil
}

func writeCreateOnlyOBSLiveEvidence(path string, payload []byte) error {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	complete := false
	defer func() {
		_ = file.Close()
		if !complete {
			_ = os.Remove(path)
		}
	}()
	if _, err := file.Write(payload); err != nil {
		return err
	}
	if err := file.Sync(); err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm() != 0o600 {
		return errors.New("OBS live evidence file mode is not 0600")
	}
	complete = true
	return nil
}

func validateOBSLiveEvidence(evidence obsLiveEvidence, config service.PlatformHuaweiOBSConfig) error {
	if err := validateOBSLiveProvenance(evidence.SourceRevision, evidence.SourceSnapshotSHA256, evidence.SourceSnapshotFileCount, evidence.HarnessSourceSnapshotSHA256, evidence.HarnessSourceSnapshotFiles, evidence.ContainerImageDigest); err != nil {
		return err
	}
	parsedID, err := uuid.Parse(evidence.EvidenceID)
	if err != nil || parsedID.String() != evidence.EvidenceID || evidence.SchemaVersion != 3 || evidence.Kind != "relay_huawei_obs_live_acceptance" || evidence.Status != "PASS" || !evidence.Verified {
		return errors.New("OBS live evidence identity or status is invalid")
	}
	if evidence.SourceRevisionAttestation != obsLiveSourceRevisionAttestation || !evidence.ImageSourceLabelsVerified || !evidence.ImageCompiledIdentityVerified {
		return errors.New("OBS live evidence is not bound to verified candidate-image source labels and compiled identity")
	}
	endpoint, err := url.Parse(config.Endpoint)
	if err != nil || evidence.EndpointHost != strings.ToLower(strings.TrimSuffix(endpoint.Hostname(), ".")) {
		return errors.New("OBS live evidence endpoint binding is invalid")
	}
	bucketDigest := sha256.Sum256([]byte(config.Bucket))
	if evidence.BucketSHA256 != "sha256:"+hex.EncodeToString(bucketDigest[:]) || service.ValidatePlatformArtifactObjectKey(evidence.ObjectKey) != nil || evidence.SizeBytes < 1 || !obsLiveDigestPattern.MatchString(evidence.ArtifactSHA256) {
		return errors.New("OBS live evidence object binding is invalid")
	}
	if !containsOBSLiveStatus([]int{http.StatusUnauthorized, http.StatusForbidden, http.StatusNotFound}, evidence.AnonymousGetStatus) || evidence.SignedGetStatus != http.StatusOK || evidence.Cleanup.DeleteStatus < 200 || evidence.Cleanup.DeleteStatus >= 300 || evidence.Cleanup.PostDeleteStatus != http.StatusNotFound || evidence.Cleanup.Strategy != "delete_exact_test_object_then_verify_not_found" || !evidence.Cleanup.ExactObjectVerified {
		return errors.New("OBS live transfer or cleanup result is invalid")
	}
	timestampValues := []string{
		evidence.StartedAtUTC,
		evidence.PutCompletedAtUTC,
		evidence.AnonymousProbeCompletedAtUTC,
		evidence.SignedTransferCompletedAtUTC,
		evidence.Cleanup.CompletedAtUTC,
		evidence.EvidenceCreatedAtUTC,
	}
	var previous time.Time
	for _, value := range timestampValues {
		parsed, parseErr := time.Parse(time.RFC3339Nano, value)
		if parseErr != nil || parsed.Location() != time.UTC || (!previous.IsZero() && parsed.Before(previous)) {
			return errors.New("OBS live evidence chronology is invalid")
		}
		previous = parsed
	}
	return nil
}

func containsOBSLiveStatus(values []int, target int) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func writeOBSLiveEvidence(directory string, evidence obsLiveEvidence, config service.PlatformHuaweiOBSConfig, signedURL string) (string, error) {
	if err := validateOBSLiveEvidence(evidence, config); err != nil {
		return "", err
	}
	if directory == "" || directory != strings.TrimSpace(directory) || !filepath.IsAbs(directory) {
		return "", errors.New("OBS live evidence directory must be an absolute path")
	}
	info, err := os.Lstat(directory)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", errors.New("OBS live evidence directory must be an existing real directory")
	}
	payload, err := json.MarshalIndent(evidence, "", "  ")
	if err != nil {
		return "", err
	}
	payload = append(payload, '\n')
	if err := scanOBSLiveEvidence(payload, config, signedURL); err != nil {
		return "", err
	}
	path := filepath.Join(directory, "relay-obs-live-evidence-"+evidence.EvidenceID+".json")
	if err := writeCreateOnlyOBSLiveEvidence(path, payload); err != nil {
		return "", err
	}
	return path, nil
}

func obsLiveErrorStatus(err error) int {
	var value obs.ObsError
	if errors.As(err, &value) {
		return value.StatusCode
	}
	var pointer *obs.ObsError
	if errors.As(err, &pointer) && pointer != nil {
		return pointer.StatusCode
	}
	return 0
}

func requiredEnv(t *testing.T, name string) string {
	t.Helper()
	value := strings.TrimSpace(os.Getenv(name))
	require.NotEmpty(t, value, "%s is required for the live OBS gate", name)
	return value
}

func newCleanupClient(t *testing.T, config service.PlatformHuaweiOBSConfig) *obs.ObsClient {
	t.Helper()
	var client *obs.ObsClient
	var err error
	if config.SecurityToken != "" {
		client, err = obs.New(
			config.AccessKeyID,
			config.SecretAccessKey,
			config.Endpoint,
			obs.WithSecurityToken(config.SecurityToken),
			obs.WithSslVerify(true),
			obs.WithConnectTimeout(10),
			obs.WithSocketTimeout(60),
			obs.WithHeaderTimeout(30),
			obs.WithMaxRetryCount(2),
			obs.WithProxyFromEnv(false),
		)
	} else {
		client, err = obs.New(
			config.AccessKeyID,
			config.SecretAccessKey,
			config.Endpoint,
			obs.WithSslVerify(true),
			obs.WithConnectTimeout(10),
			obs.WithSocketTimeout(60),
			obs.WithHeaderTimeout(30),
			obs.WithMaxRetryCount(2),
			obs.WithProxyFromEnv(false),
		)
	}
	require.NoError(t, err)
	return client
}

func TestLiveHuaweiOBSPrivateRoundTrip(t *testing.T) {
	startedAt := time.Now().UTC()
	evidenceDirectory := requiredEnv(t, "RELAY_OBS_LIVE_EVIDENCE_DIR")
	sourceRevision := requiredEnv(t, "RELAY_OBS_LIVE_SOURCE_REVISION")
	sourceSnapshotSHA256 := requiredEnv(t, "RELAY_OBS_LIVE_SOURCE_SNAPSHOT_SHA256")
	sourceSnapshotFileCountText := requiredEnv(t, "RELAY_OBS_LIVE_SOURCE_FILE_COUNT")
	sourceSnapshotFileCount, err := strconv.Atoi(sourceSnapshotFileCountText)
	require.NoError(t, err, "RELAY_OBS_LIVE_SOURCE_FILE_COUNT must be a positive decimal integer")
	harnessSnapshotSHA256 := requiredEnv(t, "RELAY_OBS_LIVE_HARNESS_SNAPSHOT_SHA256")
	harnessSnapshotFileCountText := requiredEnv(t, "RELAY_OBS_LIVE_HARNESS_FILE_COUNT")
	harnessSnapshotFileCount, err := strconv.Atoi(harnessSnapshotFileCountText)
	require.NoError(t, err, "RELAY_OBS_LIVE_HARNESS_FILE_COUNT must be a positive decimal integer")
	imageDigest := requiredEnv(t, "RELAY_OBS_LIVE_IMAGE_DIGEST")
	require.Equal(t, obsLiveHostRunnerAttestation, requiredEnv(t, "RELAY_OBS_LIVE_PROVENANCE_ATTESTATION"),
		"the OBS live gate must be launched by the host-side candidate-image inspection runner")
	require.NoError(t, validateOBSLiveProvenance(sourceRevision, sourceSnapshotSHA256, sourceSnapshotFileCount, harnessSnapshotSHA256, harnessSnapshotFileCount, imageDigest))
	config := service.PlatformHuaweiOBSConfig{
		AccessKeyID:     requiredEnv(t, "HUAWEI_OBS_ACCESS_KEY_ID"),
		SecretAccessKey: requiredEnv(t, "HUAWEI_OBS_SECRET_ACCESS_KEY"),
		SecurityToken:   strings.TrimSpace(os.Getenv("HUAWEI_OBS_SECURITY_TOKEN")),
		Endpoint:        requiredEnv(t, "HUAWEI_OBS_ENDPOINT"),
		Bucket:          requiredEnv(t, "HUAWEI_OBS_BUCKET"),
	}
	store, err := service.NewPlatformHuaweiOBSArtifactStore(config)
	require.NoError(t, err)
	defer store.Close()

	cleanupClient := newCleanupClient(t, config)
	defer cleanupClient.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	require.NoError(t, store.Healthcheck(ctx))

	jobID := uuid.NewString()
	artifactID := uuid.NewString()
	objectKey, err := service.PlatformArtifactObjectKey(jobID, uuid.NewString(), artifactID)
	require.NoError(t, err)
	objectCreated := false
	cleanupVerified := false
	defer func() {
		if !objectCreated || cleanupVerified {
			return
		}
		output, deleteErr := cleanupClient.DeleteObject(&obs.DeleteObjectInput{
			Bucket: config.Bucket,
			Key:    objectKey,
		})
		if deleteErr != nil || output == nil || output.StatusCode < 200 || output.StatusCode >= 300 {
			t.Errorf("emergency exact-object cleanup failed: status=%v err=%v", func() int {
				if output == nil {
					return 0
				}
				return output.StatusCode
			}(), deleteErr)
		}
	}()

	payload := []byte("new-api relay Huawei OBS live acceptance " + uuid.NewString())
	digest := sha256.Sum256(payload)
	digestText := fmt.Sprintf("%x", digest)
	stored, err := store.Put(ctx, service.PlatformArtifactPutInput{
		ObjectKey:   objectKey,
		Content:     bytes.NewReader(payload),
		ContentType: "application/octet-stream",
		SizeBytes:   int64(len(payload)),
		SHA256:      digestText,
	})
	require.NoError(t, err)
	require.Equal(t, objectKey, stored.ObjectKey)
	require.Equal(t, digestText, stored.SHA256)
	objectCreated = true
	putCompletedAt := time.Now().UTC()

	signedURL, err := store.SignedDownloadURL(ctx, objectKey, 60*time.Second)
	require.NoError(t, err)
	parsed, err := url.Parse(signedURL)
	require.NoError(t, err)
	require.Equal(t, "https", parsed.Scheme)

	client := &http.Client{
		Timeout: 30 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	unsigned := *parsed
	unsigned.RawQuery = ""
	unsigned.Fragment = ""
	unsignedRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, unsigned.String(), nil)
	require.NoError(t, err)
	unsignedResponse, err := client.Do(unsignedRequest)
	require.NoError(t, err)
	_, _ = io.Copy(io.Discard, unsignedResponse.Body)
	require.NoError(t, unsignedResponse.Body.Close())
	require.Contains(t, []int{http.StatusUnauthorized, http.StatusForbidden, http.StatusNotFound}, unsignedResponse.StatusCode,
		"anonymous object probe did not return an explicit access denial")
	anonymousProbeCompletedAt := time.Now().UTC()

	signedRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, signedURL, nil)
	require.NoError(t, err)
	signedResponse, err := client.Do(signedRequest)
	if err != nil {
		t.Fatal("signed OBS download request failed")
	}
	require.Equal(t, http.StatusOK, signedResponse.StatusCode)
	downloaded, err := io.ReadAll(io.LimitReader(signedResponse.Body, int64(len(payload))+1))
	require.NoError(t, err)
	require.NoError(t, signedResponse.Body.Close())
	require.Equal(t, payload, downloaded)
	downloadedDigest := sha256.Sum256(downloaded)
	require.Equal(t, digestText, fmt.Sprintf("%x", downloadedDigest))
	signedTransferCompletedAt := time.Now().UTC()

	deleteOutput, err := cleanupClient.DeleteObject(&obs.DeleteObjectInput{Bucket: config.Bucket, Key: objectKey})
	require.NoError(t, err, "exact live-gate object cleanup failed")
	require.NotNil(t, deleteOutput, "exact live-gate object cleanup returned no response")
	require.GreaterOrEqual(t, deleteOutput.StatusCode, 200)
	require.Less(t, deleteOutput.StatusCode, 300)
	_, metadataErr := cleanupClient.GetObjectMetadata(&obs.GetObjectMetadataInput{Bucket: config.Bucket, Key: objectKey})
	postDeleteStatus := obsLiveErrorStatus(metadataErr)
	require.Error(t, metadataErr, "deleted live-gate object remained readable")
	require.Equal(t, http.StatusNotFound, postDeleteStatus, "exact live-gate object absence was not confirmed")
	cleanupCompletedAt := time.Now().UTC()
	cleanupVerified = true
	objectCreated = false

	endpoint, err := url.Parse(config.Endpoint)
	require.NoError(t, err)
	bucketIdentity := sha256.Sum256([]byte(config.Bucket))
	evidence := obsLiveEvidence{
		SchemaVersion:                 3,
		Kind:                          "relay_huawei_obs_live_acceptance",
		Status:                        "PASS",
		EvidenceID:                    uuid.NewString(),
		SourceRevision:                sourceRevision,
		SourceRevisionAttestation:     obsLiveSourceRevisionAttestation,
		SourceSnapshotSHA256:          sourceSnapshotSHA256,
		SourceSnapshotFileCount:       sourceSnapshotFileCount,
		HarnessSourceSnapshotSHA256:   harnessSnapshotSHA256,
		HarnessSourceSnapshotFiles:    harnessSnapshotFileCount,
		ImageSourceLabelsVerified:     true,
		ImageCompiledIdentityVerified: true,
		ContainerImageDigest:          imageDigest,
		EndpointHost:                  strings.ToLower(strings.TrimSuffix(endpoint.Hostname(), ".")),
		BucketSHA256:                  "sha256:" + hex.EncodeToString(bucketIdentity[:]),
		ObjectKey:                     objectKey,
		SizeBytes:                     int64(len(downloaded)),
		ArtifactSHA256:                digestText,
		AnonymousGetStatus:            unsignedResponse.StatusCode,
		SignedGetStatus:               signedResponse.StatusCode,
		StartedAtUTC:                  startedAt.Format(time.RFC3339Nano),
		PutCompletedAtUTC:             putCompletedAt.Format(time.RFC3339Nano),
		AnonymousProbeCompletedAtUTC:  anonymousProbeCompletedAt.Format(time.RFC3339Nano),
		SignedTransferCompletedAtUTC:  signedTransferCompletedAt.Format(time.RFC3339Nano),
		Cleanup: obsLiveCleanupEvidence{
			Strategy:            "delete_exact_test_object_then_verify_not_found",
			DeleteStatus:        deleteOutput.StatusCode,
			PostDeleteStatus:    postDeleteStatus,
			CompletedAtUTC:      cleanupCompletedAt.Format(time.RFC3339Nano),
			ExactObjectVerified: true,
		},
		EvidenceCreatedAtUTC: time.Now().UTC().Format(time.RFC3339Nano),
		Verified:             true,
	}
	evidencePath, err := writeOBSLiveEvidence(evidenceDirectory, evidence, config, signedURL)
	require.NoError(t, err)
	t.Logf("OBS_LIVE_PASS evidence=%s endpoint_host=%s bucket_sha256=%s object_key=%s bytes=%d sha256=%s anonymous_status=%d cleanup_status=%d",
		evidencePath, evidence.EndpointHost, evidence.BucketSHA256, objectKey, len(downloaded), digestText, unsignedResponse.StatusCode, postDeleteStatus)
}

func validOBSLiveEvidenceForTest(config service.PlatformHuaweiOBSConfig) obsLiveEvidence {
	now := time.Date(2028, time.January, 2, 3, 4, 5, 0, time.UTC)
	bucketDigest := sha256.Sum256([]byte(config.Bucket))
	return obsLiveEvidence{
		SchemaVersion:                 3,
		Kind:                          "relay_huawei_obs_live_acceptance",
		Status:                        "PASS",
		EvidenceID:                    "11111111-1111-4111-8111-111111111111",
		SourceRevision:                strings.Repeat("a", 40),
		SourceRevisionAttestation:     obsLiveSourceRevisionAttestation,
		SourceSnapshotSHA256:          "sha256:" + strings.Repeat("d", 64),
		SourceSnapshotFileCount:       123,
		HarnessSourceSnapshotSHA256:   "sha256:" + strings.Repeat("e", 64),
		HarnessSourceSnapshotFiles:    17,
		ImageSourceLabelsVerified:     true,
		ImageCompiledIdentityVerified: true,
		ContainerImageDigest:          "sha256:" + strings.Repeat("b", 64),
		EndpointHost:                  "obs.cn-north-4.myhuaweicloud.com",
		BucketSHA256:                  "sha256:" + hex.EncodeToString(bucketDigest[:]),
		ObjectKey:                     "outputs/22222222-2222-4222-8222-222222222222/33333333-3333-4333-8333-333333333333/44444444-4444-4444-8444-444444444444",
		SizeBytes:                     128,
		ArtifactSHA256:                strings.Repeat("c", 64),
		AnonymousGetStatus:            http.StatusForbidden,
		SignedGetStatus:               http.StatusOK,
		StartedAtUTC:                  now.Format(time.RFC3339Nano),
		PutCompletedAtUTC:             now.Add(time.Second).Format(time.RFC3339Nano),
		AnonymousProbeCompletedAtUTC:  now.Add(2 * time.Second).Format(time.RFC3339Nano),
		SignedTransferCompletedAtUTC:  now.Add(3 * time.Second).Format(time.RFC3339Nano),
		Cleanup: obsLiveCleanupEvidence{
			Strategy:            "delete_exact_test_object_then_verify_not_found",
			DeleteStatus:        http.StatusNoContent,
			PostDeleteStatus:    http.StatusNotFound,
			CompletedAtUTC:      now.Add(4 * time.Second).Format(time.RFC3339Nano),
			ExactObjectVerified: true,
		},
		EvidenceCreatedAtUTC: now.Add(5 * time.Second).Format(time.RFC3339Nano),
		Verified:             true,
	}
}

func TestOBSLiveEvidenceRejectsMutableOrMalformedProvenance(t *testing.T) {
	for _, item := range []struct {
		revision       string
		snapshotSHA256 string
		fileCount      int
		harnessSHA256  string
		harnessFiles   int
		image          string
	}{
		{revision: strings.Repeat("A", 40), snapshotSHA256: "sha256:" + strings.Repeat("d", 64), fileCount: 123, harnessSHA256: "sha256:" + strings.Repeat("e", 64), harnessFiles: 17, image: "sha256:" + strings.Repeat("b", 64)},
		{revision: strings.Repeat("a", 39), snapshotSHA256: "sha256:" + strings.Repeat("d", 64), fileCount: 123, harnessSHA256: "sha256:" + strings.Repeat("e", 64), harnessFiles: 17, image: "sha256:" + strings.Repeat("b", 64)},
		{revision: "main", snapshotSHA256: "sha256:" + strings.Repeat("d", 64), fileCount: 123, harnessSHA256: "sha256:" + strings.Repeat("e", 64), harnessFiles: 17, image: "sha256:" + strings.Repeat("b", 64)},
		{revision: strings.Repeat("a", 40), snapshotSHA256: strings.Repeat("d", 64), fileCount: 123, harnessSHA256: "sha256:" + strings.Repeat("e", 64), harnessFiles: 17, image: "sha256:" + strings.Repeat("b", 64)},
		{revision: strings.Repeat("a", 40), snapshotSHA256: "sha256:" + strings.Repeat("D", 64), fileCount: 123, harnessSHA256: "sha256:" + strings.Repeat("e", 64), harnessFiles: 17, image: "sha256:" + strings.Repeat("b", 64)},
		{revision: strings.Repeat("a", 40), snapshotSHA256: "sha256:" + strings.Repeat("d", 64), fileCount: 0, harnessSHA256: "sha256:" + strings.Repeat("e", 64), harnessFiles: 17, image: "sha256:" + strings.Repeat("b", 64)},
		{revision: strings.Repeat("a", 40), snapshotSHA256: "sha256:" + strings.Repeat("d", 64), fileCount: 123, harnessSHA256: "sha256:" + strings.Repeat("E", 64), harnessFiles: 17, image: "sha256:" + strings.Repeat("b", 64)},
		{revision: strings.Repeat("a", 40), snapshotSHA256: "sha256:" + strings.Repeat("d", 64), fileCount: 123, harnessSHA256: "sha256:" + strings.Repeat("e", 64), harnessFiles: 0, image: "sha256:" + strings.Repeat("b", 64)},
		{revision: strings.Repeat("a", 40), snapshotSHA256: "sha256:" + strings.Repeat("d", 64), fileCount: 123, harnessSHA256: "sha256:" + strings.Repeat("e", 64), harnessFiles: 17, image: strings.Repeat("b", 64)},
		{revision: strings.Repeat("a", 40), snapshotSHA256: "sha256:" + strings.Repeat("d", 64), fileCount: 123, harnessSHA256: "sha256:" + strings.Repeat("e", 64), harnessFiles: 17, image: "sha256:latest"},
		{revision: strings.Repeat("a", 40), snapshotSHA256: "sha256:" + strings.Repeat("d", 64), fileCount: 123, harnessSHA256: "sha256:" + strings.Repeat("e", 64), harnessFiles: 17, image: "sha256:" + strings.Repeat("B", 64)},
	} {
		if err := validateOBSLiveProvenance(item.revision, item.snapshotSHA256, item.fileCount, item.harnessSHA256, item.harnessFiles, item.image); err == nil {
			t.Fatalf("mutable or malformed provenance was accepted: %#v", item)
		}
	}
}

func TestOBSLiveEvidenceIsSecretFreeCreateOnlyAnd0600(t *testing.T) {
	config := service.PlatformHuaweiOBSConfig{
		AccessKeyID:     "LIVEACCESSKEY123456",
		SecretAccessKey: "live-secret-access-key-32-bytes-minimum",
		SecurityToken:   "live-security-token-32-bytes-minimum",
		Endpoint:        "https://obs.cn-north-4.myhuaweicloud.com",
		Bucket:          "private-live-bucket",
	}
	signedURL := "https://obs.cn-north-4.myhuaweicloud.com/private-live-bucket/object?AccessKeyId=LIVEACCESSKEY123456&Expires=1800000000&Signature=signed-secret"
	evidence := validOBSLiveEvidenceForTest(config)
	payload, err := json.Marshal(evidence)
	require.NoError(t, err)
	require.NoError(t, scanOBSLiveEvidence(payload, config, signedURL))

	for _, leaked := range [][]byte{
		[]byte(`{"leak":"LIVEACCESSKEY123456"}`),
		[]byte(`{"leak":"live-secret-access-key-32-bytes-minimum"}`),
		[]byte(`{"leak":"live-security-token-32-bytes-minimum"}`),
		[]byte(`{"leak":"` + signedURL + `"}`),
		[]byte(`{"leak":"` + base64.StdEncoding.EncodeToString([]byte(config.SecretAccessKey)) + `"}`),
		[]byte(`{"leak":"AccessKeyId=redacted&Signature=redacted"}`),
		[]byte(`{"bucket":"private-live-bucket"}`),
	} {
		if err := scanOBSLiveEvidence(leaked, config, signedURL); err == nil {
			t.Fatalf("secret-bearing OBS live evidence was accepted: %s", leaked)
		}
	}

	directory := t.TempDir()
	path, err := writeOBSLiveEvidence(directory, evidence, config, signedURL)
	require.NoError(t, err)
	info, err := os.Lstat(path)
	require.NoError(t, err)
	require.Equal(t, os.FileMode(0o600), info.Mode().Perm())
	original, err := os.ReadFile(path)
	require.NoError(t, err)
	_, err = writeOBSLiveEvidence(directory, evidence, config, signedURL)
	require.Error(t, err, "OBS live evidence must never overwrite an existing file")
	afterCollision, err := os.ReadFile(path)
	require.NoError(t, err)
	require.Equal(t, original, afterCollision)

	symlink := filepath.Join(t.TempDir(), "evidence-link")
	require.NoError(t, os.Symlink(directory, symlink))
	otherEvidence := evidence
	otherEvidence.EvidenceID = "55555555-5555-4555-8555-555555555555"
	_, err = writeOBSLiveEvidence(symlink, otherEvidence, config, signedURL)
	require.Error(t, err, "symlink evidence directory must be rejected")
}

func TestOBSLiveEvidenceRejectsBindingCleanupAndChronologyDrift(t *testing.T) {
	config := service.PlatformHuaweiOBSConfig{
		Endpoint: "https://obs.cn-north-4.myhuaweicloud.com",
		Bucket:   "private-live-bucket",
	}
	valid := validOBSLiveEvidenceForTest(config)
	require.NoError(t, validateOBSLiveEvidence(valid, config))
	mutations := []func(*obsLiveEvidence){
		func(value *obsLiveEvidence) { value.SourceRevisionAttestation = "git-commit-sha" },
		func(value *obsLiveEvidence) { value.SourceSnapshotSHA256 = "sha256:" + strings.Repeat("D", 64) },
		func(value *obsLiveEvidence) { value.SourceSnapshotFileCount = 0 },
		func(value *obsLiveEvidence) { value.HarnessSourceSnapshotSHA256 = "sha256:" + strings.Repeat("F", 64) },
		func(value *obsLiveEvidence) { value.HarnessSourceSnapshotFiles = 0 },
		func(value *obsLiveEvidence) { value.ImageSourceLabelsVerified = false },
		func(value *obsLiveEvidence) { value.ImageCompiledIdentityVerified = false },
		func(value *obsLiveEvidence) { value.BucketSHA256 = "sha256:" + strings.Repeat("d", 64) },
		func(value *obsLiveEvidence) { value.EndpointHost = "obs.example.invalid" },
		func(value *obsLiveEvidence) { value.ObjectKey += "/other" },
		func(value *obsLiveEvidence) { value.AnonymousGetStatus = http.StatusOK },
		func(value *obsLiveEvidence) { value.Cleanup.ExactObjectVerified = false },
		func(value *obsLiveEvidence) { value.Cleanup.PostDeleteStatus = http.StatusForbidden },
		func(value *obsLiveEvidence) { value.EvidenceCreatedAtUTC = value.StartedAtUTC },
	}
	for index, mutate := range mutations {
		candidate := valid
		mutate(&candidate)
		if err := validateOBSLiveEvidence(candidate, config); err == nil {
			t.Fatalf("OBS live evidence mutation %d was accepted", index)
		}
	}
}
