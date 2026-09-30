package toc

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/dto"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/service"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/stretchr/testify/require"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

type postgresCostExecutor struct {
	*fakeExecutor
	route    model.PlatformGenerationProviderRoute
	contract dto.PlatformGenerationExecutionContract
	rate     dto.PlatformProviderContractRateInput
	key      string
}

func (f *postgresCostExecutor) Create(_ context.Context, tx *gorm.DB, task Task, generation dto.PlatformGenerationRequest, catalog CatalogModel) error {
	generation.ExecutionContract = &f.contract
	if generation.Metadata == nil {
		generation.Metadata = map[string]any{}
	}
	requestJSON := fixtureJSON(generation)
	requestDigest := sha256.Sum256([]byte(requestJSON))
	contractSHA, err := f.contract.Digest()
	if err != nil {
		return err
	}
	// Explicit synthetic posting snapshot; production admission is covered in
	// service's CreateLocalTOC test. The PG gate exercises Vault, Stage and costs.
	generation.Metadata["relay_execution_cost_snapshot"] = map[string]any{
		"schema_version": 1, "execution_contract_sha256": contractSHA,
		"route_id": f.route.RouteKey, "contract_rate": f.rate,
	}
	requestJSON = fixtureJSON(generation)
	job := model.PlatformGenerationJob{ID: task.ID, TenantID: model.RelayTOCGenerationTenantID,
		SourceClientID: model.RelayTOCGenerationClientID, IdempotencyKey: task.ID, RequestJSON: requestJSON,
		RequestHash: fmt.Sprintf("%x", requestDigest), Model: catalog.PublicModelID, Mode: generation.Mode,
		ExpectedCapabilityRevision: catalog.CapabilityRevision, CapabilityRevision: catalog.CapabilityRevision,
		Status: "queued", OutputsJSON: "[]", ProviderRouteID: f.route.ID, ProviderChannelID: f.route.ChannelID,
		CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC()}
	if err := tx.Create(&job).Error; err != nil {
		return err
	}
	authority := model.RelayGenerationAuthority{JobID: job.ID, Kind: model.RelayGenerationAuthorityTOC,
		OwnerUserID: task.UserID, AdmissionID: task.AdmissionID, TenantID: job.TenantID,
		SourceClientID: job.SourceClientID, BillingPolicyRevision: model.RelayTOCBillingPolicyRevision, CreatedAt: time.Now().UTC()}
	if err := tx.Create(&authority).Error; err != nil {
		return err
	}
	return ValidateAdmission(tx, &job)
}

