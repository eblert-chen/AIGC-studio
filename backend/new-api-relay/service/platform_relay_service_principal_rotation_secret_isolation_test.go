package service

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
)

type platformRelayPrincipalRotationIsolationTestFixture struct {
	global        *platformRelaySecretIsolationTestFixture
	current       []PlatformRelayServicePrincipalProvisionInput
	desired       []PlatformRelayServicePrincipalProvisionInput
	currentPath   string
	desiredPath   string
	receiptPath   string
	receiptFolder string
}

func platformRelayPrincipalRotationMarshalInputs(
	t *testing.T,
	inputs []PlatformRelayServicePrincipalProvisionInput,
) []byte {
	t.Helper()
	raw, err := json.Marshal(PlatformRelayServicePrincipalsFile{
		Kind:          PlatformRelayServicePrincipalsFileKind,
		SchemaVersion: PlatformRelayServicePrincipalsFileSchemaVersion,
		Principals:    inputs,
	})
	require.NoError(t, err)
	return raw
}

func platformRelayPrincipalRotationWriteIdentityFile(t *testing.T, path string, raw []byte) {
	t.Helper()
	if _, err := os.Lstat(path); err == nil {
		require.NoError(t, os.Chmod(path, 0o600))
	}
	require.NoError(t, os.WriteFile(path, raw, 0o600))
	require.NoError(t, os.Chmod(path, 0o400))
}

func newPlatformRelayPrincipalRotationIsolationTestFixture(
	t *testing.T,
) *platformRelayPrincipalRotationIsolationTestFixture {
	t.Helper()
	global := newPlatformRelaySecretIsolationTestFixture(t)
	desiredRaw := append([]byte(nil), global.rawByEnvironment["RELAY_SERVICE_PRINCIPALS_FILE"]...)
	desired, err := ParsePlatformRelayServicePrincipalsFile(desiredRaw)
	require.NoError(t, err)
	current := append([]PlatformRelayServicePrincipalProvisionInput(nil), desired...)
	for index := range current {
		current[index].UpstreamToken = platformRelayRuntimeSecretsTestToken(
			"principal-rotation-current-" + current[index].ClientID,
		)
	}
	currentRaw := platformRelayPrincipalRotationMarshalInputs(t, current)
	root := filepath.Dir(os.Getenv("RELAY_SERVICE_PRINCIPALS_FILE"))
	currentPath := filepath.Join(root, "source-relay-current-service-principals-file")
	desiredPath := os.Getenv("RELAY_SERVICE_PRINCIPALS_FILE")
	runtimePath := filepath.Join(root, "source-relay-principal-rotation-runtime-dsn")
	receiptFolder := filepath.Join(root, "principal-rotation")
	require.NoError(t, os.Mkdir(receiptFolder, 0o700))
	receiptPath := filepath.Join(receiptFolder, "receipt.json")
	t.Setenv(PlatformRelayCurrentServicePrincipalsFileEnvironment, currentPath)
	t.Setenv("SQL_DSN_FILE", runtimePath)
	t.Setenv(
		platformRelaySecretIsolationReceiptDirectoryEnvironment(
			PlatformRelaySecretIsolationConsumerPrincipalRotation,
		),
		receiptFolder,
	)
	t.Setenv(platformRelaySecretIsolationReceiptFileEnvironment, receiptPath)
	platformRelayPrincipalRotationWriteIdentityFile(t, currentPath, currentRaw)
	platformRelayPrincipalRotationWriteIdentityFile(t, desiredPath, desiredRaw)
	global.rawByEnvironment[PlatformRelayCurrentServicePrincipalsFileEnvironment] = currentRaw
	global.rawByEnvironment["SQL_DSN_FILE"] = append(
		[]byte(nil),
		global.rawByEnvironment[platformRelaySecretIsolationRuntimeDSNEnvironment]...,
	)
	return &platformRelayPrincipalRotationIsolationTestFixture{
		global: global, current: current, desired: desired,
		currentPath: currentPath, desiredPath: desiredPath,
		receiptPath: receiptPath, receiptFolder: receiptFolder,
	}
}

func (fixture *platformRelayPrincipalRotationIsolationTestFixture) installReceiptForVerifier(t *testing.T) []byte {
	t.Helper()
	raw, err := os.ReadFile(fixture.receiptPath)
	require.NoError(t, err)
	fixture.global.replace(platformRelaySecretIsolationReceiptFileEnvironment, append([]byte(nil), raw...))
	return raw
}

