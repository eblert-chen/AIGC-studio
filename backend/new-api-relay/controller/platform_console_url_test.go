package controller

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestPlatformConsoleURLFromBaseURL(t *testing.T) {
	tests := []struct {
		name string
		raw  string
		want string
	}{
		{
			name: "production HTTPS origin",
			raw:  "https://platform.example.com",
			want: "https://platform.example.com/platform",
		},
		{
			name: "local development origin",
			raw:  "http://127.0.0.1:5173/",
			want: "http://127.0.0.1:5173/platform",
		},
		{
			name: "local IPv6 development origin",
			raw:  "http://[::1]:5173",
			want: "http://[::1]:5173/platform",
		},
		{name: "missing origin", raw: "", want: ""},
		{name: "reject non-loopback HTTP", raw: "http://platform.example.com", want: ""},
		{name: "reject credentials", raw: "https://user:secret@platform.example.com", want: ""},
		{name: "reject existing path", raw: "https://platform.example.com/admin", want: ""},
		{name: "reject query data", raw: "https://platform.example.com?token=secret", want: ""},
		{name: "reject unsupported scheme", raw: "javascript:alert(1)", want: ""},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			assert.Equal(t, tt.want, platformConsoleURLFromBaseURL(tt.raw))
		})
	}
}
