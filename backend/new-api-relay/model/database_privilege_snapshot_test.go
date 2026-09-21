package model

import (
	"testing"

	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

type relayPinnedConnectionStateProbe struct {
	ID int64 `gorm:"primaryKey"`
}

type relayPinnedConnectionNamedResult struct {
	Value int64 `gorm:"column:value"`
}

func TestRelayPinnedConnectionSessionKeepsConnectionAndClearsStatementState(t *testing.T) {
	database, err := gorm.Open(
		sqlite.Open("file:relay-pinned-connection-session?mode=memory&cache=shared"),
		&gorm.Config{},
	)
	require.NoError(t, err)
	require.NoError(t, database.AutoMigrate(&relayPinnedConnectionStateProbe{}))

	require.NoError(t, database.Connection(func(connection *gorm.DB) error {
		var namedRows []relayPinnedConnectionNamedResult
		require.NoError(t, connection.Raw(`SELECT 1 AS value`).Scan(&namedRows).Error)
		require.NotEmpty(t, connection.Statement.Table)
		require.False(t, connection.Migrator().HasTable(&relayPinnedConnectionStateProbe{}),
			"the raw Connection callback must reproduce GORM's stale-table failure")

		isolated := relayPinnedConnectionSession(connection)
		require.True(t, isolated.Statement.ConnPool == connection.Statement.ConnPool,
			"statement isolation must retain the dedicated physical connection")
		require.True(t, isolated.Migrator().HasTable(&relayPinnedConnectionStateProbe{}))

		var count int64
		require.NoError(t, isolated.Model(&relayPinnedConnectionStateProbe{}).Count(&count).Error)
		require.Zero(t, count)
		return nil
	}))
}

func TestRelayEffectiveApplicationPrivilegeSnapshotRejectsDuplicateCatalogRows(t *testing.T) {
	_, err := relayEffectiveApplicationPrivilegeSnapshotFromRows([]relayEffectivePrivilegeSnapshotRow{
		{Kind: "table", Table: "jobs"},
		{Kind: "table", Table: "jobs"},
	})
	require.ErrorContains(t, err, "duplicate")

	_, err = relayEffectiveApplicationPrivilegeSnapshotFromRows([]relayEffectivePrivilegeSnapshotRow{
		{Kind: "table", Table: "jobs"},
		{Kind: "column", Table: "jobs", Column: "state"},
		{Kind: "column", Table: "jobs", Column: "state"},
	})
	require.ErrorContains(t, err, "duplicate")
}

func TestRelayDownloadEdgeEffectivePrivilegeSnapshotIsExact(t *testing.T) {
	manifest := relayDownloadEdgePrivilegeManifest{
		Tables: map[string]relayTablePrivilegeSet{
			"jobs": {Select: true, Insert: true},
		},
		UpdateColumns: map[string]map[string]bool{
			"jobs": {"state": true},
		},
	}
	baseline := func() relayEffectivePrivilegeSnapshot {
		return relayEffectivePrivilegeSnapshot{
			Tables: map[string]relayEffectivePrivilegeSnapshotRow{
				"jobs": {Kind: "table", Table: "jobs", Select: true, Insert: true},
			},
			Columns: map[string]map[string]relayEffectivePrivilegeSnapshotRow{
				"jobs": {
					"id":    {Kind: "column", Table: "jobs", Column: "id", Select: true, Insert: true},
					"state": {Kind: "column", Table: "jobs", Column: "state", Select: true, Insert: true, Update: true},
				},
			},
		}
	}

	require.NoError(t, verifyRelayDownloadEdgeEffectivePrivilegeSnapshot(baseline(), manifest))

	forbiddenTable := baseline()
	row := forbiddenTable.Tables["jobs"]
	row.Trigger = true
	forbiddenTable.Tables["jobs"] = row
	require.Error(t, verifyRelayDownloadEdgeEffectivePrivilegeSnapshot(forbiddenTable, manifest))

	forbiddenColumn := baseline()
	column := forbiddenColumn.Columns["jobs"]["id"]
	column.References = true
	forbiddenColumn.Columns["jobs"]["id"] = column
	require.Error(t, verifyRelayDownloadEdgeEffectivePrivilegeSnapshot(forbiddenColumn, manifest))

	missingUpdateColumn := baseline()
	delete(missingUpdateColumn.Columns["jobs"], "state")
	require.Error(t, verifyRelayDownloadEdgeEffectivePrivilegeSnapshot(missingUpdateColumn, manifest))

	unexpectedUpdate := baseline()
	column = unexpectedUpdate.Columns["jobs"]["id"]
	column.Update = true
	unexpectedUpdate.Columns["jobs"]["id"] = column
	require.Error(t, verifyRelayDownloadEdgeEffectivePrivilegeSnapshot(unexpectedUpdate, manifest))
}
