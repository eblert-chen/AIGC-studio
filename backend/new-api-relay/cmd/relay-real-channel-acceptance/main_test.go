package main

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
)

func validTestConfig(output string) acceptanceConfig {
	return acceptanceConfig{
		SchemaVersion: configSchemaVersion,
		Environment:   "relay-real-channel-staging",
		Mode:          "text_to_video",
		Platform: platformConfig{
			BaseURL:                                   "https://platform.staging.example",
			DownloadGatewayBaseURL:                    "https://downloads.staging.example",
			CompanyID:                                 "11111111-1111-4111-8111-111111111111",
			UserID:                                    "22222222-2222-4222-8222-222222222222",
			BearerTokenEnv:                            "TEST_PLATFORM_BEARER",
			DatabaseDSNEnv:                            "TEST_PLATFORM_DSN",
			ExternalDownloadCompletionID:              "44444444-4444-4444-8444-444444444444",
			ExternalDownloadCompletionSource:          "edge_gateway",
			ExternalDownloadProofPath:                 "external-download-proof.json",
			ExternalDownloadProofExpectedSHA256:       "sha256:" + strings.Repeat("d", 64),
			ExternalDownloadProofSignaturePath:        "external-download-proof.signature.json",
			ExternalDownloadProofPublicKeyEnv:         "TEST_EDGE_PROOF_PUBLIC_KEY",
			ExternalDownloadProofPublicKeyFingerprint: "sha256:" + strings.Repeat("e", 64),
			Task: taskConfig{
				ModelID:                   "33333333-3333-4333-8333-333333333333",
				ExpectedCapabilityVersion: 1,
				RequestPayload: map[string]any{
					"mode":             "text_to_video",
					"prompt":           "staging acceptance",
					"duration_seconds": 5,
					"aspect_ratio":     "16:9",
					"resolution":       "720p",
					"output_count":     1,
				},
			},
		},
		Provider: providerConfig{
			Name:                          "kling",
			AccessKeyEnv:                  "TEST_KLING_ACCESS",
			SecretKeyEnv:                  "TEST_KLING_SECRET",
			ApprovalPayloadPath:           "provider-bill-approval.json",
			ApprovalPayloadExpectedSHA256: "sha256:" + strings.Repeat("a", 64),
			ApprovalSignaturePath:         "provider-bill-approval.signature.json",
			ApprovalPublicKeyEnv:          "TEST_BILL_APPROVAL_PUBLIC_KEY",
			ApprovalPublicKeyFingerprint:  "sha256:" + strings.Repeat("b", 64),
		},
		Storage: storageConfig{
			EndpointEnv:  "TEST_OBS_ENDPOINT",
			BucketEnv:    "TEST_OBS_BUCKET",
			AccessKeyEnv: "TEST_OBS_ACCESS",
			SecretKeyEnv: "TEST_OBS_SECRET",
		},
		Runtime: runtimeConfig{
			RelayDatabaseDSNEnv:            "TEST_RELAY_DSN",
			RelayBaseURL:                   "https://relay.staging.example",
			RelayRuntimeIdentityTokenEnv:   "TEST_RELAY_RUNTIME_IDENTITY_TOKEN",
			ExpectedUpstreamGitRevision:    "0ab02020603d22e5613bc4cf46bfab06f8567769",
			ExpectedSourceGitRevision:      strings.Repeat("9", 40),
			ExpectedSourceSnapshotSHA256:   "sha256:" + strings.Repeat("8", 64),
			ExpectedSourceSnapshotFiles:    321,
			ExpectedImageDigest:            "sha256:" + strings.Repeat("7", 64),
			OutputDirectory:                output,
			CreateCheckpointPath:           "create-checkpoint.json",
			CreateCheckpointExpectedSHA256: "sha256:" + strings.Repeat("c", 64),
			PollIntervalSeconds:            1,
			TimeoutSeconds:                 30,
		},
	}
}

func TestValidateConfigFailsClosedForIdentityHeadersInRealGate(t *testing.T) {
	config := validTestConfig(t.TempDir())
	config.Platform.BearerTokenEnv = ""
	config.Platform.UseNonProductionIdentityHeads = true
	r := &runner{config: config, phase: "create"}
	if err := r.validateConfig(); err == nil {
		t.Fatal("production identity headers must be rejected")
	}
}

func TestRuntimeBuildComparisonAllowsAnotherInstanceOfTheSameImage(t *testing.T) {
	left := runtimeBuildIdentity{
		InstanceID: uuid.NewString(), UpstreamGitRevision: "upstream", SourceGitRevision: "source",
		SourceSnapshotSHA256: "sha256:snapshot", SourceSnapshotFiles: 123, ImageDigest: "sha256:image",
	}
	right := left
	right.InstanceID = uuid.NewString()
	if !sameRuntimeBuild(left, right) {
		t.Fatal("two processes of the same frozen build must compare equal")
	}
	right.SourceSnapshotSHA256 = "sha256:old-snapshot"
	if sameRuntimeBuild(left, right) {
		t.Fatal("a different source snapshot must fail closed")
	}
}

