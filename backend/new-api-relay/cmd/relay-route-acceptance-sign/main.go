// relay-route-acceptance-sign is an offline release-authority tool. It is not
// built or copied by the production Relay Dockerfile.
package main

import (
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
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/service"
	"github.com/google/uuid"
)

type signerOptions struct {
	routesFile           string
	privateKeyFile       string
	keyID                string
	releaseID            string
	environment          string
	sourceRevision       string
	sourceSnapshotSHA256 string
	imageDigest          string
	notBefore            string
	notAfter             string
}

func main() {
	options := signerOptions{}
	flag.StringVar(&options.routesFile, "routes", "", "absolute path to the reviewed route JSON")
	flag.StringVar(&options.privateKeyFile, "private-key-file", "", "absolute path to a regular, non-symlink base64 Ed25519 private-key file")
	flag.StringVar(&options.keyID, "key-id", "", "public verification key id embedded in the manifest")
	flag.StringVar(&options.releaseID, "release-id", "", "stable UUID namespace for this acceptance release")
	flag.StringVar(&options.environment, "environment", "", "exact target environment: staging or production")
	flag.StringVar(&options.sourceRevision, "source-revision", "", "committed extension source revision")
	flag.StringVar(&options.sourceSnapshotSHA256, "source-snapshot-sha256", "", "frozen source snapshot digest")
	flag.StringVar(&options.imageDigest, "image-digest", "", "final deployed image manifest digest")
	flag.StringVar(&options.notBefore, "not-before", "", "canonical UTC RFC3339 activation time")
	flag.StringVar(&options.notAfter, "not-after", "", "canonical UTC RFC3339 expiry time (maximum 90 days)")
	flag.Parse()
	if flag.NArg() != 0 {
		fatal(errors.New("positional arguments are not accepted"))
	}
	output, err := signRoutes(options)
	if err != nil {
		fatal(err)
	}
	if _, err := os.Stdout.Write(append(output, '\n')); err != nil {
		fatal(fmt.Errorf("write signed route evidence: %w", err))
	}
}

func fatal(err error) {
	// Errors intentionally contain no private key bytes or route JSON.
	fmt.Fprintln(os.Stderr, "route acceptance signing failed:", err)
	os.Exit(1)
}

func signRoutes(options signerOptions) ([]byte, error) {
	routeBytes, err := readAbsoluteRegularFile(options.routesFile, 16<<20)
	if err != nil {
		return nil, fmt.Errorf("read reviewed routes: %w", err)
	}
	routes := make(map[string][]service.PlatformRelayRouteDeclaration)
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(string(routeBytes)), &routes); err != nil {
		return nil, fmt.Errorf("decode reviewed routes: %w", err)
	}
	if len(routes) == 0 {
		return nil, errors.New("reviewed routes must not be empty")
	}
	privateKey, err := loadEd25519PrivateKey(options.privateKeyFile)
	if err != nil {
		return nil, err
	}
	defer clear(privateKey)
	releaseID, err := uuid.Parse(options.releaseID)
	if err != nil {
		return nil, errors.New("release-id must be a UUID")
	}
	notBefore, err := parseCanonicalUTC("not-before", options.notBefore)
	if err != nil {
		return nil, err
	}
	notAfter, err := parseCanonicalUTC("not-after", options.notAfter)
	if err != nil {
		return nil, err
	}
	provenance := service.PlatformRelayBuildProvenance{
		SourceGitRevision:    strings.ToLower(strings.TrimSpace(options.sourceRevision)),
		SourceSnapshotSHA256: strings.ToLower(strings.TrimSpace(options.sourceSnapshotSHA256)),
		ImageDigest:          strings.ToLower(strings.TrimSpace(options.imageDigest)),
	}
	for modelID, declarations := range routes {
		if len(declarations) == 0 {
			return nil, fmt.Errorf("model %q has no route declarations", modelID)
		}
		for index := range declarations {
			declaration := &declarations[index]
			acceptanceID := uuid.NewSHA1(releaseID, []byte(modelID+"\x00"+declaration.RouteID)).String()
			manifest, err := service.BuildPlatformRouteAcceptanceManifest(
				modelID,
				declaration,
				acceptanceID,
				options.keyID,
				strings.ToLower(strings.TrimSpace(options.environment)),
				provenance,
				notBefore,
				notAfter,
			)
			if err != nil {
				return nil, fmt.Errorf("build acceptance for model %q route %q: %w", modelID, declaration.RouteID, err)
			}
			payload, err := service.PlatformRouteAcceptanceSignaturePayload(manifest)
			if err != nil {
				return nil, fmt.Errorf("canonicalize acceptance for model %q route %q: %w", modelID, declaration.RouteID, err)
			}
			declaration.Acceptance = &service.PlatformRouteAcceptanceEvidence{
				Manifest:  manifest,
				Signature: base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, payload)),
			}
		}
		routes[modelID] = declarations
	}
	output, err := json.MarshalIndent(routes, "", "  ")
	if err != nil {
		return nil, fmt.Errorf("encode signed route evidence: %w", err)
	}
	return output, nil
}

