package service

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	taskdto "github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/logger"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay/channel/task/taskcommon"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/relaykit/dto"

	"github.com/bytedance/gopkg/util/gopool"
	"github.com/samber/lo"
)

// TaskPollingAdaptor 定义轮询所需的最小适配器接口，避免 service -> relay 的循环依赖
type TaskPollingAdaptor interface {
	Init(info *relaycommon.RelayInfo)
	FetchTask(baseURL string, key string, body map[string]any, proxy string) (*http.Response, error)
	ParseTaskResult(body []byte) (*relaycommon.TaskInfo, error)
	// AdjustBillingOnComplete 在任务到达终态（成功/失败）时由轮询循环调用。
	// 返回正数触发差额结算（补扣/退还），返回 0 保持预扣费金额不变。
	AdjustBillingOnComplete(task *model.Task, taskResult *relaycommon.TaskInfo) int
}

type contextTaskPollingAdaptor interface {
	FetchTaskWithContext(ctx context.Context, baseURL string, key string, body map[string]any, proxy string) (*http.Response, error)
}

func fetchTaskWithContext(
	ctx context.Context,
	adaptor TaskPollingAdaptor,
	baseURL string,
	key string,
	body map[string]any,
	proxy string,
) (*http.Response, error) {
	contextAdaptor, ok := adaptor.(contextTaskPollingAdaptor)
	if !ok {
		return nil, errors.New("task polling adaptor does not support lifecycle cancellation")
	}
	return contextAdaptor.FetchTaskWithContext(ctx, baseURL, key, body, proxy)
}

// GetTaskAdaptorFunc 由 main 包注入，用于获取指定平台的任务适配器。
// 打破 service -> relay -> relay/channel -> service 的循环依赖。
var GetTaskAdaptorFunc func(platform constant.TaskPlatform) TaskPollingAdaptor

// sweepTimedOutTasks 在主轮询之前独立清理超时任务。
// 每次最多处理 100 条，剩余的下个周期继续处理。
// 使用 per-task CAS (UpdateWithStatus) 防止覆盖被正常轮询已推进的任务。
func sweepTimedOutTasks(ctx context.Context) {
	if constant.TaskTimeoutMinutes <= 0 {
		return
	}
	cutoff := time.Now().Unix() - int64(constant.TaskTimeoutMinutes)*60
	tasks := model.GetTimedOutUnfinishedTasks(cutoff, 100)
	sweepTimedOutTaskRows(ctx, tasks)
}

func sweepTimedOutTaskRows(ctx context.Context, tasks []*model.Task) {
	if len(tasks) == 0 {
		return
	}

	reason := fmt.Sprintf("任务超时（%d分钟）", constant.TaskTimeoutMinutes)
	legacyReason := "任务超时（旧系统遗留任务，不进行退款，请联系管理员）"
	now := time.Now().Unix()
	timedOutCount := 0

	for _, task := range tasks {
		if model.PlatformGenerationProviderResultReconciliationRequired(task) {
			continue
		}
		isLegacy := task.SubmitTime > 0 && task.SubmitTime < model.TaskRefundLegacyCutoff

		oldStatus := task.Status
		task.Status = model.TaskStatusFailure
		task.Progress = "100%"
		task.FinishTime = now
		if isLegacy {
			task.FailReason = legacyReason
			// 旧系统任务明确不退款，随终态 CAS 一并清掉 quota，
			// 避免留下可再次退款的计费状态。
			task.Quota = 0
		} else {
			task.FailReason = reason
		}

		won, err := task.UpdateWithStatus(oldStatus)
		if err != nil {
			logger.LogError(ctx, fmt.Sprintf("sweepTimedOutTasks CAS update error for task %s: %v", task.TaskID, err))
			continue
		}
		if !won {
			logger.LogInfo(ctx, fmt.Sprintf("sweepTimedOutTasks: task %s already transitioned, skip", task.TaskID))
			continue
		}
		timedOutCount++
		if !isLegacy && task.Quota != 0 {
			RefundTaskQuota(ctx, task, reason)
		}
	}

	if timedOutCount > 0 {
		logger.LogInfo(ctx, fmt.Sprintf("sweepTimedOutTasks: timed out %d tasks", timedOutCount))
	}
}

// TaskPollSummary is the result recorded on an async_task_poll system task row,
// summarizing one polling pass.
type TaskPollSummary struct {
	UnfinishedTasks  int `json:"unfinished_tasks"`
	PlatformsScanned int `json:"platforms_scanned"`
	NullTasksFailed  int `json:"null_tasks_failed"`
}

