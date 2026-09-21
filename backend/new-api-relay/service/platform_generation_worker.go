package service

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"image"
	"io"
	"mime"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/generationprofile"
	"github.com/QuantumNous/new-api/model"
	"github.com/google/uuid"
	"gorm.io/gorm"
)

const (
	platformGenerationSubmissionLease         = 3 * time.Minute
	platformGenerationPollLease               = 30 * time.Second
	platformGenerationTransferLease           = 5 * time.Minute
	platformGenerationCallbackLease           = 30 * time.Second
	platformArtifactCleanupLease              = 30 * time.Second
	platformArtifactCleanupWorkerInterval     = 500 * time.Millisecond
	platformArtifactCleanupMaxAttempts        = 8
	platformArtifactInitialReapInterval       = 10 * time.Minute
	platformArtifactPeriodicReapInterval      = 24 * time.Hour
	platformGenerationLocalWait               = 2 * time.Second
	platformGenerationPollInterval            = 3 * time.Second
	platformGenerationReconcileWait           = 10 * time.Second
	platformGenerationResponseLimit           = 1 << 20
	platformGenerationCallbackBatch           = 32
	platformGenerationFailureThresholdDefault = 3
	platformGenerationProviderCooldownDefault = 30 * time.Second
	platformGenerationVeoInputFetchTimeout    = 20 * time.Second
	platformGenerationVeoInputMaxBytes        = 20 * 1024 * 1024
	platformGenerationVeoInputMaxDimension    = 8192
)

var errPlatformGenerationInputAssetUnavailable = errors.New("platform generation input asset is unavailable")

var (
	errPlatformGenerationSubmissionLeaseFenced = errors.New("generation submission lease was fenced")
	errPlatformGenerationTransferLeaseFenced   = errors.New("generation transfer lease was fenced")
)

type platformNativeTaskRequest struct {
	Prompt         string         `json:"prompt"`
	Model          string         `json:"model"`
	Image          string         `json:"image,omitempty"`
	Images         []string       `json:"images,omitempty"`
	Videos         []string       `json:"videos,omitempty"`
	Audios         []string       `json:"audios,omitempty"`
	Size           string         `json:"size,omitempty"`
	Duration       int            `json:"duration,omitempty"`
	Seconds        string         `json:"seconds,omitempty"`
	InputReference string         `json:"input_reference,omitempty"`
	Metadata       map[string]any `json:"metadata,omitempty"`
}

type platformNativeTemporaryResult struct {
	NativeTaskID          string `json:"native_task_id"`
	ResultURL             string `json:"result_url"`
	TaskData              string `json:"task_data,omitempty"`
	ProofContractRevision string `json:"proof_contract_revision,omitempty"`
	ReceiptSHA256         string `json:"receipt_sha256,omitempty"`
	ReceiptProofSHA256    string `json:"receipt_proof_sha256,omitempty"`
	ArtifactURLSHA256     string `json:"artifact_url_sha256,omitempty"`
}

func PlatformGenerationWorkersEnabled() bool {
	if !PlatformRelayCompatEnabled() {
		return false
	}
	configured := strings.TrimSpace(os.Getenv("RELAY_COMPAT_WORKER_ENABLED"))
	return configured == "" || strings.EqualFold(configured, "true")
}

func ValidatePlatformGenerationWorkerConfiguration() error {
	production := PlatformRelayProductionSecurityEnabled()
	if production && PlatformRelayCompatEnabled() && !PlatformGenerationWorkersEnabled() {
		return errors.New("production generation compatibility requires RELAY_COMPAT_WORKER_ENABLED=true")
	}
	if production && PlatformRelayCompatEnabled() && common.MainDatabaseType() != common.DatabaseTypePostgreSQL {
		return errors.New("production generation compatibility requires PostgreSQL as the main database")
	}
	if production && PlatformRelayCompatEnabled() {
		if err := model.ValidateProviderCredentialVaultRuntime(true); err != nil {
			return err
		}
	}
	if !PlatformGenerationWorkersEnabled() {
		return nil
	}
	if err := ValidatePlatformRelayBuildProvenance(production); err != nil {
		return err
	}
	token := PlatformRelayInternalAdmissionToken()
	if token == "" {
		return errors.New("RELAY_COMPAT_INTERNAL_ADMISSION_TOKEN is required when generation workers are enabled")
	}
	if production {
		if len([]byte(token)) < 32 || platformRelaySecretIsPlaceholder(token) {
			return errors.New("production internal generation admission token must be at least 32 bytes and non-placeholder")
		}
	}
	if err := ValidatePlatformGenerationOperationsConfiguration(); err != nil {
		return err
	}
	_, cooldown, err := platformGenerationProviderFailurePolicy()
	if err != nil {
		return err
	}
	if production && cooldown <= 0 {
		return errors.New("production generation compatibility requires a positive RELAY_PROVIDER_COOLDOWN_SECONDS")
	}
	if err := ValidatePlatformGenerationDelayQueueRuntime(
		context.Background(),
		PlatformGenerationWorkersEnabled(),
		production,
	); err != nil {
		return err
	}
	if production {
		// The Huawei OBS constructor performs a live bucket-versioning safety
		// check. Startup must fail before workers accept work when permanent
		// orphan deletion cannot be guaranteed.
		return validatePlatformGenerationWorkerArtifactReadiness()
	}
	return nil
}

func validatePlatformGenerationWorkerArtifactReadiness() error {
	store, err := platformArtifactReadinessStoreFactory()
	if err != nil {
		return err
	}
	defer closePlatformGenerationArtifactStore(store)
	if store == nil || !store.Persistent() {
		return errors.New("production generation workers require persistent artifact storage")
	}
	if err := publishPlatformArtifactReadinessProof(true, store); err != nil {
		return fmt.Errorf("artifact readiness startup proof failed: %w", err)
	}
	return nil
}

func StartPlatformGenerationWorkers() error {
	generationWorkersEnabled := PlatformGenerationWorkersEnabled()
	// Artifact cleanup protects already-committed storage writes and must not
	// share the submission queue's failure domain. Start its supervised
	// lifecycle before opening Redis. Its idle loop dynamically watches for
	// historical intents, so disabling new generation admission during a
	// rolling deploy cannot abandon a late Put from an older pod.
	if err := startPlatformArtifactCleanupWorker(); err != nil {
		return err
	}
	if !generationWorkersEnabled {
		return nil
	}
	return platformGenerationWorkers.Start(
		context.Background(),
		platformGenerationWorkerInterval,
		func() (platformGenerationWorkerDependencies, error) {
			delayQueue, err := NewPlatformGenerationDelayQueue(PlatformGenerationDelayQueueRuntimeConfig())
			if err != nil {
				return platformGenerationWorkerDependencies{}, err
			}
			return defaultPlatformGenerationWorkerDependencies(delayQueue), nil
		},
	)
}

func RunPlatformGenerationSubmissionOnce(ctx context.Context, outboxIDs ...int64) (bool, error) {
	claim, err := model.ClaimPlatformGenerationSubmission(platformGenerationSubmissionLease, outboxIDs...)
	if err != nil {
		return false, err
	}
	if claim == nil {
		// ClaimPlatformGenerationSubmission currently reports an empty queue as
		// gorm.ErrRecordNotFound. Keep this boundary defensive so a future model
		// regression cannot turn a repairable outbox inconsistency into a process
		// panic.
		return false, gorm.ErrRecordNotFound
	}
	var request dto.PlatformGenerationRequest
	if err := common.Unmarshal([]byte(claim.Job.RequestJSON), &request); err != nil {
		return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorGenerationFailed, "The accepted generation request could not be decoded", false)
	}
	if err := ValidatePlatformGenerationCapability(request); err != nil {
		code := err.Error()
		switch code {
		case model.PlatformGenerationErrorModelCapabilityUnavailable,
			model.PlatformGenerationErrorCapabilityRevisionMismatch,
			model.PlatformGenerationErrorModeNotSupportedByModel,
			model.PlatformGenerationErrorRequestNotSupportedByModel:
		default:
			code = model.PlatformGenerationErrorGenerationFailed
		}
		return true, failPlatformGenerationSubmission(*claim, code, "The generation request is not admitted by the pinned capability", false)
	}
	routeClaim, err := model.ClaimPlatformGenerationProviderRoute(claim.Job.ID, request.Model, request.Mode)
	if err != nil {
		switch {
		case errors.Is(err, model.ErrPlatformGenerationProviderRouteBusy), errors.Is(err, model.ErrPlatformGenerationProviderRouteRateLimited):
			_, releaseErr := model.ReleasePlatformGenerationSubmission(*claim, platformGenerationLocalWait, err.Error())
			return true, releaseErr
		case errors.Is(err, model.ErrPlatformGenerationRouteAdmissionHeld), errors.Is(err, model.ErrPlatformGenerationRouteAdmissionUnknown):
			if markErr := model.MarkPlatformGenerationHeldRouteUnknown(claim.Job.ID); markErr != nil {
				return true, markErr
			}
			_, completeErr := model.CompletePlatformGenerationSubmission(*claim, map[string]any{
				"status":          model.PlatformGenerationStatusReconciliationRequired,
				"error_code":      model.PlatformGenerationErrorSubmissionReconciliationRequired,
				"error_message":   "The provider submission outcome is unknown and is being reconciled",
				"error_retryable": true,
			}, BuildPlatformGenerationCallbackDelivery)
			return true, completeErr
		default:
			return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorGenerationChannelUnavailable, "No provider route can safely accept this generation", true)
		}
	}
	runtimeTransport, transportRequired, transportErr := ResolvePlatformGenerationRuntimeTransport(routeClaim.Route)
	if transportErr != nil {
		_, _ = model.ReleasePlatformGenerationProviderRoute(claim.Job.ID, routeClaim.SubmissionToken)
		return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorGenerationChannelUnavailable, "The selected Google provider transport is not currently attested", false)
	}
	if !transportRequired {
		runtimeTransport = PlatformGenerationRuntimeTransportBinding{}
	}
	profile, hasProfile, profileErr := platformGenerationProfileForProviderRoute(routeClaim.Route)
	if profileErr != nil || (request.Mode == "text_to_image" && !hasProfile) {
		_, _ = model.ReleasePlatformGenerationProviderRoute(claim.Job.ID, routeClaim.SubmissionToken)
		return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorGenerationChannelUnavailable, "The selected provider route has no executable adapter profile", false)
	}
	if hasProfile {
		if err := profile.ValidateRequest(request); err != nil {
			_, _ = model.ReleasePlatformGenerationProviderRoute(claim.Job.ID, routeClaim.SubmissionToken)
			return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorRequestNotSupportedByModel, "The generation request exceeds the selected route profile", false)
		}
		request.Metadata = generationprofile.SnapshotMetadata(request.Metadata, profile)
		serializedRequest, err := common.Marshal(request)
		if err != nil {
			_, _ = model.ReleasePlatformGenerationProviderRoute(claim.Job.ID, routeClaim.SubmissionToken)
			return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorGenerationFailed, "The selected route profile could not be persisted", true)
		}
		if err := model.BindPlatformGenerationRequestToRouteProfile(
			claim.Job.ID,
			claim.Token,
			routeClaim.AdmissionID,
			routeClaim.SubmissionToken,
			string(serializedRequest),
		); err != nil {
			_, _ = model.ReleasePlatformGenerationProviderRoute(claim.Job.ID, routeClaim.SubmissionToken)
			return true, err
		}
		claim.Job.RequestJSON = string(serializedRequest)
		claim.Job.ProviderRouteID = routeClaim.Route.ID
		claim.Job.ProviderChannelID = routeClaim.Route.ChannelID
		claim.Job.ProviderKeyIndex = routeClaim.Route.KeyIndex
		claim.Job.ProviderSubmissionAttempt = routeClaim.Attempt
	}

	nativeTaskID, err := model.PlatformGenerationNativeTaskID(claim.Job.ID)
	if err != nil {
		_, _ = model.ReleasePlatformGenerationProviderRoute(claim.Job.ID, routeClaim.SubmissionToken)
		return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorGenerationFailed, "The relay could not create a stable native task identity", true)
	}
	principal, err := GetPlatformRelayPrincipalForJob(claim.Job.SourceClientID, claim.Job.TenantID)
	if err != nil {
		_, _ = model.ReleasePlatformGenerationProviderRoute(claim.Job.ID, routeClaim.SubmissionToken)
		return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorNoProviderAvailable, "The generation service binding is no longer configured", true)
	}
	body, err := buildPlatformNativeTaskRequestWithContext(ctx, request)
	if err != nil {
		_, _ = model.ReleasePlatformGenerationProviderRoute(claim.Job.ID, routeClaim.SubmissionToken)
		if errors.Is(err, errPlatformGenerationInputAssetUnavailable) {
			return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorInputAssetUnavailable, "The selected reference asset could not be verified for this model", true)
		}
		return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorGenerationFailed, "The relay could not build the native provider request", true)
	}

	providerStarted, statusCode, callErr, leaseErr := runPlatformGenerationSubmissionWithLeaseKeeper(
		ctx,
		*claim,
		platformGenerationSubmissionLease,
		platformGenerationSubmissionLease/3,
		func(submissionContext context.Context) (bool, int, error) {
			return submitPlatformNativeTask(
				submissionContext,
				*claim,
				*routeClaim,
				*principal,
				body,
				runtimeTransport,
			)
		},
	)
	if leaseErr != nil {
		// Ownership could have expired before the provider call. Leave the held
		// route untouched so the replacement worker takes the conservative
		// reconciliation path; a stale worker must not release another owner's
		// admission.
		return true, leaseErr
	}
	assignment := map[string]any{
		"native_task_id":              nativeTaskID,
		"provider_route_id":           routeClaim.Route.ID,
		"provider_channel_id":         routeClaim.Route.ChannelID,
		"provider_key_index":          routeClaim.Route.KeyIndex,
		"provider_submission_attempt": routeClaim.Attempt,
	}
	if callErr != nil || statusCode < 200 || statusCode >= 300 {
		if providerStarted {
			_, markErr := model.MarkPlatformGenerationRouteSubmissionUnknown(claim.Job.ID, routeClaim.SubmissionToken)
			if markErr != nil {
				return true, markErr
			}
			assignment["status"] = model.PlatformGenerationStatusReconciliationRequired
			assignment["error_code"] = model.PlatformGenerationErrorSubmissionReconciliationRequired
			assignment["error_message"] = "The provider submission outcome is unknown and is being reconciled"
			assignment["error_retryable"] = true
			_, completeErr := model.CompletePlatformGenerationSubmission(*claim, assignment, BuildPlatformGenerationCallbackDelivery)
			return true, completeErr
		}
		_, releaseErr := model.ReleasePlatformGenerationProviderRoute(claim.Job.ID, routeClaim.SubmissionToken)
		if releaseErr != nil {
			return true, releaseErr
		}
		message := "The native relay rejected the request before contacting the provider"
		if statusCode == http.StatusTooManyRequests || statusCode >= 500 {
			message = "The native relay was unavailable before contacting the provider"
		}
		return true, failPlatformGenerationSubmission(*claim, model.PlatformGenerationErrorGenerationChannelUnavailable, message, statusCode >= 500)
	}

	nativeTask, err := model.GetPlatformGenerationNativeTask(nativeTaskID, routeClaim.Route.ChannelID)
	if err != nil || nativeTask.PrivateData.UpstreamTaskID == "" {
		_, markErr := model.MarkPlatformGenerationRouteSubmissionUnknown(claim.Job.ID, routeClaim.SubmissionToken)
		if markErr != nil {
			return true, markErr
		}
		assignment["status"] = model.PlatformGenerationStatusReconciliationRequired
		assignment["error_code"] = model.PlatformGenerationErrorSubmissionReconciliationRequired
		assignment["error_message"] = "The native task acknowledgement requires reconciliation"
		assignment["error_retryable"] = true
		_, completeErr := model.CompletePlatformGenerationSubmission(*claim, assignment, BuildPlatformGenerationCallbackDelivery)
		return true, completeErr
	}
	assignment["status"] = model.PlatformGenerationStatusProcessing
	assignment["progress"] = nativeTaskProgress(nativeTask.Progress)
	assignment["upstream_task_id"] = nativeTask.PrivateData.UpstreamTaskID
	assignment["error_code"] = ""
	assignment["error_message"] = ""
	assignment["error_retryable"] = false
	_, err = model.CompletePlatformGenerationSubmission(*claim, assignment, BuildPlatformGenerationCallbackDelivery)
	return true, err
}

