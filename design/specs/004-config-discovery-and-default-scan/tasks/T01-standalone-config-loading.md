---
task_id: "T01"
title: "add standalone config file loading to config.py"
status: "done"
depends_on: []
implements: ["FR#3", "FR#4"]
---

## Target Files

- modify: `src/house_lint/config.py`
- modify: `tests/unit/test_config.py`

## Prompt

Add support for standalone `house-lint.toml` / `.house-lint.toml` config files in `config.py`.

### 1. Add `get_standalone_table()`

Add a function parallel to `get_house_lint_table()` that extracts the `[house-lint]` top-level table from a standalone config file:

```python
def get_standalone_table(document: dict[str, Any]) -> dict[str, Any] | None:
    house_lint = document.get("house-lint")
    return cast(dict[str, Any], house_lint) if isinstance(house_lint, dict) else None
```

### 2. Add standalone config file name constants

Add a module-level constant for the recognized standalone filenames:

```python
STANDALONE_CONFIG_NAMES: tuple[str, ...] = ("house-lint.toml", ".house-lint.toml")
```

### 3. Add `is_standalone_config()`

Add a helper that checks whether a path is a standalone config file (by filename):

```python
def is_standalone_config(path: Path) -> bool:
    return path.name in STANDALONE_CONFIG_NAMES
```

### 4. Update `load_config()`

Add a `standalone: bool = False` parameter. When `standalone=True`, use `get_standalone_table()` instead of `get_house_lint_table()`. Update both:
- The "config lacks" error message (line 455) to reference `[house-lint]` instead of `[tool.house-lint]`
- The `_strict_keys(house, {...}, "tool.house-lint")` call (line 456-468) — the third argument is the error-message prefix for unknown keys, so it must read `"house-lint"` when loading a standalone file

### 5. Tests

In `tests/unit/test_config.py`, add tests for:
- `get_standalone_table()` returns the `[house-lint]` table when present
- `get_standalone_table()` returns `None` when `[house-lint]` is absent
- `load_config()` with `standalone=True` loads from `[house-lint]` table
- `load_config()` with `standalone=True` raises `ConfigError` when `[house-lint]` is missing
- `is_standalone_config()` returns `True` for both filename variants

## Verify

- [ ] FR#3: `get_standalone_table()` correctly extracts `[house-lint]` from a standalone TOML file
- [ ] FR#4: `load_config(path, standalone=True)` successfully loads a standalone config file