// RunTaskPollingOnce performs one async-task (Suno/video) polling pass
// synchronously. It honors ctx cancellation (the system-task runner cancels it
// when the lease is lost) and, when report is non-nil, reports progress as
// (processedPlatforms, totalPlatforms). It returns immediately if the task
// adaptor factory has not been wired yet, to avoid a nil call during startup.
func RunTaskPollingOnce(ctx context.Context, report func(processed, total int)) TaskPollSummary {
	summary := TaskPollSummary{}
	if GetTaskAdaptorFunc == nil {
		return summary
	}
	if ctx == nil {
		ctx = context.Background()
	}

	common.SysLog("任务进度轮询开始")
	var allTasks []*model.Task
	if model.RelayDatabaseRoleAttestationRequired() {
		protectedTasks, err := loadProtectedPlatformNativeUnfinishedTasks()
		if err != nil {
			logger.LogError(ctx, "protected native task billing attestation failed; polling is disabled")
			return summary
		}
		cutoff := time.Now().Unix() - int64(constant.TaskTimeoutMinutes)*60
		timedOut := make([]*model.Task, 0, 100)
		if constant.TaskTimeoutMinutes > 0 {
			for _, task := range protectedTasks {
				if model.PlatformGenerationProviderResultReconciliationRequired(task) {
					continue
				}
				if len(timedOut) == 100 {
					break
				}
				if task.SubmitTime < cutoff {
					timedOut = append(timedOut, task)
				}
			}
		}
		sweepTimedOutTaskRows(ctx, timedOut)
		for _, task := range protectedTasks {
			if model.PlatformGenerationProviderResultReconciliationRequired(task) {
				continue
			}
			if task.Progress != "100%" && task.Status != model.TaskStatusFailure && task.Status != model.TaskStatusSuccess {
				allTasks = append(allTasks, task)
				if len(allTasks) == constant.TaskQueryLimit {
					break
				}
			}
		}
	} else {
		sweepTimedOutTasks(ctx)
		allTasks = model.GetAllUnFinishSyncTasks(constant.TaskQueryLimit)
	}
	eligibleTasks := allTasks[:0]
	for _, task := range allTasks {
		if model.PlatformGenerationProviderResultReconciliationRequired(task) {
			continue
		}
		eligibleTasks = append(eligibleTasks, task)
	}
	allTasks = eligibleTasks
	summary.UnfinishedTasks = len(allTasks)
	platformTask := make(map[constant.TaskPlatform][]*model.Task)
	for _, t := range allTasks {
		platformTask[t.Platform] = append(platformTask[t.Platform], t)
	}

	totalPlatforms := len(platformTask)
	processedPlatforms := 0
	for platform, tasks := range platformTask {
		if ctx.Err() != nil {
			break
		}
		if report != nil {
			report(processedPlatforms, totalPlatforms)
		}
		processedPlatforms++
		if len(tasks) == 0 {
			continue
		}
		summary.PlatformsScanned++
		taskChannelM := make(map[int][]string)
		taskM := make(map[string]*model.Task)
		nullTaskIds := make([]int64, 0)
		for _, task := range tasks {
			upstreamID := task.GetUpstreamTaskID()
			if upstreamID == "" {
				// 统计失败的未完成任务
				nullTaskIds = append(nullTaskIds, task.ID)
				continue
			}
			taskM[upstreamID] = task
			taskChannelM[task.ChannelId] = append(taskChannelM[task.ChannelId], upstreamID)
		}
		if len(nullTaskIds) > 0 {
			summary.NullTasksFailed += len(nullTaskIds)
			err := model.TaskBulkUpdateByID(nullTaskIds, map[string]any{
				"status":   "FAILURE",
				"progress": "100%",
			})
			if err != nil {
				logger.LogError(ctx, fmt.Sprintf("Fix null task_id task error: %v", err))
			} else {
				logger.LogInfo(ctx, fmt.Sprintf("Fix null task_id task success: %v", nullTaskIds))
			}
		}
		if len(taskChannelM) == 0 {
			continue
		}

		DispatchPlatformUpdate(ctx, platform, taskChannelM, taskM)
	}
	if report != nil && ctx.Err() == nil {
		report(totalPlatforms, totalPlatforms)
	}
	common.SysLog("任务进度轮询完成")
	return summary
}

// DispatchPlatformUpdate 按平台分发轮询更新
func DispatchPlatformUpdate(ctx context.Context, platform constant.TaskPlatform, taskChannelM map[int][]string, taskM map[string]*model.Task) {
	if ctx == nil {
		ctx = context.Background()
	}
	switch platform {
	case constant.TaskPlatformMidjourney:
		// MJ 轮询由其自身处理，这里预留入口
	case constant.TaskPlatformSuno:
		_ = UpdateSunoTasks(ctx, taskChannelM, taskM)
	default:
		if err := UpdateVideoTasks(ctx, platform, taskChannelM, taskM); err != nil {
			common.SysLog(fmt.Sprintf("UpdateVideoTasks fail: %s", err))
		}
	}
}

// UpdateSunoTasks 按渠道更新所有 Suno 任务
func UpdateSunoTasks(ctx context.Context, taskChannelM map[int][]string, taskM map[string]*model.Task) error {
	for channelId, taskIds := range taskChannelM {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		err := updateSunoTasks(ctx, channelId, taskIds, taskM)
		if err != nil {
			logger.LogError(ctx, fmt.Sprintf("渠道 #%d 更新异步任务失败: %s", channelId, err.Error()))
		}
	}
	return nil
}

