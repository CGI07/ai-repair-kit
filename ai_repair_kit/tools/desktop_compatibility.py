"""Classify Desktop cache formats without authorizing a write."""

from dataclasses import dataclass
from enum import StrEnum

from .desktop_catalog import Unsupported, canonical_id
from .desktop_history import (
    ASSIGNMENTS, HOST_MAP, ORDER, PROJECTLESS, REGISTRY, _object, _string_list,
    matches_recent_history,
)

MIGRATION = "app-server-projects-migration-by-host"
ACCOUNT_STATE = "electron-account-renderer-state-v1"


class Operation(StrEnum):
    EMPTY_REGISTRY = "empty_registry"
    PARTIAL_REGISTRY = "partial_registry"
    TIMESTAMPS = "timestamps"
    STRING_HISTORY = "string_history"
    OBJECT_HISTORY = "object_history"
    SCOPED_REGISTRY = "scoped_registry"
    SCOPED_CHAT_LINKS = "scoped_chat_links"


class CacheFormatError(Unsupported):
    """An unknown or account-qualified cache needs independent evidence."""


@dataclass(frozen=True)
class Layout:
    operation: Operation
    allow_wal: bool = False


def _assignment_encoding(cache: dict) -> str | None:
    encodings = set()
    for value in _object(cache, ASSIGNMENTS).values():
        if isinstance(value, str) and 0 < len(value) <= 256:
            encodings.add("string")
        elif (isinstance(value, dict) and value.keys() == {"projectKind", "projectId"}
              and value["projectKind"] in ("local", "cloud")
              and isinstance(value["projectId"], str) and 0 < len(value["projectId"]) <= 256):
            encodings.add("object")
        else:
            raise CacheFormatError("unrecognized assignment shape")
    if len(encodings) > 1:
        raise CacheFormatError("mixed assignment encodings")
    return next(iter(encodings), None)


def require_legacy_scope(cache: dict) -> None:
    if ACCOUNT_STATE in cache:
        raise CacheFormatError("account-scoped renderer state")
    for field in (HOST_MAP, MIGRATION):
        hosts = _object(cache, field)
        if any(key.startswith("local:") for key in hosts):
            raise CacheFormatError("profile-qualified local state")
        _object(hosts, "local")


def _legacy_layout(cache: dict) -> str | None:
    require_legacy_scope(cache)
    if not isinstance(cache.get(REGISTRY), dict):
        raise CacheFormatError("unrecognized project registry")
    order = _string_list(cache, ORDER)
    if len(set(order)) != len(order):
        raise Unsupported("duplicate project order")
    if any(not canonical_id(thread) for thread in _string_list(cache, PROJECTLESS)):
        raise Unsupported("unsupported projectless markers")
    return _assignment_encoding(cache)


def classify_layout(current: dict, historical: dict | None = None) -> Layout:
    """Select a documented format independently of the registered package number.

    The caller must still verify the SQLite schema, identities and evidence,
    confirm profile ownership, and retain the existing transaction guards.
    """
    active_encoding = _legacy_layout(current)
    if historical is not None:
        old_encoding = _legacy_layout(historical)
        if active_encoding and old_encoding and active_encoding != old_encoding:
            raise CacheFormatError("snapshot assignment encoding differs")
        if old_encoding == "object":
            return Layout(Operation.OBJECT_HISTORY)
        if active_encoding == "object":
            raise CacheFormatError("no corroborating object-format history")
        return Layout(Operation.STRING_HISTORY, matches_recent_history(current))

    registry = current[REGISTRY]
    if not registry:
        return Layout(Operation.EMPTY_REGISTRY)
    if any(isinstance(entry, dict) and ("createdAt" not in entry or "updatedAt" not in entry)
           for entry in registry.values()):
        if active_encoding == "object":
            raise CacheFormatError("unverified timestamp and object assignment combination")
        return Layout(Operation.TIMESTAMPS)
    return Layout(Operation.PARTIAL_REGISTRY)
