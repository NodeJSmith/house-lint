---
task_id: "T03"
title: "Update test infrastructure — xfails, fuzz ceiling, full verification"
status: "planned"
depends_on: ["T02"]
implements: ["AC#2", "AC#4", "AC#5", "AC#7", "AC#9"]
---

## Summary

Update the integration test infrastructure to reflect the fixed divergence. Handle the over-linting xfail per AC#9 (the under-linting xfail was already removed in T02), set the fuzz divergence ceiling to 0, remove the known-defect classification if no longer needed, and run the full test suite to verify everything passes together.

## Target Files

- modify: `tests/integration/test_gitignore_parity.py` — handle over-linting xfail per AC#9 (under-linting xfail already removed in T02)
- modify: `tests/integration/test_gitignore_fuzz.py` — set divergence ceiling to 0, remove defect classification if needed
- read: `tests/integration/_git_harness.py` — confirm harness is unchanged
- read: `design/specs/002-per-directory-gitignore-matcher/design.md` — AC#9 governance

## Prompt

### Update `tests/integration/test_gitignore_parity.py`:

1. **Handle the over-linting xfail** at lines 275-286 per AC#9 (the under-linting xfail at lines 303-318 was already removed in T02): Run the test `test_negated_directory_pattern_does_not_re_include_nested_directories` first. If it passes (the per-directory evaluation also fixed this divergence), remove its xfail marker. If it still fails, leave the xfail marker unchanged.

### Update `tests/integration/test_gitignore_fuzz.py`:

1. **Set `MAX_KNOWN_DIRECTORY_NEGATION_DIVERGENCES = 0`** at line 57. The defect class this ceiling governed no longer exists.

2. **Evaluate `_is_known_directory_negation_defect`** (lines 153-177) and `test_the_known_directory_negation_defect_does_not_widen` (lines 274-293): `_is_known_directory_negation_defect` is called at line 266 inside `test_no_divergence_ever_skips_a_file_git_would_lint` (not the ceiling test). With `MAX_KNOWN_DIRECTORY_NEGATION_DIVERGENCES` at 0, the ceiling test (`test_the_known_directory_negation_defect_does_not_widen`) becomes vacuous — asserting `len(unsafe) <= 0` when `test_no_divergence_ever_skips_a_file_git_would_lint` already asserts `not unexplained` on the same list. If the defect class no longer exists, remove `_is_known_directory_negation_defect`, simplify line 266 in `test_no_divergence_ever_skips_a_file_git_would_lint` to directly assert `not unsafe` (no known class to filter), and remove the now-vacuous ceiling test.

3. **Run the fuzz suite and check rates**: `CI=1 uv run pytest -s tests/integration/test_gitignore_fuzz.py`. If the divergence rates changed, update the `max_divergence_rate` values in the `DISTRIBUTIONS` tuple (lines 125-132) to match the new observed rates. The rates should be the same or lower — the per-directory evaluation should not introduce new divergences.

### Run full verification:

1. `uv run pytest` — full test suite must pass (AC#7)
2. Verify `test_harness_detects_a_real_divergence` still passes (AC#5) — it's parametrized and runs automatically as part of the parity suite
3. Verify fuzz suite passes within ceilings (AC#4) — covered by the fuzz run above

## Focus

- The over-linting xfail (AC#9) is a conditional: check whether the test passes BEFORE removing the marker. If it still fails, `strict=True` will cause an unexpected failure if you remove it — leave it as-is.
- The fuzz suite runs only when `CI` is set — always prefix with `CI=1`.
- `_is_known_directory_negation_defect` at line 153 checks for negated directory-only patterns in the rule set. With the defect fixed, this function's only purpose is the ceiling test. If removing both, also clean up the import in `test_no_divergence_ever_skips_a_file_git_would_lint` (line 266) — simplify to directly asserting `not unsafe` since there's no known class to filter.
- The divergence rate table in `docs/configuration.md` will be regenerated in T04 using the output from the fuzz run here. Save the output.

## Verify

- [ ] AC#2: `grep 'MAX_KNOWN_DIRECTORY_NEGATION_DIVERGENCES' tests/integration/test_gitignore_fuzz.py` shows `= 0`, and `CI=1 uv run pytest tests/integration/test_gitignore_fuzz.py` passes
- [ ] AC#4: The fuzz suite's three distributions all pass within their documented ceilings
- [ ] AC#5: `uv run pytest tests/integration/test_gitignore_parity.py::test_harness_detects_a_real_divergence -v` passes
- [ ] AC#7: `uv run pytest` passes (full test suite)
- [ ] AC#9: If the over-linting xfail test passes, its marker is removed; if it still fails, the marker is left unchanged
