package model

import (
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/mysql"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func relaySchemaV4RouteFixture(routeKey string) PlatformGenerationProviderRoute {
	return PlatformGenerationProviderRoute{
		RouteKey:            routeKey,
		Model:               "seedream-4.0",
		Mode:                "text_to_image",
		ProviderName:        "volcengine",
		AccountID:           "account-one",
		ChannelID:           1,
		AcceptedChannelType: 1,
		KeyFingerprint:      strings.Repeat("a", 64),
		ChannelClass:        PlatformGenerationChannelClassOfficialProvider,
		UpstreamModel:       "seedream-4-0-250828",
		RPMWindowSeconds:    60,
		RPMLimit:            120,
		ActiveLimit:         4,
	}
}

func TestRelaySchemaV4RouteBindingGuardsRejectDirectSQLMutation(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, database.AutoMigrate(&PlatformGenerationProviderRoute{}))
	require.NoError(t, installPlatformGenerationRouteBindingGuardsV4(database))

	route := relaySchemaV4RouteFixture("seedream-primary")
	require.NoError(t, database.Create(&route).Error)

	partialInsert := relaySchemaV4RouteFixture("seedream-partial")
	partialInsert.CapabilityProfileID = "seedream-v1"
	require.Error(t, database.Create(&partialInsert).Error,
		"a direct insert must not bypass the all-or-none profile binding")

	profileRevision := "sha256:" + strings.Repeat("b", 64)
	profileSnapshot := `{"profile_id":"seedream-v1"}`
	require.Error(t, database.Exec(`UPDATE platform_generation_provider_routes
SET capability_profile_id = ? WHERE id = ?`, "seedream-v1", route.ID).Error,
		"a direct partial backfill must fail")
	require.NoError(t, database.Exec(`UPDATE platform_generation_provider_routes
SET capability_profile_id = ?, capability_profile_revision = ?, capability_profile_snapshot = ?
WHERE id = ?`, "seedream-v1", profileRevision, profileSnapshot, route.ID).Error,
		"a historical empty profile group may be backfilled atomically once")
	require.Error(t, database.Exec(`UPDATE platform_generation_provider_routes
SET capability_profile_revision = ? WHERE id = ?`, "sha256:"+strings.Repeat("c", 64), route.ID).Error,
		"a direct SQL caller must not mutate an established profile binding")

	releaseRevision := "sha256:" + strings.Repeat("d", 64)
	capabilityRevision := "sha256:" + strings.Repeat("e", 64)
	require.Error(t, database.Exec(`UPDATE platform_generation_provider_routes
SET model_release_id = ? WHERE id = ?`, "release-one", route.ID).Error,
		"a direct partial release backfill must fail")
	require.NoError(t, database.Exec(`UPDATE platform_generation_provider_routes
SET model_release_id = ?, model_release_revision = ?, model_release_capability_revision = ?
WHERE id = ?`, "release-one", releaseRevision, capabilityRevision, route.ID).Error,
		"a historical empty release group may be backfilled atomically once")
	require.Error(t, database.Exec(`UPDATE platform_generation_provider_routes
SET model_release_id = ? WHERE id = ?`, "release-two", route.ID).Error,
		"a direct SQL caller must not mutate an established release binding")

	coolingUntil := time.Now().UTC().Add(time.Minute).Truncate(time.Second)
	require.NoError(t, database.Exec(`UPDATE platform_generation_provider_routes
SET capability_profile_id = ?, capability_profile_revision = ?, capability_profile_snapshot = ?,
    model_release_id = ?, model_release_revision = ?, model_release_capability_revision = ?,
    enabled = ?, production_ready = ?, rpm_limit = ?, active_limit = ?, cooling_until = ?
WHERE id = ?`,
		"seedream-v1", profileRevision, profileSnapshot,
		"release-one", releaseRevision, capabilityRevision,
		true, true, 321, 7, coolingUntil, route.ID).Error,
		"same immutable values must not block operational state updates")

	var persisted PlatformGenerationProviderRoute
	require.NoError(t, database.First(&persisted, route.ID).Error)
	require.True(t, persisted.Enabled)
	require.True(t, persisted.ProductionReady)
	require.Equal(t, 321, persisted.RPMLimit)
	require.Equal(t, 7, persisted.ActiveLimit)
	require.NotNil(t, persisted.CoolingUntil)
}

