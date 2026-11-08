"""Synthetic checks for the pinned Desktop projection model."""

import json
import unittest

from scripts.verify_desktop_projection_lab import LINKS, MIGRATION, REGISTRY, load_cache, model, synthetic


class ProjectionLabTests(unittest.TestCase):
    def test_valid_empty_registry_does_not_use_old_backup(self):
        result = synthetic()["empty_registry_with_retained_links"]
        self.assertEqual((result["cache_source"], result["projected_local_projects"]), ("primary", 0))
        self.assertEqual((result["durable_projects"], result["canonical_thread_links"]), (2, 2))
        self.assertTrue(result["migration_incomplete"])
        self.assertEqual(result["unresolved_local_links"], 2)

    def test_timestamp_schema_rejects_malformed_project_rows(self):
        results = synthetic()
        self.assertEqual(results["intact"]["projected_roots"], 2)
        invalid = results["invalid_project_records"]
        self.assertEqual((invalid["projected_local_projects"], invalid["filtered_project_rows"]), (0, 2))

    def test_backup_is_used_for_malformed_primary_only(self):
        results = synthetic()
        self.assertEqual(results["malformed_primary_uses_backup"]["projected_local_projects"], 2)
        self.assertEqual(results["missing_primary_does_not_read_backup"]["projected_local_projects"], 0)
        status, value = load_cache(b'{"value":NaN}', b'{}')
        self.assertEqual((status, value), ("backup", {}))

    def test_conflicts_are_counts_and_do_not_change_durable_links(self):
        result = synthetic()
        self.assertEqual(result["conflicting_cache_link"]["modeled_rpc_set"], 1)
        self.assertEqual(result["explicit_projectless_conflict"]["modeled_rpc_clear"], 1)
        self.assertTrue(all(case["canonical_thread_links"] == 2 for case in result.values()))
        self.assertNotIn("thread-a", json.dumps(result))

    def test_read_migration_only_visits_pending_threads(self):
        scope = "local:synthetic"
        cache = {REGISTRY: {"core-b": {}},
                 LINKS: {"old": {"projectKind": "local", "projectId": "core-b"}},
                 MIGRATION: {scope: {"threadAssignmentsReadMigrated": True,
                                    "pendingThreadAssignmentIds": []}}}
        self.assertEqual(model(cache, scope, {"core-a", "core-b"}, {"old": "core-a"})["modeled_rpc_set"], 0)
        cache[MIGRATION][scope]["pendingThreadAssignmentIds"].append("old")
        self.assertEqual(model(cache, scope, {"core-a", "core-b"}, {"old": "core-a"})["modeled_rpc_set"], 1)
