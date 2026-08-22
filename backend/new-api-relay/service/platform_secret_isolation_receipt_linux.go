//go:build linux

package service

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"syscall"

	"golang.org/x/sys/unix"
)

func platformRelayRootIsolationProofPathAbsent(path string) bool {
	if path == "" || !filepath.IsAbs(path) || filepath.Clean(path) != path {
		return false
	}
	if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
		return false
	}
	parent := filepath.Dir(path)
	before, err := os.Lstat(parent)
	if err != nil || before.Mode()&os.ModeSymlink != 0 || !before.IsDir() || before.Mode().Perm() != 0o700 {
		return false
	}
	handle, err := os.Open(parent)
	if err != nil {
		return false
	}
	defer handle.Close()
	after, err := handle.Stat()
	if err != nil || !after.IsDir() || !os.SameFile(before, after) {
		return false
	}
	stat, ok := after.Sys().(*syscall.Stat_t)
	if !ok || int(stat.Uid) != os.Geteuid() {
		return false
	}
	var filesystem unix.Statfs_t
	if err := unix.Fstatfs(int(handle.Fd()), &filesystem); err != nil {
		return false
	}
	return filesystem.Flags&unix.ST_RDONLY != 0
}

type platformRelaySecretIsolationReceiptDirectory struct {
	path   string
	handle *os.File
	info   os.FileInfo
}

func platformRelaySecretIsolationOpenReceiptDirectory(path string) (*platformRelaySecretIsolationReceiptDirectory, error) {
	pathInfo, err := os.Lstat(path)
	if err != nil || pathInfo.Mode()&os.ModeSymlink != 0 || !pathInfo.IsDir() {
		return nil, errors.New("receipt directory is invalid")
	}
	handle, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	openedInfo, err := handle.Stat()
	if err != nil || !openedInfo.IsDir() || !os.SameFile(pathInfo, openedInfo) {
		_ = handle.Close()
		return nil, errors.New("receipt directory changed while opening")
	}
	stat, ok := openedInfo.Sys().(*syscall.Stat_t)
	if !ok || openedInfo.Mode().Perm() != 0o700 || int(stat.Uid) != os.Geteuid() {
		_ = handle.Close()
		return nil, errors.New("receipt directory ownership or mode is invalid")
	}
	return &platformRelaySecretIsolationReceiptDirectory{path: path, handle: handle, info: openedInfo}, nil
}

func (directory *platformRelaySecretIsolationReceiptDirectory) close() error {
	if directory == nil || directory.handle == nil {
		return nil
	}
	return directory.handle.Close()
}

func (directory *platformRelaySecretIsolationReceiptDirectory) remove(name string) error {
	if directory == nil || directory.handle == nil ||
		(name != "receipt.json" && name != ".receipt.json.tmp" && name != "proof.json" &&
			!platformRelayRootProofTemporaryNameValid(name)) {
		return errors.New("receipt path is invalid")
	}
	return unix.Unlinkat(int(directory.handle.Fd()), name, 0)
}

