# Windows distribution

## Source and candidate builds

AI Repair Kit source is licensed under GPL-3.0-only. It uses PySide6 Essentials and Shiboken. The Windows build includes the Python runtime, SQLite, runtime libraries and other files collected by PyInstaller. Qt for Python offers open-source licensing options. PyInstaller's bundling exception leaves the obligations of the bundled dependencies in effect. [Qt licensing](https://doc.qt.io/qt-6/licensing.html) [Qt for Python licenses](https://doc.qt.io/qtforpython-6/licenses.html) [PyInstaller license](https://pyinstaller.org/en/stable/license.html)

The locally built candidate is unsigned and used for verification. The presence of the project license in the build directory does not complete the dependency notices and corresponding-source review required before binary distribution.

The locally installed PySide6 Essentials and Shiboken 6.11.3 packages declare open-source license alternatives in their metadata, while their installed license directories list only `LicenseRef-Qt-Commercial.txt`. Review the exact artifacts and collect the applicable open-source notices and corresponding-source materials before shipping this build.

## First public source release

The source checkout can be published separately from the Windows executable after the maintainer reviews Git history metadata, the included files, and the release notes. Use the source installation instructions in the README. Keep local binaries and lab evidence outside GitHub release assets. The version-specific cache operations remain available through an explicit preview and approval. Their evidence is synthetic, and independent verification in the Codex Desktop UI remains open.

## Before publishing binaries

- Build from the reviewed release revision in a clean Windows x64 environment. Record Python, dependency versions, the build command, and file hashes.
- Inventory the shipped DLLs, Qt plugins, Python extension modules and other bundled components. Verify the applicable license, notices, and exact corresponding-source access or other redistribution conditions for each shipped component.
- Package the complete application directory with `AI-Repair-Kit.exe`, `_internal/`, applicable notices, and source-access information. Test the same package on Windows 10 and Windows 11. Decide how to document or sign the unsigned build and publish SHA-256 checksums.
- Exclude VM images, Codex installers, test profiles, dependency caches, local logs, and developer-only lab executables.
- Review the [changelog](../CHANGELOG.md), [verification record](desktop-vm-verification.md), and [security reporting instructions](../SECURITY.md). The maintainer approves publication separately.
