package service

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// This is gzip(base64(sample_fragmented.mp4)) from go-mp4's Apache-2.0 test
// corpus. Keeping a real muxed fixture here prevents the validator test from
// blessing a hand-written ftyp-only byte string as a valid video.
const platformArtifactValidMP4GzipBase64 = `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`

// This is gzip(base64(one-frame.webm)), a 16x16 one-frame VP9 video generated
// with FFmpeg for the test corpus. The production validator itself has no
// FFmpeg dependency.
const platformArtifactValidWebMGzipBase64 = `H4sIAAAAAAAEAJNyvb94vlNbI6PTdyD+1Mji9LmRw6mppTw1KdepvZHJqbWRSSK4IZ2RAQQYXwv6zi7Z5bu7O3h1i6jnyrTgNY0LITyxkHXZQN4N3909QJ5QyOH04DVNjKoQrkzw7mwQ9+obiDkMkQy0AiBXbdK6vrGZ38nBt6HXJ7EszcxIzxCIDAzCHVH5Lp0dDv0OYG0g559YB3Wd/fVGxuKjHWnnN3yWk9OMm9PIoLR1TnNpXkpHI0Nba1h8WIBlcyOj8uPmFutZpxgeTNjQKLCrUWBWI1PohpbQnY2MIN87NBQXL0g+0JB+Ypbr4nZXP2d/F9cgl3ZUBxQX30o+0J2MsCr9xFJk1TOAqpOBqo0sQKoVcjKTygoqdMsKLNNPLHRd3OESGuQY4unv59I+2cDACoQM9QxggEHeeVvp0ueNDIsXNDIwNDR5NjsxMHxg+MZgoSIj4cXAYJDAwCCw//9fjx4GUPRM3N2/uZFhexcwGXxsYlz9oZEZAGL6bjsbAgAA`

// This one-frame H.264 MP4 was generated for the test corpus with its moov
// box after mdat. It exercises validation through ReaderAt rather than assuming
// all metadata is present in the captured prefix.
const platformArtifactTailMoovMP4GzipBase64 = `H4sIAAAAAAAEAH1UQY8bNRR2dqGqqh720AUOi2RKkTg0szOTbFiijihaKnIAiQsVQoiRx/ZkrNhjr+1kk55A6oFf0J+AVAm49gTaAxwQ4s4KJISQEKgSPdILhOdJlmQL1Br7Pb/3vff83rMHIYRLPzPCaYXQBgoUZkomNFGmmyCEzpeWc9B9qxjxQD899+R8fveHG799/svJ4N6dq9/hk+d/fzBNe13cxlRbjpPeHradNO3guLsf0yIGxSACwO6bb914vd3Fr948ACTjFBQH2swkLz1O47jTTuN0D4SV96a/u3t0dBRNBONakjrSdrgbokSVVxIw2niha9fHlBSEZgm2vMw6mPFCajrKkn7cjzGpiZw5nsXTTj+eJkkHK55VfIrduADuJWzcDExhzS3LkigGI1iwElPO8uAxAYvcknrIs6SHaWW1IjmYJthbLqVwwO1P9xn1wNBDlcVwBMJu6ZpnaXI1SXBJnM+NGwkTEAsHhybXZem4z9op9pUFi+BIaj0iFWzylcxJQflKEOPaNjGoUMSHc4jacysJgEBeyLEls5xqZYiHPYUSeUtEDS4AaEnAlJYo7qBYRW5mwAuWpcATRkzIosgLQUIkJixv8jriYlj5AjhteJ0PtQHtQmjAdMRn4DtL9+IlmytRh6NTXnM69lk3xk3wUFHLXQXWlub/ZBv0lmbUllgVUNaQFWyytBPF+DDkksVRD1gT/DaUTLPey8A4z03WxcJAj+A+QAvBFzmE9odmog/gAm/xj26j7flff3z94Pjj+9//+CFCm9tK6wnopJpUDJ0Zm782s4XCtxqts6hH99fRY8cGfK9AAUbAv+dHTczNf3sLsR8f5z/jbi0nQlc48w7oDpfOryyWfhvb1j3FBAEGK/Zo7teb+faXzaZdMWlPNeEZriNvhmc5IDWTPGBaBXSmBObSRDVO1495hS10Owzav5bGxbGVeMG3LjtfSOC/cN6xNcxn4U/0P6UISW+hAdDBKWL7DcD30ijdjxJ4xlIU4ZexZvHMfA5rF1AHLYYuzH9CTw+BfnLy/vFroSVPhOX8tcGdd1roXHX/528uH//58GETyxBnlqcI8xLc1GWJn3rx1LvzTfnRChdKinYgK3pWvvIDulsL1cZXKxnVa/gYZjFmPrTtXcUbGsZz6y2CtlpijFwvUFss7sELd70ODp+Ffzg5LSXUqoRaJWmo1d/lDtvqCgYAAA==`

