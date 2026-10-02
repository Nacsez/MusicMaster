[CmdletBinding()]
param(
    [string]$OutputDirectory = "artifacts\portal-gui-smoke",

    [string]$BrowserPath,

    [ValidateSet(
        "master-job",
        "catalog",
        "runs",
        "events",
        "diagnostics",
        "reference-sets"
    )]
    [string]$InitialTab = "master-job",

    [ValidateRange(800, 3840)]
    [int]$ViewportWidth = 1440,

    [ValidateRange(600, 2160)]
    [int]$ViewportHeight = 1000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path -Path $PSScriptRoot -ChildPath "_Common.ps1")

$projectRoot = Get-ProjectRoot
Set-Location -LiteralPath $projectRoot
$python = Get-ProjectPython
Assert-PythonVersion -Python $python

if ([string]::IsNullOrWhiteSpace($BrowserPath)) {
    $browserCandidates = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    )
    $BrowserPath = $browserCandidates |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($BrowserPath) -or
    -not (Test-Path -LiteralPath $BrowserPath -PathType Leaf)) {
    throw (
        "A Chromium browser was not found. Pass -BrowserPath with the full " +
        "path to Microsoft Edge or Google Chrome."
    )
}

$artifactRoot = Resolve-ProjectPath -Path $OutputDirectory
$timestamp = [System.DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
$runDirectory = Join-Path -Path $artifactRoot -ChildPath $timestamp
if (Test-Path -LiteralPath $runDirectory) {
    throw "Refusing to reuse a GUI smoke artifact directory: $runDirectory"
}
New-Item -ItemType Directory -Path $runDirectory | Out-Null

$workspace = Join-Path -Path $runDirectory -ChildPath "workspace"
$readyPath = Join-Path -Path $runDirectory -ChildPath "portal-ready.json"
$portalStdout = Join-Path -Path $runDirectory -ChildPath "portal-stdout.log"
$portalStderr = Join-Path -Path $runDirectory -ChildPath "portal-stderr.log"
$browserLog = Join-Path -Path $runDirectory -ChildPath "browser.log"
$domPath = Join-Path -Path $runDirectory -ChildPath "rendered-dom.html"
$screenshotPath = Join-Path -Path $runDirectory -ChildPath "$InitialTab.png"
$screenshotProfile = Join-Path -Path $runDirectory -ChildPath "browser-profile-screenshot"
$domProfile = Join-Path -Path $runDirectory -ChildPath "browser-profile-dom"
$portalProcess = $null
$token = $null
$origin = $null

try {
    Write-ToolLog -Level "INFO" -Message "Starting an isolated private portal."
    $portalArguments = @(
        "-m",
        "music_mastering_tools",
        "gui",
        "--workspace",
        ('"{0}"' -f $workspace),
        "--no-browser",
        "--write-ready",
        ('"{0}"' -f $readyPath)
    )
    $portalProcess = Start-Process `
        -FilePath $python `
        -ArgumentList $portalArguments `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $portalStdout `
        -RedirectStandardError $portalStderr

    $deadline = [System.DateTimeOffset]::UtcNow.AddSeconds(20)
    while (-not (Test-Path -LiteralPath $readyPath -PathType Leaf)) {
        if ($portalProcess.HasExited) {
            throw (
                "The portal exited before reporting readiness. Review " +
                "$portalStderr"
            )
        }
        if ([System.DateTimeOffset]::UtcNow -ge $deadline) {
            throw "The portal did not report readiness within 20 seconds."
        }
        Start-Sleep -Milliseconds 100
    }

    $ready = Get-Content -LiteralPath $readyPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    $origin = [string]$ready.origin
    $readyUrl = [string]$ready.url
    if ($readyUrl -notmatch "[?&]token=([^&#]+)") {
        throw "The portal ready document did not contain its private launch token."
    }
    $token = [System.Uri]::UnescapeDataString($Matches[1])
    $launchUrl = "{0}#{1}" -f $readyUrl, $InitialTab

    Write-ToolLog -Level "INFO" -Message (
        "Rendering '$InitialTab' in headless Chromium at " +
        "$ViewportWidth x $ViewportHeight."
    )
    $commonBrowserArguments = @(
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        ("--window-size={0},{1}" -f $ViewportWidth, $ViewportHeight),
        "--virtual-time-budget=7000"
    )
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Chromium can emit benign diagnostic lines on stderr even when a
        # headless render succeeds. Preserve them without allowing PowerShell's
        # native-command adapter to turn them into terminating exceptions.
        $ErrorActionPreference = "Continue"
        & $BrowserPath @commonBrowserArguments `
            ("--user-data-dir={0}" -f $screenshotProfile) `
            ("--screenshot={0}" -f $screenshotPath) `
            $launchUrl 2>> $browserLog |
            Out-Null
        $screenshotExitCode = $LASTEXITCODE
        if ($screenshotExitCode -ne 0) {
            throw (
                "Chromium screenshot rendering failed with exit code " +
                "$screenshotExitCode."
            )
        }

        $domLines = @(
            & $BrowserPath @commonBrowserArguments `
                ("--user-data-dir={0}" -f $domProfile) `
                "--dump-dom" `
                $launchUrl 2>> $browserLog |
                ForEach-Object { [string]$_ }
        )
        $domExitCode = $LASTEXITCODE
        if ($domExitCode -ne 0) {
            throw "Chromium DOM rendering failed with exit code $domExitCode."
        }
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $domText = ($domLines -join [System.Environment]::NewLine).Replace(
        $token,
        "[REDACTED]"
    )
    [System.IO.File]::WriteAllText(
        $domPath,
        $domText,
        [System.Text.UTF8Encoding]::new($false)
    )

    if (-not (Test-Path -LiteralPath $screenshotPath -PathType Leaf) -or
        (Get-Item -LiteralPath $screenshotPath).Length -lt 10000) {
        throw "Chromium did not produce a non-empty GUI screenshot."
    }
    $requiredElementIds = @(
        "workspace-main",
        ("panel-{0}" -f $InitialTab),
        "app-menu",
        "readiness-inputs",
        "readiness-references",
        "readiness-destination",
        "readiness-output",
        "audition-picker-a",
        "audition-picker-b",
        "audition-swap-button",
        "audition-player",
        "global-status",
        "header-version"
    )
    $missingElementIds = @(
        foreach ($elementId in $requiredElementIds) {
            $idPattern = '\bid="{0}"' -f [regex]::Escape($elementId)
            if ($domText -notmatch $idPattern) {
                $elementId
            }
        }
    )
    $requestedPanelPattern = '<section[^>]*\bid="panel-{0}"[^>]*>' -f (
        [regex]::Escape($InitialTab)
    )
    $requestedPanel = [regex]::Match(
        $domText,
        $requestedPanelPattern,
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    $bodyReady = $domText -match '<body[^>]*\bdata-app-state="ready"'
    $requestedPanelVisible = (
        $requestedPanel.Success -and
        $requestedPanel.Value -notmatch '(?i)\shidden(?:\s|=|>)'
    )
    $versionPattern = '<span[^>]*\bid="header-version"[^>]*>([^<]+)</span>'
    $versionMatch = [regex]::Match(
        $domText,
        $versionPattern,
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    $versionInitialized = (
        $versionMatch.Success -and
        $versionMatch.Groups[1].Value -notmatch '(?i)loading'
    )
    if ($missingElementIds.Count -gt 0 -or
        -not $bodyReady -or
        -not $requestedPanelVisible -or
        -not $versionInitialized -or
        $domText -match "Portal initialization failed") {
        throw (
            "The rendered DOM did not reach the expected initialized GUI state. " +
            "Missing IDs: $($missingElementIds -join ', '). " +
            "Ready: $bodyReady; requested panel visible: " +
            "$requestedPanelVisible; version initialized: $versionInitialized. " +
            "Review $domPath and $browserLog."
        )
    }

    Write-ToolLog -Level "INFO" -Message "Requesting graceful portal shutdown."
    Invoke-RestMethod `
        -Method Post `
        -Uri ("{0}/api/shutdown" -f $origin) `
        -Headers @{
            "X-MMT-Token" = $token
            "Origin" = $origin
        } `
        -ContentType "application/json" `
        -Body "{}" | Out-Null
    if (-not $portalProcess.WaitForExit(10000)) {
        throw "The portal did not stop within 10 seconds of its shutdown response."
    }
    $portalProcess.WaitForExit()
    $portalProcess.Refresh()
    if ($null -ne $portalProcess.ExitCode -and $portalProcess.ExitCode -ne 0) {
        throw "The portal exited with code $($portalProcess.ExitCode)."
    }

    Write-ToolLog -Level "INFO" -Message (
        "Portal GUI smoke passed. Screenshot: $screenshotPath"
    )
    Write-ToolLog -Level "INFO" -Message "Redacted rendered DOM: $domPath"
}
finally {
    if ($null -ne $portalProcess -and -not $portalProcess.HasExited) {
        Write-ToolLog -Level "WARN" -Message (
            "Stopping the isolated smoke-test portal after an incomplete run."
        )
        Stop-Process -Id $portalProcess.Id -Force
        $portalProcess.WaitForExit()
    }
    if ([string]::IsNullOrWhiteSpace($token) -and
        (Test-Path -LiteralPath $readyPath -PathType Leaf)) {
        $readyText = Get-Content -LiteralPath $readyPath -Raw -Encoding UTF8
        if ($readyText -match "[?&]token=([^&`"\\]+)") {
            $token = [System.Uri]::UnescapeDataString($Matches[1])
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($token)) {
        foreach ($textArtifact in @(
            $readyPath,
            $portalStdout,
            $portalStderr,
            $browserLog
        )) {
            if (-not (Test-Path -LiteralPath $textArtifact -PathType Leaf)) {
                continue
            }
            try {
                $artifactText = Get-Content `
                    -LiteralPath $textArtifact `
                    -Raw `
                    -Encoding UTF8
                if ($null -eq $artifactText) {
                    $artifactText = ""
                }
                $artifactText = $artifactText.Replace($token, "[REDACTED]")
                [System.IO.File]::WriteAllText(
                    $textArtifact,
                    $artifactText,
                    [System.Text.UTF8Encoding]::new($false)
                )
            }
            catch {
                Write-ToolLog -Level "WARN" -Message (
                    "Could not redact a stopped smoke-session text artifact " +
                    "$textArtifact`: $($_.Exception.Message)"
                )
            }
        }
    }
    foreach ($browserProfile in @($screenshotProfile, $domProfile)) {
        if (-not (Test-Path -LiteralPath $browserProfile -PathType Container)) {
            continue
        }
        $resolvedRunDirectory = [System.IO.Path]::GetFullPath($runDirectory)
        $resolvedProfile = [System.IO.Path]::GetFullPath($browserProfile)
        $requiredPrefix = $resolvedRunDirectory.TrimEnd(
            [System.IO.Path]::DirectorySeparatorChar
        ) + [System.IO.Path]::DirectorySeparatorChar
        if (-not $resolvedProfile.StartsWith(
            $requiredPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Refusing to remove a browser profile outside the GUI smoke run."
        }
        $removedProfile = $false
        for ($attempt = 0; $attempt -lt 5 -and -not $removedProfile; $attempt++) {
            try {
                Remove-Item -LiteralPath $resolvedProfile -Recurse -Force
                $removedProfile = $true
            }
            catch {
                Start-Sleep -Milliseconds 200
            }
        }
        if (-not $removedProfile) {
            Write-ToolLog -Level "WARN" -Message (
                "The disposable headless-browser profile is still locked and " +
                "was retained for later cleanup: $resolvedProfile"
            )
        }
    }
}
