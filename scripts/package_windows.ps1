param(
    [Parameter(Mandatory)][string]$OutputDirectory,
    [Parameter(Mandatory)][string]$NoticesDirectory
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$bundle = Join-Path $project 'dist\AI-Repair-Kit'
$exe = Join-Path $bundle 'AI-Repair-Kit.exe'
$internal = Join-Path $bundle '_internal'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf) -or
    -not (Test-Path -LiteralPath $internal -PathType Container)) {
    throw 'Build the Windows application first.'
}

$vcRuntime = '^(msvcp140|vcruntime140|concrt140|vcomp140|vcamp140|vccorlib140)(_[a-z0-9_]+)?\.dll$'
if (Get-ChildItem -LiteralPath $bundle -Recurse -File | Where-Object Name -Match $vcRuntime) {
    throw 'The Windows ZIP must not include Microsoft VC runtime DLLs.'
}
foreach ($required in @('README.md', 'QT-SOURCES.sha256', 'CPython-LICENSE.txt', 'PyInstaller-COPYING.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $NoticesDirectory $required) -PathType Leaf)) {
        throw "Missing dependency notice: $required"
    }
}
$noticeTarget = Join-Path $bundle 'third-party-notices'
if (Test-Path -LiteralPath $noticeTarget) { throw 'Package from a fresh build output.' }

$versionFile = Join-Path $project 'ai_repair_kit\__init__.py'
$versionLine = Select-String -LiteralPath $versionFile -Pattern '^__version__ = "([0-9][a-z0-9.]*)"$'
if ($versionLine.Count -ne 1) { throw 'Expected one valid application version.' }
$version = $versionLine.Matches[0].Groups[1].Value
$zipName = "AI-Repair-Kit-$version-windows-x64-candidate.zip"
$null = New-Item -ItemType Directory -Force -Path $OutputDirectory
$zip = Join-Path $OutputDirectory $zipName
$checksums = Join-Path $OutputDirectory 'SHA256SUMS.txt'
if ((Test-Path -LiteralPath $zip) -or (Test-Path -LiteralPath $checksums)) {
    throw 'Use an empty output directory for each build.'
}

Copy-Item -LiteralPath (Join-Path $project 'docs\windows-quick-start.md') -Destination (Join-Path $bundle 'START-HERE.md')
Copy-Item -LiteralPath $NoticesDirectory -Destination $noticeTarget -Recurse
Compress-Archive -LiteralPath $bundle -DestinationPath $zip
$zipHash = (Get-FileHash -LiteralPath $zip).Hash.ToLowerInvariant()
$exeHash = (Get-FileHash -LiteralPath $exe).Hash.ToLowerInvariant()
@("$zipHash  $zipName", "$exeHash  AI-Repair-Kit/AI-Repair-Kit.exe") |
    Set-Content -LiteralPath $checksums -Encoding ascii
Get-Content -LiteralPath $checksums
