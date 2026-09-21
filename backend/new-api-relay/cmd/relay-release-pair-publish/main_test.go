package main

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

func releasePairFixture(t *testing.T) (publishOptions, []byte, []byte) {
	t.Helper()
	directory := t.TempDir()
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkImageGenerationV1)
	require.True(t, ok)
	release := generationrelease.Release{
		APIVersion:             generationrelease.APIVersion,
		Kind:                   generationrelease.Kind,
		ReleaseID:              "seedream-5-pair-r1",
		PublicModelID:          constant.PlatformGenerationPublicSeedream50Model,
		ProviderModelID:        constant.PlatformGenerationArkSeedream50Model,
		AdapterProfileID:       profile.ID,
		AdapterProfileRevision: profile.Revision,
		LegacyPublicAliases:    []string{constant.PlatformGenerationLegacySeedream50LitePublicAlias},
		Capability:             profile.Capability,
		Audit: generationrelease.Audit{
			CreatedAt: "2026-08-29T00:00:00Z",
			CreatedBy: "release-authority",
			Reason:    "pair publication test",
			SourceRef: "test/pair-publication",
		},
		Attestation: &generationrelease.Attestation{
			Algorithm: generationrelease.Algorithm,
			KeyID:     "model-release-test",
			SignedAt:  "2026-08-29T00:00:00Z",
			NotAfter:  "2026-09-29T00:00:00Z",
			Signature: base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{1}, ed25519.SignatureSize)),
		},
	}
	modelBytes, err := common.Marshal(release)
	require.NoError(t, err)
	routesBytes, err := common.Marshal(map[string][]service.PlatformRelayRouteDeclaration{
		constant.PlatformGenerationPublicSeedream50Model: {
			{
				RouteID:           "seedream-pair-route",
				ProviderName:      "volcengine-ark",
				AccountID:         "ark-pair-account",
				ChannelID:         17,
				NativeChannelType: constant.ChannelTypeVolcEngine,
				KeyFingerprint:    string(bytes.Repeat([]byte{'a'}, 64)),
				ChannelClass:      service.PlatformChannelClassOfficial,
				UpstreamModel:     release.ProviderModelID,
				RPMLimit:          10,
				ActiveTaskLimit:   2,
				Capabilities:      profile.Capability,
				CapabilityProfile: profile.ID,
				ModelRelease:      &release,
				Acceptance: &service.PlatformRouteAcceptanceEvidence{
					Manifest: service.PlatformRouteAcceptanceManifest{
						NotBefore: "2026-08-29T00:00:00Z",
						NotAfter:  "2026-09-29T00:00:00Z",
					},
					Signature: base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{2}, ed25519.SignatureSize)),
				},
			},
		},
	})
	require.NoError(t, err)
	options := publishOptions{
		modelSource:       filepath.Join(directory, "model.stage.json"),
		routesSource:      filepath.Join(directory, "routes.stage.json"),
		modelDestination:  filepath.Join(directory, "model.signed.json"),
		routesDestination: filepath.Join(directory, "routes.signed.json"),
	}
	require.NoError(t, os.WriteFile(options.modelSource, modelBytes, 0o600))
	require.NoError(t, os.WriteFile(options.routesSource, routesBytes, 0o600))
	return options, modelBytes, routesBytes
}

func TestPublishReleasePairCommitsRoutesLastAndSupportsExactReplay(t *testing.T) {
	options, modelBytes, routesBytes := releasePairFixture(t)
	require.NoError(t, publishReleasePair(options, defaultPublishOperations()))
	require.FileExists(t, options.modelDestination)
	require.FileExists(t, options.routesDestination)
	require.Equal(t, modelBytes, mustReadFile(t, options.modelDestination))
	require.Equal(t, routesBytes, mustReadFile(t, options.routesDestination))
	require.NoError(t, publishReleasePair(options, defaultPublishOperations()), "an exact committed pair is idempotent")
	requireNoReleasePairTemps(t, filepath.Dir(options.modelDestination))
}

