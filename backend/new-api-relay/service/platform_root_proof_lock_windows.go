//go:build windows

package service

import (
	"errors"
	"os"
	"path/filepath"
	"time"

	"golang.org/x/sys/windows"
)

type platformRelayRootProofStateLock struct {
	file          *os.File
	directory     *os.File
	directoryInfo os.FileInfo
	overlapped    windows.Overlapped
}

func platformRelayRootIsolationAcquireProofStateLock(proofPath string) (*platformRelayRootProofStateLock, error) {
	if proofPath == "" || !filepath.IsAbs(proofPath) || filepath.Clean(proofPath) != proofPath {
		return nil, errors.New("Relay root isolation proof path is invalid")
	}
	parentPath := filepath.Dir(proofPath)
	parentBefore, err := os.Lstat(parentPath)
	if err != nil || parentBefore.Mode()&os.ModeSymlink != 0 || !parentBefore.IsDir() {
		return nil, errors.New("Relay root isolation proof lock is invalid")
	}
	parent, err := os.Open(parentPath)
	if err != nil {
		return nil, errors.New("Relay root isolation proof lock is unavailable")
	}
	parentAfter, err := parent.Stat()
	if err != nil || !parentAfter.IsDir() || !os.SameFile(parentBefore, parentAfter) {
		_ = parent.Close()
		return nil, errors.New("Relay root isolation proof lock is invalid")
	}
	lockPath := filepath.Join(parentPath, platformRelayRootProofLockFileName)
	before, err := os.Lstat(lockPath)
	if err != nil || before.Mode()&os.ModeSymlink != 0 || !before.Mode().IsRegular() || before.Size() != 0 {
		_ = parent.Close()
		return nil, errors.New("Relay root isolation proof lock is invalid")
	}
	file, err := os.Open(lockPath)
	if err != nil {
		_ = parent.Close()
		return nil, errors.New("Relay root isolation proof lock is unavailable")
	}
	after, err := file.Stat()
	if err != nil || !after.Mode().IsRegular() || !os.SameFile(before, after) || after.Size() != 0 {
		_ = file.Close()
		_ = parent.Close()
		return nil, errors.New("Relay root isolation proof lock is invalid")
	}
	lock := &platformRelayRootProofStateLock{file: file, directory: parent, directoryInfo: parentAfter}
	deadline := time.Now().Add(platformRelayRootProofLockTimeout)
	for {
		err = windows.LockFileEx(
			windows.Handle(file.Fd()),
			windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY,
			0, 1, 0, &lock.overlapped,
		)
		if err == nil {
			break
		}
		if !errors.Is(err, windows.ERROR_LOCK_VIOLATION) {
			_ = file.Close()
			_ = parent.Close()
			return nil, errors.New("Relay root isolation proof lock could not be acquired")
		}
		if !platformRelayRootProofLockWait(deadline) {
			_ = file.Close()
			_ = parent.Close()
			return nil, errors.New("Relay root isolation proof lock acquisition timed out")
		}
	}
	return lock, nil
}

func (lock *platformRelayRootProofStateLock) release() error {
	if lock == nil || lock.file == nil {
		return nil
	}
	unlockErr := windows.UnlockFileEx(windows.Handle(lock.file.Fd()), 0, 1, 0, &lock.overlapped)
	closeErr := lock.file.Close()
	directoryCloseErr := lock.directory.Close()
	lock.file = nil
	lock.directory = nil
	if unlockErr != nil {
		return unlockErr
	}
	if closeErr != nil {
		return closeErr
	}
	return directoryCloseErr
}
