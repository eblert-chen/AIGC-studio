package service

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
)

const (
	PlatformRelaySecretIsolationConsumerRootBootstrap = "root-bootstrap"
	PlatformRelayRootUsernameEnvironment              = "RELAY_PROVISION_ROOT_USERNAME"
	PlatformRelayRootPasswordFileEnvironment          = "RELAY_PROVISION_ROOT_PASSWORD_FILE"
	PlatformRelayRootProofDirectoryEnvironment        = "RELAY_ROOT_SECRET_ISOLATION_PROOF_DIRECTORY"
	PlatformRelayRootProofFileEnvironment             = "RELAY_ROOT_SECRET_ISOLATION_PROOF_FILE"

	platformRelayRootPasswordFileMaximumBytes = 4096
	platformRelayRootProofMaximumBytes        = 16 * 1024
	platformRelayRootProofKind                = "relay_root_secret_isolation_proof"
	platformRelayRootProofSchemaVersion       = 1
	platformRelayRootProofLockFileName        = ".proof.lock"
)

var platformRelayRootIsolationAfterProofCommit func() error
var platformRelayRootIsolationProofSourceAbsent = platformRelayRootIsolationProofPathAbsent

type platformRelayRootIsolationProof struct {
	SchemaVersion      int                                 `json:"schema_version"`
	Kind               string                              `json:"kind"`
	ProofID            string                              `json:"proof_id"`
	CreatedRelease     platformRelaySecretIsolationRelease `json:"created_release"`
	RootPasswordSHA256 string                              `json:"root_password_sha256"`
}

func platformRelayRootProofTemporaryNameValid(name string) bool {
	if !strings.HasPrefix(name, ".proof.") || !strings.HasSuffix(name, ".tmp") {
		return false
	}
	proofID := strings.TrimSuffix(strings.TrimPrefix(name, ".proof."), ".tmp")
	return platformRelaySecretIsolationRunIDPattern.MatchString(proofID)
}

// PlatformRelayRootProvisionInputs are parsed from the exact strict reads
// committed by the install-only isolation profile. The root command must use
// this value directly and must not reopen the host bind mount.
type PlatformRelayRootProvisionInputs struct {
	Username string
	Password string
}

func platformRelayRootIsolationFile(raw []byte, username string) (platformRelaySecretIsolationFile, error) {
	if err := model.ValidateProductionRootCredentials(username, string(raw)); err != nil {
		return platformRelaySecretIsolationFile{}, errors.New("Relay root isolation input is invalid")
	}
	return platformRelaySecretIsolationFile{
		id:          "root_password",
		environment: PlatformRelayRootPasswordFileEnvironment,
		raw:         raw,
		representations: []platformRelaySecretIsolationRepresentation{
			platformRelaySecretIsolationDigest("root.password", raw, ""),
		},
	}, nil
}

func platformRelayRootIsolationProofFile(
	raw []byte,
	rootRaw []byte,
) (platformRelaySecretIsolationFile, string, error) {
	proof, err := platformRelayRootIsolationParseProof(raw)
	rootDigest := sha256.Sum256(rootRaw)
	if err != nil || proof.RootPasswordSHA256 != hex.EncodeToString(rootDigest[:]) {
		return platformRelaySecretIsolationFile{}, "", errors.New("Relay root isolation proof does not match the root source")
	}
	return platformRelaySecretIsolationFile{
		id:          "root_proof",
		environment: PlatformRelayRootProofFileEnvironment,
		raw:         raw,
		representations: []platformRelaySecretIsolationRepresentation{
			platformRelaySecretIsolationDigest("root.proof_id", []byte(proof.ProofID), ""),
		},
	}, proof.ProofID, nil
}

func platformRelayRootIsolationUsername() (string, error) {
	username := os.Getenv(PlatformRelayRootUsernameEnvironment)
	if username == "" || username != strings.TrimSpace(username) || strings.ContainsAny(username, "\x00\r\n") {
		return "", errors.New("Relay root isolation input is invalid")
	}
	return username, nil
}

