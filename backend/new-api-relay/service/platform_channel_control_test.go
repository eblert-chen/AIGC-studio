package service

import (
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/stretchr/testify/assert"
)

func TestIsChannelTestSupportedRejectsVideoOnlySora(t *testing.T) {
	assert.False(t, IsChannelTestSupported(constant.ChannelTypeSora), "Sora only implements the OpenAI video endpoint and must never be probed through chat/completions")
	assert.True(t, IsChannelTestSupported(constant.ChannelTypeOpenAI), "ordinary OpenAI-compatible chat channels retain native connectivity tests")
}
