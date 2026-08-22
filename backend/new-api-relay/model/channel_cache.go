package model

import (
	"errors"
	"fmt"
	"math/rand"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/logger"
	"github.com/QuantumNous/new-api/relaykit/dto"
	"github.com/QuantumNous/new-api/setting/ratio_setting"
	"gorm.io/gorm"
)

var group2model2channels map[string]map[string][]int // enabled channel
var channelsIDM map[int]*Channel                     // all channels include disabled
// channel2advancedCustomConfig caches parsed Advanced Custom (type 58) configs so
// path-aware selection avoids re-parsing JSON per request. Refreshed on full sync.
var channel2advancedCustomConfig map[int]*dto.AdvancedCustomConfig
var channelSyncLock sync.RWMutex

var channelCacheSecretFreeColumns = []string{
	"id", "type", "credential_set_version", "open_ai_organization", "test_model",
	"status", "name", "weight", "created_time", "test_time", "response_time",
	"base_url", "other", "balance", "balance_updated_time", "models", "group",
	"used_quota", "model_mapping", "status_code_mapping", "priority", "auto_ban",
	"other_info", "tag", "setting", "param_override", "header_override", "remark",
	"channel_info", "settings", "control_revision",
}

func InitChannelCache() {
	if !common.MemoryCacheEnabled {
		InvalidatePricingCache()
		return
	}
	newChannelId2channel := make(map[int]*Channel)
	newChannel2advancedCustomConfig := make(map[int]*dto.AdvancedCustomConfig)
	var channels []*Channel
	// Global cache entries are deliberately secret-free. Keep the opaque
	// current-version pointer, skip AfterFind hydration, and decrypt only a
	// selected clone after channelSyncLock is released.
	DB.Session(&gorm.Session{SkipHooks: true}).Select(channelCacheSecretFreeColumns).Find(&channels)
	for _, channel := range channels {
		newChannelId2channel[channel.Id] = cloneSecretFreeChannel(channel)
		if channel.Type == constant.ChannelTypeAdvancedCustom {
			if config := channel.GetOtherSettings().AdvancedCustom; config != nil {
				newChannel2advancedCustomConfig[channel.Id] = config
			}
		}
	}
	var abilities []*Ability
	DB.Find(&abilities)
	groups := make(map[string]bool)
	for _, ability := range abilities {
		groups[ability.Group] = true
	}
	newGroup2model2channels := make(map[string]map[string][]int)
	for group := range groups {
		newGroup2model2channels[group] = make(map[string][]int)
	}
	for _, channel := range channels {
		if channel.Status != common.ChannelStatusEnabled {
			continue // skip disabled channels
		}
		groups := strings.Split(channel.Group, ",")
		for _, group := range groups {
			models := strings.Split(channel.Models, ",")
			for _, model := range models {
				if _, ok := newGroup2model2channels[group][model]; !ok {
					newGroup2model2channels[group][model] = make([]int, 0)
				}
				newGroup2model2channels[group][model] = append(newGroup2model2channels[group][model], channel.Id)
			}
		}
	}

	// sort by priority
	for group, model2channels := range newGroup2model2channels {
		for model, channels := range model2channels {
			sort.Slice(channels, func(i, j int) bool {
				return newChannelId2channel[channels[i]].GetPriority() > newChannelId2channel[channels[j]].GetPriority()
			})
			newGroup2model2channels[group][model] = channels
		}
	}

	channelSyncLock.Lock()
	group2model2channels = newGroup2model2channels
	//channelsIDM = newChannelId2channel
	for i, channel := range newChannelId2channel {
		if channel.ChannelInfo.IsMultiKey {
			if channel.ChannelInfo.MultiKeyMode == constant.MultiKeyModePolling {
				if oldChannel, ok := channelsIDM[i]; ok {
					// 存在旧的渠道，如果是多key且轮询，保留轮询索引信息
					if oldChannel.ChannelInfo.IsMultiKey && oldChannel.ChannelInfo.MultiKeyMode == constant.MultiKeyModePolling {
						channel.ChannelInfo.MultiKeyPollingIndex = oldChannel.ChannelInfo.MultiKeyPollingIndex
					}
				}
			}
		}
	}
	channelsIDM = newChannelId2channel
	channel2advancedCustomConfig = newChannel2advancedCustomConfig
	channelSyncLock.Unlock()
	// Lock ordering: InvalidatePricingCache acquires updatePricingLock, and
	// GetPricing (holding updatePricingLock) nests channelSyncLock.RLock via
	// loadPricingAdvancedCustomConfigs. channelSyncLock MUST be released before
	// invalidating the pricing cache, otherwise the reversed order deadlocks.
	InvalidatePricingCache()
	common.SysLog("channels synced from database")
}

