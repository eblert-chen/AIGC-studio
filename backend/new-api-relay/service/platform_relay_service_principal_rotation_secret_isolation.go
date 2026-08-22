package service

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/QuantumNous/new-api/common"
)

const (
	PlatformRelaySecretIsolationConsumerPrincipalRotation = "principal-rotation"
	PlatformRelayCurrentServicePrincipalsFileEnvironment  = "RELAY_CURRENT_SERVICE_PRINCIPALS_FILE"

	platformRelayPrincipalRotationPrincipalsMaxBytes = 512 * 1024
)

// PlatformRelayServicePrincipalRotationInputs are the exact, receipt-bound
// documents that an offline rotation command may submit to the database. The
// verifier returns values decoded from its strict-read byte slices; the caller
// never reopens either bind mount.
type PlatformRelayServicePrincipalRotationInputs struct {
	AttemptID string
	Current   []PlatformRelayServicePrincipalProvisionInput
	Desired   []PlatformRelayServicePrincipalProvisionInput
}

func clearPlatformRelayServicePrincipalRotationInputs(
	inputs []PlatformRelayServicePrincipalProvisionInput,
) {
	for index := range inputs {
		inputs[index].UpstreamToken = ""
	}
}

type platformRelayPrincipalRotationFileIdentity struct {
	currentPath string
	desiredPath string
	currentInfo os.FileInfo
	desiredInfo os.FileInfo
}

func platformRelayPrincipalRotationInspectFileIdentity() (platformRelayPrincipalRotationFileIdentity, error) {
	identity := platformRelayPrincipalRotationFileIdentity{
		currentPath: strings.TrimSpace(os.Getenv(PlatformRelayCurrentServicePrincipalsFileEnvironment)),
		desiredPath: strings.TrimSpace(os.Getenv("RELAY_SERVICE_PRINCIPALS_FILE")),
	}
	if identity.currentPath == "" || identity.desiredPath == "" ||
		identity.currentPath != os.Getenv(PlatformRelayCurrentServicePrincipalsFileEnvironment) ||
		identity.desiredPath != os.Getenv("RELAY_SERVICE_PRINCIPALS_FILE") ||
		!filepath.IsAbs(identity.currentPath) || !filepath.IsAbs(identity.desiredPath) ||
		filepath.Clean(identity.currentPath) != identity.currentPath ||
		filepath.Clean(identity.desiredPath) != identity.desiredPath ||
		identity.currentPath == identity.desiredPath {
		return platformRelayPrincipalRotationFileIdentity{}, errors.New("Relay principal rotation source identity is invalid")
	}
	currentInfo, currentErr := os.Lstat(identity.currentPath)
	desiredInfo, desiredErr := os.Lstat(identity.desiredPath)
	if currentErr != nil || desiredErr != nil ||
		currentInfo.Mode()&os.ModeSymlink != 0 || desiredInfo.Mode()&os.ModeSymlink != 0 ||
		!currentInfo.Mode().IsRegular() || !desiredInfo.Mode().IsRegular() ||
		os.SameFile(currentInfo, desiredInfo) {
		return platformRelayPrincipalRotationFileIdentity{}, errors.New("Relay principal rotation source identity is invalid")
	}
	identity.currentInfo = currentInfo
	identity.desiredInfo = desiredInfo
	return identity, nil
}

func (identity platformRelayPrincipalRotationFileIdentity) verifyStable() error {
	currentInfo, currentErr := os.Lstat(identity.currentPath)
	desiredInfo, desiredErr := os.Lstat(identity.desiredPath)
	if currentErr != nil || desiredErr != nil ||
		currentInfo.Mode()&os.ModeSymlink != 0 || desiredInfo.Mode()&os.ModeSymlink != 0 ||
		!currentInfo.Mode().IsRegular() || !desiredInfo.Mode().IsRegular() ||
		!os.SameFile(identity.currentInfo, currentInfo) ||
		!os.SameFile(identity.desiredInfo, desiredInfo) ||
		os.SameFile(currentInfo, desiredInfo) {
		return errors.New("Relay principal rotation source identity changed during validation")
	}
	return nil
}

func platformRelayPrincipalRotationFindFile(
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
			return platformRelaySecretIsolationFile{}, errors.New("Relay principal rotation source set is ambiguous")
		}
		selected = file
		found = true
	}
	if !found || len(selected.raw) == 0 {
		return platformRelaySecretIsolationFile{}, errors.New("Relay principal rotation source set is incomplete")
	}
	return selected, nil
}

