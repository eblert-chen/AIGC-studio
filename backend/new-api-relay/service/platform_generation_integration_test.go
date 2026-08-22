package service

import (
	"context"
	"crypto/sha256"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestPlatformGenerationTransferPublishesOnlyVerifiedDurableOutput(t *testing.T) {
	truncate(t)
	payload := platformArtifactValidMP4Fixture(t)
	provider := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", "video/mp4")
		_, _ = response.Write(payload)
	}))
	defer provider.Close()
	downloader, sourceURL, _ := artifactTestDownloader(
		t,
		provider,
		PlatformArtifactDownloadConfig{MaxBytes: int64(len(payload)) + 1, Timeout: 5 * time.Second},
		[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
	)

	tenantID := uuid.NewString()
	jobID := uuid.NewString()
	token := uuid.NewString()
	nativeTaskID, err := model.PlatformGenerationNativeTaskID(jobID)
	require.NoError(t, err)
	request := dto.NewPlatformGenerationRequest()
	request.Model = "verified-video-model"
	request.Mode = "text_to_video"
	request.ExpectedCapabilityRevision = "sha256:" + strings.Repeat("a", 64)
	request.Inputs.Prompt = "safe scene"
	requestJSON, err := common.Marshal(request)
	require.NoError(t, err)
	temporaryJSON, err := common.Marshal(platformNativeTemporaryResult{
		NativeTaskID: nativeTaskID,
		ResultURL:    sourceURL,
	})
	require.NoError(t, err)
	job := model.PlatformGenerationJob{
		ID:                         jobID,
		TenantID:                   tenantID,
		SourceClientID:             "platform",
		RequestID:                  "transfer-request",
		IdempotencyKey:             "transfer-idempotency",
		RequestHash:                strings.Repeat("b", 64),
		RequestJSON:                string(requestJSON),
		Model:                      request.Model,
		Mode:                       request.Mode,
		ExpectedCapabilityRevision: request.ExpectedCapabilityRevision,
		CapabilityRevision:         request.ExpectedCapabilityRevision,
		Status:                     model.PlatformGenerationStatusTransferring,
		Progress:                   95,
		NativeTaskID:               nativeTaskID,
		TemporaryResultJSON:        string(temporaryJSON),
		OutputsJSON:                "[]",
		ErrorDetailsJSON:           "{}",
		TransferLeaseToken:         token,
		TransferLeaseExpiresAt:     time.Now().UTC().Add(time.Minute),
		ArtifactTransferAttempts:   1,
		CreatedAt:                  time.Now().UTC(),
		UpdatedAt:                  time.Now().UTC(),
	}
	require.NoError(t, model.DB.Create(&job).Error)

	root := filepath.Join(t.TempDir(), "artifacts")
	store, err := NewPlatformFilesystemArtifactStore(
		root,
		"https://relay.example",
		[]byte("artifact-signing-secret-with-32-bytes"),
	)
	require.NoError(t, err)
	require.NoError(t, transferClaimedPlatformGeneration(context.Background(), job, token, downloader, store))

	persisted, err := model.GetPlatformGenerationJob(jobID, tenantID)
	require.NoError(t, err)
	assert.Equal(t, model.PlatformGenerationStatusSucceeded, persisted.Status)
	assert.Empty(t, persisted.TemporaryResultJSON)
	assert.Empty(t, persisted.UpstreamResultURL)
	assert.NotContains(t, persisted.OutputsJSON, sourceURL)
	var outputs []dto.PlatformGenerationArtifact
	require.NoError(t, common.Unmarshal([]byte(persisted.OutputsJSON), &outputs))
	require.Len(t, outputs, 1)
	assert.Equal(t, uuid.NewSHA1(uuid.NameSpaceURL, []byte("relay-artifact:"+jobID+":0")).String(), outputs[0].AssetID)
	assert.NotContains(t, outputs[0].ObjectKey, outputs[0].AssetID, "storage object is lease-versioned independently of the stable public asset id")
	digest := sha256.Sum256(payload)
	assert.Equal(t, fmt.Sprintf("%x", digest), outputs[0].SHA256)

	snapshot, err := platformGenerationSnapshot(*persisted)
	require.NoError(t, err)
	serialized, err := common.Marshal(snapshot)
	require.NoError(t, err)
	assert.NotContains(t, string(serialized), sourceURL)

	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "development")
	t.Setenv("RELAY_ARTIFACT_STORE", PlatformArtifactFilesystemKind)
	t.Setenv("RELAY_ARTIFACT_FILESYSTEM_ROOT", root)
	t.Setenv("RELAY_ARTIFACT_PUBLIC_BASE_URL", "https://relay.example")
	t.Setenv("RELAY_ARTIFACT_SIGNING_SECRET", "artifact-signing-secret-with-32-bytes")
	download, err := GetPlatformGenerationArtifactSignedDownload(
		context.Background(),
		PlatformRelayPrincipal{TenantID: tenantID},
		jobID,
		outputs[0].AssetID,
	)
	require.NoError(t, err)
	assert.Nil(t, download.StorageBinding)
	parsed, err := url.Parse(download.URL)
	require.NoError(t, err)
	assert.Equal(t, PlatformArtifactDownloadPath, parsed.Path)
	assert.NotEmpty(t, parsed.Query().Get("signature"))

	_, err = GetPlatformGenerationArtifactSignedDownload(
		context.Background(),
		PlatformRelayPrincipal{TenantID: uuid.NewString()},
		jobID,
		outputs[0].AssetID,
	)
	assert.ErrorIs(t, err, gorm.ErrRecordNotFound)

	outputs[0].SizeBytes = 0
	emptyOutputJSON, err := common.Marshal(outputs)
	require.NoError(t, err)
	require.NoError(t, model.DB.Model(&model.PlatformGenerationJob{}).
		Where("id = ? AND tenant_id = ?", jobID, tenantID).
		Update("outputs_json", string(emptyOutputJSON)).Error)
	_, err = GetPlatformGenerationArtifactSignedDownload(
		context.Background(),
		PlatformRelayPrincipal{TenantID: tenantID},
		jobID,
		outputs[0].AssetID,
	)
	assert.ErrorIs(t, err, gorm.ErrRecordNotFound)
}

