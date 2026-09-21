// relay-release-pair-publish durably publishes the two public artifacts of a
// signed model/route release. The signed-routes file is the commit marker: a
// model file without the matching routes file is resumable, never publishable.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/generationrelease"
	"github.com/QuantumNous/new-api/service"
)

const releasePairMaximumArtifactBytes = 32 << 20

type publishOptions struct {
	modelSource       string
	routesSource      string
	modelDestination  string
	routesDestination string
}

type publishOperations struct {
	syncFile func(*os.File) error
	link     func(string, string) error
	remove   func(string) error
	syncDir  func(string) error
}

type stagedArtifact struct {
	path     string
	identity os.FileInfo
}

type inspectedDestination struct {
	exists   bool
	matches  bool
	identity os.FileInfo
}

func main() {
	options := publishOptions{}
	flag.StringVar(&options.modelSource, "model-source", "", "absolute staged signed model-release file")
	flag.StringVar(&options.routesSource, "routes-source", "", "absolute staged signed-routes file")
	flag.StringVar(&options.modelDestination, "model-destination", "", "absolute immutable model-release output")
	flag.StringVar(&options.routesDestination, "routes-destination", "", "absolute immutable signed-routes commit marker")
	flag.Parse()
	if flag.NArg() != 0 {
		fatal(errors.New("positional arguments are not accepted"))
	}
	if err := publishReleasePair(options, defaultPublishOperations()); err != nil {
		fatal(err)
	}
}

func fatal(err error) {
	// Errors intentionally contain no signed document contents.
	fmt.Fprintln(os.Stderr, "release pair publication failed:", err)
	os.Exit(1)
}

func defaultPublishOperations() publishOperations {
	return publishOperations{
		syncFile: func(file *os.File) error { return file.Sync() },
		link:     os.Link,
		remove:   os.Remove,
		syncDir: func(directory string) error {
			if runtime.GOOS == "windows" {
				return nil
			}
			handle, err := os.Open(directory)
			if err != nil {
				return err
			}
			defer handle.Close()
			return handle.Sync()
		},
	}
}

func publishReleasePair(options publishOptions, operations publishOperations) error {
	parent, err := validatePublishOptions(options)
	if err != nil {
		return err
	}
	modelBytes, err := readStableRegularFile(options.modelSource)
	if err != nil {
		return fmt.Errorf("read staged model release: %w", err)
	}
	routesBytes, err := readStableRegularFile(options.routesSource)
	if err != nil {
		return fmt.Errorf("read staged routes: %w", err)
	}
	if err := validateReleasePair(modelBytes, routesBytes); err != nil {
		return err
	}

	modelTemporary, err := stageReleaseArtifact(parent, "model", modelBytes, operations)
	if err != nil {
		return err
	}
	defer cleanupStagedArtifact(modelTemporary, operations)()
	routesTemporary, err := stageReleaseArtifact(parent, "routes", routesBytes, operations)
	if err != nil {
		return err
	}
	defer cleanupStagedArtifact(routesTemporary, operations)()

	modelDestination, err := inspectDestination(options.modelDestination, modelBytes)
	if err != nil {
		return err
	}
	routesDestination, err := inspectDestination(options.routesDestination, routesBytes)
	if err != nil {
		return err
	}
	if routesDestination.exists {
		if modelDestination.exists && modelDestination.matches && routesDestination.matches {
			if err := verifyCommittedReleasePair(
				options, modelBytes, modelDestination.identity, routesBytes, routesDestination.identity,
			); err != nil {
				return fmt.Errorf("verify exact committed release pair before directory sync: %w", err)
			}
			if err := operations.syncDir(parent); err != nil {
				return fmt.Errorf("resync exact committed release pair: %w", err)
			}
			if err := verifyCommittedReleasePair(
				options, modelBytes, modelDestination.identity, routesBytes, routesDestination.identity,
			); err != nil {
				return fmt.Errorf("verify exact committed release pair after directory sync: %w", err)
			}
			return nil // exact replay also re-establishes directory durability
		}
		return errors.New("signed-routes commit marker already exists with an incomplete or different release pair")
	}
	if modelDestination.exists && !modelDestination.matches {
		return errors.New("model-release destination already exists with different contents")
	}

	var modelCommittedIdentity os.FileInfo
	modelIdentity := modelDestination.identity
	if !modelDestination.exists {
		modelCommittedIdentity, err = commitStagedArtifact(
			modelTemporary, options.modelDestination, modelBytes, operations,
		)
		if err != nil {
			return fmt.Errorf("commit model-release artifact without replacement: %w", err)
		}
		if err := operations.syncDir(parent); err != nil {
			cleanupCommittedDestination(options.modelDestination, modelCommittedIdentity, operations)
			return fmt.Errorf("sync model-release directory entry: %w", err)
		}
		modelIdentity = modelCommittedIdentity
	}
	if err := verifyDestinationArtifact(options.modelDestination, modelBytes, modelIdentity); err != nil {
		if modelCommittedIdentity != nil {
			cleanupCommittedDestination(options.modelDestination, modelCommittedIdentity, operations)
		}
		return fmt.Errorf("verify model-release destination before signed-routes commit: %w", err)
	}

	// Creating this hard link is the atomic no-replace commit point. A prior
	// model-only artifact with identical bytes is an interrupted transaction
	// and may be resumed; consumers must require this routes commit marker.
	routesCommittedIdentity, err := commitStagedArtifact(
		routesTemporary, options.routesDestination, routesBytes, operations,
	)
	if err != nil {
		if modelCommittedIdentity != nil {
			cleanupCommittedDestination(options.modelDestination, modelCommittedIdentity, operations)
		}
		return fmt.Errorf("commit signed-routes marker without replacement: %w", err)
	}
	if err := verifyCommittedReleasePair(
		options, modelBytes, modelIdentity, routesBytes, routesCommittedIdentity,
	); err != nil {
		cleanupCommittedDestination(options.routesDestination, routesCommittedIdentity, operations)
		if modelCommittedIdentity != nil {
			cleanupCommittedDestination(options.modelDestination, modelCommittedIdentity, operations)
		}
		return fmt.Errorf("verify committed release pair before directory sync: %w", err)
	}
	if err := operations.syncDir(parent); err != nil {
		return fmt.Errorf("sync committed release pair: %w", err)
	}
	if err := verifyCommittedReleasePair(
		options, modelBytes, modelIdentity, routesBytes, routesCommittedIdentity,
	); err != nil {
		cleanupCommittedDestination(options.routesDestination, routesCommittedIdentity, operations)
		if modelCommittedIdentity != nil {
			cleanupCommittedDestination(options.modelDestination, modelCommittedIdentity, operations)
		}
		return fmt.Errorf("verify committed release pair after directory sync: %w", err)
	}
	return nil
}

