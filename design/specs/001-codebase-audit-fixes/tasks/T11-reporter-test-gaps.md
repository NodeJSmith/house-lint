---
task_id: "T11"
title: "Close reporter test gaps: render_json empty-scan and Unicode round-trip"
status: "planned"
depends_on: []
implements: ["FR#10", "AC#10"]
---

## Target Files

- modify: `tests/integration/test_reporters.py`

## Prompt

`tests/integration/test_reporters.py` has two small coverage gaps, both verified during the audit:

1. `test_text_reporter_makes_clean_empty_scans_explicit` (currently lines 57-60) asserts the
   empty-scan special case (`"empty scan: no Python files selected"`) only via `render_text`. There
   is no equivalent assertion for `render_json` on the same empty `ScanResult`.
2. No test in this file exercises non-ASCII characters in a finding's `message` or `path`. Python's
   `json.dumps` defaults to `ensure_ascii=True` (escapes non-ASCII to `\uXXXX`), while `render_text`
   prints Unicode literally — this is correct, expected behavior, just currently unverified.

Add two things to `tests/integration/test_reporters.py`:

1. Extend (or add a sibling assertion to) the empty-scan test to also call `render_json` on the
   same `ScanResult` and assert something reasonable about the JSON output for an empty scan (e.g.
   that `json.loads(render_json(result))["findings"] == []` or however `ScanResult.to_dict()`
   represents an empty findings list — check `src/house_lint/results.py`'s `to_dict()` method
   first to know the exact shape).
2. A new test with a `Finding` whose `message` contains a non-ASCII character (e.g. `"café"` or
   similar), asserting: `render_json(result)` contains the escaped form (`é` or equivalent —
   confirm the exact escape by checking what `json.dumps` actually produces for the chosen
   character) rather than the raw character, while `render_text(result)` contains the raw
   character unescaped. Follow this file's existing pattern for constructing a minimal `ScanResult`
   with one `Finding` (see `test_reporters_render_deterministic_text_and_complete_json`, lines
   13-47, for the constructor shape).

## Verify

- [ ] FR#10: Both new assertions exist in `tests/integration/test_reporters.py`.
- [ ] AC#10: `uv run pytest tests/integration/test_reporters.py -v` passes, including both new
      test cases.
- [ ] `uv run pytest -q` reports all tests passing.
