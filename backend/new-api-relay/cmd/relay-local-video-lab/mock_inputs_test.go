//go:build relay_local_video_lab

package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const labInputImageID = "00000000-0000-4000-8000-000000000001"
const labInputVideoID = "00000000-0000-4000-8000-000000000002"
const labInputSigningSecret = "only-a-local-input-fixture-signing-key"

func labTestInputURL(id, secret string) string {
	expires := strconv.FormatInt(time.Now().UTC().Add(10*time.Minute).Unix(), 10)
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte("v1\n" + id + "\n" + expires + "\ninline"))
	return "https://" + labObjectStoreHost + "/api/v1/input-assets/" + id + "/content?expires=" + expires + "&disposition=inline&signature=" + fmt.Sprintf("%x", mac.Sum(nil))
}

func labTestInputPNG(t *testing.T) []byte {
	t.Helper()
	var buffer bytes.Buffer
	picture := image.NewRGBA(image.Rect(0, 0, 256, 256))
	for y := 0; y < 256; y++ {
		for x := 0; x < 256; x++ {
			picture.SetRGBA(x, y, color.RGBA{R: 8, G: 123, B: 128, A: 255})
		}
	}
	require.NoError(t, png.Encode(&buffer, picture))
	return buffer.Bytes()
}

func labTestInputMP4(t *testing.T) []byte {
	t.Helper()
	// Real locally encoded H.264: 1366x768, 24 fps, four seconds, no audio.
	// Gzip only keeps the fixture source compact; this is not an ftyp-only stub.
	const compressed = "H4sIAAAAAAACCu1YzYsjRRSvnszIHsI6iyPMQaHEEUQ3ne6eTMgONGYNi3NQ0MMOImqm0l093aSqu1NVySR7moOIeFckswcvgv4FXhTmIKIXr+6KB73I6sGP297iq86E1Ay44EFU6Mqrer/36tWr1+9VN6QQQluRmuSJzDhCK0hz6E3oHhkFLs8bLujv8SwbIYQYH8UhOtcq9wpmFbRs1nmri3IbPbCtwIJflCB9wK+rfrFn5QHe/9a+a/sLZ9bbPEwIAMzDi8/lFOPNLwpWi0MmFjOjJKSm5T7I2R5JQ0a1jeXyJI0AbIx44dQMYSuczz0WChoZIVaHguEz/IdUPQb8I6lkaNi8pwvyVwlY24dH2kPzXlg8+iLYNz3ba9mu42CW9MZes2Es2ZzNYKyDVcd6/hTPfkSbh8C/s17dnHy22tZJgqFa+bL6sYVW428e/grM13Mi87MAdF+XSknDKcgyMOQNkG9diBVsgsyQn+YjOtZVUKLgyPBvtt4wVLpar3FacN2eMCsD1RQkz5m5qJYwqYA/9YnK9KaPh6RYXDiHFEWQItfTKQLd73DOdVnWeRQbqbfuwFnU+i01119bRlepFscVXb5tzTUbKgrV+dCtD5QYpmC3BsIByPdXCv3l27r0Zf+3+iObHE4D1OXmQ2uz2Svf3/j585/u7n36/tU7+O6Tv/6m3xdcw0EmKHabO1hse942dhotJ+g5MLFng0H9pZdvvFBr4Ov7HbAMaQATnSyfMBop7DnOds1zvB1Qxkrlu/X60dGRrb8gGSOpnYnDut7FjhVnYJPlKslSuYsD0iOB72D4SvguDmmPZUHfd3bhh0lK2ERSLWFOffiCYTnsAXJwLidgDmNXhL5rO2ACA+bJmIZd7Uuv6AqSHlLfbeIgFhknXVjq6nePsUSCRWvcCgMFIBhwGENKwltZSn3Pveq6OCJSdXPZT3JYdOZgkHezKJJUL1KxgAXS9xqYZVmfxCB1F8oGliwJ6FLh4FQUewQJJ0rHkaSKCkbACPQ9NhRk0g0ynpMiIkgOvIpJCi7AUBBtEwnCqXZ1RJPDWOWA+nQC0763s4Bd+CbrkGRAUxoMta9ivU6KoDLWqQ78QESY9yATOpcg+N627eCB3t537CbAXPspOBn7zWsApKI5PFeSQ1qheJD1BpRo4DvoGM7Yc/Sdt3avvItWrgxOUdtroUuNr/+J9u0bw5JKKqmkkkoqqaSSSirp/01vwv/US9en+JnjlVVnjtsGPjDwsb3EHxr41MA/LPEJMjA2cNvABwY2/J8Y/k8M/yeG/6nhf2r4nxr+p4b/Mv4y/jL+/0r8HQ6XKsCfVcCt81fOF25lK1WruPiEK1OxuNTt/AmoVzXHVxgAAA=="
	encoded, err := base64.StdEncoding.DecodeString(compressed)
	require.NoError(t, err)
	reader, err := gzip.NewReader(bytes.NewReader(encoded))
	require.NoError(t, err)
	defer reader.Close()
	payload, err := io.ReadAll(reader)
	require.NoError(t, err)
	require.Len(t, payload, 6231)
	return payload
}

