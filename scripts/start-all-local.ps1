param(
    [switch]$SkipFrontend,
    [int]$HealthTimeoutSeconds = 60,
    [switch]$VideoLab,
    [ValidateSet("mock", "live")][string]$VideoLabProviderMode = "mock",
    [string]$VideoLabPaidProbeApproval = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($VideoLab) {
    $labNode = (Get-Command node -ErrorAction Stop).Source
    $labScript = Join-Path $PSScriptRoot "local-video-lab.mjs"
    $labArguments = @($labScript, "start", "--mode", $VideoLabProviderMode)
    if ($SkipFrontend) { $labArguments += "--skip-frontend" }
    if ($VideoLabPaidProbeApproval) { $labArguments += @("--paid-probe-approval", $VideoLabPaidProbeApproval) }
    $previousLabEntry = $env:LOCAL_VIDEO_LAB_START_ENTRY
    try {
        $env:LOCAL_VIDEO_LAB_START_ENTRY = "services:start:local"
        & $labNode @labArguments
        if ($LASTEXITCODE -ne 0) { throw "Isolated video lab startup failed; existing ordinary services were not reconfigured." }
    } finally {
        $env:LOCAL_VIDEO_LAB_START_ENTRY = $previousLabEntry
    }
    return
}

function Get-DotEnvDefinitionCount {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $definitionCount = 0
    $escapedName = [System.Text.RegularExpressions.Regex]::Escape($Name)
    # Compose accepts both NAME=value and NAME: value, permits whitespace
    # around the separator, and accepts an optional export prefix. This scan is
    # deliberately only an exact-source guard; Compose itself resolves values.
    $definitionPattern = "^\s*(?:export\s+)?$escapedName\s*(?:=|:)"
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ([string]$line -cmatch $definitionPattern) {
            $definitionCount += 1
        }
    }
    return $definitionCount
}

