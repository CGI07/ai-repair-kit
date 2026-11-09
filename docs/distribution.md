# Windows distribution

## Source and candidate builds

AI Repair Kit source is licensed under GPL-3.0-only. It uses PySide6 Essentials and Shiboken. The Windows build includes the Python runtime, SQLite, runtime libraries and other files collected by PyInstaller. Qt for Python offers open-source licensing options. PyInstaller's bundling exception leaves the obligations of the bundled dependencies in effect. [Qt licensing](https://doc.qt.io/qt-6/licensing.html) [Qt for Python licenses](https://doc.qt.io/qtforpython-6/licenses.html) [PyInstaller license](https://pyinstaller.org/en/stable/license.html)

Windows builds are unsigned. The package script includes the GPL project license, the CPython license, PyInstaller's license, Qt library notices and matching upstream source checksums. Release assets must provide the corresponding source materials for the shipped Qt libraries. The installed PySide6 Essentials and Shiboken 6.11.3 license folders alone do not provide the complete open-source notices.

The releases provide Qt 6.11.3 corresponding source archives alongside the Windows ZIP. Check each bundle against its pinned dependencies and corresponding-source materials before publishing.

## Windows prerelease packaging

Every public release includes a tested Windows x64 executable ZIP, checksums, English installation and release notes, and corresponding source for the same tag. Build from a reviewed source revision. Hold publication if the executable or its required source and notices cannot be validated. The ZIP includes the Python runtime. Users obtain the required x64 Visual C++ Redistributable directly from Microsoft when needed. The [Windows guide](windows-quick-start.md) explains extraction and troubleshooting.

The candidate workflow can run manually or on a version tag. It uses hash-pinned Windows wheels, runs the source tests and prepares a downloadable artifact with the ZIP, SHA-256 and source revision. A build or test failure stops artifact creation. The maintainer reviews the candidate and approves publication separately. The [local build instructions](../CONTRIBUTING.md#build-the-windows-candidate) reproduce the workflow using pinned dependencies. Byte-identical ZIPs and EXEs across different runs are not guaranteed.

Cache operations require an explicit preview and approval. The rc4 Windows EXE was verified against the full registry loss case on an isolated Codex Desktop MSIX `26.1103.4239.0` profile with synthetic projects and chats. The tested binary and tag build may differ at the byte level. Record the release asset hash and verify its source matches the tag. [Verification scope](desktop-vm-verification.md#final-rc4-package-on-windows-11)

## Before publishing binaries

- Build from the reviewed release revision in a clean Windows x64 environment. Record Python, dependency versions, the build command, and file hashes.
- Inventory the shipped DLLs, Qt plugins, Python extension modules and other bundled components. Verify the applicable license, notices, and exact corresponding-source access or other redistribution conditions for each shipped component.
- Package the complete application directory with `AI-Repair-Kit.exe`, `_internal/`, applicable notices, and source-access information. Test the same package on Windows 10 and Windows 11. Decide how to document or sign the unsigned build and publish SHA-256 checksums.
- Exclude VM images, Codex installers, test profiles, dependency caches, local logs, and developer-only lab executables.
- Review the [changelog](../CHANGELOG.md), [verification record](desktop-vm-verification.md), and [security reporting instructions](../SECURITY.md). The maintainer approves publication separately.