func labTestInputHTTPS(t *testing.T, handler http.Handler) (*http.Client, *httptest.Server) {
	t.Helper()
	directory := t.TempDir()
	require.NoError(t, prepareLabObjectStoreTLS(directory, "video-mock-"+strings.Repeat("a", 20), "mock-"+strings.Repeat("b", 64)))
	certificate, err := tls.LoadX509KeyPair(filepath.Join(directory, "server.pem"), filepath.Join(directory, "server-key.pem"))
	require.NoError(t, err)
	server := httptest.NewUnstartedServer(handler)
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS12, Certificates: []tls.Certificate{certificate}}
	server.StartTLS()
	t.Cleanup(server.Close)
	ca, err := os.ReadFile(filepath.Join(directory, "ca.pem"))
	require.NoError(t, err)
	client, err := newLabMockInputClient(ca)
	require.NoError(t, err)
	// Only the test socket changes. The production URL, Host, SNI, local CA
	// verification, response streaming and redirect policy remain intact.
	transport := client.Transport.(*http.Transport)
	transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		assert.Equal(t, labObjectStoreHost+":443", address)
		return (&net.Dialer{}).DialContext(ctx, network, server.Listener.Addr().String())
	}
	t.Cleanup(client.CloseIdleConnections)
	return client, server
}

func labTestInputSource(t *testing.T, imageBytes, videoBytes []byte, reads *atomic.Int64) http.Handler {
	t.Helper()
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		reads.Add(1)
		assert.Equal(t, http.MethodGet, request.Method)
		assert.Equal(t, labObjectStoreHost, request.Host)
		assert.Equal(t, labObjectStoreHost, request.TLS.ServerName)
		for _, name := range []string{"Authorization", "Cookie", "X-Bootstrap-Token", "X-API-Key", "Origin", "X-Forwarded-For"} {
			assert.Empty(t, request.Header.Get(name))
		}
		id := strings.TrimSuffix(strings.TrimPrefix(request.URL.Path, "/api/v1/input-assets/"), "/content")
		query := request.URL.Query()
		mac := hmac.New(sha256.New, []byte(labInputSigningSecret))
		_, _ = mac.Write([]byte("v1\n" + id + "\n" + query.Get("expires") + "\n" + query.Get("disposition")))
		if !hmac.Equal([]byte(query.Get("signature")), []byte(fmt.Sprintf("%x", mac.Sum(nil)))) {
			http.NotFound(writer, request)
			return
		}
		switch id {
		case labInputImageID:
			writer.Header().Set("Content-Type", "image/png")
			_, _ = writer.Write(imageBytes)
		case labInputVideoID:
			writer.Header().Set("Content-Type", "video/mp4")
			_, _ = writer.Write(videoBytes)
		default:
			// As with the Platform endpoint, missing and disabled private assets
			// are indistinguishable and never disclose an object or identity.
			http.NotFound(writer, request)
		}
	})
}

