---
task_id: "T10"
title: "Standardize guard clause and __all__ across the 7 rule files"
status: "done"
depends_on: ["T04", "T08"]
implements: ["FR#9", "AC#9"]
---

## Target Files

- modify: `src/house_lint/rules/constants_position.py`
- modify: `src/house_lint/rules/exception_names.py`
- modify: `src/house_lint/rules/file_length.py`
- modify: `src/house_lint/rules/lazy_imports.py`
- modify: `src/house_lint/rules/llm_cruft.py`
- modify: `src/house_lint/rules/spec_tokens.py`
- modify: `src/house_lint/rules/type_checking_position.py`

## Prompt

Two small stylistic inconsistencies across the 7 rule files:

1. **Guard clause spelling.** `file_length.py`, `llm_cruft.py`, `spec_tokens.py` guard with
   `if source.error is not None: return []`. `constants_position.py`, `exception_names.py`,
   `lazy_imports.py`, `type_checking_position.py` additionally check `or source.tree is None` —
   confirm this yourself by reading each file, since the exact set of files can be easy to
   misremember; `exception_names.py` has the redundant form too, not just the other three. Per
   `source.py`'s invariants (confirmed during the audit — `source.tree` is only `None` when
   `source.error` is set), the extra check is always redundant. Standardize all 7 files to the
   shorter form: `if source.error is not None: return []`. Remove the redundant
   `or source.tree is None` from the 4 files that have it.

2. **`__all__` export.** `exception_names.py`, `file_length.py`, `spec_tokens.py` already have
   `__all__ = ["detect"]` at the bottom. `constants_position.py`, `lazy_imports.py`, `llm_cruft.py`,
   `type_checking_position.py` don't. Add `__all__ = ["detect"]` to the 4 files missing it,
   following the placement/style already used in the 3 files that have it.

Do not touch `lazy_imports.py`'s use of a class-based `ast.NodeVisitor` (`_LazyImportVisitor`) —
that's a reasonable technical choice for depth-tracking during import detection, not a defect, and
is out of scope for this style pass.

This task runs after T04 (lazy_imports refactor) and T08 (detect signature standardization) so it
touches each rule file's final shape rather than getting immediately stale.

## Verify

- [ ] FR#9: `grep -rn "or source.tree is None" src/house_lint/rules/*.py` returns no matches.
- [ ] AC#9: `grep -rl "__all__" src/house_lint/rules/*.py | wc -l` equals 7.
- [ ] `uv run pytest -q` reports all tests passing (pure refactor, no behavior change expected).
- [ ] `uv run ruff check .` is clean.
