[CmdletBinding()]
param(
    [string]$VirtualEnvironment = ".venv",

    [ValidateSet("Base", "Dev")]
    [string]$DependencySet = "Dev",

    [string]$PythonCommand = "python",

    [switch]$InstallDependencies,

    [switch]$Offline,

    [string]$Wheelhouse
)

. (Join-Path -Path $PSScriptRoot -ChildPath "_Common.ps1")

$projectRoot = Get-ProjectRoot
Set-Location -LiteralPath $projectRoot

Write-ToolLog -Level "INFO" -Message "Bootstrapping Music Mastering Tools at $projectRoot"
$venvPath = Resolve-ProjectPath -Path $VirtualEnvironment

if (Test-Path -LiteralPath $venvPath) {
    if (-not (Test-Path -LiteralPath $venvPath -PathType Container)) {
        throw "The requested virtual-environment path is not a directory: $venvPath"
    }
    Write-ToolLog -Level "INFO" -Message "Reusing existing environment directory: $venvPath"
}
else {
    $systemPythonCandidates = @(
        Get-Command -Name $PythonCommand -CommandType Application -ErrorAction SilentlyContinue
    )
    if ($systemPythonCandidates.Count -eq 0) {
        throw "Python command '$PythonCommand' was not found."
    }
    $systemPython = $systemPythonCandidates[0]

    Assert-PythonVersion -Python $systemPython.Source
    Write-ToolLog -Level "INFO" -Message "Creating virtual environment: $venvPath"
    Invoke-CheckedCommand -Executable $systemPython.Source -Arguments @("-m", "venv", $venvPath)
}

$venvPythonCandidates = @(
    (Join-Path -Path $venvPath -ChildPath "Scripts\python.exe"),
    (Join-Path -Path $venvPath -ChildPath "bin/python")
)
$venvPython = $null
foreach ($candidate in $venvPythonCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $venvPython = $candidate
        break
    }
}
if ($null -eq $venvPython) {
    throw (
        "The environment directory exists but contains no Python interpreter. " +
        "It was preserved; choose a different path or repair it manually: $venvPath"
    )
}

Assert-PythonVersion -Python $venvPython
Invoke-CheckedCommand -Executable $venvPython -Arguments @("-m", "pip", "--version")

if (-not $InstallDependencies) {
    if ($Offline -or $Wheelhouse) {
        Write-ToolLog -Level "WARN" -Message (
            "-Offline and -Wheelhouse apply only when -InstallDependencies is selected."
        )
    }
    Write-ToolLog -Level "INFO" -Message (
        "Environment is ready. Dependencies were not installed; rerun with " +
        "-InstallDependencies when package access is available."
    )
    return
}

$constraintsPath = Resolve-ProjectPath -Path "requirements\constraints.txt"
$editableRequirement = $projectRoot
if ($DependencySet -eq "Dev") {
    $editableRequirement = "{0}[dev]" -f $projectRoot
}

$pipArguments = @(
    "-m",
    "pip",
    "install",
    "--disable-pip-version-check",
    "--constraint",
    $constraintsPath
)

if ($Offline) {
    $pipArguments += "--no-index"
}

if ($Wheelhouse) {
    $wheelhousePath = Resolve-ProjectPath -Path $Wheelhouse
    if (-not (Test-Path -LiteralPath $wheelhousePath -PathType Container)) {
        throw "Wheelhouse directory not found: $wheelhousePath"
    }
    $pipArguments += @("--find-links", $wheelhousePath)
}

$pipArguments += @("--editable", $editableRequirement)
Write-ToolLog -Level "INFO" -Message "Installing the $DependencySet dependency set."
Invoke-CheckedCommand -Executable $venvPython -Arguments $pipArguments

$environmentArtifactDirectory = Resolve-ProjectPath -Path "artifacts\environment"
New-Item -ItemType Directory -Path $environmentArtifactDirectory -Force | Out-Null
$timestamp = [System.DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
$freezePath = Join-Path -Path $environmentArtifactDirectory -ChildPath (
    "pip-freeze-{0}-{1}.txt" -f $timestamp, $PID
)
if (Test-Path -LiteralPath $freezePath) {
    throw "Refusing to overwrite an existing environment record: $freezePath"
}

Write-ToolLog -Level "INFO" -Message "Recording the resolved environment at $freezePath"
$freezeLines = & $venvPython -m pip freeze --all
$freezeExitCode = $LASTEXITCODE
if ($freezeExitCode -ne 0) {
    throw "pip freeze failed with exit code $freezeExitCode."
}
[System.IO.File]::WriteAllLines(
    $freezePath,
    [string[]]$freezeLines,
    [System.Text.UTF8Encoding]::new($false)
)

Write-ToolLog -Level "INFO" -Message "Bootstrap completed successfully."
