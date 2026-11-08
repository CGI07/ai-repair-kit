"""Preview scratch cleanup or check whether an offline transfer ISO can be reused."""

import argparse
import hashlib
import stat
import subprocess
from pathlib import Path


PROJECT = Path(__file__).absolute().parents[1]
SCRATCH = (
    Path("build/pyinstaller"),
    Path("build/lib"),
    Path(".test-work/scratch/builds"),
)


def checked_path(base: Path, relative: Path) -> Path:
    if relative.anchor or ".." in relative.parts:
        raise ValueError("Paths must stay inside the selected project directory.")
    path = base
    for part in (None, *relative.parts):
        if part is not None:
            path /= part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError(f"Link or junction found at {path}")
    return path


def scratch_bytes(directory: Path) -> int:
    total = 0
    pending = [directory]
    while pending:
        for child in pending.pop().iterdir():
            child = checked_path(directory, child.relative_to(directory))
            if child.is_dir():
                pending.append(child)
            elif child.is_file():
                total += child.stat().st_size
            else:
                raise ValueError(f"Unexpected entry at {child}")
    return total


def preview_scratch() -> None:
    total = 0
    print("Dry run. Review inactive scratch paths before removing anything.")
    for relative in SCRATCH:
        try:
            size = scratch_bytes(checked_path(PROJECT, relative))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            print(f"Skipped {relative} ({error})")
            continue
        total += size
        print(f"{size:>14,} bytes  {relative}")
    print(f"Estimated total by file length: {total:,} bytes ({total / 2**30:.3f} GiB)")


def check_iso(iso_path: Path, payload_dir: Path, names: list[str]) -> None:
    work = checked_path(PROJECT, Path(".test-work"))
    iso = checked_path(work, iso_path)
    payload = checked_path(work, payload_dir)
    if iso.suffix.lower() != ".iso" or not iso.is_file() or not payload.is_dir():
        raise ValueError("Select an ISO file and a payload directory inside .test-work.")
    if len({name.casefold() for name in names}) != len(names) or any(
        not name or Path(name).name != name or name in {".", ".."} or name.startswith("-")
        for name in names
    ):
        raise ValueError("Provide unique, flat ISO file names.")
    listing = subprocess.run(
        ["tar.exe", "-tf", str(iso)], check=True, capture_output=True, text=True, timeout=30
    )
    members = [name for name in listing.stdout.splitlines() if name != "."]
    if sorted(members) != sorted(names):
        raise ValueError("The ISO member list differs from the explicit payload list.")
    for name in names:
        source = checked_path(payload, Path(name))
        if not source.is_file():
            raise ValueError(f"Missing payload file {name}")
        with source.open("rb") as stream:
            expected = hashlib.file_digest(stream, "sha256").hexdigest()
        with subprocess.Popen(["tar.exe", "-xOf", str(iso), name], stdout=subprocess.PIPE) as tar:
            assert tar.stdout is not None
            actual = hashlib.file_digest(tar.stdout, "sha256").hexdigest()
            if tar.wait() or actual != expected:
                raise ValueError(f"ISO payload differs for {name}")
        print(f"{expected}  {name}")
    print("Payload matches. Check the VM's optical slot and isolation before attaching.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-iso", nargs="+", metavar="PATH",
        help="ISO path, payload directory (both relative to .test-work), then file names",
    )
    options = parser.parse_args()
    if not options.check_iso:
        preview_scratch()
        return
    if len(options.check_iso) < 3:
        parser.error("--check-iso needs an ISO path, a payload directory and at least one name")
    iso, payload, *names = options.check_iso
    try:
        check_iso(Path(iso), Path(payload), names)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
