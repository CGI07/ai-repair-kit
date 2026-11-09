"""Exercise format recognition independently of MSIX identity and writes."""

import unittest
from uuid import UUID

from ai_repair_kit.tools.desktop_catalog import Unsupported
from ai_repair_kit.tools.desktop_compatibility import (
    ACCOUNT_STATE, MIGRATION, CacheFormatError, Layout, Operation, classify_layout,
)
from ai_repair_kit.tools.desktop_history import ASSIGNMENTS, HOST_MAP, ORDER, PROJECTLESS, REGISTRY

THREAD = str(UUID(int=201))
ROW = {"id": "legacy", "name": "Fixture", "rootPaths": [], "createdAt": 10, "updatedAt": 20}


class CompatibilityTests(unittest.TestCase):
    def test_recognizes_registry_shapes(self):
        self.assertEqual(classify_layout({REGISTRY: {}}), Layout(Operation.EMPTY_REGISTRY))
        self.assertEqual(classify_layout({REGISTRY: {"legacy": ROW}}), Layout(Operation.PARTIAL_REGISTRY))
        missing = {key: value for key, value in ROW.items() if key != "updatedAt"}
        self.assertEqual(classify_layout({REGISTRY: {"legacy": missing}}), Layout(Operation.TIMESTAMPS))

    def test_historical_encoding_and_guarded_wal_depend_on_evidence(self):
        empty = {REGISTRY: {}}
        pending = {**empty, MIGRATION: {"local": {
            "projectsMigrated": True, "threadAssignmentsMigrated": False,
        }}}
        old = {REGISTRY: {"legacy": ROW}, ASSIGNMENTS: {THREAD: "legacy"}}
        self.assertEqual(classify_layout(empty, old), Layout(Operation.STRING_HISTORY))
        self.assertEqual(classify_layout(pending, old), Layout(Operation.STRING_HISTORY, True))
        object_old = {**old, ASSIGNMENTS: {THREAD: {"projectKind": "local", "projectId": "legacy"}}}
        self.assertEqual(classify_layout(pending, object_old), Layout(Operation.OBJECT_HISTORY))
        with self.assertRaises(CacheFormatError):
            classify_layout({**empty, ASSIGNMENTS: {THREAD: "legacy"}}, object_old)

    def test_account_and_profile_qualified_state_are_not_legacy(self):
        for fields in ({ACCOUNT_STATE: {}}, {HOST_MAP: {"local:another": {}}},
                       {MIGRATION: {"local:another": {}}}, {REGISTRY: None}):
            with self.subTest(fields=fields), self.assertRaises(CacheFormatError):
                classify_layout({REGISTRY: {}, **fields})
        with self.assertRaises(CacheFormatError):
            classify_layout({REGISTRY: {}}, {REGISTRY: {}, ACCOUNT_STATE: {}})

    def test_unrecognized_links_order_and_projectless_markers_are_rejected(self):
        mixed = {THREAD: "legacy", str(UUID(int=202)): {"projectKind": "local", "projectId": "legacy"}}
        for fields in ({ASSIGNMENTS: mixed}, {ASSIGNMENTS: {THREAD: {"projectId": "legacy"}}},
                       {ORDER: ["legacy", "legacy"]}, {PROJECTLESS: ["invalid"]}):
            with self.subTest(fields=fields), self.assertRaises(Unsupported):
                classify_layout({REGISTRY: {}, **fields})