func (fixture *platformRelayPrincipalRotationIsolationTestFixture) replaceCurrent(
	t *testing.T,
	inputs []PlatformRelayServicePrincipalProvisionInput,
) {
	t.Helper()
	raw := platformRelayPrincipalRotationMarshalInputs(t, inputs)
	fixture.global.replace(PlatformRelayCurrentServicePrincipalsFileEnvironment, raw)
	platformRelayPrincipalRotationWriteIdentityFile(t, fixture.currentPath, raw)
}

func (fixture *platformRelayPrincipalRotationIsolationTestFixture) replaceDesired(
	t *testing.T,
	inputs []PlatformRelayServicePrincipalProvisionInput,
) {
	t.Helper()
	raw := platformRelayPrincipalRotationMarshalInputs(t, inputs)
	fixture.global.replace("RELAY_SERVICE_PRINCIPALS_FILE", raw)
	platformRelayPrincipalRotationWriteIdentityFile(t, fixture.desiredPath, raw)
}

func requirePlatformRelayPrincipalRotationReceiptHasNoCredentialOracle(
	t *testing.T,
	raw []byte,
	inputs ...[]PlatformRelayServicePrincipalProvisionInput,
) {
	t.Helper()
	for _, set := range inputs {
		for _, input := range set {
			bare := strings.TrimPrefix(input.UpstreamToken, "sk-")
			canonicalDigest := sha256.Sum256([]byte(input.UpstreamToken))
			bareDigest := sha256.Sum256([]byte(bare))
			for _, forbidden := range [][]byte{
				[]byte(input.UpstreamToken),
				[]byte(bare),
				[]byte(fmt.Sprintf("%x", canonicalDigest)),
				[]byte(fmt.Sprintf("%x", bareDigest)),
			} {
				if len(forbidden) > 0 && bytes.Contains(raw, forbidden) {
					t.Fatal("rotation receipt exposes credential material or a credential digest")
				}
			}
		}
	}
}

func TestValidateAndVerifyPlatformRelayPrincipalRotationSecretIsolation(t *testing.T) {
	fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
	require.Len(t, platformRelaySecretIsolationConsumers, PlatformRelaySecretIsolationConsumerCount)
	require.NotContains(t, platformRelaySecretIsolationConsumers, PlatformRelaySecretIsolationConsumerPrincipalRotation)
	require.NoError(t, ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation())

	receiptRaw := fixture.installReceiptForVerifier(t)
	defer clear(receiptRaw)
	receipt, err := platformRelaySecretIsolationParseReceipt(receiptRaw)
	require.NoError(t, err)
	require.Equal(t, PlatformRelaySecretIsolationConsumerPrincipalRotation, receipt.Consumer)
	require.Regexp(t, platformRelaySecretIsolationRunIDPattern, receipt.RunID)
	fileIDs := make([]string, 0, len(receipt.Files))
	for _, commitment := range receipt.Files {
		fileIDs = append(fileIDs, commitment.ID)
	}
	sort.Strings(fileIDs)
	require.Equal(t, []string{
		"current_service_principals", "relay_database_ca", "runtime_dsn", "service_principals",
	}, fileIDs)

	for _, commitment := range receipt.Semantics {
		if strings.HasPrefix(commitment.ID, "rotation.") || strings.HasPrefix(commitment.ID, "principal.") {
			t.Fatal("rotation receipt contains a credential-derived semantic commitment")
		}
	}
	requirePlatformRelayPrincipalRotationReceiptHasNoCredentialOracle(
		t, receiptRaw, fixture.current, fixture.desired,
	)

	verified, err := VerifyPlatformRelayPrincipalRotationSecretIsolationReceipt()
	require.NoError(t, err)
	require.Equal(t, receipt.RunID, verified.AttemptID)
	require.Equal(t, fixture.current, verified.Current)
	require.Equal(t, fixture.desired, verified.Desired)
	replayed, err := VerifyPlatformRelayPrincipalRotationSecretIsolationReceipt()
	require.NoError(t, err)
	require.Equal(t, verified.AttemptID, replayed.AttemptID)

	// Revalidating the same exact bytes deliberately starts a new auditable
	// attempt. The random identity is stable only for exact receipt replay and
	// remains independent of every current/desired credential value.
	require.NoError(t, ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation())
	secondReceiptRaw := fixture.installReceiptForVerifier(t)
	defer clear(secondReceiptRaw)
	secondReceipt, err := platformRelaySecretIsolationParseReceipt(secondReceiptRaw)
	require.NoError(t, err)
	require.NotEqual(t, receipt.RunID, secondReceipt.RunID)
	requirePlatformRelayPrincipalRotationReceiptHasNoCredentialOracle(
		t, secondReceiptRaw, fixture.current, fixture.desired,
	)
	secondVerified, err := VerifyPlatformRelayPrincipalRotationSecretIsolationReceipt()
	require.NoError(t, err)
	require.Equal(t, secondReceipt.RunID, secondVerified.AttemptID)

	// The command's later SQL_DSN_FILE read must consume the verifier's exact
	// pinned bytes even if the underlying test source changes after approval.
	expectedRuntime := append([]byte(nil), fixture.global.rawByEnvironment[platformRelaySecretIsolationRuntimeDSNEnvironment]...)
	fixture.global.replace("SQL_DSN_FILE", []byte("not-the-attested-runtime-dsn"))
	pinnedRuntime, err := common.ReadProtectedSecretFile("SQL_DSN_FILE", 16*1024)
	require.NoError(t, err)
	require.Equal(t, expectedRuntime, pinnedRuntime)
	clear(expectedRuntime)
	clear(pinnedRuntime)
	expectedCA := append([]byte(nil), fixture.global.rawByEnvironment[platformRelaySecretIsolationRelayDatabaseCAEnvironment]...)
	fixture.global.replace(platformRelaySecretIsolationRelayDatabaseCAEnvironment, []byte("not-the-attested-relay-ca"))
	pinnedCA, err := common.ReadProtectedSecretFile(platformRelaySecretIsolationRelayDatabaseCAEnvironment, 256*1024)
	require.NoError(t, err)
	require.Equal(t, expectedCA, pinnedCA)
	clear(expectedCA)
	clear(pinnedCA)
}

