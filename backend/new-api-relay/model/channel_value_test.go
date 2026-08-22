package model

import (
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/stretchr/testify/require"
)

func TestChannelInfoValueIsTextJSON(t *testing.T) {
	info := ChannelInfo{
		IsMultiKey:             true,
		MultiKeySize:           2,
		MultiKeyStatusList:     map[int]int{0: 1, 1: 2},
		MultiKeyDisabledReason: map[int]string{1: "integration"},
		MultiKeyDisabledTime:   map[int]int64{1: 1234},
		MultiKeyPollingIndex:   1,
		MultiKeyMode:           constant.MultiKeyModePolling,
	}

	value, err := info.Value()
	require.NoError(t, err)
	text, ok := value.(string)
	require.True(t, ok, "JSON driver value must be text, got %T", value)

	var decoded ChannelInfo
	require.NoError(t, decoded.Scan(text))
	require.Equal(t, info, decoded)
	require.NoError(t, decoded.Scan([]byte(text)))
	require.Equal(t, info, decoded)
}