func TestRelaySchemaV3ToV4RouteBindingMigrationResumesAfterPartialDDL(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, database.AutoMigrate(
		&RelaySchemaState{},
		&PlatformGenerationProviderRoute{},
		&PlatformGenerationJob{},
		&Task{},
		&ProviderCredentialVersion{},
	))

	route := relaySchemaV4RouteFixture("seedream-upgrade")
	require.NoError(t, database.Create(&route).Error)
	for _, field := range []string{
		"CapabilityProfileID",
		"CapabilityProfileRevision",
		"CapabilityProfileSnapshot",
		"ModelReleaseID",
		"ModelReleaseRevision",
		"ModelReleaseCapabilityRevision",
	} {
		require.NoError(t, database.Migrator().DropColumn(&PlatformGenerationProviderRoute{}, field))
	}
	// Simulate a process dying after the first ALTER TABLE committed. The next
	// process must converge the remaining columns and reinstall the guards.
	require.NoError(t, database.Migrator().AddColumn(&PlatformGenerationProviderRoute{}, "CapabilityProfileID"))
	require.NoError(t, database.Create(&RelaySchemaState{
		ID:              relaySchemaStateSingletonID,
		BaselineVersion: relaySchemaV3FrozenVersion,
		CurrentVersion:  relaySchemaV3FrozenVersion,
		TargetVersion:   relaySchemaV4FrozenVersion,
		State:           RelaySchemaStateApplying,
		Dirty:           true,
		AttemptID:       uuid.NewString(),
		CurrentChecksum: relaySchemaV3FrozenChecksumSHA256,
		TargetChecksum:  relaySchemaV4FrozenChecksumSHA256,
	}).Error)

	require.NoError(t, migrateRelaySchemaV4GenerationRouteReleaseBinding(database))
	require.NoError(t, migrateRelaySchemaV4GenerationRouteReleaseBinding(database),
		"restarting the same applying migration must be idempotent")
	for _, field := range []string{
		"CapabilityProfileID",
		"CapabilityProfileRevision",
		"CapabilityProfileSnapshot",
		"ModelReleaseID",
		"ModelReleaseRevision",
		"ModelReleaseCapabilityRevision",
	} {
		require.True(t, database.Migrator().HasColumn(&PlatformGenerationProviderRoute{}, field))
	}

	var persisted PlatformGenerationProviderRoute
	require.NoError(t, database.First(&persisted, route.ID).Error)
	require.Empty(t, persisted.CapabilityProfileID)
	require.Empty(t, persisted.ModelReleaseID)
	require.Error(t, database.Exec(`UPDATE platform_generation_provider_routes
SET capability_profile_id = ? WHERE id = ?`, "partial-after-restart", route.ID).Error,
		"the resumed migration must leave the database guard active")
}

