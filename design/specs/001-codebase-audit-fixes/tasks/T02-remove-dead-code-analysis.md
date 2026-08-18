---
task_id: "T02"
title: "Remove confirmed dead code from analysis.py"
status: "planned"
depends_on: []
implements: ["FR#2", "AC#2"]
---

## Target Files

- modify: `src/house_lint/analysis.py`

## Prompt

`src/house_lint/analysis.py`'s `statement_owner_for_line(source, line, column=None)` contains **two
separate** `if`/`else` structures — read the current source carefully before editing, since only
one of them is dead:

```python
def statement_owner_for_line(
    source: SourceFile, line: int, column: int | None = None
) -> ast.stmt | None:
    """Return a comment's owner: innermost for trailing text, outermost at line ends."""
    if column is not None and source.lines[line - 1][:column].strip():
        candidates = [ ... ]  # trailing-comment candidates
    else:
        candidates = [ ... ]  # standalone-comment candidates
    if not candidates:
        return None
    if column is not None:
        return max(candidates, key=lambda statement: (statement.lineno, statement.col_offset))
    return min(candidates, key=statement_span)
```

Verified by grepping every call site in `src/` and `tests/`: both real callers
(`src/house_lint/suppressions.py:178` and `analysis.py`'s own `comment_owner_for_line`) always pass
an explicit `column`, so `column` is never actually `None` at runtime. That means:

- **The first `if`/`else` (the `candidates = [...]` computation) is NOT dead.** Its condition is
  `column is not None and source.lines[line - 1][:column].strip()` — a compound condition. Even
  though `column is not None` is always true, the second half
  (`source.lines[line - 1][:column].strip()`) varies per call: it's true for a *trailing* comment
  (code precedes the comment on the same line) and false for a *standalone* comment (only
  whitespace precedes it). **Both branches of this if/else are live and must be preserved** — this
  is exactly what distinguishes trailing-comment ownership from standalone-comment ownership.
  `tests/unit/rules/test_llm_cruft.py::test_standalone_body_comment_has_no_owner` and
  `tests/unit/test_suppressions.py::test_header_ignore_does_not_own_comment_findings_in_its_body`
  both depend on the standalone-comment path (the `else` branch) working correctly. Do not remove
  or merge this if/else.
- **The second `if`/`else` (the final `return`) IS dead in its `else` branch.** Since `column` is
  never `None`, `if column is not None: return max(...)` always fires, and
  `return min(candidates, key=statement_span)` never runs. This is the actual dead code, along with
  `statement_span` itself, which is called *only* from that unreachable line.

The fix: since `column` is never `None` in practice, make it a required parameter and simplify
away only the now-always-true conditions — not the branch logic that depends on the *other* half
of the compound condition:

```python
def statement_owner_for_line(source: SourceFile, line: int, column: int) -> ast.stmt | None:
    """Return the statement a comment or docstring line is attached to.

    Trailing comments (code precedes them on the line) resolve to their innermost enclosing
    statement; standalone comments resolve to whichever statement starts or ends on that line.
    """
    if source.lines[line - 1][:column].strip():
        candidates = [
            statement
            for statement in source.statements
            if statement.lineno <= line <= (statement.end_lineno or statement.lineno)
        ]
    else:
        candidates = [
            statement
            for statement in source.statements
            if statement.lineno == line or (statement.end_lineno or statement.lineno) == line
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda statement: (statement.lineno, statement.col_offset))
```

Then delete `statement_span` entirely — with the `return`'s dead branch gone, it has no remaining
caller anywhere in the codebase.

`comment_owner_for_line` (which calls `statement_owner_for_line(source, line,
source.lines[line - 1].index(comment))`) needs no changes — it already passes an explicit column.

Do not change `statement_key`, `candidate_for_statement`, `candidate_for_line`,
`docstring_owner_for_line`, `append_candidate`, or `CandidateBudgetExceeded`.

## Verify

- [ ] FR#2: `grep -n "statement_span" src/house_lint/analysis.py` returns no matches.
- [ ] AC#2: `statement_owner_for_line`'s signature has `column: int` (no default value); run
      `uv run pyright` (strict, `src/` only) and confirm it's clean.
- [ ] `uv run pytest -q` reports all tests passing — in particular
      `tests/unit/rules/test_llm_cruft.py::test_standalone_body_comment_has_no_owner` and
      `tests/unit/test_suppressions.py::test_header_ignore_does_not_own_comment_findings_in_its_body`
      must still pass, since they exercise the standalone-comment branch this task must preserve.