func SyncChannelCache(frequency int) {
	for {
		time.Sleep(time.Duration(frequency) * time.Second)
		common.SysLog("syncing channels from database")
		InitChannelCache()
	}
}

func GetRandomSatisfiedChannel(group string, model string, retry int, requestPath string) (*Channel, error) {
	// if memory cache is disabled, get channel directly from database
	if !common.MemoryCacheEnabled {
		return GetChannel(group, model, retry, requestPath)
	}

	channelSyncLock.RLock()
	channel, err := getRandomSatisfiedChannelFromCacheLocked(group, model, retry, requestPath)
	channelSyncLock.RUnlock()
	if err != nil || channel == nil {
		return channel, err
	}
	if err := HydrateChannelCredential(DB, channel); err != nil {
		return nil, err
	}
	return channel, nil
}

// getRandomSatisfiedChannelFromCacheLocked performs no database or crypto I/O
// and returns a deep secret-free clone. Its caller releases channelSyncLock
// before hydrating provider material.
func getRandomSatisfiedChannelFromCacheLocked(group string, model string, retry int, requestPath string) (*Channel, error) {

	// First, try to find channels with the exact model name.
	channels := filterChannelsByRequestPathAndModel(group2model2channels[group][model], requestPath, model)

	// If no channels found, try to find channels with the normalized model name.
	if len(channels) == 0 {
		normalizedModel := ratio_setting.FormatMatchingModelName(model)
		channels = filterChannelsByRequestPathAndModel(group2model2channels[group][normalizedModel], requestPath, model)
	}

	if len(channels) == 0 {
		return nil, nil
	}

	if len(channels) == 1 {
		if channel, ok := channelsIDM[channels[0]]; ok {
			return cloneSecretFreeChannel(channel), nil
		}
		return nil, fmt.Errorf("数据库一致性错误，渠道# %d 不存在，请联系管理员修复", channels[0])
	}

	uniquePriorities := make(map[int]bool)
	for _, channelId := range channels {
		if channel, ok := channelsIDM[channelId]; ok {
			uniquePriorities[int(channel.GetPriority())] = true
		} else {
			return nil, fmt.Errorf("数据库一致性错误，渠道# %d 不存在，请联系管理员修复", channelId)
		}
	}
	var sortedUniquePriorities []int
	for priority := range uniquePriorities {
		sortedUniquePriorities = append(sortedUniquePriorities, priority)
	}
	sort.Sort(sort.Reverse(sort.IntSlice(sortedUniquePriorities)))

	if retry >= len(uniquePriorities) {
		retry = len(uniquePriorities) - 1
	}
	targetPriority := int64(sortedUniquePriorities[retry])

	// get the priority for the given retry number
	var sumWeight = 0
	var targetChannels []*Channel
	for _, channelId := range channels {
		if channel, ok := channelsIDM[channelId]; ok {
			if channel.GetPriority() == targetPriority {
				sumWeight += channel.GetWeight()
				targetChannels = append(targetChannels, channel)
			}
		} else {
			return nil, fmt.Errorf("数据库一致性错误，渠道# %d 不存在，请联系管理员修复", channelId)
		}
	}

	if len(targetChannels) == 0 {
		return nil, errors.New(fmt.Sprintf("no channel found, group: %s, model: %s, priority: %d", group, model, targetPriority))
	}

	// smoothing factor and adjustment
	smoothingFactor := 1
	smoothingAdjustment := 0

	if sumWeight == 0 {
		// when all channels have weight 0, set sumWeight to the number of channels and set smoothing adjustment to 100
		// each channel's effective weight = 100
		sumWeight = len(targetChannels) * 100
		smoothingAdjustment = 100
	} else if sumWeight/len(targetChannels) < 10 {
		// when the average weight is less than 10, set smoothing factor to 100
		smoothingFactor = 100
	}

	// Calculate the total weight of all channels up to endIdx
	totalWeight := sumWeight * smoothingFactor

	// Generate a random value in the range [0, totalWeight)
	randomWeight := rand.Intn(totalWeight)

	// Find a channel based on its weight
	for _, channel := range targetChannels {
		randomWeight -= channel.GetWeight()*smoothingFactor + smoothingAdjustment
		if randomWeight < 0 {
			return cloneSecretFreeChannel(channel), nil
		}
	}
	// return null if no channel is not found
	return nil, errors.New("channel not found")
}

