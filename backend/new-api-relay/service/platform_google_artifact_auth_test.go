package service

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const (
	googleArtifactSourceHost       = "generativelanguage.googleapis.com"
	googleArtifactStorageHost      = "storage.googleapis.com"
	googleArtifactUserContentHost  = "video-download.googleusercontent.com"
	googleArtifactTestProviderKey  = "test-google-api-key"
	googleArtifactTestDownloadPath = "/v1beta/files/test-video:download?alt=media"
)

type googleArtifactObservedRequest struct {
	host   string
	path   string
	query  string
	apiKey string
}

func googleArtifactRedirectTLSServer(t *testing.T, handler http.Handler) *httptest.Server {
	t.Helper()
	privateKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	require.NoError(t, err)
	now := time.Now()
	template := &x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "Google artifact redirect test"},
		NotBefore:    now.Add(-time.Hour),
		NotAfter:     now.Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames: []string{
			googleArtifactSourceHost,
			googleArtifactStorageHost,
			googleArtifactUserContentHost,
		},
		BasicConstraintsValid: true,
		IsCA:                  true,
	}
	certificateDER, err := x509.CreateCertificate(rand.Reader, template, template, &privateKey.PublicKey, privateKey)
	require.NoError(t, err)
	server := httptest.NewUnstartedServer(handler)
	server.TLS = &tls.Config{
		Certificates: []tls.Certificate{{
			Certificate: [][]byte{certificateDER},
			PrivateKey:  privateKey,
			Leaf:        template,
		}},
		MinVersion: tls.VersionTLS12,
	}
	server.StartTLS()
	t.Cleanup(server.Close)
	return server
}

func googleArtifactProductionDownloader(
	t *testing.T,
	server *httptest.Server,
	addresses artifactStaticResolver,
	maxBytes int64,
) *PlatformArtifactDownloader {
	t.Helper()
	downloader, err := newPlatformArtifactDownloader(
		PlatformArtifactDownloadConfig{Production: true, MaxBytes: maxBytes, Timeout: 5 * time.Second},
		addresses,
		func(ctx context.Context, network string, _ string) (net.Conn, error) {
			dialer := &net.Dialer{}
			return dialer.DialContext(ctx, network, server.Listener.Addr().String())
		},
	)
	require.NoError(t, err)
	roots := x509.NewCertPool()
	roots.AddCert(server.Certificate())
	downloader.tlsConfig = &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}
	return downloader
}

func googleArtifactDownloadAuthorization() PlatformArtifactDownloadAuthorization {
	return PlatformArtifactDownloadAuthorization{
		HeaderName:  "x-goog-api-key",
		HeaderValue: googleArtifactTestProviderKey,
		AllowedHost: googleArtifactSourceHost,
	}
}

func googleArtifactDownloadSourceURL() string {
	return "https://" + googleArtifactSourceHost + googleArtifactTestDownloadPath
}

func TestGoogleGeminiFileDownloadURLIsExactAndCredentialFree(t *testing.T) {
	valid := "https://generativelanguage.googleapis.com/v1beta/files/video-file_01:download?alt=media"
	require.NoError(t, validateGoogleGeminiFileDownloadURL(valid))

	for name, candidate := range map[string]string{
		"api key in query": valid + "&key=secret",
		"duplicate alt":    valid + "&alt=media",
		"host suffix":      "https://generativelanguage.googleapis.com.attacker.invalid/v1beta/files/video:download?alt=media",
		"userinfo":         "https://secret@generativelanguage.googleapis.com/v1beta/files/video:download?alt=media",
		"wrong version":    "https://generativelanguage.googleapis.com/v1/files/video:download?alt=media",
		"nested path":      "https://generativelanguage.googleapis.com/v1beta/files/folder/video:download?alt=media",
		"encoded identity": "https://generativelanguage.googleapis.com/v1beta/files/video%2Fother:download?alt=media",
		"fragment":         valid + "#secret",
	} {
		t.Run(name, func(t *testing.T) {
			require.ErrorIs(t, validateGoogleGeminiFileDownloadURL(candidate), ErrPlatformArtifactSecurity)
		})
	}
}