func validatePublishOptions(options publishOptions) (string, error) {
	paths := []struct {
		value string
		name  string
	}{
		{options.modelSource, "model source"},
		{options.routesSource, "routes source"},
		{options.modelDestination, "model destination"},
		{options.routesDestination, "routes destination"},
	}
	for _, item := range paths {
		if !filepath.IsAbs(item.value) || filepath.Clean(item.value) != item.value {
			return "", fmt.Errorf("%s must be a clean absolute path", item.name)
		}
	}
	if options.modelSource == options.modelDestination || options.routesSource == options.routesDestination ||
		options.modelDestination == options.routesDestination {
		return "", errors.New("release pair paths must be distinct")
	}
	modelParent := filepath.Dir(options.modelDestination)
	routesParent := filepath.Dir(options.routesDestination)
	if modelParent != routesParent {
		return "", errors.New("release pair destinations must share one directory")
	}
	parentInfo, err := os.Lstat(modelParent)
	if err != nil || !parentInfo.IsDir() || parentInfo.Mode()&os.ModeSymlink != 0 {
		return "", errors.New("release pair destination directory is unavailable")
	}
	return modelParent, nil
}

func readStableRegularFile(path string) ([]byte, error) {
	contents, _, err := readStableRegularFileWithIdentity(path)
	return contents, err
}

func readStableRegularFileWithIdentity(path string) ([]byte, os.FileInfo, error) {
	pathBefore, err := os.Lstat(path)
	if err != nil || !pathBefore.Mode().IsRegular() || pathBefore.Mode()&os.ModeSymlink != 0 {
		return nil, nil, errors.New("file must be a regular non-symlink file")
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, nil, errors.New("file could not be opened")
	}
	defer file.Close()
	openedBefore, err := file.Stat()
	if err != nil || !os.SameFile(pathBefore, openedBefore) {
		return nil, nil, errors.New("file identity changed while opening")
	}
	contents, err := io.ReadAll(io.LimitReader(file, releasePairMaximumArtifactBytes+1))
	if err != nil || len(contents) == 0 || len(contents) > releasePairMaximumArtifactBytes {
		return nil, nil, errors.New("file size is invalid")
	}
	openedAfter, openedErr := file.Stat()
	pathAfter, pathErr := os.Lstat(path)
	if openedErr != nil || pathErr != nil || !openedAfter.Mode().IsRegular() ||
		pathAfter.Mode()&os.ModeSymlink != 0 || !os.SameFile(openedBefore, openedAfter) ||
		!os.SameFile(openedAfter, pathAfter) || openedAfter.Size() != int64(len(contents)) ||
		openedAfter.Size() != openedBefore.Size() || !openedAfter.ModTime().Equal(openedBefore.ModTime()) {
		return nil, nil, errors.New("file identity or contents changed while reading")
	}
	return contents, openedAfter, nil
}