// runPlatformGenerationSubmissionWithLeaseKeeper makes stopping the renewal
// goroutine an all-exit invariant. In particular, an unforeseen panic in the
// provider boundary must not be recovered by the outer worker loop while its
// abandoned keeper continues renewing the job and outbox forever.
func runPlatformGenerationSubmissionWithLeaseKeeper(
	parent context.Context,
	claim model.PlatformGenerationClaim,
	lease time.Duration,
	renewEvery time.Duration,
	run func(context.Context) (bool, int, error),
) (providerStarted bool, statusCode int, callErr error, leaseErr error) {
	if run == nil {
		return false, 0, nil, errors.New("generation submission operation is required")
	}
	submissionContext, stopSubmissionLease, err := startPlatformGenerationSubmissionLeaseKeeper(
		parent,
		claim,
		lease,
		renewEvery,
	)
	if err != nil {
		return false, 0, nil, err
	}
	// stopSubmissionLease is idempotent for this single owning goroutine. Keep
	// the defer even though the normal path stops explicitly so panic and every
	// future early return cannot strand the renewal goroutine.
	defer func() { _ = stopSubmissionLease() }()

	providerStarted, statusCode, callErr = run(submissionContext)
	leaseErr = stopSubmissionLease()
	return providerStarted, statusCode, callErr, leaseErr
}

// startPlatformGenerationSubmissionLeaseKeeper keeps both the submission job
// and its durable outbox claim alive while the provider POST is in flight. It
// renews synchronously before returning so time spent selecting a route cannot
// consume the safety margin. A failed or fenced renewal cancels the HTTP
// context. stop performs one final ownership check before any caller mutates
// the route or commits the provider result.
func startPlatformGenerationSubmissionLeaseKeeper(
	parent context.Context,
	claim model.PlatformGenerationClaim,
	lease time.Duration,
	renewEvery time.Duration,
) (context.Context, func() error, error) {
	if parent == nil {
		return nil, nil, errors.New("generation submission context is required")
	}
	if err := parent.Err(); err != nil {
		return nil, nil, err
	}
	if lease < time.Second || lease > 24*time.Hour || renewEvery <= 0 || renewEvery >= lease {
		return nil, nil, errors.New("generation submission lease renewal interval is invalid")
	}
	renewed, err := model.RenewPlatformGenerationSubmission(claim, lease)
	if err != nil {
		return nil, nil, fmt.Errorf("generation submission lease renewal failed: %w", err)
	}
	if !renewed {
		return nil, nil, errPlatformGenerationSubmissionLeaseFenced
	}

	submissionContext, cancel := context.WithCancelCause(parent)
	stopRequested := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		ticker := time.NewTicker(renewEvery)
		defer ticker.Stop()
		for {
			select {
			case <-stopRequested:
				done <- nil
				return
			case <-submissionContext.Done():
				done <- context.Cause(submissionContext)
				return
			case <-ticker.C:
				won, renewErr := model.RenewPlatformGenerationSubmission(claim, lease)
				if renewErr != nil {
					cause := fmt.Errorf("generation submission lease renewal failed: %w", renewErr)
					cancel(cause)
					done <- cause
					return
				}
				if !won {
					cancel(errPlatformGenerationSubmissionLeaseFenced)
					done <- errPlatformGenerationSubmissionLeaseFenced
					return
				}
			}
		}
	}()
	stopped := false
	var stopErr error
	stop := func() error {
		if stopped {
			return stopErr
		}
		stopped = true
		close(stopRequested)
		stopErr = <-done
		if stopErr == nil {
			won, renewErr := model.RenewPlatformGenerationSubmission(claim, lease)
			switch {
			case renewErr != nil:
				stopErr = fmt.Errorf("generation submission final lease renewal failed: %w", renewErr)
			case !won:
				stopErr = errPlatformGenerationSubmissionLeaseFenced
			}
		}
		cancel(context.Canceled)
		return stopErr
	}
	return submissionContext, stop, nil
}

func RunPlatformGenerationPollOnce(ctx context.Context) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	job, token, err := model.ClaimPlatformGenerationPoll(platformGenerationPollLease)
	if err != nil {
		return false, err
	}
	return true, inspectPlatformNativeTask(*job, token, model.PlatformGenerationStatusProcessing)
}

func RunPlatformGenerationReconciliationOnce(ctx context.Context) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	job, token, err := model.ClaimPlatformGenerationReconciliation(platformGenerationPollLease)
	if err != nil {
		return false, err
	}
	nativeTask, lookupErr := loadProtectedPlatformNativeTaskForJob(*job, false)
	if errors.Is(lookupErr, gorm.ErrRecordNotFound) {
		return true, model.ReleasePlatformGenerationReconciliation(job.ID, token, platformGenerationReconcileWait)
	}
	if lookupErr != nil {
		return true, model.ReleasePlatformGenerationReconciliation(job.ID, token, platformGenerationReconcileWait)
	}
	if nativeTask == nil {
		return true, model.ReleasePlatformGenerationReconciliation(job.ID, token, platformGenerationReconcileWait)
	}
	if nativeTask.PrivateData.UpstreamTaskID == "" {
		return true, model.ReleasePlatformGenerationReconciliation(job.ID, token, platformGenerationReconcileWait)
	}
	if model.PlatformGenerationProviderResultReconciliationRequired(nativeTask) {
		return true, inspectClaimedPlatformNativeTask(*job, token, model.PlatformGenerationStatusReconciliationRequired, *nativeTask)
	}
	if nativeTask.Status == model.TaskStatusSuccess || nativeTask.Status == model.TaskStatusFailure {
		return true, inspectClaimedPlatformNativeTask(*job, token, model.PlatformGenerationStatusReconciliationRequired, *nativeTask)
	}
	_, err = model.CompletePlatformGenerationReconciliation(job.ID, token, map[string]any{
		"status":           model.PlatformGenerationStatusProcessing,
		"progress":         nativeTaskProgress(nativeTask.Progress),
		"upstream_task_id": nativeTask.PrivateData.UpstreamTaskID,
		"error_code":       "",
		"error_message":    "",
		"error_retryable":  false,
	}, BuildPlatformGenerationCallbackDelivery)
	return true, err
}

func RunPlatformGenerationTransferOnce(ctx context.Context) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	job, token, err := model.ClaimPlatformGenerationTransfer(platformGenerationTransferLease)
	if err != nil {
		return false, err
	}
	downloader, err := NewPlatformArtifactDownloaderFromEnvironment()
	if err != nil {
		return true, retryOrFailPlatformGenerationTransfer(*job, token, err)
	}
	store, err := NewPlatformArtifactStoreFromEnvironment()
	if err != nil {
		return true, retryOrFailPlatformGenerationTransfer(*job, token, err)
	}
	defer closePlatformGenerationArtifactStore(store)
	if err := transferClaimedPlatformGeneration(ctx, *job, token, downloader, store); err != nil {
		return true, retryOrFailPlatformGenerationTransfer(*job, token, err)
	}
	return true, nil
}

func RunPlatformArtifactCleanupOnce(ctx context.Context) (bool, error) {
	store, err := NewPlatformArtifactStoreFromEnvironment()
	if err != nil {
		return false, err
	}
	defer closePlatformGenerationArtifactStore(store)
	return runPlatformArtifactCleanupOnce(ctx, store, platformArtifactCleanupMaxAttempts)
}

func runPlatformArtifactCleanupOnce(
	ctx context.Context,
	store PlatformArtifactStore,
	maxAttempts int,
) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if store == nil || maxAttempts < 1 || maxAttempts > 100 {
		return false, errors.New("artifact cleanup worker configuration is invalid")
	}
	claim, err := model.ClaimPlatformArtifactCleanup(platformArtifactCleanupLease)
	if err != nil {
		return false, err
	}
	if claim.Intent.StoreKind != store.Kind() || claim.Intent.StoreBindingID != store.BindingID() {
		return true, releasePlatformArtifactCleanupClaim(
			*claim,
			maxAttempts,
			"artifact_store_binding_changed",
			errors.New("artifact cleanup store binding no longer matches the upload intent"),
		)
	}
	deleteContext, cancel := context.WithTimeout(ctx, platformArtifactCleanupTimeout)
	var deleteErr error
	if claim.Intent.StoreVersionID != "" {
		versionedStore, ok := store.(PlatformArtifactVersionedStore)
		if !ok {
			deleteErr = errors.New("artifact store cannot delete the acknowledged object version")
		} else {
			deleteErr = versionedStore.DeleteVersion(
				deleteContext,
				claim.Intent.ObjectKey,
				claim.Intent.StoreVersionID,
			)
		}
	} else {
		deleteErr = store.Delete(deleteContext, claim.Intent.ObjectKey)
	}
	cancel()
	if deleteErr != nil {
		return true, releasePlatformArtifactCleanupClaim(
			*claim,
			maxAttempts,
			"artifact_delete_failed",
			deleteErr,
		)
	}
	won, err := model.CompletePlatformArtifactCleanup(
		claim.Intent.ID,
		claim.Token,
		platformArtifactInitialReapInterval,
		platformArtifactPeriodicReapInterval,
		model.PlatformArtifactCleanupMinimumDeletePasses,
	)
	if err != nil {
		return true, err
	}
	if !won {
		return true, model.ErrPlatformArtifactCleanupLeaseFenced
	}
	return true, nil
}

