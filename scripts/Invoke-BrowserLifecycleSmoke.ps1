[CmdletBinding()]
param(
    [string]$ExecutablePath,
    [string]$BrowserPath,
    [switch]$IncludeCrashFallback
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_Common.ps1")
Set-Location -LiteralPath (Get-ProjectRoot)
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Browser lifecycle smoke requires Node.js 22+ for its built-in WebSocket."
}
if ([int]((& node --version).Trim().TrimStart('v').Split('.')[0]) -lt 22) {
    throw "Browser lifecycle smoke requires Node.js 22+."
}
if ([string]::IsNullOrWhiteSpace($BrowserPath)) {
    $BrowserPath = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($BrowserPath)) { throw "Chrome or Edge was not found." }
if ([string]::IsNullOrWhiteSpace($ExecutablePath)) {
    $mode = "source"
    $target = Get-ProjectPython
}
else {
    $mode = "frozen"
    $target = (Resolve-Path -LiteralPath $ExecutablePath).Path
}
$artifacts = Resolve-ProjectPath -Path "artifacts\browser-lifecycle-smoke"
Write-ToolLog -Level "INFO" -Message "Starting isolated $mode browser lifecycle audit."
$crash = if ($IncludeCrashFallback) { "crash" } else { "no-crash" }
& node (Join-Path $PSScriptRoot "browser_lifecycle_audit.mjs") $mode $target $BrowserPath $artifacts $crash
if ($LASTEXITCODE -ne 0) {
    throw "Browser lifecycle smoke failed. Inspect artifacts/browser-lifecycle-smoke/*/verification-summary.json."
}
