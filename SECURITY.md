# Security policy

This policy describes AI Repair Kit 1.0.0rc3.

## What the app does with local data

- The app's diagnostics and recovery code has no HTTP client, telemetry, automatic upload or update check. The About button opens the public repository in your browser when selected. The build scripts separately download pinned dependencies and official license materials.
- A scan checks the selected profile's local configuration and supported metadata, including SQLite, Desktop cache, session filenames and Goal status. It does not open credential files or conversation transcripts. The installed MSIX check runs a fixed query using Windows PowerShell and reads only the version of the registered package.
- You can review a support report before copying it to the clipboard. Its fields are limited to fixed codes, counts and version numbers. A full rollback backup contains original cache or configuration bytes and may include private settings. Keep backup files local. The app stores them unencrypted.
- A forward Desktop repair requires the exact installed Store-signed x64 MSIX and a matching preview. The rules write only the Desktop cache. The separate configuration repair can replace an all-NUL configuration file. Both require approval and a verified backup. New Desktop backups bind to the profile directory identity as well as the path. SHA-256 checks detect byte changes and provide no authentication against someone who can replace files and recompute the hashes.

## Verify a download or build it yourself

Compare the Windows ZIP against `SHA256SUMS.txt` from the same GitHub release. This verifies the bytes against the published checksum. The EXE is unsigned. Use the [pinned build instructions](CONTRIBUTING.md#build-the-windows-candidate) to build a reviewed checkout. Rebuilding the same source is supported. A byte-identical EXE or ZIP is not guaranteed by the current build scripts. The [distribution checklist](docs/distribution.md) explains what to compare and which notices to retain.

## Scope of the security checks

The rc3 review checked the source, version and profile gates, backup and rollback behavior, and the build inputs. See the [repository's code scanning](https://github.com/CGI07/ai-repair-kit/security/code-scanning) for results tied to a commit. On November 8, 2026 the [OSV API](https://google.github.io/osv.dev/post-v1-querybatch/) returned no listed entries for the nine pinned PyPI packages. That query does not cover native Qt libraries, the bundled CPython 3.11.9 runtime, SQLite, or unreported vulnerabilities. The Windows build uses the official SQLite 3.54.1 DLL, which includes later fixes for malformed database handling. Use your own local profile or synthetic fixtures. [SQLite vulnerability notes](https://sqlite.org/cves.html) · [SQLite security guidance](https://sqlite.org/security.html)

The Windows ZIP has no independent security audit. The [VM verification notes](docs/desktop-vm-verification.md#authenticated-baseline-for-msix-26110342390) distinguish normal Desktop restart behavior from unverified recovery. The two newly researched Desktop write rules are exercised in synthetic tests and unavailable in the distributed application.

## Report a vulnerability

Report suspected vulnerabilities, unauthorized file access, unsafe recovery, or data loss through GitHub's private vulnerability reporting on the repository's **Security** tab when it is enabled. If it is unavailable, ask for a private reporting channel in a public issue without including any vulnerability details. [Private reporting guidance](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/privately-reporting-a-security-vulnerability)

Include the AI Repair Kit version, Windows version, Codex MSIX version, expected safety invariant, and a synthetic reproducer when available. Keep original evidence local. Never attach profiles, backups, transcripts, databases, raw logs, credentials, or screenshots containing private data. Do not reproduce on a real profile solely to demonstrate a vulnerability.

Maintain the verified backup if recovery verification fails. Stop further writes and use a disposable copy to investigate.
