param(
  [string]$NewApiPostgresPassword = "local-new-api-postgres-password-x",
  [string]$MigrationPassword = "local-migrator-postgres-password",
  [string]$RuntimePassword = "local-runtime-postgres-password-x",
  [string]$DownloadEdgePassword = "local-download-edge-postgres-key-2026-abcd"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$secretDirectory = Join-Path $repositoryRoot "deploy/secrets"
New-Item -ItemType Directory -Path $secretDirectory -Force | Out-Null

function Write-LocalSecret([string]$Name, [string]$Value) {
  $path = Join-Path $secretDirectory $Name
  [System.IO.File]::WriteAllText($path, $Value, [System.Text.UTF8Encoding]::new($false))
}

$encodedAdminPassword = [System.Uri]::EscapeDataString($NewApiPostgresPassword)
$encodedMigrationPassword = [System.Uri]::EscapeDataString($MigrationPassword)
$encodedRuntimePassword = [System.Uri]::EscapeDataString($RuntimePassword)
$encodedEdgePassword = [System.Uri]::EscapeDataString($DownloadEdgePassword)
Write-LocalSecret "relay-local-role-admin-dsn" "postgresql://new_api:$encodedAdminPassword@relay-new-api-postgres:5432/new_api?sslmode=disable&search_path=public"
Write-LocalSecret "relay-local-migration-db-password" $MigrationPassword
Write-LocalSecret "relay-local-runtime-db-password" $RuntimePassword
Write-LocalSecret "relay-local-download-edge-db-password" $DownloadEdgePassword
Write-LocalSecret "relay-local-migration-sql-dsn" "postgresql://relay_schema_migrator:$encodedMigrationPassword@relay-new-api-postgres:5432/new_api?sslmode=disable&search_path=public&options=-c%20role%3Drelay_schema_owner"
Write-LocalSecret "relay-local-runtime-sql-dsn" "postgresql://relay_runtime:$encodedRuntimePassword@relay-new-api-postgres:5432/new_api?sslmode=disable&search_path=public"
Write-LocalSecret "relay-local-download-edge-sql-dsn" "postgresql://relay_download_edge:$encodedEdgePassword@relay-new-api-postgres:5432/new_api?sslmode=disable&search_path=public"

Write-Output "relay-local-db-secret-bootstrap=PASS"
