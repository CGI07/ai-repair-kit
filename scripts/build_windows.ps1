$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Create the project .venv with Python 3.11 before building.'
}

Push-Location -LiteralPath $project
try {
    & $python -c 'import struct, sys; sys.exit(0 if sys.platform == "win32" and sys.version_info[:2] == (3, 11) and struct.calcsize("P") == 8 else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Windows with 64-bit Python 3.11 is required.' }
    foreach ($directory in @('build', 'build\pyinstaller', 'build\pyinstaller-cache', 'dist')) {
        if ((Test-Path -LiteralPath $directory) -and
            ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Build paths cannot be links or junctions: $directory"
        }
    }
    if (Test-Path -LiteralPath 'dist\AI-Repair-Kit') {
        throw 'An AI-Repair-Kit output directory already exists. Move it before rebuilding.'
    }
    $work = Join-Path 'build\pyinstaller' ([guid]::NewGuid().ToString('N'))
    $oldCache = $env:PYINSTALLER_CONFIG_DIR
    $oldPath = $env:PATH
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $project 'build\pyinstaller-cache'
    $system = [Environment]::GetFolderPath('System')
    # Avoid collecting unrelated DLLs from other development tools on PATH.
    $env:PATH = "$system;$(Split-Path -Parent $system)"
    try {
        & $python -m PyInstaller --onedir --windowed --noupx --name AI-Repair-Kit `
            --paths $project --distpath dist --workpath (Join-Path $work 'work') --specpath $work `
            --add-data "$project\ai_repair_kit\translations\*.qm;ai_repair_kit/translations" `
            --add-data "$project\LICENSE;." scripts\windows_entry.py
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
    } finally {
        $env:PYINSTALLER_CONFIG_DIR = $oldCache
        $env:PATH = $oldPath
    }
    Copy-Item -LiteralPath LICENSE -Destination dist\AI-Repair-Kit\LICENSE
    Get-FileHash -Algorithm SHA256 -LiteralPath dist\AI-Repair-Kit\AI-Repair-Kit.exe
} finally {
    Pop-Location
}
