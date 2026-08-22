package main

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestPlatformGenerationWorkerStartupFailureClosesServer(t *testing.T) {
	startupErr := errors.New("redis disappeared")
	shutdownCalled := false
	err := startPlatformGenerationWorkersFailClosed(
		func() error { return startupErr },
		func(ctx context.Context) error {
			shutdownCalled = true
			assert.NoError(t, ctx.Err())
			return nil
		},
		time.Second,
	)
	assert.ErrorIs(t, err, startupErr)
	assert.True(t, shutdownCalled)
}

func TestPlatformGenerationWorkerStartupSuccessKeepsServerOpen(t *testing.T) {
	shutdownCalled := false
	require.NoError(t, startPlatformGenerationWorkersFailClosed(
		func() error { return nil },
		func(context.Context) error {
			shutdownCalled = true
			return nil
		},
		time.Second,
	))
	assert.False(t, shutdownCalled)
}

func TestProtectedBatchUpdateLifecycleRequiresExactFalse(t *testing.T) {
	for _, value := range []string{"", "true", " false", "false ", "FALSE"} {
		t.Run(value, func(t *testing.T) {
			t.Setenv("BATCH_UPDATE_ENABLED", value)
			require.Error(t, validatePlatformRelayBatchUpdateLifecycle(true))
		})
	}
	t.Setenv("BATCH_UPDATE_ENABLED", "false")
	require.NoError(t, validatePlatformRelayBatchUpdateLifecycle(true))

	// Development may retain the legacy batch updater while protected
	// staging/production use synchronous accounting updates.
	t.Setenv("BATCH_UPDATE_ENABLED", "true")
	require.NoError(t, validatePlatformRelayBatchUpdateLifecycle(false))
}

func TestProtectedNativeCompatibilityLifecycleRequiresExactFalse(t *testing.T) {
	for _, value := range []string{"", "true", " false", "false ", "FALSE"} {
		t.Run(value, func(t *testing.T) {
			t.Setenv("RELAY_NATIVE_PAID_COMPAT_ENABLED", value)
			require.Error(t, validatePlatformRelayNativeCompatibilityLifecycle(true))
		})
	}
	t.Setenv("RELAY_NATIVE_PAID_COMPAT_ENABLED", "false")
	require.NoError(t, validatePlatformRelayNativeCompatibilityLifecycle(true))
	t.Setenv("RELAY_NATIVE_PAID_COMPAT_ENABLED", "true")
	require.NoError(t, validatePlatformRelayNativeCompatibilityLifecycle(false))
}
