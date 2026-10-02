[CmdletBinding()]
param(
    [switch]$TrackedOnly
)

. (Join-Path -Path $PSScriptRoot -ChildPath "_Common.ps1")

$projectRoot = Get-ProjectRoot
$gitSafeDirectory = $projectRoot.Replace("\", "/")
$gitArguments = @(
    "-c", "safe.directory=$gitSafeDirectory",
    "-c", "core.fsmonitor=false",
    "-c", "core.quotepath=false",
    "-C", $projectRoot,
    "ls-files", "--cached"
)
if (-not $TrackedOnly) {
    $gitArguments += @("--others", "--exclude-standard")
}
$candidateFiles = @(& git @gitArguments | Sort-Object -Unique)
if ($LASTEXITCODE -ne 0) {
    throw "Git could not enumerate the publication candidate."
}

$allowedRoots = @(
    "src", "tests", "scripts", "docs", "configs", "schemas", "requirements",
    "packaging", ".github"
)
$allowedRootFiles = @(
    ".editorconfig", ".gitattributes", ".gitignore", "LICENSE", "NOTICE",
    "README.md", "pyproject.toml", "Launch-Music-Mastering-Tools.cmd"
)
$privateExtensions = @(
    ".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aif", ".aiff", ".wma",
    ".sqlite", ".sqlite3", ".db", ".pfx", ".p12", ".pem", ".exe", ".zip"
)
$textExtensions = @(
    ".py", ".ps1", ".cmd", ".bat", ".md", ".txt", ".json", ".toml",
    ".yaml", ".yml", ".html", ".css", ".js", ".spec"
)
$secretPattern = (
    "-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----" +
    "|(?:github_pat_|ghp_|gho_)[A-Za-z0-9_]{20,}" +
    "|AKIA[A-Z0-9]{16}" +
    "|sk-[A-Za-z0-9_-]{32,}"
)
$personalPathPattern = '[A-Za-z]:[\\/]Users[\\/](?!Public(?:[\\/]|\b)|USER(?:[\\/]|\b)|USERNAME(?:[\\/]|\b))[^\\/\s"<>]+'
$findings = [System.Collections.Generic.List[object]]::new()

foreach ($relativePath in $candidateFiles) {
    $normalized = $relativePath.Replace("\", "/")
    $firstComponent = $normalized.Split("/")[0]
    $extension = [System.IO.Path]::GetExtension($normalized).ToLowerInvariant()
    $allowed = (
        $allowedRoots -contains $firstComponent -or
        $allowedRootFiles -contains $normalized
    )
    if (-not $allowed) {
        $findings.Add(@{ path = $normalized; rule = "outside-source-allowlist" })
    }
    if ($privateExtensions -contains $extension) {
        $findings.Add(@{ path = $normalized; rule = "private-media-or-generated-binary" })
    }
    if ($normalized -match '(?i)(^|/)(private-workspace|artifacts|logs|outputs|\.venv|\.aws|\.codex|\.agents)(/|$)|(^|/)\.env($|\.)|\.ready\.json$|\.sqlite3-(wal|shm)$') {
        $findings.Add(@{ path = $normalized; rule = "private-runtime-or-configuration" })
    }

    $absolutePath = Resolve-ProjectPath -Path $relativePath
    if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
        continue
    }
    if ($textExtensions -contains $extension -or $allowedRootFiles -contains $normalized) {
        $contents = [System.IO.File]::ReadAllText($absolutePath)
        if ($contents -match $secretPattern) {
            $findings.Add(@{ path = $normalized; rule = "credential-pattern" })
        }
        if ($contents -match $personalPathPattern) {
            $findings.Add(@{ path = $normalized; rule = "personal-windows-path" })
        }
    }
}

$artifactDirectory = Resolve-ProjectPath -Path "artifacts\release-audit"
New-Item -ItemType Directory -Path $artifactDirectory -Force | Out-Null
$reportPath = Join-Path -Path $artifactDirectory -ChildPath (
    "publication-audit-{0}-{1}.json" -f
    [System.DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ"), $PID
)
$report = [ordered]@{
    schema_version = 1
    checked_at_utc = [System.DateTimeOffset]::UtcNow.ToString("o")
    tracked_only = [bool]$TrackedOnly
    candidate_count = $candidateFiles.Count
    finding_count = $findings.Count
    findings = @($findings.ToArray())
    candidate_files = @($candidateFiles)
}
[System.IO.File]::WriteAllText(
    $reportPath,
    ($report | ConvertTo-Json -Depth 6),
    [System.Text.UTF8Encoding]::new($false)
)
Write-ToolLog -Level "INFO" -Message (
    "Publication candidate: $($candidateFiles.Count) files; " +
    "$($findings.Count) findings. Report: $reportPath"
)
foreach ($finding in $findings) {
    Write-ToolLog -Level "ERROR" -Message "$($finding.rule): $($finding.path)"
}
if ($findings.Count -gt 0) {
    throw "Publication audit failed. Inspect the report; no files were staged or uploaded."
}
if ($candidateFiles.Count -eq 0) {
    Write-ToolLog -Level "WARN" -Message "No candidate files were found; this is not a publication approval."
}
Write-ToolLog -Level "INFO" -Message "Read-only publication audit passed."
