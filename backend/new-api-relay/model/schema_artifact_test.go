package model

import (
	"bytes"
	"crypto/sha256"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
)

func TestRelaySchemaV1ArtifactsAreFrozen(t *testing.T) {
	if !relaySchemaV1LiveArtifactValidationRequired(RelaySchemaTargetVersion) {
		assertRelaySchemaV1HistoricalDefinition(t)
		return
	}
	modelDigest := sha256.Sum256(relaySchemaV1LiveModelManifestBytes())
	actualModel := fmt.Sprintf("sha256:%x", modelDigest[:])
	if actualModel != relaySchemaV1ModelArtifactSHA256 {
		t.Fatalf("v1 model artifact changed: got %s; add a new migration version instead of reinterpreting v1", actualModel)
	}

	source, err := relaySchemaV1LiveSourceArtifact()
	if err != nil {
		t.Fatal(err)
	}
	sourceDigest := sha256.Sum256(source)
	actualSource := fmt.Sprintf("sha256:%x", sourceDigest[:])
	if actualSource != relaySchemaV1SourceArtifactSHA256 {
		t.Fatalf("v1 source artifact changed: got %s; add a new migration version instead of editing v1", actualSource)
	}
	if relaySchemaV1FrozenChecksumSHA256 == "sha256:pending" {
		t.Fatalf("freeze v1 migration checksum as %s", RelaySchemaV1Checksum())
	}
	if RelaySchemaV1Checksum() != relaySchemaV1FrozenChecksumSHA256 {
		t.Fatalf("v1 migration checksum changed: got %s", RelaySchemaV1Checksum())
	}
	assertRelaySchemaV1HistoricalDefinition(t)
}

func TestRelaySchemaV2ArtifactsAreFrozen(t *testing.T) {
	if !relaySchemaV2LiveArtifactValidationRequired(RelaySchemaTargetVersion) {
		assertRelaySchemaV2HistoricalDefinition(t)
		return
	}
	modelDigest := sha256.Sum256(relaySchemaV2LiveModelManifestBytes())
	actualModel := fmt.Sprintf("sha256:%x", modelDigest[:])
	if actualModel != relaySchemaV2ModelArtifactSHA256 {
		t.Errorf("v2 model artifact changed: got %s; add a new migration version instead of reinterpreting v2", actualModel)
	}

	source, err := relaySchemaV2LiveSourceArtifact()
	if err != nil {
		t.Fatal(err)
	}
	sourceDigest := sha256.Sum256(source)
	actualSource := fmt.Sprintf("sha256:%x", sourceDigest[:])
	if actualSource != relaySchemaV2SourceArtifactSHA256 {
		t.Errorf("v2 source artifact changed: got %s; add a new migration version instead of editing v2", actualSource)
	}
	if relaySchemaV2FrozenChecksumSHA256 == "sha256:pending" {
		t.Errorf("freeze v2 migration checksum as %s", RelaySchemaV2Checksum())
	} else if RelaySchemaV2Checksum() != relaySchemaV2FrozenChecksumSHA256 {
		t.Errorf("v2 migration checksum changed: got %s", RelaySchemaV2Checksum())
	}
	assertRelaySchemaV2HistoricalDefinition(t)
}

func TestRelaySchemaV3ArtifactsAreFrozen(t *testing.T) {
	if !relaySchemaV3LiveArtifactValidationRequired(RelaySchemaTargetVersion) {
		assertRelaySchemaV3HistoricalDefinition(t)
		return
	}
	modelDigest := sha256.Sum256(relaySchemaV3LiveModelManifestBytes())
	actualModel := fmt.Sprintf("sha256:%x", modelDigest[:])
	if actualModel != relaySchemaV3ModelArtifactSHA256 {
		t.Errorf("v3 model artifact changed: got %s; add a new migration version instead of reinterpreting v3", actualModel)
	}

	source, err := relaySchemaV3LiveSourceArtifact()
	if err != nil {
		t.Fatal(err)
	}
	sourceDigest := sha256.Sum256(source)
	actualSource := fmt.Sprintf("sha256:%x", sourceDigest[:])
	if actualSource != relaySchemaV3SourceArtifactSHA256 {
		t.Errorf("v3 source artifact changed: got %s; add a new migration version instead of editing v3", actualSource)
	}
	if relaySchemaV3FrozenChecksumSHA256 == "sha256:pending" {
		t.Errorf("freeze v3 migration checksum as %s", RelaySchemaV3Checksum())
	} else if RelaySchemaV3Checksum() != relaySchemaV3FrozenChecksumSHA256 {
		t.Errorf("v3 migration checksum changed: got %s", RelaySchemaV3Checksum())
	}
	assertRelaySchemaV3HistoricalDefinition(t)
}

func TestRelaySchemaV4ArtifactsAreFrozen(t *testing.T) {
	if !relaySchemaV4LiveArtifactValidationRequired(RelaySchemaTargetVersion) {
		assertRelaySchemaV4HistoricalDefinition(t)
		return
	}
	modelDigest := sha256.Sum256(relaySchemaV4LiveModelManifestBytes())
	actualModel := fmt.Sprintf("sha256:%x", modelDigest[:])
	if relaySchemaV4ModelArtifactSHA256 == "sha256:pending" {
		t.Errorf("freeze v4 model artifact as %s", actualModel)
	} else if actualModel != relaySchemaV4ModelArtifactSHA256 {
		t.Errorf("v4 model artifact changed: got %s; add a new migration version instead of reinterpreting v4", actualModel)
	}

	source, err := relaySchemaV4LiveSourceArtifact()
	if err != nil {
		t.Fatal(err)
	}
	sourceDigest := sha256.Sum256(source)
	actualSource := fmt.Sprintf("sha256:%x", sourceDigest[:])
	if relaySchemaV4SourceArtifactSHA256 == "sha256:pending" {
		t.Errorf("freeze v4 source artifact as %s", actualSource)
	} else if actualSource != relaySchemaV4SourceArtifactSHA256 {
		t.Errorf("v4 source artifact changed: got %s; add a new migration version instead of editing v4", actualSource)
	}
	if relaySchemaV4FrozenChecksumSHA256 == "sha256:pending" {
		t.Errorf("freeze v4 migration checksum as %s", RelaySchemaV4Checksum())
	} else if RelaySchemaV4Checksum() != relaySchemaV4FrozenChecksumSHA256 {
		t.Errorf("v4 migration checksum changed: got %s", RelaySchemaV4Checksum())
	}
	assertRelaySchemaV4HistoricalDefinition(t)
}

func TestRelaySchemaV5ArtifactsAreFrozen(t *testing.T) {
	if !relaySchemaV5LiveArtifactValidationRequired(RelaySchemaTargetVersion) {
		assertRelaySchemaV5HistoricalDefinition(t)
		return
	}
	modelDigest := sha256.Sum256(relaySchemaV5LiveModelManifestBytes())
	actualModel := fmt.Sprintf("sha256:%x", modelDigest[:])
	if relaySchemaV5ModelArtifactSHA256 == "sha256:pending" {
		t.Errorf("freeze v5 model artifact as %s", actualModel)
	} else if actualModel != relaySchemaV5ModelArtifactSHA256 {
		t.Errorf("v5 model artifact changed: got %s; add a new migration version instead of reinterpreting v5", actualModel)
	}

	source, err := relaySchemaV5LiveSourceArtifact()
	if err != nil {
		t.Fatal(err)
	}
	sourceDigest := sha256.Sum256(source)
	actualSource := fmt.Sprintf("sha256:%x", sourceDigest[:])
	if relaySchemaV5SourceArtifactSHA256 == "sha256:pending" {
		t.Errorf("freeze v5 source artifact as %s", actualSource)
	} else if actualSource != relaySchemaV5SourceArtifactSHA256 {
		t.Errorf("v5 source artifact changed: got %s; add a new migration version instead of editing v5", actualSource)
	}
	if relaySchemaV5FrozenChecksumSHA256 == "sha256:pending" {
		t.Errorf("freeze v5 migration checksum as %s", RelaySchemaV5Checksum())
	} else if RelaySchemaV5Checksum() != relaySchemaV5FrozenChecksumSHA256 {
		t.Errorf("v5 migration checksum changed: got %s", RelaySchemaV5Checksum())
	}
	assertRelaySchemaV5HistoricalDefinition(t)
}

// V5 remains independently reproducible after the live operation ORM grows
// v6 lifecycle fields. This check is unconditional: unlike the release-time
// live artifact gate above, it must keep protecting the pinned historical
// projection while a later schema version is the current target.
func TestRelaySchemaV5PinnedModelArtifactIsFrozen(t *testing.T) {
	manifest := relaySchemaV5LiveModelManifestBytes()
	for _, v6Field := range []string{
		"IntentTransportRevision", "ProviderSubmissionState", "ProviderTaskID",
		"ProviderArtifactSHA256", "ReconciliationReason",
	} {
		if bytes.Contains(manifest, []byte("field|"+v6Field+"|")) {
			t.Fatalf("v5 artifact absorbed v6 field %s", v6Field)
		}
	}
	digest := sha256.Sum256(manifest)
	actual := fmt.Sprintf("sha256:%x", digest[:])
	if actual != relaySchemaV5ModelArtifactSHA256 {
		t.Fatalf("pinned v5 model artifact changed: got %s", actual)
	}
	if RelaySchemaV5Checksum() != relaySchemaV5FrozenChecksumSHA256 {
		t.Fatalf("frozen v5 checksum changed: got %s", RelaySchemaV5Checksum())
	}
}

