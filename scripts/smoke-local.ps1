param(
    [string]$GatewayBase = "http://127.0.0.1:8180",
    [string]$PlatformBase = "http://127.0.0.1:8200",
    [string]$RelayBase = "http://127.0.0.1:8300",
    [string]$RelayClientId = "",
    [string]$RelayApiKey = "",
    [string]$InternalServiceToken = "",
    [string]$BootstrapToken = "",
    [string]$PlatformAdminUserId = "",
    [string]$MockWebhookSecret = "development-only-secret",
    [string]$FixtureUrl = "https://raw.githubusercontent.com/github/explore/main/topics/python/python.png",
    [string]$OutputFixtureUrl = "https://media.w3.org/2010/05/sintel/trailer.mp4"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

function Read-DotEnvValue {
    param(
        [string]$Path,
        [string]$Name
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $prefix = "$Name="
        if ($trimmed.StartsWith($prefix, [System.StringComparison]::Ordinal)) {
            $value = $trimmed.Substring($prefix.Length).Trim()
            if (
                $value.Length -ge 2 -and
                (
                    ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                    ($value.StartsWith("'") -and $value.EndsWith("'"))
                )
            ) {
                return $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return $null
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$dotEnvPath = Join-Path $repoRoot ".env"
if ([string]::IsNullOrWhiteSpace($RelayClientId)) {
    $RelayClientId = Read-DotEnvValue -Path $dotEnvPath -Name "RELAY_CLIENT_ID"
}
if ([string]::IsNullOrWhiteSpace($RelayApiKey)) {
    $RelayApiKey = Read-DotEnvValue -Path $dotEnvPath -Name "RELAY_API_KEY"
}
if ([string]::IsNullOrWhiteSpace($RelayClientId)) {
    $RelayClientId = "customer-platform"
}
if ([string]::IsNullOrWhiteSpace($RelayApiKey)) {
    $RelayApiKey = "local-customer-platform-relay-key-change-me"
}
if ([string]::IsNullOrWhiteSpace($InternalServiceToken)) {
    $InternalServiceToken = [Environment]::GetEnvironmentVariable("INTERNAL_SERVICE_TOKEN", "Process")
}
if ([string]::IsNullOrWhiteSpace($InternalServiceToken)) {
    $InternalServiceToken = Read-DotEnvValue -Path $dotEnvPath -Name "INTERNAL_SERVICE_TOKEN"
}
if ([string]::IsNullOrWhiteSpace($InternalServiceToken)) {
    $InternalServiceToken = "local-internal-service-token-change-me-04"
}
if ([string]::IsNullOrWhiteSpace($BootstrapToken)) {
    $BootstrapToken = [Environment]::GetEnvironmentVariable("PLATFORM_BOOTSTRAP_TOKEN", "Process")
}
if ([string]::IsNullOrWhiteSpace($BootstrapToken)) {
    $BootstrapToken = Read-DotEnvValue -Path $dotEnvPath -Name "PLATFORM_BOOTSTRAP_TOKEN"
}
if ([string]::IsNullOrWhiteSpace($BootstrapToken)) {
    $BootstrapToken = "local-platform-bootstrap-secret-2026-08-14"
}
$bootstrapHeaders = @{}
if (-not [string]::IsNullOrWhiteSpace($BootstrapToken)) {
    $bootstrapHeaders["X-Bootstrap-Token"] = $BootstrapToken
}

$stamp = Get-Date -Format "yyyyMMddHHmmssfff"

$tenant = Invoke-RestMethod `
    -Method Post `
    -Uri "$GatewayBase/api/v1/bootstrap" `
    -Headers $bootstrapHeaders `
    -ContentType "application/json" `
    -Body (@{
        company_name = "Local smoke $stamp"
        owner_email = "smoke-$stamp@demo-ai-video.cn"
        owner_display_name = "Local Smoke Owner"
    } | ConvertTo-Json)

$smokeModeCapability = @{
    input_media_types = @("audio", "image", "video")
    supports_face = $true
    required_resource_keys = @()
    limits = @{
        max_prompt_length = 10000
        max_images = 9
        max_videos = 3
        max_audio = 3
        duration_seconds = @(5, 10)
        aspect_ratios = @("16:9", "9:16", "1:1")
        resolutions = @("720p", "1080p")
        output_counts = @(1, 2, 3, 4)
    }
}

$model = Invoke-RestMethod `
    -Method Post `
    -Uri "$GatewayBase/api/v1/bootstrap/models" `
    -Headers $bootstrapHeaders `
    -ContentType "application/json" `
    -Body (@{
        slug = "mock.video.v1"
        display_name = "Mock Video V1"
        provider_key = "mock-video"
        billing_mode = "per_item"
        capability_version = 1
        capabilities = @(@{
            key = "generation"
            config = @{
                schema_version = 1
                modes = @{
                    text_to_image = $smokeModeCapability
                    text_to_video = $smokeModeCapability
                    image_to_video = $smokeModeCapability
                    video_to_video = $smokeModeCapability
                }
            }
        })
    } | ConvertTo-Json -Depth 10)

$bootstrappedAdmin = Invoke-RestMethod `
    -Method Post `
    -Uri "$GatewayBase/api/v1/bootstrap/platform-admin" `
    -Headers $bootstrapHeaders `
    -ContentType "application/json" `
    -Body (@{
        email = "smoke-admin-$stamp@demo-ai-video.cn"
        display_name = "Local Smoke Platform Admin"
    } | ConvertTo-Json)
if ([string]::IsNullOrWhiteSpace($PlatformAdminUserId)) {
    $PlatformAdminUserId = $bootstrappedAdmin.user_id
}

$tenantHeaders = @{
    "X-Company-ID" = $tenant.company_id
    "X-User-ID" = $tenant.user_id
}
$adminHeaders = @{
    "X-Platform-Admin-User-ID" = $PlatformAdminUserId
}
$relayHeaders = @{
    "X-Client-ID" = $RelayClientId
    "X-API-Key" = $RelayApiKey
}

$adminCreatedCompany = Invoke-RestMethod `
    -Method Post `
    -Uri "$GatewayBase/api/v1/platform-admin/companies" `
    -Headers $adminHeaders `
    -ContentType "application/json" `
    -Body (@{
        name = "Admin-created smoke $stamp"
        owner_email = "admin-created-owner-$stamp@demo-ai-video.cn"
        owner_display_name = "Admin-created Smoke Owner"
    } | ConvertTo-Json)

$adminCompanies = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/platform-admin/companies?page=1&page_size=100" `
    -Headers $adminHeaders
if ($adminCreatedCompany.id -notin @($adminCompanies.items | ForEach-Object { $_.id })) {
    throw "Platform-admin company creation was not visible in the company list"
}

$resourceDefinitions = @{}
foreach ($resourceKind in @("feature", "agent", "external_api")) {
    $resourceKey = "smoke.$resourceKind.$stamp"
    $resourceDefinitions[$resourceKind] = Invoke-RestMethod `
        -Method Post `
        -Uri "$GatewayBase/api/v1/platform-admin/resources" `
        -Headers $adminHeaders `
        -ContentType "application/json" `
        -Body (@{
            key = $resourceKey
            kind = $resourceKind
            display_name = "Smoke $resourceKind $stamp"
            description = "Local Section 5 smoke resource"
            active = $true
        } | ConvertTo-Json)
}

$initialEntitlements = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/platform-admin/companies/$($tenant.company_id)/entitlements" `
    -Headers $adminHeaders
foreach ($resourceKind in $resourceDefinitions.Keys) {
    $resource = $resourceDefinitions[$resourceKind]
    $entitlement = @(
        $initialEntitlements.resources |
            Where-Object { $_.resource_id -eq $resource.id }
    )
    if ($entitlement.Count -ne 1 -or $entitlement[0].enabled) {
        throw "New $resourceKind resource was not present and disabled by default"
    }
}

$externalApiResource = $resourceDefinitions["external_api"]
Invoke-RestMethod `
    -Method Put `
    -Uri "$GatewayBase/api/v1/platform-admin/companies/$($tenant.company_id)/resources/$($externalApiResource.id)" `
    -Headers $adminHeaders `
    -ContentType "application/json" `
    -Body (@{
        enabled = $true
        config_override = @{ scope = "local-smoke" }
    } | ConvertTo-Json -Depth 4) | Out-Null

$externalApiResource = Invoke-RestMethod `
    -Method Put `
    -Uri "$GatewayBase/api/v1/platform-admin/resources/$($externalApiResource.id)" `
    -Headers $adminHeaders `
    -ContentType "application/json" `
    -Body (@{
        display_name = $externalApiResource.display_name
        description = "Disabled during the local Section 5 smoke"
        active = $false
    } | ConvertTo-Json)

$inactiveEntitlements = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/platform-admin/companies/$($tenant.company_id)/entitlements" `
    -Headers $adminHeaders
$inactiveExternalApi = @(
    $inactiveEntitlements.resources |
        Where-Object { $_.resource_id -eq $externalApiResource.id }
)
if (
    $inactiveExternalApi.Count -ne 1 -or
    $inactiveExternalApi[0].active -or
    -not $inactiveExternalApi[0].enabled
) {
    throw "Inactive resource did not preserve the historical company grant"
}

Invoke-RestMethod `
    -Method Put `
    -Uri "$GatewayBase/api/v1/platform-admin/companies/$($tenant.company_id)/resources/$($externalApiResource.id)" `
    -Headers $adminHeaders `
    -ContentType "application/json" `
    -Body (@{
        enabled = $false
        config_override = @{ scope = "local-smoke" }
    } | ConvertTo-Json -Depth 4) | Out-Null

$assetHttpClient = [System.Net.Http.HttpClient]::new()
try {
    $fixtureBytes = $assetHttpClient.GetByteArrayAsync($FixtureUrl).GetAwaiter().GetResult()
    $multipart = [System.Net.Http.MultipartFormDataContent]::new()
    $fileContent = [System.Net.Http.ByteArrayContent]::new($fixtureBytes)
    $fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new("image/png")
    $multipart.Add($fileContent, "file", "smoke-input.png")
    $multipart.Add([System.Net.Http.StringContent]::new("image"), "media_type")

    $uploadRequest = [System.Net.Http.HttpRequestMessage]::new(
        [System.Net.Http.HttpMethod]::Post,
        "$GatewayBase/api/v1/companies/$($tenant.company_id)/assets"
    )
    $uploadRequest.Headers.Add("X-Company-ID", [string]$tenant.company_id)
    $uploadRequest.Headers.Add("X-User-ID", [string]$tenant.user_id)
    $uploadRequest.Headers.Add("Idempotency-Key", "smoke-asset-$stamp")
    $uploadRequest.Content = $multipart
    $uploadResponse = $assetHttpClient.SendAsync($uploadRequest).GetAwaiter().GetResult()
    $uploadJson = $uploadResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    $uploadResponse.EnsureSuccessStatusCode() | Out-Null
    $inputAsset = $uploadJson | ConvertFrom-Json
}
finally {
    if ($null -ne $uploadRequest) { $uploadRequest.Dispose() }
    if ($null -ne $multipart) { $multipart.Dispose() }
    $assetHttpClient.Dispose()
}

$assetList = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/assets" `
    -Headers $tenantHeaders
if ($inputAsset.id -notin @($assetList | ForEach-Object { $_.id })) {
    throw "Uploaded private input asset was not returned by the company library"
}
$assetPreview = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/assets/$($inputAsset.id)/preview" `
    -Headers $tenantHeaders
$assetPreviewClient = [System.Net.Http.HttpClient]::new()
try {
    $previewResponse = $assetPreviewClient.GetAsync($assetPreview.url).GetAwaiter().GetResult()
    $previewResponse.EnsureSuccessStatusCode() | Out-Null
    $previewBytes = $previewResponse.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
}
finally {
    $assetPreviewClient.Dispose()
}
if ($previewBytes.Length -ne $inputAsset.size_bytes) {
    throw "Private input asset preview size does not match metadata"
}

Invoke-RestMethod `
    -Method Put `
    -Uri "$GatewayBase/api/v1/platform-admin/companies/$($tenant.company_id)/model-grants" `
    -Headers $adminHeaders `
    -ContentType "application/json" `
    -Body (@{
        model_id = $model.id
        enabled = $true
        price_per_item_cents = 25
        config_override = @{}
    } | ConvertTo-Json -Depth 5) | Out-Null

$grantedEntitlements = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/platform-admin/companies/$($tenant.company_id)/entitlements" `
    -Headers $adminHeaders
$grantedModel = @(
    $grantedEntitlements.models |
        Where-Object { $_.model_id -eq $model.id }
)
if (
    $grantedModel.Count -ne 1 -or
    -not $grantedModel[0].enabled -or
    $grantedModel[0].price_per_item_cents -ne 25
) {
    throw "Company model entitlement did not preserve the enabled price"
}

$availableModels = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/models" `
    -Headers $tenantHeaders
$availableModel = @(
    $availableModels |
        Where-Object { $_.id -eq $model.id }
)
if ($availableModel.Count -ne 1) {
    throw "Granted model was not returned by the company model API"
}
$effectiveImageToVideo = $availableModel[0].effective_capabilities.modes.image_to_video
if (
    $availableModel[0].capability_version -ne $model.capability_version -or
    $effectiveImageToVideo.limits.max_images -ne 9 -or
    $effectiveImageToVideo.limits.max_videos -ne 3 -or
    $effectiveImageToVideo.limits.max_audio -ne 3 -or
    -not $effectiveImageToVideo.supports_face
) {
    throw "Company model API did not return the expected effective capability"
}

Invoke-RestMethod `
    -Method Post `
    -Uri "$GatewayBase/api/v1/platform-admin/companies/$($tenant.company_id)/recharge" `
    -Headers $adminHeaders `
    -ContentType "application/json" `
    -Body (@{
        amount_cents = 1000
        idempotency_key = "smoke-recharge-$stamp"
        note = "local smoke only"
    } | ConvertTo-Json) | Out-Null

$taskBody = @{
        model_id = $model.id
        expected_capability_version = $availableModel[0].capability_version
        idempotency_key = "smoke-task-$stamp"
        request_payload = @{
            mode = "image_to_video"
            prompt = "local end-to-end integration smoke"
            duration_seconds = 5
            aspect_ratio = "16:9"
            resolution = "720p"
            output_count = 1
            face_enabled = $true
            assets = @(@{
                asset_id = $inputAsset.id
                media_type = "image"
            })
        }
    } | ConvertTo-Json -Depth 10

$task = Invoke-RestMethod `
    -Method Post `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/tasks" `
    -Headers $tenantHeaders `
    -ContentType "application/json" `
    -Body $taskBody
if (
    $task.capability_snapshot.capability_version -ne $availableModel[0].capability_version -or
    $task.capability_snapshot.effective_capabilities.modes.image_to_video.limits.max_images -ne 9 -or
    -not $task.request_payload.face_enabled
) {
    throw "Task did not preserve the selected effective capability and request"
}

$replayedTask = Invoke-RestMethod `
    -Method Post `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/tasks" `
    -Headers $tenantHeaders `
    -ContentType "application/json" `
    -Body $taskBody
if ($replayedTask.id -ne $task.id) {
    throw "Task idempotency replay created a different task"
}

for ($attempt = 0; $attempt -lt 60 -and -not $task.relay_job_id; $attempt++) {
    Start-Sleep -Milliseconds 500
    $task = Invoke-RestMethod `
        -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/tasks/$($task.id)" `
        -Headers $tenantHeaders
}
if (-not $task.relay_job_id) {
    throw "Platform did not dispatch the task to Relay"
}

$relayJob = $null
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $relayJob = Invoke-RestMethod `
        -Uri "$RelayBase/v1/generations/$($task.relay_job_id)" `
        -Headers $relayHeaders
    if ($relayJob.status -in @("processing", "succeeded", "failed")) {
        break
    }
    Start-Sleep -Milliseconds 500
}
if ($relayJob.status -ne "processing") {
    throw "Relay did not submit the job to Mock Provider"
}
if (
    -not $relayJob.output.face_enabled -or
    $relayJob.output.resolution -ne "720p" -or
    $relayJob.inputs.assets.Count -ne 1 -or
    $relayJob.inputs.assets[0].media_type -ne "image"
) {
    throw "Relay did not receive the capability-validated task parameters"
}
$mockProviderTaskId = "mock-$($task.relay_job_id)"

Invoke-RestMethod `
    -Method Post `
    -Uri "$RelayBase/v1/providers/mock-video/webhooks" `
    -Headers @{ "X-Mock-Webhook-Secret" = $MockWebhookSecret } `
    -ContentType "application/json" `
    -Body (@{
        event_id = "smoke-success-$stamp"
        provider_task_id = $mockProviderTaskId
        status = "succeeded"
        outputs = @(@{
            url = $OutputFixtureUrl
            media_type = "video"
            content_type = "video/mp4"
        })
    } | ConvertTo-Json -Depth 8) | Out-Null

for ($attempt = 0; $attempt -lt 90; $attempt++) {
    $relayJob = Invoke-RestMethod `
        -Uri "$RelayBase/v1/generations/$($task.relay_job_id)" `
        -Headers $relayHeaders
    if ($relayJob.status -in @("succeeded", "failed")) {
        break
    }
    Start-Sleep -Milliseconds 500
}
if ($relayJob.status -ne "succeeded") {
    throw "Relay terminal status is $($relayJob.status)"
}

for ($attempt = 0; $attempt -lt 90; $attempt++) {
    $task = Invoke-RestMethod `
        -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/tasks/$($task.id)" `
        -Headers $tenantHeaders
    if ($task.status -in @("succeeded", "failed")) {
        break
    }
    Start-Sleep -Milliseconds 500
}
if ($task.status -ne "succeeded") {
    throw "Platform terminal status is $($task.status)"
}
if ($task.reserved_cents -ne 0 -or $task.actual_cost_cents -ne 25) {
    throw "Wallet settlement did not match the quoted 25 cents"
}
if ($task.output_artifacts.Count -ne 1) {
    throw "Platform did not persist exactly one artifact"
}

$taskHistory = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/task-history?scope=mine&page=1&page_size=20" `
    -Headers $tenantHeaders
$historyTask = @(
    $taskHistory.items |
        Where-Object { $_.id -eq $task.id }
)
if (
    $historyTask.Count -ne 1 -or
    $historyTask[0].company_id -ne $tenant.company_id -or
    $historyTask[0].user_id -ne $tenant.user_id -or
    $historyTask[0].model_id -ne $model.id -or
    $historyTask[0].request_payload.prompt -ne "local end-to-end integration smoke" -or
    $historyTask[0].status -ne "succeeded" -or
    $historyTask[0].actual_cost_cents -ne 25 -or
    $historyTask[0].artifact_count -ne 1 -or
    $historyTask[0].downloaded
) {
    throw "Task history did not preserve the Section 7 audit fields"
}

$artworksBeforeDownload = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/artworks?scope=mine&media_type=video&page=1&page_size=20" `
    -Headers $tenantHeaders
$artworkBeforeDownload = @(
    $artworksBeforeDownload.items |
        Where-Object { $_.task_id -eq $task.id }
)
if (
    $artworkBeforeDownload.Count -ne 1 -or
    $artworkBeforeDownload[0].asset_id -ne $task.output_artifacts[0].asset_id -or
    $artworkBeforeDownload[0].download_issue_count -ne 0 -or
    $artworkBeforeDownload[0].download_completed_count -ne 0 -or
    $artworkBeforeDownload[0].downloaded
) {
    throw "Archived artwork was not indexed with an honest initial download state"
}

$dashboardBeforeCost = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/platform-admin/dashboard?page=1&page_size=100" `
    -Headers $adminHeaders
$channelCostBody = @{
    amount_cents = 9
    idempotency_key = "smoke-channel-cost-$stamp"
    channel_key = "smoke.official.$stamp"
    channel_type = "official"
    occurred_at = (Get-Date).ToUniversalTime().ToString("o")
    external_reference = "smoke-provider-bill-$stamp"
    company_id = $tenant.company_id
    task_id = $task.id
    relay_job_id = $task.relay_job_id
    note = "local Section 5 smoke cost"
} | ConvertTo-Json
$channelCost = Invoke-RestMethod `
    -Method Post `
    -Uri "$GatewayBase/api/v1/platform-admin/channel-costs" `
    -Headers $adminHeaders `
    -ContentType "application/json" `
    -Body $channelCostBody
$channelCostReplay = Invoke-RestMethod `
    -Method Post `
    -Uri "$GatewayBase/api/v1/platform-admin/channel-costs" `
    -Headers $adminHeaders `
    -ContentType "application/json" `
    -Body $channelCostBody
if ($channelCostReplay.id -ne $channelCost.id) {
    throw "Channel cost idempotency replay created a duplicate entry"
}

$taskCosts = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/platform-admin/channel-costs?task_id=$($task.id)&page=1&page_size=50" `
    -Headers $adminHeaders
if (
    $taskCosts.total -ne 1 -or
    $taskCosts.total_amount_cents -ne 9 -or
    $taskCosts.items[0].source -ne "platform_admin"
) {
    throw "Channel cost entry was not persisted with the expected source and amount"
}

$dashboardAfterCost = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/platform-admin/dashboard?page=1&page_size=100" `
    -Headers $adminHeaders
$expectedUnreconciledCount = $dashboardBeforeCost.unreconciled_succeeded_count - 1
$expectedGrossProfit = if ($expectedUnreconciledCount -eq 0) {
    $dashboardAfterCost.known_gross_profit_cents
} else {
    $null
}
if (
    $dashboardAfterCost.channel_cost_cents -ne ($dashboardBeforeCost.channel_cost_cents + 9) -or
    $dashboardAfterCost.known_gross_profit_cents -ne (
        $dashboardBeforeCost.known_gross_profit_cents - 9
    ) -or
    $dashboardAfterCost.gross_profit_cents -ne $expectedGrossProfit -or
    $dashboardAfterCost.platform_income_cents -ne $dashboardBeforeCost.platform_income_cents -or
    $dashboardAfterCost.unreconciled_succeeded_count -ne $expectedUnreconciledCount
) {
    throw "Platform dashboard did not reconcile income, channel cost, and gross profit"
}
$smokeChannelBreakdown = @(
    $dashboardAfterCost.channel_costs |
        Where-Object { $_.channel_key -eq "smoke.official.$stamp" }
)
if (
    $smokeChannelBreakdown.Count -ne 1 -or
    $smokeChannelBreakdown[0].amount_cents -ne 9
) {
    throw "Platform dashboard did not include the channel cost breakdown"
}
$smokeCompanySummary = @(
    $dashboardAfterCost.companies |
        Where-Object { $_.company_id -eq $tenant.company_id }
)
if (
    $smokeCompanySummary.Count -ne 1 -or
    $smokeCompanySummary[0].recharge_cents -ne 1000 -or
    $smokeCompanySummary[0].consumption_cents -ne 25 -or
    $smokeCompanySummary[0].succeeded_count -ne 1
) {
    throw "Platform dashboard company summary did not match the smoke task"
}

$callbackEvents = $null
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $callbackEvents = Invoke-RestMethod `
        -Uri "$PlatformBase/internal/relay-callback-events?task_id=$($task.id)&relay_status=succeeded" `
        -Headers @{ "X-Internal-Service-Token" = $InternalServiceToken }
    if ($callbackEvents.total -ge 1) {
        break
    }
    Start-Sleep -Milliseconds 500
}
if ($callbackEvents.total -lt 1) {
    throw "Relay succeeded callback was not durably received by Platform"
}

$asset = $task.output_artifacts[0]
$download = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/tasks/$($task.id)/artifacts/$($asset.asset_id)/download" `
    -Headers $tenantHeaders
if (-not $download.download_record_id -or $download.download_status -ne "issued") {
    throw "Artifact access did not return an immutable issued download record"
}

$issuedRecords = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/download-records?scope=mine&task_id=$($task.id)&asset_id=$($asset.asset_id)" `
    -Headers $tenantHeaders
if (
    $issuedRecords.total -ne 1 -or
    $issuedRecords.items[0].id -ne $download.download_record_id -or
    $issuedRecords.items[0].status -ne "issued" -or
    $issuedRecords.items[0].downloaded
) {
    throw "Signed download was not recorded as issued-only"
}

$httpClient = [System.Net.Http.HttpClient]::new()
try {
    $downloadResponse = $httpClient.GetAsync($download.url).GetAwaiter().GetResult()
    $downloadResponse.EnsureSuccessStatusCode() | Out-Null
    $downloadBytes = $downloadResponse.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
}
finally {
    $httpClient.Dispose()
}
if ($downloadBytes.Length -ne $asset.size_bytes) {
    throw "Downloaded artifact size does not match stored metadata"
}

$stillIssuedRecords = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/download-records?scope=mine&task_id=$($task.id)&asset_id=$($asset.asset_id)" `
    -Headers $tenantHeaders
if ($stillIssuedRecords.items[0].status -ne "issued") {
    throw "Browser transfer was incorrectly treated as confirmed without trusted evidence"
}
$postTransferRecords = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/download-records?scope=mine&task_id=$($task.id)&asset_id=$($asset.asset_id)" `
    -Headers $tenantHeaders
if (
    $postTransferRecords.total -ne 1 -or
    $postTransferRecords.items[0].status -ne "issued" -or
    $postTransferRecords.items[0].downloaded -or
    $null -ne $postTransferRecords.items[0].completed_at -or
    $null -ne $postTransferRecords.items[0].completion_source
) {
    throw "Direct browser transfer was incorrectly promoted to trusted download evidence"
}

$downloadedArtworks = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/artworks?scope=mine&downloaded=true&page=1&page_size=20" `
    -Headers $tenantHeaders
$matchingDownloadedArtwork = @(
    $downloadedArtworks.items |
        Where-Object { $_.task_id -eq $task.id }
)
if ($matchingDownloadedArtwork.Count -ne 0) {
    throw "Artwork library reported a download without trusted completion evidence"
}

$wallet = Invoke-RestMethod `
    -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/wallet" `
    -Headers $tenantHeaders

Invoke-RestMethod `
    -Method Patch `
    -Uri "$GatewayBase/api/v1/platform-admin/companies/$($tenant.company_id)/status" `
    -Headers $adminHeaders `
    -ContentType "application/json" `
    -Body (@{ status = "suspended" } | ConvertTo-Json) | Out-Null
$companySuspendBlocked = $false
try {
    Invoke-RestMethod `
        -Uri "$GatewayBase/api/v1/companies/$($tenant.company_id)/me" `
        -Headers $tenantHeaders | Out-Null
}
catch {
    $statusCode = [int]$_.Exception.Response.StatusCode
    if ($statusCode -eq 404) {
        $companySuspendBlocked = $true
    }
    else {
        throw
    }
}
finally {
    Invoke-RestMethod `
        -Method Patch `
        -Uri "$GatewayBase/api/v1/platform-admin/companies/$($tenant.company_id)/status" `
        -Headers $adminHeaders `
        -ContentType "application/json" `
        -Body (@{ status = "active" } | ConvertTo-Json) | Out-Null
}
if (-not $companySuspendBlocked) {
    throw "Suspended company was still admitted by the customer platform"
}

@{
    result = "passed"
    admin_created_company_id = $adminCreatedCompany.id
    company_id = $tenant.company_id
    user_id = $tenant.user_id
    task_id = $task.id
    relay_job_id = $task.relay_job_id
    platform_status = $task.status
    relay_status = $relayJob.status
    idempotency_replay_same_task = ($replayedTask.id -eq $task.id)
    input_asset_id = $inputAsset.id
    input_asset_bytes = $previewBytes.Length
    succeeded_callback_events = $callbackEvents.total
    settled_cents = $task.actual_cost_cents
    artifact_bytes = $downloadBytes.Length
    download_expires_seconds = $download.expires_seconds
    download_record_id = $download.download_record_id
    download_completion_id = $null
    download_status = $postTransferRecords.items[0].status
    trusted_download_completion = $false
    task_history_records = $taskHistory.total
    archived_artworks = $artworksBeforeDownload.total
    wallet_available_cents = $wallet.available_cents
    wallet_reserved_cents = $wallet.reserved_cents
    entitlement_models = $grantedEntitlements.models.Count
    entitlement_resources = $grantedEntitlements.resources.Count
    effective_capability_version = $availableModel[0].capability_version
    effective_max_images = $effectiveImageToVideo.limits.max_images
    effective_max_videos = $effectiveImageToVideo.limits.max_videos
    effective_max_audio = $effectiveImageToVideo.limits.max_audio
    effective_supports_face = $effectiveImageToVideo.supports_face
    external_api_disabled_after_catalog_retirement = (-not $inactiveExternalApi[0].active)
    channel_cost_entry_id = $channelCost.id
    channel_cost_cents = $taskCosts.total_amount_cents
    known_gross_profit_cents = $dashboardAfterCost.known_gross_profit_cents
    gross_profit_cents = $dashboardAfterCost.gross_profit_cents
    channel_cost_status = $dashboardAfterCost.channel_cost_status
    cost_reconciliation_complete = ($dashboardAfterCost.channel_cost_status -eq "complete")
    company_suspend_blocks_requests = $companySuspendBlocked
} | ConvertTo-Json
