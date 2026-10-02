[CmdletBinding()]
param()

. (Join-Path -Path $PSScriptRoot -ChildPath "_Common.ps1")

$projectRoot = Get-ProjectRoot
Set-Location -LiteralPath $projectRoot
$python = Get-ProjectPython
Assert-PythonVersion -Python $python

Write-ToolLog -Level "INFO" -Message (
    "Running opt-in acceptance on the interactive Windows desktop. " +
    "The check opens isolated Explorer fixtures, verifies their actual " +
    "folder/selection state, and closes only its newly created test windows."
)
Invoke-CheckedCommand -Executable $python -Arguments @(
    (Join-Path -Path $PSScriptRoot -ChildPath "folder_navigation_smoke.py")
)
