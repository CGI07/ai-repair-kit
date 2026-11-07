# Windows distribution

## Source and candidate builds

AI Repair Kit source is licensed under GPL-3.0-only. It uses PySide6 Essentials and Shiboken. The Windows build includes the Python runtime, SQLite, runtime libraries and other files collected by PyInstaller. Qt for Python offers open-source licensing options. PyInstaller's bundling exception leaves the obligations of the bundled dependencies in effect. [Qt licensing](https://doc.qt.io/qt-6/licensing.html) [Qt for Python licenses](https://doc.qt.io/qtforpython-6/licenses.html) [PyInstaller license](https://pyinstaller.org/en/stable/license.html)

The locally built candidate is unsigned and used for verification. The presence of the project license in the build directory does not complete the dependency notices and corresponding-source review required before binary distribution.

The locally installed PySide6 Essentials and Shiboken 6.11.3 packages declare open-source license alternatives in their metadata, while their installed license directories list only `LicenseRef-Qt-Commercial.txt`. Review the exact artifacts and collect the applicable open-source notices and corresponding-source materials before shipping this build.

## First Windows prerelease

Build from a reviewed source revision and ship the Windows x64 ZIP, checksums, English notes and corresponding source for the same tag. The ZIP includes the Python runtime. Users obtain the required x64 Visual C++ Redistributable directly from Microsoft when needed. The [Windows guide](windows-quick-start.md) explains extraction and troubleshooting.

The candidate workflow can run manually or on a version tag. It uses hash-pinned Windows wheels, runs the source tests and prepares a downloadable artifact with the ZIP, SHA-256 and source revision. A build or test failure stops artifact creation. The maintainer reviews the candidate and approves publication separately.

The version-specific cache operations require an explicit preview and approval. Current recovery evidence comes from synthetic fixtures. Independent verification in Codex Desktop remains open.

## Before publishing binaries

- Build from the reviewed release revision in a clean Windows x64 environment. Record Python, dependency versions, the build command, and file hashes.
- Inventory the shipped DLLs, Qt plugins, Python extension modules and other bundled components. Verify the applicable license, notices, and exact corresponding-source access or other redistribution conditions for each shipped component.
- Package the complete application directory with `AI-Repair-Kit.exe`, `_internal/`, applicable notices, and source-access information. Test the same package on Windows 10 and Windows 11. Decide how to document or sign the unsigned build and publish SHA-256 checksums.
- Exclude VM images, Codex installers, test profiles, dependency caches, local logs, and developer-only lab executables.
- Review the [changelog](../CHANGELOG.md), [verification record](desktop-vm-verification.md), and [security reporting instructions](../SECURITY.md). The maintainer approves publication separately.