// filterChannelsByRequestPathAndModel restricts candidates by request path and
// model. Only Advanced Custom (type 58) channels are path-checked: they are kept
// only when one of their configured routes matches requestPath and model. All
// other channel types always pass. When requestPath is empty, filtering is skipped.
// Caller must hold channelSyncLock (read lock). The cached slice is never mutated.
func filterChannelsByRequestPathAndModel(channels []int, requestPath string, model string) []int {
	if requestPath == "" || len(channels) == 0 {
		return channels
	}
	filtered := make([]int, 0, len(channels))
	for _, channelId := range channels {
		channel, ok := channelsIDM[channelId]
		if !ok {
			// keep it so the downstream consistency error is raised as before
			filtered = append(filtered, channelId)
			continue
		}
		if channel.Type != constant.ChannelTypeAdvancedCustom {
			filtered = append(filtered, channelId)
			continue
		}
		if config := channel2advancedCustomConfig[channelId]; config != nil && config.SupportsPathForModel(requestPath, model) {
			filtered = append(filtered, channelId)
		}
	}
	return filtered
}

func CacheGetChannel(id int) (*Channel, error) {
	if !common.MemoryCacheEnabled {
		return GetChannelById(id, true)
	}
	channelSyncLock.RLock()

	c, ok := channelsIDM[id]
	if !ok {
		channelSyncLock.RUnlock()
		return nil, fmt.Errorf("渠道# %d，已不存在", id)
	}
	clone := cloneSecretFreeChannel(c)
	channelSyncLock.RUnlock()
	if err := HydrateChannelCredential(DB, clone); err != nil {
		return nil, err
	}
	return clone, nil
}

func CacheGetChannelInfo(id int) (*ChannelInfo, error) {
	if !common.MemoryCacheEnabled {
		channel, err := GetChannelById(id, false)
		if err != nil {
			return nil, err
		}
		info := cloneChannelInfo(channel.ChannelInfo)
		return &info, nil
	}
	channelSyncLock.RLock()

	c, ok := channelsIDM[id]
	if !ok {
		channelSyncLock.RUnlock()
		return nil, fmt.Errorf("渠道# %d，已不存在", id)
	}
	info := cloneChannelInfo(c.ChannelInfo)
	channelSyncLock.RUnlock()
	return &info, nil
}

func CacheUpdateChannelStatus(id int, status int) {
	if !common.MemoryCacheEnabled {
		return
	}
	channelSyncLock.Lock()
	defer channelSyncLock.Unlock()
	if channel, ok := channelsIDM[id]; ok {
		channel.Status = status
	}
	if status != common.ChannelStatusEnabled {
		// delete the channel from group2model2channels
		for group, model2channels := range group2model2channels {
			for model, channels := range model2channels {
				for i, channelId := range channels {
					if channelId == id {
						// remove the channel from the slice
						group2model2channels[group][model] = append(channels[:i], channels[i+1:]...)
						break
					}
				}
			}
		}
	}
}

