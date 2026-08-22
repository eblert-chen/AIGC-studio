//go:build windows

package common

import "os"

func protectedSecretFileOwnedByEffectiveUser(_ os.FileInfo) bool {
	return true
}

func protectedSecretFileHasOwnerOnlyMode(_ os.FileInfo) bool {
	return true
}

func protectedSecretFileFilesystemReadOnly(_ *os.File) bool {
	return true
}
