[CmdletBinding()]
param(
    [string]$OutputDirectory = "artifacts\smoke\fixtures",

    [switch]$FixturesOnly
)

. (Join-Path -Path $PSScriptRoot -ChildPath "_Common.ps1")

$projectRoot = Get-ProjectRoot
Set-Location -LiteralPath $projectRoot
$python = Get-ProjectPython
Assert-PythonVersion -Python $python

$outputPath = Resolve-ProjectPath -Path $OutputDirectory
$generatorPath = Resolve-ProjectPath -Path "scripts\generate_wav_fixtures.py"

Write-ToolLog -Level "INFO" -Message "Generating deterministic smoke-test audio."
Invoke-CheckedCommand -Executable $python -Arguments @(
    $generatorPath,
    "--output-dir",
    $outputPath,
    "--verbose"
)

Write-ToolLog -Level "INFO" -Message "Verifying fixture bytes and WAV metadata."
Invoke-CheckedCommand -Executable $python -Arguments @(
    $generatorPath,
    "--output-dir",
    $outputPath,
    "--check",
    "--verbose"
)

if ($FixturesOnly) {
    Write-ToolLog -Level "INFO" -Message (
        "Fixture-only smoke check passed. Package and DSP execution were intentionally skipped."
    )
    return
}

$importProbe = @'
from importlib import metadata

import matchering
import music_mastering_tools

project_version = metadata.version('music-mastering-tools')
matchering_version = getattr(matchering, '__version__', 'unknown')
print(f'music-mastering-tools={project_version}')
print(f'matchering={matchering_version}')
print(f'package={music_mastering_tools.__name__}')
'@

Write-ToolLog -Level "INFO" -Message "Checking installed project and Matchering imports."
Invoke-CheckedCommand -Executable $python -Arguments @("-c", $importProbe)

Write-ToolLog -Level "INFO" -Message "Running any registered pytest smoke cases."
& (Join-Path -Path $PSScriptRoot -ChildPath "Invoke-Tests.ps1") -Tier "Smoke"

Write-ToolLog -Level "INFO" -Message (
    "Smoke workflow passed: deterministic fixtures, package imports, audited " +
    "dry run, exact single-reference renders, and deterministic weighted " +
    "multi-reference renders."
)
