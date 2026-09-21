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
$pinnedV1ApplicationReferenceFixtureSHA256 = "905ec8ac2cf0915820029292e015ca54dbe833f8b675e78d7404759080aeacfc"
$pinnedWebDistFixtureSHA256 = "sha256:e0edb8c300ffb44695341c84541ac0d4c86297535f0e8f7982873ac10c119a67"
$pinnedV2GitRevision = "2535972505c63a059fdbe678e79577671481c358"
$pinnedV2SourceRevision = "b64cc406eb4efc9393bccbcd94cec6b58400c2d3"
$pinnedV2SourceSnapshot = "sha256:c4930050e31cfa2ce1ee7c17323b665a09770c29d136e7e23d5efa453c640e17"
$pinnedV2SourceFileCount = "2061"
$pinnedV3GitRevision = "f0042a96b048b501c9ac76470a234cffc0a54926"
$pinnedV3SourceRevision = "9d1ea67c9d8be3462c975c2f5b71b4532de8904c"
$pinnedV3SourceSnapshot = "sha256:c793805748019ff266e231cf41581b9cf2dc48f4a069afe926a9998e67214e7b"
$pinnedV3SourceFileCount = "2061"
$pinnedV4Image = "sha256:53a5d65cefbc4400e9e572665b227cf4a9628774aa77c814be4082f1abd7f84f"
$pinnedV4RepoDigest = "ai-video/new-api-relay@sha256:53a5d65cefbc4400e9e572665b227cf4a9628774aa77c814be4082f1abd7f84f"
$pinnedV4SourceRevision = "eb6c79983ab6488678c939b07ffe818982e2a33a"
$pinnedV4UpstreamRevision = "0ab02020603d22e5613bc4cf46bfab06f8567769"
$pinnedV4SourceSnapshot = "sha256:9ee8fe52159740f184d65b0388509655f919aea99291d72d9660aa4c731a04ef"
$pinnedV4SourceFileCount = "2095"
$pinnedV5Image = "sha256:d5998561f1142e5189ca15f6086b42da31127ecb5d58269ac492dc4aa3f61b9a"
$pinnedV5RepoDigest = "ai-video/new-api-relay@sha256:d5998561f1142e5189ca15f6086b42da31127ecb5d58269ac492dc4aa3f61b9a"
$pinnedV5Tag = "ai-video/new-api-relay:v5-canary-4e1c94bc"
$pinnedV5SourceRevision = "4e1c94bca595ddcc245a02befa69c546c46ff2b7"
$pinnedV5UpstreamRevision = "0ab02020603d22e5613bc4cf46bfab06f8567769"
$pinnedV5SourceSnapshot = "sha256:83c927086762a6aceffc0c36031afb573ca152d340fefd7f9f59e2893fbe18ce"
$pinnedV5SourceFileCount = "2099"
$pinnedV6Image = "sha256:576b836d26f19825532feaca1f9f7950f28affc78477d61d7b29dca474b8817f"
$pinnedV6RepoDigest = "ai-video/new-api-relay@sha256:576b836d26f19825532feaca1f9f7950f28affc78477d61d7b29dca474b8817f"
$pinnedV6Tag = "ai-video/new-api-relay:v6-canary-0b0b8bf5"
$pinnedV6SourceRevision = "0b0b8bf597aeb9e69e89a04f9b4d7b1d712e3391"
$pinnedV6UpstreamRevision = "0ab02020603d22e5613bc4cf46bfab06f8567769"
$pinnedV6SourceSnapshot = "sha256:1ad8305e212ac45bca32d2bbcf064c20bdb4337623b952452659748b17d2e836"
$pinnedV6SourceFileCount = "2106"
$pinnedV6Checksum = "sha256:8cffc546bb13c3f36f734dbb2e45a750da5b3e614de3beda82c6dd87dd84af00"
$pinnedV6Catalog = "sha256:180546808883afca58bb9fd246340339d87ac023aff91fe23186d9274dc15135"
$currentV7Checksum = "sha256:da3ddb86260818f894b13fc6b3ad34031b6089dddb83954172984d07e451c4c3"
$currentV7Catalog = "sha256:af1377416cb2788093391a03491d2bb380fc4b81db5f08d0d628f3f8a2abef01"
$qualifiedPostgresImage = "ai-video-platform-postgres16-pgaudit-canary:16.1"
$qualifiedPostgresImageID = "sha256:9c2d47297a4a7bfcdeaa8565bc66f40243e73bd3eab03f6cccbaadf652d76e10"
$legacyPostgres = "ai-video-relay-schema-legacy-gate-pg16"
$referencePostgres = "ai-video-relay-schema-reference-gate-pg16"
$v1ReferencePostgres = "ai-video-relay-schema-v1-reference-gate-pg16"
$candidateContainer = "ai-video-relay-schema-legacy-gate-candidate"
$protectedSecretVolume = "ai-video-relay-schema-gate-protected-secrets"
$pinnedV1SourceVolume = "ai-video-relay-schema-gate-v1-source"
$pinnedV3SourceVolume = "ai-video-relay-schema-gate-v3-source"
$currentV7BinaryVolume = "ai-video-relay-schema-gate-current-v7-binary"
$pinnedV3BinaryVolume = "ai-video-relay-schema-gate-pinned-v3-binary"
$postgresTLSVolume = "ai-video-relay-schema-gate-postgres-tls"
$legacyPassword = "relay-schema-legacy-gate-admin-password"
$referencePassword = "relay-schema-reference-gate-admin-password"
$migrationPassword = "relay-migration-password-0123456789"
$referenceRuntimePassword = "relay-runtime-password-0123456789-ab"
$edgePassword = "relay-edge-password-0123456789-abcdef"
$referenceDatabase = "relay_v7_fresh"
$v1ReferenceDatabase = "relay_v1_fresh"
$guardDatabase = "relay_v7_guards"
$repository = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$pinnedV1FixturePatch = Join-Path $repository "scripts\fixtures\relay-schema-v1-pg16-tls-test-fixture.patch"
$pinnedV1ApplicationReferenceFixture = Join-Path $repository "scripts\fixtures\relay-schema-v1-application-reference-test.go.fixture"
$pinnedWebDistFixture = Join-Path $repository "scripts\fixtures\relay-schema-gate-web-dist-index.html"
$currentCandidateBaselinePath = Join-Path $repositoryRoot "artifacts\relay-candidate-current.json"
$providerKeyringJSON = '{"schema_version":1,"active_key_id":"test-v1","keys":{"test-v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}}'
$providerKeyringJSONBase64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($providerKeyringJSON))

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

function Get-SingleJSONRecord([object[]]$Output, [string]$GateName) {
    $records = @($Output | ForEach-Object {
        try { "$_" | ConvertFrom-Json } catch { $null }
    } | Where-Object { $_ -ne $null })
    if ($records.Count -ne 1) {
        throw "$GateName did not emit exactly one JSON result"
    }
    return $records[0]
}

