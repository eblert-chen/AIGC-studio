package controller

import (
	"testing"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relay"
	relaycommon "github.com/QuantumNous/new-api/relay/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/stretchr/testify/require"
)

func TestApplyTaskSubmitResultPersistsPlatformOwnedImmediateTerminal(t *testing.T) {
	task := &model.Task{
		SubmitTime: 100,
		Status:     model.TaskStatusNotStart,
		Progress:   "0%",
		Quota:      999,
		PrivateData: model.TaskPrivateData{
			BillingSource:  "wallet",
			SubscriptionId: 17,
			TokenId:        23,
			BillingContext: &model.TaskBillingContext{ModelPrice: 10},
		},
	}
	result := &relay.TaskSubmitResult{
		UpstreamTaskID: "seedream:0123456789abcdef",
		TaskData:       []byte(`{"id":"seedream:0123456789abcdef","data":[{"url":"https://provider.example/result.png?X-Signature=secret"}]}`),
		Quota:          777,
		ImmediateTerminal: &relaycommon.TaskInfo{
			TaskID:   "seedream:0123456789abcdef",
			Status:   string(model.TaskStatusSuccess),
			Progress: "100%",
			Url:      "https://provider.example/result.png?X-Signature=secret",
		},
	}
	info := &relaycommon.RelayInfo{
		TaskRelayInfo: &relaycommon.TaskRelayInfo{Action: constant.TaskActionGenerate},
	}

	applyTaskSubmitResultForInsert(task, result, info, true, "relay-node-test", 125)

	require.Equal(t, model.TaskStatus(model.TaskStatusSuccess), task.Status)
	require.Equal(t, "100%", task.Progress)
	require.EqualValues(t, 100, task.StartTime)
	require.EqualValues(t, 125, task.FinishTime)
	require.Equal(t, "https://provider.example/result.png?X-Signature=secret", task.PrivateData.ResultURL)
	require.Equal(t, result.UpstreamTaskID, task.PrivateData.UpstreamTaskID)
	require.Equal(t, "relay-node-test", task.PrivateData.NodeName)
	require.Equal(t, service.BillingSourcePlatformExternal, task.PrivateData.BillingSource)
	require.Zero(t, task.PrivateData.SubscriptionId)
	require.Zero(t, task.PrivateData.TokenId)
	require.Nil(t, task.PrivateData.BillingContext)
	require.Zero(t, task.Quota)
	require.Contains(t, string(task.Data), `"body_sha256":"sha256:`)
	require.NotContains(t, string(task.Data), "provider.example")
	require.NotContains(t, string(task.Data), "X-Signature")
	require.Empty(t, task.GetPublicResultURL())
	serialized, err := common.Marshal(task)
	require.NoError(t, err)
	require.NotContains(t, string(serialized), "provider.example")
	require.NotContains(t, string(serialized), "X-Signature")
	publicDTO := relay.TaskModel2Dto(task)
	require.Empty(t, publicDTO.ResultURL)
	require.Empty(t, publicDTO.Data)
	require.Equal(t, constant.TaskActionGenerate, task.Action)
}

func TestApplyTaskSubmitResultLeavesAsyncTaskNonTerminal(t *testing.T) {
	task := &model.Task{SubmitTime: 100, Status: model.TaskStatusNotStart, Progress: "0%"}
	result := &relay.TaskSubmitResult{UpstreamTaskID: "video-provider-task", Quota: 9}
	info := &relaycommon.RelayInfo{TaskRelayInfo: &relaycommon.TaskRelayInfo{Action: constant.TaskActionGenerate}}

	applyTaskSubmitResultForInsert(task, result, info, true, "relay-node-test", 125)

	require.Equal(t, model.TaskStatusNotStart, task.Status)
	require.Equal(t, "0%", task.Progress)
	require.Zero(t, task.FinishTime)
	require.Empty(t, task.PrivateData.ResultURL)
}
