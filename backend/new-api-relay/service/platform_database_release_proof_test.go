package service

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/model"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func platformRelayDatabaseReleaseTestIdentity() model.RelayDatabaseReleaseIdentity {
	return model.RelayDatabaseReleaseIdentity{
		Environment:                  "staging",
		PostmasterStartTime:          "2026-08-18T00:00:00Z",
		ConfigLoadTime:               "2026-08-18T00:00:01Z",
		SystemSemanticSHA256:         "sha256:" + strings.Repeat("a", 64),
		PGAuditExtensionExact:        false,
		PGAuditSharedPreload:         false,
		SharedPreloadManifest:        "",
		SessionPreloadEmpty:          true,
		PGAuditLogClassCoverage:      false,
		CredentialLoggingPolicyExact: true,
	}
}

func platformRelayDatabaseReleaseTestDirectory(t *testing.T) string {
	t.Helper()
	directory := t.TempDir()
	require.NoError(t, os.Chmod(directory, 0o700))
	return directory
}

func platformRelayDatabaseReleaseAllowCurrentSchema(t *testing.T) {
	t.Helper()
	previous := requireRelaySchemaCurrentForDatabaseReleaseProof
	requireRelaySchemaCurrentForDatabaseReleaseProof = func(*gorm.DB) (model.RelaySchemaStatus, error) {
		return model.RelaySchemaStatus{
			Classification: model.RelaySchemaStatusCurrent,
			CurrentVersion: model.RelaySchemaTargetVersion,
			TargetVersion:  model.RelaySchemaTargetVersion,
			Current:        true,
			Compatible:     true,
		}, nil
	}
	t.Cleanup(func() { requireRelaySchemaCurrentForDatabaseReleaseProof = previous })
}

func platformRelayDatabaseReleaseRejectCompatibleSchema(t *testing.T) {
	t.Helper()
	previous := requireRelaySchemaCurrentForDatabaseReleaseProof
	requireRelaySchemaCurrentForDatabaseReleaseProof = func(*gorm.DB) (model.RelaySchemaStatus, error) {
		return model.RelaySchemaStatus{
			Classification:  model.RelaySchemaStatusCompatible,
			BaselineVersion: model.RelaySchemaTargetVersion - 1,
			CurrentVersion:  model.RelaySchemaTargetVersion - 1,
			TargetVersion:   model.RelaySchemaTargetVersion,
			Compatible:      true,
		}, errors.New("Relay schema is compatible but not current")
	}
	t.Cleanup(func() { requireRelaySchemaCurrentForDatabaseReleaseProof = previous })
}

func TestPlatformRelayDatabaseReleaseProofRoundTripBindsVerifiedRunAndEndpoint(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
	t.Setenv("SQL_DSN_FILE", os.Getenv(platformRelaySecretIsolationRoleAdminDSNEnvironment))
	fixture.rawByEnvironment["SQL_DSN_FILE"] = append(
		[]byte(nil),
		fixture.rawByEnvironment[platformRelaySecretIsolationRoleAdminDSNEnvironment]...,
	)
	fixture.installConsumerProof(t, PlatformRelaySecretIsolationConsumerPre)
	require.NoError(t, VerifyPlatformRelaySecretIsolationReceipt(PlatformRelaySecretIsolationConsumerPre))

	proofDirectory := platformRelayDatabaseReleaseTestDirectory(t)
	proofPath := filepath.Join(proofDirectory, "receipt.json")
	t.Setenv(RelayDatabaseReleaseProofDirectoryEnvironment, proofDirectory)
	t.Setenv(RelayDatabaseReleaseProofFileEnvironment, proofPath)

	previousInspect := inspectRelayDatabaseReleaseIdentity
	previousVerify := verifyRelayDatabaseReleaseIdentity
	identity := platformRelayDatabaseReleaseTestIdentity()
	inspectRelayDatabaseReleaseIdentity = func(*gorm.DB) (model.RelayDatabaseReleaseIdentity, error) {
		return identity, nil
	}
	verified := false
	verifyRelayDatabaseReleaseIdentity = func(_ *gorm.DB, actual model.RelayDatabaseReleaseIdentity) error {
		verified = true
		require.Equal(t, identity, actual)
		return nil
	}
	t.Cleanup(func() {
		inspectRelayDatabaseReleaseIdentity = previousInspect
		verifyRelayDatabaseReleaseIdentity = previousVerify
	})

	writer, err := PreparePlatformRelayDatabaseReleaseProof()
	require.NoError(t, err)
	require.NoError(t, writer.Commit(nil))
	require.NoError(t, writer.Close())
	proofRaw, err := os.ReadFile(proofPath)
	require.NoError(t, err)
	defer clear(proofRaw)
	fixture.replace(RelayDatabaseReleaseProofFileEnvironment, append([]byte(nil), proofRaw...))

	require.NoError(t, VerifyPlatformRelayDatabaseReleaseProof(nil, PlatformRelaySecretIsolationConsumerPre))
	require.True(t, verified)
}