// This gate only accepts an empty database in a separately launched loopback
// PostgreSQL instance. It never connects to relay_toc_local, drops a schema,
// changes frozen migration definitions, or grants UPDATE on financial facts.
func TestTOCProviderCostPostgresLeastPrivilege(t *testing.T) {
	dsn := os.Getenv("RELAY_TOC_COST_PG_TEST_DSN")
	if dsn == "" {
		t.Skip("requires a fresh isolated relay_toc_cost_gate PostgreSQL database")
	}
	parsed, err := url.Parse(dsn)
	require.NoError(t, err)
	require.Contains(t, []string{"postgres", "postgresql"}, parsed.Scheme)
	require.NotNil(t, parsed.User)
	require.Equal(t, "postgres", parsed.User.Username())
	require.Equal(t, "/relay_toc_cost_gate", parsed.Path)
	require.True(t, parsed.Hostname() == "localhost" || net.ParseIP(parsed.Hostname()).IsLoopback())
	for key := range parsed.Query() {
		require.Contains(t, []string{"sslmode", "connect_timeout"}, key)
	}
	admin, err := gorm.Open(postgres.Open(dsn), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	adminPool, err := admin.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = adminPool.Close() })
	var initialTables int64
	require.NoError(t, admin.Raw("SELECT count(*) FROM pg_tables WHERE schemaname='public'").Scan(&initialTables).Error)
	require.Zero(t, initialTables, "refusing an initialized database")
	for key, value := range map[string]string{
		"SQL_DSN": dsn, "SQL_DSN_FILE": "", "RELAY_RUNTIME_PROFILE": "toc", "RELAY_PLATFORM_CONNECTOR_ENABLED": "false",
		"APP_ENV": "development", "DEPLOYMENT_ENV": "development", "RELAY_COMPAT_ENVIRONMENT": "development",
		"RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED": "false", "RELAY_DATABASE_TLS_ATTESTATION_REQUIRED": "false",
		"RELAY_DATABASE_SECRET_FILES_REQUIRED": "false", "RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED": "false",
		"RELAY_RUNTIME_DATABASE_ROLE": "relay_runtime", "RELAY_SCHEMA_OWNER_DATABASE_ROLE": "postgres",
		"RELAY_COMPAT_SOURCE_REVISION": strings.Repeat("a", 40), "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256": "sha256:" + strings.Repeat("b", 64),
	} {
		t.Setenv(key, value)
	}
	previousDB, previousLogDB := model.DB, model.LOG_DB
	previousMain, previousLog := common.MainDatabaseType(), common.LogDatabaseType()
	previousRedis := common.RedisEnabled
	t.Cleanup(func() {
		model.DB, model.LOG_DB = previousDB, previousLogDB
		common.SetDatabaseTypes(previousMain, previousLog)
		common.RedisEnabled = previousRedis
	})
	common.SetDatabaseTypes(common.DatabaseTypePostgreSQL, common.DatabaseTypePostgreSQL)
	common.RedisEnabled = false
	model.DB, model.LOG_DB = admin, admin
	runtimePassword := uuid.NewString() + uuid.NewString()
	verifier, err := model.GenerateRelaySCRAMSHA256Verifier([]byte(runtimePassword))
	require.NoError(t, err)
	require.NoError(t, admin.Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec("SELECT set_config('relay.cost_gate_password', ?, true)", verifier).Error; err != nil {
			return err
		}
		return tx.Exec(`DO $roles$ BEGIN
 CREATE ROLE relay_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 CREATE ROLE relay_download_edge NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 EXECUTE format('CREATE ROLE relay_cost_test_runtime LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',current_setting('relay.cost_gate_password'));
 GRANT relay_runtime TO relay_cost_test_runtime;
 REVOKE CREATE,TEMPORARY ON DATABASE relay_toc_cost_gate FROM PUBLIC,relay_runtime,relay_download_edge,relay_cost_test_runtime;
 GRANT CONNECT ON DATABASE relay_toc_cost_gate TO relay_runtime,relay_download_edge,relay_cost_test_runtime;
 REVOKE ALL ON SCHEMA public FROM PUBLIC,relay_runtime,relay_download_edge,relay_cost_test_runtime;
 GRANT USAGE ON SCHEMA public TO relay_runtime,relay_download_edge,relay_cost_test_runtime;
 ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
 END $roles$`).Error
	}))
	migration, err := model.RunRelaySchemaMigrations(context.Background(), "")
	require.NoError(t, err)
	require.EqualValues(t, 12, migration.ToVersion)
	require.True(t, migration.Status.Current)
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "true")
	require.NoError(t, model.ApplyRelayDatabasePrivilegeManifestWithDB(admin))
	require.NoError(t, model.ApplyRelayDownloadEdgeDatabasePrivilegeManifestWithDB(admin))
	t.Setenv("RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED", "false")
	runtimeURL := *parsed
	runtimeURL.User = url.UserPassword("relay_cost_test_runtime", runtimePassword)
	runtimeDB, err := gorm.Open(postgres.Open(runtimeURL.String()), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
	require.NoError(t, err)
	runtimePool, err := runtimeDB.DB()
	require.NoError(t, err)
	t.Cleanup(func() { _ = runtimePool.Close() })
	var safeRole bool
	require.NoError(t, runtimeDB.Raw(`SELECT current_user='relay_cost_test_runtime' AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreatedb AND NOT rolcreaterole FROM pg_roles WHERE rolname=current_user`).Scan(&safeRole).Error)
	require.True(t, safeRole)
	for _, table := range []string{"platform_provider_terminal_outcomes", "platform_channel_cost_events", "toc_provider_cost_receipts"} {
		var updateAllowed bool
		require.NoError(t, runtimeDB.Raw("SELECT has_table_privilege(current_user, ?, 'UPDATE')", table).Scan(&updateAllowed).Error)
		require.False(t, updateAllowed, table)
	}
	for _, table := range []string{"platform_channel_cost_reconciliations", "platform_generation_jobs", "platform_generation_provider_routes"} {
		var updateAllowed bool
		require.NoError(t, runtimeDB.Raw("SELECT has_table_privilege(current_user, ?, 'UPDATE')", table).Scan(&updateAllowed).Error)
		require.True(t, updateAllowed, "mutable serialization rows retain their manifest locks: "+table)
	}
	input, outcome, fixtureService, executor, request := seedTOCPostgresCostEvidence(t, admin)
	model.DB, model.LOG_DB = runtimeDB, runtimeDB
	created, err := model.DiscoverPlatformChannelCostReconciliations(10)
	require.NoError(t, err)
	require.Equal(t, 1, created)
	claim, err := model.ClaimPlatformChannelCostReconciliation(time.Minute)
	require.NoError(t, err)
	require.NotNil(t, claim)
	checkClaim := func(candidate model.PlatformChannelCostReconciliationClaim, wantExisting bool) error {
		return runtimeDB.Transaction(func(tx *gorm.DB) error {
			facts, existing, lockErr := model.LockPlatformProviderCostMaterializationClaimTx(tx, candidate)
			if lockErr != nil {
				return lockErr
			}
			require.Equal(t, outcome.ID, facts.Outcome.ID)
			require.Equal(t, input.RelayJobID, facts.Job.ID)
			require.Equal(t, wantExisting, existing != nil)
			return nil
		})
	}
	require.NoError(t, checkClaim(*claim, false), "immutable outcome SELECT must not require UPDATE")
	stale := *claim
	stale.Token = uuid.NewString()
	require.ErrorIs(t, checkClaim(stale, false), model.ErrPlatformCostReconciliationClaimLost)
	createdEvent, err := service.EnqueuePlatformChannelCost(input)
	require.NoError(t, err)
	require.True(t, createdEvent)
	require.NoError(t, checkClaim(*claim, true), "immutable event replay SELECT must not require UPDATE")
	createdEvent, err = service.EnqueuePlatformChannelCost(input)
	require.NoError(t, err)
	require.False(t, createdEvent)
	for _, table := range []string{"platform_provider_terminal_outcomes", "platform_channel_cost_events"} {
		updateErr := runtimeDB.Exec("UPDATE " + table + " SET id=id").Error
		var postgresError *pgconn.PgError
		require.True(t, errors.As(updateErr, &postgresError), table)
		require.Equal(t, "42501", postgresError.Code, "runtime must remain unable to update immutable evidence")
	}
	require.NoError(t, runtimeDB.Model(&model.PlatformChannelCostReconciliation{}).Where("relay_job_id = ?", claim.RelayJobID).
		Update("claim_expires_at", time.Now().UTC().Add(-time.Minute)).Error)
	require.ErrorIs(t, checkClaim(*claim, true), model.ErrPlatformCostReconciliationClaimLost)
	recoveredClaim, err := model.ClaimPlatformChannelCostReconciliation(time.Minute)
	require.NoError(t, err)
	require.NotNil(t, recoveredClaim)
	require.Equal(t, claim.RelayJobID, recoveredClaim.RelayJobID)
	completed, err := model.CompletePlatformChannelCostReconciliation(*recoveredClaim)
	require.NoError(t, err)
	require.True(t, completed)
	require.NoError(t, service.RegisterRelayTOCLocalCostRecorder(RecordProviderCost))
	require.NoError(t, service.RegisterRelayTOCCostReadiness(VerifySchema))
	delivery, err := model.ClaimPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindChannelCost, time.Minute)
	require.NoError(t, err)
	require.NotNil(t, delivery)
	state, won, err := service.DeliverRelayTOCLocalCostClaim(context.Background(), *delivery)
	require.NoError(t, err)
	require.True(t, won)
	require.Equal(t, model.PlatformRelayDeliveryDelivered, state)
	var receipt CostReceipt
	require.NoError(t, runtimeDB.First(&receipt, "event_id = ?", input.EventID).Error)
	require.Equal(t, input.RelayJobID, receipt.JobID)
	require.Equal(t, 2, receipt.UserID)
	require.NoError(t, runtimeDB.Transaction(func(tx *gorm.DB) error {
		event, eventErr := model.GetPlatformChannelCostEvent(input.EventID)
		if eventErr != nil {
			return eventErr
		}
		return RecordProviderCost(tx, input.RelayJobID, *event)
	}))
	var count int64
	require.NoError(t, runtimeDB.Model(&CostReceipt{}).Count(&count).Error)
	require.EqualValues(t, 1, count)
	_, won, err = service.DeliverRelayTOCLocalCostClaim(context.Background(), *delivery)
	require.Error(t, err)
	require.False(t, won)

	// A second independently admitted TOC reservation traverses the actual
	// Vault -> recovery writer -> Reconcile producer -> local receipt under the
	// runtime login, without seeding or directly enqueueing its cost event.
	fixtureService.DB, executor.db = runtimeDB, runtimeDB
	request.IdempotencyKey = uuid.NewString()
	second, err := fixtureService.CreateTask(context.Background(), 2, request)
	require.NoError(t, err)
	stageTOCPostgresCostEvidence(t, runtimeDB, second.ID, executor)
	created, err = model.DiscoverPlatformChannelCostReconciliations(10)
	require.NoError(t, err)
	require.Equal(t, 1, created)
	reconcileClaim, err := model.ClaimPlatformChannelCostReconciliation(time.Minute)
	require.NoError(t, err)
	require.NotNil(t, reconcileClaim)
	require.Equal(t, second.ID, reconcileClaim.RelayJobID)
	result, err := service.ReconcilePlatformChannelCostClaim(*reconcileClaim)
	require.NoError(t, err)
	require.True(t, result.Materialized)
	require.True(t, result.Completed)
	require.Empty(t, result.DeferredCode)
	var produced model.PlatformChannelCostEvent
	require.NoError(t, runtimeDB.First(&produced, "relay_job_id = ?", second.ID).Error)
	require.EqualValues(t, 22, produced.AmountCents)
	var producedPayload dto.PlatformChannelCostPayload
	require.NoError(t, common.Unmarshal([]byte(produced.PayloadJSON), &producedPayload))
	require.NotEqual(t, executor.contract.Routes[0].ProviderCredentialSetVersion, producedPayload.ProviderCredentialVersion)
	_, err = service.ReconcilePlatformChannelCostClaim(*reconcileClaim)
	require.ErrorIs(t, err, model.ErrPlatformCostReconciliationClaimLost)
	delivery, err = model.ClaimPlatformRelayExternalDelivery(model.PlatformRelayDeliveryKindChannelCost, time.Minute)
	require.NoError(t, err)
	require.NotNil(t, delivery)
	state, won, err = service.DeliverRelayTOCLocalCostClaim(context.Background(), *delivery)
	require.NoError(t, err)
	require.True(t, won)
	require.Equal(t, model.PlatformRelayDeliveryDelivered, state)
	require.NoError(t, runtimeDB.Model(&CostReceipt{}).Where("job_id = ?", second.ID).Count(&count).Error)
	require.EqualValues(t, 1, count)
}

