package model

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"
	"unicode"

	"github.com/QuantumNous/new-api/common"
	"github.com/google/uuid"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

const (
	PlatformChannelControlOperationKindTest   = "test"
	PlatformChannelControlOperationKindStatus = "status"

	PlatformChannelControlOperationPending   = "pending"
	PlatformChannelControlOperationSucceeded = "succeeded"
	PlatformChannelControlOperationFailed    = "failed"

	PlatformChannelControlErrorTestFailed       = "CHANNEL_TEST_FAILED"
	PlatformChannelControlErrorTestUnavailable  = "CHANNEL_TEST_UNAVAILABLE"
	PlatformChannelControlErrorTestValidation   = "CHANNEL_TEST_PROVIDER_VALIDATION"
	PlatformChannelControlErrorTestAuth         = "CHANNEL_TEST_PROVIDER_AUTH"
	PlatformChannelControlErrorTestQuota        = "CHANNEL_TEST_PROVIDER_QUOTA"
	PlatformChannelControlErrorTestTerminal     = "CHANNEL_TEST_PROVIDER_TERMINAL"
	PlatformChannelControlErrorTestArtifact     = "CHANNEL_TEST_ARTIFACT_INVALID"
	PlatformChannelControlErrorTestRouteDrift   = "CHANNEL_TEST_ROUTE_DRIFT"
	PlatformChannelControlErrorTestReconciled   = "CHANNEL_TEST_RECONCILED_NO_CREATION"
	PlatformChannelControlErrorRevisionConflict = "CHANNEL_REVISION_CONFLICT"

	PlatformChannelTestSubmissionNotStarted           = "not_started"
	PlatformChannelTestSubmissionUnknown              = "submission_unknown"
	PlatformChannelTestSubmissionSubmitted            = "submitted"
	PlatformChannelTestSubmissionArtifactVerified     = "artifact_verified"
	PlatformChannelTestSubmissionProviderTerminal     = "provider_terminal"
	PlatformChannelTestSubmissionProviderRejected     = "provider_rejected"
	PlatformChannelTestSubmissionPreSubmitFailed      = "pre_submit_failed"
	PlatformChannelTestSubmissionReconciledNoCreation = "reconciled_no_creation"
)

var (
	ErrPlatformChannelControlOperationConflict  = errors.New("channel control operation conflicts with an existing operation")
	ErrPlatformChannelControlRevisionConflict   = errors.New("channel control expected revision does not match")
	ErrPlatformChannelControlOperationImmutable = errors.New("channel control operation intent and terminal receipt are immutable")
	ErrPlatformChannelTestRouteBlocked          = errors.New("channel test route has an unresolved provider submission")
	ErrPlatformChannelTestSubmissionUnknown     = errors.New("channel test provider submission outcome requires reconciliation")
	ErrPlatformChannelTestReconciliationInvalid = errors.New("channel test reconciliation confirmation is invalid")
)

const platformChannelControlTerminalTransitionKey = "platform_channel_control_terminal_transition"
const platformChannelControlSubmissionTransitionKey = "platform_channel_control_submission_transition"

var platformChannelControlProviderTaskIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$`)
var platformChannelTestReconciliationSecretPattern = regexp.MustCompile(
	`(?i)(bearer[[:space:]]+|authorization[[:space:]]*:|(?:api[_-]?key|access[_-]?key|secret[_-]?key|credential|token|key|password|secret|signature|sig)[[:space:]]*[:=]|[?&](?:x-amz-[^=[:space:]]*|x-obs-[^=[:space:]]*|x-goog-[^=[:space:]]*|x-cos-[^=[:space:]]*|x-oss-[^=[:space:]]*|signature|sig|token|key)[[:space:]]*=)`,
)

func isSafePlatformChannelTestReconciliationText(value string) bool {
	for _, character := range value {
		if unicode.IsControl(character) || unicode.In(character, unicode.Cf) {
			return false
		}
	}
	normalized := strings.ToLower(value)
	for _, forbidden := range []string{
		"http://", "https://", "://", "credential",
		"x-amz-", "x-obs-", "x-goog-", "x-cos-", "x-oss-",
	} {
		if strings.Contains(normalized, forbidden) {
			return false
		}
	}
	return !platformChannelTestReconciliationSecretPattern.MatchString(value)
}

// platformChannelControlGuardMigrationAdvisoryLock serializes PostgreSQL
// trigger replacement across independently starting Relay instances.
const platformChannelControlGuardMigrationAdvisoryLock int64 = 0x41564944454f0002

// platformChannelControlRevisionMigrationAdvisoryLock independently
// serializes installation of the Channel revision trigger. Keeping a
// dedicated lock avoids coupling it to receipt-guard maintenance.
const platformChannelControlRevisionMigrationAdvisoryLock int64 = 0x41564944454f0003

// platformChannelControlRelevantColumns is the server-owned definition of a
// Channel control-plane mutation. Observational/accounting columns such as
// test_time, response_time, balance, balance_updated_time, and used_quota are
// intentionally excluded so health polling and accounting do not invalidate
// an operator approval proof.
var platformChannelControlRelevantColumns = []string{
	"type",
	"credential_set_version",
	"open_ai_organization",
	"test_model",
	"status",
	"name",
	"weight",
	"base_url",
	"other",
	"models",
	"group",
	"model_mapping",
	"status_code_mapping",
	"priority",
	"auto_ban",
	"other_info",
	"tag",
	"setting",
	"param_override",
	"header_override",
	"remark",
	"channel_info",
	"settings",
}

type PlatformChannelControlIntent struct {
	OperationID                 string
	TenantID                    string
	ChannelID                   int
	Kind                        string
	RequestID                   string
	Actor                       string
	Reason                      string
	Model                       string
	EndpointType                string
	Stream                      bool
	PublicModelID               string
	RouteID                     string
	Mode                        string
	UpstreamModel               string
	CapabilityProfileID         string
	CapabilityProfileRevision   string
	ModelReleaseID              string
	ModelReleaseRevision        string
	CapabilityRevision          string
	RoutingReleaseSHA256        string
	RouteBindingSHA256          string
	CredentialFingerprintSHA256 string
	TransportRevision           string
	TransportSHA256             string
	ExpectedRevision            string
	TargetStatus                int
}

// PlatformChannelControlOperation is a durable, secret-free intent and result
// receipt. Provider credentials, URLs, settings, headers, proxy configuration,
// and raw provider errors must never be written to this table.
type PlatformChannelControlOperation struct {
	ID                          string     `json:"id" gorm:"type:varchar(36);primaryKey"`
	TenantID                    string     `json:"tenant_id" gorm:"type:varchar(36);not null;uniqueIndex:ux_platform_channel_control_operation,priority:1"`
	OperationID                 string     `json:"operation_id" gorm:"type:varchar(128);not null;uniqueIndex:ux_platform_channel_control_operation,priority:2"`
	ChannelID                   int        `json:"channel_id" gorm:"not null;index:idx_platform_channel_control_channel"`
	Kind                        string     `json:"kind" gorm:"type:varchar(16);not null"`
	State                       string     `json:"state" gorm:"type:varchar(16);not null;index"`
	RequestID                   string     `json:"request_id" gorm:"type:varchar(80);not null"`
	Actor                       string     `json:"actor" gorm:"type:varchar(128);not null"`
	Reason                      string     `json:"reason" gorm:"type:varchar(240);not null"`
	IntentSHA256                string     `json:"intent_sha256" gorm:"type:char(64);not null"`
	IntentJSON                  string     `json:"-" gorm:"type:text;not null"`
	IntentExpectedRevision      string     `json:"intent_expected_revision,omitempty" gorm:"type:varchar(72);not null;default:''"`
	IntentTargetStatus          string     `json:"intent_target_status,omitempty" gorm:"type:varchar(24);not null;default:''"`
	IntentPublicModelID         string     `json:"-" gorm:"type:varchar(128);not null;default:'';index:idx_platform_channel_control_route,priority:2"`
	IntentRouteID               string     `json:"-" gorm:"type:varchar(128);not null;default:'';index:idx_platform_channel_control_route,priority:3"`
	IntentTransportRevision     string     `json:"-" gorm:"type:varchar(72);not null;default:''"`
	IntentTransportSHA256       string     `json:"-" gorm:"type:varchar(72);not null;default:''"`
	ProviderSubmissionState     string     `json:"provider_submission_state,omitempty" gorm:"type:varchar(32);not null;default:'';index"`
	ProviderBlockerCode         string     `json:"provider_blocker_code,omitempty" gorm:"type:varchar(64);not null;default:''"`
	ProviderTaskID              string     `json:"-" gorm:"type:varchar(256);not null;default:''"`
	ProviderArtifactSHA256      string     `json:"-" gorm:"type:char(64);not null;default:''"`
	ProviderArtifactSizeBytes   int64      `json:"-" gorm:"not null;default:0"`
	ProviderArtifactContentType string     `json:"-" gorm:"type:varchar(64);not null;default:''"`
	ReconciliationActor         string     `json:"reconciliation_actor,omitempty" gorm:"type:varchar(128);not null;default:''"`
	ReconciliationReason        string     `json:"reconciliation_reason,omitempty" gorm:"type:varchar(240);not null;default:''"`
	ReconciledAt                *time.Time `json:"reconciled_at,omitempty"`
	PreviousRevision            string     `json:"previous_revision,omitempty" gorm:"type:varchar(72);not null;default:''"`
	ResultRevision              string     `json:"result_revision,omitempty" gorm:"type:varchar(72);not null;default:''"`
	ResultSuccess               *bool      `json:"result_success,omitempty"`
	ResultResponseMS            *int64     `json:"result_response_ms,omitempty"`
	ResultErrorCode             string     `json:"result_error_code,omitempty" gorm:"type:varchar(64);not null;default:''"`
	ResultPreviousStatus        string     `json:"result_previous_status,omitempty" gorm:"type:varchar(24);not null;default:''"`
	ResultCurrentStatus         string     `json:"result_current_status,omitempty" gorm:"type:varchar(24);not null;default:''"`
	ResultChanged               *bool      `json:"result_changed,omitempty"`
	CreatedAt                   time.Time  `json:"created_at" gorm:"not null;index"`
	CompletedAt                 *time.Time `json:"completed_at,omitempty"`
}

func (PlatformChannelControlOperation) TableName() string {
	return "platform_channel_control_operations"
}

// ListSuccessfulPlatformChannelTestsSince returns durable terminal receipts,
// never the observational Channel.TestTime field. Callers must still verify
// the canonical route binding in IntentJSON against the current release.
func ListSuccessfulPlatformChannelTestsSince(since time.Time) ([]PlatformChannelControlOperation, error) {
	receipts := make([]PlatformChannelControlOperation, 0)
	err := DB.Session(&gorm.Session{SkipHooks: true}).
		Where("kind = ? AND state = ? AND result_success = ? AND completed_at IS NOT NULL AND completed_at >= ?",
			PlatformChannelControlOperationKindTest,
			PlatformChannelControlOperationSucceeded,
			true,
			since.UTC(),
		).
		Order("completed_at DESC, id ASC").
		Find(&receipts).Error
	return receipts, err
}

func (*PlatformChannelControlOperation) BeforeDelete(*gorm.DB) error {
	return ErrPlatformChannelControlOperationImmutable
}

func (*PlatformChannelControlOperation) BeforeUpdate(tx *gorm.DB) error {
	terminal, _ := tx.Get(platformChannelControlTerminalTransitionKey)
	submission, _ := tx.Get(platformChannelControlSubmissionTransitionKey)
	if terminal != true && submission != true {
		return ErrPlatformChannelControlOperationImmutable
	}
	return nil
}

type platformChannelControlCanonicalIntent struct {
	OperationID                 string `json:"operation_id"`
	TenantID                    string `json:"tenant_id"`
	ChannelID                   int    `json:"channel_id"`
	Kind                        string `json:"kind"`
	Actor                       string `json:"actor"`
	Reason                      string `json:"reason"`
	Model                       string `json:"model,omitempty"`
	EndpointType                string `json:"endpoint_type,omitempty"`
	Stream                      bool   `json:"stream,omitempty"`
	PublicModelID               string `json:"public_model_id,omitempty"`
	RouteID                     string `json:"route_id,omitempty"`
	Mode                        string `json:"mode,omitempty"`
	UpstreamModel               string `json:"upstream_model,omitempty"`
	CapabilityProfileID         string `json:"capability_profile_id,omitempty"`
	CapabilityProfileRevision   string `json:"capability_profile_revision,omitempty"`
	ModelReleaseID              string `json:"model_release_id,omitempty"`
	ModelReleaseRevision        string `json:"model_release_revision,omitempty"`
	CapabilityRevision          string `json:"capability_revision,omitempty"`
	RoutingReleaseSHA256        string `json:"routing_release_sha256,omitempty"`
	RouteBindingSHA256          string `json:"route_binding_sha256,omitempty"`
	CredentialFingerprintSHA256 string `json:"credential_fingerprint_sha256,omitempty"`
	TransportRevision           string `json:"transport_revision,omitempty"`
	TransportSHA256             string `json:"transport_sha256,omitempty"`
	ExpectedRevision            string `json:"expected_revision,omitempty"`
	TargetStatus                int    `json:"target_status,omitempty"`
}

func validatePlatformChannelControlIntent(intent PlatformChannelControlIntent) error {
	if !platformGenerationOperationIDPattern.MatchString(intent.OperationID) ||
		!isCanonicalPlatformGenerationReconciliationUUID(intent.TenantID) ||
		intent.ChannelID <= 0 ||
		!platformGenerationRequestIDPattern.MatchString(intent.RequestID) ||
		strings.TrimSpace(intent.Actor) != intent.Actor || len(intent.Actor) < 1 || len(intent.Actor) > 128 ||
		strings.TrimSpace(intent.Reason) != intent.Reason || len(intent.Reason) < 3 || len(intent.Reason) > 240 {
		return fmt.Errorf("channel control intent is invalid")
	}
	switch intent.Kind {
	case PlatformChannelControlOperationKindTest:
		if strings.TrimSpace(intent.Model) != intent.Model || len(intent.Model) < 1 || len(intent.Model) > 128 ||
			intent.EndpointType != "" || intent.Stream || intent.ExpectedRevision != "" || intent.TargetStatus != 0 {
			return fmt.Errorf("channel test intent is invalid")
		}
		modelReleasePairValid := (intent.ModelReleaseID == "" && intent.ModelReleaseRevision == "") ||
			(strings.TrimSpace(intent.ModelReleaseID) == intent.ModelReleaseID && len(intent.ModelReleaseID) >= 1 && len(intent.ModelReleaseID) <= 128 &&
				platformGenerationProofTokenPattern.MatchString(intent.ModelReleaseRevision))
		transportPairValid := platformGenerationProofTokenPattern.MatchString(intent.TransportRevision) &&
			platformGenerationProofTokenPattern.MatchString(intent.TransportSHA256)
		if strings.TrimSpace(intent.PublicModelID) != intent.PublicModelID || len(intent.PublicModelID) < 1 || len(intent.PublicModelID) > 128 ||
			strings.TrimSpace(intent.RouteID) != intent.RouteID || len(intent.RouteID) < 1 || len(intent.RouteID) > 128 ||
			(intent.Mode != "" && intent.Mode != "text_to_image" && intent.Mode != "text_to_video" && intent.Mode != "image_to_video" && intent.Mode != "video_to_video") ||
			strings.TrimSpace(intent.UpstreamModel) != intent.UpstreamModel || len(intent.UpstreamModel) < 1 || len(intent.UpstreamModel) > 128 ||
			intent.Model != intent.UpstreamModel ||
			strings.TrimSpace(intent.CapabilityProfileID) != intent.CapabilityProfileID || len(intent.CapabilityProfileID) < 1 || len(intent.CapabilityProfileID) > 128 ||
			strings.TrimSpace(intent.CapabilityProfileRevision) != intent.CapabilityProfileRevision || len(intent.CapabilityProfileRevision) < 1 || len(intent.CapabilityProfileRevision) > 128 ||
			!modelReleasePairValid || !transportPairValid ||
			!platformGenerationProofTokenPattern.MatchString(intent.CapabilityRevision) ||
			!platformGenerationProofTokenPattern.MatchString(intent.RoutingReleaseSHA256) ||
			!platformGenerationProofTokenPattern.MatchString(intent.RouteBindingSHA256) ||
			len(intent.CredentialFingerprintSHA256) != sha256.Size*2 || strings.ToLower(intent.CredentialFingerprintSHA256) != intent.CredentialFingerprintSHA256 {
			return fmt.Errorf("channel test route binding is invalid")
		}
		if _, err := hex.DecodeString(intent.CredentialFingerprintSHA256); err != nil {
			return fmt.Errorf("channel test route binding is invalid")
		}
	case PlatformChannelControlOperationKindStatus:
		if intent.Model != "" || intent.EndpointType != "" || intent.Stream ||
			!isPlatformChannelControlRevision(intent.ExpectedRevision) ||
			(intent.TargetStatus != common.ChannelStatusEnabled && intent.TargetStatus != common.ChannelStatusManuallyDisabled) {
			return fmt.Errorf("channel status intent is invalid")
		}
	default:
		return fmt.Errorf("channel control operation kind is invalid")
	}
	return nil
}

func platformChannelControlIntentPayload(intent PlatformChannelControlIntent) (string, string, error) {
	payload, err := json.Marshal(platformChannelControlCanonicalIntent{
		OperationID:                 intent.OperationID,
		TenantID:                    intent.TenantID,
		ChannelID:                   intent.ChannelID,
		Kind:                        intent.Kind,
		Actor:                       intent.Actor,
		Reason:                      intent.Reason,
		Model:                       intent.Model,
		EndpointType:                intent.EndpointType,
		Stream:                      intent.Stream,
		PublicModelID:               intent.PublicModelID,
		RouteID:                     intent.RouteID,
		Mode:                        intent.Mode,
		UpstreamModel:               intent.UpstreamModel,
		CapabilityProfileID:         intent.CapabilityProfileID,
		CapabilityProfileRevision:   intent.CapabilityProfileRevision,
		ModelReleaseID:              intent.ModelReleaseID,
		ModelReleaseRevision:        intent.ModelReleaseRevision,
		CapabilityRevision:          intent.CapabilityRevision,
		RoutingReleaseSHA256:        intent.RoutingReleaseSHA256,
		RouteBindingSHA256:          intent.RouteBindingSHA256,
		CredentialFingerprintSHA256: intent.CredentialFingerprintSHA256,
		TransportRevision:           intent.TransportRevision,
		TransportSHA256:             intent.TransportSHA256,
		ExpectedRevision:            intent.ExpectedRevision,
		TargetStatus:                intent.TargetStatus,
	})
	if err != nil {
		return "", "", err
	}
	digest := sha256.Sum256(payload)
	return string(payload), hex.EncodeToString(digest[:]), nil
}

func platformChannelControlOperationID(tenantID string, operationID string) string {
	return uuid.NewSHA1(uuid.NameSpaceURL, []byte("platform-channel-control-v1\x00"+tenantID+"\x00"+operationID)).String()
}

func platformChannelControlOperationMatches(operation PlatformChannelControlOperation, intent PlatformChannelControlIntent, digest string) bool {
	return operation.TenantID == intent.TenantID && operation.OperationID == intent.OperationID &&
		operation.ChannelID == intent.ChannelID && operation.Kind == intent.Kind &&
		operation.IntentSHA256 == digest
}

func findPlatformChannelControlOperationTx(tx *gorm.DB, tenantID string, operationID string) (*PlatformChannelControlOperation, error) {
	var operation PlatformChannelControlOperation
	if err := tx.Where("tenant_id = ? AND operation_id = ?", tenantID, operationID).First(&operation).Error; err != nil {
		return nil, err
	}
	return &operation, nil
}

func newPlatformChannelControlOperation(intent PlatformChannelControlIntent, payload string, digest string, now time.Time) PlatformChannelControlOperation {
	operation := PlatformChannelControlOperation{
		ID:           platformChannelControlOperationID(intent.TenantID, intent.OperationID),
		TenantID:     intent.TenantID,
		OperationID:  intent.OperationID,
		ChannelID:    intent.ChannelID,
		Kind:         intent.Kind,
		State:        PlatformChannelControlOperationPending,
		RequestID:    intent.RequestID,
		Actor:        intent.Actor,
		Reason:       intent.Reason,
		IntentSHA256: digest,
		IntentJSON:   payload,
		CreatedAt:    now,
	}
	if intent.Kind == PlatformChannelControlOperationKindTest && intent.RouteID != "" {
		operation.IntentPublicModelID = intent.PublicModelID
		operation.IntentRouteID = intent.RouteID
		operation.IntentTransportRevision = intent.TransportRevision
		operation.IntentTransportSHA256 = intent.TransportSHA256
		operation.ProviderSubmissionState = PlatformChannelTestSubmissionNotStarted
	}
	if intent.Kind == PlatformChannelControlOperationKindStatus {
		operation.IntentExpectedRevision = intent.ExpectedRevision
		operation.IntentTargetStatus = PlatformChannelControlStatusName(intent.TargetStatus)
	}
	return operation
}

const platformChannelControlChannelColumns = "id,type,credential_set_version,test_model,status,name,weight,created_time,test_time,response_time,models,priority,auto_ban,tag,channel_info,control_revision"

func ListPlatformChannelControlChannels(page int, pageSize int, status *int) ([]Channel, int64, error) {
	if page < 1 || page > 1_000_000 || pageSize < 1 || pageSize > 100 {
		return nil, 0, fmt.Errorf("channel control page is invalid")
	}
	query := DB.Session(&gorm.Session{SkipHooks: true}).Model(&Channel{})
	if status != nil {
		query = query.Where("status = ?", *status)
	}
	var total int64
	if err := query.Count(&total).Error; err != nil {
		return nil, 0, err
	}
	channels := make([]Channel, 0)
	if err := query.Select(platformChannelControlChannelColumns).
		Order("id ASC").Offset((page - 1) * pageSize).Limit(pageSize).
		Find(&channels).Error; err != nil {
		return nil, 0, err
	}
	return channels, total, nil
}

func GetPlatformChannelControlChannel(channelID int) (*Channel, error) {
	if channelID <= 0 {
		return nil, gorm.ErrRecordNotFound
	}
	var channel Channel
	if err := DB.Session(&gorm.Session{SkipHooks: true}).Select(platformChannelControlChannelColumns).Where("id = ?", channelID).First(&channel).Error; err != nil {
		return nil, err
	}
	return &channel, nil
}

// BeginPlatformChannelTestOperation commits the intent before any provider
// request. Only the caller that inserted the row may execute the test; every
// replay is receipt-only, including a pending receipt left by a lost response.
func BeginPlatformChannelTestOperation(intent PlatformChannelControlIntent) (*PlatformChannelControlOperation, bool, bool, error) {
	if intent.Kind != PlatformChannelControlOperationKindTest {
		return nil, false, false, fmt.Errorf("channel test intent kind is invalid")
	}
	if err := validatePlatformChannelControlIntent(intent); err != nil {
		return nil, false, false, err
	}
	payload, digest, err := platformChannelControlIntentPayload(intent)
	if err != nil {
		return nil, false, false, err
	}
	var operation *PlatformChannelControlOperation
	created := false
	err = DB.Transaction(func(tx *gorm.DB) error {
		if existing, findErr := findPlatformChannelControlOperationTx(tx, intent.TenantID, intent.OperationID); findErr == nil {
			if !platformChannelControlOperationMatches(*existing, intent, digest) {
				return ErrPlatformChannelControlOperationConflict
			}
			operation = existing
			return nil
		} else if !errors.Is(findErr, gorm.ErrRecordNotFound) {
			return findErr
		}
		var channelID int
		if err := tx.Model(&Channel{}).Select("id").Where("id = ?", intent.ChannelID).Scan(&channelID).Error; err != nil {
			return err
		}
		if channelID != intent.ChannelID {
			return gorm.ErrRecordNotFound
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		candidate := newPlatformChannelControlOperation(intent, payload, digest, now)
		// The receipt ID is deterministic for the same tenant/operation pair.
		// Concurrent first writers can therefore race on either the composite
		// unique index or the primary key; both mean "load and compare" rather
		// than exposing a backend-specific duplicate-key error.
		result := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&candidate)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected == 1 {
			operation = &candidate
			created = true
			return nil
		}
		existing, err := findPlatformChannelControlOperationTx(tx, intent.TenantID, intent.OperationID)
		if err != nil {
			if errors.Is(err, gorm.ErrRecordNotFound) && intent.RouteID != "" {
				var blocked int64
				if blockErr := tx.Model(&PlatformChannelControlOperation{}).
					Where("channel_id = ? AND intent_public_model_id = ? AND intent_route_id = ? AND kind = ? AND state = ?",
						intent.ChannelID, intent.PublicModelID, intent.RouteID,
						PlatformChannelControlOperationKindTest, PlatformChannelControlOperationPending).
					Count(&blocked).Error; blockErr != nil {
					return blockErr
				}
				if blocked > 0 {
					return ErrPlatformChannelTestRouteBlocked
				}
			}
			return err
		}
		if !platformChannelControlOperationMatches(*existing, intent, digest) {
			return ErrPlatformChannelControlOperationConflict
		}
		operation = existing
		return nil
	})
	return operation, created, !created && err == nil, err
}

// ClaimPlatformChannelTestSubmission is the only transition that authorizes a
// provider POST. The durable state is changed to submission_unknown before the
// caller can read a credential or open a provider connection. Replays of a
// submitted operation can only poll the stored task id and never claim again.
func ClaimPlatformChannelTestSubmission(tenantID string, operationID string) (*PlatformChannelControlOperation, bool, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!platformGenerationOperationIDPattern.MatchString(operationID) {
		return nil, false, fmt.Errorf("channel test submission claim is invalid")
	}
	var operation PlatformChannelControlOperation
	claimed := false
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := lockForUpdate(tx.Where("tenant_id = ? AND operation_id = ?", tenantID, operationID)).First(&operation).Error; err != nil {
			return err
		}
		if operation.Kind != PlatformChannelControlOperationKindTest || operation.IntentRouteID == "" {
			return ErrPlatformChannelControlOperationConflict
		}
		if operation.State != PlatformChannelControlOperationPending {
			return nil
		}
		switch operation.ProviderSubmissionState {
		case PlatformChannelTestSubmissionNotStarted:
			result := tx.Set(platformChannelControlSubmissionTransitionKey, true).
				Model(&PlatformChannelControlOperation{}).
				Where("id = ? AND state = ? AND provider_submission_state = ?", operation.ID,
					PlatformChannelControlOperationPending, PlatformChannelTestSubmissionNotStarted).
				Update("provider_submission_state", PlatformChannelTestSubmissionUnknown)
			if result.Error != nil {
				return result.Error
			}
			if result.RowsAffected != 1 {
				return ErrPlatformChannelControlOperationConflict
			}
			operation.ProviderSubmissionState = PlatformChannelTestSubmissionUnknown
			claimed = true
			return nil
		case PlatformChannelTestSubmissionUnknown:
			return ErrPlatformChannelTestSubmissionUnknown
		case PlatformChannelTestSubmissionSubmitted:
			return nil
		default:
			return ErrPlatformChannelControlOperationConflict
		}
	})
	return &operation, claimed, err
}

// RecordPlatformChannelTestSubmitted binds the exact provider task to the
// operation. ProviderTaskID is internal-only and is never present in a JSON
// response or the canonical intent document.
func RecordPlatformChannelTestSubmitted(tenantID string, operationID string, providerTaskID string) (*PlatformChannelControlOperation, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!platformGenerationOperationIDPattern.MatchString(operationID) ||
		!platformChannelControlProviderTaskIDPattern.MatchString(providerTaskID) {
		return nil, fmt.Errorf("channel test submitted task identity is invalid")
	}
	var operation PlatformChannelControlOperation
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := lockForUpdate(tx.Where("tenant_id = ? AND operation_id = ?", tenantID, operationID)).First(&operation).Error; err != nil {
			return err
		}
		if operation.Kind != PlatformChannelControlOperationKindTest || operation.IntentRouteID == "" {
			return ErrPlatformChannelControlOperationConflict
		}
		if operation.State != PlatformChannelControlOperationPending {
			return ErrPlatformChannelControlOperationConflict
		}
		if operation.ProviderSubmissionState == PlatformChannelTestSubmissionSubmitted {
			if operation.ProviderTaskID != providerTaskID {
				return ErrPlatformChannelControlOperationConflict
			}
			return nil
		}
		if operation.ProviderSubmissionState != PlatformChannelTestSubmissionUnknown || operation.ProviderTaskID != "" {
			return ErrPlatformChannelControlOperationConflict
		}
		result := tx.Set(platformChannelControlSubmissionTransitionKey, true).
			Model(&PlatformChannelControlOperation{}).
			Where("id = ? AND state = ? AND provider_submission_state = ? AND provider_task_id = ''", operation.ID,
				PlatformChannelControlOperationPending, PlatformChannelTestSubmissionUnknown).
			Updates(map[string]any{
				"provider_submission_state": PlatformChannelTestSubmissionSubmitted,
				"provider_task_id":          providerTaskID,
			})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return ErrPlatformChannelControlOperationConflict
		}
		operation.ProviderSubmissionState = PlatformChannelTestSubmissionSubmitted
		operation.ProviderTaskID = providerTaskID
		return nil
	})
	return &operation, err
}

// RecordPlatformChannelTestBlocker persists only a closed, secret-free
// acceptance phase while leaving the exact submitted provider task sticky.
// It never terminalizes the operation or releases the unresolved-route lock.
func RecordPlatformChannelTestBlocker(
	tenantID string,
	operationID string,
	errorCode string,
) (*PlatformChannelControlOperation, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!platformGenerationOperationIDPattern.MatchString(operationID) ||
		!IsPlatformChannelControlTestErrorCode(errorCode) ||
		errorCode == PlatformChannelControlErrorTestReconciled {
		return nil, fmt.Errorf("channel test blocker is invalid")
	}
	var operation PlatformChannelControlOperation
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := lockForUpdate(tx.Where("tenant_id = ? AND operation_id = ?", tenantID, operationID)).First(&operation).Error; err != nil {
			return err
		}
		if operation.Kind != PlatformChannelControlOperationKindTest || operation.IntentRouteID == "" ||
			operation.State != PlatformChannelControlOperationPending ||
			operation.ProviderSubmissionState != PlatformChannelTestSubmissionSubmitted ||
			operation.ProviderTaskID == "" {
			return ErrPlatformChannelControlOperationConflict
		}
		if operation.ProviderBlockerCode == errorCode {
			return nil
		}
		result := tx.Set(platformChannelControlSubmissionTransitionKey, true).
			Model(&PlatformChannelControlOperation{}).
			Where("id = ? AND state = ? AND provider_submission_state = ? AND provider_task_id <> ''", operation.ID,
				PlatformChannelControlOperationPending, PlatformChannelTestSubmissionSubmitted).
			Update("provider_blocker_code", errorCode)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return ErrPlatformChannelControlOperationConflict
		}
		operation.ProviderBlockerCode = errorCode
		return nil
	})
	return &operation, err
}

type PlatformChannelTestArtifactEvidence struct {
	SHA256      string
	SizeBytes   int64
	ContentType string
}

// CompletePlatformChannelRouteTestSuccess accepts success only after an exact
// provider task has been persisted and the controlled downloader has returned
// secret-free evidence for one protected generation artifact contract. The
// provider URL itself is never stored; profile matching happens before this
// generic lifecycle transition and again when release evidence is projected.
func CompletePlatformChannelRouteTestSuccess(
	tenantID string,
	operationID string,
	responseTimeMS int64,
	evidence PlatformChannelTestArtifactEvidence,
) (*PlatformChannelControlOperation, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!platformGenerationOperationIDPattern.MatchString(operationID) || responseTimeMS < 0 ||
		len(evidence.SHA256) != sha256.Size*2 || strings.ToLower(evidence.SHA256) != evidence.SHA256 ||
		evidence.SizeBytes <= 0 || !isPlatformChannelTestArtifactContentType(evidence.ContentType) {
		return nil, fmt.Errorf("channel test artifact evidence is invalid")
	}
	if _, err := hex.DecodeString(evidence.SHA256); err != nil {
		return nil, fmt.Errorf("channel test artifact evidence is invalid")
	}
	return completePlatformChannelRouteTestOperation(
		tenantID, operationID, true, responseTimeMS, "",
		PlatformChannelTestSubmissionSubmitted,
		PlatformChannelTestSubmissionArtifactVerified,
		evidence,
	)
}

func isPlatformChannelTestArtifactContentType(contentType string) bool {
	switch contentType {
	case "image/png", "video/mp4":
		return true
	default:
		return false
	}
}

// CompletePlatformChannelRouteTestFailure records only a proven-safe terminal
// phase. A submission_unknown operation is deliberately excluded and remains
// blocked until the owner-only reconciliation API confirms non-creation.
func CompletePlatformChannelRouteTestFailure(
	tenantID string,
	operationID string,
	responseTimeMS int64,
	errorCode string,
	providerRequestStarted bool,
) (*PlatformChannelControlOperation, error) {
	if !IsPlatformChannelControlTestErrorCode(errorCode) ||
		errorCode == PlatformChannelControlErrorTestReconciled || responseTimeMS < 0 {
		return nil, fmt.Errorf("channel test route failure is invalid")
	}
	if providerRequestStarted && !isPlatformChannelControlProviderTerminalErrorCode(errorCode) {
		// Once a provider task exists, only an exact failed/cancelled/expired
		// provider proof may release the route. Transport, proof, or artifact
		// acceptance failures keep the submitted operation sticky.
		return nil, fmt.Errorf("channel test route failure is not provider-terminal")
	}
	expected := PlatformChannelTestSubmissionSubmitted
	terminal := PlatformChannelTestSubmissionProviderTerminal
	if !providerRequestStarted {
		expected = PlatformChannelTestSubmissionNotStarted
		terminal = PlatformChannelTestSubmissionPreSubmitFailed
	}
	return completePlatformChannelRouteTestOperation(
		tenantID, operationID, false, responseTimeMS, errorCode,
		expected, terminal, PlatformChannelTestArtifactEvidence{},
	)
}

func isPlatformChannelControlProviderTerminalErrorCode(errorCode string) bool {
	switch errorCode {
	case PlatformChannelControlErrorTestValidation,
		PlatformChannelControlErrorTestAuth,
		PlatformChannelControlErrorTestQuota,
		PlatformChannelControlErrorTestTerminal:
		return true
	default:
		return false
	}
}

func verifyManagedPlatformChannelTestBindingTx(
	tx *gorm.DB,
	operation PlatformChannelControlOperation,
) error {
	if tx == nil || operation.Kind != PlatformChannelControlOperationKindTest || operation.IntentRouteID == "" {
		return ErrPlatformChannelControlOperationConflict
	}
	var channel Channel
	if err := lockForUpdate(tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).
		Select("id", "tag", "other_info", "credential_set_version").
		Where("id = ?", operation.ChannelID)).First(&channel).Error; err != nil {
		return err
	}
	if !IsProviderOnboardingManagedChannel(&channel) {
		return nil
	}
	var intent platformChannelControlCanonicalIntent
	if err := json.Unmarshal([]byte(operation.IntentJSON), &intent); err != nil ||
		intent.ChannelID != operation.ChannelID || intent.RouteID != operation.IntentRouteID ||
		intent.PublicModelID != operation.IntentPublicModelID {
		return ErrPlatformChannelControlOperationConflict
	}
	payloadDigest := sha256.Sum256([]byte(operation.IntentJSON))
	if subtle.ConstantTimeCompare([]byte(hex.EncodeToString(payloadDigest[:])), []byte(operation.IntentSHA256)) != 1 {
		return ErrPlatformChannelControlOperationConflict
	}
	credential, err := providerOnboardingCredentialRowTx(tx, channel)
	if err != nil || subtle.ConstantTimeCompare(
		[]byte(credential.KeySetFingerprint), []byte(intent.CredentialFingerprintSHA256),
	) != 1 {
		return ErrPlatformChannelControlOperationConflict
	}
	query := lockForUpdate(tx.Where(
		"route_key = ? AND model = ? AND channel_id = ? AND key_index = ? AND key_fingerprint = ? AND enabled = ?",
		intent.RouteID, intent.PublicModelID, operation.ChannelID, 0, intent.CredentialFingerprintSHA256, true,
	))
	if intent.Mode != "" {
		query = query.Where("mode = ?", intent.Mode)
	}
	var routes []PlatformGenerationProviderRoute
	if err := query.Find(&routes).Error; err != nil {
		return err
	}
	if len(routes) != 1 {
		return ErrPlatformChannelControlOperationConflict
	}
	route := routes[0]
	if route.UpstreamModel != intent.UpstreamModel || route.CapabilityProfileID != intent.CapabilityProfileID ||
		route.CapabilityProfileRevision != intent.CapabilityProfileRevision ||
		route.ModelReleaseID != intent.ModelReleaseID || route.ModelReleaseRevision != intent.ModelReleaseRevision {
		return ErrPlatformChannelControlOperationConflict
	}
	return nil
}

// CompletePlatformChannelRouteTestProvenNoCreation records a failure only when
// the caller has direct proof that no provider task can exist (for example, a
// rejected HTTP response or a local failure before provider I/O). It is not a
// substitute for owner reconciliation after a lost response.
func CompletePlatformChannelRouteTestProvenNoCreation(
	tenantID string,
	operationID string,
	responseTimeMS int64,
	errorCode string,
) (*PlatformChannelControlOperation, error) {
	if !isPlatformChannelControlProviderTerminalErrorCode(errorCode) || responseTimeMS < 0 {
		return nil, fmt.Errorf("channel test proven no-creation failure is invalid")
	}
	return completePlatformChannelRouteTestOperation(
		tenantID, operationID, false, responseTimeMS, errorCode,
		PlatformChannelTestSubmissionUnknown,
		PlatformChannelTestSubmissionProviderRejected,
		PlatformChannelTestArtifactEvidence{},
	)
}

func completePlatformChannelRouteTestOperation(
	tenantID string,
	operationID string,
	success bool,
	responseTimeMS int64,
	errorCode string,
	expectedSubmissionState string,
	terminalSubmissionState string,
	evidence PlatformChannelTestArtifactEvidence,
) (*PlatformChannelControlOperation, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!platformGenerationOperationIDPattern.MatchString(operationID) {
		return nil, fmt.Errorf("channel test route result is invalid")
	}
	var operation PlatformChannelControlOperation
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := lockForUpdate(tx.Where("tenant_id = ? AND operation_id = ?", tenantID, operationID)).First(&operation).Error; err != nil {
			return err
		}
		if operation.Kind != PlatformChannelControlOperationKindTest || operation.IntentRouteID == "" {
			return ErrPlatformChannelControlOperationConflict
		}
		if operation.State != PlatformChannelControlOperationPending ||
			operation.ProviderSubmissionState != expectedSubmissionState {
			return ErrPlatformChannelControlOperationConflict
		}
		if expectedSubmissionState == PlatformChannelTestSubmissionSubmitted && operation.ProviderTaskID == "" {
			return ErrPlatformChannelControlOperationConflict
		}
		if err := verifyManagedPlatformChannelTestBindingTx(tx, operation); err != nil {
			return err
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		state := PlatformChannelControlOperationFailed
		if success {
			state = PlatformChannelControlOperationSucceeded
		}
		updates := map[string]any{
			"state": state, "result_success": success, "result_response_ms": responseTimeMS,
			"result_error_code": errorCode, "completed_at": now,
			"provider_submission_state":      terminalSubmissionState,
			"provider_blocker_code":          "",
			"provider_artifact_sha256":       evidence.SHA256,
			"provider_artifact_size_bytes":   evidence.SizeBytes,
			"provider_artifact_content_type": evidence.ContentType,
		}
		result := tx.Set(platformChannelControlTerminalTransitionKey, true).
			Model(&PlatformChannelControlOperation{}).
			Where("id = ? AND state = ? AND provider_submission_state = ?", operation.ID,
				PlatformChannelControlOperationPending, expectedSubmissionState).
			Updates(updates)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return ErrPlatformChannelControlOperationConflict
		}
		operation.State = state
		operation.ResultSuccess = &success
		operation.ResultResponseMS = &responseTimeMS
		operation.ResultErrorCode = errorCode
		operation.ProviderSubmissionState = terminalSubmissionState
		operation.ProviderBlockerCode = ""
		operation.ProviderArtifactSHA256 = evidence.SHA256
		operation.ProviderArtifactSizeBytes = evidence.SizeBytes
		operation.ProviderArtifactContentType = evidence.ContentType
		operation.CompletedAt = &now
		return nil
	})
	return &operation, err
}

// ReconcilePlatformChannelTestNoCreation is intentionally one-way. It accepts
// only an explicit confirmation that the provider created no task. A caller
// that finds a provider task must leave the operation blocked for sticky
// recovery; it can never use this method to authorize another POST.
func ReconcilePlatformChannelTestNoCreation(
	tenantID string,
	channelID int,
	operationID string,
	actor string,
	reason string,
	confirmedNoProviderCreation bool,
) (*PlatformChannelControlOperation, error) {
	if !confirmedNoProviderCreation || channelID <= 0 ||
		!isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!platformGenerationOperationIDPattern.MatchString(operationID) ||
		strings.TrimSpace(actor) != actor || len(actor) < 1 || len(actor) > 128 ||
		strings.TrimSpace(reason) != reason || len(reason) < 3 || len(reason) > 240 {
		return nil, ErrPlatformChannelTestReconciliationInvalid
	}
	if !isSafePlatformChannelTestReconciliationText(actor) || !isSafePlatformChannelTestReconciliationText(reason) {
		return nil, ErrPlatformChannelTestReconciliationInvalid
	}
	var operation PlatformChannelControlOperation
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := lockForUpdate(tx.Where("tenant_id = ? AND channel_id = ? AND operation_id = ?", tenantID, channelID, operationID)).First(&operation).Error; err != nil {
			return err
		}
		if operation.Kind != PlatformChannelControlOperationKindTest || operation.IntentRouteID == "" ||
			operation.State != PlatformChannelControlOperationPending ||
			operation.ProviderSubmissionState != PlatformChannelTestSubmissionUnknown ||
			operation.ProviderTaskID != "" || operation.CompletedAt != nil || operation.ReconciledAt != nil {
			return ErrPlatformChannelControlOperationConflict
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		failed := false
		zero := int64(0)
		result := tx.Set(platformChannelControlTerminalTransitionKey, true).
			Model(&PlatformChannelControlOperation{}).
			Where("id = ? AND state = ? AND provider_submission_state = ? AND provider_task_id = ''", operation.ID,
				PlatformChannelControlOperationPending, PlatformChannelTestSubmissionUnknown).
			Updates(map[string]any{
				"state":                     PlatformChannelControlOperationFailed,
				"result_success":            failed,
				"result_response_ms":        zero,
				"result_error_code":         PlatformChannelControlErrorTestReconciled,
				"provider_submission_state": PlatformChannelTestSubmissionReconciledNoCreation,
				"reconciliation_actor":      actor,
				"reconciliation_reason":     reason,
				"reconciled_at":             now,
				"completed_at":              now,
			})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return ErrPlatformChannelControlOperationConflict
		}
		operation.State = PlatformChannelControlOperationFailed
		operation.ResultSuccess = &failed
		operation.ResultResponseMS = &zero
		operation.ResultErrorCode = PlatformChannelControlErrorTestReconciled
		operation.ProviderSubmissionState = PlatformChannelTestSubmissionReconciledNoCreation
		operation.ReconciliationActor = actor
		operation.ReconciliationReason = reason
		operation.ReconciledAt = &now
		operation.CompletedAt = &now
		return nil
	})
	return &operation, err
}

func CompletePlatformChannelTestOperation(
	tenantID string,
	operationID string,
	success bool,
	responseTimeMS int64,
	errorCode string,
) (*PlatformChannelControlOperation, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(tenantID) ||
		!platformGenerationOperationIDPattern.MatchString(operationID) || responseTimeMS < 0 ||
		(success && errorCode != "") || (!success && !IsPlatformChannelControlTestErrorCode(errorCode)) {
		return nil, fmt.Errorf("channel test result is invalid")
	}
	var operation PlatformChannelControlOperation
	err := DB.Transaction(func(tx *gorm.DB) error {
		if err := lockForUpdate(tx.Where("tenant_id = ? AND operation_id = ?", tenantID, operationID)).First(&operation).Error; err != nil {
			return err
		}
		if operation.Kind != PlatformChannelControlOperationKindTest {
			return ErrPlatformChannelControlOperationConflict
		}
		if operation.IntentRouteID != "" {
			return ErrPlatformChannelControlOperationConflict
		}
		if operation.State != PlatformChannelControlOperationPending {
			return nil
		}
		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		state := PlatformChannelControlOperationSucceeded
		if !success {
			state = PlatformChannelControlOperationFailed
		}
		operation.State = state
		operation.ResultSuccess = &success
		operation.ResultResponseMS = &responseTimeMS
		operation.ResultErrorCode = errorCode
		operation.CompletedAt = &now
		return tx.Set(platformChannelControlTerminalTransitionKey, true).Model(&PlatformChannelControlOperation{}).
			Where("id = ? AND state = ?", operation.ID, PlatformChannelControlOperationPending).
			Updates(map[string]any{
				"state": state, "result_success": success, "result_response_ms": responseTimeMS,
				"result_error_code": errorCode, "completed_at": now,
			}).Error
	})
	if err != nil {
		return nil, err
	}
	return &operation, nil
}

// IsPlatformChannelControlTestErrorCode is the closed, secret-free failure
// taxonomy allowed in immutable channel-test receipts. Provider response
// bodies, messages, URLs, credentials, and vendor-specific codes never cross
// this boundary; operators receive only the phase/class needed to remediate a
// failed acceptance test.
func IsPlatformChannelControlTestErrorCode(errorCode string) bool {
	switch errorCode {
	case PlatformChannelControlErrorTestFailed,
		PlatformChannelControlErrorTestUnavailable,
		PlatformChannelControlErrorTestValidation,
		PlatformChannelControlErrorTestAuth,
		PlatformChannelControlErrorTestQuota,
		PlatformChannelControlErrorTestTerminal,
		PlatformChannelControlErrorTestArtifact,
		PlatformChannelControlErrorTestRouteDrift,
		PlatformChannelControlErrorTestReconciled:
		return true
	default:
		return false
	}
}

func ApplyPlatformChannelStatusOperation(intent PlatformChannelControlIntent) (*PlatformChannelControlOperation, bool, error) {
	if intent.Kind != PlatformChannelControlOperationKindStatus {
		return nil, false, fmt.Errorf("channel status intent kind is invalid")
	}
	if err := validatePlatformChannelControlIntent(intent); err != nil {
		return nil, false, err
	}
	payload, digest, err := platformChannelControlIntentPayload(intent)
	if err != nil {
		return nil, false, err
	}
	var operation *PlatformChannelControlOperation
	idempotentReplay := false
	var semanticErr error
	err = DB.Transaction(func(tx *gorm.DB) error {
		// The generic Platform facade is not an alternate lifecycle API for
		// credential-late managed channels. Reject reserved IDs without a read,
		// then lock the secret-free lifecycle tuple before receipt lookup. A
		// completed receipt for a subsequently deleted ordinary channel keeps its
		// historical replay semantics; a present managed row never does.
		if IsProviderOnboardingReservedChannelID(intent.ChannelID) {
			return ErrProviderOnboardingManagedChannel
		}
		var channel Channel
		channelMissing := false
		if lockErr := lockForUpdate(tx.Session(&gorm.Session{NewDB: true, SkipHooks: true}).
			Select("id", "status", "control_revision", "tag", "other_info", "credential_set_version").
			Where("id = ?", intent.ChannelID)).First(&channel).Error; errors.Is(lockErr, gorm.ErrRecordNotFound) {
			channelMissing = true
		} else if lockErr != nil {
			return lockErr
		}
		if !channelMissing && IsProviderOnboardingManagedChannel(&channel) {
			return ErrProviderOnboardingManagedChannel
		}
		if existing, findErr := findPlatformChannelControlOperationTx(tx, intent.TenantID, intent.OperationID); findErr == nil {
			if !platformChannelControlOperationMatches(*existing, intent, digest) {
				return ErrPlatformChannelControlOperationConflict
			}
			operation = existing
			idempotentReplay = true
			if existing.ResultErrorCode == PlatformChannelControlErrorRevisionConflict {
				semanticErr = ErrPlatformChannelControlRevisionConflict
			}
			return nil
		} else if !errors.Is(findErr, gorm.ErrRecordNotFound) {
			return findErr
		}
		if channelMissing {
			return gorm.ErrRecordNotFound
		}

		now, err := GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		candidate := newPlatformChannelControlOperation(intent, payload, digest, now)
		insert := tx.Clauses(clause.OnConflict{DoNothing: true}).Create(&candidate)
		if insert.Error != nil {
			return insert.Error
		}
		if insert.RowsAffected != 1 {
			existing, findErr := findPlatformChannelControlOperationTx(tx, intent.TenantID, intent.OperationID)
			if findErr != nil {
				return findErr
			}
			if !platformChannelControlOperationMatches(*existing, intent, digest) {
				return ErrPlatformChannelControlOperationConflict
			}
			operation = existing
			idempotentReplay = true
			if existing.ResultErrorCode == PlatformChannelControlErrorRevisionConflict {
				semanticErr = ErrPlatformChannelControlRevisionConflict
			}
			return nil
		}

		previousRevision := PlatformChannelControlRevision(channel)
		candidate.PreviousRevision = previousRevision
		candidate.ResultPreviousStatus = PlatformChannelControlStatusName(channel.Status)
		if previousRevision != intent.ExpectedRevision {
			candidate.State = PlatformChannelControlOperationFailed
			candidate.ResultErrorCode = PlatformChannelControlErrorRevisionConflict
			candidate.ResultRevision = previousRevision
			candidate.ResultCurrentStatus = candidate.ResultPreviousStatus
			changed := false
			candidate.ResultChanged = &changed
			candidate.CompletedAt = &now
			if err := completePlatformChannelStatusOperationTx(tx, candidate); err != nil {
				return err
			}
			operation = &candidate
			semanticErr = ErrPlatformChannelControlRevisionConflict
			return nil
		}

		changed := channel.Status != intent.TargetStatus
		if changed {
			channel.Status = intent.TargetStatus
			if err := tx.Model(&Channel{}).Where("id = ?", channel.Id).Updates(map[string]any{
				"status": channel.Status,
			}).Error; err != nil {
				return err
			}
			if err := tx.Model(&Ability{}).Where("channel_id = ?", channel.Id).
				Select("enabled").Update("enabled", channel.Status == common.ChannelStatusEnabled).Error; err != nil {
				return err
			}
			// The database trigger owns control_revision. Reload the row while
			// retaining this transaction's row lock so the receipt contains the
			// exact single-bump revision produced by the status mutation.
			if err := tx.Select("id", "status", "control_revision").First(&channel, channel.Id).Error; err != nil {
				return err
			}
		}
		resultRevision := PlatformChannelControlRevision(channel)
		candidate.State = PlatformChannelControlOperationSucceeded
		candidate.ResultRevision = resultRevision
		candidate.ResultCurrentStatus = PlatformChannelControlStatusName(channel.Status)
		candidate.ResultChanged = &changed
		candidate.CompletedAt = &now
		if err := completePlatformChannelStatusOperationTx(tx, candidate); err != nil {
			return err
		}
		operation = &candidate
		return nil
	})
	if err != nil {
		return nil, false, err
	}
	return operation, idempotentReplay, semanticErr
}

func completePlatformChannelStatusOperationTx(tx *gorm.DB, operation PlatformChannelControlOperation) error {
	if tx == nil || operation.Kind != PlatformChannelControlOperationKindStatus ||
		(operation.State != PlatformChannelControlOperationSucceeded && operation.State != PlatformChannelControlOperationFailed) ||
		operation.CompletedAt == nil || operation.ResultChanged == nil {
		return fmt.Errorf("channel status operation result is invalid")
	}
	result := tx.Set(platformChannelControlTerminalTransitionKey, true).
		Model(&PlatformChannelControlOperation{}).
		Where("id = ? AND state = ?", operation.ID, PlatformChannelControlOperationPending).
		Updates(map[string]any{
			"state":                  operation.State,
			"previous_revision":      operation.PreviousRevision,
			"result_revision":        operation.ResultRevision,
			"result_error_code":      operation.ResultErrorCode,
			"result_previous_status": operation.ResultPreviousStatus,
			"result_current_status":  operation.ResultCurrentStatus,
			"result_changed":         *operation.ResultChanged,
			"completed_at":           *operation.CompletedAt,
		})
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return ErrPlatformChannelControlOperationConflict
	}
	return nil
}

func GetPlatformChannelControlOperation(tenantID string, channelID int, operationID string) (*PlatformChannelControlOperation, error) {
	if !isCanonicalPlatformGenerationReconciliationUUID(tenantID) || channelID <= 0 ||
		!platformGenerationOperationIDPattern.MatchString(operationID) {
		return nil, gorm.ErrRecordNotFound
	}
	var operation PlatformChannelControlOperation
	if err := DB.Where("tenant_id = ? AND channel_id = ? AND operation_id = ?", tenantID, channelID, operationID).
		First(&operation).Error; err != nil {
		return nil, err
	}
	return &operation, nil
}

func PlatformChannelControlRevision(channel Channel) string {
	payload := struct {
		ChannelID       int   `json:"channel_id"`
		Status          int   `json:"status"`
		ControlRevision int64 `json:"control_revision"`
	}{ChannelID: channel.Id, Status: channel.Status, ControlRevision: channel.ControlRevision}
	encoded, _ := json.Marshal(payload)
	digest := sha256.Sum256(encoded)
	return "sha256:" + hex.EncodeToString(digest[:])
}

func isPlatformChannelControlRevision(revision string) bool {
	if len(revision) != len("sha256:")+sha256.Size*2 || !strings.HasPrefix(revision, "sha256:") {
		return false
	}
	digest, err := hex.DecodeString(strings.TrimPrefix(revision, "sha256:"))
	return err == nil && len(digest) == sha256.Size && strings.ToLower(revision) == revision
}

func PlatformChannelControlStatusName(status int) string {
	switch status {
	case common.ChannelStatusEnabled:
		return "enabled"
	case common.ChannelStatusManuallyDisabled:
		return "manually_disabled"
	case common.ChannelStatusAutoDisabled:
		return "auto_disabled"
	default:
		return "unknown"
	}
}

func MigratePlatformChannelControlStorage() error {
	return MigratePlatformChannelControlStorageWithDB(DB)
}

// platformChannelControlOperationPreV5 preserves the historical v1-v4 table
// shape. Those frozen bootstrap registries must not inherit columns added by
// the still-unreleased v5 contract through a mutable latest ORM model.
type platformChannelControlOperationPreV5 struct {
	ID                     string `gorm:"type:varchar(36);primaryKey"`
	TenantID               string `gorm:"type:varchar(36);not null;uniqueIndex:ux_platform_channel_control_operation,priority:1"`
	OperationID            string `gorm:"type:varchar(128);not null;uniqueIndex:ux_platform_channel_control_operation,priority:2"`
	ChannelID              int    `gorm:"not null;index:idx_platform_channel_control_channel"`
	Kind                   string `gorm:"type:varchar(16);not null"`
	State                  string `gorm:"type:varchar(16);not null;index"`
	RequestID              string `gorm:"type:varchar(80);not null"`
	Actor                  string `gorm:"type:varchar(128);not null"`
	Reason                 string `gorm:"type:varchar(240);not null"`
	IntentSHA256           string `gorm:"type:char(64);not null"`
	IntentJSON             string `gorm:"type:text;not null"`
	IntentExpectedRevision string `gorm:"type:varchar(72);not null;default:''"`
	IntentTargetStatus     string `gorm:"type:varchar(24);not null;default:''"`
	PreviousRevision       string `gorm:"type:varchar(72);not null;default:''"`
	ResultRevision         string `gorm:"type:varchar(72);not null;default:''"`
	ResultSuccess          *bool
	ResultResponseMS       *int64
	ResultErrorCode        string `gorm:"type:varchar(64);not null;default:''"`
	ResultPreviousStatus   string `gorm:"type:varchar(24);not null;default:''"`
	ResultCurrentStatus    string `gorm:"type:varchar(24);not null;default:''"`
	ResultChanged          *bool
	CreatedAt              time.Time `gorm:"not null;index"`
	CompletedAt            *time.Time
}

func (platformChannelControlOperationPreV5) TableName() string {
	return "platform_channel_control_operations"
}

func MigratePlatformChannelControlStorageWithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	if err := db.AutoMigrate(&Channel{}, &platformChannelControlOperationPreV5{}); err != nil {
		return err
	}
	if err := InstallPlatformChannelControlRevisionGuardWithDB(db); err != nil {
		return err
	}
	return InstallPlatformChannelControlOperationGuardsWithDB(db)
}

// MigratePlatformChannelControlStorageV5WithDB preserves the deployed v5
// table shape and installs only its frozen diagnostic taxonomy. Durable route
// test lifecycle columns are introduced exclusively by schema v6.
func MigratePlatformChannelControlStorageV5WithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	if err := MigratePlatformChannelControlStorageWithDB(db); err != nil {
		return err
	}
	return installPlatformChannelControlDiagnosticTaxonomyV5WithDB(db)
}

// InstallPlatformChannelControlRevisionGuard makes control_revision a
// database-owned monotonic value. PostgreSQL uses a BEFORE trigger so stale
// full-row writes cannot overwrite the revision. SQLite uses an AFTER trigger
// for its test/development runtime; native full-row save helpers additionally
// omit the database-owned column.
func InstallPlatformChannelControlRevisionGuard() error {
	return InstallPlatformChannelControlRevisionGuardWithDB(DB)
}

func InstallPlatformChannelControlRevisionGuardWithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	if db.Dialector.Name() == "postgres" {
		return installPostgresPlatformChannelControlRevisionGuardWithDB(db)
	}
	if db.Dialector.Name() == "sqlite" {
		return installSQLitePlatformChannelControlRevisionGuardWithDB(db)
	}
	return nil
}

func platformChannelControlQuotedColumns() []string {
	columns := make([]string, 0, len(platformChannelControlRelevantColumns))
	for _, column := range platformChannelControlRelevantColumns {
		columns = append(columns, `"`+column+`"`)
	}
	return columns
}

func platformChannelControlPostgresChangedExpression() string {
	parts := make([]string, 0, len(platformChannelControlRelevantColumns))
	for _, column := range platformChannelControlRelevantColumns {
		quoted := `"` + column + `"`
		if column == "channel_info" {
			parts = append(parts, "NEW."+quoted+"::text IS DISTINCT FROM OLD."+quoted+"::text")
			continue
		}
		parts = append(parts, "NEW."+quoted+" IS DISTINCT FROM OLD."+quoted)
	}
	return strings.Join(parts, "\n        OR ")
}

func platformChannelControlSQLiteChangedExpression() string {
	parts := make([]string, 0, len(platformChannelControlRelevantColumns))
	for _, quoted := range platformChannelControlQuotedColumns() {
		parts = append(parts, "NEW."+quoted+" IS NOT OLD."+quoted)
	}
	return strings.Join(parts, "\n        OR ")
}

func installPostgresPlatformChannelControlRevisionGuard() error {
	return installPostgresPlatformChannelControlRevisionGuardWithDB(DB)
}

func installPostgresPlatformChannelControlRevisionGuardWithDB(db *gorm.DB) error {
	return db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec(
			"SELECT pg_advisory_xact_lock(?)",
			platformChannelControlRevisionMigrationAdvisoryLock,
		).Error; err != nil {
			return err
		}
		functionSQL := fmt.Sprintf(`
