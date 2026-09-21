param(
    [Parameter(Mandatory = $true)][string]$UnsignedModelReleasePath,
    [Parameter(Mandatory = $true)][string]$ReviewedRoutesPath,
    [Parameter(Mandatory = $true)][string]$SignedModelReleasePath,
    [Parameter(Mandatory = $true)][string]$SignedRoutesPath,
    [Parameter(Mandatory = $true)][string]$ModelReleasePrivateKeyFile,
    [Parameter(Mandatory = $true)][string]$RouteAcceptancePrivateKeyFile,
    [Parameter(Mandatory = $true)][string]$ModelReleaseKeyId,
    [Parameter(Mandatory = $true)][string]$RouteAcceptanceKeyId,
    [Parameter(Mandatory = $true)][string]$RouteAcceptanceReleaseId,
    [Parameter(Mandatory = $true)][ValidateSet("staging", "production")][string]$Environment,
    [Parameter(Mandatory = $true)][string]$SourceRevision,
    [Parameter(Mandatory = $true)][string]$SourceSnapshotSha256,
    [Parameter(Mandatory = $true)][string]$ImageDigest,
    [Parameter(Mandatory = $true)][string]$ModelReleaseSignedAt,
    [Parameter(Mandatory = $true)][string]$ModelReleaseNotAfter,
    [Parameter(Mandatory = $true)][string]$RouteAcceptanceNotBefore,
    [Parameter(Mandatory = $true)][string]$RouteAcceptanceNotAfter
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-AbsoluteInputFile {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Name)

    if (-not [System.IO.Path]::IsPathRooted($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Name must be an existing absolute file path"
    }
}

function Assert-AbsoluteOutputFilePath {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Name)

    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw "$Name must be an absolute file path"
    }
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "$Name parent directory does not exist"
    }
}

foreach ($inputSpec in @(
    @{ Path = $UnsignedModelReleasePath; Name = "UnsignedModelReleasePath" },
    @{ Path = $ReviewedRoutesPath; Name = "ReviewedRoutesPath" },
    @{ Path = $ModelReleasePrivateKeyFile; Name = "ModelReleasePrivateKeyFile" },
    @{ Path = $RouteAcceptancePrivateKeyFile; Name = "RouteAcceptancePrivateKeyFile" }
)) {
    Assert-AbsoluteInputFile -Path $inputSpec.Path -Name $inputSpec.Name
}
Assert-AbsoluteOutputFilePath -Path $SignedModelReleasePath -Name "SignedModelReleasePath"
Assert-AbsoluteOutputFilePath -Path $SignedRoutesPath -Name "SignedRoutesPath"
if (
    [System.IO.Path]::GetFullPath($SignedModelReleasePath) -eq
    [System.IO.Path]::GetFullPath($SignedRoutesPath)
) {
    throw "Signed model-release and route artifacts must use different output paths"
}

try {
    $utcTimestampStyle = (
        [System.Globalization.DateTimeStyles]::AssumeUniversal -bor
        [System.Globalization.DateTimeStyles]::AdjustToUniversal
    )
    $modelReleaseStart = [DateTimeOffset]::ParseExact(
        $ModelReleaseSignedAt,
        "yyyy-MM-dd'T'HH:mm:ss'Z'",
        [System.Globalization.CultureInfo]::InvariantCulture,
        $utcTimestampStyle
    )
    $modelReleaseExpiry = [DateTimeOffset]::ParseExact(
        $ModelReleaseNotAfter,
        "yyyy-MM-dd'T'HH:mm:ss'Z'",
        [System.Globalization.CultureInfo]::InvariantCulture,
        $utcTimestampStyle
    )
    $routeAcceptanceStart = [DateTimeOffset]::ParseExact(
        $RouteAcceptanceNotBefore,
        "yyyy-MM-dd'T'HH:mm:ss'Z'",
        [System.Globalization.CultureInfo]::InvariantCulture,
        $utcTimestampStyle
    )
    $routeAcceptanceExpiry = [DateTimeOffset]::ParseExact(
        $RouteAcceptanceNotAfter,
        "yyyy-MM-dd'T'HH:mm:ss'Z'",
        [System.Globalization.CultureInfo]::InvariantCulture,
        $utcTimestampStyle
    )
}
catch {
    throw "Release validity values must be canonical UTC RFC3339 seconds"
}
if ($modelReleaseStart -gt $routeAcceptanceStart) {
    throw "Model-release attestation must be active before route acceptance"
}
if ($modelReleaseExpiry -lt $routeAcceptanceExpiry) {
    throw "Model-release attestation must not expire before route acceptance"
}

$relayRoot = Split-Path -Parent $PSScriptRoot
$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$workingDirectory = Join-Path $temporaryRoot ("relay-model-route-release-" + [guid]::NewGuid().ToString("N"))
[System.IO.Directory]::CreateDirectory($workingDirectory) | Out-Null