func updateSunoTasks(ctx context.Context, channelId int, taskIds []string, taskM map[string]*model.Task) error {
	logger.LogInfo(ctx, fmt.Sprintf("渠道 #%d 未完成的任务有: %d", channelId, len(taskIds)))
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if len(taskIds) == 0 {
		return nil
	}
	ch, err := model.CacheGetChannel(channelId)
	if err != nil {
		common.SysLog(fmt.Sprintf("CacheGetChannel: %v", err))
		// Collect DB primary key IDs for bulk update (taskIds are upstream IDs, not task_id column values)
		var failedIDs []int64
		for _, upstreamID := range taskIds {
			if t, ok := taskM[upstreamID]; ok {
				failedIDs = append(failedIDs, t.ID)
			}
		}
		err = model.TaskBulkUpdateByID(failedIDs, map[string]any{
			"fail_reason": fmt.Sprintf("获取渠道信息失败，请联系管理员，渠道ID：%d", channelId),
			"status":      "FAILURE",
			"progress":    "100%",
		})
		if err != nil {
			common.SysLog(fmt.Sprintf("UpdateSunoTask error: %v", err))
		}
		return err
	}
	adaptor := GetTaskAdaptorFunc(constant.TaskPlatformSuno)
	if adaptor == nil {
		return errors.New("adaptor not found")
	}
	proxy := ch.GetSetting().Proxy
	resp, err := fetchTaskWithContext(ctx, adaptor, *ch.BaseURL, ch.Key, map[string]any{
		"ids": taskIds,
	}, proxy)
	if err != nil {
		common.SysLog(fmt.Sprintf("Get Task Do req error: %v", err))
		return err
	}
	if resp.StatusCode != http.StatusOK {
		logger.LogError(ctx, fmt.Sprintf("Get Task status code: %d", resp.StatusCode))
		return fmt.Errorf("Get Task status code: %d", resp.StatusCode)
	}
	defer resp.Body.Close()
	responseBody, err := relaycommon.ReadProviderTaskResponseBody(resp.Body)
	if err != nil {
		common.SysLog(fmt.Sprintf("Get Suno Task parse body error: %v", err))
		return err
	}
	var responseItems taskdto.TaskResponse[[]taskdto.SunoDataResponse]
	err = common.Unmarshal(responseBody, &responseItems)
	if err != nil {
		logger.LogError(ctx, fmt.Sprintf("Get Suno Task parse body error2: %v, body: %s", err, string(responseBody)))
		return err
	}
	if !responseItems.IsSuccess() {
		common.SysLog(fmt.Sprintf("渠道 #%d 未完成的任务有: %d, 成功获取到任务数: %s", channelId, len(taskIds), string(responseBody)))
		return err
	}

	for _, responseItem := range responseItems.Data {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		task := taskM[responseItem.TaskID]
		if task == nil {
			logger.LogWarn(ctx, fmt.Sprintf("Suno task response ignored: unknown task_id=%s", responseItem.TaskID))
			continue
		}
		if !taskNeedsUpdate(task, responseItem) {
			continue
		}

		prevStatus := task.Status
		task.Status = lo.If(model.TaskStatus(responseItem.Status) != "", model.TaskStatus(responseItem.Status)).Else(task.Status)
		task.FailReason = lo.If(responseItem.FailReason != "", responseItem.FailReason).Else(task.FailReason)
		task.SubmitTime = lo.If(responseItem.SubmitTime != 0, responseItem.SubmitTime).Else(task.SubmitTime)
		task.StartTime = lo.If(responseItem.StartTime != 0, responseItem.StartTime).Else(task.StartTime)
		task.FinishTime = lo.If(responseItem.FinishTime != 0, responseItem.FinishTime).Else(task.FinishTime)
		isFailure := responseItem.FailReason != "" || task.Status == model.TaskStatusFailure
		if isFailure {
			logger.LogInfo(ctx, task.TaskID+" 构建失败，"+task.FailReason)
			task.Status = model.TaskStatusFailure
			task.Progress = "100%"
		}
		if responseItem.Status == model.TaskStatusSuccess {
			task.Progress = "100%"
		}
		task.Data = responseItem.Data

		// 持久化走 CAS，防止重叠轮询/sweep/多实例/持久化失败重试导致重复退款或覆盖终态。
		won, err := task.UpdateWithStatus(prevStatus)
		if err != nil {
			logger.LogError(ctx, fmt.Sprintf("UpdateSunoTask task %s error: %v", task.TaskID, err))
		} else if !won {
			logger.LogWarn(ctx, fmt.Sprintf("Task %s CAS lost or no-op update, skip billing", task.TaskID))
		} else if isFailure && prevStatus != model.TaskStatusFailure && task.Quota != 0 {
			RefundTaskQuota(ctx, task, task.FailReason)
		}
	}
	return nil
}

// taskNeedsUpdate 检查 Suno 任务是否需要更新
func taskNeedsUpdate(oldTask *model.Task, newTask taskdto.SunoDataResponse) bool {
	if oldTask.SubmitTime != newTask.SubmitTime {
		return true
	}
	if oldTask.StartTime != newTask.StartTime {
		return true
	}
	if oldTask.FinishTime != newTask.FinishTime {
		return true
	}
	if string(oldTask.Status) != newTask.Status {
		return true
	}
	if oldTask.FailReason != newTask.FailReason {
		return true
	}

	if (oldTask.Status == model.TaskStatusFailure || oldTask.Status == model.TaskStatusSuccess) && oldTask.Progress != "100%" {
		return true
	}

	oldData, _ := common.Marshal(oldTask.Data)
	newData, _ := common.Marshal(newTask.Data)

	sort.Slice(oldData, func(i, j int) bool {
		return oldData[i] < oldData[j]
	})
	sort.Slice(newData, func(i, j int) bool {
		return newData[i] < newData[j]
	})

	if string(oldData) != string(newData) {
		return true
	}
	return false
}

// UpdateVideoTasks 按渠道更新所有视频任务
func UpdateVideoTasks(ctx context.Context, platform constant.TaskPlatform, taskChannelM map[int][]string, taskM map[string]*model.Task) error {
	channelIDs := make([]int, 0, len(taskChannelM))
	for channelID := range taskChannelM {
		channelIDs = append(channelIDs, channelID)
	}
	sort.Ints(channelIDs)

	var wg sync.WaitGroup
	for _, channelId := range channelIDs {
		taskIds := taskChannelM[channelId]
		if len(taskIds) == 0 {
			continue
		}
		taskIds = append([]string(nil), taskIds...)

		wg.Add(1)
		gopool.Go(func() {
			defer wg.Done()
			if err := updateVideoTasks(ctx, platform, channelId, taskIds, taskM); err != nil {
				logger.LogError(ctx, fmt.Sprintf("Channel #%d failed to update video async tasks: %s", channelId, err.Error()))
			}
		})
	}
	wg.Wait()
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return nil
}

