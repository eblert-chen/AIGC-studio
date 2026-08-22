//go:build !linux && !windows

package service

import (
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

type platformRelayRootProofStateLock struct {
	file          *os.File
	directory     *os.File
	directoryInfo os.FileInfo
}

func platformRelayRootIsolationAcquireProofStateLock(proofPath string) (*platformRelayRootProofStateLock, error) {
	if proofPath == "" || !filepath.IsAbs(proofPath) || filepath.Clean(proofPath) != proofPath {
		return nil, errors.New("Relay root isolation proof path is invalid")
	}
	parentPath := filepath.Dir(proofPath)
	parentBefore, err := os.Lstat(parentPath)
	if err != nil || parentBefore.Mode()&os.ModeSymlink != 0 || !parentBefore.IsDir() ||
		parentBefore.Mode().Perm() != 0o700 {
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
	parentStat, ok := parentAfter.Sys().(*syscall.Stat_t)
	if !ok || int(parentStat.Uid) != os.Geteuid() || parentAfter.Mode().Perm() != 0o700 {
		_ = parent.Close()
		return nil, errors.New("Relay root isolation proof lock is invalid")
	}
	fd, err := unix.Openat(
		int(parent.Fd()), platformRelayRootProofLockFileName,
		unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0,
	)
	if err != nil {
		_ = parent.Close()
		return nil, errors.New("Relay root isolation proof lock is unavailable")
	}
	file := os.NewFile(uintptr(fd), platformRelayRootProofLockFileName)
	if file == nil {
		_ = unix.Close(fd)
		_ = parent.Close()
		return nil, errors.New("Relay root isolation proof lock is unavailable")
	}
	after, err := file.Stat()
	if err != nil {
		_ = file.Close()
		_ = parent.Close()
		return nil, errors.New("Relay root isolation proof lock is invalid")
	}
	stat, ok := after.Sys().(*syscall.Stat_t)
	if !after.Mode().IsRegular() ||
		after.Mode().Perm() != 0o600 || after.Size() != 0 || !ok || int(stat.Uid) != os.Geteuid() {
		_ = file.Close()
		_ = parent.Close()
		return nil, errors.New("Relay root isolation proof lock is invalid")
	}
	deadline := time.Now().Add(platformRelayRootProofLockTimeout)
	for {
		err = unix.Flock(int(file.Fd()), unix.LOCK_EX|unix.LOCK_NB)
		if err == nil {
			break
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
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
	return &platformRelayRootProofStateLock{file: file, directory: parent, directoryInfo: parentAfter}, nil
}

func (lock *platformRelayRootProofStateLock) release() error {
	if lock == nil || lock.file == nil {
		return nil
	}
	unlockErr := unix.Flock(int(lock.file.Fd()), unix.LOCK_UN)
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
