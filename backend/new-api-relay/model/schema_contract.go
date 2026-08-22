package model

import (
	"crypto/sha256"
	"fmt"
	"reflect"
	"strings"
)

const (
	RelaySchemaTargetVersion int64 = 3
	RelaySchemaMinVersion    int64 = 1
	RelaySchemaMaxVersion    int64 = 3

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

func relaySchemaV1LiveArtifactValidationRequired(targetVersion int64) bool {
	return targetVersion == 1
}

func relaySchemaV2LiveArtifactValidationRequired(targetVersion int64) bool {
	return targetVersion == 2
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
func relaySchemaV1LiveModelManifestBytes() []byte {
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
		builder.WriteString(typeOf.PkgPath())
		builder.WriteByte('|')
		builder.WriteString(typeOf.Name())
		builder.WriteByte('\n')
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
		builder.WriteString(typeOf.String())
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
		if typeOf.Kind() != reflect.Struct || typeOf.PkgPath() == "time" || seen[typeOf] {
			return
		}
		seen[typeOf] = true
		builder.WriteString("type|")
		builder.WriteString(typeOf.PkgPath())
		builder.WriteByte('|')
		builder.WriteString(typeOf.Name())
		builder.WriteByte('\n')
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
		builder.WriteString(typeOf.String())
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
		if typeOf.Kind() != reflect.Struct || typeOf.PkgPath() == "time" || seen[typeOf] {
			return
		}
		seen[typeOf] = true
		builder.WriteString("type|")
		builder.WriteString(typeOf.PkgPath())
		builder.WriteByte('|')
		builder.WriteString(typeOf.Name())
		builder.WriteByte('\n')
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
		builder.WriteString(typeOf.String())
		if table, ok := value.(interface{ TableName() string }); ok {
			builder.WriteByte('|')
			builder.WriteString(table.TableName())
		}
		builder.WriteByte('\n')
		writeType(typeOf)
	}
	return []byte(builder.String())
}

func relaySchemaV1ArtifactModels() []any {
	models := append([]any{}, relaySchemaV1Models()...)
	models = append(models,
		&RelaySchemaState{}, &RelaySchemaMigration{}, &SubscriptionPlan{},
		&PlatformArtifactUploadIntent{}, &PlatformGenerationReconciliationEvent{},
		&PlatformGenerationCallbackRedriveEvent{}, &PlatformChannelControlOperation{},
	)
	models = append(models, PlatformProviderMonitorAndCostModels()...)
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

func GetRelaySchemaContract() RelaySchemaContract {
	return RelaySchemaContract{
		TargetVersion: RelaySchemaTargetVersion,
		MinVersion:    RelaySchemaMinVersion,
		MaxVersion:    RelaySchemaMaxVersion,
		Checksums: map[int64]string{
			1: RelaySchemaV1Checksum(),
			2: RelaySchemaV2Checksum(),
			3: RelaySchemaV3Checksum(),
		},
	}
}

// These package-private providers are an integration seam for exercising a
// future bridge contract against a real database without changing the frozen
// production v1 constants. Production never mutates them; version-evolution
// tests replace the complete set together and restore it before returning.
var relaySchemaContractForRuntime = GetRelaySchemaContract