func seedTOCPostgresCostEvidence(t *testing.T, db *gorm.DB) (dto.PlatformChannelCostInput, model.PlatformProviderTerminalOutcome, *Service, *postgresCostExecutor, CreateTaskRequest) {
	t.Helper()
	// Reuse only the test offer/capability fixture; all persisted authority,
	// reservation, cost and receipt rows below use the officially migrated PG.
	s, fake := fixture(t)
	s.DB, fake.db = db, db
	for id := 1; id <= 3; id++ {
		role := common.RoleCommonUser
		if id == 1 {
			role = common.RoleRootUser
		}
		require.NoError(t, db.Create(&model.User{Id: id, Username: fmt.Sprintf("cost-gate-user-%d", id), Password: "isolated-no-login", Role: role, Status: common.UserStatusEnabled, Group: "default", AffCode: fmt.Sprintf("cost-gate-aff-%d", id), AuthVersion: 1}).Error)
	}
	channel, member, key := seedTOCCostVault(t, db, 21)
	route := model.PlatformGenerationProviderRoute{RouteKey: "cost-gate-route", Model: "seedream-5", Mode: "text_to_image", ProviderName: "volcengine-ark", AccountID: "primary", ChannelID: channel.Id, KeyFingerprint: member.PrivateData.PinnedKeyFingerprint, ChannelClass: "official", UpstreamModel: "seedream-5", CapabilityProfileID: "isolated-cost-gate", CapabilityProfileRevision: fake.evidence.Resource.CapabilityRevision, CapabilityProfileSnapshot: fixtureJSON(fake.evidence.Resource.Capabilities)}
	require.NoError(t, db.Create(&route).Error)
	rate := dto.PlatformProviderContractRateInput{ID: uuid.NewString(), ProviderName: route.ProviderName, ChannelID: route.ChannelID, UpstreamModel: route.UpstreamModel, Mode: route.Mode, Resolution: "2k", BillingUnit: dto.PlatformContractRateUnitOutputItem, UnitAmountCents: 22, Currency: "CNY", EffectiveFrom: time.Now().UTC().Add(-time.Hour).Truncate(time.Microsecond), SourceReference: "isolated PG contract", SourceDocumentSHA256: strings.Repeat("b", 64)}
	require.NoError(t, rate.Validate())
	costSHA, err := dto.PlatformExecutionCanonicalSHA256(rate)
	require.NoError(t, err)
	contract := dto.PlatformGenerationExecutionContract{SchemaVersion: 1, RoutingReleaseSHA256: fake.evidence.RoutingReleaseSHA256, ProviderCostReadinessSHA256: fake.evidence.CostReadinessSHA256,
		Routes: []dto.PlatformGenerationExecutionRoute{{RouteID: route.RouteKey, ChannelID: route.ChannelID, ProviderAccountID: route.AccountID, ProviderCredentialSetVersion: channel.CredentialSetVersion, RouteBindingSHA256: "sha256:" + strings.Repeat("f", 64), Mode: route.Mode, Resolution: "2k", CostKind: "contract_rate", CostID: rate.ID, CostSHA256: costSHA}}}
	executor := &postgresCostExecutor{fakeExecutor: fake, route: route, contract: contract, rate: rate, key: key}
	s.Executor = executor
	_, request := configureOffer(t, s, fake)
	task, err := s.CreateTask(context.Background(), 2, request)
	require.NoError(t, err)
	contractSHA, err := contract.Digest()
	require.NoError(t, err)
	outcome := stageTOCPostgresCostEvidence(t, db, task.ID, executor)
	input := dto.PlatformChannelCostInput{SchemaVersion: 2, EventID: uuid.NewString(), AmountCents: 22, IdempotencyKey: uuid.NewString(), ChannelKey: route.RouteKey, ChannelType: route.ChannelClass, RouteID: route.ID,
		OccurredAt: outcome.OccurredAt, ExternalReference: outcome.ExternalReference, PersonalWorkspaceID: workspace(2), TaskID: task.ID, RelayJobID: task.ID, EvidenceSource: "contract_rate", EvidenceReference: "isolated PG contract", SourceDocumentSHA256: strings.Repeat("b", 64), ExecutionContractSHA256: contractSHA, ProviderCostRevisionSHA256: costSHA,
		PlatformProviderRouteIdentity: dto.PlatformProviderRouteIdentity{IdentityStatus: "bound", ProviderName: route.ProviderName, ProviderAccountID: route.AccountID, ProviderChannelID: route.ChannelID, ProviderRouteID: route.ID, ProviderKeyFingerprint: route.KeyFingerprint, ProviderCredentialVersion: member.PrivateData.ProviderCredentialVersion, RouteKey: route.RouteKey, RoutingReleaseSHA256: contract.RoutingReleaseSHA256}}
	require.NoError(t, input.Validate())
	return input, outcome, s, executor, request
}

