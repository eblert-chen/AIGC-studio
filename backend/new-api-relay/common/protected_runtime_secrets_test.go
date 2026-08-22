package common

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"os/exec"
	"testing"
	"time"

	"github.com/go-redis/redis/v8"
	"github.com/stretchr/testify/require"
)

func protectedRelayRedisTLSCATestPEM(t *testing.T, serial int64) []byte {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	require.NoError(t, err)
	template := &x509.Certificate{
		SerialNumber: big.NewInt(serial), Subject: pkix.Name{CommonName: "Relay Redis TLS test CA"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, publicKey, privateKey)
	require.NoError(t, err)
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
}

func TestParseProtectedRelayRedisTLSCARequiresCanonicalCertificatePEM(t *testing.T) {
	valid := protectedRelayRedisTLSCATestPEM(t, 1)
	parsed, err := ParseProtectedRelayRedisTLSCA(valid)
	require.NoError(t, err)
	require.True(t, parsed.valid)
	require.NotNil(t, parsed.rootCAs)
	require.NotEmpty(t, parsed.rootCAs.Subjects())

	privateKey := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: []byte("not-a-key")})
	for name, raw := range map[string][]byte{
		"empty":            nil,
		"carriage return":  []byte("-----BEGIN CERTIFICATE-----\r\n"),
		"trailing content": append(append([]byte(nil), valid...), '\n'),
		"private key":      privateKey,
		"invalid DER":      pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: []byte("not-a-certificate")}),
	} {
		t.Run(name, func(t *testing.T) {
			_, parseErr := ParseProtectedRelayRedisTLSCA(raw)
			require.EqualError(t, parseErr, "protected Relay Redis TLS CA is invalid")
			if len(raw) > 0 {
				require.NotContains(t, parseErr.Error(), string(raw))
			}
		})
	}
}

func TestProtectedRelayRedisTLSCAConfiguresOnlyTheRedisClient(t *testing.T) {
	raw := protectedRelayRedisTLSCATestPEM(t, 2)
	defer clear(raw)
	trust, err := ParseProtectedRelayRedisTLSCA(raw)
	require.NoError(t, err)
	options, err := redis.ParseURL("rediss://:password@redis.example.test:6380/0")
	require.NoError(t, err)
	require.NoError(t, configureProtectedRelayRedisTLS(options, trust.rootCAs))
	require.NotNil(t, options.TLSConfig)
	require.Equal(t, "redis.example.test", options.TLSConfig.ServerName)
	require.Equal(t, uint16(tls.VersionTLS12), options.TLSConfig.MinVersion)
	require.NotEmpty(t, options.TLSConfig.RootCAs.Subjects())
}

func TestConfigureProtectedRelayRuntimeSecretsBindsRedisTrustSnapshot(t *testing.T) {
	if os.Getenv("RELAY_REDIS_TLS_CA_STATE_HELPER") == "1" {
		firstRaw := protectedRelayRedisTLSCATestPEM(t, 3)
		secondRaw := protectedRelayRedisTLSCATestPEM(t, 4)
		defer clear(firstRaw)
		defer clear(secondRaw)
		first, err := ParseProtectedRelayRedisTLSCA(firstRaw)
		require.NoError(t, err)
		second, err := ParseProtectedRelayRedisTLSCA(secondRaw)
		require.NoError(t, err)
		require.NoError(t, ConfigureProtectedRelayRuntimeSecrets(
			"rediss://:password@redis.example.test:6380/0", "session", "crypto", first,
		))
		require.NoError(t, ConfigureProtectedRelayRuntimeSecrets(
			"rediss://:password@redis.example.test:6380/0", "session", "crypto", first,
		))
		require.EqualError(t, ConfigureProtectedRelayRuntimeSecrets(
			"rediss://:password@redis.example.test:6380/0", "session", "crypto", second,
		), "protected Relay runtime secrets are immutable")
		_, roots, configured := protectedRelayRedisConfiguration()
		require.True(t, configured)
		require.NotNil(t, roots)
		require.NotEmpty(t, roots.Subjects())
		return
	}
	command := exec.Command(os.Args[0], "-test.run=^TestConfigureProtectedRelayRuntimeSecretsBindsRedisTrustSnapshot$")
	command.Env = append(os.Environ(), "RELAY_REDIS_TLS_CA_STATE_HELPER=1")
	output, err := command.CombinedOutput()
	require.NoError(t, err, string(output))
}
