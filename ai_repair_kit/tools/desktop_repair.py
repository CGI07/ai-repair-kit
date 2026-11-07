"""Recover the documented empty Codex Desktop project registry on Windows."""

import ctypes
import hashlib
import json
import os
import sqlite3
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator
from uuid import UUID, uuid4

from ..local_files import ordinary_path, pinned_directory, stable_file
from .codex_diagnostics import inspect_config_bytes, locked_config
from .codex_repair import RepairError, _publish_backup
from .desktop_catalog import (
    CACHE_LIMIT, CACHE_NAME, STATE_NAME, Unsupported, decode_cache, legacy_keys,
    open_snapshot, projects, read_cache,
)

from .desktop_history import (
    HISTORY_BUILD, HISTORY_BUILDS, RECENT_HISTORY_BUILD, matches_recent_history, merge_history,
)

SUPPORTED_BUILD = "26.911.7940.0"
HISTORY_FILENAMES = (CACHE_NAME, CACHE_NAME + ".bak")


@dataclass(frozen=True)
class Plan:
    home: Path
    db_home: Path
    before: str
    after: str
    count: int
    build: str
    uses_wal: bool
    linked: int = 0
    skipped: int = 0
    history: Path | None = None
    history_hash: str = ""
    config_hash: str | None = None
    sqlite_override: Path | None = None


