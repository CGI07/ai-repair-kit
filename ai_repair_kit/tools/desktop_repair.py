"""Recover documented Codex Desktop project cache failures on Windows."""

import ctypes
import hashlib
import json
import os
import re
import sqlite3
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator
from uuid import UUID, uuid4

from ..local_files import ordinary_path, pinned_directory, stable_file
from .codex import installed_desktop_version
from .codex_diagnostics import inspect_config_bytes, locked_config
from .codex_repair import RepairError, _publish_backup
from .desktop_catalog import (
    CACHE_LIMIT, CACHE_NAME, STATE_NAME, SnapshotHasSidecars, Unsupported, decode_cache, legacy_keys,
    open_snapshot, projects, read_cache,
)

from .desktop_compatibility import ACCOUNT_STATE, CacheFormatError, Operation, classify_layout, require_legacy_scope
from .desktop_history import (
    ASSIGNMENTS, HOST_MAP, ORDER, REGISTRY,
    HISTORY_BUILD, HISTORY_BUILDS as STRING_LINK_BUILDS, RECENT_HISTORY_BUILD,
    matches_recent_history, merge_history,
)
from .desktop_registry import (
    OBJECT_LINK_BUILD, PARTIAL_REGISTRY_BUILD, HistoricalEvidenceError, build_registry,
    _require_identity_schema, scoped_assignment_evidence, scoped_registry_candidate,
)

SUPPORTED_BUILD = "26.911.7940.0"
TIMESTAMP_BUILD = "26.903.8094.0"
REGISTRY_BUILDS = (SUPPORTED_BUILD, TIMESTAMP_BUILD)
HISTORY_BUILDS = STRING_LINK_BUILDS
# Keep the two new rules covered by synthetic lab tests until Desktop GUI
# verification can establish that applying them preserves project membership.
LAB_CACHE_BUILDS = (PARTIAL_REGISTRY_BUILD, OBJECT_LINK_BUILD)
HISTORY_FILENAMES = (CACHE_NAME, CACHE_NAME + ".bak", CACHE_NAME + ".tmp-*")


@dataclass(frozen=True)
class Plan:
    home: Path
    db_home: Path
    before: str
    after: str
    count: int
    build: str
    uses_wal: bool
    profile: str
    linked: int = 0
    skipped: int = 0
    history: Path | None = None
    history_hash: str = ""
    config_hash: str | None = None
    sqlite_override: Path | None = None
    compatible_timestamps: bool = False
    scoped_registry: bool = False


@dataclass(frozen=True)
class RestorePlan:
    home: Path
    backup_id: str
    original_hash: str
    current_hash: str | None
    size: int
    profile: str


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


