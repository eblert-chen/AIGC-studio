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
	"time"

	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
	"github.com/stretchr/testify/require"
)

func reviewedModelRelease(t *testing.T) generationrelease.Release {
	t.Helper()
	profile, ok := generationprofile.Get(generationprofile.VolcengineArkImageGenerationV1)
	require.True(t, ok)
	return generationrelease.Release{
		APIVersion:             generationrelease.APIVersion,
		Kind:                   generationrelease.Kind,
		ReleaseID:              "seedream-5-r1",
		PublicModelID:          "seedream-5",
		ProviderModelID:        "doubao-seedream-5-0-260128",
		AdapterProfileID:       profile.ID,
		AdapterProfileRevision: profile.Revision,
		LegacyPublicAliases:    []string{"seedream-5-lite"},
		Capability:             profile.Capability,
		Audit: generationrelease.Audit{
			CreatedAt: "2026-08-29T00:00:00Z",
			CreatedBy: "release-authority",
			Reason:    "reviewed real-provider Seedream release",
			SourceRef: "acceptance/seedream/2026-08-29",
		},
	}
}

func writeModelReleaseSignerFixture(t *testing.T, directory string, name string, value []byte, mode os.FileMode) string {
	t.Helper()
	path := filepath.Join(directory, name)
	require.NoError(t, os.WriteFile(path, value, mode))
	if runtime.GOOS != "windows" {
		require.NoError(t, os.Chmod(path, mode))
	}
	return path
}

func TestOfflineModelReleaseSignerProducesDeterministicVerifiableEvidence(t *testing.T) {
	directory := t.TempDir()
	releaseBytes, err := json.Marshal(reviewedModelRelease(t))
	require.NoError(t, err)
	releasePath := writeModelReleaseSignerFixture(t, directory, "reviewed-release.json", releaseBytes, 0o600)
	seed := bytes.Repeat([]byte{0x42}, ed25519.SeedSize)
	privateKey := ed25519.NewKeyFromSeed(seed)
	privatePath := writeModelReleaseSignerFixture(
		t,
		directory,
		"model-release.ed25519",
		[]byte(base64.StdEncoding.EncodeToString(seed)+"\n"),
		0o600,
	)
	options := signerOptions{
		releaseFile: releasePath, privateKeyFile: privatePath, keyID: "release-2026-q3",
		signedAt: "2026-08-29T01:00:00Z", notAfter: "2026-09-29T01:00:00Z",
	}

	first, err := signModelRelease(options)
	require.NoError(t, err)
	second, err := signModelRelease(options)
	require.NoError(t, err)
	require.Equal(t, first, second)
	require.NotContains(t, string(first), base64.StdEncoding.EncodeToString(seed))

	signed, err := generationrelease.DecodeStrict(first)
	require.NoError(t, err)
	require.NotNil(t, signed.Attestation)
	publicKey := privateKey.Public().(ed25519.PublicKey)
	require.NoError(t, signed.VerifyAttestation(
		map[string]ed25519.PublicKey{"release-2026-q3": publicKey},
		time.Date(2026, 8, 30, 0, 0, 0, 0, time.UTC),
	))

	tampered := signed
	tampered.ProviderModelID = "tampered-provider-model"
	require.ErrorContains(t, tampered.VerifyAttestation(
		map[string]ed25519.PublicKey{"release-2026-q3": publicKey},
		time.Date(2026, 8, 30, 0, 0, 0, 0, time.UTC),
	), "verification failed")
	require.ErrorContains(t, signed.VerifyAttestation(
		map[string]ed25519.PublicKey{"release-2026-q3": publicKey},
		time.Date(2026, 9, 29, 1, 0, 0, 0, time.UTC),
	), "validity window")
}

func TestOfflineModelReleaseSignerRejectsUnknownFieldsAndExistingAttestation(t *testing.T) {
	directory := t.TempDir()
	release := reviewedModelRelease(t)
	raw, err := json.Marshal(release)
	require.NoError(t, err)
	var document map[string]any
	require.NoError(t, json.Unmarshal(raw, &document))
	document["route_weight"] = 10
	unknown, err := json.Marshal(document)
	require.NoError(t, err)
	unknownPath := writeModelReleaseSignerFixture(t, directory, "unknown.json", unknown, 0o600)

	_, err = signModelRelease(signerOptions{releaseFile: unknownPath})
	require.ErrorContains(t, err, "invalid")
	require.NotContains(t, err.Error(), string(unknown))

	release.Attestation = &generationrelease.Attestation{}
	attested, err := json.Marshal(release)
	require.NoError(t, err)
	attestedPath := writeModelReleaseSignerFixture(t, directory, "already-signed.json", attested, 0o600)
	_, err = signModelRelease(signerOptions{releaseFile: attestedPath})
	require.ErrorContains(t, err, "must be unsigned")
}

func TestOfflineModelReleaseSignerRejectsInvalidWindowsAndExcessLifetime(t *testing.T) {
	directory := t.TempDir()
	releaseBytes, err := json.Marshal(reviewedModelRelease(t))
	require.NoError(t, err)
	releasePath := writeModelReleaseSignerFixture(t, directory, "reviewed-release.json", releaseBytes, 0o600)
	seed := bytes.Repeat([]byte{0x51}, ed25519.SeedSize)
	privatePath := writeModelReleaseSignerFixture(
		t,
		directory,
		"model-release.ed25519",
		[]byte(base64.StdEncoding.EncodeToString(seed)),
		0o600,
	)
	base := signerOptions{
		releaseFile: releasePath, privateKeyFile: privatePath, keyID: "release-2026-q3",
		signedAt: "2026-08-29T01:00:00Z", notAfter: "2026-09-29T01:00:00Z",
	}

	beforeAudit := base
	beforeAudit.signedAt = "2026-08-28T23:59:59Z"
	_, err = signModelRelease(beforeAudit)
	require.ErrorContains(t, err, "must not precede")

	nonCanonical := base
	nonCanonical.signedAt = "2026-08-29T01:00:00+00:00"
	_, err = signModelRelease(nonCanonical)
	require.ErrorContains(t, err, "canonical UTC RFC3339")

	tooLong := base
	tooLong.notAfter = "2027-08-30T01:00:01Z"
	_, err = signModelRelease(tooLong)
	require.ErrorContains(t, err, "window")
}

