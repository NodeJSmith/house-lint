# Design: Codebase Audit Fixes

**Date:** 2026-08-18
**Status:** archived
**Mode:** sketch

## Problem

A codebase audit of house-lint (2,677 src lines, 165 tests, solo dev, 8 days old) surfaced 12
findings ranging from a rule-registration duplication risk to confirmed dead code, an undertested
safety valve, and small duplication/drift. None are urgent on their own, but left alone they
compound: the registration duplication in particular risks a future rule silently never running,
which matters because house-lint ships to the author's personal projects and two employers' repos.
This design covers all 12 findings via 11 functional requirements — FR#1 resolves two findings
(the registration duplication and an unused `ownership_scope` field) with one structural fix.

## Goals

- Eliminate the rule-registration duplication structurally (single source of truth + an import-time
  check), not just test around it.
- Close the undertested candidate-budget safety path with real per-rule tests.
- Delete confirmed-dead code and duplicated logic rather than documenting around it.
- Land everything as a sequence of small, independently-verifiable commits — the full 165+ test
  suite stays green after every task.

## Functional Requirements

- **FR#1** A new leaf module, `src/house_lint/rule_catalog.py`, becomes the single source of truth
  for rule IDs, display metadata, and enablement tier (`RuleMetadata`, `RULES`, plus the
  `is_known_rule`/`rule_ids`/`rule_metadata` accessors, moved from `registry.py`). `config.py`'s
  `DEFAULT_SELECT` and `ORDINARY_RULES` are *derived* from `RULES` instead of independently
  hardcoded. `registry.py` keeps only dispatch (`_DETECTORS`, `detect_candidates`) and checks
  `_DETECTORS` against the catalog at import time — not just in a test — so a rule added to the
  catalog without a matching dispatcher fails immediately (`RuntimeError` on import, not a stripped
  `assert`) rather than silently misbehaving. This also drops the unused `ownership_scope` field,
  since the metadata dataclass is being rebuilt from scratch anyway.
- **FR#2** `analysis.py`'s unreachable `statement_owner_for_line` `column=None` branch and the
  `statement_span` helper it exclusively calls are removed; `column` becomes a required parameter.
- **FR#3** `lazy_imports.py`'s `_LazyImportVisitor._append` routes its budget check through
  `analysis.append_candidate` instead of reimplementing it.
- **FR#4** Each of the 7 rule detectors has a test asserting `CandidateBudgetExceeded` is raised
  when its own `limit=` is exceeded (currently only `llm_cruft` has one).
- **FR#5** TOML-loading (`tomllib.load` + error mapping to `ConfigError`) is implemented once and
  reused by `config.py` and `discovery.py`'s two call sites, instead of three near-duplicate copies.
- **FR#6 (superseded post-archival — see KI-001 in `known-issues.md`)** Originally: `cli.py`'s
  `check()` no longer pre-computes its own weaker copy of `resolve_project`'s root/config
  resolution just to have something to show on a `ConfigError`. This was later reversed: the
  pre-computation is back, now canonicalized with `.resolve()` and moved inside the `try:` block,
  as a best-effort fallback for error reporting when `resolve_project()` itself raises before
  returning.
- **FR#7** All 7 rule `detect()` functions accept `(source, options, *, limit=None)`, matching the
  documented `Detector` Protocol, even where a rule doesn't use `options`.
- **FR#8** `registry.py`'s 7 lazy `from .rules import ...` dispatch imports carry an inline comment
  explaining the lazy-loading rationale and an `HSL002` suppression pragma, so self-linting this
  repo later doesn't produce 7 unexplained findings.
- **FR#9** All 7 rule files use one consistent early-return guard (`source.error is not None`,
  dropping the redundant `or source.tree is None`) and a consistent `__all__ = ["detect"]` export.
- **FR#10** `test_reporters.py` asserts the empty-scan case for `render_json` (currently only
  `render_text` is asserted) and includes one non-ASCII round-trip test.
- **FR#11** `cli.py`'s scan-orchestration functions (`_scan_file`, `_load_source`,
  `_scan_ready_source`, `_recover_candidate_budget`, roughly lines 120-242) move to a new
  `src/house_lint/scanner.py` module; `cli.py` keeps Cyclopts declarations and the
  `ScanResult`-to-exit-code conversion.

## Acceptance Criteria

- **AC#1** `src/house_lint/rule_catalog.py` exists and is the only place `DEFAULT_SELECT`,
  `ORDINARY_RULES`, and rule metadata are literally spelled out; `config.py` imports
  `DEFAULT_SELECT`/`ORDINARY_RULES` rather than defining them; `registry.py` raises `RuntimeError`
  at import time if `_DETECTORS` and the catalog's ordinary-rule set diverge (verify by temporarily
  deleting one `_hslNNN` entry from `_DETECTORS` locally and confirming `import house_lint.registry`
  fails — then restore it); `grep -rn "ownership_scope" src/ tests/` has no matches. FR#1.
- **AC#2** `grep -rn "statement_span\b" src/house_lint/` has no matches; `analysis.py`'s
  `statement_owner_for_line` signature has no default for `column`; full suite passes. FR#2.