func platformRelayPrincipalRotationCurrentIsolationFile(
	raw []byte,
) (platformRelaySecretIsolationFile, error) {
	file, err := platformRelaySecretIsolationPrincipalFile(raw)
	if err != nil {
		return platformRelaySecretIsolationFile{}, errors.New("Relay principal rotation current source is invalid")
	}
	file.id = "current_service_principals"
	file.environment = PlatformRelayCurrentServicePrincipalsFileEnvironment
	for index := range file.representations {
		file.representations[index].id = "rotation.current." + file.representations[index].id
	}
	return file, nil
}

// An unchanged token in a partial rotation deliberately appears once in each
// principal document. Mark only those exact same-purpose canonical/bare pairs
// as two-member equivalence groups. Every old token that changes remains an
// ordinary globally unique representation and therefore cannot collide with a
// secret being introduced elsewhere in the same release.
func platformRelayPrincipalRotationBindUnchangedRepresentations(
	current *platformRelaySecretIsolationFile,
	desired *platformRelaySecretIsolationFile,
	currentInputs []PlatformRelayServicePrincipalProvisionInput,
	desiredInputs []PlatformRelayServicePrincipalProvisionInput,
) error {
	if current == nil || desired == nil ||
		current.id != "current_service_principals" || desired.id != "service_principals" ||
		len(currentInputs) != len(desiredInputs) ||
		len(current.representations) != 2*len(currentInputs) ||
		len(desired.representations) != 2*len(desiredInputs) {
		return errors.New("Relay principal rotation representation set is invalid")
	}
	for index := range currentInputs {
		before := currentInputs[index]
		after := desiredInputs[index]
		if before.ClientID != after.ClientID || before.TenantID != after.TenantID {
			return ErrProtectedPlatformRelayPrincipalRotationConflict
		}
		beforeDigest := sha256.Sum256([]byte(before.UpstreamToken))
		afterDigest := sha256.Sum256([]byte(after.UpstreamToken))
		if subtle.ConstantTimeCompare(beforeDigest[:], afterDigest[:]) != 1 {
			continue
		}
		for offset, representationKind := range []string{"canonical", "bare"} {
			group := fmt.Sprintf("rotation.same-purpose.%03d.%s", index, representationKind)
			currentRepresentation := &current.representations[2*index+offset]
			desiredRepresentation := &desired.representations[2*index+offset]
			currentRepresentation.equivalenceGroup = group
			currentRepresentation.equivalenceCount = 2
			desiredRepresentation.equivalenceGroup = group
			desiredRepresentation.equivalenceCount = 2
		}
	}
	return nil
}

func platformRelayPrincipalRotationReceipt(
	release platformRelaySecretIsolationRelease,
	runtime platformRelaySecretIsolationFile,
	relayCA platformRelaySecretIsolationFile,
	desired platformRelaySecretIsolationFile,
	current platformRelaySecretIsolationFile,
	plan platformRelayServicePrincipalRotationPlan,
) (platformRelaySecretIsolationReceipt, error) {
	if runtime.id != "runtime_dsn" || desired.id != "service_principals" ||
		current.id != "current_service_principals" || relayCA.id != "relay_database_ca" ||
		len(runtime.raw) == 0 || len(relayCA.raw) == 0 || len(desired.raw) == 0 || len(current.raw) == 0 ||
		len(runtime.representations) != 3 ||
		len(relayCA.representations) != 0 ||
		len(desired.representations) != 2*len(plan.desired) ||
		len(current.representations) != 2*len(plan.current) {
		return platformRelaySecretIsolationReceipt{}, errors.New("Relay principal rotation receipt source is invalid")
	}
	receipt := platformRelaySecretIsolationReceipt{
		SchemaVersion: PlatformRelaySecretIsolationReceiptSchemaVersion,
		Kind:          PlatformRelaySecretIsolationReceiptKind,
		Consumer:      PlatformRelaySecretIsolationConsumerPrincipalRotation,
		Release:       release,
	}
	for _, file := range []struct {
		id  string
		raw []byte
	}{
		{id: runtime.id, raw: runtime.raw},
		{id: relayCA.id, raw: relayCA.raw},
		{id: desired.id, raw: desired.raw},
		{id: current.id, raw: current.raw},
	} {
		digest := sha256.Sum256(file.raw)
		receipt.Files = append(receipt.Files, platformRelaySecretIsolationCommitment{
			ID: file.id, SHA256: hex.EncodeToString(digest[:]),
		})
	}
	receipt.Semantics = append(
		receipt.Semantics,
		platformRelaySecretIsolationRepresentations(runtime.representations).toCommitments()...,
	)
	sort.Slice(receipt.Files, func(left, right int) bool { return receipt.Files[left].ID < receipt.Files[right].ID })
	sort.Slice(receipt.Semantics, func(left, right int) bool { return receipt.Semantics[left].ID < receipt.Semantics[right].ID })
	return receipt, nil
}

