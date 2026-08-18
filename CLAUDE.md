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

Rules are registered in two places in `src/house_lint/registry.py`: static metadata in `_RULES`
(a `MappingProxyType` of `RuleMetadata`), and a detector function in `_DETECTORS` that lazily
imports each `rules/<name>.py` module (avoids import cost for unused rules). A new rule needs
both. Detector signature: `(source, options, *, limit=None) -> list[CandidateFinding]`.

## Gotchas

- `HSL900` (suppression-pragma validation) can never be disabled or suppressed — it governs how
  every other rule's findings can be silenced (`ignore`, `ignore-next`, `ignore-file` pragmas).
- File discovery does **not** shell out to git or read nested `.gitignore` files — only the root
  `.gitignore` plus configured excludes. `--no-gitignore` disables just that root file.
- Default scan roots are `src`, `tests`, `scripts`, `tools`, `examples`, configurable via
  `[tool.house-lint] include`.

## Conventions

Conventional Commits are enforced by CI on PR titles (`feat`, `fix`, `refactor`, `docs`, `test`,
`chore`, `perf`, `ci`; scope optional). Releases are automated via release-please, driven by
commit type — do not hand-edit `CHANGELOG.md` or bump `version` in `pyproject.toml` manually.
