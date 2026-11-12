# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: `--select HSL901` alone has no end-to-end test

Status: open
Recorded: 2026-11-12 (91)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- tests/integration/test_cli.py
- src/house_lint/rule_catalog.py

Issue:
`docs/suppressions.md` promises that `--select HSL901` alone still discovers Python files and reports every pragma naming another Python rule as naming a disabled rule. That depends on the HSL901 row declaring Python for `discovered_languages`. Only the row-level test (`test_the_stale_suppression_rule_is_an_opt_in_python_rule_with_no_detector`) pins it. Every integration test pairs HSL901 with another rule.

Why deferred:
The user accepted it as a rare case in the ship-time challenge: the field it depends on is pinned, and a regression would be a clean zero-file run, with no data loss.

Recommended follow-up:
Add one CLI test if a consumer starts using a `--select HSL901`-only run as a gate.

Acceptance criteria:
- A CLI test runs `check --select HSL901` on a file with a pragma for another rule and asserts one HSL901 disabled-rule finding and a nonzero scanned-file count.
