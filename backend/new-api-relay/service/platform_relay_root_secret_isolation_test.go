package service

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/require"
)

type platformRelayRootIsolationTestFixture struct {
	global      *platformRelaySecretIsolationTestFixture
	password    []byte
	receiptPath string
	proofPath   string
}

func newPlatformRelayRootIsolationTestFixture(t *testing.T) *platformRelayRootIsolationTestFixture {
	t.Helper()
	global := newPlatformRelaySecretIsolationTestFixture(t)
	digest := sha256.Sum256([]byte("relay-root-bootstrap-isolation-test-password"))
	password := []byte(hex.EncodeToString(digest[:]))
	root := filepath.Dir(os.Getenv("RELAY_SERVICE_PRINCIPALS_FILE"))
	passwordPath := filepath.Join(root, "source-relay-root-password")
	runtimePath := filepath.Join(root, "source-relay-root-runtime-dsn")
	receiptDirectory := filepath.Join(root, PlatformRelaySecretIsolationConsumerRootBootstrap)
	receiptPath := filepath.Join(receiptDirectory, "receipt.json")
	proofDirectory := filepath.Join(root, "root-proof")
	proofPath := filepath.Join(proofDirectory, "proof.json")
	require.NoError(t, os.Mkdir(receiptDirectory, 0o700))
	require.NoError(t, os.Mkdir(proofDirectory, 0o700))
	require.NoError(t, os.WriteFile(
		filepath.Join(proofDirectory, platformRelayRootProofLockFileName), nil, 0o600,
	))
	t.Setenv(PlatformRelayRootUsernameEnvironment, "root-admin")
	t.Setenv(PlatformRelayRootPasswordFileEnvironment, passwordPath)
	t.Setenv("SQL_DSN_FILE", runtimePath)
	t.Setenv(
		platformRelaySecretIsolationReceiptDirectoryEnvironment(
			PlatformRelaySecretIsolationConsumerRootBootstrap,
		),
		receiptDirectory,
	)
	t.Setenv(platformRelaySecretIsolationReceiptFileEnvironment, receiptPath)
	t.Setenv(PlatformRelayRootProofDirectoryEnvironment, proofDirectory)
	t.Setenv(PlatformRelayRootProofFileEnvironment, proofPath)
	global.rawByEnvironment[PlatformRelayRootPasswordFileEnvironment] = append([]byte(nil), password...)
	global.rawByEnvironment["SQL_DSN_FILE"] = append(
		[]byte(nil), global.rawByEnvironment[platformRelaySecretIsolationRuntimeDSNEnvironment]...,
	)
	return &platformRelayRootIsolationTestFixture{
		global: global, password: password, receiptPath: receiptPath, proofPath: proofPath,
	}
}

func (fixture *platformRelayRootIsolationTestFixture) installReceiptForVerifier(t *testing.T) []byte {
	t.Helper()
	raw, err := os.ReadFile(fixture.receiptPath)
	require.NoError(t, err)
	fixture.global.replace(platformRelaySecretIsolationReceiptFileEnvironment, append([]byte(nil), raw...))
	proofRaw, err := os.ReadFile(fixture.proofPath)
	require.NoError(t, err)
	fixture.global.replace(PlatformRelayRootProofFileEnvironment, proofRaw)
	return raw
}

