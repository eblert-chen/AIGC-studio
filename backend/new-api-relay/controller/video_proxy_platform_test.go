package controller

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/model"
	"github.com/gin-gonic/gin"
	"github.com/glebarez/sqlite"
	"github.com/stretchr/testify/require"
	"gorm.io/gorm"
)

func TestVideoProxyRejectsPlatformOwnedProviderResultBeforeTransfer(t *testing.T) {
	previousDB := model.DB
	database, err := gorm.Open(sqlite.Open("file:platform_video_proxy?mode=memory&cache=shared"), &gorm.Config{})
	require.NoError(t, err)
	model.DB = database
	t.Cleanup(func() { model.DB = previousDB })
	require.NoError(t, database.AutoMigrate(&model.Task{}))

	providerURL := "https://provider.example/result.mp4?X-Signature=proxy-secret"
	task := model.Task{
		CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
		TaskID: "platform-proxy-task", UserId: 0, Status: model.TaskStatusSuccess, Progress: "100%",
		Data: []byte(`{"content":{"video_url":"` + providerURL + `"}}`),
		PrivateData: model.TaskPrivateData{
			BillingSource: model.TaskBillingSourcePlatformExternal,
			ResultURL:     providerURL,
		},
	}
	require.NoError(t, database.Create(&task).Error)

	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.GET("/v1/videos/:task_id/content", func(c *gin.Context) {
		c.Set("id", 0)
		VideoProxy(c)
	})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/v1/videos/platform-proxy-task/content", nil)
	router.ServeHTTP(recorder, request)

	require.Equal(t, http.StatusNotFound, recorder.Code)
	require.NotContains(t, recorder.Body.String(), providerURL)
	require.NotContains(t, recorder.Body.String(), "X-Signature")
	require.Contains(t, recorder.Body.String(), "Task not found")
}