func TestValidateAndVerifyPlatformRelayPrincipalRotationSecretIsolationAllowsExactUnchangedPurpose(t *testing.T) {
	fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
	fixture.current[0].UpstreamToken = fixture.desired[0].UpstreamToken
	fixture.replaceCurrent(t, fixture.current)
	require.NoError(t, ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation())
	receiptRaw := fixture.installReceiptForVerifier(t)
	defer clear(receiptRaw)
	verified, err := VerifyPlatformRelayPrincipalRotationSecretIsolationReceipt()
	require.NoError(t, err)
	require.Equal(t, fixture.current, verified.Current)
	require.Equal(t, fixture.desired, verified.Desired)
}

func TestVerifyPlatformRelayPrincipalRotationSecretIsolationRejectsChangedCurrent(t *testing.T) {
	fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation())
	receiptRaw := fixture.installReceiptForVerifier(t)
	defer clear(receiptRaw)

	changed := append([]PlatformRelayServicePrincipalProvisionInput(nil), fixture.current...)
	changed[0].UpstreamToken = platformRelayRuntimeSecretsTestToken("principal-rotation-stale-current")
	fixture.replaceCurrent(t, changed)
	_, err := VerifyPlatformRelayPrincipalRotationSecretIsolationReceipt()
	require.ErrorContains(t, err, "does not match mounted sources")
}

func TestVerifyPlatformRelayPrincipalRotationSecretIsolationRejectsChangedDesired(t *testing.T) {
	fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation())
	receiptRaw := fixture.installReceiptForVerifier(t)
	defer clear(receiptRaw)

	changed := append([]PlatformRelayServicePrincipalProvisionInput(nil), fixture.desired...)
	changed[0].UpstreamToken = platformRelayRuntimeSecretsTestToken("principal-rotation-stale-desired")
	fixture.replaceDesired(t, changed)
	_, err := VerifyPlatformRelayPrincipalRotationSecretIsolationReceipt()
	require.ErrorContains(t, err, "does not match mounted sources")
}

