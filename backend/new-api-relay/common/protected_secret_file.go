package common

import (
	"crypto/subtle"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

const protectedSecretFileSnapshotMaximumBytes = 16 * 1024 * 1024

// ProtectedSecretFileSnapshot is one value returned by a strict protected-file
// read and approved by a higher-level cross-file proof. Installation copies the
// value; callers must clear their source buffer after installation.
type ProtectedSecretFileSnapshot struct {
	Environment string
	Value       []byte
}

var protectedSecretFileSnapshots = struct {
	sync.RWMutex
	byPath map[string][]byte
}{byPath: make(map[string][]byte)}

func protectedSecretFilePath(environment string, maximumBytes int64) (string, error) {
	path := os.Getenv(environment)
	if path == "" || strings.TrimSpace(path) != path || strings.ContainsAny(path, "\x00\r\n") ||
		!filepath.IsAbs(path) || filepath.Clean(path) != path || maximumBytes < 1 {
		return "", fmt.Errorf("%s path is invalid", environment)
	}
	return path, nil
}

// InstallProtectedSecretFileSnapshots atomically pins the exact byte slices
// that a higher-level commitment verifier just approved. Subsequent protected
// reads of the same mounted path return a copy of these bytes instead of
// reopening the bind mount, so verification and use share one immutable input.
// Exact replay is allowed; a different value for an already pinned path fails.
func InstallProtectedSecretFileSnapshots(snapshots []ProtectedSecretFileSnapshot) error {
	if len(snapshots) == 0 {
		return fmt.Errorf("protected secret file snapshot set is empty")
	}
	candidates := make(map[string][]byte, len(snapshots))
	for _, snapshot := range snapshots {
		path, err := protectedSecretFilePath(snapshot.Environment, int64(len(snapshot.Value)))
		if err != nil || len(snapshot.Value) == 0 || len(snapshot.Value) > protectedSecretFileSnapshotMaximumBytes {
			return fmt.Errorf("protected secret file snapshot is invalid")
		}
		if previous, duplicate := candidates[path]; duplicate {
			if len(previous) != len(snapshot.Value) || subtle.ConstantTimeCompare(previous, snapshot.Value) != 1 {
				return fmt.Errorf("protected secret file snapshot path is ambiguous")
			}
			continue
		}
		candidates[path] = snapshot.Value
	}

	protectedSecretFileSnapshots.Lock()
	defer protectedSecretFileSnapshots.Unlock()
	for path, value := range candidates {
		if existing, present := protectedSecretFileSnapshots.byPath[path]; present &&
			(len(existing) != len(value) || subtle.ConstantTimeCompare(existing, value) != 1) {
			return fmt.Errorf("protected secret file snapshot is immutable")
		}
	}
	for path, value := range candidates {
		if _, present := protectedSecretFileSnapshots.byPath[path]; !present {
			protectedSecretFileSnapshots.byPath[path] = append([]byte(nil), value...)
		}
	}
	return nil
}

func protectedSecretFileInstalledSnapshot(path string, maximumBytes int64) ([]byte, bool, error) {
	protectedSecretFileSnapshots.RLock()
	value, present := protectedSecretFileSnapshots.byPath[path]
	if !present {
		protectedSecretFileSnapshots.RUnlock()
		return nil, false, nil
	}
	if int64(len(value)) > maximumBytes {
		protectedSecretFileSnapshots.RUnlock()
		return nil, true, fmt.Errorf("protected secret file snapshot exceeds its read bound")
	}
	result := append([]byte(nil), value...)
	protectedSecretFileSnapshots.RUnlock()
	return result, true, nil
}

// ReadProtectedSecretFile reads one protected bind mount without following a
// symlink or accepting a path/inode swap. Linux additionally requires the file
// to be owned by the process uid with mode 0400/0600; the mount itself must be
// read-only so even an owner-writable host mode cannot be used in-container.
func ReadProtectedSecretFile(environment string, maximumBytes int64) ([]byte, error) {
	path, err := protectedSecretFilePath(environment, maximumBytes)
	if err != nil {
		return nil, err
	}
	if snapshot, installed, snapshotErr := protectedSecretFileInstalledSnapshot(path, maximumBytes); installed {
		return snapshot, snapshotErr
	}
	lstat, err := os.Lstat(path)
	if err != nil || !lstat.Mode().IsRegular() || lstat.Size() < 1 || lstat.Size() > maximumBytes {
		return nil, fmt.Errorf("%s is unavailable or invalid", environment)
	}
	if !protectedSecretFileHasOwnerOnlyMode(lstat) {
		return nil, fmt.Errorf("%s must not be group or world readable", environment)
	}
	if !protectedSecretFileOwnedByEffectiveUser(lstat) {
		return nil, fmt.Errorf("%s must use an owner-only file identity and mode", environment)
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("%s could not be read", environment)
	}
	defer file.Close()
	fstat, err := file.Stat()
	if err != nil || !fstat.Mode().IsRegular() || !os.SameFile(lstat, fstat) ||
		fstat.Size() < 1 || fstat.Size() > maximumBytes ||
		!protectedSecretFileHasOwnerOnlyMode(fstat) ||
		!protectedSecretFileOwnedByEffectiveUser(fstat) {
		return nil, fmt.Errorf("%s changed while it was opened", environment)
	}
	if !protectedSecretFileFilesystemReadOnly(file) {
		return nil, fmt.Errorf("%s must be mounted on a read-only filesystem", environment)
	}
	if writable, openErr := os.OpenFile(path, os.O_WRONLY, 0); openErr == nil {
		_ = writable.Close()
		return nil, fmt.Errorf("%s must be mounted read-only", environment)
	}
	pathStat, err := os.Lstat(path)
	if err != nil || !pathStat.Mode().IsRegular() || !os.SameFile(pathStat, fstat) ||
		!protectedSecretFileOwnedByEffectiveUser(pathStat) {
		return nil, fmt.Errorf("%s changed during its read-only check", environment)
	}
	raw, err := io.ReadAll(io.LimitReader(file, maximumBytes+1))
	if err != nil || int64(len(raw)) != fstat.Size() || int64(len(raw)) > maximumBytes {
		clear(raw)
		return nil, fmt.Errorf("%s could not be read", environment)
	}
	return raw, nil
}
