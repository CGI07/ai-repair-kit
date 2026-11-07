# AI Repair Kit for Windows

AI Repair Kit targets Windows 10 1809 or later and Windows 11 on x64. Check the release notes for the Windows builds actually tested. The ZIP includes Python. It uses the system-wide Microsoft Visual C++ v14 Redistributable (x64).

If the Microsoft runtime is missing or Windows reports a missing MSVCP140 or VCRUNTIME140 DLL, use the [official Microsoft x64 installer](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist#latest-supported-redistributable-version). Installation may require an administrator. Avoid downloading individual DLLs. On a clean Windows 11 test without the runtime, the EXE displayed **Failed to load Python DLL** even though the bundled DLL was present. Verify the ZIP hash and extraction, then check the x64 runtime.

1. Download the Windows x64 ZIP and its SHA-256 checksum from the same release. In PowerShell, run `Get-FileHash -Algorithm SHA256 -LiteralPath <ZIP path>` and compare the result with the ZIP entry in `SHA256SUMS.txt`.
2. Use **Extract All**. Keep `AI-Repair-Kit.exe` and its `_internal` folder together. The folder contains Python, Qt, translations and other files the EXE loads. Double-click the EXE from the extracted folder.
3. The app starts in English. To change the language, open **Settings**. Open **Diagnostics** to choose a profile and run a read-only scan.
4. Close Codex and related processes before using any repair. Review the preview and backup. After repair, reopen Codex to check the result. Use **Backups** to preview and approve a rollback when available.
5. If a repair does not help, use **Diagnostics → Review support report**. Read the summary and copy it if you want to share it. Submit it with the installed Codex MSIX version, Windows version and steps to reproduce in [GitHub Issues](https://github.com/CGI07/ai-repair-kit/issues). Keep original logs, backups, paths and credentials private. If the report is inconclusive, add the symptom, whether restarting Codex changed it, and steps to reproduce on a synthetic project. Report suspected unsafe writes through the [security policy](https://github.com/CGI07/ai-repair-kit/security/policy).

An unsigned download can trigger Microsoft Defender SmartScreen or be blocked by your organization's policy. A checksum verifies the downloaded bytes against the release checksum. Keep protection enabled and stop if Windows blocks the program. Report the warning and checksum so the maintainer can investigate. See [Microsoft's guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).
