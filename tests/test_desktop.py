"""Synthetic tests for the version-gated Desktop recovery paths."""

import json
import os
import shutil
import sqlite3
import subprocess
import time
import unittest
from concurrent.futures import wait
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import UUID

from ai_repair_kit.tools import desktop_repair as repair
from ai_repair_kit.tools.codex_diagnostics import Code, inspect
from ai_repair_kit.tools.codex_repair import RepairError
from ai_repair_kit.tools.desktop_thread import (
    GoalComparison, GoalDetails, GoalStatus, Presence, ProjectComparison, ProjectLink, ProjectLinks,
    RolloutReference, ThreadReport, inspect_thread,
)

WORK = Path(__file__).resolve().parents[1] / ".test-work"
CORE = str(UUID(int=101))
THREAD = str(UUID(int=201))
ORPHAN = str(UUID(int=202))
INDEX_ONLY = str(UUID(int=203))
FILE_ONLY = str(UUID(int=204))


class DesktopFixture(unittest.TestCase):
    def setUp(self):
        package_probe = patch(
            "ai_repair_kit.tools.codex_diagnostics.installed_desktop_version", return_value=None,
        )
        package_probe.start()
        self.addCleanup(package_probe.stop)
        repair_probe = patch.object(repair, "installed_desktop_version", return_value=repair.SUPPORTED_BUILD)
        self.installed_desktop_version = repair_probe.start()
        self.addCleanup(repair_probe.stop)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("CODEX_HOME", None)
        WORK.mkdir(exist_ok=True)
        work = TemporaryDirectory(dir=WORK)
        self.addCleanup(work.cleanup)
        self.base = Path(work.name)
        self.home = self.base / "fake-home"
        self.home.mkdir()
        self.root = self.base / "project"
        self.root.mkdir()
        self.store = self.base / "backups"
        self.cache = self.home / repair.CACHE_NAME
        self.state = self.home / repair.STATE_NAME
        self.original = b'{"local-projects":{},"other":{"keep":true}}'
        self.cache.write_bytes(self.original)
        (self.home / "config.toml").write_bytes(b"")
        with closing(sqlite3.connect(self.state)) as db:
            db.executescript("""
                CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT, position INTEGER,
                                      created_at_ms INTEGER, updated_at_ms INTEGER);
                CREATE TABLE project_roots(project_id TEXT, position INTEGER, path TEXT,
                                           PRIMARY KEY(project_id, position));
                CREATE TABLE project_idempotency_keys(key TEXT PRIMARY KEY, project_id TEXT);
                CREATE TABLE threads(id TEXT PRIMARY KEY, project_id TEXT, cwd TEXT, rollout_path TEXT);
            """)
            db.execute("INSERT INTO projects VALUES (?, 'Fixture', 0, 10, 20)", (CORE,))
            db.execute("INSERT INTO project_roots VALUES (?, 0, ?)", (CORE, str(self.root)))
            db.execute("INSERT INTO project_idempotency_keys VALUES ('legacy', ?)", (CORE,))
            db.execute("INSERT INTO threads VALUES (?, NULL, ?, ?)",
                       (THREAD, str(self.root), str(self.home / "sessions" / "rollout-missing.jsonl")))
            db.commit()

    def plan(self):
        return repair.prepare(self.home, self.home, repair.SUPPORTED_BUILD)

    def timestamp_cache(self):
        self.installed_desktop_version.return_value = repair.TIMESTAMP_BUILD
        data = json.loads(self.original)
        data["local-projects"] = {
            "legacy": {"id": "legacy", "name": "Fixture", "rootPaths": [str(self.root)]},
        }
        data["thread-project-assignments"] = {THREAD: "legacy"}
        data["project-order"] = ["legacy"]
        data["unrelated"] = {"preserve": [1, 2, 3]}
        self.cache.write_text(json.dumps(data), encoding="utf-8")
        return self.cache.read_bytes()

    def historical(self, extra=None):
        if self.installed_desktop_version.return_value == repair.SUPPORTED_BUILD:
            self.installed_desktop_version.return_value = repair.HISTORY_BUILD
        snapshot = self.base / repair.CACHE_NAME
        data = {
            "local-projects": {"legacy": {"id": "legacy", "name": "Fixture", "rootPaths": [str(self.root)]}},
            "app-server-project-id-by-legacy-project-id-by-host": {"local": {"legacy": CORE}},
            "project-order": ["legacy"],
            "thread-project-assignments": {THREAD: "legacy", ORPHAN: "legacy"},
        }
        data.update(extra or {})
        snapshot.write_text(json.dumps(data), encoding="utf-8")
        return snapshot


