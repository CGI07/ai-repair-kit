"""Build two cache-only candidates from verified, version-specific evidence."""

import sqlite3
from pathlib import Path

from ..local_files import ordinary_path
from .desktop_catalog import (
    Unsupported, _rows, canonical_id, columns, legacy_keys, local_assignment_key, projects,
)
from .desktop_history import ASSIGNMENTS, HOST_MAP, ORDER, PROJECTLESS, REGISTRY, _object, _string_list

PARTIAL_REGISTRY_BUILD = "26.915.4065.0"
OBJECT_LINK_BUILD = "26.924.2738.0"


class HistoricalEvidenceError(Unsupported):
    """The selected snapshot has conflicting or insufficient evidence."""


def _require_identity_schema(db: sqlite3.Connection) -> None:
    for table, expected in (
        ("projects", {"id": 1}),
        ("project_roots", {"project_id": 1, "position": 2}),
        ("project_idempotency_keys", {"key": 1}),
    ):
        if {name: col[5] for name, col in columns(db, table).items() if col[5]} != expected:
            raise Unsupported("project key schema")


def build_registry(
    db: sqlite3.Connection, current: dict, historical: dict | None,
) -> tuple[dict, int, int, int]:
    """Rebuild only missing registrations, with optional proven object links."""
    _require_identity_schema(db)
    catalog, aliases = projects(db), legacy_keys(db)
    registry = current.get(REGISTRY)
    if not isinstance(registry, dict) or not catalog or set(aliases) != set(catalog):
        raise Unsupported("incomplete project identities")
    if _object(current, ASSIGNMENTS):
        raise Unsupported("existing cache assignments")
    if historical is not None and registry:
        raise Unsupported("object-link rule requires an empty registry")

    hosts = _object(current, HOST_MAP)
    migration = _object(current, "app-server-projects-migration-by-host")
    if any(key.startswith("local:") for key in (*hosts, *migration)):
        raise Unsupported("profile-qualified cache")
    current_map = _object(hosts, "local")
    order = _string_list(current, ORDER)
    if len(set(order)) != len(order):
        raise Unsupported("duplicate project order")

    expected: dict[str, dict] = {}
    mapping: dict[str, str] = {}
    used_roots: set[tuple[int, int]] = set()
    for core_id, project in catalog.items():
        keys = aliases[core_id]
        if len(keys) != 1 or not project.roots:
            raise Unsupported("ambiguous project identity")
        key, = keys
        for root in map(Path, project.roots):
            info = ordinary_path(root, directory=True)
            identity = (info.st_dev, info.st_ino)
            if identity in used_roots:
                raise Unsupported("shared project root")
            used_roots.add(identity)
        expected[key] = {
            "id": key, "name": project.name, "rootPaths": list(project.roots),
            "createdAt": project.created, "updatedAt": project.updated,
        }
        mapping[key] = core_id

    if any(mapping.get(key) != value or key not in mapping for key, value in current_map.items()):
        raise Unsupported("conflicting live project mapping")
    for key, entry in registry.items():
        if (key not in expected or not isinstance(entry, dict)
                or any(entry.get(field) != expected[key][field] for field in expected[key])
                or any(type(entry[field]) is not int for field in ("createdAt", "updatedAt"))):
            raise Unsupported("conflicting live project entry")

    missing = {key: entry for key, entry in expected.items() if key not in registry}
    if not missing:
        raise Unsupported("no missing project entries")
    candidate = dict(current)
    candidate[REGISTRY] = registry | missing
    candidate[ORDER] = order + [key for key in missing if key not in order]
    if historical is None:
        return candidate, len(missing), 0, 0

    links, skipped = _object_links(db, current, historical, expected, mapping)
    candidate[ASSIGNMENTS] = links
    candidate[HOST_MAP] = hosts | {"local": mapping}
    return candidate, len(missing), len(links), skipped


def _object_links(
    db: sqlite3.Connection, current: dict, historical: dict,
    expected: dict[str, dict], mapping: dict[str, str],
) -> tuple[dict[str, dict[str, str]], int]:
    old_projects = _object(historical, REGISTRY)
    old_hosts = _object(historical, HOST_MAP)
    old_mapping = _object(old_hosts, "local")
    old_migration = _object(historical, "app-server-projects-migration-by-host")
    if (old_mapping != mapping or old_projects.keys() != expected.keys()
            or any(key.startswith("local:") for key in (*old_hosts, *old_migration))):
        raise HistoricalEvidenceError("desktop_snapshot_conflict")
    for key, project in expected.items():
        old = old_projects[key]
        if not isinstance(old, dict) or any(
            old.get(field) != project[field] for field in ("id", "name", "rootPaths")
        ):
            raise HistoricalEvidenceError("desktop_snapshot_conflict")
    projectless = set(_string_list(current, PROJECTLESS) + _string_list(historical, PROJECTLESS))
    if any(not canonical_id(thread) for thread in projectless):
        raise Unsupported("unsupported projectless markers")

    schema = columns(db, "threads")
    if (not {"id", "project_id", "archived", "thread_source"} <= schema.keys()
            or {name for name, col in schema.items() if col[5]} != {"id"}):
        raise Unsupported("thread identity schema")
    rows = _rows(db, "SELECT id, project_id FROM main.threads WHERE archived = 0 "
                     "AND thread_source COLLATE BINARY = 'user' AND project_id IS NOT NULL")
    confirmed = dict(rows)
    if len(confirmed) != len(rows) or any(
        not canonical_id(thread) or not canonical_id(project) for thread, project in rows
    ):
        raise Unsupported("thread identity rows")

    links = {}
    previous = _object(historical, ASSIGNMENTS)
    for thread, value in previous.items():
        key = local_assignment_key(value)
        if (thread not in projectless and key in mapping
                and confirmed.get(thread) == mapping[key]):
            links[thread] = {"projectKind": "local", "projectId": key}
    if not links:
        raise HistoricalEvidenceError("desktop_snapshot_no_links")
    return links, len(previous) - len(links)
