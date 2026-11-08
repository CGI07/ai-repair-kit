"""Read-only checks for the supported Codex metadata and configuration."""

import json
import os
import sqlite3
import tomllib
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator
from uuid import UUID

from ..local_files import UnsafePath, ordinary_path, stable_file
from .codex import installed_desktop_version
from .desktop_catalog import (
    STATE_NAME, DamagedSnapshot, SnapshotHasSidecars, catalog_status, check_rollout_file,
    columns, local_rollout_path, open_snapshot,
)

CONFIG_LIMIT = 1024 * 1024
INDEX_LIMIT = 4 * 1024 * 1024
ROW_LIMIT = 2000


class Code(Enum):
    HOME_MISSING = "home_missing"
    HOME_UNAVAILABLE = "home_unavailable"
    CONFIG_MISSING = "config_missing"
    CONFIG_UNAVAILABLE = "config_unavailable"
    CONFIG_INVALID = "config_invalid"
    CONFIG_ALL_NUL = "config_all_nul"
    INDEX_UNAVAILABLE = "index_unavailable"
    INDEX_MALFORMED = "index_malformed"
    INDEX_UNKNOWN = "index_unknown"
    DB_MISSING = "db_missing"
    DB_OTHER_VERSION = "db_other_version"
    DB_UNAVAILABLE = "db_unavailable"
    DB_SIDECARS = "db_sidecars"
    DB_WAL_DIAGNOSTIC = "db_wal_diagnostic"
    DB_DAMAGED = "db_damaged"
    DB_SCHEMA = "db_schema"
    ROW_LIMIT = "row_limit"
    PROJECT_MISSING = "project_missing"
    PROJECT_UNAVAILABLE = "project_unavailable"
    ROLLOUT_MISSING = "rollout_missing"
    ROLLOUT_UNAVAILABLE = "rollout_unavailable"
    DESKTOP_UNAVAILABLE = "desktop_unavailable"
    DESKTOP_SCHEMA = "desktop_schema"
    DESKTOP_PROJECTS = "desktop_projects"
    DESKTOP_ROOT_MISSING = "desktop_root_missing"
    DESKTOP_CACHE_EMPTY = "desktop_cache_empty"
    DESKTOP_CACHE_UNAVAILABLE = "desktop_cache_unavailable"
    DESKTOP_CACHE_INCOMPLETE = "desktop_cache_incomplete"
    CODEX_HOME_OVERRIDE = "codex_home_override"
    CODEX_HOME_MISMATCH = "codex_home_mismatch"
    DB_HOME_REDIRECTED = "db_home_redirected"
    DESKTOP_PROFILE_QUALIFIED_MIGRATION = "desktop_profile_qualified_migration"
    DESKTOP_LEGACY_IDENTITY_MISSING = "desktop_legacy_identity_missing"
    DESKTOP_LEGACY_IDENTITY_AMBIGUOUS = "desktop_legacy_identity_ambiguous"
    DESKTOP_CACHE_OBJECT_LINKS = "desktop_cache_object_links"
    DESKTOP_CACHE_STALE_LINKS = "desktop_cache_stale_links"
    DESKTOP_CACHE_LINK_CONFLICTS = "desktop_cache_link_conflicts"
    DESKTOP_CANONICAL_LINKS_WITHOUT_CACHE = "desktop_canonical_links_without_cache"
    DESKTOP_PROJECT_TIMESTAMPS_MISSING = "desktop_project_timestamps_missing"
    DESKTOP_ROOT_SHARED = "desktop_root_shared"
    DESKTOP_ASSIGNMENT_MIGRATION_PENDING = "desktop_assignment_migration_pending"
    DESKTOP_ASSIGNMENT_MIGRATION_ORPHANS = "desktop_assignment_migration_orphans"
    DESKTOP_ASSIGNMENT_PROJECTLESS = "desktop_assignment_projectless"
    DESKTOP_UNASSIGNED = "desktop_unassigned"
    DESKTOP_CANDIDATES = "desktop_candidates"
    DESKTOP_DANGLING = "desktop_dangling"
    DESKTOP_THREAD_LIMIT = "desktop_thread_limit"
    GOAL_UNAVAILABLE = "goal_unavailable"
    GOAL_SIDECARS = "goal_sidecars"
    GOAL_LEGACY = "goal_legacy"
    GOAL_SPLIT = "goal_split"
    GOAL_ORPHAN = "goal_orphan"
    GOAL_NONE = "goal_none"
    GOAL_DUAL = "goal_dual"
    GOAL_THREAD_CHECK_SKIPPED = "goal_thread_check_skipped"
    DESKTOP_SESSION_THREAD_CHECK_SKIPPED = "desktop_session_thread_check_skipped"
    DESKTOP_SESSION_UNAVAILABLE = "desktop_session_unavailable"
    DESKTOP_SESSION_UNLISTED = "desktop_session_unlisted"
    DESKTOP_SESSION_DUPLICATE = "desktop_session_duplicate"
    DESKTOP_SESSION_INDEX_ONLY = "desktop_session_index_only"