func parseCanonicalUTC(name string, value string) (time.Time, error) {
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil || parsed.Location() != time.UTC || parsed.Format(time.RFC3339) != value {
		return time.Time{}, fmt.Errorf("%s must be canonical UTC RFC3339", name)
	}
	return parsed, nil
}

func readAbsoluteRegularFile(path string, maximumBytes int64) ([]byte, error) {
	return readAbsoluteRegularFileWithPolicy(path, maximumBytes, false)
}

func readAbsoluteRegularFileWithPolicy(path string, maximumBytes int64, ownerOnly bool) ([]byte, error) {
	if path == "" || !filepath.IsAbs(path) {
		return nil, errors.New("path must be absolute")
	}
	info, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
		return nil, errors.New("path must identify a regular non-symlink file")
	}
	if info.Size() < 1 || info.Size() > maximumBytes {
		return nil, errors.New("file size is outside the permitted range")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	openedInfo, err := file.Stat()
	if err != nil {
		return nil, err
	}
	if !openedInfo.Mode().IsRegular() || !os.SameFile(info, openedInfo) {
		return nil, errors.New("file identity changed while opening")
	}
	if ownerOnly && runtime.GOOS != "windows" && openedInfo.Mode().Perm()&0o077 != 0 {
		return nil, errors.New("private-key file must not be group- or world-readable")
	}
	contents, err := io.ReadAll(io.LimitReader(file, maximumBytes+1))
	if err != nil {
		return nil, err
	}
	if int64(len(contents)) > maximumBytes {
		return nil, errors.New("file exceeds the permitted size")
	}
	return contents, nil
}

func loadEd25519PrivateKey(path string) (ed25519.PrivateKey, error) {
	encoded, err := readAbsoluteRegularFileWithPolicy(path, 1024, true)
	if err != nil {
		return nil, fmt.Errorf("read Ed25519 private-key file: %w", err)
	}
	value := strings.TrimSpace(string(encoded))
	decoded, err := base64.StdEncoding.DecodeString(value)
	if err != nil || base64.StdEncoding.EncodeToString(decoded) != value {
		return nil, errors.New("Ed25519 private-key file must contain canonical base64")
	}
	defer clear(decoded)
	switch len(decoded) {
	case ed25519.SeedSize:
		return ed25519.NewKeyFromSeed(decoded), nil
	case ed25519.PrivateKeySize:
		privateKey := ed25519.PrivateKey(append([]byte(nil), decoded...))
		derived := ed25519.NewKeyFromSeed(privateKey.Seed())
		if !privateKey.Equal(derived) {
			return nil, errors.New("Ed25519 private-key bytes are internally inconsistent")
		}
		return privateKey, nil
	default:
		return nil, errors.New("Ed25519 private-key file must contain a 32-byte seed or 64-byte private key")
	}
}
