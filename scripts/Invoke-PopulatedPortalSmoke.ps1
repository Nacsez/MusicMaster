[CmdletBinding()]
param(
    [string]$OutputDirectory = "artifacts\ux-populated-smoke",
    [string]$BrowserPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path -Path $PSScriptRoot -ChildPath "_Common.ps1")

$projectRoot = Get-ProjectRoot
Set-Location -LiteralPath $projectRoot
$python = Get-ProjectPython
Assert-PythonVersion -Python $python
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "The populated browser smoke requires Node.js 22+ for its built-in WebSocket."
}
$nodeMajor = [int]((& node --version).Trim().TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 22) {
    throw "The populated browser smoke requires Node.js 22+; found major version $nodeMajor."
}

if ([string]::IsNullOrWhiteSpace($BrowserPath)) {
    $BrowserPath = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($BrowserPath) -or
    -not (Test-Path -LiteralPath $BrowserPath -PathType Leaf)) {
    throw "Chromium was not found. Pass -BrowserPath with the full Chrome or Edge path."
}

$artifactRoot = Resolve-ProjectPath -Path $OutputDirectory
$allowedRoot = Resolve-ProjectPath -Path "artifacts\ux-populated-smoke"
$allowedPrefix = $allowedRoot.TrimEnd([char[]]"\/") +
    [System.IO.Path]::DirectorySeparatorChar
if (-not $artifactRoot.Equals($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
    -not $artifactRoot.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Populated smoke is restricted to $allowedRoot."
}
$timestamp = [System.DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
$runDirectory = Join-Path -Path $artifactRoot -ChildPath $timestamp
$readyPath = Join-Path -Path $runDirectory -ChildPath "portal-ready.json"
$portalProcess = $null
$token = $null
$origin = $null
$passed = $false

try {
    Write-ToolLog -Level "INFO" -Message "Creating isolated populated WAV/catalog fixtures."
    $fixtureOutput = @(& $python (Join-Path $PSScriptRoot "populate_portal_smoke.py") $runDirectory)
    if ($LASTEXITCODE -ne 0) { throw "Populated fixture construction failed." }
    $fixtureOutput | Out-File -LiteralPath (Join-Path $runDirectory "fixture-console.json") -Encoding UTF8
    $workspace = Join-Path -Path $runDirectory -ChildPath "workspace"
    $portalArguments = @(
        "-m", "music_mastering_tools", "gui", "--workspace",
        ('"{0}"' -f $workspace), "--no-browser", "--write-ready", ('"{0}"' -f $readyPath)
    )
    $portalProcess = Start-Process -FilePath $python -ArgumentList $portalArguments `
        -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runDirectory "portal-stdout.log") `
        -RedirectStandardError (Join-Path $runDirectory "portal-stderr.log")
    $deadline = [System.DateTimeOffset]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $readyPath -PathType Leaf)) {
        if ($portalProcess.HasExited) { throw "Portal exited before readiness; inspect portal-stderr.log." }
        if ([System.DateTimeOffset]::UtcNow -ge $deadline) { throw "Portal readiness timed out." }
        Start-Sleep -Milliseconds 100
    }
    $ready = Get-Content -LiteralPath $readyPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $origin = [string]$ready.origin
    if ([string]$ready.url -notmatch "[?&]token=([^&#]+)") {
        throw "Portal readiness did not include its private launch token."
    }
    $token = [System.Uri]::UnescapeDataString($Matches[1])
    Write-ToolLog -Level "INFO" -Message (
        "Auditing populated Master, Library, Activity, A/B, keyboard, zoom and forced colors."
    )
    & node (Join-Path $PSScriptRoot "portal_browser_audit.mjs") $BrowserPath $readyPath $runDirectory
    if ($LASTEXITCODE -ne 0) { throw "Populated browser audit failed; inspect browser-audit.json and browser.log." }
    $passed = $true
}
finally {
    if ($null -ne $portalProcess -and -not $portalProcess.HasExited) {
        if ($null -ne $token -and $null -ne $origin) {
            try {
                Invoke-RestMethod -Method Post -Uri ("$origin/api/shutdown") `
                    -Headers @{ "X-MMT-Token" = $token; "Origin" = $origin } `
                    -ContentType "application/json" -Body "{}" | Out-Null
            }
            catch {
                Write-ToolLog -Level "WARN" -Message "Graceful shutdown failed; stopping isolated smoke portal."
            }
        }
        if (-not $portalProcess.WaitForExit(10000)) {
            Stop-Process -Id $portalProcess.Id -Force
        }
    }
    if ($null -ne $token) {
        foreach ($name in @("portal-ready.json", "portal-stdout.log", "portal-stderr.log")) {
            $path = Join-Path $runDirectory $name
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $content = (Get-Content -LiteralPath $path -Raw -Encoding UTF8).Replace($token, "[REDACTED]")
                [System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($false))
            }
        }
    }
}

if ($passed) {
    & node (Join-Path $PSScriptRoot "summarize_portal_smoke.mjs") $runDirectory
    if ($LASTEXITCODE -ne 0) { throw "Populated smoke summary failed." }
    Write-ToolLog -Level "INFO" -Message "Populated browser smoke passed. Evidence: $runDirectory"
}