func updateVideoTasks(ctx context.Context, platform constant.TaskPlatform, channelId int, taskIds []string, taskM map[string]*model.Task) error {
	logger.LogInfo(ctx, fmt.Sprintf("Channel #%d pending video tasks: %d", channelId, len(taskIds)))
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if len(taskIds) == 0 {
		return nil
	}
	cacheGetChannel, err := model.CacheGetChannel(channelId)
	if err != nil {
		// Collect DB primary key IDs for bulk update (taskIds are upstream IDs, not task_id column values)
		var failedIDs []int64
		for _, upstreamID := range taskIds {
			if t, ok := taskM[upstreamID]; ok {
				failedIDs = append(failedIDs, t.ID)
			}
		}
		errUpdate := model.TaskBulkUpdateByID(failedIDs, map[string]any{
			"fail_reason": fmt.Sprintf("Failed to get channel info, channel ID: %d", channelId),
			"status":      "FAILURE",
			"progress":    "100%",
		})
		if errUpdate != nil {
			common.SysLog(fmt.Sprintf("UpdateVideoTask error: %v", errUpdate))
		}
		return fmt.Errorf("CacheGetChannel failed: %w", err)
	}
	adaptor := GetTaskAdaptorFunc(platform)
	if adaptor == nil {
		return fmt.Errorf("video adaptor not found")
	}
	info := &relaycommon.RelayInfo{}
	info.ChannelMeta = &relaycommon.ChannelMeta{
		ChannelBaseUrl: cacheGetChannel.GetBaseURL(),
	}
	info.ApiKey = cacheGetChannel.Key
	adaptor.Init(info)
	disablePollingSleep := cacheGetChannel.GetOtherSettings().DisableTaskPollingSleep
	for i, taskId := range taskIds {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err := updateVideoSingleTask(ctx, adaptor, cacheGetChannel, taskId, taskM); err != nil {
			logger.LogError(ctx, fmt.Sprintf("Failed to update video task %s: %s", taskId, err.Error()))
		}
		if disablePollingSleep || i == len(taskIds)-1 {
			continue
		}

		// sleep 1 second between tasks for this channel only.
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(1 * time.Second):
		}
	}
	return nil
}

