param(
    [string]$CandidateImage = "sha256:142185d134d0427cc073e7235a5bb10c248d5eabad1c1e737abdf83e56c611e6"
)

$ErrorActionPreference = "Stop"
$pinnedCandidateID = "sha256:142185d134d0427cc073e7235a5bb10c248d5eabad1c1e737abdf83e56c611e6"
$pinnedCandidateRepoDigest = "ai-video/new-api-relay@sha256:142185d134d0427cc073e7235a5bb10c248d5eabad1c1e737abdf83e56c611e6"
$pinnedCandidateRevision = "b345647f137a2f68e71392b77768627c39c412c5"
$pinnedCandidateUpstreamRevision = "0ab02020603d22e5613bc4cf46bfab06f8567769"
$pinnedCandidateSourceSnapshot = "sha256:6e4bb769e60cb91cd1408aa1102a0ff037da29fd0d3b9d5f05516a4b8fbee230"
$pinnedCandidateSourceFileCount = "1962"
$pinnedV1Revision = "709e9b45b25a6baa415ab985078bd7764a35eaf9"
$pinnedV1FixturePatchSHA256 = "dd3bbe7dea195bf83222f2acb32ea0ab96208ac64d7f66dcbc2ddb5f5e3a3449"
$qualifiedPostgresImage = "ai-video-platform-postgres16-pgaudit-canary:16.1"
$qualifiedPostgresImageID = "sha256:9c2d47297a4a7bfcdeaa8565bc66f40243e73bd3eab03f6cccbaadf652d76e10"
$legacyPostgres = "ai-video-relay-schema-legacy-gate-pg16"
$referencePostgres = "ai-video-relay-schema-reference-gate-pg16"
$candidateContainer = "ai-video-relay-schema-legacy-gate-candidate"
$protectedSecretVolume = "ai-video-relay-schema-gate-protected-secrets"
$pinnedV1SourceVolume = "ai-video-relay-schema-gate-v1-source"
$postgresTLSVolume = "ai-video-relay-schema-gate-postgres-tls"
$legacyPassword = "relay-schema-legacy-gate-admin-password"
$referencePassword = "relay-schema-reference-gate-admin-password"
$referenceRuntimePassword = "relay-runtime-password-0123456789-ab"
$referenceDatabase = "relay_v2_fresh"
$repository = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$pinnedV1FixturePatch = Join-Path $repository "scripts\fixtures\relay-schema-v1-pg16-tls-test-fixture.patch"

function Remove-GateContainer([string]$Name) {
    $existing = docker ps -a --filter "name=^/$Name$" --format "{{.Names}}"
    if ($existing -eq $Name) {
        docker rm -f $Name | Out-Null
    }
}

function Wait-Postgres([string]$Name) {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        docker exec $Name pg_isready -U postgres *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "PostgreSQL container $Name did not become ready"
}

function Get-PostgresPort([string]$Name) {
    $mapping = docker port $Name 5432/tcp
    if ($mapping -notmatch ":([0-9]+)$") {
        throw "PostgreSQL port mapping for $Name is invalid"
    }
    return $Matches[1]
}

function Get-SHA256Hex([string]$Value) {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Value))
        return -join ($digest | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $sha256.Dispose()
    }
}

function Assert-GoTestPassed([string[]]$Output, [string]$TestName, [string]$GateName) {
    $records = @($Output | ForEach-Object {
        try { $_ | ConvertFrom-Json } catch { $null }
    } | Where-Object { $_ -ne $null -and $_.Test -eq $TestName })
    if ($records.Action -contains "skip" -or $records.Action -notcontains "pass") {
        throw "$GateName did not record an explicit non-skipped PASS for $TestName"
    }
    $Output | Write-Output
}

