package model

import (
	"crypto/sha256"
	"fmt"
	"reflect"
	"strings"
)

const (
	RelaySchemaTargetVersion int64 = 8
	RelaySchemaMinVersion    int64 = 1
	RelaySchemaMaxVersion    int64 = 8

	RelaySchemaStateClean    = "clean"
	RelaySchemaStateApplying = "applying"
	RelaySchemaStateFailed   = "failed"

	RelaySchemaStatusUninitialized = "uninitialized"
	RelaySchemaStatusMigrating     = "migrating"
	RelaySchemaStatusDirty         = "dirty"
	RelaySchemaStatusCorrupt       = "corrupt"
	RelaySchemaStatusLedgerGap     = "ledger_gap"
	RelaySchemaStatusTooOld        = "too_old"
	RelaySchemaStatusAhead         = "ahead"
	RelaySchemaStatusCompatible    = "compatible"
	RelaySchemaStatusCurrent       = "current"
	RelaySchemaStatusUnavailable   = "unavailable"
)

// These digests freeze the actual v1 artifacts. The schema artifact tests
// derive them from the executable migration call graph and the complete GORM
// model metadata (including TableName and nested model types). Editing a v1
// body or a v1 model without introducing a new schema version therefore fails
// the build instead of silently reinterpreting an already-applied version.
//
// They are intentionally not calculated from the live model at runtime: doing
// that would make an ordinary future model edit rewrite the identity of v1 and
// report every existing database as corrupt.
const (
	relaySchemaV1FrozenVersion int64 = 1
	relaySchemaV1FrozenName          = "frozen_new_api_relay_baseline"
	relaySchemaV1FrozenPhase         = "baseline"
)

const (
	relaySchemaV1SourceArtifactSHA256 = "sha256:25d689955d670d89d968c640c516973222eadda800556fd9cd1ca06b4adda37c"
	relaySchemaV1ModelArtifactSHA256  = "sha256:dfbb25b9c63da6134574548c7519fc7262abac327f0cd7b1feb977d5b04c5e56"
	relaySchemaV1FrozenChecksumSHA256 = "sha256:369af2b5c47652ae9e03a2f79ba64f56c3b517deb7f4c8f933ce3957082698a7"
)

const (
	relaySchemaV2FrozenVersion int64 = 2
	relaySchemaV2FrozenName          = "relay_release_hardening_v2"
	relaySchemaV2FrozenPhase         = "hardening"
)

const (
	relaySchemaV2SourceArtifactSHA256 = "sha256:03de3ed038c3a9f7b6e160ac720e4350b9d468c09417cdc9e280289ed390fef2"
	relaySchemaV2ModelArtifactSHA256  = "sha256:dfbb25b9c63da6134574548c7519fc7262abac327f0cd7b1feb977d5b04c5e56"
	relaySchemaV2FrozenChecksumSHA256 = "sha256:a3dc154ca42086544096cc0c3e3f2c84479e52e2ad76bd4d32aa2806c2c9af0e"
)

const (
	relaySchemaV3FrozenVersion int64 = 3
	relaySchemaV3FrozenName          = "provider_channel_credential_ordering_v3"
	relaySchemaV3FrozenPhase         = "hardening"
)

const (
	relaySchemaV3SourceArtifactSHA256 = "sha256:4d784286e5480a10a83f4408b303eec075a347fa405d45650e12c19425e4659d"
	relaySchemaV3ModelArtifactSHA256  = "sha256:dfbb25b9c63da6134574548c7519fc7262abac327f0cd7b1feb977d5b04c5e56"
	relaySchemaV3FrozenChecksumSHA256 = "sha256:0295d36ca5032088cc2e0b3b7f935aaeb24c3c5847a6b0a92a4dc3099d58e553"
)

const (
	relaySchemaV4FrozenVersion int64 = 4
	relaySchemaV4FrozenName          = "generation_route_release_binding_v4"
	relaySchemaV4FrozenPhase         = "release"
)

// These values are filled from the independently-derived source/model and
// PostgreSQL catalog artifacts by the v4 freeze tests. They must never be
// changed after this version is deployed.
const (
	relaySchemaV4SourceArtifactSHA256 = "sha256:6c1ff01ee6567115190f12dcd947a96b01d88bbee5668705d811f51a526e4c34"
	relaySchemaV4ModelArtifactSHA256  = "sha256:75c900e5d3edb4785f774d44b23e60b7d0fdd23ba541c28ccf6cb0ec727bb01c"
	relaySchemaV4FrozenChecksumSHA256 = "sha256:4a91686133814c07401a11eea3fe373154219923c4d63666ca99e3049d96079d"
)

const (
	relaySchemaV5FrozenVersion int64 = 5
	relaySchemaV5FrozenName          = "channel_test_diagnostic_taxonomy_v5"
	relaySchemaV5FrozenPhase         = "hardening"
)

// Filled from the independently-derived source/model and PostgreSQL catalog
// fixtures before the v5 migration is released. Once frozen, these values are
// historical identities and must never be changed.
const (
	relaySchemaV5SourceArtifactSHA256 = "sha256:1d63451fdcdc0edfd869df3995d4ea14b3d0a3fa0871dadde9a842197b92c9d8"
	relaySchemaV5ModelArtifactSHA256  = "sha256:75c900e5d3edb4785f774d44b23e60b7d0fdd23ba541c28ccf6cb0ec727bb01c"
	relaySchemaV5FrozenChecksumSHA256 = "sha256:d8066d7081eb4a73239333bab10b78e825457dcf78bc3d3aaa195c52edc8b7f6"
)

const (
	relaySchemaV6FrozenVersion int64 = 6
	relaySchemaV6FrozenName          = "channel_test_durable_lifecycle_v6"
	relaySchemaV6FrozenPhase         = "hardening"
)

// These values are derived from the executable v6 migration, current v6 model
// registry and independently bootstrapped PostgreSQL 16 catalog. Once this
// release is published they become historical identities like v1-v5.
const (
	relaySchemaV6SourceArtifactSHA256 = "sha256:12fb064e910090af18396887453d64bfdafbcd6efc0a17b4efce3a0f6c68c303"
	relaySchemaV6ModelArtifactSHA256  = "sha256:44ace85c79be776150c9aa62696d2b79c26d2646ef06f9248f2a3085aaff7977"
	relaySchemaV6FrozenChecksumSHA256 = "sha256:8cffc546bb13c3f36f734dbb2e45a750da5b3e614de3beda82c6dd87dd84af00"
)

const (
	relaySchemaV7FrozenVersion int64 = 7
	relaySchemaV7FrozenName          = "channel_test_artifact_content_types_v7"
	relaySchemaV7FrozenPhase         = "hardening"
)

const (
	relaySchemaV8FrozenVersion int64 = 8
	relaySchemaV8FrozenName          = "provider_cost_allocation_evidence_v8"
	relaySchemaV8FrozenPhase         = "billing"
)

// Filled from the independently-derived v8 source/model and PostgreSQL 16
// catalog artifacts before release. V8 adds the immutable, receipt-bound
// multi-component provider cost allocation ledger.
const (
	relaySchemaV8SourceArtifactSHA256 = "sha256:4d1422f6f691d1440600536f9b89c3e1fb9af9e69f880437b95d437b101f71dd"
	relaySchemaV8ModelArtifactSHA256  = "sha256:24f03d665ed5df5958cbf82075bfd28fb8aeb97fee277164e443a0218132776d"
	relaySchemaV8FrozenChecksumSHA256 = "sha256:1def22667e226cf8dfbd467e18445874dcce6959a8b9e74b43623e14bbc31de7"
)

