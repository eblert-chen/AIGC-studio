//go:build !linux

package service

import (
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
)

func platformRelayRootIsolationProofPathAbsent(path string) bool {
	if path == "" || !filepath.IsAbs(path) || filepath.Clean(path) != path {
		return false
	}
	_, err := os.Lstat(path)
	return errors.Is(err, os.ErrNotExist)
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
	return os.Remove(filepath.Join(directory.path, name))
}

func (directory *platformRelaySecretIsolationReceiptDirectory) readRootProof(maximumBytes int64) ([]byte, error) {
	if directory == nil || directory.handle == nil || maximumBytes <= 0 {
		return nil, errors.New("root proof directory is invalid")
	}
	path := filepath.Join(directory.path, "proof.json")
	before, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if before.Mode()&os.ModeSymlink != 0 || !before.Mode().IsRegular() ||
		(runtime.GOOS != "windows" && before.Mode().Perm() != 0o600) ||
		before.Size() <= 0 || before.Size() > maximumBytes {
		return nil, errors.New("root proof is invalid")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	after, err := file.Stat()
	if err != nil || !after.Mode().IsRegular() || !os.SameFile(before, after) ||
		(runtime.GOOS != "windows" && after.Mode().Perm() != 0o600) ||
		after.Size() <= 0 || after.Size() > maximumBytes {
		return nil, errors.New("root proof changed while opening")
	}
	raw, err := io.ReadAll(io.LimitReader(file, maximumBytes+1))
	if err != nil || len(raw) == 0 || int64(len(raw)) > maximumBytes {
		clear(raw)
		return nil, errors.New("root proof is invalid")
	}
	return raw, nil
}

func (directory *platformRelaySecretIsolationReceiptDirectory) sync() error {
	return nil
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
	finalPath := filepath.Join(directory.path, "receipt.json")
	temporaryPath := filepath.Join(directory.path, ".receipt.json.tmp")
	_ = os.Remove(temporaryPath)
	file, err := os.OpenFile(temporaryPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
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
		_ = os.Remove(temporaryPath)
		return errors.New("Relay secret isolation receipt could not be committed")
	}
	if err := os.Rename(temporaryPath, finalPath); err != nil {
		_ = os.Remove(temporaryPath)
		return errors.New("Relay secret isolation receipt could not be committed")
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
	finalPath := filepath.Join(directory.path, "proof.json")
	temporaryPath := filepath.Join(directory.path, ".proof."+proof.ProofID+".tmp")
	file, err := os.OpenFile(temporaryPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
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
		_ = os.Remove(temporaryPath)
		return errors.New("Relay root isolation proof could not be committed")
	}
	if err := os.Link(temporaryPath, finalPath); err != nil {
		_ = os.Remove(temporaryPath)
		if errors.Is(err, os.ErrExist) {
			return os.ErrExist
		}
		return errors.New("Relay root isolation proof could not be committed")
	}
	if err := os.Remove(temporaryPath); err != nil {
		return errors.New("Relay root isolation proof could not be committed")
	}
	return nil
}
