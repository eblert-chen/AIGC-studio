//go:build relay_local_video_lab

package main

import (
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"math/big"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/localvideoconfig"
)

const labObjectStoreHost = "lab-objects.local.test"

// configureLabArtifactStorage configures the existing OBS SDK/store against a
// real, persistent, local OBS-v2 protocol substitute. The protocol binding is
// produced by that existing store after actual Put/HEAD verification; it is not
// attached to a filesystem URL or asserted as production Huawei acceptance.
// The separately tagged entry calls this after preparing mock provider TLS.
func configureLabArtifactStorage(options labOptions, state *labState, config localvideoconfig.Config, environment map[string]string) error {
	if state == nil || len(state.Seed) < 32 || state.Manifest.StateID != config.StateID || options.Mode != config.Mode {
		return errors.New("local object storage requires the exact prepared lab state")
	}
	if err := localvideoconfig.Validate(config); err != nil {
		return err
	}
	directory := filepath.Join(state.Directory, "object-store", "certs")
	if err := os.MkdirAll(directory, 0700); err != nil {
		return errors.New("could not create local object storage certificate directory")
	}
	resolved, err := filepath.EvalSymlinks(directory)
	if err != nil || filepath.Clean(resolved) != filepath.Clean(directory) {
		return errors.New("local object storage certificate path must not traverse symlinks")
	}
	digest := sha256.Sum256([]byte(config.StateID))
	bucket := "video-" + config.Mode + "-" + hex.EncodeToString(digest[:10])
	derive := func(label string) string {
		mac := hmac.New(sha256.New, state.Seed)
		_, _ = mac.Write([]byte("local-video-object-store:v1:" + config.StateID + ":" + label))
		return hex.EncodeToString(mac.Sum(nil))
	}
	accessKey := "LAB" + strings.ToUpper(derive("access-key")[:20])
	secretKey := derive("secret-key")
	if err := prepareLabObjectStoreTLS(directory, bucket, config.StateID); err != nil {
		return err
	}
	ca, err := readLabPrivateFile(filepath.Join(directory, "ca.pem"), 64*1024)
	if err != nil {
		return err
	}
	combined := append([]byte(nil), ca...)
	// Preserve normal public TLS roots for live providers. The additional local
	// CA is DNS-name-constrained and cannot authenticate official provider hosts.
	for _, systemBundle := range []string{"/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"} {
		if bundle, err := os.ReadFile(systemBundle); err == nil && len(bundle) > 0 {
			combined = append(combined, bundle...)
			break
		}
	}
	if config.Mode == localvideoconfig.ModeMock {
		mockCA, err := readLabPrivateFile(filepath.Join(state.Directory, "mock-ca.pem"), 64*1024)
		if err != nil {
			return err
		}
		combined = append(combined, mockCA...)
	}
	bundlePath := filepath.Join(directory, "combined-roots.pem")
	if err := writeLabPrivateFile(bundlePath, combined, false); err != nil {
		return err
	}
	environment["RELAY_ARTIFACT_STORE"] = "huawei_obs"
	environment["HUAWEI_OBS_ENDPOINT"] = "https://" + labObjectStoreHost
	environment["HUAWEI_OBS_BUCKET"] = bucket
	environment["HUAWEI_OBS_ACCESS_KEY_ID"] = accessKey
	environment["HUAWEI_OBS_SECRET_ACCESS_KEY"] = secretKey
	environment["HUAWEI_OBS_SECURITY_TOKEN"] = ""
	environment["SSL_CERT_FILE"] = bundlePath
	for key, value := range map[string]string{
		"KIND":     "local-persistent-object-store-not-production-obs",
		"PROTOCOL": "obs-v2", "ENDPOINT_HOST": labObjectStoreHost, "BUCKET": bucket,
		"ACCESS_KEY_ID": accessKey, "SECRET_ACCESS_KEY": secretKey, "STATE_ID": config.StateID, "MODE": config.Mode,
		"CA_FILE": filepath.Join(directory, "ca.pem"), "TLS_CERT_FILE": filepath.Join(directory, "server.pem"),
		"TLS_KEY_FILE": filepath.Join(directory, "server-key.pem"),
	} {
		environment["PLATFORM_LAB_OBJECT_STORE_"+key] = value
	}
	return nil
}