func seedTOCCostVault(t *testing.T, db *gorm.DB, channelID int) (model.Channel, *model.Task, string) {
	t.Helper()
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE", "")
	t.Setenv("RELAY_PROVIDER_CREDENTIAL_KEYRING_JSON", `{"schema_version":1,"active_key_id":"isolated-gate","keys":{"isolated-gate":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}`)
	if db.Dialector.Name() == "sqlite" {
		require.NoError(t, db.AutoMigrate(&model.Channel{}, &model.ProviderChannelCredentialSetVersion{}, &model.ProviderCredentialVersion{}))
	}
	previous := model.DB
	model.DB = db
	defer func() { model.DB = previous }()
	key := "isolated-local-provider-key-" + uuid.NewString()
	channel := model.Channel{Id: channelID, Name: "isolated-cost-gate", Key: key, Status: common.ChannelStatusEnabled}
	require.NoError(t, db.Create(&channel).Error)
	index := 0
	member := &model.Task{ChannelId: channel.Id, PrivateData: model.TaskPrivateData{PinnedKeyIndex: &index, PinnedKeyFingerprint: fmt.Sprintf("%x", sha256.Sum256([]byte(key))), TransientProviderKey: key}}
	require.NoError(t, model.BindTaskProviderCredentialVersion(member, model.RelayTOCGenerationTenantID))
	require.NotEmpty(t, channel.CredentialSetVersion)
	require.NotEqual(t, channel.CredentialSetVersion, member.PrivateData.ProviderCredentialVersion)
	return channel, member, key
}

