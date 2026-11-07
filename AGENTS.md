# AI Repair Kit development guide

AI Repair Kit is a free, GPL-3.0 Windows application for diagnosing and safely repairing AI development tools. OpenAI Codex is the first target. Keep the UI approachable and localize user-facing text from the start.

- Use Python 3.11, Qt Widgets, and the project-local `.venv`. Keep dependencies minimal and compatible with GPL-3.0.
- Keep tool-specific code in `ai_repair_kit/tools/`. Keep Qt Widgets separate from file and tool logic. Prefer simple modules, readable names, and type hints.
- Use synthetic fixtures and keep test writes under the project-local `.test-work/`. Any verification that needs to inspect real Codex files requires separate user approval. Never use real user data for write, deletion, or recovery tests.
- Keep diagnosis read-only. Never open credential or conversation files. Minimize metadata reads, keep processing local, and omit private content, raw exceptions, and paths from diagnostic output and test artifacts.
- Require a documented and reproducible failure pattern, a bounded repair rule, a verified backup, a preview, explicit approval, post-write verification, and rollback before adding a write path.
- Preserve existing work and the GPL-3.0 license. Obtain user authorization for any real-data modification, commit, push, visibility change, or release.
- Run the documented automated tests and check changed screens in both languages and themes.