@dataclass(frozen=True)
class Finding:
    code: Code
    count: int = 1


@dataclass(frozen=True)
class Report:
    findings: tuple[Finding, ...]
    null_config_size: int = 0
    db_home: Path | None = None
    sqlite_override: Path | None = None
    desktop_version: str | None = None


def inspect(home: Path, sqlite_override: Path | None = None, *,
            check_desktop_version: bool = False) -> Report:
    version = installed_desktop_version() if check_desktop_version else None
    findings = []
    override = os.environ.get("CODEX_HOME")
    if override:
        code = Code.CODEX_HOME_OVERRIDE if home == Path(override) else Code.CODEX_HOME_MISMATCH
        findings.append(Finding(code))
    try:
        ordinary_path(home, directory=True)
    except FileNotFoundError:
        return Report((*findings, Finding(Code.HOME_MISSING)), desktop_version=version)
    except OSError:
        return Report((*findings, Finding(Code.HOME_UNAVAILABLE)), desktop_version=version)

    config_issue, null_size, config_db_home = _config(home / "config.toml")
    results = findings
    if config_issue:
        results.append(config_issue)
    index: set[str] = set()
    index_findings = _session_index(home / "session_index.jsonl", index)
    results.extend(index_findings)
    # User-config sqlite_home takes precedence. Relative paths are intentionally
    # unsupported here because the active Codex config stack can change the base.
    db_home = config_db_home or sqlite_override or home
    if db_home != home:
        results.append(Finding(Code.DB_HOME_REDIRECTED))
    with ExitStack() as snapshots:
        state_findings, state = _state_db(home, db_home, snapshots)
        results.extend(state_findings)
        desktop = catalog_status(home, db_home, None if index_findings else index, state=state)
    results.extend(Finding(Code(key), count) for key, count in desktop.items())
    return Report(tuple(results), null_size, db_home, sqlite_override, version)


@contextmanager
def locked_config(home: Path) -> Iterator[bytes | None]:
    """Keep an existing Codex config stable for the caller's bounded operation."""
    with ExitStack() as guard:
        try:
            stream = guard.enter_context(stable_file(home / "config.toml"))
        except FileNotFoundError:
            yield None
        else:
            yield stream.read(CONFIG_LIMIT + 1)


def _config(path: Path) -> tuple[Finding | None, int, Path | None]:
    try:
        with stable_file(path) as stream:
            data = stream.read(CONFIG_LIMIT + 1)
    except FileNotFoundError:
        return Finding(Code.CONFIG_MISSING), 0, None
    except OSError:
        return Finding(Code.CONFIG_UNAVAILABLE), 0, None
    return inspect_config_bytes(data)


def inspect_config_bytes(data: bytes) -> tuple[Finding | None, int, Path | None]:
    if len(data) > CONFIG_LIMIT:
        return Finding(Code.CONFIG_UNAVAILABLE), 0, None
    if data and not data.strip(b"\0"):
        return Finding(Code.CONFIG_ALL_NUL), len(data), None
    try:
        config = tomllib.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return Finding(Code.CONFIG_INVALID), 0, None
    sqlite_home = config.get("sqlite_home")
    if sqlite_home is not None and (not isinstance(sqlite_home, str) or not sqlite_home):
        return Finding(Code.CONFIG_INVALID), 0, None
    return None, 0, Path(sqlite_home) if sqlite_home else None