func TestRelaySchemaHistoricalChannelCostModelArtifactsRemainPinned(t *testing.T) {
	historical := []struct {
		name     string
		manifest func() []byte
		expected string
	}{
		{name: "v1", manifest: relaySchemaV1LiveModelManifestBytes},
		{name: "v2", manifest: relaySchemaV2LiveModelManifestBytes},
		{name: "v3", manifest: relaySchemaV3LiveModelManifestBytes},
		{name: "v4", manifest: relaySchemaV4LiveModelManifestBytes},
		{name: "v5", manifest: relaySchemaV5LiveModelManifestBytes, expected: relaySchemaV5ModelArtifactSHA256},
		{name: "v6", manifest: relaySchemaV6LiveModelManifestBytes, expected: relaySchemaV6ModelArtifactSHA256},
	}
	for _, release := range historical {
		t.Run(release.name, func(t *testing.T) {
			manifest := release.manifest()
			if bytes.Contains(manifest, []byte("field|PersonalWorkspaceID|")) {
				t.Fatalf("%s channel-cost artifact absorbed the v7 personal scope", release.name)
			}
			if release.expected != "" {
				digest := sha256.Sum256(manifest)
				actual := fmt.Sprintf("sha256:%x", digest[:])
				if actual != release.expected {
					t.Fatalf("%s pinned model artifact changed: got %s", release.name, actual)
				}
			}
		})
	}

	v7Manifest := relaySchemaV7LiveModelManifestBytes()
	if count := bytes.Count(v7Manifest, []byte("field|PersonalWorkspaceID|")); count != 1 {
		t.Fatalf("v7 must freeze exactly one personal channel-cost scope field, got %d", count)
	}
	v7Digest := sha256.Sum256(v7Manifest)
	v7Actual := fmt.Sprintf("sha256:%x", v7Digest[:])
	if v7Actual != relaySchemaV7ModelArtifactSHA256 {
		t.Fatalf("v7 pinned model artifact changed: got %s", v7Actual)
	}
}

func TestRelaySchemaV6ArtifactsAreFrozen(t *testing.T) {
	if !relaySchemaV6LiveArtifactValidationRequired(RelaySchemaTargetVersion) {
		assertRelaySchemaV6HistoricalDefinition(t)
		return
	}
	modelDigest := sha256.Sum256(relaySchemaV6LiveModelManifestBytes())
	actualModel := fmt.Sprintf("sha256:%x", modelDigest[:])
	if relaySchemaV6ModelArtifactSHA256 == "sha256:pending" {
		t.Errorf("freeze v6 model artifact as %s", actualModel)
	} else if actualModel != relaySchemaV6ModelArtifactSHA256 {
		t.Errorf("v6 model artifact changed: got %s; add a new migration version instead of reinterpreting v6", actualModel)
	}

	source, err := relaySchemaV6LiveSourceArtifact()
	if err != nil {
		t.Fatal(err)
	}
	sourceDigest := sha256.Sum256(source)
	actualSource := fmt.Sprintf("sha256:%x", sourceDigest[:])
	if relaySchemaV6SourceArtifactSHA256 == "sha256:pending" {
		t.Errorf("freeze v6 source artifact as %s", actualSource)
	} else if actualSource != relaySchemaV6SourceArtifactSHA256 {
		t.Errorf("v6 source artifact changed: got %s; add a new migration version instead of editing v6", actualSource)
	}
	if relaySchemaV6FrozenChecksumSHA256 == "sha256:pending" {
		t.Errorf("freeze v6 migration checksum as %s", RelaySchemaV6Checksum())
	} else if RelaySchemaV6Checksum() != relaySchemaV6FrozenChecksumSHA256 {
		t.Errorf("v6 migration checksum changed: got %s", RelaySchemaV6Checksum())
	}
	assertRelaySchemaV6HistoricalDefinition(t)
}

func TestRelaySchemaV7ArtifactsAreFrozen(t *testing.T) {
	if relaySchemaV7LiveArtifactValidationRequired(RelaySchemaTargetVersion) {
		modelDigest := sha256.Sum256(relaySchemaV7LiveModelManifestBytes())
		actualModel := fmt.Sprintf("sha256:%x", modelDigest[:])
		if relaySchemaV7ModelArtifactSHA256 == "sha256:pending" {
			t.Errorf("freeze v7 model artifact as %s", actualModel)
		} else if actualModel != relaySchemaV7ModelArtifactSHA256 {
			t.Errorf("v7 model artifact changed: got %s; add a new migration version instead of reinterpreting v7", actualModel)
		}

		source, err := relaySchemaV7LiveSourceArtifact()
		if err != nil {
			t.Fatal(err)
		}
		sourceDigest := sha256.Sum256(source)
		actualSource := fmt.Sprintf("sha256:%x", sourceDigest[:])
		if relaySchemaV7SourceArtifactSHA256 == "sha256:pending" {
			t.Errorf("freeze v7 source artifact as %s", actualSource)
		} else if actualSource != relaySchemaV7SourceArtifactSHA256 {
			t.Errorf("v7 source artifact changed: got %s; add a new migration version instead of editing v7", actualSource)
		}
	}
	if relaySchemaV7FrozenChecksumSHA256 == "sha256:pending" {
		t.Errorf("freeze v7 migration checksum as %s", RelaySchemaV7Checksum())
	} else if RelaySchemaV7Checksum() != relaySchemaV7FrozenChecksumSHA256 {
		t.Errorf("v7 migration checksum changed: got %s", RelaySchemaV7Checksum())
	}
	assertRelaySchemaV7HistoricalDefinition(t)
}

func TestRelaySchemaV8ArtifactsAreFrozen(t *testing.T) {
	modelDigest := sha256.Sum256(relaySchemaV8LiveModelManifestBytes())
	actualModel := fmt.Sprintf("sha256:%x", modelDigest[:])
	if relaySchemaV8ModelArtifactSHA256 == "sha256:pending" {
		t.Errorf("freeze v8 model artifact as %s", actualModel)
	} else if actualModel != relaySchemaV8ModelArtifactSHA256 {
		t.Errorf("v8 model artifact changed: got %s; add a new migration version instead of reinterpreting v8", actualModel)
	}

	source, err := relaySchemaV8LiveSourceArtifact()
	if err != nil {
		t.Fatal(err)
	}
	sourceDigest := sha256.Sum256(source)
	actualSource := fmt.Sprintf("sha256:%x", sourceDigest[:])
	if relaySchemaV8SourceArtifactSHA256 == "sha256:pending" {
		t.Errorf("freeze v8 source artifact as %s", actualSource)
	} else if actualSource != relaySchemaV8SourceArtifactSHA256 {
		t.Errorf("v8 source artifact changed: got %s; add a new migration version instead of editing v8", actualSource)
	}
	if relaySchemaV8FrozenChecksumSHA256 == "sha256:pending" {
		t.Errorf("freeze v8 migration checksum as %s", RelaySchemaV8Checksum())
	} else if RelaySchemaV8Checksum() != relaySchemaV8FrozenChecksumSHA256 {
		t.Errorf("v8 migration checksum changed: got %s", RelaySchemaV8Checksum())
	}
	assertRelaySchemaV8HistoricalDefinition(t)
}

func TestRelaySchemaV3ArtifactIncludesExecutedV2Incremental(t *testing.T) {
	definitions := relaySchemaMigrations()
	plan, err := buildRelaySchemaExecutionPlan(RelaySchemaStatus{
		Classification:  RelaySchemaStatusCompatible,
		BaselineVersion: relaySchemaV1FrozenVersion,
		CurrentVersion:  relaySchemaV1FrozenVersion,
	}, definitions[:3], relaySchemaV3FrozenVersion)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Incremental) != 2 || plan.Incremental[0].Version != relaySchemaV2FrozenVersion ||
		plan.Incremental[1].Version != relaySchemaV3FrozenVersion {
		t.Fatal("exact v1-to-v3 planning must execute the v2 incremental before v3")
	}

	source, err := relaySchemaV3LiveSourceArtifact()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(source, []byte("func:migrateRelaySchemaV2NoCatalogDelta\n")) {
		t.Fatal("v3 source artifact does not recursively freeze the still-executed v2 incremental")
	}
}

func TestRelaySchemaCurrentArtifactFreezeCannotSkip(t *testing.T) {
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("schema artifact test source is unavailable")
	}
	fset := token.NewFileSet()
	parsed, err := parser.ParseFile(fset, currentFile, nil, parser.SkipObjectResolution)
	if err != nil {
		t.Fatal(err)
	}
	var freeze *ast.FuncDecl
	for _, declaration := range parsed.Decls {
		function, isFunction := declaration.(*ast.FuncDecl)
		if isFunction && function.Name.Name == "TestRelaySchemaV8ArtifactsAreFrozen" {
			freeze = function
			break
		}
	}
	if freeze == nil {
		t.Fatal("current Relay schema artifact freeze test is missing")
	}
	ast.Inspect(freeze.Body, func(node ast.Node) bool {
		switch typed := node.(type) {
		case *ast.ReturnStmt:
			t.Errorf("current Relay schema artifact freeze test must not contain an early return at %s", fset.Position(typed.Pos()))
		case *ast.CallExpr:
			selector, isSelector := typed.Fun.(*ast.SelectorExpr)
			if isSelector && (selector.Sel.Name == "Skip" || selector.Sel.Name == "Skipf" || selector.Sel.Name == "SkipNow") {
				t.Errorf("current Relay schema artifact freeze test must not skip at %s", fset.Position(typed.Pos()))
			}
		}
		return true
	})
}

func TestRelayDownloadEdgeDatabasePrivilegeManifestV1IsFrozen(t *testing.T) {
	manifest, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(1)
	if err != nil {
		t.Fatal(err)
	}
	if relayDownloadEdgeDatabasePrivilegeManifestV1SHA256 == "sha256:pending" {
		t.Fatalf("freeze v1 download edge privilege manifest digest as %s", relayDownloadEdgeDatabasePrivilegeManifestSHA256(manifest))
	}
	if relayDownloadEdgeDatabasePrivilegeManifestSHA256(manifest) != relayDownloadEdgeDatabasePrivilegeManifestV1SHA256 {
		t.Fatal("v1 download edge privilege manifest digest changed")
	}
	if _, exists := manifest.Tables["relay_schema_state"]; !exists || !manifest.Tables["relay_schema_state"].Select {
		t.Fatal("download edge must retain read-only schema status access")
	}
	if _, exists := manifest.Tables["simulated_v2_table"]; exists {
		t.Fatal("v1 download edge manifest must not absorb future tables")
	}
}