func TestPreflightReportIsCreateOnlyAndSecretFree(t *testing.T) {
	output := t.TempDir()
	config := validTestConfig(output)
	values := map[string]string{
		"TEST_PLATFORM_BEARER":              "browser-token-that-must-not-appear",
		"TEST_PLATFORM_DSN":                 "postgresql://user:platform-password@db/platform",
		"TEST_KLING_ACCESS":                 "kling-access-key",
		"TEST_KLING_SECRET":                 "kling-secret-key-that-must-not-appear",
		"TEST_OBS_ENDPOINT":                 "https://obs.cn-north-4.myhuaweicloud.com",
		"TEST_OBS_BUCKET":                   "acceptance-private-bucket",
		"TEST_OBS_ACCESS":                   "obs-access-key",
		"TEST_OBS_SECRET":                   "obs-secret-key-that-must-not-appear",
		"TEST_RELAY_DSN":                    "postgresql://user:relay-password@db/relay",
		"TEST_RELAY_RUNTIME_IDENTITY_TOKEN": "runtime-identity-token-that-must-not-appear-01",
	}
	for name, value := range values {
		t.Setenv(name, value)
	}
	r := &runner{
		config:     config,
		phase:      "preflight",
		configDir:  t.TempDir(),
		runID:      uuid.NewString(),
		checks:     map[string]bool{},
		httpClient: &httpClientForTests,
	}
	if status := r.run(context.Background()); status != "PASS" {
		t.Fatalf("preflight status = %s", status)
	}
	contents, err := os.ReadFile(filepath.Join(r.outputDir, "report.json"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(contents)
	for _, secret := range values {
		if strings.Contains(text, secret) {
			t.Fatalf("report leaked a configured value")
		}
	}
	if err := writeCreateOnly(filepath.Join(r.outputDir, "report.json"), []byte("replace")); err == nil {
		t.Fatal("create-only report was overwritten")
	}
}

func TestPreflightWithoutExternalCredentialsIsBlockedAndNamesOnlyMissingVariables(t *testing.T) {
	output := t.TempDir()
	config := validTestConfig(output)
	for _, name := range []string{
		"TEST_PLATFORM_BEARER",
		"TEST_PLATFORM_DSN",
		"TEST_KLING_ACCESS",
		"TEST_KLING_SECRET",
		"TEST_OBS_ENDPOINT",
		"TEST_OBS_BUCKET",
		"TEST_OBS_ACCESS",
		"TEST_OBS_SECRET",
		"TEST_RELAY_DSN",
		"TEST_RELAY_RUNTIME_IDENTITY_TOKEN",
	} {
		t.Setenv(name, "")
	}
	r := &runner{
		config:     config,
		phase:      "preflight",
		configDir:  t.TempDir(),
		runID:      uuid.NewString(),
		checks:     map[string]bool{},
		httpClient: newAcceptanceHTTPClient(),
	}
	if status := r.run(context.Background()); status != "BLOCKED" {
		t.Fatalf("missing-credential preflight status = %s", status)
	}
	contents, err := os.ReadFile(filepath.Join(r.outputDir, "report.json"))
	if err != nil {
		t.Fatal(err)
	}
	var report runReport
	if err := json.Unmarshal(contents, &report); err != nil {
		t.Fatal(err)
	}
	if report.Status != "BLOCKED" || report.Reason != "required_staging_credentials_or_connections_missing" {
		t.Fatalf("unexpected fail-closed report: %#v", report)
	}
	for _, required := range []string{"TEST_KLING_ACCESS", "TEST_KLING_SECRET", "TEST_OBS_ENDPOINT", "TEST_OBS_BUCKET", "TEST_OBS_ACCESS", "TEST_OBS_SECRET"} {
		if !containsString(report.Missing, required) {
			t.Fatalf("missing variable %s was not reported", required)
		}
	}
	text := string(contents)
	for _, forbidden := range []string{"X-Amz-Signature", "X-Obs-Signature", "AccessKeyId=", "postgresql://"} {
		if strings.Contains(text, forbidden) {
			t.Fatalf("blocked report contains forbidden credential or signed URL material")
		}
	}
}

func TestCompleteArtifactHTTPResponseRejectsPartialOrMismatchedTransfers(t *testing.T) {
	for _, item := range []struct {
		name          string
		status        int
		contentRange  string
		contentLength int64
		expectedSize  int64
		accepted      bool
	}{
		{name: "complete 200", status: http.StatusOK, contentLength: 4096, expectedSize: 4096, accepted: true},
		{name: "chunked complete 200", status: http.StatusOK, contentLength: -1, expectedSize: 4096, accepted: true},
		{name: "partial 206", status: http.StatusPartialContent, contentLength: 4096, expectedSize: 4096},
		{name: "content range on 200", status: http.StatusOK, contentRange: "bytes 0-4095/8192", contentLength: 4096, expectedSize: 4096},
		{name: "wrong content length", status: http.StatusOK, contentLength: 1024, expectedSize: 4096},
	} {
		t.Run(item.name, func(t *testing.T) {
			response := &http.Response{
				StatusCode:    item.status,
				ContentLength: item.contentLength,
				Header:        make(http.Header),
			}
			if item.contentRange != "" {
				response.Header.Set("Content-Range", item.contentRange)
			}
			if got := isCompleteArtifactHTTPResponse(response, item.expectedSize); got != item.accepted {
				t.Fatalf("accepted = %v, want %v", got, item.accepted)
			}
		})
	}
}

func TestAcceptanceHTTPClientRejectsRedirects(t *testing.T) {
	client := newAcceptanceHTTPClient()
	request, err := http.NewRequest(http.MethodGet, "https://redirect.example", nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := client.CheckRedirect(request, nil); err != http.ErrUseLastResponse {
		t.Fatalf("redirect policy returned %v", err)
	}
}

func TestAnonymousOBSProbeRequiresExplicitAccessDenial(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden, http.StatusNotFound} {
		if !anonymousOBSAccessDenied(status) {
			t.Fatalf("explicit denial %d was rejected", status)
		}
	}
	for _, status := range []int{http.StatusOK, http.StatusFound, http.StatusInternalServerError, http.StatusServiceUnavailable} {
		if anonymousOBSAccessDenied(status) {
			t.Fatalf("non-denial status %d was treated as private", status)
		}
	}
}

func TestDownloadGatewayTicketURLIsOpaqueOriginBoundAndQueryFree(t *testing.T) {
	token := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("t", 32)))
	base := "https://downloads.staging.example"
	valid := base + "/downloads/" + token
	metadata, err := validateDownloadGatewayTicketURL(valid, base)
	if err != nil {
		t.Fatalf("valid Gateway ticket was rejected: %v", err)
	}
	if metadata.EndpointHost != "downloads.staging.example" || metadata.PathSHA256 != sha256Hex([]byte("/downloads/"+token)) {
		t.Fatal("Gateway ticket metadata was not canonical")
	}
	for _, raw := range []string{
		"http://downloads.staging.example/downloads/" + token,
		"https://downloads.staging.example.evil.invalid/downloads/" + token,
		"https://user@downloads.staging.example/downloads/" + token,
		"https://downloads.staging.example:444/downloads/" + token,
		valid + "?ticket=secret",
		valid + "#fragment",
		base + "/downloads/" + token + "/extra",
		base + "/downloads/" + token + "%2fextra",
		base + "/downloads/" + token + "=",
		base + "/downloads/short",
	} {
		if _, err := validateDownloadGatewayTicketURL(raw, base); err == nil {
			t.Fatalf("unsafe Gateway ticket was accepted: %s", raw)
		}
	}
	for _, rawBase := range []string{"", "http://downloads.staging.example", "https://localhost", "https://127.0.0.1", "https://downloads.staging.example/prefix", "https://downloads.staging.example?x=1"} {
		if err := validateDownloadGatewayBaseURL(rawBase); err == nil {
			t.Fatalf("unsafe Gateway base URL was accepted: %s", rawBase)
		}
	}
}

