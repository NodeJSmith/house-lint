---
task_id: "T04"
title: "Route lazy_imports' budget check through analysis.append_candidate"
status: "done"
depends_on: ["T03"]
implements: ["FR#3", "AC#3"]
---

## Target Files

- modify: `src/house_lint/rules/lazy_imports.py`

## Prompt

`src/house_lint/rules/lazy_imports.py`'s `_LazyImportVisitor._append` currently hand-rolls the same
budget-check-and-raise logic that `analysis.append_candidate` (`src/house_lint/analysis.py:53-62`)
already provides — every other rule in this codebase calls `append_candidate` directly; this rule
is the only one that reimplements it. T03 (already done) added a test pinning the current
behavior — this task must keep that test passing while removing the duplication.

Current `_append`:

```python
def _append(self, node: ast.Import | ast.ImportFrom) -> None:
    if self.limit is not None and len(self.findings) >= self.limit:
        raise CandidateBudgetExceeded(
            self.source.relative_path, candidates=tuple(self.findings)
        )
    self.findings.append(_candidate(self.source, node))
```

Replace it to call the shared helper instead:

```python
def _append(self, node: ast.Import | ast.ImportFrom) -> None:
    append_candidate(self.findings, _candidate(self.source, node), self.source, self.limit)
```

Update the import at the top of the file: drop `CandidateBudgetExceeded` from the
`from house_lint.analysis import ...` line (it's no longer referenced directly in this module —
`append_candidate` raises it internally) and add `append_candidate`:

```python
from house_lint.analysis import CandidateFinding, append_candidate, candidate_for_statement
```

Everything else in the file (the `visit_FunctionDef`/`visit_AsyncFunctionDef`/`visit_Import`/
`visit_ImportFrom` methods, `_candidate`) stays unchanged.

## Verify

- [ ] FR#3: `grep -n "CandidateBudgetExceeded" src/house_lint/rules/lazy_imports.py` shows no
      matches (it's no longer imported or referenced directly).
- [ ] AC#3: `uv run pytest tests/unit/rules/test_lazy_imports.py -v` passes — specifically the
      budget test added in T03 must still pass unchanged, proving the refactor preserves behavior.
- [ ] `uv run pytest -q` reports all tests passing.
- [ ] `uv run pyright` (strict, `src/` only) is clean.