func TestRelayDownloadEdgeDatabasePrivilegeManifestV2IsFrozen(t *testing.T) {
	manifest, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(2)
	if err != nil {
		t.Fatal(err)
	}
	if relayDownloadEdgeDatabasePrivilegeManifestSHA256(manifest) != relayDownloadEdgeDatabasePrivilegeManifestV2SHA256 {
		t.Fatal("v2 download edge privilege manifest digest changed")
	}
	v1, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(1)
	if err != nil {
		t.Fatal(err)
	}
	if relayDownloadEdgeDatabasePrivilegeManifestCanonical(manifest) != relayDownloadEdgeDatabasePrivilegeManifestCanonical(v1) {
		t.Fatal("no-catalog-delta v2 download edge manifest differs from v1")
	}
}

func TestRelayDownloadEdgeDatabasePrivilegeManifestV3IsFrozen(t *testing.T) {
	manifest, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(3)
	if err != nil {
		t.Fatal(err)
	}
	if relayDownloadEdgeDatabasePrivilegeManifestSHA256(manifest) != relayDownloadEdgeDatabasePrivilegeManifestV3SHA256 {
		t.Fatal("v3 download edge privilege manifest digest changed")
	}
	v2, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(2)
	if err != nil {
		t.Fatal(err)
	}
	if relayDownloadEdgeDatabasePrivilegeManifestCanonical(manifest) != relayDownloadEdgeDatabasePrivilegeManifestCanonical(v2) {
		t.Fatal("no-ACL-delta v3 download edge manifest differs from v2")
	}
}

func TestRelayDownloadEdgeDatabasePrivilegeManifestV4IsFrozen(t *testing.T) {
	manifest, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(4)
	if err != nil {
		t.Fatal(err)
	}
	if relayDownloadEdgeDatabasePrivilegeManifestSHA256(manifest) != relayDownloadEdgeDatabasePrivilegeManifestV4SHA256 {
		t.Fatal("v4 download edge privilege manifest digest changed")
	}
	v3, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(3)
	if err != nil {
		t.Fatal(err)
	}
	if relayDownloadEdgeDatabasePrivilegeManifestCanonical(manifest) != relayDownloadEdgeDatabasePrivilegeManifestCanonical(v3) {
		t.Fatal("no-ACL-delta v4 download edge manifest differs from v3")
	}
}

func TestRelayDownloadEdgeDatabasePrivilegeManifestsV5ThroughV7AreFrozen(t *testing.T) {
	testCases := []struct {
		version  int64
		digest   string
		previous int64
	}{
		{version: 5, digest: relayDownloadEdgeDatabasePrivilegeManifestV5SHA256, previous: 4},
		{version: 6, digest: relayDownloadEdgeDatabasePrivilegeManifestV6SHA256, previous: 5},
		{version: 7, digest: relayDownloadEdgeDatabasePrivilegeManifestV7SHA256, previous: 6},
	}
	for _, testCase := range testCases {
		t.Run(fmt.Sprintf("v%d", testCase.version), func(t *testing.T) {
			manifest, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(testCase.version)
			if err != nil {
				t.Fatal(err)
			}
			if testCase.digest == "sha256:pending" {
				t.Fatalf("freeze v%d download edge privilege manifest digest as %s", testCase.version,
					relayDownloadEdgeDatabasePrivilegeManifestSHA256(manifest))
			}
			if relayDownloadEdgeDatabasePrivilegeManifestSHA256(manifest) != testCase.digest {
				t.Fatalf("v%d download edge privilege manifest digest changed", testCase.version)
			}
			previous, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(testCase.previous)
			if err != nil {
				t.Fatal(err)
			}
			if relayDownloadEdgeDatabasePrivilegeManifestCanonical(manifest) !=
				relayDownloadEdgeDatabasePrivilegeManifestCanonical(previous) {
				t.Fatalf("no-ACL-delta v%d download edge manifest differs from v%d", testCase.version, testCase.previous)
			}
		})
	}
}

func TestRelayRuntimeDatabasePrivilegeManifestV1IsFrozen(t *testing.T) {
	staticManifest, err := relayRuntimeDatabasePrivilegeManifestForVersion(1)
	if relayRuntimeDatabasePrivilegeManifestV1Artifact == "" {
		database := newRelaySchemaSQLite(t)
		liveManifest, liveErr := relayRuntimeDatabasePrivilegeManifestLiveV1(database)
		if liveErr != nil {
			t.Fatal(liveErr)
		}
		t.Fatalf("freeze v1 runtime privilege manifest:\n%s", relayRuntimeDatabasePrivilegeManifestCanonical(liveManifest))
	}
	if err != nil {
		t.Fatal(err)
	}
	if relayRuntimeDatabasePrivilegeManifestV1SHA256 == "sha256:pending" {
		t.Fatalf("freeze v1 runtime privilege manifest digest as %s", relayRuntimeDatabasePrivilegeManifestSHA256(staticManifest))
	}
	if relayRuntimeDatabasePrivilegeManifestSHA256(staticManifest) != relayRuntimeDatabasePrivilegeManifestV1SHA256 {
		t.Fatal("v1 runtime privilege manifest digest changed")
	}
	if relaySchemaV1LiveArtifactValidationRequired(RelaySchemaTargetVersion) {
		database := newRelaySchemaSQLite(t)
		liveManifest, liveErr := relayRuntimeDatabasePrivilegeManifestLiveV1(database)
		if liveErr != nil {
			t.Fatal(liveErr)
		}
		if relayRuntimeDatabasePrivilegeManifestCanonical(liveManifest) !=
			relayRuntimeDatabasePrivilegeManifestCanonical(staticManifest) {
			t.Fatal("v1 runtime privilege manifest changed; add a new schema version and manifest")
		}
	}
}

func TestRelayRuntimeDatabasePrivilegeManifestV2IsFrozen(t *testing.T) {
	manifest, err := relayRuntimeDatabasePrivilegeManifestForVersion(2)
	if err != nil {
		t.Fatal(err)
	}
	if relayRuntimeDatabasePrivilegeManifestSHA256(manifest) != relayRuntimeDatabasePrivilegeManifestV2SHA256 {
		t.Fatal("v2 runtime privilege manifest digest changed")
	}
	v1, err := relayRuntimeDatabasePrivilegeManifestForVersion(1)
	if err != nil {
		t.Fatal(err)
	}
	if relayRuntimeDatabasePrivilegeManifestCanonical(manifest) != relayRuntimeDatabasePrivilegeManifestCanonical(v1) {
		t.Fatal("no-catalog-delta v2 runtime manifest differs from v1")
	}
}

func TestRelayRuntimeDatabasePrivilegeManifestV3IsFrozen(t *testing.T) {
	manifest, err := relayRuntimeDatabasePrivilegeManifestForVersion(3)
	if err != nil {
		t.Fatal(err)
	}
	if relayRuntimeDatabasePrivilegeManifestSHA256(manifest) != relayRuntimeDatabasePrivilegeManifestV3SHA256 {
		t.Fatal("v3 runtime privilege manifest digest changed")
	}
	v2, err := relayRuntimeDatabasePrivilegeManifestForVersion(2)
	if err != nil {
		t.Fatal(err)
	}
	if relayRuntimeDatabasePrivilegeManifestCanonical(manifest) != relayRuntimeDatabasePrivilegeManifestCanonical(v2) {
		t.Fatal("no-ACL-delta v3 runtime manifest differs from v2")
	}
}

func TestRelayRuntimeDatabasePrivilegeManifestV4IsFrozen(t *testing.T) {
	manifest, err := relayRuntimeDatabasePrivilegeManifestForVersion(4)
	if err != nil {
		t.Fatal(err)
	}
	if relayRuntimeDatabasePrivilegeManifestSHA256(manifest) != relayRuntimeDatabasePrivilegeManifestV4SHA256 {
		t.Fatal("v4 runtime privilege manifest digest changed")
	}
	v3, err := relayRuntimeDatabasePrivilegeManifestForVersion(3)
	if err != nil {
		t.Fatal(err)
	}
	if relayRuntimeDatabasePrivilegeManifestCanonical(manifest) != relayRuntimeDatabasePrivilegeManifestCanonical(v3) {
		t.Fatal("no-ACL-delta v4 runtime manifest differs from v3")
	}
}

func TestRelayRuntimeDatabasePrivilegeManifestsV5ThroughV7AreFrozen(t *testing.T) {
	testCases := []struct {
		version  int64
		digest   string
		previous int64
	}{
		{version: 5, digest: relayRuntimeDatabasePrivilegeManifestV5SHA256, previous: 4},
		{version: 6, digest: relayRuntimeDatabasePrivilegeManifestV6SHA256, previous: 5},
		{version: 7, digest: relayRuntimeDatabasePrivilegeManifestV7SHA256, previous: 6},
	}
	for _, testCase := range testCases {
		t.Run(fmt.Sprintf("v%d", testCase.version), func(t *testing.T) {
			manifest, err := relayRuntimeDatabasePrivilegeManifestForVersion(testCase.version)
			if err != nil {
				t.Fatal(err)
			}
			if testCase.digest == "sha256:pending" {
				t.Fatalf("freeze v%d runtime privilege manifest digest as %s", testCase.version,
					relayRuntimeDatabasePrivilegeManifestSHA256(manifest))
			}
			if relayRuntimeDatabasePrivilegeManifestSHA256(manifest) != testCase.digest {
				t.Fatalf("v%d runtime privilege manifest digest changed", testCase.version)
			}
			previous, err := relayRuntimeDatabasePrivilegeManifestForVersion(testCase.previous)
			if err != nil {
				t.Fatal(err)
			}
			if relayRuntimeDatabasePrivilegeManifestCanonical(manifest) !=
				relayRuntimeDatabasePrivilegeManifestCanonical(previous) {
				t.Fatalf("no-ACL-delta v%d runtime manifest differs from v%d", testCase.version, testCase.previous)
			}
		})
	}
}

