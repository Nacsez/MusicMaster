[CmdletBinding()]
param(
    [switch]$CheckOnly,

    [switch]$SkipBootstrap,

    [switch]$Terminal,

    [switch]$NoBrowser,

    [switch]$NoPause
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path -Path $PSScriptRoot -ChildPath "_Common.ps1")

$launcherExitCode = 0
$transcriptStarted = $false
$transcriptPath = $null

function Test-ProjectEnvironment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Python
    )

    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        Write-ToolLog -Level "WARN" -Message (
            "The project interpreter does not exist: $Python"
        )
        return $false
    }

    try {
        Assert-PythonVersion -Python $Python | ForEach-Object {
            Write-Host $_
        }
        $importProbe = @'
from importlib.metadata import version; import matchering; import music_mastering_tools; print('Project package import passed (music-mastering-tools ' + music_mastering_tools.__version__ + ', Matchering ' + version('matchering') + ').')
'@
        & $Python -c $importProbe | ForEach-Object {
            Write-Host $_
        }
        $probeExitCode = $LASTEXITCODE
        if ($probeExitCode -ne 0) {
            Write-ToolLog -Level "WARN" -Message (
                "The project package import probe failed with exit code $probeExitCode."
            )
            return $false
        }
    }
    catch {
        Write-ToolLog -Level "WARN" -Message (
            "The project environment probe failed: {0}" -f $_.Exception.Message
        )
        return $false
    }

    return $true
}

try {
    $projectRoot = Get-ProjectRoot
    Set-Location -LiteralPath $projectRoot

    $privateWorkspace = Resolve-ProjectPath -Path "private-workspace"
    $logDirectory = Resolve-ProjectPath -Path "private-workspace\logs"
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

    $timestamp = [System.DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
    $transcriptPath = Join-Path -Path $logDirectory -ChildPath (
        "launcher-{0}-{1}.log" -f $timestamp, $PID
    )
    if (Test-Path -LiteralPath $transcriptPath) {
        throw "Refusing to overwrite an existing launcher transcript: $transcriptPath"
    }

    Start-Transcript -Path $transcriptPath -NoClobber | Out-Null
    $transcriptStarted = $true

    Write-ToolLog -Level "INFO" -Message "Music Mastering Tools launcher started."
    Write-ToolLog -Level "INFO" -Message "Project root: $projectRoot"
    Write-ToolLog -Level "INFO" -Message "Private workspace: $privateWorkspace"
    Write-ToolLog -Level "INFO" -Message "Launch transcript: $transcriptPath"

    $projectPython = Join-Path -Path $projectRoot -ChildPath ".venv\Scripts\python.exe"
    $environmentReady = Test-ProjectEnvironment -Python $projectPython

    if (-not $environmentReady) {
        if ($SkipBootstrap) {
            throw (
                "The private project environment is missing or unusable, and " +
                "-SkipBootstrap prevents repair. Run " +
                "'.\scripts\Bootstrap.ps1 -InstallDependencies -DependencySet Base' " +
                "from the project root, then retry."
            )
        }

        Write-ToolLog -Level "WARN" -Message (
            "The private Python environment must be created or repaired before launch."
        )
        $confirmation = Read-Host (
            "Type YES to install the base dependencies into .venv; " +
            "any other response cancels"
        )
        if ($confirmation -cne "YES") {
            throw "Bootstrap was declined. No dependency installation was started."
        }

        $bootstrapScript = Resolve-ProjectPath -Path "scripts\Bootstrap.ps1"
        Write-ToolLog -Level "INFO" -Message (
            "Running the approved base-environment bootstrap."
        )
        & $bootstrapScript -InstallDependencies -DependencySet Base

        if (-not (Test-ProjectEnvironment -Python $projectPython)) {
            throw (
                "Bootstrap returned, but the project environment still failed its " +
                "version or import probe. Review the transcript before retrying."
            )
        }
    }

    Write-ToolLog -Level "INFO" -Message (
        "Running strict mastering dependency diagnostics."
    )
    & $projectPython -m music_mastering_tools doctor --strict-mastering
    $doctorExitCode = $LASTEXITCODE
    if ($doctorExitCode -ne 0) {
        $launcherExitCode = $doctorExitCode
        throw (
            "Strict mastering diagnostics failed with exit code $doctorExitCode. " +
            "The workbench was not launched."
        )
    }
    Write-ToolLog -Level "INFO" -Message "Strict mastering diagnostics passed."

    if ($CheckOnly) {
        Write-ToolLog -Level "INFO" -Message (
            "Check-only launch verification completed successfully; " +
            "the graphical portal was not started."
        )
    }
    elseif ($Terminal) {
        Write-ToolLog -Level "INFO" -Message (
            "Starting the explicit terminal fallback workbench."
        )
        $terminalArguments = @(
            "-m",
            "music_mastering_tools",
            "workbench",
            "--workspace",
            $privateWorkspace
        )
        & $projectPython @terminalArguments
        $workbenchExitCode = $LASTEXITCODE
        if ($workbenchExitCode -ne 0) {
            $launcherExitCode = $workbenchExitCode
            throw "The terminal workbench exited with code $workbenchExitCode."
        }
        Write-ToolLog -Level "INFO" -Message "Terminal workbench exited normally."
    }
    else {
        Write-ToolLog -Level "INFO" -Message (
            "Starting the private localhost graphical portal."
        )
        $portalArguments = @(
            "-m",
            "music_mastering_tools",
            "gui",
            "--workspace",
            $privateWorkspace
        )
        if ($NoBrowser) {
            $portalArguments += "--no-browser"
        }
        if ($transcriptStarted) {
            Write-ToolLog -Level "INFO" -Message (
                "Closing the launcher transcript before portal handoff so a " +
                "private fallback launch URL cannot be retained. The portal " +
                "writes its own token-redacted log under the workspace log directory."
            )
            Stop-Transcript | Out-Null
            $transcriptStarted = $false
        }
        & $projectPython @portalArguments
        $portalExitCode = $LASTEXITCODE
        if ($portalExitCode -ne 0) {
            $launcherExitCode = $portalExitCode
            throw "The graphical portal exited with code $portalExitCode."
        }
        Write-ToolLog -Level "INFO" -Message "Graphical portal exited normally."
    }
}
catch {
    if ($launcherExitCode -eq 0) {
        $launcherExitCode = 1
    }
    Write-ToolLog -Level "ERROR" -Message $_.Exception.Message
    if ($null -ne $transcriptPath) {
        Write-ToolLog -Level "ERROR" -Message "Launch transcript: $transcriptPath"
    }

    if (-not $NoPause) {
        try {
            Read-Host "Press Enter to close this window" | Out-Null
        }
        catch {
            Write-ToolLog -Level "WARN" -Message (
                "The launcher could not wait for keyboard input: {0}" -f
                $_.Exception.Message
            )
        }
    }
}
finally {
    if ($transcriptStarted) {
        try {
            Stop-Transcript | Out-Null
        }
        catch {
            Write-ToolLog -Level "ERROR" -Message (
                "Could not close the launcher transcript cleanly: {0}" -f
                $_.Exception.Message
            )
            if ($launcherExitCode -eq 0) {
                $launcherExitCode = 1
            }
        }
    }
}

exit $launcherExitCode