def _timestamp_candidate(db: sqlite3.Connection, cache: dict) -> tuple[bytes, int]:
    registry = cache.get("local-projects")
    if not isinstance(registry, dict) or not registry:
        raise RepairError("desktop_ineligible")
    reconstructed, _ = _candidate(db, dict(cache, **{"local-projects": {}}))
    expected = decode_cache(reconstructed)["local-projects"]
    if registry.keys() != expected.keys():
        raise RepairError("desktop_ineligible")
    assignments = cache.get("thread-project-assignments")
    if assignments is not None and (not isinstance(assignments, dict) or any(
        not isinstance(key, str) or key not in registry for key in assignments.values()
    )):
        raise RepairError("desktop_ineligible")
    updated = {}
    count = 0
    for key, entry in registry.items():
        template = expected[key]
        if not isinstance(entry, dict) or any(
            entry.get(field) != template[field] for field in ("id", "name", "rootPaths")
        ):
            raise RepairError("desktop_ineligible")
        result = dict(entry)
        for field in ("createdAt", "updatedAt"):
            if field in entry and (type(entry[field]) is not int or entry[field] != template[field]):
                raise RepairError("desktop_ineligible")
            result.setdefault(field, template[field])
        count += result != entry
        updated[key] = result
    if not count:
        raise RepairError("desktop_ineligible")
    candidate = dict(cache, **{"local-projects": updated})
    data = (json.dumps(candidate, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("ascii")
    if decode_cache(data) != candidate:
        raise RepairError("desktop_ineligible")
    return data, count


def _scoped_registry_bytes(db: sqlite3.Connection, home: Path, cache: dict,
                           history: bytes | None) -> tuple[bytes, int, int, int]:
    if history is None:
        raise RepairError("desktop_ineligible")
    merged, count = scoped_registry_candidate(db, home, cache, decode_cache(history))
    result = (json.dumps(merged, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("ascii")
    if decode_cache(result) != merged:
        raise RepairError("desktop_ineligible")
    return result, count, 0, 0


def _build_candidate(db: sqlite3.Connection, cache: dict, history: bytes | None, build: str,
                     *, compatible_timestamps: bool = False):
    require_legacy_scope(cache)
    if compatible_timestamps:
        if history is not None or classify_layout(cache).operation != Operation.TIMESTAMPS:
            raise RepairError("desktop_version")
        if ORDER in cache and set(cache[ORDER]) != cache[REGISTRY].keys():
            raise RepairError("desktop_ineligible")
        # Prove the original identities, existing roots and host mapping before
        # reusing the timestamp-only transformation on this layout.
        build_registry(db, {**cache, REGISTRY: {}, ASSIGNMENTS: {}}, None)
        data, count = _timestamp_candidate(db, cache)
        return data, count, 0, 0
    if build in LAB_CACHE_BUILDS:
        merged, count, linked, skipped = build_registry(
            db, cache, decode_cache(history) if history is not None else None,
        )
    elif history is None:
        result, count = _timestamp_candidate(db, cache) if build == TIMESTAMP_BUILD else _candidate(db, cache)
        return result, count, 0, 0
    else:
        if build == RECENT_HISTORY_BUILD and not matches_recent_history(cache):
            raise RepairError("desktop_ineligible")
        previous = decode_cache(history)
        require_legacy_scope(previous)
        merged, count, linked, skipped = merge_history(db, cache, previous)
    result = (json.dumps(merged, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("ascii")
    if decode_cache(result) != merged:
        raise RepairError("desktop_ineligible")
    return result, count, linked, skipped


@dataclass(frozen=True)
class Assessment:
    """Read-only evidence. It cannot be passed to apply()."""

    outcome: str
    operation: Operation | None = None
    projects: int = 0
    links: int = 0
    skipped: int = 0
    fields: tuple[str, ...] = ()


def assess(home: Path, db_home: Path, history: Path | None = None, *,
           sqlite_override: Path | None = None) -> Assessment:
    """Check a candidate in memory without consulting or trusting a package number."""
    try:
        if history is not None:
            _validate_history_path(home, history)
        with pinned_directory(home), locked_config(home) as config:
            _check_database(home, db_home, config, sqlite_override)
            _, cache = read_cache(home)
            previous = decode_cache(_read_bytes(history)) if history is not None else None
            if previous is not None and ACCOUNT_STATE in cache:
                if db_home != home:
                    return Assessment("desktop_profile_mismatch", Operation.SCOPED_REGISTRY)
                with open_snapshot(db_home / STATE_NAME, allow_wal=True, allow_empty_wal=True) as snapshot:
                    if cache.get(ASSIGNMENTS) != previous.get(ASSIGNMENTS):
                        links, skipped = scoped_assignment_evidence(snapshot.connection, home, cache, previous)
                        return Assessment("desktop_link_evidence", Operation.SCOPED_CHAT_LINKS,
                                          links=links, skipped=skipped)
                    _, count = scoped_registry_candidate(snapshot.connection, home, cache, previous)
                return Assessment("desktop_candidate", Operation.SCOPED_REGISTRY, count, fields=(REGISTRY,))
            layout = classify_layout(cache, previous)
            override = os.environ.get("CODEX_HOME")
            if (override and Path(override) != home) or (
                db_home != home and layout.operation in (Operation.PARTIAL_REGISTRY, Operation.OBJECT_HISTORY)
            ):
                return Assessment("desktop_profile_mismatch", layout.operation)
            with open_snapshot(db_home / STATE_NAME, allow_wal=layout.allow_wal) as snapshot:
                return _assess_candidate(snapshot.connection, cache, previous, layout.operation)
    except CacheFormatError:
        return Assessment("desktop_format")
    except HistoricalEvidenceError as error:
        return Assessment(str(error))
    except RepairError as error:
        return Assessment(str(error))
    except SnapshotHasSidecars:
        return Assessment("desktop_safety")
    except (Unsupported, sqlite3.Error):
        return Assessment("desktop_ineligible")
    except (ValueError, OSError):
        return Assessment("desktop_safety")


def _assess_candidate(db: sqlite3.Connection, cache: dict, previous: dict | None,
                      operation: Operation) -> Assessment:
    try:
        _require_identity_schema(db)
    except (Unsupported, sqlite3.Error):
        return Assessment("desktop_schema", operation)
    linked = skipped = 0
    if operation == Operation.TIMESTAMPS:
        data, count = _timestamp_candidate(db, cache)
        merged = decode_cache(data)
    elif operation == Operation.STRING_HISTORY and previous is not None:
        merged, count, linked, skipped = merge_history(db, cache, previous)
    else:
        if operation == Operation.PARTIAL_REGISTRY:
            # Reconstruct only in memory. A complete, matching registry has no missing entries.
            expected, *_ = build_registry(db, {**cache, REGISTRY: {}, ASSIGNMENTS: {}}, None)
            entries = cache[REGISTRY]
            if entries.keys() == expected[REGISTRY].keys() and all(
                isinstance(entry, dict) and all(
                    entry.get(key) == value and type(entry.get(key)) is type(value)
                    for key, value in expected[REGISTRY][project].items()
                ) for project, entry in entries.items()
            ):
                return Assessment("desktop_no_changes", operation)
        merged, count, linked, skipped = build_registry(db, cache, previous)
    encoded = json.dumps(merged, ensure_ascii=True, allow_nan=False).encode("ascii")
    decode_cache(encoded)
    fields = tuple(key for key in (REGISTRY, ORDER, HOST_MAP, ASSIGNMENTS)
                   if key in merged and (key not in cache or merged[key] != cache[key]))
    return Assessment("desktop_candidate", operation, count, linked, skipped, fields)


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
    name = history.name.casefold()
    temporary = re.fullmatch(re.escape(CACHE_NAME) + r"\.tmp-[a-z0-9_-]{1,128}", name)
    if (name not in HISTORY_FILENAMES[:2] and not temporary) or history == home / CACHE_NAME:
        raise RepairError("desktop_snapshot_name")


def _check_installed_build(home: Path, db_home: Path, build: str) -> None:
    if build in LAB_CACHE_BUILDS:
        override = os.environ.get("CODEX_HOME")
        if db_home != home or (override and Path(override) != home):
            raise RepairError("desktop_profile_mismatch")
    if installed_desktop_version() != build:
        raise RepairError("desktop_version_unverified")


def prepare(home: Path, db_home: Path, build: str, history: Path | None = None, *,
            sqlite_override: Path | None = None) -> Plan:
    compatible_timestamps = history is None and build not in REGISTRY_BUILDS
    if (build not in (HISTORY_BUILDS if history is not None else REGISTRY_BUILDS)
            and not _active_local_profile(home, db_home)):
        raise RepairError("desktop_version")
    _check_installed_build(home, db_home, build)
    if history is not None:
        _validate_history_path(home, history)
    try:
        with pinned_directory(home):
            return _prepare_pinned(home, db_home, build, history, sqlite_override, compatible_timestamps)
    except HistoricalEvidenceError as error:
        raise RepairError(str(error)) from None
    except (ValueError, OSError, sqlite3.Error):
        raise RepairError("desktop_ineligible") from None


def _active_local_profile(home: Path, db_home: Path) -> bool:
    override = os.environ.get("CODEX_HOME")
    sqlite_override = os.environ.get("CODEX_SQLITE_HOME")
    active_home = Path(override) if override else Path.home() / ".codex"
    return home == db_home == active_home and (not sqlite_override or Path(sqlite_override) == home)


def _prepare_pinned(home: Path, db_home: Path, build: str, history: Path | None,
                    sqlite_override: Path | None, compatible_timestamps: bool) -> Plan:
    profile = _profile_identity(home)
    before, cache = read_cache(home)
    scoped = history is not None and ACCOUNT_STATE in cache and _active_local_profile(home, db_home)
    if history is not None and not scoped and build not in HISTORY_BUILDS:
        raise RepairError("desktop_version")
    historical = _read_bytes(history) if history is not None else None
    with locked_config(home) as config:
        _check_database(home, db_home, config, sqlite_override)
        with open_snapshot(db_home / STATE_NAME, allow_wal=scoped or build == RECENT_HISTORY_BUILD,
                           allow_empty_wal=scoped) as snapshot:
            if scoped:
                candidate, count, linked, skipped = _scoped_registry_bytes(
                    snapshot.connection, home, cache, historical,
                )
            else:
                candidate, count, linked, skipped = _build_candidate(
                    snapshot.connection, cache, historical, build, compatible_timestamps=compatible_timestamps,
                )
    return Plan(
        home, db_home, digest(before), digest(candidate), count, build, snapshot.uses_wal, profile,
        linked, skipped, history, digest(historical) if historical is not None else "",
        digest(config) if config is not None else None, sqlite_override, compatible_timestamps, scoped,
    )


def _profile_key(home: Path) -> str:
    return digest(os.fsencode(os.path.normcase(str(home))))


def _profile_identity(home: Path) -> str:
    info = ordinary_path(home, directory=True)
    if not info.st_ino:
        raise RepairError("desktop_ineligible")
    return digest(f"{_profile_key(home)}:{info.st_dev}:{info.st_ino}".encode("ascii"))


def _matches_profile(header: dict, home: Path, identity: str) -> bool:
    return header["profile"] == (_profile_key(home) if header["format"] == 1 else identity)


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
    header = {"format": 2, "profile": _profile_identity(home), "before": digest(original), "after": after}
    with pinned_directory(folder):
        _write_new(staging, json.dumps(header).encode("ascii") + b"\n" + original)
        _publish_backup(staging, target)
        if _read_backup(folder, backup_id) != (header, original):
            raise RepairError("backup_invalid")
    return backup_id


def _read_backup(folder: Path, backup_id: str) -> tuple[dict, bytes]:
    try:
        if UUID(backup_id).hex != backup_id:
            raise ValueError
        with stable_file(folder / (backup_id + ".arkd")) as stream:
            header = stream.readline(513)
            if len(header) > 512 or not header.endswith(b"\n"):
                raise ValueError
            metadata = json.loads(header)
            if (not isinstance(metadata, dict) or set(metadata) != {"format", "profile", "before", "after"}
                    or type(metadata["format"]) is not int or metadata["format"] not in (1, 2)
                    or not all(isinstance(metadata[key], str) and len(metadata[key]) == 64
                               and all(char in "0123456789abcdef" for char in metadata[key])
                               for key in ("before", "after", "profile"))):
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
    try:
        profile = _profile_identity(home) if home is not None else ""
    except FileNotFoundError:
        return []
    verified = []
    for _, key in sorted(candidates, reverse=True):
        try:
            header, _ = _read_backup(folder, key)
            if home is not None and not _matches_profile(header, home, profile):
                continue
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
    with pinned_directory(home):
        profile = _profile_identity(home)
        header, original = _read_backup(store / "desktop", backup_id)
        if not _matches_profile(header, home, profile):
            raise RepairError("stale")
        current = _current_cache(home)
        current_hash = digest(current) if current is not None else None
        if current_hash is None:
            _displaced_original(home, backup_id, original)
        elif current_hash != header["after"]:
            raise RepairError("stale")
        return RestorePlan(home, backup_id, header["before"], current_hash, len(original), profile)


def apply(plan: Plan, store: Path, *, approved: bool) -> str:
    if not approved:
        raise RepairError("approval_required")
    if plan.scoped_registry:
        if (plan.history is None or plan.compatible_timestamps
                or not _active_local_profile(plan.home, plan.db_home)):
            raise RepairError("desktop_profile_mismatch")
    elif plan.compatible_timestamps:
        if plan.history is not None or not _active_local_profile(plan.home, plan.db_home):
            raise RepairError("desktop_profile_mismatch")
    elif plan.build not in (HISTORY_BUILDS if plan.history is not None else REGISTRY_BUILDS):
        raise RepairError("desktop_version")
    if plan.history is not None:
        _validate_history_path(plan.home, plan.history)
    with _mutex(plan.home), pinned_directory(plan.home):
        ensure_closed()
        if _profile_identity(plan.home) != plan.profile:
            raise RepairError("stale")
        _check_installed_build(plan.home, plan.db_home, plan.build)
        try:
            with locked_config(plan.home) as config, ExitStack() as guards:
                config_hash = digest(config) if config is not None else None
                if config_hash != plan.config_hash:
                    raise RepairError("stale")
                _check_database(plan.home, plan.db_home, config, plan.sqlite_override)
                state_path = plan.db_home / STATE_NAME
                snapshot = guards.enter_context(open_snapshot(
                    state_path, allow_wal=plan.scoped_registry or plan.build == RECENT_HISTORY_BUILD,
                    allow_empty_wal=plan.scoped_registry,
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
                if plan.scoped_registry:
                    candidate, count, linked, skipped = _scoped_registry_bytes(
                        snapshot.connection, plan.home, cache, history,
                    )
                else:
                    candidate, count, linked, skipped = _build_candidate(
                        snapshot.connection, cache, history, plan.build,
                        compatible_timestamps=plan.compatible_timestamps,
                    )
                if (digest(candidate), count, linked, skipped) != (plan.after, plan.count, plan.linked, plan.skipped):
                    raise RepairError("stale")
                folder = _safe_store(store, plan.home)
                backup_id = _backup(folder, plan.home, before, plan.after)
                _check_installed_build(plan.home, plan.db_home, plan.build)
                if ((plan.compatible_timestamps or plan.scoped_registry)
                        and not _active_local_profile(plan.home, plan.db_home)):
                    raise RepairError("desktop_profile_mismatch")
                journals = ("-journal",) if snapshot.uses_wal else ("-journal", "-wal", "-shm")
                for suffix in journals:
                    _require_missing(state_path.with_name(state_path.name + suffix))
                missing_config = plan.home / "config.toml" if config is None else None
                _replace_checked(plan.home / CACHE_NAME, before, candidate, backup_id,
                                 missing_config=missing_config)
        except HistoricalEvidenceError as error:
            raise RepairError(str(error)) from None
        except (Unsupported, sqlite3.Error) as error:
            raise RepairError("desktop_ineligible") from error
    return backup_id


def restore(plan: RestorePlan, store: Path, *, approved: bool) -> str | None:
    if not approved:
        raise RepairError("approval_required")
    with _mutex(plan.home), pinned_directory(plan.home):
        ensure_closed()
        header, original = _read_backup(store / "desktop", plan.backup_id)
        profile = _profile_identity(plan.home)
        if (profile != plan.profile or not _matches_profile(header, plan.home, profile)
                or header["before"] != plan.original_hash or len(original) != plan.size):
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
    displaced = plan.home / (".ark-" + plan.backup_id + ".original")
    with ExitStack() as guard:
        try:
            source = guard.enter_context(stable_file(displaced, allow_rename=True))
        except FileNotFoundError:
            raise RepairError("stale") from None
        if _read_bounded(source) != original:
            raise RepairError("stale")
        expected = os.fstat(source.fileno())
        _require_missing(target)
        try:
            # Preserve the open original and its metadata without overwriting a new target.
            _publish_backup(displaced, target)
        except OSError:
            _require_missing(target)
            raise
        try:
            with stable_file(target) as restored:
                if (not os.path.samestat(expected, os.fstat(restored.fileno()))
                        or _read_bounded(restored) != original):
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
