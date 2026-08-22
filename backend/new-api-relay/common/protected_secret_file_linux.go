//go:build linux

package common

import (
	"os"

	"golang.org/x/sys/unix"
)

func protectedSecretFileFilesystemReadOnly(file *os.File) bool {
	var filesystem unix.Statfs_t
	return unix.Fstatfs(int(file.Fd()), &filesystem) == nil &&
		filesystem.Flags&unix.ST_RDONLY != 0
}
