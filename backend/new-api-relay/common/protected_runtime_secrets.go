package common

import (
	"bytes"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"sort"
	"sync"
)

const ProtectedRelayRedisTLSCAMaximumBytes int64 = 256 * 1024

// ProtectedRelayRedisTLSCA is an opaque, parsed Redis trust snapshot. Callers
// can only construct it from one strict canonical PEM input, so runtime Redis
// clients never need an ambient process-wide certificate override.
type ProtectedRelayRedisTLSCA struct {
	rootCAs     *x509.CertPool
	fingerprint [sha256.Size]byte
	valid       bool
}

func ParseProtectedRelayRedisTLSCA(raw []byte) (ProtectedRelayRedisTLSCA, error) {
	invalid := func() (ProtectedRelayRedisTLSCA, error) {
		return ProtectedRelayRedisTLSCA{}, errors.New("protected Relay Redis TLS CA is invalid")
	}
	if len(raw) == 0 || int64(len(raw)) > ProtectedRelayRedisTLSCAMaximumBytes ||
		bytes.Contains(raw, []byte("\r")) || raw[len(raw)-1] != '\n' {
		return invalid()
	}
	remaining := raw
	canonical := make([]byte, 0, len(raw))
	defer clear(canonical)
	rootCAs := x509.NewCertPool()
	count := 0
	for len(remaining) > 0 {
		if !bytes.HasPrefix(remaining, []byte("-----BEGIN CERTIFICATE-----\n")) {
			return invalid()
		}
		block, rest := pem.Decode(remaining)
		if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 {
			return invalid()
		}
		certificate, err := x509.ParseCertificate(block.Bytes)
		if err != nil {
			return invalid()
		}
		rootCAs.AddCert(certificate)
		canonical = append(canonical, pem.EncodeToMemory(block)...)
		remaining = rest
		count++
	}
	if count == 0 || !bytes.Equal(canonical, raw) {
		return invalid()
	}
	return ProtectedRelayRedisTLSCA{
		rootCAs: rootCAs, fingerprint: sha256.Sum256(raw), valid: true,
	}, nil
}

func (value ProtectedRelayRedisTLSCA) Fingerprint() [sha256.Size]byte {
	return value.fingerprint
}

type protectedRelayRuntimeSecrets struct {
	redisDSN   string
	redisTLSCA ProtectedRelayRedisTLSCA
	session    string
	crypto     string
	digests    map[[sha256.Size]byte]struct{}
	digestSet  [sha256.Size]byte
	configured bool
}

var protectedRelayRuntimeSecretState struct {
	sync.RWMutex
	value protectedRelayRuntimeSecrets
}

// ConfigureProtectedRelayRuntimeSecrets installs the typed bootstrap snapshot
// before InitEnv, Redis, logging, database access, or outbound HTTP starts.
// Values are never copied back into process environment.
func ConfigureProtectedRelayRuntimeSecrets(
	redisDSN, sessionSecret, cryptoSecret string,
	redisTLSCA ProtectedRelayRedisTLSCA,
	secretDigests ...[sha256.Size]byte,
) error {
	if !redisTLSCA.valid || redisTLSCA.rootCAs == nil || len(redisTLSCA.rootCAs.Subjects()) == 0 {
		return errors.New("protected Relay runtime Redis TLS CA is unavailable")
	}
	digestSet, digestMap := protectedRelaySecretDigestSet(secretDigests)
	protectedRelayRuntimeSecretState.Lock()
	defer protectedRelayRuntimeSecretState.Unlock()
	if protectedRelayRuntimeSecretState.value.configured {
		current := protectedRelayRuntimeSecretState.value
		for _, pair := range [][2]string{{current.redisDSN, redisDSN}, {current.session, sessionSecret}, {current.crypto, cryptoSecret}} {
			left := sha256.Sum256([]byte(pair[0]))
			right := sha256.Sum256([]byte(pair[1]))
			if subtle.ConstantTimeCompare(left[:], right[:]) != 1 {
				return errors.New("protected Relay runtime secrets are immutable")
			}
		}
		if subtle.ConstantTimeCompare(current.digestSet[:], digestSet[:]) != 1 {
			return errors.New("protected Relay runtime secrets are immutable")
		}
		if subtle.ConstantTimeCompare(current.redisTLSCA.fingerprint[:], redisTLSCA.fingerprint[:]) != 1 {
			return errors.New("protected Relay runtime secrets are immutable")
		}
		return nil
	}
	protectedRelayRuntimeSecretState.value = protectedRelayRuntimeSecrets{
		redisDSN: redisDSN,
		redisTLSCA: ProtectedRelayRedisTLSCA{
			rootCAs: redisTLSCA.rootCAs.Clone(), fingerprint: redisTLSCA.fingerprint, valid: true,
		},
		session: sessionSecret, crypto: cryptoSecret,
		digests: digestMap, digestSet: digestSet, configured: true,
	}
	return nil
}

func protectedRelaySecretDigestSet(values [][sha256.Size]byte) ([sha256.Size]byte, map[[sha256.Size]byte]struct{}) {
	unique := make(map[[sha256.Size]byte]struct{}, len(values))
	ordered := make([][sha256.Size]byte, 0, len(values))
	for _, value := range values {
		if _, duplicate := unique[value]; duplicate {
			continue
		}
		unique[value] = struct{}{}
		ordered = append(ordered, value)
	}
	sort.Slice(ordered, func(left, right int) bool {
		return bytes.Compare(ordered[left][:], ordered[right][:]) < 0
	})
	hash := sha256.New()
	for _, value := range ordered {
		_, _ = hash.Write(value[:])
	}
	var fingerprint [sha256.Size]byte
	copy(fingerprint[:], hash.Sum(nil))
	return fingerprint, unique
}

// ProtectedRelaySecretDigestRegistered checks only an in-memory SHA-256 set;
// callers never need to copy API runtime secrets across package boundaries.
func ProtectedRelaySecretDigestRegistered(value []byte) bool {
	digest := sha256.Sum256(value)
	protectedRelayRuntimeSecretState.RLock()
	_, registered := protectedRelayRuntimeSecretState.value.digests[digest]
	protectedRelayRuntimeSecretState.RUnlock()
	return registered
}

func ProtectedRelayRuntimeSecretsConfigured() bool {
	protectedRelayRuntimeSecretState.RLock()
	configured := protectedRelayRuntimeSecretState.value.configured
	protectedRelayRuntimeSecretState.RUnlock()
	return configured
}

func protectedRelayApplicationSecrets() (sessionSecret, cryptoSecret string, configured bool) {
	protectedRelayRuntimeSecretState.RLock()
	value := protectedRelayRuntimeSecretState.value
	protectedRelayRuntimeSecretState.RUnlock()
	return value.session, value.crypto, value.configured
}

func protectedRelayRedisConfiguration() (string, *x509.CertPool, bool) {
	protectedRelayRuntimeSecretState.RLock()
	value := protectedRelayRuntimeSecretState.value
	protectedRelayRuntimeSecretState.RUnlock()
	if !value.configured || value.redisTLSCA.rootCAs == nil {
		return value.redisDSN, nil, false
	}
	return value.redisDSN, value.redisTLSCA.rootCAs.Clone(), true
}