func platformRelayRootIsolationFindFile(
	files []platformRelaySecretIsolationFile,
	id string,
) (platformRelaySecretIsolationFile, error) {
	var selected platformRelaySecretIsolationFile
	found := false
	for _, file := range files {
		if file.id != id {
			continue
		}
		if found {
			return platformRelaySecretIsolationFile{}, errors.New("Relay root isolation source set is ambiguous")
		}
		selected = file
		found = true
	}
	if !found || len(selected.raw) == 0 {
		return platformRelaySecretIsolationFile{}, errors.New("Relay root isolation source set is incomplete")
	}
	return selected, nil
}

func platformRelayRootIsolationReceipt(
	release platformRelaySecretIsolationRelease,
	runtime platformRelaySecretIsolationFile,
	relayCA platformRelaySecretIsolationFile,
	root platformRelaySecretIsolationFile,
	proof platformRelaySecretIsolationFile,
	username string,
) (platformRelaySecretIsolationReceipt, error) {
	if runtime.id != "runtime_dsn" || relayCA.id != "relay_database_ca" || root.id != "root_password" ||
		proof.id != "root_proof" || len(runtime.raw) == 0 || len(relayCA.raw) == 0 || len(root.raw) == 0 ||
		len(proof.raw) == 0 || username == "" || len(runtime.representations) != 3 ||
		len(relayCA.representations) != 0 || len(root.representations) != 1 || len(proof.representations) != 1 {
		return platformRelaySecretIsolationReceipt{}, errors.New("Relay root isolation receipt source is invalid")
	}
	receipt := platformRelaySecretIsolationReceipt{
		SchemaVersion: PlatformRelaySecretIsolationReceiptSchemaVersion,
		Kind:          PlatformRelaySecretIsolationReceiptKind,
		Consumer:      PlatformRelaySecretIsolationConsumerRootBootstrap,
		Release:       release,
	}
	for _, file := range []platformRelaySecretIsolationFile{runtime, relayCA, root, proof} {
		digest := sha256.Sum256(file.raw)
		receipt.Files = append(receipt.Files, platformRelaySecretIsolationCommitment{
			ID: file.id, SHA256: hex.EncodeToString(digest[:]),
		})
	}
	for _, representation := range runtime.representations {
		receipt.Semantics = append(receipt.Semantics, platformRelaySecretIsolationCommitment{
			ID: representation.id, SHA256: hex.EncodeToString(representation.digest[:]),
		})
	}
	for _, representation := range proof.representations {
		receipt.Semantics = append(receipt.Semantics, platformRelaySecretIsolationCommitment{
			ID: representation.id, SHA256: hex.EncodeToString(representation.digest[:]),
		})
	}
	usernameDigest := sha256.Sum256([]byte(username))
	receipt.Semantics = append(receipt.Semantics, platformRelaySecretIsolationCommitment{
		ID: "root.username", SHA256: hex.EncodeToString(usernameDigest[:]),
	})
	sort.Slice(receipt.Files, func(left, right int) bool { return receipt.Files[left].ID < receipt.Files[right].ID })
	sort.Slice(receipt.Semantics, func(left, right int) bool { return receipt.Semantics[left].ID < receipt.Semantics[right].ID })
	return receipt, nil
}

func platformRelayRootIsolationPrepareReceiptDirectory() (*platformRelaySecretIsolationReceiptDirectory, error) {
	environment := platformRelaySecretIsolationReceiptDirectoryEnvironment(
		PlatformRelaySecretIsolationConsumerRootBootstrap,
	)
	directory := os.Getenv(environment)
	if directory == "" || directory != strings.TrimSpace(directory) ||
		!filepath.IsAbs(directory) || filepath.Clean(directory) != directory {
		return nil, errors.New("Relay root isolation receipt directory is invalid")
	}
	opened, err := platformRelaySecretIsolationOpenReceiptDirectory(directory)
	if err != nil {
		return nil, errors.New("Relay root isolation receipt directory is invalid")
	}
	if removeErr := opened.remove("receipt.json"); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
		_ = opened.close()
		return nil, errors.New("Relay root isolation stale receipt could not be removed")
	}
	if err := opened.sync(); err != nil {
		_ = opened.close()
		return nil, errors.New("Relay root isolation receipt directory could not be synchronized")
	}
	return opened, nil
}

