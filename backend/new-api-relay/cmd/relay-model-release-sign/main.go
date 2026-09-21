// relay-model-release-sign is an offline release-authority tool. It is not
// built or copied by the production Relay Dockerfile.
package main

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
)

const maximumModelReleaseBytes int64 = 4 << 20

type signerOptions struct {
	releaseFile    string
	privateKeyFile string
	keyID          string
	signedAt       string
	notAfter       string
}

var (
	openModelReleaseSignerFile      = os.Open
	afterModelReleaseSignerFileRead = func(string) {}
)

func main() {
	options := signerOptions{}
	flag.StringVar(&options.releaseFile, "release", "", "absolute path to the reviewed unsigned model-release JSON")
	flag.StringVar(&options.privateKeyFile, "private-key-file", "", "absolute path to a regular, non-symlink base64 Ed25519 private-key file")
	flag.StringVar(&options.keyID, "key-id", "", "public verification key id embedded in the attestation")
	flag.StringVar(&options.signedAt, "signed-at", "", "canonical UTC RFC3339 signing time")
	flag.StringVar(&options.notAfter, "not-after", "", "canonical UTC RFC3339 expiry time (maximum 365 days)")
	flag.Parse()
	if flag.NArg() != 0 {
		fatal(errors.New("positional arguments are not accepted"))
	}
	output, err := signModelRelease(options)
	if err != nil {
		fatal(err)
	}
	if _, err := os.Stdout.Write(append(output, '\n')); err != nil {
		fatal(fmt.Errorf("write signed model release: %w", err))
	}
}

func fatal(err error) {
	// Errors intentionally contain no private key bytes or release document.
	fmt.Fprintln(os.Stderr, "model release signing failed:", err)
	os.Exit(1)
}

func signModelRelease(options signerOptions) ([]byte, error) {
	releaseBytes, err := readModelReleaseSignerFile(options.releaseFile, maximumModelReleaseBytes, false)
	if err != nil {
		return nil, fmt.Errorf("read reviewed model release: %w", err)
	}
	defer clear(releaseBytes)
	release, err := generationrelease.DecodeStrict(releaseBytes)
	if err != nil {
		return nil, errors.New("reviewed model release is invalid")
	}
	if release.Attestation != nil {
		return nil, errors.New("reviewed model release must be unsigned")
	}
	profile, ok := generationprofile.Get(release.AdapterProfileID)
	if !ok {
		return nil, errors.New("reviewed model release references an unavailable adapter profile")
	}
	if err := release.Validate(profile); err != nil {
		return nil, errors.New("reviewed model release does not satisfy its adapter profile")
	}

	signedAt, err := parseModelReleaseSignerUTC("signed-at", options.signedAt)
	if err != nil {
		return nil, err
	}
	notAfter, err := parseModelReleaseSignerUTC("not-after", options.notAfter)
	if err != nil {
		return nil, err
	}
	createdAt, _ := time.Parse(time.RFC3339, release.Audit.CreatedAt)
	if signedAt.Before(createdAt) {
		return nil, errors.New("signed-at must not precede the reviewed release audit")
	}
	if !notAfter.After(signedAt) || notAfter.Sub(signedAt) > generationrelease.MaximumAttestationLifetime {
		return nil, errors.New("model release attestation window is invalid")
	}

	privateKey, err := loadModelReleaseSignerPrivateKey(options.privateKeyFile)
	if err != nil {
		return nil, err
	}
	defer clear(privateKey)
	attestation := generationrelease.Attestation{
		Algorithm: generationrelease.Algorithm,
		KeyID:     options.keyID,
		SignedAt:  signedAt.Format(time.RFC3339),
		NotAfter:  notAfter.Format(time.RFC3339),
	}
	payload, err := release.SigningPayload(attestation)
	if err != nil {
		return nil, errors.New("canonicalize reviewed model release")
	}
	defer clear(payload)
	attestation.Signature = base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, payload))
	release.Attestation = &attestation
	if err := release.Validate(profile); err != nil {
		return nil, errors.New("signed model release is invalid")
	}
	publicKey := privateKey.Public().(ed25519.PublicKey)
	if err := release.VerifyAttestation(map[string]ed25519.PublicKey{attestation.KeyID: publicKey}, signedAt); err != nil {
		return nil, errors.New("signed model release failed self-verification")
	}
	output, err := json.MarshalIndent(release, "", "  ")
	if err != nil {
		return nil, errors.New("encode signed model release")
	}
	return output, nil
}

