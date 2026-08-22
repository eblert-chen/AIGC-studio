package main

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

func TestPlatformRelayOfflineBuildIdentityCommandDoesNotStartTheService(t *testing.T) {
	var output bytes.Buffer
	handled, err := handlePlatformRelayOfflineCommand([]string{"/new-api", "relay-build-identity"}, &output)
	require.NoError(t, err)
	require.True(t, handled)
	var identity service.PlatformRelayCompiledBuildIdentity
	require.NoError(t, json.Unmarshal(output.Bytes(), &identity))
	require.Equal(t, 1, identity.SchemaVersion)
	require.Equal(t, "relay_compiled_build_identity", identity.Kind)

	output.Reset()
	handled, err = handlePlatformRelayOfflineCommand([]string{"/new-api"}, &output)
	require.NoError(t, err)
	require.False(t, handled)
	require.Empty(t, output.String())
}
