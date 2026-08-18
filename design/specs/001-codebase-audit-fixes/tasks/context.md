# Context: Codebase Audit Fixes

## Problem & Motivation

A codebase audit of house-lint surfaced 12 findings: a rule-registration duplication risk between
`registry.py` and `config.py` (two independently-hardcoded rule-ID lists that had to be kept in
sync by hand), confirmed dead code in `analysis.py`, an undertested candidate-budget safety valve,
duplicated logic, and several small consistency/drift issues. Two of those 12 findings — the
registration duplication and an unused `ownership_scope` metadata field — are resolved together by
one structural fix (FR#1), so the design doc's 11 functional requirements cover all 12 findings.
house-lint is a personal, semver-disciplined tool consumed across the author's personal projects
and two employers' repos, so silent correctness gaps matter more than the codebase's small size
might suggest.

## Key Decisions

1. Every task keeps `uv run pytest -q` green when it finishes — no task leaves the suite red for a
   later task to fix.
2. Where a task is a refactor with no dedicated test today (lazy_imports' budget check), the test
   is added *before* the implementation changes, pinning current behavior first (see T03 → T04).
3. Tasks are ordered so no two touch the same file in a way that causes rework — `cli.py`'s dedup
   (T07) lands before its extraction (T12); the rule-file style pass (T10) lands after the other
   rule-file edits (T04, T08) so it isn't immediately stale.
4. The registration duplication (former finding) is fixed structurally, not with a sync test — see
   FR#1 / T01: a new leaf module (`rule_catalog.py`) becomes the single source of truth, and
   `registry.py` enforces consistency at import time with a `RuntimeError`, not a test that could
   be silently deleted.
5. Where the design leaves an implementation detail open (e.g. exactly where the shared
   `_load_toml` helper lives), the task prompt says so explicitly — use judgment, but keep exactly
   one implementation.

## Constraints

- Do not change any rule's user-visible behavior (message text, rule IDs, finding locations) —
  this is a cleanup/hardening batch, not a feature change.
- Do not add new dependencies.
- Do not touch `design/specs/001-llm-lint-extraction/` — that's a pre-existing, unrelated spec
  directory in this repo; don't confuse it with this one.
- Do not add a `[tool.house-lint]` self-lint section to this project's own `pyproject.toml` — that
  is out of scope (FR#8 only prepares for it).
- Keep diffs minimal — this is a maintenance pass, not a rewrite. Don't restructure beyond what
  each finding specifically calls for.