func labTestMockSubmit(t *testing.T, provider *labMockProvider, family, modelID string, content []labMockContent) *httptest.ResponseRecorder {
	t.Helper()
	host, path := "api.minimax.cn", "/v2/video_generation"
	if family == "ark" {
		host, path = "ark.cn-beijing.volces.com", "/api/v3/contents/generations/tasks"
	}
	payload, err := common.Marshal(map[string]any{"model": modelID, "content": content, "resolution": "768P", "duration": 4, "ratio": "16:9"})
	require.NoError(t, err)
	request := httptest.NewRequest(http.MethodPost, "https://"+host+path, bytes.NewReader(payload))
	request.Header.Set("Authorization", "Bearer mock-only-provider-key")
	request.Header.Set("Cookie", "not-a-source-credential")
	response := httptest.NewRecorder()
	provider.ServeHTTP(response, request)
	return response
}

func TestLabMockProviderReadsImageVideoReferencesAndPersistsOnlyByteEvidence(t *testing.T) {
	imageBytes, videoBytes := labTestInputPNG(t), labTestInputMP4(t)
	for _, family := range []string{"ark", "minimax"} {
		t.Run(family, func(t *testing.T) {
			var reads atomic.Int64
			client, _ := labTestInputHTTPS(t, labTestInputSource(t, imageBytes, videoBytes, &reads))
			modelID, host := "MiniMax-H3", "api.minimax.cn"
			if family == "ark" {
				modelID, host = "doubao-seedance-2-5-260628", "ark.cn-beijing.volces.com"
			}
			fixture := labVideoFixture{Resolution: "768p", DurationSeconds: 4, AspectRatio: "16:9", payload: videoBytes, SHA256: fmt.Sprintf("%x", sha256.Sum256(videoBytes))}
			provider, err := newLabMockProvider(t.TempDir(), []labMockModel{{ID: modelID, Family: family, Host: host, Key: "mock-only-provider-key"}}, map[string]labVideoFixture{labFixtureKey("768p", 4, "16:9"): fixture}, bytes.Repeat([]byte{12}, 32))
			require.NoError(t, err)
			provider.inputClient = client
			content := []labMockContent{{Type: "text", Text: "An explicit local reference read"}, {Type: "image_url", ImageURL: &labMockInputLocator{URL: labTestInputURL(labInputImageID, labInputSigningSecret)}}, {Type: "video_url", VideoURL: &labMockInputLocator{URL: labTestInputURL(labInputVideoID, labInputSigningSecret)}}}
			response := labTestMockSubmit(t, provider, family, modelID, content)
			require.Equal(t, http.StatusOK, response.Code, response.Body.String())
			assert.Equal(t, int64(2), reads.Load())
			assert.Equal(t, int64(2), provider.inputFetches.Load())
			assert.Equal(t, int64(len(imageBytes)+len(videoBytes)), provider.inputBytes.Load())
			var receipt map[string]string
			require.NoError(t, common.Unmarshal(response.Body.Bytes(), &receipt))
			id := receipt["task_id"]
			if family == "ark" {
				id = receipt["id"]
			}
			task, err := provider.readTask(id)
			require.NoError(t, err)
			require.Len(t, task.Inputs, 2)
			assert.Equal(t, labMockInputEvidence{MediaType: "image", ContentType: "image/png", SizeBytes: int64(len(imageBytes)), SHA256: fmt.Sprintf("%x", sha256.Sum256(imageBytes))}, task.Inputs[0])
			assert.Equal(t, labMockInputEvidence{MediaType: "video", ContentType: "video/mp4", SizeBytes: int64(len(videoBytes)), SHA256: fmt.Sprintf("%x", sha256.Sum256(videoBytes))}, task.Inputs[1])
			stored, err := os.ReadFile(filepath.Join(provider.directory, id+".json"))
			require.NoError(t, err)
			for _, forbidden := range []string{"https://", "signature=", labInputSigningSecret, "mock-only-provider-key"} {
				assert.NotContains(t, string(stored), forbidden)
			}
			// Polling reuses the existing task. It does not refetch or silently
			// reacquire an expired reference bearer URL.
			poll := httptest.NewRequest(http.MethodGet, "https://"+host+"/", nil)
			poll.Header.Set("Authorization", "Bearer mock-only-provider-key")
			provider.poll(httptest.NewRecorder(), poll, family, id)
			assert.Equal(t, int64(2), reads.Load())
		})
	}
}