func prepareLabObjectStoreTLS(directory, bucket, stateID string) error {
	paths := []string{filepath.Join(directory, "ca.pem"), filepath.Join(directory, "server.pem"), filepath.Join(directory, "server-key.pem")}
	existing := 0
	for _, path := range paths {
		if _, err := os.Lstat(path); err == nil {
			existing++
		} else if !os.IsNotExist(err) {
			return errors.New("local object TLS identity cannot be inspected")
		}
	}
	if existing != 0 {
		if existing != len(paths) {
			return errors.New("incomplete local object TLS identity; use a fresh lab state")
		}
		return validateLabObjectStoreTLS(directory, bucket, stateID)
	}
	publicCA, privateCA, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return errors.New("could not create local object CA")
	}
	publicServer, privateServer, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return errors.New("could not create local object TLS key")
	}
	serialLimit := new(big.Int).Lsh(big.NewInt(1), 128)
	serial, err := rand.Int(rand.Reader, serialLimit)
	if err != nil {
		return err
	}
	serverSerial, err := rand.Int(rand.Reader, serialLimit)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	ca := &x509.Certificate{
		SerialNumber: serial, Subject: pkix.Name{CommonName: "Local objects " + stateID},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(30 * 24 * time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
		PermittedDNSDomainsCritical: true, PermittedDNSDomains: []string{labObjectStoreHost},
	}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, publicCA, privateCA)
	if err != nil {
		return errors.New("could not create constrained local object CA")
	}
	server := &x509.Certificate{
		SerialNumber: serverSerial, Subject: pkix.Name{CommonName: "Local object storage - not production OBS"},
		NotBefore: ca.NotBefore, NotAfter: ca.NotAfter,
		DNSNames: []string{labObjectStoreHost, bucket + "." + labObjectStoreHost},
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
	}
	serverDER, err := x509.CreateCertificate(rand.Reader, server, ca, publicServer, privateCA)
	if err != nil {
		return errors.New("could not create local object TLS certificate")
	}
	privateDER, err := x509.MarshalPKCS8PrivateKey(privateServer)
	if err != nil {
		return errors.New("could not encode local object TLS key")
	}
	for path, content := range map[string][]byte{
		paths[0]: pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}),
		paths[1]: pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: serverDER}),
		paths[2]: pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: privateDER}),
	} {
		if err := writeLabPrivateFile(path, content, true); err != nil {
			return err
		}
	}
	// The CA private key is never persisted: a live artifact endpoint cannot
	// mint another certificate, even within this explicitly constrained domain.
	return validateLabObjectStoreTLS(directory, bucket, stateID)
}

func validateLabObjectStoreTLS(directory, bucket, stateID string) error {
	caPEM, err := readLabPrivateFile(filepath.Join(directory, "ca.pem"), 64*1024)
	if err != nil {
		return err
	}
	block, remainder := pem.Decode(caPEM)
	if block == nil || block.Type != "CERTIFICATE" || strings.TrimSpace(string(remainder)) != "" {
		return errors.New("local object CA is malformed")
	}
	ca, err := x509.ParseCertificate(block.Bytes)
	if err != nil || !ca.IsCA || !ca.PermittedDNSDomainsCritical ||
		!reflect.DeepEqual(ca.PermittedDNSDomains, []string{labObjectStoreHost}) || ca.Subject.CommonName != "Local objects "+stateID {
		return errors.New("local object CA is not constrained to the exact lab state")
	}
	certPEM, err := readLabPrivateFile(filepath.Join(directory, "server.pem"), 64*1024)
	if err != nil {
		return err
	}
	keyPEM, err := readLabPrivateFile(filepath.Join(directory, "server-key.pem"), 64*1024)
	if err != nil {
		return err
	}
	pair, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil || len(pair.Certificate) != 1 {
		return errors.New("local object TLS key does not match its certificate")
	}
	certificate, err := x509.ParseCertificate(pair.Certificate[0])
	if err != nil || !reflect.DeepEqual(certificate.DNSNames, []string{labObjectStoreHost, bucket + "." + labObjectStoreHost}) || len(certificate.IPAddresses) != 0 {
		return errors.New("local object TLS certificate has an unexpected host")
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	for _, host := range certificate.DNSNames {
		if _, err := certificate.Verify(x509.VerifyOptions{Roots: roots, DNSName: host}); err != nil {
			return errors.New("local object TLS identity is expired or invalid; use a fresh lab state")
		}
	}
	return nil
}
