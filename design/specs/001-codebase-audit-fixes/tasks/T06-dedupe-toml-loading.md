---
task_id: "T06"
title: "Extract a shared TOML-loading helper"
status: "planned"
depends_on: []
implements: ["FR#5", "AC#5"]
---

## Target Files

- modify: `src/house_lint/config.py`
- modify: `src/house_lint/discovery.py`

## Prompt

The pattern `path.open("rb")` → `tomllib.load(stream)` wrapped in exception handling that raises
`ConfigError` is duplicated three times:

- `src/house_lint/config.py:277-281` (inside `load_config`)
- `src/house_lint/discovery.py:322-328` (inside `resolve_project`'s upward `pyproject.toml` search)
- `src/house_lint/discovery.py:342-348` (inside `resolve_project`'s final root-adjacent check)

The three copies already have subtly different error messages ("cannot read config {path}" vs
"invalid project configuration: {exc}"), which is exactly the kind of drift that happens when the
same logic lives in three places.

Add a shared helper function `_load_toml(path: Path) -> dict[str, Any]` to `src/house_lint/config.py`
(it already owns `ConfigError` and is a natural home — `discovery.py` already imports from
`config.py` for `ConfigError` and `get_house_lint_table`, so adding one more import is consistent
with the existing dependency direction):

```python
def _load_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file, raising ConfigError with a consistent message on failure."""
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid project configuration: {exc}") from exc
```

Use exactly this wording (`discovery.py`'s existing phrasing), not `config.py`'s current "cannot
read config {path}" wording — this is not a free choice.
`tests/unit/test_discovery.py::test_invalid_ancestor_pyproject_is_a_configuration_failure` (line
444) asserts `pytest.raises(ConfigError, match="invalid project configuration")`; the `config.py`
wording would fail that test. `tests/unit/test_config.py` has no assertion on the exact message
text, so switching `config.py`'s call site to the new shared wording is safe.

Update all three call sites to use `_load_toml(path)` instead of their inline
`tomllib.load`/`except` blocks:
- `config.py`'s `load_config` (replaces its own inline block with a call to the new helper it now
  owns).
- `discovery.py`'s two call sites in `resolve_project` (import `_load_toml` from `.config`).

Keep the surrounding logic (what happens with the parsed `dict` afterward — e.g. checking
`get_house_lint_table(data)`) unchanged; only the load-and-raise mechanics move into the helper.

## Verify

- [ ] FR#5: `grep -c "tomllib.load" src/house_lint/config.py src/house_lint/discovery.py` — the
      total count across both files should now be 1 (in the new `_load_toml` helper), not 3.
- [ ] AC#5: `uv run pytest tests/unit/test_config.py tests/unit/test_discovery.py -v` passes —
      these already cover the "bad TOML" / "unreadable config" error paths, so this is the real
      regression check.
- [ ] `uv run pytest -q` reports all tests passing.
- [ ] `uv run pyright` (strict, `src/` only) is clean.