func platformRelayRootIsolationOpenProofDirectory() (*platformRelaySecretIsolationReceiptDirectory, error) {
	directory := os.Getenv(PlatformRelayRootProofDirectoryEnvironment)
	if directory == "" || directory != strings.TrimSpace(directory) ||
		!filepath.IsAbs(directory) || filepath.Clean(directory) != directory {
		return nil, errors.New("Relay root isolation proof directory is invalid")
	}
	opened, err := platformRelaySecretIsolationOpenReceiptDirectory(directory)
	if err != nil {
		return nil, errors.New("Relay root isolation proof directory is invalid")
	}
	return opened, nil
}

func platformRelayRootIsolationParseProof(raw []byte) (platformRelayRootIsolationProof, error) {
	var proof platformRelayRootIsolationProof
	if len(raw) == 0 || common.RejectDuplicateJSONKeys(raw) != nil {
		return proof, errors.New("Relay root isolation proof is invalid")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&proof) != nil {
		return platformRelayRootIsolationProof{}, errors.New("Relay root isolation proof is invalid")
	}
	var trailing any
	if decoder.Decode(&trailing) != io.EOF ||
		proof.SchemaVersion != platformRelayRootProofSchemaVersion || proof.Kind != platformRelayRootProofKind ||
		!platformRelaySecretIsolationRunIDPattern.MatchString(proof.ProofID) ||
		!platformRelaySecretIsolationReleaseShapeValid(proof.CreatedRelease) ||
		len(proof.RootPasswordSHA256) != sha256.Size*2 || proof.RootPasswordSHA256 != strings.ToLower(proof.RootPasswordSHA256) {
		return platformRelayRootIsolationProof{}, errors.New("Relay root isolation proof is invalid")
	}
	decoded, err := hex.DecodeString(proof.RootPasswordSHA256)
	if err != nil || len(decoded) != sha256.Size {
		clear(decoded)
		return platformRelayRootIsolationProof{}, errors.New("Relay root isolation proof is invalid")
	}
	clear(decoded)
	canonical, err := json.Marshal(proof)
	if err != nil || len(canonical) != len(raw) || subtle.ConstantTimeCompare(canonical, raw) != 1 {
		clear(canonical)
		return platformRelayRootIsolationProof{}, errors.New("Relay root isolation proof is invalid")
	}
	clear(canonical)
	return proof, nil
}

func platformRelayRootIsolationEnsureProof(
	directory *platformRelaySecretIsolationReceiptDirectory,
	release platformRelaySecretIsolationRelease,
	rootDigest [sha256.Size]byte,
) (string, error) {
	readExisting := func() (string, error) {
		raw, readErr := directory.readRootProof(platformRelayRootProofMaximumBytes)
		if readErr != nil {
			return "", readErr
		}
		proof, parseErr := platformRelayRootIsolationParseProof(raw)
		clear(raw)
		if parseErr != nil || proof.RootPasswordSHA256 != hex.EncodeToString(rootDigest[:]) {
			return "", errors.New("Relay root isolation proof conflicts with the requested root")
		}
		return proof.ProofID, nil
	}
	raw, err := directory.readRootProof(platformRelayRootProofMaximumBytes)
	if err == nil {
		proof, parseErr := platformRelayRootIsolationParseProof(raw)
		clear(raw)
		if parseErr != nil || proof.RootPasswordSHA256 != hex.EncodeToString(rootDigest[:]) {
			return "", errors.New("Relay root isolation proof conflicts with the requested root")
		}
		return proof.ProofID, nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return "", errors.New("Relay root isolation proof is unavailable or invalid")
	}
	var proofIDBytes [32]byte
	if _, err := io.ReadFull(rand.Reader, proofIDBytes[:]); err != nil {
		return "", errors.New("Relay root isolation proof identity could not be generated")
	}
	proof := platformRelayRootIsolationProof{
		SchemaVersion:      platformRelayRootProofSchemaVersion,
		Kind:               platformRelayRootProofKind,
		ProofID:            hex.EncodeToString(proofIDBytes[:]),
		CreatedRelease:     release,
		RootPasswordSHA256: hex.EncodeToString(rootDigest[:]),
	}
	clear(proofIDBytes[:])
	if err := directory.writeRootProof(proof); err != nil {
		if errors.Is(err, os.ErrExist) {
			return readExisting()
		}
		return "", err
	}
	return proof.ProofID, nil
}