func TestRelaySchemaV7PersonalCostColumnInheritsOnlyTheExactTablePrivileges(t *testing.T) {
	runtimeManifest, err := relayRuntimeDatabasePrivilegeManifestForVersion(relaySchemaV7FrozenVersion)
	if err != nil {
		t.Fatal(err)
	}
	if actual := runtimeManifest["platform_channel_cost_events"]; actual != (relayTablePrivilegeSet{Select: true, Insert: true}) {
		t.Fatalf("v7 runtime channel-cost privilege surface changed: %+v", actual)
	}
	edgeManifest, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(relaySchemaV7FrozenVersion)
	if err != nil {
		t.Fatal(err)
	}
	if actual := edgeManifest.Tables["platform_channel_cost_events"]; actual != (relayTablePrivilegeSet{}) {
		t.Fatalf("v7 download-edge channel-cost privilege surface changed: %+v", actual)
	}
	if columns := edgeManifest.UpdateColumns["platform_channel_cost_events"]; len(columns) != 0 {
		t.Fatalf("v7 download edge unexpectedly owns channel-cost column privileges: %+v", columns)
	}
}

func TestRelaySchemaV8ProviderCostEvidenceOwnsOnlyAppendPrivileges(t *testing.T) {
	runtimeManifest, err := relayRuntimeDatabasePrivilegeManifestForVersion(relaySchemaV8FrozenVersion)
	if err != nil {
		t.Fatal(err)
	}
	actualRuntimeDigest := relayRuntimeDatabasePrivilegeManifestSHA256(runtimeManifest)
	if relayRuntimeDatabasePrivilegeManifestV8SHA256 == "sha256:pending" {
		t.Fatalf("freeze v8 runtime privilege manifest digest as %s", actualRuntimeDigest)
	}
	if actualRuntimeDigest != relayRuntimeDatabasePrivilegeManifestV8SHA256 {
		t.Fatalf("v8 runtime privilege manifest digest changed: %s", actualRuntimeDigest)
	}
	if actual := runtimeManifest["platform_provider_cost_allocation_evidence"]; actual != (relayTablePrivilegeSet{Select: true, Insert: true}) {
		t.Fatalf("v8 runtime provider-cost evidence privilege surface changed: %+v", actual)
	}

	edgeManifest, err := relayDownloadEdgeDatabasePrivilegeManifestForVersion(relaySchemaV8FrozenVersion)
	if err != nil {
		t.Fatal(err)
	}
	actualEdgeDigest := relayDownloadEdgeDatabasePrivilegeManifestSHA256(edgeManifest)
	if relayDownloadEdgeDatabasePrivilegeManifestV8SHA256 == "sha256:pending" {
		t.Fatalf("freeze v8 download-edge privilege manifest digest as %s", actualEdgeDigest)
	}
	if actualEdgeDigest != relayDownloadEdgeDatabasePrivilegeManifestV8SHA256 {
		t.Fatalf("v8 download-edge privilege manifest digest changed: %s", actualEdgeDigest)
	}
	if actual := edgeManifest.Tables["platform_provider_cost_allocation_evidence"]; actual != (relayTablePrivilegeSet{}) {
		t.Fatalf("v8 download edge unexpectedly owns provider-cost evidence: %+v", actual)
	}
	if columns := edgeManifest.UpdateColumns["platform_provider_cost_allocation_evidence"]; len(columns) != 0 {
		t.Fatalf("v8 download edge unexpectedly owns provider-cost evidence columns: %+v", columns)
	}
}

func assertRelaySchemaV1HistoricalDefinition(t *testing.T) {
	t.Helper()
	definitions := relaySchemaMigrations()
	if len(definitions) < 1 || definitions[0].Version != relaySchemaV1FrozenVersion ||
		definitions[0].Name != relaySchemaV1FrozenName || definitions[0].Phase != relaySchemaV1FrozenPhase ||
		definitions[0].Checksum != relaySchemaV1FrozenChecksumSHA256 ||
		RelaySchemaV1Checksum() != relaySchemaV1FrozenChecksumSHA256 {
		t.Fatal("historical v1 registry definition changed")
	}
}

func assertRelaySchemaV2HistoricalDefinition(t *testing.T) {
	t.Helper()
	definitions := relaySchemaMigrations()
	if len(definitions) < 2 || definitions[1].Version != relaySchemaV2FrozenVersion ||
		definitions[1].Name != relaySchemaV2FrozenName || definitions[1].Phase != relaySchemaV2FrozenPhase ||
		definitions[1].Checksum != relaySchemaV2FrozenChecksumSHA256 ||
		RelaySchemaV2Checksum() != relaySchemaV2FrozenChecksumSHA256 {
		t.Fatal("historical v2 registry definition changed")
	}
}

func assertRelaySchemaV3HistoricalDefinition(t *testing.T) {
	t.Helper()
	definitions := relaySchemaMigrations()
	if len(definitions) < 3 || definitions[2].Version != relaySchemaV3FrozenVersion ||
		definitions[2].Name != relaySchemaV3FrozenName || definitions[2].Phase != relaySchemaV3FrozenPhase ||
		definitions[2].Checksum != relaySchemaV3FrozenChecksumSHA256 ||
		RelaySchemaV3Checksum() != relaySchemaV3FrozenChecksumSHA256 {
		t.Fatal("historical v3 registry definition changed")
	}
}

func assertRelaySchemaV4HistoricalDefinition(t *testing.T) {
	t.Helper()
	definitions := relaySchemaMigrations()
	if len(definitions) < 4 || definitions[3].Version != relaySchemaV4FrozenVersion ||
		definitions[3].Name != relaySchemaV4FrozenName || definitions[3].Phase != relaySchemaV4FrozenPhase ||
		definitions[3].Checksum != relaySchemaV4FrozenChecksumSHA256 ||
		RelaySchemaV4Checksum() != relaySchemaV4FrozenChecksumSHA256 {
		t.Fatal("current v4 registry definition changed")
	}
}

func assertRelaySchemaV5HistoricalDefinition(t *testing.T) {
	t.Helper()
	definitions := relaySchemaMigrations()
	if len(definitions) < 5 || definitions[4].Version != relaySchemaV5FrozenVersion ||
		definitions[4].Name != relaySchemaV5FrozenName || definitions[4].Phase != relaySchemaV5FrozenPhase ||
		definitions[4].Checksum != relaySchemaV5FrozenChecksumSHA256 ||
		RelaySchemaV5Checksum() != relaySchemaV5FrozenChecksumSHA256 {
		t.Fatal("current v5 registry definition changed")
	}
}

func assertRelaySchemaV6HistoricalDefinition(t *testing.T) {
	t.Helper()
	definitions := relaySchemaMigrations()
	if len(definitions) < 6 || definitions[5].Version != relaySchemaV6FrozenVersion ||
		definitions[5].Name != relaySchemaV6FrozenName || definitions[5].Phase != relaySchemaV6FrozenPhase ||
		definitions[5].Checksum != relaySchemaV6FrozenChecksumSHA256 ||
		RelaySchemaV6Checksum() != relaySchemaV6FrozenChecksumSHA256 {
		t.Fatal("current v6 registry definition changed")
	}
}

func assertRelaySchemaV7HistoricalDefinition(t *testing.T) {
	t.Helper()
	definitions := relaySchemaMigrations()
	if len(definitions) < 7 || definitions[6].Version != relaySchemaV7FrozenVersion ||
		definitions[6].Name != relaySchemaV7FrozenName || definitions[6].Phase != relaySchemaV7FrozenPhase ||
		definitions[6].Checksum != relaySchemaV7FrozenChecksumSHA256 ||
		RelaySchemaV7Checksum() != relaySchemaV7FrozenChecksumSHA256 {
		t.Fatal("current v7 registry definition changed")
	}
}

func assertRelaySchemaV8HistoricalDefinition(t *testing.T) {
	t.Helper()
	definitions := relaySchemaMigrations()
	if len(definitions) < 8 || definitions[7].Version != relaySchemaV8FrozenVersion ||
		definitions[7].Name != relaySchemaV8FrozenName || definitions[7].Phase != relaySchemaV8FrozenPhase ||
		definitions[7].Checksum != relaySchemaV8FrozenChecksumSHA256 ||
		RelaySchemaV8Checksum() != relaySchemaV8FrozenChecksumSHA256 {
		t.Fatal("current v8 registry definition changed")
	}
}

// relaySchemaV2LiveSourceArtifact uses a corrected declaration boundary: it
// follows package functions referenced as identifiers, and follows a selector
// method only when that method name resolves to one package-local declaration.
// It therefore captures actual nested migration helpers without pulling every
// unrelated Update/Delete/Scan method in the model package into the release.
func relaySchemaV2LiveSourceArtifact() ([]byte, error) {
	return relaySchemaLiveSourceArtifact([]string{
		"func:GetRelaySchemaContract",
		"func:relaySchemaMigrations",
		"func:RunRelaySchemaMigrations",
		"func:RequireRelaySchemaCompatible",
		"func:RequireRelaySchemaCurrent",
		"func:relaySchemaV2BootstrapSteps",
		"func:migrateRelaySchemaV2Bootstrap",
		"func:migrateRelaySchemaV2Models",
		"func:migrateRelaySchemaV2PreviousCandidateCatalog",
		"func:migrateRelaySchemaV2SubscriptionPlan",
		"func:migrateRelaySchemaV2NoCatalogDelta",
		"func:buildRelaySchemaExecutionPlan",
		"func:validateRelaySchemaRegistry",
		"func:ensureRelaySchemaMetadata",
		"func:installRelaySchemaLedgerGuards",
		"func:GetRelaySchemaStatus",
		"func:markRelaySchemaApplying",
		"func:markRelaySchemaFailed",
		"func:reconcileRelaySchemaCommitOutcome",
		"func:runRelaySchemaBootstrapTransaction",
		"func:runRelaySchemaDefinitionTransaction",
		"func:GetRelaySchemaCatalogFingerprint",
	}, map[string]bool{
		"RelaySchemaV1Checksum":                        true,
		"RelaySchemaV2Checksum":                        true,
		"relaySchemaV1CanonicalBytes":                  true,
		"relaySchemaV2CanonicalBytes":                  true,
		"relaySchemaV1SourceArtifactSHA256":            true,
		"relaySchemaV1ModelArtifactSHA256":             true,
		"relaySchemaV1FrozenChecksumSHA256":            true,
		"relaySchemaV2SourceArtifactSHA256":            true,
		"relaySchemaV2ModelArtifactSHA256":             true,
		"relaySchemaV2FrozenChecksumSHA256":            true,
		"relaySchemaV1LiveModelManifestBytes":          true,
		"relaySchemaV2LiveModelManifestBytes":          true,
		"relaySchemaV1Models":                          true,
		"relaySchemaV1ArtifactModels":                  true,
		"relaySchemaV1Steps":                           true,
		"migrateRelaySchemaV1":                         true,
		"migrateRelaySchemaV1Models":                   true,
		"migrateRelaySchemaV1SubscriptionPlan":         true,
		"migrateRelaySchemaV1PreviousCandidateCatalog": true,
	}, 2)
}

