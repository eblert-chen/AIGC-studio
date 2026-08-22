package model

import (
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const (
	PlatformArtifactUploadIntentPending     = "pending"
	PlatformArtifactUploadIntentClaimed     = "claimed"
	PlatformArtifactUploadIntentQuarantined = "quarantined"
	PlatformArtifactUploadIntentCleaned     = "cleaned"
	PlatformArtifactUploadIntentPublished   = "published"
	PlatformArtifactUploadIntentDeadLetter  = "dead_letter"

	// Cleaned tombstones are retained and reaped forever after this many
	// successful delete passes. Keep the migration and worker on one semantic
	// threshold so an upgraded row cannot silently lose perpetual cleanup.
	PlatformArtifactCleanupMinimumDeletePasses = 2
)

var (
	ErrPlatformArtifactUploadIntentFenced  = errors.New("artifact upload intent owner was fenced")
	ErrPlatformArtifactCleanupLeaseFenced  = errors.New("artifact cleanup lease was fenced")
	platformArtifactUploadStoreKindPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_]{0,31}$`)
	platformArtifactStoreBindingIDPattern  = regexp.MustCompile(`^[a-f0-9]{64}$`)
)

// PlatformArtifactUploadIntent is durable ownership for one token-scoped
// object key. It is inserted before storage Put, published in the same
// transaction as the generation job, and otherwise consumed by cleanup.
type PlatformArtifactUploadIntent struct {
	ID             string     `json:"id" gorm:"type:varchar(36);primaryKey"`
	JobID          string     `json:"job_id" gorm:"type:varchar(36);not null;uniqueIndex:uniq_platform_artifact_upload_token,priority:1;index"`
	TransferToken  string     `json:"-" gorm:"type:varchar(36);not null;uniqueIndex:uniq_platform_artifact_upload_token,priority:2;index"`
	ObjectKey      string     `json:"object_key" gorm:"type:varchar(160);not null;uniqueIndex"`
	StoreKind      string     `json:"store_kind" gorm:"type:varchar(32);not null"`
	StoreBindingID string     `json:"store_binding_id" gorm:"type:varchar(64);not null;index"`
	StoreVersionID string     `json:"-" gorm:"type:varchar(256)"`
	State          string     `json:"state" gorm:"type:varchar(16);not null;index:idx_platform_artifact_cleanup_available,priority:1"`
	Attempts       int        `json:"attempts" gorm:"not null"`
	DeletePasses   int        `json:"delete_passes" gorm:"not null;default:0"`
	AvailableAt    time.Time  `json:"available_at" gorm:"not null;index:idx_platform_artifact_cleanup_available,priority:2"`
	ClaimToken     string     `json:"-" gorm:"type:varchar(36);index"`
	ClaimExpiresAt time.Time  `json:"-" gorm:"index"`
	LastErrorCode  string     `json:"last_error_code" gorm:"type:varchar(160)"`
	PutCompletedAt *time.Time `json:"put_completed_at"`
	QuarantinedAt  *time.Time `json:"quarantined_at"`
	CleanedAt      *time.Time `json:"cleaned_at"`
	PublishedAt    *time.Time `json:"published_at"`
	CreatedAt      time.Time  `json:"created_at"`
	UpdatedAt      time.Time  `json:"updated_at"`
}

type PlatformArtifactCleanupClaim struct {
	Intent PlatformArtifactUploadIntent
	Token  string
}

type PlatformArtifactUploadIntentCounts struct {
	Pending                   int64
	Claimed                   int64
	Quarantined               int64
	Cleaned                   int64
	Published                 int64
	DeadLetter                int64
	BindingMismatchDeadLetter int64
	Retrying                  int64
	RetryingBindingMismatch   int64
	CleanedRetrying           int64
	CleanedBindingMismatch    int64
	Due                       int64
}

type platformPublishedArtifactReference struct {
	ObjectKey string `json:"object_key"`
}

// MigratePlatformArtifactUploadIntentStorage adds artifact cleanup evidence
// columns and conservatively upgrades only rows that crossed the schema
// boundary where delete_passes did not yet exist. Re-running this migration
// never repairs a zero value in the current schema: such a value may be real
// evidence and must not be overwritten by a blind startup backfill.
func MigratePlatformArtifactUploadIntentStorage() error {
	return MigratePlatformArtifactUploadIntentStorageWithDB(DB)
}

func MigratePlatformArtifactUploadIntentStorageWithDB(db *gorm.DB) error {
	if db == nil {
		return errors.New("database is not initialized")
	}
	migrator := db.Migrator()
	tableExisted := migrator.HasTable(&PlatformArtifactUploadIntent{})
	deletePassesExisted := false
	if tableExisted {
		columns, err := migrator.ColumnTypes(&PlatformArtifactUploadIntent{})
		if err != nil {
			return err
		}
		for _, column := range columns {
			if strings.EqualFold(column.Name(), "delete_passes") {
				deletePassesExisted = true
				break
			}
		}
	}
	if err := db.AutoMigrate(&PlatformArtifactUploadIntent{}); err != nil {
		return err
	}
	if !tableExisted || deletePassesExisted {
		return nil
	}
	return db.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		return tx.Model(&PlatformArtifactUploadIntent{}).Where(
			"state = ? AND delete_passes = ?",
			PlatformArtifactUploadIntentCleaned,
			0,
		).Updates(map[string]any{
			"delete_passes": PlatformArtifactCleanupMinimumDeletePasses,
			"updated_at":    now,
		}).Error
	})
}

// PlatformArtifactCleanupMaintenanceRequired reports whether storage cleanup
// must keep running independently of new generation admission. Published
// intents are the only rows that can never require deletion; permanent
// cleaned tombstones and operator-visible dead letters still bind the service
// to its historical artifact store.
func PlatformArtifactCleanupMaintenanceRequired() (bool, error) {
	if DB == nil {
		return false, errors.New("database is not initialized")
	}
	var intent PlatformArtifactUploadIntent
	err := DB.Select("id").
		Where("state <> ?", PlatformArtifactUploadIntentPublished).
		Take(&intent).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return false, nil
	}
	return err == nil, err
}

func CreatePlatformArtifactUploadIntent(
	jobID string,
	transferToken string,
	objectKey string,
	storeKind string,
	storeBindingID string,
) (*PlatformArtifactUploadIntent, error) {
	if err := validatePlatformArtifactUploadIntentIdentity(jobID, transferToken, objectKey, storeKind, storeBindingID); err != nil {
		return nil, err
	}
	var persisted PlatformArtifactUploadIntent
	err := DB.Transaction(func(tx *gorm.DB) error {
		// Generation transitions and artifact-intent transitions use the same
		// job -> intent lock order. Besides avoiding a Create/Complete deadlock,
		// this current locking read sees a creator that committed while waiting
		// under MySQL's default REPEATABLE READ isolation.
		var job PlatformGenerationJob
		if err := lockForUpdate(tx.Where("id = ?", jobID)).First(&job).Error; err != nil {
			return err
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		if job.Status != PlatformGenerationStatusTransferring ||
			job.TransferLeaseToken != transferToken ||
			!job.TransferLeaseExpiresAt.After(now) {
			return ErrPlatformArtifactUploadIntentFenced
		}

		existingErr := lockForUpdate(tx.Where(
			"job_id = ? AND transfer_token = ?",
			jobID,
			transferToken,
		)).First(&persisted).Error
		if existingErr == nil {
			if persisted.ObjectKey != objectKey ||
				persisted.StoreKind != storeKind ||
				persisted.StoreBindingID != storeBindingID ||
				persisted.State != PlatformArtifactUploadIntentPending {
				return ErrPlatformArtifactUploadIntentFenced
			}
			return nil
		}
		if !errors.Is(existingErr, gorm.ErrRecordNotFound) {
			return existingErr
		}

		persisted = PlatformArtifactUploadIntent{
			ID:             uuid.NewString(),
			JobID:          jobID,
			TransferToken:  transferToken,
			ObjectKey:      objectKey,
			StoreKind:      storeKind,
			StoreBindingID: storeBindingID,
			State:          PlatformArtifactUploadIntentPending,
			AvailableAt:    job.TransferLeaseExpiresAt,
			CreatedAt:      now,
			UpdatedAt:      now,
		}
		return tx.Create(&persisted).Error
	})
	return &persisted, err
}

// RecordPlatformArtifactUploadPut stores the strongest evidence returned by
// the backing store after Put. It deliberately does not require a live
// transfer lease: a slow Put can finish after its owner was fenced, and that
// late acknowledgement is cleanup evidence rather than authority to publish.
// A Put observed in any non-published cleanup state re-arms the durable intent
// from a fresh retry budget so a replacement worker deletes the late object.
// A truly published intent is never made cleanup-eligible again.
func RecordPlatformArtifactUploadPut(
	jobID string,
	transferToken string,
	objectKey string,
	storeVersionID string,
) (bool, error) {
	if !platformArtifactCanonicalUUID(jobID) || !platformArtifactCanonicalUUID(transferToken) ||
		strings.TrimSpace(objectKey) == "" || !platformArtifactStoreVersionIDValid(storeVersionID) {
		return false, fmt.Errorf("artifact upload Put evidence is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		// Preserve the global generation job -> artifact intent lock order.
		var job PlatformGenerationJob
		if err := lockForUpdate(tx.Where("id = ?", jobID)).First(&job).Error; err != nil {
			return err
		}
		var intent PlatformArtifactUploadIntent
		if err := lockForUpdate(tx.Where(
			"job_id = ? AND transfer_token = ? AND object_key = ?",
			jobID,
			transferToken,
			objectKey,
		)).First(&intent).Error; err != nil {
			return err
		}
		if intent.StoreVersionID != "" && intent.StoreVersionID != storeVersionID {
			return ErrPlatformArtifactUploadIntentFenced
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		updates := map[string]any{
			"put_completed_at": now,
			"updated_at":       now,
		}
		if storeVersionID != "" {
			updates["store_version_id"] = storeVersionID
		}
		switch intent.State {
		case PlatformArtifactUploadIntentPublished:
			// Evidence may arrive after a lost acknowledgement, but publication is
			// terminal and cleanup must never be re-enabled for this object.
		case PlatformArtifactUploadIntentPending,
			PlatformArtifactUploadIntentClaimed,
			PlatformArtifactUploadIntentQuarantined,
			PlatformArtifactUploadIntentCleaned,
			PlatformArtifactUploadIntentDeadLetter:
			lateCleanupPut := intent.State != PlatformArtifactUploadIntentPending ||
				intent.Attempts > 0 || intent.DeletePasses > 0 || intent.LastErrorCode != ""
			if lateCleanupPut {
				updates["state"] = PlatformArtifactUploadIntentPending
				updates["attempts"] = 0
				updates["delete_passes"] = 0
				updates["available_at"] = now
				updates["claim_token"] = ""
				updates["claim_expires_at"] = nil
				updates["last_error_code"] = "late_put_after_cleanup"
				updates["quarantined_at"] = nil
				updates["cleaned_at"] = nil
			}
		case "":
			return ErrPlatformArtifactUploadIntentFenced
		default:
			return fmt.Errorf("artifact upload intent state is invalid")
		}
		result := tx.Model(&intent).Updates(updates)
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

// SchedulePlatformArtifactUploadCleanup makes an unpublished intent eligible
// immediately. A live matching transfer lease still blocks cleanup at claim
// time, so scheduling cannot race a valid publisher.
func SchedulePlatformArtifactUploadCleanup(jobID string, transferToken string) (bool, error) {
	if !platformArtifactCanonicalUUID(jobID) || !platformArtifactCanonicalUUID(transferToken) {
		return false, fmt.Errorf("artifact upload cleanup identity is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		result := tx.Model(&PlatformArtifactUploadIntent{}).Where(
			"job_id = ? AND transfer_token = ? AND state = ?",
			jobID,
			transferToken,
			PlatformArtifactUploadIntentPending,
		).Updates(map[string]any{
			"available_at": now,
			"updated_at":   now,
		})
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

func ClaimPlatformArtifactCleanup(lease time.Duration) (*PlatformArtifactCleanupClaim, error) {
	if lease < time.Second || lease > 24*time.Hour {
		return nil, fmt.Errorf("artifact cleanup lease is invalid")
	}
	claim := &PlatformArtifactCleanupClaim{}
	claimed := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		for {
			selectionNow, err := GetDBTimeTx(tx)
			if err != nil {
				return err
			}
			var candidate PlatformArtifactUploadIntent
			query := tx.Where(
				"((state IN ? AND available_at <= ?) OR (state = ? AND claim_expires_at <= ?))",
				[]string{
					PlatformArtifactUploadIntentPending,
					PlatformArtifactUploadIntentQuarantined,
					PlatformArtifactUploadIntentCleaned,
				},
				selectionNow,
				PlatformArtifactUploadIntentClaimed,
				selectionNow,
			).Order("available_at ASC, created_at ASC, id ASC")
			if err := query.First(&candidate).Error; err != nil {
				if errors.Is(err, gorm.ErrRecordNotFound) {
					// Commit any live-lease deferrals performed earlier in this
					// transaction, then report an empty queue to the caller.
					return nil
				}
				return err
			}

			var job PlatformGenerationJob
			jobErr := lockForUpdate(tx.Where("id = ?", candidate.JobID)).First(&job).Error
			if jobErr != nil && !errors.Is(jobErr, gorm.ErrRecordNotFound) {
				return jobErr
			}
			claim.Intent = PlatformArtifactUploadIntent{}
			claim.Token = ""
			if err := lockForUpdate(tx.Where("id = ?", candidate.ID)).First(&claim.Intent).Error; err != nil {
				if errors.Is(err, gorm.ErrRecordNotFound) {
					continue
				}
				return err
			}
			now, err := GetDBTimeTx(tx)
			if err != nil {
				return err
			}
			eligible := (claim.Intent.State == PlatformArtifactUploadIntentPending ||
				claim.Intent.State == PlatformArtifactUploadIntentQuarantined ||
				claim.Intent.State == PlatformArtifactUploadIntentCleaned) && !claim.Intent.AvailableAt.After(now)
			eligible = eligible || claim.Intent.State == PlatformArtifactUploadIntentClaimed && !claim.Intent.ClaimExpiresAt.After(now)
			if !eligible {
				continue
			}
			if jobErr == nil && job.Status == PlatformGenerationStatusSucceeded {
				published, parseErr := platformGenerationPublishesArtifact(job.OutputsJSON, claim.Intent.ObjectKey)
				if parseErr != nil {
					if err := tx.Model(&claim.Intent).Updates(map[string]any{
						"state":            PlatformArtifactUploadIntentDeadLetter,
						"claim_token":      "",
						"claim_expires_at": nil,
						"last_error_code":  "published_outputs_unreadable",
						"updated_at":       now,
					}).Error; err != nil {
						return err
					}
					continue
				}
				if published {
					if err := tx.Model(&claim.Intent).Updates(map[string]any{
						"state":            PlatformArtifactUploadIntentPublished,
						"claim_token":      "",
						"claim_expires_at": nil,
						"last_error_code":  "",
						"published_at":     now,
						"updated_at":       now,
					}).Error; err != nil {
						return err
					}
					continue
				}
			}
			if jobErr == nil &&
				job.Status == PlatformGenerationStatusTransferring &&
				job.TransferLeaseToken == claim.Intent.TransferToken &&
				job.TransferLeaseExpiresAt.After(now) {
				updates := map[string]any{
					"available_at": job.TransferLeaseExpiresAt,
					"updated_at":   now,
				}
				if claim.Intent.State == PlatformArtifactUploadIntentClaimed {
					updates["state"] = PlatformArtifactUploadIntentPending
					updates["claim_token"] = ""
					updates["claim_expires_at"] = nil
				}
				if err := tx.Model(&claim.Intent).Updates(updates).Error; err != nil {
					return err
				}
				continue
			}

			claim.Token = uuid.NewString()
			claim.Intent.State = PlatformArtifactUploadIntentClaimed
			claim.Intent.Attempts++
			claim.Intent.ClaimToken = claim.Token
			claim.Intent.ClaimExpiresAt = now.Add(lease)
			claim.Intent.UpdatedAt = now
			result := tx.Model(&PlatformArtifactUploadIntent{}).Where(
				"id = ? AND state IN ?",
				claim.Intent.ID,
				[]string{
					PlatformArtifactUploadIntentPending,
					PlatformArtifactUploadIntentQuarantined,
					PlatformArtifactUploadIntentCleaned,
					PlatformArtifactUploadIntentClaimed,
				},
			).Updates(map[string]any{
				"state":            PlatformArtifactUploadIntentClaimed,
				"attempts":         gorm.Expr("attempts + 1"),
				"claim_token":      claim.Token,
				"claim_expires_at": claim.Intent.ClaimExpiresAt,
				"last_error_code":  "",
				"updated_at":       now,
			})
			if result.Error != nil {
				return result.Error
			}
			if result.RowsAffected != 1 {
				continue
			}
			claimed = true
			return nil
		}
	})
	if err != nil {
		return nil, err
	}
	if !claimed {
		return nil, gorm.ErrRecordNotFound
	}
	return claim, nil
}

func CompletePlatformArtifactCleanup(
	intentID string,
	token string,
	quarantine time.Duration,
	periodicReap time.Duration,
	minimumDeletePasses int,
) (bool, error) {
	if !platformArtifactCanonicalUUID(intentID) || !platformArtifactCanonicalUUID(token) ||
		quarantine < time.Second || quarantine > 24*time.Hour ||
		periodicReap < time.Minute || periodicReap > 30*24*time.Hour ||
		minimumDeletePasses < 2 || minimumDeletePasses > 10 {
		return false, fmt.Errorf("artifact cleanup identity is invalid")
	}
	won := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var intent PlatformArtifactUploadIntent
		if err := lockForUpdate(tx.Where(
			"id = ? AND state = ? AND claim_token = ?",
			intentID,
			PlatformArtifactUploadIntentClaimed,
			token,
		)).First(&intent).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		if !intent.ClaimExpiresAt.After(now) {
			return nil
		}
		deletePasses := intent.DeletePasses + 1
		state := PlatformArtifactUploadIntentQuarantined
		availableAt := now.Add(quarantine)
		updates := map[string]any{
			"state":            state,
			"delete_passes":    deletePasses,
			"attempts":         0,
			"available_at":     availableAt,
			"claim_token":      "",
			"claim_expires_at": nil,
			"last_error_code":  "",
			"updated_at":       now,
		}
		if intent.QuarantinedAt == nil {
			updates["quarantined_at"] = now
		}
		if deletePasses >= minimumDeletePasses {
			updates["state"] = PlatformArtifactUploadIntentCleaned
			updates["available_at"] = now.Add(periodicReap)
			if intent.CleanedAt == nil {
				updates["cleaned_at"] = now
			}
		}
		result := tx.Model(&PlatformArtifactUploadIntent{}).Where(
			"id = ? AND state = ? AND claim_token = ?",
			intentID,
			PlatformArtifactUploadIntentClaimed,
			token,
		).Updates(updates)
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, err
}

func ReleasePlatformArtifactCleanup(
	intentID string,
	token string,
	maxAttempts int,
	delay time.Duration,
	errorCode string,
	retryForever bool,
) (bool, bool, error) {
	if !platformArtifactCanonicalUUID(intentID) || !platformArtifactCanonicalUUID(token) ||
		maxAttempts < 1 || maxAttempts > 100 || delay < 0 || delay > 24*time.Hour ||
		strings.TrimSpace(errorCode) == "" || len(errorCode) > 160 {
		return false, false, fmt.Errorf("artifact cleanup release is invalid")
	}
	won := false
	deadLetter := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		var intent PlatformArtifactUploadIntent
		query := tx.Where(
			"id = ? AND state = ? AND claim_token = ?",
			intentID,
			PlatformArtifactUploadIntentClaimed,
			token,
		)
		if err := lockForUpdate(query).First(&intent).Error; err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) {
				return nil
			}
			return err
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		if !intent.ClaimExpiresAt.After(now) {
			return nil
		}
		state := PlatformArtifactUploadIntentPending
		availableAt := now.Add(delay)
		if retryForever {
			// Once the mandatory initial deletes have completed, this row is a
			// permanent tombstone. An OBS outage or binding change must remain
			// visible and retryable forever instead of converting it into a
			// terminal dead letter that can admit a later orphan.
			state = PlatformArtifactUploadIntentCleaned
		} else if intent.Attempts >= maxAttempts {
			state = PlatformArtifactUploadIntentDeadLetter
			availableAt = now
			deadLetter = true
		}
		result := tx.Model(&intent).Updates(map[string]any{
			"state":            state,
			"available_at":     availableAt,
			"claim_token":      "",
			"claim_expires_at": nil,
			"last_error_code":  errorCode,
			"updated_at":       now,
		})
		won = result.RowsAffected == 1
		return result.Error
	})
	return won, deadLetter, err
}

func GetPlatformArtifactUploadIntentCounts() (PlatformArtifactUploadIntentCounts, error) {
	counts := PlatformArtifactUploadIntentCounts{}
	err := DB.Transaction(func(tx *gorm.DB) error {
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		rows := []struct {
			State string
			Count int64
		}{}
		if err := tx.Model(&PlatformArtifactUploadIntent{}).
			Select("state, COUNT(*) AS count").
			Group("state").
			Scan(&rows).Error; err != nil {
			return err
		}
		for _, row := range rows {
			switch row.State {
			case PlatformArtifactUploadIntentPending:
				counts.Pending = row.Count
			case PlatformArtifactUploadIntentClaimed:
				counts.Claimed = row.Count
			case PlatformArtifactUploadIntentQuarantined:
				counts.Quarantined = row.Count
			case PlatformArtifactUploadIntentCleaned:
				counts.Cleaned = row.Count
			case PlatformArtifactUploadIntentPublished:
				counts.Published = row.Count
			case PlatformArtifactUploadIntentDeadLetter:
				counts.DeadLetter = row.Count
			default:
				return fmt.Errorf("artifact upload intent state is invalid")
			}
		}
		if err := tx.Model(&PlatformArtifactUploadIntent{}).Where(
			"state = ? AND last_error_code = ?",
			PlatformArtifactUploadIntentDeadLetter,
			"artifact_store_binding_changed",
		).Count(&counts.BindingMismatchDeadLetter).Error; err != nil {
			return err
		}
		if err := tx.Model(&PlatformArtifactUploadIntent{}).Where(
			"state IN ? AND last_error_code <> ?",
			[]string{
				PlatformArtifactUploadIntentPending,
				PlatformArtifactUploadIntentQuarantined,
			},
			"",
		).Count(&counts.Retrying).Error; err != nil {
			return err
		}
		if err := tx.Model(&PlatformArtifactUploadIntent{}).Where(
			"state IN ? AND last_error_code = ?",
			[]string{
				PlatformArtifactUploadIntentPending,
				PlatformArtifactUploadIntentQuarantined,
			},
			"artifact_store_binding_changed",
		).Count(&counts.RetryingBindingMismatch).Error; err != nil {
			return err
		}
		if err := tx.Model(&PlatformArtifactUploadIntent{}).Where(
			"state = ? AND last_error_code <> ?",
			PlatformArtifactUploadIntentCleaned,
			"",
		).Count(&counts.CleanedRetrying).Error; err != nil {
			return err
		}
		if err := tx.Model(&PlatformArtifactUploadIntent{}).Where(
			"state = ? AND last_error_code = ?",
			PlatformArtifactUploadIntentCleaned,
			"artifact_store_binding_changed",
		).Count(&counts.CleanedBindingMismatch).Error; err != nil {
			return err
		}
		return tx.Model(&PlatformArtifactUploadIntent{}).Where(
			"(state IN ? AND available_at <= ?) OR (state = ? AND claim_expires_at <= ?)",
			[]string{
				PlatformArtifactUploadIntentPending,
				PlatformArtifactUploadIntentQuarantined,
				PlatformArtifactUploadIntentCleaned,
			},
			now,
			PlatformArtifactUploadIntentClaimed,
			now,
		).Count(&counts.Due).Error
	})
	return counts, err
}

func platformGenerationPublishesArtifact(outputsJSON string, objectKey string) (bool, error) {
	var outputs []platformPublishedArtifactReference
	if strings.TrimSpace(outputsJSON) == "" || common.Unmarshal([]byte(outputsJSON), &outputs) != nil {
		return false, fmt.Errorf("published generation outputs are invalid")
	}
	for _, output := range outputs {
		if output.ObjectKey == objectKey {
			return true, nil
		}
	}
	return false, nil
}

func validatePlatformArtifactUploadIntentIdentity(
	jobID string,
	transferToken string,
	objectKey string,
	storeKind string,
	storeBindingID string,
) error {
	if !platformArtifactCanonicalUUID(jobID) || !platformArtifactCanonicalUUID(transferToken) {
		return fmt.Errorf("artifact upload intent token identity is invalid")
	}
	parts := strings.Split(objectKey, "/")
	if len(parts) != 4 || parts[0] != "outputs" || len(objectKey) > 160 {
		return fmt.Errorf("artifact upload intent object key is invalid")
	}
	for _, part := range parts[1:] {
		if !platformArtifactCanonicalUUID(part) {
			return fmt.Errorf("artifact upload intent object key is invalid")
		}
	}
	if !platformArtifactUploadStoreKindPattern.MatchString(storeKind) {
		return fmt.Errorf("artifact upload intent store kind is invalid")
	}
	if !platformArtifactStoreBindingIDPattern.MatchString(storeBindingID) {
		return fmt.Errorf("artifact upload intent store binding is invalid")
	}
	return nil
}

func platformArtifactCanonicalUUID(value string) bool {
	parsed, err := uuid.Parse(value)
	return err == nil && parsed.String() == value
}

func platformArtifactStoreVersionIDValid(value string) bool {
	return len(value) <= 256 && value == strings.TrimSpace(value) &&
		!strings.ContainsAny(value, "\r\n\x00\t ")
}