type artifactStaticResolver map[string][]net.IPAddr

func (resolver artifactStaticResolver) LookupIPAddr(_ context.Context, host string) ([]net.IPAddr, error) {
	addresses, ok := resolver[host]
	if !ok {
		return nil, fmt.Errorf("unexpected lookup for %s", host)
	}
	return addresses, nil
}

func artifactTestDownloader(
	t *testing.T,
	server *httptest.Server,
	config PlatformArtifactDownloadConfig,
	addresses []net.IPAddr,
) (*PlatformArtifactDownloader, string, *[]string) {
	t.Helper()
	serverURL, err := url.Parse(server.URL)
	require.NoError(t, err)
	host := "artifact.example"
	sourceURL := "http://" + net.JoinHostPort(host, serverURL.Port()) + "/artifact"
	dialed := make([]string, 0, 1)
	downloader, err := newPlatformArtifactDownloader(
		config,
		artifactStaticResolver{host: addresses},
		func(ctx context.Context, network string, address string) (net.Conn, error) {
			dialed = append(dialed, address)
			dialer := &net.Dialer{}
			return dialer.DialContext(ctx, network, server.Listener.Addr().String())
		},
	)
	require.NoError(t, err)
	return downloader, sourceURL, &dialed
}

func platformArtifactGzipFixture(t *testing.T, encoded string) []byte {
	t.Helper()
	compressed, err := base64.StdEncoding.DecodeString(encoded)
	require.NoError(t, err)
	reader, err := gzip.NewReader(bytes.NewReader(compressed))
	require.NoError(t, err)
	payload, err := io.ReadAll(reader)
	require.NoError(t, err)
	require.NoError(t, reader.Close())
	require.NotEmpty(t, payload)
	return payload
}

func platformArtifactValidMP4Fixture(t *testing.T) []byte {
	t.Helper()
	payload := platformArtifactGzipFixture(t, platformArtifactTailMoovMP4GzipBase64)
	reader := bytes.NewReader(payload)
	offset := int64(0)
	mediaDataOffset := int64(-1)
	movieOffset := int64(-1)
	for offset < int64(len(payload)) {
		box, err := readPlatformISOBox(reader, offset, int64(len(payload)))
		require.NoError(t, err)
		switch string(box.typ[:]) {
		case "mdat":
			mediaDataOffset = box.start
		case "moov":
			movieOffset = box.start
		}
		offset = box.end
	}
	require.GreaterOrEqual(t, mediaDataOffset, int64(0))
	require.Greater(t, movieOffset, mediaDataOffset)
	return payload
}

func platformArtifactFragmentedMP4Fixture(t *testing.T) []byte {
	t.Helper()
	return platformArtifactGzipFixture(t, platformArtifactValidMP4GzipBase64)
}

func platformArtifactMP4FileTypeOnlyFixture(t *testing.T) []byte {
	t.Helper()
	payload := platformArtifactValidMP4Fixture(t)
	require.GreaterOrEqual(t, len(payload), 8)
	require.Equal(t, "ftyp", string(payload[4:8]))
	boxSize := int(binary.BigEndian.Uint32(payload[:4]))
	require.GreaterOrEqual(t, boxSize, 16)
	require.LessOrEqual(t, boxSize, len(payload))
	return append([]byte(nil), payload[:boxSize]...)
}