func releasePlatformArtifactCleanupClaim(
	claim model.PlatformArtifactCleanupClaim,
	maxAttempts int,
	errorCode string,
	cause error,
) error {
	delay := 5 * time.Second
	for attempt := 1; attempt < claim.Intent.Attempts && delay < 5*time.Minute; attempt++ {
		delay *= 2
	}
	if delay > 5*time.Minute {
		delay = 5 * time.Minute
	}
	won, deadLetter, err := model.ReleasePlatformArtifactCleanup(
		claim.Intent.ID,
		claim.Token,
		maxAttempts,
		delay,
		errorCode,
		claim.Intent.CleanedAt != nil ||
			claim.Intent.DeletePasses >= model.PlatformArtifactCleanupMinimumDeletePasses,
	)
	if err != nil {
		return err
	}
	if !won {
		return model.ErrPlatformArtifactCleanupLeaseFenced
	}
	if cause != nil {
		state := "retry scheduled"
		if deadLetter {
			state = "dead-lettered"
		}
		common.SysError("platform artifact cleanup " + state + " for " + claim.Intent.StoreKind + ": " + cause.Error())
	}
	return nil
}

func transferClaimedPlatformGeneration(
	ctx context.Context,
	job model.PlatformGenerationJob,
	token string,
	downloader *PlatformArtifactDownloader,
	store PlatformArtifactStore,
) error {
	return transferClaimedPlatformGenerationWithLeasePolicy(
		ctx,
		job,
		token,
		downloader,
		store,
		platformGenerationTransferLease,
		platformGenerationTransferLease/3,
	)
}

