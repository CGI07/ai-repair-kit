# Third-party components and source access

AI Repair Kit is distributed under GPL-3.0-only. The Windows package contains Python, PyInstaller's bootloader, PySide6 Essentials, Shiboken and the Qt shared libraries and plugins used by the program. The external Microsoft Visual C++ Redistributable is available directly from Microsoft.

The application uses the LGPL-3.0 option for the Qt libraries and Python bindings where available. This folder includes upstream license texts, copyright attributions and archive hashes. Some upstream notices refer to optional components that are outside this package. The LGPL permits using compatible modified versions of the libraries and reverse engineering for debugging modifications to those libraries. The shared libraries and plugins are kept as separate files under _internal. Preserve a copy of the original package and keep replacement libraries compatible with the corresponding Qt and binding versions.

QT-SOURCES.sha256 identifies the official Qt 6.11.3 source archives by file name, URL and SHA-256. The PySide6 source archive includes both bindings and Shiboken, plus its build scripts. The wheels declare LGPL-3.0-only, GPL-2.0-only and GPL-3.0-only alternatives. The release maintainer must confirm any wheel-specific patches and necessary build material and provide the corresponding source with equivalent access when distributing a binary.

The CPython license file includes the Python Software Foundation terms, bundled third-party notices and conditions for the Windows build. PyInstaller's COPYING includes its bootloader exception. For the application and these components, use the version-matched source and build scripts identified with the candidate.

The ZIP ships the official SQLite 3.54.1 x64 DLL in place of the Python installer's SQLite library. SQLite is public domain. The release also provides the pinned `sqlite-amalgamation-3540100.zip` source archive.

- [Qt for Python licenses](https://doc.qt.io/qtforpython-6/licenses.html)
- [Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations)
- [PySide6 Windows build instructions](https://doc.qt.io/qtforpython-6/building_from_source/windows.html)
- [CPython 3.11.9 source](https://www.python.org/downloads/release/python-3119/)
- [PyInstaller 6.22.3 source](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3)
- [SQLite 3.54.1 release](https://sqlite.org/releaselog/3_54_1.html) and [public-domain terms](https://sqlite.org/copyright.html)
- [Microsoft Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
