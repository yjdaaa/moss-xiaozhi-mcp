# Project Instructions

## Scope

- Work directly in the existing repository layout unless the task requires an isolated worktree.
- Read the changed files, their direct dependencies, matching tests, and applicable project documentation. Do not read unrelated documents by default.
- Prefer existing helpers, naming, registration, and error-handling patterns over new abstractions.

## Safety

- Treat local command execution, GUI automation, camera access, network control, serial control, G-code, GRBL, material calibration, and laser workflows as high risk.
- Motion, laser firing, complete-file sending, file execution, deletion, formatting, restart, firmware update, upload, and network reconfiguration require explicit user confirmation and applicable online checks.
- Generation and preview must not send to hardware by default.
- Hardware, network, GUI, subprocess, and filesystem side effects in tests must use mocks, fakes, or temporary directories.
- Never guess or commit device addresses, credentials, tokens, serial ports, private paths, or user-specific runtime values.

## Development

- Preserve `register_tool(mcp)` for MCP tool modules and keep startup orchestration outside tool modules.
- Keep user-visible behavior, tool registration, prompts, and documentation consistent when contracts or defaults change.
- Use `docs/developer/safety.md` as the entry point for high-risk changes, then read only the relevant laser, runtime, or integration documentation.

## Verification

- Use the project's configured checks. The current general test command is:
  `python -m unittest discover -s tests`
- Run targeted tests for narrow changes; run the full suite for cross-module, registration, runtime, or safety-sensitive changes.
- Run linting or type checking only when the project configures the tool or the task explicitly adds it.
- Report skipped checks, unavailable hardware, and environment blockers explicitly. Do not claim PASS for checks that were not run.

## Git And Cleanup

- Do not commit, push, amend, reset, or broadly clean unless the user explicitly requests it.
- Never overwrite or include unrelated existing worktree changes.
- Clean only temporary artifacts created by the current task, using an exact verified path.

## Completion

A task is complete when the requested behavior or acceptance criteria are met, applicable checks have been run and reported, and known limitations are documented. Missing optional checks, unavailable hardware, or pending user-authorized Git actions do not by themselves invalidate the work; label the result `partial` or `blocked` when appropriate.