func validateReleasePair(modelBytes []byte, routesBytes []byte) error {
	release, err := generationrelease.DecodeStrict(modelBytes)
	if err != nil {
		return errors.New("signed model-release document is invalid")
	}
	profile, ok := generationprofile.Get(release.AdapterProfileID)
	if !ok || release.Validate(profile) != nil || release.Attestation == nil ||
		release.PublicModelID != constant.PlatformGenerationPublicSeedream50Model ||
		len(release.LegacyPublicAliases) != 1 ||
		release.LegacyPublicAliases[0] != constant.PlatformGenerationLegacySeedream50LitePublicAlias {
		return errors.New("signed model release is not the reviewed Seedream binding")
	}
	if err := common.RejectDuplicateJSONKeys(routesBytes); err != nil {
		return errors.New("signed routes document is ambiguous")
	}
	var rawRoutes map[string][]map[string]json.RawMessage
	if err := common.DecodeJsonDisallowUnknownFields(bytes.NewReader(routesBytes), &rawRoutes); err != nil {
		return errors.New("signed routes document is invalid")
	}
	var routes map[string][]service.PlatformRelayRouteDeclaration
	if err := common.DecodeJsonDisallowUnknownFields(bytes.NewReader(routesBytes), &routes); err != nil {
		return errors.New("signed routes document is invalid")
	}
	declarations, ok := routes[constant.PlatformGenerationPublicSeedream50Model]
	if !ok || len(routes) != 1 || len(declarations) == 0 || len(rawRoutes) != 1 ||
		len(rawRoutes[constant.PlatformGenerationPublicSeedream50Model]) != len(declarations) {
		return errors.New("signed routes must contain only the reviewed Seedream model")
	}
	canonicalModel, err := common.Marshal(release)
	if err != nil {
		return errors.New("signed model release could not be normalized")
	}
	for index, declaration := range declarations {
		rawDeclaration := rawRoutes[constant.PlatformGenerationPublicSeedream50Model][index]
		if _, present := rawDeclaration["staging_ready"]; present {
			return errors.New("signed routes contain a legacy readiness boolean")
		}
		if _, present := rawDeclaration["production_ready"]; present {
			return errors.New("signed routes contain a legacy readiness boolean")
		}
		if declaration.ModelRelease == nil || declaration.ModelRelease.Attestation == nil ||
			declaration.Acceptance == nil || declaration.Acceptance.Signature == "" {
			return errors.New("signed route output is missing required release evidence")
		}
		modelStart, _ := time.Parse(time.RFC3339, release.Attestation.SignedAt)
		modelExpiry, _ := time.Parse(time.RFC3339, release.Attestation.NotAfter)
		routeStart, startErr := time.Parse(time.RFC3339, declaration.Acceptance.Manifest.NotBefore)
		routeExpiry, expiryErr := time.Parse(time.RFC3339, declaration.Acceptance.Manifest.NotAfter)
		if startErr != nil || expiryErr != nil ||
			declaration.Acceptance.Manifest.NotBefore != routeStart.UTC().Format(time.RFC3339) ||
			declaration.Acceptance.Manifest.NotAfter != routeExpiry.UTC().Format(time.RFC3339) ||
			modelStart.After(routeStart) || modelExpiry.Before(routeExpiry) {
			return errors.New("model attestation must contain the complete route-acceptance window")
		}
		embeddedModel, err := common.Marshal(*declaration.ModelRelease)
		if err != nil || !bytes.Equal(canonicalModel, embeddedModel) {
			return errors.New("signed routes do not embed the exact signed model release")
		}
	}
	return nil
}

