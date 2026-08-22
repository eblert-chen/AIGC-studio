package service

import (
	"errors"
	"os"
)

// PlatformRelayDeploymentStateLock serializes every offline Relay database
// release consumer with the privileged predecessor that revokes and replaces
// the database release proof. It deliberately reuses the permanent root-proof
// volume lock: the global secret generation and the database proof therefore
// cannot advance independently.
type PlatformRelayDeploymentStateLock struct {
	state *platformRelayRootProofStateLock
}

// AcquirePlatformRelayDeploymentStateLock must run before an offline command
// verifies its receipt or database proof. The underlying lock has a fixed
// bounded acquisition deadline and is keyed by the opened .proof.lock inode.
func AcquirePlatformRelayDeploymentStateLock() (*PlatformRelayDeploymentStateLock, error) {
	state, err := platformRelayRootIsolationAcquireProofStateLock(
		os.Getenv(PlatformRelayRootProofFileEnvironment),
	)
	if err != nil {
		return nil, errors.New("Relay deployment state lock could not be acquired")
	}
	return &PlatformRelayDeploymentStateLock{state: state}, nil
}

// Release closes the pinned root-proof directory and lock handles.
func (lock *PlatformRelayDeploymentStateLock) Release() error {
	if lock == nil || lock.state == nil {
		return nil
	}
	err := lock.state.release()
	lock.state = nil
	if err != nil {
		return errors.New("Relay deployment state lock could not be released")
	}
	return nil
}