def _session_index(path: Path, ids: set[str]) -> list[Finding]:
    counts: Counter[Code] = Counter()
    try:
        with stable_file(path) as stream:
            if os.fstat(stream.fileno()).st_size > INDEX_LIMIT:
                return [Finding(Code.INDEX_UNAVAILABLE)]
            for line in stream:
                try:
                    entry = json.loads(line.decode("utf-8"))
                except (ValueError, RecursionError):
                    counts[Code.INDEX_MALFORMED] += 1
                    continue
                if not _index_entry(entry):
                    counts[Code.INDEX_UNKNOWN] += 1
                else:
                    ids.add(str(UUID(entry["id"])))
    except FileNotFoundError:
        return []  # The optional name index is created when needed.
    except OSError:
        return [Finding(Code.INDEX_UNAVAILABLE)]
    return [Finding(code, count) for code, count in counts.items()]


def _index_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    if not all(isinstance(entry.get(field), str) for field in ("id", "thread_name", "updated_at")):
        return False
    try:
        UUID(entry["id"])
    except ValueError:
        return False
    return True


def _state_db(home: Path, db_home: Path, snapshots: ExitStack) -> tuple[list[Finding], sqlite3.Connection | None]:
    try:
        snapshot = snapshots.enter_context(
            open_snapshot(db_home / STATE_NAME, allow_wal=True, allow_empty_wal=True)
        )
        db = snapshot.connection
        if not {"id", "cwd", "rollout_path"} <= columns(db, "threads").keys():
            return [Finding(Code.DB_SCHEMA)], None
        findings = _db_metadata(home, db)
        if snapshot.uses_wal:
            findings.insert(0, Finding(Code.DB_WAL_DIAGNOSTIC))
        return findings, db
    except FileNotFoundError:
        try:
            return [Finding(_missing_db(db_home))], None
        except OSError:
            return [Finding(Code.DB_UNAVAILABLE)], None
    except SnapshotHasSidecars:
        return [Finding(Code.DB_SIDECARS)], None
    except DamagedSnapshot:
        return [Finding(Code.DB_DAMAGED)], None
    except sqlite3.Error as error:
        result = getattr(error, "sqlite_errorcode", 0) & 0xFF
        code = Code.DB_DAMAGED if result in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB) else Code.DB_UNAVAILABLE
        return [Finding(code)], None
    except (OSError, ValueError):
        return [Finding(Code.DB_UNAVAILABLE)], None


def _missing_db(db_home: Path) -> Code:
    try:
        ordinary_path(db_home, directory=True)
    except FileNotFoundError:
        return Code.DB_MISSING
    with os.scandir(db_home) as entries:
        for index, entry in enumerate(entries):
            if index >= ROW_LIMIT:
                return Code.DB_UNAVAILABLE
            if entry.name.startswith("state_") and entry.name.endswith(".sqlite"):
                version = entry.name.removeprefix("state_").removesuffix(".sqlite")
                if version.isascii() and version.isdigit():
                    return Code.DB_OTHER_VERSION
    return Code.DB_MISSING


def _db_metadata(home: Path, connection: sqlite3.Connection) -> list[Finding]:
    # Avoid pulling arbitrary-sized values, titles, or conversation content.
    rows = connection.execute(
        "SELECT CASE WHEN length(cwd) <= 4096 THEN cwd END, "
        "CASE WHEN length(rollout_path) <= 4096 THEN rollout_path END "
        "FROM main.threads LIMIT ?", (ROW_LIMIT + 1,),
    ).fetchall()

    counts: Counter[Code] = Counter()
    if len(rows) > ROW_LIMIT:
        counts[Code.ROW_LIMIT] = ROW_LIMIT
    for cwd, rollout in rows[:ROW_LIMIT]:
        project_issue = _reference(cwd, directory=True)
        if project_issue:
            counts[project_issue] += 1
        rollout_issue = _reference(rollout, directory=False, home=home)
        if rollout_issue:
            counts[rollout_issue] += 1
    return [Finding(code, count) for code, count in counts.items()]


def _reference(value: object, *, directory: bool, home: Path | None = None) -> Code | None:
    missing = Code.PROJECT_MISSING if directory else Code.ROLLOUT_MISSING
    unavailable = Code.PROJECT_UNAVAILABLE if directory else Code.ROLLOUT_UNAVAILABLE
    if not isinstance(value, str) or not value or "\0" in value:
        return unavailable
    try:
        if home is None:
            ordinary_path(Path(value), directory=directory)
        else:
            check_rollout_file(local_rollout_path(home, value))
    except FileNotFoundError:
        return missing
    except (OSError, ValueError):
        return unavailable
    return None