func platformRelayRootIsolationReadPermanentProof() (platformRelayRootIsolationProof, error) {
	raw, err := platformRelaySecretIsolationReadFile(
		PlatformRelayRootProofFileEnvironment,
		platformRelayRootProofMaximumBytes,
	)
	if err != nil {
		return platformRelayRootIsolationProof{}, errors.New("Relay root isolation proof is unavailable")
	}
	defer clear(raw)
	proof, err := platformRelayRootIsolationParseProof(raw)
	if err != nil {
		return platformRelayRootIsolationProof{}, errors.New("Relay root isolation proof is invalid")
	}
	return proof, nil
}

// ValidateAndCommitPlatformRelayRootSecretIsolation is the network-free fresh
// install gate. It proves the one-time root password is globally distinct from
// every normal Relay/Platform secret without adding that destroyed-after-use
// password to the fourteen-consumer ordinary rollout DAG.
func ValidateAndCommitPlatformRelayRootSecretIsolation() error {
	proofLock, err := platformRelayRootIsolationAcquireProofStateLock(
		os.Getenv(PlatformRelayRootProofFileEnvironment),
	)
	if err != nil {
		return err
	}
	defer proofLock.release()

	directory, err := platformRelayRootIsolationPrepareReceiptDirectory()
	if err != nil {
		return err
	}
	defer directory.close()
	proofDirectory, err := platformRelayRootIsolationOpenProofDirectory()
	if err != nil {
		return err
	}
	defer proofDirectory.close()
	if !os.SameFile(proofLock.directoryInfo, proofDirectory.info) {
		return errors.New("Relay root isolation proof read and write directories must be identical")
	}
	if os.SameFile(directory.info, proofDirectory.info) {
		return errors.New("Relay root isolation proof and receipt directories must be distinct")
	}
	release, err := platformRelaySecretIsolationReleaseIdentity()
	if err != nil {
		return err
	}
	username, err := platformRelayRootIsolationUsername()
	if err != nil {
		return err
	}
	files, err := platformRelaySecretIsolationReadValidatorSources()
	if err != nil {
		return err
	}
	defer platformRelaySecretIsolationClearFiles(files)
	rootRaw, err := platformRelaySecretIsolationReadFile(
		PlatformRelayRootPasswordFileEnvironment,
		platformRelayRootPasswordFileMaximumBytes,
	)
	if err != nil {
		return errors.New("Relay root isolation password source is unavailable or invalid")
	}
	rootFile, err := platformRelayRootIsolationFile(rootRaw, username)
	if err != nil {
		clear(rootRaw)
		return err
	}
	files = append(files, rootFile)
	if err := platformRelaySecretIsolationBindPlatformContracts(files); err != nil {
		return err
	}
	if err := platformRelaySecretIsolationValidateRepresentations(files); err != nil {
		return err
	}
	// Revoke the pre-root marker and every pre-root receipt before proof.json can
	// appear. The proof-state lock prevents another pre-root validator from
	// committing a replacement generation until this command has released it.
	if err := platformRelaySecretIsolationRevokeOrdinaryReceipts(); err != nil {
		return err
	}
	rootDigest := sha256.Sum256(rootFile.raw)
	proofID, err := platformRelayRootIsolationEnsureProof(proofDirectory, release, rootDigest)
	if err != nil {
		return err
	}
	if platformRelayRootIsolationAfterProofCommit != nil {
		if err := platformRelayRootIsolationAfterProofCommit(); err != nil {
			return err
		}
	}
	proofRaw, err := proofDirectory.readRootProof(platformRelayRootProofMaximumBytes)
	if err != nil {
		return errors.New("Relay root isolation proof could not be rebound to the root receipt")
	}
	defer clear(proofRaw)
	proofFile, reboundProofID, err := platformRelayRootIsolationProofFile(proofRaw, rootFile.raw)
	if err != nil || reboundProofID != proofID {
		return errors.New("Relay root isolation proof could not be rebound to the root receipt")
	}
	runtimeFile, err := platformRelayRootIsolationFindFile(files, "runtime_dsn")
	if err != nil {
		return err
	}
	relayCAFile, err := platformRelayRootIsolationFindFile(files, "relay_database_ca")
	if err != nil {
		return err
	}
	receipt, err := platformRelayRootIsolationReceipt(
		release, runtimeFile, relayCAFile, rootFile, proofFile, username,
	)
	if err != nil {
		return err
	}
	var runBytes [32]byte
	if _, err := io.ReadFull(rand.Reader, runBytes[:]); err != nil {
		return errors.New("Relay root isolation run identity could not be generated")
	}
	receipt.RunID = hex.EncodeToString(runBytes[:])
	clear(runBytes[:])
	return directory.writeReceipt(receipt)
}

