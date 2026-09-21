package model

import (
	"go/ast"
	"go/parser"
	"go/token"
	"testing"

	"github.com/stretchr/testify/require"
)

func relayRuntimeProofFunctionCallCounts(t *testing.T, functionName string) map[string]int {
	t.Helper()
	file, err := parser.ParseFile(token.NewFileSet(), "runtime_database_role_proof.go", nil, 0)
	require.NoError(t, err)
	counts := make(map[string]int)
	for _, declaration := range file.Decls {
		function, ok := declaration.(*ast.FuncDecl)
		if !ok || function.Name.Name != functionName || function.Body == nil {
			continue
		}
		ast.Inspect(function.Body, func(node ast.Node) bool {
			call, ok := node.(*ast.CallExpr)
			if !ok {
				return true
			}
			switch target := call.Fun.(type) {
			case *ast.Ident:
				counts[target.Name]++
			case *ast.SelectorExpr:
				counts[target.Sel.Name]++
			}
			return true
		})
		return counts
	}
	t.Fatalf("function %s was not found", functionName)
	return nil
}

func TestRelayRuntimeDatabaseRoleProofCallGraphSeparatesFullAndLiveSurfaces(t *testing.T) {
	full := relayRuntimeProofFunctionCallCounts(t, "AttestRelayRuntimeDatabaseRoleWithContext")
	live := relayRuntimeProofFunctionCallCounts(t, "VerifyRelayRuntimeDatabaseRoleProof")
	require.Equal(t, 1, full["RequireRelaySchemaCurrent"], "startup/refresh must fingerprint the full catalog exactly once")
	require.Equal(t, 1, full["verifyRelayProtectedDatabaseSurfacePreflight"])
	require.Equal(t, 1, full["verifyRelayDatabaseRoleTopologyAfterExactSurface"])
	require.Zero(t, live["RequireRelaySchemaCurrent"], "the serving probe must never fingerprint the full catalog")
	require.Zero(t, live["verifyRelayProtectedDatabaseSurfacePreflight"])
	require.Equal(t, 1, live["verifyRelayDownloadEdgeSchemaReleaseProof"])
	require.Equal(t, 1, live["verifyRelayDatabaseRoleLiveTopology"], "runtime and edge must share one live topology query")
	require.Equal(t, 1, live["verifyRelayRuntimeDatabasePrivilegeManifestOptimized"])
	require.Equal(t, 1, live["verifyRelayDownloadEdgeCurrentDatabaseRoleOptimizedAfterTopology"])
}
