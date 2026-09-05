# Xiaozhi/MOSS Prompt Archive

This directory is the canonical repository archive for Xiaozhi/MOSS console prompts and knowledge-base text.

For the minimal user startup path, see [docs/user/quick-start.md](../../user/quick-start.md). For developer safety rules before editing high-risk tools, see [docs/developer/safety.md](../../developer/safety.md).

## Files

- `latest.md`: current paste-ready Xiaozhi/MOSS console prompt and knowledge-base text.
- `history/`: dated snapshots of previous and current prompt versions.

## Maintenance Rules

1. Every Xiaozhi/MOSS console prompt update must be saved in `latest.md` and copied to a dated file under `history/`.
2. Every Xiaozhi/MOSS knowledge-base update follows the same rule, even when only the knowledge-base text changes.
3. Keep Xiaozhi console prompts within **2000 total characters** by default (spaces, punctuation, and newlines count; measure the paste-ready body with Python `len(text)`), unless the user gives a different current limit.
4. Keep knowledge-base text within **200 total characters** by default under the same counting rule, and aligned with the single configured Xiaozhi knowledge-base destination; do not split guidance across multiple Xiaozhi knowledge bases. Put long SOPs, material tables, and troubleshooting in `docs/laser/` or the feature glossary, not in the 200-character knowledge-base field.
5. Do not store real MCP tokens, device IPs, serial ports, Wi-Fi passwords, camera credentials, WebUI passwords, or private runtime paths in prompt files.

## Snapshot Naming

Use `history/YYYY-MM-DD-short-topic.md`, for example `history/2026-07-01-initial.md`.
