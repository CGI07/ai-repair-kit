# AI Repair Kit — OpenAI Codex Desktop troubleshooting for Windows

AI Repair Kit is a free, open-source Windows utility for troubleshooting OpenAI Codex Desktop. It diagnoses missing projects, broken chat and session links, and inaccessible Goal metadata, and previews a small set of documented configuration and Desktop cache repairs. Version **1.0.0rc2** is an early release candidate under development.

[Download the Windows x64 prerelease](https://github.com/CGI07/ai-repair-kit/releases/tag/v1.0.0rc2) · [Windows installation](docs/windows-quick-start.md) · [Recovery guide](docs/desktop-recovery.md) · [Changelog](CHANGELOG.md)

![AI Repair Kit dashboard on Windows](docs/images/dashboard.png)

## What you can do

| Symptom | What AI Repair Kit can do |
| --- | --- |
| Projects disappeared from the Desktop sidebar | Check the local project catalog. Preview an empty-registry repair for MSIX `26.911.7940.0` when the surviving catalog provides the required IDs and roots. |
| Projects exist in the cache but are hidden after a custom migration | For MSIX `26.903.8094.0`, preview filling only missing numeric project timestamps from matching SQLite records. |
| Old chats no longer appear under their projects | Use a trusted pre-incident cache snapshot to restore exact associations for MSIX `26.1013.3656.0`, or for the documented empty-cache and incomplete-migration case on `26.1029.7803.0`. |
| A chat opens with only part of its history | Check the thread, referenced session file and, for known paginated storage, the history index file. [Interpret the results](docs/desktop-recovery.md#a-chat-opens-with-only-part-of-its-history). |
| A chat or Goal appears missing | Check the selected thread's recorded project link, referenced rollout file, and legacy or separate Goal records. This check does not restore chat contents or Goals. |
| The local user configuration contains only NUL bytes | Preview replacing that exact file with an empty TOML file so Codex can use defaults for the user layer. |

The version-specific repairs change only the Desktop JSON cache. A matching project directory alone is insufficient evidence of chat membership. Database changes, session reconstruction, account issues and other AI tools are outside this candidate's recovery scope. The [recovery guide](docs/desktop-recovery.md) lists the checks, evidence, and conditions for each rule.

The application has been tested on Windows 10 x64 and an offline Windows 11 x64 guest. The tests cover synthetic profiles, including metadata created by the official bundled Codex App Server. Results from independent public incident reports and the lab's own results are recorded separately. [Windows VM verification](docs/desktop-vm-verification.md)

The [issue #42739 review](docs/issue-42739.md) covers all 79 comments checked on November 7, 2026, including failed workarounds, different account/profile cases, and repairs that disappeared after restart. Each recovery rule applies only to its documented evidence and version. The [research roadmap](docs/research-roadmap.md) records related reports, evidence gaps and development priorities.

## Run on Windows

The intended platform is Windows 10 1809 or later or Windows 11, x64, with 64-bit Python 3.11 for source runs. [Qt platform requirements](https://doc.qt.io/qt-6.11/windows.html)

Download the Windows x64 ZIP and SHA256SUMS.txt from the prerelease. Verify the checksum, use **Extract All**, and open AI-Repair-Kit.exe with _internal beside it. The ZIP includes Python. If needed, install the [Microsoft Visual C++ v14 Redistributable for x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist#latest-supported-redistributable-version). See the [Windows installation guide](docs/windows-quick-start.md) for details.

To run from source, use these PowerShell commands from a checkout. Reuse an existing healthy Python 3.11 virtual environment.

```powershell
py -0p
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m ai_repair_kit
```

To build a local unsigned EXE, see the [packaging procedure](CONTRIBUTING.md#build-the-windows-candidate) and the [distribution checklist](docs/distribution.md).

The interface starts in English. Choose English, Korean, or the Windows language in **Settings**. Language changes apply immediately and your selection is saved for the next launch.

## Data safety

Select a local Codex data folder and choose **Run diagnostics**. Scans read bounded metadata. The product does not open conversation transcripts or credentials and does not send data or collect telemetry. Opening the GitHub link from **About** opens your browser.

Close Codex and related background processes before recovery. Review the preview and approve each change. The program verifies a backup before writing and checks the result afterward. Rollback has its own preview and refuses to overwrite a file that no longer matches the expected state. Recovery backups may contain private configuration or cache metadata. Keep them local. [User guide](docs/user-guide.md)

## Contribute

Use [GitHub Issues](https://github.com/CGI07/ai-repair-kit/issues) for reproducible bugs and feature requests. Share synthetic fixtures or redacted summaries. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, and translations, and [the architecture guide](docs/architecture.md) for recovery invariants. Near-term work focuses on independently verifying Desktop visibility after repair, expanding verified version coverage, and checking redistribution materials for a Windows package.

AI Repair Kit is an independent project licensed under [GPL-3.0-only](LICENSE). [Third-party components and distribution checklist](docs/distribution.md)
