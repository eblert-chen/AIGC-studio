//go:build integration

package platformrelay_test

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"sync"
	"testing"

	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type providerOnboardingConcurrentWriteResult struct {
	credential string
	view       service.ProviderOnboardingProviderView
	changed    bool
	err        error
}

func providerOnboardingCredentialFingerprint(credential string) string {
	digest := sha256.Sum256([]byte(credential))
	return hex.EncodeToString(digest[:])
}

func configureProviderOnboardingDevelopmentRuntime(t *testing.T) {
	t.Helper()
	setProviderChannelCredentialDevelopmentKeyring(t, providerChannelKeyringA)
	t.Setenv("APP_ENV", "development")
	t.Setenv("DEPLOYMENT_ENV", "development")
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_TLS_ATTESTATION_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_SECRET_FILES_REQUIRED", "false")
	t.Setenv("RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED", "false")
	t.Setenv("RELAY_SECRET_ISOLATION_GENERATION", "")
	t.Setenv("RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE", "")
	t.Setenv("RELAY_SECRET_ISOLATION_RECEIPT_FILE", "")
	t.Setenv("RELAY_SECRET_ISOLATION_COMMIT_FILE", "")
}

func runProviderOnboardingConcurrentWrites(
	provider string,
	credentials []string,
	expectedRevision string,
) []providerOnboardingConcurrentWriteResult {
	start := make(chan struct{})
	results := make(chan providerOnboardingConcurrentWriteResult, len(credentials))
	var group sync.WaitGroup
	for _, credential := range credentials {
		credential := credential
		group.Add(1)
		go func() {
			defer group.Done()
			<-start
			view, changed, err := service.PutProviderOnboardingCredential(
				provider,
				credential,
				"concurrent provider credential control revision gate",
				expectedRevision,
			)
			results <- providerOnboardingConcurrentWriteResult{
				credential: credential,
				view:       view,
				changed:    changed,
				err:        err,
			}
		}()
	}
	close(start)
	group.Wait()
	close(results)
	collected := make([]providerOnboardingConcurrentWriteResult, 0, len(credentials))
	for result := range results {
		collected = append(collected, result)
	}
	return collected
}

