//go:build !windows

package common

import (
	"os"
	"syscall"
)

func protectedSecretFileOwnedByEffectiveUser(info os.FileInfo) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && stat.Uid == uint32(os.Geteuid())
}

func protectedSecretFileHasOwnerOnlyMode(info os.FileInfo) bool {
	return info.Mode().Perm() == 0o400 || info.Mode().Perm() == 0o600
}
