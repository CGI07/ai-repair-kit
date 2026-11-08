"""Replace the frozen app's SQLite DLL with the pinned official x64 build."""

import argparse
import ctypes
import hashlib
from pathlib import Path
from zipfile import ZipFile

from prepare_windows_notices import verified_source


VERSION = "3.54.1"
BASE = "https://sqlite.org/2026/"
WINDOWS_ARCHIVE = "sqlite-dll-win-x64-3540100.zip"
SOURCE_ARCHIVE = "sqlite-amalgamation-3540100.zip"
ARCHIVES = {
    WINDOWS_ARCHIVE: "f436f53ad8f965af43f3817b42c99c0689e6787f6b7ace208fe38dab7ffc4971",
    SOURCE_ARCHIVE: "0a9c8e34352dad46006aa82836d5383bff9dc6bc9ce5a60553c05aa30e6ad1f9",
}
DLL_SHA256 = "4b59d3cded9dc1608544160f3b4b10ac974c6ac0d6ffc23064e5f4c8dbd05aea"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    target = parser.parse_args().target
    if target.name != "sqlite3.dll" or not target.is_file():
        raise ValueError("Expected the existing sqlite3.dll in a fresh Windows bundle.")
    cache = Path("build/sqlite-runtime")
    cache.mkdir(parents=True, exist_ok=True)
    archives = {
        name: verified_source(cache, name, BASE + name, checksum)
        for name, checksum in ARCHIVES.items()
    }
    with ZipFile(archives[WINDOWS_ARCHIVE]) as archive:
        dll = archive.read("sqlite3.dll")
    if hashlib.sha256(dll).hexdigest() != DLL_SHA256:
        raise ValueError("Official SQLite DLL checksum mismatch.")
    target.write_bytes(dll)
    runtime = ctypes.WinDLL(str(target.resolve()))
    runtime.sqlite3_libversion.restype = ctypes.c_char_p
    if runtime.sqlite3_libversion() != VERSION.encode("ascii"):
        raise ValueError("Unexpected SQLite library version.")
    print(f"Bundled SQLite {VERSION} ({DLL_SHA256})")


if __name__ == "__main__":
    main()
