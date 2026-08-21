---
task_id: "T03"
title: "change default include to scan from project root"
status: "planned"
depends_on: ["T01", "T06"]
implements: ["FR#6", "FR#7"]
---

## Target Files

- modify: `src/house_lint/config.py`
- modify: `tests/unit/test_discovery.py`
- modify: `tests/unit/test_config.py`

## Prompt

Change the default scan behavior from a hardcoded directory list to scanning from the project root.

### 1. Change `DEFAULT_INCLUDE` in `config.py`

Change from:
```python
DEFAULT_INCLUDE = ("src", "tests", "scripts", "tools", "examples")
```
To:
```python
DEFAULT_INCLUDE = (".",)
```

### 2. Confirm `_validate_include()` accepts `"."` in `config.py`

`"."` already passes the existing validation: `Path(".").is_absolute()` is `False`, `".."` is not in `Path(".").parts`, `"."` is not empty, and `"."` contains no glob characters. No code change needed — add a test to confirm this invariant holds.

### 3. Add `include_is_default` to `LintConfig`

Add a field `include_is_default: bool = True` to `LintConfig` **as the last field** in the dataclass. `LintConfig` is `@dataclass(frozen=True)` without `kw_only=True`, and it is constructed positionally at `config.py:493` (`return LintConfig(include, exclude, enabled, *options, per_file_ignores)`). Adding a field anywhere except last would silently shift positional arguments. The new field must be passed as a **keyword argument** at that call site to avoid the shift:

```python
return LintConfig(include, exclude, enabled, *options, per_file_ignores, include_is_default=include_is_default)
```

Use presence-based detection — set it to `False` in `load_config()` when the TOML table contained an explicit `include` key:

```python
include_is_default = "include" not in house
```

Update `default_config()` to set `include_is_default=True`.

### 4. Update tests

In `tests/unit/test_discovery.py`:
- Update `test_no_path_scan_uses_all_documented_default_include_roots` — this test asserts all five named directories are used as default include roots. It needs to change to assert that files anywhere under the project root are discovered (not just under `src/`, `tests/`, etc.), and that files in excluded directories (`.venv/`, `.git/`, etc.) are still excluded.
- Update `test_missing_implicit_include_root_is_an_empty_scan` — with `include=(".",)`, a missing root is impossible (root always exists). This test should be replaced with one that confirms an empty root (no Python files) produces an empty scan.
- Verify `test_empty_full_scan_is_explicitly_clean` still passes (it uses `include=()` explicitly).

In `tests/unit/test_config.py`:
- Test that `include_is_default` is `True` when no `include` key in config
- Test that `include_is_default` is `False` when `include` is explicitly set (even to the same values as default)

## Verify

- [ ] FR#6: Running `discover_files()` with default include scans from the project root
- [ ] FR#7: Explicit `include` config still works as before — replaces the default
- [ ] AC#5: Python files in non-standard directories (e.g., `packages/`) are discovered by default
- [ ] AC#8: Existing tests pass with the new default (backward compat for explicit include)
