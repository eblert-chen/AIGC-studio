//go:build relay_local_video_lab

package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/localvideoconfig"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func localObjectFixture(t *testing.T, mode string) (labOptions, *labState, localvideoconfig.Config, map[string]string) {
	t.Helper()
	seed := []byte("offline-local-object-storage-seed-32-bytes")
	keys := localvideoconfig.ProviderKeys{}
	if mode == localvideoconfig.ModeLive {
		keys.MiniMax = "offline-object-storage-not-a-provider-key"
	}
	config, err := localvideoconfig.Build(localvideoconfig.Options{
		Mode: mode, Environment: "development", Namespace: "offline-object-storage-test",
		Now: time.Date(2026, 8, 31, 12, 0, 0, 0, time.UTC), RuntimeSeed: seed,
		CreatedBy: "offline-object-test", Reason: "Local persistence contract; never production acceptance",
		ModelIDs: []string{"minimax-h3-max"}, Keys: keys, CallbackURL: "http://127.0.0.1:8000/internal/relay-callbacks/new-api-v1",
	})
	require.NoError(t, err)
	state := &labState{Directory: t.TempDir(), Manifest: labStateManifest{Mode: mode, StateID: config.StateID}, Seed: seed}
	options := labOptions{Mode: mode, ContainerLab: true, StateDirectory: state.Directory}
	if mode == localvideoconfig.ModeMock {
		require.NoError(t, prepareLabMockTLS(state.Directory, nil))
	}
	environment, err := config.RuntimeEnvironment()
	require.NoError(t, err)
	return options, state, config, environment
}

func TestLocalObjectStorageUsesRealSDKBindingsAndNarrowTLSAuthority(t *testing.T) {
	for _, mode := range []string{localvideoconfig.ModeMock, localvideoconfig.ModeLive} {
		t.Run(mode, func(t *testing.T) {
			options, state, config, environment := localObjectFixture(t, mode)
			require.NoError(t, configureLabArtifactStorage(options, state, config, environment))
			assert.Equal(t, "huawei_obs", environment["RELAY_ARTIFACT_STORE"])
			assert.Equal(t, "https://lab-objects.local.test", environment["HUAWEI_OBS_ENDPOINT"])
			assert.Equal(t, config.StateID, environment["PLATFORM_LAB_OBJECT_STORE_STATE_ID"])
			assert.Equal(t, "local-persistent-object-store-not-production-obs", environment["PLATFORM_LAB_OBJECT_STORE_KIND"])
			assert.Equal(t, environment["HUAWEI_OBS_SECRET_ACCESS_KEY"], environment["PLATFORM_LAB_OBJECT_STORE_SECRET_ACCESS_KEY"])
			assert.NotEqual(t, config.Principal.ArtifactSigningSecret, environment["HUAWEI_OBS_SECRET_ACCESS_KEY"])
			assert.Empty(t, environment["HUAWEI_OBS_SECURITY_TOKEN"])
			caPEM, err := os.ReadFile(environment["PLATFORM_LAB_OBJECT_STORE_CA_FILE"])
			require.NoError(t, err)
			block, _ := pem.Decode(caPEM)
			require.NotNil(t, block)
			ca, err := x509.ParseCertificate(block.Bytes)
			require.NoError(t, err)
			assert.True(t, ca.PermittedDNSDomainsCritical)
			assert.Equal(t, []string{labObjectStoreHost}, ca.PermittedDNSDomains)
			certificatePEM, err := os.ReadFile(environment["PLATFORM_LAB_OBJECT_STORE_TLS_CERT_FILE"])
			require.NoError(t, err)
			block, _ = pem.Decode(certificatePEM)
			require.NotNil(t, block)
			certificate, err := x509.ParseCertificate(block.Bytes)
			require.NoError(t, err)
			assert.Equal(t, []string{labObjectStoreHost, environment["HUAWEI_OBS_BUCKET"] + "." + labObjectStoreHost}, certificate.DNSNames)
			assert.Error(t, certificate.VerifyHostname("api.minimax.cn"), "artifact identity must never impersonate a paid provider")
			assert.Error(t, certificate.VerifyHostname("ark.cn-beijing.volces.com"))
			for key, value := range environment {
				if !strings.HasPrefix(key, "PLATFORM_LAB_") {
					t.Setenv(key, value)
				}
			}
			store, err := service.NewPlatformArtifactStoreFromEnvironment()
			require.NoError(t, err)
			require.IsType(t, &service.PlatformHuaweiOBSArtifactStore{}, store)
			defer store.(*service.PlatformHuaweiOBSArtifactStore).Close()
			objectKey := "outputs/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222/33333333-3333-4333-8333-333333333333"
			download, err := store.IssueSignedDownload(context.Background(), objectKey, 300*time.Second)
			require.NoError(t, err, "SDK signing is local and performs no upload or provider call")
			require.NotNil(t, download.StorageBinding)
			assert.Equal(t, "huawei_obs", download.StorageBinding.Provider)
			assert.Equal(t, labObjectStoreHost, download.StorageBinding.EndpointHost)
			assert.Equal(t, objectKey, download.StorageBinding.ObjectKey)
			assert.Equal(t, 300*time.Second, download.StorageBinding.ExpiresAt.Sub(download.StorageBinding.IssuedAt))
			hash := sha256.Sum256([]byte(download.URL))
			assert.Equal(t, hex.EncodeToString(hash[:]), download.StorageBinding.URLSHA256)
			assert.NotContains(t, download.URL, environment["HUAWEI_OBS_SECRET_ACCESS_KEY"])
			require.NoError(t, configureLabArtifactStorage(options, state, config, environment))
			reused, err := os.ReadFile(environment["PLATFORM_LAB_OBJECT_STORE_TLS_CERT_FILE"])
			require.NoError(t, err)
			assert.Equal(t, certificatePEM, reused, "restart must preserve the existing TLS identity")
		})
	}
}