func TestRelaySchemaV3ToV4ScrubsOnlyStrictTerminalPlatformNativeResultURLs(t *testing.T) {
	database := newRelaySchemaSQLite(t)
	require.NoError(t, database.AutoMigrate(
		&RelaySchemaState{},
		&PlatformGenerationProviderRoute{},
		&PlatformGenerationJob{},
		&Task{},
	))

	route := relaySchemaV4RouteFixture("seedream-terminal-url-upgrade")
	route.KeyIndex = 2
	require.NoError(t, database.Create(&route).Error)
	for _, field := range []string{
		"CapabilityProfileID",
		"CapabilityProfileRevision",
		"CapabilityProfileSnapshot",
		"ModelReleaseID",
		"ModelReleaseRevision",
		"ModelReleaseCapabilityRevision",
	} {
		require.NoError(t, database.Migrator().DropColumn(&PlatformGenerationProviderRoute{}, field))
	}

	now := time.Now().UTC()
	providerURL := "https://provider.example/terminal.png?X-Signature=historical-secret"
	differentProviderURL := "https://provider.example/different.png?X-Signature=must-remain"
	temporaryResultJSON := `{"result_url":"` + providerURL + `"}`
	providerData := []byte(`{"content":{"video_url":"` + providerURL + `"},"usage":{"total_tokens":42}}`)
	createJobAndTask := func(
		status string,
		billingSource string,
		pinnedFingerprint string,
		taskIDOverride string,
		resultURL string,
		failReason string,
	) (PlatformGenerationJob, Task) {
		t.Helper()
		job := PlatformGenerationJob{
			ID:                         uuid.NewString(),
			TenantID:                   uuid.NewString(),
			SourceClientID:             "platform",
			RequestID:                  uuid.NewString(),
			IdempotencyKey:             uuid.NewString(),
			RequestHash:                strings.Repeat("f", 64),
			RequestJSON:                `{}`,
			Model:                      route.Model,
			Mode:                       route.Mode,
			ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
			CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
			Status:                     status,
			Progress:                   100,
			ProviderRouteID:            route.ID,
			ProviderChannelID:          route.ChannelID,
			OutputsJSON:                `[]`,
			UpstreamResultURL:          providerURL,
			TemporaryResultJSON:        temporaryResultJSON,
			ErrorDetailsJSON:           `{}`,
			CreatedAt:                  now,
			UpdatedAt:                  now,
		}
		nativeTaskID, err := PlatformGenerationNativeTaskID(job.ID)
		require.NoError(t, err)
		job.NativeTaskID = nativeTaskID
		job.UpstreamTaskID = "provider-task-" + job.ID
		require.NoError(t, database.Create(&job).Error)
		if taskIDOverride != "" {
			nativeTaskID = taskIDOverride
		}
		keyIndex := route.KeyIndex
		credentialVersion := createPlatformGenerationProviderCredentialFixture(t, database, job.TenantID, route)
		task := Task{
			CreatedAt:  now.Unix(),
			UpdatedAt:  now.Unix(),
			TaskID:     nativeTaskID,
			ChannelId:  route.ChannelID,
			Quota:      0,
			Status:     TaskStatusSuccess,
			Progress:   "100%",
			FailReason: failReason,
			Data:       providerData,
			PrivateData: TaskPrivateData{
				PinnedKeyIndex:             &keyIndex,
				PinnedKeyFingerprint:       pinnedFingerprint,
				ProviderCredentialTenantID: job.TenantID,
				ProviderCredentialVersion:  credentialVersion,
				BillingSource:              billingSource,
				UpstreamTaskID:             job.UpstreamTaskID,
				ResultURL:                  resultURL,
			},
		}
		require.NoError(t, database.Create(&task).Error)
		return job, task
	}

	strictJob, strictTask := createJobAndTask(
		PlatformGenerationStatusSucceeded,
		TaskBillingSourcePlatformExternal,
		route.KeyFingerprint,
		"",
		providerURL,
		providerURL,
	)
	failReasonOnlyJob, failReasonOnlyTask := createJobAndTask(
		PlatformGenerationStatusSucceeded,
		TaskBillingSourcePlatformExternal,
		route.KeyFingerprint,
		"",
		"",
		providerURL,
	)
	ordinaryErrorText := "provider render failed before artifact creation"
	privateURLAndErrorJob, privateURLAndErrorTask := createJobAndTask(
		PlatformGenerationStatusSucceeded,
		TaskBillingSourcePlatformExternal,
		route.KeyFingerprint,
		"",
		providerURL,
		ordinaryErrorText,
	)
	privateURLAndDifferentURLJob, privateURLAndDifferentURLTask := createJobAndTask(
		PlatformGenerationStatusSucceeded,
		TaskBillingSourcePlatformExternal,
		route.KeyFingerprint,
		"",
		providerURL,
		differentProviderURL,
	)
	ordinaryErrorOnlyJob, ordinaryErrorOnlyTask := createJobAndTask(
		PlatformGenerationStatusSucceeded,
		TaskBillingSourcePlatformExternal,
		route.KeyFingerprint,
		"",
		"",
		ordinaryErrorText,
	)
	activeJob, activeTask := createJobAndTask(
		PlatformGenerationStatusProcessing,
		TaskBillingSourcePlatformExternal,
		route.KeyFingerprint,
		"",
		providerURL,
		providerURL,
	)
	ordinaryJob, ordinaryTask := createJobAndTask(
		PlatformGenerationStatusSucceeded,
		"wallet",
		route.KeyFingerprint,
		"",
		providerURL,
		providerURL,
	)
	mismatchedJob, mismatchedTask := createJobAndTask(
		PlatformGenerationStatusFailed,
		TaskBillingSourcePlatformExternal,
		strings.Repeat("c", 64),
		"",
		"",
		ordinaryErrorText,
	)
	mismatchedTask.Data = nil
	require.NoError(t, database.Model(&Task{}).Where("id = ?", mismatchedTask.ID).Updates(map[string]any{
		"private_data": mismatchedTask.PrivateData,
		"fail_reason":  mismatchedTask.FailReason,
		"data":         nil,
	}).Error)
	noNativeTaskJob, noNativeTask := createJobAndTask(
		PlatformGenerationStatusSucceeded,
		TaskBillingSourcePlatformExternal,
		route.KeyFingerprint,
		"",
		providerURL,
		providerURL,
	)
	require.NoError(t, database.Delete(&noNativeTask).Error)
	noNativeTaskJob.NativeTaskID = ""
	require.NoError(t, database.Model(&PlatformGenerationJob{}).Where("id = ?", noNativeTaskJob.ID).
		Update("native_task_id", "").Error)
	missingTaskJob, missingTask := createJobAndTask(
		PlatformGenerationStatusSucceeded,
		TaskBillingSourcePlatformExternal,
		route.KeyFingerprint,
		"",
		providerURL,
		providerURL,
	)
	require.NoError(t, database.Delete(&missingTask).Error)
	require.NoError(t, database.Create(&RelaySchemaState{
		ID:                   relaySchemaStateSingletonID,
		BaselineVersion:      relaySchemaV3FrozenVersion,
		CurrentVersion:       relaySchemaV3FrozenVersion,
		TargetVersion:        relaySchemaV4FrozenVersion,
		State:                RelaySchemaStateApplying,
		Dirty:                true,
		AttemptID:            uuid.NewString(),
		CurrentChecksum:      relaySchemaV3FrozenChecksumSHA256,
		TargetChecksum:       relaySchemaV4FrozenChecksumSHA256,
		CurrentCatalogSHA256: "",
		TargetCatalogSHA256:  "",
	}).Error)

	require.NoError(t, migrateRelaySchemaV4GenerationRouteReleaseBinding(database))
	require.NoError(t, migrateRelaySchemaV4GenerationRouteReleaseBinding(database),
		"the terminal credential scrub must be restart-safe")

	for _, assertion := range []struct {
		id             int64
		jobID          string
		expectedResult string
		expectedReason string
		jobCleared     bool
		dataCleared    bool
		resultScrubbed bool
	}{
		{id: strictTask.ID, jobID: strictJob.ID, jobCleared: true, dataCleared: true, resultScrubbed: true},
		{id: failReasonOnlyTask.ID, jobID: failReasonOnlyJob.ID, jobCleared: true, dataCleared: true, resultScrubbed: true},
		{id: privateURLAndErrorTask.ID, jobID: privateURLAndErrorJob.ID, expectedReason: ordinaryErrorText, jobCleared: true, dataCleared: true, resultScrubbed: true},
		{id: privateURLAndDifferentURLTask.ID, jobID: privateURLAndDifferentURLJob.ID, expectedReason: differentProviderURL, jobCleared: true, dataCleared: true, resultScrubbed: true},
		{id: ordinaryErrorOnlyTask.ID, jobID: ordinaryErrorOnlyJob.ID, expectedReason: ordinaryErrorText, jobCleared: true, dataCleared: true},
		{id: activeTask.ID, jobID: activeJob.ID, expectedResult: providerURL, expectedReason: providerURL},
		{id: ordinaryTask.ID, jobID: ordinaryJob.ID, expectedResult: providerURL, expectedReason: providerURL, jobCleared: true},
		{id: mismatchedTask.ID, jobID: mismatchedJob.ID, expectedReason: ordinaryErrorText, jobCleared: true},
	} {
		var persisted Task
		require.NoError(t, database.First(&persisted, assertion.id).Error)
		require.Equal(t, assertion.expectedResult, persisted.PrivateData.ResultURL)
		require.Equal(t, assertion.resultScrubbed, persisted.PrivateData.ProviderResultURLScrubbed)
		require.Equal(t, assertion.expectedReason, persisted.FailReason)
		if assertion.expectedResult == "" && assertion.expectedReason == "" {
			require.Empty(t, persisted.GetResultURL())
		}
		if assertion.dataCleared {
			require.Empty(t, persisted.Data)
		} else if assertion.id != mismatchedTask.ID {
			require.Equal(t, string(providerData), string(persisted.Data))
		}
		var persistedJob PlatformGenerationJob
		require.NoError(t, database.First(&persistedJob, "id = ?", assertion.jobID).Error)
		if assertion.jobCleared {
			require.Empty(t, persistedJob.UpstreamResultURL)
			require.Empty(t, persistedJob.TemporaryResultJSON)
		} else {
			require.Equal(t, providerURL, persistedJob.UpstreamResultURL)
			require.Equal(t, temporaryResultJSON, persistedJob.TemporaryResultJSON)
		}
	}
	for _, jobID := range []string{noNativeTaskJob.ID, missingTaskJob.ID} {
		var persistedJob PlatformGenerationJob
		require.NoError(t, database.First(&persistedJob, "id = ?", jobID).Error)
		require.Empty(t, persistedJob.UpstreamResultURL)
		require.Empty(t, persistedJob.TemporaryResultJSON)
	}
}