func platformRelayRootIsolationReadConsumerSources() (
	[]platformRelaySecretIsolationFile,
	string,
	error,
) {
	username, err := platformRelayRootIsolationUsername()
	if err != nil {
		return nil, "", err
	}
	rootRaw, err := platformRelaySecretIsolationReadFile(
		PlatformRelayRootPasswordFileEnvironment,
		platformRelayRootPasswordFileMaximumBytes,
	)
	if err != nil {
		return nil, "", errors.New("Relay root isolation password source is unavailable or invalid")
	}
	rootFile, err := platformRelayRootIsolationFile(rootRaw, username)
	if err != nil {
		clear(rootRaw)
		return nil, "", err
	}
	files := []platformRelaySecretIsolationFile{rootFile}
	fail := func(err error) ([]platformRelaySecretIsolationFile, string, error) {
		platformRelaySecretIsolationClearFiles(files)
		return nil, "", err
	}
	runtimeRaw, err := platformRelaySecretIsolationReadFile("SQL_DSN_FILE", 16*1024)
	if err != nil {
		return fail(errors.New("Relay root isolation database source is unavailable or invalid"))
	}
	runtimeFile, password, err := platformRelaySecretIsolationDSNFile("runtime_dsn", runtimeRaw, "")
	clear(password)
	if err != nil {
		clear(runtimeRaw)
		return fail(errors.New("Relay root isolation database source is invalid"))
	}
	runtimeFile.environment = "SQL_DSN_FILE"
	files = append(files, runtimeFile)
	relayCARaw, err := platformRelaySecretIsolationReadFile(
		platformRelaySecretIsolationRelayDatabaseCAEnvironment,
		256*1024,
	)
	if err != nil {
		return fail(errors.New("Relay root isolation database CA source is unavailable or invalid"))
	}
	relayCAFile, err := platformRelaySecretIsolationCAFile("relay_database_ca", relayCARaw)
	if err != nil {
		clear(relayCARaw)
		return fail(errors.New("Relay root isolation database CA source is invalid"))
	}
	relayCAFile.environment = platformRelaySecretIsolationRelayDatabaseCAEnvironment
	files = append(files, relayCAFile)
	proofRaw, err := platformRelaySecretIsolationReadFile(
		PlatformRelayRootProofFileEnvironment,
		platformRelayRootProofMaximumBytes,
	)
	if err != nil {
		return fail(errors.New("Relay root isolation proof is unavailable or invalid"))
	}
	proofFile, _, err := platformRelayRootIsolationProofFile(proofRaw, rootFile.raw)
	if err != nil {
		clear(proofRaw)
		return fail(err)
	}
	files = append(files, proofFile)
	return files, username, nil
}

