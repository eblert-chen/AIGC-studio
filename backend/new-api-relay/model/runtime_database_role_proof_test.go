package model

import (
	"database/sql"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func relayRuntimeProofTestStatus() RelaySchemaStatus {
	status := relayDownloadEdgeProofTestStatus()
	status.PendingVersion = status.CurrentVersion
	return status
}

func TestRelayRuntimeDatabaseRoleProofSameReleaseCoversWholeCandidateTuple(t *testing.T) {
	pool := &sql.DB{}
	verifiedAt := time.Date(2026, time.August, 28, 12, 0, 0, 0, time.UTC)
	base := RelayRuntimeDatabaseRoleProof{
		pool: pool, role: "relay_runtime", edgeRole: relayDownloadEdgeDatabaseRoleName,
		schemaStatus: relayRuntimeProofTestStatus(), verifiedAt: verifiedAt,
	}
	require.True(t, base.SameRelease(base))
	require.Equal(t, verifiedAt, base.VerifiedAt())

	tests := []struct {
		name   string
		mutate func(*RelaySchemaStatus)
	}{
		{name: "attempt", mutate: func(status *RelaySchemaStatus) { status.AttemptID = "stale-attempt" }},
		{name: "classification", mutate: func(status *RelaySchemaStatus) { status.Classification = RelaySchemaStatusDirty }},
		{name: "state", mutate: func(status *RelaySchemaStatus) { status.State = RelaySchemaStateFailed }},
		{name: "dirty", mutate: func(status *RelaySchemaStatus) { status.Dirty = true }},
		{name: "baseline", mutate: func(status *RelaySchemaStatus) { status.BaselineVersion++ }},
		{name: "current", mutate: func(status *RelaySchemaStatus) { status.CurrentVersion++ }},
		{name: "target", mutate: func(status *RelaySchemaStatus) { status.TargetVersion++ }},
		{name: "pending", mutate: func(status *RelaySchemaStatus) { status.PendingVersion++ }},
		{name: "minimum", mutate: func(status *RelaySchemaStatus) { status.MinVersion++ }},
		{name: "maximum", mutate: func(status *RelaySchemaStatus) { status.MaxVersion++ }},
		{name: "current checksum", mutate: func(status *RelaySchemaStatus) { status.CurrentChecksum = "sha256:" + strings.Repeat("c", 64) }},
		{name: "expected checksum", mutate: func(status *RelaySchemaStatus) { status.ExpectedChecksum = "sha256:" + strings.Repeat("d", 64) }},
		{name: "target checksum", mutate: func(status *RelaySchemaStatus) { status.TargetChecksum = "sha256:" + strings.Repeat("e", 64) }},
		{name: "catalog", mutate: func(status *RelaySchemaStatus) { status.CatalogSHA256 = "sha256:" + strings.Repeat("f", 64) }},
		{name: "expected catalog", mutate: func(status *RelaySchemaStatus) { status.ExpectedCatalogSHA256 = "sha256:" + strings.Repeat("1", 64) }},
		{name: "candidate revision", mutate: func(status *RelaySchemaStatus) { status.SourceRevision = strings.Repeat("2", 40) }},
		{name: "candidate snapshot", mutate: func(status *RelaySchemaStatus) { status.SnapshotSHA256 = "sha256:" + strings.Repeat("3", 64) }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			candidate := base
			test.mutate(&candidate.schemaStatus)
			require.False(t, base.SameRelease(candidate))
		})
	}

	otherPool := base
	otherPool.pool = &sql.DB{}
	require.False(t, base.SameRelease(otherPool))
	otherRole := base
	otherRole.role = relayDownloadEdgeDatabaseRoleName
	require.False(t, base.SameRelease(otherRole))
	otherEdgeRole := base
	otherEdgeRole.edgeRole = "relay_download_edge_drift"
	require.False(t, base.SameRelease(otherEdgeRole))
}
