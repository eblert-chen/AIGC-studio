package model

import (
	"context"
	"sync/atomic"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/stretchr/testify/require"
)

func TestRelayRuntimeDatabaseResetSessionDoesNotRepeatFullReleaseIdentity(t *testing.T) {
	previousFence := verifyRelayRuntimeDatabaseSessionFence
	previousIdentity := verifyRelayInstalledDatabaseReleaseIdentitySession
	var fences atomic.Int32
	var fullIdentities atomic.Int32
	verifyRelayRuntimeDatabaseSessionFence = func(context.Context, *pgx.Conn, string) error {
		fences.Add(1)
		return nil
	}
	verifyRelayInstalledDatabaseReleaseIdentitySession = func(context.Context, *pgx.Conn) error {
		fullIdentities.Add(1)
		return nil
	}
	t.Cleanup(func() {
		verifyRelayRuntimeDatabaseSessionFence = previousFence
		verifyRelayInstalledDatabaseReleaseIdentitySession = previousIdentity
	})

	for range 10 {
		require.NoError(t, verifyRelayRuntimeDatabaseResetSession(context.Background(), nil, "relay_download_edge"))
	}
	require.Equal(t, int32(10), fences.Load())
	require.Zero(t, fullIdentities.Load())

	require.NoError(t, verifyRelayRuntimeDatabaseConnectionSession(context.Background(), nil, "relay_download_edge"))
	require.Equal(t, int32(11), fences.Load())
	require.Equal(t, int32(1), fullIdentities.Load())
}