function Get-ComposeResolvedEnvironment {
    param([Parameter(Mandatory = $true)][string[]]$ComposeArguments)

    # Never print this output: it contains the fully resolved runtime values.
    $commandArguments = @($ComposeArguments + @("config", "--environment"))
    $resolvedLines = @(& docker @commandArguments 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose could not resolve the local runtime environment"
    }

    $resolved = [System.Collections.Generic.Dictionary[string, string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($lineValue in $resolvedLines) {
        $line = [string]$lineValue
        $separator = $line.IndexOf('=')
        if ($separator -le 0) {
            continue
        }
        $name = $line.Substring(0, $separator)
        # Windows contributes process variables such as ProgramFiles(x86) and
        # IntelliJ IDEA. Compose reports them, but they cannot be service env
        # keys and are irrelevant to this POSIX container contract.
        if ($name -cnotmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            continue
        }
        $resolved[$name] = $line.Substring($separator + 1)
    }
    return $resolved
}

function Assert-PaidCanaryRouteInventory {
    param(
        [Parameter(Mandatory = $true)][string]$RawRoutes,
        [Parameter(Mandatory = $true)][string]$Environment
    )

    if ([string]::IsNullOrWhiteSpace($RawRoutes)) {
        throw "NEW_API_RELAY_MODEL_ROUTES_JSON is required in the paid canary runtime environment"
    }

    try {
        $routes = $RawRoutes | ConvertFrom-Json
    }
    catch {
        throw "NEW_API_RELAY_MODEL_ROUTES_JSON is not valid JSON"
    }
    if ($null -eq $routes -or $routes -isnot [System.Management.Automation.PSCustomObject]) {
        throw "NEW_API_RELAY_MODEL_ROUTES_JSON must be a JSON object"
    }

    # Runtime routes are a closed set: the existing Seedream release plus
    # acceptance candidates loaded from the same reviewed Relay manifests that
    # drive `/v1/models`. Merely adding arbitrary JSON to the private env file
    # cannot create another public route.
    $reviewedModelIDs = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    [void]$reviewedModelIDs.Add("seedream-5")
    $workspaceRoot = Split-Path -Parent $PSScriptRoot
    foreach ($manifestRelativePath in @(
        "backend/new-api-relay/generationprofile/seedance_models.v1.json",
        "backend/new-api-relay/generationprofile/minimax_h3_models.v1.json",
        "backend/new-api-relay/generationprofile/google_video_models.v1.json"
    )) {
        $manifestPath = Join-Path $workspaceRoot $manifestRelativePath
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            throw "Reviewed provider model manifest is missing: $manifestRelativePath"
        }
        try {
            $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        catch {
            throw "Reviewed provider model manifest is invalid: $manifestRelativePath"
        }
        if ([int]$manifest.schema_version -ne 1) {
            throw "Reviewed provider model manifest has an unsupported schema: $manifestRelativePath"
        }
        foreach ($model in @($manifest.models)) {
            if (
                [string]$model.lifecycle -ceq "acceptance_candidate" -and
                $model.new_routes_allowed -eq $true -and
                [string]$model.public_model_id -cmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
            ) {
                [void]$reviewedModelIDs.Add([string]$model.public_model_id)
            }
        }
    }

    $modelIDs = @($routes.PSObject.Properties.Name)
    if ($modelIDs.Count -lt 1) {
        throw "Paid canary routes must contain at least one reviewed public model"
    }
    foreach ($modelID in $modelIDs) {
        if (-not $reviewedModelIDs.Contains([string]$modelID)) {
            throw "Paid canary routes contain a model outside the reviewed provider manifests"
        }
    }

    foreach ($modelID in $modelIDs) {
        $declarations = @($routes.PSObject.Properties[$modelID].Value)
        if ($declarations.Count -lt 1) {
            throw "Paid canary model $modelID requires at least one reviewed route declaration"
        }
        foreach ($declaration in $declarations) {
            if (
                $null -eq $declaration -or
                [string]::IsNullOrWhiteSpace([string]$declaration.capability_profile)
            ) {
                throw "Paid canary route is not bound to its reviewed canonical capability"
            }
            $hasModelRelease = $null -ne $declaration.model_release
            if (
                $hasModelRelease -and
                [string]$declaration.model_release.public_model_id -cne [string]$modelID
            ) {
                throw "Paid canary route model release does not match its public model"
            }
            if (-not $hasModelRelease -and (
                $Environment -cne "development" -or
                [string]$modelID -cne "seedream-5" -or
                [string]$declaration.provider_name -cne "volcengine-ark" -or
                [string]$declaration.upstream_model -cne "doubao-seedream-5-0-260128" -or
                [string]$declaration.capability_profile -cne "volcengine.ark.image-generation.v1"
            )) {
                throw "Paid canary route is missing its reviewed canonical model release"
            }
            if ([string]$modelID -ceq "seedream-5") {
                if ([string]$declaration.capability_profile -cne "volcengine.ark.image-generation.v1") {
                    throw "Paid canary seedream-5 capability profile is not canonical"
                }
                if ($hasModelRelease) {
                    $aliases = @($declaration.model_release.legacy_public_aliases)
                    if ($aliases.Count -ne 1 -or [string]$aliases[0] -cne "seedream-5-lite") {
                        throw "Paid canary seedream-5 must expose only the reviewed seedream-5-lite legacy alias"
                    }
                }
            }
            if (
                $null -ne $declaration.PSObject.Properties['staging_ready'] -or
                $null -ne $declaration.PSObject.Properties['production_ready']
            ) {
                throw "Paid canary routes must use signed acceptance evidence instead of readiness booleans"
            }
            if ($Environment -in @("staging", "production") -and (
                $null -eq $declaration.model_release.attestation -or
                [string]::IsNullOrWhiteSpace([string]$declaration.model_release.attestation.signature) -or
                $null -eq $declaration.acceptance -or
                [string]::IsNullOrWhiteSpace([string]$declaration.acceptance.signature)
            )) {
                throw "Secure paid canary requires both signed model-release attestation and route acceptance evidence"
            }
        }
    }
}

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]]$CommandArguments)

    $fullArguments = @($script:ComposeArgs + $CommandArguments)
    & docker @fullArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose command failed: $($CommandArguments -join ' ')"
    }
}

function Test-ComposeOneShotSucceeded {
    param([Parameter(Mandatory = $true)][string]$Service)

    $arguments = @($script:ComposeArgs + @("ps", "-a", "-q", $Service))
    $containerIds = @(& docker @arguments)
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose could not inspect the local bootstrap state"
    }
    $containerId = [string]($containerIds | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        return $false
    }

    $stateJson = & docker inspect --format "{{json .State}}" $containerId
    if ($LASTEXITCODE -ne 0) {
        throw "Docker could not inspect the local bootstrap container"
    }
    $state = $stateJson | ConvertFrom-Json
    return $state.Status -eq "exited" -and [int]$state.ExitCode -eq 0
}

