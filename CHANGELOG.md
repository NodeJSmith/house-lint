# Changelog

All notable changes to `house-lint` are documented here.

## [0.6.0](https://github.com/NodeJSmith/house-lint/compare/v0.5.0...v0.6.0) (2026-11-12)


### ⚠ BREAKING CHANGES

* Stale-suppression reporting moves to a new opt-in rule, overlapping pragmas now suppress, and a CLI `--select` replaces the configured selection.
    #### What changed and what to do
    - HSL900 no longer reports unused, disabled-rule, or overlapping
    pragmas; the opt-in HSL901 does. Add `extend-select = ["HSL901"]` to
    `[tool.house-lint]` to keep them reported. JSON consumers filtering
    `rule_id == "HSL900"` for stale pragmas see them under HSL901, or not at
    all while it's off.
    - Overlapping pragmas now suppress their finding instead of failing
    closed, so a run that exited 1 on an overlap can exit 0, and the
    `conflicting suppression for X` message is gone. With HSL901 on, the
    extra pragmas are reported as `redundant suppression for X`.
    - A CLI `--select` replaces the config's `select`, `ignore`,
    `extend-select`, and `extend-ignore`. A command that relied on
    configured `extend-ignore` or `extend-select` under `--select` must pass
    `--ignore`, `--extend-ignore`, or `--extend-select` on the command line
    instead.
* HSL001, HSL101, HSL103, and HSL900 now report `column` and `end_column` as UTF-8 byte offsets, as the other rules already did. On lines with non-ASCII text, these values are larger than before in text and JSON output. A consumer that maps columns to characters must treat them as byte offsets into the line's UTF-8 encoding.
* JSON output moves to `schema_version` 3. Every finding now has a `related` list of the other places the same issue occurs (empty for single-file rules), and text output appends `(also at ...)` to a project-rule finding's line. A consumer that checks `schema_version == 2` must accept 3; otherwise the change is additive. `summary.suppressed_count` now also counts project-rule occurrences a pragma suppressed.

### Features

* add --base to report only findings a commit introduced ([#92](https://github.com/NodeJSmith/house-lint/issues/92)) ([1b2e025](https://github.com/NodeJSmith/house-lint/commit/1b2e02542bbbb7efbb568b5f0349382000003b7a))
* add HSL104 opt-in rule for code copied across a project ([#89](https://github.com/NodeJSmith/house-lint/issues/89)) ([a2172b1](https://github.com/NodeJSmith/house-lint/commit/a2172b19b5eb1832099cf307ab201e7f8e30b1b2))
* let pragmas suppress project-rule findings and list related locations ([#84](https://github.com/NodeJSmith/house-lint/issues/84)) ([87eea01](https://github.com/NodeJSmith/house-lint/commit/87eea01cd06693e7e1486093d331e127118d1be7))
* split stale suppression reports into opt-in HSL901 ([#93](https://github.com/NodeJSmith/house-lint/issues/93)) ([5741dc7](https://github.com/NodeJSmith/house-lint/commit/5741dc79de06a6b4b753ce391c5e3fb783d35552))


### Documentation

* drop TypeScript from house-lint's scope ([#88](https://github.com/NodeJSmith/house-lint/issues/88)) ([c6210f0](https://github.com/NodeJSmith/house-lint/commit/c6210f0ae02d1b3a542e3b7fdade86a299a66e1a))

## [0.5.0](https://github.com/NodeJSmith/house-lint/compare/v0.4.0...v0.5.0) (2026-11-03)

### Breaking Changes

house-lint now discovers only files in a language that some enabled rule handles. Every shipped rule handles Python, so a config that enables at least one rule scans the same files and gets the same findings as before. (#82)

- **`select = []` now scans nothing.** It is a zero-file scan, so `HSL900` no longer validates pragmas either. Enable at least one rule to keep pragma validation.
- **An explicit path that no enabled rule handles is now an error.** With no rule enabled, the pre-commit hook exits 3 on any commit that stages a `.py` file, so don't run the hook with such a config.
- **The zero-file summary now reads `empty scan: no files selected`.** Update anything that matches the old text.

### Configuration

- Invalid rule options are reported with new wording, for example ``rules.HSL102: Expected `int` >= 1 - at `$.max_lines` ``. The same configs are accepted and rejected as before. house-lint now depends on `msgspec`. (#79)

## [0.4.0](https://github.com/NodeJSmith/house-lint/compare/v0.3.0...v0.4.0) (2026-11-02)

### Breaking Changes

Inside a git repository, house-lint now gets its file list from `git ls-files` instead of applying ignore rules itself. This changes which files are scanned, along with several flags and output fields. (#71)

#### What gets scanned

- **Tracked files that match an ignore pattern are now scanned.** Git only ignores untracked files. Add the file to `exclude` to keep it out.
- **A tree that isn't a git repository no longer honours `.gitignore`.** Use `exclude` there.
- **A scan rooted below the repository root now honours ignore files above it and `.git/info/exclude`.** Files they hide are no longer scanned.
- **Your global git ignore file now applies.** An untracked file it hides is no longer scanned on that machine, so a local run can differ from CI.
- **Submodules and nested clones are no longer scanned unless named directly.** Pass the path on the command line or list it in `include` to scan one.
- **A gitignored file or directory that you name is now linted,** whether on the command line or in `include`. Built-in and configured excludes still apply, so use `exclude` to keep it out.

#### Flags, config, and output

- **`--no-gitignore` is renamed to `--no-git`.** It also turns off `.git/info/exclude` and global excludes. Update any scripts or hooks that pass it.
- **A git failure inside a repository now stops the run** with exit code 3 and a new `git-error` code. This covers a missing git binary, a `safe.directory` refusal, a `.git` entry git rejects, and any git warning on stderr. Pass `--no-git` to scan without git.
- **`!` patterns in `exclude` are now a config error.** This includes the whitelist idiom `exclude = ["*", "!*/", "!*.py"]`. Remove them, or narrow the broader pattern instead.
- **`files_skipped` is removed from the JSON output and the text summary, and the `check` output's `schema_version` is now 2.** The `rules` output stays at 1. Update any script that reads the key or checks the version.

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
