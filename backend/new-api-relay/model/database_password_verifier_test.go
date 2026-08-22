package model

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestGenerateRelaySCRAMSHA256VerifierKnownVector(t *testing.T) {
	password := []byte("correct-horse-battery-staple-2026")
	salt := []byte{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}

	verifier, err := generateRelaySCRAMSHA256VerifierWithSalt(password, salt)
	require.NoError(t, err)
	require.Equal(t,
		"SCRAM-SHA-256$4096:AAECAwQFBgcICQoLDA0ODw==$5XplHSQgDky+8MUJDknWhdKms/Zoyud6lt/oRVtBcJQ=:JHoUcwr02H3NZ1AZ5ZPeTV3hZdsYOVTITi5mEPo1GG0=",
		verifier,
	)
}

func TestGenerateRelaySCRAMSHA256VerifierRequiresHighEntropyBase64URLPassword(t *testing.T) {
	tests := []struct {
		name     string
		password string
	}{
		{name: "short", password: "short-base64url-password"},
		{name: "unicode_requires_saslprep", password: "valid_length_but_unicode_password_é"},
		{name: "whitespace", password: "valid_length_password_with_space_x "},
		{name: "base64_padding", password: "valid_length_password_with_padding=="},
		{name: "low_diversity", password: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
		{name: "short_period", password: "Ab3_def-Ab3_def-Ab3_def-Ab3_def-"},
		{name: "placeholder", password: "replace-me-with-database-role-secret"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			verifier, err := GenerateRelaySCRAMSHA256Verifier([]byte(test.password))
			require.Error(t, err)
			require.Empty(t, verifier)
			require.NotContains(t, err.Error(), test.password)
		})
	}
}
