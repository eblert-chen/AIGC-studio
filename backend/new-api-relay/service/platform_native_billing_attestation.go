package service

import (
	"database/sql"
	"errors"
	"fmt"

	"github.com/QuantumNous/new-api/model"
	"gorm.io/gorm"
)

var ErrProtectedPlatformNativeBillingState = errors.New("protected Platform native billing state is not exact")

// Bound readiness work. Exceeding this limit is an operational backlog that
// must be reconciled explicitly; a health probe must never turn it into an
// unbounded scan or an N+1 query storm.
const protectedPlatformNativeBillingAttestationMaxRows = 10_000

// ValidateProtectedPlatformNativeBillingState proves that every native task
// which the generic polling runner could touch is actually the zero-billing
// provider identity of exactly one Platform generation job. This is run before
// the scheduler starts, on each polling pass and on readiness. Ordinary/legacy
// or ambiguous tasks are never polled in a protected deployment.
func ValidateProtectedPlatformNativeBillingState() error {
	if !model.RelayDatabaseRoleAttestationRequired() {
		return nil
	}
	_, err := loadProtectedPlatformNativeUnfinishedTasks()
	return err
}

func protectedPlatformTaskIsUnfinished(task *model.Task) bool {
	return task != nil && task.Progress != "100%" &&
		task.Status != model.TaskStatusFailure && task.Status != model.TaskStatusSuccess
}

func loadProtectedPlatformNativeUnfinishedTasks() ([]*model.Task, error) {
	if model.DB == nil {
		return nil, fmt.Errorf("%w: database is unavailable", ErrProtectedPlatformNativeBillingState)
	}
	var tasks []*model.Task
	err := model.DB.Transaction(func(tx *gorm.DB) error {
		loaded, err := loadProtectedPlatformNativeUnfinishedTasksTx(tx)
		if err != nil {
			return err
		}
		tasks = loaded
		return nil
	}, &sql.TxOptions{Isolation: sql.LevelRepeatableRead, ReadOnly: true})
	if err != nil {
		if errors.Is(err, ErrProtectedPlatformNativeBillingState) {
			return nil, err
		}
		return nil, fmt.Errorf("%w: billing evidence snapshot could not be inspected", ErrProtectedPlatformNativeBillingState)
	}
	return tasks, nil
}

