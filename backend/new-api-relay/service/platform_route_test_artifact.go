package service

import (
	"context"
	"fmt"
	"strings"

	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
)

type platformRouteTestArtifactDownloadFunc func(
	context.Context,
	string,
	PlatformArtifactDownloadExpectation,
) (*PlatformDownloadedArtifact, error)

// VerifyPlatformRouteTestArtifact downloads the provider URL through the same
// SSRF-safe, size-bounded, redirect-denying, media-validating path used by
// production artifact transfer. The temporary file is synchronously closed and
// removed before any evidence is returned. The signed URL is never persisted.
func VerifyPlatformRouteTestArtifact(
	ctx context.Context,
	providerURL string,
) (model.PlatformChannelTestArtifactEvidence, error) {
	return VerifyPlatformRouteTestArtifactForContract(ctx, providerURL, generationprofile.ArtifactContract{
		MediaType: "video", ContentType: "video/mp4", Count: 1,
	})
}

// VerifyPlatformRouteTestArtifactForContract verifies the complete immutable
// artifact contract selected by the pinned adapter profile. For PNG images the
// controlled downloader must decode and prove the exact profile dimensions;
// MIME alone is never sufficient acceptance evidence.
func VerifyPlatformRouteTestArtifactForContract(
	ctx context.Context,
	providerURL string,
	contract generationprofile.ArtifactContract,
) (model.PlatformChannelTestArtifactEvidence, error) {
	downloader, err := NewPlatformArtifactDownloaderFromEnvironment()
	if err != nil {
		return model.PlatformChannelTestArtifactEvidence{}, err
	}
	return verifyPlatformRouteTestArtifactForContract(ctx, providerURL, contract, downloader.Download)
}

// VerifyPlatformGoogleRouteTestArtifactForContract verifies a private Gemini
// Files artifact without ever placing the API key in the provider URL or in
// durable route-test evidence. The URL shape is the same exact, credential-free
// locator accepted by the production transfer worker; the key exists only on
// the first host-bound request made by the controlled downloader.
func VerifyPlatformGoogleRouteTestArtifactForContract(
	ctx context.Context,
	providerURL string,
	contract generationprofile.ArtifactContract,
	apiKey string,
) (model.PlatformChannelTestArtifactEvidence, error) {
	if err := validateGoogleGeminiFileDownloadURL(providerURL); err != nil {
		return model.PlatformChannelTestArtifactEvidence{}, err
	}
	apiKey = strings.TrimSpace(apiKey)
	if apiKey == "" || len(apiKey) > 4096 || strings.ContainsAny(apiKey, "\x00\r\n") {
		return model.PlatformChannelTestArtifactEvidence{}, fmt.Errorf("%w: Google route-test credential is invalid", ErrPlatformArtifactSecurity)
	}
	downloader, err := NewPlatformArtifactDownloaderFromEnvironment()
	if err != nil {
		return model.PlatformChannelTestArtifactEvidence{}, err
	}
	return verifyPlatformRouteTestArtifactForContract(
		ctx,
		providerURL,
		contract,
		func(
			downloadContext context.Context,
			sourceURL string,
			expectation PlatformArtifactDownloadExpectation,
		) (*PlatformDownloadedArtifact, error) {
			return downloader.DownloadAuthorized(
				downloadContext,
				sourceURL,
				expectation,
				PlatformArtifactDownloadAuthorization{
					HeaderName:  "x-goog-api-key",
					HeaderValue: apiKey,
					AllowedHost: "generativelanguage.googleapis.com",
				},
			)
		},
	)
}

func verifyPlatformRouteTestArtifact(
	ctx context.Context,
	providerURL string,
	download platformRouteTestArtifactDownloadFunc,
) (model.PlatformChannelTestArtifactEvidence, error) {
	return verifyPlatformRouteTestArtifactForContract(ctx, providerURL, generationprofile.ArtifactContract{
		MediaType: "video", ContentType: "video/mp4", Count: 1,
	}, download)
}

func verifyPlatformRouteTestArtifactForContract(
	ctx context.Context,
	providerURL string,
	contract generationprofile.ArtifactContract,
	download platformRouteTestArtifactDownloadFunc,
) (model.PlatformChannelTestArtifactEvidence, error) {
	expectation, err := platformRouteTestArtifactExpectation(contract)
	if err != nil || ctx == nil || strings.TrimSpace(providerURL) != providerURL || providerURL == "" || download == nil {
		return model.PlatformChannelTestArtifactEvidence{}, fmt.Errorf("%w: route-test artifact request is invalid", ErrPlatformArtifactIntegrity)
	}
	artifact, err := download(ctx, providerURL, expectation)
	if err != nil {
		return model.PlatformChannelTestArtifactEvidence{}, err
	}
	if artifact == nil || artifact.Content == nil || artifact.ContentType != contract.ContentType ||
		artifact.SizeBytes <= 0 || len(artifact.SHA256) != 64 {
		if artifact != nil {
			_ = artifact.Close()
		}
		return model.PlatformChannelTestArtifactEvidence{}, fmt.Errorf("%w: route-test artifact evidence is incomplete", ErrPlatformArtifactIntegrity)
	}
	evidence := model.PlatformChannelTestArtifactEvidence{
		SHA256: artifact.SHA256, SizeBytes: artifact.SizeBytes, ContentType: artifact.ContentType,
	}
	if err := artifact.Close(); err != nil {
		return model.PlatformChannelTestArtifactEvidence{}, fmt.Errorf("%w: route-test artifact cleanup failed", ErrPlatformArtifactIntegrity)
	}
	return evidence, nil
}

func platformRouteTestArtifactExpectation(contract generationprofile.ArtifactContract) (PlatformArtifactDownloadExpectation, error) {
	if contract.Count != 1 {
		return PlatformArtifactDownloadExpectation{}, fmt.Errorf("route-test artifact count is unsupported")
	}
	switch {
	case contract.MediaType == "image" && contract.ContentType == "image/png" &&
		contract.Width > 0 && contract.Height > 0:
		return PlatformArtifactDownloadExpectation{
			ExpectedContentType: contract.ContentType,
			ExpectedImageWidth:  contract.Width,
			ExpectedImageHeight: contract.Height,
		}, nil
	case contract.MediaType == "video" && contract.ContentType == "video/mp4" &&
		contract.Width == 0 && contract.Height == 0:
		return PlatformArtifactDownloadExpectation{ExpectedContentType: contract.ContentType}, nil
	default:
		return PlatformArtifactDownloadExpectation{}, fmt.Errorf("route-test artifact contract is unsupported")
	}
}