func TestValidateAndVerifyPlatformRelayRootSecretIsolation(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	require.NotContains(t, platformRelaySecretIsolationConsumers, PlatformRelaySecretIsolationConsumerRootBootstrap)
	require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())

	receiptRaw := fixture.installReceiptForVerifier(t)
	defer clear(receiptRaw)
	require.NotContains(t, receiptRaw, fixture.password)
	receipt, err := platformRelaySecretIsolationParseReceipt(receiptRaw)
	require.NoError(t, err)
	require.Equal(t, PlatformRelaySecretIsolationConsumerRootBootstrap, receipt.Consumer)
	fileIDs := make([]string, 0, len(receipt.Files))
	for _, commitment := range receipt.Files {
		fileIDs = append(fileIDs, commitment.ID)
	}
	sort.Strings(fileIDs)
	require.Equal(t, []string{"relay_database_ca", "root_password", "root_proof", "runtime_dsn"}, fileIDs)
	semanticIDs := make([]string, 0, len(receipt.Semantics))
	for _, commitment := range receipt.Semantics {
		semanticIDs = append(semanticIDs, commitment.ID)
	}
	require.Contains(t, semanticIDs, "root.username")
	require.Contains(t, semanticIDs, "root.proof_id")
	require.NotContains(t, semanticIDs, "root.password")

	verified, err := VerifyPlatformRelayRootSecretIsolationReceipt()
	require.NoError(t, err)
	require.Equal(t, "root-admin", verified.Username)
	require.Equal(t, string(fixture.password), verified.Password)

	// Host-side replacement after verification cannot affect the database/root
	// bytes subsequently consumed by this process.
	replacement := []byte("replacement-root-password-that-must-never-be-consumed-92841")
	fixture.global.replace(PlatformRelayRootPasswordFileEnvironment, replacement)
	pinned, err := common.ReadProtectedSecretFile(PlatformRelayRootPasswordFileEnvironment, 4096)
	require.NoError(t, err)
	require.Equal(t, fixture.password, pinned)
	clear(pinned)
}

func TestValidatePlatformRelayRootSecretIsolationRejectsGlobalPasswordReuse(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	runtimePassword := []byte(platformRelaySecretIsolationTestPassword("runtime"))
	fixture.global.replace(PlatformRelayRootPasswordFileEnvironment, runtimePassword)
	require.EqualError(t, ValidateAndCommitPlatformRelayRootSecretIsolation(),
		"Relay secret isolation found cross-domain secret reuse")
}

func TestVerifyPlatformRelayRootSecretIsolationRejectsChangedUsername(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	receiptRaw := fixture.installReceiptForVerifier(t)
	defer clear(receiptRaw)
	t.Setenv(PlatformRelayRootUsernameEnvironment, "other-root-admin")
	_, err := VerifyPlatformRelayRootSecretIsolationReceipt()
	require.ErrorContains(t, err, "does not match mounted sources")
}