Push-Location $relayRoot
try {
    # Normalize with the Go contract before PowerShell parses the document.
    # This rejects duplicate and unknown JSON keys instead of letting a JSON
    # object conversion silently choose one value and sign the ambiguity away.
    $normalizedRouteLines = @(& go run ./cmd/relay-route-acceptance-sign `
        --routes $ReviewedRoutesPath `
        --validate-only)
    if ($LASTEXITCODE -ne 0) {
        throw "Reviewed route JSON failed strict validation"
    }
    $normalizedRoutesJSON = $normalizedRouteLines -join [Environment]::NewLine

    # Model identity/profile/capability is signed first. The private key is
    # supplied only as an absolute file path; no private bytes enter argv or
    # the environment.
    $signedModelLines = @(& go run ./cmd/relay-model-release-sign `
        --release $UnsignedModelReleasePath `
        --private-key-file $ModelReleasePrivateKeyFile `
        --key-id $ModelReleaseKeyId `
        --signed-at $ModelReleaseSignedAt `
        --not-after $ModelReleaseNotAfter)
    if ($LASTEXITCODE -ne 0) {
        throw "Offline model-release signing failed"
    }
    $signedModelJSON = $signedModelLines -join [Environment]::NewLine
    $signedModel = $signedModelJSON | ConvertFrom-Json
    $legacyAliases = @($signedModel.legacy_public_aliases)
    if (
        [string]$signedModel.public_model_id -cne "seedream-5" -or
        $legacyAliases.Count -ne 1 -or
        [string]$legacyAliases[0] -cne "seedream-5-lite" -or
        $null -eq $signedModel.attestation -or
        [string]$signedModel.attestation.signature -eq ""
    ) {
        throw "Signed model release is not the reviewed Seedream binding"
    }

    # Build the exact route declaration from the signed release. This current
    # paid canary intentionally has one public model; draft Seedance profiles
    # must never be carried into the secure runtime inventory.
    $routes = $normalizedRoutesJSON | ConvertFrom-Json
    $modelIds = @($routes.PSObject.Properties.Name)
    if ($modelIds.Count -ne 1 -or $modelIds[0] -cne "seedream-5") {
        throw "Reviewed paid-canary routes must contain only seedream-5"
    }
    $declarations = @($routes.'seedream-5')
    if ($declarations.Count -lt 1) {
        throw "Reviewed paid-canary routes contain no Seedream route"
    }
    foreach ($declaration in $declarations) {
        $declaration | Add-Member -NotePropertyName model_release -NotePropertyValue $signedModel -Force
        if (
            $null -ne $declaration.PSObject.Properties['staging_ready'] -or
            $null -ne $declaration.PSObject.Properties['production_ready']
        ) {
            throw "Reviewed routes must use signed evidence instead of readiness booleans"
        }
    }
    $reviewedSignedModelRoutesPath = Join-Path $workingDirectory "reviewed-routes.with-model-release.json"
    [System.IO.File]::WriteAllText(
        $reviewedSignedModelRoutesPath,
        ($routes | ConvertTo-Json -Depth 100 -Compress),
        [System.Text.UTF8Encoding]::new($false)
    )

    # Route acceptance is signed only after the signed model release has been
    # embedded. Its canonical route digest commits the immutable release ID,
    # binding revision and capability revision; secure runtime verification
    # independently verifies the embedded model attestation itself.
    $signedRouteLines = @(& go run ./cmd/relay-route-acceptance-sign `
        --routes $reviewedSignedModelRoutesPath `
        --private-key-file $RouteAcceptancePrivateKeyFile `
        --key-id $RouteAcceptanceKeyId `
        --release-id $RouteAcceptanceReleaseId `
        --environment $Environment `
        --source-revision $SourceRevision `
        --source-snapshot-sha256 $SourceSnapshotSha256 `
        --image-digest $ImageDigest `
        --not-before $RouteAcceptanceNotBefore `
        --not-after $RouteAcceptanceNotAfter)
    if ($LASTEXITCODE -ne 0) {
        throw "Offline route-acceptance signing failed"
    }
    $signedRoutesJSON = $signedRouteLines -join [Environment]::NewLine
    $signedRoutes = $signedRoutesJSON | ConvertFrom-Json
    foreach ($declaration in @($signedRoutes.'seedream-5')) {
        if ($null -eq $declaration.model_release.attestation -or $null -eq $declaration.acceptance) {
            throw "Signed route output is missing required release evidence"
        }
    }

    # Both documents are staged and flushed in the destination directory by
    # the Go publisher. It commits with no-replace hard links and creates the
    # signed-routes path last as the pair's commit marker. An exact model-only
    # artifact from an interrupted attempt is resumable; differing files are
    # never overwritten.
    $signedModelPublishSource = Join-Path $workingDirectory "seedream-5.signed.publish.json"
    $signedRoutesPublishSource = Join-Path $workingDirectory "seedream-5.routes.signed.publish.json"
    [System.IO.File]::WriteAllText(
        $signedModelPublishSource,
        ($signedModelJSON + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        $signedRoutesPublishSource,
        ($signedRoutesJSON + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )
    & go run ./cmd/relay-release-pair-publish `
        --model-source $signedModelPublishSource `
        --routes-source $signedRoutesPublishSource `
        --model-destination $SignedModelReleasePath `
        --routes-destination $SignedRoutesPath
    if ($LASTEXITCODE -ne 0) {
        throw "Signed release pair publication failed"
    }
}
finally {
    Pop-Location
    $resolvedWorkingDirectory = [System.IO.Path]::GetFullPath($workingDirectory)
    if ($resolvedWorkingDirectory.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedWorkingDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