func loadProtectedPlatformNativeUnfinishedTasksTx(tx *gorm.DB) ([]*model.Task, error) {
	now, err := model.GetDBTimeTx(tx)
	if err != nil {
		return nil, fmt.Errorf("%w: database time could not be inspected", ErrProtectedPlatformNativeBillingState)
	}
	terminalJobStatuses := []string{
		model.PlatformGenerationStatusSucceeded,
		model.PlatformGenerationStatusFailed,
		model.PlatformGenerationStatusCancelled,
	}
	terminalTaskStatuses := []string{model.TaskStatusFailure, model.TaskStatusSuccess}
	var corruptTaskIdentity struct{ ID int64 }
	corruptTaskResult := tx.Model(&model.Task{}).
		Select("id").
		Where("status IS NULL OR progress IS NULL").
		Order("id ASC").
		Limit(1).
		Take(&corruptTaskIdentity)
	if corruptTaskResult.Error != nil && !errors.Is(corruptTaskResult.Error, gorm.ErrRecordNotFound) {
		return nil, fmt.Errorf("%w: task lifecycle consistency could not be inspected", ErrProtectedPlatformNativeBillingState)
	}
	if corruptTaskResult.Error == nil {
		return nil, fmt.Errorf("%w: task lifecycle state is inconsistent", ErrProtectedPlatformNativeBillingState)
	}

	// First collect the exact legacy-poller predicate. UNKNOWN and historical
	// arbitrary states are unfinished too; an allowlist would let them bypass
	// protected attestation.
	type taskIdentity struct {
		ID     int64
		TaskID string
	}
	var unfinishedTaskIdentities []taskIdentity
	if err := tx.Model(&model.Task{}).
		Select("id, task_id").
		Where("progress != ?", "100%").
		Where("status NOT IN ?", terminalTaskStatuses).
		Order("id ASC").
		Limit(protectedPlatformNativeBillingAttestationMaxRows + 1).
		Find(&unfinishedTaskIdentities).Error; err != nil {
		return nil, fmt.Errorf("%w: unfinished tasks could not be inspected", ErrProtectedPlatformNativeBillingState)
	}
	if len(unfinishedTaskIdentities) > protectedPlatformNativeBillingAttestationMaxRows {
		return nil, fmt.Errorf("%w: unfinished task attestation limit exceeded", ErrProtectedPlatformNativeBillingState)
	}
	unfinishedTaskIDs := make([]string, 0, len(unfinishedTaskIdentities))
	for _, identity := range unfinishedTaskIdentities {
		if identity.TaskID == "" {
			return nil, fmt.Errorf("%w: unfinished task identity is empty", ErrProtectedPlatformNativeBillingState)
		}
		unfinishedTaskIDs = append(unfinishedTaskIDs, identity.TaskID)
	}

	// Include every non-terminal Platform job that has begun native submission,
	// plus any job (including a terminal/corrupt one) referenced by an unfinished
	// task. This catches orphan v2 recovery evidence and terminal-job mismatches.
	var jobs []model.PlatformGenerationJob
	jobQuery := tx.Model(&model.PlatformGenerationJob{}).
		Where("status NOT IN ? AND (native_task_id <> '' OR native_task_recovery_json <> '')", terminalJobStatuses)
	if len(unfinishedTaskIDs) > 0 {
		jobQuery = tx.Model(&model.PlatformGenerationJob{}).
			Where("native_task_id IN ?", unfinishedTaskIDs).
			Or("status NOT IN ? AND (native_task_id <> '' OR native_task_recovery_json <> '')", terminalJobStatuses)
	}
	if err := jobQuery.Order("id ASC").
		Limit(protectedPlatformNativeBillingAttestationMaxRows + 1).
		Find(&jobs).Error; err != nil {
		return nil, fmt.Errorf("%w: Platform jobs could not be inspected", ErrProtectedPlatformNativeBillingState)
	}
	if len(jobs) > protectedPlatformNativeBillingAttestationMaxRows {
		return nil, fmt.Errorf("%w: Platform job attestation limit exceeded", ErrProtectedPlatformNativeBillingState)
	}

	jobIndexesByNativeTaskID := make(map[string][]int, len(jobs))
	jobIDs := make([]string, 0, len(jobs))
	routeIDs := make([]int64, 0, len(jobs))
	credentialVersions := make([]string, 0, len(jobs))
	credentialReferenceByJobID := make(map[string]model.PlatformGenerationNativeTaskCredentialReference, len(jobs))
	for index := range jobs {
		job := jobs[index]
		if job.NativeTaskID == "" {
			return nil, fmt.Errorf("%w: Platform job native task identity is empty", ErrProtectedPlatformNativeBillingState)
		}
		jobIndexesByNativeTaskID[job.NativeTaskID] = append(jobIndexesByNativeTaskID[job.NativeTaskID], index)
		if len(jobIndexesByNativeTaskID[job.NativeTaskID]) != 1 {
			return nil, fmt.Errorf("%w: native task identity is bound to multiple Platform jobs", ErrProtectedPlatformNativeBillingState)
		}
		credentialReference, err := model.PlatformGenerationNativeTaskRecoveryCredentialReference(job)
		if err != nil || credentialReference.Version == "" {
			return nil, fmt.Errorf("%w: Platform job recovery evidence is invalid", ErrProtectedPlatformNativeBillingState)
		}
		credentialReferenceByJobID[job.ID] = credentialReference
		jobIDs = append(jobIDs, job.ID)
		routeIDs = append(routeIDs, job.ProviderRouteID)
		credentialVersions = append(credentialVersions, credentialReference.Version)
	}

	// Re-read all candidate Task rows in the same snapshot, adding terminal task
	// rows referenced by an active Platform job. The generic poller receives only
	// the unfinished subset after every binding has been validated.
	var taskRows []model.Task
	taskQuery := tx.Model(&model.Task{}).
		Where("progress != ?", "100%").
		Where("status NOT IN ?", terminalTaskStatuses)
	if len(jobIndexesByNativeTaskID) > 0 {
		nativeTaskIDs := make([]string, 0, len(jobIndexesByNativeTaskID))
		for nativeTaskID := range jobIndexesByNativeTaskID {
			nativeTaskIDs = append(nativeTaskIDs, nativeTaskID)
		}
		taskQuery = tx.Model(&model.Task{}).
			Where("task_id IN ?", nativeTaskIDs).
			Or("progress != ? AND status NOT IN ?", "100%", terminalTaskStatuses)
	}
	if err := taskQuery.Order("id ASC").
		Limit(protectedPlatformNativeBillingAttestationMaxRows + 1).
		Find(&taskRows).Error; err != nil {
		return nil, fmt.Errorf("%w: native task rows could not be inspected", ErrProtectedPlatformNativeBillingState)
	}
	if len(taskRows) > protectedPlatformNativeBillingAttestationMaxRows {
		return nil, fmt.Errorf("%w: native task attestation limit exceeded", ErrProtectedPlatformNativeBillingState)
	}
	taskByNativeTaskID := make(map[string]*model.Task, len(taskRows))
	for index := range taskRows {
		task := &taskRows[index]
		if task.TaskID == "" || taskByNativeTaskID[task.TaskID] != nil {
			return nil, fmt.Errorf("%w: native task identity is duplicated", ErrProtectedPlatformNativeBillingState)
		}
		taskByNativeTaskID[task.TaskID] = task
	}

	var routes []model.PlatformGenerationProviderRoute
	if len(routeIDs) > 0 {
		if err := tx.Where("id IN ?", routeIDs).Limit(len(routeIDs) + 1).Find(&routes).Error; err != nil {
			return nil, fmt.Errorf("%w: provider routes could not be inspected", ErrProtectedPlatformNativeBillingState)
		}
	}
	routeByID := make(map[int64]model.PlatformGenerationProviderRoute, len(routes))
	for _, route := range routes {
		routeByID[route.ID] = route
	}

	var admissions []model.PlatformGenerationRouteAdmission
	if len(jobIDs) > 0 {
		if err := tx.Where("job_id IN ?", jobIDs).Limit(len(jobIDs) + 1).Find(&admissions).Error; err != nil {
			return nil, fmt.Errorf("%w: route admissions could not be inspected", ErrProtectedPlatformNativeBillingState)
		}
	}
	admissionsByJobID := make(map[string][]model.PlatformGenerationRouteAdmission, len(admissions))
	for _, admission := range admissions {
		admissionsByJobID[admission.JobID] = append(admissionsByJobID[admission.JobID], admission)
	}

	var credentials []model.ProviderCredentialVersion
	if len(credentialVersions) > 0 {
		if err := tx.Where("credential_version IN ?", credentialVersions).
			Limit(len(credentialVersions) + 1).Find(&credentials).Error; err != nil {
			return nil, fmt.Errorf("%w: credential versions could not be inspected", ErrProtectedPlatformNativeBillingState)
		}
	}
	credentialByVersion := make(map[string]model.ProviderCredentialVersion, len(credentials))
	for _, credential := range credentials {
		credentialByVersion[credential.CredentialVersion] = credential
	}

	validatedUnfinishedTasks := make([]*model.Task, 0, len(unfinishedTaskIdentities))
	for index := range jobs {
		job := jobs[index]
		admissionRows := admissionsByJobID[job.ID]
		if len(admissionRows) != 1 {
			return nil, fmt.Errorf("%w: Platform job route admission is ambiguous", ErrProtectedPlatformNativeBillingState)
		}
		credentialReference := credentialReferenceByJobID[job.ID]
		credential, credentialFound := credentialByVersion[credentialReference.Version]
		route, routeFound := routeByID[job.ProviderRouteID]
		if !credentialFound || !routeFound {
			return nil, fmt.Errorf("%w: Platform job route credential is missing", ErrProtectedPlatformNativeBillingState)
		}
		task := taskByNativeTaskID[job.NativeTaskID]
		if err := model.ValidatePlatformGenerationNativeTaskBindingEvidence(
			job,
			task,
			route,
			admissionRows[0],
			credential,
			now,
			false,
		); err != nil {
			return nil, fmt.Errorf("%w: Platform native task binding is invalid", ErrProtectedPlatformNativeBillingState)
		}
		if protectedPlatformTaskIsUnfinished(task) {
			validatedUnfinishedTasks = append(validatedUnfinishedTasks, task)
		}
	}

	// Every unfinished task must have appeared exactly once in the validated job
	// map. This rejects ordinary, legacy and late-inserted native billing rows.
	if len(validatedUnfinishedTasks) != len(unfinishedTaskIdentities) {
		return nil, fmt.Errorf("%w: unfinished task lacks an exact Platform binding", ErrProtectedPlatformNativeBillingState)
	}
	return validatedUnfinishedTasks, nil
}