func relaySchemaV3LiveSourceArtifact() ([]byte, error) {
	return relaySchemaLiveSourceArtifact([]string{
		"func:GetRelaySchemaContract",
		"func:relaySchemaMigrations",
		"func:RunRelaySchemaMigrations",
		"func:RequireRelaySchemaCompatible",
		"func:RequireRelaySchemaCurrent",
		"func:relaySchemaV3BootstrapSteps",
		"func:migrateRelaySchemaV3Bootstrap",
		"func:migrateRelaySchemaV3Models",
		"func:migrateRelaySchemaV3PreviousCandidateCatalog",
		"func:migrateRelaySchemaV3SubscriptionPlan",
		"func:migrateRelaySchemaV3ProviderChannelCredentialOrdering",
		"func:buildRelaySchemaExecutionPlan",
		"func:validateRelaySchemaRegistry",
		"func:ensureRelaySchemaMetadata",
		"func:installRelaySchemaLedgerGuards",
		"func:GetRelaySchemaStatus",
		"func:markRelaySchemaApplying",
		"func:markRelaySchemaFailed",
		"func:reconcileRelaySchemaCommitOutcome",
		"func:runRelaySchemaBootstrapTransaction",
		"func:runRelaySchemaDefinitionTransaction",
		"func:GetRelaySchemaCatalogFingerprint",
	}, map[string]bool{
		"RelaySchemaV1Checksum":                        true,
		"RelaySchemaV2Checksum":                        true,
		"RelaySchemaV3Checksum":                        true,
		"relaySchemaV1CanonicalBytes":                  true,
		"relaySchemaV2CanonicalBytes":                  true,
		"relaySchemaV3CanonicalBytes":                  true,
		"relaySchemaV1SourceArtifactSHA256":            true,
		"relaySchemaV1ModelArtifactSHA256":             true,
		"relaySchemaV1FrozenChecksumSHA256":            true,
		"relaySchemaV2SourceArtifactSHA256":            true,
		"relaySchemaV2ModelArtifactSHA256":             true,
		"relaySchemaV2FrozenChecksumSHA256":            true,
		"relaySchemaV3SourceArtifactSHA256":            true,
		"relaySchemaV3ModelArtifactSHA256":             true,
		"relaySchemaV3FrozenChecksumSHA256":            true,
		"relaySchemaV1LiveModelManifestBytes":          true,
		"relaySchemaV2LiveModelManifestBytes":          true,
		"relaySchemaV3LiveModelManifestBytes":          true,
		"relaySchemaV1Models":                          true,
		"relaySchemaV1ArtifactModels":                  true,
		"relaySchemaV1Steps":                           true,
		"migrateRelaySchemaV1":                         true,
		"migrateRelaySchemaV1Models":                   true,
		"migrateRelaySchemaV1SubscriptionPlan":         true,
		"migrateRelaySchemaV1PreviousCandidateCatalog": true,
		"relaySchemaV2Models":                          true,
		"relaySchemaV2ArtifactModels":                  true,
		"relaySchemaV2BootstrapSteps":                  true,
		"migrateRelaySchemaV2Bootstrap":                true,
		"migrateRelaySchemaV2Models":                   true,
		"migrateRelaySchemaV2SubscriptionPlan":         true,
		"migrateRelaySchemaV2PreviousCandidateCatalog": true,
	}, 3)
}

func relaySchemaV4LiveSourceArtifact() ([]byte, error) {
	return relaySchemaLiveSourceArtifact([]string{
		"func:GetRelaySchemaContract",
		"func:relaySchemaMigrations",
		"func:RunRelaySchemaMigrations",
		"func:RequireRelaySchemaCompatible",
		"func:RequireRelaySchemaCurrent",
		"func:relaySchemaV4BootstrapSteps",
		"func:migrateRelaySchemaV4Bootstrap",
		"func:migrateRelaySchemaV4Models",
		"func:migrateRelaySchemaV4PreviousCandidateCatalog",
		"func:migrateRelaySchemaV4SubscriptionPlan",
		"func:migrateRelaySchemaV4GenerationRouteReleaseBinding",
		"func:buildRelaySchemaExecutionPlan",
		"func:validateRelaySchemaRegistry",
		"func:ensureRelaySchemaMetadata",
		"func:installRelaySchemaLedgerGuards",
		"func:GetRelaySchemaStatus",
		"func:markRelaySchemaApplying",
		"func:markRelaySchemaFailed",
		"func:reconcileRelaySchemaCommitOutcome",
		"func:runRelaySchemaBootstrapTransaction",
		"func:runRelaySchemaDefinitionTransaction",
		"func:GetRelaySchemaCatalogFingerprint",
	}, map[string]bool{
		"RelaySchemaV1Checksum":                        true,
		"RelaySchemaV2Checksum":                        true,
		"RelaySchemaV3Checksum":                        true,
		"RelaySchemaV4Checksum":                        true,
		"relaySchemaV1CanonicalBytes":                  true,
		"relaySchemaV2CanonicalBytes":                  true,
		"relaySchemaV3CanonicalBytes":                  true,
		"relaySchemaV4CanonicalBytes":                  true,
		"relaySchemaV1SourceArtifactSHA256":            true,
		"relaySchemaV1ModelArtifactSHA256":             true,
		"relaySchemaV1FrozenChecksumSHA256":            true,
		"relaySchemaV2SourceArtifactSHA256":            true,
		"relaySchemaV2ModelArtifactSHA256":             true,
		"relaySchemaV2FrozenChecksumSHA256":            true,
		"relaySchemaV3SourceArtifactSHA256":            true,
		"relaySchemaV3ModelArtifactSHA256":             true,
		"relaySchemaV3FrozenChecksumSHA256":            true,
		"relaySchemaV4SourceArtifactSHA256":            true,
		"relaySchemaV4ModelArtifactSHA256":             true,
		"relaySchemaV4FrozenChecksumSHA256":            true,
		"relaySchemaV1LiveModelManifestBytes":          true,
		"relaySchemaV2LiveModelManifestBytes":          true,
		"relaySchemaV3LiveModelManifestBytes":          true,
		"relaySchemaV4LiveModelManifestBytes":          true,
		"relaySchemaV1Models":                          true,
		"relaySchemaV1ArtifactModels":                  true,
		"relaySchemaV1Steps":                           true,
		"migrateRelaySchemaV1":                         true,
		"migrateRelaySchemaV1Models":                   true,
		"migrateRelaySchemaV1SubscriptionPlan":         true,
		"migrateRelaySchemaV1PreviousCandidateCatalog": true,
		"relaySchemaV2Models":                          true,
		"relaySchemaV2ArtifactModels":                  true,
		"relaySchemaV2BootstrapSteps":                  true,
		"migrateRelaySchemaV2Bootstrap":                true,
		"migrateRelaySchemaV2Models":                   true,
		"migrateRelaySchemaV2SubscriptionPlan":         true,
		"migrateRelaySchemaV2PreviousCandidateCatalog": true,
		"relaySchemaV3Models":                          true,
		"relaySchemaV3ArtifactModels":                  true,
		"relaySchemaV3BootstrapSteps":                  true,
		"migrateRelaySchemaV3Bootstrap":                true,
		"migrateRelaySchemaV3Models":                   true,
		"migrateRelaySchemaV3SubscriptionPlan":         true,
		"migrateRelaySchemaV3PreviousCandidateCatalog": true,
	}, 4)
}