CREATE OR REPLACE FUNCTION enforce_channel_control_revision()
RETURNS trigger AS $$
BEGIN
    IF %s
    THEN
        IF OLD.control_revision = 9223372036854775807 THEN
            RAISE EXCEPTION 'channel control revision exhausted';
        END IF;
        NEW.control_revision := OLD.control_revision + 1;
    ELSE
        NEW.control_revision := OLD.control_revision;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql`, platformChannelControlPostgresChangedExpression())
		if err := tx.Exec(functionSQL).Error; err != nil {
			return err
		}
		if err := tx.Exec("DROP TRIGGER IF EXISTS trg_channels_control_revision ON channels").Error; err != nil {
			return err
		}
		return tx.Exec("CREATE TRIGGER trg_channels_control_revision BEFORE UPDATE ON channels FOR EACH ROW EXECUTE FUNCTION enforce_channel_control_revision()").Error
	})
}

func installSQLitePlatformChannelControlRevisionGuard() error {
	return installSQLitePlatformChannelControlRevisionGuardWithDB(DB)
}

func installSQLitePlatformChannelControlRevisionGuardWithDB(db *gorm.DB) error {
	return db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec("DROP TRIGGER IF EXISTS trg_channels_control_revision").Error; err != nil {
			return err
		}
		triggerSQL := fmt.Sprintf(`