func transferClaimedPlatformGenerationWithLeasePolicy(
	ctx context.Context,
	job model.PlatformGenerationJob,
	token string,
	downloader *PlatformArtifactDownloader,
	store PlatformArtifactStore,
	lease time.Duration,
	renewEvery time.Duration,
) error {
	var request dto.PlatformGenerationRequest
	if err := common.Unmarshal([]byte(job.RequestJSON), &request); err != nil {
		return fmt.Errorf("accepted generation request is unreadable: %w", err)
	}
	if request.Output.Count != 1 {
		return errors.New("native result bridge cannot prove the requested artifact count")
	}
	var temporary platformNativeTemporaryResult
	rawTemporary := []byte(job.TemporaryResultJSON)
	if err := common.RejectDuplicateJSONKeys(rawTemporary); err != nil {
		if job.Mode != "text_to_image" {
			return model.ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		return errors.New("provider result manifest is invalid")
	}
	if err := common.DecodeJsonDisallowUnknownFields(strings.NewReader(job.TemporaryResultJSON), &temporary); err != nil {
		if job.Mode != "text_to_image" {
			return model.ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		return fmt.Errorf("provider result manifest is unreadable: %w", err)
	}
	if strings.TrimSpace(temporary.ResultURL) == "" || temporary.NativeTaskID != job.NativeTaskID {
		if job.Mode != "text_to_image" {
			return model.ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		return errors.New("provider result manifest is incomplete")
	}
	providerProtocol := ""
	var providerResultReceipt *model.PlatformGenerationProviderResultReceipt
	if job.Mode != "text_to_image" {
		profile, found, err := generationprofile.ResolveSnapshot(request.Metadata)
		if err != nil || !found {
			return model.ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		expectedRevision, supported := model.PlatformGenerationProviderResultProofRevision(profile.Protocol)
		if !supported || temporary.ProofContractRevision != expectedRevision ||
			temporary.ReceiptSHA256 == "" || temporary.ReceiptProofSHA256 == "" ||
			temporary.ArtifactURLSHA256 == "" || temporary.TaskData != "" {
			return model.ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		providerProtocol = profile.Protocol
		artifactDigest := sha256.Sum256([]byte(temporary.ResultURL))
		if temporary.ArtifactURLSHA256 != fmt.Sprintf("sha256:%x", artifactDigest) {
			return model.ErrPlatformGenerationProviderMaterialReconciliationRequired
		}
		resolvedReceipt, err := model.ResolvePlatformGenerationProviderResultForTransfer(
			job.ID,
			token,
			temporary.NativeTaskID,
			temporary.ReceiptSHA256,
			temporary.ReceiptProofSHA256,
			temporary.ArtifactURLSHA256,
		)
		if err != nil {
			if errors.Is(err, model.ErrPlatformGenerationProviderMaterialReconciliationRequired) {
				return fmt.Errorf("provider result proof could not be revalidated for transfer: %w", err)
			}
			return err
		}
		providerResultReceipt = &resolvedReceipt
	} else if temporary.ProofContractRevision != "" || temporary.ReceiptSHA256 != "" ||
		temporary.ReceiptProofSHA256 != "" || temporary.ArtifactURLSHA256 != "" {
		return errors.New("synchronous image result contains an unexpected asynchronous proof")
	}
	mediaType := "video"
	if request.Mode == "text_to_image" {
		mediaType = "image"
	}
	assetID := uuid.NewSHA1(uuid.NameSpaceURL, []byte("relay-artifact:"+job.ID+":0")).String()
	storageObjectID := uuid.NewSHA1(
		uuid.NameSpaceURL,
		[]byte("relay-artifact-object:"+job.ID+":"+token+":0"),
	).String()
	objectKey, err := PlatformArtifactObjectKey(job.TenantID, job.ID, storageObjectID)
	if err != nil {
		return err
	}
	if _, err := model.CreatePlatformArtifactUploadIntent(
		job.ID,
		token,
		objectKey,
		store.Kind(),
		store.BindingID(),
	); err != nil {
		return err
	}
	queueCleanup := func(cause error) error {
		_, scheduleErr := model.SchedulePlatformArtifactUploadCleanup(job.ID, token)
		if scheduleErr != nil {
			return errors.Join(cause, fmt.Errorf("durable artifact cleanup scheduling failed: %w", scheduleErr))
		}
		return cause
	}
	transferContext, stopLease, err := startPlatformGenerationTransferLeaseKeeper(
		ctx,
		job.ID,
		token,
		lease,
		renewEvery,
	)
	if err != nil {
		return queueCleanup(err)
	}
	leaseStopped := false
	cleanupScheduled := false
	cleanupRequired := true
	var leaseStopErr error
	stopLeaseOnce := func() error {
		if leaseStopped {
			return leaseStopErr
		}
		leaseStopped = true
		leaseStopErr = stopLease()
		return leaseStopErr
	}
	queueCleanupOnce := func(cause error) error {
		if cleanupScheduled {
			return cause
		}
		cleanupScheduled = true
		return queueCleanup(cause)
	}
	// A panic may escape the downloader, storage implementation, evidence
	// recorder, or final database commit. Stop renewing before the outer worker
	// loop recovers it, and make the pre-created upload intent immediately
	// cleanup-eligible unless publication was proven.
	defer func() {
		_ = stopLeaseOnce()
		if cleanupRequired {
			_ = queueCleanupOnce(nil)
		}
	}()
	stopAndQueueCleanup := func(cause error) error {
		leaseErr := stopLeaseOnce()
		if leaseErr != nil {
			return queueCleanupOnce(leaseErr)
		}
		return queueCleanupOnce(cause)
	}
	transferRequest, err := platformGenerationArtifactTransferRequest(job, request, temporary.ResultURL, storageObjectID, mediaType)
	if err != nil {
		return stopAndQueueCleanup(err)
	}
	if err := bindPlatformGenerationProviderArtifactExpectation(
		&transferRequest,
		providerProtocol,
		providerResultReceipt,
	); err != nil {
		return stopAndQueueCleanup(err)
	}
	authorization, err := platformGenerationArtifactSourceAuthorization(job, temporary.ResultURL, providerProtocol)
	if err != nil {
		return stopAndQueueCleanup(err)
	}
	transferRequest.SourceAuthorization = authorization
	stored, err := TransferPlatformProviderArtifact(transferContext, downloader, store, transferRequest)
	if err != nil {
		return stopAndQueueCleanup(err)
	}
	putRecorded, err := model.RecordPlatformArtifactUploadPut(
		job.ID,
		token,
		stored.ObjectKey,
		stored.VersionID,
	)
	if err != nil {
		cleanupUnpublishedPlatformArtifactVersion(store, stored.ObjectKey, stored.VersionID)
		return stopAndQueueCleanup(fmt.Errorf("durable artifact Put evidence could not be recorded: %w", err))
	}
	if !putRecorded {
		cleanupUnpublishedPlatformArtifactVersion(store, stored.ObjectKey, stored.VersionID)
		return stopAndQueueCleanup(errors.New("durable artifact Put evidence was fenced"))
	}
	outputs := []dto.PlatformGenerationArtifact{{
		AssetID:     assetID,
		ObjectKey:   stored.ObjectKey,
		MediaType:   mediaType,
		ContentType: stored.ContentType,
		SizeBytes:   stored.SizeBytes,
		SHA256:      stored.SHA256,
	}}
	if len(outputs) != request.Output.Count {
		return stopAndQueueCleanup(errors.New("stored artifact count does not match accepted request"))
	}
	serialized, err := common.Marshal(outputs)
	if err != nil {
		return stopAndQueueCleanup(err)
	}
	if err := stopLeaseOnce(); err != nil {
		return queueCleanupOnce(err)
	}
	won, err := model.CompletePlatformGenerationTransfer(
		job.ID,
		token,
		objectKey,
		string(serialized),
		BuildPlatformGenerationCallbackDelivery,
	)
	if err != nil {
		return queueCleanupOnce(err)
	}
	if !won {
		return queueCleanupOnce(errPlatformGenerationTransferLeaseFenced)
	}
	cleanupRequired = false
	return nil
}

// startPlatformGenerationTransferLeaseKeeper renews a live transfer lease
// while provider download and durable storage are in progress. A failed or
// fenced renewal cancels the context consumed by the downloader and OBS body
// reader. stop must be called exactly once before the final fenced commit.
func startPlatformGenerationTransferLeaseKeeper(
	parent context.Context,
	jobID string,
	token string,
	lease time.Duration,
	renewEvery time.Duration,
) (context.Context, func() error, error) {
	if parent == nil {
		return nil, nil, errors.New("generation transfer context is required")
	}
	if err := parent.Err(); err != nil {
		return nil, nil, err
	}
	if lease < time.Second || lease > 24*time.Hour || renewEvery <= 0 || renewEvery >= lease {
		return nil, nil, errors.New("generation transfer lease renewal interval is invalid")
	}
	transferContext, cancel := context.WithCancelCause(parent)
	stopRequested := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		ticker := time.NewTicker(renewEvery)
		defer ticker.Stop()
		for {
			select {
			case <-stopRequested:
				done <- nil
				return
			case <-transferContext.Done():
				done <- context.Cause(transferContext)
				return
			case <-ticker.C:
				won, err := model.RenewPlatformGenerationTransfer(jobID, token, lease)
				if err != nil {
					cause := fmt.Errorf("generation transfer lease renewal failed: %w", err)
					cancel(cause)
					done <- cause
					return
				}
				if !won {
					cancel(errPlatformGenerationTransferLeaseFenced)
					done <- errPlatformGenerationTransferLeaseFenced
					return
				}
			}
		}
	}()
	stopped := false
	var stopErr error
	stop := func() error {
		if stopped {
			return stopErr
		}
		stopped = true
		close(stopRequested)
		stopErr = <-done
		cancel(context.Canceled)
		return stopErr
	}
	return transferContext, stop, nil
}

func platformGenerationArtifactTransferRequest(
	job model.PlatformGenerationJob,
	request dto.PlatformGenerationRequest,
	sourceURL string,
	storageObjectID string,
	mediaType string,
) (PlatformArtifactTransferRequest, error) {
	transferRequest := PlatformArtifactTransferRequest{
		SourceURL: sourceURL,
		TenantID:  job.TenantID,
		JobID:     job.ID,
		AssetID:   storageObjectID,
		MediaType: mediaType,
	}
	profile, hasProfile, err := platformGenerationProfileForStoredJob(job, request)
	if err != nil {
		return PlatformArtifactTransferRequest{}, err
	}
	if request.Mode == "text_to_image" && !hasProfile {
		return PlatformArtifactTransferRequest{}, errors.New("image artifact verification requires an immutable capability profile snapshot")
	}
	if hasProfile {
		if err := profile.ValidateRequest(request); err != nil {
			return PlatformArtifactTransferRequest{}, err
		}
		artifact, ok := profile.Artifact(request.Mode)
		if !ok || artifact.MediaType != mediaType || artifact.Count != request.Output.Count {
			return PlatformArtifactTransferRequest{}, errors.New("artifact does not match the immutable capability profile")
		}
		transferRequest.ExpectedContentType = artifact.ContentType
		transferRequest.ExpectedImageWidth = artifact.Width
		transferRequest.ExpectedImageHeight = artifact.Height
	}
	return transferRequest, nil
}

func bindPlatformGenerationProviderArtifactExpectation(
	request *PlatformArtifactTransferRequest,
	protocol string,
	receipt *model.PlatformGenerationProviderResultReceipt,
) error {
	if protocol != generationprofile.GoogleGeminiInteractionsVideoProtocolV1 {
		return nil
	}
	if request == nil || receipt == nil || receipt.Protocol != protocol ||
		receipt.ArtifactSizeBytes <= 0 {
		return model.ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	normalized, err := normalizePlatformArtifactSHA256(receipt.ArtifactSHA256)
	if err != nil || normalized != receipt.ArtifactSHA256 {
		return model.ErrPlatformGenerationProviderMaterialReconciliationRequired
	}
	expectedSize := receipt.ArtifactSizeBytes
	request.ExpectedSizeBytes = &expectedSize
	request.ExpectedSHA256 = receipt.ArtifactSHA256
	return nil
}

// platformGenerationArtifactSourceAuthorization reconstructs provider
// authentication from the exact encrypted credential version already bound to
// the native task. Google Files download URLs are deliberately persisted
// without an API key; the key exists only in this immediate network boundary.
func platformGenerationArtifactSourceAuthorization(
	job model.PlatformGenerationJob,
	sourceURL string,
	protocol string,
) (*PlatformArtifactDownloadAuthorization, error) {
	switch protocol {
	case "":
		return nil, nil
	case generationprofile.GoogleGeminiInteractionsVideoProtocolV1, generationprofile.GoogleGeminiVeoVideoProtocolV1:
		if err := validateGoogleGeminiFileDownloadURL(sourceURL); err != nil {
			return nil, err
		}
		nativeTask, err := loadProtectedPlatformNativeTaskForJob(job, false)
		if err != nil || nativeTask == nil {
			return nil, fmt.Errorf("%w: Google artifact credential binding is unavailable", ErrPlatformArtifactSecurity)
		}
		providerKey, err := model.ResolveTaskProviderCredential(nativeTask)
		if err != nil || providerKey == "" || providerKey != strings.TrimSpace(providerKey) ||
			len(providerKey) > 4096 || strings.ContainsAny(providerKey, "\x00\r\n") {
			return nil, fmt.Errorf("%w: Google artifact credential is unavailable", ErrPlatformArtifactSecurity)
		}
		return &PlatformArtifactDownloadAuthorization{
			HeaderName:  "x-goog-api-key",
			HeaderValue: providerKey,
			AllowedHost: "generativelanguage.googleapis.com",
		}, nil
	default:
		return nil, nil
	}
}

func validateGoogleGeminiFileDownloadURL(raw string) error {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() != "generativelanguage.googleapis.com" ||
		(parsed.Port() != "" && parsed.Port() != "443") || parsed.User != nil || parsed.Fragment != "" ||
		parsed.RawPath != "" || parsed.ForceQuery || parsed.RawQuery != "alt=media" {
		return fmt.Errorf("%w: Google artifact URL is outside the reviewed Files endpoint", ErrPlatformArtifactSecurity)
	}
	const prefix = "/v1beta/files/"
	if !strings.HasPrefix(parsed.Path, prefix) || !strings.HasSuffix(parsed.Path, ":download") {
		return fmt.Errorf("%w: Google artifact URL is outside the reviewed Files endpoint", ErrPlatformArtifactSecurity)
	}
	fileID := strings.TrimSuffix(strings.TrimPrefix(parsed.Path, prefix), ":download")
	if fileID == "" || len(fileID) > 192 {
		return fmt.Errorf("%w: Google artifact file identity is invalid", ErrPlatformArtifactSecurity)
	}
	for _, character := range fileID {
		if (character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
			(character >= '0' && character <= '9') || character == '-' || character == '_' || character == '.' {
			continue
		}
		return fmt.Errorf("%w: Google artifact file identity is invalid", ErrPlatformArtifactSecurity)
	}
	return nil
}

func retryOrFailPlatformGenerationTransfer(job model.PlatformGenerationJob, token string, cause error) error {
	if errors.Is(cause, model.ErrPlatformGenerationProviderMaterialReconciliationRequired) {
		won, err := model.CompletePlatformGenerationTransferProviderMaterialConflict(
			job.ID,
			token,
			BuildPlatformGenerationCallbackDelivery,
		)
		if err != nil || !won {
			if err != nil {
				return err
			}
			return errPlatformGenerationTransferLeaseFenced
		}
		return nil
	}
	maxAttempts := common.GetEnvOrDefault("RELAY_ARTIFACT_TRANSFER_MAX_ATTEMPTS", 8)
	if maxAttempts < 1 || maxAttempts > 100 {
		maxAttempts = 8
	}
	message := "The generated artifact could not be verified and stored"
	if job.ArtifactTransferAttempts >= maxAttempts {
		won, err := model.FailPlatformGenerationTransfer(
			job.ID,
			token,
			message,
			BuildPlatformGenerationCallbackDelivery,
		)
		if err != nil || !won {
			return err
		}
		return nil
	}
	delay := 5 * time.Second
	for attempt := 1; attempt < job.ArtifactTransferAttempts && delay < 5*time.Minute; attempt++ {
		delay *= 2
	}
	if delay > 5*time.Minute {
		delay = 5 * time.Minute
	}
	won, err := model.ReleasePlatformGenerationTransfer(
		job.ID,
		token,
		delay,
		model.PlatformGenerationErrorArtifactTransferRetrying,
		message,
	)
	if err != nil || !won {
		return err
	}
	// Log only the internal reason. The public job never contains the provider
	// temporary URL or a storage implementation error.
	if cause != nil {
		common.SysError("platform artifact transfer retry: " + cause.Error())
	}
	return nil
}

func RunPlatformGenerationCallbackBackfillOnce(ctx context.Context) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	jobs, err := model.ListPlatformGenerationCallbackBackfillJobs(platformGenerationCallbackBatch)
	if err != nil {
		return false, err
	}
	if len(jobs) == 0 {
		return false, gorm.ErrRecordNotFound
	}
	for _, job := range jobs {
		if _, err := EnqueuePlatformGenerationCallbackForJob(job); err != nil {
			return true, err
		}
		if _, err := model.MarkPlatformGenerationCallbackBackfilled(job.ID, job.Status, job.Progress); err != nil {
			return true, err
		}
	}
	return true, nil
}

func RunPlatformGenerationCallbackOnce(ctx context.Context) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	claim, err := model.ClaimPlatformGenerationCallbackDelivery(platformGenerationCallbackLease)
	if err != nil {
		return false, err
	}
	principal := PlatformRelayPrincipal{}
	configured, lookupErr := GetPlatformRelayPrincipalForJob(
		claim.Delivery.SourceClientID,
		claim.Delivery.TenantID,
	)
	if lookupErr == nil && configured != nil {
		principal = *configured
	}
	_, _, err = DeliverPlatformGenerationCallbackClaim(ctx, *claim, principal)
	return true, err
}

func closePlatformGenerationArtifactStore(store PlatformArtifactStore) {
	if closer, ok := store.(interface{ Close() }); ok {
		closer.Close()
	}
}

func GetPlatformGenerationArtifactSignedDownload(
	ctx context.Context,
	principal PlatformRelayPrincipal,
	jobID string,
	assetID string,
) (dto.PlatformSignedDownload, error) {
	if _, err := uuid.Parse(jobID); err != nil {
		return dto.PlatformSignedDownload{}, gorm.ErrRecordNotFound
	}
	if _, err := uuid.Parse(assetID); err != nil {
		return dto.PlatformSignedDownload{}, gorm.ErrRecordNotFound
	}
	job, err := model.GetPlatformGenerationJob(jobID, principal.TenantID)
	if err != nil || job.Status != model.PlatformGenerationStatusSucceeded {
		if err != nil {
			return dto.PlatformSignedDownload{}, err
		}
		return dto.PlatformSignedDownload{}, gorm.ErrRecordNotFound
	}
	outputs := make([]dto.PlatformGenerationArtifact, 0)
	if err := common.Unmarshal([]byte(job.OutputsJSON), &outputs); err != nil {
		return dto.PlatformSignedDownload{}, err
	}
	var acceptedRequest dto.PlatformGenerationRequest
	if err := common.Unmarshal([]byte(job.RequestJSON), &acceptedRequest); err != nil {
		return dto.PlatformSignedDownload{}, err
	}
	if acceptedRequest.Output.Count != 1 || len(outputs) != acceptedRequest.Output.Count {
		return dto.PlatformSignedDownload{}, errors.New("stored generation output count is invalid")
	}
	objectKey := ""
	for _, output := range outputs {
		if output.AssetID == assetID &&
			ValidatePlatformArtifactObjectKey(output.ObjectKey) == nil &&
			strings.HasPrefix(output.ObjectKey, "outputs/"+principal.TenantID+"/"+jobID+"/") &&
			(output.MediaType == "image" || output.MediaType == "video") &&
			strings.HasPrefix(output.ContentType, output.MediaType+"/") &&
			output.SizeBytes > 0 && len(output.SHA256) == 64 {
			objectKey = output.ObjectKey
			break
		}
	}
	if objectKey == "" {
		return dto.PlatformSignedDownload{}, gorm.ErrRecordNotFound
	}
	store, err := NewPlatformArtifactStoreFromEnvironment()
	if err != nil {
		return dto.PlatformSignedDownload{}, err
	}
	defer closePlatformGenerationArtifactStore(store)
	ttlSeconds := common.GetEnvOrDefault("RELAY_ARTIFACT_SIGNED_URL_TTL_SECONDS", 600)
	if ttlSeconds < 30 || ttlSeconds > 3600 {
		ttlSeconds = 600
	}
	issuedDownload, err := store.IssueSignedDownload(ctx, objectKey, time.Duration(ttlSeconds)*time.Second)
	if err != nil {
		if errors.Is(err, ErrPlatformArtifactNotFound) {
			return dto.PlatformSignedDownload{}, gorm.ErrRecordNotFound
		}
		return dto.PlatformSignedDownload{}, err
	}
	return platformGenerationArtifactDownloadResponse(
		issuedDownload,
		objectKey,
		ttlSeconds,
		PlatformRelayProductionSecurityEnabled(),
	)
}

func platformGenerationArtifactDownloadResponse(
	issued PlatformIssuedArtifactDownload,
	objectKey string,
	ttlSeconds int,
	requireStorageBinding bool,
) (dto.PlatformSignedDownload, error) {
	if strings.TrimSpace(issued.URL) == "" || issued.URL != strings.TrimSpace(issued.URL) {
		return dto.PlatformSignedDownload{}, fmt.Errorf("%w: signed URL is invalid", ErrPlatformArtifactStore)
	}
	if ttlSeconds < 1 || ttlSeconds > int(platformArtifactMaxSignedTTL/time.Second) {
		return dto.PlatformSignedDownload{}, fmt.Errorf("%w: signed URL expiry is outside the allowed range", ErrPlatformArtifactStore)
	}
	response := dto.PlatformSignedDownload{
		APIVersion:     dto.PlatformRelayAPIVersion,
		SchemaVersion:  dto.PlatformRelaySchemaVersion,
		URL:            issued.URL,
		ExpiresSeconds: ttlSeconds,
	}
	if issued.StorageBinding == nil {
		if requireStorageBinding {
			return dto.PlatformSignedDownload{}, fmt.Errorf("%w: production artifact download requires a storage binding", ErrPlatformArtifactConfiguration)
		}
		return response, nil
	}
	binding := issued.StorageBinding
	if binding.Provider != PlatformArtifactHuaweiOBSKind ||
		binding.EndpointHost != strings.ToLower(binding.EndpointHost) ||
		strings.HasSuffix(binding.EndpointHost, ".") ||
		!platformHuaweiOBSEndpointHostPattern.MatchString(binding.EndpointHost) ||
		!platformHuaweiOBSBucketPattern.MatchString(binding.Bucket) ||
		binding.ObjectKey != objectKey ||
		ValidatePlatformArtifactObjectKey(binding.ObjectKey) != nil ||
		binding.IssuedAt.IsZero() ||
		binding.ExpiresAt.IsZero() ||
		!platformArtifactTimeIsUTC(binding.IssuedAt) ||
		!platformArtifactTimeIsUTC(binding.ExpiresAt) ||
		binding.IssuedAt.Nanosecond() != 0 ||
		binding.ExpiresAt.Nanosecond() != 0 ||
		binding.ExpiresAt.Sub(binding.IssuedAt) != time.Duration(ttlSeconds)*time.Second {
		return dto.PlatformSignedDownload{}, fmt.Errorf("%w: artifact storage binding is invalid", ErrPlatformArtifactStore)
	}
	urlDigest := sha256.Sum256([]byte(issued.URL))
	if binding.URLSHA256 != hex.EncodeToString(urlDigest[:]) {
		return dto.PlatformSignedDownload{}, fmt.Errorf("%w: artifact storage binding URL digest is invalid", ErrPlatformArtifactStore)
	}
	response.StorageBinding = &dto.PlatformArtifactStorageBinding{
		Provider:     binding.Provider,
		EndpointHost: binding.EndpointHost,
		Bucket:       binding.Bucket,
		ObjectKey:    binding.ObjectKey,
		IssuedAt:     platformArtifactRFC3339UTC(binding.IssuedAt),
		ExpiresAt:    platformArtifactRFC3339UTC(binding.ExpiresAt),
		URLSHA256:    binding.URLSHA256,
	}
	return response, nil
}

func platformArtifactRFC3339UTC(value time.Time) string {
	return value.UTC().Format("2006-01-02T15:04:05") + "+00:00"
}

func platformArtifactTimeIsUTC(value time.Time) bool {
	_, offset := value.Zone()
	return offset == 0
}

func OpenPlatformGenerationFilesystemArtifact(
	ctx context.Context,
	objectKey string,
	expires int64,
	signature string,
) (*PlatformOpenedArtifact, error) {
	store, err := NewPlatformArtifactStoreFromEnvironment()
	if err != nil {
		return nil, err
	}
	filesystem, ok := store.(*PlatformFilesystemArtifactStore)
	if !ok {
		closePlatformGenerationArtifactStore(store)
		return nil, ErrPlatformArtifactNotFound
	}
	return filesystem.OpenSigned(ctx, objectKey, expires, signature)
}

func ResolvePlatformGenerationUnknownSubmission(
	tenantID string,
	jobID string,
	request dto.PlatformGenerationReconciliationRequest,
	requestID string,
) (dto.PlatformGenerationSnapshot, dto.PlatformGenerationReconciliationResult, bool, error) {
	if _, err := uuid.Parse(tenantID); err != nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, gorm.ErrRecordNotFound
	}
	if _, err := uuid.Parse(jobID); err != nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, gorm.ErrRecordNotFound
	}
	created := false
	switch request.Outcome {
	case "created":
		created = true
	case "not_created":
	default:
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, model.ErrPlatformGenerationReconciliationConflict
	}
	if existing, err := model.GetPlatformGenerationReconciliationReceiptByOperation(
		tenantID,
		request.OperationID,
	); err == nil {
		if existing.Event.JobID != jobID {
			return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, model.ErrPlatformGenerationReconciliationConflict
		}
		return replayPlatformGenerationReconciliation(
			tenantID,
			jobID,
			request,
			requestID,
			existing.Event,
		)
	} else if !errors.Is(err, gorm.ErrRecordNotFound) {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, err
	}
	var synchronousResult *model.PlatformGenerationSynchronousResultEvidence
	if created {
		candidate, err := model.GetPlatformGenerationSubmissionUnknown(jobID, tenantID)
		if err != nil {
			return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, err
		}
		synchronousResult, err = validatePlatformGenerationCreatedReconciliation(*candidate, request)
		if err != nil {
			return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, err
		}
	} else if request.SynchronousResult != nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, model.ErrPlatformGenerationReconciliationConflict
	}
	job, event, idempotentReplay, err := model.ResolvePlatformGenerationSubmissionUnknown(
		jobID,
		tenantID,
		model.PlatformGenerationReconciliationResolution{
			Created:                     created,
			UpstreamTaskID:              request.UpstreamTaskID,
			ExpectedRouteID:             request.ExpectedRouteID,
			ExpectedSubmissionAttempt:   request.ExpectedSubmissionAttempt,
			ExpectedReconciliationToken: request.ExpectedReconciliationToken,
			OperationID:                 request.OperationID,
			RequestID:                   requestID,
			VerificationReference:       request.VerificationReference,
			ApprovedBy:                  request.ApprovedBy,
			ApprovalReason:              request.ApprovalReason,
			ApprovalKeyID:               request.ApprovalKeyID,
			ApprovalSignature:           request.ApprovalSignature,
			SynchronousResult:           synchronousResult,
		},
		BuildPlatformGenerationCallbackDelivery,
	)
	if err != nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, err
	}
	snapshot, err := platformGenerationSnapshot(*job)
	if err != nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, err
	}
	if event == nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, fmt.Errorf("generation reconciliation receipt was not committed")
	}
	reconciliationResult, err := platformGenerationReconciliationResult(*event, snapshot.Status)
	if err != nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, err
	}
	return snapshot, reconciliationResult, idempotentReplay, nil
}