func TestProviderOnboardingPostgresConcurrentCreateAndRotateHaveOneEffectiveVersion(t *testing.T) {
	resetIntegrationState(t)
	configureProviderOnboardingDevelopmentRuntime(t)

	t.Run("concurrent create of the same credential is idempotent", func(t *testing.T) {
		credential := "google-concurrent-create-shared-" + uuid.NewString()
		fingerprint := providerOnboardingCredentialFingerprint(credential)
		results := runProviderOnboardingConcurrentWrites(
			service.CredentialLateProviderGoogleGemini,
			[]string{credential, credential},
			"",
		)
		require.Len(t, results, 2)
		changedCount := 0
		for _, result := range results {
			require.NoError(t, result.err)
			if result.changed {
				changedCount++
			}
			assert.Equal(t, fingerprint[:12], result.view.Credential.FingerprintPrefix)
			assert.NotEmpty(t, result.view.ControlRevision)
		}
		assert.Equal(t, 1, changedCount)
		assert.Equal(t, results[0].view.ControlRevision, results[1].view.ControlRevision)

		var channel struct {
			CredentialSetVersion string
		}
		require.NoError(t, integrationDB.Table("channels").
			Select("credential_set_version").
			Where("id = ?", model.ProviderOnboardingGoogleChannelID).
			Take(&channel).Error)
		assert.NotEmpty(t, channel.CredentialSetVersion)
		var current model.ProviderChannelCredentialSetVersion
		require.NoError(t, integrationDB.Where("credential_set_version = ?", channel.CredentialSetVersion).
			Take(&current).Error)
		assert.Equal(t, fingerprint, current.KeySetFingerprint)

		var candidateVersionCount int64
		require.NoError(t, integrationDB.Model(&model.ProviderChannelCredentialSetVersion{}).
			Where("channel_id = ? AND key_set_fingerprint = ?", model.ProviderOnboardingGoogleChannelID, fingerprint).
			Count(&candidateVersionCount).Error)
		assert.Equal(t, int64(1), candidateVersionCount)
	})

	t.Run("concurrent rotate accepts one control revision", func(t *testing.T) {
		initialCredential := "minimax-concurrent-initial-" + uuid.NewString()
		initial, changed, err := service.PutProviderOnboardingCredential(
			service.CredentialLateProviderMiniMax,
			initialCredential,
			"configure initial MiniMax credential for concurrency gate",
			"",
		)
		require.NoError(t, err)
		require.True(t, changed)
		var before struct {
			ControlRevision int64
		}
		require.NoError(t, integrationDB.Table("channels").Select("control_revision").
			Where("id = ?", model.ProviderOnboardingMiniMaxChannelID).
			Take(&before).Error)

		credentials := []string{
			"minimax-concurrent-rotate-alpha-" + uuid.NewString(),
			"minimax-concurrent-rotate-bravo-" + uuid.NewString(),
		}
		results := runProviderOnboardingConcurrentWrites(
			service.CredentialLateProviderMiniMax,
			credentials,
			initial.ControlRevision,
		)
		require.Len(t, results, 2)
		successes := make([]providerOnboardingConcurrentWriteResult, 0, 1)
		failures := make([]providerOnboardingConcurrentWriteResult, 0, 1)
		for _, result := range results {
			if result.err != nil {
				failures = append(failures, result)
				continue
			}
			successes = append(successes, result)
		}
		require.Len(t, successes, 1)
		require.Len(t, failures, 1)
		assert.True(t, successes[0].changed)
		assert.True(t, errors.Is(failures[0].err, model.ErrPlatformChannelControlRevisionConflict))

		var channel struct {
			CredentialSetVersion string
			ControlRevision      int64
		}
		require.NoError(t, integrationDB.Table("channels").
			Select("credential_set_version, control_revision").
			Where("id = ?", model.ProviderOnboardingMiniMaxChannelID).
			Take(&channel).Error)
		assert.Equal(t, before.ControlRevision+1, channel.ControlRevision)
		var current model.ProviderChannelCredentialSetVersion
		require.NoError(t, integrationDB.Where("credential_set_version = ?", channel.CredentialSetVersion).
			Take(&current).Error)
		winnerFingerprint := providerOnboardingCredentialFingerprint(successes[0].credential)
		loserFingerprint := providerOnboardingCredentialFingerprint(failures[0].credential)
		assert.Equal(t, winnerFingerprint, current.KeySetFingerprint)
		assert.Equal(t, winnerFingerprint[:12], successes[0].view.Credential.FingerprintPrefix)

		candidateFingerprints := []string{
			providerOnboardingCredentialFingerprint(initialCredential),
			providerOnboardingCredentialFingerprint(credentials[0]),
			providerOnboardingCredentialFingerprint(credentials[1]),
		}
		var candidateVersionCount int64
		require.NoError(t, integrationDB.Model(&model.ProviderChannelCredentialSetVersion{}).
			Where("channel_id = ? AND key_set_fingerprint IN ?", model.ProviderOnboardingMiniMaxChannelID, candidateFingerprints).
			Count(&candidateVersionCount).Error)
		assert.Equal(t, int64(2), candidateVersionCount, "only the initial and winning credential versions may exist")
		var loserVersionCount int64
		require.NoError(t, integrationDB.Model(&model.ProviderChannelCredentialSetVersion{}).
			Where("channel_id = ? AND key_set_fingerprint = ?", model.ProviderOnboardingMiniMaxChannelID, loserFingerprint).
			Count(&loserVersionCount).Error)
		assert.Zero(t, loserVersionCount, "the losing CAS must not leave an alternate new credential version")
	})
}
