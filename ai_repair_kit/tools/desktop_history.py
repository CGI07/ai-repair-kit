"""Merge only confirmed Desktop project links from an explicit historical snapshot."""

import sqlite3
from collections import defaultdict
from pathlib import Path

from ..local_files import ordinary_path
from .desktop_catalog import (
    Unsupported, _rows, canonical_id, columns, legacy_keys, pending_assignment_migration, projects,
)

HISTORY_BUILD = "26.1013.3656.0"
RECENT_HISTORY_BUILD = "26.1029.7803.0"
HISTORY_BUILDS = (HISTORY_BUILD, RECENT_HISTORY_BUILD)
REGISTRY = "local-projects"
ASSIGNMENTS = "thread-project-assignments"
HOST_MAP = "app-server-project-id-by-legacy-project-id-by-host"
ORDER = "project-order"
PROJECTLESS = "projectless-thread-ids"


def _object(data: dict, key: str) -> dict:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise Unsupported("unexpected desktop object")
    return value


def _string_list(data: dict, key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise Unsupported("unexpected desktop list")
    return value


def _aliases(mapping: dict) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = defaultdict(set)
    for key, project in mapping.items():
        if canonical_id(project):
            aliases[project].add(key)
    return aliases


def matches_recent_history(cache: dict) -> bool:
    return (cache.get(REGISTRY) == {} and not _object(cache, ASSIGNMENTS)
            and pending_assignment_migration(cache) is not None)


def merge_history(db: sqlite3.Connection, current: dict, historical: dict) -> tuple[dict, int, int, int]:
    catalog = projects(db)
    legacy = legacy_keys(db)
    old_hosts = _object(historical, HOST_MAP)
    old_mapping = _object(old_hosts, "local")
    new_hosts = dict(_object(current, HOST_MAP))
    current_mapping = _object(new_hosts, "local")
    current_aliases = _aliases(current_mapping)
    historical_aliases = _aliases(old_mapping)
    registry = dict(_object(current, REGISTRY))
    assignments = dict(_object(current, ASSIGNMENTS))
    historical_assignments = _object(historical, ASSIGNMENTS)
    projectless = set(_string_list(current, PROJECTLESS) + _string_list(historical, PROJECTLESS))
    order = list(_string_list(current, ORDER))
    old_order = _string_list(historical, ORDER)
    if len(set(order)) != len(order) or len(set(old_order)) != len(old_order):
        raise Unsupported("duplicate project order")
    validated = {}
    added = 0
    affected = set()
    for key, old in _object(historical, REGISTRY).items():
        core = old_mapping.get(key)
        if not isinstance(core, str) or core not in catalog or legacy.get(core) != [key]:
            continue
        project = catalog[core]
        if not _same_project(old, key, project.name, project.roots):
            continue
        if key in registry and not _same_project(registry[key], key, project.name, project.roots):
            continue
        if key in current_mapping and current_mapping[key] != core:
            continue
        if current_aliases.get(core, set()) - {key} or historical_aliases[core] != {key}:
            continue
        try:
            for root in project.roots:
                ordinary_path(Path(root), directory=True)
        except OSError:
            continue
        if not project.roots:
            continue
        if key not in registry:
            registry[key] = {
                "id": key, "name": project.name, "rootPaths": list(project.roots),
                "createdAt": project.created, "updatedAt": project.updated,
            }
            added += 1
            affected.add(key)
        validated[key] = core
    schema = columns(db, "threads")
    if not {"id", "project_id"} <= schema.keys() or {name for name, col in schema.items() if col[5]} != {"id"}:
        raise Unsupported("thread identity schema")
    rows = _rows(db, "SELECT id, project_id FROM main.threads")
    threads = dict(rows)
    if len(threads) != len(rows):
        raise Unsupported("thread identity rows")
    linked = 0
    skipped = 0
    for thread, key in historical_assignments.items():
        if (not canonical_id(thread) or not isinstance(key, str) or key not in validated
                or thread in projectless or thread not in threads or thread in assignments
                or threads[thread] not in (None, validated[key])):
            skipped += 1
            continue
        assignments[thread] = key
        linked += 1
        affected.add(key)
    if not added and not linked:
        raise Unsupported("no historically corroborated changes")
    merged = dict(current)
    if added:
        merged[REGISTRY] = registry
    if linked:
        merged[ASSIGNMENTS] = assignments
    mapping = current_mapping | {key: core for key, core in validated.items() if key in affected}
    if mapping != current_mapping:
        new_hosts["local"] = mapping
        merged[HOST_MAP] = new_hosts
    extra_order = [key for key in old_order if key in affected and key not in order]
    if extra_order:
        merged[ORDER] = order + extra_order
    return merged, added, linked, skipped


def _same_project(entry: object, key: str, name: str, roots: tuple[str, ...]) -> bool:
    return (
        isinstance(entry, dict) and entry.get("id") == key and entry.get("name") == name
        and entry.get("rootPaths") == list(roots)
    )
