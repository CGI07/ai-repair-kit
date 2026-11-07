# Contributing

AI Repair Kit is a Windows desktop toolkit for local Codex diagnostics. Keep changes focused and describe the behavior you verified. See [AGENTS.md](AGENTS.md) for project safety rules.

## Development and tests

From the repository in PowerShell, use the Python 3.11 environment described in [README.md](README.md). In VS Code, select `.venv\Scripts\python.exe` as the interpreter. Run the tests with the same interpreter.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests exercise the actual widgets, language and theme changes, guarded repair and rollback, and isolate settings in temporary INI files under the ignored `.test-work/` directory. To run without a Windows desktop session, set `$env:QT_QPA_PLATFORM = 'offscreen'` before running the tests. A manual desktop check should cover the five pages, Tab and arrow navigation, both themes and both languages, and smaller window sizes. For end-to-end recovery verification, use the [isolated Windows VM procedure](docs/desktop-vm-verification.md).

Use synthetic fixtures for tests and public reports. Keep private logs, settings, credentials, and user data out of the repository.

## Review before sharing

For partial chat history reports, distinguish missing files, missing indexed entries and incomplete display. Tests may use a fabricated history index path, but the application checks its presence without opening it. See the [history diagnostic guide](docs/desktop-recovery.md#a-chat-opens-with-only-part-of-its-history).

Use ordinary issues for reproducible bugs and feature proposals, and the [private reporting process](SECURITY.md) for security concerns. Attach only synthetic fixtures or redacted descriptions. Keep logs, virtual disks, Codex installers, local backups and test profiles out of Git. The Windows CI workflow runs the same tests with synthetic data and read-only repository permissions.

For recovery changes, explain the evidence that makes a case eligible, how stale input is rejected, and how backup, approval, verification and rollback are exercised. State which checks used synthetic data, the bundled App Server, or Codex Desktop. Keep edits focused and retain applicable notices. See the [binary distribution checklist](docs/distribution.md) before preparing release assets.

## Build the Windows candidate

From a Windows x64 checkout with the project Python 3.11 virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[package]"
.\scripts\build_windows.ps1
```

The executable is `dist/AI-Repair-Kit/AI-Repair-Kit.exe`. Keep the whole directory together. The script checks for an existing output folder and prints the executable hash. Verify launch and close on the Windows desktop. Use synthetic data for a full repair and rollback check. Release signing, dependency redistribution materials, and publication are separate steps.

## Translations

English is the source language. The Korean catalog is `ai_repair_kit/translations/app_ko.ts`. Update it after changing any `tr()` text in the UI.

```powershell
.\.venv\Scripts\pyside6-lupdate.exe ai_repair_kit/gui/window.py ai_repair_kit/gui/codex_workflow.py -ts ai_repair_kit/translations/app_ko.ts
.\.venv\Scripts\pyside6-lrelease.exe ai_repair_kit/translations/app_ko.ts -qm ai_repair_kit/translations/app_ko.qm
```

Edit the TS file with Qt Linguist or a text editor between those commands. Keep placeholders such as `%1` and adapt their position to the language. Keep pluralizable text in one message and use Qt's numerus argument and `%n` when adding counts. Mark unreviewed strings unfinished so Qt can fall back to English. Keep both TS and QM in the change, and test each page with the resulting catalog.

For a new language, run `lupdate` with a new `app_<code>.ts` and `-target-language <locale>`. Compile it to the matching QM and register its code and native display name in `ai_repair_kit/i18n.py` once the translation has been reviewed. Use Qt locale codes such as `pt_BR` when region matters. Verify wrapping and keyboard navigation in the new language. The Qt locale sets the layout direction, so also check mirrored layouts and icon meanings when adding an RTL language.