func TestPlatformRelayDatabaseReleaseProofRejectsCompatibleV1ForProtectedAPI(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
	t.Setenv("SQL_DSN_FILE", os.Getenv(platformRelaySecretIsolationRuntimeDSNEnvironment))
	fixture.rawByEnvironment["SQL_DSN_FILE"] = append(
		[]byte(nil), fixture.rawByEnvironment[platformRelaySecretIsolationRuntimeDSNEnvironment]...,
	)
	fixture.installConsumerProof(t, PlatformRelaySecretIsolationConsumerAPI)
	require.NoError(t, VerifyPlatformRelaySecretIsolationReceipt(PlatformRelaySecretIsolationConsumerAPI))
	receiptRaw := fixture.receipt(t, PlatformRelaySecretIsolationConsumerAPI)
	defer clear(receiptRaw)
	receipt, err := platformRelaySecretIsolationParseReceipt(receiptRaw)
	require.NoError(t, err)
	markerRaw := fixture.commitMarker(t)
	defer clear(markerRaw)
	marker, err := platformRelaySecretIsolationParseCommitMarker(markerRaw)
	require.NoError(t, err)
	endpointDigest, err := platformRelayDatabaseReleaseEndpointDigest(
		receipt,
		PlatformRelaySecretIsolationConsumerAPI,
	)
	require.NoError(t, err)
	identity := platformRelayDatabaseReleaseTestIdentity()
	proofRaw, err := json.Marshal(relayDatabaseReleaseProof{
		SchemaVersion:          relayDatabaseReleaseProofSchemaVersion,
		Kind:                   relayDatabaseReleaseProofKind,
		RunID:                  marker.RunID,
		Generation:             marker.Generation,
		RootProofID:            marker.RootProofID,
		Release:                marker.Release,
		DatabaseEndpointSHA256: endpointDigest,
		Database:               identity,
	})
	require.NoError(t, err)
	defer clear(proofRaw)
	t.Setenv(RelayDatabaseReleaseProofFileEnvironment, filepath.Join(t.TempDir(), "receipt.json"))
	fixture.replace(RelayDatabaseReleaseProofFileEnvironment, append([]byte(nil), proofRaw...))
	platformRelayDatabaseReleaseRejectCompatibleSchema(t)
	previousVerify := verifyRelayDatabaseReleaseIdentity
	identityVerified := false
	verifyRelayDatabaseReleaseIdentity = func(*gorm.DB, model.RelayDatabaseReleaseIdentity) error {
		identityVerified = true
		return nil
	}
	t.Cleanup(func() { verifyRelayDatabaseReleaseIdentity = previousVerify })
	require.EqualError(t,
		VerifyPlatformRelayDatabaseReleaseProof(nil, PlatformRelaySecretIsolationConsumerAPI),
		"Relay database release proof requires the current schema",
	)
	require.False(t, identityVerified)
}

func TestPreparePlatformRelayDatabaseReleaseProofRejectsMismatchedReadMountBeforeRevocation(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
	fixture.installConsumerProof(t, PlatformRelaySecretIsolationConsumerPre)

	writeDirectory := platformRelayDatabaseReleaseTestDirectory(t)
	readDirectory := platformRelayDatabaseReleaseTestDirectory(t)
	stale := []byte(`{"stale":true}`)
	writePath := filepath.Join(writeDirectory, "receipt.json")
	require.NoError(t, os.WriteFile(writePath, stale, 0o400))
	t.Setenv(RelayDatabaseReleaseProofDirectoryEnvironment, writeDirectory)
	t.Setenv(RelayDatabaseReleaseProofFileEnvironment, filepath.Join(readDirectory, "receipt.json"))

	writer, err := PreparePlatformRelayDatabaseReleaseProof()
	require.Nil(t, writer)
	require.EqualError(t, err, "Relay database release proof read and write mounts are not the same directory")
	unchanged, readErr := os.ReadFile(writePath)
	require.NoError(t, readErr)
	require.Equal(t, stale, unchanged)
}