func TestLabMockProviderRejectsBadSignaturesMissingAndDisabledInputs(t *testing.T) {
	var reads atomic.Int64
	client, _ := labTestInputHTTPS(t, labTestInputSource(t, labTestInputPNG(t), nil, &reads))
	for _, test := range []struct{ name, id, secret string }{
		{"bad signature", labInputImageID, "not-the-Platform-key"},
		{"missing", "00000000-0000-4000-8000-000000000003", labInputSigningSecret},
		{"disabled", "00000000-0000-4000-8000-000000000004", labInputSigningSecret},
	} {
		t.Run(test.name, func(t *testing.T) {
			provider, err := newLabMockProvider(t.TempDir(), []labMockModel{{ID: "MiniMax-H3", Family: "minimax", Host: "api.minimax.cn", Key: "mock-only-provider-key"}}, map[string]labVideoFixture{labFixtureKey("768p", 4, "16:9"): {Resolution: "768p", DurationSeconds: 4, AspectRatio: "16:9"}}, bytes.Repeat([]byte{13}, 32))
			require.NoError(t, err)
			provider.inputClient = client
			response := labTestMockSubmit(t, provider, "minimax", "MiniMax-H3", []labMockContent{{Type: "text", Text: "A rejected reference"}, {Type: "image_url", ImageURL: &labMockInputLocator{URL: labTestInputURL(test.id, test.secret)}}})
			assert.Equal(t, http.StatusUnprocessableEntity, response.Code)
			assert.NotContains(t, response.Body.String(), test.secret)
			assert.NotContains(t, response.Body.String(), "signature=")
			assert.Zero(t, provider.inputFetches.Load())
			entries, err := os.ReadDir(provider.directory)
			require.NoError(t, err)
			assert.Empty(t, entries, "no mock provider task may exist after a rejected reference")
		})
	}
	assert.Equal(t, int64(3), reads.Load(), "the valid-shaped bad signatures must actually reach the signed source")
}

func TestLabMockInputRejectsUnsafeTargetsBeforeHTTP(t *testing.T) {
	var calls atomic.Int64
	client, _ := labTestInputHTTPS(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls.Add(1) }))
	valid := labTestInputURL(labInputImageID, labInputSigningSecret)
	for _, raw := range []string{
		strings.Replace(valid, "https:", "http:", 1), strings.Replace(valid, labObjectStoreHost, "attacker.invalid", 1),
		strings.Replace(valid, labObjectStoreHost, labObjectStoreHost+":443", 1), strings.Replace(valid, labObjectStoreHost, "127.0.0.1", 1),
		strings.Replace(valid, labObjectStoreHost, "key:secret@"+labObjectStoreHost, 1), strings.Replace(valid, "/api/v1/", "/admin/", 1),
		strings.Replace(valid, "/api/", "/%61pi/", 1), valid + "#hidden", valid + "&redirect=https://attacker.invalid", valid + "&signature=" + strings.Repeat("a", 64),
		strings.Replace(valid, "signature=", "signature=BAD", 1), strings.Replace(valid, "disposition=inline", "disposition=invalid", 1),
		strings.Replace(valid, "expires=", "expires=0", 1),
	} {
		_, err := readLabMockInput(context.Background(), client, raw, "image")
		require.Error(t, err)
		assert.NotContains(t, err.Error(), raw)
	}
	assert.Zero(t, calls.Load())
}