func relaySchemaV5LiveSourceArtifact() ([]byte, error) {
	return relaySchemaLiveSourceArtifact([]string{
		"func:GetRelaySchemaContract",
		"func:relaySchemaMigrations",
		"func:RunRelaySchemaMigrations",
		"func:RequireRelaySchemaCompatible",
		"func:RequireRelaySchemaCurrent",
		"func:relaySchemaV5BootstrapSteps",
		"func:migrateRelaySchemaV5Bootstrap",
		"func:migrateRelaySchemaV5Models",
		"func:migrateRelaySchemaV5PreviousCandidateCatalog",
		"func:migrateRelaySchemaV5SubscriptionPlan",
		"func:migrateRelaySchemaV5ChannelTestDiagnosticTaxonomy",
		"func:installPlatformChannelControlDiagnosticTaxonomyV5WithDB",
		"func:buildRelaySchemaExecutionPlan",
		"func:validateRelaySchemaRegistry",
		"func:ensureRelaySchemaMetadata",
		"func:installRelaySchemaLedgerGuards",
		"func:GetRelaySchemaStatus",
		"func:markRelaySchemaApplying",
		"func:markRelaySchemaFailed",
		"func:reconcileRelaySchemaCommitOutcome",
		"func:runRelaySchemaBootstrapTransaction",
		"func:runRelaySchemaDefinitionTransaction",
		"func:GetRelaySchemaCatalogFingerprint",
	}, map[string]bool{
		"RelaySchemaV1Checksum":                             true,
		"RelaySchemaV2Checksum":                             true,
		"RelaySchemaV3Checksum":                             true,
		"RelaySchemaV4Checksum":                             true,
		"RelaySchemaV5Checksum":                             true,
		"relaySchemaV1CanonicalBytes":                       true,
		"relaySchemaV2CanonicalBytes":                       true,
		"relaySchemaV3CanonicalBytes":                       true,
		"relaySchemaV4CanonicalBytes":                       true,
		"relaySchemaV5CanonicalBytes":                       true,
		"relaySchemaV1SourceArtifactSHA256":                 true,
		"relaySchemaV1ModelArtifactSHA256":                  true,
		"relaySchemaV1FrozenChecksumSHA256":                 true,
		"relaySchemaV2SourceArtifactSHA256":                 true,
		"relaySchemaV2ModelArtifactSHA256":                  true,
		"relaySchemaV2FrozenChecksumSHA256":                 true,
		"relaySchemaV3SourceArtifactSHA256":                 true,
		"relaySchemaV3ModelArtifactSHA256":                  true,
		"relaySchemaV3FrozenChecksumSHA256":                 true,
		"relaySchemaV4SourceArtifactSHA256":                 true,
		"relaySchemaV4ModelArtifactSHA256":                  true,
		"relaySchemaV4FrozenChecksumSHA256":                 true,
		"relaySchemaV5SourceArtifactSHA256":                 true,
		"relaySchemaV5ModelArtifactSHA256":                  true,
		"relaySchemaV5FrozenChecksumSHA256":                 true,
		"relaySchemaV1LiveModelManifestBytes":               true,
		"relaySchemaV2LiveModelManifestBytes":               true,
		"relaySchemaV3LiveModelManifestBytes":               true,
		"relaySchemaV4LiveModelManifestBytes":               true,
		"relaySchemaV5LiveModelManifestBytes":               true,
		"relaySchemaV1Models":                               true,
		"relaySchemaV1ArtifactModels":                       true,
		"relaySchemaV1Steps":                                true,
		"migrateRelaySchemaV1":                              true,
		"migrateRelaySchemaV1Models":                        true,
		"migrateRelaySchemaV1SubscriptionPlan":              true,
		"migrateRelaySchemaV1PreviousCandidateCatalog":      true,
		"relaySchemaV2Models":                               true,
		"relaySchemaV2ArtifactModels":                       true,
		"relaySchemaV2BootstrapSteps":                       true,
		"migrateRelaySchemaV2Bootstrap":                     true,
		"migrateRelaySchemaV2Models":                        true,
		"migrateRelaySchemaV2SubscriptionPlan":              true,
		"migrateRelaySchemaV2PreviousCandidateCatalog":      true,
		"relaySchemaV3Models":                               true,
		"relaySchemaV3ArtifactModels":                       true,
		"relaySchemaV3BootstrapSteps":                       true,
		"migrateRelaySchemaV3Bootstrap":                     true,
		"migrateRelaySchemaV3Models":                        true,
		"migrateRelaySchemaV3SubscriptionPlan":              true,
		"migrateRelaySchemaV3PreviousCandidateCatalog":      true,
		"relaySchemaV4Models":                               true,
		"relaySchemaV4ArtifactModels":                       true,
		"relaySchemaV4BootstrapSteps":                       true,
		"migrateRelaySchemaV4Bootstrap":                     true,
		"migrateRelaySchemaV4Models":                        true,
		"migrateRelaySchemaV4SubscriptionPlan":              true,
		"migrateRelaySchemaV4PreviousCandidateCatalog":      true,
		"migrateRelaySchemaV4GenerationRouteReleaseBinding": true,
	}, 5)
}

func relaySchemaV6LiveSourceArtifact() ([]byte, error) {
	return relaySchemaLiveSourceArtifact([]string{
		"func:GetRelaySchemaContract",
		"func:relaySchemaMigrations",
		"func:RunRelaySchemaMigrations",
		"func:RequireRelaySchemaCompatible",
		"func:RequireRelaySchemaCurrent",
		"func:relaySchemaV6BootstrapSteps",
		"func:migrateRelaySchemaV6Bootstrap",
		"func:migrateRelaySchemaV6Models",
		"func:migrateRelaySchemaV6PreviousCandidateCatalog",
		"func:migrateRelaySchemaV6SubscriptionPlan",
		"func:migrateRelaySchemaV6ChannelTestLifecycle",
		"func:MigratePlatformChannelControlStorageV6WithDB",
		"func:installPlatformChannelControlLifecycleIndexesV6WithDB",
		"func:installPostgresPlatformChannelControlLifecycleConstraintsV6WithDB",
		"func:installPostgresPlatformChannelControlLifecycleGuardV6WithDB",
		"func:installSQLitePlatformChannelControlLifecycleGuardV6WithDB",
		"func:buildRelaySchemaExecutionPlan",
		"func:validateRelaySchemaRegistry",
		"func:ensureRelaySchemaMetadata",
		"func:installRelaySchemaLedgerGuards",
		"func:GetRelaySchemaStatus",
		"func:markRelaySchemaApplying",
		"func:markRelaySchemaFailed",
		"func:reconcileRelaySchemaCommitOutcome",
		"func:runRelaySchemaBootstrapTransaction",
		"func:runRelaySchemaDefinitionTransaction",
		"func:GetRelaySchemaCatalogFingerprint",
	}, map[string]bool{
		"RelaySchemaV1Checksum":                              true,
		"RelaySchemaV2Checksum":                              true,
		"RelaySchemaV3Checksum":                              true,
		"RelaySchemaV4Checksum":                              true,
		"RelaySchemaV5Checksum":                              true,
		"RelaySchemaV6Checksum":                              true,
		"relaySchemaV1CanonicalBytes":                        true,
		"relaySchemaV2CanonicalBytes":                        true,
		"relaySchemaV3CanonicalBytes":                        true,
		"relaySchemaV4CanonicalBytes":                        true,
		"relaySchemaV5CanonicalBytes":                        true,
		"relaySchemaV6CanonicalBytes":                        true,
		"relaySchemaV1SourceArtifactSHA256":                  true,
		"relaySchemaV1ModelArtifactSHA256":                   true,
		"relaySchemaV1FrozenChecksumSHA256":                  true,
		"relaySchemaV2SourceArtifactSHA256":                  true,
		"relaySchemaV2ModelArtifactSHA256":                   true,
		"relaySchemaV2FrozenChecksumSHA256":                  true,
		"relaySchemaV3SourceArtifactSHA256":                  true,
		"relaySchemaV3ModelArtifactSHA256":                   true,
		"relaySchemaV3FrozenChecksumSHA256":                  true,
		"relaySchemaV4SourceArtifactSHA256":                  true,
		"relaySchemaV4ModelArtifactSHA256":                   true,
		"relaySchemaV4FrozenChecksumSHA256":                  true,
		"relaySchemaV5SourceArtifactSHA256":                  true,
		"relaySchemaV5ModelArtifactSHA256":                   true,
		"relaySchemaV5FrozenChecksumSHA256":                  true,
		"relaySchemaV6SourceArtifactSHA256":                  true,
		"relaySchemaV6ModelArtifactSHA256":                   true,
		"relaySchemaV6FrozenChecksumSHA256":                  true,
		"relaySchemaV1LiveModelManifestBytes":                true,
		"relaySchemaV2LiveModelManifestBytes":                true,
		"relaySchemaV3LiveModelManifestBytes":                true,
		"relaySchemaV4LiveModelManifestBytes":                true,
		"relaySchemaV5LiveModelManifestBytes":                true,
		"relaySchemaV6LiveModelManifestBytes":                true,
		"relaySchemaV6Models":                                true,
		"relaySchemaV6ArtifactModels":                        true,
		"platformChannelControlV5GuardSQL":                   true,
		"platformChannelControlV6GuardSQL":                   true,
		"platformChannelControlV6SQLiteInsertGuardSQL":       true,
		"platformChannelControlV6SQLiteUpdateGuardSQL":       true,
		"relaySchemaV6PostgresCatalogSHA256":                 true,
		"relayRuntimeDatabasePrivilegeManifestV6Artifact":    true,
		"relayRuntimeDatabasePrivilegeManifestV6SHA256":      true,
		"relayDownloadEdgeDatabasePrivilegeManifestV6SHA256": true,
		"relayDownloadEdgeV6UpdateColumns":                   true,
	}, 6)
}

