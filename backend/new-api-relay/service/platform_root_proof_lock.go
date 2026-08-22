package service

import "time"

var (
	platformRelayRootProofLockTimeout = 10 * time.Second
	platformRelayRootProofLockRetry   = 25 * time.Millisecond
)

func platformRelayRootProofLockWait(deadline time.Time) bool {
	remaining := time.Until(deadline)
	if remaining <= 0 {
		return false
	}
	delay := platformRelayRootProofLockRetry
	if delay <= 0 || delay > remaining {
		delay = remaining
	}
	timer := time.NewTimer(delay)
	<-timer.C
	return true
}