function Invoke-ComposeOneShot {
    param(
        [Parameter(Mandatory = $true)][string]$Service,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    # Force recreation is part of the provenance boundary: a successful old
    # receipt must never satisfy a migration performed by a newly built Relay.
    Invoke-Compose -CommandArguments @(
        "up", "-d", "--no-deps", "--force-recreate", $Service
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max($TimeoutSeconds, 10))
    do {
        $arguments = @($script:ComposeArgs + @("ps", "-a", "-q", $Service))
        $containerIds = @(& docker @arguments)
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose could not inspect Relay one-shot service $Service"
        }
        $containerId = [string]($containerIds | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
        if (-not [string]::IsNullOrWhiteSpace($containerId)) {
            $stateJson = & docker inspect --format "{{json .State}}" $containerId
            if ($LASTEXITCODE -ne 0) {
                throw "Docker could not inspect Relay one-shot service $Service"
            }
            $state = $stateJson | ConvertFrom-Json
            if ($state.Status -eq "exited") {
                if ([int]$state.ExitCode -ne 0) {
                    throw "Relay one-shot service $Service failed with exit code $($state.ExitCode)"
                }
                return
            }
            if ($state.Status -in @("dead", "removing")) {
                throw "Relay one-shot service $Service entered state $($state.Status)"
            }
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)

    throw "Relay one-shot service $Service did not succeed within $TimeoutSeconds seconds"
}

function Get-ComposeContainerId {
    param([Parameter(Mandatory = $true)][string]$Service)

    $arguments = @($script:ComposeArgs + @("ps", "-q", $Service))
    $containerIds = @(& docker @arguments)
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose could not inspect running service $Service"
    }
    return [string]($containerIds | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1)
}

function Get-DockerContainerImageId {
    param([string]$ContainerId)

    if ([string]::IsNullOrWhiteSpace($ContainerId)) {
        return $null
    }
    $imageId = & docker inspect --format "{{.Image}}" $ContainerId 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$imageId)) {
        return $null
    }
    return ([string]$imageId).Trim().ToLowerInvariant()
}

function Get-DockerImageId {
    param([Parameter(Mandatory = $true)][string]$Image)

    # A missing target tag is the normal first-build state. Windows PowerShell
    # otherwise promotes Docker's stderr to a terminating NativeCommandError
    # because this script is fail-fast globally.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $imageId = & docker image inspect --format "{{.Id}}" $Image 2>$null
        $inspectExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($inspectExitCode -ne 0 -or [string]::IsNullOrWhiteSpace([string]$imageId)) {
        return $null
    }
    return ([string]$imageId).Trim().ToLowerInvariant()
}

function Get-RelayRuntimeBuildIdentity {
    param([Parameter(Mandatory = $true)][string]$Uri)

    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -Headers @{ Accept = "application/json" } `
            -Uri $Uri `
            -TimeoutSec 5
        if ($response.StatusCode -ne 200) {
            return $null
        }
        $sourceRevision = [string]$response.Headers["X-Relay-Source-Revision"]
        $sourceSnapshot = [string]$response.Headers["X-Relay-Source-Snapshot-SHA256"]
        $sourceFileCountRaw = [string]$response.Headers["X-Relay-Source-File-Count"]
        $sourceFileCount = 0
        if (-not [int]::TryParse($sourceFileCountRaw, [ref]$sourceFileCount)) {
            return $null
        }
        return [pscustomobject]@{
            SourceRevision = $sourceRevision.Trim().ToLowerInvariant()
            SourceSnapshotSHA256 = $sourceSnapshot.Trim().ToLowerInvariant()
            SourceFileCount = $sourceFileCount
        }
    }
    catch {
        return $null
    }
}