func (directory *platformRelaySecretIsolationReceiptDirectory) readRootProof(maximumBytes int64) ([]byte, error) {
	if directory == nil || directory.handle == nil || maximumBytes <= 0 {
		return nil, errors.New("root proof directory is invalid")
	}
	fd, err := unix.Openat(
		int(directory.handle.Fd()), "proof.json",
		unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW,
		0,
	)
	if err != nil {
		return nil, err
	}
	file := os.NewFile(uintptr(fd), "proof.json")
	if file == nil {
		_ = unix.Close(fd)
		return nil, errors.New("root proof could not be opened")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != 0o600 || info.Size() <= 0 || info.Size() > maximumBytes {
		return nil, errors.New("root proof is invalid")
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || int(stat.Uid) != os.Geteuid() {
		return nil, errors.New("root proof ownership is invalid")
	}
	raw, err := io.ReadAll(io.LimitReader(file, maximumBytes+1))
	if err != nil || len(raw) == 0 || int64(len(raw)) > maximumBytes {
		clear(raw)
		return nil, errors.New("root proof is invalid")
	}
	return raw, nil
}

func (directory *platformRelaySecretIsolationReceiptDirectory) sync() error {
	if directory == nil || directory.handle == nil {
		return errors.New("receipt directory is invalid")
	}
	return unix.Fsync(int(directory.handle.Fd()))
}

func (directory *platformRelaySecretIsolationReceiptDirectory) writeReceipt(receipt any) error {
	if directory == nil || directory.handle == nil {
		return errors.New("Relay secret isolation receipt directory is invalid")
	}
	encoded, err := json.Marshal(receipt)
	if err != nil {
		return errors.New("Relay secret isolation receipt could not be encoded")
	}
	defer clear(encoded)
	const temporaryName = ".receipt.json.tmp"
	const finalName = "receipt.json"
	_ = directory.remove(temporaryName)
	fd, err := unix.Openat(
		int(directory.handle.Fd()), temporaryName,
		unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_CLOEXEC|unix.O_NOFOLLOW,
		0o600,
	)
	if err != nil {
		return errors.New("Relay secret isolation receipt could not be created")
	}
	file := os.NewFile(uintptr(fd), temporaryName)
	if file == nil {
		_ = unix.Close(fd)
		_ = directory.remove(temporaryName)
		return errors.New("Relay secret isolation receipt could not be created")
	}
	_, writeErr := file.Write(encoded)
	if writeErr == nil {
		writeErr = file.Sync()
	}
	if writeErr == nil {
		writeErr = file.Chmod(0o400)
	}
	closeErr := file.Close()
	if writeErr != nil || closeErr != nil {
		_ = directory.remove(temporaryName)
		return errors.New("Relay secret isolation receipt could not be committed")
	}
	if err := unix.Renameat(
		int(directory.handle.Fd()), temporaryName,
		int(directory.handle.Fd()), finalName,
	); err != nil {
		_ = directory.remove(temporaryName)
		return errors.New("Relay secret isolation receipt could not be committed")
	}
	if err := directory.sync(); err != nil {
		return errors.New("Relay secret isolation receipt directory could not be synchronized")
	}
	return nil
}

func (directory *platformRelaySecretIsolationReceiptDirectory) writeRootProof(proof platformRelayRootIsolationProof) error {
	if directory == nil || directory.handle == nil {
		return errors.New("Relay root isolation proof directory is invalid")
	}
	encoded, err := json.Marshal(proof)
	if err != nil {
		return errors.New("Relay root isolation proof could not be encoded")
	}
	defer clear(encoded)
	const finalName = "proof.json"
	temporaryName := ".proof." + proof.ProofID + ".tmp"
	fd, err := unix.Openat(
		int(directory.handle.Fd()), temporaryName,
		unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_CLOEXEC|unix.O_NOFOLLOW,
		0o600,
	)
	if err != nil {
		return errors.New("Relay root isolation proof could not be created")
	}
	file := os.NewFile(uintptr(fd), temporaryName)
	if file == nil {
		_ = unix.Close(fd)
		_ = directory.remove(temporaryName)
		return errors.New("Relay root isolation proof could not be created")
	}
	_, writeErr := file.Write(encoded)
	if writeErr == nil {
		writeErr = file.Sync()
	}
	if writeErr == nil {
		writeErr = file.Chmod(0o600)
	}
	closeErr := file.Close()
	if writeErr != nil || closeErr != nil {
		_ = directory.remove(temporaryName)
		return errors.New("Relay root isolation proof could not be committed")
	}
	if err := unix.Renameat2(
		int(directory.handle.Fd()), temporaryName,
		int(directory.handle.Fd()), finalName,
		unix.RENAME_NOREPLACE,
	); err != nil {
		_ = directory.remove(temporaryName)
		if errors.Is(err, syscall.EEXIST) {
			return os.ErrExist
		}
		return errors.New("Relay root isolation proof could not be committed")
	}
	if err := directory.sync(); err != nil {
		return errors.New("Relay root isolation proof directory could not be synchronized")
	}
	return nil
}
