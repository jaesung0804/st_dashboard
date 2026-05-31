param(
    [string]$AsOf = "",
    [int]$Workers = 8
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not $env:OPENDART_API_KEY -and -not $env:DART_API_KEY) {
    Write-Error "Set OPENDART_API_KEY before running this script."
}

$DateArgs = @()
$AsOfDate = (Get-Date).ToString("yyyyMMdd")
if ($AsOf) {
    $DateArgs = @("--asof", $AsOf)
    $AsOfDate = $AsOf
}

Set-Location $ProjectRoot

& $Python -m ai_stock_assistant.cli daily-refresh --markets KOSPI KOSDAQ @DateArgs

$CurrentYear = [int](Get-Date).Year
$Years = @($CurrentYear - 1, $CurrentYear)
& $Python -m ai_stock_assistant.cli fetch-kr-financials `
    --listings-path "data\raw\krx_listings_kospi_kosdaq_$AsOfDate.csv" `
    --years $Years `
    --reports annual q1 half q3 `
    --workers $Workers `
    --sleep 0