func TestValidatePlatformRelayRootSecretIsolationRevokesStaleReceiptBeforeFailure(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	require.NoError(t, os.WriteFile(fixture.receiptPath, []byte("stale"), 0o400))
	fixture.global.replace(
		PlatformRelayRootPasswordFileEnvironment,
		[]byte(platformRelaySecretIsolationTestPassword("runtime")),
	)
	require.Error(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	_, err := os.Lstat(fixture.receiptPath)
	require.ErrorIs(t, err, os.ErrNotExist)
}

func TestPlatformRelayRootIsolationReceiptContainsNoRawPassword(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	receiptRaw, err := os.ReadFile(fixture.receiptPath)
	require.NoError(t, err)
	require.False(t, bytes.Contains(receiptRaw, fixture.password))
}

func TestPlatformRelayRootIsolationProofIsIdempotentRejectsDifferentRootAndSurvivesReleaseUpgrade(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	first, err := os.ReadFile(fixture.proofPath)
	require.NoError(t, err)
	defer clear(first)
	info, err := os.Stat(fixture.proofPath)
	require.NoError(t, err)
	if runtime.GOOS != "windows" {
		require.Equal(t, os.FileMode(0o600), info.Mode().Perm())
	}

	require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	second, err := os.ReadFile(fixture.proofPath)
	require.NoError(t, err)
	require.Equal(t, first, second)
	clear(second)

	otherDigest := sha256.Sum256([]byte("root-isolation-other-strong-password"))
	fixture.global.replace(PlatformRelayRootPasswordFileEnvironment, []byte(hex.EncodeToString(otherDigest[:])))
	require.ErrorContains(t, ValidateAndCommitPlatformRelayRootSecretIsolation(), "proof conflicts")
	fixture.global.replace(PlatformRelayRootPasswordFileEnvironment, append([]byte(nil), fixture.password...))

	t.Setenv("PLATFORM_SOURCE_REVISION", strings.Repeat("8", 40))
	require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	upgraded, err := os.ReadFile(fixture.proofPath)
	require.NoError(t, err)
	require.Equal(t, first, upgraded)
	clear(upgraded)
}

func TestPlatformRelayRootIsolationProofRejectsTruncationModeAndSymlink(t *testing.T) {
	t.Run("truncated", func(t *testing.T) {
		fixture := newPlatformRelayRootIsolationTestFixture(t)
		require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
		require.NoError(t, os.WriteFile(fixture.proofPath, []byte("{"), 0o600))
		require.Error(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	})
	t.Run("wrong mode", func(t *testing.T) {
		if runtime.GOOS == "windows" {
			t.Skip("POSIX mode is enforced by the Linux deployment contract")
		}
		fixture := newPlatformRelayRootIsolationTestFixture(t)
		require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
		require.NoError(t, os.Chmod(fixture.proofPath, 0o400))
		require.Error(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	})
	t.Run("symlink", func(t *testing.T) {
		fixture := newPlatformRelayRootIsolationTestFixture(t)
		require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
		target := fixture.proofPath + ".real"
		require.NoError(t, os.Rename(fixture.proofPath, target))
		if err := os.Symlink(target, fixture.proofPath); err != nil {
			t.Skipf("symlink creation is unavailable: %v", err)
		}
		require.Error(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	})
}

func TestPlatformRelayRootIsolationKillAfterProofCanResumeOnlyWithExactRoot(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	previous := platformRelayRootIsolationAfterProofCommit
	platformRelayRootIsolationAfterProofCommit = func() error { return errors.New("simulated termination") }
	t.Cleanup(func() { platformRelayRootIsolationAfterProofCommit = previous })
	require.EqualError(t, ValidateAndCommitPlatformRelayRootSecretIsolation(), "simulated termination")
	require.FileExists(t, fixture.proofPath)
	_, err := os.Stat(fixture.receiptPath)
	require.ErrorIs(t, err, os.ErrNotExist)

	otherDigest := sha256.Sum256([]byte("root-isolation-kill-other-password"))
	fixture.global.replace(PlatformRelayRootPasswordFileEnvironment, []byte(hex.EncodeToString(otherDigest[:])))
	require.ErrorContains(t, ValidateAndCommitPlatformRelayRootSecretIsolation(), "proof conflicts")
	fixture.global.replace(PlatformRelayRootPasswordFileEnvironment, append([]byte(nil), fixture.password...))
	platformRelayRootIsolationAfterProofCommit = nil
	require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	require.FileExists(t, fixture.receiptPath)
}

func TestPlatformRelayRootIsolationProofConcurrentDifferentRootsNeverOverwrite(t *testing.T) {
	setPlatformRelaySecretIsolationTestRelease(t)
	release, err := platformRelaySecretIsolationReleaseIdentity()
	require.NoError(t, err)
	directoryPath := t.TempDir()
	require.NoError(t, os.Chmod(directoryPath, 0o700))
	leftDirectory, err := platformRelaySecretIsolationOpenReceiptDirectory(directoryPath)
	require.NoError(t, err)
	defer leftDirectory.close()
	rightDirectory, err := platformRelaySecretIsolationOpenReceiptDirectory(directoryPath)
	require.NoError(t, err)
	defer rightDirectory.close()

	passwords := [][]byte{
		[]byte("concurrent-root-password-left-0123456789"),
		[]byte("concurrent-root-password-right-9876543210"),
	}
	digests := [][sha256.Size]byte{sha256.Sum256(passwords[0]), sha256.Sum256(passwords[1])}
	defer clear(passwords[0])
	defer clear(passwords[1])
	type result struct {
		index   int
		proofID string
		err     error
	}
	start := make(chan struct{})
	results := make(chan result, 2)
	for index, directory := range []*platformRelaySecretIsolationReceiptDirectory{leftDirectory, rightDirectory} {
		go func(index int, directory *platformRelaySecretIsolationReceiptDirectory) {
			<-start
			proofID, ensureErr := platformRelayRootIsolationEnsureProof(directory, release, digests[index])
			results <- result{index: index, proofID: proofID, err: ensureErr}
		}(index, directory)
	}
	close(start)
	first := <-results
	second := <-results
	winner := -1
	for _, current := range []result{first, second} {
		if current.err == nil {
			require.Equal(t, -1, winner, "only one different root may create the permanent proof")
			winner = current.index
			require.Regexp(t, platformRelaySecretIsolationRunIDPattern, current.proofID)
		} else {
			require.ErrorContains(t, current.err, "conflicts with the requested root")
		}
	}
	require.NotEqual(t, -1, winner)
	raw, err := leftDirectory.readRootProof(platformRelayRootProofMaximumBytes)
	require.NoError(t, err)
	defer clear(raw)
	proof, err := platformRelayRootIsolationParseProof(raw)
	require.NoError(t, err)
	require.Equal(t, hex.EncodeToString(digests[winner][:]), proof.RootPasswordSHA256)
	loser := 1 - winner
	_, _, err = platformRelayRootIsolationProofFile(raw, passwords[loser])
	require.Error(t, err)
}

func TestPostRootGlobalValidationRejectsSourceSwappedToRootPassword(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	require.NoError(t, ValidateAndCommitPlatformRelayRootSecretIsolation())
	proofRaw, err := os.ReadFile(fixture.proofPath)
	require.NoError(t, err)
	defer clear(proofRaw)
	fixture.global.replace(PlatformRelayRootProofFileEnvironment, append([]byte(nil), proofRaw...))
	t.Setenv(platformRelaySecretIsolationGenerationEnvironment, platformRelaySecretIsolationGenerationRootProofPresent)

	original := append([]byte(nil), fixture.global.rawByEnvironment["RELAY_API_RUNTIME_SECRETS_FILE"]...)
	defer clear(original)
	document, err := ParsePlatformRelayAPIRuntimeSecretsFile(original)
	require.NoError(t, err)
	document.ArtifactSigningSecret = string(fixture.password)
	mutated, err := json.Marshal(document)
	require.NoError(t, err)
	fixture.global.replace("RELAY_API_RUNTIME_SECRETS_FILE", mutated)
	require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
		"Relay secret isolation found cross-domain secret reuse")

	fixture.global.replace("RELAY_API_RUNTIME_SECRETS_FILE", append([]byte(nil), original...))
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
	markerRaw := fixture.global.commitMarker(t)
	marker, err := platformRelaySecretIsolationParseCommitMarker(markerRaw)
	clear(markerRaw)
	require.NoError(t, err)
	require.Equal(t, platformRelaySecretIsolationGenerationRootProofPresent, marker.Generation)
	require.NotEmpty(t, marker.RootProofID)

	proof, err := platformRelayRootIsolationParseProof(proofRaw)
	require.NoError(t, err)
	for _, consumer := range platformRelaySecretIsolationConsumers {
		receiptRaw := fixture.global.receipt(t, consumer)
		require.NotContains(t, string(receiptRaw), proof.RootPasswordSHA256)
		clear(receiptRaw)
	}

	// The permanent proof records its creation release for audit only. A later
	// release and an exact rollback both carry the same forbidden root digest
	// into a newly committed ordinary generation without needing the destroyed
	// root password source again.
	originalPlatformRevision := os.Getenv("PLATFORM_SOURCE_REVISION")
	t.Setenv("PLATFORM_SOURCE_REVISION", strings.Repeat("8", 40))
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
	t.Setenv("PLATFORM_SOURCE_REVISION", originalPlatformRevision)
	require.NoError(t, ValidateAndCommitPlatformRelaySecretIsolation())
}

func TestPreRootValidationAndRootProofCreationAreSerializedByPermanentProofLock(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	delete(fixture.global.rawByEnvironment, PlatformRelayRootProofFileEnvironment)
	t.Setenv(platformRelaySecretIsolationGenerationEnvironment, platformRelaySecretIsolationGenerationPreRoot)

	previousHook := platformRelaySecretIsolationAfterReceiptCommit
	preRootPaused := make(chan struct{})
	releasePreRoot := make(chan struct{})
	platformRelaySecretIsolationAfterReceiptCommit = func(committed int) error {
		if committed == 1 {
			close(preRootPaused)
			<-releasePreRoot
		}
		return nil
	}
	t.Cleanup(func() { platformRelaySecretIsolationAfterReceiptCommit = previousHook })

	preRootDone := make(chan error, 1)
	go func() { preRootDone <- ValidateAndCommitPlatformRelaySecretIsolation() }()
	<-preRootPaused

	rootDone := make(chan error, 1)
	go func() { rootDone <- ValidateAndCommitPlatformRelayRootSecretIsolation() }()
	select {
	case err := <-rootDone:
		require.Failf(t, "root validator bypassed proof lock", "returned before pre-root commit: %v", err)
	case <-time.After(150 * time.Millisecond):
	}

	close(releasePreRoot)
	require.NoError(t, <-preRootDone)
	require.NoError(t, <-rootDone)
	require.FileExists(t, fixture.proofPath)
	_, err := os.Lstat(filepath.Join(fixture.global.commitDirectory, "receipt.json"))
	require.ErrorIs(t, err, os.ErrNotExist)
	for _, directory := range fixture.global.directories {
		_, err := os.Lstat(filepath.Join(directory, "receipt.json"))
		require.ErrorIs(t, err, os.ErrNotExist)
	}

	proofRaw, err := os.ReadFile(fixture.proofPath)
	require.NoError(t, err)
	fixture.global.replace(PlatformRelayRootProofFileEnvironment, proofRaw)
	require.EqualError(t, ValidateAndCommitPlatformRelaySecretIsolation(),
		"Relay secret isolation cannot downgrade an existing root proof")
}

func TestRootProofStateLockTimeoutLeavesNoProofMarkerOrReceipt(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	delete(fixture.global.rawByEnvironment, PlatformRelayRootProofFileEnvironment)
	t.Setenv(platformRelaySecretIsolationGenerationEnvironment, platformRelaySecretIsolationGenerationPreRoot)

	held, err := platformRelayRootIsolationAcquireProofStateLock(fixture.proofPath)
	require.NoError(t, err)
	defer held.release()
	previousTimeout := platformRelayRootProofLockTimeout
	previousRetry := platformRelayRootProofLockRetry
	platformRelayRootProofLockTimeout = 80 * time.Millisecond
	platformRelayRootProofLockRetry = 5 * time.Millisecond
	t.Cleanup(func() {
		platformRelayRootProofLockTimeout = previousTimeout
		platformRelayRootProofLockRetry = previousRetry
	})

	for _, validate := range []func() error{
		ValidateAndCommitPlatformRelaySecretIsolation,
		ValidateAndCommitPlatformRelayRootSecretIsolation,
	} {
		require.EqualError(t, validate(), "Relay root isolation proof lock acquisition timed out")
	}
	require.NoFileExists(t, fixture.proofPath)
	require.NoFileExists(t, fixture.receiptPath)
	require.NoFileExists(t, filepath.Join(fixture.global.commitDirectory, "receipt.json"))
	for _, directory := range fixture.global.directories {
		require.NoFileExists(t, filepath.Join(directory, "receipt.json"))
	}
}

func TestRootProofStateLockUsesOpenedInodeAcrossPathAliases(t *testing.T) {
	leftDirectory := t.TempDir()
	rightDirectory := t.TempDir()
	if runtime.GOOS != "windows" {
		require.NoError(t, os.Chmod(leftDirectory, 0o700))
		require.NoError(t, os.Chmod(rightDirectory, 0o700))
	}
	leftLock := filepath.Join(leftDirectory, platformRelayRootProofLockFileName)
	rightLock := filepath.Join(rightDirectory, platformRelayRootProofLockFileName)
	require.NoError(t, os.WriteFile(leftLock, nil, 0o600))
	if err := os.Link(leftLock, rightLock); err != nil {
		t.Skipf("hardlink aliases are unavailable: %v", err)
	}
	leftInfo, err := os.Stat(leftLock)
	require.NoError(t, err)
	rightInfo, err := os.Stat(rightLock)
	require.NoError(t, err)
	require.True(t, os.SameFile(leftInfo, rightInfo))

	held, err := platformRelayRootIsolationAcquireProofStateLock(filepath.Join(leftDirectory, "proof.json"))
	require.NoError(t, err)
	defer held.release()
	previousTimeout := platformRelayRootProofLockTimeout
	previousRetry := platformRelayRootProofLockRetry
	platformRelayRootProofLockTimeout = 80 * time.Millisecond
	platformRelayRootProofLockRetry = 5 * time.Millisecond
	t.Cleanup(func() {
		platformRelayRootProofLockTimeout = previousTimeout
		platformRelayRootProofLockRetry = previousRetry
	})

	_, err = platformRelayRootIsolationAcquireProofStateLock(filepath.Join(rightDirectory, "proof.json"))
	require.EqualError(t, err, "Relay root isolation proof lock acquisition timed out")
}

func TestRootProofCreationHoldsLockUntilPreRootCanObserveCommittedProof(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	previousHook := platformRelayRootIsolationAfterProofCommit
	rootPaused := make(chan struct{})
	releaseRoot := make(chan struct{})
	platformRelayRootIsolationAfterProofCommit = func() error {
		close(rootPaused)
		<-releaseRoot
		return nil
	}
	t.Cleanup(func() { platformRelayRootIsolationAfterProofCommit = previousHook })

	rootDone := make(chan error, 1)
	go func() { rootDone <- ValidateAndCommitPlatformRelayRootSecretIsolation() }()
	<-rootPaused
	proofRaw, err := os.ReadFile(fixture.proofPath)
	require.NoError(t, err)
	fixture.global.replace(PlatformRelayRootProofFileEnvironment, proofRaw)
	t.Setenv(platformRelaySecretIsolationGenerationEnvironment, platformRelaySecretIsolationGenerationPreRoot)

	preRootDone := make(chan error, 1)
	go func() { preRootDone <- ValidateAndCommitPlatformRelaySecretIsolation() }()
	select {
	case err := <-preRootDone:
		require.Failf(t, "pre-root validator bypassed proof lock", "returned before root validator released lock: %v", err)
	case <-time.After(150 * time.Millisecond):
	}

	close(releaseRoot)
	require.NoError(t, <-rootDone)
	require.EqualError(t, <-preRootDone, "Relay secret isolation cannot downgrade an existing root proof")
	require.NoFileExists(t, filepath.Join(fixture.global.commitDirectory, "receipt.json"))
	for _, directory := range fixture.global.directories {
		require.NoFileExists(t, filepath.Join(directory, "receipt.json"))
	}
}

func TestRootProofValidatorRejectsDifferentReadAndWriteVolumesBeforeRevocationOrWrite(t *testing.T) {
	fixture := newPlatformRelayRootIsolationTestFixture(t)
	writeDirectory := t.TempDir()
	if runtime.GOOS != "windows" {
		require.NoError(t, os.Chmod(writeDirectory, 0o700))
	}
	t.Setenv(PlatformRelayRootProofDirectoryEnvironment, writeDirectory)

	require.EqualError(t, ValidateAndCommitPlatformRelayRootSecretIsolation(),
		"Relay root isolation proof read and write directories must be identical")
	require.NoFileExists(t, filepath.Join(writeDirectory, "proof.json"))
	require.NoFileExists(t, fixture.proofPath)
	require.NoFileExists(t, fixture.receiptPath)
	require.NoFileExists(t, filepath.Join(fixture.global.commitDirectory, "receipt.json"))
}
