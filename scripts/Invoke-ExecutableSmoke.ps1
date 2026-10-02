[CmdletBinding()]
param(
    [string]$ExecutablePath = "dist\windows\MusicMasteringTools.exe",
    [string]$PythonPath,
    [string]$OutputDirectory = "artifacts\executable-smoke"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
& $PythonPath (Join-Path $PSScriptRoot "executable_smoke.py") --executable $ExecutablePath --artifacts $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw "Frozen executable smoke failed; review $OutputDirectory." }