// Filled from the independently-derived v7 source/model and PostgreSQL 16
// catalog artifacts before release. V7 widens protected channel-test artifact
// evidence and adds the personal provider-cost billing scope.
const (
	relaySchemaV7SourceArtifactSHA256 = "sha256:fd39a91c83f977d4516b1fad5bb12fe55b4ca4335e9a703c27ed74395dcc9cb2"
	relaySchemaV7ModelArtifactSHA256  = "sha256:48ebd15848b4cd0047f2e54c98a85efcc0e56e5a00ec42b89d807da36f03b17b"
	relaySchemaV7FrozenChecksumSHA256 = "sha256:da3ddb86260818f894b13fc6b3ad34031b6089dddb83954172984d07e451c4c3"
)

func relaySchemaV1LiveArtifactValidationRequired(targetVersion int64) bool {
	return targetVersion == 1
}

func relaySchemaV2LiveArtifactValidationRequired(targetVersion int64) bool {
	return targetVersion == 2
}

func relaySchemaV3LiveArtifactValidationRequired(targetVersion int64) bool {
	return targetVersion == 3
}

func relaySchemaV4LiveArtifactValidationRequired(targetVersion int64) bool {
	return targetVersion == 4
}

func relaySchemaV5LiveArtifactValidationRequired(targetVersion int64) bool {
	return targetVersion == 5
}

func relaySchemaV6LiveArtifactValidationRequired(targetVersion int64) bool {
	return targetVersion == 6
}

func relaySchemaV7LiveArtifactValidationRequired(targetVersion int64) bool {
	return targetVersion == 7
}

type RelaySchemaContract struct {
	TargetVersion int64            `json:"target_version"`
	MinVersion    int64            `json:"min_version"`
	MaxVersion    int64            `json:"max_version"`
	Checksums     map[int64]string `json:"checksums"`
}

func RelaySchemaV1Checksum() string {
	digest := sha256.Sum256(relaySchemaV1CanonicalBytes())
	return fmt.Sprintf("sha256:%x", digest[:])
}

func RelaySchemaV2Checksum() string {
	digest := sha256.Sum256(relaySchemaV2CanonicalBytes())
	return fmt.Sprintf("sha256:%x", digest[:])
}

func RelaySchemaV3Checksum() string {
	digest := sha256.Sum256(relaySchemaV3CanonicalBytes())
	return fmt.Sprintf("sha256:%x", digest[:])
}

func RelaySchemaV4Checksum() string {
	digest := sha256.Sum256(relaySchemaV4CanonicalBytes())
	return fmt.Sprintf("sha256:%x", digest[:])
}

func RelaySchemaV5Checksum() string {
	digest := sha256.Sum256(relaySchemaV5CanonicalBytes())
	return fmt.Sprintf("sha256:%x", digest[:])
}

func RelaySchemaV6Checksum() string {
	digest := sha256.Sum256(relaySchemaV6CanonicalBytes())
	return fmt.Sprintf("sha256:%x", digest[:])
}

func RelaySchemaV7Checksum() string {
	digest := sha256.Sum256(relaySchemaV7CanonicalBytes())
	return fmt.Sprintf("sha256:%x", digest[:])
}

func RelaySchemaV8Checksum() string {
	digest := sha256.Sum256(relaySchemaV8CanonicalBytes())
	return fmt.Sprintf("sha256:%x", digest[:])
}

