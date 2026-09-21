package main

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func onboardingRouteFixture(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "signed-routes.json")
	require.NoError(t, os.WriteFile(path, []byte(`{"model":[]}`), 0o600))
	return path
}

func TestRunFailsBeforeDatabaseWhenProtectedCredentialIsMissingOrAmbiguous(t *testing.T) {
	original := readProviderOnboardingCredential
	t.Cleanup(func() { readProviderOnboardingCredential = original })
	routes := onboardingRouteFixture(t)

	readProviderOnboardingCredential = func(string, int64) ([]byte, error) {
		return nil, errors.New("fixture missing")
	}
	err := run([]string{"--environment", "staging", "--signed-routes", routes}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "credential is unavailable")

	secret := "must-never-appear-in-errors"
	readProviderOnboardingCredential = func(string, int64) ([]byte, error) {
		return []byte(`{"schema_version":1,"api_key":"` + secret + `","api_key":"duplicate"}`), nil
	}
	err = run([]string{"--environment", "staging", "--signed-routes", routes}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "ambiguous")
	assert.NotContains(t, err.Error(), secret)
}

func TestStageDoesNotRequireOrAcceptSignedRoutes(t *testing.T) {
	original := readProviderOnboardingCredential
	t.Cleanup(func() { readProviderOnboardingCredential = original })
	readProviderOnboardingCredential = func(string, int64) ([]byte, error) {
		return nil, errors.New("fixture missing")
	}

	err := run([]string{"--environment", "staging", "--phase", "stage"}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "credential is unavailable")
	assert.NotContains(t, err.Error(), "signed route inventory")
	err = run([]string{"--environment", "development", "--phase", "stage"}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "credential is unavailable")
	assert.NotContains(t, err.Error(), "signed route inventory")

	err = run([]string{"--environment", "staging", "--phase", "stage", "--signed-routes", onboardingRouteFixture(t)}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "not accepted during credential check or stage")
}

func TestRunRejectsUnknownPhaseBeforeReadingSecrets(t *testing.T) {
	original := readProviderOnboardingCredential
	t.Cleanup(func() { readProviderOnboardingCredential = original })
	readProviderOnboardingCredential = func(string, int64) ([]byte, error) {
		assert.Fail(t, "credential reader must not run")
		return nil, errors.New("unexpected")
	}
	err := run([]string{"--environment", "staging", "--phase", "publish"}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "--phase must be check-credential, stage, plan-routes, or finalize")
}

func TestRunRejectsUnknownManagedProviderBeforeDatabase(t *testing.T) {
	t.Setenv(providerOnboardingCredentialEnvironment, "")
	err := run([]string{
		"--environment", "development", "--phase", "stage",
		"--managed-provider", "invented-provider",
	}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "--managed-provider must be")
}

func TestRunRejectsManagedAndLegacyCredentialSourcesTogether(t *testing.T) {
	t.Setenv(providerOnboardingCredentialEnvironment, onboardingRouteFixture(t))
	err := run([]string{
		"--environment", "development", "--phase", "stage",
		"--managed-provider", "minimax",
	}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "mutually exclusive")
}

func TestPlanRoutesRequiresDevelopmentAndExplicitInventoryPolicyBeforeReadingSecrets(t *testing.T) {
	original := readProviderOnboardingCredential
	t.Cleanup(func() { readProviderOnboardingCredential = original })
	readProviderOnboardingCredential = func(string, int64) ([]byte, error) {
		assert.Fail(t, "credential reader must not run")
		return nil, errors.New("unexpected")
	}

	err := run([]string{"--environment", "staging", "--phase", "plan-routes", "--replace-all-routes"}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "restricted to development")

	err = run([]string{"--environment", "development", "--phase", "plan-routes"}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "exactly one")

	err = run([]string{
		"--environment", "development", "--phase", "plan-routes",
		"--current-routes", onboardingRouteFixture(t), "--replace-all-routes",
	}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "exactly one")
}

func TestProtectedFinalizeRejectsFullReplacementBeforeReadingSecrets(t *testing.T) {
	original := readProviderOnboardingCredential
	t.Cleanup(func() { readProviderOnboardingCredential = original })
	readProviderOnboardingCredential = func(string, int64) ([]byte, error) {
		assert.Fail(t, "credential reader must not run")
		return nil, errors.New("unexpected")
	}

	err := run([]string{
		"--environment", "production", "--phase", "finalize",
		"--signed-routes", onboardingRouteFixture(t), "--replace-all-routes",
	}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "restricted to development")
}

func TestRunRejectsCredentialUnknownFieldsBeforeDatabase(t *testing.T) {
	original := readProviderOnboardingCredential
	t.Cleanup(func() { readProviderOnboardingCredential = original })
	secret := "must-never-appear-in-errors"
	readProviderOnboardingCredential = func(string, int64) ([]byte, error) {
		return []byte(`{"schema_version":1,"provider":"minimax","region":"cn","account_id":"account","channel_id":35,"api_key":"` + secret + `","public_model_ids":["minimax-h3"],"enable_native_ability":true}`), nil
	}
	err := run([]string{"--environment", "staging", "--signed-routes", onboardingRouteFixture(t)}, &bytes.Buffer{}, &bytes.Buffer{})
	require.ErrorContains(t, err, "JSON is invalid")
	assert.NotContains(t, err.Error(), secret)
}

func TestReadStableOnboardingFileRejectsRelativeAndSymlinkPaths(t *testing.T) {
	_, err := readStableOnboardingFile("relative.json", 1024)
	require.ErrorContains(t, err, "path is invalid")

	target := onboardingRouteFixture(t)
	link := filepath.Join(t.TempDir(), "routes-link.json")
	if err := os.Symlink(target, link); err == nil {
		_, err = readStableOnboardingFile(link, 1024)
		require.ErrorContains(t, err, "unavailable or invalid")
	}
}