// loadProtectedPlatformNativeTaskForJob is the worker-side counterpart of the
// batch readiness attestation. It reloads job, Task, route, admission and vault
// identity from one repeatable-read snapshot before any poll/reconciliation
// transition consumes provider state. Terminal Task rows are included.
func loadProtectedPlatformNativeTaskForJob(
	claimedJob model.PlatformGenerationJob,
	allowProcessingTaskAbsenceTransition bool,
) (*model.Task, error) {
	if !model.RelayDatabaseRoleAttestationRequired() {
		return model.GetPlatformGenerationNativeTask(claimedJob.NativeTaskID, claimedJob.ProviderChannelID)
	}
	var task *model.Task
	err := model.DB.Transaction(func(tx *gorm.DB) error {
		now, err := model.GetDBTimeTx(tx)
		if err != nil {
			return err
		}
		var job model.PlatformGenerationJob
		if err := tx.Where("id = ?", claimedJob.ID).First(&job).Error; err != nil {
			return err
		}
		if job.Status != claimedJob.Status || job.NativeTaskID != claimedJob.NativeTaskID ||
			job.ProviderRouteID != claimedJob.ProviderRouteID ||
			job.ProviderSubmissionAttempt != claimedJob.ProviderSubmissionAttempt {
			return ErrProtectedPlatformNativeBillingState
		}
		var boundJobs []model.PlatformGenerationJob
		if err := tx.Where("native_task_id = ?", job.NativeTaskID).Order("id ASC").Limit(2).Find(&boundJobs).Error; err != nil {
			return err
		}
		if len(boundJobs) != 1 || boundJobs[0].ID != job.ID {
			return ErrProtectedPlatformNativeBillingState
		}
		var taskRows []model.Task
		if err := tx.Where("task_id = ?", job.NativeTaskID).Order("id ASC").Limit(2).Find(&taskRows).Error; err != nil {
			return err
		}
		if len(taskRows) > 1 {
			return ErrProtectedPlatformNativeBillingState
		}
		var candidate *model.Task
		if len(taskRows) == 1 {
			candidate = &taskRows[0]
		}
		var route model.PlatformGenerationProviderRoute
		if err := tx.Where("id = ?", job.ProviderRouteID).First(&route).Error; err != nil {
			return err
		}
		var admissions []model.PlatformGenerationRouteAdmission
		if err := tx.Where("job_id = ?", job.ID).Order("id ASC").Limit(2).Find(&admissions).Error; err != nil {
			return err
		}
		if len(admissions) != 1 {
			return ErrProtectedPlatformNativeBillingState
		}
		credentialReference, err := model.PlatformGenerationNativeTaskRecoveryCredentialReference(job)
		if err != nil || credentialReference.Version == "" {
			return ErrProtectedPlatformNativeBillingState
		}
		var credential model.ProviderCredentialVersion
		if err := tx.Where("credential_version = ?", credentialReference.Version).First(&credential).Error; err != nil {
			return err
		}
		if err := model.ValidatePlatformGenerationNativeTaskBindingEvidence(
			job,
			candidate,
			route,
			admissions[0],
			credential,
			now,
			allowProcessingTaskAbsenceTransition,
		); err != nil {
			return ErrProtectedPlatformNativeBillingState
		}
		task = candidate
		return nil
	}, &sql.TxOptions{Isolation: sql.LevelRepeatableRead, ReadOnly: true})
	if err != nil {
		if errors.Is(err, ErrProtectedPlatformNativeBillingState) {
			return nil, err
		}
		return nil, fmt.Errorf("%w: native task binding snapshot could not be inspected", ErrProtectedPlatformNativeBillingState)
	}
	return task, nil
}
