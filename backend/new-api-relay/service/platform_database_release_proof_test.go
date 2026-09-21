package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/QuantumNous/new-api/model"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func platformRelayDatabaseReleaseTestDB(t *testing.T) *gorm.DB {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+strings.ReplaceAll(t.Name(), "/", "-")+"?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	pool, err := db.DB()
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, pool.Close()) })
	return db
}

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

func platformRelayDatabaseReleaseBindingTestProof() relayDatabaseReleaseProof {
	return relayDatabaseReleaseProof{
		SchemaVersion: relayDatabaseReleaseProofSchemaVersion,
		Kind:          relayDatabaseReleaseProofKind,
		RunID:         strings.Repeat("1", 64),
		Generation:    platformRelaySecretIsolationGenerationRootProofPresent,
		RootProofID:   strings.Repeat("2", 64),
		Release: platformRelaySecretIsolationRelease{
			ImageDigest: "sha256:" + strings.Repeat("3", 64), SourceRevision: strings.Repeat("4", 40),
			SourceSnapshotSHA256: "sha256:" + strings.Repeat("5", 64), SourceSnapshotFileCount: 123,
			UpstreamRevision: strings.Repeat("6", 40), RouteAcceptanceTrustKeysSHA256: "sha256:" + strings.Repeat("7", 64),
			PlatformImage: "platform@example", PlatformSourceRevision: strings.Repeat("8", 40),
			PlatformSourceSnapshotSHA256: "sha256:" + strings.Repeat("9", 64),
			PlatformOrigin:               "https://platform.example.test", RelayOrigin: "https://relay.example.test",
			EdgeOrigin: "https://edge.example.test", RelayContractRevision: "generations.v1",
		},
		DatabaseEndpointSHA256: strings.Repeat("a", 64),
		Database:               platformRelayDatabaseReleaseTestIdentity(),
	}
}

func TestPlatformRelayDatabaseReleaseBindingCoversWholeCandidateAndDatabaseTuple(t *testing.T) {
	db := platformRelayDatabaseReleaseTestDB(t)
	pool, err := db.DB()
	require.NoError(t, err)
	otherDB := platformRelayDatabaseReleaseTestDB(t)
	otherPool, err := otherDB.DB()
	require.NoError(t, err)
	base := platformRelayDatabaseReleaseProofBinding{
		pool: pool, consumer: PlatformRelaySecretIsolationConsumerEdge,
		proof: platformRelayDatabaseReleaseBindingTestProof(),
		schemaStatus: model.RelaySchemaStatus{
			Classification: model.RelaySchemaStatusCurrent, CurrentVersion: model.RelaySchemaTargetVersion,
			TargetVersion: model.RelaySchemaTargetVersion, AttemptID: "12345678-1234-4234-9234-123456789abc",
			Current: true, Compatible: true,
		},
	}
	base.digest[0] = 1
	require.True(t, samePlatformRelayDatabaseReleaseProofBinding(base, base))

	mutations := []struct {
		name   string
		mutate func(*platformRelayDatabaseReleaseProofBinding)
	}{
		{name: "pool", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.pool = otherPool }},
		{name: "consumer", mutate: func(value *platformRelayDatabaseReleaseProofBinding) {
			value.consumer = PlatformRelaySecretIsolationConsumerAPI
		}},
		{name: "run", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.RunID = strings.Repeat("b", 64) }},
		{name: "generation", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.Generation = "changed" }},
		{name: "root proof", mutate: func(value *platformRelayDatabaseReleaseProofBinding) {
			value.proof.RootProofID = strings.Repeat("c", 64)
		}},
		{name: "image digest", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.Release.ImageDigest += "x" }},
		{name: "source revision", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.Release.SourceRevision += "x" }},
		{name: "source snapshot", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.Release.SourceSnapshotSHA256 += "x" }},
		{name: "file count", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.Release.SourceSnapshotFileCount++ }},
		{name: "upstream", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.Release.UpstreamRevision += "x" }},
		{name: "trust keys", mutate: func(value *platformRelayDatabaseReleaseProofBinding) {
			value.proof.Release.RouteAcceptanceTrustKeysSHA256 += "x"
		}},
		{name: "platform image", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.Release.PlatformImage += "x" }},
		{name: "platform source revision", mutate: func(value *platformRelayDatabaseReleaseProofBinding) {
			value.proof.Release.PlatformSourceRevision += "x"
		}},
		{name: "platform source snapshot", mutate: func(value *platformRelayDatabaseReleaseProofBinding) {
			value.proof.Release.PlatformSourceSnapshotSHA256 += "x"
		}},
		{name: "platform origin", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.Release.PlatformOrigin += "/x" }},
		{name: "relay origin", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.Release.RelayOrigin += "/x" }},
		{name: "edge origin", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.proof.Release.EdgeOrigin += "/x" }},
		{name: "contract", mutate: func(value *platformRelayDatabaseReleaseProofBinding) {
			value.proof.Release.RelayContractRevision += ".x"
		}},
		{name: "endpoint", mutate: func(value *platformRelayDatabaseReleaseProofBinding) {
			value.proof.DatabaseEndpointSHA256 = strings.Repeat("d", 64)
		}},
		{name: "database identity", mutate: func(value *platformRelayDatabaseReleaseProofBinding) {
			value.proof.Database.SystemSemanticSHA256 += "x"
		}},
		{name: "attempt", mutate: func(value *platformRelayDatabaseReleaseProofBinding) {
			value.schemaStatus.AttemptID = "87654321-4321-4321-8321-cba987654321"
		}},
		{name: "digest", mutate: func(value *platformRelayDatabaseReleaseProofBinding) { value.digest[0] = 2 }},
	}
	for _, test := range mutations {
		t.Run(test.name, func(t *testing.T) {
			candidate := base
			test.mutate(&candidate)
			require.False(t, samePlatformRelayDatabaseReleaseProofBinding(base, candidate))
		})
	}
}