func TestPreparePlatformRelayDatabaseReleaseProofRejectsNonCanonicalFilename(t *testing.T) {
	fixture := newPlatformRelaySecretIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
	fixture.installConsumerProof(t, PlatformRelaySecretIsolationConsumerPre)

	directory := platformRelayDatabaseReleaseTestDirectory(t)
	t.Setenv(RelayDatabaseReleaseProofDirectoryEnvironment, directory)
	t.Setenv(RelayDatabaseReleaseProofFileEnvironment, filepath.Join(directory, "old-proof.json"))
	writer, err := PreparePlatformRelayDatabaseReleaseProof()
	require.Nil(t, writer)
	require.EqualError(t, err, "Relay database release proof read and write mounts are not the same directory")
}

func TestVerifyPlatformRelayRootDatabaseReleaseProofRejectsPostRootGeneration(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	receiptRaw := fixture.installReceiptForVerifier(t)
	defer clear(receiptRaw)
	receipt, err := platformRelaySecretIsolationParseReceipt(receiptRaw)
	require.NoError(t, err)
	endpointDigest, err := platformRelayDatabaseReleaseEndpointDigest(
		receipt,
		PlatformRelaySecretIsolationConsumerRootBootstrap,
	)
	require.NoError(t, err)

	proof := relayDatabaseReleaseProof{
		SchemaVersion:          relayDatabaseReleaseProofSchemaVersion,
		Kind:                   relayDatabaseReleaseProofKind,
		RunID:                  strings.Repeat("8", 64),
		Generation:             platformRelaySecretIsolationGenerationRootProofPresent,
		RootProofID:            strings.Repeat("9", 64),
		Release:                receipt.Release,
		DatabaseEndpointSHA256: endpointDigest,
		Database:               platformRelayDatabaseReleaseTestIdentity(),
	}
	proofRaw, err := json.Marshal(proof)
	require.NoError(t, err)
	proofPath := filepath.Join(t.TempDir(), "database-release-proof.json")
	t.Setenv(RelayDatabaseReleaseProofFileEnvironment, proofPath)
	fixture.global.replace(RelayDatabaseReleaseProofFileEnvironment, proofRaw)

	previousVerify := verifyRelayDatabaseReleaseIdentity
	called := false
	verifyRelayDatabaseReleaseIdentity = func(*gorm.DB, model.RelayDatabaseReleaseIdentity) error {
		called = true
		return nil
	}
	t.Cleanup(func() { verifyRelayDatabaseReleaseIdentity = previousVerify })
	require.EqualError(t,
		VerifyPlatformRelayRootDatabaseReleaseProof(nil),
		"Relay root database release proof is not bound to this install",
	)
	require.False(t, called)
}

func TestVerifyPlatformRelayRootDatabaseReleaseProofAcceptsBoundPreRootProof(t *testing.T) {
	platformRelayDatabaseReleaseAllowCurrentSchema(t)
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	receiptRaw := fixture.installReceiptForVerifier(t)
	defer clear(receiptRaw)
	receipt, err := platformRelaySecretIsolationParseReceipt(receiptRaw)
	require.NoError(t, err)
	endpointDigest, err := platformRelayDatabaseReleaseEndpointDigest(
		receipt,
		PlatformRelaySecretIsolationConsumerRootBootstrap,
	)
	require.NoError(t, err)
	identity := platformRelayDatabaseReleaseTestIdentity()
	proof := relayDatabaseReleaseProof{
		SchemaVersion:          relayDatabaseReleaseProofSchemaVersion,
		Kind:                   relayDatabaseReleaseProofKind,
		RunID:                  strings.Repeat("8", 64),
		Generation:             platformRelaySecretIsolationGenerationPreRoot,
		Release:                receipt.Release,
		DatabaseEndpointSHA256: endpointDigest,
		Database:               identity,
	}
	proofRaw, err := json.Marshal(proof)
	require.NoError(t, err)
	defer clear(proofRaw)
	t.Setenv(RelayDatabaseReleaseProofFileEnvironment, filepath.Join(t.TempDir(), "receipt.json"))
	fixture.global.replace(RelayDatabaseReleaseProofFileEnvironment, append([]byte(nil), proofRaw...))

	previousVerify := verifyRelayDatabaseReleaseIdentity
	verifyRelayDatabaseReleaseIdentity = func(_ *gorm.DB, actual model.RelayDatabaseReleaseIdentity) error {
		require.Equal(t, identity, actual)
		return nil
	}
	t.Cleanup(func() { verifyRelayDatabaseReleaseIdentity = previousVerify })
	require.NoError(t, VerifyPlatformRelayRootDatabaseReleaseProof(nil))
	platformRelayDatabaseReleaseRejectCompatibleSchema(t)
	require.EqualError(t,
		VerifyPlatformRelayRootDatabaseReleaseProof(nil),
		"Relay root database release proof requires the current schema",
	)
}

