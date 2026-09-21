package geminiomni

import (
	"context"
	"io"
	"net/http"
	"strings"

	"github.com/QuantumNous/new-api/common"
	taskdto "github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay/channel"
	"github.com/QuantumNous/new-api/relay/channel/task/gemini"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/gin-gonic/gin"
)

// DispatchTaskAdaptor keeps Gemini Developer API credentials and the existing
// channel type shared while selecting the protocol from the immutable provider
// model. Omni is an Interactions API model; every other Gemini task retains the
// existing Veo predictLongRunning behavior.
type DispatchTaskAdaptor struct {
	omni TaskAdaptor
	veo  gemini.TaskAdaptor
}

func (a *DispatchTaskAdaptor) Init(info *relaycommon.RelayInfo) {
	a.omni.Init(info)
	a.veo.Init(info)
}

func isOmniInfo(info *relaycommon.RelayInfo) bool {
	return info != nil && (info.UpstreamModelName == ModelGeminiOmni11Flash || info.OriginModelName == ModelGeminiOmni11Flash)
}

func isOmniPollingBody(body map[string]any) bool {
	if providerModel, _ := body["provider_model"].(string); providerModel == ModelGeminiOmni11Flash {
		return true
	}
	taskID, _ := body["task_id"].(string)
	return strings.HasPrefix(taskID, "omni-file:")
}

func (a *DispatchTaskAdaptor) ValidateRequestAndSetAction(c *gin.Context, info *relaycommon.RelayInfo) *taskdto.TaskError {
	if isOmniInfo(info) {
		return a.omni.ValidateRequestAndSetAction(c, info)
	}
	return a.veo.ValidateRequestAndSetAction(c, info)
}

func (a *DispatchTaskAdaptor) EstimateBilling(c *gin.Context, info *relaycommon.RelayInfo) map[string]float64 {
	if isOmniInfo(info) {
		return a.omni.EstimateBilling(c, info)
	}
	return a.veo.EstimateBilling(c, info)
}

func (a *DispatchTaskAdaptor) AdjustBillingOnSubmit(info *relaycommon.RelayInfo, taskData []byte) map[string]float64 {
	if isOmniInfo(info) {
		return a.omni.AdjustBillingOnSubmit(info, taskData)
	}
	return a.veo.AdjustBillingOnSubmit(info, taskData)
}

func (a *DispatchTaskAdaptor) AdjustBillingOnComplete(task *model.Task, result *relaycommon.TaskInfo) int {
	if task != nil && (task.Properties.UpstreamModelName == ModelGeminiOmni11Flash ||
		strings.HasPrefix(task.GetUpstreamTaskID(), "omni-file:")) {
		return a.omni.AdjustBillingOnComplete(task, result)
	}
	return a.veo.AdjustBillingOnComplete(task, result)
}

func (a *DispatchTaskAdaptor) BuildRequestURL(info *relaycommon.RelayInfo) (string, error) {
	if isOmniInfo(info) {
		return a.omni.BuildRequestURL(info)
	}
	return a.veo.BuildRequestURL(info)
}

func (a *DispatchTaskAdaptor) BuildRequestHeader(c *gin.Context, req *http.Request, info *relaycommon.RelayInfo) error {
	if isOmniInfo(info) {
		return a.omni.BuildRequestHeader(c, req, info)
	}
	return a.veo.BuildRequestHeader(c, req, info)
}

func (a *DispatchTaskAdaptor) BuildRequestBody(c *gin.Context, info *relaycommon.RelayInfo) (io.Reader, error) {
	if isOmniInfo(info) {
		return a.omni.BuildRequestBody(c, info)
	}
	return a.veo.BuildRequestBody(c, info)
}

func (a *DispatchTaskAdaptor) DoRequest(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (*http.Response, error) {
	if isOmniInfo(info) {
		return a.omni.DoRequest(c, info, requestBody)
	}
	return a.veo.DoRequest(c, info, requestBody)
}

// DoRequestNoRedirect is the paid exact-route acceptance seam. The shared
// protected client returns every 3xx response to the caller and removes the
// request-body replay hook, so neither an Omni Interactions POST nor a Veo
// predictLongRunning POST can be repeated at a redirect target.
func (a *DispatchTaskAdaptor) DoRequestNoRedirect(c *gin.Context, info *relaycommon.RelayInfo, requestBody io.Reader) (*http.Response, error) {
	return channel.DoTaskApiRequestNoRedirect(a, c, info, requestBody)
}

func (a *DispatchTaskAdaptor) DoResponse(c *gin.Context, resp *http.Response, info *relaycommon.RelayInfo) (string, []byte, *taskdto.TaskError) {
	if isOmniInfo(info) {
		return a.omni.DoResponse(c, resp, info)
	}
	return a.veo.DoResponse(c, resp, info)
}

func (a *DispatchTaskAdaptor) GetModelList() []string {
	models := a.veo.GetModelList()
	return append(models, ModelList...)
}

func (a *DispatchTaskAdaptor) GetChannelName() string {
	return ChannelName
}

func (a *DispatchTaskAdaptor) FetchTask(baseURL string, key string, body map[string]any, proxy string) (*http.Response, error) {
	return a.FetchTaskWithContext(context.Background(), baseURL, key, body, proxy)
}

func (a *DispatchTaskAdaptor) FetchTaskWithContext(ctx context.Context, baseURL string, key string, body map[string]any, proxy string) (*http.Response, error) {
	if isOmniPollingBody(body) {
		return a.omni.FetchTaskWithContext(ctx, baseURL, key, body, proxy)
	}
	return a.veo.FetchTaskWithContext(ctx, baseURL, key, body, proxy)
}

func (a *DispatchTaskAdaptor) ParseTaskResult(respBody []byte) (*relaycommon.TaskInfo, error) {
	var discriminator struct {
		Object        string `json:"object"`
		TaskID        string `json:"task_id"`
		ProviderModel string `json:"provider_model"`
	}
	if common.RejectDuplicateJSONKeys(respBody) == nil && common.Unmarshal(respBody, &discriminator) == nil &&
		(discriminator.Object == fileEnvelopeObject || discriminator.ProviderModel == ModelGeminiOmni11Flash ||
			strings.HasPrefix(discriminator.TaskID, "omni-file:")) {
		return a.omni.ParseTaskResult(respBody)
	}
	return a.veo.ParseTaskResult(respBody)
}

func (a *DispatchTaskAdaptor) ConvertToOpenAIVideo(task *model.Task) ([]byte, error) {
	if task != nil && (task.Properties.UpstreamModelName == ModelGeminiOmni11Flash ||
		strings.HasPrefix(task.GetUpstreamTaskID(), "omni-file:")) {
		return a.omni.ConvertToOpenAIVideo(task)
	}
	return a.veo.ConvertToOpenAIVideo(task)
}