try {
    $candidateInspectionJSON = docker image inspect $CandidateImage
    if ($LASTEXITCODE -ne 0) {
        throw "The immutable previous-candidate image is unavailable: $CandidateImage"
    }
    $candidateInspection = $candidateInspectionJSON | ConvertFrom-Json
    if ($candidateInspection.Count -ne 1) {
        throw "The immutable previous-candidate image reference is ambiguous"
    }
    $candidateMetadata = $candidateInspection[0]
    $candidateLabels = $candidateMetadata.Config.Labels
    if ($candidateMetadata.Id -ne $pinnedCandidateID -or
        $candidateMetadata.RepoDigests -notcontains $pinnedCandidateRepoDigest -or
        $candidateLabels.'org.opencontainers.image.revision' -ne $pinnedCandidateRevision -or
        $candidateLabels.'ai.video.relay.upstream-revision' -ne $pinnedCandidateUpstreamRevision -or
        $candidateLabels.'ai.video.relay.source-snapshot-sha256' -ne $pinnedCandidateSourceSnapshot -or
        $candidateLabels.'ai.video.relay.source-file-count' -ne $pinnedCandidateSourceFileCount) {
        throw "The previous-candidate image does not match the pinned release evidence"
    }
    Write-Output "legacy-candidate-id=$($candidateMetadata.Id)"
    Write-Output "legacy-candidate-repo-digest=$pinnedCandidateRepoDigest"
    Write-Output "legacy-candidate-source-revision=$pinnedCandidateRevision"
    Write-Output "legacy-candidate-upstream-revision=$pinnedCandidateUpstreamRevision"
    Write-Output "legacy-candidate-source-snapshot=$pinnedCandidateSourceSnapshot"
    Write-Output "legacy-candidate-source-file-count=$pinnedCandidateSourceFileCount"
    $actualQualifiedPostgresID = docker image inspect $qualifiedPostgresImage --format "{{.Id}}"
    if ($LASTEXITCODE -ne 0 -or $actualQualifiedPostgresID -ne $qualifiedPostgresImageID) {
        throw "The qualified PostgreSQL 16/pgaudit image does not match the pinned release gate image"
    }
    $actualPinnedV1Revision = git -C $repositoryRoot rev-parse "$pinnedV1Revision^{commit}"
    if ($LASTEXITCODE -ne 0 -or $actualPinnedV1Revision -ne $pinnedV1Revision) {
        throw "The immutable Relay schema v1 source revision is unavailable"
    }
    $actualFixturePatchSHA256 = (Get-FileHash -Algorithm SHA256 $pinnedV1FixturePatch).Hash.ToLowerInvariant()
    if ($actualFixturePatchSHA256 -ne $pinnedV1FixturePatchSHA256) {
        throw "The pinned v1 TLS test fixture patch does not match its frozen digest"
    }
    Write-Output "qualified-postgres-image-id=$actualQualifiedPostgresID"
    Write-Output "relay-schema-v1-source-revision=$actualPinnedV1Revision"
    Write-Output "relay-schema-v1-test-fixture-patch-sha256=sha256:$actualFixturePatchSHA256"
    Remove-GateContainer $candidateContainer
    Remove-GateContainer $legacyPostgres
    Remove-GateContainer $referencePostgres
    docker volume rm -f $protectedSecretVolume *> $null
    docker volume rm -f $pinnedV1SourceVolume *> $null
    docker volume rm -f $postgresTLSVolume *> $null
    docker volume create $protectedSecretVolume | Out-Null
    docker volume create $pinnedV1SourceVolume | Out-Null
    docker volume create $postgresTLSVolume | Out-Null

    docker run --rm -v "${repositoryRoot}:/workspace:ro" -v "${pinnedV1SourceVolume}:/v1" `
      -w /workspace golang:1.25.1 bash -ec `
      "git -c safe.directory=/workspace archive $pinnedV1Revision | tar -x -C /v1 && cd /v1 && git apply --check /workspace/backend/new-api-relay/scripts/fixtures/relay-schema-v1-pg16-tls-test-fixture.patch && git apply /workspace/backend/new-api-relay/scripts/fixtures/relay-schema-v1-pg16-tls-test-fixture.patch"
    if ($LASTEXITCODE -ne 0) {
        throw "The immutable Relay schema v1 test source could not be materialized"
    }

    $tlsScript = @'
set -eu
umask 077
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=relay-schema-gate-ca -keyout /tls/ca.key -out /tls/ca.crt
openssl req -newkey rsa:2048 -nodes -subj /CN=host.docker.internal -addext subjectAltName=DNS:host.docker.internal,DNS:localhost,IP:127.0.0.1 -keyout /tls/server.key -out /tls/server.csr
openssl x509 -req -days 2 -in /tls/server.csr -CA /tls/ca.crt -CAkey /tls/ca.key -CAcreateserial -copy_extensions copy -out /tls/server.crt
openssl req -newkey rsa:2048 -nodes -subj /CN=obs.lifecycle-gate.myhuaweicloud.com -addext subjectAltName=DNS:obs.lifecycle-gate.myhuaweicloud.com,DNS:relay-lifecycle-artifacts.obs.lifecycle-gate.myhuaweicloud.com -keyout /tls/obs-server.key -out /tls/obs-server.csr
openssl x509 -req -days 2 -in /tls/obs-server.csr -CA /tls/ca.crt -CAkey /tls/ca.key -CAcreateserial -copy_extensions copy -out /tls/obs-server.crt
chown 999:999 /tls/server.key /tls/server.crt /tls/obs-server.key /tls/obs-server.crt /tls/ca.crt
chmod 0600 /tls/server.key /tls/obs-server.key
chmod 0644 /tls/server.crt /tls/obs-server.crt /tls/ca.crt
'@
    docker run --rm -v "${postgresTLSVolume}:/tls" $qualifiedPostgresImage bash -ec $tlsScript
    if ($LASTEXITCODE -ne 0) {
        throw "The disposable PostgreSQL TLS fixture could not be created"
    }

    $postgresArguments = @(
        "-c", "ssl=on",
        "-c", "ssl_cert_file=/tls/server.crt",
        "-c", "ssl_key_file=/tls/server.key",
        "-c", "ssl_ca_file=/tls/ca.crt",
        "-c", "shared_preload_libraries=auto_explain,pgaudit",
        "-c", "pgaudit.log=ddl,role,write",
        "-c", "pgaudit.log_parameter=off",
        "-c", "log_parameter_max_length=0",
        "-c", "log_parameter_max_length_on_error=0",
        "-c", "auto_explain.log_parameter_max_length=0"
    )
    docker run -d --name $legacyPostgres -e "POSTGRES_PASSWORD=$legacyPassword" `
      -v "${postgresTLSVolume}:/tls:ro" -p "127.0.0.1::5432" $qualifiedPostgresImage $postgresArguments | Out-Null
    docker run -d --name $referencePostgres -e "POSTGRES_PASSWORD=$referencePassword" `
      -v "${postgresTLSVolume}:/tls:ro" -p "127.0.0.1::5432" $qualifiedPostgresImage $postgresArguments | Out-Null
    Wait-Postgres $legacyPostgres
    Wait-Postgres $referencePostgres
    docker exec $legacyPostgres createdb -U postgres new_api
    docker exec $referencePostgres createdb -U postgres $referenceDatabase
    docker exec $legacyPostgres psql -v ON_ERROR_STOP=1 -U postgres -d new_api -c "CREATE EXTENSION pgaudit WITH SCHEMA pg_catalog" | Out-Null
    docker exec $referencePostgres psql -v ON_ERROR_STOP=1 -U postgres -d $referenceDatabase -c "CREATE EXTENSION pgaudit WITH SCHEMA pg_catalog" | Out-Null

    $legacyPort = Get-PostgresPort $legacyPostgres
    $referencePort = Get-PostgresPort $referencePostgres
    $referenceDSN = "postgresql://postgres:$referencePassword@host.docker.internal:$referencePort/${referenceDatabase}?sslmode=verify-full&sslrootcert=/tls/ca.crt"
    $referenceTestOutput = & docker run --rm `
      --add-host "obs.lifecycle-gate.myhuaweicloud.com:127.0.0.2" `
      --add-host "relay-lifecycle-artifacts.obs.lifecycle-gate.myhuaweicloud.com:127.0.0.2" `
      -e "TEST_POSTGRES_DSN=$referenceDSN" `
      -e "TEST_OBS_CERT=/tls/obs-server.crt" -e "TEST_OBS_KEY=/tls/obs-server.key" `
      -e "TEST_PROTECTED_SECRET_SOURCE_DIR=/relay-secret-source" `
      -e "TEST_PROTECTED_SECRET_READONLY_DIR=/run/relay-secrets" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/relay-secret-source:rw" -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src golang:1.25.1 bash -ec `
      'cat /tls/ca.crt >> /etc/ssl/certs/ca-certificates.crt && exec /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresFreshCatalogRoleAndLock$" -count=1'
    $referenceTestExitCode = $LASTEXITCODE
    if ($referenceTestExitCode -ne 0) {
		$referenceTestOutput | Write-Output
        throw "The independently migrated PostgreSQL 16 reference failed"
    }
    Assert-GoTestPassed $referenceTestOutput "TestRelaySchemaPostgresFreshCatalogRoleAndLock" "fresh Relay schema v2 reference"
    Write-Output "fresh-v2-row2-only-gate=PASS"

    $referenceRuntimeDSN = "postgresql://relay_runtime:$referenceRuntimePassword@host.docker.internal:$referencePort/${referenceDatabase}?sslmode=verify-full&sslrootcert=/tls/ca.crt&search_path=public"
    $referenceRuntimeVerifier = docker exec $referencePostgres psql -U postgres -d $referenceDatabase -Atc "SELECT rolpassword FROM pg_catalog.pg_authid WHERE rolname = 'relay_runtime'"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($referenceRuntimeVerifier)) {
        throw "The disposable reference runtime role verifier is unavailable"
    }
    $rotationBarrierOutput = & docker run --rm -e "TEST_POSTGRES_ROTATION_ADMIN_DSN=$referenceDSN" `
      -e "TEST_POSTGRES_ROTATION_RUNTIME_DSN=$referenceRuntimeDSN" `
      -e "TEST_PROTECTED_SECRET_SOURCE_DIR=/relay-secret-source" `
      -e "TEST_PROTECTED_SECRET_READONLY_DIR=/run/relay-secrets" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/relay-secret-source:rw" -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src golang:1.25.1 /usr/local/go/bin/go test -json ./service -run "^TestProtectedPlatformRelayServicePrincipalRotationPostgresBarrier$" -count=1
    $rotationBarrierExitCode = $LASTEXITCODE
    if ($rotationBarrierExitCode -ne 0) {
		$rotationBarrierOutput | Write-Output
        throw "The protected service-principal rotation PostgreSQL barrier gate failed"
    }
    Assert-GoTestPassed $rotationBarrierOutput "TestProtectedPlatformRelayServicePrincipalRotationPostgresBarrier" "protected service-principal rotation PostgreSQL barrier"

    $rotationLifecycleOutput = & docker run --rm -e "TEST_POSTGRES_ROTATION_ADMIN_DSN=$referenceDSN" `
      -e "TEST_POSTGRES_ROTATION_RUNTIME_DSN=$referenceRuntimeDSN" `
      -e "TEST_PROTECTED_SECRET_SOURCE_DIR=/relay-secret-source" `
      -e "TEST_PROTECTED_SECRET_READONLY_DIR=/run/relay-secrets" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/relay-secret-source:rw" -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src golang:1.25.1 /usr/local/go/bin/go test -json . -run "^TestPlatformRelayPrincipalRotationLifecycleLockPostgresTimesOutWithoutWrites$" -count=1
    $rotationLifecycleExitCode = $LASTEXITCODE
    if ($rotationLifecycleExitCode -ne 0) {
		$rotationLifecycleOutput | Write-Output
        throw "The protected service-principal rotation lifecycle timeout gate failed"
    }
    Assert-GoTestPassed $rotationLifecycleOutput "TestPlatformRelayPrincipalRotationLifecycleLockPostgresTimesOutWithoutWrites" "protected service-principal rotation lifecycle timeout"

    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $referenceServerLogs = (& docker logs $referencePostgres 2>&1 | Out-String)
    $referenceServerLogsExitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($referenceServerLogsExitCode -ne 0) {
        throw "Unable to read the disposable PostgreSQL reference logs"
    }
    $rotationServerLogCanaries = @($referenceRuntimePassword, $referenceRuntimeVerifier)
    $rotationClientIDs = @(
        "lifecycle-platform-api",
        "lifecycle-platform-dispatcher",
        "lifecycle-platform-relay-sync",
        "lifecycle-platform-timeout"
    )
    foreach ($clientID in $rotationClientIDs) {
        $oldKey = (Get-SHA256Hex ("relay-schema-lifecycle" + [char]0 + "upstream-token-" + $clientID)).Substring(0, 48)
        $newKey = (Get-SHA256Hex ("relay-principal-rotation-pg-barrier-v1" + [char]0 + $clientID)).Substring(0, 48)
        $killKey = (Get-SHA256Hex ("relay-principal-rotation-pg-kill-v1" + [char]0 + $clientID)).Substring(0, 48)
        $rotationServerLogCanaries += @(
            $oldKey,
            "sk-$oldKey",
            (Get-SHA256Hex $oldKey),
            (Get-SHA256Hex "sk-$oldKey"),
            $newKey,
            "sk-$newKey",
            (Get-SHA256Hex $newKey),
            (Get-SHA256Hex "sk-$newKey"),
            $killKey,
            "sk-$killKey",
            (Get-SHA256Hex $killKey),
            (Get-SHA256Hex "sk-$killKey")
        )
    }
    foreach ($canary in $rotationServerLogCanaries) {
        if ($referenceServerLogs.Contains($canary)) {
            throw "The PostgreSQL rotation barrier gate leaked a credential canary into server logs"
        }
    }
    Write-Output "service-principal-rotation-postgres-barrier=PASS"
    Write-Output "service-principal-rotation-postgres-kill-rollback=PASS"
    Write-Output "service-principal-rotation-postgres-lifecycle-timeout=PASS"
    Write-Output "service-principal-rotation-postgres-stale-cache-rejection=PASS"
    Write-Output "service-principal-rotation-postgres-server-log-canaries=PASS"

    $legacyDSN = "postgresql://postgres:$legacyPassword@host.docker.internal:$legacyPort/new_api?sslmode=verify-full&sslrootcert=/tls/ca.crt"
    docker run -d --name $candidateContainer -e NODE_TYPE=master -e APP_ENV=development -e DEPLOYMENT_ENV=development -e "SQL_DSN=$legacyDSN" -e SESSION_SECRET=legacy-candidate-session-secret-32-bytes -e CRYPTO_SECRET=legacy-candidate-crypto-secret-32-bytes -v "${postgresTLSVolume}:/tls:ro" $CandidateImage | Out-Null
    $candidateReady = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $candidateState = docker inspect $candidateContainer --format "{{.State.Status}}"
        # The immutable candidate legitimately emits compatibility warnings on
        # stderr. PowerShell 5 turns those records into terminating errors when
        # ErrorActionPreference is Stop, even when `docker logs` exits zero.
        $savedErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $candidateLogs = (& docker logs $candidateContainer 2>&1 | Out-String)
        $candidateLogsExitCode = $LASTEXITCODE
        $ErrorActionPreference = $savedErrorActionPreference
        if ($candidateLogsExitCode -ne 0) {
            throw "Unable to read previous-candidate startup logs"
        }
        if ($candidateState -ne "running") {
            throw "The previous candidate exited before its startup readiness marker"
        }
        if ($candidateLogs -match "ready in [0-9]+ ms") {
            $candidateReady = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $candidateReady) {
        throw "The previous candidate did not reach its startup readiness marker"
    }
    $tableCount = docker exec $legacyPostgres psql -U postgres -d new_api -Atc "select count(*) from pg_tables where schemaname='public'"
    if ($LASTEXITCODE -ne 0 -or [int]$tableCount -ne 58) {
        throw "The immutable previous-candidate schema is incomplete or unexpected"
    }
    docker stop -t 5 $candidateContainer | Out-Null

    $fixtureSQL = @"
