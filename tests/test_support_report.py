"""The support report must never include locations or free-form errors."""

import json
import unittest
from pathlib import Path

from ai_repair_kit.tools.codex_diagnostics import Code, Finding, Report
from ai_repair_kit.tools.desktop_thread import Presence, ThreadReport
from ai_repair_kit.tools.support_report import format_report


class SupportReportTests(unittest.TestCase):
    def test_only_approved_metadata_is_exported(self) -> None:
        scan = Report(
            (Finding(Code.DESKTOP_CACHE_EMPTY), Finding(Code.DESKTOP_PROJECTS, 11)),
            null_config_size=123,
            db_home=Path("private-db"),
            sqlite_override=Path("private-override"),
        )
        thread = ThreadReport(Presence.FOUND, Presence.ABSENT, Presence.ABSENT,
                              history_index=Presence.ABSENT)
        summary = json.loads(format_report(scan, thread, "private-error-message"))
        self.assertEqual(set(summary), {"format", "ai_repair_kit", "last_scan", "thread_check"})
        self.assertEqual(summary["last_scan"], {"desktop_cache_empty": 1, "desktop_projects": 11})
        self.assertEqual(summary["thread_check"]["history_index"], "absent")
        self.assertNotIn("private", json.dumps(summary))

    def test_repair_failure_can_be_shared_without_stale_scan_or_thread(self) -> None:
        thread = ThreadReport(Presence.FOUND, Presence.ABSENT, Presence.ABSENT)
        summary = json.loads(format_report(None, thread, "rollback_failed"))
        self.assertEqual(summary["operation_outcome"], "rollback_failed")
        self.assertNotIn("thread_check", summary)
        self.assertNotIn("last_scan", summary)
