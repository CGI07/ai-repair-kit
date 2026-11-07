"""Read the upstream Codex project schema and bounded Desktop metadata."""

import json
import os
import sqlite3
import time
from collections import Counter
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator
from uuid import UUID

from ..local_files import ordinary_path, stable_file

CACHE_NAME = ".codex-global-state.json"
CACHE_LIMIT = 4 * 1024 * 1024
STATE_NAME = "state_5.sqlite"
GOAL_NAME = "goals_1.sqlite"
DB_LIMIT = 128 * 1024 * 1024
ROW_LIMIT = 10000


class Unsupported(ValueError):
    """Unrecognized or incomplete evidence. No writes are safe."""


class SnapshotHasSidecars(Unsupported):
    """A SQLite journal may contain changes absent from the main file."""


class DamagedSnapshot(Unsupported):
    """SQLite's integrity check failed."""


@dataclass(frozen=True)
class Snapshot:
    connection: sqlite3.Connection
    uses_wal: bool


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    created: int
    updated: int
    roots: tuple[str, ...]


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = dict(items)
    if len(result) != len(items):
        raise Unsupported("duplicate JSON key")
    return result


def _constant(value: str) -> None:
    raise Unsupported("nonstandard JSON constant")


def decode_cache(data: bytes) -> dict:
    if not 0 < len(data) <= CACHE_LIMIT:
        raise Unsupported("cache size")
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, RecursionError) as error:
        raise Unsupported("invalid desktop cache") from error
    if not isinstance(result, dict):
        raise Unsupported("invalid desktop cache")
    return result


def read_cache(home: Path) -> tuple[bytes, dict]:
    with stable_file(home / CACHE_NAME) as stream:
        data = stream.read(CACHE_LIMIT + 1)
    return data, decode_cache(data)


