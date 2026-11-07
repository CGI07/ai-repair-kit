# Research and development roadmap

Reviewed on November 7, 2026. [Big Model Radar #665](https://github.com/litang9/big_model_radar/issues/665) is an automated secondary digest. It was used to locate reports, then checked against the original issues, comments, relevant PRs and release notes. A reporter's proposed cause is recorded as a claim until supported by reproducible evidence. Open issue status alone establishes neither continuing reproducibility on every build nor the absence of all related fixes.

## Current support

AI Repair Kit focuses on Windows Codex metadata. It diagnoses surviving projects, registry gaps, exact thread links, rollout references, legacy and separate Goal records, and known history-index presence. It offers bounded JSON cache and all-NUL configuration repairs. The [#42739 review](issue-42739.md) explains the complete 79-comment snapshot and the distinct eligible and unsupported cases. [Recovery rules and versions](desktop-recovery.md)

The public commit 59f044959f1ab34c08adda0292ecd115aa4d7af3 passed 77 Windows CI tests. The local follow-up passes 86 tests, including English-first startup, timestamp repair, temporary snapshots and failure paths. Independent Codex Desktop visibility after these repairs remains unverified. Test counts describe implementations and fixtures, not a production recovery success rate.

## Original reports

### Codex projects and account switching

[Codex #59895](https://github.com/openai/codex/issues/59895) reports Windows package 26.1103.3517.0 and a sequence involving an app update and switching accounts. The reporter found surviving project rows and an inactive account snapshot with saved links. Other comments report recurrence after account changes. The issue is open, and the exact cause and a fixed Desktop package were not established.

The current scanner can show surviving selected-profile records, cache gaps and incomplete migration. It does not inspect inactive account caches or transfer metadata between accounts. There is no synthetic account-switch reproduction or Desktop validation in this project. Priority is high because copying the wrong account state could expose or corrupt data. Next evidence needed is an isolated, supported account transition with explicit fixture ownership and a before/after UI baseline.

### Codex MCP lifecycle and memory

[Codex #30408](https://github.com/openai/codex/issues/30408) originally reports macOS Desktop 0.142.3 and retained per-thread MCP processes. [Codex #12491](https://github.com/openai/codex/issues/12491) originally reports macOS 26.3 and bundled CLI 0.98.0 with a large accumulated worker tree. Both issues remain open. Later comments contain different builds, process-lifetime measurements and proposed fixes. Their counts are workload-specific observations.

Three official PRs are merged. [#50380](https://github.com/openai/codex/pull/50380) handles a disconnect during MCP startup. [#51420](https://github.com/openai/codex/pull/51420) continues a slow idle shutdown. [#52081](https://github.com/openai/codex/pull/52081) addresses disconnected V2 children and changes unload timing. These are specific lifecycle changes. They do not establish that every retained or subscribed MCP process in the two reports is fixed, and this review did not establish a released Desktop package containing a complete fix.

AI Repair Kit has no MCP memory scanner or cleanup feature. Its existing process guard only refuses recovery while recognized writers may be active. Priority is medium for a future consent-based read-only diagnostic. First build isolated synthetic parent/child fixtures and record PID plus creation time, ownership, bounded process counts and memory samples. Avoid command-line strings, environment values and profile data. Parent PID or process name alone cannot prove a process is abandoned. Do not implement automatic termination or change the host Codex instance during development.

### Codex interrupted-session continuity

[Codex #45383](https://github.com/openai/codex/issues/45383) reports Windows app build 26.903.71938 and repeated reconstruction after usage-limit interruption. The issue remains open. The reporter explicitly lacks a measured breakdown of replay versus productive usage. The review found no official fix version for this exact report.

AI Repair Kit cannot refund quota, bypass limits, reconstruct missing model context or prove how much usage a continuation consumed. Priority is medium for user-owned, explicit checkpoint guidance and low for a new recovery engine. A metadata-presence check is not a continuity benchmark. No reproduction or recovery fixture for this issue exists in this repository.

### Qwen oversized transcripts

[Qwen Code #13113](https://github.com/QwenLM/qwen-code/issues/13113) reports 0.23.4 on macOS 26.6.2 and sessions rejected after growing past a transcript-index limit. Collaborator comments confirm several code paths but correct parts of the initial explanation and an ineffective byte-identical dedup proposal. This is not evidence that the same mechanism exists in Codex.

The issue is open. The tagged [v0.26.9 reader source](https://github.com/QwenLM/qwen-code/blob/v0.26.9/packages/core/src/services/session-transcript-reader.ts) still defines a 256 MiB limit and throws when the snapshot exceeds it. This source-level check does not reproduce the reporter's workload. The reviewed [v0.26.9 release](https://github.com/QwenLM/qwen-code/releases/tag/v0.26.9) does not establish a fix for the issue.

Qwen support is future work. A future opt-in size check could use file metadata and explain the version-specific limit. Transcript compaction, record deletion and parent-graph rewrites require a separately verified format and recovery design. No Qwen repair or runtime fixture was added. Priority is low until Codex Desktop verification is complete.

### Claude Code cleared content

[Claude Code #107979](https://github.com/anthropics/claude-code/issues/107979) reports version 2.1.228 on Windows 11 Enterprise with cleared pasted material after a long session and compaction. The reporter did not isolate a deterministic reproduction or establish whether the material was deleted on disk, omitted from context or filtered in display.

The issue remains open. The reviewed [v2.1.319 notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.319) do not establish a fix for this report. AI Repair Kit has no Claude-specific diagnostic or recovery implementation. Priority is low and research-only. Do not transfer a Claude conclusion to Codex, inspect transcripts without consent, or promise reconstruction of unavailable context.

### OpenCode queued Goal after undo

[OpenCode #62138](https://github.com/anomalyco/opencode/issues/62138) reports version 2.2.5 on NixOS 26.11. It gives a concrete sequence involving undo, a staged revert and a Goal kickoff that remains queued. The issue carries a reproduced label. Current contributor comments describe a planned fix and test, not a merged release.

The reviewed [v2.2.6 release](https://github.com/anomalyco/opencode/releases/tag/v2.2.6) adds opt-in MCP connection pooling. That feature is separate from the queued-Goal report and does not establish its resolution. AI Repair Kit has no OpenCode module or reproduction fixture. Priority is low. Preserve the distinction between OpenCode's queue lifecycle and Codex Goal storage.

## Release and source distinctions

[Codex CLI 0.167.0](https://github.com/openai/codex/releases/tag/rust-v0.167.0) includes provider-switch history visibility and compaction changes. Its notes do not establish a fix for the five Codex reports above. A CLI tag, an About-screen build and a Windows MSIX version identify different artifacts. Record each separately and require evidence for the exact installed Desktop package before adding a write rule.

## Development priorities

1. Complete independent Desktop observation for the supported JSON repair patterns, including persistence after restart, an old chat and the original Goal. This needs a separately approved isolated connectivity and test-account path.
2. Expand exact evidence coverage for project registrations, assignment objects and account-scoped caches only after version-specific fixtures and ownership rules exist. Preserve current identities and explicit projectless choices.
3. Improve optional history-index diagnostics while distinguishing file presence, committed events, API projection and visible items. An index repair needs a supported schema, no transcript collection and independent replay verification.
4. Design opt-in MCP process diagnostics against synthetic fixtures. Keep cleanup and process termination outside the initial feature.
5. Keep other AI tools as separate future modules. A similar symptom does not supply a compatible storage schema or safe repair.

## Minimal issue research record

Maintain this document with the tool, original URL, observed artifact version and OS, reported versus verified facts, issue state, relevant merged PR, established fix version or unknown, current utility support, fixture status, safe write eligibility and next evidence needed. Store only sanitized summaries and public links. Never copy private logs, tokens, transcripts or profile files into public research.

A future manual or scheduled GitHub Actions job could read a small approved list of public issue metadata using read-only permissions. Limit calls, respect pagination and rate-limit responses, and record the observation date. Keep changes as a reviewed proposal, without automatic comments, releases, installations or repair-rule updates. No monitoring workflow or paid API dependency was added. Any implementation needs a separate permission and usage review before enabling it.