func TestDownloadRecordBindingRejectsCrossObjectTicketAndIdentityDrift(t *testing.T) {
	now := time.Date(2028, time.January, 2, 3, 4, 5, 0, time.UTC)
	token := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("g", 32)))
	ticketURL := "https://downloads.staging.example/downloads/" + token
	companyID := "11111111-1111-4111-8111-111111111111"
	userID := "22222222-2222-4222-8222-222222222222"
	taskID := "33333333-3333-4333-8333-333333333333"
	assetID := "asset-acceptance-1"
	objectKey := "outputs/33333333-3333-4333-8333-333333333333/44444444-4444-4444-8444-444444444444/55555555-5555-4555-8555-555555555555"
	gatewayIssuedAt := now.Add(-5 * time.Second)
	gatewayExpiresAt := gatewayIssuedAt.Add(5 * time.Minute)
	record := downloadRecordBinding{
		ID:                           "66666666-6666-4666-8666-666666666666",
		CompanyID:                    companyID,
		TaskID:                       taskID,
		AssetID:                      assetID,
		RequestedByUserID:            userID,
		ExpiresSeconds:               300,
		ExpiresAt:                    gatewayExpiresAt,
		IssuanceRequestID:            "77777777-7777-4777-8777-777777777777",
		StorageBindingVersion:        1,
		StorageProvider:              "huawei_obs",
		StorageEndpointHost:          "obs.cn-north-4.myhuaweicloud.com",
		StorageBucket:                "private-acceptance-bucket",
		StorageObjectKey:             objectKey,
		SourceURLSHA256:              strings.Repeat("a", 64),
		RelayIssuedAt:                now.Add(-30 * time.Second),
		RelayExpiresAt:               now.Add(10 * time.Minute),
		GatewayRegistrationRequestID: "88888888-8888-4888-8888-888888888888",
		GatewayTicketID:              "99999999-9999-4999-8999-999999999999",
		GatewayTicketURLSHA256:       sha256Hex([]byte(ticketURL)),
		GatewayIssuedAt:              gatewayIssuedAt,
		GatewayExpiresAt:             gatewayExpiresAt,
		GatewayTransferReference:     "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		CreatedAt:                    now.Add(-4 * time.Second),
	}
	download := artifactDownload{URL: ticketURL, ExpiresSeconds: 300, DownloadRecordID: record.ID, DownloadStatus: "issued"}
	task := platformTask{ID: taskID, CompanyID: companyID, UserID: userID}
	output := dto.PlatformGenerationArtifact{AssetID: assetID, ObjectKey: objectKey}
	validate := func(candidate downloadRecordBinding, candidateDownload artifactDownload) error {
		return validateDownloadRecordBinding(candidate, candidateDownload, task, output, "https://obs.cn-north-4.myhuaweicloud.com", record.StorageBucket, now)
	}
	if err := validate(record, download); err != nil {
		t.Fatalf("valid immutable download binding was rejected: %v", err)
	}
	mutations := []func(*downloadRecordBinding, *artifactDownload){
		func(candidate *downloadRecordBinding, _ *artifactDownload) { candidate.StorageObjectKey += "-other" },
		func(candidate *downloadRecordBinding, _ *artifactDownload) {
			candidate.GatewayTicketURLSHA256 = strings.Repeat("b", 64)
		},
		func(candidate *downloadRecordBinding, _ *artifactDownload) {
			candidate.IssuanceRequestID = candidate.GatewayRegistrationRequestID
		},
		func(candidate *downloadRecordBinding, _ *artifactDownload) {
			candidate.RequestedByUserID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
		},
		func(candidate *downloadRecordBinding, _ *artifactDownload) {
			candidate.GatewayExpiresAt = candidate.RelayExpiresAt.Add(time.Second)
		},
		func(_ *downloadRecordBinding, candidateDownload *artifactDownload) {
			candidateDownload.URL += "?ticket=leak"
		},
	}
	for index, mutate := range mutations {
		candidate, candidateDownload := record, download
		mutate(&candidate, &candidateDownload)
		if err := validate(candidate, candidateDownload); err == nil {
			t.Fatalf("download binding mutation %d was accepted", index)
		}
	}
}