// Go does not permit methods on a slice type literal. Keep the conversion
// local and explicit so rotation receipts use the exact representation digests
// computed by the normal global validator without exposing their raw values.
type platformRelaySecretIsolationRepresentations []platformRelaySecretIsolationRepresentation

func (representations platformRelaySecretIsolationRepresentations) toCommitments() []platformRelaySecretIsolationCommitment {
	commitments := make([]platformRelaySecretIsolationCommitment, 0, len(representations))
	for _, representation := range representations {
		commitments = append(commitments, platformRelaySecretIsolationCommitment{
			ID: representation.id, SHA256: hex.EncodeToString(representation.digest[:]),
		})
	}
	return commitments
}

func platformRelayPrincipalRotationPrepareReceiptDirectory() (*platformRelaySecretIsolationReceiptDirectory, error) {
	environment := platformRelaySecretIsolationReceiptDirectoryEnvironment(
		PlatformRelaySecretIsolationConsumerPrincipalRotation,
	)
	directory := strings.TrimSpace(os.Getenv(
		environment,
	))
	if directory == "" || directory != os.Getenv(environment) ||
		!filepath.IsAbs(directory) || filepath.Clean(directory) != directory {
		return nil, errors.New("Relay principal rotation receipt directory is invalid")
	}
	opened, err := platformRelaySecretIsolationOpenReceiptDirectory(directory)
	if err != nil {
		return nil, errors.New("Relay principal rotation receipt directory is invalid")
	}
	if removeErr := opened.remove("receipt.json"); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
		_ = opened.close()
		return nil, errors.New("Relay principal rotation stale receipt could not be removed")
	}
	if err := opened.sync(); err != nil {
		_ = opened.close()
		return nil, errors.New("Relay principal rotation receipt directory could not be synchronized")
	}
	return opened, nil
}

// ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation is a
// network-free same-image release gate. It reuses the complete normal global
// source parser and collision proof for desired A, then adds a strict current A
// CAS source without joining the ordinary rollout consumer set.
func ValidateAndCommitPlatformRelayPrincipalRotationSecretIsolation() error {
	directory, err := platformRelayPrincipalRotationPrepareReceiptDirectory()
	if err != nil {
		return err
	}
	defer directory.close()
	release, err := platformRelaySecretIsolationReleaseIdentity()
	if err != nil {
		return err
	}
	identity, err := platformRelayPrincipalRotationInspectFileIdentity()
	if err != nil {
		return err
	}
	files, err := platformRelaySecretIsolationReadValidatorSources()
	if err != nil {
		return err
	}
	defer platformRelaySecretIsolationClearFiles(files)
	currentRaw, err := platformRelaySecretIsolationReadFile(
		PlatformRelayCurrentServicePrincipalsFileEnvironment,
		platformRelayPrincipalRotationPrincipalsMaxBytes,
	)
	if err != nil {
		return errors.New("Relay principal rotation current source is unavailable or invalid")
	}
	defer clear(currentRaw)
	if err := identity.verifyStable(); err != nil {
		return err
	}
	if err := platformRelaySecretIsolationBindPlatformContracts(files); err != nil {
		return err
	}
	desiredFile, err := platformRelayPrincipalRotationFindFile(files, "service_principals")
	if err != nil {
		return err
	}
	runtimeFile, err := platformRelayPrincipalRotationFindFile(files, "runtime_dsn")
	if err != nil {
		return err
	}
	relayCAFile, err := platformRelayPrincipalRotationFindFile(files, "relay_database_ca")
	if err != nil {
		return err
	}
	current, err := ParsePlatformRelayServicePrincipalsFile(currentRaw)
	if err != nil {
		return errors.New("Relay principal rotation current source is invalid")
	}
	defer clearPlatformRelayServicePrincipalRotationInputs(current)
	desired, err := ParsePlatformRelayServicePrincipalsFile(desiredFile.raw)
	if err != nil {
		return errors.New("Relay principal rotation desired source is invalid")
	}
	defer clearPlatformRelayServicePrincipalRotationInputs(desired)
	plan, err := buildPlatformRelayServicePrincipalRotationPlan(current, desired)
	if err != nil {
		return err
	}
	currentFile, err := platformRelayPrincipalRotationCurrentIsolationFile(currentRaw)
	if err != nil {
		return err
	}
	if err := platformRelayPrincipalRotationBindUnchangedRepresentations(
		&currentFile, &desiredFile, current, desired,
	); err != nil {
		return err
	}
	files = append(files, currentFile)
	if err := platformRelaySecretIsolationValidateRepresentations(files); err != nil {
		return err
	}
	receipt, err := platformRelayPrincipalRotationReceipt(
		release, runtimeFile, relayCAFile, desiredFile, currentFile, plan,
	)
	if err != nil {
		return err
	}
	var runBytes [32]byte
	if _, err := io.ReadFull(rand.Reader, runBytes[:]); err != nil {
		return errors.New("Relay principal rotation run identity could not be generated")
	}
	receipt.RunID = hex.EncodeToString(runBytes[:])
	clear(runBytes[:])
	return directory.writeReceipt(receipt)
}