func relaySchemaV7LiveSourceArtifact() ([]byte, error) {
	return relaySchemaLiveSourceArtifact([]string{
		"func:GetRelaySchemaContract",
		"func:relaySchemaMigrations",
		"func:RunRelaySchemaMigrations",
		"func:RequireRelaySchemaCompatible",
		"func:RequireRelaySchemaCurrent",
		"func:relaySchemaV7BootstrapSteps",
		"func:migrateRelaySchemaV7Bootstrap",
		"func:migrateRelaySchemaV7Models",
		"func:migrateRelaySchemaV7PreviousCandidateCatalog",
		"func:migrateRelaySchemaV7SubscriptionPlan",
		"func:migrateRelaySchemaV7ChannelTestArtifactContentTypes",
		"func:MigratePlatformChannelCostPersonalScopeV7WithDB",
		"func:MigratePlatformChannelControlStorageV7WithDB",
		"func:installPostgresPlatformChannelControlArtifactEvidenceV7WithDB",
		"func:platformChannelControlPostgresGuardV7SQL",
		"func:installSQLitePlatformChannelControlArtifactEvidenceV7WithDB",
		"func:platformChannelControlSQLiteGuardsV7",
		"func:buildRelaySchemaExecutionPlan",
		"func:validateRelaySchemaRegistry",
		"func:ensureRelaySchemaMetadata",
		"func:installRelaySchemaLedgerGuards",
		"func:GetRelaySchemaStatus",
		"func:markRelaySchemaApplying",
		"func:markRelaySchemaFailed",
		"func:reconcileRelaySchemaCommitOutcome",
		"func:runRelaySchemaBootstrapTransaction",
		"func:runRelaySchemaDefinitionTransaction",
		"func:GetRelaySchemaCatalogFingerprint",
	}, map[string]bool{
		"RelaySchemaV1Checksum":                              true,
		"RelaySchemaV2Checksum":                              true,
		"RelaySchemaV3Checksum":                              true,
		"RelaySchemaV4Checksum":                              true,
		"RelaySchemaV5Checksum":                              true,
		"RelaySchemaV6Checksum":                              true,
		"RelaySchemaV7Checksum":                              true,
		"relaySchemaV1CanonicalBytes":                        true,
		"relaySchemaV2CanonicalBytes":                        true,
		"relaySchemaV3CanonicalBytes":                        true,
		"relaySchemaV4CanonicalBytes":                        true,
		"relaySchemaV5CanonicalBytes":                        true,
		"relaySchemaV6CanonicalBytes":                        true,
		"relaySchemaV7CanonicalBytes":                        true,
		"relaySchemaV1SourceArtifactSHA256":                  true,
		"relaySchemaV1ModelArtifactSHA256":                   true,
		"relaySchemaV1FrozenChecksumSHA256":                  true,
		"relaySchemaV2SourceArtifactSHA256":                  true,
		"relaySchemaV2ModelArtifactSHA256":                   true,
		"relaySchemaV2FrozenChecksumSHA256":                  true,
		"relaySchemaV3SourceArtifactSHA256":                  true,
		"relaySchemaV3ModelArtifactSHA256":                   true,
		"relaySchemaV3FrozenChecksumSHA256":                  true,
		"relaySchemaV4SourceArtifactSHA256":                  true,
		"relaySchemaV4ModelArtifactSHA256":                   true,
		"relaySchemaV4FrozenChecksumSHA256":                  true,
		"relaySchemaV5SourceArtifactSHA256":                  true,
		"relaySchemaV5ModelArtifactSHA256":                   true,
		"relaySchemaV5FrozenChecksumSHA256":                  true,
		"relaySchemaV6SourceArtifactSHA256":                  true,
		"relaySchemaV6ModelArtifactSHA256":                   true,
		"relaySchemaV6FrozenChecksumSHA256":                  true,
		"relaySchemaV7SourceArtifactSHA256":                  true,
		"relaySchemaV7ModelArtifactSHA256":                   true,
		"relaySchemaV7FrozenChecksumSHA256":                  true,
		"relaySchemaV1LiveModelManifestBytes":                true,
		"relaySchemaV2LiveModelManifestBytes":                true,
		"relaySchemaV3LiveModelManifestBytes":                true,
		"relaySchemaV4LiveModelManifestBytes":                true,
		"relaySchemaV5LiveModelManifestBytes":                true,
		"relaySchemaV6LiveModelManifestBytes":                true,
		"relaySchemaV7LiveModelManifestBytes":                true,
		"relaySchemaV7Models":                                true,
		"relaySchemaV7ArtifactModels":                        true,
		"platformChannelControlV5GuardSQL":                   true,
		"relaySchemaV7PostgresCatalogSHA256":                 true,
		"relayRuntimeDatabasePrivilegeManifestV7Artifact":    true,
		"relayRuntimeDatabasePrivilegeManifestV7SHA256":      true,
		"relayDownloadEdgeDatabasePrivilegeManifestV7SHA256": true,
		"relayDownloadEdgeV7UpdateColumns":                   true,
	}, 7)
}

func relaySchemaV8LiveSourceArtifact() ([]byte, error) {
	return relaySchemaLiveSourceArtifact([]string{
		"func:GetRelaySchemaContract",
		"func:relaySchemaMigrations",
		"func:RunRelaySchemaMigrations",
		"func:RequireRelaySchemaCompatible",
		"func:RequireRelaySchemaCurrent",
		"func:relaySchemaV8BootstrapSteps",
		"func:migrateRelaySchemaV8Bootstrap",
		"func:migrateRelaySchemaV8Models",
		"func:migrateRelaySchemaV8PreviousCandidateCatalog",
		"func:migrateRelaySchemaV8SubscriptionPlan",
		"func:migrateRelaySchemaV8ProviderCostAllocationEvidence",
		"func:MigratePlatformProviderCostEvidenceStorageV8WithDB",
		"func:installPlatformProviderCostEvidenceAppendOnlyGuardsV8",
		"func:buildRelaySchemaExecutionPlan",
		"func:validateRelaySchemaRegistry",
		"func:ensureRelaySchemaMetadata",
		"func:installRelaySchemaLedgerGuards",
		"func:GetRelaySchemaStatus",
		"func:markRelaySchemaApplying",
		"func:markRelaySchemaFailed",
		"func:reconcileRelaySchemaCommitOutcome",
		"func:runRelaySchemaBootstrapTransaction",
		"func:runRelaySchemaDefinitionTransaction",
		"func:GetRelaySchemaCatalogFingerprint",
	}, map[string]bool{
		"RelaySchemaV1Checksum":                              true,
		"RelaySchemaV2Checksum":                              true,
		"RelaySchemaV3Checksum":                              true,
		"RelaySchemaV4Checksum":                              true,
		"RelaySchemaV5Checksum":                              true,
		"RelaySchemaV6Checksum":                              true,
		"RelaySchemaV7Checksum":                              true,
		"RelaySchemaV8Checksum":                              true,
		"relaySchemaV1CanonicalBytes":                        true,
		"relaySchemaV2CanonicalBytes":                        true,
		"relaySchemaV3CanonicalBytes":                        true,
		"relaySchemaV4CanonicalBytes":                        true,
		"relaySchemaV5CanonicalBytes":                        true,
		"relaySchemaV6CanonicalBytes":                        true,
		"relaySchemaV7CanonicalBytes":                        true,
		"relaySchemaV8CanonicalBytes":                        true,
		"relaySchemaV1SourceArtifactSHA256":                  true,
		"relaySchemaV1ModelArtifactSHA256":                   true,
		"relaySchemaV1FrozenChecksumSHA256":                  true,
		"relaySchemaV2SourceArtifactSHA256":                  true,
		"relaySchemaV2ModelArtifactSHA256":                   true,
		"relaySchemaV2FrozenChecksumSHA256":                  true,
		"relaySchemaV3SourceArtifactSHA256":                  true,
		"relaySchemaV3ModelArtifactSHA256":                   true,
		"relaySchemaV3FrozenChecksumSHA256":                  true,
		"relaySchemaV4SourceArtifactSHA256":                  true,
		"relaySchemaV4ModelArtifactSHA256":                   true,
		"relaySchemaV4FrozenChecksumSHA256":                  true,
		"relaySchemaV5SourceArtifactSHA256":                  true,
		"relaySchemaV5ModelArtifactSHA256":                   true,
		"relaySchemaV5FrozenChecksumSHA256":                  true,
		"relaySchemaV6SourceArtifactSHA256":                  true,
		"relaySchemaV6ModelArtifactSHA256":                   true,
		"relaySchemaV6FrozenChecksumSHA256":                  true,
		"relaySchemaV7SourceArtifactSHA256":                  true,
		"relaySchemaV7ModelArtifactSHA256":                   true,
		"relaySchemaV7FrozenChecksumSHA256":                  true,
		"relaySchemaV8SourceArtifactSHA256":                  true,
		"relaySchemaV8ModelArtifactSHA256":                   true,
		"relaySchemaV8FrozenChecksumSHA256":                  true,
		"relaySchemaV1LiveModelManifestBytes":                true,
		"relaySchemaV2LiveModelManifestBytes":                true,
		"relaySchemaV3LiveModelManifestBytes":                true,
		"relaySchemaV4LiveModelManifestBytes":                true,
		"relaySchemaV5LiveModelManifestBytes":                true,
		"relaySchemaV6LiveModelManifestBytes":                true,
		"relaySchemaV7LiveModelManifestBytes":                true,
		"relaySchemaV8LiveModelManifestBytes":                true,
		"relaySchemaV8Models":                                true,
		"relaySchemaV8ArtifactModels":                        true,
		"relaySchemaV8PostgresCatalogSHA256":                 true,
		"relayRuntimeDatabasePrivilegeManifestV8Artifact":    true,
		"relayRuntimeDatabasePrivilegeManifestV8SHA256":      true,
		"relayDownloadEdgeDatabasePrivilegeManifestV8SHA256": true,
		"relayDownloadEdgeV8UpdateColumns":                   true,
	}, 8)
}