func TestPlatformArtifactDownloaderAttachesGoogleKeyOnlyToPinnedHost(t *testing.T) {
	payload := platformArtifactValidMP4Fixture(t)
	const providerKey = "test-google-api-key"
	received := make(chan string, 1)
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		received <- request.Header.Get("x-goog-api-key")
		response.Header().Set("Content-Type", "video/mp4")
		_, _ = response.Write(payload)
	}))
	t.Cleanup(server.Close)

	downloader, sourceURL, _ := artifactTestDownloader(
		t,
		server,
		PlatformArtifactDownloadConfig{MaxBytes: int64(len(payload)) + 1, Timeout: 5 * time.Second},
		[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
	)
	authorization := PlatformArtifactDownloadAuthorization{
		HeaderName:  "x-goog-api-key",
		HeaderValue: providerKey,
		AllowedHost: "artifact.example",
	}
	artifact, err := downloader.DownloadAuthorized(
		context.Background(),
		sourceURL,
		PlatformArtifactDownloadExpectation{ExpectedContentType: "video/mp4"},
		authorization,
	)
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, artifact.Close()) })
	assert.Equal(t, providerKey, <-received)

	wrongHost := authorization
	wrongHost.AllowedHost = "other.example"
	artifact, err = downloader.DownloadAuthorized(
		context.Background(),
		sourceURL,
		PlatformArtifactDownloadExpectation{},
		wrongHost,
	)
	require.ErrorIs(t, err, ErrPlatformArtifactSecurity)
	require.Nil(t, artifact)
}

func TestPlatformArtifactDownloaderFollowsOneAuthorizedGoogleRedirectWithoutForwardingKey(t *testing.T) {
	payload := platformArtifactValidMP4Fixture(t)
	requests := make(chan googleArtifactObservedRequest, 2)
	server := googleArtifactRedirectTLSServer(t, http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		requests <- googleArtifactObservedRequest{
			host:   request.Host,
			path:   request.URL.Path,
			query:  request.URL.RawQuery,
			apiKey: request.Header.Get("x-goog-api-key"),
		}
		switch request.Host {
		case googleArtifactSourceHost:
			response.Header().Set("Location", "https://"+googleArtifactStorageHost+"/signed/video.mp4?X-Goog-Signature=test-signature")
			response.WriteHeader(http.StatusTemporaryRedirect)
		case googleArtifactStorageHost:
			response.Header().Set("Content-Type", "video/mp4")
			_, _ = response.Write(payload)
		default:
			response.WriteHeader(http.StatusNotFound)
		}
	}))
	downloader := googleArtifactProductionDownloader(
		t,
		server,
		artifactStaticResolver{
			googleArtifactSourceHost:  {{IP: net.ParseIP("8.8.8.8")}},
			googleArtifactStorageHost: {{IP: net.ParseIP("8.8.4.4")}},
		},
		int64(len(payload))+1,
	)

	artifact, err := downloader.DownloadAuthorized(
		context.Background(),
		googleArtifactDownloadSourceURL(),
		PlatformArtifactDownloadExpectation{ExpectedContentType: "video/mp4"},
		googleArtifactDownloadAuthorization(),
	)
	require.NoError(t, err)
	t.Cleanup(func() { require.NoError(t, artifact.Close()) })
	assert.Equal(t, int64(len(payload)), artifact.SizeBytes)

	first := <-requests
	second := <-requests
	assert.Equal(t, googleArtifactSourceHost, first.host)
	assert.Equal(t, googleArtifactTestProviderKey, first.apiKey)
	assert.Equal(t, googleArtifactStorageHost, second.host)
	assert.Equal(t, "/signed/video.mp4", second.path)
	assert.Equal(t, "X-Goog-Signature=test-signature", second.query)
	assert.Empty(t, second.apiKey, "the provider API key must never cross the redirect boundary")
}