INSERT INTO users (id,username,password,display_name,role,status,email,quota,used_quota,request_count,created_at,auth_version)
VALUES (91001,'legacy-migration-owner-fixture','synthetic-password-hash-not-real','Legacy fixture',1,1,'legacy-fixture@example.invalid',17,3,2,1700000000,1);
INSERT INTO users
  (id,username,password,display_name,role,status,access_token,quota,used_quota,request_count,"group",aff_code,
   aff_count,aff_quota,aff_history,inviter_id,created_at,last_login_at,auth_version)
VALUES
  (92001,'lifecycle_root',
   '`$2b`$10`$L9OoVFbX8jndUh4JJz7dQ.E5sOxbeo2kEpvoMpqQmRKRG1VxyUgF.',
   'Root User',100,1,NULL,100000000,0,0,'default','',0,0,0,0,1700000001,0,1);
INSERT INTO setups (id,version,initialized_at) VALUES (92001,'v0.0.0',1700000001);
INSERT INTO channels (id,type,key,status,name,weight,created_time,base_url,models,priority,auto_ban,status_code_mapping)
VALUES (91001,1,'sk-legacy-channel-fixture-not-real',1,'legacy-migration-channel-fixture',1,1700000000,'https://fixture.invalid','legacy-model',0,1,'');
INSERT INTO tasks (id,created_at,updated_at,task_id,platform,user_id,channel_id,quota,action,status,progress,private_data)
VALUES (91001,1700000000,1700000000,'legacy-task-fixture-0001','legacy',91001,91001,7,'TEXT_TO_VIDEO','SUCCESS','100%',
        json_build_object('key','sk-legacy-task-fixture-not-real','pinned_key_index',0,
          'pinned_key_fingerprint','45027b56f8fc0ae3835b9e092baacee3fb286fa857c9e1d339efc78194ab6cdf'));