func TestLocalObjectStorageRejectsMismatchedStateAndIncompleteTLS(t *testing.T) {
	options, state, config, environment := localObjectFixture(t, localvideoconfig.ModeLive)
	state.Manifest.StateID = "live-" + strings.Repeat("0", 64)
	assert.Error(t, configureLabArtifactStorage(options, state, config, environment))
	state.Manifest.StateID = config.StateID
	require.NoError(t, configureLabArtifactStorage(options, state, config, environment))
	require.NoError(t, os.Remove(environment["PLATFORM_LAB_OBJECT_STORE_TLS_KEY_FILE"]))
	assert.Error(t, configureLabArtifactStorage(options, state, config, environment), "do not silently rotate a partially lost storage identity")
}

// This optional offline wire test is run in a disposable node:lts-alpine
// container with --network none and two loopback-only /etc/hosts entries.
// No real provider, OBS endpoint, persistent application DB, or key is used.
func TestLocalObjectStoreSDKWire(t *testing.T) {
	if os.Getenv("LAB_OBJECT_STORE_WIRE_TEST") != "1" {
		t.Skip("set LAB_OBJECT_STORE_WIRE_TEST=1 in the isolated Node+Go test container")
	}
	node, err := exec.LookPath("node")
	require.NoError(t, err)
	script := os.Getenv("LAB_OBJECT_STORE_SCRIPT")
	require.True(t, filepath.IsAbs(script))
	options, state, config, environment := localObjectFixture(t, localvideoconfig.ModeLive)
	require.NoError(t, configureLabArtifactStorage(options, state, config, environment))
	bucket := environment["HUAWEI_OBS_BUCKET"]
	for _, host := range []string{labObjectStoreHost, bucket + "." + labObjectStoreHost} {
		addresses, err := net.LookupIP(host)
		require.NoError(t, err, "wire test host must be explicitly bound to loopback: %s", host)
		require.NotEmpty(t, addresses)
		for _, address := range addresses {
			require.True(t, address.IsLoopback(), "offline object wire test must never contact another host")
		}
	}
	for key, value := range environment {
		if !strings.HasPrefix(key, "PLATFORM_LAB_") {
			t.Setenv(key, value)
		}
	}
	root := filepath.Join(state.Directory, "stored-objects")
	command := exec.Command(node, script)
	command.Env = append(os.Environ(),
		"LAB_OBJECT_STORE_ENVIRONMENT=development", "LAB_OBJECT_STORE_MODE=live", "LAB_OBJECT_STORE_STATE_ID="+config.StateID,
		"LAB_OBJECT_STORE_ROOT="+root, "LAB_OBJECT_STORE_ENDPOINT_HOST="+labObjectStoreHost, "LAB_OBJECT_STORE_BUCKET="+bucket,
		"LAB_OBJECT_STORE_ACCESS_KEY_ID="+environment["HUAWEI_OBS_ACCESS_KEY_ID"], "LAB_OBJECT_STORE_SECRET_ACCESS_KEY="+environment["HUAWEI_OBS_SECRET_ACCESS_KEY"],
		"LAB_OBJECT_STORE_TLS_CERT_FILE="+environment["PLATFORM_LAB_OBJECT_STORE_TLS_CERT_FILE"], "LAB_OBJECT_STORE_TLS_KEY_FILE="+environment["PLATFORM_LAB_OBJECT_STORE_TLS_KEY_FILE"],
		"LAB_OBJECT_STORE_LISTEN_HOST=127.0.0.1", "LAB_OBJECT_STORE_LISTEN_PORT=443")
	stdout, err := command.StdoutPipe()
	require.NoError(t, err)
	var stderr bytes.Buffer
	command.Stderr = &stderr
	require.NoError(t, command.Start())
	t.Cleanup(func() { _ = command.Process.Kill(); _ = command.Wait() })
	ready := make(chan bool, 1)
	go func() {
		scanner := bufio.NewScanner(stdout)
		ready <- scanner.Scan() && strings.Contains(scanner.Text(), config.StateID)
	}()
	select {
	case success := <-ready:
		require.True(t, success, "local TLS object server did not start: %s", stderr.String())
	case <-time.After(10 * time.Second):
		t.Fatal("local TLS object server startup timed out")
	}
	store, err := service.NewPlatformArtifactStoreFromEnvironment()
	require.NoError(t, err)
	defer store.(*service.PlatformHuaweiOBSArtifactStore).Close()
	require.NoError(t, store.Healthcheck(context.Background()))
	content := []byte("actual bytes written through the existing Huawei OBS SDK")
	hash := sha256.Sum256(content)
	key := "outputs/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222/33333333-3333-4333-8333-333333333333"
	stored, err := store.Put(context.Background(), service.PlatformArtifactPutInput{
		ObjectKey: key, Content: bytes.NewReader(content), ContentType: "video/mp4", SizeBytes: int64(len(content)), SHA256: hex.EncodeToString(hash[:]),
	})
	require.NoError(t, err)
	assert.Equal(t, hex.EncodeToString(hash[:]), stored.SHA256)
	download, err := store.IssueSignedDownload(context.Background(), key, 300*time.Second)
	require.NoError(t, err)
	require.NotNil(t, download.StorageBinding)
	ca, err := os.ReadFile(environment["PLATFORM_LAB_OBJECT_STORE_CA_FILE"])
	require.NoError(t, err)
	roots := x509.NewCertPool()
	require.True(t, roots.AppendCertsFromPEM(ca))
	client := &http.Client{Transport: &http.Transport{Proxy: nil, TLSClientConfig: &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12}}, Timeout: 10 * time.Second}
	request, err := http.NewRequest(http.MethodGet, download.URL, nil)
	require.NoError(t, err)
	request.Header.Set("Range", "bytes=0-15")
	response, err := client.Do(request)
	require.NoError(t, err)
	result, err := io.ReadAll(response.Body)
	require.NoError(t, err)
	require.NoError(t, response.Body.Close())
	assert.Equal(t, http.StatusPartialContent, response.StatusCode)
	assert.Equal(t, content[:16], result)
	objectHash := sha256.Sum256([]byte(key))
	persisted, err := os.ReadFile(filepath.Join(root, "objects", hex.EncodeToString(objectHash[:])+".blob"))
	require.NoError(t, err)
	assert.Equal(t, content, persisted)
	require.NoError(t, store.Delete(context.Background(), key))
	missing, err := client.Get(download.URL)
	require.NoError(t, err)
	defer missing.Body.Close()
	assert.Equal(t, http.StatusNotFound, missing.StatusCode)
}