func replayPlatformGenerationReconciliation(
	tenantID string,
	jobID string,
	request dto.PlatformGenerationReconciliationRequest,
	requestID string,
	existing model.PlatformGenerationReconciliationEvent,
) (dto.PlatformGenerationSnapshot, dto.PlatformGenerationReconciliationResult, bool, error) {
	var synchronousResult *model.PlatformGenerationSynchronousResultEvidence
	if request.Outcome == "created" {
		var err error
		synchronousResult, err = platformGenerationSynchronousResultReplayEvidence(existing, request)
		if err != nil {
			return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, err
		}
	} else if request.SynchronousResult != nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, model.ErrPlatformGenerationReconciliationConflict
	}
	job, event, idempotentReplay, err := model.ResolvePlatformGenerationSubmissionUnknown(
		jobID,
		tenantID,
		platformGenerationReconciliationResolution(request, requestID, synchronousResult),
		BuildPlatformGenerationCallbackDelivery,
	)
	if err != nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, err
	}
	if !idempotentReplay || event == nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, model.ErrPlatformGenerationReconciliationConflict
	}
	snapshot, err := platformGenerationSnapshot(*job)
	if err != nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, err
	}
	result, err := platformGenerationReconciliationResult(*event, snapshot.Status)
	if err != nil {
		return dto.PlatformGenerationSnapshot{}, dto.PlatformGenerationReconciliationResult{}, false, err
	}
	return snapshot, result, true, nil
}

