"""Read the exact thread and Goal presence and session filenames without opening the conversation."""

import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from ..local_files import ordinary_path
from .codex_diagnostics import Report, inspect_config_bytes, locked_config
from .desktop_catalog import (
    GOAL_NAME, STATE_NAME, SnapshotHasSidecars, canonical_id, check_rollout_file, columns, local_rollout_path,
    open_snapshot, read_cache, rollout_identity, session_files,
)
from .desktop_history import ASSIGNMENTS, HOST_MAP, PROJECTLESS, REGISTRY, _object, _string_list


class Presence(Enum):
    FOUND = auto()
    ABSENT = auto()
    STORE_MISSING = auto()
    TABLE_MISSING = auto()
    SIDECARS = auto()
    UNAVAILABLE = auto()


class RolloutReference(Enum):
    NOT_CHECKED = auto()
    NOT_RECORDED = auto()
    FOUND = auto()
    FOUND_ARCHIVED = auto()
    MISSING = auto()
    WRONG_THREAD = auto()
    UNAVAILABLE = auto()


class ProjectLink(Enum):
    NOT_CHECKED = auto()
    UNAVAILABLE = auto()
    UNASSIGNED = auto()
    PROJECTLESS = auto()
    LINKED = auto()
    MISSING_PROJECT = auto()
    INCOMPLETE_MAPPING = auto()
    CONFLICT = auto()


class ProjectComparison(Enum):
    NOT_COMPARABLE = auto()
    AGREE = auto()
    CONFLICT = auto()


@dataclass(frozen=True)
class ProjectLinks:
    database: ProjectLink
    cache: ProjectLink
    comparison: ProjectComparison = ProjectComparison.NOT_COMPARABLE


