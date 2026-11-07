"""Collect versioned upstream notices for a Windows build candidate."""

import argparse
import hashlib
import importlib.metadata
import json
import posixpath
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath
from urllib.request import urlopen


QT_VERSION = "6.11.3"
QT_BASE = f"https://download.qt.io/official_releases/qt/6.11/{QT_VERSION}/submodules/"
PYSIDE_BASE = f"https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{QT_VERSION}-src/"
QT_DIGESTS = {
    "qtbase": "46de0710b594945a5d446ae44354daa386141c6903c53781b2ca547bc62f8f8f",
    "qtsvg": "9bdf6fa4b5a2d91c3eaa2edd12263fdff0ae19d0ac25d3c99946cd4189a07123",
    "qtimageformats": "2759929c7abbc5376120394d1ccac1abbb816bc2791b34ece6c1412d323e6213",
    "qttranslations": "499e3067b91e53675eead7a54a3cd771c30939d04d03b4e67f12167484e290f2",
    "pyside-setup": "050819cb05e14d6689f42b56e4fa9c8ff63be2bb339e0c094c45b990b7cabde1",
}


def verified_source(cache: Path, name: str, url: str, expected: str) -> Path:
    archive = cache / name
    if not archive.exists():
        temporary = archive.with_suffix(".partial")
        try:
            with urlopen(url, timeout=60) as response, temporary.open("xb") as file:
                shutil.copyfileobj(response, file)
            temporary.rename(archive)
        finally:
            temporary.unlink(missing_ok=True)
    with archive.open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != expected:
            raise ValueError(f"Source checksum mismatch: {name}")
    return archive


def copy_qt_notices(archive: Path, output: Path) -> None:
    root = archive.name.removesuffix(".tar.xz")
    references: set[str] = set()

    def copy_member(package: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
        name = PurePosixPath(member.name)
        if name.parts[0] != root or ".." in name.parts or "\\" in member.name:
            raise ValueError(f"Unexpected notice path: {member.name}")
        with package.extractfile(member) as file:
            contents = file.read()
        target = output.joinpath(*name.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
        return contents

    with tarfile.open(archive, "r:xz") as package:
        for member in package:
            name = PurePosixPath(member.name)
            if not member.isfile() or len(name.parts) < 2:
                continue
            if name.parts[1] == "LICENSES":
                copy_member(package, member)
            if name.name == "qt_attribution.json":
                # Several upstream notices contain literal line breaks in JSON strings.
                rows = json.loads(copy_member(package, member).decode("utf-8-sig"), strict=False)
                for row in rows if isinstance(rows, list) else [rows]:
                    values = row.get("LicenseFiles") or []
                    if isinstance(values, str):
                        values = [values]
                    for value in [row.get("LicenseFile"), *values]:
                        if isinstance(value, str) and value:
                            path = posixpath.normpath(str(name.parent / value))
                            if not path.startswith(root + "/"):
                                raise ValueError(f"Notice outside source archive: {path}")
                            references.add(path)

    with tarfile.open(archive, "r:xz") as package:
        for member in package:
            if member.isfile() and member.name in references:
                copy_member(package, member)
                references.remove(member.name)
    if references:
        raise ValueError(f"Missing notices in {archive.name}: {sorted(references)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-cache", type=Path, default=Path("build/qt-license-sources"))
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    args.source_cache.mkdir(parents=True, exist_ok=True)
    for dependency in ("PySide6-Essentials", "shiboken6"):
        if importlib.metadata.version(dependency) != QT_VERSION:
            raise ValueError(f"Update the source archive list for {dependency}")
    sums = []
    for module, digest in QT_DIGESTS.items():
        name = f"{module}-everywhere-src-{QT_VERSION}.tar.xz"
        url = (PYSIDE_BASE if module == "pyside-setup" else QT_BASE) + name
        copy_qt_notices(verified_source(args.source_cache, name, url, digest), args.output)
        sums.append(f"{digest}  {name}  {url}")
    (args.output / "QT-SOURCES.sha256").write_text("\n".join(sums) + "\n", encoding="ascii")
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", args.output / "CPython-LICENSE.txt")
    pyinstaller = importlib.metadata.distribution("PyInstaller")
    copying, = (file for file in pyinstaller.files if file.name == "COPYING.txt" and "licenses" in file.parts)
    shutil.copyfile(pyinstaller.locate_file(copying), args.output / "PyInstaller-COPYING.txt")
    shutil.copyfile(Path(__file__).resolve().parents[1] / "docs/windows-third-party.md", args.output / "README.md")


if __name__ == "__main__":
    main()
