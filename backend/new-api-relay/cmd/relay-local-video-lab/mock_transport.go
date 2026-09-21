//go:build relay_local_video_lab

package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"math/big"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/service"
)

func prepareLabMockTLS(directory string, models []labMockModel) error {
	certificatePath := filepath.Join(directory, "mock-certificate.pem")
	keyPath := filepath.Join(directory, "mock-private-key.pem")
	caPath := filepath.Join(directory, "mock-ca.pem")
	if labFixtureFileExists(certificatePath) && labFixtureFileExists(keyPath) && labFixtureFileExists(caPath) {
		_, err := tls.LoadX509KeyPair(certificatePath, keyPath)
		return err
	}
	for _, path := range []string{certificatePath, keyPath, caPath} {
		if _, err := os.Lstat(path); err == nil {
			return errors.New("incomplete mock TLS identity; use a new state directory")
		}
	}
	publicCA, privateCA, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return errors.New("could not create mock CA key")
	}
	publicServer, privateServer, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return errors.New("could not create mock server key")
	}
	serialLimit := new(big.Int).Lsh(big.NewInt(1), 128)
	caSerial, err := rand.Int(rand.Reader, serialLimit)
	if err != nil {
		return err
	}
	serverSerial, err := rand.Int(rand.Reader, serialLimit)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	ca := &x509.Certificate{
		SerialNumber: caSerial, Subject: pkix.Name{CommonName: "Isolated local video lab CA - never production"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(30 * 24 * time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
	}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, publicCA, privateCA)
	if err != nil {
		return errors.New("could not create mock CA certificate")
	}
	hosts := map[string]bool{labArtifactHost: true}
	for _, model := range models {
		hosts[model.Host] = true
	}
	names := make([]string, 0, len(hosts))
	for host := range hosts {
		names = append(names, host)
	}
	sort.Strings(names)
	certificate := &x509.Certificate{
		SerialNumber: serverSerial, Subject: pkix.Name{CommonName: "Explicit mock video provider"}, DNSNames: names,
		NotBefore: ca.NotBefore, NotAfter: ca.NotAfter, KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, BasicConstraintsValid: true,
	}
	serverDER, err := x509.CreateCertificate(rand.Reader, certificate, ca, publicServer, privateCA)
	if err != nil {
		return errors.New("could not create mock TLS certificate")
	}
	privateDER, err := x509.MarshalPKCS8PrivateKey(privateServer)
	if err != nil {
		return errors.New("could not encode mock TLS key")
	}
	for path, data := range map[string][]byte{
		caPath:          pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}),
		certificatePath: pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: serverDER}),
		keyPath:         pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateDER}),
	} {
		if err := writeLabPrivateFile(path, data, true); err != nil {
			return err
		}
	}
	return nil
}

// This transport exists only in the separately tagged lab binary. The adapter
// still constructs the exact official URL, Host, Bearer header and JSON. Only
// its dial destination changes; TLS verifies the original hostname against the
// isolated test CA. The artifact downloader deliberately does NOT use this:
// Docker DNS maps its public-looking address and it enforces the real guard.
type labMockTransport struct {
	providerHosts map[string]bool
	provider      *http.Transport
	internal      *http.Transport
	internalURL   string
}

func newLabMockTransport(models []labMockModel, caPEM []byte, tlsAddress string, internalURL string) (*labMockTransport, error) {
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("mock CA bundle is invalid")
	}
	hosts := make(map[string]bool, len(models))
	for _, model := range models {
		hosts[model.Host] = true
	}
	dialer := &net.Dialer{Timeout: 5 * time.Second}
	transport := &http.Transport{
		Proxy: nil, TLSClientConfig: &tls.Config{RootCAs: roots, MinVersion: tls.VersionTLS12},
		ForceAttemptHTTP2: false, DisableKeepAlives: true,
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(address)
			if err != nil || port != "443" || !hosts[host] {
				return nil, errors.New("mock provider dial target is not an exact official endpoint")
			}
			return dialer.DialContext(ctx, network, tlsAddress)
		},
	}
	return &labMockTransport{
		providerHosts: hosts, provider: transport, internalURL: internalURL,
		internal: &http.Transport{Proxy: nil, DialContext: dialer.DialContext, DisableKeepAlives: true},
	}, nil
}

func (transport *labMockTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	if request.URL.User != nil || request.URL.Fragment != "" {
		return nil, errors.New("unsafe URL in mock lab transport")
	}
	if request.URL.Scheme == "https" && transport.providerHosts[request.URL.Hostname()] && (request.URL.Port() == "" || request.URL.Port() == "443") {
		if !labProviderPath(request) {
			return nil, errors.New("provider operation is outside the local video lab")
		}
		return transport.provider.RoundTrip(request)
	}
	if request.URL.Scheme+"://"+request.URL.Host == transport.internalURL && request.URL.Path == "/internal/platform-generations/native-submit" {
		return transport.internal.RoundTrip(request)
	}
	return nil, errors.New("mock lab transport blocks every unrecognized outbound target")
}

func labProviderPath(request *http.Request) bool {
	if request.URL.RawQuery != "" {
		return false
	}
	switch request.Method {
	case http.MethodPost:
		return request.URL.Path == "/v2/video_generation" || request.URL.Path == "/api/v3/contents/generations/tasks"
	case http.MethodGet:
		return strings.HasPrefix(request.URL.Path, "/v2/query/video_generation/") || strings.HasPrefix(request.URL.Path, "/api/v3/contents/generations/tasks/")
	default:
		return false
	}
}

func installLabMockTransport(models []labMockModel, directory, internalURL string) error {
	caPEM, err := readLabPrivateFile(filepath.Join(directory, "mock-ca.pem"), 64*1024)
	if err != nil {
		return err
	}
	transport, err := newLabMockTransport(models, caPEM, "127.0.0.1:443", internalURL)
	if err != nil {
		return err
	}
	client, err := service.GetHttpClientWithProxy("")
	if err != nil || client == nil {
		return errors.New("Relay HTTP client is not initialized")
	}
	client.Transport = transport
	client.Timeout = 30 * time.Second
	return nil
}
