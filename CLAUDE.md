---
audience: personal tool, semver-disciplined
developers: solo
data-sensitivity: internal
---

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`house-lint` is an opinionated Python house-style linter (comments, imports, module layout,
project conventions) with a CLI built on `cyclopts`. It checks rules `HSL001`-`HSL004` (default),
`HSL101`-`HSL103` (opt-in), and `HSL900` (always-on, validates suppression pragmas). It is
explicitly not a general-purpose style framework, an AI-authorship detector, or a Ruff
replacement.

## Commands

Package manager is **uv** (build backend is `uv_build`, not hatchling/setuptools — do not add one).
There is no Makefile/justfile; everything runs through `uv run`.

- Test: `uv run pytest`
- Lint: `uv run ruff check .`
- Format: `uv run ruff format`
- Type check: `uv run pyright` — **strict mode**, scoped to `src/` only (tests are not type-checked)
- Build: `uv build`

CI (`.github/workflows/ci.yml`) runs `test` across Python 3.11-3.14 and `quality`
(ruff + pyright + build) on 3.11 only.

## Structure

Single package, `src/` layout: `src/house_lint/` with `reporters/` and `rules/` subpackages.
`tests/unit/` and `tests/integration/`; `tests/unit/rules/` mirrors `src/house_lint/rules/`.
`tests/conftest.py` provides `write_sample(content: str) -> Path`, which dedents and writes to
`tmp_path/sample.py` — use it instead of ad hoc file writes in rule tests.

Rule metadata (IDs, display info, enablement tier) lives in `src/house_lint/rule_catalog.py` — the
single source of truth (`RULES`, plus `is_known_rule`/`rule_ids`/`rule_metadata` accessors).
`src/house_lint/registry.py` owns only dispatch: `_DETECTORS` maps rule IDs to detector functions
that lazily import each `rules/<name>.py` module (avoids import cost for unused rules), and an
import-time check raises `RuntimeError` if `_DETECTORS` and `rule_catalog.ORDINARY_RULES` diverge.
A new rule needs an entry in both `rule_catalog.py` and `registry.py`'s `_DETECTORS`. Detector
signature: `(source, options, *, limit=None) -> list[CandidateFinding]`.

## Gotchas

- `HSL900` (suppression-pragma validation) can never be disabled or suppressed — it governs how
  every other rule's findings can be silenced (`ignore`, `ignore-next`, `ignore-file` pragmas).
- File discovery does **not** shell out to git. It reads the root `.gitignore` plus every nested
  `.gitignore` between the root and each file, reimplementing git's precedence on `pathspec`.
  `--no-gitignore` disables that at every level. Because it is a reimplementation, changes to
  `discovery.py`'s pattern handling belong in `tests/integration/test_gitignore_parity.py`, which
  differentially checks discovery against real `git check-ignore` — adding a case there costs one
  `Scenario` entry and needs no expected-value literal. Two divergences are known, both from
  `pathspec` deciding directory-only patterns from pattern text rather than from a real `is_dir`:
  one over-lints, one **under**-lints (hides findings). Do not assume the old "always errs toward
  over-linting" guarantee — it was false and has been removed. See `docs/configuration.md` and
  `design/research/2026-08-20-gitignore-style-exclusion-inclusion/`.
- An ignored directory is pruned rather than enumerated, so `files_skipped` counts one skip per
  pruned directory, not one per file inside it.
- Default scan roots are `src`, `tests`, `scripts`, `tools`, `examples`, configurable via
  `[tool.house-lint] include`.
- A scanned file is read **exactly once** per scan, by `SourceFile.load()`. The cache key is
  derived from that same buffer (`SourceFile.content_bytes` → `hash_source_content`), which is
  what stops an entry from ever describing content that was not scanned under that key. Adding a
  second read of a scanned path reopens that window;
  `test_each_scanned_file_is_read_exactly_once` is what catches it.
- The result cache is namespaced by `<version>-<hash of house-lint's own sources>`, so editing
  rule code invalidates it without a version bump. house-lint writes a self-ignoring `.gitignore`
  into its own default cache base only — never into a user-supplied `--cache-dir`.

## Conventions

Conventional Commits are enforced by CI on PR titles (`feat`, `fix`, `refactor`, `docs`, `test`,
`chore`, `perf`, `ci`; scope optional). Releases are automated via release-please, driven by
commit type — do not hand-edit `CHANGELOG.md` or bump `version` in `pyproject.toml` manually.
