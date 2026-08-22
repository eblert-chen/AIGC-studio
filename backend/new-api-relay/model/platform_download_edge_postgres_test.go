package model

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
)

func preparePlatformDownloadEdgePostgresTest(t *testing.T) {
	t.Helper()
	dsn := strings.TrimSpace(os.Getenv("TEST_POSTGRES_DSN"))
	if dsn == "" {
		t.Skip("set TEST_POSTGRES_DSN to run real PostgreSQL download-edge concurrency tests")
	}
	parsed, err := url.Parse(dsn)
	require.NoError(t, err)
	require.Contains(t, []string{"postgres", "postgresql"}, parsed.Scheme)
	schema := "download_edge_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	require.Regexp(t, `^download_edge_[0-9a-f]{32}$`, schema)
	admin, err := gorm.Open(postgres.Open(dsn), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, admin.Exec(`CREATE SCHEMA "`+schema+`"`).Error)
	query := parsed.Query()
	query.Set("search_path", schema)
	parsed.RawQuery = query.Encode()
	db, err := gorm.Open(postgres.Open(parsed.String()), &gorm.Config{})
	require.NoError(t, err)
	sqlDB, err := db.DB()
	require.NoError(t, err)
	sqlDB.SetMaxOpenConns(32)
	sqlDB.SetMaxIdleConns(8)
	previousDB := DB
	previousType := common.MainDatabaseType()
	DB = db
	common.SetMainDatabaseType(common.DatabaseTypePostgreSQL)
	require.NoError(t, MigratePlatformProviderMonitorAndCostStorage())
	t.Cleanup(func() {
		DB = previousDB
		common.SetMainDatabaseType(previousType)
		require.NoError(t, sqlDB.Close())
		require.NoError(t, admin.Exec(`DROP SCHEMA "`+schema+`" CASCADE`).Error)
		adminSQL, err := admin.DB()
		if err == nil {
			require.NoError(t, adminSQL.Close())
		}
	})
}