func TestVerifyPlatformRelayPrincipalRotationDatabaseReleaseProofBindsPostRootEndpoint(t *testing.T) {
	platformRelayDatabaseReleaseAllowCurrentSchema(t)
	fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation())
	receiptRaw := fixture.installReceiptForVerifier(t)
	defer clear(receiptRaw)
	inputs, err := VerifyPlatformRelayPrincipalRotationSecretIsolationReceipt()
	require.NoError(t, err)
	defer clearPlatformRelayServicePrincipalRotationInputs(inputs.Current)
	defer clearPlatformRelayServicePrincipalRotationInputs(inputs.Desired)
	receipt, err := platformRelaySecretIsolationParseReceipt(receiptRaw)
	require.NoError(t, err)
	endpointDigest, err := platformRelayDatabaseReleaseEndpointDigest(
		receipt,
		PlatformRelaySecretIsolationConsumerPrincipalRotation,
	)
	require.NoError(t, err)
	identity := platformRelayDatabaseReleaseTestIdentity()
	proof := relayDatabaseReleaseProof{
		SchemaVersion:          relayDatabaseReleaseProofSchemaVersion,
		Kind:                   relayDatabaseReleaseProofKind,
		RunID:                  strings.Repeat("8", 64),
		Generation:             platformRelaySecretIsolationGenerationRootProofPresent,
		RootProofID:            strings.Repeat("9", 64),
		Release:                receipt.Release,
		DatabaseEndpointSHA256: endpointDigest,
		Database:               identity,
	}
	proofRaw, err := json.Marshal(proof)
	require.NoError(t, err)
	defer clear(proofRaw)
	t.Setenv(RelayDatabaseReleaseProofFileEnvironment, filepath.Join(t.TempDir(), "receipt.json"))
	fixture.global.replace(RelayDatabaseReleaseProofFileEnvironment, append([]byte(nil), proofRaw...))

	previousVerify := verifyRelayDatabaseReleaseIdentity
	verified := false
	verifyRelayDatabaseReleaseIdentity = func(_ *gorm.DB, actual model.RelayDatabaseReleaseIdentity) error {
		verified = true
		require.Equal(t, identity, actual)
		return nil
	}
	t.Cleanup(func() { verifyRelayDatabaseReleaseIdentity = previousVerify })
	require.NoError(t, VerifyPlatformRelayPrincipalRotationDatabaseReleaseProof(nil, inputs.AttemptID))
	require.True(t, verified)
	platformRelayDatabaseReleaseRejectCompatibleSchema(t)
	verified = false
	require.EqualError(t,
		VerifyPlatformRelayPrincipalRotationDatabaseReleaseProof(nil, inputs.AttemptID),
		"Relay principal rotation database release proof requires the current schema",
	)
	require.False(t, verified)

	proof.Generation = platformRelaySecretIsolationGenerationPreRoot
	proof.RootProofID = ""
	proofRaw, err = json.Marshal(proof)
	require.NoError(t, err)
	fixture.global.replace(RelayDatabaseReleaseProofFileEnvironment, append([]byte(nil), proofRaw...))
	verified = false
	require.EqualError(t,
		VerifyPlatformRelayPrincipalRotationDatabaseReleaseProof(nil, inputs.AttemptID),
		"Relay principal rotation database release proof is not bound to this rotation",
	)
	require.False(t, verified)
}
