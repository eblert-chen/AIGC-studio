package volcengine

import (
	"testing"

	"github.com/QuantumNous/new-api/constant"
	"github.com/stretchr/testify/require"
)

func TestModelListContainsExactSeedream50LiteOnce(t *testing.T) {
	count := 0
	for _, model := range ModelList {
		if model == constant.PlatformGenerationArkSeedream50Model {
			count++
		}
	}
	require.Equal(t, 1, count)
	require.Equal(t, "doubao-seedream-5-0-260128", constant.PlatformGenerationArkSeedream50Model)
}

func TestModelListDoesNotExposeSeedanceThroughTheChatAdaptor(t *testing.T) {
	for _, model := range ModelList {
		require.NotContains(t, model, "seedance", "Seedance routes are lifecycle-controlled by the async task adaptor manifest")
	}
}