func TestPlatformDownloadEdgePostgresConcurrentRegistrationClaimFinalizeAndAppendOnlyGuards(t *testing.T) {
	preparePlatformDownloadEdgePostgresTest(t)
	registrationID := uuid.NewString()
	transferReference := uuid.NewString()
	downloadRecordID := uuid.NewString()
	companyID := uuid.NewString()
	taskID := uuid.NewString()
	assetID := uuid.NewString()
	payloadSHA := strings.Repeat("1", 64)
	artifactSHA := strings.Repeat("2", 64)
	sourceSHA := strings.Repeat("3", 64)
	sourceExpiresAt := time.Now().UTC().Add(15 * time.Minute).Truncate(time.Microsecond)

	const workers = 16
	start := make(chan struct{})
	ids := make(chan string, workers)
	errorsChannel := make(chan error, workers)
	var createdCount atomic.Int32
	var group sync.WaitGroup
	for index := 0; index < workers; index++ {
		group.Add(1)
		go func(index int) {
			defer group.Done()
			<-start
			tokenDigest := sha256.Sum256([]byte(fmt.Sprintf("registration-token-%d", index)))
			ticket := PlatformDownloadEdgeTicket{
				ID: uuid.NewString(), TokenSHA256: hex.EncodeToString(tokenDigest[:]),
				RegistrationRequestID: registrationID, RegistrationPayloadSHA256: payloadSHA,
				DownloadRecordID: downloadRecordID, CompanyID: companyID, TaskID: taskID,
				AssetID: assetID, ExpectedSizeBytes: 64, ArtifactSHA256: artifactSHA,
				OBSBucket: "artifact-bucket", OBSObjectKey: "results/video.mp4", OBSVersionID: "version-1",
				IssuanceRequestID: "issuance-request", TransferReference: transferReference,
				SourceURLSHA256: sourceSHA, SourceExpiresAt: sourceExpiresAt,
				SourceURLCiphertext: []byte("ciphertext-at-least-sixteen-bytes"),
				SourceURLNonce:      []byte("123456789012"),
			}
			created, err := CreatePlatformDownloadEdgeTicket(&ticket, 5*time.Minute, time.Minute)
			if err != nil {
				errorsChannel <- err
				return
			}
			if created {
				createdCount.Add(1)
			}
			ids <- ticket.ID
		}(index)
	}
	close(start)
	group.Wait()
	close(errorsChannel)
	for err := range errorsChannel {
		require.NoError(t, err)
	}
	close(ids)
	var stableID string
	for id := range ids {
		if stableID == "" {
			stableID = id
		}
		assert.Equal(t, stableID, id)
	}
	assert.Equal(t, int32(1), createdCount.Load())
	var ticket PlatformDownloadEdgeTicket
	require.NoError(t, DB.First(&ticket, "id = ?", stableID).Error)

	start = make(chan struct{})
	claims := make(chan *PlatformDownloadEdgeTicketClaim, workers)
	errorsChannel = make(chan error, workers)
	for index := 0; index < workers; index++ {
		group.Add(1)
		go func() {
			defer group.Done()
			<-start
			claim, err := ClaimPlatformDownloadEdgeTicket(ticket.TokenSHA256, 30*time.Second)
			if err == nil {
				claims <- claim
				return
			}
			if !errors.Is(err, ErrPlatformDownloadEdgeTicketUnavailable) {
				errorsChannel <- err
			}
		}()
	}
	close(start)
	group.Wait()
	close(claims)
	close(errorsChannel)
	for err := range errorsChannel {
		require.NoError(t, err)
	}
	var winningClaim *PlatformDownloadEdgeTicketClaim
	claimCount := 0
	for claim := range claims {
		winningClaim = claim
		claimCount++
	}
	require.Equal(t, 1, claimCount)

	eventID := uuid.NewString()
	start = make(chan struct{})
	results := make(chan error, workers)
	var finalizeWins atomic.Int32
	for index := 0; index < workers; index++ {
		group.Add(1)
		go func() {
			defer group.Done()
			<-start
			_, err := FinalizePlatformDownloadEdgeTransfer(winningClaim.Ticket.ID, winningClaim.Token, eventID, 3)
			if err == nil {
				finalizeWins.Add(1)
				return
			}
			if !errors.Is(err, ErrPlatformDownloadEdgeClaimLost) {
				results <- err
			}
		}()
	}
	close(start)
	group.Wait()
	close(results)
	for err := range results {
		require.NoError(t, err)
	}
	assert.Equal(t, int32(1), finalizeWins.Load())
	var eventCount, deliveryCount int64
	require.NoError(t, DB.Model(&PlatformDownloadCompletionEvent{}).Count(&eventCount).Error)
	require.NoError(t, DB.Model(&PlatformRelayExternalDelivery{}).
		Where("event_kind = ?", PlatformRelayDeliveryKindDownloadCompletion).Count(&deliveryCount).Error)
	assert.Equal(t, int64(1), eventCount)
	assert.Equal(t, int64(1), deliveryCount)
	var event PlatformDownloadCompletionEvent
	require.NoError(t, DB.First(&event, "id = ?", eventID).Error)
	assert.Equal(t, "issuance-request", event.GatewayRequestID)

	deliveryClaim, err := ClaimPlatformRelayExternalDelivery(PlatformRelayDeliveryKindDownloadCompletion, 30*time.Second)
	require.NoError(t, err)
	proofPayload := `{"schema_version":1}`
	proofDigest := sha256.Sum256([]byte(proofPayload))
	won, err := CompletePlatformDownloadCompletionDelivery(
		eventID, deliveryClaim.Token, 201,
		func(deliveredAt time.Time) (*PlatformDownloadCompletionProof, error) {
			return &PlatformDownloadCompletionProof{
				EventID: eventID, CompletionID: uuid.NewString(), KeyID: "postgres-proof-key",
				PayloadJSON: proofPayload, PayloadSHA256: hex.EncodeToString(proofDigest[:]),
				SignatureBase64: base64.StdEncoding.EncodeToString(make([]byte, 64)), ProducedAt: deliveredAt,
			}, nil
		},
	)
	require.NoError(t, err)
	require.True(t, won)

	assert.Error(t, DB.Exec("UPDATE platform_download_completion_events SET artifact_sha256 = ? WHERE id = ?", strings.Repeat("4", 64), eventID).Error)
	assert.Error(t, DB.Exec("DELETE FROM platform_download_completion_events WHERE id = ?", eventID).Error)
	assert.Error(t, DB.Exec("TRUNCATE TABLE platform_download_completion_events").Error)
	assert.Error(t, DB.Exec("UPDATE platform_download_completion_proofs SET key_id = 'tampered' WHERE event_id = ?", eventID).Error)
	assert.Error(t, DB.Exec("DELETE FROM platform_download_completion_proofs WHERE event_id = ?", eventID).Error)
	assert.Error(t, DB.Exec("TRUNCATE TABLE platform_download_completion_proofs").Error)
}