func TestAnonymousOBSObjectURLContainsNoCredentialMaterial(t *testing.T) {
	objectKey := "outputs/11111111-1111-4111-8111-111111111111/22222222-2222-4222-8222-222222222222/33333333-3333-4333-8333-333333333333"
	raw, err := anonymousOBSObjectURL("https://obs.cn-north-4.myhuaweicloud.com", "private-acceptance-bucket", objectKey)
	if err != nil {
		t.Fatal(err)
	}
	if strings.ContainsAny(raw, "?#") || raw != "https://obs.cn-north-4.myhuaweicloud.com/private-acceptance-bucket/"+objectKey {
		t.Fatalf("anonymous OBS URL is not exact and credential-free: %s", raw)
	}
}

var httpClientForTests = http.Client{Timeout: time.Second}

func containsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func TestStrictJSONRejectsUnknownBillFields(t *testing.T) {
	_, err := decodeStrictJSON[providerBillEvidence]([]byte(`{"schema_version":1,"unknown":true}`))
	if err == nil {
		t.Fatal("unknown provider bill field must be rejected")
	}
}

func TestEvidencePathsResolveFromMigrationConfigDirectory(t *testing.T) {
	configDir := t.TempDir()
	outputDir := t.TempDir()
	r := &runner{
		config:    acceptanceConfig{Runtime: runtimeConfig{OutputDirectory: outputDir}},
		configDir: configDir,
		runID:     uuid.NewString(),
	}
	file, err := r.writeEvidence("provider_task", map[string]any{"secret_free": true})
	if err != nil {
		t.Fatal(err)
	}
	resolved := filepath.Clean(filepath.Join(configDir, filepath.FromSlash(file.Path)))
	expected := filepath.Join(r.outputDir, "provider_task.json")
	if resolved != expected {
		t.Fatalf("evidence path resolves to %s, expected %s", resolved, expected)
	}
	if _, err := os.Stat(resolved); err != nil {
		t.Fatal(err)
	}
}