// relaySchemaV2CanonicalBytes binds the independent v2 bootstrap and
// incremental source/model snapshots together with the exact no-catalog-delta
// PostgreSQL and database-principal surfaces. Historical v1 participates only
// through its already-frozen checksum.
func relaySchemaV2CanonicalBytes() []byte {
	var builder strings.Builder
	builder.WriteString("ai-video/new-api-relay/schema/v2\n")
	builder.WriteString("requires-v1-checksum|")
	builder.WriteString(relaySchemaV1FrozenChecksumSHA256)
	builder.WriteByte('\n')
	builder.WriteString("source-artifact|")
	builder.WriteString(relaySchemaV2SourceArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("model-artifact|")
	builder.WriteString(relaySchemaV2ModelArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("postgres-catalog|")
	builder.WriteString(relaySchemaV2PostgresCatalogSHA256)
	builder.WriteByte('\n')
	builder.WriteString("runtime-privilege-manifest|")
	builder.WriteString(relayRuntimeDatabasePrivilegeManifestV2SHA256)
	builder.WriteByte('\n')
	builder.WriteString("download-edge-privilege-manifest|")
	builder.WriteString(relayDownloadEdgeDatabasePrivilegeManifestV2SHA256)
	builder.WriteByte('\n')
	for _, step := range relaySchemaV2BootstrapSteps() {
		builder.WriteString("bootstrap-step|")
		builder.WriteString(step.ID)
		builder.WriteByte('\n')
	}
	builder.WriteString("incremental-step|attest-exact-v1-no-catalog-delta-v2\n")
	return []byte(builder.String())
}

// relaySchemaV3CanonicalBytes binds the standalone fresh-v3 snapshot and the
// exact v2-to-v3 correction to the frozen v2 release. The PostgreSQL catalog
// and principal surfaces are deliberately unchanged; v3 fixes only the
// credential-vault migration's lock ordering and search-path resolution.
func relaySchemaV3CanonicalBytes() []byte {
	var builder strings.Builder
	builder.WriteString("ai-video/new-api-relay/schema/v3\n")
	builder.WriteString("requires-v2-checksum|")
	builder.WriteString(relaySchemaV2FrozenChecksumSHA256)
	builder.WriteByte('\n')
	builder.WriteString("source-artifact|")
	builder.WriteString(relaySchemaV3SourceArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("model-artifact|")
	builder.WriteString(relaySchemaV3ModelArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("postgres-catalog|")
	builder.WriteString(relaySchemaV3PostgresCatalogSHA256)
	builder.WriteByte('\n')
	builder.WriteString("runtime-privilege-manifest|")
	builder.WriteString(relayRuntimeDatabasePrivilegeManifestV3SHA256)
	builder.WriteByte('\n')
	builder.WriteString("download-edge-privilege-manifest|")
	builder.WriteString(relayDownloadEdgeDatabasePrivilegeManifestV3SHA256)
	builder.WriteByte('\n')
	for _, step := range relaySchemaV3BootstrapSteps() {
		builder.WriteString("bootstrap-step|")
		builder.WriteString(step.ID)
		builder.WriteByte('\n')
	}
	builder.WriteString("incremental-step|provider-channel-credential-lock-ordering-v3\n")
	return []byte(builder.String())
}

// relaySchemaV4CanonicalBytes binds the first durable generation-route
// profile/release identity to an independently versioned schema release.
// Historical v1-v3 checksums participate only as immutable prerequisites.
func relaySchemaV4CanonicalBytes() []byte {
	var builder strings.Builder
	builder.WriteString("ai-video/new-api-relay/schema/v4\n")
	builder.WriteString("requires-v3-checksum|")
	builder.WriteString(relaySchemaV3FrozenChecksumSHA256)
	builder.WriteByte('\n')
	builder.WriteString("source-artifact|")
	builder.WriteString(relaySchemaV4SourceArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("model-artifact|")
	builder.WriteString(relaySchemaV4ModelArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("postgres-catalog|")
	builder.WriteString(relaySchemaV4PostgresCatalogSHA256)
	builder.WriteByte('\n')
	builder.WriteString("runtime-privilege-manifest|")
	builder.WriteString(relayRuntimeDatabasePrivilegeManifestV4SHA256)
	builder.WriteByte('\n')
	builder.WriteString("download-edge-privilege-manifest|")
	builder.WriteString(relayDownloadEdgeDatabasePrivilegeManifestV4SHA256)
	builder.WriteByte('\n')
	for _, step := range relaySchemaV4BootstrapSteps() {
		builder.WriteString("bootstrap-step|")
		builder.WriteString(step.ID)
		builder.WriteByte('\n')
	}
	builder.WriteString("incremental-step|generation-route-release-binding-v4\n")
	return []byte(builder.String())
}

// relaySchemaV5CanonicalBytes binds the closed, secret-free channel-test
// diagnostic taxonomy to a new catalog identity. The immutable v4 checksum is
// the only historical prerequisite; v5 never redefines a v4 receipt.
func relaySchemaV5CanonicalBytes() []byte {
	var builder strings.Builder
	builder.WriteString("ai-video/new-api-relay/schema/v5\n")
	builder.WriteString("requires-v4-checksum|")
	builder.WriteString(relaySchemaV4FrozenChecksumSHA256)
	builder.WriteByte('\n')
	builder.WriteString("source-artifact|")
	builder.WriteString(relaySchemaV5SourceArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("model-artifact|")
	builder.WriteString(relaySchemaV5ModelArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("postgres-catalog|")
	builder.WriteString(relaySchemaV5PostgresCatalogSHA256)
	builder.WriteByte('\n')
	builder.WriteString("runtime-privilege-manifest|")
	builder.WriteString(relayRuntimeDatabasePrivilegeManifestV5SHA256)
	builder.WriteByte('\n')
	builder.WriteString("download-edge-privilege-manifest|")
	builder.WriteString(relayDownloadEdgeDatabasePrivilegeManifestV5SHA256)
	builder.WriteByte('\n')
	for _, step := range relaySchemaV5BootstrapSteps() {
		builder.WriteString("bootstrap-step|")
		builder.WriteString(step.ID)
		builder.WriteByte('\n')
	}
	builder.WriteString("incremental-step|channel-test-diagnostic-taxonomy-v5\n")
	return []byte(builder.String())
}

// relaySchemaV6CanonicalBytes binds the first durable route-test submission,
// provider-task, verified-artifact and reconciliation state machine. Frozen
// v5 participates only through its already-deployed checksum.
func relaySchemaV6CanonicalBytes() []byte {
	var builder strings.Builder
	builder.WriteString("ai-video/new-api-relay/schema/v6\n")
	builder.WriteString("requires-v5-checksum|")
	builder.WriteString(relaySchemaV5FrozenChecksumSHA256)
	builder.WriteByte('\n')
	builder.WriteString("source-artifact|")
	builder.WriteString(relaySchemaV6SourceArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("model-artifact|")
	builder.WriteString(relaySchemaV6ModelArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("postgres-catalog|")
	builder.WriteString(relaySchemaV6PostgresCatalogSHA256)
	builder.WriteByte('\n')
	builder.WriteString("runtime-privilege-manifest|")
	builder.WriteString(relayRuntimeDatabasePrivilegeManifestV6SHA256)
	builder.WriteByte('\n')
	builder.WriteString("download-edge-privilege-manifest|")
	builder.WriteString(relayDownloadEdgeDatabasePrivilegeManifestV6SHA256)
	builder.WriteByte('\n')
	for _, step := range relaySchemaV6BootstrapSteps() {
		builder.WriteString("bootstrap-step|")
		builder.WriteString(step.ID)
		builder.WriteByte('\n')
	}
	builder.WriteString("incremental-step|channel-test-durable-lifecycle-v6\n")
	return []byte(builder.String())
}

// relaySchemaV7CanonicalBytes binds the protected artifact MIME widening and
// personal provider-cost scope to the immutable v6 lifecycle release.
func relaySchemaV7CanonicalBytes() []byte {
	var builder strings.Builder
	builder.WriteString("ai-video/new-api-relay/schema/v7\n")
	builder.WriteString("requires-v6-checksum|")
	builder.WriteString(relaySchemaV6FrozenChecksumSHA256)
	builder.WriteByte('\n')
	builder.WriteString("source-artifact|")
	builder.WriteString(relaySchemaV7SourceArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("model-artifact|")
	builder.WriteString(relaySchemaV7ModelArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("postgres-catalog|")
	builder.WriteString(relaySchemaV7PostgresCatalogSHA256)
	builder.WriteByte('\n')
	builder.WriteString("runtime-privilege-manifest|")
	builder.WriteString(relayRuntimeDatabasePrivilegeManifestV7SHA256)
	builder.WriteByte('\n')
	builder.WriteString("download-edge-privilege-manifest|")
	builder.WriteString(relayDownloadEdgeDatabasePrivilegeManifestV7SHA256)
	builder.WriteByte('\n')
	for _, step := range relaySchemaV7BootstrapSteps() {
		builder.WriteString("bootstrap-step|")
		builder.WriteString(step.ID)
		builder.WriteByte('\n')
	}
	builder.WriteString("incremental-step|channel-test-artifact-and-personal-cost-scope-v7\n")
	return []byte(builder.String())
}

// relaySchemaV8CanonicalBytes binds the exact provider cost rate-set, terminal
// usage, FX conversion and final cent rounding evidence to the immutable v7
// release without changing any historical schema identity.
func relaySchemaV8CanonicalBytes() []byte {
	var builder strings.Builder
	builder.WriteString("ai-video/new-api-relay/schema/v8\n")
	builder.WriteString("requires-v7-checksum|")
	builder.WriteString(relaySchemaV7FrozenChecksumSHA256)
	builder.WriteByte('\n')
	builder.WriteString("source-artifact|")
	builder.WriteString(relaySchemaV8SourceArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("model-artifact|")
	builder.WriteString(relaySchemaV8ModelArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("postgres-catalog|")
	builder.WriteString(relaySchemaV8PostgresCatalogSHA256)
	builder.WriteByte('\n')
	builder.WriteString("runtime-privilege-manifest|")
	builder.WriteString(relayRuntimeDatabasePrivilegeManifestV8SHA256)
	builder.WriteByte('\n')
	builder.WriteString("download-edge-privilege-manifest|")
	builder.WriteString(relayDownloadEdgeDatabasePrivilegeManifestV8SHA256)
	builder.WriteByte('\n')
	for _, step := range relaySchemaV8BootstrapSteps() {
		builder.WriteString("bootstrap-step|")
		builder.WriteString(step.ID)
		builder.WriteByte('\n')
	}
	builder.WriteString("incremental-step|provider-cost-allocation-evidence-v8\n")
	return []byte(builder.String())
}

// relaySchemaV1CanonicalBytes is the immutable executable v1 identity. The
// ordered step registry drives both execution and this manifest; the two
// frozen digests are independently verified from source by tests.
func relaySchemaV1CanonicalBytes() []byte {
	var builder strings.Builder
	builder.WriteString("ai-video/new-api-relay/schema/v1\n")
	builder.WriteString("source-artifact|")
	builder.WriteString(relaySchemaV1SourceArtifactSHA256)
	builder.WriteByte('\n')
	builder.WriteString("model-artifact|")
	builder.WriteString(relaySchemaV1ModelArtifactSHA256)
	builder.WriteByte('\n')
	for _, step := range relaySchemaV1Steps() {
		builder.WriteString("step|")
		builder.WriteString(step.ID)
		builder.WriteByte('\n')
	}
	return []byte(builder.String())
}

// relaySchemaV1LiveModelManifestBytes is used only by the artifact freeze
// check. Runtime schema identity uses the frozen digest above.
func relaySchemaCanonicalChannelCostArtifactType(typeOf reflect.Type) (string, string, bool) {
	for typeOf.Kind() == reflect.Pointer || typeOf.Kind() == reflect.Slice || typeOf.Kind() == reflect.Array {
		typeOf = typeOf.Elem()
	}
	if typeOf != reflect.TypeOf(platformChannelCostEventArtifactV6{}) &&
		typeOf != reflect.TypeOf(platformChannelCostEventArtifactV7{}) {
		return "", "", false
	}
	liveType := reflect.TypeOf(PlatformChannelCostEvent{})
	return liveType.PkgPath(), liveType.Name(), true
}

func relaySchemaCanonicalChannelCostArtifactModelName(typeOf reflect.Type) string {
	baseType := typeOf
	for baseType.Kind() == reflect.Pointer || baseType.Kind() == reflect.Slice || baseType.Kind() == reflect.Array {
		baseType = baseType.Elem()
	}
	if _, _, pinned := relaySchemaCanonicalChannelCostArtifactType(baseType); pinned {
		return reflect.TypeOf(&PlatformChannelCostEvent{}).String()
	}
	return typeOf.String()
}

func relaySchemaWriteCanonicalChannelCostArtifactType(builder *strings.Builder, typeOf reflect.Type) {
	if packagePath, name, pinned := relaySchemaCanonicalChannelCostArtifactType(typeOf); pinned {
		builder.WriteString(packagePath)
		builder.WriteByte('|')
		builder.WriteString(name)
		builder.WriteByte('\n')
		return
	}
	builder.WriteString(typeOf.PkgPath())
	builder.WriteByte('|')
	builder.WriteString(typeOf.Name())
	builder.WriteByte('\n')
}

// taskPrivateDataArtifactV7 freezes the JSON-backed task projection that was
// part of every schema model artifact through v7. Provider transport binding
// was added to the live task record for v8-era runtime safety; allowing those
// fields to leak into a historical reflection walk would silently rewrite the
// already released v1-v7 model identities.
type taskPrivateDataArtifactV7 struct {
	PinnedKeyIndex                  *int                `json:"pinned_key_index,omitempty"`
	PinnedKeyFingerprint            string              `json:"pinned_key_fingerprint,omitempty"`
	ProviderCredentialTenantID      string              `json:"provider_credential_tenant_id,omitempty"`
	ProviderCredentialVersion       string              `json:"provider_credential_version,omitempty"`
	ProviderResultURLScrubbed       bool                `json:"provider_result_url_scrubbed,omitempty"`
	TransientProviderKey            string              `json:"-"`
	LegacyProviderCredentialPresent bool                `json:"-"`
	UpstreamTaskID                  string              `json:"upstream_task_id,omitempty"`
	ResultURL                       string              `json:"result_url,omitempty"`
	BillingSource                   string              `json:"billing_source,omitempty"`
	SubscriptionId                  int                 `json:"subscription_id,omitempty"`
	TokenId                         int                 `json:"token_id,omitempty"`
	NodeName                        string              `json:"node_name,omitempty"`
	BillingContext                  *TaskBillingContext `json:"billing_context,omitempty"`
}

func relaySchemaPinHistoricalTaskPrivateDataArtifact(typeOf reflect.Type) reflect.Type {
	if typeOf == reflect.TypeOf(TaskPrivateData{}) {
		return reflect.TypeOf(taskPrivateDataArtifactV7{})
	}
	return typeOf
}

func relaySchemaWriteCanonicalHistoricalArtifactType(builder *strings.Builder, typeOf reflect.Type) {
	if typeOf == reflect.TypeOf(taskPrivateDataArtifactV7{}) {
		liveType := reflect.TypeOf(TaskPrivateData{})
		builder.WriteString(liveType.PkgPath())
		builder.WriteByte('|')
		builder.WriteString(liveType.Name())
		builder.WriteByte('\n')
		return
	}
	relaySchemaWriteCanonicalChannelCostArtifactType(builder, typeOf)
}

func relaySchemaV1LiveModelManifestBytes() []byte {
	var builder strings.Builder
	seen := make(map[reflect.Type]bool)
	var writeType func(reflect.Type)
	writeType = func(typeOf reflect.Type) {
		for typeOf.Kind() == reflect.Pointer || typeOf.Kind() == reflect.Slice || typeOf.Kind() == reflect.Array {
			typeOf = typeOf.Elem()
		}
		typeOf = relaySchemaPinHistoricalTaskPrivateDataArtifact(typeOf)
		if typeOf.Kind() != reflect.Struct || typeOf.PkgPath() == "time" || seen[typeOf] {
			return
		}
		seen[typeOf] = true
		builder.WriteString("type|")
		relaySchemaWriteCanonicalHistoricalArtifactType(&builder, typeOf)
		for index := 0; index < typeOf.NumField(); index++ {
			field := typeOf.Field(index)
			builder.WriteString("field|")
			builder.WriteString(field.Name)
			builder.WriteByte('|')
			builder.WriteString(field.Type.String())
			builder.WriteByte('|')
			builder.WriteString(string(field.Tag))
			builder.WriteByte('\n')
			writeType(field.Type)
		}
	}
	for _, value := range relaySchemaV1ArtifactModels() {
		typeOf := reflect.TypeOf(value)
		builder.WriteString("model|")
		builder.WriteString(relaySchemaCanonicalChannelCostArtifactModelName(typeOf))
		if table, ok := value.(interface{ TableName() string }); ok {
			builder.WriteByte('|')
			builder.WriteString(table.TableName())
		}
		builder.WriteByte('\n')
		writeType(typeOf)
	}
	return []byte(builder.String())
}

// relaySchemaV2LiveModelManifestBytes is the independent fresh-v2 model
// snapshot. It deliberately does not call the historical v1 manifest helper.
func relaySchemaV2LiveModelManifestBytes() []byte {
	var builder strings.Builder
	seen := make(map[reflect.Type]bool)
	var writeType func(reflect.Type)
	writeType = func(typeOf reflect.Type) {
		for typeOf.Kind() == reflect.Pointer || typeOf.Kind() == reflect.Slice || typeOf.Kind() == reflect.Array {
			typeOf = typeOf.Elem()
		}
		typeOf = relaySchemaPinHistoricalTaskPrivateDataArtifact(typeOf)
		if typeOf.Kind() != reflect.Struct || typeOf.PkgPath() == "time" || seen[typeOf] {
			return
		}
		seen[typeOf] = true
		builder.WriteString("type|")
		relaySchemaWriteCanonicalHistoricalArtifactType(&builder, typeOf)
		for index := 0; index < typeOf.NumField(); index++ {
			field := typeOf.Field(index)
			builder.WriteString("field|")
			builder.WriteString(field.Name)
			builder.WriteByte('|')
			builder.WriteString(field.Type.String())
			builder.WriteByte('|')
			builder.WriteString(string(field.Tag))
			builder.WriteByte('\n')
			writeType(field.Type)
		}
	}
	for _, value := range relaySchemaV2ArtifactModels() {
		typeOf := reflect.TypeOf(value)
		builder.WriteString("model|")
		builder.WriteString(relaySchemaCanonicalChannelCostArtifactModelName(typeOf))
		if table, ok := value.(interface{ TableName() string }); ok {
			builder.WriteByte('|')
			builder.WriteString(table.TableName())
		}
		builder.WriteByte('\n')
		writeType(typeOf)
	}
	return []byte(builder.String())
}

// relaySchemaV3LiveModelManifestBytes is the independent fresh-v3 model
// snapshot used by the current-version artifact freeze test.
func relaySchemaV3LiveModelManifestBytes() []byte {
	var builder strings.Builder
	seen := make(map[reflect.Type]bool)
	var writeType func(reflect.Type)
	writeType = func(typeOf reflect.Type) {
		for typeOf.Kind() == reflect.Pointer || typeOf.Kind() == reflect.Slice || typeOf.Kind() == reflect.Array {
			typeOf = typeOf.Elem()
		}
		typeOf = relaySchemaPinHistoricalTaskPrivateDataArtifact(typeOf)
		if typeOf.Kind() != reflect.Struct || typeOf.PkgPath() == "time" || seen[typeOf] {
			return
		}
		seen[typeOf] = true
		builder.WriteString("type|")
		relaySchemaWriteCanonicalHistoricalArtifactType(&builder, typeOf)
		for index := 0; index < typeOf.NumField(); index++ {
			field := typeOf.Field(index)
			builder.WriteString("field|")
			builder.WriteString(field.Name)
			builder.WriteByte('|')
			builder.WriteString(field.Type.String())
			builder.WriteByte('|')
			builder.WriteString(string(field.Tag))
			builder.WriteByte('\n')
			writeType(field.Type)
		}
	}
	for _, value := range relaySchemaV3ArtifactModels() {
		typeOf := reflect.TypeOf(value)
		builder.WriteString("model|")
		builder.WriteString(relaySchemaCanonicalChannelCostArtifactModelName(typeOf))
		if table, ok := value.(interface{ TableName() string }); ok {
			builder.WriteByte('|')
			builder.WriteString(table.TableName())
		}
		builder.WriteByte('\n')
		writeType(typeOf)
	}
	return []byte(builder.String())
}

// relaySchemaV4LiveModelManifestBytes is the independent fresh-v4 model
// snapshot. It is the only live model artifact validated by the v4 image;
// v1-v3 remain identified solely by their frozen digests.
func relaySchemaV4LiveModelManifestBytes() []byte {
	var builder strings.Builder
	seen := make(map[reflect.Type]bool)
	var writeType func(reflect.Type)
	writeType = func(typeOf reflect.Type) {
		for typeOf.Kind() == reflect.Pointer || typeOf.Kind() == reflect.Slice || typeOf.Kind() == reflect.Array {
			typeOf = typeOf.Elem()
		}
		typeOf = relaySchemaPinHistoricalTaskPrivateDataArtifact(typeOf)
		if typeOf.Kind() != reflect.Struct || typeOf.PkgPath() == "time" || seen[typeOf] {
			return
		}
		seen[typeOf] = true
		builder.WriteString("type|")
		relaySchemaWriteCanonicalHistoricalArtifactType(&builder, typeOf)
		for index := 0; index < typeOf.NumField(); index++ {
			field := typeOf.Field(index)
			builder.WriteString("field|")
			builder.WriteString(field.Name)
			builder.WriteByte('|')
			builder.WriteString(field.Type.String())
			builder.WriteByte('|')
			builder.WriteString(string(field.Tag))
			builder.WriteByte('\n')
			writeType(field.Type)
		}
	}
	for _, value := range relaySchemaV4ArtifactModels() {
		typeOf := reflect.TypeOf(value)
		builder.WriteString("model|")
		builder.WriteString(relaySchemaCanonicalChannelCostArtifactModelName(typeOf))
		if table, ok := value.(interface{ TableName() string }); ok {
			builder.WriteByte('|')
			builder.WriteString(table.TableName())
		}
		builder.WriteByte('\n')
		writeType(typeOf)
	}
	return []byte(builder.String())
}

// relaySchemaV5LiveModelManifestBytes is the independent fresh-v5 model
// snapshot. V5 changes a guard function rather than a table, but retaining an
// explicit model artifact proves that the migration did not silently absorb a
// concurrent GORM model change.
func relaySchemaV5LiveModelManifestBytes() []byte {
	var builder strings.Builder
	seen := make(map[reflect.Type]bool)
	var writeType func(reflect.Type, bool)
	writeType = func(typeOf reflect.Type, pinnedV5Operation bool) {
		for typeOf.Kind() == reflect.Pointer || typeOf.Kind() == reflect.Slice || typeOf.Kind() == reflect.Array {
			typeOf = typeOf.Elem()
		}
		typeOf = relaySchemaPinHistoricalTaskPrivateDataArtifact(typeOf)
		if typeOf.Kind() != reflect.Struct || typeOf.PkgPath() == "time" || seen[typeOf] {
			return
		}
		seen[typeOf] = true
		builder.WriteString("type|")
		if pinnedV5Operation {
			builder.WriteString(reflect.TypeOf(PlatformChannelControlOperation{}).PkgPath())
			builder.WriteString("|PlatformChannelControlOperation\n")
		} else {
			relaySchemaWriteCanonicalHistoricalArtifactType(&builder, typeOf)
		}
		for index := 0; index < typeOf.NumField(); index++ {
			field := typeOf.Field(index)
			builder.WriteString("field|")
			builder.WriteString(field.Name)
			builder.WriteByte('|')
			builder.WriteString(field.Type.String())
			builder.WriteByte('|')
			builder.WriteString(string(field.Tag))
			builder.WriteByte('\n')
			writeType(field.Type, false)
		}
	}
	pinnedV5OperationType := reflect.TypeOf(&platformChannelControlOperationArtifactV5{})
	for _, value := range relaySchemaV5ArtifactModels() {
		typeOf := reflect.TypeOf(value)
		builder.WriteString("model|")
		if typeOf == pinnedV5OperationType {
			builder.WriteString("*model.PlatformChannelControlOperation")
		} else {
			builder.WriteString(relaySchemaCanonicalChannelCostArtifactModelName(typeOf))
		}
		if table, ok := value.(interface{ TableName() string }); ok {
			builder.WriteByte('|')
			builder.WriteString(table.TableName())
		}
		builder.WriteByte('\n')
		writeType(typeOf, typeOf == pinnedV5OperationType)
	}
	return []byte(builder.String())
}

// relaySchemaV6LiveModelManifestBytes is the independently frozen fresh-v6
// model snapshot. Unlike v5, it intentionally includes the current
// PlatformChannelControlOperation lifecycle projection.
func relaySchemaV6LiveModelManifestBytes() []byte {
	var builder strings.Builder
	seen := make(map[reflect.Type]bool)
	var writeType func(reflect.Type)
	writeType = func(typeOf reflect.Type) {
		for typeOf.Kind() == reflect.Pointer || typeOf.Kind() == reflect.Slice || typeOf.Kind() == reflect.Array {
			typeOf = typeOf.Elem()
		}
		typeOf = relaySchemaPinHistoricalTaskPrivateDataArtifact(typeOf)
		if typeOf.Kind() != reflect.Struct || typeOf.PkgPath() == "time" || seen[typeOf] {
			return
		}
		seen[typeOf] = true
		builder.WriteString("type|")
		relaySchemaWriteCanonicalHistoricalArtifactType(&builder, typeOf)
		for index := 0; index < typeOf.NumField(); index++ {
			field := typeOf.Field(index)
			builder.WriteString("field|")
			builder.WriteString(field.Name)
			builder.WriteByte('|')
			builder.WriteString(field.Type.String())
			builder.WriteByte('|')
			builder.WriteString(string(field.Tag))
			builder.WriteByte('\n')
			writeType(field.Type)
		}
	}
	for _, value := range relaySchemaV6ArtifactModels() {
		typeOf := reflect.TypeOf(value)
		builder.WriteString("model|")
		builder.WriteString(relaySchemaCanonicalChannelCostArtifactModelName(typeOf))
		if table, ok := value.(interface{ TableName() string }); ok {
			builder.WriteByte('|')
			builder.WriteString(table.TableName())
		}
		builder.WriteByte('\n')
		writeType(typeOf)
	}
	return []byte(builder.String())
}

// V7 retains the v6 model registry membership while freezing the explicitly
// migrated personal channel-cost scope in its own artifact projection.
func relaySchemaV7LiveModelManifestBytes() []byte {
	var builder strings.Builder
	seen := make(map[reflect.Type]bool)
	var writeType func(reflect.Type)
	writeType = func(typeOf reflect.Type) {
		for typeOf.Kind() == reflect.Pointer || typeOf.Kind() == reflect.Slice || typeOf.Kind() == reflect.Array {
			typeOf = typeOf.Elem()
		}
		typeOf = relaySchemaPinHistoricalTaskPrivateDataArtifact(typeOf)
		if typeOf.Kind() != reflect.Struct || typeOf.PkgPath() == "time" || seen[typeOf] {
			return
		}
		seen[typeOf] = true
		builder.WriteString("type|")
		relaySchemaWriteCanonicalHistoricalArtifactType(&builder, typeOf)
		for index := 0; index < typeOf.NumField(); index++ {
			field := typeOf.Field(index)
			builder.WriteString("field|")
			builder.WriteString(field.Name)
			builder.WriteByte('|')
			builder.WriteString(field.Type.String())
			builder.WriteByte('|')
			builder.WriteString(string(field.Tag))
			builder.WriteByte('\n')
			writeType(field.Type)
		}
	}
	for _, value := range relaySchemaV7ArtifactModels() {
		typeOf := reflect.TypeOf(value)
		builder.WriteString("model|")
		builder.WriteString(relaySchemaCanonicalChannelCostArtifactModelName(typeOf))
		if table, ok := value.(interface{ TableName() string }); ok {
			builder.WriteByte('|')
			builder.WriteString(table.TableName())
		}
		builder.WriteByte('\n')
		writeType(typeOf)
	}
	return []byte(builder.String())
}

// V8 keeps the entire v7 model projection frozen and adds only the immutable
// provider cost allocation evidence model.
func relaySchemaV8LiveModelManifestBytes() []byte {
	var builder strings.Builder
	seen := make(map[reflect.Type]bool)
	var writeType func(reflect.Type)
	writeType = func(typeOf reflect.Type) {
		for typeOf.Kind() == reflect.Pointer || typeOf.Kind() == reflect.Slice || typeOf.Kind() == reflect.Array {
			typeOf = typeOf.Elem()
		}
		if typeOf.Kind() != reflect.Struct || typeOf.PkgPath() == "time" || seen[typeOf] {
			return
		}
		seen[typeOf] = true
		builder.WriteString("type|")
		relaySchemaWriteCanonicalChannelCostArtifactType(&builder, typeOf)
		for index := 0; index < typeOf.NumField(); index++ {
			field := typeOf.Field(index)
			builder.WriteString("field|")
			builder.WriteString(field.Name)
			builder.WriteByte('|')
			builder.WriteString(field.Type.String())
			builder.WriteByte('|')
			builder.WriteString(string(field.Tag))
			builder.WriteByte('\n')
			writeType(field.Type)
		}
	}
	for _, value := range relaySchemaV8ArtifactModels() {
		typeOf := reflect.TypeOf(value)
		builder.WriteString("model|")
		builder.WriteString(relaySchemaCanonicalChannelCostArtifactModelName(typeOf))
		if table, ok := value.(interface{ TableName() string }); ok {
			builder.WriteByte('|')
			builder.WriteString(table.TableName())
		}
		builder.WriteByte('\n')
		writeType(typeOf)
	}
	return []byte(builder.String())
}

func relaySchemaPinChannelCostArtifact(models []any, v7 bool) []any {
	pinned := append([]any(nil), models...)
	liveType := reflect.TypeOf(&PlatformChannelCostEvent{})
	v6Type := reflect.TypeOf(&platformChannelCostEventArtifactV6{})
	v7Type := reflect.TypeOf(&platformChannelCostEventArtifactV7{})
	var replacement any = &platformChannelCostEventArtifactV6{}
	if v7 {
		replacement = &platformChannelCostEventArtifactV7{}
	}
	for index, candidate := range pinned {
		typeOf := reflect.TypeOf(candidate)
		if typeOf == liveType || typeOf == v6Type || typeOf == v7Type {
			pinned[index] = replacement
		}
	}
	return pinned
}

func relaySchemaV1ArtifactModels() []any {
	models := append([]any{}, relaySchemaV1Models()...)
	models = append(models,
		&RelaySchemaState{}, &RelaySchemaMigration{}, &SubscriptionPlan{},
		&PlatformArtifactUploadIntent{}, &PlatformGenerationReconciliationEvent{},
		&PlatformGenerationCallbackRedriveEvent{}, &platformChannelControlOperationArtifactV5{},
	)
	models = append(models, PlatformProviderMonitorAndCostModels()...)
	models = relaySchemaPinChannelCostArtifact(models, false)
	seen := make(map[reflect.Type]bool, len(models))
	unique := make([]any, 0, len(models))
	for _, model := range models {
		typeOf := reflect.TypeOf(model)
		if seen[typeOf] {
			continue
		}
		seen[typeOf] = true
		unique = append(unique, model)
	}
	return unique
}

func relaySchemaV2ArtifactModels() []any {
	models := append([]any{}, relaySchemaV2Models()...)
	models = append(models,
		&RelaySchemaState{}, &RelaySchemaMigration{}, &SubscriptionPlan{},
		&PlatformArtifactUploadIntent{}, &PlatformGenerationReconciliationEvent{},
		&PlatformGenerationCallbackRedriveEvent{}, &PlatformChannelControlOperation{},
	)
	models = append(models, PlatformProviderMonitorAndCostModels()...)
	models = relaySchemaPinChannelCostArtifact(models, false)
	seen := make(map[reflect.Type]bool, len(models))
	unique := make([]any, 0, len(models))
	for _, model := range models {
		typeOf := reflect.TypeOf(model)
		if seen[typeOf] {
			continue
		}
		seen[typeOf] = true
		unique = append(unique, model)
	}
	return unique
}

func relaySchemaV3ArtifactModels() []any {
	models := append([]any{}, relaySchemaV3Models()...)
	models = append(models,
		&RelaySchemaState{}, &RelaySchemaMigration{}, &SubscriptionPlan{},
		&PlatformArtifactUploadIntent{}, &PlatformGenerationReconciliationEvent{},
		&PlatformGenerationCallbackRedriveEvent{}, &PlatformChannelControlOperation{},
	)
	models = append(models, PlatformProviderMonitorAndCostModels()...)
	models = relaySchemaPinChannelCostArtifact(models, false)
	seen := make(map[reflect.Type]bool, len(models))
	unique := make([]any, 0, len(models))
	for _, model := range models {
		typeOf := reflect.TypeOf(model)
		if seen[typeOf] {
			continue
		}
		seen[typeOf] = true
		unique = append(unique, model)
	}
	return unique
}

func relaySchemaV4ArtifactModels() []any {
	models := append([]any{}, relaySchemaV4Models()...)
	models = append(models,
		&RelaySchemaState{}, &RelaySchemaMigration{}, &SubscriptionPlan{},
		&PlatformArtifactUploadIntent{}, &PlatformGenerationReconciliationEvent{},
		&PlatformGenerationCallbackRedriveEvent{}, &PlatformChannelControlOperation{},
	)
	models = append(models, PlatformProviderMonitorAndCostModels()...)
	models = relaySchemaPinChannelCostArtifact(models, false)
	seen := make(map[reflect.Type]bool, len(models))
	unique := make([]any, 0, len(models))
	for _, model := range models {
		typeOf := reflect.TypeOf(model)
		if seen[typeOf] {
			continue
		}
		seen[typeOf] = true
		unique = append(unique, model)
	}
	return unique
}

func relaySchemaV5ArtifactModels() []any {
	models := append([]any{}, relaySchemaV5Models()...)
	models = append(models,
		&RelaySchemaState{}, &RelaySchemaMigration{}, &SubscriptionPlan{},
		&PlatformArtifactUploadIntent{}, &PlatformGenerationReconciliationEvent{},
		&PlatformGenerationCallbackRedriveEvent{}, &platformChannelControlOperationArtifactV5{},
	)
	models = append(models, PlatformProviderMonitorAndCostModels()...)
	models = relaySchemaPinChannelCostArtifact(models, false)
	seen := make(map[reflect.Type]bool, len(models))
	unique := make([]any, 0, len(models))
	for _, model := range models {
		typeOf := reflect.TypeOf(model)
		if seen[typeOf] {
			continue
		}
		seen[typeOf] = true
		unique = append(unique, model)
	}
	return unique
}

func relaySchemaV6ArtifactModels() []any {
	models := append([]any{}, relaySchemaV6Models()...)
	models = append(models,
		&RelaySchemaState{}, &RelaySchemaMigration{}, &SubscriptionPlan{},
		&PlatformArtifactUploadIntent{}, &PlatformGenerationReconciliationEvent{},
		&PlatformGenerationCallbackRedriveEvent{}, &PlatformChannelControlOperation{},
	)
	models = append(models, PlatformProviderMonitorAndCostModels()...)
	models = relaySchemaPinChannelCostArtifact(models, false)
	seen := make(map[reflect.Type]bool, len(models))
	unique := make([]any, 0, len(models))
	for _, model := range models {
		typeOf := reflect.TypeOf(model)
		if seen[typeOf] {
			continue
		}
		seen[typeOf] = true
		unique = append(unique, model)
	}
	return unique
}

func relaySchemaV7ArtifactModels() []any {
	return relaySchemaPinChannelCostArtifact(relaySchemaV6ArtifactModels(), true)
}

func relaySchemaV8ArtifactModels() []any {
	models := append([]any{}, relaySchemaV7ArtifactModels()...)
	return append(models, &PlatformProviderCostAllocationEvidence{})
}

func relaySchemaV1Models() []any {
	models := []any{
		&Channel{}, &ProviderChannelCredentialSetVersion{}, &ProviderCredentialVersion{},
		&Token{}, &User{}, &UserSession{}, &AuthFlow{}, &ExternalIdentityClaim{},
		&PasskeyCredential{}, &Option{}, &Redemption{}, &Ability{}, &Log{},
		&Midjourney{}, &TopUp{}, &QuotaData{}, &Task{}, &Model{}, &Vendor{},
		&PrefillGroup{}, &Setup{}, &TwoFA{}, &TwoFABackupCode{}, &Checkin{},
		&SubscriptionOrder{}, &UserSubscription{}, &SubscriptionPreConsumeRecord{},
		&CustomOAuthProvider{}, &UserOAuthBinding{}, &PerfMetric{}, &SystemInstance{},
		&SystemTask{}, &SystemTaskLock{}, &PlatformGenerationJob{},
		&PlatformGenerationOutbox{}, &PlatformGenerationProviderAccountState{},
		&PlatformGenerationProviderRoute{}, &PlatformGenerationRouteAdmission{},
		&PlatformGenerationCallbackDelivery{}, &PlatformProviderMonitorLease{},
		&PlatformProviderRouteHealth{}, &PlatformProviderTerminalOutcome{},
		&PlatformProviderIncident{}, &PlatformProviderAlertEvent{},
		&PlatformProviderRetirementAcknowledgement{}, &PlatformChannelCostEvent{},
		&PlatformRelayExternalDelivery{}, &CasbinRule{}, &AuthzRole{},
	}
	return models
}

// relaySchemaV2Models is a standalone fresh-bootstrap snapshot. The list is
// intentionally repeated instead of delegating to relaySchemaV1Models so a
// future version never reinterprets or executes the historical v1 snapshot.
func relaySchemaV2Models() []any {
	models := []any{
		&Channel{}, &ProviderChannelCredentialSetVersion{}, &ProviderCredentialVersion{},
		&Token{}, &User{}, &UserSession{}, &AuthFlow{}, &ExternalIdentityClaim{},
		&PasskeyCredential{}, &Option{}, &Redemption{}, &Ability{}, &Log{},
		&Midjourney{}, &TopUp{}, &QuotaData{}, &Task{}, &Model{}, &Vendor{},
		&PrefillGroup{}, &Setup{}, &TwoFA{}, &TwoFABackupCode{}, &Checkin{},
		&SubscriptionOrder{}, &UserSubscription{}, &SubscriptionPreConsumeRecord{},
		&CustomOAuthProvider{}, &UserOAuthBinding{}, &PerfMetric{}, &SystemInstance{},
		&SystemTask{}, &SystemTaskLock{}, &PlatformGenerationJob{},
		&PlatformGenerationOutbox{}, &PlatformGenerationProviderAccountState{},
		&PlatformGenerationProviderRoute{}, &PlatformGenerationRouteAdmission{},
		&PlatformGenerationCallbackDelivery{}, &PlatformProviderMonitorLease{},
		&PlatformProviderRouteHealth{}, &PlatformProviderTerminalOutcome{},
		&PlatformProviderIncident{}, &PlatformProviderAlertEvent{},
		&PlatformProviderRetirementAcknowledgement{}, &PlatformChannelCostEvent{},
		&PlatformRelayExternalDelivery{}, &CasbinRule{}, &AuthzRole{},
	}
	return models
}

// relaySchemaV3Models is a standalone fresh-bootstrap snapshot. It is
// intentionally independent of both historical model registries.
func relaySchemaV3Models() []any {
	models := []any{
		&Channel{}, &ProviderChannelCredentialSetVersion{}, &ProviderCredentialVersion{},
		&Token{}, &User{}, &UserSession{}, &AuthFlow{}, &ExternalIdentityClaim{},
		&PasskeyCredential{}, &Option{}, &Redemption{}, &Ability{}, &Log{},
		&Midjourney{}, &TopUp{}, &QuotaData{}, &Task{}, &Model{}, &Vendor{},
		&PrefillGroup{}, &Setup{}, &TwoFA{}, &TwoFABackupCode{}, &Checkin{},
		&SubscriptionOrder{}, &UserSubscription{}, &SubscriptionPreConsumeRecord{},
		&CustomOAuthProvider{}, &UserOAuthBinding{}, &PerfMetric{}, &SystemInstance{},
		&SystemTask{}, &SystemTaskLock{}, &PlatformGenerationJob{},
		&PlatformGenerationOutbox{}, &PlatformGenerationProviderAccountState{},
		&PlatformGenerationProviderRoute{}, &PlatformGenerationRouteAdmission{},
		&PlatformGenerationCallbackDelivery{}, &PlatformProviderMonitorLease{},
		&PlatformProviderRouteHealth{}, &PlatformProviderTerminalOutcome{},
		&PlatformProviderIncident{}, &PlatformProviderAlertEvent{},
		&PlatformProviderRetirementAcknowledgement{}, &PlatformChannelCostEvent{},
		&PlatformRelayExternalDelivery{}, &CasbinRule{}, &AuthzRole{},
	}
	return models
}

// relaySchemaV4Models is the complete standalone bootstrap registry for the
// route-bound adapter-profile/model-release release. It is intentionally
// independent of every historical model registry.
func relaySchemaV4Models() []any {
	models := []any{
		&Channel{}, &ProviderChannelCredentialSetVersion{}, &ProviderCredentialVersion{},
		&Token{}, &User{}, &UserSession{}, &AuthFlow{}, &ExternalIdentityClaim{},
		&PasskeyCredential{}, &Option{}, &Redemption{}, &Ability{}, &Log{},
		&Midjourney{}, &TopUp{}, &QuotaData{}, &Task{}, &Model{}, &Vendor{},
		&PrefillGroup{}, &Setup{}, &TwoFA{}, &TwoFABackupCode{}, &Checkin{},
		&SubscriptionOrder{}, &UserSubscription{}, &SubscriptionPreConsumeRecord{},
		&CustomOAuthProvider{}, &UserOAuthBinding{}, &PerfMetric{}, &SystemInstance{},
		&SystemTask{}, &SystemTaskLock{}, &PlatformGenerationJob{},
		&PlatformGenerationOutbox{}, &PlatformGenerationProviderAccountState{},
		&PlatformGenerationProviderRoute{}, &PlatformGenerationRouteAdmission{},
		&PlatformGenerationCallbackDelivery{}, &PlatformProviderMonitorLease{},
		&PlatformProviderRouteHealth{}, &PlatformProviderTerminalOutcome{},
		&PlatformProviderIncident{}, &PlatformProviderAlertEvent{},
		&PlatformProviderRetirementAcknowledgement{}, &PlatformChannelCostEvent{},
		&PlatformRelayExternalDelivery{}, &CasbinRule{}, &AuthzRole{},
	}
	return models
}

// relaySchemaV5Models is the complete standalone bootstrap registry for the
// diagnostic-taxonomy release. It is deliberately independent of every
// historical model registry even though this release adds no table column.
func relaySchemaV5Models() []any {
	models := []any{
		&Channel{}, &ProviderChannelCredentialSetVersion{}, &ProviderCredentialVersion{},
		&Token{}, &User{}, &UserSession{}, &AuthFlow{}, &ExternalIdentityClaim{},
		&PasskeyCredential{}, &Option{}, &Redemption{}, &Ability{}, &Log{},
		&Midjourney{}, &TopUp{}, &QuotaData{}, &Task{}, &Model{}, &Vendor{},
		&PrefillGroup{}, &Setup{}, &TwoFA{}, &TwoFABackupCode{}, &Checkin{},
		&SubscriptionOrder{}, &UserSubscription{}, &SubscriptionPreConsumeRecord{},
		&CustomOAuthProvider{}, &UserOAuthBinding{}, &PerfMetric{}, &SystemInstance{},
		&SystemTask{}, &SystemTaskLock{}, &PlatformGenerationJob{},
		&PlatformGenerationOutbox{}, &PlatformGenerationProviderAccountState{},
		&PlatformGenerationProviderRoute{}, &PlatformGenerationRouteAdmission{},
		&PlatformGenerationCallbackDelivery{}, &PlatformProviderMonitorLease{},
		&PlatformProviderRouteHealth{}, &PlatformProviderTerminalOutcome{},
		&PlatformProviderIncident{}, &PlatformProviderAlertEvent{},
		&PlatformProviderRetirementAcknowledgement{}, &PlatformChannelCostEvent{},
		&PlatformRelayExternalDelivery{}, &CasbinRule{}, &AuthzRole{},
	}
	return models
}

// relaySchemaV6Models is the complete standalone bootstrap registry for the
// durable channel-test lifecycle release. It is intentionally repeated rather
// than delegating to a historical registry.
func relaySchemaV6Models() []any {
	models := []any{
		&Channel{}, &ProviderChannelCredentialSetVersion{}, &ProviderCredentialVersion{},
		&Token{}, &User{}, &UserSession{}, &AuthFlow{}, &ExternalIdentityClaim{},
		&PasskeyCredential{}, &Option{}, &Redemption{}, &Ability{}, &Log{},
		&Midjourney{}, &TopUp{}, &QuotaData{}, &Task{}, &Model{}, &Vendor{},
		&PrefillGroup{}, &Setup{}, &TwoFA{}, &TwoFABackupCode{}, &Checkin{},
		&SubscriptionOrder{}, &UserSubscription{}, &SubscriptionPreConsumeRecord{},
		&CustomOAuthProvider{}, &UserOAuthBinding{}, &PerfMetric{}, &SystemInstance{},
		&SystemTask{}, &SystemTaskLock{}, &PlatformGenerationJob{},
		&PlatformGenerationOutbox{}, &PlatformGenerationProviderAccountState{},
		&PlatformGenerationProviderRoute{}, &PlatformGenerationRouteAdmission{},
		&PlatformGenerationCallbackDelivery{}, &PlatformProviderMonitorLease{},
		&PlatformProviderRouteHealth{}, &PlatformProviderTerminalOutcome{},
		&PlatformProviderIncident{}, &PlatformProviderAlertEvent{},
		&PlatformProviderRetirementAcknowledgement{}, &PlatformChannelCostEvent{},
		&PlatformRelayExternalDelivery{}, &CasbinRule{}, &AuthzRole{},
	}
	return models
}

// relaySchemaV7Models remains an explicit release registry entry. The v7-only
// channel-cost field is excluded from AutoMigrate and added by its own step.
func relaySchemaV7Models() []any {
	return relaySchemaV6Models()
}

// relaySchemaV8Models is the complete base snapshot before the v8-owned cost
// evidence table is created by its explicit migration step.
func relaySchemaV8Models() []any {
	return relaySchemaV7Models()
}

func GetRelaySchemaContract() RelaySchemaContract {
	return RelaySchemaContract{
		TargetVersion: RelaySchemaTargetVersion,
		MinVersion:    RelaySchemaMinVersion,
		MaxVersion:    RelaySchemaMaxVersion,
		Checksums: map[int64]string{
			1: RelaySchemaV1Checksum(),
			2: RelaySchemaV2Checksum(),
			3: RelaySchemaV3Checksum(),
			4: RelaySchemaV4Checksum(),
			5: RelaySchemaV5Checksum(),
			6: RelaySchemaV6Checksum(),
			7: RelaySchemaV7Checksum(),
			8: RelaySchemaV8Checksum(),
		},
	}
}

// These package-private providers are an integration seam for exercising a
// future bridge contract against a real database without changing the frozen
// production v1 constants. Production never mutates them; version-evolution
// tests replace the complete set together and restore it before returning.
var relaySchemaContractForRuntime = GetRelaySchemaContract