func stageTOCPostgresCostEvidence(t *testing.T, db *gorm.DB, jobID string, executor *postgresCostExecutor) model.PlatformProviderTerminalOutcome {
	t.Helper()
	lease := uuid.NewString()
	now, err := model.GetDBTimeTx(db)
	require.NoError(t, err)
	require.NoError(t, db.Model(&model.PlatformGenerationJob{}).Where("id = ?", jobID).Updates(map[string]any{
		"status": model.PlatformGenerationStatusSubmitting, "submission_lease_token": lease, "submission_lease_expires_at": now.Add(time.Minute),
	}).Error)
	admission := model.PlatformGenerationRouteAdmission{JobID: jobID, RouteID: executor.route.ID, State: model.PlatformGenerationRouteAdmissionPosting, SlotHeld: true, Attempt: 1}
	require.NoError(t, db.Create(&admission).Error)
	nativeID, err := model.PlatformGenerationNativeTaskID(jobID)
	require.NoError(t, err)
	index := executor.route.KeyIndex
	task := &model.Task{TaskID: nativeID, ChannelId: executor.route.ChannelID, Platform: "isolated-gate", UserId: 1, Group: "default", Action: "text_to_image", SubmitTime: now.Unix(),
		PrivateData: model.TaskPrivateData{PinnedKeyIndex: &index, PinnedKeyFingerprint: executor.route.KeyFingerprint, TransientProviderKey: executor.key,
			BillingSource: model.TaskBillingSourceRelayTOCExternal, ProviderRoutingReleaseSHA256: executor.contract.RoutingReleaseSHA256, ProviderRouteBindingSHA256: executor.contract.Routes[0].RouteBindingSHA256}}
	require.NoError(t, model.BindTaskProviderCredentialVersion(task, model.RelayTOCGenerationTenantID))
	require.NotEqual(t, executor.contract.Routes[0].ProviderCredentialSetVersion, task.PrivateData.ProviderCredentialVersion)
	require.NoError(t, model.StagePlatformGenerationNativeTaskRecovery(jobID, lease, task))
	var staged model.PlatformGenerationJob
	require.NoError(t, db.First(&staged, "id = ?", jobID).Error)
	require.NoError(t, model.ValidatePlatformGenerationRequestSnapshotBinding(staged))
	outcome := model.PlatformProviderTerminalOutcome{ID: uuid.NewString(), RouteID: executor.route.ID, RouteKey: executor.route.RouteKey, ProviderName: executor.route.ProviderName, ChannelClass: executor.route.ChannelClass, RelayJobID: jobID, Outcome: model.PlatformProviderOutcomeSucceeded, FailureOwner: model.PlatformProviderFailureOwnerNone, OccurredAt: now.Truncate(time.Microsecond), ExternalReference: "isolated-postgres-provider-receipt-" + jobID}
	created, err := model.CreatePlatformProviderTerminalOutcome(&outcome)
	require.NoError(t, err)
	require.True(t, created)
	return outcome
}