INSERT INTO platform_generation_provider_routes
  (id,route_key,model,mode,provider_name,account_id,channel_id,key_index,key_fingerprint,account_state_id,
   channel_class,upstream_model,staging_ready,production_ready,enabled,consecutive_failures,last_error_code,
   rpm_window_seconds,rpm_limit,rpm_window_count,active_count,active_limit,created_at,updated_at)
VALUES
  (91001,'legacy-route-fixture','legacy-model','text_to_video','legacy-provider','legacy-account',91001,0,
   'ca41acbc26fc869c3f4e79a15d59e4081e400099ddac028247f226a02d7aad1b',0,'official','legacy-upstream',
   false,false,false,0,'',60,1,0,0,1,now(),now());
INSERT INTO options (key,value)
VALUES ('ApiInfo', json_build_array(json_build_object('url','https://api.example.invalid','route','primary',
        'description','legacy fixture','color','blue'))::text);
"@
    $fixtureSQL | docker exec -i $legacyPostgres psql -v ON_ERROR_STOP=1 -U postgres -d new_api | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "The synthetic previous-candidate fixture could not be installed"
    }

    $legacyV1TestOutput = & docker run --rm -e "TEST_POSTGRES_LEGACY_DSN=$legacyDSN" -e "TEST_POSTGRES_LEGACY_REFERENCE_DSN=$referenceDSN" `
      -e "TEST_PROTECTED_SECRET_SOURCE_DIR=/relay-secret-source" `
      -e "TEST_PROTECTED_SECRET_READONLY_DIR=/run/relay-secrets" `
      -v "${pinnedV1SourceVolume}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/relay-secret-source:rw" -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src/backend/new-api-relay golang:1.25.1 /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresLegacyCandidateUpgrade$" -count=1
    $legacyV1TestExitCode = $LASTEXITCODE
    if ($legacyV1TestExitCode -ne 0) {
		$legacyV1TestOutput | Write-Output
        throw "The previous-candidate to immutable-v1 PostgreSQL 16 gate failed"
    }
    Assert-GoTestPassed $legacyV1TestOutput "TestRelaySchemaPostgresLegacyCandidateUpgrade" "raw legacy to immutable Relay schema v1"
    $v1NoRuntimeState = docker exec $legacyPostgres psql -U postgres -d new_api -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) || '|' || (SELECT count(*) FROM users WHERE role = 100) || '|' || (SELECT count(*) FROM setups) || '|' || (SELECT count(*) FROM users WHERE remark = 'platform-relay-service-v1' OR left(lower(username), 5) = 'rsvc_') || '|' || (SELECT count(*) FROM tokens WHERE left(name, 15) = 'platform-relay:') FROM relay_schema_state WHERE id = 1"
    if ($LASTEXITCODE -ne 0 -or $v1NoRuntimeState -ne "1|1|1|clean|1|1|1|0|0") {
        throw "The immutable-v1 bridge stage created a protected runtime/root/principal side effect"
    }
    Write-Output "legacy-to-v1-gate=PASS"
    Write-Output "v1-compatible-no-runtime-side-effects=PASS"

    $legacyV2TestOutput = & docker run --rm -e "TEST_POSTGRES_V1_UPGRADE_DSN=$legacyDSN" `
      -e "TEST_PROTECTED_SECRET_SOURCE_DIR=/relay-secret-source" `
      -e "TEST_PROTECTED_SECRET_READONLY_DIR=/run/relay-secrets" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/relay-secret-source:rw" -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src golang:1.25.1 /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresV1ToV2NoCatalogDelta$" -count=1
    $legacyV2TestExitCode = $LASTEXITCODE
    if ($legacyV2TestExitCode -ne 0) {
		$legacyV2TestOutput | Write-Output
        throw "The immutable-v1 to current-v2 no-catalog-delta gate failed"
    }
    Assert-GoTestPassed $legacyV2TestOutput "TestRelaySchemaPostgresV1ToV2NoCatalogDelta" "immutable Relay schema v1 to current v2"
    Write-Output "v1-to-v2-no-catalog-delta-gate=PASS"

    $legacyMigrationDSN = "postgresql://relay_schema_migrator:relay-migration-password-0123456789@host.docker.internal:$legacyPort/new_api?sslmode=verify-full&sslrootcert=/tls/ca.crt&search_path=public&options=-c%20role%3Drelay_schema_owner"
    $legacyRuntimeDSN = "postgresql://relay_runtime:$referenceRuntimePassword@host.docker.internal:$legacyPort/new_api?sslmode=verify-full&sslrootcert=/tls/ca.crt&search_path=public"
    $postV2LifecycleOutput = & docker run --rm `
      --add-host "obs.lifecycle-gate.myhuaweicloud.com:127.0.0.2" `
      --add-host "relay-lifecycle-artifacts.obs.lifecycle-gate.myhuaweicloud.com:127.0.0.2" `
      -e "TEST_POSTGRES_LIFECYCLE_ADMIN_DSN=$legacyDSN" `
      -e "TEST_POSTGRES_LIFECYCLE_MIGRATION_DSN=$legacyMigrationDSN" `
      -e "TEST_POSTGRES_LIFECYCLE_RUNTIME_DSN=$legacyRuntimeDSN" `
      -e "TEST_OBS_CERT=/tls/obs-server.crt" -e "TEST_OBS_KEY=/tls/obs-server.key" `
      -e "TEST_POSTGRES_LIFECYCLE_ROOT_PROVISION_STATE=unchanged" `
      -e "TEST_POSTGRES_LIFECYCLE_PRINCIPAL_PROVISION_STATE=created" `
      -e "TEST_POSTGRES_LIFECYCLE_REQUIRE_LEGACY_FIXTURES=true" `
      -e "TEST_PROTECTED_SECRET_SOURCE_DIR=/relay-secret-source" `
      -e "TEST_PROTECTED_SECRET_READONLY_DIR=/run/relay-secrets" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/relay-secret-source:rw" -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src golang:1.25.1 bash -ec `
      'cat /tls/ca.crt >> /etc/ssl/certs/ca-certificates.crt && exec /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresProtectedLifecycleProcess$" -count=1'
    $postV2LifecycleExitCode = $LASTEXITCODE
    if ($postV2LifecycleExitCode -ne 0) {
		$postV2LifecycleOutput | Write-Output
        throw "The same-database current-v2 proof/root/principal/API lifecycle gate failed"
    }
    Assert-GoTestPassed $postV2LifecycleOutput "TestRelaySchemaPostgresProtectedLifecycleProcess" "same-database current-v2 proof/root/principal/API lifecycle"
    $v2ProtectedState = docker exec $legacyPostgres psql -U postgres -d new_api -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) || '|' || (SELECT count(*) FROM users WHERE role = 100) || '|' || (SELECT count(*) FROM setups) || '|' || (SELECT count(*) FROM users WHERE remark = 'platform-relay-service-v1') || '|' || (SELECT count(*) FROM tokens WHERE left(name, 15) = 'platform-relay:') FROM relay_schema_state WHERE id = 1"
    if ($LASTEXITCODE -ne 0 -or $v2ProtectedState -ne "1|2|2|clean|1,2|1|1|4|4") {
        throw "The same-database current-v2 protected lifecycle terminal state is not exact"
    }
    Write-Output "post-v2-proof-root-principal-api-current-gate=PASS"
    Write-Output "legacy-schema-upgrade-gate=PASS"
}
finally {
    Remove-GateContainer $candidateContainer
    Remove-GateContainer $legacyPostgres
    Remove-GateContainer $referencePostgres
    docker volume rm -f $protectedSecretVolume *> $null
    docker volume rm -f $pinnedV1SourceVolume *> $null
    docker volume rm -f $postgresTLSVolume *> $null
}