function Assert-RelayTargetImageIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$Image,
        [Parameter(Mandatory = $true)][string]$SourceRevision,
        [Parameter(Mandatory = $true)][string]$SourceSnapshotSHA256,
        [Parameter(Mandatory = $true)][int]$SourceFileCount
    )

    # This command has no network, mounts or runtime environment. It exposes
    # only linker-owned source provenance from the newly built binary.
    $identityJson = & docker run `
        --rm `
        --pull=never `
        --network none `
        --read-only `
        --cap-drop ALL `
        --security-opt "no-new-privileges:true" `
        --entrypoint /new-api `
        $Image `
        relay-build-identity
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$identityJson)) {
        throw "The newly built Relay image could not prove its compiled identity"
    }
    try {
        $identity = $identityJson | ConvertFrom-Json
    }
    catch {
        throw "The newly built Relay image returned an invalid compiled identity"
    }
    if (
        [string]$identity.kind -cne "relay_compiled_build_identity" -or
        [string]$identity.source_revision -cne $SourceRevision -or
        [string]$identity.source_snapshot_sha256 -cne $SourceSnapshotSHA256 -or
        [int]$identity.source_snapshot_file_count -ne $SourceFileCount
    ) {
        throw "The newly built Relay image does not match the Compose-resolved source identity"
    }
}

function Test-TcpPort {
    param(
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][int]$Port
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync($HostName, $Port)
        if (-not $connection.Wait(1000)) {
            return $false
        }
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Test-HttpOk {
    param([Parameter(Mandatory = $true)][string]$Uri)

    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -Headers @{ Accept = "text/html,application/json" } `
            -Uri $Uri `
            -TimeoutSec 5
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $envFiles = @(
        ".env",
        "deploy/secrets/huawei-obs.runtime.env",
        "deploy/secrets/paid-canary.runtime.env",
        "deploy/secrets/platform-canary.runtime.env"
    )
    foreach ($envFile in $envFiles) {
        if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
            throw "Required local runtime environment file is missing: $envFile"
        }
    }

    # Keep .env first. Later runtime files may add environment-specific values,
    # but the source checks below prevent them from redefining protected Relay
    # settings. The very same argument list is used for validation and startup.
    $script:ComposeArgs = @(
        "compose",
        "--env-file", ".env",
        "--env-file", "deploy/secrets/huawei-obs.runtime.env",
        "--env-file", "deploy/secrets/paid-canary.runtime.env",
        "--env-file", "deploy/secrets/platform-canary.runtime.env",
        "-f", "docker-compose.yml",
        "-f", "deploy/compose.internal-pilot.yml"
    )
    $composeEnvironment = Get-ComposeResolvedEnvironment -ComposeArguments $script:ComposeArgs

    $routeSettingName = "NEW_API_RELAY_MODEL_ROUTES_JSON"
    $routeDefinitionCount = 0
    $routeDefinitionSource = $null
    foreach ($envFile in $envFiles) {
        $fileDefinitionCount = Get-DotEnvDefinitionCount -Path $envFile -Name $routeSettingName
        if ($fileDefinitionCount -gt 0) {
            $routeDefinitionCount += $fileDefinitionCount
            $routeDefinitionSource = $envFile
        }
    }
    if (
        $routeDefinitionCount -ne 1 -or
        $routeDefinitionSource -cne "deploy/secrets/paid-canary.runtime.env"
    ) {
        throw "NEW_API_RELAY_MODEL_ROUTES_JSON must be defined exactly once in the paid canary environment file"
    }
    $ambientRouteSetting = [Environment]::GetEnvironmentVariable(
        $routeSettingName,
        [EnvironmentVariableTarget]::Process
    )
    if ($null -ne $ambientRouteSetting) {
        throw "Ambient NEW_API_RELAY_MODEL_ROUTES_JSON overrides are forbidden"
    }

    $compatSettingName = "NEW_API_RELAY_COMPAT_ENVIRONMENT"
    $compatDefinitionCount = 0
    $compatDefinitionSource = $null
    foreach ($envFile in $envFiles) {
        $fileDefinitionCount = Get-DotEnvDefinitionCount -Path $envFile -Name $compatSettingName
        if ($fileDefinitionCount -gt 0) {
            $compatDefinitionCount += $fileDefinitionCount
            $compatDefinitionSource = $envFile
        }
    }
    if (
        $compatDefinitionCount -gt 1 -or
        ($compatDefinitionCount -eq 1 -and $compatDefinitionSource -cne "deploy/secrets/paid-canary.runtime.env")
    ) {
        throw "NEW_API_RELAY_COMPAT_ENVIRONMENT may be defined only once in the paid canary environment file"
    }
    $ambientCompatEnvironment = [Environment]::GetEnvironmentVariable(
        $compatSettingName,
        [EnvironmentVariableTarget]::Process
    )
    if ($null -ne $ambientCompatEnvironment) {
        throw "Ambient NEW_API_RELAY_COMPAT_ENVIRONMENT overrides are forbidden"
    }
    $relayCompatEnvironment = "development"
    if ($composeEnvironment.ContainsKey($compatSettingName)) {
        $relayCompatEnvironment = $composeEnvironment[$compatSettingName].Trim().ToLowerInvariant()
    }
    if ($relayCompatEnvironment -notin @("development", "test", "staging", "production")) {
        throw "NEW_API_RELAY_COMPAT_ENVIRONMENT is invalid"
    }
    if (-not $composeEnvironment.ContainsKey($routeSettingName)) {
        throw "NEW_API_RELAY_MODEL_ROUTES_JSON is absent from the Compose-resolved environment"
    }
    Assert-PaidCanaryRouteInventory `
        -RawRoutes $composeEnvironment[$routeSettingName] `
        -Environment $relayCompatEnvironment

    $requiredAuthKeys = @(
        "PLATFORM_OIDC_ENABLED",
        "PLATFORM_OIDC_ISSUER",
        "PLATFORM_OIDC_AUTHORIZATION_ENDPOINT",
        "PLATFORM_OIDC_TOKEN_ENDPOINT",
        "PLATFORM_OIDC_JWKS_URI",
        "PLATFORM_OIDC_CLIENT_ID",
        "PLATFORM_OIDC_REDIRECT_URI",
        "PLATFORM_FRONTEND_ORIGIN",
        "PLATFORM_OWNER_USER_IDS_JSON"
    )
    foreach ($key in $requiredAuthKeys) {
        if (-not $composeEnvironment.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($composeEnvironment[$key])) {
            throw "Required local authentication setting is empty: $key"
        }
    }
    if ($composeEnvironment["PLATFORM_OIDC_ENABLED"].ToLowerInvariant() -ne "true") {
        throw "PLATFORM_OIDC_ENABLED must be true for the authenticated local stack"
    }

    $relaySourceRevisionName = "NEW_API_RELAY_SOURCE_REVISION"
    $relaySourceSnapshotName = "NEW_API_RELAY_SOURCE_SNAPSHOT_SHA256"
    $relaySourceFileCountName = "NEW_API_RELAY_SOURCE_SNAPSHOT_FILE_COUNT"
    foreach ($key in @($relaySourceRevisionName, $relaySourceSnapshotName, $relaySourceFileCountName)) {
        if (-not $composeEnvironment.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($composeEnvironment[$key])) {
            throw "Required Relay source identity is empty: $key"
        }
    }
    $relayTargetSourceRevision = $composeEnvironment[$relaySourceRevisionName].Trim().ToLowerInvariant()
    $relayTargetSourceSnapshot = $composeEnvironment[$relaySourceSnapshotName].Trim().ToLowerInvariant()
    $relayTargetSourceFileCount = 0
    if (
        $relayTargetSourceRevision -cnotmatch '^[0-9a-f]{40}$' -or
        $relayTargetSourceRevision -ceq ("1" * 40) -or
        $relayTargetSourceSnapshot -cnotmatch '^sha256:[0-9a-f]{64}$' -or
        $relayTargetSourceSnapshot -ceq ("sha256:" + ("1" * 64)) -or
        -not [int]::TryParse(
            $composeEnvironment[$relaySourceFileCountName].Trim(),
            [ref]$relayTargetSourceFileCount
        ) -or
        $relayTargetSourceFileCount -lt 1
    ) {
        throw "Compose-resolved Relay source identity is invalid or a development placeholder"
    }
    $relayImageTag = "0ab0202-local"
    if (
        $composeEnvironment.ContainsKey("NEW_API_RELAY_IMAGE_TAG") -and
        -not [string]::IsNullOrWhiteSpace($composeEnvironment["NEW_API_RELAY_IMAGE_TAG"])
    ) {
        $relayImageTag = $composeEnvironment["NEW_API_RELAY_IMAGE_TAG"].Trim()
    }
    if ($relayImageTag -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$') {
        throw "Compose-resolved Relay image tag is invalid"
    }
    $relayTargetImage = "ai-video/new-api-relay:$relayImageTag"

    Invoke-Compose -CommandArguments @("config", "--quiet")

    # The Relay API and download edge hold a shared database lifecycle lock.
    # Re-running the privileged one-shot chain while either process exists can
    # wait on that lock and fail after 30 seconds. A source-and-image-identical
    # runtime therefore uses the existing warm path; a different target image
    # is built and verified before either long-lived process is stopped.
    $relayBootstrapComplete = Test-ComposeOneShotSucceeded -Service "relay-new-api-db-role-post"
    $relayAlreadyReady = Test-HttpOk -Uri "http://127.0.0.1:8300/health/ready"
    $relayEdgeAlreadyReady = Test-HttpOk -Uri "http://127.0.0.1:8400/health/ready"
    $relayApiContainerId = Get-ComposeContainerId -Service "relay-new-api"
    $relayEdgeContainerId = Get-ComposeContainerId -Service "relay-download-edge"
    if (
        -not $relayBootstrapComplete -and
        (
            $relayAlreadyReady -or
            $relayEdgeAlreadyReady -or
            -not [string]::IsNullOrWhiteSpace($relayApiContainerId) -or
            -not [string]::IsNullOrWhiteSpace($relayEdgeContainerId)
        )
    ) {
        throw "Relay is running without a successful role-post bootstrap receipt; refusing to start a conflicting lifecycle chain"
    }

    $relayApiImageId = Get-DockerContainerImageId -ContainerId $relayApiContainerId
    $relayEdgeImageId = Get-DockerContainerImageId -ContainerId $relayEdgeContainerId
    $relayTaggedImageId = Get-DockerImageId -Image $relayTargetImage
    $relayRuntimeIdentity = Get-RelayRuntimeBuildIdentity -Uri "http://127.0.0.1:8300/health/ready"
    $relayRuntimeMatchesTarget = (
        $relayBootstrapComplete -and
        $null -ne $relayRuntimeIdentity -and
        $null -ne $relayTaggedImageId -and
        $relayApiImageId -ceq $relayTaggedImageId -and
        $relayEdgeImageId -ceq $relayTaggedImageId -and
        $relayRuntimeIdentity.SourceRevision -ceq $relayTargetSourceRevision -and
        $relayRuntimeIdentity.SourceSnapshotSHA256 -ceq $relayTargetSourceSnapshot -and
        $relayRuntimeIdentity.SourceFileCount -eq $relayTargetSourceFileCount
    )
    $relayRuntimeUpgradePerformed = $false

    if ($relayBootstrapComplete -and -not $relayRuntimeMatchesTarget) {
        # Build and inspect the exact target while the old runtime is still
        # serving. A failed build or provenance mismatch causes no downtime.
        Invoke-Compose -CommandArguments @("build", "relay-new-api")
        $relayTaggedImageId = Get-DockerImageId -Image $relayTargetImage
        if ($null -eq $relayTaggedImageId) {
            throw "The Compose target Relay image is unavailable after a successful build"
        }
        Assert-RelayTargetImageIdentity `
            -Image $relayTaggedImageId `
            -SourceRevision $relayTargetSourceRevision `
            -SourceSnapshotSHA256 $relayTargetSourceSnapshot `
            -SourceFileCount $relayTargetSourceFileCount
        if ((Get-DockerImageId -Image $relayTargetImage) -cne $relayTaggedImageId) {
            throw "The Compose Relay image tag changed during offline identity verification"
        }

        # Only a verified target may take the old data plane offline. If any
        # subsequent one-shot fails, the mismatched API/edge remain stopped.
        Invoke-Compose -CommandArguments @("stop", "relay-download-edge")
        Invoke-Compose -CommandArguments @("stop", "relay-new-api")
        Invoke-Compose -CommandArguments @(
            "up", "-d", "--no-deps", "--wait", "--wait-timeout", "$HealthTimeoutSeconds",
            "relay-new-api-postgres",
            "relay-new-api-redis"
        )
        foreach ($relayOneShot in @(
            "relay-new-api-volume-init",
            "relay-new-api-db-role-pre",
            "relay-new-api-migrate",
            "relay-new-api-db-role-post"
        )) {
            Invoke-ComposeOneShot `
                -Service $relayOneShot `
                -TimeoutSeconds $HealthTimeoutSeconds
        }
        $relayRuntimeUpgradePerformed = $true
    }

    if ($relayBootstrapComplete) {
        Invoke-Compose -CommandArguments @(
            "up", "-d", "--no-deps", "--wait", "--wait-timeout", "$HealthTimeoutSeconds",
            "postgres",
            "relay-new-api-postgres",
            "relay-new-api-redis"
        )
        if ($relayRuntimeUpgradePerformed) {
            Invoke-Compose -CommandArguments @(
                "up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "$HealthTimeoutSeconds",
                "relay-new-api",
                "relay-download-edge"
            )
        }
        else {
            # Exact source and image identity: preserve the low-disruption warm
            # path and do not rerun privileged database one-shots.
            Invoke-Compose -CommandArguments @(
                "up", "-d", "--no-deps", "--wait", "--wait-timeout", "$HealthTimeoutSeconds",
                "relay-new-api",
                "relay-download-edge"
            )
        }
        Invoke-Compose -CommandArguments @(
            "up", "-d", "--build", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "$HealthTimeoutSeconds",
            "platform-api"
        )
        Invoke-Compose -CommandArguments @(
            "up", "-d", "--build", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "$HealthTimeoutSeconds",
            "platform-timeout-worker",
            "platform-dispatcher",
            "platform-download-gateway-registration-worker",
            "platform-relay-catalog-sync",
            "platform-relay-sync",
            "api-gateway"
        )
    }
    else {
        # First boot (or an unavailable Relay) must execute the complete,
        # dependency-ordered database bootstrap and migration graph.
        Invoke-Compose -CommandArguments @("up", "-d")
    }
    Invoke-Compose -CommandArguments @("restart", "api-gateway")

    $deadline = (Get-Date).AddSeconds([Math]::Max($HealthTimeoutSeconds, 10))
    $backendReady = $false
    do {
        try {
            $platformHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8200/health" -TimeoutSec 5
            $gatewayHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8180/health" -TimeoutSec 5
            $authSession = Invoke-RestMethod -Uri "http://127.0.0.1:8180/api/v1/auth/session" -TimeoutSec 5
            $relayStatus = Invoke-RestMethod -Uri "http://127.0.0.1:8300/health/ready" -TimeoutSec 5
            $relayEdgeStatus = Invoke-RestMethod -Uri "http://127.0.0.1:8400/health/ready" -TimeoutSec 5
            $backendReady = (
                $platformHealth.status -eq "ok" -and
                $gatewayHealth.status -eq "ok" -and
                $authSession.login_available -eq $true -and
                $null -ne $relayStatus -and
                $null -ne $relayEdgeStatus
            )
        }
        catch {
            $backendReady = $false
        }
        if (-not $backendReady) {
            Start-Sleep -Seconds 2
        }
    } while (-not $backendReady -and (Get-Date) -lt $deadline)

    if (-not $backendReady) {
        throw "Local backend did not become ready with login_available=true within $HealthTimeoutSeconds seconds"
    }

    if (-not $SkipFrontend -and -not (Test-HttpOk -Uri "http://127.0.0.1:5173/platform")) {
        if (Test-TcpPort -HostName "127.0.0.1" -Port 5173) {
            throw "Port 5173 is occupied, but /platform is not available; refusing to stop an unknown process"
        }
        $npmCommand = Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue
        if ($null -eq $npmCommand) {
            $npmCommand = Get-Command npm -CommandType Application -ErrorAction SilentlyContinue
        }
        if ($null -eq $npmCommand) {
            throw "npm is required to start the local frontend"
        }
        $runtimeDirectory = Join-Path $repoRoot ".tmp/local-services"
        New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
        $frontendProcess = Start-Process `
            -FilePath $npmCommand.Source `
            -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort") `
            -WorkingDirectory $repoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $runtimeDirectory "frontend.stdout.log") `
            -RedirectStandardError (Join-Path $runtimeDirectory "frontend.stderr.log") `
            -PassThru
        Set-Content -LiteralPath (Join-Path $runtimeDirectory "frontend.pid") -Value $frontendProcess.Id -Encoding ascii

        do {
            if (Test-HttpOk -Uri "http://127.0.0.1:5173/platform") {
                break
            }
            if ($frontendProcess.HasExited) {
                throw "The local frontend exited before /platform became available"
            }
            Start-Sleep -Seconds 1
        } while ((Get-Date) -lt $deadline)
    }

    if (-not $SkipFrontend -and -not (Test-HttpOk -Uri "http://127.0.0.1:5173/platform")) {
        throw "The local frontend did not expose /platform within $HealthTimeoutSeconds seconds"
    }

    Write-Output "LOCAL_SERVICES_READY"
    Write-Output "frontend=http://127.0.0.1:5173/platform"
    Write-Output "gateway=http://127.0.0.1:8180"
    Write-Output "platform=http://127.0.0.1:8200"
    Write-Output "relay=http://127.0.0.1:8300"
    Write-Output "login_available=true"
}
finally {
    Pop-Location
}