func platformRelayPrincipalRotationReadConsumerSources() (
	[]platformRelaySecretIsolationFile,
	[]PlatformRelayServicePrincipalProvisionInput,
	[]PlatformRelayServicePrincipalProvisionInput,
	platformRelayServicePrincipalRotationPlan,
	error,
) {
	fail := func(files []platformRelaySecretIsolationFile, err error) (
		[]platformRelaySecretIsolationFile,
		[]PlatformRelayServicePrincipalProvisionInput,
		[]PlatformRelayServicePrincipalProvisionInput,
		platformRelayServicePrincipalRotationPlan,
		error,
	) {
		platformRelaySecretIsolationClearFiles(files)
		return nil, nil, nil, platformRelayServicePrincipalRotationPlan{}, err
	}
	identity, err := platformRelayPrincipalRotationInspectFileIdentity()
	if err != nil {
		return fail(nil, err)
	}
	currentRaw, err := platformRelaySecretIsolationReadFile(
		PlatformRelayCurrentServicePrincipalsFileEnvironment,
		platformRelayPrincipalRotationPrincipalsMaxBytes,
	)
	if err != nil {
		return fail(nil, errors.New("Relay principal rotation current source is unavailable or invalid"))
	}
	currentFile, err := platformRelayPrincipalRotationCurrentIsolationFile(currentRaw)
	if err != nil {
		clear(currentRaw)
		return fail(nil, err)
	}
	files := []platformRelaySecretIsolationFile{currentFile}
	desiredRaw, err := platformRelaySecretIsolationReadFile(
		"RELAY_SERVICE_PRINCIPALS_FILE",
		platformRelayPrincipalRotationPrincipalsMaxBytes,
	)
	if err != nil {
		return fail(files, errors.New("Relay principal rotation desired source is unavailable or invalid"))
	}
	desiredFile, err := platformRelaySecretIsolationPrincipalFile(desiredRaw)
	if err != nil {
		clear(desiredRaw)
		return fail(files, errors.New("Relay principal rotation desired source is invalid"))
	}
	desiredFile.environment = "RELAY_SERVICE_PRINCIPALS_FILE"
	files = append(files, desiredFile)
	runtimeRaw, err := platformRelaySecretIsolationReadFile("SQL_DSN_FILE", 16*1024)
	if err != nil {
		return fail(files, errors.New("Relay principal rotation database source is unavailable or invalid"))
	}
	runtimeFile, password, err := platformRelaySecretIsolationDSNFile("runtime_dsn", runtimeRaw, "")
	clear(password)
	if err != nil {
		clear(runtimeRaw)
		return fail(files, errors.New("Relay principal rotation database source is invalid"))
	}
	runtimeFile.environment = "SQL_DSN_FILE"
	files = append(files, runtimeFile)
	relayCARaw, err := platformRelaySecretIsolationReadFile(
		platformRelaySecretIsolationRelayDatabaseCAEnvironment,
		256*1024,
	)
	if err != nil {
		return fail(files, errors.New("Relay principal rotation database CA source is unavailable or invalid"))
	}
	relayCAFile, err := platformRelaySecretIsolationCAFile("relay_database_ca", relayCARaw)
	if err != nil {
		clear(relayCARaw)
		return fail(files, errors.New("Relay principal rotation database CA source is invalid"))
	}
	relayCAFile.environment = platformRelaySecretIsolationRelayDatabaseCAEnvironment
	files = append(files, relayCAFile)
	if err := identity.verifyStable(); err != nil {
		return fail(files, err)
	}
	current, err := ParsePlatformRelayServicePrincipalsFile(currentRaw)
	if err != nil {
		return fail(files, errors.New("Relay principal rotation current source is invalid"))
	}
	desired, err := ParsePlatformRelayServicePrincipalsFile(desiredRaw)
	if err != nil {
		clearPlatformRelayServicePrincipalRotationInputs(current)
		return fail(files, errors.New("Relay principal rotation desired source is invalid"))
	}
	plan, err := buildPlatformRelayServicePrincipalRotationPlan(current, desired)
	if err != nil {
		clearPlatformRelayServicePrincipalRotationInputs(current)
		clearPlatformRelayServicePrincipalRotationInputs(desired)
		return fail(files, err)
	}
	if err := platformRelayPrincipalRotationBindUnchangedRepresentations(
		&files[0], &files[1], current, desired,
	); err != nil {
		clearPlatformRelayServicePrincipalRotationInputs(current)
		clearPlatformRelayServicePrincipalRotationInputs(desired)
		return fail(files, err)
	}
	return files, current, desired, plan, nil
}

