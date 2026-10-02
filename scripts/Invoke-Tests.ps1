[CmdletBinding()]
param(
    [ValidateSet("All", "Unit", "Integration", "Regression", "Smoke")]
    [string]$Tier = "All",

    [switch]$Coverage,

    [string[]]$AdditionalArguments = @()
)

. (Join-Path -Path $PSScriptRoot -ChildPath "_Common.ps1")

$projectRoot = Get-ProjectRoot
Set-Location -LiteralPath $projectRoot
$python = Get-ProjectPython
Assert-PythonVersion -Python $python

$testsPath = Join-Path -Path $projectRoot -ChildPath "tests"
$testFiles = @()
if (Test-Path -LiteralPath $testsPath -PathType Container) {
    $testFiles = @(
        Get-ChildItem -LiteralPath $testsPath -Recurse -File |
            Where-Object {
                $_.Name -like "test_*.py" -or $_.Name -like "*_test.py"
            }
    )
}

if ($testFiles.Count -eq 0) {
    Write-ToolLog -Level "WARN" -Message (
        "No Python test modules exist yet. The documented test harness is ready, " +
        "but no test result is being claimed."
    )
    return
}

$artifactDirectory = Resolve-ProjectPath -Path "artifacts\tests"
New-Item -ItemType Directory -Path $artifactDirectory -Force | Out-Null
$tierName = $Tier.ToLowerInvariant()
$runId = "{0}-{1}" -f (
    [System.DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
), $PID
$pytestLog = Join-Path -Path $artifactDirectory -ChildPath (
    "pytest-{0}-{1}.log" -f $tierName, $runId
)
$junitReport = Join-Path -Path $artifactDirectory -ChildPath (
    "junit-{0}-{1}.xml" -f $tierName, $runId
)

$pytestArguments = @(
    "-m",
    "pytest",
    "--log-file",
    $pytestLog,
    "--log-file-level",
    "DEBUG",
    "--junitxml",
    $junitReport
)

if ($Tier -ne "All") {
    $pytestArguments += @("-m", $tierName)
}

if ($Coverage) {
    $coverageDirectory = Resolve-ProjectPath -Path "artifacts\coverage"
    New-Item -ItemType Directory -Path $coverageDirectory -Force | Out-Null
    $coverageData = Join-Path -Path $coverageDirectory -ChildPath ".coverage-${runId}"
    $coverageXml = Join-Path -Path $coverageDirectory -ChildPath "coverage-${runId}.xml"
    $pytestArguments += @(
        "--cov=music_mastering_tools",
        "--cov-branch",
        "--cov-report=term-missing",
        "--cov-report=xml:$coverageXml"
    )
}

$pytestArguments += $AdditionalArguments

Write-ToolLog -Level "INFO" -Message (
    "Running the '$Tier' test tier across $($testFiles.Count) discovered module(s)."
)
if ($Coverage) {
    $previousCoverageFile = $env:COVERAGE_FILE
    try {
        $env:COVERAGE_FILE = $coverageData
        Invoke-CheckedCommand -Executable $python -Arguments $pytestArguments
    }
    finally {
        if ($null -eq $previousCoverageFile) {
            Remove-Item -LiteralPath "Env:\COVERAGE_FILE" -ErrorAction SilentlyContinue
        }
        else {
            $env:COVERAGE_FILE = $previousCoverageFile
        }
    }
}
else {
    Invoke-CheckedCommand -Executable $python -Arguments $pytestArguments
}
Write-ToolLog -Level "INFO" -Message (
    "Tests passed. Detailed log: $pytestLog; JUnit report: $junitReport"
)