func TestPlatformGenerationCallbackWorkerMovesExhaustedDeliveryToDeadLetter(t *testing.T) {
	truncate(t)
	require.NoError(t, model.DB.AutoMigrate(&model.PlatformGenerationCallbackDelivery{}))
	t.Cleanup(func() {
		model.DB.Exec("DELETE FROM platform_generation_callback_deliveries")
	})
	t.Setenv("RELAY_COMPAT_CLIENT_CREDENTIALS_JSON", "")
	delivery := model.PlatformGenerationCallbackDelivery{
		ID:             uuid.NewString(),
		TenantID:       uuid.NewString(),
		SourceClientID: "missing-client",
		JobID:          uuid.NewString(),
		CallbackURL:    "https://callback.example/generation",
		RequestID:      "callback-dlq-request",
		PayloadJSON:    "{}",
		PayloadSHA256:  strings.Repeat("0", 64),
		MaxAttempts:    1,
	}
	created, err := model.CreatePlatformGenerationCallbackDelivery(&delivery)
	require.NoError(t, err)
	require.True(t, created)

	processed, err := RunPlatformGenerationCallbackOnce(context.Background())
	require.NoError(t, err)
	assert.True(t, processed)
	var persisted model.PlatformGenerationCallbackDelivery
	require.NoError(t, model.DB.First(&persisted, "id = ?", delivery.ID).Error)
	assert.Equal(t, model.PlatformGenerationCallbackDeadLetter, persisted.State)
	assert.Equal(t, model.PlatformGenerationCallbackFailureConfiguration, persisted.LastError)
	assert.Equal(t, 1, persisted.Attempts)
	require.NotNil(t, persisted.DeadLetteredAt)
}

func TestProductionCompatibilityCannotDisableGenerationWorkers(t *testing.T) {
	t.Setenv("RELAY_COMPAT_ENABLED", "true")
	t.Setenv("RELAY_COMPAT_ENVIRONMENT", "production")
	t.Setenv("RELAY_COMPAT_WORKER_ENABLED", "false")
	err := ValidatePlatformGenerationWorkerConfiguration()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "requires RELAY_COMPAT_WORKER_ENABLED=true")
}