class GoalStatus(Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    USAGE_LIMITED = "usage_limited"
    BUDGET_LIMITED = "budget_limited"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


class GoalComparison(Enum):
    NOT_COMPARED = auto()
    SAME_GOAL_AND_STATUS = auto()
    SAME_GOAL_UNKNOWN_STATUS = auto()
    DIFFERENT_STATUS = auto()
    DIFFERENT_GOAL = auto()
    UNAVAILABLE = auto()


@dataclass(frozen=True)
class GoalDetails:
    legacy: GoalStatus | None = None
    separate: GoalStatus | None = None
    comparison: GoalComparison = GoalComparison.NOT_COMPARED


@dataclass(frozen=True)
class ThreadReport:
    thread: Presence
    legacy_goal: Presence
    separate_goal: Presence
    active_files: int | None = 0
    archived_files: int | None = 0
    rollout_reference: RolloutReference = RolloutReference.NOT_CHECKED
    project_links: ProjectLinks = ProjectLinks(ProjectLink.NOT_CHECKED, ProjectLink.NOT_CHECKED)
    goal_details: GoalDetails = GoalDetails()
    archived: bool | None = None
    history_index: Presence | None = None


class ThreadLocationChanged(ValueError):
    """The profile no longer has the SQLite location used for the scan."""


def inspect_scanned_thread(home: Path, scan: Report, key: str) -> ThreadReport:
    with locked_config(home) as config:
        configured = None
        if config is not None:
            issue, _, configured = inspect_config_bytes(config)
            if issue and not any(finding.code is issue.code for finding in scan.findings):
                raise ThreadLocationChanged
        db_home = configured or scan.sqlite_override or home
        if db_home != scan.db_home:
            raise ThreadLocationChanged
        result = inspect_thread(home, db_home, key)
        if config is None:
            # A missing file cannot be guarded against creation. Discard the result if it appeared.
            try:
                (home / "config.toml").lstat()
            except FileNotFoundError:
                pass
            else:
                raise ThreadLocationChanged
        return result


def inspect_thread(home: Path, db_home: Path, key: str) -> ThreadReport:
    if not canonical_id(key):
        raise ValueError("Expected a canonical thread ID")

    reference = RolloutReference.NOT_CHECKED
    database_link, database_id = ProjectLink.NOT_CHECKED, None
    legacy_record = separate_record = None
    archived = None
    history_index = None
    # Keep the state snapshot stable while opening the separate Goal store.
    with ExitStack() as snapshots:
        try:
            state = snapshots.enter_context(open_snapshot(db_home / STATE_NAME, allow_wal=True)).connection
            thread = _row(state, "threads", "id", key)
            if thread is Presence.FOUND:
                archived = _archive_flag(state, key)
                history_index = _history_index_presence(state, db_home, key)
                reference = _rollout_reference(home, state, key)
                database_link, database_id = _database_project(state, key)
            legacy, legacy_record = _goal_record(state, key)
        except FileNotFoundError:
            thread = legacy = Presence.STORE_MISSING
        except SnapshotHasSidecars:
            thread = legacy = Presence.SIDECARS
        except (OSError, ValueError, sqlite3.Error):
            thread = legacy = Presence.UNAVAILABLE
        cache_link, cache_id = _cache_project(home, key)

        try:
            goals = snapshots.enter_context(open_snapshot(db_home / GOAL_NAME, allow_wal=True)).connection
            separate, separate_record = _goal_record(goals, key)
        except FileNotFoundError:
            separate = Presence.STORE_MISSING
        except SnapshotHasSidecars:
            separate = Presence.SIDECARS
        except (OSError, ValueError, sqlite3.Error):
            separate = Presence.UNAVAILABLE
    return ThreadReport(thread, legacy, separate,
                        _session_count(home, key, archived=False),
                        _session_count(home, key, archived=True), reference,
                        _compare_projects(database_link, database_id, cache_link, cache_id),
                        _compare_goals(legacy_record, separate_record), archived, history_index)


def _history_index_presence(db: sqlite3.Connection, db_home: Path, key: str) -> Presence | None:
    fields = "history_mode COLLATE BINARY = 'paginated'"
    _, row = _matching_row(db, "threads", "id", key, fields)
    if row != (1,):
        return None
    try:
        # This database can contain conversation bodies. Check file metadata only.
        ordinary_path(db_home / "thread_history_1.sqlite", directory=False)
    except FileNotFoundError:
        return Presence.STORE_MISSING
    except OSError:
        return Presence.UNAVAILABLE
    return Presence.FOUND


def _archive_flag(db: sqlite3.Connection, key: str) -> bool | None:
    fields = "CASE WHEN typeof(archived) = 'integer' AND archived IN (0, 1) THEN archived END"
    _, row = _matching_row(db, "threads", "id", key, fields)
    return bool(row[0]) if row is not None and row[0] is not None else None


def _database_project(db: sqlite3.Connection, key: str) -> tuple[ProjectLink, str | None]:
    try:
        if "project_id" not in columns(db, "threads"):
            return ProjectLink.UNAVAILABLE, None
        row = db.execute(
            "SELECT CASE WHEN typeof(project_id) = 'text' AND length(project_id) = 36 "
            "THEN project_id END, project_id IS NULL FROM main.threads WHERE id COLLATE BINARY = ?",
            (key,),
        ).fetchone()
        if row is None:
            return ProjectLink.UNAVAILABLE, None
        project, unassigned = row
        if unassigned:
            return ProjectLink.UNASSIGNED, None
        if not canonical_id(project):
            return ProjectLink.UNAVAILABLE, None
        status = _row(db, "projects", "id", project)
        if status is Presence.FOUND:
            return ProjectLink.LINKED, project
        if status is Presence.ABSENT:
            return ProjectLink.MISSING_PROJECT, None
    except sqlite3.Error:
        pass
    return ProjectLink.UNAVAILABLE, None


def _cache_project(home: Path, key: str) -> tuple[ProjectLink, str | None]:
    try:
        _, cache = read_cache(home)
        assignment = _object(cache, ASSIGNMENTS).get(key)
        if key in _string_list(cache, PROJECTLESS):
            status = ProjectLink.PROJECTLESS if assignment is None else ProjectLink.CONFLICT
            return status, None
        if assignment is None:
            return ProjectLink.UNASSIGNED, None
        if not isinstance(assignment, str) or not assignment:
            return ProjectLink.UNAVAILABLE, None
        entry = _object(cache, REGISTRY).get(assignment)
        project = _object(_object(cache, HOST_MAP), "local").get(assignment)
        if not isinstance(entry, dict) or entry.get("id") != assignment or not canonical_id(project):
            return ProjectLink.INCOMPLETE_MAPPING, None
        return ProjectLink.LINKED, project
    except (OSError, ValueError):
        return ProjectLink.UNAVAILABLE, None


def _compare_projects(database: ProjectLink, database_id: str | None,
                      cache: ProjectLink, cache_id: str | None) -> ProjectLinks:
    comparison = ProjectComparison.NOT_COMPARABLE
    if database_id is not None and cache_id is not None:
        comparison = ProjectComparison.AGREE if database_id == cache_id else ProjectComparison.CONFLICT
    elif database is ProjectLink.LINKED and cache is ProjectLink.PROJECTLESS:
        comparison = ProjectComparison.CONFLICT
    return ProjectLinks(database, cache, comparison)


def _rollout_reference(home: Path, db: sqlite3.Connection, key: str) -> RolloutReference:
    try:
        if "rollout_path" not in columns(db, "threads"):
            return RolloutReference.UNAVAILABLE
        row = db.execute(
            "SELECT CASE WHEN typeof(rollout_path) = 'text' AND length(rollout_path) <= 4096 "
            "THEN rollout_path END, rollout_path IS NULL OR rollout_path = '' "
            "FROM main.threads WHERE id COLLATE BINARY = ?", (key,)
        ).fetchone()
        if row is None:
            return RolloutReference.UNAVAILABLE
        value, absent = row
        if absent:
            return RolloutReference.NOT_RECORDED
        path = local_rollout_path(home, value)
        identity = rollout_identity(path.name)
        if identity is None:
            return RolloutReference.UNAVAILABLE
        if identity[0] != key:
            return RolloutReference.WRONG_THREAD
        check_rollout_file(path)
        if path.is_relative_to(home / "archived_sessions"):
            return RolloutReference.FOUND_ARCHIVED
        return RolloutReference.FOUND
    except FileNotFoundError:
        return RolloutReference.MISSING
    except (OSError, ValueError, sqlite3.Error):
        return RolloutReference.UNAVAILABLE


def _session_count(home: Path, key: str, *, archived: bool) -> int | None:
    try:
        return sum(1 for _ in session_files(home, archived=archived, thread_id=key))
    except (OSError, ValueError):
        return None


def _goal_record(db: sqlite3.Connection, key: str) -> tuple[Presence, tuple[str | None, GoalStatus] | None]:
    fields = (
        "CASE WHEN typeof(goal_id) = 'text' AND length(goal_id) BETWEEN 1 AND 128 THEN goal_id END, "
        "CASE WHEN typeof(status) = 'text' AND length(status) <= 32 THEN status END"
    )
    presence, row = _matching_row(db, "thread_goals", "thread_id", key, fields)
    if row is None:
        return presence, None
    identifier, raw_status = row
    try:
        status = GoalStatus(raw_status)
    except ValueError:
        status = GoalStatus.UNKNOWN
    return presence, (identifier, status)


def _compare_goals(legacy: tuple[str | None, GoalStatus] | None,
                   separate: tuple[str | None, GoalStatus] | None) -> GoalDetails:
    legacy_status = legacy[1] if legacy else None
    separate_status = separate[1] if separate else None
    comparison = GoalComparison.NOT_COMPARED
    if legacy is not None and separate is not None:
        if legacy[0] is None or separate[0] is None:
            comparison = GoalComparison.UNAVAILABLE
        elif legacy[0] != separate[0]:
            comparison = GoalComparison.DIFFERENT_GOAL
        elif GoalStatus.UNKNOWN in (legacy_status, separate_status):
            comparison = GoalComparison.SAME_GOAL_UNKNOWN_STATUS
        elif legacy_status != separate_status:
            comparison = GoalComparison.DIFFERENT_STATUS
        else:
            comparison = GoalComparison.SAME_GOAL_AND_STATUS
    return GoalDetails(legacy_status, separate_status, comparison)


def _row(db: sqlite3.Connection, table: str, field: str, key: str) -> Presence:
    return _matching_row(db, table, field, key, "1")[0]


def _matching_row(db: sqlite3.Connection, table: str, field: str, key: str,
                  selection: str) -> tuple[Presence, tuple | None]:
    # Identifiers and selection are literals from internal callers. Bind the UUID.
    try:
        schema = columns(db, table)
        if not schema:
            exists = db.execute("SELECT 1 FROM main.sqlite_schema WHERE name = ?", (table,)).fetchone()
            return (Presence.UNAVAILABLE if exists else Presence.TABLE_MISSING), None
        if field not in schema or {name for name, col in schema.items() if col[5]} != {field}:
            return Presence.UNAVAILABLE, None
        if table == "thread_goals" and not {"goal_id", "status", "objective"} <= schema.keys():
            return Presence.UNAVAILABLE, None
        row = db.execute(f"SELECT {selection} FROM main.{table} WHERE {field} COLLATE BINARY = ?", (key,)).fetchone()
        return (Presence.FOUND if row else Presence.ABSENT), row
    except sqlite3.Error:
        return Presence.UNAVAILABLE, None