- **AC#3** `lazy_imports.py` no longer imports `CandidateBudgetExceeded` directly (it calls
  `append_candidate`, which raises it); the existing lazy-imports test plus its new budget test
  (AC#4) both pass. FR#3.
- **AC#4** `grep -rl "CandidateBudgetExceeded" tests/unit/rules/*.py | wc -l` equals 7 — *unless*
  `file_length.py` (HSL102) is structurally incapable of producing a budget-exceeded case (it may
  only ever emit 0 or 1 finding per scan; T05 determines this by reading the source), in which case
  6 plus a one-line comment in `test_file_length.py` documenting why satisfies FR#4. FR#4.
- **AC#5** `grep -c "tomllib.load" src/house_lint/config.py src/house_lint/discovery.py` shows the
  call collapsed into one shared helper (used from both files); full suite passes. FR#5.
- **AC#6 (superseded post-archival — see FR#6 note above)** Originally: `cli.py`'s `check()` no
  longer contains the `resolved_config = config.expanduser()...` pre-computation block;
  `tests/integration/test_cli.py`'s config-error tests still pass unchanged in behavior. FR#6.
  This is no longer true — the block was reinstated rather than removed.
- **AC#7** Reading each of the 7 rule files' `detect()` signature (some span multiple lines, so a
  single-line grep is not sufficient on its own) confirms `options` is present as the second
  positional parameter. FR#7.
- **AC#8** `grep -c "house-lint: ignore-next\[HSL002\]" src/house_lint/registry.py` equals 7. FR#8.
- **AC#9** `grep -rn "__all__" src/house_lint/rules/*.py | wc -l` equals 7; all 7 files use the
  same guard-clause spelling. FR#9.
- **AC#10** `tests/integration/test_reporters.py` contains a `render_json` empty-scan assertion and
  a non-ASCII test; both pass. FR#10.
- **AC#11** `src/house_lint/scanner.py` exists and contains the moved functions; `cli.py`'s line
  count drops by roughly the moved amount; `uv run pyright` (strict, `src/` only) passes. FR#11.
- **AC#12** `uv run pytest -q` reports all tests passing (165 plus the ~7 added by FR#4/FR#10) with
  zero failures; `uv run ruff check .` and `uv run pyright` are both clean. Verifies the whole
  batch stayed green throughout.

## Approach

This is a maintenance batch, not a feature — the approach is sequencing, not architecture. Order
tasks so no two touch the same file in a way that would make later tasks redo earlier work, and so
refactors are pinned by a test before the implementation changes (per this repo's own
`refactoring-discipline` convention: capture behavior, then move structure):

