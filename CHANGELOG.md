# Changelog

All notable changes to `house-lint` are documented here.

## [0.2.1](https://github.com/NodeJSmith/house-lint/compare/v0.2.0...v0.2.1) (2026-09-05)


### Bug Fixes

* correct line model, exclude matching, and suppression scope ([#48](https://github.com/NodeJSmith/house-lint/issues/48)) ([ab2dbf0](https://github.com/NodeJSmith/house-lint/commit/ab2dbf0ee9a16d633522899f5f5dfa8ecf10d292))
* let ignore-next suppress standalone-comment findings ([#50](https://github.com/NodeJSmith/house-lint/issues/50)) ([e2885bd](https://github.com/NodeJSmith/house-lint/commit/e2885bdfa3d0e01ebbcb31b09d51da91695fe803))
* stop upward config discovery at the .git boundary ([#51](https://github.com/NodeJSmith/house-lint/issues/51)) ([9d3c8ee](https://github.com/NodeJSmith/house-lint/commit/9d3c8eefa1eca7b9e2fbda9ea84fbe2db5e08de6))

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