@contextmanager
def open_snapshot(path: Path, *, allow_wal: bool = False) -> Iterator[Snapshot]:
    with ExitStack() as guard:
        stream = guard.enter_context(stable_file(path))
        if os.fstat(stream.fileno()).st_size > DB_LIMIT:
            raise Unsupported("database size")
        sidecars = {}
        for suffix in ("-wal", "-shm", "-journal"):
            candidate = path.with_name(path.name + suffix)
            try:
                candidate.lstat()
            except FileNotFoundError:
                continue
            sidecars[suffix] = candidate

        options = "mode=ro&immutable=1"
        if sidecars:
            if not allow_wal or os.name != "nt" or sidecars.keys() != {"-wal", "-shm"}:
                raise SnapshotHasSidecars("sqlite sidecars")
            try:
                wal = guard.enter_context(stable_file(sidecars["-wal"]))
                guard.enter_context(stable_file(sidecars["-shm"]))
            except OSError:
                raise SnapshotHasSidecars("sqlite sidecars are busy or unsupported") from None
            if (not 32 <= os.fstat(wal.fileno()).st_size <= DB_LIMIT
                    or wal.read(4) not in (b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83")):
                raise SnapshotHasSidecars("sqlite WAL header or size")
            # All three Windows file handles deny writes and renames. Rebuild the
            # WAL index in memory without opening the on-disk SHM in SQLite.
            options = "mode=ro&vfs=win32-none"

        uri = path.as_uri() + "?" + options
        with closing(sqlite3.connect(uri, uri=True, timeout=0)) as db:
            deadline = time.monotonic() + 4
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA temp_store=MEMORY")
            if sidecars and (db.execute("PRAGMA locking_mode=EXCLUSIVE").fetchone() != ("exclusive",)
                             or db.execute("PRAGMA journal_mode").fetchone() != ("wal",)):
                raise SnapshotHasSidecars("sqlite WAL mode unavailable")
            if db.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                raise DamagedSnapshot("database integrity")
            yield Snapshot(db, bool(sidecars))


def columns(db: sqlite3.Connection, table: str) -> dict[str, tuple]:
    # Only literal, internal table names are allowed at call sites.
    if ("main", table, "table") not in {
        row[:3] for row in db.execute(f"PRAGMA table_list('{table}')")
    }:
        return {}
    return {row[1]: row for row in db.execute(f"PRAGMA table_info('{table}')")}


def _rows(db: sqlite3.Connection, query: str) -> list[tuple]:
    rows = db.execute(query + " LIMIT ?", (ROW_LIMIT + 1,)).fetchall()
    if len(rows) > ROW_LIMIT:
        raise Unsupported("row limit")
    return rows


def canonical_id(value: object) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def projects(db: sqlite3.Connection) -> dict[str, Project]:
    specs = {
        "projects": {"id", "name", "position", "created_at_ms", "updated_at_ms"},
        "project_roots": {"project_id", "position", "path"},
        "project_idempotency_keys": {"key", "project_id"},
    }
    if any(not required <= columns(db, table).keys() for table, required in specs.items()):
        raise Unsupported("project schema")
    if not all(columns(db, table)[key][5] == 1 for table, key in (
        ("projects", "id"), ("project_roots", "project_id"), ("project_idempotency_keys", "key")
    )):
        raise Unsupported("project key schema")

    root_rows = _rows(db, "SELECT project_id, position, CASE WHEN length(path) <= 4096 THEN path END "
                          "FROM main.project_roots ORDER BY project_id, position")
    root_map: dict[str, list[str]] = {}
    for project_id, pos, root in root_rows:
        if not canonical_id(project_id) or type(pos) is not int or not isinstance(root, str) or not root:
            raise Unsupported("project root row")
        root_map.setdefault(project_id, []).append(root)
    result = {}
    for key, name, created, updated in _rows(
        db, "SELECT id, CASE WHEN length(name) <= 512 THEN name END, created_at_ms, updated_at_ms "
            "FROM main.projects ORDER BY position, id"
    ):
        if (not canonical_id(key) or not isinstance(name, str) or not name.strip()
                or type(created) is not int or type(updated) is not int
                or not 0 < created <= updated < 9_007_199_254_740_992):
            raise Unsupported("project row")
        result[key] = Project(key, name, created, updated, tuple(root_map.pop(key, ())))
    if root_map:
        raise Unsupported("orphan project roots")
    return result


def legacy_keys(db: sqlite3.Connection) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for key, project_id in _rows(
        db, "SELECT CASE WHEN length(key) <= 256 THEN key END, project_id FROM main.project_idempotency_keys"
    ):
        if not isinstance(key, str) or not key or any(ord(c) < 32 for c in key) or not canonical_id(project_id):
            raise Unsupported("legacy key row")
        keys.setdefault(project_id, []).append(key)
    return keys


def catalog_status(home: Path, db_home: Path, index: set[str] | None, *,
                   state: sqlite3.Connection | None) -> dict[str, int]:
    """Compare against the same guarded state snapshot used by the main scan."""
    counts: Counter[str] = Counter()
    known: set[str] | None = None
    if state is not None:
        try:
            known = _thread_ids(state)
            _project_status(home, state, known, counts)
            _goal_status(state, known, counts, "legacy")
        except (OSError, ValueError, sqlite3.Error):
            counts["desktop_unavailable"] += 1

    try:
        with open_snapshot(db_home / GOAL_NAME, allow_wal=True) as goals:
            _goal_status(goals.connection, known, counts, "split")
    except FileNotFoundError:
        pass
    except SnapshotHasSidecars:
        counts["goal_sidecars"] += 1
    except (OSError, ValueError, sqlite3.Error):
        counts["goal_unavailable"] += 1

    _session_status(home, known, index, counts)

    if counts["goal_legacy"] and counts["goal_split"]:
        counts["goal_dual"] += 1
    if known is None and (counts["goal_legacy"] or counts["goal_split"]):
        counts["goal_thread_check_skipped"] += 1
    if counts["desktop_projects"] and not any(counts[key] for key in (
        "goal_legacy", "goal_split", "goal_unavailable", "goal_sidecars", "desktop_unavailable"
    )):
        counts["goal_none"] += 1
    return {key: count for key, count in counts.items() if count}


def _thread_ids(db: sqlite3.Connection) -> set[str] | None:
    if "id" not in columns(db, "threads"):
        return None
    try:
        rows = _rows(db, "SELECT id FROM main.threads")
    except (sqlite3.Error, Unsupported):
        return None
    known = {key for key, in rows if canonical_id(key)}
    return known if len(known) == len(rows) else None


def pending_assignment_migration(cache: dict) -> dict | None:
    hosts = cache.get("app-server-projects-migration-by-host")
    local = hosts.get("local") if isinstance(hosts, dict) else None
    if (isinstance(local, dict) and local.get("projectsMigrated") is True
            and local.get("threadAssignmentsMigrated") is False):
        return local
    return None


def _migration_status(cache: dict, known: set[str] | None, counts: Counter[str]) -> None:
    local = pending_assignment_migration(cache)
    if local is None:
        return
    counts["desktop_assignment_migration_pending"] += 1
    pending = local.get("pendingThreadAssignmentIds")
    if (known is not None and isinstance(pending, list) and len(pending) <= ROW_LIMIT
            and all(canonical_id(key) for key in pending)):
        counts["desktop_assignment_migration_orphans"] += len(set(pending) - known)


def _project_status(home: Path, db: sqlite3.Connection, known: set[str] | None,
                    counts: Counter[str]) -> None:
    thread_cols = columns(db, "threads")
    if "project_id" not in thread_cols and not columns(db, "projects"):
        return
    if not {"id", "project_id", "cwd"} <= thread_cols.keys():
        counts["desktop_schema"] += 1
        return
    try:
        catalog = projects(db)
    except Unsupported:
        counts["desktop_schema"] += 1
        return
    counts["desktop_projects"] += len(catalog)
    roots: dict[Path, set[str]] = {}
    for project in catalog.values():
        if not project.roots:
            counts["desktop_root_missing"] += 1
        for root in project.roots:
            path = Path(root)
            try:
                ordinary_path(path, directory=True)
            except OSError:
                counts["desktop_root_missing"] += 1
                continue
            roots.setdefault(path, set()).add(project.id)
    counts["desktop_root_shared"] += sum(len(owners) > 1 for owners in roots.values())
    try:
        _, cache = read_cache(home)
        _migration_status(cache, known, counts)
        registry = cache.get("local-projects")
        if catalog and registry == {}:
            counts["desktop_cache_empty"] += 1
        elif catalog and not isinstance(registry, dict):
            counts["desktop_cache_unavailable"] += 1
        elif isinstance(registry, dict):
            keys = legacy_keys(db)
            missing = sum(
                len(keys.get(project_id, [])) == 1 and keys[project_id][0] not in registry
                for project_id in catalog
            )
            if registry and missing:
                counts["desktop_cache_incomplete"] += missing
    except (OSError, Unsupported):
        if catalog:
            counts["desktop_cache_unavailable"] += 1
    try:
        rows = _rows(db, "SELECT project_id, CASE WHEN length(cwd) <= 4096 THEN cwd END "
                         "FROM main.threads")
    except Unsupported:
        counts["desktop_thread_limit"] += 1
        return
    for project_id, cwd in rows:
        if project_id is None:
            counts["desktop_unassigned"] += 1
            owners = roots.get(Path(cwd), set()) if isinstance(cwd, str) else set()
            if len(owners) == 1:
                counts["desktop_candidates"] += 1
        elif project_id not in catalog:
            counts["desktop_dangling"] += 1


def _goal_status(goals: sqlite3.Connection, known: set[str] | None,
                 counts: Counter[str], label: str) -> None:
    schema = columns(goals, "thread_goals")
    if not schema and label == "legacy" and not goals.execute(
        "SELECT 1 FROM main.sqlite_schema WHERE name = 'thread_goals'"
    ).fetchone():
        return
    primary_key = {name for name, column in schema.items() if column[5]}
    if not {"thread_id", "goal_id", "status", "objective"} <= schema.keys() or primary_key != {"thread_id"}:
        counts["goal_unavailable"] += 1
        return
    try:
        goal_ids = _rows(goals, "SELECT thread_id FROM main.thread_goals")
    except (sqlite3.Error, Unsupported):
        counts["goal_unavailable"] += 1
        return
    if any(not canonical_id(key) for key, in goal_ids):
        counts["goal_unavailable"] += 1
        return
    counts["goal_" + label] += len(goal_ids)
    if known is not None:
        counts["goal_orphan"] += sum(key not in known for key, in goal_ids)

def local_rollout_path(home: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise Unsupported("invalid rollout path")
    path = Path(value)
    roots = (home / "sessions", home / "archived_sessions")
    if (not path.is_absolute() or ".." in path.parts
            or not any(path.is_relative_to(root) for root in roots)
            or not path.name.startswith("rollout-") or not path.name.endswith((".jsonl", ".jsonl.zst"))):
        raise Unsupported("unsupported rollout path")
    return path


def check_rollout_file(path: Path) -> None:
    """Stat a known rollout path or its plain/compressed counterpart."""
    try:
        ordinary_path(path, directory=False)
    except FileNotFoundError:
        alternate = path.with_suffix("") if path.suffix == ".zst" else path.with_name(path.name + ".zst")
        ordinary_path(alternate, directory=False)


def rollout_identity(name: str) -> tuple[str, str] | None:
    name = name.removesuffix(".zst")
    if not name.startswith("rollout-") or not name.endswith(".jsonl"):
        return None
    body = name[8:-6]
    if len(body) < 20 or body[19] != "-":
        return None
    thread_id, separator, rollout_id = body[20:].partition("_")
    if not canonical_id(thread_id) or (separator and not canonical_id(rollout_id)):
        return None
    try:
        datetime.strptime(body[:19], "%Y-%m-%dT%H-%M-%S")
    except ValueError:
        return None
    return thread_id, rollout_id if separator else thread_id


def session_files(home: Path, *, archived: bool, thread_id: str | None = None) -> Iterator[tuple[str, str]]:
    """Yield thread and rollout IDs from filenames. Never open the files."""
    pending = [(home / "archived_sessions", 0)] if archived else [(home / "sessions", 3)]
    visited = 0
    while pending:
        path, level = pending.pop()
        try:
            ordinary_path(path, directory=True)
        except FileNotFoundError:
            continue
        seen: set[str] = set()
        with os.scandir(path) as entries:
            for entry in entries:
                visited += 1
                if visited > 20_000:
                    raise Unsupported("session scan limit")
                if level:
                    name = entry.name
                    if len(name) == (4 if level == 3 else 2) and name.isascii() and name.isdigit():
                        pending.append((Path(entry.path), level - 1))
                    continue
                # A plain file and its compressed sibling are one logical rollout.
                name = entry.name.removesuffix(".zst")
                identity = rollout_identity(name)
                if identity is None or name in seen or (thread_id is not None and identity[0] != thread_id):
                    continue
                ordinary_path(Path(entry.path), directory=False)
                seen.add(name)
                yield identity


def _session_status(home: Path, known: set[str] | None, index: set[str] | None,
                    counts: Counter[str]) -> None:
    try:
        files = Counter(session_files(home, archived=False))
        files.update(session_files(home, archived=True))
    except (OSError, ValueError):
        counts["desktop_session_unavailable"] += 1
        return
    counts["desktop_session_duplicate"] += sum(count > 1 for count in files.values())
    thread_ids = {key for key, _ in files}
    if known is None:
        counts["desktop_session_thread_check_skipped"] += len(thread_ids)
        return
    counts["desktop_session_unlisted"] += len(thread_ids - known)
    if index is not None:
        counts["desktop_session_index_only"] += len(index - known - thread_ids)

