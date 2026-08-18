---
task_id: "T03"
title: "Pin lazy_imports' current budget-cutoff behavior with a test"
status: "done"
depends_on: []
implements: ["FR#4", "AC#4"]
---

## Target Files

- modify: `tests/unit/rules/test_lazy_imports.py`

## Prompt

`src/house_lint/rules/lazy_imports.py`'s `detect()` (HSL002) currently has its own hand-rolled
budget check inside `_LazyImportVisitor._append` (raises `CandidateBudgetExceeded` when
`len(self.findings) >= self.limit`). A later task (T04) will refactor this to route through the
shared `analysis.append_candidate` helper instead. Per this repo's refactoring convention, pin the
current behavior with a test *before* that refactor, so the refactor's correctness is provable.

Add a test to `tests/unit/rules/test_lazy_imports.py` mirroring
`tests/unit/rules/test_llm_cruft.py:124-128`'s `test_limits_materialized_candidates_when_requested`:

```python
def test_limits_materialized_candidates_when_requested(write_sample) -> None:
    body = "\n".join(f"    import mod_{i}" for i in range(10_002))
    path = write_sample(f"def example():\n{body}\n")

    with pytest.raises(CandidateBudgetExceeded):
        detect(SourceFile(path, path.parent), limit=10_000)
```

Adjust the generated source to whatever shape actually produces >10,000 `HSL002` candidates for
this rule (imports inside a function body — check the existing test file for how `write_sample` and
`SourceFile` are already imported/used in this file, and follow that pattern). Import
`CandidateBudgetExceeded` from `house_lint.analysis` if not already imported in this test file
(check first — it likely isn't, since this is the first budget test in this file).

Do not touch `src/house_lint/rules/lazy_imports.py` itself in this task — this is a test-only
change that must pass against the *current* implementation.

## Verify

- [ ] FR#4: `uv run pytest tests/unit/rules/test_lazy_imports.py -v` passes, including the new test,
      against the current (unrefactored) `lazy_imports.py`.
- [ ] `uv run pytest -q` reports all tests passing (no regressions elsewhere).
