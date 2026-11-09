# Changelog

## 1.0.0rc4 · Windows prerelease

- Distinguish retained chat links from missing Desktop project registrations when SQLite corroborates the local mapping.
- Reproduce No projects in the isolated MSIX `26.1103.4239.0` Desktop GUI after clearing only the synthetic registry. Preserve a separate verified baseline before Desktop rotates its companion backup.
- Add an experimental, version-independent registry-only repair for the verified same-account snapshot layout. Preserve historical Desktop timestamps and existing chat assignments, and refuse conflicting or incomplete evidence.
- Check the actual cache layout and SQLite evidence for recovery candidates, including when the installed MSIX has no write rule. Continue independent metadata checks when one store is unavailable.
- Allow the timestamp-only repair on a registered x64 Store package with a compatible legacy layout and exact active-profile evidence. Continue to require approval, a verified backup, and a fresh package and source check before replacement.
- Keep writes blocked for unverified account and profile layouts. Report candidate changes and excluded links separately from permission to write.
- Allow the lab isolation checker to validate saved VMs before a normal resume.
- Verify full registry recovery using the packaged Windows EXE and confirm both original projects and chats in isolated Codex Desktop MSIX `26.1103.4239.0` after restart. Verify backup and byte-for-byte rollback through the product UI. Reject rollback if Desktop has since reserialized the cache. Cover partial registry loss, missing chat links and conflicting migration evidence in synthetic regression tests.
- Report retained Desktop cache links when SQLite has no project assignment. With a verified snapshot, count absent cache links confirmed by explicit SQLite membership separately from unconfirmed links. The snapshot comparison remains read-only.

## 1.0.0rc3 · Published prerelease

- Added synthetic lab coverage for missing project registrations on MSIX `26.915.4065.0` and object-shaped cache links on MSIX `26.924.2738.0`. Both versions remain diagnostic-only in the distributed build until Desktop recovery can be verified.
- Added read-only findings for profile redirects, missing or ambiguous original project IDs, stale or conflicting cache links, and SQLite links missing from the cache.
- Read guarded zero-byte SQLite WALs left by a checkpoint during diagnosis, including on the `26.1103.4239.0` Windows 11 lab fixture. Existing write restrictions still apply.
- Included the detected Windows MSIX version and last scan's diagnostic codes in the optional support report.
- Reviewed all 81 comments in Codex issue #42739 and grouped follow-ups by incident. Added a security and build verification guide in response to the latest comment.

- Required an exact Store x64 MSIX match for every forward Desktop cache repair. Bound previews and new cache backups to the profile directory identity, and rejected late SQLite journals, duplicate root identities and inconsistent snapshot scope.

## 1.0.0rc2 · Published prerelease

- Defaulted fresh installations to English while retaining saved language choices.
- Added a bounded missing-project-timestamp repair for MSIX `26.903.8094.0` and recognized historical `.tmp-*` snapshots.
- Reported pending chat IDs also marked projectless without inferring membership.
- Reviewed all 79 comments available in Codex issue #42739 and documented unsupported cases.

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
