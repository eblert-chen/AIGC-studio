package service

import (
	"context"
	"testing"

	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/stretchr/testify/require"
)

func TestPlatformRouteTestArtifactVerifierEnforcesSeedreamDimensions(t *testing.T) {
	contract := generationprofile.ArtifactContract{
		MediaType: "image", ContentType: "image/png", Width: 2048, Height: 2048, Count: 1,
	}
	verify := func(payload []byte) (string, error) {
		evidence, err := verifyPlatformRouteTestArtifactForContract(
			context.Background(),
			"https://provider.example/seedream.png",
			contract,
			func(_ context.Context, _ string, expectation PlatformArtifactDownloadExpectation) (*PlatformDownloadedArtifact, error) {
				require.Equal(t, "image/png", expectation.ExpectedContentType)
				require.Equal(t, 2048, expectation.ExpectedImageWidth)
				require.Equal(t, 2048, expectation.ExpectedImageHeight)
				return artifactTestDownloadPayloadWithExpectation(t, payload, "image/png", expectation)
			},
		)
		return evidence.ContentType, err
	}

	contentType, err := verify(platformArtifactPNGDimensionsFixture(t, 2048, 2048))
	require.NoError(t, err)
	require.Equal(t, "image/png", contentType)

	contentType, err = verify(platformArtifactPNGDimensionsFixture(t, 1, 1))
	require.ErrorIs(t, err, ErrPlatformArtifactIntegrity)
	require.Empty(t, contentType)
}

func TestPlatformRouteTestArtifactVerifierKeepsVideoMIMEContract(t *testing.T) {
	contract := generationprofile.ArtifactContract{
		MediaType: "video", ContentType: "video/mp4", Count: 1,
	}
	evidence, err := verifyPlatformRouteTestArtifactForContract(
		context.Background(),
		"https://provider.example/seedance.mp4",
		contract,
		func(_ context.Context, _ string, expectation PlatformArtifactDownloadExpectation) (*PlatformDownloadedArtifact, error) {
			require.Equal(t, "video/mp4", expectation.ExpectedContentType)
			require.Zero(t, expectation.ExpectedImageWidth)
			require.Zero(t, expectation.ExpectedImageHeight)
			return artifactTestDownloadPayloadWithExpectation(
				t, platformArtifactValidMP4Fixture(t), "video/mp4", expectation,
			)
		},
	)
	require.NoError(t, err)
	require.Equal(t, "video/mp4", evidence.ContentType)
}
