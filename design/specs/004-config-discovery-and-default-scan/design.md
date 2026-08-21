# Design: Standalone Config Discovery & Root-Based Default Scan

**Date:** 2026-08-21
**Status:** draft
**Mode:** sketch

## Problem

house-lint has two config/discovery limitations: (1) it only discovers `pyproject.toml` for config, requiring `--config` for standalone config files, and (2) its default include list (`src`, `tests`, `scripts`, `tools`, `examples`) silently produces zero results for any project that doesn't match that layout — monorepos, flat layouts, or non-standard directory structures. Prior art research found that no major Python linter uses a hardcoded directory-name list as default scan roots; the dominant pattern (Ruff, Black) is scan-from-root filtered by gitignore + excludes.

## Goals

- Discover standalone `house-lint.toml` and `.house-lint.toml` config files during project resolution, matching Ruff's pattern.
- Replace the hardcoded default include list with scan-from-root behavior (the project root directory itself), filtered by the existing gitignore + builtin excludes infrastructure.
- Emit a diagnostic warning when a scan discovers zero Python files, making the silent-zero-results failure mode visible.
- Preserve backward compatibility: explicit `include` config continues to work as before; `--config` overrides still take precedence.

## Non-Goals

- Nested/hierarchical config inheritance (Ruff's `extend` mechanism) — out of scope for this change.
- Per-file config resolution for monorepos — Pyright declined this; we follow suit.
- Changing `exclude` semantics or the gitignore reimplementation.

## Compatibility Notes

- **New config filenames claim a previously-inert namespace.** Before this change, `house-lint.toml` and `.house-lint.toml` anywhere in the tree were inert files house-lint never inspected. After this change, `resolve_project()`'s upward walk treats them as authoritative config ahead of `pyproject.toml`. A project with an incidentally-named `house-lint.toml` (fixture, copy from another tool) goes from "ignored" to "parsed as config." This matches Ruff's `ruff.toml`/`.ruff.toml` precedent and is an intentional trade-off.
- **Default scan scope expansion is a breaking change.** Changing `DEFAULT_INCLUDE` from the five-directory list to `(".",)` expands the scan surface for every project without explicit `include` config. This must be flagged as a breaking change (`feat!`) in the commit/release. house-lint's own repo has no Python files outside `src/` and `tests/`, so no explicit `include` is needed in its own `pyproject.toml` — the expanded scan discovers the same files.

## Functional Requirements

- **FR#1** `resolve_project()` checks for `house-lint.toml` → `.house-lint.toml` → `pyproject.toml` (with `[tool.house-lint]`) in each candidate directory during the upward walk, returning the first match.
- **FR#2** When `--root` is given without `--config`, the root directory is checked for `house-lint.toml` → `.house-lint.toml` → `pyproject.toml` (with `[tool.house-lint]`), in that order.
- **FR#3** Standalone config files (`house-lint.toml`, `.house-lint.toml`) use `[house-lint]` as the top-level table (not `[tool.house-lint]`), since they are not embedded in a multi-tool manifest.
- **FR#4** `load_config()` accepts both standalone (`[house-lint]`) and pyproject-embedded (`[tool.house-lint]`) formats, determined by which file type was resolved.
- **FR#5** `--config` accepts standalone config files directly — the file is loaded with the appropriate table lookup based on filename.
- **FR#6** When no `include` is configured, `discover_files()` defaults to scanning from the project root directory (`.`) instead of the hardcoded directory list.
- **FR#7** When `include` is explicitly configured (in any config file format), it behaves exactly as today — the configured list replaces the default.
- **FR#8** When a scan discovers zero Python files and no errors occurred, the reporters emit a context-aware diagnostic indicating no files were found, with guidance tailored to the resolved config format. This fires for both default and explicit `include` — a typo'd explicit `include` is the most common real trigger.
- **FR#9** The context-aware guidance portion of the zero-file diagnostic is suppressed when `include = []` is explicitly configured (intentional empty scan) or when explicit paths are given on the CLI. The base "empty scan: no Python files selected" message still appears (it is the existing, pinned behavior). These are the only two suppression conditions.
- **FR#10** `BUILTIN_EXCLUDES` is expanded to match Ruff's default exclude list plus house-lint's existing extras, as a prerequisite for scan-from-root safety.
- **FR#11** When `--debug` is active and more than one recognized config source exists at the winning directory level, a debug-level message names which file was used and which were shadowed.

## Acceptance Criteria

- **AC#1** Running `house-lint` in a directory containing only `house-lint.toml` with `[house-lint]` discovers and uses that config. (FR#1, FR#3)
- **AC#2** Running `house-lint` in a directory containing only `.house-lint.toml` discovers and uses that config. (FR#1)
- **AC#3** When both `house-lint.toml` and `pyproject.toml` with `[tool.house-lint]` exist, `house-lint.toml` takes precedence. (FR#1)
- **AC#4** `--config path/to/house-lint.toml` loads the standalone format correctly. (FR#5)
- **AC#5** Running `house-lint` in a project with no `src/`/`tests/` dirs but Python files at the root or under non-standard dirs (e.g., `packages/`) discovers those files. (FR#6)
- **AC#6** Running `house-lint` in an empty project (no Python files at all) prints a context-aware diagnostic message in the reporter output (stdout for text, included in JSON output). (FR#8)
- **AC#7** Running `house-lint` with `include = []` in config produces the base "empty scan" message but without config guidance (intentional empty scan). (FR#9)
- **AC#8** Existing tests continue to pass — backward compatibility for projects with explicit `include` config. (FR#7)
- **AC#9** `--root /path --config` not given checks for standalone config files in the root dir before falling back to no-config. (FR#2)
- **AC#10** `BUILTIN_EXCLUDES` contains all 22 entries (Ruff's 20 + `__pycache__/` + `site-packages/`). (FR#10)
- **AC#11** Running with `--debug` in a directory containing both `house-lint.toml` and `pyproject.toml` with `[tool.house-lint]` shows which file was used and which was shadowed. (FR#11)

## Approach

### Config file discovery (FR#1–FR#5)

Modify `resolve_project()` in `discovery.py`. The upward walk currently checks only `pyproject.toml` for `[tool.house-lint]`. Change to check `house-lint.toml` → `.house-lint.toml` → `pyproject.toml` at each directory level:

- `house-lint.toml` / `.house-lint.toml`: if the file exists and contains a `[house-lint]` table, use it.
- `pyproject.toml`: existing behavior — check for `[tool.house-lint]`.

Add a `get_standalone_table()` function in `config.py` that extracts the `[house-lint]` table from a standalone file (parallel to `get_house_lint_table()` for pyproject). `load_config()` gains a `standalone: bool` parameter (or the caller passes the already-extracted table) so it knows which table layout to expect.

For `--config`, detect standalone format by filename: if the filename is `house-lint.toml` or `.house-lint.toml`, use standalone table lookup; otherwise use pyproject-style. This matches Ruff's behavior.

The `--root` without `--config` path (lines 859-864) gets the same three-file check.

When `--debug` is active and more than one recognized config source exists at the winning directory level (e.g., both `house-lint.toml` and `pyproject.toml` with `[tool.house-lint]`), emit a debug-level message naming which file was used and which were shadowed. The `--debug` plumbing already exists in `cli.py`.

### Default scan root (FR#6–FR#7)

Change `DEFAULT_INCLUDE` from `("src", "tests", "scripts", "tools", "examples")` to `(".",)`. This means `discover_files()` with default include will walk from the project root, filtered by builtin excludes and gitignore rules — infrastructure that already exists and works.

As a prerequisite, expand `BUILTIN_EXCLUDES` to match Ruff's default exclude list plus house-lint's existing extras (`__pycache__/`, `site-packages/`). The full list (22 entries): `.bzr/`, `.direnv/`, `.eggs/`, `.git/`, `.git-rewrite/`, `.hg/`, `.mypy_cache/`, `.nox/`, `.pants.d/`, `.pytype/`, `.ruff_cache/`, `.svn/`, `.tox/`, `.venv/`, `__pycache__/`, `__pypackages__/`, `_build/`, `buck-out/`, `dist/`, `node_modules/`, `site-packages/`, `venv/`. This is load-bearing for the scan-from-root change — without entries like `venv/`, `.tox/`, and `.mypy_cache/`, the expanded default would scan thousands of vendored `.py` files.

The `_validate_include()` function already accepts `"."` — `Path(".").is_absolute()` is `False`, `".."` is not in its parts, it's non-empty, and it contains no glob characters. The single-dot entry means "the root directory itself," which `discover_files()` already handles when it builds `requested = tuple(root / item for item in include)` — `root / "."` resolves to `root`. Add a test to confirm this.

Explicit `include` config replaces the default wholesale (existing behavior, no change needed).

### Zero-file diagnostic (FR#8–FR#9)

**Do not add a separate stderr warning.** The existing `render_text()` in `reporters/text.py` already appends `"empty scan: no Python files selected"` to stdout when `files_scanned == 0` with no findings or errors (pinned by `test_text_reporter_makes_clean_empty_scans_explicit`). Extend this existing mechanism rather than creating a second, independent one:

1. Make the existing text-mode message context-aware: append guidance referencing the specific config format relevant to `resolution.config`:
   - No config file found: suggest creating a config or passing explicit paths (`house-lint <path>`)
   - `pyproject.toml` found: reference `[tool.house-lint]` include
   - Standalone config found: reference `[house-lint]` include
2. Add an equivalent signal in `render_json()` — the JSON reporter currently has no zero-file message at all.
3. Suppress the diagnostic only when `include` is explicitly empty (`include = []`) or explicit CLI paths were given. A typo'd explicit `include` (e.g., `include = ["test"]` when the directory is `tests/`) should still trigger the warning — this is the most common real trigger.

This requires threading `include_is_default` and `resolution.config` (or a derived config-format indicator) through to the reporters. The `ScanResult` type may need a new field, or the reporters can accept additional context parameters.

This needs a way to distinguish "default include was used" from "user configured include = []". Use presence-based detection: check whether the TOML table contained an `include` key at parse time (via `"include" not in house`), independent of its value. Add an `include_is_default: bool = True` field to `LintConfig` **as the last field** — `LintConfig` is a positional (non-`kw_only`) dataclass with a positional construction site at `config.py:493`, so the new field must be appended last and passed as a keyword argument at that call site to avoid a silent positional-argument shift.

### Documentation

Update `docs/configuration.md`:
- Add standalone config file formats and discovery order.
- Update the default include description from the directory list to root-based scanning.
- Document the zero-file diagnostic.

## Smoke Test

In a temporary directory with no `src/` or `tests/` but a Python file at `lib/app.py`:

```bash
# Should discover lib/app.py (scans from root)
house-lint

# Should warn (no Python files at all)
mkdir empty && cd empty && house-lint
```

In a directory with `house-lint.toml`:

```toml
[house-lint]
select = ["HSL001"]
```

```bash
# Should use house-lint.toml config
house-lint
```

## Changed Files

- modify: `src/house_lint/config.py` — add `get_standalone_table()`, change `DEFAULT_INCLUDE` to `(".",)`, add `include_is_default` to `LintConfig`, update `_validate_include()` to accept `"."`, update `load_config()` for standalone format
- modify: `src/house_lint/discovery.py` — update `resolve_project()` to check standalone config files in the walk; update the `--root` without `--config` path similarly; expand `BUILTIN_EXCLUDES` to 22 entries
- modify: `src/house_lint/cli.py` — pass `standalone=True` to `load_config()` for standalone config files; thread config context to reporters
- modify: `src/house_lint/reporters/text.py` — make existing zero-file message context-aware with config-format guidance
- modify: `src/house_lint/reporters/json.py` — add zero-file signal to JSON output
- modify: `tests/unit/test_discovery.py` — new tests for standalone config discovery, update tests for default include change, discovery-order parity test
- modify: `tests/unit/test_config.py` — tests for standalone table loading, `include_is_default` flag
- modify: `tests/integration/test_reporters.py` — update zero-file diagnostic tests
- modify: `docs/configuration.md` — document standalone config files, new default scan behavior, expanded excludes, zero-file diagnostic
