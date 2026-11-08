param([string]$Python)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $project '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Create the project .venv with Python 3.11 before building.'
}

$Python = (Resolve-Path -LiteralPath $Python).Path
Push-Location -LiteralPath $project
try {
    & $python -c 'import struct, sys; sys.exit(0 if sys.platform == "win32" and sys.version_info[:2] == (3, 11) and struct.calcsize("P") == 8 else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Windows with 64-bit Python 3.11 is required.' }
    foreach ($directory in @('build', 'build\pyinstaller', 'build\pyinstaller-cache', 'build\sqlite-runtime', 'dist')) {
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
            --exclude-module ssl --exclude-module _ssl --exclude-module _hashlib --hidden-import _sha256 `
            --add-data "$project\ai_repair_kit\translations\*.qm;ai_repair_kit/translations" `
            --add-data "$project\LICENSE;." scripts\windows_entry.py
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
    } finally {
        $env:PYINSTALLER_CONFIG_DIR = $oldCache
        $env:PATH = $oldPath
    }
    # This local Widgets app needs neither Mesa OpenGL nor the optional Qt TLS providers.
    $internal = 'dist\AI-Repair-Kit\_internal'
    & $Python scripts\prepare_windows_sqlite.py (Join-Path $internal 'sqlite3.dll')
    if ($LASTEXITCODE -ne 0) { throw 'Could not stage the pinned SQLite runtime.' }
    $optionalFiles = @(
        'PySide6\opengl32sw.dll',
        'PySide6\plugins\tls\qcertonlybackend.dll',
        'PySide6\plugins\tls\qopensslbackend.dll',
        'PySide6\plugins\tls\qschannelbackend.dll'
    )
    foreach ($relative in $optionalFiles) {
        $file = Join-Path $internal $relative
        if (Test-Path -LiteralPath $file -PathType Leaf) { Remove-Item -LiteralPath $file }
    }
    # Use the system-wide Microsoft x64 redistributable. Keep vendor CRT copies out of the ZIP.
    $vcRuntime = '^(msvcp140|vcruntime140|concrt140|vcomp140|vcamp140|vccorlib140)(_[a-z0-9_]+)?\.dll$'
    $crtFiles = Get-ChildItem -LiteralPath $internal -Recurse -File | Where-Object Name -Match $vcRuntime
    foreach ($file in $crtFiles) { Remove-Item -LiteralPath $file.FullName }
    $openssl = Get-ChildItem -LiteralPath $internal -Recurse -File |
        Where-Object Name -Match '^(libcrypto|libssl).*\.dll$'
    if ($openssl) { throw 'An unexpected OpenSSL binary was collected.' }
    & $Python -c "import _sha256, sys; sys.exit(0 if _sha256.__spec__.origin == 'built-in' else 1)"
    if ($LASTEXITCODE -ne 0 -and
        -not (Test-Path -LiteralPath (Join-Path $internal '_sha256.pyd') -PathType Leaf)) {
        throw 'The bundled SHA-256 implementation is missing.'
    }
    Copy-Item -LiteralPath LICENSE -Destination dist\AI-Repair-Kit\LICENSE
    Get-FileHash -Algorithm SHA256 -LiteralPath dist\AI-Repair-Kit\AI-Repair-Kit.exe
} finally {
    Pop-Location
}
