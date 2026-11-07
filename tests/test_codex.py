"""Synthetic fixtures for Codex diagnostics and guarded recovery."""

import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ai_repair_kit.local_files import stable_file
from ai_repair_kit.tools.codex import discover
from ai_repair_kit.tools.codex_diagnostics import Code, inspect
from ai_repair_kit.tools import codex_repair as repair

WORK = Path(__file__).resolve().parents[1] / ".test-work"
TEST_ID = "00000000-0000-4000-8000-000000000000"


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        WORK.mkdir(exist_ok=True)
        work = TemporaryDirectory(dir=WORK)
        self.addCleanup(work.cleanup)
        self.base = Path(work.name)
        self.home = self.base / "fake-codex"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.store = self.base / "backups"


class DiagnosticsTests(Fixture):
    def test_discovery_precedence_without_opening_codex(self) -> None:
        default = discover({}, self.base)
        self.assertEqual(default.home, self.base / ".codex")
        self.assertIsNone(default.executable)
        paths = discover(
            {"CODEX_HOME": str(self.home), "CODEX_SQLITE_HOME": str(self.base / "sqlite")},
            self.base,
        )
        self.assertEqual(paths.home, self.home)
        self.assertEqual(paths.sqlite_override, self.base / "sqlite")
        bin_dir = self.base / "Programs" / "OpenAI" / "Codex" / "bin"
        bin_dir.mkdir(parents=True)
        cli = bin_dir / ("codex.exe" if os.name == "nt" else "codex")
        cli.write_bytes(b"Never executed")
        self.assertEqual(discover({"LOCALAPPDATA": str(self.base)}, self.base).executable, cli)

    def test_missing_home_and_config(self) -> None:
        self.assertEqual(inspect(self.home / "missing").findings[0].code, Code.HOME_MISSING)
        codes = {item.code for item in inspect(self.home).findings}
        self.assertEqual(codes, {Code.CONFIG_MISSING, Code.DB_MISSING})

    def test_config_only_offers_exact_all_nul_case(self) -> None:
        for data, code, repairable in [
            (b"model = ", Code.CONFIG_INVALID, False),
            (b"\xff\x00", Code.CONFIG_INVALID, False),
            (b"\0" * 50, Code.CONFIG_ALL_NUL, True),
            (b"model = 'synthetic'\n", None, False),
        ]:
            with self.subTest(data=data):
                self.config.write_bytes(data)
                report = inspect(self.home)
                self.assertEqual(bool(report.null_config_size), repairable)
                self.assertEqual(
                    {finding.code for finding in report.findings} - {Code.DB_MISSING},
                    {code} if code else set(),
                )
                self.assertEqual(self.config.read_bytes(), data)

    def test_index_and_metadata_only_checks(self) -> None:
        self.config.write_text("", encoding="utf-8")
        index = self.home / "session_index.jsonl"
        index.write_text(
            '{"id":"' + TEST_ID + '","thread_name":"synthetic","updated_at":"unknown"}\n'
            '{"partial":\n{"unrecognized":true}\n', encoding="utf-8",
        )
        rollout = self.home / "sessions" / "rollout-synthetic.jsonl"
        rollout.parent.mkdir()
        rollout.write_text('FAKE PRIVATE CONVERSATION CONTENT', encoding="utf-8")
        project = self.base / "workspace"
        project.mkdir()
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as db:
            db.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, cwd TEXT, rollout_path TEXT, title TEXT)")
            db.executemany("INSERT INTO threads VALUES (?, ?, ?, ?)", [
                ("a", str(project), str(rollout), "private title"),
                ("b", str(project / "moved"), str(rollout.with_name("rollout-missing.jsonl")), "private"),
                ("c", "relative", str(self.base / "rollout-outside.jsonl"), "private"),
            ])
            db.commit()
        codes = {item.code: item.count for item in inspect(self.home).findings}
        self.assertEqual(codes, {
            Code.INDEX_MALFORMED: 1, Code.INDEX_UNKNOWN: 1, Code.PROJECT_MISSING: 1,
            Code.PROJECT_UNAVAILABLE: 1, Code.ROLLOUT_MISSING: 1, Code.ROLLOUT_UNAVAILABLE: 1,
        })
        self.assertEqual(rollout.read_text(encoding="utf-8"), "FAKE PRIVATE CONVERSATION CONTENT")

    def test_database_corruption_version_schema_and_sidecars(self) -> None:
        state = self.home / "state_5.sqlite"
        other = self.home / "state_9.sqlite"
        other.touch()
        self.assertIn(Code.DB_OTHER_VERSION, {item.code for item in inspect(self.home).findings})
        state.write_bytes(b"not a SQLite database")
        self.assertIn(Code.DB_DAMAGED, {item.code for item in inspect(self.home).findings})
        state.unlink()
        with closing(sqlite3.connect(state)) as db:
            db.execute("CREATE TABLE threads(id TEXT PRIMARY KEY)")
        self.assertIn(Code.DB_SCHEMA, {item.code for item in inspect(self.home).findings})
        wal = state.with_name("state_5.sqlite-wal")
        wal.write_bytes(b"synthetic")
        self.assertIn(Code.DB_SIDECARS, {item.code for item in inspect(self.home).findings})
        self.assertEqual(wal.read_bytes(), b"synthetic")

    def test_configured_database_home_and_hardlink_refusal(self) -> None:
        separate = self.base / "configured-db"
        separate.mkdir()
        (separate / "state_4.sqlite").touch()
        self.config.write_text(f"sqlite_home = '{separate.as_posix()}'", encoding="utf-8")
        self.assertIn(Code.DB_OTHER_VERSION, {item.code for item in inspect(self.home).findings})
        os.link(self.config, self.base / "other-config")
        self.assertIn(Code.CONFIG_UNAVAILABLE, {item.code for item in inspect(self.home).findings})


