package main

import (
	"crypto/sha1" // #nosec G505 -- SHA-1 is a compatibility revision, not a security signature.
	"crypto/sha256"
	"encoding/hex"
	"flag"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

const sourcePathPrefix = "backend/new-api-relay/"

var (
	ignoredDirectories = map[string]struct{}{
		".git": {}, ".gocache": {}, "bin": {}, "dist": {}, "node_modules": {},
	}
	sha1Pattern   = regexp.MustCompile(`^[0-9a-f]{40}$`)
	sha256Pattern = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	countPattern  = regexp.MustCompile(`^[1-9][0-9]*$`)
)

type sourceFile struct {
	absolute string
	portable string
}

func included(path string) bool {
	name := filepath.Base(path)
	return strings.HasSuffix(path, ".go") ||
		name == "go.mod" ||
		name == "go.sum" ||
		path == "Dockerfile" ||
		path == "Dockerfile.dev" ||
		path == ".dockerignore" ||
		path == "VERSION" ||
		path == "LICENSE" ||
		path == "NOTICE" ||
		path == "THIRD-PARTY-LICENSES.md" ||
		strings.HasPrefix(path, "web/") ||
		strings.HasPrefix(path, "i18n/locales/") ||
		(strings.HasPrefix(path, "generationprofile/") && strings.HasSuffix(path, ".json")) ||
		path == "common/limiter/lua/rate_limit.lua"
}

func discover(root string) ([]sourceFile, error) {
	files := make([]sourceFile, 0, 256)
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if path == root {
			return nil
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("Relay source contains a symbolic link: %s", path)
		}
		if entry.IsDir() {
			if _, ignored := ignoredDirectories[entry.Name()]; ignored {
				return filepath.SkipDir
			}
			return nil
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		portable := filepath.ToSlash(relative)
		if !strings.Contains(portable, "/") && strings.HasPrefix(portable, ".tmp-new-api-") {
			return fmt.Errorf("Relay source root contains a forbidden temporary build artifact")
		}
		if entry.Type().IsRegular() && included(portable) {
			files = append(files, sourceFile{absolute: path, portable: sourcePathPrefix + portable})
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Slice(files, func(left, right int) bool { return files[left].portable < files[right].portable })
	return files, nil
}

func snapshot(files []sourceFile) (string, string, error) {
	legacy := sha1.New() // #nosec G401 -- compatibility revision only; SHA-256 is verified too.
	secure := sha256.New()
	for _, file := range files {
		contents, err := os.ReadFile(file.absolute)
		if err != nil {
			return "", "", err
		}
		for _, digest := range []interface{ Write([]byte) (int, error) }{legacy, secure} {
			_, _ = digest.Write([]byte(file.portable))
			_, _ = digest.Write([]byte{0})
			_, _ = digest.Write(contents)
			_, _ = digest.Write([]byte{0})
		}
	}
	return hex.EncodeToString(legacy.Sum(nil)), "sha256:" + hex.EncodeToString(secure.Sum(nil)), nil
}

func run() error {
	root := flag.String("root", ".", "Relay source root")
	expectedRevision := flag.String("expected-revision", "", "expected lowercase SHA-1 source revision")
	expectedSHA256 := flag.String("expected-sha256", "", "expected lowercase sha256: source digest")
	expectedCount := flag.String("expected-file-count", "", "expected positive source file count")
	flag.Parse()
	if flag.NArg() != 0 {
		return fmt.Errorf("unexpected positional arguments")
	}
	if !sha1Pattern.MatchString(*expectedRevision) {
		return fmt.Errorf("expected source revision must be a lowercase 40-hex value")
	}
	if !sha256Pattern.MatchString(*expectedSHA256) {
		return fmt.Errorf("expected source digest must be a lowercase sha256: value")
	}
	if !countPattern.MatchString(*expectedCount) {
		return fmt.Errorf("expected source file count must be a canonical positive integer")
	}
	count, err := strconv.Atoi(*expectedCount)
	if err != nil {
		return fmt.Errorf("parse expected source file count: %w", err)
	}
	files, err := discover(filepath.Clean(*root))
	if err != nil {
		return err
	}
	revision, digest, err := snapshot(files)
	if err != nil {
		return err
	}
	if len(files) != count || revision != *expectedRevision || digest != *expectedSHA256 {
		return fmt.Errorf(
			"Relay build source identity mismatch: got revision=%s digest=%s file_count=%d",
			revision,
			digest,
			len(files),
		)
	}
	fmt.Printf("verified Relay build source revision=%s digest=%s file_count=%d\n", revision, digest, len(files))
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