func TestPlatformRelayDatabaseReleaseBoundAttestationReadsOnceAndReusesSchemaProof(t *testing.T) {
	db := platformRelayDatabaseReleaseTestDB(t).Table("statement_poison")
	pool, err := db.DB()
	require.NoError(t, err)
	proof := platformRelayDatabaseReleaseBindingTestProof()
	verified := platformRelaySecretIsolationVerifiedContext{
		receipt: platformRelaySecretIsolationReceipt{Consumer: PlatformRelaySecretIsolationConsumerEdge},
		marker: platformRelaySecretIsolationCommitMarker{
			RunID: proof.RunID, Generation: proof.Generation, RootProofID: proof.RootProofID, Release: proof.Release,
		},
	}
	previousCurrent := currentRelayDatabaseReleaseVerifiedContext
	previousRead := readRelayDatabaseReleaseProofForAttestation
	previousEndpoint := relayDatabaseReleaseEndpointDigestForAttestation
	previousRequire := requireRelaySchemaCurrentForDatabaseReleaseProof
	previousVerify := verifyRelayDatabaseReleaseIdentity
	var reads atomic.Int32
	var requires atomic.Int32
	var identities atomic.Int32
	currentRelayDatabaseReleaseVerifiedContext = func(string) (platformRelaySecretIsolationVerifiedContext, error) { return verified, nil }
	readRelayDatabaseReleaseProofForAttestation = func() (relayDatabaseReleaseProof, error) {
		reads.Add(1)
		return proof, nil
	}
	relayDatabaseReleaseEndpointDigestForAttestation = func(platformRelaySecretIsolationReceipt, string) (string, error) {
		return proof.DatabaseEndpointSHA256, nil
	}
	requireRelaySchemaCurrentForDatabaseReleaseProof = func(*gorm.DB) (model.RelaySchemaStatus, error) {
		requires.Add(1)
		return model.RelaySchemaStatus{}, errors.New("bound attestation repeated the schema catalog")
	}
	verifyRelayDatabaseReleaseIdentity = func(pinned *gorm.DB, identity model.RelayDatabaseReleaseIdentity) error {
		identities.Add(1)
		require.Equal(t, proof.Database, identity)
		return nil
	}
	t.Cleanup(func() {
		currentRelayDatabaseReleaseVerifiedContext = previousCurrent
		readRelayDatabaseReleaseProofForAttestation = previousRead
		relayDatabaseReleaseEndpointDigestForAttestation = previousEndpoint
		requireRelaySchemaCurrentForDatabaseReleaseProof = previousRequire
		verifyRelayDatabaseReleaseIdentity = previousVerify
	})
	status := model.RelaySchemaStatus{
		Classification: model.RelaySchemaStatusCurrent, CurrentVersion: model.RelaySchemaTargetVersion,
		TargetVersion: model.RelaySchemaTargetVersion, AttemptID: "12345678-1234-4234-9234-123456789abc",
		Current: true, Compatible: true,
	}
	binding, err := attestPlatformRelayDatabaseReleaseProofBoundToSchema(
		context.Background(), db, PlatformRelaySecretIsolationConsumerEdge, status,
	)
	require.NoError(t, err)
	require.Equal(t, int32(1), reads.Load())
	require.Zero(t, requires.Load())
	require.Equal(t, int32(1), identities.Load())
	require.Same(t, pool, binding.pool)
	require.Equal(t, status, binding.schemaStatus)
	require.True(t, binding.valid())

	var schemaConnection gorm.ConnPool
	var identityConnection gorm.ConnPool
	requireRelaySchemaCurrentForDatabaseReleaseProof = func(pinned *gorm.DB) (model.RelaySchemaStatus, error) {
		requires.Add(1)
		schemaConnection = pinned.Statement.ConnPool
		return status, nil
	}
	verifyRelayDatabaseReleaseIdentity = func(pinned *gorm.DB, identity model.RelayDatabaseReleaseIdentity) error {
		identities.Add(1)
		require.Equal(t, proof.Database, identity)
		identityConnection = pinned.Statement.ConnPool
		return nil
	}
	_, err = attestPlatformRelayDatabaseReleaseProofWithContext(
		context.Background(), db, PlatformRelaySecretIsolationConsumerEdge,
	)
	require.NoError(t, err)
	require.Equal(t, int32(2), reads.Load())
	require.Equal(t, int32(1), requires.Load())
	require.Equal(t, int32(2), identities.Load())
	require.NotNil(t, schemaConnection)
	require.Same(t, schemaConnection, identityConnection)
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

	require.NoError(t, VerifyPlatformRelayDatabaseReleaseProof(platformRelayDatabaseReleaseTestDB(t), PlatformRelaySecretIsolationConsumerPre))
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
		VerifyPlatformRelayDatabaseReleaseProof(platformRelayDatabaseReleaseTestDB(t), PlatformRelaySecretIsolationConsumerAPI),
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
