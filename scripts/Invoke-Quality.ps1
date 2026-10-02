[CmdletBinding()]
param(
    [switch]$SkipFormatCheck,

    [switch]$SkipTypeCheck
)

. (Join-Path -Path $PSScriptRoot -ChildPath "_Common.ps1")

$projectRoot = Get-ProjectRoot
Set-Location -LiteralPath $projectRoot
$python = Get-ProjectPython
Assert-PythonVersion -Python $python

Write-ToolLog -Level "INFO" -Message "Running static quality checks."

$ruffTargets = @("scripts")
if (Test-Path -LiteralPath (Join-Path -Path $projectRoot -ChildPath "packaging")) {
    $ruffTargets += "packaging"
}
if (Test-Path -LiteralPath (Join-Path -Path $projectRoot -ChildPath "src")) {
    $ruffTargets += "src"
}
if (Test-Path -LiteralPath (Join-Path -Path $projectRoot -ChildPath "tests")) {
    $ruffTargets += "tests"
}

Invoke-CheckedCommand -Executable $python -Arguments @("-m", "ruff", "--version")
Invoke-CheckedCommand -Executable $python -Arguments (
    @("-m", "ruff", "check") + $ruffTargets
)

if (-not $SkipFormatCheck) {
    Invoke-CheckedCommand -Executable $python -Arguments (
        @("-m", "ruff", "format", "--check") + $ruffTargets
    )
}
else {
    Write-ToolLog -Level "WARN" -Message "Ruff format verification was skipped by request."
}

if (-not $SkipTypeCheck) {
    $mypyTargets = @(
        "scripts/generate_wav_fixtures.py",
        "scripts/folder_navigation_smoke.py",
        "scripts/populate_portal_smoke.py",
        "scripts/executable_smoke.py",
        "packaging/collect_notices.py",
        "packaging/desktop_entry.py",
        "packaging/build_manifest.py"
    )
    $packagePath = Join-Path -Path $projectRoot -ChildPath "src\music_mastering_tools"
    if (Test-Path -LiteralPath $packagePath -PathType Container) {
        $mypyTargets = @("src/music_mastering_tools") + $mypyTargets
    }

    Invoke-CheckedCommand -Executable $python -Arguments @("-m", "mypy", "--version")
    Invoke-CheckedCommand -Executable $python -Arguments (
        @("-m", "mypy") + $mypyTargets
    )
}
else {
    Write-ToolLog -Level "WARN" -Message "Mypy verification was skipped by request."
}

Write-ToolLog -Level "INFO" -Message "All selected quality checks passed."
