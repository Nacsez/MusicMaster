Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:ProjectRoot = [System.IO.Path]::GetFullPath(
    (Join-Path -Path $PSScriptRoot -ChildPath "..")
)

function Write-ToolLog {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("DEBUG", "INFO", "WARN", "ERROR")]
        [string]$Level,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $timestamp = [System.DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    Write-Host ("[{0}] [{1}] {2}" -f $timestamp, $Level, $Message)
}

function Get-ProjectRoot {
    [CmdletBinding()]
    param()

    return $script:ProjectRoot
}

function Resolve-ProjectPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [switch]$AllowProjectRoot
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        $resolved = [System.IO.Path]::GetFullPath($Path)
    }
    else {
        $resolved = [System.IO.Path]::GetFullPath(
            (Join-Path -Path $script:ProjectRoot -ChildPath $Path)
        )
    }

    $rootPrefix = $script:ProjectRoot.TrimEnd([char[]]"\/") +
        [System.IO.Path]::DirectorySeparatorChar
    $isRoot = $resolved.Equals(
        $script:ProjectRoot,
        [System.StringComparison]::OrdinalIgnoreCase
    )
    $isDescendant = $resolved.StartsWith(
        $rootPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )

    if (-not $isDescendant -and -not ($AllowProjectRoot -and $isRoot)) {
        throw "Refusing to operate outside the project workspace: $resolved"
    }

    # Reject existing reparse points in the requested path. Without this check,
    # a workspace-local junction or symlink could redirect a write elsewhere.
    $probe = $resolved
    while (
        $probe.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase) -and
        -not $probe.Equals($script:ProjectRoot, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        if (Test-Path -LiteralPath $probe) {
            $item = Get-Item -Force -LiteralPath $probe
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing to operate through a workspace reparse point: $probe"
            }
        }
        $probe = [System.IO.Path]::GetDirectoryName($probe)
    }

    return $resolved
}

function Get-ProjectPython {
    [CmdletBinding()]
    param(
        [switch]$RequireVirtualEnvironment
    )

    $candidates = @(
        (Join-Path -Path $script:ProjectRoot -ChildPath ".venv\Scripts\python.exe"),
        (Join-Path -Path $script:ProjectRoot -ChildPath ".venv/bin/python")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            Write-ToolLog -Level "DEBUG" -Message "Using project interpreter: $candidate"
            return $candidate
        }
    }

    if ($RequireVirtualEnvironment) {
        throw "No project interpreter was found. Run scripts/Bootstrap.ps1 first."
    }

    $systemPythonCandidates = @(
        Get-Command -Name "python" -CommandType Application -ErrorAction SilentlyContinue
    )
    if ($systemPythonCandidates.Count -eq 0) {
        throw "Python was not found. Install Python 3.11 or run from an activated environment."
    }
    $systemPython = $systemPythonCandidates[0]

    Write-ToolLog -Level "WARN" -Message (
        "No .venv interpreter was found; using system Python at {0}" -f $systemPython.Source
    )
    return $systemPython.Source
}

function Invoke-CheckedCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$Arguments
    )

    $displayArguments = $Arguments | ForEach-Object {
        if ($_ -match "\s") {
            '"{0}"' -f $_
        }
        else {
            $_
        }
    }
    Write-ToolLog -Level "DEBUG" -Message (
        "Running: {0} {1}" -f $Executable, ($displayArguments -join " ")
    )

    & $Executable @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $Executable"
    }
}

function Assert-PythonVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Python
    )

    $probe = @'
import platform
import sys

print(f'Python {platform.python_version()} ({sys.executable})')
if sys.version_info[:2] != (3, 11):
    print('This private baseline requires Python 3.11.x.', file=sys.stderr)
    raise SystemExit(1)
'@
    Invoke-CheckedCommand -Executable $Python -Arguments @("-c", $probe)
}