func updateVideoSingleTask(ctx context.Context, adaptor TaskPollingAdaptor, ch *model.Channel, taskId string, taskM map[string]*model.Task) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	baseURL := constant.ChannelBaseURLs[ch.Type]
	if ch.GetBaseURL() != "" {
		baseURL = ch.GetBaseURL()
	}
	proxy := ch.GetSetting().Proxy

	task := taskM[taskId]
	if task == nil {
		logger.LogError(ctx, fmt.Sprintf("Task %s not found in taskM", taskId))
		return fmt.Errorf("task %s not found", taskId)
	}
	isPlatformExternal := task.IsPlatformExternalBilling()
	key := ch.Key
	if isPlatformExternal && (ch.Type == constant.ChannelTypeGemini || ch.Type == constant.ChannelTypeVertexAi ||
		task.PrivateData.ProviderTransportRevision != "" || task.PrivateData.ProviderTransportSHA256 != "") {
		freshChannel, channelErr := model.GetChannelById(ch.Id, false)
		if channelErr != nil {
			return errors.New("protected Google task transport channel is unavailable")
		}
		expected := PlatformGenerationRuntimeTransportBinding{
			Revision: task.PrivateData.ProviderTransportRevision,
			SHA256:   task.PrivateData.ProviderTransportSHA256,
		}
		if err := ValidatePlatformGenerationRuntimeTransport(freshChannel, expected); err != nil {
			return errors.New("protected Google task transport binding changed")
		}
		ch = freshChannel
		baseURL = ch.GetBaseURL()
		proxy = ch.GetSetting().Proxy
	}

	privateData := task.PrivateData
	if privateData.LegacyProviderCredentialPresent {
		return fmt.Errorf("legacy plaintext provider credential is rejected for task %s", taskId)
	}
	if privateData.ProviderCredentialVersion != "" {
		resolvedKey, err := model.ResolveTaskProviderCredential(task)
		if err != nil {
			return fmt.Errorf("provider credential version is unavailable for task %s: %w", taskId, err)
		}
		key = resolvedKey
	} else if privateData.PinnedKeyIndex != nil {
		return fmt.Errorf("provider credential version is missing for task %s", taskId)
	}
	providerModel := task.Properties.UpstreamModelName
	if isPlatformExternal && (ch.Type == constant.ChannelTypeMiniMax ||
		ch.Type == constant.ChannelTypeGemini || ch.Type == constant.ChannelTypeVertexAi) {
		// A channel can host several provider protocols/models. Dispatch polling
		// by the task's immutable route, never by mutable channel configuration.
		binding, err := model.ResolvePlatformGenerationProviderResultBinding(*task)
		if err != nil {
			return errors.New("protected provider task route binding is unavailable")
		}
		providerModel = binding.Route.UpstreamModel
		googleRoute := (binding.Route.AcceptedChannelType == constant.ChannelTypeGemini ||
			binding.Route.AcceptedChannelType == constant.ChannelTypeVertexAi) &&
			strings.HasPrefix(binding.Route.CapabilityProfileID, "google.")
		if googleRoute {
			if ch.Id != binding.Route.ChannelID ||
				task.PrivateData.ProviderTransportRevision == "" || task.PrivateData.ProviderTransportSHA256 == "" {
				return errors.New("protected Google task transport binding is unavailable")
			}
		}
	}
	pollRequest := map[string]any{"task_id": task.GetUpstreamTaskID(), "action": task.Action}
	if providerModel != "" {
		pollRequest["provider_model"] = providerModel
	}
	resp, err := fetchTaskWithContext(ctx, adaptor, baseURL, key, pollRequest, proxy)
	if err != nil {
		if isPlatformExternal {
			errorDigest := sha256.Sum256([]byte(err.Error()))
			return fmt.Errorf(
				"provider fetch failed for protected task %s (error_sha256=%x)",
				taskId,
				errorDigest,
			)
		}
		return fmt.Errorf("fetchTask failed for task %s: %w", taskId, err)
	}
	if resp == nil || resp.Body == nil {
		if isPlatformExternal {
			return fmt.Errorf("provider returned no response body for protected task %s", taskId)
		}
		return fmt.Errorf("provider returned no response body for task %s", taskId)
	}
	defer resp.Body.Close()
	responseBody, err := relaycommon.ReadProviderTaskResponseBody(resp.Body)
	if err != nil {
		if isPlatformExternal {
			if errors.Is(err, relaycommon.ErrProviderTaskResponseBodyTooLarge) {
				return fmt.Errorf("provider response exceeded the limit for protected task %s", taskId)
			}
			return fmt.Errorf("provider response read failed for protected task %s", taskId)
		}
		return fmt.Errorf("readAll failed for task %s: %w", taskId, err)
	}
	responseDigest := sha256.Sum256(responseBody)
	if isPlatformExternal && (resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices) {
		return fmt.Errorf(
			"provider returned non-success status for protected task %s (status_code=%d body_bytes=%d body_sha256=%x)",
			taskId,
			resp.StatusCode,
			len(responseBody),
			responseDigest,
		)
	}
	redactedResponseBody := redactVideoResponseBody(responseBody, isPlatformExternal)
	logger.LogDebug(
		ctx,
		"updateVideoSingleTask provider response: task_id=%s status_code=%d body_bytes=%d body_sha256=%x",
		task.TaskID,
		resp.StatusCode,
		len(responseBody),
		responseDigest,
	)

	snap := task.Snapshot()

	taskResult := &relaycommon.TaskInfo{}
	// try parse as New API response format
	var responseItems taskdto.TaskResponse[model.Task]
	if err = common.Unmarshal(responseBody, &responseItems); err == nil && responseItems.IsSuccess() {
		logger.LogDebug(ctx, "updateVideoSingleTask parsed provider response: task_id=%s format=new-api", task.TaskID)
		t := responseItems.Data
		if isPlatformExternal {
			if err := validateProtectedTaskResponseEnvelope(task, t); err != nil {
				return persistPlatformGenerationProviderProofConflict(task, snap.Status, responseBody)
			}
		}
		taskResult.TaskID = t.TaskID
		taskResult.Status = string(t.Status)
		taskResult.Url = t.GetResultURL()
		taskResult.Progress = t.Progress
		taskResult.Reason = t.FailReason
		task.Data = t.Data
	} else if taskResult, err = adaptor.ParseTaskResult(responseBody); err != nil {
		if isPlatformExternal {
			return persistPlatformGenerationProviderProofConflict(task, snap.Status, responseBody)
		}
		return fmt.Errorf("parseTaskResult failed for task %s: %w", taskId, err)
	}
	var providerResultReceipt *model.PlatformGenerationProviderResultReceipt
	if isPlatformExternal {
		if err := validateProtectedTaskResult(task, taskResult); err != nil {
			return persistPlatformGenerationProviderProofConflict(task, snap.Status, responseBody)
		}
		if taskResult.Status == string(model.TaskStatusSuccess) || taskResult.Status == string(model.TaskStatusFailure) {
			receipt, err := buildPlatformGenerationProviderResultReceipt(task, taskResult, responseBody)
			if err != nil {
				return persistPlatformGenerationProviderProofConflict(task, snap.Status, responseBody)
			}
			providerResultReceipt = &receipt
		}
	}

	if isPlatformExternal {
		// Platform-owned polling rows are observable through the native operator
		// API while artifact transfer is still pending. Persist only a
		// secret-free receipt; the signed provider URL remains solely in the
		// private short-lived ResultURL used by the transfer worker.
		if providerResultReceipt != nil {
			task.SetData(*providerResultReceipt)
		} else {
			task.SetPlatformExternalResponseReceipt(responseBody, taskResult.Status)
		}
	} else {
		task.Data = redactedResponseBody
	}

	logger.LogDebug(
		ctx,
		"updateVideoSingleTask parsed result: task_id=%s status=%s progress=%s",
		task.TaskID,
		taskResult.Status,
		taskResult.Progress,
	)
	if isPlatformExternal && taskResult.Status == string(model.TaskStatusSuccess) &&
		strings.HasPrefix(strings.ToLower(strings.TrimSpace(taskResult.Url)), "data:") {
		taskResult.Status = string(model.TaskStatusFailure)
		taskResult.Progress = taskcommon.ProgressComplete
		taskResult.Reason = "provider returned an unsupported inline artifact"
		taskResult.Url = ""
	}

	now := time.Now().Unix()
	if taskResult.Status == "" {
		//taskResult = relaycommon.FailTaskInfo("upstream returned empty status")
		errorResult := &dto.GeneralErrorResponse{}
		if err = common.Unmarshal(responseBody, &errorResult); err == nil {
			openaiError := errorResult.TryToOpenAIError()
			if openaiError != nil {
				// 返回规范的 OpenAI 错误格式，提取错误信息，判断错误是否为任务失败
				if openaiError.Code == "429" {
					// 429 错误通常表示请求过多或速率限制，暂时不认为是任务失败，保持原状态等待下一轮轮询
					return nil
				}

				// 其他错误认为是任务失败，记录错误信息并更新任务状态
				taskResult = relaycommon.FailTaskInfo("upstream returned error")
			} else {
				logger.LogError(ctx, fmt.Sprintf(
					"Task %s returned empty status with unrecognized error format (body_bytes=%d body_sha256=%x)",
					taskId,
					len(responseBody),
					responseDigest,
				))
				taskResult = relaycommon.FailTaskInfo("upstream returned unrecognized message")
			}
		}
	}

	shouldRefund := false
	shouldSettle := false
	quota := task.Quota

	task.Status = model.TaskStatus(taskResult.Status)
	switch taskResult.Status {
	case model.TaskStatusSubmitted:
		task.Progress = taskcommon.ProgressSubmitted
	case model.TaskStatusQueued:
		task.Progress = taskcommon.ProgressQueued
	case model.TaskStatusInProgress:
		task.Progress = taskcommon.ProgressInProgress
		if task.StartTime == 0 {
			task.StartTime = now
		}
	case model.TaskStatusSuccess:
		task.Progress = taskcommon.ProgressComplete
		if task.FinishTime == 0 {
			task.FinishTime = now
		}
		if strings.HasPrefix(taskResult.Url, "data:") {
			// data: URI (e.g. Vertex base64 encoded video) — keep in Data, not in ResultURL
			task.PrivateData.ResultURL = taskcommon.BuildProxyURL(task.TaskID)
		} else if taskResult.Url != "" {
			// Direct upstream URL (e.g. Kling, Ali, Doubao, etc.)
			task.PrivateData.ResultURL = taskResult.Url
		} else {
			// No URL from adaptor — construct proxy URL using public task ID
			task.PrivateData.ResultURL = taskcommon.BuildProxyURL(task.TaskID)
		}
		shouldSettle = true
	case model.TaskStatusFailure:
		task.Status = model.TaskStatusFailure
		task.Progress = taskcommon.ProgressComplete
		if task.FinishTime == 0 {
			task.FinishTime = now
		}
		task.FailReason = taskResult.Reason
		if isPlatformExternal {
			reasonDigest := sha256.Sum256([]byte(taskResult.Reason))
			task.FailReason = model.SanitizePlatformExternalFailReason(taskResult.Reason)
			logger.LogInfo(ctx, fmt.Sprintf(
				"Protected task %s failed (reason_sha256=%x)",
				task.TaskID,
				reasonDigest,
			))
		} else {
			logger.LogJson(ctx, fmt.Sprintf("Task %s failed", taskId), task)
			logger.LogInfo(ctx, fmt.Sprintf("Task %s failed: %s", task.TaskID, task.FailReason))
		}
		taskResult.Progress = taskcommon.ProgressComplete
		if quota != 0 {
			shouldRefund = true
		}
	default:
		return fmt.Errorf("unknown task status %s for task %s", taskResult.Status, task.TaskID)
	}
	if taskResult.Progress != "" {
		task.Progress = taskResult.Progress
	}

	isDone := task.Status == model.TaskStatusSuccess || task.Status == model.TaskStatusFailure
	if isDone && snap.Status != task.Status {
		won, err := task.UpdateWithStatus(snap.Status)
		if err != nil {
			logger.LogError(ctx, fmt.Sprintf("UpdateWithStatus failed for task %s: %s", task.TaskID, err.Error()))
			shouldRefund = false
			shouldSettle = false
		} else if !won {
			logger.LogWarn(ctx, fmt.Sprintf("Task %s CAS lost or no-op update, skip billing", task.TaskID))
			shouldRefund = false
			shouldSettle = false
		}
	} else if !snap.Equal(task.Snapshot()) {
		if _, err := task.UpdateWithStatus(snap.Status); err != nil {
			logger.LogError(ctx, fmt.Sprintf("Failed to update task %s: %s", task.TaskID, err.Error()))
		}
	} else {
		// No changes, skip update
		logger.LogDebug(ctx, "No update needed for task %s", task.TaskID)
	}

	if shouldSettle {
		settleTaskBillingOnComplete(ctx, adaptor, task, taskResult)
	}
	if shouldRefund {
		RefundTaskQuota(ctx, task, task.FailReason)
	}

	return nil
}

