# Changelog

## 1.0.0rc2 · Unreleased

- Added read-only diagnosis of Desktop project, chat, rollout, and Goal metadata, including guarded Windows SQLite WAL snapshots.
- Added preview, explicit approval, verified backup, and rollback for documented Desktop cache failure patterns. The supported MSIX builds and their separate conditions are in the [recovery guide](docs/desktop-recovery.md).
- Added exact historical cache recovery for the incomplete-migration case reported on MSIX `26.1029.7803.0`. The guarded state database is evidence for this operation. Writes are limited to the Desktop cache.
- Restricted historical recovery to recognized cache filenames before opening the selected file.
- Kept the historical cache snapshot locked against changes through backup and replacement.
- Required a fresh scan before exact thread lookup when the configured database location changes.
- Kept the state database guarded while reading the separate Goal store for exact thread comparisons.
- Verified the displaced cache during automatic rollback and retained it when a competing replacement was detected.
- Distinguished the thread's archive flag from rollout presence and showed Codex's unarchive path when the archived record and file agree.
- Reported the presence of the known paginated history index file for an exact chat without opening the index or transcript.
- Added tests for conflicting assignments, unsupported schemas, concurrent file changes, interrupted replacements, and rollback.
- Styled the profile path field for readable input in both Windows themes.
- Verified candidate builds on synthetic data on Windows 10 and in an offline Windows 11 guest. The [lab record](docs/desktop-vm-verification.md) distinguishes file-level recovery and the Codex App Server from Desktop screen behavior.

The build is unsigned. Goal and session-content recovery remain unavailable. The lab has not independently verified Codex Desktop visibility after recovery.