func TestPublishReleasePairSecondCommitFailureLeavesNoOwnedFinalArtifact(t *testing.T) {
	options, _, _ := releasePairFixture(t)
	operations := defaultPublishOperations()
	linkCalls := 0
	operations.link = func(source string, destination string) error {
		linkCalls++
		if linkCalls == 2 {
			return errors.New("injected routes commit failure")
		}
		return os.Link(source, destination)
	}
	require.ErrorContains(t, publishReleasePair(options, operations), "routes commit failure")
	require.NoFileExists(t, options.modelDestination)
	require.NoFileExists(t, options.routesDestination)
	requireNoReleasePairTemps(t, filepath.Dir(options.modelDestination))
}

func TestPublishReleasePairFlushFailurePublishesNothing(t *testing.T) {
	options, _, _ := releasePairFixture(t)
	operations := defaultPublishOperations()
	flushCalls := 0
	operations.syncFile = func(file *os.File) error {
		flushCalls++
		if flushCalls == 2 {
			return errors.New("injected flush failure")
		}
		return file.Sync()
	}
	require.ErrorContains(t, publishReleasePair(options, operations), "flush")
	require.NoFileExists(t, options.modelDestination)
	require.NoFileExists(t, options.routesDestination)
	requireNoReleasePairTemps(t, filepath.Dir(options.modelDestination))
}

func TestPublishReleasePairTargetRaceNeverOverwritesAndRollsBackOwnedModel(t *testing.T) {
	options, _, _ := releasePairFixture(t)
	operations := defaultPublishOperations()
	linkCalls := 0
	operations.link = func(source string, destination string) error {
		linkCalls++
		if linkCalls == 2 {
			require.NoError(t, os.WriteFile(options.routesDestination, []byte("raced"), 0o600))
		}
		return os.Link(source, destination)
	}
	require.Error(t, publishReleasePair(options, operations))
	require.NoFileExists(t, options.modelDestination)
	require.Equal(t, []byte("raced"), mustReadFile(t, options.routesDestination))
}

func TestPublishReleasePairRejectsPreexistingModelSwapDuringRoutesCommit(t *testing.T) {
	options, modelBytes, _ := releasePairFixture(t)
	require.NoError(t, os.WriteFile(options.modelDestination, modelBytes, 0o600))

	operations := defaultPublishOperations()
	operations.link = func(source string, destination string) error {
		if destination == options.routesDestination {
			require.NoError(t, os.Remove(options.modelDestination))
			require.NoError(t, os.WriteFile(options.modelDestination, []byte("swapped-preexisting-model"), 0o600))
		}
		return os.Link(source, destination)
	}

	require.ErrorContains(t, publishReleasePair(options, operations), "verify committed release pair")
	require.Equal(t, []byte("swapped-preexisting-model"), mustReadFile(t, options.modelDestination))
	require.NoFileExists(t, options.routesDestination, "the owned commit marker must be removed after pair drift")
	requireNoReleasePairTemps(t, filepath.Dir(options.modelDestination))
}

func TestPublishReleasePairRejectsOwnedModelSwapAfterModelCommit(t *testing.T) {
	options, _, _ := releasePairFixture(t)
	operations := defaultPublishOperations()
	linkCalls := 0
	operations.link = func(source string, destination string) error {
		linkCalls++
		if linkCalls == 2 {
			require.NoError(t, os.Remove(options.modelDestination))
			require.NoError(t, os.WriteFile(options.modelDestination, []byte("swapped-owned-model"), 0o600))
		}
		return os.Link(source, destination)
	}

	require.ErrorContains(t, publishReleasePair(options, operations), "verify committed release pair")
	require.Equal(t, []byte("swapped-owned-model"), mustReadFile(t, options.modelDestination), "cleanup must not delete a replacement inode")
	require.NoFileExists(t, options.routesDestination, "the owned commit marker must be removed after pair drift")
	requireNoReleasePairTemps(t, filepath.Dir(options.modelDestination))
}

func TestPublishReleasePairExactReplayRejectsDestinationSwapDuringDirectorySync(t *testing.T) {
	options, _, routesBytes := releasePairFixture(t)
	require.NoError(t, publishReleasePair(options, defaultPublishOperations()))

	operations := defaultPublishOperations()
	operations.syncDir = func(string) error {
		require.NoError(t, os.Remove(options.modelDestination))
		require.NoError(t, os.WriteFile(options.modelDestination, []byte("swapped-exact-replay-model"), 0o600))
		return nil
	}

	require.ErrorContains(t, publishReleasePair(options, operations), "verify exact committed release pair after directory sync")
	require.Equal(t, []byte("swapped-exact-replay-model"), mustReadFile(t, options.modelDestination))
	require.Equal(t, routesBytes, mustReadFile(t, options.routesDestination), "replay must not remove a preexisting marker it does not own")
	requireNoReleasePairTemps(t, filepath.Dir(options.modelDestination))
}