func platformArtifactImageFixture(t *testing.T, contentType string) []byte {
	t.Helper()
	picture := image.NewNRGBA(image.Rect(0, 0, 2, 2))
	picture.SetNRGBA(0, 0, color.NRGBA{R: 255, A: 255})
	picture.SetNRGBA(1, 0, color.NRGBA{G: 255, A: 255})
	picture.SetNRGBA(0, 1, color.NRGBA{B: 255, A: 255})
	picture.SetNRGBA(1, 1, color.NRGBA{R: 255, G: 255, B: 255, A: 255})

	var payload bytes.Buffer
	switch contentType {
	case "image/jpeg":
		require.NoError(t, jpeg.Encode(&payload, picture, &jpeg.Options{Quality: 90}))
	case "image/png":
		require.NoError(t, png.Encode(&payload, picture))
	default:
		t.Fatalf("unsupported image fixture type %q", contentType)
	}
	return payload.Bytes()
}

func platformArtifactValidWebPFixture(t *testing.T) []byte {
	t.Helper()
	payload, err := base64.StdEncoding.DecodeString("UklGRh4AAABXRUJQVlA4TBEAAAAvAAAAAAfQ//73v/+BiOh/AAA=")
	require.NoError(t, err)
	return payload
}

func platformArtifactValidWebMFixture(t *testing.T) []byte {
	t.Helper()
	return platformArtifactGzipFixture(t, platformArtifactValidWebMGzipBase64)
}

func artifactTestDownloadPayload(
	t *testing.T,
	payload []byte,
	contentType string,
) (*PlatformDownloadedArtifact, error) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		response.Header().Set("Content-Type", contentType)
		_, _ = response.Write(payload)
	}))
	t.Cleanup(server.Close)
	downloader, sourceURL, _ := artifactTestDownloader(
		t,
		server,
		PlatformArtifactDownloadConfig{MaxBytes: int64(len(payload)) + 1, Timeout: 5 * time.Second},
		[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
	)
	return downloader.Download(context.Background(), sourceURL, PlatformArtifactDownloadExpectation{})
}

func TestPlatformArtifactDownloaderPinsPublicDNSAndStreamsIntegrity(t *testing.T) {
	payload := platformArtifactValidMP4Fixture(t)
	digest := sha256.Sum256(payload)
	server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		assert.Equal(t, "/artifact", request.URL.Path)
		response.Header().Set("Content-Type", "video/mp4; charset=binary")
		_, _ = response.Write(payload)
	}))
	defer server.Close()
	downloader, sourceURL, dialed := artifactTestDownloader(
		t,
		server,
		PlatformArtifactDownloadConfig{MaxBytes: int64(len(payload)) + 1, Timeout: 5 * time.Second},
		[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
	)
	expectedSize := int64(len(payload))
	artifact, err := downloader.Download(context.Background(), sourceURL, PlatformArtifactDownloadExpectation{
		SizeBytes: &expectedSize,
		SHA256:    fmt.Sprintf("sha256:%x", digest),
	})
	require.NoError(t, err)
	defer artifact.Close()

	actual, err := io.ReadAll(artifact.Content)
	require.NoError(t, err)
	assert.Equal(t, payload, actual)
	assert.Equal(t, "video/mp4", artifact.ContentType)
	assert.Equal(t, expectedSize, artifact.SizeBytes)
	assert.Equal(t, fmt.Sprintf("%x", digest), artifact.SHA256)
	require.Len(t, *dialed, 1)
	assert.True(t, strings.HasPrefix((*dialed)[0], "8.8.8.8:"), (*dialed)[0])
}

func TestPlatformArtifactDownloaderAcceptsStructurallyValidMedia(t *testing.T) {
	tests := []struct {
		name        string
		contentType string
		payload     []byte
	}{
		{name: "JPEG", contentType: "image/jpeg", payload: platformArtifactImageFixture(t, "image/jpeg")},
		{name: "PNG", contentType: "image/png", payload: platformArtifactImageFixture(t, "image/png")},
		{name: "WebP", contentType: "image/webp", payload: platformArtifactValidWebPFixture(t)},
		{name: "tail-moov MP4", contentType: "video/mp4", payload: platformArtifactValidMP4Fixture(t)},
		{name: "fragmented MP4", contentType: "video/mp4", payload: platformArtifactFragmentedMP4Fixture(t)},
		{name: "WebM", contentType: "video/webm", payload: platformArtifactValidWebMFixture(t)},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			artifact, err := artifactTestDownloadPayload(t, test.payload, test.contentType)
			require.NoError(t, err)
			t.Cleanup(func() { require.NoError(t, artifact.Close()) })
			assert.Equal(t, test.contentType, artifact.ContentType)
			assert.Equal(t, int64(len(test.payload)), artifact.SizeBytes)
		})
	}
}