@unittest.skipUnless(os.name == "nt", "The write guard requires Windows")
class RepairTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.before = b"\0" * 4096
        self.config.write_bytes(self.before)

    def test_approval_repair_backup_and_explicit_rollback(self) -> None:
        plan = repair.prepare_repair(self.home)
        with self.assertRaisesRegex(repair.RepairError, "approval_required"):
            repair.apply(plan, self.store, approved=False)
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertFalse(self.store.exists())
        backup = repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.config.read_bytes(), b"")
        self.assertEqual(repair.list_backups(self.store), [backup])
        self.assertNotIn(Code.CONFIG_ALL_NUL, {item.code for item in inspect(self.home).findings})
        rollback = repair.prepare_restore(self.home, self.store, backup.id)
        repair.apply(rollback, self.store, approved=True)
        self.assertEqual(self.config.read_bytes(), self.before)

    def test_stale_approval_and_open_file_refused(self) -> None:
        plan = repair.prepare_repair(self.home)
        with stable_file(self.config):
            with self.assertRaises(OSError):
                repair.apply(plan, self.store, approved=True)
        self.config.write_bytes(b"x" * len(self.before))
        with self.assertRaisesRegex(repair.RepairError, "stale"):
            repair.apply(plan, self.store, approved=True)
        self.assertFalse(self.store.exists())

    def test_backup_failure_leaves_original_intact(self) -> None:
        plan = repair.prepare_repair(self.home)
        with patch.object(repair, "_publish_backup", side_effect=OSError("synthetic failure")):
            with self.assertRaises(OSError):
                repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(repair.list_backups(self.store), [])

    def test_post_write_failure_rolls_back_with_verified_backup(self) -> None:
        plan = repair.prepare_repair(self.home)
        verify = repair._verify

        def fail_first(stream, expected):
            if not expected:
                raise repair.RepairError("verify_failed")
            verify(stream, expected)

        with patch.object(repair, "_verify", side_effect=fail_first):
            with self.assertRaisesRegex(repair.RepairError, "^rolled_back$"):
                repair.apply(plan, self.store, approved=True)
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual(len(repair.list_backups(self.store)), 1)

    def test_restore_refuses_replacement_even_when_it_is_empty(self) -> None:
        backup = repair.apply(repair.prepare_repair(self.home), self.store, approved=True)
        self.config.rename(self.home / "old-config")
        self.config.write_bytes(b"")
        with self.assertRaisesRegex(repair.RepairError, "stale"):
            repair.prepare_restore(self.home, self.store, backup.id)

    def test_restore_refuses_changed_config_or_damaged_backup(self) -> None:
        backup = repair.apply(repair.prepare_repair(self.home), self.store, approved=True)
        self.config.write_bytes(b"model = 'synthetic'")
        with self.assertRaisesRegex(repair.RepairError, "stale"):
            repair.prepare_restore(self.home, self.store, backup.id)
        self.config.write_bytes(b"")
        (self.store / (backup.id + ".ark")).write_bytes(b"tampered\n")
        with self.assertRaisesRegex(repair.RepairError, "backup_invalid"):
            repair.prepare_restore(self.home, self.store, backup.id)
        self.assertEqual(self.config.read_bytes(), b"")
