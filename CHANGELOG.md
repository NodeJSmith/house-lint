# Changelog

All notable changes to `house-lint` are documented here.

## [0.3.0](https://github.com/NodeJSmith/house-lint/compare/v0.2.0...v0.3.0) (2026-10-30)

### Breaking Changes

- **Config discovery now stops at the nearest `.git` directory.** Previously, a nested repo with no config of its own inherited a recognized config from an ancestor directory outside the repo, which also moved the scan root up to that ancestor. A nested repo now uses its own root and the default rules. If you relied on inheriting, add a config file to the nested repo or pass `--config`. (#51)
- **Some suppression pragmas are now reported by HSL900.** An `ignore-file` or `ignore-next` placed between a decorator and its `def`/`class` is now misplaced; move it above the whole decorated statement. An `ignore[HSL103]` on a `try:` line is now an unused suppression; put it on each `except` line instead. (#48)

### Rules

- `HSL001` no longer flags coding cookies and emacs modelines (`# -*- coding: utf-8 -*-`). It now flags all-hash rows (`########`) and hash-flanked titles (`#### x ####`), so you may see new findings. (#48)
- `HSL003` now recognizes aliased guards (`import typing as t; if t.TYPE_CHECKING:` and `from typing import TYPE_CHECKING as TC`), so you may see new findings. (#48)

### Suppressions

- `ignore-next` can now suppress findings on standalone comment lines such as dividers. Previously the pragma was reported as unused, and only `ignore-file` worked. (#50)
- `ignore-next` above a decorated `def` or `class` now applies to it instead of being reported as misplaced. (#48)
- `ignore[HSL103]` on one `except` clause now silences only that handler, not every handler of the same `try`. (#48)

### Discovery

- Configured `exclude` patterns now follow git's semantics. The whitelist idiom `exclude = ["*", "!*/", "!*.py"]` no longer skips every subdirectory. (#48)

### Bug Fixes

- A form feed or U+2028 in a source file no longer shifts line numbers. This could abort an `HSL101` run, break valid `ignore-next` pragmas, and misread docstring lines. (#48)
- Zero-file guidance now names the actual `--config` file instead of always saying `pyproject.toml`. (#48)

## [0.2.0](https://github.com/NodeJSmith/house-lint/compare/v0.1.2...v0.2.0) (2026-08-23)

### Breaking Changes

- **The default scan now covers the entire project tree, not five hardcoded directories.** Previously `house-lint check` (with no `include` set) only scanned `src`, `tests`, `scripts`, `tools`, and `examples`. It now scans everything under the project root, filtered by `.gitignore` and an expanded built-in exclude list. If you relied on the old implicit scope, set `[tool.house-lint] include` to restore it. (#37)
- **`house-lint.toml` and `.house-lint.toml` are now recognized as config files**, checked ahead of `pyproject.toml`'s `[tool.house-lint]` table. A file with either of these names that was previously inert in your project is now read as configuration — review its contents before upgrading. (#37)

### Configuration

- Add `extend-select`/`extend-ignore` (CLI flags and TOML keys) to layer additional rules on top of your existing selection, instead of `--select` replacing it wholesale. (#14)
- Add `per-file-ignores` to silence specific rules for matching files (e.g. `HSL002` under `tests/**`) without disabling them project-wide. (#15)
- A zero-file scan now explains what to check (no config found, or which `include` list is in effect) instead of silently reporting nothing; `--fail-on-empty` now exits with an error on a zero-file scan instead of exiting 0. (#37)
- When more than one recognized config file exists at the winning directory, the shadowed file(s) are now named in the output. (#37)

### Discovery

- Nested `.gitignore` files are now honored, matching git's own precedence — a closer `.gitignore`'s negation can override a farther one's ignore. Previously only the root `.gitignore` was read. (#13)
- Fixed a range of gitignore-matching edge cases — directory-only negations, repeated `**` segments, symlinked ignore files, and patterns reached via explicit paths — to match real `git check-ignore` behavior. (#29)

### Rules

- `HSL101` now ships built-in spec token families, so it produces useful findings without any config. (#28)

### Performance Improvements

- Add per-file result caching (`--cache-dir`, `--no-cache`) so unchanged files skip re-analysis on the next run. (#16)
- The pre-commit hook now batches all changed files into a single `house-lint` invocation instead of spawning one process per file. (#12)

## [0.1.2](https://github.com/NodeJSmith/house-lint/compare/v0.1.1...v0.1.2) (2026-08-11)


### Documentation

* remove name references from CLI help, pyproject, and docs ([#6](https://github.com/NodeJSmith/house-lint/issues/6)) ([da6fd35](https://github.com/NodeJSmith/house-lint/commit/da6fd35a087241602b2c03bac5f6199754e6a3a4))

## [0.1.1](https://github.com/NodeJSmith/house-lint/compare/v0.1.0...v0.1.1) (2026-08-11)


### Documentation

* trim README — depersonalize, remove setup notes, merge non-goals ([#4](https://github.com/NodeJSmith/house-lint/issues/4)) ([57beef2](https://github.com/NodeJSmith/house-lint/commit/57beef2a3f21ef68f86e6f69f1ec5466fdd28d5f))

## [0.1.0] (2026-08-11)

Initial public release of Jessica's opinionated Python house-style linter.

- Hardens source discovery, result validation, and suppression handling for release use (#1).
- Adds the `house-lint check` and `house-lint rules` commands for Python 3.11+.
- Ships default rules `HSL001`–`HSL004`, opt-in rules `HSL101`–`HSL103`, and always-on suppression diagnostics `HSL900`.
- Adds strict root/configuration/path discovery, deterministic text and schema-versioned JSON output, and documented exit categories.
- Adds statement-aware `house-lint:` suppressions. Existing Hassette annotations such as `# lazy-import:`, `# constant-after-def:`, and `# file-size-exempt:` are not compatible; use the documented reasoned pragma grammar when adopting this package.
- Adds distributable pre-commit metadata that filters Python filenames before invoking the strict CLI.

### Compatibility

The command-line, TOML, JSON, rule-ID, and suppression surfaces are compatibility contracts. Before 1.0, changes may occur, but releases will document migration steps here.