1. **T01** (FR#1) — the structural fix: extract `rule_catalog.py`, derive `config.py`'s lists from
   it, add the import-time dispatch check to `registry.py`, drop `ownership_scope`. Do first — it's
   the one finding the user specifically asked to solve structurally rather than patch.
2. **T02** (FR#2) — `analysis.py` dead-code removal, independent of everything else.
3. **T03** (FR#4, lazy_imports only) — add the budget-cutoff test to
   `tests/unit/rules/test_lazy_imports.py` **before** touching the implementation, pinning current
   behavior. Follow the exact pattern in `tests/unit/rules/test_llm_cruft.py:124-128`
   (`test_limits_materialized_candidates_when_requested`), adapted to `lazy_imports`' need for a
   Python file whose function bodies contain >N import statements.
4. **T04** (FR#3) — depends on T03's pin. Refactor `_LazyImportVisitor._append` to call
   `analysis.append_candidate(self.findings, _candidate(self.source, node), self.source,
   self.limit)` instead of hand-rolling the check; drop the now-unused `CandidateBudgetExceeded`
   import from `lazy_imports.py`. T03's test must still pass unchanged.
5. **T05** (FR#4, remaining 5 rules) — add one `CandidateBudgetExceeded` cutoff test per rule to
   `constants_position`, `exception_names`, `file_length`, `spec_tokens`, `type_checking_position`
   test files, mirroring `test_llm_cruft.py`'s pattern. Pure test-gap-filling, no implementation
   risk, independent of every other task.
6. **T06** (FR#5) — extract a shared TOML-loading helper (e.g. `load_toml(path: Path) -> dict[str,
   Any]` raising `ConfigError`) used by `config.py:277-281` and both call sites in
   `discovery.py:322-328` and `discovery.py:342-348`. Where the file lives (config.py, since it
   already owns `ConfigError`, with discovery.py importing it, or a new small shared module) is an
   implementation choice for the executor — either is fine as long as there's exactly one
   implementation.
7. **T07** (FR#6) — simplify `cli.py`'s `check()` (lines 322-333) so the `ConfigError` handler no
   longer needs a hand-computed fallback. Simplest correct approach: only pass the raw, unresolved
   `root`/`config` CLI arguments to `_result_for_config_error` on failure (no re-derivation of
   `resolve_project`'s logic) — `_result_for_config_error`'s signature already accepts optional
   `root`/`config`, so this is a deletion, not a rebuild.
8. **T08** (FR#7) — standardize `detect()` signatures across the 4 rules missing `options`
   (`llm_cruft`, `lazy_imports`, `type_checking_position`, `constants_position`) to accept
   `options: object` (or a typed options class where one exists) even if unused, and update
   `registry.py`'s corresponding `_hslNNN` wrappers to pass it through instead of dropping it.
9. **T09** (FR#8) — add lazy-import rationale comments and `HSL002` suppression pragmas to
   `registry.py`'s 7 `_hslNNN` functions. Pragma syntax (from `suppressions.py:24`):
   `# house-lint: <ignore|ignore-next|ignore-file>[<RULE_ID>] - <reason>`. Use `ignore-next` on its
   own line above each `from .rules import ...` — a trailing same-line `ignore` comment with this
   rationale text pushes every one of the 7 real import lines past the project's 100-character
   `ruff` limit, so `ignore-next` is the only form that fits without a case-by-case wrap decision.
10. **T10** (FR#9) — standardize the `source.error is not None` guard (drop the redundant
    `or source.tree is None`, present in `constants_position.py`, `exception_names.py`,
    `lazy_imports.py`, `type_checking_position.py`) and add `__all__ = ["detect"]` to the 4 files
    missing it. Do this after T04/T08 touch the rule files, to avoid rework.
11. **T11** (FR#10) — add the missing `render_json` empty-scan assertion (mirror
    `test_text_reporter_makes_clean_empty_scans_explicit`, `test_reporters.py:57-60`) and one
    non-ASCII test (e.g. a `Finding` whose `message` contains an accented character, asserting
    `render_json` escapes it per `json.dumps` defaults and `render_text` prints it literally).
12. **T12** (FR#11) — depends on T07 (both touch `cli.py`; land the dedup first so the extraction
    moves already-clean code). Move `_scan_file`, `_load_source`, `_scan_ready_source`,
    `_recover_candidate_budget` (and any private helpers they alone use) to
    `src/house_lint/scanner.py`; `cli.py` imports from it. No behavior change — this is a pure
    move, verified by the existing `tests/integration/test_cli.py` suite passing unchanged.

Each task keeps `uv run pytest -q` green before moving to the next — this is the proof chain a
reviewer can walk.

## Changed Files

- `src/house_lint/rule_catalog.py` — create (FR#1: `RuleMetadata`, `RULES`, accessors)
- `src/house_lint/registry.py` — modify (FR#1: drop local metadata, add import-time dispatch
  check; FR#7 wrappers; FR#8)
- `src/house_lint/config.py` — modify (FR#1: derive `DEFAULT_SELECT`/`ORDINARY_RULES`; FR#5)
- `src/house_lint/cli.py` — modify (FR#1: update imports to `rule_catalog`; FR#6; FR#11)
- `src/house_lint/suppressions.py` — modify (FR#1: update `is_known_rule` import to `rule_catalog`)
- `src/house_lint/analysis.py` — modify (FR#2: remove dead branch + `statement_span`)
- `src/house_lint/rules/lazy_imports.py` — modify (FR#3, FR#7, FR#9)
- `src/house_lint/rules/llm_cruft.py` — modify (FR#7, FR#9)
- `src/house_lint/rules/type_checking_position.py` — modify (FR#7, FR#9)
- `src/house_lint/rules/constants_position.py` — modify (FR#7, FR#9)
- `src/house_lint/rules/exception_names.py` — modify (FR#9, if guard/`__all__` inconsistent)
- `src/house_lint/rules/file_length.py` — modify (FR#9, if guard/`__all__` inconsistent)
- `src/house_lint/rules/spec_tokens.py` — modify (FR#9, if guard/`__all__` inconsistent)
- `src/house_lint/discovery.py` — modify (FR#5)
- `src/house_lint/scanner.py` — create (FR#11)
- `tests/unit/rules/test_llm_cruft.py` — modify (FR#7: add `options` argument to 9 call sites)
- `tests/unit/test_rule_catalog.py` — create (FR#1: metadata/derivation assertions)
- `tests/unit/test_registry.py` — modify (FR#1: keep dispatch-focused assertions, update imports;
  add the import-time-check regression test)
- `tests/unit/rules/test_lazy_imports.py` — modify (FR#3 pin test)
- `tests/unit/rules/test_constants_position.py` — modify (FR#4)
- `tests/unit/rules/test_exception_names.py` — modify (FR#4)
- `tests/unit/rules/test_file_length.py` — modify (FR#4)
- `tests/unit/rules/test_spec_tokens.py` — modify (FR#4)
- `tests/unit/rules/test_type_checking_position.py` — modify (FR#4)
- `tests/integration/test_reporters.py` — modify (FR#10)
- `tests/integration/test_cli.py` — modify (T07/T12: monkeypatch targets retargeted to
  `scanner.MAX_CANDIDATES_PER_FILE`/`scanner.detect_candidates`/`scanner.SourceFile`, plus a new
  auto-discovery regression test from T07's fixer pass)