func platformGenerationSynchronousResultReplayEvidence(
	event model.PlatformGenerationReconciliationEvent,
	request dto.PlatformGenerationReconciliationRequest,
) (*model.PlatformGenerationSynchronousResultEvidence, error) {
	stored, err := model.PlatformGenerationReconciliationSynchronousResult(event)
	if err != nil {
		return nil, err
	}
	requested := request.SynchronousResult
	if stored == nil || requested == nil {
		if stored == nil && requested == nil {
			return nil, nil
		}
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	artifactURL := strings.TrimSpace(requested.ArtifactURL)
	parsedURL, urlErr := url.Parse(artifactURL)
	providerCreatedAt, createdErr := time.Parse(time.RFC3339, requested.ProviderCreatedAt)
	artifactDigest := sha256.Sum256([]byte(artifactURL))
	if urlErr != nil || artifactURL != requested.ArtifactURL || len(artifactURL) > 8192 ||
		parsedURL.Scheme != "https" || parsedURL.Host == "" || parsedURL.User != nil || parsedURL.Fragment != "" ||
		createdErr != nil || requested.ProviderCreatedAt != providerCreatedAt.UTC().Format(time.RFC3339) ||
		requested.ProviderModelID != stored.ProviderModelID ||
		requested.ProviderResponseSHA256 != stored.ProviderResponseSHA256 ||
		hex.EncodeToString(artifactDigest[:]) != stored.ArtifactURLSHA256 ||
		!providerCreatedAt.Equal(stored.ProviderCreatedAt) ||
		requested.GeneratedImages != stored.GeneratedImages || requested.OutputTokens != stored.OutputTokens ||
		requested.TotalTokens != stored.TotalTokens {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	return &model.PlatformGenerationSynchronousResultEvidence{
		ProviderModelID:        requested.ProviderModelID,
		ProviderResponseSHA256: requested.ProviderResponseSHA256,
		ResultURL:              artifactURL,
		// Size is validated and used only while reconstructing a first native
		// task. On replay, the immutable receipt has already committed all
		// public synchronous fields and model.Resolve returns before mutation.
		Size:              "receipt-replay",
		ProviderCreatedAt: providerCreatedAt.UTC(),
		GeneratedImages:   requested.GeneratedImages,
		OutputTokens:      requested.OutputTokens,
		TotalTokens:       requested.TotalTokens,
	}, nil
}

func platformGenerationReconciliationResolution(
	request dto.PlatformGenerationReconciliationRequest,
	requestID string,
	synchronousResult *model.PlatformGenerationSynchronousResultEvidence,
) model.PlatformGenerationReconciliationResolution {
	return model.PlatformGenerationReconciliationResolution{
		Created:                     request.Outcome == "created",
		UpstreamTaskID:              request.UpstreamTaskID,
		ExpectedRouteID:             request.ExpectedRouteID,
		ExpectedSubmissionAttempt:   request.ExpectedSubmissionAttempt,
		ExpectedReconciliationToken: request.ExpectedReconciliationToken,
		OperationID:                 request.OperationID,
		RequestID:                   requestID,
		VerificationReference:       request.VerificationReference,
		ApprovedBy:                  request.ApprovedBy,
		ApprovalReason:              request.ApprovalReason,
		ApprovalKeyID:               request.ApprovalKeyID,
		ApprovalSignature:           request.ApprovalSignature,
		SynchronousResult:           synchronousResult,
	}
}

func validatePlatformGenerationCreatedReconciliation(
	candidate model.PlatformGenerationReconciliationCandidate,
	request dto.PlatformGenerationReconciliationRequest,
) (*model.PlatformGenerationSynchronousResultEvidence, error) {
	job := candidate.Job
	var accepted dto.PlatformGenerationRequest
	if err := common.Unmarshal([]byte(job.RequestJSON), &accepted); err != nil || accepted.Mode != job.Mode {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	if accepted.Mode != "text_to_image" {
		if request.SynchronousResult != nil {
			return nil, model.ErrPlatformGenerationReconciliationConflict
		}
		return nil, nil
	}
	evidence := request.SynchronousResult
	if evidence == nil || candidate.Route.ID != request.ExpectedRouteID ||
		candidate.Route.UpstreamModel != evidence.ProviderModelID ||
		request.UpstreamTaskID != "seedream:"+evidence.ProviderResponseSHA256 {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	responseDigest, digestErr := hex.DecodeString(evidence.ProviderResponseSHA256)
	if digestErr != nil || len(responseDigest) != sha256.Size || strings.ToLower(evidence.ProviderResponseSHA256) != evidence.ProviderResponseSHA256 {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	artifactURL := strings.TrimSpace(evidence.ArtifactURL)
	parsedURL, urlErr := url.Parse(artifactURL)
	if urlErr != nil || artifactURL != evidence.ArtifactURL || len(artifactURL) > 8192 || parsedURL.Scheme != "https" ||
		parsedURL.Host == "" || parsedURL.User != nil || parsedURL.Fragment != "" {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	providerCreatedAt, createdErr := time.Parse(time.RFC3339, evidence.ProviderCreatedAt)
	if createdErr != nil || evidence.ProviderCreatedAt != providerCreatedAt.UTC().Format(time.RFC3339) ||
		providerCreatedAt.Before(job.CreatedAt.UTC().Add(-5*time.Minute)) || providerCreatedAt.After(time.Now().UTC().Add(5*time.Minute)) {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	profile, hasProfile, profileErr := platformGenerationProfileForStoredJob(job, accepted)
	if profileErr != nil || !hasProfile || profile.Image == nil {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	if err := profile.ValidateRequest(accepted); err != nil {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	artifact, hasArtifact := profile.Artifact(accepted.Mode)
	if !hasArtifact || artifact.MediaType != "image" || artifact.ContentType != "image/png" ||
		artifact.Count != accepted.Output.Count || evidence.GeneratedImages != artifact.Count ||
		evidence.OutputTokens < 0 || evidence.TotalTokens < evidence.OutputTokens {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	// A manual created decision may not turn a missing provider price into an
	// implicit zero-cost fact. Require a versioned contract rate that covers the
	// provider occurrence before committing the terminal provider evidence.
	facts := model.PlatformProviderCostMaterializationFacts{
		Job:   job,
		Route: candidate.Route,
		Outcome: model.PlatformProviderTerminalOutcome{
			OccurredAt: providerCreatedAt.UTC(),
		},
	}
	rate, rateErr := model.FindPlatformProviderContractRate(facts, accepted.Output.Resolution)
	if rateErr != nil {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	if _, costErr := calculatePlatformProviderContractCost(*rate, accepted.Output); costErr != nil {
		return nil, model.ErrPlatformGenerationReconciliationConflict
	}
	return &model.PlatformGenerationSynchronousResultEvidence{
		ProviderModelID:        evidence.ProviderModelID,
		ProviderResponseSHA256: evidence.ProviderResponseSHA256,
		ResultURL:              evidence.ArtifactURL,
		Size:                   profile.Image.Size,
		ProviderCreatedAt:      providerCreatedAt.UTC(),
		GeneratedImages:        evidence.GeneratedImages,
		OutputTokens:           evidence.OutputTokens,
		TotalTokens:            evidence.TotalTokens,
	}, nil
}

func platformGenerationReconciliationResult(
	event model.PlatformGenerationReconciliationEvent,
	currentStatus string,
) (dto.PlatformGenerationReconciliationResult, error) {
	result := dto.PlatformGenerationReconciliationResult{
		APIVersion:                  dto.PlatformRelayAPIVersion,
		SchemaVersion:               dto.PlatformRelaySchemaVersion,
		Object:                      "generation.reconciliation_result",
		EventID:                     event.ID,
		OperationID:                 event.OperationID,
		RequestID:                   event.RequestID,
		TenantID:                    event.TenantID,
		JobID:                       event.JobID,
		Outcome:                     event.Outcome,
		UpstreamTaskID:              event.UpstreamTaskID,
		ExpectedRouteID:             event.ExpectedRouteID,
		ExpectedSubmissionAttempt:   event.ExpectedSubmissionAttempt,
		ExpectedReconciliationToken: event.ExpectedReconciliationToken,
		VerificationReference:       event.VerificationReference,
		ApprovedBy:                  event.ApprovedBy,
		ApprovalReason:              event.ApprovalReason,
		ApprovalKeyID:               event.ApprovalKeyID,
		ApprovalSignature:           event.ApprovalSignature,
		ResolvedStatus:              event.ResolvedStatus,
		CurrentStatus:               currentStatus,
		PayloadSHA256:               event.PayloadSHA256,
		ResolvedAt:                  event.ResolvedAt,
	}
	synchronous, err := model.PlatformGenerationReconciliationSynchronousResult(event)
	if err != nil {
		return dto.PlatformGenerationReconciliationResult{}, err
	}
	if synchronous != nil {
		result.SynchronousResult = &dto.PlatformGenerationSynchronousResultReceipt{
			ProviderModelID:        synchronous.ProviderModelID,
			ProviderResponseSHA256: synchronous.ProviderResponseSHA256,
			ArtifactURLSHA256:      synchronous.ArtifactURLSHA256,
			ProviderCreatedAt:      synchronous.ProviderCreatedAt.UTC().Format(time.RFC3339),
			GeneratedImages:        synchronous.GeneratedImages,
			OutputTokens:           synchronous.OutputTokens,
			TotalTokens:            synchronous.TotalTokens,
		}
	}
	return result, nil
}

func GetPlatformGenerationReconciliationResult(
	tenantID string,
	jobID string,
	operationID string,
) (dto.PlatformGenerationReconciliationResult, error) {
	if _, err := uuid.Parse(tenantID); err != nil {
		return dto.PlatformGenerationReconciliationResult{}, gorm.ErrRecordNotFound
	}
	if _, err := uuid.Parse(jobID); err != nil {
		return dto.PlatformGenerationReconciliationResult{}, gorm.ErrRecordNotFound
	}
	receipt, err := model.GetPlatformGenerationReconciliationReceipt(jobID, tenantID, operationID)
	if err != nil {
		return dto.PlatformGenerationReconciliationResult{}, err
	}
	return platformGenerationReconciliationResult(receipt.Event, receipt.CurrentStatus)
}

func platformGenerationReconciliationItem(
	candidate model.PlatformGenerationReconciliationCandidate,
) dto.PlatformGenerationReconciliationItem {
	return dto.PlatformGenerationReconciliationItem{
		APIVersion:                dto.PlatformRelayAPIVersion,
		SchemaVersion:             dto.PlatformRelaySchemaVersion,
		Object:                    "generation.reconciliation",
		JobID:                     candidate.Job.ID,
		TenantID:                  candidate.Job.TenantID,
		ClientReferenceID:         candidate.Job.ClientReferenceID,
		Model:                     candidate.Job.Model,
		Mode:                      candidate.Job.Mode,
		Status:                    candidate.Job.Status,
		ProviderRouteID:           candidate.Route.ID,
		ProviderRouteKey:          candidate.Route.RouteKey,
		ProviderName:              candidate.Route.ProviderName,
		ProviderAccountID:         candidate.Route.AccountID,
		ProviderChannelID:         candidate.Route.ChannelID,
		ProviderKeyIndex:          candidate.Route.KeyIndex,
		ProviderChannelClass:      candidate.Route.ChannelClass,
		ProviderUpstreamModel:     candidate.Route.UpstreamModel,
		ProviderSubmissionAttempt: candidate.Admission.Attempt,
		UnknownAt:                 candidate.Admission.UnknownAt,
		ReconciliationToken:       candidate.ReconciliationToken,
		ErrorCode:                 candidate.Job.ErrorCode,
		ErrorMessage:              candidate.Job.ErrorMessage,
		CreatedAt:                 candidate.Job.CreatedAt,
		UpdatedAt:                 candidate.Job.UpdatedAt,
	}
}

func GetPlatformGenerationUnknownSubmission(
	tenantID string,
	jobID string,
) (dto.PlatformGenerationReconciliationItem, error) {
	if _, err := uuid.Parse(tenantID); err != nil {
		return dto.PlatformGenerationReconciliationItem{}, gorm.ErrRecordNotFound
	}
	if _, err := uuid.Parse(jobID); err != nil {
		return dto.PlatformGenerationReconciliationItem{}, gorm.ErrRecordNotFound
	}
	candidate, err := model.GetPlatformGenerationSubmissionUnknown(jobID, tenantID)
	if err != nil {
		return dto.PlatformGenerationReconciliationItem{}, err
	}
	return platformGenerationReconciliationItem(*candidate), nil
}

func ListPlatformGenerationUnknownSubmissions(
	tenantID string,
	page int,
	pageSize int,
) (dto.PlatformGenerationReconciliationPage, error) {
	if _, err := uuid.Parse(tenantID); err != nil {
		return dto.PlatformGenerationReconciliationPage{}, gorm.ErrRecordNotFound
	}
	candidates, total, err := model.ListPlatformGenerationSubmissionUnknown(
		tenantID,
		page,
		pageSize,
	)
	if err != nil {
		return dto.PlatformGenerationReconciliationPage{}, err
	}
	items := make([]dto.PlatformGenerationReconciliationItem, 0, len(candidates))
	for _, candidate := range candidates {
		items = append(items, platformGenerationReconciliationItem(candidate))
	}
	return dto.PlatformGenerationReconciliationPage{
		APIVersion:    dto.PlatformRelayAPIVersion,
		SchemaVersion: dto.PlatformRelaySchemaVersion,
		Object:        "list",
		Data:          items,
		Page:          page,
		PageSize:      pageSize,
		Total:         total,
	}, nil
}

func platformGenerationProviderResultReconciliationItem(
	job model.PlatformGenerationJob,
) (dto.PlatformGenerationProviderResultReconciliationItem, error) {
	evidenceRetained, err := model.PlatformGenerationProviderReconciliationEvidenceRetained(job)
	if err != nil {
		return dto.PlatformGenerationProviderResultReconciliationItem{}, err
	}
	kind := model.PlatformGenerationProviderReconciliationKind(job)
	errorCode := ""
	if job.ErrorCode == model.PlatformGenerationErrorProviderPollReconciliationRequired {
		errorCode = job.ErrorCode
	}
	errorMessage := "Provider result evidence requires manual reconciliation"
	switch kind {
	case model.PlatformGenerationProviderReconciliationKindMissingNativeTask:
		errorMessage = "Native task evidence is unavailable and requires manual reconciliation"
	case model.PlatformGenerationProviderReconciliationKindProviderResultProof:
		errorMessage = "Provider terminal proof requires manual reconciliation"
	case model.PlatformGenerationProviderReconciliationKindProviderMaterial:
		errorMessage = "Retained provider material requires manual reconciliation"
	}
	return dto.PlatformGenerationProviderResultReconciliationItem{
		APIVersion:                dto.PlatformRelayAPIVersion,
		SchemaVersion:             dto.PlatformRelaySchemaVersion,
		Object:                    "generation.provider_result_reconciliation",
		JobID:                     job.ID,
		TenantID:                  job.TenantID,
		ClientReferenceID:         job.ClientReferenceID,
		Model:                     job.Model,
		Mode:                      job.Mode,
		Status:                    job.Status,
		Progress:                  job.Progress,
		ProviderRouteID:           job.ProviderRouteID,
		ProviderChannelID:         job.ProviderChannelID,
		ProviderSubmissionAttempt: job.ProviderSubmissionAttempt,
		UpstreamTaskID:            job.UpstreamTaskID,
		ReconciliationKind:        kind,
		ResolutionSupported:       false,
		EvidenceRetained:          evidenceRetained,
		// A terminal provider-material marker deliberately leaves the successful
		// job's error fact empty. Project that persisted fact instead of
		// fabricating a poll error for an already-published canonical artifact.
		ErrorCode:    errorCode,
		ErrorMessage: errorMessage,
		CreatedAt:    job.CreatedAt,
		UpdatedAt:    job.UpdatedAt,
	}, nil
}

func GetPlatformGenerationProviderResultReconciliation(
	tenantID string,
	jobID string,
) (dto.PlatformGenerationProviderResultReconciliationItem, error) {
	if _, err := uuid.Parse(tenantID); err != nil {
		return dto.PlatformGenerationProviderResultReconciliationItem{}, gorm.ErrRecordNotFound
	}
	if _, err := uuid.Parse(jobID); err != nil {
		return dto.PlatformGenerationProviderResultReconciliationItem{}, gorm.ErrRecordNotFound
	}
	job, err := model.GetPlatformGenerationProviderResultReconciliation(jobID, tenantID)
	if err != nil {
		return dto.PlatformGenerationProviderResultReconciliationItem{}, err
	}
	return platformGenerationProviderResultReconciliationItem(*job)
}

func ListPlatformGenerationProviderResultReconciliations(
	tenantID string,
	page int,
	pageSize int,
) (dto.PlatformGenerationProviderResultReconciliationPage, error) {
	if _, err := uuid.Parse(tenantID); err != nil {
		return dto.PlatformGenerationProviderResultReconciliationPage{}, gorm.ErrRecordNotFound
	}
	jobs, total, err := model.ListPlatformGenerationProviderResultReconciliations(tenantID, page, pageSize)
	if err != nil {
		return dto.PlatformGenerationProviderResultReconciliationPage{}, err
	}
	items := make([]dto.PlatformGenerationProviderResultReconciliationItem, 0, len(jobs))
	for _, job := range jobs {
		item, err := platformGenerationProviderResultReconciliationItem(job)
		if err != nil {
			return dto.PlatformGenerationProviderResultReconciliationPage{}, err
		}
		items = append(items, item)
	}
	return dto.PlatformGenerationProviderResultReconciliationPage{
		APIVersion:    dto.PlatformRelayAPIVersion,
		SchemaVersion: dto.PlatformRelaySchemaVersion,
		Object:        "list",
		Data:          items,
		Page:          page,
		PageSize:      pageSize,
		Total:         total,
	}, nil
}

func inspectPlatformNativeTask(job model.PlatformGenerationJob, token string, fromStatus string) error {
	nativeTask, err := loadProtectedPlatformNativeTaskForJob(job, true)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		if model.RelayDatabaseRoleAttestationRequired() {
			_, completeErr := model.CompletePlatformGenerationMissingNativeTask(
				job.ID,
				token,
				BuildPlatformGenerationCallbackDelivery,
			)
			return completeErr
		}
		_, completeErr := model.CompletePlatformGenerationPoll(job.ID, token, map[string]any{
			"status":          model.PlatformGenerationStatusReconciliationRequired,
			"error_code":      model.PlatformGenerationErrorProviderPollReconciliationRequired,
			"error_message":   "The native task row is temporarily unavailable",
			"error_retryable": true,
			"error_details_json": model.PlatformGenerationProviderReconciliationDetailsJSON(
				model.PlatformGenerationProviderReconciliationKindMissingNativeTask,
			),
		}, BuildPlatformGenerationCallbackDelivery)
		return completeErr
	}
	if err != nil {
		return model.ReleasePlatformGenerationPoll(job.ID, token, platformGenerationPollInterval)
	}
	if nativeTask == nil {
		_, completeErr := model.CompletePlatformGenerationMissingNativeTask(
			job.ID,
			token,
			BuildPlatformGenerationCallbackDelivery,
		)
		return completeErr
	}
	return inspectClaimedPlatformNativeTask(job, token, fromStatus, *nativeTask)
}

func inspectClaimedPlatformNativeTask(job model.PlatformGenerationJob, token string, fromStatus string, nativeTask model.Task) error {
	if model.PlatformGenerationProviderResultReconciliationRequired(&nativeTask) {
		_, err := model.CompletePlatformGenerationProviderProofConflict(
			job.ID,
			token,
			fromStatus,
			BuildPlatformGenerationCallbackDelivery,
		)
		return err
	}
	switch nativeTask.Status {
	case model.TaskStatusSuccess:
		if job.Mode != "text_to_image" {
			transition, receipt, err := platformGenerationProviderResultTransition(job, nativeTask, true)
			if err != nil {
				_, conflictErr := model.CompletePlatformGenerationProviderProofConflict(
					job.ID,
					token,
					fromStatus,
					BuildPlatformGenerationCallbackDelivery,
				)
				return conflictErr
			}
			failureThreshold, cooldown, err := platformGenerationProviderFailurePolicy()
			if err != nil {
				return err
			}
			temporary, err := common.Marshal(platformNativeTemporaryResult{
				NativeTaskID:          nativeTask.TaskID,
				ResultURL:             nativeTask.PrivateData.ResultURL,
				ProofContractRevision: receipt.ProofContractRevision,
				ReceiptSHA256:         transition.ReceiptSHA256,
				ReceiptProofSHA256:    receipt.ProofSHA256,
				ArtifactURLSHA256:     transition.ArtifactURLSHA256,
			})
			if err != nil {
				return err
			}
			outcome, err := platformGenerationProviderTerminalOutcome(job, nativeTask, true, &receipt)
			if err != nil {
				return err
			}
			_, err = model.CompletePlatformGenerationTerminalWithProviderResultProofPolicy(
				job.ID,
				token,
				fromStatus,
				map[string]any{
					"status":                    model.PlatformGenerationStatusTransferring,
					"progress":                  95,
					"temporary_result_json":     string(temporary),
					"error_code":                "",
					"error_message":             "",
					"error_retryable":           false,
					"callback_backfill_pending": true,
				},
				outcome,
				failureThreshold,
				cooldown,
				transition,
			)
			if errors.Is(err, model.ErrPlatformGenerationProviderMaterialReconciliationRequired) {
				_, conflictErr := model.CompletePlatformGenerationProviderProofConflict(
					job.ID,
					token,
					fromStatus,
					BuildPlatformGenerationCallbackDelivery,
				)
				return conflictErr
			}
			return err
		}
		failureThreshold, cooldown, err := platformGenerationProviderFailurePolicy()
		if err != nil {
			return err
		}
		temporary, err := common.Marshal(platformNativeTemporaryResult{
			NativeTaskID: nativeTask.TaskID,
			ResultURL:    nativeTask.PrivateData.ResultURL,
			TaskData:     string(nativeTask.Data),
		})
		if err != nil {
			return err
		}
		outcome, err := platformGenerationProviderTerminalOutcome(job, nativeTask, true)
		if err != nil {
			return err
		}
		_, err = model.CompletePlatformGenerationTerminalWithOutcomePolicy(job.ID, token, fromStatus, map[string]any{
			"status":                    model.PlatformGenerationStatusTransferring,
			"progress":                  95,
			"temporary_result_json":     string(temporary),
			"error_code":                "",
			"error_message":             "",
			"error_retryable":           false,
			"callback_backfill_pending": true,
		}, outcome, failureThreshold, cooldown)
		return err
	case model.TaskStatusFailure:
		if job.Mode != "text_to_image" {
			transition, receipt, err := platformGenerationProviderResultTransition(job, nativeTask, false)
			if err != nil {
				_, conflictErr := model.CompletePlatformGenerationProviderProofConflict(
					job.ID,
					token,
					fromStatus,
					BuildPlatformGenerationCallbackDelivery,
				)
				return conflictErr
			}
			failureThreshold, cooldown, err := platformGenerationProviderFailurePolicy()
			if err != nil {
				return err
			}
			outcome, err := platformGenerationProviderTerminalOutcome(job, nativeTask, false, &receipt)
			if err != nil {
				return err
			}
			_, err = model.CompletePlatformGenerationTerminalWithProviderResultProofPolicy(
				job.ID,
				token,
				fromStatus,
				map[string]any{
					"status":                    model.PlatformGenerationStatusFailed,
					"progress":                  100,
					"error_code":                platformGenerationProviderFailurePublicError(receipt),
					"error_message":             "The provider reported that generation failed",
					"error_retryable":           false,
					"callback_backfill_pending": true,
				},
				outcome,
				failureThreshold,
				cooldown,
				transition,
			)
			if errors.Is(err, model.ErrPlatformGenerationProviderMaterialReconciliationRequired) {
				_, conflictErr := model.CompletePlatformGenerationProviderProofConflict(
					job.ID,
					token,
					fromStatus,
					BuildPlatformGenerationCallbackDelivery,
				)
				return conflictErr
			}
			return err
		}
		failureThreshold, cooldown, err := platformGenerationProviderFailurePolicy()
		if err != nil {
			return err
		}
		outcome, err := platformGenerationProviderTerminalOutcome(job, nativeTask, false)
		if err != nil {
			return err
		}
		_, err = model.CompletePlatformGenerationTerminalWithOutcomePolicy(job.ID, token, fromStatus, map[string]any{
			"status":                    model.PlatformGenerationStatusFailed,
			"progress":                  100,
			"error_code":                model.PlatformGenerationErrorUpstreamFailed,
			"error_message":             "The provider reported that generation failed",
			"error_retryable":           false,
			"callback_backfill_pending": true,
		}, outcome, failureThreshold, cooldown)
		return err
	default:
		if fromStatus == model.PlatformGenerationStatusReconciliationRequired {
			_, err := model.CompletePlatformGenerationReconciliation(job.ID, token, map[string]any{
				"status":           model.PlatformGenerationStatusProcessing,
				"progress":         nativeTaskProgress(nativeTask.Progress),
				"upstream_task_id": nativeTask.PrivateData.UpstreamTaskID,
				"error_code":       "",
				"error_message":    "",
				"error_retryable":  false,
			}, BuildPlatformGenerationCallbackDelivery)
			return err
		}
		return model.ReleasePlatformGenerationPoll(job.ID, token, platformGenerationPollInterval)
	}
}

func platformGenerationProviderResultTransition(
	job model.PlatformGenerationJob,
	nativeTask model.Task,
	succeeded bool,
) (model.PlatformGenerationProviderResultTransition, model.PlatformGenerationProviderResultReceipt, error) {
	if job.ID == "" || nativeTask.TaskID != job.NativeTaskID ||
		(succeeded && nativeTask.Status != model.TaskStatusSuccess) ||
		(!succeeded && nativeTask.Status != model.TaskStatusFailure) || nativeTask.Progress != "100%" {
		return model.PlatformGenerationProviderResultTransition{}, model.PlatformGenerationProviderResultReceipt{},
			errors.New("provider result transition identity is inconsistent")
	}
	receipt, err := model.DecodePlatformGenerationProviderResultReceipt(nativeTask.Data)
	if err != nil {
		return model.PlatformGenerationProviderResultTransition{}, model.PlatformGenerationProviderResultReceipt{}, err
	}
	binding, err := model.ResolvePlatformGenerationProviderResultBinding(nativeTask)
	if err != nil || binding.Job.ID != job.ID {
		return model.PlatformGenerationProviderResultTransition{}, model.PlatformGenerationProviderResultReceipt{},
			errors.New("provider result transition durable binding is inconsistent")
	}
	if err := model.ValidatePlatformGenerationProviderResultReceipt(nativeTask, binding, receipt); err != nil {
		return model.PlatformGenerationProviderResultTransition{}, model.PlatformGenerationProviderResultReceipt{}, err
	}
	transition := model.PlatformGenerationProviderResultTransition{
		NativeTaskID:       nativeTask.TaskID,
		ReceiptSHA256:      model.PlatformGenerationProviderResultReceiptSHA256(nativeTask.Data),
		ReceiptProofSHA256: receipt.ProofSHA256,
		ArtifactURLSHA256:  receipt.ArtifactURLSHA256,
		Succeeded:          succeeded,
	}
	return transition, receipt, nil
}

func platformGenerationProviderFailurePolicy() (int, time.Duration, error) {
	threshold := platformGenerationFailureThresholdDefault
	if raw := strings.TrimSpace(os.Getenv("RELAY_PROVIDER_FAILURE_THRESHOLD")); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil {
			return 0, 0, errors.New("RELAY_PROVIDER_FAILURE_THRESHOLD must be an integer")
		}
		threshold = parsed
	}
	if threshold < 1 || threshold > 100 {
		return 0, 0, errors.New("RELAY_PROVIDER_FAILURE_THRESHOLD must be between 1 and 100")
	}

	cooldownSeconds := int(platformGenerationProviderCooldownDefault / time.Second)
	if raw := strings.TrimSpace(os.Getenv("RELAY_PROVIDER_COOLDOWN_SECONDS")); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil {
			return 0, 0, errors.New("RELAY_PROVIDER_COOLDOWN_SECONDS must be an integer")
		}
		cooldownSeconds = parsed
	}
	if cooldownSeconds < 0 || cooldownSeconds > int((24*time.Hour)/time.Second) {
		return 0, 0, errors.New("RELAY_PROVIDER_COOLDOWN_SECONDS must be between 0 and 86400")
	}
	return threshold, time.Duration(cooldownSeconds) * time.Second, nil
}

func platformGenerationProviderTerminalOutcome(
	job model.PlatformGenerationJob,
	nativeTask model.Task,
	succeeded bool,
	receipts ...*model.PlatformGenerationProviderResultReceipt,
) (*model.PlatformProviderTerminalOutcome, error) {
	upstreamTaskID := nativeTask.PrivateData.UpstreamTaskID
	if upstreamTaskID == "" {
		upstreamTaskID = job.UpstreamTaskID
	}
	if strings.TrimSpace(upstreamTaskID) == "" || len(upstreamTaskID) > 191 || job.ProviderRouteID <= 0 {
		return nil, errors.New("provider terminal outcome lacks its sticky route evidence")
	}
	occurredAt := time.Now().UTC()
	if nativeTask.FinishTime > 0 {
		occurredAt = time.Unix(nativeTask.FinishTime, 0).UTC()
	}
	outcome := &model.PlatformProviderTerminalOutcome{
		ID:                uuid.NewSHA1(uuid.NameSpaceURL, []byte("relay-provider-terminal:"+job.ID)).String(),
		RouteID:           job.ProviderRouteID,
		RelayJobID:        job.ID,
		Outcome:           model.PlatformProviderOutcomeSucceeded,
		FailureOwner:      model.PlatformProviderFailureOwnerNone,
		OccurredAt:        occurredAt,
		ExternalReference: "provider-task:" + upstreamTaskID,
	}
	if !succeeded {
		outcome.Outcome = model.PlatformProviderOutcomeFailed
		outcome.FailureOwner = model.PlatformProviderFailureOwnerProvider
		outcome.FailureCode = "upstream_failed"
		if len(receipts) == 1 && receipts[0] != nil {
			outcome.FailureOwner = receipts[0].FailureOwner
			outcome.FailureCode = receipts[0].FailureCode
		}
	}
	return outcome, nil
}

func platformGenerationProviderFailurePublicError(
	receipt model.PlatformGenerationProviderResultReceipt,
) string {
	if receipt.FailureOwner == model.PlatformProviderFailureOwnerClient &&
		receipt.FailureCode == "content_policy_rejected" {
		return model.PlatformGenerationErrorContentPolicyRejected
	}
	return model.PlatformGenerationErrorUpstreamFailed
}

func failPlatformGenerationSubmission(claim model.PlatformGenerationClaim, code string, message string, retryable bool) error {
	if !model.IsPlatformGenerationPublicErrorCode(code) {
		code = model.PlatformGenerationErrorGenerationFailed
	}
	_, err := model.CompletePlatformGenerationSubmission(claim, map[string]any{
		"status":          model.PlatformGenerationStatusFailed,
		"progress":        100,
		"error_code":      code,
		"error_message":   message,
		"error_retryable": retryable,
	}, BuildPlatformGenerationCallbackDelivery)
	return err
}

func buildPlatformNativeTaskRequest(request dto.PlatformGenerationRequest) ([]byte, error) {
	return buildPlatformNativeTaskRequestWithContext(context.Background(), request)
}

func buildPlatformNativeTaskRequestWithContext(ctx context.Context, request dto.PlatformGenerationRequest) ([]byte, error) {
	return buildPlatformNativeTaskRequestWithClient(ctx, request, GetSSRFProtectedHTTPClient())
}

func buildPlatformNativeTaskRequestWithClient(
	ctx context.Context,
	request dto.PlatformGenerationRequest,
	assetClient *http.Client,
) ([]byte, error) {
	return buildPlatformNativeTaskRequestWithDependencies(
		ctx,
		request,
		assetClient,
		ValidateSSRFProtectedFetchURL,
	)
}

func buildPlatformNativeTaskRequestWithDependencies(
	ctx context.Context,
	request dto.PlatformGenerationRequest,
	assetClient *http.Client,
	validateAssetURL func(string) error,
) ([]byte, error) {
	profile, hasProfile, err := platformGenerationProfileForStoredRequest(request)
	if err != nil {
		return nil, err
	}
	if hasProfile {
		if err := profile.ValidateRequest(request); err != nil {
			return nil, err
		}
	}
	metadata := make(map[string]any, len(request.Metadata)+7)
	for key, value := range request.Metadata {
		metadata[key] = value
	}
	metadata["platform_generation_mode"] = request.Mode
	metadata["durationSeconds"] = request.Output.DurationSeconds
	metadata["aspectRatio"] = request.Output.AspectRatio
	metadata["resolution"] = request.Output.Resolution
	metadata["sampleCount"] = request.Output.Count
	metadata["face_enabled"] = request.Output.FaceEnabled
	images := make([]string, 0)
	videos := make([]string, 0)
	audios := make([]string, 0)
	inputReference := ""
	for _, asset := range request.Inputs.Assets {
		assetValue := asset.URL
		if hasProfile && asset.MediaType == "image" && request.Mode == "image_to_video" &&
			platformGenerationGoogleVeoProfile(profile) {
			assetValue, err = platformGenerationInlineVeoImageWithValidator(
				ctx,
				assetClient,
				asset.URL,
				validateAssetURL,
			)
			if err != nil {
				return nil, fmt.Errorf("%w: %v", errPlatformGenerationInputAssetUnavailable, err)
			}
		}
		switch asset.MediaType {
		case "image":
			images = append(images, assetValue)
		case "video":
			videos = append(videos, assetValue)
			if inputReference == "" {
				inputReference = assetValue
			}
		case "audio":
			audios = append(audios, assetValue)
		}
	}
	image := ""
	if len(images) > 0 {
		image = images[0]
	}
	providerSize := platformGenerationVideoSize(request.Output.Resolution, request.Output.AspectRatio)
	duration := request.Output.DurationSeconds
	seconds := strconv.Itoa(request.Output.DurationSeconds)
	if request.Mode == "text_to_image" {
		if !hasProfile || profile.Image == nil {
			return nil, errors.New("protected image generation requires an immutable capability profile")
		}
		providerSize = profile.Image.Size
		// Image duration is not a provider capability. Omit the legacy v1
		// duration sentinel from the native request entirely.
		duration = 0
		seconds = ""
	}
	native := platformNativeTaskRequest{
		Prompt:         request.Inputs.Prompt,
		Model:          request.Model,
		Image:          image,
		Images:         images,
		Videos:         videos,
		Audios:         audios,
		Size:           providerSize,
		Duration:       duration,
		Seconds:        seconds,
		InputReference: inputReference,
		Metadata:       metadata,
	}
	return common.Marshal(native)
}

func platformGenerationGoogleVeoProfile(profile generationprofile.Profile) bool {
	switch profile.Protocol {
	case generationprofile.GoogleGeminiVeoVideoProtocolV1,
		generationprofile.GoogleVertexVeoVideoProtocolV1:
		return true
	default:
		return false
	}
}

func platformGenerationInlineVeoImage(
	ctx context.Context,
	client *http.Client,
	rawURL string,
) (string, error) {
	return platformGenerationInlineVeoImageWithValidator(
		ctx,
		client,
		rawURL,
		ValidateSSRFProtectedFetchURL,
	)
}

func platformGenerationInlineVeoImageWithValidator(
	ctx context.Context,
	client *http.Client,
	rawURL string,
	validateURL func(string) error,
) (string, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" ||
		(parsed.Scheme != "http" && parsed.Scheme != "https") {
		return "", errors.New("reference image URL is invalid")
	}
	if PlatformRelayProductionSecurityEnabled() && parsed.Scheme != "https" {
		return "", errors.New("reference image URL must use HTTPS")
	}
	if validateURL == nil {
		return "", errors.New("reference image URL policy is unavailable")
	}
	if err := validateURL(parsed.String()); err != nil {
		return "", errors.New("reference image URL is not permitted")
	}
	if client == nil {
		return "", errors.New("reference image fetch client is unavailable")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	fetchContext, cancel := context.WithTimeout(ctx, platformGenerationVeoInputFetchTimeout)
	defer cancel()
	request, err := http.NewRequestWithContext(fetchContext, http.MethodGet, parsed.String(), nil)
	if err != nil {
		return "", errors.New("reference image request is invalid")
	}
	request.Header.Set("Accept", "image/png, image/jpeg, image/webp")
	request.Header.Set("Accept-Encoding", "identity")
	boundedClient := *client
	boundedClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	response, err := boundedClient.Do(request)
	if err != nil {
		return "", errors.New("reference image could not be fetched")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("reference image returned HTTP %d", response.StatusCode)
	}
	if encoding := strings.TrimSpace(response.Header.Get("Content-Encoding")); encoding != "" && !strings.EqualFold(encoding, "identity") {
		return "", errors.New("reference image content encoding is unsupported")
	}
	if response.ContentLength > platformGenerationVeoInputMaxBytes {
		return "", errors.New("reference image exceeds the size limit")
	}
	payload, err := io.ReadAll(io.LimitReader(response.Body, platformGenerationVeoInputMaxBytes+1))
	if err != nil {
		return "", errors.New("reference image could not be read")
	}
	if len(payload) == 0 || len(payload) > platformGenerationVeoInputMaxBytes {
		return "", errors.New("reference image size is invalid")
	}
	detectedMIME := http.DetectContentType(payload)
	declaredMIME := strings.TrimSpace(response.Header.Get("Content-Type"))
	if declaredMIME != "" {
		declaredMIME, _, err = mime.ParseMediaType(declaredMIME)
		if err != nil {
			return "", errors.New("reference image content type is invalid")
		}
	}
	if declaredMIME == "" || declaredMIME == "application/octet-stream" {
		declaredMIME = detectedMIME
	}
	if declaredMIME != detectedMIME {
		return "", errors.New("reference image content type does not match its bytes")
	}
	switch detectedMIME {
	case "image/png", "image/jpeg", "image/webp":
	default:
		return "", errors.New("reference image format is unsupported")
	}
	config, _, err := image.DecodeConfig(bytes.NewReader(payload))
	if err != nil || config.Width < 1 || config.Height < 1 ||
		config.Width > platformGenerationVeoInputMaxDimension || config.Height > platformGenerationVeoInputMaxDimension ||
		int64(config.Width)*int64(config.Height) > int64(platformGenerationVeoInputMaxDimension)*int64(platformGenerationVeoInputMaxDimension) {
		return "", errors.New("reference image dimensions are invalid")
	}
	return "data:" + detectedMIME + ";base64," + base64.StdEncoding.EncodeToString(payload), nil
}

func platformGenerationVideoSize(resolution string, aspectRatio string) string {
	// These dimensions preserve the requested contract ratio. The 720p and
	// 1080p values also match Wan2.7's documented output matrix, allowing its
	// adaptor to recover resolution + ratio without provider-specific metadata.
	matrices := map[string]map[string]string{
		"720p": {
			"16:9": "1280x720",
			"9:16": "720x1280",
			"1:1":  "960x960",
			"4:3":  "1104x832",
			"3:4":  "832x1104",
		},
		"1080p": {
			"16:9": "1920x1080",
			"9:16": "1080x1920",
			"1:1":  "1440x1440",
			"4:3":  "1648x1248",
			"3:4":  "1248x1648",
		},
	}
	resolutionKey := strings.ToLower(strings.TrimSpace(resolution))
	if matrix, ok := matrices[resolutionKey]; ok {
		if size, supported := matrix[aspectRatio]; supported {
			return size
		}
		return matrix["16:9"]
	}

	longEdge := 3840
	if resolutionKey != "4k" && resolutionKey != "2160p" {
		longEdge = 1280
	}
	switch aspectRatio {
	case "9:16":
		return fmt.Sprintf("%dx%d", longEdge*9/16, longEdge)
	case "1:1":
		return fmt.Sprintf("%dx%d", longEdge, longEdge)
	case "4:3":
		return fmt.Sprintf("%dx%d", longEdge, longEdge*3/4)
	case "3:4":
		return fmt.Sprintf("%dx%d", longEdge*3/4, longEdge)
	default:
		return fmt.Sprintf("%dx%d", longEdge, longEdge*9/16)
	}
}

func submitPlatformNativeTask(
	ctx context.Context,
	claim model.PlatformGenerationClaim,
	routeClaim model.PlatformGenerationRouteClaim,
	principal PlatformRelayPrincipal,
	body []byte,
	transportBindings ...PlatformGenerationRuntimeTransportBinding,
) (bool, int, error) {
	transport := PlatformGenerationRuntimeTransportBinding{}
	if len(transportBindings) > 1 {
		return false, 0, errors.New("protected Google transport binding is ambiguous")
	}
	if len(transportBindings) == 1 {
		transport = transportBindings[0]
	}
	if err := ValidateProtectedPlatformRelayPrincipalForJobID(claim.Job.ID); err != nil {
		return false, 0, err
	}
	timeoutSeconds := common.GetEnvOrDefault("RELAY_COMPAT_SUBMISSION_TIMEOUT_SECONDS", 120)
	if timeoutSeconds < 1 || timeoutSeconds > 600 {
		timeoutSeconds = 120
	}
	requestContext, cancel := context.WithTimeout(ctx, time.Duration(timeoutSeconds)*time.Second)
	defer cancel()
	baseURL, err := platformNativeSubmissionBaseURL()
	if err != nil {
		return false, 0, err
	}
	req, err := http.NewRequestWithContext(requestContext, http.MethodPost, baseURL+"/internal/platform-generations/native-submit", bytes.NewReader(body))
	if err != nil {
		return false, 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(principal.UpstreamToken))
	req.Header.Set("X-Request-ID", claim.Job.RequestID)
	req.Header.Set(constant.HeaderPlatformGenerationInternalAdmission, PlatformRelayInternalAdmissionToken())
	req.Header.Set(constant.HeaderPlatformGenerationJobID, claim.Job.ID)
	req.Header.Set(constant.HeaderPlatformGenerationRouteID, strconv.FormatInt(routeClaim.Route.ID, 10))
	req.Header.Set(constant.HeaderPlatformGenerationWorkerLeaseToken, claim.Token)
	req.Header.Set(constant.HeaderPlatformGenerationSubmissionToken, routeClaim.SubmissionToken)
	if transport.Revision != "" || transport.SHA256 != "" {
		if !platformRelaySnapshotDigestPattern.MatchString(transport.Revision) ||
			!platformRelaySnapshotDigestPattern.MatchString(transport.SHA256) {
			return false, 0, errors.New("protected Google transport binding is invalid")
		}
		req.Header.Set(constant.HeaderPlatformGenerationTransportRevision, transport.Revision)
		req.Header.Set(constant.HeaderPlatformGenerationTransportSHA256, transport.SHA256)
	}
	client := GetHttpClient()
	if client == nil {
		client = http.DefaultClient
	}
	resp, err := client.Do(req)
	if err != nil {
		// Once bytes may have left this process, a transport failure is unknown.
		return true, 0, err
	}
	defer resp.Body.Close()
	_, readErr := io.ReadAll(io.LimitReader(resp.Body, platformGenerationResponseLimit))
	providerStarted := strings.EqualFold(resp.Header.Get(constant.HeaderPlatformGenerationProviderStarted), "true")
	if readErr != nil {
		return providerStarted, resp.StatusCode, readErr
	}
	return providerStarted, resp.StatusCode, nil
}

func platformNativeSubmissionBaseURL() (string, error) {
	if model.RelayDatabaseRoleAttestationRequired() || PlatformRelayProductionSecurityEnabled() {
		if _, present := os.LookupEnv("RELAY_COMPAT_INTERNAL_BASE_URL"); present {
			return "", errors.New("protected Relay native submission target cannot be overridden")
		}
		port := os.Getenv("PORT")
		if port == "" {
			port = strconv.Itoa(*common.Port)
		}
		numericPort, err := strconv.Atoi(port)
		if err != nil || numericPort < 1 || numericPort > 65535 || port != strconv.Itoa(numericPort) {
			return "", errors.New("protected Relay native submission target is invalid")
		}
		return "http://127.0.0.1:" + port, nil
	}
	baseURL := strings.TrimRight(strings.TrimSpace(os.Getenv("RELAY_COMPAT_INTERNAL_BASE_URL")), "/")
	if baseURL != "" {
		return baseURL, nil
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = strconv.Itoa(*common.Port)
	}
	return "http://127.0.0.1:" + port, nil
}

func nativeTaskProgress(value string) int {
	trimmed := strings.TrimSuffix(strings.TrimSpace(value), "%")
	progress, err := strconv.Atoi(trimmed)
	if err != nil || progress < 0 {
		return 0
	}
	if progress > 99 {
		return 99
	}
	return progress
}