func TestRelaySchemaV4RequiresManualReconciliationForAmbiguousProviderMaterial(t *testing.T) {
	for _, test := range []struct {
		name         string
		providerData []byte
		mutate       func(*Task, *Task)
	}{
		{
			name: "credential binding mismatch",
			mutate: func(task *Task, _ *Task) {
				task.PrivateData.PinnedKeyFingerprint = strings.Repeat("f", 64)
			},
		},
		{
			name: "duplicate deterministic native task",
			mutate: func(_ *Task, duplicate *Task) {
				duplicate.ID = 0
			},
		},
		{
			name: "upstream task binding mismatch",
			mutate: func(task *Task, _ *Task) {
				task.PrivateData.UpstreamTaskID = "forged-provider-task"
			},
		},
		{
			name: "forged credential version row",
		},
		{
			name: "active native task on terminal job",
			mutate: func(task *Task, _ *Task) {
				task.Status = TaskStatusInProgress
				task.Progress = "50%"
			},
		},
		{
			name:         "escaped slash provider URL",
			providerData: []byte(`{"url":"https:\/\/provider.example\/result.png?sig=escaped"}`),
			mutate: func(task *Task, _ *Task) {
				task.PrivateData.PinnedKeyFingerprint = strings.Repeat("f", 64)
				task.PrivateData.ResultURL = ""
				task.FailReason = ""
			},
		},
		{
			name:         "unicode escaped provider URL",
			providerData: []byte(`{"url":"https:\u002f\u002fprovider.example\u002fresult.png?sig=unicode"}`),
			mutate: func(task *Task, _ *Task) {
				task.PrivateData.PinnedKeyFingerprint = strings.Repeat("f", 64)
				task.PrivateData.ResultURL = ""
				task.FailReason = ""
			},
		},
		{
			name:         "malformed provider data",
			providerData: []byte(`{"url":`),
			mutate: func(task *Task, _ *Task) {
				task.PrivateData.PinnedKeyFingerprint = strings.Repeat("f", 64)
				task.PrivateData.ResultURL = ""
				task.FailReason = ""
			},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			database := newRelaySchemaSQLite(t)
			require.NoError(t, database.AutoMigrate(
				&PlatformGenerationProviderRoute{},
				&PlatformGenerationJob{},
				&Task{},
				&ProviderCredentialVersion{},
			))
			route := relaySchemaV4RouteFixture("manual-reconciliation-" + strings.ReplaceAll(test.name, " ", "-"))
			route.KeyIndex = 2
			require.NoError(t, database.Create(&route).Error)
			providerURL := "https://provider.example/terminal.png?X-Signature=must-reconcile"
			job := PlatformGenerationJob{
				ID: uuid.NewString(), TenantID: uuid.NewString(), SourceClientID: "platform",
				RequestID: uuid.NewString(), IdempotencyKey: uuid.NewString(), RequestHash: strings.Repeat("a", 64),
				RequestJSON: `{}`, Model: route.Model, Mode: route.Mode,
				ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
				CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
				Status:                     PlatformGenerationStatusSucceeded, Progress: 100,
				ProviderRouteID: route.ID, ProviderChannelID: route.ChannelID,
				UpstreamResultURL: providerURL, TemporaryResultJSON: `{"result_url":"` + providerURL + `"}`,
				OutputsJSON: `[]`, ErrorDetailsJSON: `{}`, CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC(),
			}
			nativeTaskID, err := PlatformGenerationNativeTaskID(job.ID)
			require.NoError(t, err)
			job.NativeTaskID = nativeTaskID
			job.UpstreamTaskID = "provider-task-" + job.ID
			require.NoError(t, database.Create(&job).Error)
			keyIndex := route.KeyIndex
			credentialVersion := createPlatformGenerationProviderCredentialFixture(t, database, job.TenantID, route)
			task := Task{
				CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
				TaskID: nativeTaskID, ChannelId: route.ChannelID, Status: TaskStatusSuccess, Progress: "100%",
				FailReason: providerURL, Data: []byte(`{"content":{"video_url":"` + providerURL + `"}}`),
				PrivateData: TaskPrivateData{
					BillingSource: TaskBillingSourcePlatformExternal, ResultURL: providerURL,
					PinnedKeyIndex: &keyIndex, PinnedKeyFingerprint: route.KeyFingerprint,
					ProviderCredentialTenantID: job.TenantID, ProviderCredentialVersion: credentialVersion,
					UpstreamTaskID: job.UpstreamTaskID,
				},
			}
			if len(test.providerData) > 0 {
				task.Data = test.providerData
			}
			duplicate := task
			if test.mutate != nil {
				test.mutate(&task, &duplicate)
			}
			if test.name == "forged credential version row" {
				task.PrivateData.ProviderCredentialVersion = createPlatformGenerationProviderCredentialFixture(
					t,
					database,
					uuid.NewString(),
					route,
				)
			}
			require.NoError(t, database.Create(&task).Error)
			if test.name == "duplicate deterministic native task" {
				require.NoError(t, database.Create(&duplicate).Error)
			}

			err = scrubPlatformGenerationTerminalNativeResultURLsV4(database)
			require.Error(t, err)
			require.ErrorContains(t, err, "manual reconciliation")
			require.ErrorContains(t, err, "count=1")
			require.ErrorContains(t, err, "job_ids_sha256=")
			require.NotContains(t, err.Error(), providerURL)
			require.NotContains(t, err.Error(), "X-Signature")
			var persistedJob PlatformGenerationJob
			require.NoError(t, database.First(&persistedJob, "id = ?", job.ID).Error)
			require.Equal(t, providerURL, persistedJob.UpstreamResultURL,
				"preflight must fail before partially mutating an unresolved database")
		})
	}
}

