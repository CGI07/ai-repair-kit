"""Reproduce a pinned Codex Desktop cache projection using synthetic data."""

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import struct
import subprocess
from contextlib import closing
from pathlib import Path
from tempfile import mkdtemp
from uuid import UUID
from xml.etree import ElementTree
from zipfile import ZipFile

from ai_repair_kit.tools.desktop_catalog import columns, legacy_keys, open_snapshot, projects

from scripts.verify_codex_link_lab import (
    check_guest_isolation, fixture_thread, open_server, read_link, require,
)

VERSION = "26.1103.4239.0"
PACKAGE_SHA256 = "9a5fdf536327ce471f9594b0ac6e369244ec3ebfd5e09ec5a66524575dbaa60c"
CLI_SHA256 = "4b3286e35d7fe866c6b0eb3f07d7a4cdb5e96489576170dfd6b3a15aa1c67382"
CLI_MEMBER = "app/resources/codex.exe"
REGISTRY = "local-projects"
ORDER = "project-order"
LINKS = "thread-project-assignments"
PROJECTLESS = "projectless-thread-ids"
MAPPING = "app-server-project-id-by-legacy-project-id-by-host"
MIGRATION = "app-server-projects-migration-by-host"
PACKAGE_CHUNKS = {
    "early-bootstrap-133-1c544c3280919085.js": (
        "2fde4e50ae9ea97e9fb0b1a6b741b64a00257b9cf73fd143fc6a0c9921ec2992",
        ("Failed to load global state", "Loaded global state from backup", "hasAccountScopedRendererState"),
    ),
    "early-bootstrap-199-e3316cddb4ea6a6b.js": (
        "3202cb775ee05404d2b08f6dbb6fa3e524321da435564d8b2a7c6359ccdc6b0a",
        ("async initializeProjects", "this.cache.hostId", "activeServerProjectId(e)",
         "thread/metadata/update", "pendingThreadAssignmentIds"),
    ),
    "early-bootstrap-339-ce026819bf837ba7.js": (
        "3147715e4f64625f331cacd455d3474dcaa368da7579b8ed69f3d8b746509ad8",
        ("39229(e,t,n)", "QN:s", "PROJECT_ORDER"),
    ),
    "early-bootstrap.js": (
        "130d12cbaa4335cd6f21c8d281b9706c6c25f5c8be455cef1c85f57cdd5613b4",
        (REGISTRY, LINKS, MAPPING, MIGRATION, "createdAt:o.number()", "updatedAt:o.number()"),
    ),
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_package(package: Path) -> dict:
    require(package.name == "ChatGPT-x64.msix" and package.stat().st_size == 1_061_531_069,
            "Expected the exact official lab package name and size.")
    require(digest(package) == PACKAGE_SHA256, "The package differs from the pinned signed MSIX.")
    with ZipFile(package) as archive:
        require("AppxSignature.p7x" in archive.namelist(), "Missing package signature.")
        manifest = ElementTree.fromstring(archive.read("AppxManifest.xml"))
        identity = manifest.find("{*}Identity").attrib
        require((identity["Name"], identity["Version"], identity["ProcessorArchitecture"])
                == ("OpenAI.Codex", VERSION, "x64"), "Unexpected package identity.")
        services = sum(node.tag.endswith("}Service") for node in manifest.iter())
        with archive.open("app/resources/app.asar") as asar:
            first, header_size, _, json_size = struct.unpack("<IIII", asar.read(16))
            require(first == 4 and 0 < json_size <= 16 * 1024 * 1024
                    and json_size + 8 <= header_size, "Unexpected ASAR header.")
            index = json.loads(asar.read(json_size))
            for name in (".vite", "build"):
                index = index["files"][name]
            chunks = index["files"]
            for name in sorted(PACKAGE_CHUNKS, key=lambda key: int(chunks[key]["offset"])):
                entry = chunks[name]
                expected, markers = PACKAGE_CHUNKS[name]
                require(0 < entry["size"] <= 24 * 1024 * 1024, "Unexpected ASAR member size.")
                asar.seek(8 + header_size + int(entry["offset"]))
                data = asar.read(entry["size"])
                require(hashlib.sha256(data).hexdigest() == expected
                        and all(marker.encode() in data for marker in markers),
                        "An ASAR chunk differs from the reviewed source.")
    return {"msix": VERSION, "msix_sha256": PACKAGE_SHA256, "verified_chunks": len(PACKAGE_CHUNKS),
            "packaged_services": services}


def _nonstandard_constant(value: str) -> None:
    raise ValueError("Nonstandard JSON constant")


def _decode(data: bytes | None) -> dict | None:
    if data is None:
        return None
    try:
        parsed = json.loads(data.decode("utf-8"), parse_constant=_nonstandard_constant)
    except (ValueError, RecursionError):
        return None
    return parsed if isinstance(parsed, dict) else None


def load_cache(primary: bytes | None, backup: bytes | None) -> tuple[str, dict]:
    """Model the object-shaped local case. A missing primary does not consult the backup."""
    if primary is None:
        return "missing", {}
    parsed = _decode(primary)
    if parsed is not None:
        return "primary", parsed
    fallback = _decode(backup)
    return ("backup", fallback) if fallback is not None else ("invalid", {})


def _number(value: object) -> bool:
    return type(value) in (int, float) and abs(value) <= 1.7976931348623157e308


def _projects(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        key: row for key, row in value.items()
        if isinstance(row, dict) and isinstance(row.get("id"), str)
        and isinstance(row.get("name"), str)
        and isinstance(row.get("rootPaths"), list)
        and all(isinstance(root, str) for root in row["rootPaths"])
        and _number(row.get("createdAt")) and _number(row.get("updatedAt"))
    }


def model(cache: dict, scope: str, catalog: set[str],
          threads: dict[str, str | None]) -> dict[str, int | bool]:
    """Model local cache inputs and possible migration RPCs. No RPC is sent here."""
    raw = cache.get(REGISTRY)
    raw = raw if isinstance(raw, dict) else {}
    visible = _projects(raw)
    order = cache.get(ORDER, [])
    ordered = [visible[key] for key in order if key in visible]
    ordered.extend(row for row in visible.values() if row["id"] not in order)
    roots = {root for row in ordered for root in row["rootPaths"]}
    persisted = cache.get(MAPPING, {}).get(scope, {})
    reverse = {core: legacy for legacy, core in persisted.items()}
    server = {reverse.get(core, core): core for core in catalog}
    migration = cache.get(MIGRATION, {}).get(scope, {})
    assignments = cache.get(LINKS, {})
    projectless = set(cache.get(PROJECTLESS, []))
    pending = set(migration.get("pendingThreadAssignmentIds", []))
    would_set = would_clear = unresolved = 0
    for thread, canonical in threads.items():
        if migration.get("threadAssignmentsMigrated") is True or (
            migration.get("threadAssignmentsReadMigrated") is True and thread not in pending
        ):
            continue
        saved = assignments.get(thread)
        if isinstance(saved, dict) and saved.get("projectKind") == "local":
            key = saved.get("projectId")
            target = server.get(key) if raw.get(key) is not None else None
            if target is None:
                unresolved += 1
            elif target != canonical:
                would_set += 1
        elif saved is None and thread in projectless and canonical is not None:
            would_clear += 1
    return {"durable_projects": len(catalog), "projected_local_projects": len(visible),
            "projected_roots": len(roots), "filtered_project_rows": len(raw) - len(visible),
            "canonical_thread_links": sum(value is not None for value in threads.values()),
            "migration_incomplete": migration.get("projectsMigrated") is True
            and migration.get("threadAssignmentsMigrated") is False,
            "modeled_rpc_set": would_set, "modeled_rpc_clear": would_clear,
            "unresolved_local_links": unresolved}


def scenarios(catalog: dict, aliases: dict, threads: dict[str, str | None], scope: str) -> dict:
    def document(registry, assignments, projectless=()):
        return {REGISTRY: registry, ORDER: list(catalog), LINKS: assignments,
                PROJECTLESS: list(projectless), MAPPING: {scope: aliases}, MIGRATION: {scope: {
                    "projectsMigrated": True, "threadAssignmentsMigrated": False,
                }}}

    reverse = {core: legacy for legacy, core in aliases.items()}
    links = {thread: {"projectKind": "local", "projectId": reverse[core]}
             for thread, core in threads.items() if core is not None}
    legacy, other = aliases
    linked = next(iter(links))
    mismatch = dict(links)
    mismatch[linked] = {"projectKind": "local", "projectId": other if links[linked]["projectId"] == legacy else legacy}
    rows_without_timestamp = {key: {k: v for k, v in row.items() if k != "updatedAt"}
                              for key, row in catalog.items()}
    baseline = document(catalog, links)
    backup = json.dumps(baseline).encode()
    input_cases = {
        "intact": (backup, None),
        "empty_registry_with_retained_links": (json.dumps(document({}, links)).encode(), backup),
        "invalid_project_records": (json.dumps(document(rows_without_timestamp, links)).encode(), None),
        "malformed_primary_uses_backup": (b"{", backup),
        "missing_primary_does_not_read_backup": (None, backup),
        "conflicting_cache_link": (json.dumps(document(catalog, mismatch)).encode(), None),
        "explicit_projectless_conflict": (
            json.dumps(document(catalog, {k: v for k, v in links.items() if k != linked}, [linked])).encode(), None,
        ),
    }
    results = {}
    for label, (primary, old) in input_cases.items():
        loaded, cache = load_cache(primary, old)
        results[label] = {"cache_source": loaded, **model(cache, scope, set(aliases.values()), threads)}
    return results


def synthetic() -> dict:
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript("""
            CREATE TABLE projects(id TEXT PRIMARY KEY);
            CREATE TABLE threads(id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id));
            INSERT INTO projects VALUES ('core-a'), ('core-b');
            INSERT INTO threads VALUES ('thread-a', 'core-a'), ('thread-b', 'core-b');
        """)
        threads = dict(db.execute("SELECT id, project_id FROM threads"))
        aliases = dict(zip(("lab-a", "lab-b"), (row[0] for row in db.execute("SELECT id FROM projects"))))
        registry = {key: {"id": key, "name": key, "rootPaths": [key], "createdAt": 1, "updatedAt": 2}
                    for key in aliases}
        result = scenarios(registry, aliases, threads, "local:synthetic-profile")
        require(dict(db.execute("SELECT id, project_id FROM threads")) == threads, "Fixture links changed.")
        return result


def _metadata_hashes(profile: Path) -> dict[str, str]:
    return {file.name: digest(file) for file in profile.iterdir()
            if re.fullmatch(r"(?:state|goals)_\d+\.sqlite(?:-wal|-shm)?", file.name)}


def native_guest(package: Path, guest_uuid: UUID) -> dict:
    check_guest_isolation(guest_uuid)
    evidence = inspect_package(package)
    guest_work = Path.home() / ".test-work"
    guest_work.mkdir(exist_ok=True)
    lab = Path(mkdtemp(prefix="ark-projection-lab-", dir=guest_work))
    profile = lab / "synthetic"
    profile.mkdir()
    executable = lab / "codex.exe"
    with ZipFile(package) as archive, archive.open(CLI_MEMBER) as vendor, executable.open("xb") as dest:
        shutil.copyfileobj(vendor, dest)
    require(digest(executable) == CLI_SHA256, "The embedded App Server hash differs.")
    with open_server(executable, profile, expected_version=None) as server:
        a, core_a = fixture_thread(server, profile, "lab-a")
        b, core_b = fixture_thread(server, profile, "lab-b")
        goal = server.call("thread/goal/set", threadId=a, objective="Synthetic lab Goal.", status="paused")["goal"]
        require(len(server.call("project/list")["data"]) == 2, "Unexpected native project count.")
        user_agent = server.user_agent
    version = re.search(r"/([0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?)\s", user_agent)
    require(version is not None, "Unrecognized App Server version.")
    with open_server(executable, profile, expected_version=version[1]) as server:
        require(read_link(server, a) == core_a and read_link(server, b) == core_b,
                "Native links did not persist after restart.")
        current_goal = server.call("thread/goal/get", threadId=a)["goal"]
        require(current_goal == goal, "Native Goal did not persist after restart.")
        require(len(server.call("project/list")["data"]) == 2, "Native project list changed on restart.")
    before = _metadata_hashes(profile)
    paths = list(profile.glob("state_*.sqlite"))
    require(len(paths) == 1, "Expected one state database in the synthetic fixture.")
    with open_snapshot(paths[0], allow_wal=True) as snapshot:
        db = snapshot.connection
        catalog = projects(db)
        aliases = {key: core for core, keys in legacy_keys(db).items() for key in keys}
        require(aliases == {"lab-a": core_a, "lab-b": core_b}, "Unexpected durable idempotency mapping.")
        threads = dict(db.execute("SELECT id, project_id FROM main.threads"))
        require(threads == {a: core_a, b: core_b}, "The durable links differ from native results.")
        registry = {key: {"id": key, "name": catalog[core].name, "rootPaths": list(catalog[core].roots),
                          "createdAt": catalog[core].created, "updatedAt": catalog[core].updated}
                    for key, core in aliases.items()}
        schema = {table: sorted(columns(db, table)) for table in
                  ("projects", "project_roots", "project_idempotency_keys", "threads")}
    goal_paths = list(profile.glob("goals_*.sqlite"))
    require(len(goal_paths) == 1, "Expected a separate native Goal database.")
    with open_snapshot(goal_paths[0], allow_wal=True) as snapshot:
        require(snapshot.connection.execute("SELECT status FROM main.thread_goals WHERE thread_id=?", (a,))
                .fetchone() == ("paused",), "Unexpected separate Goal record.")
    result = scenarios(registry, aliases, threads, "local:" + str(profile))
    require(_metadata_hashes(profile) == before, "The read-only checks changed native metadata bytes.")
    return {**evidence, "app_server": version[1], "native_restart_passed": True,
            "metadata_unchanged": True, "state_schema": schema,
            "separate_goal": True, "projection": result, "desktop_gui_tested": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True, help="the pinned official ChatGPT-x64.msix")
    parser.add_argument("--guest-uuid", type=UUID, help="run the native App Server only in this offline lab")
    args = parser.parse_args()
    try:
        if args.guest_uuid:
            result = native_guest(args.package, args.guest_uuid)
        else:
            allowed = Path(".test-work").resolve()
            require(args.package.resolve().is_relative_to(allowed), "Keep the official package under .test-work.")
            result = {**inspect_package(args.package), "projection": synthetic(), "desktop_gui_tested": False}
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    except (OSError, ValueError, KeyError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        # Never print raw paths, process output, database rows, or content.
        detail = str(error) if type(error) is RuntimeError else type(error).__name__
        parser.exit(1, f"FAIL {detail}\n")


if __name__ == "__main__":
    main()
