# AI Repair Kit user guide

For missing Desktop projects, chat grouping, session files, or Goals, see the [Desktop recovery guide](desktop-recovery.md).

## Run a read-only scan

Open **Diagnostics**, select **Detect paths**, and review the proposed Codex data folder. Alternatively select **Choose folder**. Select **Run diagnostics** when the path is correct. The app checks the user configuration and supported local metadata. It checks project and session paths for existence without opening project files or session contents. No diagnostics are run at startup.

| Area | Checked | Automatic action |
| --- | --- | --- |
| Installation | Local `PATH`, `CODEX_INSTALL_DIR`, and the Windows standalone CLI install directory | None |
| Data location | `CODEX_HOME` or `~/.codex`, or the folder you select | None |
| User configuration | Presence, UTF-8 TOML syntax, invalid SQLite location value, and the all-NUL failure pattern | Repair for all-NUL content only |
| Optional session-name index | Malformed JSONL and unrecognized record shape | None |
| SQLite state | `state_5.sqlite`, a bounded quick integrity check, thread schema, and up to 2,000 project and rollout references | None |

An absolute `sqlite_home` in the user configuration takes precedence over the detected `CODEX_SQLITE_HOME` override. Other config layers and relative overrides depend on the active Codex environment. Select the appropriate local Codex folder when auto-detection differs from the installation in use. Discovery does not confirm Microsoft Store or IDE package registration.

A missing user configuration can be normal. A missing SQLite database can be normal for a new installation or when its data location differs. A missing project path can indicate that the project moved. The utility skips unknown state versions and schemas, unsupported paths, and files that exceed scan limits. The state and separate Goal stores can be checked with a complete WAL and SHM pair when Windows allows all three files to be guarded against writes. The `26.1029.7803.0` historical cache rule can also use that guarded state view. The other Desktop cache rules require a journal-free state database. Session transcripts, authentication, subscriptions, permissions, network errors, and remote service problems are outside these checks.

## Preview and repair an all-NUL configuration

1. Close Codex, its terminals and any IDE extensions using it. Keep them closed until the operation finishes.
2. Run a diagnostic scan. If it detects a user `config.toml` made entirely of NUL bytes, choose **Preview repair**.
3. Review the target path and size. Approve only if this is the file you want to repair. The utility saves and verifies the original bytes, writes an empty TOML file, and checks the result.
4. Run diagnostics again. Launch Codex separately to confirm the original symptom is gone. An empty user config lets Codex use defaults for that layer. It cannot reconstruct settings from NUL bytes.

## Roll back a repair

Go to **Backups**, refresh the list, select a recovery backup, and choose **Preview rollback**. Review and approve separately. The Codex folder selected in Diagnostics must match the backup. Rollback restores the original damaged file and can prevent Codex from starting again. If the file has been edited, moved, or replaced, the app refuses to overwrite it.

Backups are stored in the application's per-user local application data folder under `recovery-backups`. Keep them if either the write or automatic rollback fails verification. All development recovery tests used synthetic data. Validation against an existing real Codex installation or data folder requires separate permission.

## Upstream evidence

- Codex documents its user configuration and storage overrides. [Config basics](https://learn.chatgpt.com/docs/config-file/config-basic) · [Environment variables](https://learn.chatgpt.com/docs/config-file/environment-variables)
- Codex parses the user config as TOML. A Windows report documents a configuration filled with NUL bytes and a startup failure. [Config loader source](https://github.com/openai/codex/blob/main/codex-rs/config/src/loader/mod.rs) · [Windows report #44374](https://github.com/openai/codex/issues/44374)
- The read-only metadata checks follow the upstream index record and thread-table definitions. [Session index source](https://github.com/openai/codex/blob/main/codex-rs/rollout/src/session_index.rs) · [SQLite thread schema](https://github.com/openai/codex/blob/main/codex-rs/state/migrations/0001_threads.sql)

## When a check or repair does not resolve the problem

Open **Diagnostics → Review support report** to see the app version, the last scan's diagnostic codes and counts, optional thread status, and any recognized operation result. Choose **Copy report** after reviewing it. Nothing is sent automatically. Include the Windows and Codex MSIX versions and explain whether the symptom persists after restarting Codex. Use [GitHub Issues](https://github.com/CGI07/ai-repair-kit/issues) for ordinary troubleshooting, and follow [the security policy](../SECURITY.md) for suspected data loss or unsafe writes.

If the report shows an unsupported version or inadequate evidence, keep the original files and use the documented checks. A file-level repair result does not show whether Codex Desktop restored visibility. If rollback could not be verified, keep Codex closed and preserve the verified backup. Do not upload raw logs, cache files, databases, credentials, or transcripts.