// VerifyPlatformRelayPrincipalRotationSecretIsolationReceipt binds the
// rotation-only current/desired pair and runtime DSN to the same immutable
// release that performed the complete desired-secret global isolation proof.
// It returns parsed values from those exact reads and atomically pins both A
// documents, the runtime DSN, and the Relay CA before the caller opens the
// database.
func VerifyPlatformRelayPrincipalRotationSecretIsolationReceipt() (
	PlatformRelayServicePrincipalRotationInputs,
	error,
) {
	var result PlatformRelayServicePrincipalRotationInputs
	release, err := platformRelaySecretIsolationReleaseIdentity()
	if err != nil {
		return result, err
	}
	files, current, desired, plan, err := platformRelayPrincipalRotationReadConsumerSources()
	if err != nil {
		return result, err
	}
	inputsHandedOff := false
	defer func() {
		if !inputsHandedOff {
			clearPlatformRelayServicePrincipalRotationInputs(current)
			clearPlatformRelayServicePrincipalRotationInputs(desired)
		}
	}()
	defer platformRelaySecretIsolationClearFiles(files)
	currentFile, err := platformRelayPrincipalRotationFindFile(files, "current_service_principals")
	if err != nil {
		return result, err
	}
	desiredFile, err := platformRelayPrincipalRotationFindFile(files, "service_principals")
	if err != nil {
		return result, err
	}
	runtimeFile, err := platformRelayPrincipalRotationFindFile(files, "runtime_dsn")
	if err != nil {
		return result, err
	}
	relayCAFile, err := platformRelayPrincipalRotationFindFile(files, "relay_database_ca")
	if err != nil {
		return result, err
	}
	expected, err := platformRelayPrincipalRotationReceipt(
		release, runtimeFile, relayCAFile, desiredFile, currentFile, plan,
	)
	if err != nil {
		return result, err
	}
	raw, err := platformRelaySecretIsolationReadFile(
		platformRelaySecretIsolationReceiptFileEnvironment,
		platformRelaySecretIsolationReceiptMaxBytes,
	)
	if err != nil {
		return result, errors.New("Relay principal rotation secret isolation receipt is unavailable")
	}
	defer clear(raw)
	actual, err := platformRelaySecretIsolationParseReceipt(raw)
	if err != nil || actual.Consumer != PlatformRelaySecretIsolationConsumerPrincipalRotation {
		return result, errors.New("Relay principal rotation secret isolation receipt is invalid")
	}
	expected.RunID = actual.RunID
	expectedRaw, expectedErr := platformRelaySecretIsolationCanonicalReceipt(expected)
	actualRaw, actualErr := platformRelaySecretIsolationCanonicalReceipt(actual)
	if expectedErr != nil || actualErr != nil || len(expectedRaw) != len(actualRaw) ||
		subtle.ConstantTimeCompare(expectedRaw, actualRaw) != 1 {
		clear(expectedRaw)
		clear(actualRaw)
		return result, errors.New("Relay principal rotation secret isolation receipt does not match mounted sources")
	}
	clear(expectedRaw)
	clear(actualRaw)
	snapshots := make([]common.ProtectedSecretFileSnapshot, 0, len(files))
	for _, file := range files {
		if file.environment == "" || len(file.raw) == 0 {
			return result, errors.New("Relay principal rotation source snapshot is invalid")
		}
		snapshots = append(snapshots, common.ProtectedSecretFileSnapshot{
			Environment: file.environment, Value: file.raw,
		})
	}
	if err := common.InstallProtectedSecretFileSnapshots(snapshots); err != nil {
		return result, errors.New("Relay principal rotation source snapshot could not be installed")
	}
	result.AttemptID = actual.RunID
	result.Current = current
	result.Desired = desired
	inputsHandedOff = true
	return result, nil
}