func parseModelReleaseSignerUTC(name string, value string) (time.Time, error) {
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil || parsed.Location() != time.UTC || parsed.Format(time.RFC3339) != value {
		return time.Time{}, fmt.Errorf("%s must be canonical UTC RFC3339", name)
	}
	return parsed, nil
}

func readModelReleaseSignerFile(path string, maximumBytes int64, ownerOnly bool) ([]byte, error) {
	if path == "" || !filepath.IsAbs(path) {
		return nil, errors.New("path must be absolute")
	}
	pathInfo, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if pathInfo.Mode()&os.ModeSymlink != 0 || !pathInfo.Mode().IsRegular() {
		return nil, errors.New("path must identify a regular non-symlink file")
	}
	if pathInfo.Size() < 1 || pathInfo.Size() > maximumBytes {
		return nil, errors.New("file size is outside the permitted range")
	}
	file, err := openModelReleaseSignerFile(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	openedBefore, err := file.Stat()
	if err != nil {
		return nil, err
	}
	if !openedBefore.Mode().IsRegular() || !os.SameFile(pathInfo, openedBefore) {
		return nil, errors.New("file identity changed while opening")
	}
	if ownerOnly && runtime.GOOS != "windows" && openedBefore.Mode().Perm()&0o077 != 0 {
		return nil, errors.New("private-key file must not be group- or world-readable")
	}
	contents, err := io.ReadAll(io.LimitReader(file, maximumBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(contents)) > maximumBytes {
		clear(contents)
		return nil, errors.New("file exceeds the permitted size")
	}
	afterModelReleaseSignerFileRead(path)
	openedAfter, err := file.Stat()
	if err != nil {
		clear(contents)
		return nil, err
	}
	pathAfter, err := os.Lstat(path)
	if err != nil || !openedAfter.Mode().IsRegular() || !pathAfter.Mode().IsRegular() || pathAfter.Mode()&os.ModeSymlink != 0 ||
		!os.SameFile(openedBefore, openedAfter) || !os.SameFile(openedAfter, pathAfter) ||
		openedBefore.Size() != openedAfter.Size() || !openedBefore.ModTime().Equal(openedAfter.ModTime()) ||
		int64(len(contents)) != openedAfter.Size() {
		clear(contents)
		return nil, errors.New("file identity or contents changed while reading")
	}
	if ownerOnly && runtime.GOOS != "windows" &&
		(openedAfter.Mode().Perm()&0o077 != 0 || pathAfter.Mode().Perm()&0o077 != 0) {
		clear(contents)
		return nil, errors.New("private-key file must not be group- or world-readable")
	}
	return contents, nil
}

func loadModelReleaseSignerPrivateKey(path string) (ed25519.PrivateKey, error) {
	encoded, err := readModelReleaseSignerFile(path, 1024, true)
	if err != nil {
		return nil, fmt.Errorf("read Ed25519 private-key file: %w", err)
	}
	defer clear(encoded)
	value := bytes.TrimSpace(encoded)
	decodedBuffer := make([]byte, base64.StdEncoding.DecodedLen(len(value)))
	decodedLength, err := base64.StdEncoding.Decode(decodedBuffer, value)
	decoded := decodedBuffer[:decodedLength]
	canonical := make([]byte, base64.StdEncoding.EncodedLen(len(decoded)))
	base64.StdEncoding.Encode(canonical, decoded)
	if err != nil || !bytes.Equal(canonical, value) {
		clear(canonical)
		clear(decodedBuffer)
		return nil, errors.New("Ed25519 private-key file must contain canonical base64")
	}
	clear(canonical)
	defer clear(decodedBuffer)
	switch len(decoded) {
	case ed25519.SeedSize:
		return ed25519.NewKeyFromSeed(decoded), nil
	case ed25519.PrivateKeySize:
		privateKey := ed25519.PrivateKey(append([]byte(nil), decoded...))
		derived := ed25519.NewKeyFromSeed(privateKey.Seed())
		defer clear(derived)
		if !privateKey.Equal(derived) {
			clear(privateKey)
			return nil, errors.New("Ed25519 private-key bytes are internally inconsistent")
		}
		return privateKey, nil
	default:
		return nil, errors.New("Ed25519 private-key file must contain a 32-byte seed or 64-byte private key")
	}
}