$candidateBaselineNode = Get-Command node -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $candidateBaselineNode -or [string]::IsNullOrWhiteSpace($candidateBaselineNode.Source)) {
    throw "Node.js is required to verify the current Relay candidate baseline"
}
$candidateBaselineCheckScript = Join-Path $repositoryRoot "scripts\relay-candidate-baseline.mjs"
if (-not (Test-Path -LiteralPath $candidateBaselineCheckScript -PathType Leaf)) {
    throw "The Relay candidate baseline verifier is unavailable"
}
$savedErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $candidateBaselineCheckOutput = @(& $candidateBaselineNode.Source $candidateBaselineCheckScript --check 2>&1)
    $candidateBaselineCheckExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $savedErrorActionPreference
}
if ($candidateBaselineCheckExitCode -ne 0) {
    throw "The current Relay candidate baseline does not match the current workspace"
}
if ($candidateBaselineCheckOutput.Count -ne 1 -or
    [string]$candidateBaselineCheckOutput[0] -ne "candidate baseline matches current Relay, Platform, and harness sources") {
    throw "The Relay candidate baseline verifier returned an invalid success result"
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
    $actualPinnedV2Revision = git -C $repositoryRoot rev-parse "$pinnedV2GitRevision^{commit}"
    if ($LASTEXITCODE -ne 0 -or $actualPinnedV2Revision -ne $pinnedV2GitRevision) {
        throw "The immutable Relay schema v2 source revision is unavailable"
    }
    $actualPinnedV3Revision = git -C $repositoryRoot rev-parse "$pinnedV3GitRevision^{commit}"
    if ($LASTEXITCODE -ne 0 -or $actualPinnedV3Revision -ne $pinnedV3GitRevision) {
        throw "The immutable Relay schema v3 source revision is unavailable"
    }
    $pinnedV4InspectionJSON = docker image inspect $pinnedV4Image
    if ($LASTEXITCODE -ne 0) {
        throw "The immutable Relay schema v4 one-shot image is unavailable"
    }
    $pinnedV4Inspection = $pinnedV4InspectionJSON | ConvertFrom-Json
    if ($pinnedV4Inspection.Count -ne 1) {
        throw "The immutable Relay schema v4 image reference is ambiguous"
    }
    $pinnedV4Metadata = $pinnedV4Inspection[0]
    $pinnedV4Labels = $pinnedV4Metadata.Config.Labels
    if ($pinnedV4Metadata.Id -ne $pinnedV4Image -or
        $pinnedV4Metadata.RepoDigests -notcontains $pinnedV4RepoDigest -or
        $pinnedV4Labels.'org.opencontainers.image.revision' -ne $pinnedV4SourceRevision -or
        $pinnedV4Labels.'ai.video.relay.upstream-revision' -ne $pinnedV4UpstreamRevision -or
        $pinnedV4Labels.'ai.video.relay.source-snapshot-sha256' -ne $pinnedV4SourceSnapshot -or
        $pinnedV4Labels.'ai.video.relay.source-file-count' -ne $pinnedV4SourceFileCount) {
        throw "The immutable Relay schema v4 image does not match its frozen provenance"
    }
    $pinnedV5InspectionJSON = docker image inspect $pinnedV5Image
    if ($LASTEXITCODE -ne 0) {
        throw "The immutable Relay schema v5 behavior fixture is unavailable"
    }
    $pinnedV5Inspection = $pinnedV5InspectionJSON | ConvertFrom-Json
    if ($pinnedV5Inspection.Count -ne 1) {
        throw "The immutable Relay schema v5 image reference is ambiguous"
    }
    $pinnedV5Metadata = $pinnedV5Inspection[0]
    $pinnedV5Labels = $pinnedV5Metadata.Config.Labels
    if ($pinnedV5Metadata.Id -ne $pinnedV5Image -or
        $pinnedV5Metadata.RepoDigests -notcontains $pinnedV5RepoDigest -or
        $pinnedV5Metadata.RepoTags -notcontains $pinnedV5Tag -or
        $pinnedV5Labels.'org.opencontainers.image.revision' -ne $pinnedV5SourceRevision -or
        $pinnedV5Labels.'ai.video.relay.upstream-revision' -ne $pinnedV5UpstreamRevision -or
        $pinnedV5Labels.'ai.video.relay.source-snapshot-sha256' -ne $pinnedV5SourceSnapshot -or
        $pinnedV5Labels.'ai.video.relay.source-file-count' -ne $pinnedV5SourceFileCount) {
        throw "The immutable Relay schema v5 behavior fixture does not match its pinned digest and labels"
    }
    $pinnedV6InspectionJSON = docker image inspect $pinnedV6Image
    if ($LASTEXITCODE -ne 0) {
        throw "The immutable Relay schema v6 release image is unavailable"
    }
    $pinnedV6Inspection = $pinnedV6InspectionJSON | ConvertFrom-Json
    if ($pinnedV6Inspection.Count -ne 1) {
        throw "The immutable Relay schema v6 image reference is ambiguous"
    }
    $pinnedV6Metadata = $pinnedV6Inspection[0]
    $pinnedV6Labels = $pinnedV6Metadata.Config.Labels
    if ($pinnedV6Metadata.Id -ne $pinnedV6Image -or
        $pinnedV6Metadata.RepoDigests -notcontains $pinnedV6RepoDigest -or
        $pinnedV6Metadata.RepoTags -notcontains $pinnedV6Tag -or
        $pinnedV6Labels.'org.opencontainers.image.revision' -ne $pinnedV6SourceRevision -or
        $pinnedV6Labels.'ai.video.relay.upstream-revision' -ne $pinnedV6UpstreamRevision -or
        $pinnedV6Labels.'ai.video.relay.source-snapshot-sha256' -ne $pinnedV6SourceSnapshot -or
        $pinnedV6Labels.'ai.video.relay.source-file-count' -ne $pinnedV6SourceFileCount) {
        throw "The immutable Relay schema v6 release image does not match its pinned digest and labels"
    }
    $actualFixturePatchSHA256 = (Get-FileHash -Algorithm SHA256 $pinnedV1FixturePatch).Hash.ToLowerInvariant()
    if ($actualFixturePatchSHA256 -ne $pinnedV1FixturePatchSHA256) {
        throw "The pinned v1 TLS test fixture patch does not match its frozen digest"
    }
    $actualV1ApplicationReferenceFixtureSHA256 = (Get-FileHash -Algorithm SHA256 $pinnedV1ApplicationReferenceFixture).Hash.ToLowerInvariant()
    if ($actualV1ApplicationReferenceFixtureSHA256 -ne $pinnedV1ApplicationReferenceFixtureSHA256) {
        throw "The pinned v1 application-reference fixture does not match its frozen digest"
    }
    $actualWebDistFixtureSHA256 = "sha256:" + (Get-FileHash -Algorithm SHA256 $pinnedWebDistFixture).Hash.ToLowerInvariant()
    if ($actualWebDistFixtureSHA256 -ne $pinnedWebDistFixtureSHA256) {
        throw "The pinned schema-gate web/dist fixture does not match its frozen digest"
    }
    Write-Output "qualified-postgres-image-id=$actualQualifiedPostgresID"
    Write-Output "relay-schema-v1-source-revision=$actualPinnedV1Revision"
    Write-Output "relay-schema-v1-test-fixture-patch-sha256=sha256:$actualFixturePatchSHA256"
    Write-Output "relay-schema-v1-application-reference-fixture-sha256=sha256:$actualV1ApplicationReferenceFixtureSHA256"
    Write-Output "relay-schema-web-dist-fixture-sha256=$actualWebDistFixtureSHA256"
    Write-Output "relay-schema-v2-source-revision=$actualPinnedV2Revision"
    Write-Output "relay-schema-v3-source-revision=$actualPinnedV3Revision"
    Write-Output "relay-schema-v4-image-id=$($pinnedV4Metadata.Id)"
    Write-Output "relay-schema-v4-image-repo-digest=$pinnedV4RepoDigest"
    Write-Output "relay-schema-v4-source-revision=$pinnedV4SourceRevision"
    Write-Output "relay-schema-v4-source-snapshot=$pinnedV4SourceSnapshot"
    Write-Output "relay-schema-v4-source-file-count=$pinnedV4SourceFileCount"
    Write-Output "relay-schema-v5-image-id=$($pinnedV5Metadata.Id)"
    Write-Output "relay-schema-v5-image-repo-digest=$pinnedV5RepoDigest"
    Write-Output "relay-schema-v5-source-revision=$pinnedV5SourceRevision"
    Write-Output "relay-schema-v5-source-snapshot=$pinnedV5SourceSnapshot"
    Write-Output "relay-schema-v5-source-file-count=$pinnedV5SourceFileCount"
    Write-Output "relay-schema-v6-image-id=$($pinnedV6Metadata.Id)"
    Write-Output "relay-schema-v6-image-repo-digest=$pinnedV6RepoDigest"
    Write-Output "relay-schema-v6-source-revision=$pinnedV6SourceRevision"
    Write-Output "relay-schema-v6-upstream-revision=$pinnedV6UpstreamRevision"
    Write-Output "relay-schema-v6-source-snapshot=$pinnedV6SourceSnapshot"
    Write-Output "relay-schema-v6-source-file-count=$pinnedV6SourceFileCount"
    if (-not (Test-Path -LiteralPath $currentCandidateBaselinePath -PathType Leaf)) {
        throw "The current Relay candidate baseline is unavailable"
    }
    $currentCandidateBaseline = Get-Content -LiteralPath $currentCandidateBaselinePath -Raw | ConvertFrom-Json
    $currentV7SourceRevision = [string]$currentCandidateBaseline.source.sha1
    $currentV7SourceSnapshot = [string]$currentCandidateBaseline.source.sha256
    $currentV7SourceFileCount = [string]$currentCandidateBaseline.source.file_count
    $currentV7UpstreamRevision = [string]$currentCandidateBaseline.upstream_git_revision
    if ($currentV7SourceRevision -notmatch '^[0-9a-f]{40}$' -or
        $currentV7SourceSnapshot -notmatch '^sha256:[0-9a-f]{64}$' -or
        $currentV7SourceFileCount -notmatch '^[1-9][0-9]*$' -or
        $currentV7UpstreamRevision -notmatch '^[0-9a-f]{40}$') {
        throw "The current Relay candidate baseline provenance is invalid"
    }
    if ($currentV7SourceRevision -eq $pinnedV6SourceRevision -or $currentV7SourceSnapshot -eq $pinnedV6SourceSnapshot) {
        throw "The current v7 candidate cannot reuse the immutable v6 provenance"
    }
    Write-Output "relay-schema-v7-source-revision=$currentV7SourceRevision"
    Write-Output "relay-schema-v7-source-snapshot=$currentV7SourceSnapshot"
    Write-Output "relay-schema-v7-source-file-count=$currentV7SourceFileCount"
    Remove-GateContainer $candidateContainer
    Remove-GateContainer $legacyPostgres
    Remove-GateContainer $referencePostgres
    Remove-GateContainer $v1ReferencePostgres
    docker volume rm -f $protectedSecretVolume *> $null
    docker volume rm -f $pinnedV1SourceVolume *> $null
    docker volume rm -f $pinnedV3SourceVolume *> $null
    docker volume rm -f $currentV7BinaryVolume *> $null
    docker volume rm -f $pinnedV3BinaryVolume *> $null
    docker volume rm -f $postgresTLSVolume *> $null
    docker volume create $protectedSecretVolume | Out-Null
    docker volume create $pinnedV1SourceVolume | Out-Null
    docker volume create $pinnedV3SourceVolume | Out-Null
    docker volume create $currentV7BinaryVolume | Out-Null
    docker volume create $pinnedV3BinaryVolume | Out-Null
    docker volume create $postgresTLSVolume | Out-Null

    docker run --rm -v "${repositoryRoot}:/workspace:ro" -v "${pinnedV1SourceVolume}:/v1" `
      -w /workspace golang:1.25.1 bash -ec `
      "git -c safe.directory=/workspace archive $pinnedV1Revision | tar -x -C /v1 && cd /v1 && git apply --check /workspace/backend/new-api-relay/scripts/fixtures/relay-schema-v1-pg16-tls-test-fixture.patch && git apply /workspace/backend/new-api-relay/scripts/fixtures/relay-schema-v1-pg16-tls-test-fixture.patch && cp /workspace/backend/new-api-relay/scripts/fixtures/relay-schema-v1-application-reference-test.go.fixture /v1/backend/new-api-relay/model/schema_v1_application_reference_fixture_test.go && mkdir -p /v1/backend/new-api-relay/web/dist && cp /workspace/backend/new-api-relay/scripts/fixtures/relay-schema-gate-web-dist-index.html /v1/backend/new-api-relay/web/dist/index.html"
    if ($LASTEXITCODE -ne 0) {
        throw "The immutable Relay schema v1 test source could not be materialized"
    }

    docker run --rm -v "${repositoryRoot}:/workspace:ro" -v "${pinnedV3SourceVolume}:/v3" `
      -w /workspace golang:1.25.1 bash -ec `
      "git -c safe.directory=/workspace archive $pinnedV3GitRevision | tar -x -C /v3 && mkdir -p /v3/backend/new-api-relay/web/dist && cp /workspace/backend/new-api-relay/scripts/fixtures/relay-schema-gate-web-dist-index.html /v3/backend/new-api-relay/web/dist/index.html"
    if ($LASTEXITCODE -ne 0) {
        throw "The immutable Relay schema v3 source could not be materialized"
    }

    $currentV7Version = (Get-Content -LiteralPath (Join-Path $repository "VERSION") -Raw).Trim()
    if ($currentV7Version -notmatch '^v[0-9]+\.[0-9]+\.[0-9]+(?:[+.-][0-9A-Za-z.+-]+)?$') {
        throw "The current Relay release version is invalid"
    }
    $currentV7LinkerFlags = "-s -w -X github.com/QuantumNous/new-api/common.Version=$currentV7Version -X github.com/QuantumNous/new-api/service.platformRelayCompiledUpstreamRevision=$currentV7UpstreamRevision -X github.com/QuantumNous/new-api/service.platformRelayCompiledSourceRevision=$currentV7SourceRevision -X github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotSHA256=$currentV7SourceSnapshot -X github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotFileCount=$currentV7SourceFileCount -X github.com/QuantumNous/new-api/service.platformRelayCompiledRouteAcceptanceKeysSHA256=sha256:1111111111111111111111111111111111111111111111111111111111111111"
    docker run --rm -e "RELAY_GATE_LDFLAGS=$currentV7LinkerFlags" `
      -v "${repository}:/src:ro" -v "${currentV7BinaryVolume}:/release" `
      -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -w /src golang:1.25.1 bash /src/scripts/build-relay-schema-gate-binary.sh /release/new-api
    if ($LASTEXITCODE -ne 0) {
        throw "The current Relay schema v7 one-shot could not be built"
    }

    $pinnedV3LinkerFlags = "-s -w -X github.com/QuantumNous/new-api/common.Version=$currentV7Version -X github.com/QuantumNous/new-api/service.platformRelayCompiledUpstreamRevision=$pinnedCandidateUpstreamRevision -X github.com/QuantumNous/new-api/service.platformRelayCompiledSourceRevision=$pinnedV3SourceRevision -X github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotSHA256=$pinnedV3SourceSnapshot -X github.com/QuantumNous/new-api/service.platformRelayCompiledSnapshotFileCount=$pinnedV3SourceFileCount -X github.com/QuantumNous/new-api/service.platformRelayCompiledRouteAcceptanceKeysSHA256=sha256:1111111111111111111111111111111111111111111111111111111111111111"
    docker run --rm -e "RELAY_GATE_LDFLAGS=$pinnedV3LinkerFlags" `
      -v "${pinnedV3SourceVolume}:/snapshot:ro" -v "${pinnedV3BinaryVolume}:/release" `
      -v "${repository}\scripts:/gate-scripts:ro" `
      -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -w /snapshot/backend/new-api-relay golang:1.25.1 bash /gate-scripts/build-relay-schema-gate-binary.sh /release/new-api
    if ($LASTEXITCODE -ne 0) {
        throw "The immutable historical schema-v3 Relay one-shot could not be built"
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
    docker run -d --name $v1ReferencePostgres -e "POSTGRES_PASSWORD=$referencePassword" `
      -v "${postgresTLSVolume}:/tls:ro" -p "127.0.0.1::5432" $qualifiedPostgresImage $postgresArguments | Out-Null
    Wait-Postgres $legacyPostgres
    Wait-Postgres $referencePostgres
    Wait-Postgres $v1ReferencePostgres
    docker exec $legacyPostgres createdb -U postgres new_api
    docker exec $referencePostgres createdb -U postgres $referenceDatabase
    docker exec $referencePostgres createdb -U postgres $guardDatabase
    docker exec $v1ReferencePostgres createdb -U postgres $v1ReferenceDatabase
    docker exec $legacyPostgres psql -v ON_ERROR_STOP=1 -U postgres -d new_api -c "CREATE EXTENSION pgaudit WITH SCHEMA pg_catalog" | Out-Null
    docker exec $referencePostgres psql -v ON_ERROR_STOP=1 -U postgres -d $referenceDatabase -c "CREATE EXTENSION pgaudit WITH SCHEMA pg_catalog" | Out-Null
    docker exec $v1ReferencePostgres psql -v ON_ERROR_STOP=1 -U postgres -d $v1ReferenceDatabase -c "CREATE EXTENSION pgaudit WITH SCHEMA pg_catalog" | Out-Null

    $legacyPort = Get-PostgresPort $legacyPostgres
    $referencePort = Get-PostgresPort $referencePostgres
    $v1ReferencePort = Get-PostgresPort $v1ReferencePostgres
    $referenceDSN = "postgresql://postgres:$referencePassword@host.docker.internal:$referencePort/${referenceDatabase}?sslmode=verify-full&sslrootcert=/tls/ca.crt"
    $v1ReferenceDSN = "postgresql://postgres:$referencePassword@host.docker.internal:$v1ReferencePort/${v1ReferenceDatabase}?sslmode=verify-full&sslrootcert=/tls/ca.crt"
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
    Assert-GoTestPassed $referenceTestOutput "TestRelaySchemaPostgresFreshCatalogRoleAndLock" "fresh Relay schema v7 reference"
    $freshV6State = docker exec $referencePostgres psql -U postgres -d $referenceDatabase -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) FROM relay_schema_state WHERE id = 1"
    if ($LASTEXITCODE -ne 0 -or $freshV6State -ne "7|7|7|clean|7") {
        throw "The independently migrated fresh-v7 ledger is not exact"
    }
    Write-Output "fresh-v7-row7-only-gate=PASS"

    $v1ReferenceTestOutput = & docker run --rm `
      -e "TEST_POSTGRES_V1_REFERENCE_DSN=$v1ReferenceDSN" `
      -v "${pinnedV1SourceVolume}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -w /src/backend/new-api-relay golang:1.25.1 bash -ec `
      'exec /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresFreshV1ApplicationReference$" -count=1'
    $v1ReferenceTestExitCode = $LASTEXITCODE
    if ($v1ReferenceTestExitCode -ne 0) {
		$v1ReferenceTestOutput | Write-Output
        throw "The independently migrated immutable-v1 PostgreSQL 16 reference failed"
    }
    Assert-GoTestPassed $v1ReferenceTestOutput "TestRelaySchemaPostgresFreshV1ApplicationReference" "fresh immutable Relay schema v1 application reference"
    $freshV1State = docker exec $v1ReferencePostgres psql -U postgres -d $v1ReferenceDatabase -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) FROM relay_schema_state WHERE id = 1"
    if ($LASTEXITCODE -ne 0 -or $freshV1State -ne "1|1|1|clean|1") {
        throw "The independently migrated fresh-v1 reference ledger is not exact"
    }
    Write-Output "fresh-v1-row1-only-reference-gate=PASS"

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
        "lifecycle-platform-relay-catalog-sync",
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
-- This row is migration evidence, not a live provider. Keep it manually
-- disabled so the real lifecycle channel-test worker cannot legitimately
-- rewrite its observational test_time/response_time fields while the gate
-- continues to require the complete legacy row to remain byte-for-byte stable.
INSERT INTO channels (id,type,key,status,name,weight,created_time,base_url,models,priority,auto_ban,status_code_mapping)
VALUES (91001,1,'sk-legacy-channel-fixture-not-real',2,'legacy-migration-channel-fixture',1,1700000000,'https://fixture.invalid','legacy-model',0,1,'');
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

    $legacyV1TestOutput = & docker run --rm -e "TEST_POSTGRES_LEGACY_DSN=$legacyDSN" -e "TEST_POSTGRES_LEGACY_REFERENCE_DSN=$v1ReferenceDSN" `
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

    $historicalV1ToV2TestOutput = & docker run --rm -e "TEST_POSTGRES_V1_UPGRADE_DSN=$legacyDSN" `
      -e "TEST_PROTECTED_SECRET_SOURCE_DIR=/relay-secret-source" `
      -e "TEST_PROTECTED_SECRET_READONLY_DIR=/run/relay-secrets" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/relay-secret-source:rw" -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src golang:1.25.1 /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresV1ToV2NoCatalogDelta$" -count=1
    $historicalV1ToV2TestExitCode = $LASTEXITCODE
    if ($historicalV1ToV2TestExitCode -ne 0) {
		$historicalV1ToV2TestOutput | Write-Output
        throw "The immutable-v1 to frozen-v2 historical no-catalog-delta gate failed"
    }
    Assert-GoTestPassed $historicalV1ToV2TestOutput "TestRelaySchemaPostgresV1ToV2NoCatalogDelta" "immutable Relay schema v1 to frozen historical v2"
    Write-Output "historical-frozen-v1-to-v2-no-catalog-delta-gate=PASS"
    Write-Output "v1-to-v2-no-catalog-delta-gate=PASS"

    $legacyRoleAdminDSN = "postgresql://postgres:$legacyPassword@host.docker.internal:$legacyPort/new_api?sslmode=verify-full&sslrootcert=/run/relay-secrets/current-v6-ca.crt&search_path=public"
    $legacyMigrationDSN = "postgresql://relay_schema_migrator:$migrationPassword@host.docker.internal:$legacyPort/new_api?sslmode=verify-full&sslrootcert=/run/relay-secrets/current-v6-ca.crt&search_path=public&options=-c%20role%3Drelay_schema_owner"
    $legacyDiagnosticDSN = "postgresql://relay_schema_migrator:$migrationPassword@host.docker.internal:$legacyPort/new_api?sslmode=verify-full&sslrootcert=/tls/ca.crt&search_path=public&options=-c%20role%3Drelay_schema_owner"
    $legacyRuntimeDSN = "postgresql://relay_runtime:$referenceRuntimePassword@host.docker.internal:$legacyPort/new_api?sslmode=verify-full&sslrootcert=/run/relay-secrets/current-v6-ca.crt&search_path=public"
    # Frozen v3's explicitly development-only role rehearsal requires its
    # historical TLS-attestation flag to be false. Keep the wire encrypted with
    # sslmode=require, but do not pass a CA/rootcert that frozen v3 rejects in
    # that mode. Every current-v7/v4/v5/v6 stage continues to use verify-full.
    $pinnedV3MigrationDSN = "postgresql://relay_schema_migrator:$migrationPassword@host.docker.internal:$legacyPort/new_api?sslmode=require&search_path=public&options=-c%20role%3Drelay_schema_owner"
    # Frozen v4 retained the same local-rehearsal TLS predicate as v3. Give it
    # its own protected historical DSN so neither exception can drift into the
    # current-v7 or immutable-v5/v6 verify-full stages.
    $pinnedV4MigrationDSN = "postgresql://relay_schema_migrator:$migrationPassword@host.docker.internal:$legacyPort/new_api?sslmode=require&search_path=public&options=-c%20role%3Drelay_schema_owner"
    docker run --rm `
      -e "ROLE_ADMIN_DSN=$legacyRoleAdminDSN" -e "MIGRATION_DSN=$legacyMigrationDSN" `
      -e "PINNED_V3_MIGRATION_DSN=$pinnedV3MigrationDSN" `
      -e "PINNED_V4_MIGRATION_DSN=$pinnedV4MigrationDSN" `
      -e "MIGRATION_PASSWORD=$migrationPassword" -e "RUNTIME_PASSWORD=$referenceRuntimePassword" `
      -e "EDGE_PASSWORD=$edgePassword" -e "PROVIDER_KEYRING_BASE64=$providerKeyringJSONBase64" `
      -v "${protectedSecretVolume}:/secrets" -v "${postgresTLSVolume}:/tls:ro" `
      $qualifiedPostgresImage bash -ec `
      'umask 077; printf "%s" "$ROLE_ADMIN_DSN" > /secrets/current-v6-role-admin-dsn; printf "%s" "$MIGRATION_DSN" > /secrets/current-v6-migration-dsn; printf "%s" "$PINNED_V3_MIGRATION_DSN" > /secrets/pinned-v3-migration-dsn; printf "%s" "$PINNED_V4_MIGRATION_DSN" > /secrets/pinned-v4-migration-dsn; printf "%s" "$MIGRATION_PASSWORD" > /secrets/current-v6-migration-password; printf "%s" "$RUNTIME_PASSWORD" > /secrets/current-v6-runtime-password; printf "%s" "$EDGE_PASSWORD" > /secrets/current-v6-edge-password; printf "%s" "$PROVIDER_KEYRING_BASE64" | base64 -d > /secrets/current-v6-provider-keyring.json; cp /tls/ca.crt /secrets/current-v6-ca.crt; chown 10001:10001 /secrets/current-v6-* /secrets/pinned-v3-migration-dsn /secrets/pinned-v4-migration-dsn; chmod 0400 /secrets/current-v6-* /secrets/pinned-v3-migration-dsn /secrets/pinned-v4-migration-dsn'
    if ($LASTEXITCODE -ne 0) {
        throw "The current-release protected fixture could not be created"
    }

    docker run --rm --user 10001:10001 `
      -v "${currentV7BinaryVolume}:/release-current:ro" `
      -v "${pinnedV3BinaryVolume}:/release-v3:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      golang:1.25.1 bash -ec `
      'set -eu; test -x /release-current/new-api; test -x /release-v3/new-api; for secret in /run/relay-secrets/current-v6-role-admin-dsn /run/relay-secrets/current-v6-migration-dsn /run/relay-secrets/pinned-v3-migration-dsn /run/relay-secrets/pinned-v4-migration-dsn /run/relay-secrets/current-v6-migration-password /run/relay-secrets/current-v6-runtime-password /run/relay-secrets/current-v6-edge-password /run/relay-secrets/current-v6-provider-keyring.json /run/relay-secrets/current-v6-ca.crt; do test -r "$secret"; test ! -w "$secret"; stat -c %u:%g:%a "$secret" | grep -Fqx 10001:10001:400; done'
    if ($LASTEXITCODE -ne 0) {
        throw "The schema-v7 one-shot owner/mode preflight failed"
    }
    Write-Output "current-v7-owner-mode-preflight=PASS"

    $currentV7RolePreOutput = & docker run --rm --user 10001:10001 `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime -e RELAY_DATABASE_CA_FILE=/run/relay-secrets/current-v6-ca.crt `
      -e SQL_DSN_FILE=/run/relay-secrets/current-v6-role-admin-dsn `
      -e RELAY_MIGRATION_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-migration-password `
      -e RELAY_RUNTIME_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-runtime-password `
      -e RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-edge-password `
      -e "RELAY_COMPAT_SOURCE_REVISION=$currentV7SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$currentV7SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$currentV7SourceFileCount" `
      -v "${currentV7BinaryVolume}:/release:ro" -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      golang:1.25.1 /release/new-api relay-provision-database-roles
    $currentV7RolePreExitCode = $LASTEXITCODE
    if ($currentV7RolePreExitCode -ne 0) {
        $currentV7RolePreOutput | Write-Output
        throw "The current schema-v7 role-pre one-shot failed"
    }
    $currentV7RolePre = Get-SingleJSONRecord $currentV7RolePreOutput "current schema-v7 role-pre one-shot"
    if ($currentV7RolePre.kind -ne "relay_database_role_provision" -or $currentV7RolePre.state -ne "provisioned") {
        throw "The current schema-v7 role-pre receipt is invalid"
    }

    $pinnedV3MigrationOutput = & docker run --rm --user 10001:10001 `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=false `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime `
      -e SQL_DSN_FILE=/run/relay-secrets/pinned-v3-migration-dsn `
      -e RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE=/run/relay-secrets/current-v6-provider-keyring.json `
      -e "RELAY_COMPAT_SOURCE_REVISION=$pinnedV3SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$pinnedV3SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$pinnedV3SourceFileCount" `
      -v "${pinnedV3BinaryVolume}:/release:ro" -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      golang:1.25.1 /release/new-api relay-migrate
    $pinnedV3MigrationExitCode = $LASTEXITCODE
    if ($pinnedV3MigrationExitCode -ne 0) {
        $pinnedV3MigrationOutput | Write-Output
        throw "The exact frozen-v2 to immutable-v3 one-shot migration failed"
    }
    $pinnedV3Migration = Get-SingleJSONRecord $pinnedV3MigrationOutput "immutable schema-v3 migration one-shot"
    if ($pinnedV3Migration.kind -ne "relay_schema_migration" -or
        $pinnedV3Migration.state -ne "migrated" -or
        [int64]$pinnedV3Migration.from_version -ne 2 -or [int64]$pinnedV3Migration.to_version -ne 3 -or
        [int64]$pinnedV3Migration.status.baseline_version -ne 1 -or
        [int64]$pinnedV3Migration.status.current_version -ne 3 -or
        [int64]$pinnedV3Migration.status.target_version -ne 3 -or
        [int64]$pinnedV3Migration.status.min_version -ne 1 -or
        [int64]$pinnedV3Migration.status.max_version -ne 3 -or
        -not [bool]$pinnedV3Migration.status.current) {
        throw "The exact frozen-v2 to immutable-v3 migration result is invalid"
    }
    $exactV1ToV3State = docker exec $legacyPostgres psql -U postgres -d new_api -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) FROM relay_schema_state WHERE id = 1"
    if ($LASTEXITCODE -ne 0 -or $exactV1ToV3State -ne "1|3|3|clean|1,2,3") {
        throw "The exact-v1 through immutable-v3 ledger is not exact"
    }
    Write-Output "pinned-v3-local-tls-require-acl-rehearsal=PASS"
    Write-Output "v2-to-v3-frozen-one-shot-gate=PASS"
    Write-Output "exact-v1-to-v3-ledger-gate=PASS"

    # Every protected schema release begins from the zero-ACL role-pre stub.
    # The v3 migration committed its versioned DML grants, so reset them before
    # asking the immutable v4 binary to cross the next schema boundary. This
    # current role provisioner performs no schema migration and the pinned v4
    # binary remains the only process allowed to execute v3->v4.
    $preV4RoleOutput = & docker run --rm --user 10001:10001 `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime -e RELAY_DATABASE_CA_FILE=/run/relay-secrets/current-v6-ca.crt `
      -e SQL_DSN_FILE=/run/relay-secrets/current-v6-role-admin-dsn `
      -e RELAY_MIGRATION_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-migration-password `
      -e RELAY_RUNTIME_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-runtime-password `
      -e RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-edge-password `
      -e "RELAY_COMPAT_SOURCE_REVISION=$currentV7SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$currentV7SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$currentV7SourceFileCount" `
      -v "${currentV7BinaryVolume}:/release:ro" -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      golang:1.25.1 /release/new-api relay-provision-database-roles
    $preV4RoleExitCode = $LASTEXITCODE
    if ($preV4RoleExitCode -ne 0) {
        $preV4RoleOutput | Write-Output
        throw "The exact-v3 to pinned-v4 role-pre reset failed"
    }
    $preV4Role = Get-SingleJSONRecord $preV4RoleOutput "exact-v3 to pinned-v4 role-pre reset"
    if ($preV4Role.kind -ne "relay_database_role_provision" -or $preV4Role.state -ne "provisioned") {
        throw "The exact-v3 to pinned-v4 role-pre receipt is invalid"
    }
    Write-Output "pre-v4-zero-acl-role-stub-gate=PASS"

    $pinnedV4MigrationOutput = & docker run --rm `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=false `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime `
      -e SQL_DSN_FILE=/run/relay-secrets/pinned-v4-migration-dsn `
      -e RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE=/run/relay-secrets/current-v6-provider-keyring.json `
      -e "RELAY_COMPAT_SOURCE_REVISION=$pinnedV4SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$pinnedV4SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$pinnedV4SourceFileCount" `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      $pinnedV4Image relay-migrate
    $pinnedV4MigrationExitCode = $LASTEXITCODE
    if ($pinnedV4MigrationExitCode -ne 0) {
        $pinnedV4MigrationOutput | Write-Output
        throw "The exact immutable-v3 to pinned-v4 one-shot migration failed"
    }
    $pinnedV4Migration = Get-SingleJSONRecord $pinnedV4MigrationOutput "pinned schema-v4 migration one-shot"
    if ($pinnedV4Migration.kind -ne "relay_schema_migration" -or
        $pinnedV4Migration.state -ne "migrated" -or
        [int64]$pinnedV4Migration.from_version -ne 3 -or [int64]$pinnedV4Migration.to_version -ne 4 -or
        [int64]$pinnedV4Migration.status.baseline_version -ne 1 -or
        [int64]$pinnedV4Migration.status.current_version -ne 4 -or
        [int64]$pinnedV4Migration.status.target_version -ne 4 -or
        [int64]$pinnedV4Migration.status.min_version -ne 1 -or
        [int64]$pinnedV4Migration.status.max_version -ne 4 -or
        $pinnedV4Migration.status.current_checksum -ne "sha256:4a91686133814c07401a11eea3fe373154219923c4d63666ca99e3049d96079d" -or
        $pinnedV4Migration.status.catalog_sha256 -ne "sha256:b8260ee751d0b9bb6dcd0c2d2d4105bef475296f25a4babb13fca3e117888126" -or
        -not [bool]$pinnedV4Migration.status.current) {
        throw "The exact immutable-v3 to pinned-v4 migration result is invalid"
    }
    $exactV1ToV4State = docker exec $legacyPostgres psql -U postgres -d new_api -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) FROM relay_schema_state WHERE id = 1"
    if ($LASTEXITCODE -ne 0 -or $exactV1ToV4State -ne "1|4|4|clean|1,2,3,4") {
        throw "The exact-v1 through pinned-v4 ledger is not exact"
    }
    Write-Output "pinned-v4-local-tls-require-acl-rehearsal=PASS"
    Write-Output "v3-to-pinned-v4-one-shot-gate=PASS"
    Write-Output "exact-v1-to-v4-ledger-gate=PASS"

    # Current source may inspect but must not reconstruct historical v4. Run a
    # read-only/rollback verifier directly against the gate-owned exact-v4
    # database produced above by the immutable image. It proves the complete
    # state and ledger receipts, frozen catalog, least-privilege ACLs, and both
    # route-binding and ledger mutation guards before v4->v5 can begin.
    $pinnedV4ReleaseFixtureOutput = & docker run --rm `
      -e "TEST_RELAY_SCHEMA_V4_RELEASE_DSN=$legacyMigrationDSN" `
      -e "TEST_RELAY_SCHEMA_V4_SOURCE_REVISION=$pinnedV4SourceRevision" `
      -e "TEST_RELAY_SCHEMA_V4_SOURCE_SNAPSHOT_SHA256=$pinnedV4SourceSnapshot" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src golang:1.25.1 /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresPinnedV4ReleaseFixture$" -count=1
    $pinnedV4ReleaseFixtureExitCode = $LASTEXITCODE
    if ($pinnedV4ReleaseFixtureExitCode -ne 0) {
        $pinnedV4ReleaseFixtureOutput | Write-Output
        throw "The immutable pinned-v4 state/ledger/catalog/ACL/guard fixture gate failed"
    }
    Assert-GoTestPassed $pinnedV4ReleaseFixtureOutput "TestRelaySchemaPostgresPinnedV4ReleaseFixture" "immutable pinned-v4 state/ledger/catalog/ACL/guards"
    Write-Output "pinned-v4-state-ledger-catalog-acl-guards-gate=PASS"

    # Exercise the diagnostic boundary against the exact v4 catalog produced
    # by the immutable image above. A pg_dump/restore clone is not equivalent:
    # it can rewrite catalog-normalized definitions, ownership, and ACL state.
    # The Go test owns one outer transaction and always rolls it back; a test
    # process or connection failure also makes PostgreSQL roll it back, so the
    # immutable v4 fixture remains unchanged for the committed v5 migration.
    $guardDSN = "postgresql://postgres:$referencePassword@host.docker.internal:$referencePort/${guardDatabase}?sslmode=verify-full&sslrootcert=/tls/ca.crt"
    $v4ToV5DiagnosticOutput = & docker run --rm `
      -e "TEST_RELAY_SCHEMA_V5_POSTGRES_DSN=$legacyDiagnosticDSN" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -w /src golang:1.25.1 /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresV4ToV5DiagnosticTaxonomy$" -count=1
    $v4ToV5DiagnosticExitCode = $LASTEXITCODE
    if ($v4ToV5DiagnosticExitCode -ne 0) {
        $v4ToV5DiagnosticOutput | Write-Output
        throw "The exact v4-to-v5 diagnostic taxonomy PostgreSQL gate failed"
    }
    Assert-GoTestPassed $v4ToV5DiagnosticOutput "TestRelaySchemaPostgresV4ToV5DiagnosticTaxonomy" "exact v4-to-v5 diagnostic taxonomy"
    Write-Output "v4-to-v5-diagnostic-taxonomy-rollback-gate=PASS"

    $postDiagnosticV4State = docker exec $legacyPostgres psql -U postgres -d new_api -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || current_catalog_sha256 || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) FROM relay_schema_state WHERE id = 1"
    if ($LASTEXITCODE -ne 0 -or $postDiagnosticV4State -ne "1|4|4|clean|sha256:b8260ee751d0b9bb6dcd0c2d2d4105bef475296f25a4babb13fca3e117888126|1,2,3,4") {
        throw "The rollback-only v4-to-v5 diagnostic gate changed the immutable v4 state or ledger"
    }
    $postDiagnosticV4FixtureOutput = & docker run --rm `
      -e "TEST_RELAY_SCHEMA_V4_RELEASE_DSN=$legacyMigrationDSN" `
      -e "TEST_RELAY_SCHEMA_V4_SOURCE_REVISION=$pinnedV4SourceRevision" `
      -e "TEST_RELAY_SCHEMA_V4_SOURCE_SNAPSHOT_SHA256=$pinnedV4SourceSnapshot" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src golang:1.25.1 /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresPinnedV4ReleaseFixture$" -count=1
    $postDiagnosticV4FixtureExitCode = $LASTEXITCODE
    if ($postDiagnosticV4FixtureExitCode -ne 0) {
        $postDiagnosticV4FixtureOutput | Write-Output
        throw "The rollback-only v4-to-v5 diagnostic gate changed the immutable v4 catalog or ACL surface"
    }
    Assert-GoTestPassed $postDiagnosticV4FixtureOutput "TestRelaySchemaPostgresPinnedV4ReleaseFixture" "post-diagnostic immutable v4 state/ledger/catalog/ACL/guards"
    Write-Output "v4-to-v5-diagnostic-rollback-preserves-exact-v4-gate=PASS"

    # The immutable v4 migration also committed its versioned ACLs. Reset the
    # protected roles with the immutable v5 behavior fixture so v5 crosses its
    # own release boundary from the mandatory zero-ACL pre-stub.
    $preV5RoleOutput = & docker run --rm `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime -e RELAY_DATABASE_CA_FILE=/run/relay-secrets/current-v6-ca.crt `
      -e SQL_DSN_FILE=/run/relay-secrets/current-v6-role-admin-dsn `
      -e RELAY_MIGRATION_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-migration-password `
      -e RELAY_RUNTIME_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-runtime-password `
      -e RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-edge-password `
      -e "RELAY_COMPAT_SOURCE_REVISION=$pinnedV5SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$pinnedV5SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$pinnedV5SourceFileCount" `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      $pinnedV5Image relay-provision-database-roles
    $preV5RoleExitCode = $LASTEXITCODE
    if ($preV5RoleExitCode -ne 0) {
        $preV5RoleOutput | Write-Output
        throw "The pinned-v4 to immutable-v5 role-pre reset failed"
    }
    $preV5Role = Get-SingleJSONRecord $preV5RoleOutput "pinned-v4 to immutable-v5 role-pre reset"
    if ($preV5Role.kind -ne "relay_database_role_provision" -or $preV5Role.state -ne "provisioned") {
        throw "The pinned-v4 to immutable-v5 role-pre receipt is invalid"
    }
    Write-Output "pre-v5-zero-acl-role-stub-gate=PASS"

    $pinnedV5MigrationOutput = & docker run --rm `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime -e RELAY_DATABASE_CA_FILE=/run/relay-secrets/current-v6-ca.crt `
      -e SQL_DSN_FILE=/run/relay-secrets/current-v6-migration-dsn `
      -e RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE=/run/relay-secrets/current-v6-provider-keyring.json `
      -e "RELAY_COMPAT_SOURCE_REVISION=$pinnedV5SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$pinnedV5SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$pinnedV5SourceFileCount" `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      $pinnedV5Image relay-migrate
    $pinnedV5MigrationExitCode = $LASTEXITCODE
    if ($pinnedV5MigrationExitCode -ne 0) {
        $pinnedV5MigrationOutput | Write-Output
        throw "The exact pinned-v4 to immutable-v5 one-shot migration failed"
    }
    $pinnedV5Migration = Get-SingleJSONRecord $pinnedV5MigrationOutput "immutable schema-v5 migration one-shot"
    if ($pinnedV5Migration.kind -ne "relay_schema_migration" -or
        $pinnedV5Migration.state -ne "migrated" -or
        [int64]$pinnedV5Migration.from_version -ne 4 -or [int64]$pinnedV5Migration.to_version -ne 5 -or
        [int64]$pinnedV5Migration.status.baseline_version -ne 1 -or
        [int64]$pinnedV5Migration.status.current_version -ne 5 -or
        [int64]$pinnedV5Migration.status.target_version -ne 5 -or
        [int64]$pinnedV5Migration.status.min_version -ne 1 -or
        [int64]$pinnedV5Migration.status.max_version -ne 5 -or
        $pinnedV5Migration.status.current_checksum -ne "sha256:d8066d7081eb4a73239333bab10b78e825457dcf78bc3d3aaa195c52edc8b7f6" -or
        $pinnedV5Migration.status.catalog_sha256 -ne "sha256:2bc1bf2f68e513d12de36cd4f8c2ca6a569d102b93a6fbaea444facad3189fc1" -or
        -not [bool]$pinnedV5Migration.status.current) {
        throw "The exact pinned-v4 to immutable-v5 migration result is invalid"
    }
    $exactV1ToV5State = docker exec $legacyPostgres psql -U postgres -d new_api -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) FROM relay_schema_state WHERE id = 1"
    if ($LASTEXITCODE -ne 0 -or $exactV1ToV5State -ne "1|5|5|clean|1,2,3,4,5") {
        throw "The exact-v1 through immutable-v5 ledger is not exact"
    }
    Write-Output "v4-to-pinned-v5-one-shot-gate=PASS"
    Write-Output "exact-v1-to-v5-ledger-gate=PASS"

    # Current source may inspect and exercise the exact immutable-v5 database
    # only inside a rollback transaction. The immutable v6 image remains the
    # sole owner of the durable 5->6 release transition below.
    $v5ToV6LifecycleOutput = & docker run --rm `
      -e "TEST_RELAY_SCHEMA_V5_RELEASE_DSN=$legacyMigrationDSN" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src golang:1.25.1 /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresV5ToV6DurableChannelTestLifecycle$" -count=1
    $v5ToV6LifecycleExitCode = $LASTEXITCODE
    if ($v5ToV6LifecycleExitCode -ne 0) {
        $v5ToV6LifecycleOutput | Write-Output
        throw "The exact immutable-v5 to frozen-v6 lifecycle rollback gate failed"
    }
    Assert-GoTestPassed $v5ToV6LifecycleOutput "TestRelaySchemaPostgresV5ToV6DurableChannelTestLifecycle" "exact immutable-v5 to frozen-v6 lifecycle rollback"
    Write-Output "pinned-v5-to-v6-lifecycle-rollback-gate=PASS"

    # Reset the v5 grants with the immutable v6 release binary. This role-pre
    # does not migrate schema; it establishes the mandatory zero-ACL stub before
    # the same pinned v6 binary exclusively executes 5->6.
    $preV6RoleOutput = & docker run --rm --user 10001:10001 `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime -e RELAY_DATABASE_CA_FILE=/run/relay-secrets/current-v6-ca.crt `
      -e SQL_DSN_FILE=/run/relay-secrets/current-v6-role-admin-dsn `
      -e RELAY_MIGRATION_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-migration-password `
      -e RELAY_RUNTIME_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-runtime-password `
      -e RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-edge-password `
      -e "RELAY_COMPAT_SOURCE_REVISION=$pinnedV6SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$pinnedV6SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$pinnedV6SourceFileCount" `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      $pinnedV6Image relay-provision-database-roles
    $preV6RoleExitCode = $LASTEXITCODE
    if ($preV6RoleExitCode -ne 0) {
        $preV6RoleOutput | Write-Output
        throw "The immutable-v5 to pinned-v6 role-pre reset failed"
    }
    $preV6Role = Get-SingleJSONRecord $preV6RoleOutput "immutable-v5 to pinned-v6 role-pre reset"
    if ($preV6Role.kind -ne "relay_database_role_provision" -or $preV6Role.state -ne "provisioned") {
        throw "The immutable-v5 to pinned-v6 role-pre receipt is invalid"
    }
    Write-Output "pre-v6-zero-acl-role-stub-gate=PASS"

    $pinnedV6MigrationOutput = & docker run --rm --user 10001:10001 `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime -e RELAY_DATABASE_CA_FILE=/run/relay-secrets/current-v6-ca.crt `
      -e SQL_DSN_FILE=/run/relay-secrets/current-v6-migration-dsn `
      -e RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE=/run/relay-secrets/current-v6-provider-keyring.json `
      -e "RELAY_COMPAT_SOURCE_REVISION=$pinnedV6SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$pinnedV6SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$pinnedV6SourceFileCount" `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      $pinnedV6Image relay-migrate
    $pinnedV6MigrationExitCode = $LASTEXITCODE
    if ($pinnedV6MigrationExitCode -ne 0) {
        $pinnedV6MigrationOutput | Write-Output
        throw "The exact immutable-v5 to pinned-v6 one-shot migration failed"
    }
    $pinnedV6Migration = Get-SingleJSONRecord $pinnedV6MigrationOutput "pinned schema-v6 migration one-shot"
    if ($pinnedV6Migration.kind -ne "relay_schema_migration" -or
        $pinnedV6Migration.state -ne "migrated" -or
        [int64]$pinnedV6Migration.from_version -ne 5 -or [int64]$pinnedV6Migration.to_version -ne 6 -or
        [int64]$pinnedV6Migration.status.baseline_version -ne 1 -or
        [int64]$pinnedV6Migration.status.current_version -ne 6 -or
        [int64]$pinnedV6Migration.status.target_version -ne 6 -or
        [int64]$pinnedV6Migration.status.min_version -ne 1 -or
        [int64]$pinnedV6Migration.status.max_version -ne 6 -or
        $pinnedV6Migration.status.current_checksum -ne $pinnedV6Checksum -or
        $pinnedV6Migration.status.catalog_sha256 -ne $pinnedV6Catalog -or
        -not [bool]$pinnedV6Migration.status.current) {
        throw "The exact immutable-v5 to pinned-v6 migration result is invalid"
    }
    $exactV1ToV6State = docker exec $legacyPostgres psql -U postgres -d new_api -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) FROM relay_schema_state WHERE id = 1"
    if ($LASTEXITCODE -ne 0 -or $exactV1ToV6State -ne "1|6|6|clean|1,2,3,4,5,6") {
        throw "The exact-v1 through pinned-v6 ledger is not exact"
    }
    Write-Output "v5-to-pinned-v6-one-shot-gate=PASS"
    Write-Output "exact-v1-to-v6-ledger-gate=PASS"

    # The current v7 source verifies the exact image-produced v6 release
    # boundary inside an outer rollback transaction before any v7 ACL reset or
    # durable migration is allowed.
    $v6ToV7ArtifactOutput = & docker run --rm `
      -e "TEST_RELAY_SCHEMA_V6_RELEASE_DSN=$legacyMigrationDSN" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      -w /src golang:1.25.1 /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaPostgresV6ToV7ArtifactContentTypes$" -count=1
    $v6ToV7ArtifactExitCode = $LASTEXITCODE
    if ($v6ToV7ArtifactExitCode -ne 0) {
        $v6ToV7ArtifactOutput | Write-Output
        throw "The exact pinned-v6 to current-v7 artifact-content rollback gate failed"
    }
    Assert-GoTestPassed $v6ToV7ArtifactOutput "TestRelaySchemaPostgresV6ToV7ArtifactContentTypes" "exact pinned-v6 to current-v7 artifact-content rollback"
    Write-Output "pinned-v6-to-v7-artifact-content-rollback-gate=PASS"

    # The rollback verifier must leave v6 unchanged. Only after this explicit
    # zero-ACL reset may the current v7 binary own the 6->7 transition.
    $preV7RoleOutput = & docker run --rm --user 10001:10001 `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime -e RELAY_DATABASE_CA_FILE=/run/relay-secrets/current-v6-ca.crt `
      -e SQL_DSN_FILE=/run/relay-secrets/current-v6-role-admin-dsn `
      -e RELAY_MIGRATION_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-migration-password `
      -e RELAY_RUNTIME_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-runtime-password `
      -e RELAY_DOWNLOAD_EDGE_DATABASE_PASSWORD_FILE=/run/relay-secrets/current-v6-edge-password `
      -e "RELAY_COMPAT_SOURCE_REVISION=$currentV7SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$currentV7SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$currentV7SourceFileCount" `
      -v "${currentV7BinaryVolume}:/release:ro" -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      golang:1.25.1 /release/new-api relay-provision-database-roles
    $preV7RoleExitCode = $LASTEXITCODE
    if ($preV7RoleExitCode -ne 0) {
        $preV7RoleOutput | Write-Output
        throw "The pinned-v6 to current-v7 role-pre reset failed"
    }
    $preV7Role = Get-SingleJSONRecord $preV7RoleOutput "pinned-v6 to current-v7 role-pre reset"
    if ($preV7Role.kind -ne "relay_database_role_provision" -or $preV7Role.state -ne "provisioned") {
        throw "The pinned-v6 to current-v7 role-pre receipt is invalid"
    }
    Write-Output "pre-v7-zero-acl-role-stub-gate=PASS"

    $currentV7MigrationOutput = & docker run --rm --user 10001:10001 `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime -e RELAY_DATABASE_CA_FILE=/run/relay-secrets/current-v6-ca.crt `
      -e SQL_DSN_FILE=/run/relay-secrets/current-v6-migration-dsn `
      -e RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE=/run/relay-secrets/current-v6-provider-keyring.json `
      -e "RELAY_COMPAT_SOURCE_REVISION=$currentV7SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$currentV7SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$currentV7SourceFileCount" `
      -v "${currentV7BinaryVolume}:/release:ro" -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      golang:1.25.1 /release/new-api relay-migrate
    $currentV7MigrationExitCode = $LASTEXITCODE
    if ($currentV7MigrationExitCode -ne 0) {
        $currentV7MigrationOutput | Write-Output
        throw "The exact pinned-v6 to current-v7 one-shot migration failed"
    }
    $currentV7Migration = Get-SingleJSONRecord $currentV7MigrationOutput "current schema-v7 migration one-shot"
    if ($currentV7Migration.kind -ne "relay_schema_migration" -or
        $currentV7Migration.state -ne "migrated" -or
        [int64]$currentV7Migration.from_version -ne 6 -or [int64]$currentV7Migration.to_version -ne 7 -or
        [int64]$currentV7Migration.status.baseline_version -ne 1 -or
        [int64]$currentV7Migration.status.current_version -ne 7 -or
        [int64]$currentV7Migration.status.target_version -ne 7 -or
        [int64]$currentV7Migration.status.min_version -ne 1 -or
        [int64]$currentV7Migration.status.max_version -ne 7 -or
        $currentV7Migration.status.current_checksum -ne $currentV7Checksum -or
        $currentV7Migration.status.catalog_sha256 -ne $currentV7Catalog -or
        -not [bool]$currentV7Migration.status.current) {
        throw "The exact pinned-v6 to current-v7 migration result is invalid"
    }
    $exactV1ToV7State = docker exec $legacyPostgres psql -U postgres -d new_api -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) FROM relay_schema_state WHERE id = 1"
    if ($LASTEXITCODE -ne 0 -or $exactV1ToV7State -ne "1|7|7|clean|1,2,3,4,5,6,7") {
        throw "The exact-v1 through current-v7 ledger is not exact"
    }
    Write-Output "v6-to-v7-one-shot-gate=PASS"
    Write-Output "exact-v1-to-v7-ledger-gate=PASS"

    $postV7LifecycleOutput = & docker run --rm `
      --add-host "obs.lifecycle-gate.myhuaweicloud.com:127.0.0.2" `
      --add-host "relay-lifecycle-artifacts.obs.lifecycle-gate.myhuaweicloud.com:127.0.0.2" `
      -e "TEST_POSTGRES_LIFECYCLE_ADMIN_DSN=$legacyRoleAdminDSN" `
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
    $postV7LifecycleExitCode = $LASTEXITCODE
    if ($postV7LifecycleExitCode -ne 0) {
		$postV7LifecycleOutput | Write-Output
        throw "The same-database current-v7 proof/root/principal/API/download-edge lifecycle gate failed"
    }
    Assert-GoTestPassed $postV7LifecycleOutput "TestRelaySchemaPostgresProtectedLifecycleProcess" "same-database current-v7 proof/root/principal/API/download-edge lifecycle"
    $v7ProtectedState = docker exec $legacyPostgres psql -U postgres -d new_api -Atc "SELECT baseline_version || '|' || current_version || '|' || target_version || '|' || state || '|' || (SELECT string_agg(version::text, ',' ORDER BY version) FROM relay_schema_migrations) || '|' || (SELECT count(*) FROM users WHERE role = 100) || '|' || (SELECT count(*) FROM setups) || '|' || (SELECT count(*) FROM users WHERE remark = 'platform-relay-service-v1') || '|' || (SELECT count(*) FROM tokens WHERE left(name, 15) = 'platform-relay:') FROM relay_schema_state WHERE id = 1"
    # The protected principal set contains API, dispatcher, catalog-sync,
    # relay-sync, and timeout-worker identities. The preceding Go lifecycle
    # gate proves the exact identities; this terminal snapshot independently
    # proves that all five durable user/token rows survived the full chain.
    if ($LASTEXITCODE -ne 0 -or $v7ProtectedState -ne "1|7|7|clean|1,2,3,4,5,6,7|1|1|5|5") {
        throw "The same-database current-v7 protected lifecycle terminal state is not exact"
    }
    Write-Output "post-v7-proof-root-principal-api-edge-current-gate=PASS"

    docker exec $referencePostgres dropdb -U postgres --if-exists $guardDatabase
    docker exec $referencePostgres createdb -U postgres $guardDatabase
    docker exec $referencePostgres psql -v ON_ERROR_STOP=1 -U postgres -d $guardDatabase -c "CREATE EXTENSION pgaudit WITH SCHEMA pg_catalog" | Out-Null
    $routeBindingGuardOutput = & docker run --rm `
      -e "TEST_RELAY_SCHEMA_V4_POSTGRES_DSN=$guardDSN" `
      -v "${repository}:/src:ro" -v newapi-go-mod:/go/pkg/mod -v newapi-go-build:/root/.cache/go-build `
      -v "${postgresTLSVolume}:/tls:ro" `
      -w /src golang:1.25.1 /usr/local/go/bin/go test -json ./model -run "^TestRelaySchemaV4RouteBindingGuardsConfiguredDatabases$" -count=1
    $routeBindingGuardExitCode = $LASTEXITCODE
    if ($routeBindingGuardExitCode -ne 0) {
        $routeBindingGuardOutput | Write-Output
        throw "The retained v4 PostgreSQL route-binding mutation/fencing gate failed"
    }
    Assert-GoTestPassed $routeBindingGuardOutput "TestRelaySchemaV4RouteBindingGuardsConfiguredDatabases" "retained v4 PostgreSQL route-binding mutation/fencing"
    Write-Output "post-v7-route-binding-mutation-fencing-gate=PASS"

    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $pinnedV6AheadOutput = @(& docker run --rm `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime -e RELAY_DATABASE_CA_FILE=/run/relay-secrets/current-v6-ca.crt `
      -e SQL_DSN_FILE=/run/relay-secrets/current-v6-migration-dsn `
      -e RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE=/run/relay-secrets/current-v6-provider-keyring.json `
      -e "RELAY_COMPAT_SOURCE_REVISION=$pinnedV6SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$pinnedV6SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$pinnedV6SourceFileCount" `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      $pinnedV6Image relay-migrate 2>&1)
    $pinnedV6AheadExitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($pinnedV6AheadExitCode -eq 0) {
        throw "The immutable max-v6 image accepted the current-v7 database"
    }
    $pinnedV6Ahead = Get-SingleJSONRecord $pinnedV6AheadOutput "immutable max-v6 ahead probe"
    if ($pinnedV6Ahead.status.classification -ne "ahead" -or
        [int64]$pinnedV6Ahead.status.current_version -ne 7 -or
        [int64]$pinnedV6Ahead.status.target_version -ne 6 -or
        [int64]$pinnedV6Ahead.status.max_version -ne 6) {
        throw "The immutable max-v6 image did not fail closed as ahead on current v7"
    }
    Write-Output "max-v6-ahead-no-direct-rollback-gate=PASS"

    # Keep the older max-v5 evidence as a nested compatibility check; the v6
    # ahead gate above is the direct rollback boundary for this v7 release.
    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $pinnedV5AheadOutput = @(& docker run --rm `
      -e APP_ENV=development -e DEPLOYMENT_ENV=development -e NODE_TYPE=master `
      -e RELAY_LOCAL_DATABASE_ROLE_REHEARSAL=true `
      -e RELAY_DATABASE_ROLE_ATTESTATION_REQUIRED=true -e RELAY_DATABASE_TLS_ATTESTATION_REQUIRED=true `
      -e RELAY_DATABASE_SECRET_FILES_REQUIRED=false -e RELAY_DATABASE_SECRET_FILE_MODE_REQUIRED=true `
      -e RELAY_SCHEMA_OWNER_DATABASE_ROLE=relay_schema_owner -e RELAY_MIGRATION_DATABASE_ROLE=relay_schema_migrator `
      -e RELAY_RUNTIME_DATABASE_ROLE=relay_runtime -e RELAY_DATABASE_CA_FILE=/run/relay-secrets/current-v6-ca.crt `
      -e SQL_DSN_FILE=/run/relay-secrets/current-v6-migration-dsn `
      -e RELAY_PROVIDER_CREDENTIAL_KEYRING_FILE=/run/relay-secrets/current-v6-provider-keyring.json `
      -e "RELAY_COMPAT_SOURCE_REVISION=$pinnedV5SourceRevision" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_SHA256=$pinnedV5SourceSnapshot" `
      -e "RELAY_COMPAT_SOURCE_SNAPSHOT_FILE_COUNT=$pinnedV5SourceFileCount" `
      -v "${postgresTLSVolume}:/tls:ro" `
      -v "${protectedSecretVolume}:/run/relay-secrets:ro" `
      $pinnedV5Image relay-migrate 2>&1)
    $pinnedV5AheadExitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($pinnedV5AheadExitCode -eq 0) {
        throw "The immutable max-v5 image accepted the current-v7 database"
    }
    $pinnedV5Ahead = Get-SingleJSONRecord $pinnedV5AheadOutput "immutable max-v5 ahead probe"
    if ($pinnedV5Ahead.status.classification -ne "ahead" -or
        [int64]$pinnedV5Ahead.status.current_version -ne 7 -or
        [int64]$pinnedV5Ahead.status.target_version -ne 5 -or
        [int64]$pinnedV5Ahead.status.max_version -ne 5) {
        throw "The immutable max-v5 image did not fail closed as ahead on current v7"
    }
    Write-Output "max-v5-ahead-no-direct-rollback-gate=PASS"
    Write-Output "legacy-schema-upgrade-gate=PASS"
}
finally {
    Remove-GateContainer $candidateContainer
    Remove-GateContainer $legacyPostgres
    Remove-GateContainer $referencePostgres
    Remove-GateContainer $v1ReferencePostgres
    docker volume rm -f $protectedSecretVolume *> $null
    docker volume rm -f $pinnedV1SourceVolume *> $null
    docker volume rm -f $pinnedV3SourceVolume *> $null
    docker volume rm -f $currentV7BinaryVolume *> $null
    docker volume rm -f $pinnedV3BinaryVolume *> $null
    docker volume rm -f $postgresTLSVolume *> $null
}