func validateProtectedTaskResponseEnvelope(expected *model.Task, received model.Task) error {
	if expected == nil {
		return errors.New("protected task identity is missing")
	}
	expectedUpstreamTaskID := strings.TrimSpace(expected.GetUpstreamTaskID())
	if expectedUpstreamTaskID == "" || received.TaskID != expectedUpstreamTaskID {
		return errors.New("protected task response identity is inconsistent")
	}
	switch received.Status {
	case model.TaskStatusSuccess, model.TaskStatusFailure:
		if received.Progress != taskcommon.ProgressComplete {
			return errors.New("protected task terminal response progress is inconsistent")
		}
	case model.TaskStatusNotStart, model.TaskStatusSubmitted, model.TaskStatusQueued, model.TaskStatusInProgress:
		if received.Progress == taskcommon.ProgressComplete {
			return errors.New("protected task non-terminal response progress is inconsistent")
		}
	default:
		return errors.New("protected task response status is invalid")
	}
	return nil
}

func validateProtectedTaskResult(expected *model.Task, result *relaycommon.TaskInfo) error {
	if expected == nil || result == nil {
		return errors.New("protected task result is missing")
	}
	expectedUpstreamTaskID := strings.TrimSpace(expected.GetUpstreamTaskID())
	if expectedUpstreamTaskID == "" {
		return errors.New("protected task upstream identity is missing")
	}
	if result.TaskID != expectedUpstreamTaskID {
		return errors.New("protected task result identity is inconsistent")
	}
	switch model.TaskStatus(result.Status) {
	case model.TaskStatusSuccess:
		if result.Progress != taskcommon.ProgressComplete {
			return errors.New("protected task success progress is inconsistent")
		}
		resultURL := strings.TrimSpace(result.Url)
		if !strings.HasPrefix(strings.ToLower(resultURL), "data:") && !absoluteHTTPProviderURL(resultURL) {
			return errors.New("protected task result URL is invalid")
		}
		if err := validatePlatformGenerationProviderResultProof(expected, result, true); err != nil {
			return err
		}
	case model.TaskStatusFailure:
		if result.Progress != taskcommon.ProgressComplete {
			return errors.New("protected task failure progress is inconsistent")
		}
		if err := validatePlatformGenerationProviderResultProof(expected, result, false); err != nil {
			return err
		}
	case model.TaskStatusNotStart, model.TaskStatusSubmitted, model.TaskStatusQueued, model.TaskStatusInProgress:
		if result.Progress == taskcommon.ProgressComplete {
			return errors.New("protected task non-terminal progress is inconsistent")
		}
		if err := validatePlatformGenerationProviderIdentityProof(expected, result); err != nil {
			return err
		}
	default:
		return errors.New("protected task result status is invalid")
	}
	return nil
}

