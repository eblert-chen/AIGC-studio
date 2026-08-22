package common

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReadProtectedSecretFileRejectsOwnerOnlyFileOnWritableLinuxFilesystem(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux mount flags are the protected deployment contract")
	}
	path := filepath.Join(t.TempDir(), "secret")
	require.NoError(t, os.WriteFile(path, []byte("non-secret-test-value"), 0o400))
	t.Setenv("TEST_PROTECTED_SECRET_FILE", path)
	_, err := ReadProtectedSecretFile("TEST_PROTECTED_SECRET_FILE", 1024)
	require.ErrorContains(t, err, "read-only filesystem")
}

func TestReadProtectedSecretFileAcceptsOwnerOnlyFileOnReadOnlyLinuxMount(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux mount flags are the protected deployment contract")
	}
	path := os.Getenv("TEST_PROTECTED_READ_ONLY_SECRET_FILE")
	if path == "" {
		t.Skip("set TEST_PROTECTED_READ_ONLY_SECRET_FILE to exercise a real read-only mount")
	}
	raw, err := ReadProtectedSecretFile("TEST_PROTECTED_READ_ONLY_SECRET_FILE", 64*1024)
	require.NoError(t, err)
	require.NotEmpty(t, raw)
	clear(raw)
}

func TestInstallProtectedSecretFileSnapshotsPinsOneStrictReadForLaterUse(t *testing.T) {
	path := filepath.Join(t.TempDir(), "source-that-must-not-be-reopened")
	t.Setenv("TEST_PROTECTED_SECRET_SNAPSHOT", path)
	original := []byte("synthetic-snapshot-value-with-high-diversity-0123456789")
	require.NoError(t, InstallProtectedSecretFileSnapshots([]ProtectedSecretFileSnapshot{{
		Environment: "TEST_PROTECTED_SECRET_SNAPSHOT",
		Value:       original,
	}}))

	// The installer owns a copy, not the caller's mutable buffer. There is no
	// file at path, so a successful read also proves that no second open occurs.
	original[0] = 'X'
	actual, err := ReadProtectedSecretFile("TEST_PROTECTED_SECRET_SNAPSHOT", 1024)
	require.NoError(t, err)
	require.Equal(t, "synthetic-snapshot-value-with-high-diversity-0123456789", string(actual))
	clear(actual)

	alias := "TEST_PROTECTED_SECRET_SNAPSHOT_ALIAS"
	t.Setenv(alias, path)
	actual, err = ReadProtectedSecretFile(alias, 1024)
	require.NoError(t, err)
	require.Equal(t, "synthetic-snapshot-value-with-high-diversity-0123456789", string(actual))
	clear(actual)
}

func TestInstallProtectedSecretFileSnapshotsIsAtomicAndImmutable(t *testing.T) {
	firstPath := filepath.Join(t.TempDir(), "first")
	secondPath := filepath.Join(t.TempDir(), "second")
	t.Setenv("TEST_PROTECTED_SECRET_SNAPSHOT_FIRST", firstPath)
	t.Setenv("TEST_PROTECTED_SECRET_SNAPSHOT_SECOND", secondPath)
	first := []byte("first-synthetic-snapshot-value-0123456789")
	second := []byte("second-synthetic-snapshot-value-9876543210")
	require.NoError(t, InstallProtectedSecretFileSnapshots([]ProtectedSecretFileSnapshot{
		{Environment: "TEST_PROTECTED_SECRET_SNAPSHOT_FIRST", Value: first},
		{Environment: "TEST_PROTECTED_SECRET_SNAPSHOT_SECOND", Value: second},
	}))

	conflicting := append([]byte(nil), first...)
	conflicting[0] = 'X'
	require.EqualError(t, InstallProtectedSecretFileSnapshots([]ProtectedSecretFileSnapshot{
		{Environment: "TEST_PROTECTED_SECRET_SNAPSHOT_FIRST", Value: conflicting},
		{Environment: "TEST_PROTECTED_SECRET_SNAPSHOT_SECOND", Value: second},
	}), "protected secret file snapshot is immutable")

	actual, err := ReadProtectedSecretFile("TEST_PROTECTED_SECRET_SNAPSHOT_FIRST", 1024)
	require.NoError(t, err)
	require.Equal(t, first, actual)
	clear(actual)
	require.NoError(t, InstallProtectedSecretFileSnapshots([]ProtectedSecretFileSnapshot{
		{Environment: "TEST_PROTECTED_SECRET_SNAPSHOT_FIRST", Value: first},
		{Environment: "TEST_PROTECTED_SECRET_SNAPSHOT_SECOND", Value: second},
	}))
}
