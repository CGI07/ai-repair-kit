"""Bound access to ordinary files on a local drive."""

import ctypes
import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

REPARSE_POINT = 0x400


class UnsafePath(OSError):
    """A path is outside the local, ordinary-file access policy."""


def ordinary_path(path: Path, *, directory: bool) -> os.stat_result:
    if not path.is_absolute() or ".." in path.parts or "\0" in str(path):
        raise UnsafePath("An absolute, normalized path is required")
    if os.name == "nt":
        if not path.drive.endswith(":") or ctypes.windll.kernel32.GetDriveTypeW(
            ctypes.c_wchar_p(path.anchor)
        ) != 3:
            raise UnsafePath("A fixed local drive is required")
        if any(":" in part or part.endswith((" ", ".")) or Path(part).is_reserved()
               for part in path.parts[1:]):
            raise UnsafePath("Special Windows paths are unsupported")

    for parent in reversed(path.parents):
        _check_stat(parent.lstat(), directory=True)
    info = path.lstat()
    _check_stat(info, directory=directory)
    return info


def _check_stat(info: os.stat_result, *, directory: bool) -> None:
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & REPARSE_POINT
            or (not directory and info.st_nlink != 1)):
        raise UnsafePath("Links and special files are unsupported")


@contextmanager
def pinned_directory(path: Path) -> Iterator[None]:
    """On Windows, hold each path component open without delete sharing."""
    ordinary_path(path, directory=True)
    with ExitStack() as stack:
        if os.name == "nt":
            for directory in (*reversed(path.parents), path):
                if directory == Path(directory.anchor):
                    continue  # The drive root cannot be renamed.
                kernel, handle = _windows_open(directory, 0x80, 3, directory=True)
                stack.callback(kernel.CloseHandle, handle)
                _check_stat(directory.lstat(), directory=True)
        yield


@contextmanager
def stable_file(path: Path, *, writable: bool = False, allow_rename: bool = False) -> Iterator[BinaryIO]:
    """Open a verified file with Windows sharing restricted to the caller's needs."""
    expected = ordinary_path(path, directory=False)
    with pinned_directory(path.parent):
        if os.name == "nt":
            import msvcrt

            access = 0x80000000 | (0x40000000 if writable else 0)
            share = 0 if writable else (5 if allow_rename else 1)  # Read, plus delete sharing for rename.
            kernel, handle = _windows_open(path, access, share)
            try:
                flags = os.O_BINARY | (os.O_RDWR if writable else os.O_RDONLY)
                descriptor = msvcrt.open_osfhandle(handle, flags)
            except BaseException:
                kernel.CloseHandle(handle)
                raise
        else:
            if writable:
                raise UnsafePath("Repair requires the Windows file-sharing guard")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)

        with os.fdopen(descriptor, "r+b" if writable else "rb") as stream:
            actual = os.fstat(stream.fileno())
            _check_stat(actual, directory=False)
            current = ordinary_path(path, directory=False)
            if not os.path.samestat(expected, actual) or not os.path.samestat(current, actual):
                raise UnsafePath("The file changed during open")
            yield stream


def _windows_open(path: Path, access: int, share: int, *, directory: bool = False):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)

    open_existing = 3
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= 0x02000000  # FILE_FLAG_BACKUP_SEMANTICS
    handle = create_file(str(path), access, share, None, open_existing, flags, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3):
            raise FileNotFoundError(error, "File unavailable")
        raise ctypes.WinError(error)
    return kernel, handle