func TestLabMockInputRejectsRedirectMIMESpoofingAndTruncatedBytes(t *testing.T) {
	imageBytes := labTestInputPNG(t)
	for _, test := range []struct {
		name    string
		handler http.HandlerFunc
	}{
		{"redirect", func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, labTestInputURL(labInputImageID, labInputSigningSecret), http.StatusFound)
		}},
		{"wrong MIME", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "text/html")
			_, _ = w.Write(imageBytes)
		}},
		{"spoofed PNG", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "image/png")
			_, _ = w.Write([]byte("<html>not image bytes</html>"))
		}},
		{"empty", func(w http.ResponseWriter, _ *http.Request) { w.Header().Set("Content-Type", "image/png") }},
		{"declared oversize", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "image/png")
			w.Header().Set("Content-Length", strconv.FormatInt(labMockInputMaxBytes+1, 10))
			_, _ = w.Write(imageBytes)
		}},
		{"truncated", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "image/png")
			w.Header().Set("Content-Length", strconv.Itoa(len(imageBytes)+1))
			_, _ = w.Write(imageBytes)
		}},
		{"compressed", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Type", "image/png")
			w.Header().Set("Content-Encoding", "gzip")
			_, _ = w.Write(imageBytes)
		}},
	} {
		t.Run(test.name, func(t *testing.T) {
			var calls atomic.Int64
			client, _ := labTestInputHTTPS(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); test.handler(w, r) }))
			_, err := readLabMockInput(context.Background(), client, labTestInputURL(labInputImageID, labInputSigningSecret), "image")
			require.Error(t, err)
			assert.Equal(t, int64(1), calls.Load(), "redirects are never followed")
		})
	}
}

type labZeroInputReader struct{}

func (labZeroInputReader) Read(p []byte) (int, error) { clear(p); return len(p), nil }

func TestLabMockInputEnforcesStreamingByteLimit(t *testing.T) {
	imageBytes := labTestInputPNG(t)
	for _, size := range []int64{labMockInputMaxBytes, labMockInputMaxBytes + 1} {
		t.Run(strconv.FormatInt(size, 10), func(t *testing.T) {
			client, _ := labTestInputHTTPS(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "image/png")
				w.(http.Flusher).Flush() // Unknown Content-Length must not bypass the limit.
				_, _ = w.Write(imageBytes)
				_, _ = io.Copy(w, io.LimitReader(labZeroInputReader{}, size-int64(len(imageBytes))))
			}))
			evidence, err := readLabMockInput(context.Background(), client, labTestInputURL(labInputImageID, labInputSigningSecret), "image")
			if size > labMockInputMaxBytes {
				require.Error(t, err)
				return
			}
			require.NoError(t, err)
			assert.Equal(t, size, evidence.SizeBytes)
			digest := sha256.New()
			_, _ = digest.Write(imageBytes)
			_, _ = io.Copy(digest, io.LimitReader(labZeroInputReader{}, size-int64(len(imageBytes))))
			assert.Equal(t, fmt.Sprintf("%x", digest.Sum(nil)), evidence.SHA256)
		})
	}
}

func TestLabMockInputRequiresTrustedTLSAndBoundedResponseTime(t *testing.T) {
	client, server := labTestInputHTTPS(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-r.Context().Done() }))
	client.Timeout = 30 * time.Millisecond
	_, err := readLabMockInput(context.Background(), client, labTestInputURL(labInputImageID, labInputSigningSecret), "image")
	require.Error(t, err)
	assert.Equal(t, "mock reference HTTPS read failed", err.Error())
	untrusted := server.Client()
	untrusted.Transport = client.Transport.(*http.Transport).Clone()
	untrusted.Transport.(*http.Transport).TLSClientConfig.RootCAs = nil
	t.Cleanup(untrusted.CloseIdleConnections)
	_, err = readLabMockInput(context.Background(), untrusted, labTestInputURL(labInputImageID, labInputSigningSecret), "image")
	require.Error(t, err)
	assert.Equal(t, "mock reference HTTPS read failed", err.Error())
}
