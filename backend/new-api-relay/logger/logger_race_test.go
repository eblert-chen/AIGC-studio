package logger

import (
	"context"
	"io"
	"sync"
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestConcurrentLoggingMaintainsAtomicRolloverState(t *testing.T) {
	common.LogWriterMu.Lock()
	previousWriter := gin.DefaultWriter
	previousErrorWriter := gin.DefaultErrorWriter
	gin.DefaultWriter = io.Discard
	gin.DefaultErrorWriter = io.Discard
	common.LogWriterMu.Unlock()
	logCount.Store(0)
	setupLogWorking.Store(false)
	t.Cleanup(func() {
		common.LogWriterMu.Lock()
		gin.DefaultWriter = previousWriter
		gin.DefaultErrorWriter = previousErrorWriter
		common.LogWriterMu.Unlock()
		logCount.Store(0)
		setupLogWorking.Store(false)
	})

	const workers = 32
	const writesPerWorker = 32
	var wait sync.WaitGroup
	for range workers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			for range writesPerWorker {
				LogInfo(context.Background(), "concurrent logger race regression")
			}
		}()
	}
	wait.Wait()
	require.Equal(t, int64(workers*writesPerWorker), logCount.Load())
}