func TestPlatformArtifactDownloaderRejectsUnreviewedAuthorizedRedirectHost(t *testing.T) {
	requests := make(chan googleArtifactObservedRequest, 2)
	server := googleArtifactRedirectTLSServer(t, http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		requests <- googleArtifactObservedRequest{host: request.Host, apiKey: request.Header.Get("x-goog-api-key")}
		response.Header().Set("Location", "https://storage.googleapis.com.attacker.invalid/video.mp4")
		response.WriteHeader(http.StatusFound)
	}))
	downloader := googleArtifactProductionDownloader(
		t,
		server,
		artifactStaticResolver{
			googleArtifactSourceHost:                  {{IP: net.ParseIP("8.8.8.8")}},
			"storage.googleapis.com.attacker.invalid": {{IP: net.ParseIP("8.8.4.4")}},
		},
		64,
	)

	artifact, err := downloader.DownloadAuthorized(
		context.Background(),
		googleArtifactDownloadSourceURL(),
		PlatformArtifactDownloadExpectation{},
		googleArtifactDownloadAuthorization(),
	)
	require.ErrorIs(t, err, ErrPlatformArtifactSecurity)
	require.Nil(t, artifact)
	first := <-requests
	assert.Equal(t, googleArtifactSourceHost, first.host)
	assert.Equal(t, googleArtifactTestProviderKey, first.apiKey)
	select {
	case request := <-requests:
		t.Fatalf("unreviewed redirect host was requested: %s", request.host)
	default:
	}
}

func TestPlatformArtifactDownloaderRejectsPrivateAuthorizedRedirectResolution(t *testing.T) {
	requests := make(chan googleArtifactObservedRequest, 2)
	server := googleArtifactRedirectTLSServer(t, http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		requests <- googleArtifactObservedRequest{host: request.Host, apiKey: request.Header.Get("x-goog-api-key")}
		response.Header().Set("Location", "https://"+googleArtifactStorageHost+"/signed/video.mp4")
		response.WriteHeader(http.StatusFound)
	}))
	downloader := googleArtifactProductionDownloader(
		t,
		server,
		artifactStaticResolver{
			googleArtifactSourceHost:  {{IP: net.ParseIP("8.8.8.8")}},
			googleArtifactStorageHost: {{IP: net.ParseIP("127.0.0.1")}},
		},
		64,
	)

	artifact, err := downloader.DownloadAuthorized(
		context.Background(),
		googleArtifactDownloadSourceURL(),
		PlatformArtifactDownloadExpectation{},
		googleArtifactDownloadAuthorization(),
	)
	require.ErrorIs(t, err, ErrPlatformArtifactSecurity)
	require.Nil(t, artifact)
	first := <-requests
	assert.Equal(t, googleArtifactSourceHost, first.host)
	select {
	case request := <-requests:
		t.Fatalf("private redirect address was requested: %s", request.host)
	default:
	}
}