func validatePlatformGenerationProviderIdentityProof(expected *model.Task, result *relaycommon.TaskInfo) error {
	binding, err := model.ResolvePlatformGenerationProviderResultBinding(*expected)
	if err != nil {
		return errors.New("protected task result database binding is unavailable")
	}
	_, profile, _, err := model.ResolvePlatformGenerationProviderResultContract(binding)
	if err != nil {
		return errors.New("protected task immutable result contract is inconsistent")
	}
	_, supported := model.PlatformGenerationProviderResultProofRevision(profile.Protocol)
	proof := result.ProviderResultProof
	if proof == nil || proof.SchemaVersion != 1 ||
		!supported || proof.Protocol != profile.Protocol ||
		proof.TaskID != expected.GetUpstreamTaskID() || proof.Model != binding.Route.UpstreamModel {
		return errors.New("protected task provider identity proof is incomplete")
	}
	switch proof.ProviderStatus {
	case "pending", "queued", "processing", "running":
		return nil
	default:
		return errors.New("protected task provider non-terminal proof is inconsistent")
	}
}

func buildPlatformGenerationProviderResultReceipt(
	task *model.Task,
	result *relaycommon.TaskInfo,
	responseBody []byte,
) (model.PlatformGenerationProviderResultReceipt, error) {
	if task == nil || result == nil || result.ProviderResultProof == nil {
		return model.PlatformGenerationProviderResultReceipt{}, errors.New("protected task provider proof is missing")
	}
	binding, err := model.ResolvePlatformGenerationProviderResultBinding(*task)
	if err != nil {
		return model.PlatformGenerationProviderResultReceipt{}, err
	}
	proof := result.ProviderResultProof
	return model.NewPlatformGenerationProviderResultReceipt(
		*task,
		binding,
		model.PlatformGenerationProviderResultObservation{
			Protocol:             proof.Protocol,
			TaskID:               proof.TaskID,
			Model:                proof.Model,
			ProviderStatus:       proof.ProviderStatus,
			Resolution:           proof.Resolution,
			DurationSeconds:      proof.DurationSeconds,
			AspectRatio:          proof.AspectRatio,
			FramesPresent:        proof.FramesPresent,
			OutputCount:          proof.OutputCount,
			MediaType:            proof.MediaType,
			UsageComplete:        proof.UsageComplete,
			InputTokens:          proof.InputTokens,
			OutputTokens:         proof.OutputTokens,
			OutputVideoTokens:    proof.OutputVideoTokens,
			OutputTextTokens:     proof.OutputTextTokens,
			ThoughtTokens:        proof.ThoughtTokens,
			CachedTokens:         proof.CachedTokens,
			TotalTokens:          proof.TotalTokens,
			ProviderTotalSeconds: proof.ProviderTotalSeconds,
			InputSeconds:         proof.InputSeconds,
			OutputSeconds:        proof.OutputSeconds,
			InputImageCount:      proof.InputImageCount,
			ArtifactSizeBytes:    proof.ArtifactSizeBytes,
			ArtifactSHA256:       proof.ArtifactSHA256,
			FailureOwner:         proof.FailureOwner,
			FailureCode:          proof.FailureCode,
			Succeeded:            result.Status == string(model.TaskStatusSuccess),
			ResponseBody:         responseBody,
			ArtifactURL:          result.Url,
		},
	)
}

func persistPlatformGenerationProviderProofConflict(task *model.Task, fromStatus model.TaskStatus, responseBody []byte) error {
	if task == nil {
		return errors.New("protected task provider proof conflict task is missing")
	}
	task.Status = model.TaskStatusUnknown
	task.Progress = "0%"
	task.FinishTime = 0
	task.FailReason = ""
	task.PrivateData.ResultURL = ""
	receipt := model.NewPlatformGenerationProviderProofConflictReceipt(responseBody)
	if binding, err := model.ResolvePlatformGenerationProviderResultBinding(*task); err == nil {
		if _, profile, _, err := model.ResolvePlatformGenerationProviderResultContract(binding); err == nil {
			if boundReceipt, err := model.NewPlatformGenerationProviderProofConflictReceiptForProtocol(profile.Protocol, responseBody); err == nil {
				receipt = boundReceipt
			}
		}
	}
	task.SetData(receipt)
	won, err := task.UpdateWithStatus(fromStatus)
	if err != nil {
		return errors.New("protected task provider proof conflict could not be persisted")
	}
	if !won {
		return errors.New("protected task provider proof conflict lost its task fence")
	}
	return nil
}

