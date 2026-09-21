package model

import (
	"database/sql"
	"strings"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func relayDownloadEdgeProofTestStatus() RelaySchemaStatus {
	contract := relaySchemaContractForRuntime()
	version := contract.TargetVersion
	checksum := contract.Checksums[version]
	catalog := relaySchemaExpectedCatalogForRuntime("postgres", version)
	return RelaySchemaStatus{
		Classification:        RelaySchemaStatusCurrent,
		BaselineVersion:       version,
		CurrentVersion:        version,
		TargetVersion:         version,
		MinVersion:            contract.MinVersion,
		MaxVersion:            contract.MaxVersion,
		State:                 RelaySchemaStateClean,
		AttemptID:             "12345678-1234-4234-9234-123456789abc",
		CurrentChecksum:       checksum,
		ExpectedChecksum:      checksum,
		TargetChecksum:        checksum,
		CatalogSHA256:         catalog,
		ExpectedCatalogSHA256: catalog,
		SourceRevision:        strings.Repeat("a", 40),
		SnapshotSHA256:        "sha256:" + strings.Repeat("b", 64),
		Compatible:            true,
		Current:               true,
	}
}

func TestRelayDownloadEdgeDatabaseRoleProofSameReleaseCoversWholeCandidateTuple(t *testing.T) {
	pool := &sql.DB{}
	base := RelayDownloadEdgeDatabaseRoleProof{
		pool: pool, role: relayDownloadEdgeDatabaseRoleName,
		schemaStatus: relayDownloadEdgeProofTestStatus(), verifiedAt: time.Now(),
	}
	require.True(t, base.SameRelease(base))

	tests := []struct {
		name   string
		mutate func(*RelaySchemaStatus)
	}{
		{name: "attempt", mutate: func(status *RelaySchemaStatus) { status.AttemptID = "stale-attempt" }},
		{name: "classification", mutate: func(status *RelaySchemaStatus) { status.Classification = RelaySchemaStatusDirty }},
		{name: "state", mutate: func(status *RelaySchemaStatus) { status.State = RelaySchemaStateFailed }},
		{name: "dirty", mutate: func(status *RelaySchemaStatus) { status.Dirty = true }},
		{name: "minimum", mutate: func(status *RelaySchemaStatus) { status.MinVersion++ }},
		{name: "maximum", mutate: func(status *RelaySchemaStatus) { status.MaxVersion++ }},
		{name: "expected checksum", mutate: func(status *RelaySchemaStatus) { status.ExpectedChecksum = "sha256:" + strings.Repeat("c", 64) }},
		{name: "expected catalog", mutate: func(status *RelaySchemaStatus) { status.ExpectedCatalogSHA256 = "sha256:" + strings.Repeat("d", 64) }},
		{name: "candidate revision", mutate: func(status *RelaySchemaStatus) { status.SourceRevision = strings.Repeat("e", 40) }},
		{name: "candidate snapshot", mutate: func(status *RelaySchemaStatus) { status.SnapshotSHA256 = "sha256:" + strings.Repeat("f", 64) }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			candidate := base
			candidate.schemaStatus = base.schemaStatus
			test.mutate(&candidate.schemaStatus)
			require.False(t, base.SameRelease(candidate))
		})
	}

	otherPool := base
	otherPool.pool = &sql.DB{}
	require.False(t, base.SameRelease(otherPool))
	otherRole := base
	otherRole.role = "relay_runtime"
	require.False(t, base.SameRelease(otherRole))
}

func TestVerifyRelayDownloadEdgeSchemaReleaseProofRejectsStateAndLedgerDrift(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:download-edge-release-proof?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	require.NoError(t, db.AutoMigrate(&RelaySchemaState{}, &RelaySchemaMigration{}))
	status := relayDownloadEdgeProofTestStatus()
	definitionByVersion := make(map[int64]relaySchemaMigrationDefinition)
	for _, definition := range relaySchemaDefinitionsForRuntime() {
		definitionByVersion[definition.Version] = definition
	}
	definition := definitionByVersion[status.CurrentVersion]
	require.NotEmpty(t, definition.Name)
	require.NoError(t, db.Create(&RelaySchemaState{
		ID: relaySchemaStateSingletonID, BaselineVersion: status.BaselineVersion,
		CurrentVersion: status.CurrentVersion, TargetVersion: status.CurrentVersion,
		State: RelaySchemaStateClean, AttemptID: status.AttemptID, CurrentChecksum: status.CurrentChecksum,
		TargetChecksum: status.CurrentChecksum, CurrentCatalogSHA256: status.CatalogSHA256,
		TargetCatalogSHA256: status.CatalogSHA256, SourceRevision: status.SourceRevision,
		SnapshotSHA256: status.SnapshotSHA256, UpdatedAt: time.Now().UTC(),
	}).Error)
	require.NoError(t, db.Create(&RelaySchemaMigration{
		Version: status.CurrentVersion, Name: definition.Name, Phase: definition.Phase,
		Checksum: definition.Checksum, CatalogSHA256: status.CatalogSHA256,
		AppliedAt: time.Now().UTC(), SourceRevision: status.SourceRevision,
		SnapshotSHA256: status.SnapshotSHA256,
	}).Error)

	require.NoError(t, verifyRelayDownloadEdgeSchemaReleaseProof(db, status))
	require.NoError(t, db.Model(&RelaySchemaState{}).Where("id = ?", relaySchemaStateSingletonID).
		Update("attempt_id", "candidate-attempt-drift").Error)
	require.ErrorContains(t, verifyRelayDownloadEdgeSchemaReleaseProof(db, status), "changed after startup")
	require.NoError(t, db.Model(&RelaySchemaState{}).Where("id = ?", relaySchemaStateSingletonID).
		Update("attempt_id", status.AttemptID).Error)
	require.NoError(t, db.Model(&RelaySchemaMigration{}).Where("version = ?", status.CurrentVersion).
		Update("source_revision", strings.Repeat("c", 40)).Error)
	require.ErrorContains(t, verifyRelayDownloadEdgeSchemaReleaseProof(db, status), "changed after startup")
}