func TestPublishReleasePairFinalDirectorySyncFailureIsDurablyRetried(t *testing.T) {
	options, modelBytes, routesBytes := releasePairFixture(t)
	operations := defaultPublishOperations()
	syncCalls := 0
	operations.syncDir = func(directory string) error {
		syncCalls++
		if syncCalls == 2 {
			return errors.New("injected final directory sync failure")
		}
		return nil
	}
	require.ErrorContains(t, publishReleasePair(options, operations), "sync committed release pair")
	require.Equal(t, modelBytes, mustReadFile(t, options.modelDestination))
	require.Equal(t, routesBytes, mustReadFile(t, options.routesDestination))
	require.NoError(t, publishReleasePair(options, defaultPublishOperations()), "exact replay must retry the directory sync")
}

func TestPublishReleasePairStagedSourceReplacementCannotBeCommitted(t *testing.T) {
	options, _, _ := releasePairFixture(t)
	operations := defaultPublishOperations()
	linkCalls := 0
	operations.link = func(source string, destination string) error {
		linkCalls++
		if linkCalls == 1 {
			require.NoError(t, os.Remove(source))
			require.NoError(t, os.WriteFile(source, []byte("replaced-stage"), 0o600))
		}
		return os.Link(source, destination)
	}
	require.ErrorContains(t, publishReleasePair(options, operations), "verified staged inode")
	require.NoFileExists(t, options.modelDestination)
	require.NoFileExists(t, options.routesDestination)
}

func TestPublishReleasePairResumesExactModelOnlyInterruptedTransaction(t *testing.T) {
	options, modelBytes, routesBytes := releasePairFixture(t)
	require.NoError(t, os.WriteFile(options.modelDestination, modelBytes, 0o600))
	require.NoError(t, publishReleasePair(options, defaultPublishOperations()))
	require.Equal(t, modelBytes, mustReadFile(t, options.modelDestination))
	require.Equal(t, routesBytes, mustReadFile(t, options.routesDestination))
}

func TestValidateReleasePairRejectsAliasDrift(t *testing.T) {
	_, modelBytes, routesBytes := releasePairFixture(t)
	release, err := generationrelease.DecodeStrict(modelBytes)
	require.NoError(t, err)
	release.LegacyPublicAliases = []string{"seedream-5-unreviewed"}
	modelBytes, err = common.Marshal(release)
	require.NoError(t, err)
	require.ErrorContains(t, validateReleasePair(modelBytes, routesBytes), "reviewed Seedream binding")
}

func TestValidateReleasePairRejectsModelWindowStartingOneSecondAfterRoute(t *testing.T) {
	_, modelBytes, routesBytes := releasePairFixture(t)
	release, err := generationrelease.DecodeStrict(modelBytes)
	require.NoError(t, err)
	release.Attestation.SignedAt = "2026-08-29T00:00:01Z"
	modelBytes, err = common.Marshal(release)
	require.NoError(t, err)
	var routes map[string][]service.PlatformRelayRouteDeclaration
	require.NoError(t, common.Unmarshal(routesBytes, &routes))
	routes[constant.PlatformGenerationPublicSeedream50Model][0].ModelRelease = &release
	routesBytes, err = common.Marshal(routes)
	require.NoError(t, err)
	require.ErrorContains(t, validateReleasePair(modelBytes, routesBytes), "contain the complete route-acceptance window")
}

func mustReadFile(t *testing.T, path string) []byte {
	t.Helper()
	contents, err := os.ReadFile(path)
	require.NoError(t, err)
	return contents
}

func requireNoReleasePairTemps(t *testing.T, directory string) {
	t.Helper()
	entries, err := os.ReadDir(directory)
	require.NoError(t, err)
	for _, entry := range entries {
		require.False(t, strings.HasPrefix(entry.Name(), ".relay-release-pair-"), "staged artifact was not cleaned: %s", entry.Name())
	}
}
