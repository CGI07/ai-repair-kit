"""Guarded, reversible repair of an all-NUL Codex user configuration."""

import ctypes
import hashlib
import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from ..local_files import UnsafePath, ordinary_path, pinned_directory, stable_file
from .codex_diagnostics import CONFIG_LIMIT


class RepairError(Exception):
    """An expected refusal or failure with a safe UI message code."""


@dataclass(frozen=True)
class Target:
    path: Path
    device: int
    inode: int
    size: int

    @property
    def binding(self) -> str:
        key = f"{os.path.normcase(str(self.path))}\0{self.device}\0{self.inode}"
        return hashlib.sha256(os.fsencode(key)).hexdigest()


@dataclass(frozen=True)
class Backup:
    id: str
    binding: str
    size: int


@dataclass(frozen=True)
class Plan:
    target: Target
    backup: Backup | None = None


def _read(stream: BinaryIO) -> bytes:
    stream.seek(0)
    return stream.read(CONFIG_LIMIT + 1)


def _target(path: Path, stream: BinaryIO, data: bytes) -> Target:
    info = os.fstat(stream.fileno())
    if not info.st_ino:
        raise UnsafePath("The file system did not provide a stable file identity")
    return Target(path, info.st_dev, info.st_ino, len(data))


def _all_nul(data: bytes) -> bool:
    return 0 < len(data) <= CONFIG_LIMIT and not data.strip(b"\0")


def prepare_repair(home: Path) -> Plan:
    path = home / "config.toml"
    with stable_file(path) as stream:
        data = _read(stream)
        if not _all_nul(data):
            raise RepairError("stale")
        return Plan(_target(path, stream, data))


def _backup_path(store: Path, backup_id: str) -> Path:
    try:
        canonical = UUID(backup_id).hex
    except (ValueError, TypeError, AttributeError):
        raise RepairError("backup_invalid") from None
    if backup_id != canonical:
        raise RepairError("backup_invalid")
    return store / (backup_id + ".ark")


def _read_backup(store: Path, backup_id: str) -> tuple[Backup, bytes]:
    with stable_file(_backup_path(store, backup_id)) as stream:
        try:
            header = json.loads(stream.readline(512))
            data = stream.read(CONFIG_LIMIT + 1)
            binding = header["target"]
            if (header["format"] != 1 or not isinstance(binding, str)
                    or len(binding) != 64 or any(char not in "0123456789abcdef" for char in binding)
                    or header["size"] != len(data) or not _all_nul(data)
                    or header["sha256"] != hashlib.sha256(data).hexdigest()):
                raise ValueError
        except (ValueError, KeyError, TypeError, RecursionError):
            raise RepairError("backup_invalid") from None
    return Backup(backup_id, binding, len(data)), data


def list_backups(store: Path) -> list[Backup]:
    try:
        ordinary_path(store, directory=True)
    except FileNotFoundError:
        return []
    results = []
    with os.scandir(store) as entries:
        for index, entry in enumerate(entries):
            if index >= 100:
                break
            if entry.name.endswith(".ark"):
                try:
                    backup, _ = _read_backup(store, entry.name[:-4])
                except (OSError, RepairError):
                    continue
                results.append(backup)
    return sorted(results, key=lambda backup: backup.id)


def _create_backup(store: Path, target: Target, data: bytes) -> Backup:
    home = target.path.parent
    if store.is_relative_to(home) or home.is_relative_to(store):
        raise UnsafePath("The backup store must be separate from Codex home")
    for directory in (*reversed(store.parents), store):
        try:
            ordinary_path(directory, directory=True)
        except FileNotFoundError:
            with pinned_directory(directory.parent):
                directory.mkdir(exist_ok=True)
                ordinary_path(directory, directory=True)
    backup_id = uuid4().hex
    destination = _backup_path(store, backup_id)
    staging = destination.with_suffix(".part")
    header = {
        "format": 1, "target": target.binding,
        "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
    }
    with pinned_directory(store):
        with staging.open("xb") as stream:
            stream.write(json.dumps(header, ensure_ascii=True).encode("ascii") + b"\n")
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _publish_backup(staging, destination)
        backup, verified = _read_backup(store, backup_id)
    if verified != data or backup.binding != target.binding:
        raise RepairError("backup_invalid")
    return backup


def _publish_backup(staging: Path, destination: Path) -> None:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    move = kernel.MoveFileExW
    move.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    move.restype = wintypes.BOOL
    # Write through without replacing an existing backup.
    if not move(str(staging), str(destination), 0x8):
        raise ctypes.WinError(ctypes.get_last_error())


def prepare_restore(home: Path, store: Path, backup_id: str) -> Plan:
    backup, _ = _read_backup(store, backup_id)
    path = home / "config.toml"
    with stable_file(path) as stream:
        data = _read(stream)
        target = _target(path, stream, data)
    if data or target.binding != backup.binding:
        raise RepairError("stale")
    return Plan(target, backup)


def _write(stream: BinaryIO, data: bytes) -> None:
    stream.seek(0)
    stream.write(data)
    stream.truncate(len(data))
    stream.flush()
    os.fsync(stream.fileno())


def _verify(stream: BinaryIO, expected: bytes) -> None:
    actual = _read(stream)
    if actual != expected:
        raise RepairError("verify_failed")
    if not expected and tomllib.loads(actual.decode("utf-8")) != {}:
        raise RepairError("verify_failed")


def apply(plan: Plan, store: Path, *, approved: bool) -> Backup:
    """Revalidate the preview, preserve original bytes, write and verify under one lock."""
    if not approved:
        raise RepairError("approval_required")
    path = plan.target.path
    if path.name != "config.toml":
        raise RepairError("stale")
    with stable_file(path, writable=True) as stream:
        before = _read(stream)
        target = _target(path, stream, before)
        if plan.target != target:
            raise RepairError("stale")
        if plan.backup:
            backup, after = _read_backup(store, plan.backup.id)
            if before or backup != plan.backup or backup.binding != target.binding:
                raise RepairError("stale")
        else:
            if not _all_nul(before):
                raise RepairError("stale")
            backup = _create_backup(store, target, before)
            after = b""
        try:
            _write(stream, after)
            _verify(stream, after)
        except (OSError, RepairError, ValueError):
            try:
                _write(stream, before)
                _verify(stream, before)
            except (OSError, RepairError, ValueError):
                raise RepairError("rollback_failed") from None
            raise RepairError("rolled_back") from None
    return backup
