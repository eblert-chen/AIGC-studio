package main

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

func TestOfflineSignerProducesStableVerifiableEvidenceWithoutLeakingPrivateKey(t *testing.T) {
	directory := t.TempDir()
	seed := bytes.Repeat([]byte{0x31}, ed25519.SeedSize)
	privateKey := ed25519.NewKeyFromSeed(seed)
	privatePath := filepath.Join(directory, "acceptance.ed25519")
	require.NoError(t, os.WriteFile(privatePath, []byte(base64.StdEncoding.EncodeToString(seed)+"\n"), 0o600))

	route := service.PlatformRelayRouteDeclaration{
		RouteID: "route-a", ProviderName: "provider-a", AccountID: "account-a",
		ChannelID: 42, NativeChannelType: constant.ChannelTypeKling, KeyIndex: 0,
		KeyFingerprint: strings.Repeat("a", 64), ChannelClass: service.PlatformChannelClassOfficial,
		UpstreamModel: "provider-video-v1", RPMLimit: 10, ActiveTaskLimit: 2,
		Capabilities: dto.PlatformGenerationCapabilities{
			SchemaVersion: 1,
			Modes: map[string]dto.PlatformModeCapability{
				"text_to_video": {
					InputMediaTypes: []string{}, SupportsFace: false, RequiredResourceKeys: []string{},
					Limits: dto.PlatformCapabilityLimits{
						MaxPromptLength: 1000, DurationSeconds: []int{5}, AspectRatios: []string{"16:9"},
						Resolutions: []string{"720p"}, OutputCounts: []int{1},
					},
				},
			},
		},
	}
	routesPath := filepath.Join(directory, "reviewed-routes.json")
	routesJSON, err := json.Marshal(map[string][]service.PlatformRelayRouteDeclaration{"video-model": {route}})
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(routesPath, routesJSON, 0o600))

	options := signerOptions{
		routesFile: routesPath, privateKeyFile: privatePath, keyID: "release-test",
		releaseID: "11111111-2222-4333-8444-555555555555", environment: "production",
		sourceRevision: strings.Repeat("b", 40), sourceSnapshotSHA256: "sha256:" + strings.Repeat("d", 64),
		imageDigest: "sha256:" + strings.Repeat("c", 64),
		notBefore:   "2026-08-15T00:00:00Z", notAfter: "2026-08-16T00:00:00Z",
	}
	first, err := signRoutes(options)
	require.NoError(t, err)
	second, err := signRoutes(options)
	require.NoError(t, err)
	require.Equal(t, first, second)
	require.NotContains(t, string(first), base64.StdEncoding.EncodeToString(seed))

	var signed map[string][]service.PlatformRelayRouteDeclaration
	require.NoError(t, json.Unmarshal(first, &signed))
	evidence := signed["video-model"][0].Acceptance
	require.NotNil(t, evidence)
	payload, err := service.PlatformRouteAcceptanceSignaturePayload(evidence.Manifest)
	require.NoError(t, err)
	signature, err := base64.StdEncoding.DecodeString(evidence.Signature)
	require.NoError(t, err)
	require.True(t, ed25519.Verify(privateKey.Public().(ed25519.PublicKey), payload, signature))
}

func TestOfflineSignerRejectsRelativeAndSymlinkPrivateKeyPaths(t *testing.T) {
	_, err := loadEd25519PrivateKey("relative.key")
	require.ErrorContains(t, err, "absolute")

	directory := t.TempDir()
	target := filepath.Join(directory, "target.key")
	require.NoError(t, os.WriteFile(target, []byte(base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{1}, ed25519.SeedSize))), 0o600))
	link := filepath.Join(directory, "link.key")
	if err := os.Symlink(target, link); err != nil {
		t.Skipf("symlink creation is unavailable: %v", err)
	}
	_, err = loadEd25519PrivateKey(link)
	require.ErrorContains(t, err, "non-symlink")
}

func TestOfflineSignerRejectsGroupOrWorldReadablePrivateKeyOnPOSIX(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits are not authoritative on Windows")
	}
	directory := t.TempDir()
	privatePath := filepath.Join(directory, "readable.key")
	require.NoError(t, os.WriteFile(
		privatePath,
		[]byte(base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{1}, ed25519.SeedSize))),
		0o644,
	))
	require.NoError(t, os.Chmod(privatePath, 0o644))

	_, err := loadEd25519PrivateKey(privatePath)
	require.ErrorContains(t, err, "group- or world-readable")
}
