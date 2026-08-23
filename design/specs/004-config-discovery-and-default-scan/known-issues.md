# Known Issues

Durable issues discovered during orchestration that were intentionally not fixed in this run.

## KI-001: README.md and CLAUDE.md still describe the old hardcoded default scan roots

Status: resolved — fixed during known issues walkthrough
Run: 111
Source: T03
Reason not fixed now: out-of-scope
Observed in: T03 (default include scan root change)
Affected files:
- README.md
- CLAUDE.md

Issue:
`DEFAULT_INCLUDE` changed from `("src", "tests", "scripts", "tools", "examples")` to `(".",)` (root-wide
scan). `README.md:15` and `CLAUDE.md:64` still describe the old five-directory default. The design doc's
Compatibility Notes explicitly flag this as a breaking change requiring communication, but no task in
this batch's decomposition (T01, T02, T03, T04, T05, T06) targets either file — T05 only covers
`docs/configuration.md`. Flagged independently and consistently by the integration reviewer across two
review passes on T03.

Why deferred:
Fixing README.md/CLAUDE.md is outside T03's declared scope (config.py/discovery.py/tests) and outside
every other task's declared target files in this batch. No task owns the fix location.

Recommended follow-up:
Update README.md and CLAUDE.md's default-scan-roots description to match the new root-wide default
(mirroring whatever language T05 lands in docs/configuration.md), as a fast-follow after this batch ships.

Acceptance criteria:
- README.md and CLAUDE.md no longer describe `src`/`tests`/`scripts`/`tools`/`examples` as the default
  scan roots.

## KI-002: T04 touched results.py without declaring it in Target Files

Status: open
Run: 111
Source: impl-review
Reason not fixed now: out-of-scope
Observed in: T04 (commit a6443c6)
Affected files:
- src/house_lint/results.py

Issue:
T04's task file declares `Target Files` as `reporters/text.py`, `reporters/json.py`, `cli.py`, and
`tests/integration/test_reporters.py`. The executor also added a 4-line `is_zero_file_scan` property to
`ScanResult` in `results.py` (and touched `tests/integration/test_cli.py`) to support threading the
zero-file diagnostic through — a small, necessary addition, but not declared up front. Flagged as a
non-blocking WARN by the implementation review's task-scope checklist item.

Why deferred:
The change itself is correct and already shipped in T04's commit; there is nothing to fix in code. The
gap is retroactive documentation of scope, not a functional defect, so it does not warrant reopening T04.

Recommended follow-up:
None required — this is a paper trail entry only, so a future scope audit of T04 isn't surprised by the
undeclared file touch.

Acceptance criteria:
- N/A — informational; no code change expected.