CREATE TRIGGER trg_channels_control_revision
AFTER UPDATE OF %s ON channels
FOR EACH ROW
WHEN %s
BEGIN
    UPDATE channels
       SET control_revision = OLD.control_revision + 1
     WHERE id = OLD.id;
END`, strings.Join(platformChannelControlQuotedColumns(), ", "), platformChannelControlSQLiteChangedExpression())
		return tx.Exec(triggerSQL).Error
	})
}

func InstallPlatformChannelControlOperationGuards() error {
	return InstallPlatformChannelControlOperationGuardsWithDB(DB)
}

func InstallPlatformChannelControlOperationGuardsWithDB(db *gorm.DB) error {
	if db == nil {
		return fmt.Errorf("database is not initialized")
	}
	if db.Dialector.Name() != "postgres" {
		return nil
	}
	const table = "platform_channel_control_operations"
	return db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec(
			"SELECT pg_advisory_xact_lock(?)",
			platformChannelControlGuardMigrationAdvisoryLock,
		).Error; err != nil {
			return err
		}
		if err := tx.Exec(`
CREATE OR REPLACE FUNCTION enforce_platform_channel_control_operation_transition()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'platform channel control operations cannot be truncated';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'platform channel control operations cannot be deleted';
    END IF;
    IF OLD.state <> 'pending'
       OR NEW.state NOT IN ('succeeded', 'failed')
       OR OLD.id IS DISTINCT FROM NEW.id
       OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
       OR OLD.operation_id IS DISTINCT FROM NEW.operation_id
       OR OLD.channel_id IS DISTINCT FROM NEW.channel_id
       OR OLD.kind IS DISTINCT FROM NEW.kind
       OR OLD.request_id IS DISTINCT FROM NEW.request_id
       OR OLD.actor IS DISTINCT FROM NEW.actor
       OR OLD.reason IS DISTINCT FROM NEW.reason
       OR OLD.intent_sha256 IS DISTINCT FROM NEW.intent_sha256
       OR OLD.intent_json IS DISTINCT FROM NEW.intent_json
       OR OLD.intent_expected_revision IS DISTINCT FROM NEW.intent_expected_revision
       OR OLD.intent_target_status IS DISTINCT FROM NEW.intent_target_status
       OR OLD.created_at IS DISTINCT FROM NEW.created_at
       OR OLD.completed_at IS NOT NULL
       OR NEW.completed_at IS NULL
       OR OLD.kind NOT IN ('test', 'status')
       OR (OLD.kind = 'test' AND (
            OLD.previous_revision IS DISTINCT FROM NEW.previous_revision
            OR OLD.result_revision IS DISTINCT FROM NEW.result_revision
            OR OLD.result_previous_status IS DISTINCT FROM NEW.result_previous_status
            OR OLD.result_current_status IS DISTINCT FROM NEW.result_current_status
            OR OLD.result_changed IS DISTINCT FROM NEW.result_changed
            OR NEW.result_success IS NULL
            OR NEW.result_response_ms IS NULL
            OR NEW.result_response_ms < 0
            OR (NEW.state = 'succeeded' AND (NEW.result_success IS DISTINCT FROM TRUE OR NEW.result_error_code <> ''))
            OR (NEW.state = 'failed' AND (NEW.result_success IS DISTINCT FROM FALSE OR NEW.result_error_code NOT IN (
                'CHANNEL_TEST_FAILED',
                'CHANNEL_TEST_UNAVAILABLE'
            )))
       ))
       OR (OLD.kind = 'status' AND (
            NEW.result_success IS NOT NULL
            OR NEW.result_response_ms IS NOT NULL
            OR NEW.previous_revision !~ '^sha256:[0-9a-f]{64}$'
            OR NEW.result_revision !~ '^sha256:[0-9a-f]{64}$'
            OR NEW.result_previous_status NOT IN ('enabled', 'manually_disabled', 'auto_disabled')
            OR NEW.result_current_status NOT IN ('enabled', 'manually_disabled', 'auto_disabled')
            OR NEW.result_changed IS NULL
            OR (NEW.state = 'succeeded' AND (
                NEW.result_error_code <> ''
                OR NEW.previous_revision <> OLD.intent_expected_revision
                OR NEW.result_current_status <> OLD.intent_target_status
            ))
            OR (NEW.state = 'failed' AND (
                NEW.result_error_code <> 'CHANNEL_REVISION_CONFLICT'
                OR NEW.previous_revision = OLD.intent_expected_revision
                OR NEW.result_previous_status <> NEW.result_current_status
                OR NEW.result_changed IS DISTINCT FROM FALSE
            ))
       ))
    THEN
        RAISE EXCEPTION 'invalid platform channel control operation transition';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql`).Error; err != nil {
			return err
		}
		for _, trigger := range []string{
			"trg_platform_channel_control_operations_transition",
			"trg_platform_channel_control_operations_no_truncate",
		} {
			if err := tx.Exec(fmt.Sprintf("DROP TRIGGER IF EXISTS %s ON %s", trigger, table)).Error; err != nil {
				return err
			}
		}
		if err := tx.Exec("CREATE TRIGGER trg_platform_channel_control_operations_transition BEFORE UPDATE OR DELETE ON " + table + " FOR EACH ROW EXECUTE FUNCTION enforce_platform_channel_control_operation_transition()").Error; err != nil {
			return err
		}
		return tx.Exec("CREATE TRIGGER trg_platform_channel_control_operations_no_truncate BEFORE TRUNCATE ON " + table + " FOR EACH STATEMENT EXECUTE FUNCTION enforce_platform_channel_control_operation_transition()").Error
	})
}
