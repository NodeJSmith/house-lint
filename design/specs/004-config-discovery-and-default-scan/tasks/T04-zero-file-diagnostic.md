---
task_id: "T04"
title: "add zero-file diagnostic to reporters"
status: "planned"
depends_on: ["T01", "T02", "T03"]
implements: ["FR#8", "FR#9"]
---

## Target Files

- modify: `src/house_lint/reporters/text.py`
- modify: `src/house_lint/reporters/json.py`
- modify: `src/house_lint/cli.py`
- modify: `tests/integration/test_reporters.py`

## Prompt

Make the existing zero-file message context-aware, rather than adding a separate stderr warning. The existing `render_text()` in `reporters/text.py` already appends `"empty scan: no Python files selected"` to stdout when `files_scanned == 0` with no findings or errors (pinned by `test_text_reporter_makes_clean_empty_scans_explicit`). Extend this mechanism.

### 1. Thread config context to reporters

The reporters need to know:
- Whether `include` was explicitly empty (`include = []`) — to suppress the diagnostic
- Whether explicit CLI paths were given — to suppress the diagnostic
- The resolved config format (no config / pyproject / standalone) — to tailor the guidance message

Read `cli.py` and the reporter call sites to determine the cleanest way to thread this. Options: add fields to `ScanResult`, pass additional parameters to the render functions, or use a small context dataclass.

### 2. Extend `render_text()` in `reporters/text.py`

When `files_scanned == 0` and `not errors`:
- If `include` was explicitly empty (`include = []`) or explicit CLI paths were given: keep the existing unadorned `"empty scan: no Python files selected"` message (intentional empty scan).
- Otherwise: append context-aware guidance to the message:
  - No config file found: suggest creating a config or passing explicit paths (`house-lint <path>`)
  - `pyproject.toml` found: reference `[tool.house-lint]` include
  - Standalone config found: reference `[house-lint]` include

This fires for BOTH default and explicit `include` — a typo'd explicit `include` (e.g., `include = ["test"]` when the directory is `tests/`) is the most common real trigger and must not be suppressed.

### 3. Add equivalent signal in `render_json()` in `reporters/json.py`

The JSON reporter currently has no zero-file message. Add a field or message to the JSON output for the same zero-file condition, so machine consumers get the signal too.

### 4. Tests

In `tests/integration/test_reporters.py`:
- Update the existing `test_text_reporter_makes_clean_empty_scans_explicit` test to verify the new context-aware message (or add a sibling test)
- Test that the diagnostic message references the correct config format
- Test that `include = []` produces the unadorned "empty scan" message (no guidance)
- Test that explicit CLI paths produce the unadorned message
- Test JSON output includes the zero-file signal

## Verify

- [ ] FR#8: Scanning a project with no Python files shows a context-aware diagnostic in both text and JSON output
- [ ] FR#9: Diagnostic guidance does not appear for `include = []` or explicit CLI paths (but the base "empty scan" message still shows)
- [ ] AC#6: Running in an empty project prints the diagnostic with config guidance
- [ ] AC#7: Running with `include = []` produces the base message without guidance