func validatePlatformGenerationProviderResultProof(
	task *model.Task,
	result *relaycommon.TaskInfo,
	succeeded bool,
) error {
	binding, err := model.ResolvePlatformGenerationProviderResultBinding(*task)
	if err != nil {
		return errors.New("protected task result database binding is unavailable")
	}
	request, profile, artifact, err := model.ResolvePlatformGenerationProviderResultContract(binding)
	if err != nil {
		return errors.New("protected task immutable result contract is inconsistent")
	}
	_, supported := model.PlatformGenerationProviderResultProofRevision(profile.Protocol)
	proof := result.ProviderResultProof
	if proof == nil || proof.SchemaVersion != 1 ||
		!supported || proof.Protocol != profile.Protocol ||
		proof.TaskID != task.GetUpstreamTaskID() || proof.Model != binding.Route.UpstreamModel {
		return errors.New("protected task provider result proof is incomplete")
	}
	if !succeeded {
		switch proof.ProviderStatus {
		case "failed", "cancelled", "expired":
			return nil
		default:
			return errors.New("protected task provider failure proof is inconsistent")
		}
	}
	if artifact.MediaType != "video" || artifact.Count != 1 ||
		request.Output.Count != 1 || proof.ProviderStatus != "succeeded" ||
		proof.Resolution != request.Output.Resolution ||
		proof.DurationSeconds != request.Output.DurationSeconds ||
		proof.AspectRatio != request.Output.AspectRatio || proof.FramesPresent ||
		proof.OutputCount != request.Output.Count || proof.MediaType != artifact.MediaType {
		return errors.New("protected task provider success proof is inconsistent")
	}
	return nil
}

func redactVideoResponseBody(body []byte, redactProviderURLs bool) []byte {
	var decoded any
	if err := common.Unmarshal(body, &decoded); err != nil {
		return nil
	}
	if root, ok := decoded.(map[string]any); ok {
		resp, _ := root["response"].(map[string]any)
		if resp != nil {
			delete(resp, "bytesBase64Encoded")
			if v, ok := resp["video"].(string); ok && !absoluteHTTPProviderURL(v) {
				resp["video"] = truncateBase64(v)
			}
			if vs, ok := resp["videos"].([]any); ok {
				for i := range vs {
					if vm, ok := vs[i].(map[string]any); ok {
						delete(vm, "bytesBase64Encoded")
					}
				}
			}
		}
	}
	if redactProviderURLs {
		redactAbsoluteProviderURLs(decoded)
	}
	b, err := common.Marshal(decoded)
	if err != nil {
		return nil
	}
	return b
}

func redactAbsoluteProviderURLs(value any) {
	switch node := value.(type) {
	case map[string]any:
		for key, child := range node {
			if text, ok := child.(string); ok && absoluteHTTPProviderURL(text) {
				node[key] = "[redacted-provider-url]"
				continue
			}
			redactAbsoluteProviderURLs(child)
		}
	case []any:
		for index, child := range node {
			if text, ok := child.(string); ok && absoluteHTTPProviderURL(text) {
				node[index] = "[redacted-provider-url]"
				continue
			}
			redactAbsoluteProviderURLs(child)
		}
	}
}

func absoluteHTTPProviderURL(value string) bool {
	candidate := strings.TrimSpace(value)
	if candidate == "" || strings.ContainsAny(candidate, "\x00\r\n") {
		return false
	}
	parsed, err := url.ParseRequestURI(candidate)
	if err != nil || parsed.Host == "" {
		return false
	}
	return strings.EqualFold(parsed.Scheme, "https") || strings.EqualFold(parsed.Scheme, "http")
}

func truncateBase64(s string) string {
	const maxKeep = 256
	if len(s) <= maxKeep {
		return s
	}
	return s[:maxKeep] + "..."
}

// settleTaskBillingOnComplete 任务完成时的统一计费调整。
// 优先级：1. adaptor.AdjustBillingOnComplete 返回正数 → 使用 adaptor 计算的额度
//
//  2. taskResult.TotalTokens > 0 → 按 token 重算
//  3. 都不满足 → 保持预扣额度不变
func settleTaskBillingOnComplete(ctx context.Context, adaptor TaskPollingAdaptor, task *model.Task, taskResult *relaycommon.TaskInfo) {
	if protectedNativeBillingMustFailClosed(ctx, task) {
		return
	}
	// Platform owns the only customer ledger for fenced generation jobs. The
	// native Task row remains solely a provider polling/recovery record; even a
	// non-zero adaptor/token estimate must never enter new-api billing.
	if taskIsPlatformExternalBilling(task) {
		if task.Quota != 0 {
			logger.LogError(ctx, fmt.Sprintf("Platform-owned task %s has forbidden native quota state", task.TaskID))
		}
		return
	}
	// 0. 按次计费的任务不做差额结算
	if bc := task.PrivateData.BillingContext; bc != nil && bc.PerCallBilling {
		logger.LogInfo(ctx, fmt.Sprintf("任务 %s 按次计费，跳过差额结算", task.TaskID))
		return
	}
	// 1. 优先让 adaptor 决定最终额度
	if actualQuota := adaptor.AdjustBillingOnComplete(task, taskResult); actualQuota > 0 {
		RecalculateTaskQuota(ctx, task, actualQuota, "adaptor计费调整")
		return
	}
	// 2. 回退到 token 重算
	if taskResult.TotalTokens > 0 {
		RecalculateTaskQuotaByTokens(ctx, task, taskResult.TotalTokens)
		return
	}
	// 3. 无调整，保持预扣额度
}