func TestSourceDocumentHashRejectsEmptySymlinkAndOversizedFiles(t *testing.T) {
	directory := t.TempDir()
	regular := filepath.Join(directory, "source.bin")
	if err := os.WriteFile(regular, []byte("provider invoice source"), 0o600); err != nil {
		t.Fatal(err)
	}
	if digest, err := sha256File(regular); err != nil || !revisionPattern.MatchString(digest) {
		t.Fatalf("bounded regular source was rejected: digest=%s err=%v", digest, err)
	}
	empty := filepath.Join(directory, "empty.bin")
	if err := os.WriteFile(empty, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := sha256File(empty); err == nil {
		t.Fatal("empty source document must be rejected")
	}
	symlink := filepath.Join(directory, "source-link.bin")
	if err := os.Symlink(regular, symlink); err != nil {
		t.Fatal(err)
	}
	if _, err := sha256File(symlink); err == nil {
		t.Fatal("symlink source document must be rejected")
	}
	oversized := filepath.Join(directory, "oversized.bin")
	file, err := os.OpenFile(oversized, os.O_CREATE|os.O_WRONLY|os.O_EXCL, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if err := file.Truncate(16*1024*1024 + 1); err != nil {
		file.Close()
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := sha256File(oversized); err == nil {
		t.Fatal("oversized source document must be rejected")
	}
}

func TestProviderTaskEvidenceShapeFailsClosed(t *testing.T) {
	for _, observation := range []map[string]any{
		{},
		{"provider_task": "wrong-type"},
		{"provider_task": map[string]any{}},
		{"provider_task": map[string]any{"provider_task_reference": "task-only"}},
	} {
		if _, err := providerTaskEvidenceFromObservation(observation); err == nil {
			t.Fatalf("invalid provider task evidence was accepted: %#v", observation)
		}
	}
	valid := map[string]any{
		"provider_task": map[string]any{
			"provider_task_reference": "provider-task-1",
			"channel_id":              "50",
		},
	}
	if _, err := providerTaskEvidenceFromObservation(valid); err != nil {
		t.Fatal(err)
	}
}

func TestCreatePhaseEvidenceWriteFailureIsReturned(t *testing.T) {
	configDir := t.TempDir()
	r := &runner{
		config:    acceptanceConfig{Runtime: runtimeConfig{OutputDirectory: t.TempDir()}},
		configDir: configDir,
		runID:     uuid.NewString(),
	}
	provider := map[string]any{"provider_task_reference": "provider-task-1", "channel_id": "50"}
	checkpoint := createCheckpoint{SchemaVersion: checkpointSchemaVersion, Status: "BLOCKED"}
	if _, err := r.writeEvidence("provider_task", provider); err != nil {
		t.Fatal(err)
	}
	if _, err := r.writeCreatePhaseEvidence(provider, checkpoint); err == nil {
		t.Fatal("create-only evidence collision must be returned to the caller")
	}
}

func TestProviderBillMustBindTaskChannelTimeCurrencyAndSource(t *testing.T) {
	directory := t.TempDir()
	now := time.Now().UTC().Truncate(time.Second)
	sourceDocument := []byte("sanitized test fixture standing in for a provider export")
	sourcePath := filepath.Join(directory, "provider-export.bin")
	if err := os.WriteFile(sourcePath, sourceDocument, 0o600); err != nil {
		t.Fatal(err)
	}
	sourceHash := "sha256:" + sha256Hex(sourceDocument)
	task := platformTask{
		ID:         "55555555-5555-4555-8555-555555555555",
		CompanyID:  "11111111-1111-4111-8111-111111111111",
		UserID:     "22222222-2222-4222-8222-222222222222",
		ModelID:    "33333333-3333-4333-8333-333333333333",
		RelayJobID: "66666666-6666-4666-8666-666666666666",
	}
	bill := providerBillEvidence{
		SchemaVersion:               2,
		ProviderName:                "kling",
		PlatformTaskID:              task.ID,
		RelayJobID:                  task.RelayJobID,
		ProviderTaskReference:       "provider-task-1",
		ProviderBillReference:       "provider-bill-1",
		ChannelID:                   50,
		OccurredAtUTC:               now.Format(time.RFC3339),
		AmountCents:                 30,
		ContractCurrency:            "CNY",
		BilledQuantity:              "30",
		BilledUnit:                  "credit",
		ContractRateSource:          "staging account contract rate export",
		ContractRateEffectiveAtUTC:  now.Add(-time.Hour).Format(time.RFC3339),
		EvidenceSource:              "provider_reported",
		SourceDocumentSHA256:        sourceHash,
		AccountConsoleVerifiedAtUTC: now.Format(time.RFC3339),
		OfficialDocumentURL:         "https://kling.ai/document-api/apiReference/model/textToVideo",
		CostDisposition:             "billed",
	}
	path := filepath.Join(directory, "bill.json")
	approvalPath := filepath.Join(directory, "approval.json")
	signaturePath := filepath.Join(directory, "approval.signature.json")
	publicKey, privateKey, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("TEST_BILL_APPROVAL_PUBLIC_KEY", base64.StdEncoding.EncodeToString(publicKey))
	config := validTestConfig(t.TempDir())
	config.Provider.BillEvidencePath = path
	config.Provider.SourceDocumentPath = sourcePath
	config.Provider.SourceDocumentExpectedSHA256 = sourceHash
	config.Provider.ApprovalPayloadPath = approvalPath
	config.Provider.ApprovalSignaturePath = signaturePath
	config.Provider.ApprovalPublicKeyEnv = "TEST_BILL_APPROVAL_PUBLIC_KEY"
	config.Provider.ApprovalPublicKeyFingerprint = "sha256:" + sha256Hex(publicKey)
	checkpointHash := "sha256:" + strings.Repeat("c", 64)
	checkpoint := createCheckpoint{CreatedAtUTC: now.Add(-2 * time.Minute).Format(time.RFC3339Nano)}
	r := &runner{config: config, configDir: directory, checks: map[string]bool{}, checkpoint: &checkpoint, checkpointSHA256: checkpointHash}
	observation := map[string]any{
		"job":   model.PlatformGenerationJob{UpstreamTaskID: bill.ProviderTaskReference, CreatedAt: now.Add(-time.Minute)},
		"route": model.PlatformGenerationProviderRoute{ChannelID: bill.ChannelID},
	}
	writeApprovedBill := func() {
		t.Helper()
		serialized, marshalErr := json.Marshal(bill)
		if marshalErr != nil {
			t.Fatal(marshalErr)
		}
		if writeErr := os.WriteFile(path, serialized, 0o600); writeErr != nil {
			t.Fatal(writeErr)
		}
		r.config.Provider.BillEvidenceExpectedSHA256 = "sha256:" + sha256Hex(serialized)
		approval := providerBillApproval{
			SchemaVersion: 1, Kind: "relay_real_channel_provider_bill_approval",
			CheckpointSHA256: checkpointHash, BillEvidenceSHA256: r.config.Provider.BillEvidenceExpectedSHA256,
			SourceDocumentSHA256: sourceHash, ProviderName: bill.ProviderName,
			PlatformTaskID: task.ID, RelayJobID: task.RelayJobID, ProviderTaskReference: bill.ProviderTaskReference,
			ProviderBillReference: bill.ProviderBillReference, ChannelID: bill.ChannelID, AmountCents: bill.AmountCents,
			ContractCurrency: bill.ContractCurrency, CostDisposition: bill.CostDisposition,
			ZeroCostReason: bill.ZeroCostReason, ZeroCostEvidenceReference: bill.ZeroCostEvidenceReference,
			Decision: "approved", ApproverSubject: "finance-approver", ApproverRole: "finance",
			ApprovedAtUTC: now.Format(time.RFC3339), ExpiresAtUTC: now.Add(time.Hour).Format(time.RFC3339), Nonce: uuid.NewString(),
		}
		approvalBytes, _ := json.Marshal(approval)
		if writeErr := os.WriteFile(approvalPath, approvalBytes, 0o600); writeErr != nil {
			t.Fatal(writeErr)
		}
		r.config.Provider.ApprovalPayloadExpectedSHA256 = "sha256:" + sha256Hex(approvalBytes)
		signingInput := append([]byte("relay-provider-bill-approval.v1\n"), approvalBytes...)
		envelope := providerBillApprovalSignature{
			SchemaVersion: 1, Algorithm: "Ed25519", KeyID: "finance-key-1",
			PayloadSHA256: "sha256:" + sha256Hex(approvalBytes), SignatureBase64: base64.StdEncoding.EncodeToString(ed25519.Sign(privateKey, signingInput)),
		}
		envelopeBytes, _ := json.Marshal(envelope)
		if writeErr := os.WriteFile(signaturePath, envelopeBytes, 0o600); writeErr != nil {
			t.Fatal(writeErr)
		}
	}
	writeApprovedBill()
	verified, summary, err := r.verifyProviderBill(task, observation)
	if err != nil {
		t.Fatal(err)
	}
	if verified.ProviderBillReference != bill.ProviderBillReference || summary["contract_currency"] != "CNY" {
		t.Fatal("bill evidence binding was not preserved")
	}
	if err := os.WriteFile(sourcePath, []byte("tampered"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := r.verifyProviderBill(task, observation); err == nil {
		t.Fatal("source document tampering must be rejected")
	}
	if err := os.WriteFile(sourcePath, sourceDocument, 0o600); err != nil {
		t.Fatal(err)
	}
	bill.AmountCents = 0
	bill.CostDisposition = "billed"
	writeApprovedBill()
	if _, _, err := r.verifyProviderBill(task, observation); err == nil {
		t.Fatal("zero cost without explicit proof must be rejected")
	}
	bill.CostDisposition = "verified_zero"
	bill.ZeroCostReason = "included_contract"
	bill.ZeroCostEvidenceReference = "invoice-zero-line-1"
	bill.EvidenceSource = "provider_invoice"
	writeApprovedBill()
	if _, _, err := r.verifyProviderBill(task, observation); err != nil {
		t.Fatalf("approved explicit zero cost must be accepted: %v", err)
	}

	bill.ContractCurrency = "CREDIT"
	writeApprovedBill()
	if _, _, err := r.verifyProviderBill(task, observation); err == nil {
		t.Fatal("implicit provider credits must not be accepted as ledger currency")
	}
	bill.ContractCurrency = "CNY"
	writeApprovedBill()
	if err := os.WriteFile(signaturePath, []byte(`{"schema_version":1,"algorithm":"Ed25519","key_id":"finance-key-1","payload_sha256":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","signature_base64":"tampered"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := r.verifyProviderBill(task, observation); err == nil {
		t.Fatal("tampered detached approval signature must be rejected")
	}
}

func TestCreateRejectsExistingTaskAndFinalizeRequiresCheckpoint(t *testing.T) {
	config := validTestConfig(t.TempDir())
	config.Platform.Task.ExistingTaskID = "55555555-5555-4555-8555-555555555555"
	if err := (&runner{config: config, phase: "create"}).validateConfig(); err == nil {
		t.Fatal("create phase must not reuse an existing task")
	}
	config.Platform.Task.ExistingTaskID = ""
	config.Runtime.CreateCheckpointPath = ""
	config.Runtime.CreateCheckpointExpectedSHA256 = ""
	if err := (&runner{config: config, phase: "finalize"}).validateConfig(); err == nil {
		t.Fatal("finalize must require a pinned create checkpoint")
	}
}

func TestCheckedInRealChannelExamplesStrictlyDecodeAndValidateV2(t *testing.T) {
	repositoryTests := filepath.Clean(filepath.Join("..", "..", "..", "..", "tests"))
	configPath := filepath.Join(repositoryTests, "relay-real-channel-acceptance.config.example.json")
	config, err := readStrictJSON[acceptanceConfig](configPath)
	if err != nil {
		t.Fatalf("config example does not strictly decode: %v", err)
	}
	if config.SchemaVersion != configSchemaVersion || config.Platform.DownloadGatewayBaseURL == "" {
		t.Fatal("config example is not the complete schema v2 Gateway contract")
	}
	if err := (&runner{config: config, phase: "preflight"}).validateConfig(); err != nil {
		t.Fatalf("config example does not validate for preflight: %v", err)
	}
	if err := (&runner{config: config, phase: "create"}).validateConfig(); err != nil {
		t.Fatalf("config example does not validate for create: %v", err)
	}
	if err := (&runner{config: config, phase: "finalize"}).validateConfig(); err != nil {
		t.Fatalf("config example does not validate for finalize: %v", err)
	}
	config.Runtime.OutputDirectory = t.TempDir()
	for _, name := range []string{
		config.Platform.BearerTokenEnv,
		config.Platform.DatabaseDSNEnv,
		config.Provider.AccessKeyEnv,
		config.Provider.SecretKeyEnv,
		config.Storage.EndpointEnv,
		config.Storage.BucketEnv,
		config.Storage.AccessKeyEnv,
		config.Storage.SecretKeyEnv,
		config.Runtime.RelayDatabaseDSNEnv,
		config.Runtime.RelayRuntimeIdentityTokenEnv,
	} {
		t.Setenv(name, "")
	}
	exampleRunner := &runner{
		config:     config,
		configDir:  repositoryTests,
		phase:      "preflight",
		runID:      uuid.NewString(),
		checks:     map[string]bool{},
		httpClient: newAcceptanceHTTPClient(),
	}
	if status := exampleRunner.run(context.Background()); status != "BLOCKED" {
		t.Fatalf("credential-free example preflight must be BLOCKED, got %s", status)
	}

	bill, err := readStrictJSON[providerBillEvidence](filepath.Join(repositoryTests, "relay-real-channel-provider-bill.example.json"))
	if err != nil || bill.SchemaVersion != 2 || bill.PlatformTaskID == "" || bill.RelayJobID == "" || bill.CostDisposition != "billed" {
		t.Fatalf("provider bill example is not schema v2: %v", err)
	}
	approval, err := readStrictJSON[providerBillApproval](filepath.Join(repositoryTests, "relay-real-channel-provider-bill-approval.example.json"))
	if err != nil || approval.Kind != "relay_real_channel_provider_bill_approval" || approval.CheckpointSHA256 == "" || approval.Decision != "approved" {
		t.Fatalf("provider bill approval example is invalid: %v", err)
	}
	approvalSignature, err := readStrictJSON[providerBillApprovalSignature](filepath.Join(repositoryTests, "relay-real-channel-provider-bill-approval.signature.example.json"))
	if err != nil || approvalSignature.Algorithm != "Ed25519" || !revisionPattern.MatchString(approvalSignature.PayloadSHA256) {
		t.Fatalf("provider bill approval signature example is invalid: %v", err)
	}
	proof, err := readStrictJSON[externalDownloadProof](filepath.Join(repositoryTests, "relay-real-channel-download-proof.example.json"))
	if err != nil || proof.Kind != "relay.download_completion.edge_gateway.proof" || proof.IssuanceRequestID != proof.GatewayRequestID || proof.TransferReference == proof.GatewayRequestID {
		t.Fatalf("download producer proof example is invalid: %v", err)
	}
	proofSignature, err := readStrictJSON[providerBillApprovalSignature](filepath.Join(repositoryTests, "relay-real-channel-download-proof.signature.example.json"))
	if err != nil || proofSignature.Algorithm != "Ed25519" || !revisionPattern.MatchString(proofSignature.PayloadSHA256) {
		t.Fatalf("download producer signature example is invalid: %v", err)
	}
}

func TestFinalizeCheckpointIsCreateOnlyHashAndTaskBound(t *testing.T) {
	directory := t.TempDir()
	checkpoint := createCheckpoint{
		SchemaVersion: checkpointSchemaVersion,
		Kind:          "relay_real_channel_create_checkpoint",
		Status:        "BLOCKED",
		Reason:        "external_download_receipt_and_independent_provider_bill_approval_required",
		CreateRunID:   uuid.NewString(),
		Environment:   "relay-real-channel-staging",
		Mode:          "text_to_video",
		CreatedAtUTC:  time.Now().UTC().Format(time.RFC3339Nano),
		CompanyID:     "11111111-1111-4111-8111-111111111111",
		UserID:        "22222222-2222-4222-8222-222222222222",
		TaskID:        "55555555-5555-4555-8555-555555555555",
		RelayJobID:    "66666666-6666-4666-8666-666666666666",
		ModelID:       "33333333-3333-4333-8333-333333333333",
		Artifact:      checkpointArtifact{AssetID: "77777777-7777-4777-8777-777777777777"},
		RuntimeBuildIdentity: runtimeBuildIdentity{
			InstanceID:           uuid.NewString(),
			UpstreamGitRevision:  "0ab02020603d22e5613bc4cf46bfab06f8567769",
			SourceGitRevision:    strings.Repeat("9", 40),
			SourceSnapshotSHA256: "sha256:" + strings.Repeat("8", 64),
			SourceSnapshotFiles:  321,
			ImageDigest:          "sha256:" + strings.Repeat("7", 64),
		},
	}
	serialized, _ := json.Marshal(checkpoint)
	checkpointPath := filepath.Join(directory, "checkpoint.json")
	if err := os.WriteFile(checkpointPath, serialized, 0o600); err != nil {
		t.Fatal(err)
	}
	config := validTestConfig(t.TempDir())
	config.Runtime.CreateCheckpointPath = checkpointPath
	config.Runtime.CreateCheckpointExpectedSHA256 = "sha256:" + sha256Hex(serialized)
	r := &runner{config: config, configDir: directory, taskOverride: checkpoint.TaskID}
	loaded, _, err := r.loadFinalizeCheckpoint()
	if err != nil || loaded.TaskID != checkpoint.TaskID {
		t.Fatalf("valid checkpoint was rejected: %v", err)
	}
	r.taskOverride = "88888888-8888-4888-8888-888888888888"
	if _, _, err := r.loadFinalizeCheckpoint(); err == nil {
		t.Fatal("checkpoint must reject a different task override")
	}
	r.taskOverride = checkpoint.TaskID
	if err := os.WriteFile(checkpointPath, append(serialized, ' '), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := r.loadFinalizeCheckpoint(); err == nil {
		t.Fatal("checkpoint hash mismatch must be rejected")
	}
}

func TestSignedOBSURLMustBindEndpointBucketObjectAndTTL(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	endpoint := "https://obs.cn-north-4.myhuaweicloud.com"
	bucket := "acceptance-private-bucket"
	objectKey := "outputs/11111111-1111-4111-8111-111111111111/66666666-6666-4666-8666-666666666666/77777777-7777-4777-8777-777777777777"
	expires := now.Add(300 * time.Second).Unix()
	validVirtual := fmt.Sprintf("https://%s.obs.cn-north-4.myhuaweicloud.com/%s?AccessKeyId=AK12345678&Expires=%d&Signature=signed", bucket, objectKey, expires)
	validPath := fmt.Sprintf("https://obs.cn-north-4.myhuaweicloud.com/%s/%s?AccessKeyId=AK12345678&Expires=%d&Signature=signed", bucket, objectKey, expires)
	for _, raw := range []string{validVirtual, validPath} {
		metadata, err := validatePlatformSignedOBSURL(raw, endpoint, bucket, objectKey, 300, now)
		if err != nil || metadata.Algorithm != "obs-v2" {
			t.Fatalf("valid Huawei OBS URL was rejected: %v", err)
		}
	}
	invalid := []string{
		strings.Replace(validVirtual, ".obs.cn-north-4.myhuaweicloud.com", ".evil.example", 1),
		strings.Replace(validVirtual, "/"+objectKey, "/other.mp4", 1),
		strings.Replace(validVirtual, "https://", "http://", 1),
		validVirtual + "&signature=duplicate",
		fmt.Sprintf("https://%s.obs.cn-north-4.myhuaweicloud.com/%s?AccessKeyId=AK12345678&Expires=%d&Signature=signed", bucket, objectKey, now.Add(-time.Second).Unix()),
		fmt.Sprintf("https://%s.obs.cn-north-4.myhuaweicloud.com/%s?AccessKeyId=AK12345678&Expires=%d&Signature=signed", bucket, objectKey, now.Add(600*time.Second).Unix()),
		strings.Replace(validVirtual, "/outputs/", "/outputs%2F", 1),
		validVirtual + "#fragment",
	}
	for _, raw := range invalid {
		if _, err := validatePlatformSignedOBSURL(raw, endpoint, bucket, objectKey, 300, now); err == nil {
			t.Fatalf("unsafe signed URL was accepted: %s", raw)
		}
	}
}

func TestEvidenceSecretScanRejectsRawEncodedAndSignedURLMaterialBeforeWrite(t *testing.T) {
	secret := "provider-secret-0123456789"
	config := validTestConfig(t.TempDir())
	t.Setenv(config.Provider.SecretKeyEnv, secret)
	r := &runner{config: config, configDir: t.TempDir(), runID: uuid.NewString()}
	for _, value := range []string{
		secret,
		base64.StdEncoding.EncodeToString([]byte(secret)),
		"https://bucket.obs.example/object?AccessKeyId=AK123&Signature=signed",
		"Bearer browser-token",
		"postgresql://user:password@db/platform",
	} {
		if err := r.scanSecretFree([]byte(`{"value":` + strconv.Quote(value) + `}`)); err == nil {
			t.Fatalf("secret material was accepted: %s", value)
		}
	}
	if err := r.scanSecretFree([]byte(`{"key_fingerprint":"sha256:` + strings.Repeat("a", 64) + `"}`)); err != nil {
		t.Fatalf("safe fingerprint was rejected: %v", err)
	}
	if _, err := r.writeEvidence("leak", map[string]string{"value": secret}); err == nil {
		t.Fatal("evidence containing a secret must not be written")
	}
	if _, err := os.Stat(filepath.Join(r.outputDir, "leak.json")); !os.IsNotExist(err) {
		t.Fatal("secret-bearing evidence file must not exist")
	}
}

func TestPlatformHeadersNeverUseIdentityHeadersForRealGate(t *testing.T) {
	config := validTestConfig(t.TempDir())
	t.Setenv(config.Platform.BearerTokenEnv, "real-staging-bearer-token")
	r := &runner{config: config, phase: "create"}
	headers := r.platformHeaders()
	if headers.Get("Authorization") != "Bearer real-staging-bearer-token" || headers.Get("X-Company-ID") != "" || headers.Get("X-User-ID") != "" {
		t.Fatalf("unexpected real-gate headers: %#v", headers)
	}
}