func TestRelaySchemaV4RouteBindingGuardsConfiguredDatabases(t *testing.T) {
	tests := []struct {
		name      string
		env       string
		dialector func(string) gorm.Dialector
	}{
		{name: "mysql57", env: "TEST_RELAY_SCHEMA_V4_MYSQL_DSN", dialector: func(dsn string) gorm.Dialector {
			return mysql.Open(dsn)
		}},
		{name: "postgres", env: "TEST_RELAY_SCHEMA_V4_POSTGRES_DSN", dialector: func(dsn string) gorm.Dialector {
			return postgres.New(postgres.Config{DSN: dsn, PreferSimpleProtocol: true})
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			dsn := strings.TrimSpace(os.Getenv(test.env))
			if dsn == "" {
				t.Skip(test.env + " is not configured")
			}
			database, err := gorm.Open(test.dialector(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
			require.NoError(t, err)
			sqlDB, err := database.DB()
			require.NoError(t, err)
			t.Cleanup(func() { _ = sqlDB.Close() })
			require.False(t, database.Migrator().HasTable(&PlatformGenerationProviderRoute{}),
				test.env+" must identify an empty disposable database")
			t.Cleanup(func() {
				_ = database.Migrator().DropTable(&Task{})
				_ = database.Migrator().DropTable(&PlatformGenerationJob{})
				_ = database.Migrator().DropTable(&PlatformGenerationProviderRoute{})
				_ = database.Migrator().DropTable(&ProviderCredentialVersion{})
				_ = database.Migrator().DropTable(&RelaySchemaState{})
				if test.name == "postgres" {
					_ = database.Exec(`DROP FUNCTION IF EXISTS enforce_platform_generation_route_binding_v4()`).Error
				}
			})

			require.NoError(t, database.AutoMigrate(
				&PlatformGenerationProviderRoute{},
				&PlatformGenerationJob{},
				&Task{},
				&ProviderCredentialVersion{},
			))
			require.NoError(t, installPlatformGenerationRouteBindingGuardsV4(database))
			route := relaySchemaV4RouteFixture("configured-" + test.name)
			require.NoError(t, database.Create(&route).Error)
			require.Error(t, database.Exec(`UPDATE platform_generation_provider_routes
SET capability_profile_id = ? WHERE id = ?`, "partial", route.ID).Error)

			profileRevision := "sha256:" + strings.Repeat("b", 64)
			profileSnapshot := `{"profile_id":"configured-v1"}`
			require.NoError(t, database.Exec(`UPDATE platform_generation_provider_routes
SET capability_profile_id = ?, capability_profile_revision = ?, capability_profile_snapshot = ?
WHERE id = ?`, "configured-v1", profileRevision, profileSnapshot, route.ID).Error)
			require.Error(t, database.Exec(`UPDATE platform_generation_provider_routes
SET capability_profile_snapshot = ? WHERE id = ?`, `{"profile_id":"tampered"}`, route.ID).Error)
			require.NoError(t, database.Exec(`UPDATE platform_generation_provider_routes
SET enabled = ?, rpm_limit = ? WHERE id = ?`, true, 240, route.ID).Error)

			providerURL := "https://provider.example/" + test.name + ".png?token=historical-secret"
			temporaryResultJSON := `{"result_url":"` + providerURL + `"}`
			terminalJob := PlatformGenerationJob{
				ID: uuid.NewString(), TenantID: uuid.NewString(), SourceClientID: "platform",
				RequestID: uuid.NewString(), IdempotencyKey: uuid.NewString(), RequestHash: strings.Repeat("f", 64),
				RequestJSON: `{}`, Model: route.Model, Mode: route.Mode,
				ExpectedCapabilityRevision: "sha256:" + strings.Repeat("b", 64),
				CapabilityRevision:         "sha256:" + strings.Repeat("b", 64),
				Status:                     PlatformGenerationStatusSucceeded, Progress: 100,
				ProviderRouteID: route.ID, ProviderChannelID: route.ChannelID,
				OutputsJSON: `[]`, UpstreamResultURL: providerURL, TemporaryResultJSON: temporaryResultJSON,
				ErrorDetailsJSON:         `{}`,
				SubmissionLeaseExpiresAt: time.Now().UTC(), PollLeaseExpiresAt: time.Now().UTC(),
				NextPollAt: time.Now().UTC(), TransferLeaseExpiresAt: time.Now().UTC(),
				NextTransferAt: time.Now().UTC(), CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC(),
			}
			terminalTaskID, err := PlatformGenerationNativeTaskID(terminalJob.ID)
			require.NoError(t, err)
			terminalJob.NativeTaskID = terminalTaskID
			terminalJob.UpstreamTaskID = "provider-" + test.name
			require.NoError(t, database.Create(&terminalJob).Error)
			keyIndex := route.KeyIndex
			terminalCredentialVersion := createPlatformGenerationProviderCredentialFixture(t, database, terminalJob.TenantID, route)
			terminalTask := Task{
				CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
				TaskID: terminalTaskID, ChannelId: route.ChannelID, Quota: 0, Status: TaskStatusSuccess, Progress: "100%",
				FailReason: providerURL,
				Data:       []byte(`{"content":{"video_url":"` + providerURL + `"}}`),
				PrivateData: TaskPrivateData{
					PinnedKeyIndex: &keyIndex, PinnedKeyFingerprint: route.KeyFingerprint,
					ProviderCredentialTenantID: terminalJob.TenantID,
					ProviderCredentialVersion:  terminalCredentialVersion, BillingSource: TaskBillingSourcePlatformExternal,
					UpstreamTaskID: "provider-" + test.name, ResultURL: providerURL,
				},
			}
			require.NoError(t, database.Create(&terminalTask).Error)
			legacyTerminalJob := terminalJob
			legacyTerminalJob.ID = uuid.NewString()
			legacyTerminalJob.RequestID = uuid.NewString()
			legacyTerminalJob.IdempotencyKey = uuid.NewString()
			legacyTerminalTaskID, err := PlatformGenerationNativeTaskID(legacyTerminalJob.ID)
			require.NoError(t, err)
			legacyTerminalJob.NativeTaskID = legacyTerminalTaskID
			legacyTerminalJob.UpstreamTaskID = "provider-legacy-" + test.name
			require.NoError(t, database.Create(&legacyTerminalJob).Error)
			legacyCredentialVersion := createPlatformGenerationProviderCredentialFixture(t, database, legacyTerminalJob.TenantID, route)
			legacyTerminalTask := Task{
				CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
				TaskID: legacyTerminalTaskID, ChannelId: route.ChannelID, Quota: 0, Status: TaskStatusSuccess, Progress: "100%",
				FailReason: providerURL,
				Data:       []byte(`{"content":{"video_url":"` + providerURL + `"}}`),
				PrivateData: TaskPrivateData{
					PinnedKeyIndex: &keyIndex, PinnedKeyFingerprint: route.KeyFingerprint,
					ProviderCredentialTenantID: legacyTerminalJob.TenantID,
					ProviderCredentialVersion:  legacyCredentialVersion, BillingSource: TaskBillingSourcePlatformExternal,
					UpstreamTaskID: "provider-legacy-" + test.name,
				},
			}
			require.NoError(t, database.Create(&legacyTerminalTask).Error)
			ordinaryTask := Task{
				CreatedAt: time.Now().UTC().Unix(), UpdatedAt: time.Now().UTC().Unix(),
				TaskID: "ordinary-" + uuid.NewString(), ChannelId: route.ChannelID, Status: TaskStatusSuccess,
				FailReason:  providerURL,
				Data:        []byte(`{"content":{"video_url":"` + providerURL + `"}}`),
				PrivateData: TaskPrivateData{BillingSource: "wallet", ResultURL: providerURL},
			}
			require.NoError(t, database.Create(&ordinaryTask).Error)
			require.NoError(t, scrubPlatformGenerationTerminalNativeResultURLsV4(database))
			require.NoError(t, scrubPlatformGenerationTerminalNativeResultURLsV4(database),
				"the configured-database scrub must be idempotent")
			require.NoError(t, database.First(&terminalTask, terminalTask.ID).Error)
			require.Empty(t, terminalTask.PrivateData.ResultURL)
			require.Empty(t, terminalTask.FailReason)
			require.Empty(t, terminalTask.GetResultURL())
			require.Empty(t, terminalTask.Data)
			require.NoError(t, database.First(&terminalJob, "id = ?", terminalJob.ID).Error)
			require.Empty(t, terminalJob.UpstreamResultURL)
			require.Empty(t, terminalJob.TemporaryResultJSON)
			require.Equal(t, `[]`, terminalJob.OutputsJSON)
			require.NoError(t, database.First(&legacyTerminalTask, legacyTerminalTask.ID).Error)
			require.Empty(t, legacyTerminalTask.PrivateData.ResultURL)
			require.Empty(t, legacyTerminalTask.FailReason)
			require.Empty(t, legacyTerminalTask.GetResultURL())
			require.Empty(t, legacyTerminalTask.Data)
			require.NoError(t, database.First(&legacyTerminalJob, "id = ?", legacyTerminalJob.ID).Error)
			require.Empty(t, legacyTerminalJob.UpstreamResultURL)
			require.Empty(t, legacyTerminalJob.TemporaryResultJSON)
			require.Equal(t, `[]`, legacyTerminalJob.OutputsJSON)
			require.NoError(t, database.First(&ordinaryTask, ordinaryTask.ID).Error)
			require.Equal(t, providerURL, ordinaryTask.PrivateData.ResultURL)
			require.Equal(t, providerURL, ordinaryTask.FailReason)
			require.Contains(t, string(ordinaryTask.Data), providerURL)

			require.NoError(t, database.Exec("DELETE FROM tasks").Error)
			require.NoError(t, database.Exec("DELETE FROM platform_generation_jobs").Error)
			require.NoError(t, database.Exec("DELETE FROM provider_credential_versions").Error)
			require.NoError(t, database.Migrator().DropTable(&PlatformGenerationProviderRoute{}))
			if test.name == "postgres" {
				require.NoError(t, database.Exec(`DROP FUNCTION IF EXISTS enforce_platform_generation_route_binding_v4()`).Error)
			}
			require.NoError(t, database.AutoMigrate(&RelaySchemaState{}, &PlatformGenerationProviderRoute{}))
			historical := relaySchemaV4RouteFixture("upgrade-" + test.name)
			require.NoError(t, database.Create(&historical).Error)
			for _, field := range []string{
				"CapabilityProfileID",
				"CapabilityProfileRevision",
				"CapabilityProfileSnapshot",
				"ModelReleaseID",
				"ModelReleaseRevision",
				"ModelReleaseCapabilityRevision",
			} {
				require.NoError(t, database.Migrator().DropColumn(&PlatformGenerationProviderRoute{}, field))
			}
			require.NoError(t, database.Migrator().AddColumn(&PlatformGenerationProviderRoute{}, "CapabilityProfileID"),
				"the fixture models a restart after one committed v4 column")
			require.NoError(t, database.Create(&RelaySchemaState{
				ID:              relaySchemaStateSingletonID,
				BaselineVersion: relaySchemaV3FrozenVersion,
				CurrentVersion:  relaySchemaV3FrozenVersion,
				TargetVersion:   relaySchemaV4FrozenVersion,
				State:           RelaySchemaStateApplying,
				Dirty:           true,
				AttemptID:       uuid.NewString(),
				CurrentChecksum: relaySchemaV3FrozenChecksumSHA256,
				TargetChecksum:  relaySchemaV4FrozenChecksumSHA256,
			}).Error)
			originalExpectedCatalog := relaySchemaExpectedCatalogForRuntime
			relaySchemaExpectedCatalogForRuntime = func(string, int64) string { return "" }
			t.Cleanup(func() { relaySchemaExpectedCatalogForRuntime = originalExpectedCatalog })
			require.NoError(t, migrateRelaySchemaV4GenerationRouteReleaseBinding(database))
			require.NoError(t, migrateRelaySchemaV4GenerationRouteReleaseBinding(database),
				"the partially applied upgrade must be restart-safe")
			require.Error(t, database.Exec(`UPDATE platform_generation_provider_routes
SET capability_profile_id = ? WHERE id = ?`, "partial-after-upgrade", historical.ID).Error)
		})
	}
}
