package common

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestJsonRawMessageToString(t *testing.T) {
	tests := []struct {
		name string
		data json.RawMessage
		want string
	}{
		{
			name: "object",
			data: json.RawMessage(`{"city":"Paris","days":0,"strict":false}`),
			want: `{"city":"Paris","days":0,"strict":false}`,
		},
		{
			name: "string",
			data: json.RawMessage(`"{\"city\":\"Paris\",\"days\":0,\"strict\":false}"`),
			want: `{"city":"Paris","days":0,"strict":false}`,
		},
		{
			name: "null",
			data: json.RawMessage(`null`),
			want: "",
		},
		{
			name: "empty",
			data: nil,
			want: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			require.Equal(t, tt.want, JsonRawMessageToString(tt.data))
		})
	}
}

func TestRejectDuplicateJSONKeysRejectsExactNestedAndCaseAmbiguousKeys(t *testing.T) {
	for _, raw := range []string{
		`{"model":1,"model":2}`,
		`{"outer":{"route_id":"a","route_id":"b"}}`,
		`{"public_model_id":"seedream-5","PUBLIC_MODEL_ID":"tampered"}`,
		`{"signed_at":"2026-08-29T00:00:00Z","ſigned_at":"tampered"}`,
		`{"key_id":"trusted","Key_id":"tampered"}`,
	} {
		require.ErrorContains(t, RejectDuplicateJSONKeys([]byte(raw)), "duplicated")
	}
	require.NoError(t, RejectDuplicateJSONKeys([]byte(`{"model-a":1,"model-b":2}`)))
}