func CacheUpdateChannel(channel *Channel) {
	if !common.MemoryCacheEnabled {
		return
	}
	channelSyncLock.Lock()
	if channel == nil {
		channelSyncLock.Unlock()
		return
	}

	if channelsIDM == nil {
		channelsIDM = make(map[int]*Channel)
	}
	if oldChannel, ok := channelsIDM[channel.Id]; ok {
		logger.LogDebug(nil, "CacheUpdateChannel before: id=%d, name=%s, status=%d, polling_index=%d", channel.Id, channel.Name, channel.Status, oldChannel.ChannelInfo.MultiKeyPollingIndex)
	}
	secretFree := cloneSecretFreeChannel(channel)
	channelsIDM[channel.Id] = secretFree
	if channel2advancedCustomConfig == nil {
		channel2advancedCustomConfig = make(map[int]*dto.AdvancedCustomConfig)
	}
	delete(channel2advancedCustomConfig, channel.Id)
	if secretFree.Type == constant.ChannelTypeAdvancedCustom {
		if config := secretFree.GetOtherSettings().AdvancedCustom; config != nil {
			channel2advancedCustomConfig[secretFree.Id] = config
		}
	}
	logger.LogDebug(nil, "CacheUpdateChannel after: id=%d, name=%s, status=%d, polling_index=%d", secretFree.Id, secretFree.Name, secretFree.Status, secretFree.ChannelInfo.MultiKeyPollingIndex)
	// Lock ordering: do NOT hold channelSyncLock while calling
	// InvalidatePricingCache. GetPricing acquires updatePricingLock first and then
	// channelSyncLock.RLock (via loadPricingAdvancedCustomConfigs); acquiring
	// updatePricingLock while holding channelSyncLock would be an AB-BA deadlock.
	channelSyncLock.Unlock()
	InvalidatePricingCache()
}

func cloneChannelInfo(info ChannelInfo) ChannelInfo {
	clone := info
	if info.MultiKeyStatusList != nil {
		clone.MultiKeyStatusList = make(map[int]int, len(info.MultiKeyStatusList))
		for key, value := range info.MultiKeyStatusList {
			clone.MultiKeyStatusList[key] = value
		}
	}
	if info.MultiKeyDisabledReason != nil {
		clone.MultiKeyDisabledReason = make(map[int]string, len(info.MultiKeyDisabledReason))
		for key, value := range info.MultiKeyDisabledReason {
			clone.MultiKeyDisabledReason[key] = value
		}
	}
	if info.MultiKeyDisabledTime != nil {
		clone.MultiKeyDisabledTime = make(map[int]int64, len(info.MultiKeyDisabledTime))
		for key, value := range info.MultiKeyDisabledTime {
			clone.MultiKeyDisabledTime[key] = value
		}
	}
	return clone
}

func cloneStringPointer(value *string) *string {
	if value == nil {
		return nil
	}
	clone := *value
	return &clone
}

func cloneIntPointer(value *int) *int {
	if value == nil {
		return nil
	}
	clone := *value
	return &clone
}

func cloneInt64Pointer(value *int64) *int64 {
	if value == nil {
		return nil
	}
	clone := *value
	return &clone
}

func cloneUintPointer(value *uint) *uint {
	if value == nil {
		return nil
	}
	clone := *value
	return &clone
}

func cloneSecretFreeChannel(channel *Channel) *Channel {
	if channel == nil {
		return nil
	}
	clone := *channel
	clone.Key = ""
	clone.LegacyKey = ""
	clone.Keys = nil
	clone.OpenAIOrganization = cloneStringPointer(channel.OpenAIOrganization)
	clone.TestModel = cloneStringPointer(channel.TestModel)
	clone.Weight = cloneUintPointer(channel.Weight)
	clone.BaseURL = cloneStringPointer(channel.BaseURL)
	clone.ModelMapping = cloneStringPointer(channel.ModelMapping)
	clone.StatusCodeMapping = cloneStringPointer(channel.StatusCodeMapping)
	clone.Priority = cloneInt64Pointer(channel.Priority)
	clone.AutoBan = cloneIntPointer(channel.AutoBan)
	clone.Tag = cloneStringPointer(channel.Tag)
	clone.Setting = cloneStringPointer(channel.Setting)
	clone.ParamOverride = cloneStringPointer(channel.ParamOverride)
	clone.HeaderOverride = cloneStringPointer(channel.HeaderOverride)
	clone.Remark = cloneStringPointer(channel.Remark)
	clone.ChannelInfo = cloneChannelInfo(channel.ChannelInfo)
	return &clone
}

// CacheUpdateChannelPollingIndex preserves polling fairness without exposing a
// shared Channel pointer to request goroutines.
func CacheUpdateChannelPollingIndex(id int, next int) {
	if !common.MemoryCacheEnabled {
		return
	}
	channelSyncLock.Lock()
	defer channelSyncLock.Unlock()
	if channel, ok := channelsIDM[id]; ok {
		channel.ChannelInfo.MultiKeyPollingIndex = next
	}
}
