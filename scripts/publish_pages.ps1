param(
    [int]$Days = 22,
    [switch]$Push
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Set-Location $ProjectRoot

if (-not (Test-Path $Python)) {
    $Python = "python"
}

$Args = @("scripts\build_pages_deploy.py", "--days", $Days)
if ($Push) {
    $Args += "--push"
}

& $Python @Args
