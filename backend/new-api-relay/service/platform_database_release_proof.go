package service

import (
	"bytes"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

const (
	RelayDatabaseReleaseProofDirectoryEnvironment = "RELAY_DATABASE_RELEASE_PROOF_DIRECTORY"
	RelayDatabaseReleaseProofFileEnvironment      = "RELAY_DATABASE_RELEASE_PROOF_FILE"

	relayDatabaseReleaseProofSchemaVersion = 1
	relayDatabaseReleaseProofKind          = "relay_database_release_proof"
	relayDatabaseReleaseProofMaximumBytes  = 64 * 1024
)

type relayDatabaseReleaseProof struct {
	SchemaVersion          int                                 `json:"schema_version"`
	Kind                   string                              `json:"kind"`
	RunID                  string                              `json:"run_id"`
	Generation             string                              `json:"generation"`
	RootProofID            string                              `json:"root_proof_id"`
	Release                platformRelaySecretIsolationRelease `json:"release"`
	DatabaseEndpointSHA256 string                              `json:"database_endpoint_sha256"`
	Database               model.RelayDatabaseReleaseIdentity  `json:"database"`
}

var (
	inspectRelayDatabaseReleaseIdentity              = model.InspectRelayDatabaseReleaseIdentity
	verifyRelayDatabaseReleaseIdentity               = model.VerifyRelayDatabaseReleaseIdentity
	requireRelaySchemaCurrentForDatabaseReleaseProof = model.RequireRelaySchemaCurrent
)

// PlatformRelayDatabaseReleaseProofWriter pins the proof directory before the
// role predecessor opens PostgreSQL. The stale proof is removed immediately;
// only a complete role/surface transaction may publish its replacement.
type PlatformRelayDatabaseReleaseProofWriter struct {
	directory *platformRelaySecretIsolationReceiptDirectory
	closed    bool
}

func platformRelayDatabaseReleaseProofDirectoryAliasesFileParent(
	directoryInfo os.FileInfo,
	environment string,
) (bool, error) {
	path := os.Getenv(environment)
	if path == "" || path != strings.TrimSpace(path) || !filepath.IsAbs(path) || filepath.Clean(path) != path {
		return false, errors.New("Relay database release proof source path is invalid")
	}
	parent := filepath.Dir(path)
	before, err := os.Lstat(parent)
	if err != nil || before.Mode()&os.ModeSymlink != 0 || !before.IsDir() {
		return false, errors.New("Relay database release proof source directory is invalid")
	}
	handle, err := os.Open(parent)
	if err != nil {
		return false, errors.New("Relay database release proof source directory is invalid")
	}
	defer handle.Close()
	after, err := handle.Stat()
	if err != nil || !after.IsDir() || !os.SameFile(before, after) {
		return false, errors.New("Relay database release proof source directory changed while opening")
	}
	return os.SameFile(directoryInfo, after), nil
}

// PreparePlatformRelayDatabaseReleaseProof invalidates an earlier proof before
// any database access and keeps the opened directory inode pinned until commit.
func PreparePlatformRelayDatabaseReleaseProof() (*PlatformRelayDatabaseReleaseProofWriter, error) {
	directoryPath := os.Getenv(RelayDatabaseReleaseProofDirectoryEnvironment)
	if directoryPath == "" || directoryPath != strings.TrimSpace(directoryPath) ||
		!filepath.IsAbs(directoryPath) || filepath.Clean(directoryPath) != directoryPath {
		return nil, errors.New("Relay database release proof directory is invalid")
	}
	directory, err := platformRelaySecretIsolationOpenReceiptDirectory(directoryPath)
	if err != nil {
		return nil, errors.New("Relay database release proof directory is invalid")
	}
	fail := func(err error) (*PlatformRelayDatabaseReleaseProofWriter, error) {
		_ = directory.close()
		return nil, err
	}
	proofPath := os.Getenv(RelayDatabaseReleaseProofFileEnvironment)
	proofAlias, proofAliasErr := platformRelayDatabaseReleaseProofDirectoryAliasesFileParent(
		directory.info,
		RelayDatabaseReleaseProofFileEnvironment,
	)
	if proofAliasErr != nil || !proofAlias || filepath.Base(proofPath) != "receipt.json" {
		return fail(errors.New("Relay database release proof read and write mounts are not the same directory"))
	}
	for _, environment := range []string{
		platformRelaySecretIsolationReceiptFileEnvironment,
		platformRelaySecretIsolationCommitFileEnvironment,
	} {
		aliased, aliasErr := platformRelayDatabaseReleaseProofDirectoryAliasesFileParent(directory.info, environment)
		if aliasErr != nil {
			return fail(aliasErr)
		}
		if aliased {
			return fail(errors.New("Relay database release proof directory is not isolated"))
		}
	}
	if err := directory.remove("receipt.json"); err != nil && !errors.Is(err, os.ErrNotExist) {
		return fail(errors.New("Relay database release proof could not be revoked"))
	}
	if err := directory.sync(); err != nil {
		return fail(errors.New("Relay database release proof directory could not be synchronized"))
	}
	return &PlatformRelayDatabaseReleaseProofWriter{directory: directory}, nil
}

func (writer *PlatformRelayDatabaseReleaseProofWriter) Close() error {
	if writer == nil || writer.closed {
		return nil
	}
	writer.closed = true
	return writer.directory.close()
}

func platformRelayDatabaseReleaseEndpointDigest(
	receipt platformRelaySecretIsolationReceipt,
	consumer string,
) (string, error) {
	id := ""
	switch consumer {
	case PlatformRelaySecretIsolationConsumerPre,
		PlatformRelaySecretIsolationConsumerPost:
		id = "database.role_admin_dsn.endpoint"
	case PlatformRelaySecretIsolationConsumerMigrate:
		id = "database.migration_dsn.endpoint"
	case PlatformRelaySecretIsolationConsumerPrincipal,
		PlatformRelaySecretIsolationConsumerAPI,
		PlatformRelaySecretIsolationConsumerRootBootstrap,
		PlatformRelaySecretIsolationConsumerPrincipalRotation:
		id = "database.runtime_dsn.endpoint"
	case PlatformRelaySecretIsolationConsumerEdge:
		id = "database.edge_dsn.endpoint"
	default:
		return "", errors.New("Relay database release proof consumer is invalid")
	}
	digest := ""
	for _, commitment := range receipt.Semantics {
		if commitment.ID != id {
			continue
		}
		if digest != "" {
			return "", errors.New("Relay database release endpoint commitment is ambiguous")
		}
		digest = commitment.SHA256
	}
	decoded, err := hex.DecodeString(digest)
	if err != nil || len(decoded) != sha256.Size || digest != strings.ToLower(digest) {
		clear(decoded)
		return "", errors.New("Relay database release endpoint commitment is invalid")
	}
	clear(decoded)
	return digest, nil
}

// Commit publishes a proof only after the privileged role predecessor has
// completed all of its database postconditions on this same connection.
func (writer *PlatformRelayDatabaseReleaseProofWriter) Commit(db *gorm.DB) error {
	if writer == nil || writer.closed || writer.directory == nil {
		return errors.New("Relay database release proof writer is invalid")
	}
	verified, err := platformRelaySecretIsolationCurrentVerifiedContext(
		PlatformRelaySecretIsolationConsumerPre,
	)
	if err != nil {
		return err
	}
	endpointDigest, err := platformRelayDatabaseReleaseEndpointDigest(
		verified.receipt,
		PlatformRelaySecretIsolationConsumerPre,
	)
	if err != nil {
		return err
	}
	database, err := inspectRelayDatabaseReleaseIdentity(db)
	if err != nil {
		return err
	}
	proof := relayDatabaseReleaseProof{
		SchemaVersion:          relayDatabaseReleaseProofSchemaVersion,
		Kind:                   relayDatabaseReleaseProofKind,
		RunID:                  verified.marker.RunID,
		Generation:             verified.marker.Generation,
		RootProofID:            verified.marker.RootProofID,
		Release:                verified.marker.Release,
		DatabaseEndpointSHA256: endpointDigest,
		Database:               database,
	}
	if err := writer.directory.writeReceipt(proof); err != nil {
		return errors.New("Relay database release proof could not be committed")
	}
	return nil
}

func platformRelayDatabaseReleaseParseProof(raw []byte) (relayDatabaseReleaseProof, error) {
	var proof relayDatabaseReleaseProof
	if len(raw) == 0 || common.RejectDuplicateJSONKeys(raw) != nil {
		return proof, errors.New("Relay database release proof is invalid")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&proof) != nil {
		return relayDatabaseReleaseProof{}, errors.New("Relay database release proof is invalid")
	}
	var trailing any
	if decoder.Decode(&trailing) != io.EOF ||
		proof.SchemaVersion != relayDatabaseReleaseProofSchemaVersion ||
		proof.Kind != relayDatabaseReleaseProofKind ||
		!platformRelaySecretIsolationRunIDPattern.MatchString(proof.RunID) ||
		!platformRelaySecretIsolationConsumerAcceptsGeneration(
			PlatformRelaySecretIsolationConsumerPre,
			proof.Generation,
			proof.RootProofID,
		) {
		return relayDatabaseReleaseProof{}, errors.New("Relay database release proof is invalid")
	}
	decoded, err := hex.DecodeString(proof.DatabaseEndpointSHA256)
	if err != nil || len(decoded) != sha256.Size || proof.DatabaseEndpointSHA256 != strings.ToLower(proof.DatabaseEndpointSHA256) {
		clear(decoded)
		return relayDatabaseReleaseProof{}, errors.New("Relay database release proof is invalid")
	}
	clear(decoded)
	canonical, err := json.Marshal(proof)
	if err != nil || len(canonical) != len(raw) || subtle.ConstantTimeCompare(canonical, raw) != 1 {
		clear(canonical)
		return relayDatabaseReleaseProof{}, errors.New("Relay database release proof is not canonical")
	}
	clear(canonical)
	return proof, nil
}

func platformRelayDatabaseReleaseReadProof() (relayDatabaseReleaseProof, error) {
	raw, err := platformRelaySecretIsolationReadFile(
		RelayDatabaseReleaseProofFileEnvironment,
		relayDatabaseReleaseProofMaximumBytes,
	)
	if err != nil {
		return relayDatabaseReleaseProof{}, errors.New("Relay database release proof is unavailable")
	}
	defer clear(raw)
	return platformRelayDatabaseReleaseParseProof(raw)
}

// VerifyPlatformRelayDatabaseReleaseProof binds a normal consumer to the same
// global isolation run and endpoint used by the role predecessor, then checks
// the proof against this consumer's live database connection.
func VerifyPlatformRelayDatabaseReleaseProof(db *gorm.DB, consumer string) error {
	verified, err := platformRelaySecretIsolationCurrentVerifiedContext(consumer)
	if err != nil {
		return err
	}
	proof, err := platformRelayDatabaseReleaseReadProof()
	if err != nil {
		return err
	}
	endpointDigest, err := platformRelayDatabaseReleaseEndpointDigest(verified.receipt, consumer)
	if err != nil {
		return err
	}
	if proof.RunID != verified.marker.RunID ||
		proof.Generation != verified.marker.Generation ||
		proof.RootProofID != verified.marker.RootProofID ||
		proof.Release != verified.marker.Release ||
		proof.DatabaseEndpointSHA256 != endpointDigest {
		return errors.New("Relay database release proof is not bound to this consumer")
	}
	switch consumer {
	case PlatformRelaySecretIsolationConsumerPost,
		PlatformRelaySecretIsolationConsumerPrincipal,
		PlatformRelaySecretIsolationConsumerAPI,
		PlatformRelaySecretIsolationConsumerRootBootstrap,
		PlatformRelaySecretIsolationConsumerEdge:
		if _, err := requireRelaySchemaCurrentForDatabaseReleaseProof(db); err != nil {
			return errors.New("Relay database release proof requires the current schema")
		}
	}
	return verifyRelayDatabaseReleaseIdentity(db, proof.Database)
}

// VerifyPlatformRelayRootDatabaseReleaseProof is the one install-only exception:
// the root validator has already revoked the pre-root global marker before this
// command runs. Its receipt still commits the runtime endpoint and current
// release, so it may consume the predecessor's pre-root proof exactly once.
func VerifyPlatformRelayRootDatabaseReleaseProof(db *gorm.DB) error {
	release, err := platformRelaySecretIsolationReleaseIdentity()
	if err != nil {
		return err
	}
	receiptRaw, err := platformRelaySecretIsolationReadFile(
		platformRelaySecretIsolationReceiptFileEnvironment,
		platformRelaySecretIsolationReceiptMaxBytes,
	)
	if err != nil {
		return errors.New("Relay root database release receipt is unavailable")
	}
	defer clear(receiptRaw)
	receipt, err := platformRelaySecretIsolationParseReceipt(receiptRaw)
	if err != nil || receipt.Consumer != PlatformRelaySecretIsolationConsumerRootBootstrap || receipt.Release != release {
		return errors.New("Relay root database release receipt is invalid")
	}
	endpointDigest, err := platformRelayDatabaseReleaseEndpointDigest(
		receipt,
		PlatformRelaySecretIsolationConsumerRootBootstrap,
	)
	if err != nil {
		return err
	}
	proof, err := platformRelayDatabaseReleaseReadProof()
	if err != nil {
		return err
	}
	if proof.Generation != platformRelaySecretIsolationGenerationPreRoot || proof.RootProofID != "" ||
		proof.Release != release || proof.DatabaseEndpointSHA256 != endpointDigest {
		return errors.New("Relay root database release proof is not bound to this install")
	}
	if _, err := requireRelaySchemaCurrentForDatabaseReleaseProof(db); err != nil {
		return errors.New("Relay root database release proof requires the current schema")
	}
	return verifyRelayDatabaseReleaseIdentity(db, proof.Database)
}

// VerifyPlatformRelayPrincipalRotationDatabaseReleaseProof binds the
// independently generated rotation receipt to the current post-root database
// release proof. Rotation has its own current/desired secret-isolation run, so
// its run ID deliberately differs from the ordinary rollout marker. The
// release, physical endpoint, permanent root generation, and live server
// identity must still match before any principal row can change.
//
// The caller must first verify the rotation isolation receipt. That verifier
// pins the exact receipt and database source bytes used below before InitDB.
func VerifyPlatformRelayPrincipalRotationDatabaseReleaseProof(db *gorm.DB, attemptID string) error {
	if !platformRelaySecretIsolationRunIDPattern.MatchString(attemptID) {
		return errors.New("Relay principal rotation database release attempt is invalid")
	}
	release, err := platformRelaySecretIsolationReleaseIdentity()
	if err != nil {
		return err
	}
	receiptRaw, err := platformRelaySecretIsolationReadFile(
		platformRelaySecretIsolationReceiptFileEnvironment,
		platformRelaySecretIsolationReceiptMaxBytes,
	)
	if err != nil {
		return errors.New("Relay principal rotation database release receipt is unavailable")
	}
	defer clear(receiptRaw)
	receipt, err := platformRelaySecretIsolationParseReceipt(receiptRaw)
	if err != nil || receipt.Consumer != PlatformRelaySecretIsolationConsumerPrincipalRotation ||
		receipt.RunID != attemptID || receipt.Release != release {
		return errors.New("Relay principal rotation database release receipt is invalid")
	}
	canonicalReceipt, err := platformRelaySecretIsolationCanonicalReceipt(receipt)
	if err != nil || len(canonicalReceipt) != len(receiptRaw) ||
		subtle.ConstantTimeCompare(canonicalReceipt, receiptRaw) != 1 {
		clear(canonicalReceipt)
		return errors.New("Relay principal rotation database release receipt is not canonical")
	}
	clear(canonicalReceipt)
	endpointDigest, err := platformRelayDatabaseReleaseEndpointDigest(
		receipt,
		PlatformRelaySecretIsolationConsumerPrincipalRotation,
	)
	if err != nil {
		return err
	}
	proof, err := platformRelayDatabaseReleaseReadProof()
	if err != nil {
		return err
	}
	if proof.Generation != platformRelaySecretIsolationGenerationRootProofPresent ||
		!platformRelaySecretIsolationRunIDPattern.MatchString(proof.RootProofID) ||
		proof.Release != release || proof.DatabaseEndpointSHA256 != endpointDigest {
		return errors.New("Relay principal rotation database release proof is not bound to this rotation")
	}
	if _, err := requireRelaySchemaCurrentForDatabaseReleaseProof(db); err != nil {
		return errors.New("Relay principal rotation database release proof requires the current schema")
	}
	return verifyRelayDatabaseReleaseIdentity(db, proof.Database)
}
