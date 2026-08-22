//go:build !linux && !windows

package common

import "os"

// Protected deployments are Linux containers. Other development Unix hosts
// retain the pathname write probe while Linux enforces the mount flag itself.
func protectedSecretFileFilesystemReadOnly(_ *os.File) bool {
	return true
}
