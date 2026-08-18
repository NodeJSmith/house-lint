---
task_id: "T12"
title: "Extract scan orchestration from cli.py into scanner.py"
status: "done"
depends_on: ["T07"]
implements: ["FR#11", "AC#11", "AC#12"]
---

## Target Files

- modify: `src/house_lint/cli.py`
- create: `src/house_lint/scanner.py`
- modify: `tests/integration/test_cli.py`

## Prompt

`src/house_lint/cli.py` (406 lines, the largest file in the project) mixes Cyclopts command
declarations with a scan-orchestration concern: `_scan_file`, `_load_source`,
`_scan_ready_source`, `_recover_candidate_budget` (roughly lines 120-242 — read the file first to
find their exact current locations, since T07 will have already changed line numbers slightly).
Per this project's design intent (stated in the design doc for the original feature), `cli.py`
should own "Cyclopts declaration and the sole conversion from `ScanResult` to process
output/exit status" — the per-file scan pipeline goes beyond that.

This task lands after T07 (which already simplified `check()`'s error-fallback logic) so the
functions being moved are already in their cleaned-up form.

1. Create `src/house_lint/scanner.py`. Move `_scan_file`, `_load_source`, `_scan_ready_source`,
   `_recover_candidate_budget`, and any other private helper functions that exist *only* to support
   this scan-orchestration pipeline (read `cli.py` fully first to identify the complete set — don't
   move anything still used by the Cyclopts command functions directly, like `_exit_code` or
   `_write_result`, which belong to the "ScanResult to process output" concern and should stay in
   `cli.py`). Leave `_scan()` (the function that runs discovery and loops over files to build the
   final `ScanResult`) in `cli.py` — it's the direct caller of the Cyclopts command functions and
   sits at the boundary between "orchestration" and "process entry point"; moving the lower-level
   per-file helpers it calls is the actual duplication/size problem this task addresses, not
   `_scan()` itself.
2. These functions are currently private (underscore-prefixed) because they were only ever called
   within `cli.py`. Once moved to their own module, decide whether they should keep the underscore
   prefix or drop it — per this project's coding convention (no default underscore prefixes unless
   there's a concrete reason; a function called across a module boundary by `cli.py` is a normal
   cross-module call, not an unsafe internal detail), drop the leading underscore for whichever of
   these becomes `scanner.py`'s public entry point(s) that `cli.py` calls into. Keep genuinely
   internal helpers (ones only called by other functions within `scanner.py` itself) prefixed if
   that reflects real intent, but don't default to prefixing just because they're new.
3. Update `cli.py` to import from `scanner.py` (e.g. `from .scanner import scan_file` or whatever
   the public entry point ends up named) and remove the moved function bodies.
4. This is a pure code move — no behavior change. Do not alter any logic while moving it.

## Verify

- [ ] FR#11: `src/house_lint/scanner.py` exists and contains the scan-orchestration functions.
- [ ] AC#11: `wc -l src/house_lint/cli.py` shows a reduced line count (roughly 100+ fewer lines).
- [ ] AC#11: `uv run pyright` (strict, `src/` only) is clean — this will catch any import or type
      mismatch introduced by the move.
- [ ] `uv run pytest tests/integration/test_cli.py -v` passes unchanged — this is the real proof
      the move didn't alter behavior, since these tests exercise the CLI end-to-end.
- [ ] `uv run pytest -q` reports all tests passing.
- [ ] AC#12: as the final task in this batch, confirm the cumulative state: `uv run pytest -q`
      shows all tests passing (165 plus the tests added across T04/T05/T11), `uv run ruff check .`
      is clean, and `uv run pyright` is clean.
