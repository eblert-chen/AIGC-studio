package service

import (
	"crypto/sha256"
	"crypto/subtle"
	"errors"
	"sort"

	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
	"gorm.io/gorm/logger"
)

const (
	PlatformRelayServicePrincipalRotationSchemaVersion = 1

	PlatformRelayServicePrincipalRotationStateRotated  = "rotated"
	PlatformRelayServicePrincipalRotationStateReplayed = "replayed"
)

var ErrProtectedPlatformRelayPrincipalRotationConflict = errors.New("protected Platform Relay service principal rotation conflicts with existing state")

type PlatformRelayServicePrincipalRotationResult struct {
	AttemptID    string
	State        string
	Count        int
	RotatedCount int
}

type platformRelayServicePrincipalRotationPlan struct {
	current      map[string]protectedPlatformRelayExpectedPrincipal
	desired      map[string]protectedPlatformRelayExpectedPrincipal
	purposes     []string
	rotatedCount int
}

// buildPlatformRelayServicePrincipalRotationPlan validates the complete
// current/desired pair before any database is opened. Both files use the same
// strict, sorted v1 principal document. The identity set is immutable during a
// rotation: only upstream tokens may differ.
func buildPlatformRelayServicePrincipalRotationPlan(
	currentInputs []PlatformRelayServicePrincipalProvisionInput,
	desiredInputs []PlatformRelayServicePrincipalProvisionInput,
) (platformRelayServicePrincipalRotationPlan, error) {
	plan := platformRelayServicePrincipalRotationPlan{}
	current, currentErr := buildProtectedPlatformRelayExpectedPrincipals(currentInputs)
	desired, desiredErr := buildProtectedPlatformRelayExpectedPrincipals(desiredInputs)
	if currentErr != nil || desiredErr != nil || len(currentInputs) != len(desiredInputs) {
		return plan, ErrProtectedPlatformRelayPrincipalRotationConflict
	}

	currentKeyOwner := make(map[[sha256.Size]byte]string, len(current))
	for index := range currentInputs {
		before := currentInputs[index]
		after := desiredInputs[index]
		if before.ClientID != after.ClientID || before.TenantID != after.TenantID {
			return platformRelayServicePrincipalRotationPlan{}, ErrProtectedPlatformRelayPrincipalRotationConflict
		}
		purpose := protectedPlatformRelayTokenPurpose(before.ClientID, before.TenantID)
		currentPrincipal, currentOK := current[purpose]
		desiredPrincipal, desiredOK := desired[purpose]
		if !currentOK || !desiredOK ||
			currentPrincipal.clientID != desiredPrincipal.clientID ||
			currentPrincipal.tenantID != desiredPrincipal.tenantID {
			return platformRelayServicePrincipalRotationPlan{}, ErrProtectedPlatformRelayPrincipalRotationConflict
		}
		currentKeyOwner[sha256.Sum256([]byte(currentPrincipal.key))] = purpose
		plan.purposes = append(plan.purposes, purpose)
	}

	for _, purpose := range plan.purposes {
		before := current[purpose]
		after := desired[purpose]
		beforeDigest := sha256.Sum256([]byte(before.key))
		afterDigest := sha256.Sum256([]byte(after.key))
		if owner, reused := currentKeyOwner[afterDigest]; reused && owner != purpose {
			// A simultaneous swap or reassignment is not a token rotation. Requiring
			// every changed key to be fresh also avoids immediate unique-index
			// failures whose database detail could otherwise contain a credential.
			return platformRelayServicePrincipalRotationPlan{}, ErrProtectedPlatformRelayPrincipalRotationConflict
		}
		if subtle.ConstantTimeCompare(beforeDigest[:], afterDigest[:]) != 1 {
			plan.rotatedCount++
		}
	}
	if plan.rotatedCount == 0 {
		return platformRelayServicePrincipalRotationPlan{}, ErrProtectedPlatformRelayPrincipalRotationConflict
	}
	plan.current = current
	plan.desired = desired
	return plan, nil
}

