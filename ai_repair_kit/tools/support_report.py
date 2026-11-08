"""Create a shareable summary from existing diagnostic results."""

import json
import re

from .. import __version__
from .codex_diagnostics import Report
from .desktop_thread import ThreadReport

OUTCOMES = frozenset({
    "backup_invalid", "desktop_busy", "desktop_ineligible", "desktop_repaired",
    "desktop_restored", "desktop_snapshot_name", "desktop_version", "io_error",
    "repaired", "restored", "rollback_failed", "rolled_back", "scan_error", "stale",
    "thread_location_changed", "desktop_profile_mismatch", "desktop_version_unverified",
    "desktop_snapshot_conflict", "desktop_snapshot_no_links",
})


def format_report(
    scan: Report | None, thread: ThreadReport | None, outcome: str,
) -> str:
    """Export fixed codes, counts, and bounded version numbers."""
    data: dict[str, object] = {"format": 1, "ai_repair_kit": __version__}
    if scan is not None:
        if scan.desktop_version and re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){3}", scan.desktop_version):
            data["codex_desktop_msix"] = scan.desktop_version
        data["last_scan"] = {finding.code.value: finding.count for finding in scan.findings}
        if thread is not None:
            data["thread_check"] = {
                "thread": thread.thread.name.lower(),
                "session_reference": thread.rollout_reference.name.lower(),
                "history_index": (
                    thread.history_index.name.lower() if thread.history_index else None
                ),
                "database_project_link": thread.project_links.database.name.lower(),
                "cache_project_link": thread.project_links.cache.name.lower(),
                "project_link_comparison": thread.project_links.comparison.name.lower(),
                "legacy_goal": thread.legacy_goal.name.lower(),
                "separate_goal": thread.separate_goal.name.lower(),
                "goal_comparison": thread.goal_details.comparison.name.lower(),
            }
    if outcome in OUTCOMES:
        data["operation_outcome"] = outcome
    return json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True)