func TestPlatformDownloadEdgePostgresExpiredClaimsCannotCommitEvidence(t *testing.T) {
	preparePlatformDownloadEdgePostgresTest(t)
	tokenDigest := sha256.Sum256([]byte("expired-ticket-token"))
	ticket := PlatformDownloadEdgeTicket{
		ID: uuid.NewString(), TokenSHA256: hex.EncodeToString(tokenDigest[:]),
		RegistrationRequestID: uuid.NewString(), RegistrationPayloadSHA256: strings.Repeat("1", 64),
		DownloadRecordID: uuid.NewString(), CompanyID: uuid.NewString(), TaskID: uuid.NewString(),
		AssetID: uuid.NewString(), ExpectedSizeBytes: 64, ArtifactSHA256: strings.Repeat("2", 64),
		OBSBucket: "artifact-bucket", OBSObjectKey: "results/video.mp4", OBSVersionID: "version-1",
		IssuanceRequestID: "issuance-request", TransferReference: uuid.NewString(),
		SourceURLSHA256: strings.Repeat("3", 64), SourceExpiresAt: time.Now().UTC().Add(15 * time.Minute).Truncate(time.Microsecond),
		SourceURLCiphertext: []byte("ciphertext-at-least-sixteen-bytes"), SourceURLNonce: []byte("123456789012"),
	}
	created, err := CreatePlatformDownloadEdgeTicket(&ticket, 5*time.Minute, time.Minute)
	require.NoError(t, err)
	require.True(t, created)
	claim, err := ClaimPlatformDownloadEdgeTicket(ticket.TokenSHA256, time.Second)
	require.NoError(t, err)
	require.NoError(t, DB.Exec(`
CREATE FUNCTION delay_download_completion_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_sleep(1.1);
  RETURN NEW;
END
$$`).Error)
	require.NoError(t, DB.Exec(`
CREATE TRIGGER delay_download_completion_insert
BEFORE INSERT ON platform_download_completion_events
FOR EACH ROW EXECUTE FUNCTION delay_download_completion_insert()`).Error)

	_, err = FinalizePlatformDownloadEdgeTransfer(claim.Ticket.ID, claim.Token, uuid.NewString(), 3)
	require.ErrorIs(t, err, ErrPlatformDownloadEdgeClaimLost)
	var eventCount, deliveryCount int64
	require.NoError(t, DB.Model(&PlatformDownloadCompletionEvent{}).Count(&eventCount).Error)
	require.NoError(t, DB.Model(&PlatformRelayExternalDelivery{}).Count(&deliveryCount).Error)
	assert.Zero(t, eventCount)
	assert.Zero(t, deliveryCount)

	eventID := uuid.NewString()
	require.NoError(t, DB.Transaction(func(tx *gorm.DB) error {
		_, createErr := CreatePlatformRelayExternalDeliveryTx(
			tx, PlatformRelayDeliveryKindDownloadCompletion, eventID,
			"edge-download-completion-"+eventID, 3,
		)
		return createErr
	}))
	oldClaim, err := ClaimPlatformRelayExternalDelivery(PlatformRelayDeliveryKindDownloadCompletion, time.Second)
	require.NoError(t, err)
	time.Sleep(1100 * time.Millisecond)
	newClaim, err := ClaimPlatformRelayExternalDelivery(PlatformRelayDeliveryKindDownloadCompletion, 30*time.Second)
	require.NoError(t, err)
	require.NotEqual(t, oldClaim.Token, newClaim.Token)

	builderCalled := false
	won, err := CompletePlatformDownloadCompletionDelivery(
		eventID, oldClaim.Token, http.StatusCreated,
		func(deliveredAt time.Time) (*PlatformDownloadCompletionProof, error) {
			builderCalled = true
			return nil, errors.New("stale proof builder must not run")
		},
	)
	require.NoError(t, err)
	assert.False(t, won)
	assert.False(t, builderCalled)
	var proofCount int64
	require.NoError(t, DB.Model(&PlatformDownloadCompletionProof{}).Count(&proofCount).Error)
	assert.Zero(t, proofCount)

	crossingEventID := uuid.NewString()
	require.NoError(t, DB.Transaction(func(tx *gorm.DB) error {
		_, createErr := CreatePlatformRelayExternalDeliveryTx(
			tx, PlatformRelayDeliveryKindDownloadCompletion, crossingEventID,
			"edge-download-completion-"+crossingEventID, 3,
		)
		return createErr
	}))
	crossingClaim, err := ClaimPlatformRelayExternalDelivery(PlatformRelayDeliveryKindDownloadCompletion, time.Second)
	require.NoError(t, err)
	proofPayload := `{"schema_version":1}`
	proofDigest := sha256.Sum256([]byte(proofPayload))
	won, err = CompletePlatformDownloadCompletionDelivery(
		crossingEventID, crossingClaim.Token, http.StatusCreated,
		func(deliveredAt time.Time) (*PlatformDownloadCompletionProof, error) {
			time.Sleep(1100 * time.Millisecond)
			return &PlatformDownloadCompletionProof{
				EventID: crossingEventID, CompletionID: uuid.NewString(), KeyID: "postgres-proof-key",
				PayloadJSON: proofPayload, PayloadSHA256: hex.EncodeToString(proofDigest[:]),
				SignatureBase64: base64.StdEncoding.EncodeToString(make([]byte, 64)), ProducedAt: deliveredAt,
			}, nil
		},
	)
	require.ErrorIs(t, err, ErrPlatformDownloadEdgeClaimLost)
	assert.False(t, won)
	require.NoError(t, DB.Model(&PlatformDownloadCompletionProof{}).Count(&proofCount).Error)
	assert.Zero(t, proofCount)
}