func TestPlatformArtifactDownloaderRejectsSpoofedOrTruncatedMedia(t *testing.T) {
	pngPayload := platformArtifactImageFixture(t, "image/png")
	tests := []struct {
		name        string
		contentType string
		payload     []byte
	}{
		{name: "arbitrary bytes as MP4", contentType: "video/mp4", payload: []byte("verified-video-payload")},
		{name: "HTML as MP4", contentType: "video/mp4", payload: []byte("<!doctype html><html><body>upstream error</body></html>")},
		{name: "JSON as MP4", contentType: "video/mp4", payload: []byte(`{"error":"provider unavailable"}`)},
		{name: "ftyp-only MP4", contentType: "video/mp4", payload: platformArtifactMP4FileTypeOnlyFixture(t)},
		{
			name:        "truncated ftyp",
			contentType: "video/mp4",
			payload:     []byte{0x00, 0x00, 0x00, 0x20, 'f', 't', 'y', 'p', 'i', 's', 'o', 'm', 0x00, 0x00, 0x02, 0x00},
		},
		{name: "PNG as MP4", contentType: "video/mp4", payload: pngPayload},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			artifact, err := artifactTestDownloadPayload(t, test.payload, test.contentType)
			require.ErrorIs(t, err, ErrPlatformArtifactIntegrity)
			assert.Nil(t, artifact)
		})
	}
}

func TestPlatformArtifactDownloaderRejectsUnsafeResponses(t *testing.T) {
	t.Run("mixed public and private DNS result", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
		defer server.Close()
		downloader, sourceURL, dialed := artifactTestDownloader(
			t,
			server,
			PlatformArtifactDownloadConfig{MaxBytes: 64, Timeout: 5 * time.Second},
			[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}, {IP: net.ParseIP("127.0.0.1")}},
		)
		artifact, err := downloader.Download(context.Background(), sourceURL, PlatformArtifactDownloadExpectation{})
		require.ErrorIs(t, err, ErrPlatformArtifactSecurity)
		assert.Nil(t, artifact)
		assert.Empty(t, *dialed)
	})

	t.Run("redirect", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
			response.Header().Set("Location", "http://redirect.example/artifact")
			response.WriteHeader(http.StatusFound)
		}))
		defer server.Close()
		downloader, sourceURL, _ := artifactTestDownloader(
			t,
			server,
			PlatformArtifactDownloadConfig{MaxBytes: 64, Timeout: 5 * time.Second},
			[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
		)
		artifact, err := downloader.Download(context.Background(), sourceURL, PlatformArtifactDownloadExpectation{})
		require.ErrorIs(t, err, ErrPlatformArtifactSecurity)
		assert.Nil(t, artifact)
	})

	t.Run("stream exceeds maximum", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
			response.Header().Set("Content-Type", "image/png")
			response.WriteHeader(http.StatusOK)
			if flusher, ok := response.(http.Flusher); ok {
				flusher.Flush()
			}
			_, _ = response.Write([]byte("12345"))
		}))
		defer server.Close()
		downloader, sourceURL, _ := artifactTestDownloader(
			t,
			server,
			PlatformArtifactDownloadConfig{MaxBytes: 4, Timeout: 5 * time.Second},
			[]net.IPAddr{{IP: net.ParseIP("8.8.8.8")}},
		)
		artifact, err := downloader.Download(context.Background(), sourceURL, PlatformArtifactDownloadExpectation{})
		require.ErrorIs(t, err, ErrPlatformArtifactTooLarge)
		assert.Nil(t, artifact)
	})

	t.Run("production requires HTTPS 443", func(t *testing.T) {
		downloader, err := newPlatformArtifactDownloader(
			PlatformArtifactDownloadConfig{Production: true, MaxBytes: 64, Timeout: 5 * time.Second},
			artifactStaticResolver{},
			func(context.Context, string, string) (net.Conn, error) {
				t.Fatal("production HTTP URL must fail before dialing")
				return nil, nil
			},
		)
		require.NoError(t, err)
		artifact, err := downloader.Download(context.Background(), "http://artifact.example/video", PlatformArtifactDownloadExpectation{})
		require.ErrorIs(t, err, ErrPlatformArtifactSecurity)
		assert.Nil(t, artifact)
	})
}