@dataclass(frozen=True)
class RestorePlan:
    home: Path
    backup_id: str
    original_hash: str
    current_hash: str | None
    size: int


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate(db: sqlite3.Connection, cache: dict) -> tuple[bytes, int]:
    if cache.get("local-projects") != {}:
        raise RepairError("desktop_ineligible")
    catalog, mappings = projects(db), legacy_keys(db)
    if not catalog or set(mappings) != set(catalog):
        raise RepairError("desktop_ineligible")
    registry = {}
    for core_id, project in catalog.items():
        keys = mappings[core_id]
        if len(keys) != 1 or not project.roots or len(set(map(Path, project.roots))) != len(project.roots):
            raise RepairError("desktop_ineligible")
        key, = keys
        for root in project.roots:
            ordinary_path(Path(root), directory=True)
        registry[key] = {
            "id": key, "name": project.name, "rootPaths": list(project.roots),
            "createdAt": project.created, "updatedAt": project.updated,
        }
    new_cache = dict(cache, **{"local-projects": registry})
    data = (json.dumps(new_cache, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("ascii")
    if decode_cache(data) != new_cache:
        raise RepairError("desktop_ineligible")
    return data, len(registry)


def _build_candidate(db: sqlite3.Connection, cache: dict, history: bytes | None, build: str):
    if history is None:
        result, count = _candidate(db, cache)
        return result, count, 0, 0
    if build == RECENT_HISTORY_BUILD and not matches_recent_history(cache):
        raise RepairError("desktop_ineligible")
    merged, count, linked, skipped = merge_history(db, cache, decode_cache(history))
    result = (json.dumps(merged, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("ascii")
    if decode_cache(result) != merged:
        raise RepairError("desktop_ineligible")
    return result, count, linked, skipped


def _check_database(home: Path, expected: Path, data: bytes | None,
                    override: Path | None) -> None:
    configured = None
    if data is not None:
        finding, _, configured = inspect_config_bytes(data)
        if finding:
            raise RepairError("desktop_ineligible")
    if (configured or override or home) != expected:
        raise RepairError("stale")


def _validate_history_path(home: Path, history: Path) -> None:
    if history.name.casefold() not in HISTORY_FILENAMES or history == home / CACHE_NAME:
        raise RepairError("desktop_snapshot_name")


def prepare(home: Path, db_home: Path, build: str, history: Path | None = None, *,
            sqlite_override: Path | None = None) -> Plan:
    if build not in (HISTORY_BUILDS if history else (SUPPORTED_BUILD,)):
        raise RepairError("desktop_version")
    if history is not None:
        _validate_history_path(home, history)
    try:
        before, cache = read_cache(home)
        historical = _read_bytes(history) if history is not None else None
        with locked_config(home) as config:
            _check_database(home, db_home, config, sqlite_override)
            with open_snapshot(db_home / STATE_NAME, allow_wal=build == RECENT_HISTORY_BUILD) as snapshot:
                candidate, count, linked, skipped = _build_candidate(snapshot.connection, cache, historical, build)
    except (ValueError, OSError, sqlite3.Error):
        raise RepairError("desktop_ineligible") from None
    return Plan(home, db_home, digest(before), digest(candidate), count, build, snapshot.uses_wal, linked, skipped,
                history, digest(historical) if historical is not None else "",
                digest(config) if config is not None else None, sqlite_override)


def _profile_key(home: Path) -> str:
    return digest(os.fsencode(os.path.normcase(str(home))))


def _read_bytes(path: Path, limit: int = CACHE_LIMIT) -> bytes:
    with stable_file(path) as stream:
        return _read_bounded(stream, limit)


def _read_bounded(stream: BinaryIO, limit: int = CACHE_LIMIT) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise RepairError("desktop_ineligible")
    return data


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _safe_store(store: Path, home: Path) -> Path:
    if not store.is_absolute() or store.is_relative_to(home) or home.is_relative_to(store):
        raise RepairError("desktop_ineligible")
    folder = store / "desktop"
    for path in (*reversed(folder.parents), folder):
        try:
            ordinary_path(path, directory=True)
        except FileNotFoundError:
            with pinned_directory(path.parent):
                path.mkdir(exist_ok=True)
    return folder


def _backup(folder: Path, home: Path, original: bytes, after: str) -> str:
    backup_id = uuid4().hex
    target = folder / (backup_id + ".arkd")
    staging = target.with_suffix(".part")
    header = {"format": 1, "profile": _profile_key(home), "before": digest(original), "after": after}
    with pinned_directory(folder):
        _write_new(staging, json.dumps(header).encode("ascii") + b"\n" + original)
        _publish_backup(staging, target)
        if _read_backup(folder, backup_id) != (header, original):
            raise RepairError("backup_invalid")
    return backup_id


def _read_backup(folder: Path, backup_id: str, *, profile: str | None = None) -> tuple[dict, bytes]:
    try:
        if UUID(backup_id).hex != backup_id:
            raise ValueError
        with stable_file(folder / (backup_id + ".arkd")) as stream:
            header = stream.readline(513)
            if len(header) > 512 or not header.endswith(b"\n"):
                raise ValueError
            metadata = json.loads(header)
            if (not isinstance(metadata, dict) or set(metadata) != {"format", "profile", "before", "after"}
                    or type(metadata["format"]) is not int or metadata["format"] != 1
                    or not all(isinstance(metadata[key], str) and len(metadata[key]) == 64
                               and all(char in "0123456789abcdef" for char in metadata[key])
                               for key in ("before", "after", "profile"))
                    or (profile is not None and metadata["profile"] != profile)):
                raise ValueError
            content = stream.read(CACHE_LIMIT + 1)
        decode_cache(content)
        if metadata["before"] != digest(content):
            raise ValueError
        return metadata, content
    except (ValueError, OSError, KeyError, TypeError, RecursionError):
        raise RepairError("backup_invalid") from None


def list_backups(store: Path, *, home: Path | None = None) -> list[str]:
    folder = store / "desktop"
    try:
        ordinary_path(folder, directory=True)
    except FileNotFoundError:
        return []
    candidates = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if not entry.name.endswith(".arkd"):
                continue
            try:
                if entry.is_file(follow_symlinks=False):
                    candidates.append((entry.stat(follow_symlinks=False).st_mtime_ns, entry.name[:-5]))
            except OSError:
                continue
    profile = _profile_key(home) if home is not None else None
    verified = []
    for _, key in sorted(candidates, reverse=True):
        try:
            _read_backup(folder, key, profile=profile)
        except RepairError:
            continue
        verified.append(key)
        if len(verified) == 100:
            break
    return verified


def _require_missing(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise RepairError("stale")


def _current_cache(home: Path) -> bytes | None:
    try:
        return _read_bytes(home / CACHE_NAME)
    except FileNotFoundError:
        return None


def _displaced_original(home: Path, backup_id: str, original: bytes) -> Path:
    path = home / (".ark-" + backup_id + ".original")
    try:
        actual = _read_bytes(path)
    except FileNotFoundError:
        raise RepairError("stale") from None
    if actual != original:
        raise RepairError("stale")
    return path


def prepare_restore(home: Path, store: Path, backup_id: str) -> RestorePlan:
    header, original = _read_backup(store / "desktop", backup_id)
    if header["profile"] != _profile_key(home):
        raise RepairError("stale")
    current = _current_cache(home)
    current_hash = digest(current) if current is not None else None
    if current_hash is None:
        _displaced_original(home, backup_id, original)
    elif current_hash != header["after"]:
        raise RepairError("stale")
    return RestorePlan(home, backup_id, header["before"], current_hash, len(original))


def apply(plan: Plan, store: Path, *, approved: bool) -> str:
    if not approved:
        raise RepairError("approval_required")
    if plan.history is not None:
        _validate_history_path(plan.home, plan.history)
    with _mutex(plan.home), pinned_directory(plan.home):
        ensure_closed()
        try:
            with locked_config(plan.home) as config, ExitStack() as guards:
                config_hash = digest(config) if config is not None else None
                if config_hash != plan.config_hash:
                    raise RepairError("stale")
                _check_database(plan.home, plan.db_home, config, plan.sqlite_override)
                snapshot = guards.enter_context(open_snapshot(
                    plan.db_home / STATE_NAME, allow_wal=plan.build == RECENT_HISTORY_BUILD,
                ))
                if snapshot.uses_wal != plan.uses_wal:
                    raise RepairError("stale")
                before, cache = read_cache(plan.home)
                if digest(before) != plan.before:
                    raise RepairError("stale")
                history = None
                if plan.history is not None:
                    try:
                        source = guards.enter_context(stable_file(plan.history))
                    except FileNotFoundError:
                        raise RepairError("stale") from None
                    history = _read_bounded(source)
                    if digest(history) != plan.history_hash:
                        raise RepairError("stale")
                candidate, count, linked, skipped = _build_candidate(snapshot.connection, cache, history, plan.build)
                if (digest(candidate), count, linked, skipped) != (plan.after, plan.count, plan.linked, plan.skipped):
                    raise RepairError("stale")
                folder = _safe_store(store, plan.home)
                backup_id = _backup(folder, plan.home, before, plan.after)
                missing_config = plan.home / "config.toml" if config is None else None
                _replace_checked(plan.home / CACHE_NAME, before, candidate, backup_id,
                                 missing_config=missing_config)
        except (Unsupported, sqlite3.Error) as error:
            raise RepairError("desktop_ineligible") from error
    return backup_id


def restore(plan: RestorePlan, store: Path, *, approved: bool) -> str | None:
    if not approved:
        raise RepairError("approval_required")
    with _mutex(plan.home), pinned_directory(plan.home):
        ensure_closed()
        header, original = _read_backup(store / "desktop", plan.backup_id)
        if (header["profile"] != _profile_key(plan.home) or header["before"] != plan.original_hash
                or len(original) != plan.size):
            raise RepairError("stale")
        target = plan.home / CACHE_NAME
        if plan.current_hash is None:
            _restore_missing_cache(plan, target, original)
            return None
        current = _current_cache(plan.home)
        if current is None or header["after"] != plan.current_hash or digest(current) != plan.current_hash:
            raise RepairError("stale")
        folder = _safe_store(store, plan.home)
        backup_id = _backup(folder, plan.home, current, plan.original_hash)
        _replace_checked(target, current, original, backup_id)
    return backup_id


def _restore_missing_cache(plan: RestorePlan, target: Path, original: bytes) -> None:
    ensure_closed()
    displaced = _displaced_original(plan.home, plan.backup_id, original)
    _require_missing(target)
    try:
        # Move the original within the profile without overwrite, retaining its metadata.
        _publish_backup(displaced, target)
    except OSError:
        _require_missing(target)
        raise
    try:
        if _read_bytes(target) != original:
            raise RepairError("rollback_failed")
    except OSError:
        raise RepairError("rollback_failed") from None


def _replace_checked(target: Path, before: bytes, after: bytes, backup_id: str, *,
                     missing_config: Path | None = None) -> None:
    prefix = ".ark-" + backup_id
    staging = target.with_name(prefix + ".new")
    displaced = target.with_name(prefix + ".original")
    rejected = target.with_name(prefix + ".rejected")
    _write_new(staging, after)
    try:
        ensure_closed()
        if _read_bytes(target) != before:
            raise RepairError("stale")
        if missing_config is not None:
            _require_missing(missing_config)
        try:
            _replace(target, staging, displaced)
            if _read_bytes(displaced) != before or _read_bytes(target) != after:
                raise RepairError("verify_failed")
        except (OSError, RepairError):
            # Prefer the bytes displaced at the instant of replacement. They may
            # belong to a competing writer that changed the target after recheck.
            try:
                actual = _read_bytes(target) if target.exists() else None
                if actual == before and not displaced.exists():
                    raise RepairError("rolled_back")
                original = _read_bytes(displaced)
                if actual == after:
                    with stable_file(target, allow_rename=True) as current:
                        if _read_bounded(current) != after:
                            raise RepairError("rollback_failed")
                        _replace(target, displaced, rejected)
                    # A competing rename is still possible. Keep the displaced
                    # bytes and report uncertainty if they were not our output.
                    if _read_bytes(rejected) != after:
                        raise RepairError("rollback_failed")
                elif actual is None:
                    _publish_backup(displaced, target)
                else:
                    raise RepairError("rollback_failed")
                if _read_bytes(target) != original:
                    raise RepairError("rollback_failed")
            except (OSError, RepairError) as error:
                if isinstance(error, RepairError) and str(error) == "rolled_back":
                    raise
                raise RepairError("rollback_failed") from None
            raise RepairError("rolled_back") from None
        # The target and its backup have already been verified. A scanner may
        # temporarily hold the displaced file, so cleanup cannot decide the outcome.
        with suppress(OSError):
            displaced.unlink(missing_ok=True)
    finally:
        with suppress(OSError):
            staging.unlink(missing_ok=True)


def _replace(target: Path, staging: Path, displaced: Path) -> None:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = (wintypes.LPCWSTR,) * 3 + (wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID)
    replace.restype = wintypes.BOOL
    if not replace(str(target), str(staging), str(displaced), 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def _mutex(home: Path) -> Iterator[None]:
    from ctypes import wintypes

    if os.name != "nt":
        raise RepairError("desktop_ineligible")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    ctypes.set_last_error(0)
    handle = create(None, False, "Local\\AIRepairKit-" + _profile_key(home))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if ctypes.get_last_error() == 183:
            raise RepairError("desktop_busy")
        yield
    finally:
        kernel.CloseHandle(handle)


def ensure_closed() -> None:
    """Fail closed if a recognized Desktop or Codex executable is running."""
    if _blocking_desktop_processes():
        raise RepairError("desktop_busy")


def _blocking_desktop_processes() -> set[str]:
    """Collect recognized executable basenames without accessing their profiles."""
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
            ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    snapshot.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    first, advance = kernel.Process32FirstW, kernel.Process32NextW
    for function in (first, advance):
        function.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry))
        function.restype = wintypes.BOOL
    handle = snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise RepairError("desktop_busy")
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        if not first(handle, ctypes.byref(entry)):
            raise RepairError("desktop_busy")
        blocking = set()
        while True:
            name = entry.name.casefold()
            if name.endswith(".exe") and name.startswith(("codex", "chatgpt")):
                blocking.add(name)
            if not advance(handle, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:
                    raise RepairError("desktop_busy")
                return blocking
    finally:
        kernel.CloseHandle(handle)
