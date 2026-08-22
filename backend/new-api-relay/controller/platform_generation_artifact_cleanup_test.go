package controller

import (
	"testing"
	"time"

	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/assert"
)

func TestPlatformArtifactCleanupReadinessStaysDegradedDuringPeriodicRetryBackoff(t *testing.T) {
	healthyWorker := service.PlatformArtifactCleanupWorkerStatus{
		Started:         true,
		Running:         true,
		LastHeartbeatAt: time.Now().UTC(),
	}
	assert.Equal(t, "healthy", platformArtifactCleanupReadinessState(
		model.PlatformArtifactUploadIntentCounts{Cleaned: 3},
		healthyWorker,
		false,
	))
	assert.Equal(t, "degraded", platformArtifactCleanupReadinessState(
		model.PlatformArtifactUploadIntentCounts{
			Cleaned:         3,
			CleanedRetrying: 1,
		},
		healthyWorker,
		false,
	))
	assert.Equal(t, "degraded", platformArtifactCleanupReadinessState(
		model.PlatformArtifactUploadIntentCounts{Retrying: 1},
		healthyWorker,
		false,
	))
	assert.Equal(t, "degraded", platformArtifactCleanupReadinessState(
		model.PlatformArtifactUploadIntentCounts{Quarantined: 1},
		healthyWorker,
		false,
	))
}

func TestPlatformArtifactCleanupReadinessRejectsDeadOrStaleWorker(t *testing.T) {
	counts := model.PlatformArtifactUploadIntentCounts{}
	notStarted := service.PlatformArtifactCleanupWorkerStatus{Stale: true}
	stale := service.PlatformArtifactCleanupWorkerStatus{
		Started: true,
		Running: true,
		Stale:   true,
	}
	failed := service.PlatformArtifactCleanupWorkerStatus{
		Started:          true,
		Running:          true,
		CurrentErrorCode: "store_init_failed",
	}

	for _, worker := range []service.PlatformArtifactCleanupWorkerStatus{notStarted, stale, failed} {
		assert.Equal(t, "degraded", platformArtifactCleanupReadinessState(counts, worker, false))
		assert.Equal(t, "unavailable", platformArtifactCleanupReadinessState(counts, worker, true))
	}
}

func TestPlatformArtifactCleanupMaintenanceOutlivesGenerationAdmission(t *testing.T) {
	assert.False(t, platformArtifactCleanupMaintenanceRequired(
		model.PlatformArtifactUploadIntentCounts{Published: 4},
	))
	assert.True(t, platformArtifactCleanupMaintenanceRequired(
		model.PlatformArtifactUploadIntentCounts{Cleaned: 1, Published: 4},
	))
	assert.True(t, platformArtifactCleanupMaintenanceRequired(
		model.PlatformArtifactUploadIntentCounts{DeadLetter: 1},
	))
}