func relaySchemaLiveSourceArtifact(queue []string, identityNames map[string]bool, version int64) ([]byte, error) {
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		return nil, fmt.Errorf("schema artifact source directory is unavailable")
	}
	directory := filepath.Dir(currentFile)
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, err
	}

	type declaration struct {
		key  string
		node ast.Node
	}
	declarations := make(map[string]declaration)
	packageFunctionsByName := make(map[string][]string)
	allFunctionsByName := make(map[string][]string)
	valuesByName := make(map[string]string)
	typesByName := make(map[string]string)
	fset := token.NewFileSet()
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".go") || strings.HasSuffix(entry.Name(), "_test.go") {
			continue
		}
		parsed, parseErr := parser.ParseFile(fset, filepath.Join(directory, entry.Name()), nil, parser.SkipObjectResolution)
		if parseErr != nil {
			return nil, parseErr
		}
		for _, candidate := range parsed.Decls {
			switch typed := candidate.(type) {
			case *ast.FuncDecl:
				key := "func:" + typed.Name.Name
				if typed.Recv != nil {
					key = "method:" + relaySchemaArtifactReceiverName(typed.Recv) + "." + typed.Name.Name
				} else {
					packageFunctionsByName[typed.Name.Name] = append(packageFunctionsByName[typed.Name.Name], key)
				}
				declarations[key] = declaration{key: key, node: typed}
				allFunctionsByName[typed.Name.Name] = append(allFunctionsByName[typed.Name.Name], key)
			case *ast.GenDecl:
				if typed.Tok != token.CONST && typed.Tok != token.VAR && typed.Tok != token.TYPE {
					continue
				}
				declarationNames := make([]string, 0)
				for _, spec := range typed.Specs {
					switch value := spec.(type) {
					case *ast.ValueSpec:
						for _, name := range value.Names {
							declarationNames = append(declarationNames, name.Name)
						}
					case *ast.TypeSpec:
						declarationNames = append(declarationNames, value.Name.Name)
					}
				}
				sort.Strings(declarationNames)
				key := fmt.Sprintf("declaration:%s:%s", typed.Tok.String(), strings.Join(declarationNames, ","))
				declarations[key] = declaration{key: key, node: typed}
				for _, spec := range typed.Specs {
					switch value := spec.(type) {
					case *ast.ValueSpec:
						for _, name := range value.Names {
							valuesByName[name.Name] = key
						}
					case *ast.TypeSpec:
						typesByName[value.Name.Name] = key
					}
				}
			}
		}
	}

	selected := make(map[string]declaration)
	for len(queue) > 0 {
		key := queue[0]
		queue = queue[1:]
		if _, exists := selected[key]; exists {
			continue
		}
		decl, exists := declarations[key]
		if !exists {
			return nil, fmt.Errorf("schema v%d artifact declaration %s is missing", version, key)
		}
		selected[key] = decl
		selectorIdentifiers := make(map[*ast.Ident]bool)
		ast.Inspect(decl.node, func(node ast.Node) bool {
			if selector, selectorOK := node.(*ast.SelectorExpr); selectorOK {
				selectorIdentifiers[selector.Sel] = true
			}
			return true
		})
		ast.Inspect(decl.node, func(node ast.Node) bool {
			switch typed := node.(type) {
			case *ast.Ident:
				if selectorIdentifiers[typed] || identityNames[typed.Name] {
					return true
				}
				if valueKey, found := valuesByName[typed.Name]; found {
					queue = append(queue, valueKey)
				}
				if typeKey, found := typesByName[typed.Name]; found {
					queue = append(queue, typeKey)
				}
				queue = append(queue, packageFunctionsByName[typed.Name]...)
			case *ast.CallExpr:
				if selector, selectorOK := typed.Fun.(*ast.SelectorExpr); selectorOK {
					candidates := allFunctionsByName[selector.Sel.Name]
					if len(candidates) == 1 {
						queue = append(queue, candidates[0])
					}
				}
			}
			return true
		})
	}

	keys := make([]string, 0, len(selected))
	for key := range selected {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var artifact bytes.Buffer
	if err := appendRelaySchemaExternalDependencyArtifact(&artifact, fset, directory); err != nil {
		return nil, err
	}
	for _, key := range keys {
		artifact.WriteString(key)
		artifact.WriteByte('\n')
		if err := format.Node(&artifact, fset, selected[key].node); err != nil {
			return nil, err
		}
		artifact.WriteByte('\n')
	}
	return artifact.Bytes(), nil
}

// relaySchemaV1LiveSourceArtifact follows package-local function/value
// references from the migration registry and transaction/ledger entrypoints.
// It deliberately hashes formatted declarations rather than developer-written
// labels, so changing a transform, guard SQL body, or nested migration helper
// requires a new schema version.
func relaySchemaV1LiveSourceArtifact() ([]byte, error) {
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		return nil, fmt.Errorf("schema artifact source directory is unavailable")
	}
	directory := filepath.Dir(currentFile)
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, err
	}

	type declaration struct {
		key  string
		node ast.Node
	}
	declarations := make(map[string]declaration)
	functionsByName := make(map[string][]string)
	valuesByName := make(map[string]string)
	typesByName := make(map[string]string)
	fset := token.NewFileSet()
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".go") || strings.HasSuffix(entry.Name(), "_test.go") {
			continue
		}
		parsed, parseErr := parser.ParseFile(fset, filepath.Join(directory, entry.Name()), nil, parser.SkipObjectResolution)
		if parseErr != nil {
			return nil, parseErr
		}
		for _, candidate := range parsed.Decls {
			switch typed := candidate.(type) {
			case *ast.FuncDecl:
				key := "func:" + typed.Name.Name
				if typed.Recv != nil {
					key = "method:" + relaySchemaArtifactReceiverName(typed.Recv) + "." + typed.Name.Name
				}
				declarations[key] = declaration{key: key, node: typed}
				functionsByName[typed.Name.Name] = append(functionsByName[typed.Name.Name], key)
			case *ast.GenDecl:
				if typed.Tok != token.CONST && typed.Tok != token.VAR && typed.Tok != token.TYPE {
					continue
				}
				declarationNames := make([]string, 0)
				for _, spec := range typed.Specs {
					switch value := spec.(type) {
					case *ast.ValueSpec:
						for _, name := range value.Names {
							declarationNames = append(declarationNames, name.Name)
						}
					case *ast.TypeSpec:
						declarationNames = append(declarationNames, value.Name.Name)
					}
				}
				sort.Strings(declarationNames)
				key := fmt.Sprintf("declaration:%s:%s", typed.Tok.String(), strings.Join(declarationNames, ","))
				declarations[key] = declaration{key: key, node: typed}
				for _, spec := range typed.Specs {
					switch value := spec.(type) {
					case *ast.ValueSpec:
						for _, name := range value.Names {
							valuesByName[name.Name] = key
						}
					case *ast.TypeSpec:
						typesByName[value.Name.Name] = key
					}
				}
			}
		}
	}

	queue := []string{
		"func:relaySchemaMigrations",
		"func:relaySchemaV1Steps",
		"func:migrateRelaySchemaV1",
		"func:relaySchemaV1Models",
		"func:buildRelaySchemaExecutionPlan",
		"func:validateRelaySchemaRegistry",
		"func:ensureRelaySchemaMetadata",
		"func:installRelaySchemaLedgerGuards",
		"func:markRelaySchemaApplying",
		"func:markRelaySchemaFailed",
		"func:reconcileRelaySchemaCommitOutcome",
		"func:runRelaySchemaBootstrapTransaction",
		"func:runRelaySchemaDefinitionTransaction",
		"func:GetRelaySchemaCatalogFingerprint",
	}
	selected := make(map[string]declaration)
	identityNames := map[string]bool{
		"RelaySchemaV1Checksum":               true,
		"relaySchemaV1CanonicalBytes":         true,
		"relaySchemaV1SourceArtifactSHA256":   true,
		"relaySchemaV1ModelArtifactSHA256":    true,
		"relaySchemaV1FrozenChecksumSHA256":   true,
		"relaySchemaV1LiveModelManifestBytes": true,
	}
	for len(queue) > 0 {
		key := queue[0]
		queue = queue[1:]
		if _, exists := selected[key]; exists {
			continue
		}
		decl, exists := declarations[key]
		if !exists {
			return nil, fmt.Errorf("schema artifact declaration %s is missing", key)
		}
		selected[key] = decl
		ast.Inspect(decl.node, func(node ast.Node) bool {
			switch typed := node.(type) {
			case *ast.Ident:
				if identityNames[typed.Name] {
					return true
				}
				if valueKey, found := valuesByName[typed.Name]; found {
					queue = append(queue, valueKey)
				}
				if typeKey, found := typesByName[typed.Name]; found {
					queue = append(queue, typeKey)
				}
				// Function values in the step registry are identifiers rather than
				// CallExpr nodes. Following every package-local function identifier
				// makes the executable Up bodies part of the frozen artifact.
				queue = append(queue, functionsByName[typed.Name]...)
			case *ast.CallExpr:
				switch function := typed.Fun.(type) {
				case *ast.Ident:
					if !identityNames[function.Name] {
						queue = append(queue, functionsByName[function.Name]...)
					}
				case *ast.SelectorExpr:
					queue = append(queue, functionsByName[function.Sel.Name]...)
				}
			}
			return true
		})
	}

	keys := make([]string, 0, len(selected))
	for key := range selected {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var artifact bytes.Buffer
	if err := appendRelaySchemaExternalDependencyArtifact(&artifact, fset, directory); err != nil {
		return nil, err
	}
	for _, key := range keys {
		artifact.WriteString(key)
		artifact.WriteByte('\n')
		if err := format.Node(&artifact, fset, selected[key].node); err != nil {
			return nil, err
		}
		artifact.WriteByte('\n')
	}
	return artifact.Bytes(), nil
}

func relaySchemaArtifactReceiverName(receiver *ast.FieldList) string {
	if receiver == nil || len(receiver.List) != 1 {
		return "invalid"
	}
	expression := receiver.List[0].Type
	if pointer, ok := expression.(*ast.StarExpr); ok {
		expression = pointer.X
	}
	if identifier, ok := expression.(*ast.Ident); ok {
		return identifier.Name
	}
	return "unknown"
}

func appendRelaySchemaExternalDependencyArtifact(artifact *bytes.Buffer, fset *token.FileSet, modelDirectory string) error {
	repositoryRoot := filepath.Dir(modelDirectory)
	for _, dependencyManifest := range []string{"go.mod", "go.sum"} {
		contents, err := os.ReadFile(filepath.Join(repositoryRoot, dependencyManifest))
		if err != nil {
			return err
		}
		artifact.WriteString("module-manifest|")
		artifact.WriteString(dependencyManifest)
		artifact.WriteByte('\n')
		artifact.WriteString(strings.ReplaceAll(string(contents), "\r\n", "\n"))
		artifact.WriteByte('\n')
	}

	dependencyFiles := []string{
		"common/constants.go",
		"common/env.go",
		"common/hash.go",
		"common/json.go",
		"common/session_cookie.go",
		"common/str.go",
		"setting/console_setting/validation.go",
	}
	for _, relativePath := range dependencyFiles {
		absolutePath := filepath.Join(repositoryRoot, filepath.FromSlash(relativePath))
		parsed, parseErr := parser.ParseFile(fset, absolutePath, nil, parser.SkipObjectResolution)
		if parseErr != nil {
			return parseErr
		}
		artifact.WriteString("dependency|")
		artifact.WriteString(relativePath)
		artifact.WriteByte('\n')
		if err := format.Node(artifact, fset, parsed); err != nil {
			return err
		}
		artifact.WriteByte('\n')
	}
	return nil
}
