---
task_id: "T05"
title: "Add budget-cutoff tests to the remaining 5 rule test files"
status: "done"
depends_on: []
implements: ["FR#4", "AC#4"]
---

## Target Files

- modify: `tests/unit/rules/test_constants_position.py`
- modify: `tests/unit/rules/test_exception_names.py`
- modify: `tests/unit/rules/test_file_length.py`
- modify: `tests/unit/rules/test_spec_tokens.py`
- modify: `tests/unit/rules/test_type_checking_position.py`

## Prompt

The `limit=`/`CandidateBudgetExceeded` safety path (bounds per-file candidate output) is currently
tested in only one rule's test suite: `tests/unit/rules/test_llm_cruft.py:124-128`
(`test_limits_materialized_candidates_when_requested`). `lazy_imports` got its own version in T03.
This task adds the same kind of test to the remaining 5 rule test files, so every rule's own path
through `analysis.append_candidate` (or, for rules with typed options, through their own detect
logic) is verified independently.

For each of these 5 files, add a test that:
1. Generates a source file (via the `write_sample` fixture already used throughout these test
   files) containing more than 10,000 instances of whatever pattern that specific rule flags.
2. Calls `detect(SourceFile(path, path.parent), <options if required>, limit=10_000)`.
3. Asserts `pytest.raises(CandidateBudgetExceeded)`.

Follow each file's own existing conventions for how `detect()` is called (some rules take
`options`, some don't — check each file's other tests first) and for what pattern that rule
actually flags:

- **`constants_position` (HSL004)**: a file with >10,000 top-level constant-after-code statements.
- **`exception_names` (HSL103)**: a file with >10,000 `except ... as <disallowed-name>:` blocks (or
  however this rule's existing tests construct violations — check `options` usage, e.g. `allowed`).
- **`file_length` (HSL102)**: this rule likely only ever produces 0 or 1 findings per file (it flags
  the file as a whole, not per-line) — read `src/house_lint/rules/file_length.py` first. If a
  budget cutoff genuinely cannot fire for this rule (fewer than `limit` candidates are structurally
  possible), skip adding a test here and note why in a one-line comment instead of forcing an
  artificial test.
- **`spec_tokens` (HSL101)**: a file with >10,000 configured spec-token violations, using
  `HSL101Options` the way `tests/unit/rules/test_spec_tokens.py`'s other tests already do.
- **`type_checking_position` (HSL003)**: a file with >10,000 `TYPE_CHECKING` blocks followed by
  imports (check the existing tests in this file for the exact violation shape).

Import `CandidateBudgetExceeded` from `house_lint.analysis` in each file that doesn't already have
it. Do not modify any `src/house_lint/rules/*.py` implementation files in this task — these rules
already route through `append_candidate` correctly (verified during the audit); this is purely
filling a test gap.

## Verify

- [ ] FR#4: Each of `constants_position`, `exception_names`, `spec_tokens`, `type_checking_position`
      has a passing budget-cutoff test (or `file_length` has a documented reason why it's skipped).
- [ ] AC#4: `grep -rl "CandidateBudgetExceeded" tests/unit/rules/*.py | wc -l` equals 7 if
      `file_length` gets a test, or 6 plus a documented skip-reason comment in
      `test_file_length.py` if it structurally can't produce a budget-exceeded case — confirm which
      applies by reading `file_length.py` first, and note the actual count/reasoning when reporting
      completion.
- [ ] `uv run pytest -q` reports all tests passing.