func TestPlatformArtifactDownloaderRejectsSecondAuthorizedRedirect(t *testing.T) {
	requests := make(chan googleArtifactObservedRequest, 3)
	server := googleArtifactRedirectTLSServer(t, http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		requests <- googleArtifactObservedRequest{host: request.Host, apiKey: request.Header.Get("x-goog-api-key")}
		switch request.Host {
		case googleArtifactSourceHost:
			response.Header().Set("Location", "https://"+googleArtifactStorageHost+"/first-hop")
			response.WriteHeader(http.StatusFound)
		case googleArtifactStorageHost:
			response.Header().Set("Location", "https://"+googleArtifactUserContentHost+"/second-hop")
			response.WriteHeader(http.StatusTemporaryRedirect)
		default:
			response.WriteHeader(http.StatusNotFound)
		}
	}))
	downloader := googleArtifactProductionDownloader(
		t,
		server,
		artifactStaticResolver{
			googleArtifactSourceHost:      {{IP: net.ParseIP("8.8.8.8")}},
			googleArtifactStorageHost:     {{IP: net.ParseIP("8.8.4.4")}},
			googleArtifactUserContentHost: {{IP: net.ParseIP("1.1.1.1")}},
		},
		64,
	)

	artifact, err := downloader.DownloadAuthorized(
		context.Background(),
		googleArtifactDownloadSourceURL(),
		PlatformArtifactDownloadExpectation{},
		googleArtifactDownloadAuthorization(),
	)
	require.ErrorIs(t, err, ErrPlatformArtifactSecurity)
	require.Nil(t, artifact)
	first := <-requests
	second := <-requests
	assert.Equal(t, googleArtifactSourceHost, first.host)
	assert.Equal(t, googleArtifactTestProviderKey, first.apiKey)
	assert.Equal(t, googleArtifactStorageHost, second.host)
	assert.Empty(t, second.apiKey)
	select {
	case request := <-requests:
		t.Fatalf("second redirect was followed: %s", request.host)
	default:
	}
}

func TestAuthorizedGoogleRedirectURLRequiresAbsoluteHTTPSReviewedHost(t *testing.T) {
	downloader, err := newPlatformArtifactDownloader(
		PlatformArtifactDownloadConfig{Production: true, MaxBytes: 64, Timeout: 5 * time.Second},
		artifactStaticResolver{},
		func(context.Context, string, string) (net.Conn, error) {
			t.Fatal("URL validation must finish before dialing")
			return nil, nil
		},
	)
	require.NoError(t, err)

	for name, location := range map[string][]string{
		"missing":          nil,
		"duplicate":        {"https://storage.googleapis.com/one", "https://storage.googleapis.com/two"},
		"relative":         {"/signed/video.mp4"},
		"HTTP":             {"http://storage.googleapis.com/video.mp4"},
		"userinfo":         {"https://secret@storage.googleapis.com/video.mp4"},
		"fragment":         {"https://storage.googleapis.com/video.mp4#secret"},
		"nonstandard port": {"https://storage.googleapis.com:444/video.mp4"},
		"bare parent":      {"https://googleusercontent.com/video.mp4"},
		"suffix lookalike": {"https://download.googleusercontent.com.attacker.invalid/video.mp4"},
	} {
		t.Run(name, func(t *testing.T) {
			_, _, err := downloader.validateAuthorizedRedirectURL(context.Background(), location)
			require.ErrorIs(t, err, ErrPlatformArtifactSecurity)
		})
	}

	for _, location := range []string{
		"https://storage.googleapis.com/video.mp4?X-Goog-Signature=test",
		"https://video-download.googleusercontent.com/video.mp4?token=test",
	} {
		parsed, port, err := downloader.validateAuthorizedRedirectURL(context.Background(), []string{location})
		require.NoError(t, err)
		assert.Equal(t, 443, port)
		assert.Equal(t, location, parsed.String())
	}
}

func TestProductionArtifactAuthorizationAcceptsOnlyReviewedGoogleHost(t *testing.T) {
	downloader, err := newPlatformArtifactDownloader(
		PlatformArtifactDownloadConfig{Production: true, MaxBytes: 64, Timeout: 5 * time.Second},
		artifactStaticResolver{},
		func(context.Context, string, string) (net.Conn, error) {
			t.Fatal("unreviewed authorization must fail before DNS or dialing")
			return nil, nil
		},
	)
	require.NoError(t, err)

	artifact, err := downloader.DownloadAuthorized(
		context.Background(),
		"https://artifact.example/video.mp4",
		PlatformArtifactDownloadExpectation{},
		PlatformArtifactDownloadAuthorization{
			HeaderName:  "x-goog-api-key",
			HeaderValue: "secret",
			AllowedHost: "artifact.example",
		},
	)
	require.ErrorIs(t, err, ErrPlatformArtifactSecurity)
	require.Nil(t, artifact)
}
