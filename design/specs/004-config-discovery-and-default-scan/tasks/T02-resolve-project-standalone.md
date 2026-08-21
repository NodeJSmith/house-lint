---
task_id: "T02"
title: "update resolve_project() to discover standalone config files"
status: "done"
depends_on: ["T01"]
implements: ["FR#1", "FR#2", "FR#5", "FR#11"]
---

## Target Files

- modify: `src/house_lint/discovery.py`
- modify: `src/house_lint/cli.py`
- modify: `tests/unit/test_discovery.py`

## Prompt

Update `resolve_project()` in `discovery.py` to discover standalone config files alongside `pyproject.toml`.

### 1. Update the upward walk (lines 840-851)

Currently the walk only checks `pyproject.toml` for `[tool.house-lint]`. Change to check at each candidate directory:

1. `house-lint.toml` — if exists and `get_standalone_table(load_toml(path))` is not None, return `ProjectResolution(candidate, path)`
2. `.house-lint.toml` — same check
3. `pyproject.toml` — existing check with `get_house_lint_table()`

Import `get_standalone_table` and `STANDALONE_CONFIG_NAMES` from `config.py`. (`is_standalone_config` is only needed in `cli.py`, not here — do not import it into `discovery.py` to avoid an unused-import lint failure.)

The `found_marker` fallback logic (`.git` or any `pyproject.toml`) stays the same.

### 2. Update the `--root` without `--config` path (lines 859-864)

Currently checks only `<root>/pyproject.toml`. Change to check `house-lint.toml` → `.house-lint.toml` → `pyproject.toml` in the root directory, returning the first match.

### 3. Update CLI `load_config` call

In `cli.py` (around line 402-416), the call to `load_config()` needs to pass `standalone=True` when `resolution.config` is a standalone config file. Use `is_standalone_config(resolution.config)` to determine this.

### 4. Update `--config` handling

When `--config` is passed explicitly (lines 852-858 in `resolve_project`), no changes needed — `resolve_project` already returns whatever config path was given. The CLI layer (step 3) handles the standalone detection.

### 4.5. Add debug-level config shadowing diagnostic (FR#11)

When `--debug` is active and more than one recognized config source exists at the winning directory level during the upward walk (e.g., both `house-lint.toml` and `pyproject.toml` with `[tool.house-lint]`), emit a debug-level message naming which file was used and which were shadowed. The `--debug` plumbing already exists in `cli.py`. `resolve_project()` can return this info as part of `ProjectResolution` (add an optional `shadowed` field), or the debug message can be emitted directly inside `resolve_project()` if a debug callback/flag is threaded through.

### 5. Tests

In `tests/unit/test_discovery.py`, add tests for:
- Upward walk finds `house-lint.toml` with `[house-lint]` table
- Upward walk finds `.house-lint.toml` with `[house-lint]` table
- `house-lint.toml` takes precedence over `pyproject.toml` with `[tool.house-lint]` in same directory
- `house-lint.toml` without `[house-lint]` table is skipped (falls through to pyproject)
- `--root` without `--config` finds standalone config in root directory
- `--config path/to/house-lint.toml` resolves correctly

## Verify

- [ ] FR#1: `resolve_project()` discovers standalone config files during upward walk
- [ ] FR#2: `--root` without `--config` checks for standalone config files in root
- [ ] FR#5: `--config` with a standalone config file path works correctly
- [ ] AC#1: Running in a directory with only `house-lint.toml` discovers and uses it
- [ ] AC#2: Running in a directory with only `.house-lint.toml` discovers and uses it
- [ ] AC#3: `house-lint.toml` takes precedence over `pyproject.toml`
- [ ] AC#4: `--config path/to/house-lint.toml` loads standalone format
- [ ] AC#9: `--root` checks for standalone config files
- [ ] FR#11: `--debug` shows which config file was used when multiple exist at the same level
- [ ] AC#11: Debug output names used and shadowed config files