// RotateProtectedPlatformRelayServicePrincipals atomically replaces only the
// token values of an exact protected principal set. The offline caller must
// already hold exclusive lifecycle gate A. PostgreSQL mutations additionally
// acquire transaction gate B and the principal namespace advisory lock.
//
// If the database already equals desired, the exact current/desired request is
// an idempotent replay. Every other precondition mismatch fails closed. Raw
// credentials are used only through a silent GORM session, and every database
// error is collapsed to the secret-free conflict sentinel.
func RotateProtectedPlatformRelayServicePrincipals(
	attemptID string,
	currentInputs []PlatformRelayServicePrincipalProvisionInput,
	desiredInputs []PlatformRelayServicePrincipalProvisionInput,
) (PlatformRelayServicePrincipalRotationResult, error) {
	result := PlatformRelayServicePrincipalRotationResult{
		AttemptID: attemptID,
		Count:     len(desiredInputs),
	}
	// The attempt identity is the independent cryptographic RunID of the
	// already-verified isolation receipt. It must never be derived from either
	// principal document: a public content digest would be an offline secret
	// oracle. Replaying the same receipt therefore preserves the ID, while a
	// fresh validation creates a distinct attempt even for identical inputs.
	if !platformRelaySecretIsolationRunIDPattern.MatchString(attemptID) ||
		!model.RelayDatabaseRoleAttestationRequired() || model.DB == nil {
		return result, ErrProtectedPlatformRelayPrincipalRotationConflict
	}
	plan, err := buildPlatformRelayServicePrincipalRotationPlan(currentInputs, desiredInputs)
	if err != nil {
		return result, ErrProtectedPlatformRelayPrincipalRotationConflict
	}
	result.RotatedCount = plan.rotatedCount
	dialect := model.DB.Dialector.Name()
	if dialect != "postgres" && dialect != "sqlite" {
		return result, ErrProtectedPlatformRelayPrincipalRotationConflict
	}

	err = model.DB.Transaction(func(tx *gorm.DB) error {
		if tx.Dialector.Name() == "postgres" {
			if err := model.AcquireRelayLifecycleMutationTransactionLock(tx); err != nil {
				return err
			}
			if err := tx.Exec(
				"SELECT pg_catalog.pg_advisory_xact_lock(?)",
				protectedPlatformRelayPrincipalProvisionLock,
			).Error; err != nil {
				return ErrProtectedPlatformRelayPrincipalRotationConflict
			}
			// A missing unique key has no row to lock. Exclude every ordinary
			// INSERT/UPDATE/DELETE before inspecting desired keys, otherwise a
			// concurrent token writer can win the gap and make PostgreSQL emit a
			// unique-constraint DETAIL containing the new credential. This table
			// lock is deliberately acquired before any application-table row lock.
			if err := tx.Exec(
				"LOCK TABLE public.tokens IN SHARE ROW EXCLUSIVE MODE",
			).Error; err != nil {
				return ErrProtectedPlatformRelayPrincipalRotationConflict
			}
		}
		if err := installProtectedPlatformRelaySecretSQLLoggingPolicyTx(tx); err != nil {
			return err
		}
		if err := verifyProtectedPlatformRelayRootSetupTx(tx); err != nil {
			return err
		}

		// Lock the complete reserved namespace before deciding whether this is a
		// fresh rotation or the exact post-commit replay of one. This lock also
		// serializes row IDs used by the fenced updates below.
		var lockedTokens []model.Token
		if err := tx.Unscoped().Clauses(clause.Locking{Strength: "UPDATE"}).
			Select("id, user_id, name, key").
			Where("name LIKE ?", model.PlatformRelayServicePrincipalTokenNamePrefix+"%").
			Order("name ASC").Limit(protectedPlatformRelayPrincipalMaxClients + 1).
			Find(&lockedTokens).Error; err != nil {
			return ErrProtectedPlatformRelayPrincipalRotationConflict
		}
		var lockedUsers []model.User
		if err := tx.Unscoped().Clauses(clause.Locking{Strength: "UPDATE"}).
			Select("id").Where(
			"remark = ? OR lower(substr(username, 1, ?)) = ?",
			model.PlatformRelayServicePrincipalRemark,
			len(model.PlatformRelayServicePrincipalUsernamePrefix),
			model.PlatformRelayServicePrincipalUsernamePrefix,
		).Limit(protectedPlatformRelayPrincipalMaxClients + 1).Find(&lockedUsers).Error; err != nil {
			return ErrProtectedPlatformRelayPrincipalRotationConflict
		}

		if err := validateProtectedPlatformRelayServicePrincipalsTx(tx, plan.current); err != nil {
			if desiredErr := validateProtectedPlatformRelayServicePrincipalsTx(tx, plan.desired); desiredErr == nil {
				result.State = PlatformRelayServicePrincipalRotationStateReplayed
				return nil
			}
			return ErrProtectedPlatformRelayPrincipalRotationConflict
		}

		lockedByPurpose := make(map[string]model.Token, len(lockedTokens))
		for _, token := range lockedTokens {
			if _, duplicate := lockedByPurpose[token.Name]; duplicate {
				return ErrProtectedPlatformRelayPrincipalRotationConflict
			}
			lockedByPurpose[token.Name] = token
		}
		if len(lockedByPurpose) != len(plan.current) {
			return ErrProtectedPlatformRelayPrincipalRotationConflict
		}

		// Check the unique key namespace before the first update. Unscoped is
		// required because a soft-deleted ordinary token still owns its key.
		desiredKeys := make([]string, 0, len(plan.purposes))
		for _, purpose := range plan.purposes {
			desiredKeys = append(desiredKeys, plan.desired[purpose].key)
		}
		secretTx := tx.Session(&gorm.Session{Logger: logger.Default.LogMode(logger.Silent)})
		var keyOwners []model.Token
		if err := secretTx.Unscoped().Clauses(clause.Locking{Strength: "UPDATE"}).
			Select("id, name, key").Where("key IN ?", desiredKeys).
			Limit(len(desiredKeys) + 1).Find(&keyOwners).Error; err != nil {
			return ErrProtectedPlatformRelayPrincipalRotationConflict
		}
		for _, owner := range keyOwners {
			desired, expectedPurpose := plan.desired[owner.Name]
			current, currentPurpose := plan.current[owner.Name]
			if !expectedPurpose || !currentPurpose {
				return ErrProtectedPlatformRelayPrincipalRotationConflict
			}
			desiredDigest := sha256.Sum256([]byte(desired.key))
			ownerDigest := sha256.Sum256([]byte(owner.Key))
			currentDigest := sha256.Sum256([]byte(current.key))
			if subtle.ConstantTimeCompare(desiredDigest[:], ownerDigest[:]) != 1 ||
				subtle.ConstantTimeCompare(currentDigest[:], desiredDigest[:]) != 1 {
				return ErrProtectedPlatformRelayPrincipalRotationConflict
			}
		}

		purposes := append([]string(nil), plan.purposes...)
		sort.Strings(purposes)
		for _, purpose := range purposes {
			before := plan.current[purpose]
			after := plan.desired[purpose]
			beforeDigest := sha256.Sum256([]byte(before.key))
			afterDigest := sha256.Sum256([]byte(after.key))
			if subtle.ConstantTimeCompare(beforeDigest[:], afterDigest[:]) == 1 {
				continue
			}
			locked, ok := lockedByPurpose[purpose]
			if !ok {
				return ErrProtectedPlatformRelayPrincipalRotationConflict
			}
			update := secretTx.Unscoped().Model(&model.Token{}).
				Where("id = ? AND name = ?", locked.Id, purpose).
				UpdateColumn("key", after.key)
			if update.Error != nil || update.RowsAffected != 1 {
				return ErrProtectedPlatformRelayPrincipalRotationConflict
			}
		}
		if err := validateProtectedPlatformRelayServicePrincipalsTx(tx, plan.desired); err != nil {
			return ErrProtectedPlatformRelayPrincipalRotationConflict
		}
		result.State = PlatformRelayServicePrincipalRotationStateRotated
		return nil
	})
	if err != nil || (result.State != PlatformRelayServicePrincipalRotationStateRotated &&
		result.State != PlatformRelayServicePrincipalRotationStateReplayed) {
		return PlatformRelayServicePrincipalRotationResult{
			AttemptID: attemptID, Count: len(desiredInputs), RotatedCount: plan.rotatedCount,
		}, ErrProtectedPlatformRelayPrincipalRotationConflict
	}
	return result, nil
}