func TestValidatePlatformRelayPrincipalRotationSecretIsolationRejectsSourceAliases(t *testing.T) {
	t.Run("same path", func(t *testing.T) {
		fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
		require.NoError(t, os.WriteFile(fixture.receiptPath, []byte("stale"), 0o400))
		t.Setenv(PlatformRelayCurrentServicePrincipalsFileEnvironment, fixture.desiredPath)
		err := ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation()
		require.ErrorContains(t, err, "source identity is invalid")
		_, statErr := os.Lstat(fixture.receiptPath)
		require.ErrorIs(t, statErr, os.ErrNotExist)
	})

	t.Run("same inode", func(t *testing.T) {
		fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
		require.NoError(t, os.Chmod(fixture.desiredPath, 0o600))
		require.NoError(t, os.Remove(fixture.desiredPath))
		require.NoError(t, os.Link(fixture.currentPath, fixture.desiredPath))
		err := ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation()
		require.ErrorContains(t, err, "source identity is invalid")
		_, statErr := os.Lstat(fixture.receiptPath)
		require.ErrorIs(t, statErr, os.ErrNotExist)
	})
}

func TestValidatePlatformRelayPrincipalRotationSecretIsolationRejectsDesiredGlobalCollision(t *testing.T) {
	fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
	require.NoError(t, os.WriteFile(fixture.receiptPath, []byte("stale"), 0o400))
	var api PlatformRelayAPIRuntimeSecretsFile
	require.NoError(t, json.Unmarshal(fixture.global.rawByEnvironment["RELAY_API_RUNTIME_SECRETS_FILE"], &api))
	api.InternalAdmissionToken = fixture.desired[0].UpstreamToken
	apiRaw, err := json.Marshal(api)
	require.NoError(t, err)
	fixture.global.replace("RELAY_API_RUNTIME_SECRETS_FILE", apiRaw)

	err = ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation()
	require.ErrorContains(t, err, "cross-domain secret reuse")
	_, statErr := os.Lstat(fixture.receiptPath)
	require.ErrorIs(t, statErr, os.ErrNotExist)
}

func TestValidatePlatformRelayPrincipalRotationSecretIsolationRejectsCurrentGlobalCollision(t *testing.T) {
	fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
	require.NoError(t, os.WriteFile(fixture.receiptPath, []byte("stale"), 0o400))
	var api PlatformRelayAPIRuntimeSecretsFile
	require.NoError(t, json.Unmarshal(fixture.global.rawByEnvironment["RELAY_API_RUNTIME_SECRETS_FILE"], &api))
	api.InternalAdmissionToken = fixture.current[0].UpstreamToken
	apiRaw, err := json.Marshal(api)
	require.NoError(t, err)
	fixture.global.replace("RELAY_API_RUNTIME_SECRETS_FILE", apiRaw)

	err = ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation()
	require.ErrorContains(t, err, "cross-domain secret reuse")
	_, statErr := os.Lstat(fixture.receiptPath)
	require.ErrorIs(t, statErr, os.ErrNotExist)
}

func TestValidatePlatformRelayPrincipalRotationSecretIsolationRejectsIdentityChange(t *testing.T) {
	t.Run("replacement", func(t *testing.T) {
		fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
		changed := append([]PlatformRelayServicePrincipalProvisionInput(nil), fixture.current...)
		changed[0].TenantID = platformRelayRuntimeSecretsTestTenantB
		fixture.replaceCurrent(t, changed)

		err := ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation()
		require.Error(t, err)
		_, statErr := os.Lstat(fixture.receiptPath)
		require.ErrorIs(t, statErr, os.ErrNotExist)
	})

	t.Run("addition", func(t *testing.T) {
		fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
		changed := append(append([]PlatformRelayServicePrincipalProvisionInput(nil), fixture.desired...), PlatformRelayServicePrincipalProvisionInput{
			ClientID: "zz-unsupported-addition", TenantID: "00000000-0000-4000-8000-000000000099",
			UpstreamToken: platformRelayRuntimeSecretsTestToken("unsupported-principal-addition"),
		})
		fixture.replaceDesired(t, changed)

		err := ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation()
		require.Error(t, err)
		_, statErr := os.Lstat(fixture.receiptPath)
		require.ErrorIs(t, statErr, os.ErrNotExist)
	})

	t.Run("removal", func(t *testing.T) {
		fixture := newPlatformRelayPrincipalRotationIsolationTestFixture(t)
		fixture.replaceDesired(t, fixture.desired[:len(fixture.desired)-1])

		err := ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation()
		require.Error(t, err)
		_, statErr := os.Lstat(fixture.receiptPath)
		require.ErrorIs(t, statErr, os.ErrNotExist)
	})
}