class DesktopDiagnostics(DesktopFixture):
    def test_release_refuses_preview_and_apply_for_diagnostic_only_builds(self):
        original = self.plan()
        for build, history in (
            (repair.PARTIAL_REGISTRY_BUILD, None),
            (repair.OBJECT_LINK_BUILD, self.base / repair.CACHE_NAME),
            ("26.1103.4239.0", None),
        ):
            with self.subTest(build=build):
                self.installed_desktop_version.return_value = build
                with self.assertRaisesRegex(RepairError, "^desktop_version$"):
                    repair.prepare(self.home, self.home, build, history)
                with self.assertRaisesRegex(RepairError, "^desktop_version$"):
                    repair.apply(replace(original, build=build, history=history),
                                 self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertFalse(self.store.exists())

    def test_reports_local_migration_and_shared_roots_without_relinking(self):
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("INSERT INTO projects VALUES (?, 'Other fixture', 1, 20, 30)", (FILE_ONLY,))
            db.execute("INSERT INTO project_roots VALUES (?, 0, ?)", (FILE_ONLY, str(self.root)))
            db.commit()
        data = json.loads(self.cache.read_bytes())
        local = {"projectsMigrated": True, "threadAssignmentsMigrated": False,
                 "pendingThreadAssignmentIds": [THREAD, ORPHAN, ORPHAN]}
        remote = {**local, "pendingThreadAssignmentIds": [INDEX_ONLY]}
        data["app-server-projects-migration-by-host"] = {"local": local, "remote": remote}

        def scan():
            self.cache.write_text(json.dumps(data), encoding="utf-8")
            original = {path: path.read_bytes() for path in (self.cache, self.state)}
            result = {item.code: item.count for item in inspect(self.home, self.home).findings}
            self.assertEqual(original, {path: path.read_bytes() for path in original})
            self.assertFalse(self.store.exists())
            return result

        result = scan()
        self.assertEqual(result[Code.DESKTOP_ROOT_SHARED], 1)
        self.assertEqual(result[Code.DESKTOP_ASSIGNMENT_MIGRATION_PENDING], 1)
        self.assertEqual(result[Code.DESKTOP_ASSIGNMENT_MIGRATION_ORPHANS], 1)
        self.assertNotIn(Code.DESKTOP_CANDIDATES, result)

        local["pendingThreadAssignmentIds"] = [ORPHAN, {"unexpected": THREAD}]
        result = scan()
        self.assertIn(Code.DESKTOP_ASSIGNMENT_MIGRATION_PENDING, result)
        self.assertNotIn(Code.DESKTOP_ASSIGNMENT_MIGRATION_ORPHANS, result)

        local["threadAssignmentsMigrated"] = 0
        result = scan()
        self.assertNotIn(Code.DESKTOP_ASSIGNMENT_MIGRATION_PENDING, result)
        self.assertNotIn(Code.DESKTOP_ASSIGNMENT_MIGRATION_ORPHANS, result)

    def test_timestamp_and_pending_projectless_findings_are_read_only(self):
        self.timestamp_cache()
        cache = json.loads(self.cache.read_bytes())
        cache["app-server-projects-migration-by-host"] = {"local": {
            "projectsMigrated": True, "threadAssignmentsMigrated": False,
            "pendingThreadAssignmentIds": [THREAD, THREAD, ORPHAN],
        }}
        cache["projectless-thread-ids"] = [THREAD, THREAD, ORPHAN]
        self.cache.write_text(json.dumps(cache), encoding="utf-8")
        before = {path: path.read_bytes() for path in (self.cache, self.state)}
        report = inspect(self.home, self.home)
        findings = {item.code: item.count for item in report.findings}
        self.assertEqual(findings[Code.DESKTOP_PROJECT_TIMESTAMPS_MISSING], 1)
        self.assertEqual(findings[Code.DESKTOP_ASSIGNMENT_PROJECTLESS], 2)
        self.assertNotIn(THREAD, repr(report.findings))
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertFalse(self.store.exists())
        for invalid in ([THREAD, {}], [THREAD, "not-a-thread"], "unexpected"):
            with self.subTest(projectless=invalid):
                cache["projectless-thread-ids"] = invalid
                self.cache.write_text(json.dumps(cache), encoding="utf-8")
                findings = {item.code for item in inspect(self.home, self.home).findings}
                self.assertNotIn(Code.DESKTOP_ASSIGNMENT_PROJECTLESS, findings)

    def test_exact_thread_presence_distinguishes_stores_and_preserves_bytes(self):
        goal_db = self.home / "goals_1.sqlite"
        with closing(sqlite3.connect(goal_db)) as db:
            db.execute("CREATE TABLE thread_goals(thread_id TEXT PRIMARY KEY, goal_id TEXT, "
                       "objective TEXT, status TEXT)")
            db.execute("INSERT INTO thread_goals VALUES (?, 'goal', 'private synthetic text', 'active')", (THREAD,))
            db.commit()
        before = {path: path.read_bytes() for path in (self.state, goal_db, self.cache)}
        self.assertEqual(inspect_thread(self.home, self.home, THREAD),
                         ThreadReport(Presence.FOUND, Presence.TABLE_MISSING, Presence.FOUND,
                                      rollout_reference=RolloutReference.UNAVAILABLE,
                                      project_links=ProjectLinks(ProjectLink.UNASSIGNED, ProjectLink.UNASSIGNED),
                                      goal_details=GoalDetails(separate=GoalStatus.ACTIVE)))
        self.assertEqual(inspect_thread(self.home, self.home, ORPHAN),
                         ThreadReport(Presence.ABSENT, Presence.TABLE_MISSING, Presence.ABSENT,
                                      project_links=ProjectLinks(ProjectLink.NOT_CHECKED, ProjectLink.UNASSIGNED)))
        self.state.with_name(self.state.name + "-wal").touch()
        self.assertEqual(inspect_thread(self.home, self.home, THREAD),
                         ThreadReport(Presence.SIDECARS, Presence.SIDECARS, Presence.FOUND,
                                      project_links=ProjectLinks(ProjectLink.NOT_CHECKED, ProjectLink.UNASSIGNED),
                                      goal_details=GoalDetails(separate=GoalStatus.ACTIVE)))
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        with self.assertRaises(ValueError):
            inspect_thread(self.home, self.home, "unsafe")
        self.assertFalse(self.store.exists())

    def test_exact_thread_and_aggregate_scan_match_compressed_and_reverted_rollouts(self):
        active = self.home / "sessions" / "2026" / "11" / "06"
        active.mkdir(parents=True)
        archived = self.home / "archived_sessions"
        archived.mkdir()
        standard = "rollout-2026-11-06T12-00-00-" + THREAD + ".jsonl"
        revision = "rollout-2026-11-06T13-00-00-" + THREAD + "_" + ORPHAN + ".jsonl"
        for path in (active / (standard + ".zst"), active / revision, active / (revision + ".zst"),
                     archived / ("rollout-2026-11-06T14-00-00-" + THREAD + "_" + FILE_ONLY + ".jsonl.zst"),
                     archived / ("rollout-invalid-" + INDEX_ONLY + ".jsonl")):
            path.write_bytes(b"synthetic content is never opened")
        separate_db = self.base / "sqlite"
        separate_db.mkdir()
        state = separate_db / "state_5.sqlite"
        state.write_bytes(self.state.read_bytes())
        with closing(sqlite3.connect(state)) as db:
            db.execute("UPDATE threads SET rollout_path = ? WHERE id = ?", (str(active / standard), THREAD))
            db.commit()
        self.assertEqual(inspect_thread(self.home, separate_db, THREAD),
                         ThreadReport(Presence.FOUND, Presence.TABLE_MISSING, Presence.STORE_MISSING, 2, 1,
                                      RolloutReference.FOUND,
                                      ProjectLinks(ProjectLink.UNASSIGNED, ProjectLink.UNASSIGNED)))
        self.assertEqual(inspect_thread(self.home, separate_db, ORPHAN).active_files, 0)
        results = {item.code for item in inspect(self.home, separate_db).findings}
        for invalid in (Code.ROLLOUT_MISSING, Code.DESKTOP_SESSION_UNLISTED, Code.DESKTOP_SESSION_DUPLICATE):
            self.assertNotIn(invalid, results)

    def test_exact_thread_compares_only_confirmed_project_metadata(self):
        before = inspect_thread(self.home, self.home, THREAD)
        self.assertEqual(before.project_links, ProjectLinks(ProjectLink.UNASSIGNED, ProjectLink.UNASSIGNED))
        data = json.loads(self.historical().read_bytes())
        data["thread-project-assignments"] = {THREAD: "legacy"}
        self.cache.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(inspect_thread(self.home, self.home, THREAD).project_links,
                         ProjectLinks(ProjectLink.UNASSIGNED, ProjectLink.LINKED))
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("UPDATE threads SET project_id=? WHERE id=?", (CORE, THREAD))
            db.commit()
        self.assertEqual(inspect_thread(self.home, self.home, THREAD).project_links,
                         ProjectLinks(ProjectLink.LINKED, ProjectLink.LINKED, ProjectComparison.AGREE))
        data["app-server-project-id-by-legacy-project-id-by-host"]["local"]["legacy"] = ORPHAN
        self.cache.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(inspect_thread(self.home, self.home, THREAD).project_links,
                         ProjectLinks(ProjectLink.LINKED, ProjectLink.LINKED, ProjectComparison.CONFLICT))
        data["thread-project-assignments"] = {}
        data["projectless-thread-ids"] = [THREAD]
        self.cache.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(inspect_thread(self.home, self.home, THREAD).project_links,
                         ProjectLinks(ProjectLink.LINKED, ProjectLink.PROJECTLESS, ProjectComparison.CONFLICT))
        data["thread-project-assignments"] = {THREAD: "legacy"}
        self.cache.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(inspect_thread(self.home, self.home, THREAD).project_links.cache,
                         ProjectLink.CONFLICT)

    def test_thread_rollout_pointer_separates_missing_revision_wrong_thread_and_unsafe_path(self):
        archived = self.home / "archived_sessions"
        archived.mkdir()
        remaining = archived / ("rollout-2026-11-06T11-00-00-" + THREAD + ".jsonl")
        remaining.write_bytes(b"synthetic old rollout")
        current = archived / ("rollout-2026-11-06T12-00-00-" + THREAD + "_" + FILE_ONLY + ".jsonl")
        wrong = archived / ("rollout-2026-11-06T12-00-00-" + ORPHAN + ".jsonl")
        wrong.write_bytes(b"synthetic unrelated rollout")
        outside = self.base / current.name
        outside.write_bytes(b"outside selected profile")
        for path, expected in ((current, RolloutReference.MISSING), (wrong, RolloutReference.WRONG_THREAD),
                               (outside, RolloutReference.UNAVAILABLE), ("", RolloutReference.NOT_RECORDED)):
            with self.subTest(path=path):
                with closing(sqlite3.connect(self.state)) as db:
                    db.execute("UPDATE threads SET rollout_path = ? WHERE id = ?", (str(path), THREAD))
                    db.commit()
                before = self.state.read_bytes()
                report = inspect_thread(self.home, self.home, THREAD)
                self.assertEqual(report.rollout_reference, expected)
                self.assertEqual(report.archived_files, 1)
                self.assertEqual(self.state.read_bytes(), before)

    def test_paginated_history_reports_index_file_metadata_without_opening_it(self):
        from ai_repair_kit.tools import desktop_catalog

        index = self.home / "thread_history_1.sqlite"
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("ALTER TABLE threads ADD COLUMN history_mode TEXT NOT NULL DEFAULT 'legacy'")
            db.execute("UPDATE threads SET history_mode = 'paginated' WHERE id = ?", (THREAD,))
            db.commit()

        self.assertIs(inspect_thread(self.home, self.home, THREAD).history_index, Presence.STORE_MISSING)
        index.mkdir()
        self.assertIs(inspect_thread(self.home, self.home, THREAD).history_index, Presence.UNAVAILABLE)
        index.rmdir()
        index.write_bytes(b"opaque synthetic file")
        before = (self.state.read_bytes(), index.read_bytes())
        with patch.object(desktop_catalog, "stable_file", wraps=desktop_catalog.stable_file) as open_file:
            report = inspect_thread(self.home, self.home, THREAD)
            self.assertIs(report.history_index, Presence.FOUND)
            self.assertNotIn(index, [call.args[0] for call in open_file.call_args_list])
        self.assertEqual((self.state.read_bytes(), index.read_bytes()), before)
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("UPDATE threads SET history_mode = 'unknown' WHERE id = ?", (THREAD,))
            db.commit()
        self.assertIsNone(inspect_thread(self.home, self.home, THREAD).history_index)

    def test_archive_flag_and_referenced_rollout_are_independent_and_read_only(self):
        folder = self.home / "archived_sessions"
        folder.mkdir()
        archived = folder / ("rollout-2026-11-06T12-00-00-" + THREAD + ".jsonl")
        archived.write_bytes(b"synthetic rollout")
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("ALTER TABLE threads ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        for value, expected in ((1, True), (0, False), (2, None), ("invalid", None)):
            with self.subTest(value=value):
                with closing(sqlite3.connect(self.state)) as db:
                    db.execute("UPDATE threads SET archived = ?, rollout_path = ? WHERE id = ?",
                               (value, str(archived), THREAD))
                    db.commit()
                before = (self.state.read_bytes(), archived.read_bytes())
                report = inspect_thread(self.home, self.home, THREAD)
                self.assertIs(report.archived, expected)
                self.assertEqual(report.rollout_reference, RolloutReference.FOUND_ARCHIVED)
                self.assertEqual((report.active_files, report.archived_files), (0, 1))
                self.assertEqual((self.state.read_bytes(), archived.read_bytes()), before)

    def test_goal_lookup_distinguishes_status_and_identity_without_exposing_values(self):
        separate = self.home / "goals_1.sqlite"
        for database in (self.state, separate):
            with closing(sqlite3.connect(database)) as db:
                db.execute("CREATE TABLE thread_goals(thread_id TEXT PRIMARY KEY, goal_id TEXT, "
                           "objective TEXT, status TEXT)")
                db.execute("INSERT INTO thread_goals VALUES (?, 'synthetic-secret-id', 'private text', 'active')",
                           (THREAD,))
                db.commit()
        cases = [
            ("synthetic-secret-id", "active", GoalStatus.ACTIVE, GoalComparison.SAME_GOAL_AND_STATUS),
            ("synthetic-secret-id", "paused", GoalStatus.PAUSED, GoalComparison.DIFFERENT_STATUS),
            ("different-secret-id", "complete", GoalStatus.COMPLETE, GoalComparison.DIFFERENT_GOAL),
            ("synthetic-secret-id", "future-unsupported-state", GoalStatus.UNKNOWN,
             GoalComparison.SAME_GOAL_UNKNOWN_STATUS),
            ("x" * 129, "blocked", GoalStatus.BLOCKED, GoalComparison.UNAVAILABLE),
        ]
        for identifier, status, expected_status, comparison in cases:
            with self.subTest(comparison=comparison):
                with closing(sqlite3.connect(separate)) as db:
                    db.execute("UPDATE thread_goals SET goal_id=?, status=? WHERE thread_id=?",
                               (identifier, status, THREAD))
                    db.commit()
                before = (self.state.read_bytes(), separate.read_bytes())
                report = inspect_thread(self.home, self.home, THREAD)
                self.assertEqual(report.goal_details, GoalDetails(GoalStatus.ACTIVE, expected_status, comparison))
                self.assertNotIn("secret-id", repr(report))
                self.assertNotIn("private text", repr(report))
                self.assertEqual((self.state.read_bytes(), separate.read_bytes()), before)

    @unittest.skipUnless(os.name == "nt", "requires Windows file-sharing guards")
    def test_exact_goal_comparison_guards_both_stores_at_once(self):
        from ai_repair_kit.tools import desktop_thread

        separate = self.home / "goals_1.sqlite"
        for database in (self.state, separate):
            with closing(sqlite3.connect(database)) as db:
                db.execute("CREATE TABLE thread_goals(thread_id TEXT PRIMARY KEY, goal_id TEXT, "
                           "objective TEXT, status TEXT)")
                db.execute("INSERT INTO thread_goals VALUES (?, 'synthetic-goal', 'synthetic objective', 'active')",
                           (THREAD,))
                db.commit()
        before = (self.state.read_bytes(), separate.read_bytes())
        real_open = desktop_thread.open_snapshot
        writes_blocked = []

        def open_with_competing_writer(path, **options):
            if path == separate:
                try:
                    with self.state.open("r+b"):
                        writes_blocked.append(False)
                except OSError:
                    writes_blocked.append(True)
            return real_open(path, **options)

        with patch.object(desktop_thread, "open_snapshot", side_effect=open_with_competing_writer):
            report = inspect_thread(self.home, self.home, THREAD)
        self.assertEqual(writes_blocked, [True])
        self.assertEqual(report.goal_details.comparison, GoalComparison.SAME_GOAL_AND_STATUS)
        self.assertEqual((self.state.read_bytes(), separate.read_bytes()), before)

    @unittest.skipUnless(os.name == "nt", "requires Windows file-sharing guards")
    def test_project_and_thread_wal_diagnosis_leaves_repair_unavailable(self):
        plan = self.plan()
        source = self.base / "state-source.sqlite"
        shutil.copyfile(self.state, source)
        second = str(UUID(int=102))
        missing = self.home / "sessions" / ("rollout-2026-11-06T12-00-00-" + THREAD + ".jsonl")
        with closing(sqlite3.connect(source)) as db:
            db.execute("PRAGMA journal_mode = WAL")
            self.assertEqual(db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone(), (0, 0, 0))
            db.execute("INSERT INTO projects VALUES (?, 'WAL project', 1, 10, 20)", (second,))
            db.execute("INSERT INTO project_roots VALUES (?, 0, ?)", (second, str(self.root)))
            db.execute("INSERT INTO project_idempotency_keys VALUES ('wal-project', ?)", (second,))
            db.execute("UPDATE threads SET project_id=?, rollout_path=? WHERE id=?",
                       (second, str(missing), THREAD))
            db.commit()
            for suffix in ("", "-wal", "-shm"):
                shutil.copyfile(source.with_name(source.name + suffix),
                                self.state.with_name(self.state.name + suffix))

        def files():
            return {path.name: path.read_bytes() for path in self.home.iterdir()}

        before = files()
        findings = {finding.code: finding.count for finding in inspect(self.home).findings}
        self.assertIn(Code.DB_WAL_DIAGNOSTIC, findings)
        self.assertNotIn(Code.DB_SIDECARS, findings)
        self.assertEqual(findings[Code.DESKTOP_PROJECTS], 2)
        self.assertIn(Code.DESKTOP_CACHE_EMPTY, findings)
        self.assertNotIn(Code.DESKTOP_UNASSIGNED, findings)
        self.assertEqual(findings[Code.ROLLOUT_MISSING], 1)
        thread = inspect_thread(self.home, self.home, THREAD)
        self.assertEqual(thread.project_links.database, ProjectLink.LINKED)
        self.assertEqual(thread.rollout_reference, RolloutReference.MISSING)
        with self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            self.plan()
        with patch.object(repair, "ensure_closed"), self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            repair.apply(plan, self.store, approved=True)
        self.assertFalse(self.store.exists())
        self.assertEqual(files(), before)

        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from ai_repair_kit.gui.window import MainWindow

        app = QApplication.instance() or QApplication([])
        prefs = QSettings(str(self.base / "state-wal-ui.ini"), QSettings.Format.IniFormat)
        prefs.setValue("language", "en")
        window = MainWindow(app, prefs, self.store)
        self.addCleanup(window.close)
        self.addCleanup(app.removeTranslator, window.translations.translator)
        window.show()
        window.nav.setCurrentRow(1)
        flow = window.workflow
        flow.path.setText(str(self.home))
        flow.start_scan()
        flow.future.result(timeout=15)
        flow.finish_scan()
        self.assertIn("State metadata was read with the SQLite WAL.", flow.results.text())
        self.assertFalse(flow.desktop_repair.isVisible())
        self.assertFalse(flow.desktop_history.isVisible())
        self.assertTrue(flow.thread_button.isVisible())
        window.language.setCurrentIndex(2)
        self.assertIn("SQLite WAL을 포함해 상태 메타데이터를 읽었습니다.", flow.results.text())
        self.assertEqual(files(), before)

    @unittest.skipUnless(os.name == "nt", "requires Windows file-sharing guards")
    def test_checkpointed_empty_wal_is_available_only_to_read_only_diagnostics(self):
        from ai_repair_kit.tools.desktop_catalog import SnapshotHasSidecars, open_snapshot

        source = self.base / "state-source.sqlite"
        shutil.copyfile(self.state, source)
        with closing(sqlite3.connect(source)) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("UPDATE threads SET project_id=? WHERE id=?", (CORE, THREAD))
            db.commit()
            self.assertEqual(db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone(), (0, 0, 0))
            for suffix in ("", "-wal", "-shm"):
                shutil.copyfile(source.with_name(source.name + suffix),
                                self.state.with_name(self.state.name + suffix))

        wal = self.state.with_name(self.state.name + "-wal")
        self.assertEqual(wal.stat().st_size, 0)
        before = {path.name: path.read_bytes() for path in self.home.iterdir()}
        findings = {finding.code: finding.count for finding in inspect(self.home).findings}
        self.assertIn(Code.DB_WAL_DIAGNOSTIC, findings)
        self.assertNotIn(Code.DB_SIDECARS, findings)
        self.assertEqual(findings[Code.DESKTOP_PROJECTS], 1)
        thread = inspect_thread(self.home, self.home, THREAD)
        self.assertEqual(thread.project_links.database, ProjectLink.LINKED)
        with self.assertRaises(SnapshotHasSidecars), open_snapshot(self.state, allow_wal=True):
            self.fail("A repair snapshot must reject the empty WAL.")
        with self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            self.plan()
        self.assertEqual({path.name: path.read_bytes() for path in self.home.iterdir()}, before)

        wal.write_bytes(b"\x37")
        self.assertIn(Code.DB_SIDECARS, {finding.code for finding in inspect(self.home).findings})

    @unittest.skipUnless(os.name == "nt", "requires Windows file-sharing guards")
    def test_goal_reads_committed_wal_without_using_or_changing_the_disk_index(self):
        source = self.base / "source.sqlite"
        goal = self.home / "goals_1.sqlite"
        wal = goal.with_name(goal.name + "-wal")
        shm = goal.with_name(goal.name + "-shm")
        with closing(sqlite3.connect(source)) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("CREATE TABLE thread_goals(thread_id TEXT PRIMARY KEY, goal_id TEXT, "
                       "objective TEXT, status TEXT)")
            db.commit()
            self.assertEqual(db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone(), (0, 0, 0))
            db.execute("INSERT INTO thread_goals VALUES (?, 'goal', 'synthetic objective', 'paused')", (THREAD,))
            db.commit()
            shutil.copyfile(source, goal)
            shutil.copyfile(source.with_name(source.name + "-wal"), wal)
        # The on-disk index cannot supply the new record. SQLite must read the WAL.
        shm.write_bytes(bytes(32768))

        with closing(sqlite3.connect(goal.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM thread_goals").fetchone(), (0,))

        def files():
            return {path.name: path.read_bytes() for path in self.home.iterdir()}

        before = files()
        counts = {finding.code: finding.count for finding in inspect(self.home).findings}
        self.assertEqual(counts[Code.GOAL_SPLIT], 1)
        self.assertNotIn(Code.GOAL_SIDECARS, counts)
        lookup = inspect_thread(self.home, self.home, THREAD)
        self.assertEqual(lookup.separate_goal, Presence.FOUND)
        self.assertEqual(lookup.goal_details.separate, GoalStatus.PAUSED)
        self.assertNotIn("synthetic objective", repr(lookup))
        self.assertEqual(files(), before)

        with wal.open("r+b"):
            self.assertEqual(inspect_thread(self.home, self.home, THREAD).separate_goal, Presence.SIDECARS)
        self.assertEqual(files(), before)

        shm.unlink()
        incomplete = files()
        codes = {finding.code for finding in inspect(self.home).findings}
        self.assertIn(Code.GOAL_SIDECARS, codes)
        self.assertNotIn(Code.GOAL_NONE, codes)
        self.assertEqual(inspect_thread(self.home, self.home, THREAD).separate_goal, Presence.SIDECARS)
        self.assertEqual(files(), incomplete)

        shm.write_bytes(bytes(32768))
        wal.write_bytes(b"invalid WAL header".ljust(32, b" "))
        malformed = files()
        self.assertEqual(inspect_thread(self.home, self.home, THREAD).separate_goal, Presence.SIDECARS)
        self.assertEqual(files(), malformed)

    def test_exact_lookup_rejects_composite_identity_and_checks_split_goal_without_state(self):
        goal_db = self.home / "goals_1.sqlite"
        with closing(sqlite3.connect(goal_db)) as db:
            db.execute("CREATE TABLE thread_goals(thread_id TEXT, goal_id TEXT, objective TEXT, "
                       "status TEXT, PRIMARY KEY(thread_id, goal_id))")
            db.executemany("INSERT INTO thread_goals VALUES (?, ?, 'private synthetic text', 'active')",
                           ((THREAD, "first-goal"), (THREAD, "second-goal")))
            db.commit()
        self.state.unlink()
        findings = {finding.code for finding in inspect(self.home).findings}
        self.assertIn(Code.GOAL_UNAVAILABLE, findings)
        self.assertNotIn(Code.GOAL_SPLIT, findings)
        self.assertEqual(inspect_thread(self.home, self.home, THREAD),
                         ThreadReport(Presence.STORE_MISSING, Presence.STORE_MISSING, Presence.UNAVAILABLE,
                                      project_links=ProjectLinks(ProjectLink.NOT_CHECKED, ProjectLink.UNASSIGNED)))

    def test_thread_lookup_discards_result_if_missing_config_appears(self):
        from ai_repair_kit.tools import desktop_thread

        config = self.home / "config.toml"
        config.unlink()
        scan = inspect(self.home)
        real_lookup = desktop_thread.inspect_thread

        def create_config(*args):
            config.write_bytes(b"sqlite_home = 'another-profile'\n")
            return real_lookup(*args)

        with patch.object(desktop_thread, "inspect_thread", side_effect=create_config):
            with self.assertRaises(desktop_thread.ThreadLocationChanged):
                desktop_thread.inspect_scanned_thread(self.home, scan, THREAD)

    def test_thread_gui_rescans_after_sqlite_location_changes(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from ai_repair_kit.gui import codex_workflow
        from ai_repair_kit.gui.window import MainWindow
        from ai_repair_kit.tools import desktop_thread

        app = QApplication.instance() or QApplication([])
        prefs = QSettings(str(self.base / "redirect-ui.ini"), QSettings.Format.IniFormat)
        prefs.setValue("language", "en")
        window = MainWindow(app, prefs, self.store)
        self.addCleanup(window.close)
        self.addCleanup(app.removeTranslator, window.translations.translator)
        window.show()
        window.nav.setCurrentRow(1)
        flow = window.workflow
        flow.path.setText(str(self.home))
        flow.start_scan()
        flow.future.result(timeout=15)
        flow.finish_scan()
        relocated = self.base / "relocated"
        relocated.mkdir()
        config = self.home / "config.toml"
        config.write_text(f"sqlite_home = '{relocated.as_posix()}'", encoding="utf-8")
        with patch.object(codex_workflow.QInputDialog, "getText", return_value=(THREAD, True)), patch.object(
            desktop_thread, "open_snapshot", wraps=desktop_thread.open_snapshot
        ) as open_snapshot:
            flow.thread_button.click()
            self.assertTrue(wait([flow.future], timeout=15).done)
            flow.finish_scan()
            open_snapshot.assert_not_called()
        self.assertIsNone(flow.thread_report)
        self.assertIsNone(flow.report)
        self.assertFalse(flow.thread_button.isVisible())
        self.assertIn("Run diagnostics again", flow.results.text())
        window.language.setCurrentIndex(2)
        self.assertIn("진단을 다시 실행하세요", flow.results.text())
        flow.start_scan()
        flow.future.result(timeout=15)
        flow.finish_scan()
        self.assertEqual(flow.report.db_home, relocated)

    def test_exact_thread_gui_accepts_link_and_retranslates_without_echoing_it(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from ai_repair_kit.gui import codex_workflow
        from ai_repair_kit.gui.window import MainWindow

        app = QApplication.instance() or QApplication([])
        prefs = QSettings(str(self.base / "thread-ui.ini"), QSettings.Format.IniFormat)
        prefs.setValue("language", "en")
        window = MainWindow(app, prefs, self.store)
        self.addCleanup(window.close)
        self.addCleanup(app.removeTranslator, window.translations.translator)
        window.show()
        window.nav.setCurrentRow(1)
        flow = window.workflow
        folder = self.home / "archived_sessions"
        folder.mkdir()
        archived = folder / ("rollout-2026-11-06T12-00-00-" + THREAD + ".jsonl")
        archived.write_bytes(b"synthetic archive")
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("ALTER TABLE threads ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            db.execute("ALTER TABLE threads ADD COLUMN history_mode TEXT NOT NULL DEFAULT 'paginated'")
            db.execute("UPDATE threads SET archived = 1, rollout_path = ? WHERE id = ?", (str(archived), THREAD))
            db.commit()
        (self.home / "thread_history_1.sqlite").write_bytes(b"opaque synthetic index")
        with closing(sqlite3.connect(self.home / "goals_1.sqlite")) as db:
            db.execute("CREATE TABLE fixture(id INTEGER)")
        (self.home / "goals_1.sqlite-wal").touch()
        flow.path.setText(str(self.home))
        flow.start_scan()
        flow.future.result(timeout=15)
        flow.finish_scan()
        self.assertTrue(flow.thread_button.isVisible())
        with patch.object(codex_workflow.QInputDialog, "getText", return_value=(f"codex://threads/{THREAD.upper()}", True)):
            flow.thread_button.click()
        flow.future.result(timeout=15)
        flow.finish_scan()
        self.assertIn("Thread in state database: Found", flow.thread_status.text())
        self.assertIn("Active session rollouts: 0", flow.thread_status.text())
        self.assertIn("Archive flag in state database: Marked archived", flow.thread_status.text())
        self.assertIn("Paginated history index file: Present.", flow.thread_status.text())
        self.assertIn("Settings > Archived chats", flow.thread_status.text())
        self.assertIn("Goal in separate database: Skipped because SQLite journal files are present",
                      flow.thread_status.text())
        self.assertNotIn(THREAD, flow.thread_status.text())
        window.language.setCurrentIndex(2)
        self.assertIn("상태 DB의 스레드: 기록 있음", flow.thread_status.text())
        self.assertIn("SQLite 저널 파일이 남아 있어 확인을 건너뜀", flow.thread_status.text())
        self.assertIn("Desktop 캐시의 프로젝트 연결: 프로젝트 연결 기록 없음", flow.thread_status.text())
        self.assertIn("상태 DB가 참조하는 롤아웃: 보관 폴더에 참조 파일이 있음", flow.thread_status.text())
        self.assertIn("설정 > 보관된 채팅", flow.thread_status.text())
        self.assertIn("페이지 방식의 기록 인덱스 파일이 있습니다.", flow.thread_status.text())
        window.resize(740, 520)
        window.theme.setCurrentIndex(1)
        self.assertTrue(flow.thread_status.wordWrap())
        self.assertIn("페이지 방식의 기록 인덱스 파일이 있습니다.", flow.thread_status.text())
        (folder / ("rollout-2026-11-06T11-00-00-" + THREAD + ".jsonl")).write_bytes(b"older synthetic archive")
        with patch.object(codex_workflow.QInputDialog, "getText", return_value=(THREAD, True)):
            flow.thread_button.click()
        flow.future.result(timeout=15)
        flow.finish_scan()
        self.assertIn("보관됨으로 표시", flow.thread_status.text())
        self.assertNotIn("설정 > 보관된 채팅", flow.thread_status.text())
        flow.path.setText(str(self.base))
        self.assertEqual(flow.thread_status.text(), "")
        self.assertFalse(flow.thread_button.isVisible())

    def test_registry_threads_goal_and_session_inventory_are_read_only(self):
        split = self.home / "goals_1.sqlite"
        for path, key in ((self.state, THREAD), (split, ORPHAN)):
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE thread_goals(thread_id TEXT PRIMARY KEY, goal_id TEXT, "
                           "objective TEXT, status TEXT)")
                db.execute("INSERT INTO thread_goals VALUES (?, 'goal', 'private synthetic text', 'active')", (key,))
                db.commit()
        sessions = self.home / "sessions" / "2026" / "11" / "06"
        archive = self.home / "archived_sessions"
        sessions.mkdir(parents=True)
        archive.mkdir()
        for folder in (sessions, archive):
            (folder / ("rollout-2026-11-06T12-00-00-" + FILE_ONLY + ".jsonl")).write_bytes(b"private fixture")
        index = self.home / "session_index.jsonl"
        index.write_text(json.dumps({"id": INDEX_ONLY, "thread_name": "fake", "updated_at": "unknown"}) + "\n")
        before = {p: p.read_bytes() for p in (self.state, self.cache, split, index)}
        codes = {item.code: item.count for item in inspect(self.home).findings}
        for code in (Code.DESKTOP_PROJECTS, Code.DESKTOP_CACHE_EMPTY, Code.DESKTOP_UNASSIGNED,
                     Code.DESKTOP_CANDIDATES, Code.GOAL_LEGACY, Code.GOAL_SPLIT, Code.GOAL_ORPHAN,
                     Code.DESKTOP_SESSION_UNLISTED, Code.DESKTOP_SESSION_DUPLICATE,
                     Code.DESKTOP_SESSION_INDEX_ONLY):
            self.assertEqual(codes[code], 1, code)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertFalse(self.store.exists())

    def test_split_goal_and_session_survive_unusable_thread_database(self):
        goal_db = self.home / "goals_1.sqlite"
        with closing(sqlite3.connect(goal_db)) as db:
            db.execute("CREATE TABLE thread_goals(thread_id TEXT PRIMARY KEY, goal_id TEXT, "
                       "objective TEXT, status TEXT)")
            db.execute("INSERT INTO thread_goals VALUES (?, 'goal', 'synthetic private text', 'active')", (THREAD,))
            db.commit()
        archive = self.home / "archived_sessions"
        archive.mkdir()
        rollout = archive / ("rollout-2026-11-06T12-00-00-" + THREAD + ".jsonl")
        rollout.write_bytes(b"synthetic private transcript")
        self.state.unlink()
        before = {path: path.read_bytes() for path in (goal_db, rollout, self.cache)}
        for expected in (Code.DB_MISSING, Code.DB_DAMAGED, Code.DB_SIDECARS):
            with self.subTest(expected=expected):
                if expected == Code.DB_DAMAGED:
                    self.state.write_bytes(b"broken synthetic database")
                elif expected == Code.DB_SIDECARS:
                    self.state.unlink()
                    self.state.touch()
                    self.state.with_name(self.state.name + "-wal").touch()
                counts = {item.code: item.count for item in inspect(self.home).findings}
                self.assertIn(expected, counts)
                self.assertEqual(counts[Code.GOAL_SPLIT], 1)
                self.assertEqual(counts[Code.GOAL_THREAD_CHECK_SKIPPED], 1)
                self.assertEqual(counts[Code.DESKTOP_SESSION_THREAD_CHECK_SKIPPED], 1)
                self.assertNotIn(Code.GOAL_ORPHAN, counts)
                self.assertNotIn(Code.DESKTOP_SESSION_UNLISTED, counts)
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertFalse(self.store.exists())

    def test_historical_recovery_rejects_duplicate_thread_identity_schema(self):
        with closing(sqlite3.connect(self.state)) as db:
            db.executescript("""
                ALTER TABLE threads RENAME TO old_threads;
                CREATE TABLE threads(id TEXT, project_id TEXT, cwd TEXT, rollout_path TEXT);
                INSERT INTO threads SELECT * FROM old_threads;
                INSERT INTO threads SELECT * FROM old_threads;
            """)
        with self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            repair.prepare(self.home, self.home, repair.HISTORY_BUILD, self.historical())
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertFalse(self.store.exists())

    def test_historical_recovery_rejects_composite_key_even_without_duplicate_rows(self):
        with closing(sqlite3.connect(self.state)) as db:
            db.executescript("""
                ALTER TABLE threads RENAME TO old_threads;
                CREATE TABLE threads(id TEXT, variant INTEGER, project_id TEXT, PRIMARY KEY(id, variant));
                INSERT INTO threads SELECT id, 1, project_id FROM old_threads;
            """)
        with self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            repair.prepare(self.home, self.home, repair.HISTORY_BUILD, self.historical())
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertFalse(self.store.exists())

    def test_unknown_schema_is_diagnostic_only(self):
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("DROP TABLE project_idempotency_keys")
        self.assertIn(Code.DESKTOP_SCHEMA, {item.code for item in inspect(self.home).findings})
        with self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            self.plan()

    @unittest.skipUnless(os.name == "nt", "requires Windows file-sharing guards")
    def test_recent_history_repairs_only_cache_with_guarded_wal(self):
        history = self.historical()
        version = repair.RECENT_HISTORY_BUILD
        self.installed_desktop_version.return_value = version
        with self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            repair.prepare(self.home, self.home, version, history)
        with self.assertRaisesRegex(RepairError, "desktop_version"):
            repair.prepare(self.home, self.home, version)
        current = json.loads(self.original)
        current["app-server-projects-migration-by-host"] = {
            "local": {"projectsMigrated": True, "threadAssignmentsMigrated": False},
        }
        self.cache.write_text(json.dumps(current), encoding="utf-8")
        source = self.base / "recent-source.sqlite"
        shutil.copyfile(self.state, source)
        sqlite_files = [self.state.with_name(self.state.name + suffix) for suffix in ("", "-wal", "-shm")]
        with closing(sqlite3.connect(source)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("UPDATE projects SET updated_at_ms=25")
            db.commit()
            for suffix, target in zip(("", "-wal", "-shm"), sqlite_files):
                shutil.copyfile(source.with_name(source.name + suffix), target)
        hashes = {path: path.read_bytes() for path in sqlite_files}
        original = self.cache.read_bytes()
        self.installed_desktop_version.return_value = repair.HISTORY_BUILD
        with self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            repair.prepare(self.home, self.home, repair.HISTORY_BUILD, history)
        self.installed_desktop_version.return_value = version
        plan = repair.prepare(self.home, self.home, version, history)
        self.assertTrue(plan.uses_wal)
        self.assertEqual((plan.count, plan.linked, plan.skipped), (1, 1, 1))
        self.assertEqual(self.cache.read_bytes(), original)
        with self.assertRaisesRegex(RepairError, "approval_required"):
            repair.apply(plan, self.store, approved=False)
        self.assertFalse(self.store.exists())
        guard = patch.object(repair, "ensure_closed")
        guard.start()
        self.addCleanup(guard.stop)
        with sqlite_files[1].open("r+b"), self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            repair.apply(plan, self.store, approved=True)
        self.assertFalse(self.store.exists())
        self.assertEqual(self.cache.read_bytes(), original)
        backup = repair.apply(plan, self.store, approved=True)
        result = json.loads(self.cache.read_bytes())
        self.assertEqual(result["thread-project-assignments"], {THREAD: "legacy"})
        self.assertEqual(result["app-server-projects-migration-by-host"],
                         current["app-server-projects-migration-by-host"])
        self.assertEqual(result["other"], current["other"])
        self.assertEqual({path: path.read_bytes() for path in sqlite_files}, hashes)
        restore = repair.prepare_restore(self.home, self.store, backup)
        repair.restore(restore, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), original)
        self.assertEqual({path: path.read_bytes() for path in sqlite_files}, hashes)

    def test_unknown_version_duplicate_json_missing_root_and_sidecar_refused(self):
        with self.assertRaisesRegex(RepairError, "desktop_version"):
            repair.prepare(self.home, self.home, "0.0.0")
        self.cache.write_bytes(b'{"local-projects":{},"local-projects":{}}')
        with self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            self.plan()
        self.cache.write_bytes(self.original)
        self.root.rmdir()
        with self.assertRaises(OSError):
            repair.ordinary_path(self.root, directory=True)
        with self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            self.plan()
        self.root.mkdir()
        self.state.with_name(self.state.name + "-wal").touch()
        with self.assertRaisesRegex(RepairError, "desktop_ineligible"):
            self.plan()


@unittest.skipUnless(os.name == "nt", "Atomic replacement uses Windows APIs")
class DesktopWrites(DesktopFixture):
    def setUp(self):
        super().setUp()
        process_check = patch.object(repair, "ensure_closed")
        self.closed = process_check.start()
        self.addCleanup(process_check.stop)

    def test_timestamp_repair_changes_only_missing_fields_and_rolls_back(self):
        original = self.timestamp_cache()
        database = self.state.read_bytes()
        plan = repair.prepare(self.home, self.home, repair.TIMESTAMP_BUILD)
        self.assertEqual((plan.count, plan.linked, plan.skipped), (1, 0, 0))
        self.assertEqual(self.cache.read_bytes(), original)
        with self.assertRaisesRegex(RepairError, "^approval_required$"):
            repair.apply(plan, self.store, approved=False)
        self.assertFalse(self.store.exists())
        key = repair.apply(plan, self.store, approved=True)
        expected = json.loads(original)
        expected["local-projects"]["legacy"].update(createdAt=10, updatedAt=20)
        self.assertEqual(json.loads(self.cache.read_bytes()), expected)
        self.assertEqual(repair._read_backup(self.store / "desktop", key)[1], original)
        self.assertEqual(self.state.read_bytes(), database)
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.apply(plan, self.store, approved=True)
        with self.assertRaisesRegex(RepairError, "^desktop_ineligible$"):
            repair.prepare(self.home, self.home, repair.TIMESTAMP_BUILD)
        repair.restore(repair.prepare_restore(self.home, self.store, key), self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), original)
        self.assertEqual(self.state.read_bytes(), database)

    def test_timestamp_repair_refuses_conflicting_or_incomplete_evidence(self):
        original = json.loads(self.timestamp_cache())
        variants = [
            {"id": "different"}, {"name": "different"}, {"rootPaths": []},
            {"createdAt": "10"}, {"createdAt": True}, {"createdAt": 11},
            {"updatedAt": None}, {"updatedAt": 19},
        ]
        for change in variants:
            with self.subTest(change=change):
                candidate = json.loads(json.dumps(original))
                candidate["local-projects"]["legacy"].update(change)
                self.cache.write_text(json.dumps(candidate), encoding="utf-8")
                before = self.cache.read_bytes()
                with self.assertRaisesRegex(RepairError, "^desktop_ineligible$"):
                    repair.prepare(self.home, self.home, repair.TIMESTAMP_BUILD)
                self.assertEqual(self.cache.read_bytes(), before)
        self.cache.write_text(json.dumps(original), encoding="utf-8")
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("INSERT INTO project_idempotency_keys VALUES ('alias', ?)", (CORE,))
            db.commit()
        with self.assertRaisesRegex(RepairError, "^desktop_ineligible$"):
            repair.prepare(self.home, self.home, repair.TIMESTAMP_BUILD)
        self.assertFalse(self.store.exists())

    def test_timestamp_repair_preserves_valid_existing_field_and_refuses_unknown_version(self):
        cache = json.loads(self.timestamp_cache())
        cache["local-projects"]["legacy"]["createdAt"] = 10
        self.cache.write_text(json.dumps(cache), encoding="utf-8")
        reference = repair.prepare(self.home, self.home, repair.TIMESTAMP_BUILD)
        for version in ("26.1103.3517.0", "26.924.2738.0"):
            with self.subTest(version=version), patch.object(repair, "read_cache") as read:
                with self.assertRaisesRegex(RepairError, "^desktop_version$"):
                    repair.prepare(self.home, self.home, version)
                with self.assertRaisesRegex(RepairError, "^desktop_version$"):
                    repair.apply(replace(reference, build=version), self.store, approved=True)
                read.assert_not_called()
        key = repair.apply(
            repair.prepare(self.home, self.home, repair.TIMESTAMP_BUILD), self.store, approved=True
        )
        self.assertEqual(
            json.loads(self.cache.read_bytes())["local-projects"]["legacy"],
            {**cache["local-projects"]["legacy"], "updatedAt": 20},
        )
        repair.restore(repair.prepare_restore(self.home, self.store, key), self.store, approved=True)
        self.assertEqual(json.loads(self.cache.read_bytes()), cache)

    def test_timestamp_repair_detects_changed_database_and_preserves_backup_on_failure(self):
        original = self.timestamp_cache()
        plan = repair.prepare(self.home, self.home, repair.TIMESTAMP_BUILD)
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("UPDATE projects SET updated_at_ms=21")
            db.commit()
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), original)
        self.assertFalse(self.store.exists())
        plan = repair.prepare(self.home, self.home, repair.TIMESTAMP_BUILD)
        with patch.object(repair, "_backup", side_effect=OSError("synthetic backup failure")):
            with self.assertRaises(OSError):
                repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), original)
        replace_file = repair._replace

        def fail_after_write(*args):
            replace_file(*args)
            if args[1].suffix == ".new":
                raise OSError("synthetic verification failure")

        with patch.object(repair, "_replace", side_effect=fail_after_write):
            with self.assertRaisesRegex(RepairError, "^rolled_back$"):
                repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), original)
        key, = repair.list_backups(self.store, home=self.home)
        self.assertEqual(repair._read_backup(self.store / "desktop", key)[1], original)

    def test_timestamp_repair_refuses_sidecars_and_missing_roots(self):
        self.timestamp_cache()
        wal = self.state.with_name(self.state.name + "-wal")
        wal.touch()
        with self.assertRaisesRegex(RepairError, "^desktop_ineligible$"):
            repair.prepare(self.home, self.home, repair.TIMESTAMP_BUILD)
        wal.unlink()
        self.root.rmdir()
        with self.assertRaisesRegex(RepairError, "^desktop_ineligible$"):
            repair.prepare(self.home, self.home, repair.TIMESTAMP_BUILD)
        self.assertFalse(self.store.exists())

    def test_temporary_historical_snapshot_is_validated_locked_and_kept(self):
        history = self.historical().rename(self.base / (repair.CACHE_NAME + ".tmp-20261107_abc123"))
        historical = history.read_bytes()
        plan = repair.prepare(self.home, self.home, repair.HISTORY_BUILD, history)
        original_replace = repair._replace

        def check_snapshot_lock(*args):
            with self.assertRaises(OSError):
                history.write_bytes(b"changed")
            with self.assertRaises(OSError):
                history.rename(history.with_name("moved.json"))
            return original_replace(*args)

        with patch.object(repair, "_replace", side_effect=check_snapshot_lock):
            key = repair.apply(plan, self.store, approved=True)
        self.assertEqual(history.read_bytes(), historical)
        repair.restore(repair.prepare_restore(self.home, self.store, key), self.store, approved=True)
        history.write_bytes(historical + b" ")
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.apply(plan, self.store, approved=True)
        for name in (repair.CACHE_NAME + ".tmp-", repair.CACHE_NAME + ".tmp-auth.json"):
            with self.subTest(name=name), patch.object(repair, "stable_file") as opened:
                with self.assertRaisesRegex(RepairError, "^desktop_snapshot_name$"):
                    repair.prepare(self.home, self.home, repair.HISTORY_BUILD, self.base / name)
                opened.assert_not_called()

    def test_timestamp_preview_cancel_approve_and_rollback_in_both_languages_and_themes(self):
        from PySide6.QtCore import QSettings, QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        from ai_repair_kit.gui import codex_workflow
        from ai_repair_kit.gui.window import MainWindow

        app = QApplication.instance() or QApplication([])
        database = self.state.read_bytes()
        for language in ("en", "ko"):
            for theme in ("light", "dark"):
                with self.subTest(language=language, theme=theme):
                    original = self.timestamp_cache()
                    settings = QSettings(str(self.base / "timestamp-ui.ini"), QSettings.Format.IniFormat)
                    settings.setValue("language", language)
                    settings.setValue("theme", theme)
                    window = MainWindow(app, settings, self.store)
                    try:
                        window.resize(1050, 720)
                        window.show()
                        window.nav.setCurrentRow(1)
                        flow = window.workflow
                        flow.path.setText(str(self.home))
                        flow.start_scan()
                        flow.future.result(timeout=20)
                        flow.finish_scan()
                        self.assertTrue(flow.desktop_repair.isVisible())
                        previews = []

                        def answer(role):
                            dialog = QApplication.activeModalWidget()
                            self.assertIsInstance(dialog, QMessageBox)
                            previews.append(dialog.text())
                            next(button for button in dialog.buttons()
                                 if dialog.buttonRole(button) == role).click()

                        with patch.object(codex_workflow.QInputDialog, "getText",
                                          return_value=(repair.TIMESTAMP_BUILD, True)):
                            QTimer.singleShot(0, lambda: answer(QMessageBox.ButtonRole.RejectRole))
                            flow.desktop_repair.click()
                            self.assertEqual(self.cache.read_bytes(), original)
                            QTimer.singleShot(0, lambda: answer(QMessageBox.ButtonRole.AcceptRole))
                            flow.desktop_repair.click()
                        self.assertIn("createdAt", previews[-1])
                        self.assertIn("updatedAt", previews[-1])
                        self.assertEqual(self.state.read_bytes(), database)
                        window.nav.setCurrentRow(2)
                        QTimer.singleShot(0, lambda: answer(QMessageBox.ButtonRole.AcceptRole))
                        window.restore.click()
                        self.assertEqual(self.cache.read_bytes(), original)
                    finally:
                        window.close()
                        app.removeTranslator(window.translations.translator)

    def test_sqlite_redirect_invalidates_preview_and_approval(self):
        config = self.home / "config.toml"
        old_report = inspect(self.home)
        old_plan = self.plan()
        moved_db = self.base / "other-state"
        moved_db.mkdir()
        (moved_db / self.state.name).write_bytes(self.state.read_bytes())
        config.write_text(f"sqlite_home = '{moved_db.as_posix()}'", encoding="utf-8")
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.prepare(self.home, old_report.db_home, repair.SUPPORTED_BUILD)
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.apply(old_plan, self.store, approved=True)
        self.assertFalse(self.store.exists())
        self.assertEqual(self.cache.read_bytes(), self.original)
        current = inspect(self.home)
        self.assertEqual(current.db_home, moved_db)
        plan = repair.prepare(self.home, current.db_home, repair.SUPPORTED_BUILD,
                              sqlite_override=current.sqlite_override)
        repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.state.read_bytes(), (moved_db / self.state.name).read_bytes())

    def test_sqlite_override_is_retained_from_scan_and_config_locked_during_backup(self):
        moved_db = self.base / "other-state"
        moved_db.mkdir()
        (moved_db / self.state.name).write_bytes(self.state.read_bytes())
        report = inspect(self.home, moved_db)
        self.assertEqual(report.sqlite_override, moved_db)
        plan = repair.prepare(self.home, report.db_home, repair.SUPPORTED_BUILD,
                              sqlite_override=report.sqlite_override)
        backup = repair._backup

        def attempt_change(*args):
            with self.assertRaises(OSError):
                (self.home / "config.toml").write_text("changed", encoding="utf-8")
            return backup(*args)

        with patch.object(repair, "_backup", side_effect=attempt_change):
            repair.apply(plan, self.store, approved=True)
        self.assertEqual((self.home / "config.toml").read_bytes(), b"")

    def test_historical_recovery_refuses_unrelated_files_before_opening_them(self):
        history = self.historical()
        plan = repair.prepare(self.home, self.home, repair.HISTORY_BUILD, history)
        for path in (self.home / "AuTh.JsOn", self.home / "sessions" / "rollout.jsonl",
                     self.home / "session_index.jsonl", self.state, self.cache, self.base / "other.json"):
            with self.subTest(name=path.name), patch.object(repair, "stable_file") as open_file:
                with self.assertRaisesRegex(RepairError, "^desktop_snapshot_name$"):
                    repair.prepare(self.home, self.home, repair.HISTORY_BUILD, path)
                with self.assertRaisesRegex(RepairError, "^desktop_snapshot_name$"):
                    repair.apply(replace(plan, history=path), self.store, approved=True)
                open_file.assert_not_called()
        self.assertFalse(self.store.exists())
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_historical_snapshot_stays_locked_through_replacement(self):
        history = self.historical().rename(self.base / (repair.CACHE_NAME + ".bak"))
        historical_bytes = history.read_bytes()
        plan = repair.prepare(self.home, self.home, repair.HISTORY_BUILD, history)
        original_backup = repair._backup
        original_replace = repair._replace

        def verify_backup_guard(*args):
            with self.assertRaises(OSError), history.open("r+b"):
                pass
            return original_backup(*args)

        def verify_replace_guard(*args):
            with self.assertRaises(OSError), history.open("r+b"):
                pass
            return original_replace(*args)

        with (patch.object(repair, "_backup", side_effect=verify_backup_guard) as backup,
              patch.object(repair, "_replace", side_effect=verify_replace_guard) as replace):
            repair.apply(plan, self.store, approved=True)
        backup.assert_called_once()
        replace.assert_called_once()
        self.assertEqual(history.read_bytes(), historical_bytes)
        self.assertEqual(json.loads(self.cache.read_bytes())["thread-project-assignments"], {THREAD: "legacy"})

    def test_changed_historical_snapshot_invalidates_approval(self):
        history = self.historical()
        plan = repair.prepare(self.home, self.home, repair.HISTORY_BUILD, history)
        history.write_bytes(history.read_bytes() + b"\n")
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.apply(plan, self.store, approved=True)
        history.unlink()
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.apply(plan, self.store, approved=True)
        self.assertFalse(self.store.exists())
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_config_created_during_backup_stops_replacement(self):
        config = self.home / "config.toml"
        config.unlink()
        plan = self.plan()
        calls = 0

        def create_after_backup():
            nonlocal calls
            calls += 1
            if calls == 2:
                config.write_text("sqlite_home = 'other-profile'", encoding="utf-8")

        self.closed.side_effect = create_after_backup
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertEqual(len(repair.list_backups(self.store)), 1)

    def test_empty_cache_backup_verification_and_rollback(self):
        database = self.state.read_bytes()
        native_backup = self.cache.with_name(self.cache.name + ".bak")
        native_backup.write_bytes(b"preserve existing Codex backup")
        plan = self.plan()
        self.assertEqual(plan.count, 1)
        with self.assertRaisesRegex(RepairError, "approval_required"):
            repair.apply(plan, self.store, approved=False)
        self.assertFalse(self.store.exists())
        key = repair.apply(plan, self.store, approved=True)
        result = json.loads(self.cache.read_bytes())
        self.assertEqual(result["other"], {"keep": True})
        self.assertEqual(result["local-projects"]["legacy"]["rootPaths"], [str(self.root)])
        self.assertEqual(repair.list_backups(self.store), [key])
        self.assertEqual(self.state.read_bytes(), database)
        self.assertEqual(native_backup.read_bytes(), b"preserve existing Codex backup")
        with self.assertRaisesRegex(RepairError, "stale"):
            repair.apply(plan, self.store, approved=True)
        rollback = repair.prepare_restore(self.home, self.store, key)
        repair.restore(rollback, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_historical_recovery_preserves_unneeded_null_or_absent_fields(self):
        initial = {"local-projects": {}, "thread-project-assignments": None,
                   "app-server-project-id-by-legacy-project-id-by-host": {"remote": {"kept": "value"}},
                   "unrelated": None}
        self.cache.write_text(json.dumps(initial), encoding="utf-8")
        snapshot = self.historical({"thread-project-assignments": {}, "project-order": []})
        plan = repair.prepare(self.home, self.home, repair.HISTORY_BUILD, snapshot)
        self.assertEqual((plan.count, plan.linked), (1, 0))
        repair.apply(plan, self.store, approved=True)
        result = json.loads(self.cache.read_bytes())
        self.assertIsNone(result["thread-project-assignments"])
        self.assertIsNone(result["unrelated"])
        self.assertNotIn("project-order", result)
        self.assertEqual(result["app-server-project-id-by-legacy-project-id-by-host"]["remote"], {"kept": "value"})

    def test_historical_recovery_skips_aliased_project_but_recovers_independent_link(self):
        second_root = self.base / "second-project"
        second_root.mkdir()
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("INSERT INTO projects VALUES (?, 'Second', 1, 10, 20)", (FILE_ONLY,))
            db.execute("INSERT INTO project_roots VALUES (?, 0, ?)", (FILE_ONLY, str(second_root)))
            db.execute("INSERT INTO project_idempotency_keys VALUES ('second', ?)", (FILE_ONLY,))
            db.execute("INSERT INTO threads VALUES (?, NULL, ?, '')", (INDEX_ONLY, str(second_root)))
            db.commit()
        source = self.historical()
        historical = json.loads(source.read_bytes())
        hosts = "app-server-project-id-by-legacy-project-id-by-host"
        assignments = "thread-project-assignments"
        registry = "local-projects"
        historical[hosts]["local"]["second"] = FILE_ONLY
        historical[registry]["second"] = {"id": "second", "name": "Second", "rootPaths": [str(second_root)]}
        historical[assignments] = {THREAD: "legacy", INDEX_ONLY: "second"}
        historical["project-order"] = ["legacy", "second"]
        alias = {"id": "alternate", "name": "Fixture", "rootPaths": [str(self.root)]}
        for conflict_in in ("current", "historical"):
            with self.subTest(conflict_in=conflict_in):
                current = {registry: {}, hosts: {"local": {}}, assignments: {}}
                incoming = json.loads(json.dumps(historical))
                conflict = current if conflict_in == "current" else incoming
                conflict[registry]["alternate"] = alias
                conflict[hosts]["local"]["alternate"] = CORE
                self.cache.write_text(json.dumps(current), encoding="utf-8")
                source.write_text(json.dumps(incoming), encoding="utf-8")
                plan = repair.prepare(self.home, self.home, repair.HISTORY_BUILD, source)
                self.assertEqual((plan.count, plan.linked, plan.skipped), (1, 1, 1))
                repair.apply(plan, self.store, approved=True)
                result = json.loads(self.cache.read_bytes())
                self.assertEqual(result[assignments], {INDEX_ONLY: "second"})
                self.assertEqual(result[hosts]["local"], current[hosts]["local"] | {"second": FILE_ONLY})
                self.assertNotIn("legacy", result[registry])
                self.assertEqual(result["project-order"], ["second"])

    def test_historical_gui_preview_shows_excluded_links_and_cancel_preserves_cache(self):
        from PySide6.QtCore import QSettings, QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        from ai_repair_kit.gui import codex_workflow
        from ai_repair_kit.gui.window import MainWindow

        snapshot = self.historical()
        app = QApplication.instance() or QApplication([])
        prefs = QSettings(str(self.base / "historical-ui.ini"), QSettings.Format.IniFormat)
        prefs.setValue("language", "en")
        window = MainWindow(app, prefs, self.store)
        self.addCleanup(window.close)
        self.addCleanup(app.removeTranslator, window.translations.translator)
        window.show()
        window.nav.setCurrentRow(1)
        flow = window.workflow
        with closing(sqlite3.connect(self.home / "goals_1.sqlite")) as db:
            db.execute("CREATE TABLE fixture(id INTEGER)")
        (self.home / "goals_1.sqlite-wal").touch()
        flow.path.setText(str(self.home))
        flow.start_scan()
        flow.future.result(timeout=20)
        flow.finish_scan()
        self.assertTrue(flow.desktop_history.isVisible())
        previews = []

        def cancel():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                previews.append(dialog.text())
                dialog.defaultButton().click()

        with patch.object(
            codex_workflow.QInputDialog, "getText", return_value=(repair.HISTORY_BUILD, True)
        ) as version_prompt, patch.object(
            codex_workflow.QFileDialog, "getOpenFileName", return_value=(str(snapshot), "")
        ) as picker:
            QTimer.singleShot(0, cancel)
            flow.desktop_history.click()
        self.assertIn("Windows package (MSIX) version", version_prompt.call_args.args[2])
        self.assertIn(repair.HISTORY_BUILD, version_prompt.call_args.args[2])
        self.assertIn("Historical chat links to add: 1", previews[-1])
        self.assertIn("Historical links left untouched: 1", previews[-1])
        self.assertEqual(picker.call_args.args[3], f"Desktop cache snapshots ({repair.CACHE_NAME} {repair.CACHE_NAME}.bak {repair.CACHE_NAME}.tmp-*)")
        with patch.object(
            codex_workflow.QInputDialog, "getText", return_value=(repair.HISTORY_BUILD, True)
        ), patch.object(
            codex_workflow.QFileDialog, "getOpenFileName", return_value=(str(self.home / "auth.json"), "")
        ):
            flow.desktop_history.click()
            self.assertEqual(flow.message_text(), "Choose a pre-incident .codex-global-state.json, .bak, or .tmp-* snapshot.")
            window.language.setCurrentIndex(2)
            window.theme.setCurrentIndex(1)
            self.assertEqual(flow.message_text(), "문제가 발생하기 전의 .codex-global-state.json, .bak 또는 .tmp-* 스냅샷을 선택하세요.")
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertFalse(self.store.exists())

    def test_historical_exact_links_preserve_projectless_current_and_sqlite(self):
        database = self.state.read_bytes()
        snapshot = self.historical()
        plan = repair.prepare(self.home, self.home, repair.HISTORY_BUILD, snapshot)
        self.assertEqual((plan.count, plan.linked, plan.skipped), (1, 1, 1))
        self.cache.write_bytes(b'{"local-projects":{},"projectless-thread-ids":["' + THREAD.encode() + b'"]}')
        with self.assertRaisesRegex(RepairError, "stale"):
            repair.apply(plan, self.store, approved=True)
        plan = repair.prepare(self.home, self.home, repair.HISTORY_BUILD, snapshot)
        self.assertEqual((plan.count, plan.linked, plan.skipped), (1, 0, 2))
        key = repair.apply(plan, self.store, approved=True)
        self.assertEqual(json.loads(self.cache.read_bytes())["projectless-thread-ids"], [THREAD])
        repair.restore(repair.prepare_restore(self.home, self.store, key), self.store, approved=True)
        self.cache.write_bytes(self.original)
        repair.apply(repair.prepare(self.home, self.home, repair.HISTORY_BUILD, snapshot),
                     self.store, approved=True)
        self.assertEqual(json.loads(self.cache.read_bytes())["thread-project-assignments"], {THREAD: "legacy"})
        self.assertEqual(self.state.read_bytes(), database)

    def test_refuses_busy_process_mutex_and_backup_failure(self):
        plan = self.plan()
        self.closed.side_effect = RepairError("desktop_busy")
        with self.assertRaisesRegex(RepairError, "desktop_busy"):
            repair.apply(plan, self.store, approved=True)
        self.closed.side_effect = None
        with repair._mutex(self.home):
            with self.assertRaisesRegex(RepairError, "desktop_busy"):
                repair.apply(plan, self.store, approved=True)
        with patch.object(repair, "_backup", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_post_replace_failure_rolls_back(self):
        replace = repair._replace
        calls = 0

        def fail_after_first_write(*args):
            nonlocal calls
            calls += 1
            replace(*args)
            if calls == 1:
                raise OSError("synthetic failure")

        with patch.object(repair, "_replace", side_effect=fail_after_first_write):
            with self.assertRaisesRegex(RepairError, "^rolled_back$"):
                repair.apply(self.plan(), self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertEqual(len(repair.list_backups(self.store)), 1)

    def test_automatic_rollback_reports_and_keeps_competing_cache_replacement(self):
        replace = repair._replace
        competitor = b'{"externalChange":true}'
        database = self.state.read_bytes()

        def race_replacement(target, staging, displaced):
            if staging.suffix == ".new":
                replace(target, staging, displaced)
                raise OSError("synthetic verification failure")
            with self.assertRaises(OSError), target.open("r+b"):
                pass
            candidate = self.home / "competing-cache.json"
            candidate.write_bytes(competitor)
            replace(target, candidate, self.home / "competing-displaced.json")
            replace(target, staging, displaced)

        with patch.object(repair, "_replace", side_effect=race_replacement):
            with self.assertRaisesRegex(RepairError, "^rollback_failed$"):
                repair.apply(self.plan(), self.store, approved=True)
        key, = repair.list_backups(self.store, home=self.home)
        self.assertEqual((self.home / (".ark-" + key + ".rejected")).read_bytes(), competitor)
        self.assertEqual(repair._read_backup(self.store / "desktop", key)[1], self.original)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertEqual(self.state.read_bytes(), database)

    def test_cleanup_lock_keeps_verified_repair_and_rollback_available(self):
        unlink = Path.unlink

        def hold_recovery_files(path, *args, **kwargs):
            if path.parent == self.home and path.name.startswith(".ark-"):
                raise PermissionError("synthetic cleanup lock")
            return unlink(path, *args, **kwargs)

        plan = self.plan()
        with patch.object(Path, "unlink", autospec=True, side_effect=hold_recovery_files):
            key = repair.apply(plan, self.store, approved=True)

        self.assertEqual(repair.digest(self.cache.read_bytes()), plan.after)
        self.assertEqual(repair._read_backup(self.store / "desktop", key)[1], self.original)
        displaced = self.home / (".ark-" + key + ".original")
        self.assertEqual(displaced.read_bytes(), self.original)
        repair.restore(repair.prepare_restore(self.home, self.store, key), self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_cleanup_lock_does_not_hide_a_verified_automatic_rollback(self):
        replace = repair._replace
        unlink = Path.unlink

        def hold_staging(path, *args, **kwargs):
            if path.parent == self.home and path.suffix == ".new":
                raise PermissionError("synthetic cleanup lock")
            return unlink(path, *args, **kwargs)

        def fail_after_replacement(target, staging, displaced):
            replace(target, staging, displaced)
            if staging.suffix == ".new":
                raise OSError("synthetic replacement failure")

        plan = self.plan()
        with patch.object(Path, "unlink", autospec=True, side_effect=hold_staging), patch.object(
            repair, "_replace", side_effect=fail_after_replacement
        ):
            with self.assertRaisesRegex(RepairError, "^rolled_back$"):
                repair.apply(plan, self.store, approved=True)

        self.assertEqual(self.cache.read_bytes(), self.original)
        key, = repair.list_backups(self.store, home=self.home)
        self.assertEqual(repair._read_backup(self.store / "desktop", key)[1], self.original)

    def test_rollback_failure_keeps_verified_backup(self):
        replace = repair._replace
        calls = 0

        def fail_twice(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                replace(*args)
            raise OSError("synthetic failure")

        with patch.object(repair, "_replace", side_effect=fail_twice):
            with self.assertRaisesRegex(RepairError, "^rollback_failed$"):
                repair.apply(self.plan(), self.store, approved=True)
        key, = repair.list_backups(self.store)
        _, original = repair._read_backup(self.store / "desktop", key)
        self.assertEqual(original, self.original)
        repair.restore(repair.prepare_restore(self.home, self.store, key), self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)


    def test_gui_preview_cancel_approve_and_rollback(self):
        from PySide6.QtCore import QSettings, QTimer
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QMessageBox
        from ai_repair_kit.gui import codex_workflow
        from ai_repair_kit.gui.window import MainWindow

        app = QApplication.instance() or QApplication([])
        settings = QSettings(str(self.base / "settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("language", "en")
        window = MainWindow(app, settings, self.store)
        self.addCleanup(window.close)
        window.show()
        window.nav.setCurrentRow(1)
        flow = window.workflow
        with closing(sqlite3.connect(self.home / "goals_1.sqlite")) as db:
            db.execute("CREATE TABLE fixture(id INTEGER)")
        (self.home / "goals_1.sqlite-wal").touch()
        flow.path.setText(str(self.home))
        flow.start_scan()
        flow.future.result(timeout=20)
        flow.finish_scan()
        QTest.qWait(20)
        self.assertIn("project cache is empty", flow.results.text())
        self.assertTrue(flow.desktop_repair.isVisible())

        previews = []

        def answer(role):
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                previews.append(dialog.text())
                next(button for button in dialog.buttons() if dialog.buttonRole(button) == role).click()

        with patch.object(codex_workflow.QInputDialog, "getText", return_value=(repair.SUPPORTED_BUILD, True)):
            QTimer.singleShot(0, lambda: answer(QMessageBox.ButtonRole.RejectRole))
            flow.desktop_repair.click()
            self.assertEqual(self.cache.read_bytes(), self.original)
            self.assertFalse(self.store.exists())
            QTimer.singleShot(0, lambda: answer(QMessageBox.ButtonRole.AcceptRole))
            flow.desktop_repair.click()
        self.assertIn("Desktop cache written and verified", flow.results.text())
        window.nav.setCurrentRow(2)
        self.assertTrue(window.restore.isEnabled())
        backup_id, = repair.list_backups(self.store)
        QTimer.singleShot(0, lambda: answer(QMessageBox.ButtonRole.AcceptRole))
        window.restore.click()
        self.assertIn(backup_id, previews[-1])
        self.assertIn(repair.digest(self.original), previews[-1])
        self.assertIn(f"{len(self.original)} bytes", previews[-1])
        self.assertIn("Original Desktop cache restored", flow.backup_status.text())
        self.assertEqual(self.cache.read_bytes(), self.original)
        selected = flow.backups.currentData()[1]
        self.assertNotEqual(selected, backup_id)
        self.assertEqual(repair.prepare_restore(self.home, self.store, selected).backup_id, selected)
        flow.path.setText(str(self.base / "other-profile"))
        self.assertEqual(flow.backups.count(), 0)
        self.assertFalse(window.restore.isEnabled())
        window.create_backup.click()
        self.assertEqual(flow.backups.count(), 0)

    def test_eleven_projects_and_166_threads_restore_only_107_exact_links(self):
        snapshot = self.historical()
        data = json.loads(snapshot.read_bytes())
        data["thread-project-assignments"] = {}
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("DELETE FROM threads")
            for index in range(1, 11):
                core = str(UUID(int=1000 + index))
                key = f"legacy-{index}"
                root = self.base / f"workspace-{index}"
                root.mkdir()
                db.execute("INSERT INTO projects VALUES (?, ?, ?, 10, 20)", (core, key, index))
                db.execute("INSERT INTO project_roots VALUES (?, 0, ?)", (core, str(root)))
                db.execute("INSERT INTO project_idempotency_keys VALUES (?, ?)", (key, core))
                data["local-projects"][key] = {"id": key, "name": key, "rootPaths": [str(root)]}
                data["app-server-project-id-by-legacy-project-id-by-host"]["local"][key] = core
                data["project-order"].append(key)
            for index in range(166):
                thread = str(UUID(int=2000 + index))
                db.execute("INSERT INTO threads VALUES (?, NULL, ?, '')", (thread, str(self.root)))
                if index < 107:
                    data["thread-project-assignments"][thread] = "legacy"
            db.commit()
        snapshot.write_text(json.dumps(data), encoding="utf-8")
        database = self.state.read_bytes()
        plan = repair.prepare(self.home, self.home, repair.HISTORY_BUILD, snapshot)
        self.assertEqual((plan.count, plan.linked), (11, 107))
        repair.apply(plan, self.store, approved=True)
        restored = json.loads(self.cache.read_bytes())
        self.assertEqual(len(restored["local-projects"]), 11)
        self.assertEqual(len(restored["thread-project-assignments"]), 107)
        exact = inspect_thread(self.home, self.home, str(UUID(int=2000)))
        self.assertEqual(exact.project_links, ProjectLinks(ProjectLink.UNASSIGNED, ProjectLink.LINKED))
        self.assertEqual(self.state.read_bytes(), database)

    def test_manual_rollback_rejects_replaced_but_individually_valid_backup(self):
        key = repair.apply(self.plan(), self.store, approved=True)
        rollback = repair.prepare_restore(self.home, self.store, key)
        current = self.cache.read_bytes()
        folder = self.store / "desktop"
        metadata, _ = repair._read_backup(folder, key)
        substitute = b'{"local-projects":{},"changed":true}'
        metadata["before"] = repair.digest(substitute)
        (folder / (key + ".arkd")).write_bytes(json.dumps(metadata).encode("ascii") + b"\n" + substitute)
        self.assertEqual(repair.list_backups(self.store), [key])
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.restore(rollback, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), current)

    def test_manual_rollback_is_approval_gated_and_reversible(self):
        key = repair.apply(self.plan(), self.store, approved=True)
        repaired = self.cache.read_bytes()
        rollback = repair.prepare_restore(self.home, self.store, key)
        self.assertEqual((rollback.original_hash, rollback.size),
                         (repair.digest(self.original), len(self.original)))
        with self.assertRaisesRegex(RepairError, "^approval_required$"):
            repair.restore(rollback, self.store, approved=False)
        with patch.object(repair, "_backup", side_effect=OSError("synthetic backup failure")):
            with self.assertRaises(OSError):
                repair.restore(rollback, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), repaired)
        self.assertEqual(repair.list_backups(self.store), [key])
        reverse_key = repair.restore(rollback, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertEqual(set(repair.list_backups(self.store)), {key, reverse_key})
        self.assertEqual(repair._read_backup(self.store / "desktop", reverse_key)[1], repaired)
        reverse = repair.prepare_restore(self.home, self.store, reverse_key)
        repair.restore(reverse, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), repaired)

    def test_desktop_backup_list_filters_by_profile_and_sorts_verified_entries(self):
        folder = repair._safe_store(self.store, self.home)
        other_home = self.base / "other-profile"
        other_home.mkdir()
        old = repair._backup(folder, self.home, self.original, repair.digest(b"after old"))
        recent = repair._backup(folder, self.home, self.original, repair.digest(b"after recent"))
        foreign = repair._backup(folder, other_home, self.original, repair.digest(b"after other"))
        for index, key in enumerate((old, recent, foreign), start=1):
            timestamp = 1_750_000_000_000_000_000 + index * 1_000_000_000
            os.utime(folder / (key + ".arkd"), ns=(timestamp, timestamp))
        # Unfinished files and unrelated records must not consume the visible limit.
        for index in range(105):
            (folder / f"{index:032x}.part").touch()
        self.assertEqual(repair.list_backups(self.store, home=self.home), [recent, old])
        self.assertEqual(repair.list_backups(self.store, home=other_home), [foreign])
        self.assertEqual(repair.list_backups(self.store), [foreign, recent, old])

    def _interrupt_after_displacing_cache(self):
        def interrupted(target, staging, displaced):
            repair._publish_backup(target, displaced)
            raise KeyboardInterrupt("synthetic interruption after displacement")

        with patch.object(repair, "_replace", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                repair.apply(self.plan(), self.store, approved=True)
        key, = repair.list_backups(self.store, home=self.home)
        return key, self.home / (".ark-" + key + ".original")

    def test_interrupted_replacement_can_restore_verified_displaced_cache(self):
        key, displaced = self._interrupt_after_displacing_cache()
        self.assertFalse(self.cache.exists())
        self.assertEqual(displaced.read_bytes(), self.original)
        restore_plan = repair.prepare_restore(self.home, self.store, key)
        self.assertIsNone(restore_plan.current_hash)
        with self.assertRaisesRegex(RepairError, "^approval_required$"):
            repair.restore(restore_plan, self.store, approved=False)
        self.assertFalse(self.cache.exists())
        self.assertIsNone(repair.restore(restore_plan, self.store, approved=True))
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertFalse(displaced.exists())
        self.assertEqual(repair.list_backups(self.store), [key])
        self.assertEqual(repair._read_backup(self.store / "desktop", key)[1], self.original)

    def test_interrupted_replacement_gui_previews_missing_target_and_requires_approval(self):
        from PySide6.QtCore import QSettings, QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox
        from ai_repair_kit.gui.window import MainWindow

        key, _ = self._interrupt_after_displacing_cache()
        app = QApplication.instance() or QApplication([])
        settings = QSettings(str(self.base / "interrupted-ui.ini"), QSettings.Format.IniFormat)
        settings.setValue("language", "en")
        window = MainWindow(app, settings, self.store)
        self.addCleanup(window.close)
        window.show()
        window.workflow.path.setText(str(self.home))
        window.nav.setCurrentRow(2)
        window.create_backup.click()
        self.assertTrue(window.restore.isEnabled())
        previews = []

        def answer(role):
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                previews.append(dialog.text())
                next(button for button in dialog.buttons() if dialog.buttonRole(button) == role).click()

        QTimer.singleShot(0, lambda: answer(QMessageBox.ButtonRole.RejectRole))
        window.restore.click()
        self.assertFalse(self.cache.exists())
        self.assertIn("The current cache is missing", previews[-1])
        self.assertIn(key, previews[-1])
        QTimer.singleShot(0, lambda: answer(QMessageBox.ButtonRole.AcceptRole))
        window.restore.click()
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertIn("Original Desktop cache restored", window.workflow.backup_status.text())

    def test_interrupted_restore_does_not_overwrite_cache_created_during_move(self):
        key, displaced = self._interrupt_after_displacing_cache()
        plan = repair.prepare_restore(self.home, self.store, key)
        move = repair._publish_backup
        created = b'{"local-projects":{},"concurrent":true}'

        def create_before_move(source, target):
            target.write_bytes(created)
            move(source, target)

        with patch.object(repair, "_publish_backup", side_effect=create_before_move):
            with self.assertRaisesRegex(RepairError, "^stale$"):
                repair.restore(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), created)
        self.assertEqual(displaced.read_bytes(), self.original)

    def test_missing_cache_needs_a_matching_displaced_original(self):
        key = repair.apply(self.plan(), self.store, approved=True)
        self.cache.unlink()
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.prepare_restore(self.home, self.store, key)
        displaced = self.home / (".ark-" + key + ".original")
        displaced.write_bytes(b'{"local-projects":{},"different":true}')
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.prepare_restore(self.home, self.store, key)
        self.assertFalse(self.cache.exists())

    def test_later_edit_blocks_manual_rollback(self):
        key = repair.apply(self.plan(), self.store, approved=True)
        rollback = repair.prepare_restore(self.home, self.store, key)
        changed = self.cache.read_bytes() + b" "
        self.cache.write_bytes(changed)
        with self.assertRaisesRegex(RepairError, "stale"):
            repair.restore(rollback, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), changed)


@unittest.skipUnless(os.name == "nt", "Process snapshots use Windows APIs")
class ExpandedDesktopWrites(DesktopFixture):
    def setUp(self):
        super().setUp()
        # Exercise the pending rules against synthetic profiles without
        # exposing a way to enable them in a distributed application.
        registry = (*repair.REGISTRY_BUILDS, repair.PARTIAL_REGISTRY_BUILD)
        history = (*repair.HISTORY_BUILDS, repair.OBJECT_LINK_BUILD)
        builds = patch.multiple(repair, REGISTRY_BUILDS=registry, HISTORY_BUILDS=history)
        builds.start()
        self.addCleanup(builds.stop)
        guard = patch.object(repair, "ensure_closed")
        self.closed = guard.start()
        self.addCleanup(guard.stop)

    def add_project(self):
        second = self.base / "second-project"
        second.mkdir()
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("INSERT INTO projects VALUES (?, 'Second', 1, 30, 40)", (FILE_ONLY,))
            db.execute("INSERT INTO project_roots VALUES (?, 0, ?)", (FILE_ONLY, str(second)))
            db.execute("INSERT INTO project_idempotency_keys VALUES ('second', ?)", (FILE_ONLY,))
            db.commit()
        return second

    def partial_cache(self):
        self.add_project()
        data = json.loads(self.original)
        data["local-projects"] = {
            "legacy": {"id": "legacy", "name": "Fixture", "rootPaths": [str(self.root)],
                       "createdAt": 10, "updatedAt": 20, "extra": "keep"}
        }
        data["project-order"] = ["remote", "legacy"]
        data["projectless-thread-ids"] = [THREAD]
        self.cache.write_text(json.dumps(data), encoding="utf-8")
        return data

    def object_history(self):
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("ALTER TABLE threads ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            db.execute("ALTER TABLE threads ADD COLUMN thread_source TEXT")
            db.execute("UPDATE threads SET project_id=?, thread_source='user' WHERE id=?", (CORE, THREAD))
            db.executemany(
                "INSERT INTO threads (id, project_id, cwd, rollout_path, archived, thread_source) "
                "VALUES (?, ?, '', '', ?, ?)",
                [(ORPHAN, None, 0, "user"), (INDEX_ONLY, CORE, 0, "subagent"),
                 (FILE_ONLY, CORE, 1, "user"), (CORE, CORE, 0, "user")],
            )
            db.commit()
        current = json.loads(self.original)
        current["projectless-thread-ids"] = [CORE]
        current["app-server-project-id-by-legacy-project-id-by-host"] = {"remote": {"keep": "unchanged"}}
        self.cache.write_text(json.dumps(current), encoding="utf-8")
        prior = {key: {"projectKind": "local", "projectId": "legacy"}
                 for key in (THREAD, ORPHAN, INDEX_ONLY, FILE_ONLY, CORE)}
        return self.historical({"thread-project-assignments": prior})

    def test_partial_registry_preserves_original_ids_order_and_existing_metadata(self):
        self.installed_desktop_version.return_value = repair.PARTIAL_REGISTRY_BUILD
        data = self.partial_cache()
        original = self.cache.read_bytes()
        database = self.state.read_bytes()
        self.assertIn(Code.DESKTOP_CACHE_INCOMPLETE, {finding.code for finding in inspect(self.home).findings})
        plan = repair.prepare(self.home, self.home, repair.PARTIAL_REGISTRY_BUILD)
        self.assertEqual((plan.count, plan.linked, plan.skipped), (1, 0, 0))
        self.assertEqual(self.cache.read_bytes(), original)
        with self.assertRaisesRegex(RepairError, "^approval_required$"):
            repair.apply(plan, self.store, approved=False)
        self.assertFalse(self.store.exists())
        backup_id = repair.apply(plan, self.store, approved=True)
        data["local-projects"]["second"] = {
            "id": "second", "name": "Second", "rootPaths": [str(self.base / "second-project")],
            "createdAt": 30, "updatedAt": 40,
        }
        data["project-order"].append("second")
        self.assertEqual(json.loads(self.cache.read_bytes()), data)
        self.assertEqual(self.state.read_bytes(), database)
        with self.assertRaisesRegex(RepairError, "^desktop_ineligible$"):
            repair.prepare(self.home, self.home, repair.PARTIAL_REGISTRY_BUILD)
        repair.restore(repair.prepare_restore(self.home, self.store, backup_id), self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), original)

    def test_partial_registry_refuses_conflicting_cache_or_duplicate_roots(self):
        self.installed_desktop_version.return_value = repair.PARTIAL_REGISTRY_BUILD
        data = self.partial_cache()
        for changes in (
            {"thread-project-assignments": {THREAD: "legacy"}},
            {"project-order": ["legacy", "legacy"]},
            {"app-server-project-id-by-legacy-project-id-by-host": {"local": {"legacy": FILE_ONLY}}},
            {"app-server-projects-migration-by-host": {"local:synthetic": {}}},
        ):
            with self.subTest(changes=changes):
                self.cache.write_text(json.dumps(data | changes), encoding="utf-8")
                before = self.cache.read_bytes()
                with self.assertRaisesRegex(RepairError, "^desktop_ineligible$"):
                    repair.prepare(self.home, self.home, repair.PARTIAL_REGISTRY_BUILD)
                self.assertEqual(self.cache.read_bytes(), before)
        self.cache.write_text(json.dumps(data), encoding="utf-8")
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("UPDATE project_roots SET path=? WHERE project_id=?", (str(self.root), FILE_ONLY))
            db.commit()
        with self.assertRaisesRegex(RepairError, "^desktop_ineligible$"):
            repair.prepare(self.home, self.home, repair.PARTIAL_REGISTRY_BUILD)

    def test_new_rules_refuse_an_unverified_package_or_split_profile(self):
        with self.assertRaisesRegex(RepairError, "^desktop_profile_mismatch$"):
            repair.prepare(self.home, self.base, repair.PARTIAL_REGISTRY_BUILD)
        self.installed_desktop_version.return_value = repair.SUPPORTED_BUILD
        with self.assertRaisesRegex(RepairError, "^desktop_version_unverified$"):
            repair.prepare(self.home, self.home, repair.PARTIAL_REGISTRY_BUILD)

    def test_old_rule_cannot_bypass_missing_or_wrong_msix(self):
        plan = self.plan()
        for version in (None, repair.PARTIAL_REGISTRY_BUILD):
            with self.subTest(installed=version):
                self.installed_desktop_version.return_value = version
                with self.assertRaisesRegex(RepairError, "^desktop_version_unverified$"):
                    self.plan()
                with self.assertRaisesRegex(RepairError, "^desktop_version_unverified$"):
                    repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertFalse(self.store.exists())

    def test_new_rule_refuses_changed_codex_home_override(self):
        self.installed_desktop_version.return_value = repair.PARTIAL_REGISTRY_BUILD
        plan = repair.prepare(self.home, self.home, repair.PARTIAL_REGISTRY_BUILD)
        with patch.dict(os.environ, {"CODEX_HOME": str(self.base / "different")}):
            with self.assertRaisesRegex(RepairError, "^desktop_profile_mismatch$"):
                repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertFalse(self.store.exists())

    def test_directory_replaced_at_same_path_invalidates_preview_and_new_backup(self):
        plan = self.plan()
        key = repair.apply(plan, self.store, approved=True)
        rollback = repair.prepare_restore(self.home, self.store, key)
        repaired = self.cache.read_bytes()
        retired = self.base / "retired-profile"
        self.home.rename(retired)
        shutil.copytree(retired, self.home)
        self.assertEqual(repair.list_backups(self.store, home=self.home), [])
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.prepare_restore(self.home, self.store, key)
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.restore(rollback, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), repaired)
        self.cache.write_bytes(self.original)
        with self.assertRaisesRegex(RepairError, "^stale$"):
            repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_path_bound_legacy_backup_can_still_be_restored(self):
        plan = self.plan()
        key = repair.apply(plan, self.store, approved=True)
        folder = self.store / "desktop"
        header, original = repair._read_backup(folder, key)
        header.update(format=1, profile=repair._profile_key(self.home))
        (folder / (key + ".arkd")).write_bytes(json.dumps(header).encode("ascii") + b"\n" + original)
        self.assertEqual(repair.list_backups(self.store, home=self.home), [key])
        rollback = repair.prepare_restore(self.home, self.store, key)
        repair.restore(rollback, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)

    def test_partial_repair_refuses_new_sqlite_journal_after_backup(self):
        self.installed_desktop_version.return_value = repair.PARTIAL_REGISTRY_BUILD
        plan = repair.prepare(self.home, self.home, repair.PARTIAL_REGISTRY_BUILD)
        original = self.state.read_bytes()
        create_backup = repair._backup
        journal = self.state.with_name(self.state.name + "-wal")

        def backup_then_journal(*args):
            key = create_backup(*args)
            journal.write_bytes(b"synthetic late journal")
            return key

        with patch.object(repair, "_backup", side_effect=backup_then_journal):
            with self.assertRaisesRegex(RepairError, "^stale$"):
                repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertEqual(self.state.read_bytes(), original)
        self.assertEqual(journal.read_bytes(), b"synthetic late journal")
        self.assertEqual(len(repair.list_backups(self.store, home=self.home)), 1)

    def test_two_spellings_of_one_root_are_refused(self):
        from ai_repair_kit.tools import desktop_registry

        self.installed_desktop_version.return_value = repair.PARTIAL_REGISTRY_BUILD
        self.partial_cache()
        with patch.object(desktop_registry, "ordinary_path", return_value=self.root.stat()):
            with self.assertRaisesRegex(RepairError, "^desktop_ineligible$"):
                repair.prepare(self.home, self.home, repair.PARTIAL_REGISTRY_BUILD)

    def test_object_link_repair_refuses_a_snapshot_with_another_profile_scope(self):
        self.installed_desktop_version.return_value = repair.OBJECT_LINK_BUILD
        path = self.object_history()
        snapshot = json.loads(path.read_bytes())
        snapshot["app-server-projects-migration-by-host"] = {"local:foreign": {}}
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        with self.assertRaisesRegex(RepairError, "^desktop_snapshot_conflict$"):
            repair.prepare(self.home, self.home, repair.OBJECT_LINK_BUILD, path)
        self.assertFalse(self.store.exists())

    def test_msix_change_after_backup_leaves_both_original_and_backup(self):
        self.installed_desktop_version.side_effect = [
            repair.PARTIAL_REGISTRY_BUILD, repair.PARTIAL_REGISTRY_BUILD, "26.1103.3517.0",
        ]
        before = self.cache.read_bytes()
        plan = repair.prepare(self.home, self.home, repair.PARTIAL_REGISTRY_BUILD)
        with self.assertRaisesRegex(RepairError, "^desktop_version_unverified$"):
            repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertEqual(len(repair.list_backups(self.store, home=self.home)), 1)

    def test_object_links_require_two_sources_and_keep_other_stores(self):
        self.installed_desktop_version.return_value = repair.OBJECT_LINK_BUILD
        history = self.object_history()
        before = self.cache.read_bytes()
        database = self.state.read_bytes()
        source = history.read_bytes()
        goal = self.home / "goals_1.sqlite"
        goal.write_bytes(b"synthetic goal store not opened by cache repair")
        session = self.home / "sessions"
        session.mkdir()
        rollout = session / "synthetic.jsonl"
        rollout.write_bytes(b"synthetic rollout not opened by cache repair")
        plan = repair.prepare(self.home, self.home, repair.OBJECT_LINK_BUILD, history)
        self.assertEqual((plan.count, plan.linked, plan.skipped), (1, 1, 4))
        self.assertEqual(self.cache.read_bytes(), before)
        backup_id = repair.apply(plan, self.store, approved=True)
        expected = json.loads(before)
        expected["local-projects"] = {
            "legacy": {"id": "legacy", "name": "Fixture", "rootPaths": [str(self.root)],
                       "createdAt": 10, "updatedAt": 20},
        }
        expected["project-order"] = ["legacy"]
        expected["app-server-project-id-by-legacy-project-id-by-host"]["local"] = {"legacy": CORE}
        expected["thread-project-assignments"] = {THREAD: {"projectKind": "local", "projectId": "legacy"}}
        self.assertEqual(json.loads(self.cache.read_bytes()), expected)
        self.assertEqual(inspect_thread(self.home, self.home, THREAD).project_links.comparison,
                         ProjectComparison.AGREE)
        repair.restore(repair.prepare_restore(self.home, self.store, backup_id), self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertEqual(self.state.read_bytes(), database)
        self.assertEqual(history.read_bytes(), source)
        self.assertEqual(goal.read_bytes(), b"synthetic goal store not opened by cache repair")
        self.assertEqual(rollout.read_bytes(), b"synthetic rollout not opened by cache repair")

    def test_object_links_refuse_conflicting_snapshot_and_missing_proof(self):
        self.installed_desktop_version.return_value = repair.OBJECT_LINK_BUILD
        history = self.object_history()
        old = json.loads(history.read_bytes())
        old["app-server-project-id-by-legacy-project-id-by-host"]["local"]["legacy"] = ORPHAN
        history.write_text(json.dumps(old), encoding="utf-8")
        with self.assertRaisesRegex(RepairError, "^desktop_snapshot_conflict$"):
            repair.prepare(self.home, self.home, repair.OBJECT_LINK_BUILD, history)
        old["app-server-project-id-by-legacy-project-id-by-host"]["local"]["legacy"] = CORE
        old["thread-project-assignments"][THREAD] = {"projectKind": "cloud", "projectId": "legacy"}
        history.write_text(json.dumps(old), encoding="utf-8")
        with self.assertRaisesRegex(RepairError, "^desktop_snapshot_no_links$"):
            repair.prepare(self.home, self.home, repair.OBJECT_LINK_BUILD, history)

    def test_object_link_repair_rolls_back_after_replacement_failure(self):
        self.installed_desktop_version.return_value = repair.OBJECT_LINK_BUILD
        history = self.object_history()
        original = self.cache.read_bytes()
        plan = repair.prepare(self.home, self.home, repair.OBJECT_LINK_BUILD, history)
        replace_file = repair._replace
        first = True

        def fail_after_first_replace(*args):
            nonlocal first
            replace_file(*args)
            if first:
                first = False
                raise OSError("synthetic failure")

        with patch.object(repair, "_replace", side_effect=fail_after_first_replace):
            with self.assertRaisesRegex(RepairError, "^rolled_back$"):
                repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.cache.read_bytes(), original)
        self.assertEqual(len(repair.list_backups(self.store, home=self.home)), 1)

    def test_read_only_diagnosis_separates_cache_conflicts_stale_links_and_profile_override(self):
        self.object_history()
        data = json.loads(self.cache.read_bytes())
        data["app-server-project-id-by-legacy-project-id-by-host"]["local"] = {"legacy": ORPHAN}
        data["app-server-projects-migration-by-host"] = {"local:private-path": {}}
        data["thread-project-assignments"] = {
            THREAD: {"projectKind": "local", "projectId": "legacy"}, ORPHAN: "deleted-project",
        }
        self.cache.write_text(json.dumps(data), encoding="utf-8")
        with closing(sqlite3.connect(self.state)) as db:
            db.execute("DELETE FROM project_idempotency_keys")
            db.commit()
        original = self.cache.read_bytes()
        database = self.state.read_bytes()
        with patch.dict(os.environ, {"CODEX_HOME": str(self.base / "alternate")}):
            result = {finding.code: finding.count for finding in inspect(self.home).findings}
        self.assertEqual(result[Code.DESKTOP_CACHE_OBJECT_LINKS], 1)
        self.assertEqual(result[Code.DESKTOP_CACHE_STALE_LINKS], 2)
        self.assertEqual(result[Code.DESKTOP_CACHE_LINK_CONFLICTS], 2)
        self.assertEqual(result[Code.DESKTOP_CANONICAL_LINKS_WITHOUT_CACHE], 2)
        self.assertEqual(result[Code.DESKTOP_LEGACY_IDENTITY_MISSING], 1)
        self.assertEqual(result[Code.DESKTOP_PROFILE_QUALIFIED_MIGRATION], 1)
        self.assertEqual(result[Code.CODEX_HOME_MISMATCH], 1)
        self.assertEqual(self.cache.read_bytes(), original)
        self.assertEqual(self.state.read_bytes(), database)


    def test_profile_qualified_diagnostics_use_only_matching_home(self):
        history = self.object_history()
        prior = json.loads(history.read_bytes())
        home_key = f"local:{self.home}"
        foreign_key = f"local:{self.base / 'foreign'}"
        marker = {"projectsMigrated": True, "threadAssignmentsMigrated": False,
                  "pendingThreadAssignmentIds": [ORPHAN]}
        prior["app-server-projects-migration-by-host"] = {
            home_key: marker,
            foreign_key: marker | {"pendingThreadAssignmentIds": [str(UUID(int=999))]},
        }
        mapping_key = "app-server-project-id-by-legacy-project-id-by-host"
        prior[mapping_key] = {home_key: {"legacy": CORE}, foreign_key: {"legacy": ORPHAN}}
        self.cache.write_text(json.dumps(prior), encoding="utf-8")
        before = self.cache.read_bytes()
        findings = {x.code: x.count for x in inspect(self.home).findings}
        self.assertEqual(findings[Code.DESKTOP_ASSIGNMENT_MIGRATION_PENDING], 1)
        self.assertEqual(findings[Code.DESKTOP_PROFILE_QUALIFIED_MIGRATION], 1)
        self.assertEqual(inspect_thread(self.home, self.home, THREAD).project_links.comparison,
                         ProjectComparison.AGREE)
        self.assertEqual(self.cache.read_bytes(), before)

        prior[mapping_key]["local"] = {"legacy": ORPHAN}
        self.cache.write_text(json.dumps(prior), encoding="utf-8")
        self.assertEqual(inspect_thread(self.home, self.home, THREAD).project_links.cache,
                         ProjectLink.INCOMPLETE_MAPPING)

    def test_new_previews_use_selected_rule_in_both_languages_and_themes(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from ai_repair_kit.gui import codex_workflow
        from ai_repair_kit.gui.window import MainWindow

        self.partial_cache()
        before = self.cache.read_bytes()
        database = self.state.read_bytes()
        self.installed_desktop_version.return_value = repair.PARTIAL_REGISTRY_BUILD
        app = QApplication.instance() or QApplication([])
        for language, expected in (("en", "Missing project registrations"), ("ko", "누락 프로젝트")):
            for theme in ("light", "dark"):
                with self.subTest(language=language, theme=theme):
                    settings = QSettings(str(self.base / "new-rule-ui.ini"), QSettings.Format.IniFormat)
                    settings.setValue("language", language)
                    settings.setValue("theme", theme)
                    window = MainWindow(app, settings, self.store)
                    try:
                        window.show()
                        window.nav.setCurrentRow(1)
                        flow = window.workflow
                        flow.path.setText(str(self.home))
                        with patch.object(codex_workflow.codex_diagnostics, "installed_desktop_version",
                                          return_value=repair.PARTIAL_REGISTRY_BUILD):
                            flow.start_scan()
                            flow.future.result(timeout=20)
                            flow.finish_scan()
                        self.assertIn(repair.PARTIAL_REGISTRY_BUILD, flow.results.text())
                        self.assertTrue(flow.desktop_repair.isVisible())
                        with (patch.object(codex_workflow.QInputDialog, "getText",
                                           return_value=(repair.PARTIAL_REGISTRY_BUILD, True)),
                              patch.object(flow, "confirm_change", return_value=False) as confirm):
                            flow.desktop_repair.click()
                        self.assertIn(expected, confirm.call_args.args[1])
                        self.assertEqual(self.cache.read_bytes(), before)
                        self.assertEqual(self.state.read_bytes(), database)
                        self.assertFalse(self.store.exists())
                    finally:
                        app.removeTranslator(window.translations.translator)
                        window.close()

    def test_object_link_preview_requires_selected_snapshot_and_shows_exact_counts(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from ai_repair_kit.gui import codex_workflow
        from ai_repair_kit.gui.window import MainWindow

        history = self.object_history()
        before = self.cache.read_bytes()
        self.installed_desktop_version.return_value = repair.OBJECT_LINK_BUILD
        app = QApplication.instance() or QApplication([])
        settings = QSettings(str(self.base / "object-ui.ini"), QSettings.Format.IniFormat)
        settings.setValue("language", "en")
        window = MainWindow(app, settings, self.store)
        try:
            window.show()
            window.nav.setCurrentRow(1)
            flow = window.workflow
            flow.path.setText(str(self.home))
            flow.report = replace(inspect(self.home), desktop_version=repair.OBJECT_LINK_BUILD)
            flow.render()
            self.assertTrue(flow.desktop_history.isVisible())
            with (patch.object(codex_workflow.QInputDialog, "getText",
                               return_value=(repair.OBJECT_LINK_BUILD, True)),
                  patch.object(codex_workflow.QFileDialog, "getOpenFileName",
                               return_value=(str(history), "")),
                  patch.object(flow, "confirm_change", return_value=False) as confirm):
                flow.desktop_history.click()
            self.assertIn("confirmed by both the snapshot and SQLite: 1", confirm.call_args.args[1])
            self.assertIn("Historical links left untouched: 4", confirm.call_args.args[1])
            self.assertEqual(self.cache.read_bytes(), before)
            self.assertFalse(self.store.exists())
        finally:
            app.removeTranslator(window.translations.translator)
            window.close()


class NativeDesktopGuard(DesktopFixture):
    def test_process_started_after_backup_is_detected_and_cache_is_preserved(self):
        # Use a distinct child process in the synthetic profile. The name filter in
        # the harness excludes unrelated host applications from this test.
        from uuid import uuid4

        name = "codex-ark-fixture-" + uuid4().hex + ".exe"
        launcher = self.base / name
        system_cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
        shutil.copyfile(system_cmd, launcher)
        snapshot = repair._blocking_desktop_processes
        backup = repair._backup
        children = []

        def cleanup():
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)
                child.stdin.close()

        def start_after_backup(*args):
            key = backup(*args)
            child = subprocess.Popen(
                [str(launcher), "/d", "/q", "/k"],
                cwd=self.base,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            children.append(child)
            deadline = time.monotonic() + 5
            while child.poll() is None and time.monotonic() < deadline:
                if name in snapshot():
                    return key
                time.sleep(0.02)
            self.fail("The synthetic process was not detected by the Windows snapshot")

        self.addCleanup(cleanup)
        before = self.state.read_bytes()
        with patch.object(repair, "_blocking_desktop_processes", side_effect=lambda: snapshot() & {name}), patch.object(
            repair, "_backup", side_effect=start_after_backup
        ):
            with self.assertRaisesRegex(RepairError, "^desktop_busy$"):
                repair.apply(self.plan(), self.store, approved=True)
        self.assertIsNone(children[0].poll())
        self.assertEqual(self.cache.read_bytes(), self.original)
        self.assertEqual(self.state.read_bytes(), before)
        key, = repair.list_backups(self.store, home=self.home)
        self.assertEqual(repair._read_backup(self.store / "desktop", key)[1], self.original)
