package service

import (
	"testing"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/require"
)

func TestPlatformGenerationIdempotencyIgnoresRelayOwnedMetadata(t *testing.T) {
	request := dto.NewPlatformGenerationRequest()
	request.Model = "model-a"
	request.ExpectedCapabilityRevision = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	request.Mode = "text_to_video"
	request.Inputs.Prompt = "hello"
	request.Metadata = map[string]any{"customer_trace": "trace-1"}

	first, firstHash, err := platformGenerationRequestForIdempotency(request)
	require.NoError(t, err)
	require.Equal(t, map[string]any{"customer_trace": "trace-1"}, first.Metadata)

	request.Metadata["relay_request_id"] = "request-two"
	request.Metadata["relay_capability_revision"] = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	request.Metadata[generationprofile.MetadataProfileID] = "new-profile"
	request.Metadata[generationprofile.MetadataProfileRevision] = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
	request.Metadata[generationprofile.MetadataProfileSnapshot] = `{"id":"new-profile"}`
	second, secondHash, err := platformGenerationRequestForIdempotency(request)
	require.NoError(t, err)
	require.Equal(t, first.Metadata, second.Metadata)
	require.Equal(t, firstHash, secondHash)
}