func stageReleaseArtifact(parent string, name string, contents []byte, operations publishOperations) (stagedArtifact, error) {
	file, err := os.CreateTemp(parent, ".relay-release-pair-"+name+"-*")
	if err != nil {
		return stagedArtifact{}, fmt.Errorf("create staged %s artifact: %w", name, err)
	}
	path := file.Name()
	succeeded := false
	defer func() {
		_ = file.Close()
		if !succeeded {
			_ = operations.remove(path)
		}
	}()
	if err := file.Chmod(0o600); err != nil {
		return stagedArtifact{}, fmt.Errorf("restrict staged %s artifact: %w", name, err)
	}
	if _, err := file.Write(contents); err != nil {
		return stagedArtifact{}, fmt.Errorf("write staged %s artifact: %w", name, err)
	}
	if err := operations.syncFile(file); err != nil {
		return stagedArtifact{}, fmt.Errorf("flush staged %s artifact: %w", name, err)
	}
	identity, err := file.Stat()
	if err != nil || !identity.Mode().IsRegular() || identity.Size() != int64(len(contents)) {
		return stagedArtifact{}, fmt.Errorf("capture staged %s artifact identity", name)
	}
	if err := file.Close(); err != nil {
		return stagedArtifact{}, fmt.Errorf("close staged %s artifact: %w", name, err)
	}
	readBack, err := readStableRegularFile(path)
	pathIdentity, pathErr := os.Lstat(path)
	if err != nil || pathErr != nil || !os.SameFile(identity, pathIdentity) || !bytes.Equal(readBack, contents) {
		return stagedArtifact{}, fmt.Errorf("verify staged %s artifact", name)
	}
	succeeded = true
	return stagedArtifact{path: path, identity: identity}, nil
}

func inspectDestination(path string, expected []byte) (inspectedDestination, error) {
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return inspectedDestination{}, nil
	}
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
		return inspectedDestination{}, errors.New("release destination is not a regular file")
	}
	actual, identity, err := readStableRegularFileWithIdentity(path)
	if err != nil {
		return inspectedDestination{}, errors.New("release destination could not be verified")
	}
	return inspectedDestination{
		exists:   true,
		matches:  bytes.Equal(actual, expected),
		identity: identity,
	}, nil
}

func verifyDestinationArtifact(path string, expected []byte, identity os.FileInfo) error {
	if identity == nil {
		return errors.New("release destination identity is unavailable")
	}
	actual, currentIdentity, err := readStableRegularFileWithIdentity(path)
	if err != nil || currentIdentity == nil || !os.SameFile(identity, currentIdentity) || !bytes.Equal(actual, expected) {
		return errors.New("release destination identity or contents changed")
	}
	return nil
}

func verifyCommittedReleasePair(
	options publishOptions,
	modelBytes []byte,
	modelIdentity os.FileInfo,
	routesBytes []byte,
	routesIdentity os.FileInfo,
) error {
	if err := verifyDestinationArtifact(options.modelDestination, modelBytes, modelIdentity); err != nil {
		return fmt.Errorf("model release: %w", err)
	}
	if err := verifyDestinationArtifact(options.routesDestination, routesBytes, routesIdentity); err != nil {
		return fmt.Errorf("signed routes: %w", err)
	}
	return nil
}

func verifyStagedArtifact(staged stagedArtifact, expected []byte) error {
	pathIdentity, err := os.Lstat(staged.path)
	if err != nil || !pathIdentity.Mode().IsRegular() || pathIdentity.Mode()&os.ModeSymlink != 0 ||
		!os.SameFile(staged.identity, pathIdentity) {
		return errors.New("staged artifact identity changed before commit")
	}
	contents, err := readStableRegularFile(staged.path)
	pathIdentity, pathErr := os.Lstat(staged.path)
	if err != nil || pathErr != nil || !os.SameFile(staged.identity, pathIdentity) || !bytes.Equal(contents, expected) {
		return errors.New("staged artifact contents changed before commit")
	}
	return nil
}

func commitStagedArtifact(
	staged stagedArtifact,
	destination string,
	expected []byte,
	operations publishOperations,
) (os.FileInfo, error) {
	if err := verifyStagedArtifact(staged, expected); err != nil {
		return nil, err
	}
	if err := operations.link(staged.path, destination); err != nil {
		return nil, err
	}
	committedIdentity, err := os.Lstat(destination)
	if err != nil {
		return nil, errors.New("committed artifact identity is unavailable")
	}
	contents, readErr := readStableRegularFile(destination)
	stagedIdentity, stagedErr := os.Lstat(staged.path)
	if readErr != nil || stagedErr != nil || !os.SameFile(committedIdentity, staged.identity) ||
		!os.SameFile(stagedIdentity, staged.identity) || !bytes.Equal(contents, expected) {
		cleanupCommittedDestination(destination, committedIdentity, operations)
		return nil, errors.New("committed artifact does not match the verified staged inode")
	}
	return committedIdentity, nil
}

func cleanupCommittedDestination(destination string, identity os.FileInfo, operations publishOperations) {
	current, err := os.Lstat(destination)
	if err == nil && identity != nil && os.SameFile(current, identity) {
		_ = operations.remove(destination)
	}
}

func cleanupStagedArtifact(staged stagedArtifact, operations publishOperations) func() {
	return func() {
		current, err := os.Lstat(staged.path)
		if err == nil && staged.identity != nil && os.SameFile(current, staged.identity) {
			_ = operations.remove(staged.path)
		}
	}
}