// VerifyPlatformRelayRootSecretIsolationReceipt returns values derived from
// the same strict reads used to verify the install receipt and pins those
// bytes for ResolveDatabaseDSN and the TLS loader before any database access.
func VerifyPlatformRelayRootSecretIsolationReceipt() (PlatformRelayRootProvisionInputs, error) {
	var result PlatformRelayRootProvisionInputs
	release, err := platformRelaySecretIsolationReleaseIdentity()
	if err != nil {
		return result, err
	}
	files, username, err := platformRelayRootIsolationReadConsumerSources()
	if err != nil {
		return result, err
	}
	defer platformRelaySecretIsolationClearFiles(files)
	rootFile, err := platformRelayRootIsolationFindFile(files, "root_password")
	if err != nil {
		return result, err
	}
	runtimeFile, err := platformRelayRootIsolationFindFile(files, "runtime_dsn")
	if err != nil {
		return result, err
	}
	relayCAFile, err := platformRelayRootIsolationFindFile(files, "relay_database_ca")
	if err != nil {
		return result, err
	}
	proofFile, err := platformRelayRootIsolationFindFile(files, "root_proof")
	if err != nil {
		return result, err
	}
	expected, err := platformRelayRootIsolationReceipt(
		release, runtimeFile, relayCAFile, rootFile, proofFile, username,
	)
	if err != nil {
		return result, err
	}
	raw, err := platformRelaySecretIsolationReadFile(
		platformRelaySecretIsolationReceiptFileEnvironment,
		platformRelaySecretIsolationReceiptMaxBytes,
	)
	if err != nil {
		return result, errors.New("Relay root isolation receipt is unavailable")
	}
	defer clear(raw)
	actual, err := platformRelaySecretIsolationParseReceipt(raw)
	if err != nil || actual.Consumer != PlatformRelaySecretIsolationConsumerRootBootstrap {
		return result, errors.New("Relay root isolation receipt is invalid")
	}
	expected.RunID = actual.RunID
	expectedRaw, expectedErr := platformRelaySecretIsolationCanonicalReceipt(expected)
	actualRaw, actualErr := platformRelaySecretIsolationCanonicalReceipt(actual)
	if expectedErr != nil || actualErr != nil || len(expectedRaw) != len(actualRaw) ||
		subtle.ConstantTimeCompare(expectedRaw, actualRaw) != 1 {
		clear(expectedRaw)
		clear(actualRaw)
		return result, errors.New("Relay root isolation receipt does not match mounted sources")
	}
	clear(expectedRaw)
	clear(actualRaw)
	// The install-only database release verifier must use the exact root receipt
	// bytes approved here after the pre-root global marker has been revoked.
	snapshots := make([]common.ProtectedSecretFileSnapshot, 0, len(files)+1)
	snapshots = append(snapshots, common.ProtectedSecretFileSnapshot{
		Environment: platformRelaySecretIsolationReceiptFileEnvironment,
		Value:       raw,
	})
	for _, file := range files {
		if file.environment == "" || len(file.raw) == 0 {
			return result, errors.New("Relay root isolation source snapshot is invalid")
		}
		snapshots = append(snapshots, common.ProtectedSecretFileSnapshot{
			Environment: file.environment,
			Value:       file.raw,
		})
	}
	if err := common.InstallProtectedSecretFileSnapshots(snapshots); err != nil {
		return result, errors.New("Relay root isolation source snapshot could not be installed")
	}
	result.Username = username
	result.Password = string(rootFile.raw)
	return result, nil
}