func TestOfflineModelReleaseSignerRejectsRelativeSymlinkAndFileIdentityRace(t *testing.T) {
	_, err := loadModelReleaseSignerPrivateKey("relative.key")
	require.ErrorContains(t, err, "absolute")

	directory := t.TempDir()
	seed := bytes.Repeat([]byte{0x61}, ed25519.SeedSize)
	target := writeModelReleaseSignerFixture(
		t,
		directory,
		"target.key",
		[]byte(base64.StdEncoding.EncodeToString(seed)),
		0o600,
	)
	link := filepath.Join(directory, "link.key")
	if err := os.Symlink(target, link); err == nil {
		_, err = loadModelReleaseSignerPrivateKey(link)
		require.ErrorContains(t, err, "non-symlink")
	}

	original := writeModelReleaseSignerFixture(t, directory, "original.json", []byte(`{"value":"original"}`), 0o600)
	replacement := writeModelReleaseSignerFixture(t, directory, "replacement.json", []byte(`{"value":"replacement"}`), 0o600)
	previousOpen := openModelReleaseSignerFile
	openModelReleaseSignerFile = func(string) (*os.File, error) { return os.Open(replacement) }
	t.Cleanup(func() { openModelReleaseSignerFile = previousOpen })
	_, err = readModelReleaseSignerFile(original, 1024, false)
	require.ErrorContains(t, err, "identity changed")
}

func TestOfflineModelReleaseSignerRejectsPostOpenFileReplacement(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("atomic replacement of an open file is not portable on Windows")
	}
	directory := t.TempDir()
	original := writeModelReleaseSignerFixture(t, directory, "reviewed.json", []byte(`{"value":"original"}`), 0o600)
	replacement := writeModelReleaseSignerFixture(t, directory, "replacement.json", []byte(`{"value":"replacement"}`), 0o600)
	previousHook := afterModelReleaseSignerFileRead
	afterModelReleaseSignerFileRead = func(path string) {
		require.NoError(t, os.Rename(replacement, path))
	}
	t.Cleanup(func() { afterModelReleaseSignerFileRead = previousHook })

	_, err := readModelReleaseSignerFile(original, 1024, false)
	require.ErrorContains(t, err, "changed while reading")
}

func TestOfflineModelReleaseSignerRejectsPrivateKeyPermissionDriftAfterRead(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits are not authoritative on Windows")
	}
	directory := t.TempDir()
	privatePath := writeModelReleaseSignerFixture(
		t,
		directory,
		"permission-drift.key",
		[]byte(base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{1}, ed25519.SeedSize))),
		0o600,
	)
	previousHook := afterModelReleaseSignerFileRead
	afterModelReleaseSignerFileRead = func(path string) {
		require.NoError(t, os.Chmod(path, 0o644))
	}
	t.Cleanup(func() { afterModelReleaseSignerFileRead = previousHook })

	_, err := loadModelReleaseSignerPrivateKey(privatePath)
	require.ErrorContains(t, err, "group- or world-readable")
}

func TestOfflineModelReleaseSignerRejectsReadableOrMalformedPrivateKeyWithoutLeakingIt(t *testing.T) {
	directory := t.TempDir()
	secretMarker := "not-a-private-key-secret-marker"
	malformed := writeModelReleaseSignerFixture(t, directory, "malformed.key", []byte(secretMarker), 0o600)
	_, err := loadModelReleaseSignerPrivateKey(malformed)
	require.Error(t, err)
	require.NotContains(t, err.Error(), secretMarker)

	if runtime.GOOS == "windows" {
		return
	}
	readable := writeModelReleaseSignerFixture(
		t,
		directory,
		"readable.key",
		[]byte(base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{1}, ed25519.SeedSize))),
		0o644,
	)
	_, err = loadModelReleaseSignerPrivateKey(readable)
	require.ErrorContains(t, err, "group- or world-readable")
}

func TestOfflineModelReleaseSignerRejectsInvalidKeyID(t *testing.T) {
	directory := t.TempDir()
	releaseBytes, err := json.Marshal(reviewedModelRelease(t))
	require.NoError(t, err)
	releasePath := writeModelReleaseSignerFixture(t, directory, "reviewed-release.json", releaseBytes, 0o600)
	seed := bytes.Repeat([]byte{0x71}, ed25519.SeedSize)
	privatePath := writeModelReleaseSignerFixture(
		t,
		directory,
		"model-release.ed25519",
		[]byte(base64.StdEncoding.EncodeToString(seed)),
		0o600,
	)
	_, err = signModelRelease(signerOptions{
		releaseFile: releasePath, privateKeyFile: privatePath, keyID: strings.Repeat("x", 65),
		signedAt: "2026-08-29T01:00:00Z", notAfter: "2026-09-29T01:00:00Z",
	})
	require.ErrorContains(t, err, "signed model release is invalid")
}
