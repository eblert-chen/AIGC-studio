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
		if isFunction && function.Name.Name == "TestRelaySchemaV2ArtifactsAreFrozen" {
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

// relaySchemaV2LiveSourceArtifact uses a corrected declaration boundary: it
// follows package functions referenced as identifiers, and follows a selector
// method only when that method name resolves to one package-local declaration.
// It therefore captures actual nested migration helpers without pulling every
// unrelated Update/Delete/Scan method in the model package into the release.
func relaySchemaV2LiveSourceArtifact() ([]byte, error) {
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

	queue := []string{
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
	}
	selected := make(map[string]declaration)
	identityNames := map[string]bool{
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
	}
	for len(queue) > 0 {
		key := queue[0]
		queue = queue[1:]
		if _, exists := selected[key]; exists {
			continue
		}
		decl, exists := declarations[key]
		if !exists {
			return nil, fmt.Errorf("schema v2 artifact declaration %s is missing", key)
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
