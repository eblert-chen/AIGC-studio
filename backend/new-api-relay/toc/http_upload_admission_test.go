package toc

import (
	"bytes"
	"context"
	"errors"
	"image"
	"image/png"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/bytedance/gopkg/util/gopool"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

type uploadBodyProbe struct {
	block       bool
	reads       atomic.Int32
	protocol    atomic.Int32
	entered     chan struct{}
	proceed     chan struct{}
	finished    chan struct{}
	releaseOnce sync.Once
}

func (probe *uploadBodyProbe) release() { probe.releaseOnce.Do(func() { close(probe.proceed) }) }

type observedUploadBody struct {
	io.ReadCloser
	ctx   context.Context
	probe *uploadBodyProbe
}

func (body *observedUploadBody) Read(buffer []byte) (int, error) {
	if body.probe.reads.Add(1) == 1 {
		close(body.probe.entered)
	}
	if body.probe.block {
		select {
		case <-body.probe.proceed:
		case <-body.ctx.Done():
			return 0, body.ctx.Err()
		}
	}
	return body.ReadCloser.Read(buffer)
}

func TestTOCHTTPUploadAdmissionPrecedesBodyReadAndReleasesEveryExit(t *testing.T) {
	s, _ := fixture(t)
	previousDB, previousLog, previousSecret := model.DB, model.LOG_DB, common.SessionSecret
	previousRedis, previousLimit := common.RedisEnabled, common.GlobalApiRateLimitEnable
	previousMain, previousLogType := common.MainDatabaseType(), common.LogDatabaseType()
	model.DB, model.LOG_DB = s.DB, s.DB
	common.SessionSecret = strings.Repeat("upload-admission-session-", 2)
	common.RedisEnabled, common.GlobalApiRateLimitEnable = false, false
	common.SetDatabaseTypes(common.DatabaseTypeSQLite, common.DatabaseTypeSQLite)
	t.Cleanup(func() {
		require.Eventually(t, func() bool { return gopool.WorkerCount() == 0 }, 3*time.Second, time.Millisecond)
		model.DB, model.LOG_DB, common.SessionSecret = previousDB, previousLog, previousSecret
		common.RedisEnabled, common.GlobalApiRateLimitEnable = previousRedis, previousLimit
		common.SetDatabaseTypes(previousMain, previousLogType)
	})
	t.Setenv("RELAY_RUNTIME_PROFILE", "toc")
	t.Setenv("TOC_CLIENT_ORIGIN", "http://127.0.0.1:14340")
	tokens := map[int]string{}
	for _, userID := range []int{1, 2, 3} {
		session := model.UserSession{SID: uuid.NewString(), UserID: userID, Version: 1, UserAuthVersion: 1, Status: model.UserSessionStatusActive, RefreshHash: uuid.NewString(), LoginMethod: "password", LastActiveAt: time.Now().Unix(), ExpiresAt: time.Now().Add(time.Hour).Unix()}
		require.NoError(t, model.CreateUserSession(&session))
		token, _, err := service.IssueAccessToken(service.AuthIdentity{UserID: userID, SessionID: session.SID, UserAuthVersion: 1, SessionVersion: 1})
		require.NoError(t, err)
		tokens[userID] = token
	}
	var picture, payload bytes.Buffer
	require.NoError(t, png.Encode(&picture, image.NewRGBA(image.Rect(0, 0, 2, 2))))
	multipartWriter := multipart.NewWriter(&payload)
	part, err := multipartWriter.CreateFormFile("file", "reference.png")
	require.NoError(t, err)
	_, err = part.Write(picture.Bytes())
	require.NoError(t, err)
	require.NoError(t, multipartWriter.WriteField("media_type", "image"))
	require.NoError(t, multipartWriter.Close())
	probes := map[string]*uploadBodyProbe{}
	for _, id := range []string{"first", "malformed", "same-owner", "third-owner", "after-malformed", "cancel", "after-cancel", "after-success"} {
		probes[id] = &uploadBodyProbe{block: id == "first" || id == "malformed" || id == "cancel", entered: make(chan struct{}), proceed: make(chan struct{}), finished: make(chan struct{})}
	}
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	require.NoError(t, engine.SetTrustedProxies(nil))
	engine.Use(func(c *gin.Context) {
		if probe := probes[c.GetHeader("X-Test-Upload-ID")]; probe != nil {
			probe.protocol.Store(int32(c.Request.ProtoMajor))
			c.Request.Body = &observedUploadBody{ReadCloser: c.Request.Body, ctx: c.Request.Context(), probe: probe}
			defer close(probe.finished)
		}
		c.Next()
	})
	RegisterRoutes(engine, s)
	server := httptest.NewUnstartedServer(engine)
	// HTTP/2 cancellation sends RST_STREAM independently of the blocked body
	// read, so the cancellation test observes the real server request context.
	server.EnableHTTP2 = true
	server.StartTLS()
	t.Cleanup(server.Close)
	t.Cleanup(func() {
		for _, probe := range probes {
			probe.release()
		}
	})
	type result struct {
		status int
		header http.Header
		body   []byte
		err    error
	}
	start := func(ctx context.Context, id string, userID int, raw []byte) <-chan result {
		request, requestErr := http.NewRequestWithContext(ctx, http.MethodPost, server.URL+"/api/v1/personal/assets", bytes.NewReader(raw))
		require.NoError(t, requestErr)
		request.Header.Set("Authorization", "Bearer "+tokens[userID])
		request.Header.Set("Origin", "http://127.0.0.1:14340")
		request.Header.Set("Content-Type", multipartWriter.FormDataContentType())
		request.Header.Set("Idempotency-Key", uuid.NewString())
		request.Header.Set("X-Test-Upload-ID", id)
		results := make(chan result, 1)
		go func() {
			response, callErr := server.Client().Do(request)
			if callErr != nil {
				results <- result{err: callErr}
				return
			}
			body, readErr := io.ReadAll(response.Body)
			closeErr := response.Body.Close()
			results <- result{status: response.StatusCode, header: response.Header, body: body, err: errors.Join(readErr, closeErr)}
		}()
		return results
	}
	wait := func(signal <-chan struct{}) {
		select {
		case <-signal:
		case <-time.After(10 * time.Second):
			t.Fatal("HTTP upload did not reach the expected lifecycle boundary")
		}
	}
	finish := func(results <-chan result) result {
		select {
		case response := <-results:
			return response
		case <-time.After(10 * time.Second):
			t.Fatal("HTTP upload did not finish")
			return result{}
		}
	}
	assertAssets := func(expected int64) {
		var count int64
		require.NoError(t, s.DB.Model(&Asset{}).Count(&count).Error)
		require.Equal(t, expected, count)
		files, readErr := os.ReadDir(s.Assets.Root)
		require.NoError(t, readErr)
		require.Len(t, files, int(expected))
	}
	first := start(context.Background(), "first", 2, payload.Bytes())
	wait(probes["first"].entered)
	malformed := start(context.Background(), "malformed", 3, []byte("malformed multipart body"))
	wait(probes["malformed"].entered)
	require.EqualValues(t, 2, probes["first"].protocol.Load())
	for _, attempt := range []struct {
		id   string
		user int
	}{{"same-owner", 2}, {"third-owner", 1}} {
		response := finish(start(context.Background(), attempt.id, attempt.user, payload.Bytes()))
		require.NoError(t, response.err)
		require.Equal(t, http.StatusTooManyRequests, response.status)
		require.Equal(t, "1", response.header.Get("Retry-After"))
		require.Contains(t, string(response.body), "TOC_ASSET_UPLOAD_BUSY")
		wait(probes[attempt.id].finished)
		require.Zero(t, probes[attempt.id].reads.Load(), "rejection must precede multipart parsing and body buffering")
	}
	assertAssets(0)
	probes["malformed"].release()
	response := finish(malformed)
	require.NoError(t, response.err)
	require.Equal(t, http.StatusUnprocessableEntity, response.status)
	wait(probes["malformed"].finished)
	response = finish(start(context.Background(), "after-malformed", 1, payload.Bytes()))
	require.NoError(t, response.err)
	require.Equal(t, http.StatusOK, response.status, "malformed input must release the global receive slot while the first owner remains blocked")
	assertAssets(1)
	probes["first"].release()
	response = finish(first)
	require.NoError(t, response.err)
	require.Equal(t, http.StatusOK, response.status)
	wait(probes["first"].finished)
	response = finish(start(context.Background(), "after-success", 2, payload.Bytes()))
	require.NoError(t, response.err)
	require.Equal(t, http.StatusOK, response.status, "successful upload releases its owner slot and does not reenter the independent decode guard")
	assertAssets(3)
	cancelContext, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	cancelled := start(cancelContext, "cancel", 2, payload.Bytes())
	wait(probes["cancel"].entered)
	cancel()
	response = finish(cancelled)
	require.ErrorIs(t, response.err, context.Canceled)
	wait(probes["cancel"].finished)
	assertAssets(3)
	response = finish(start(context.Background(), "after-cancel", 2, payload.Bytes()))
	require.NoError(t, response.err)
	require.Equal(t, http.StatusOK, response.status, "cancelled multipart parsing must release the owner slot")
	assertAssets(4)
}
