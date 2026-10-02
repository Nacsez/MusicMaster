[CmdletBinding()]
param(
    [string]$PythonPath,
    [switch]$InstallBuildTools
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
Set-Location -LiteralPath $projectRoot
$releaseDirectory = Join-Path $projectRoot "dist\windows"
$buildDirectory = Join-Path $projectRoot "build\windows"
$stamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
$artifactDirectory = Join-Path $projectRoot "artifacts\windows-build\$stamp"
New-Item -ItemType Directory -Path $releaseDirectory,$buildDirectory,$artifactDirectory -Force | Out-Null
Start-Transcript -Path (Join-Path $artifactDirectory "build.log") -NoClobber | Out-Null
try {
    & $PythonPath -c "import sys, struct; assert sys.platform == 'win32' and sys.version_info[:3] == (3,11,7) and struct.calcsize('P') == 8, 'Validated build requires Windows x64 and CPython 3.11.7; update native provenance when upgrading'"
    if ($LASTEXITCODE -ne 0) { throw "Unsupported build interpreter." }
    if ($InstallBuildTools) {
        & $PythonPath -m pip install -r requirements/windows-build.txt
        if ($LASTEXITCODE -ne 0) { throw "Build tool installation failed." }
    }
    # Refresh version/license metadata from this exact source without network
    # resolution or changing the pinned runtime dependency environment.
    & $PythonPath -m pip install --no-deps --no-build-isolation --disable-pip-version-check -e .
    if ($LASTEXITCODE -ne 0) { throw "Application metadata preparation failed." }
    & $PythonPath -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Dependency consistency check failed." }
    & $PythonPath packaging/collect_notices.py --destination $releaseDirectory
    if ($LASTEXITCODE -ne 0) { throw "Runtime inventory or notice collection failed." }
    Copy-Item -LiteralPath (Join-Path $projectRoot "packaging\START-HERE.txt") `
        -Destination (Join-Path $releaseDirectory "START-HERE.txt") -Force
    # Keep PyInstaller's mutable cache within this workspace, not the user profile.
    $previousCache = $env:PYINSTALLER_CONFIG_DIR
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $buildDirectory "cache"
    try {
        & $PythonPath -m PyInstaller --noconfirm --distpath $releaseDirectory --workpath $buildDirectory packaging/MusicMasteringTools.spec
        if ($LASTEXITCODE -ne 0) { throw "Executable build failed." }
    }
    finally {
        $env:PYINSTALLER_CONFIG_DIR = $previousCache
    }
    & $PythonPath -m pip freeze --all | Set-Content -LiteralPath (Join-Path $artifactDirectory "environment.txt") -Encoding UTF8
    & $PythonPath packaging/build_manifest.py --executable (Join-Path $releaseDirectory "MusicMasteringTools.exe") --destination (Join-Path $releaseDirectory "build-manifest.json")
    if ($LASTEXITCODE -ne 0) { throw "Build correspondence record failed." }
    $files = Get-ChildItem -LiteralPath $releaseDirectory -File -Recurse |
        Where-Object { $_.Name -ne "SHA256SUMS.txt" } | Sort-Object FullName
    $checksums = foreach ($file in $files) {
        $relative = $file.FullName.Substring($releaseDirectory.Length + 1).Replace('\','/')
        "{0}  {1}" -f (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant(),$relative
    }
    $checksums | Set-Content -LiteralPath (Join-Path $releaseDirectory "SHA256SUMS.txt") -Encoding ASCII
    Write-Host "Executable: $releaseDirectory\MusicMasteringTools.exe"
    Write-Host "Build evidence: $artifactDirectory"
}
finally {
    Stop-Transcript | Out-Null
}
