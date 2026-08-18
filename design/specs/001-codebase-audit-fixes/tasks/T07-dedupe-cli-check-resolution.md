---
task_id: "T07"
title: "Remove cli.py check()'s duplicated resolve_project fallback logic"
status: "done"
depends_on: []
implements: ["FR#6", "AC#6"]
---

## Target Files

- modify: `src/house_lint/cli.py`

## Prompt

`src/house_lint/cli.py`'s `check()` function (currently around lines 304-370) pre-computes its own
copy of root/config resolution *before* calling the real `resolve_project()`:

```python
resolved_root: Path | None = None
resolved_config: Path | None = None
try:
    if config is not None:
        resolved_config = config.expanduser().resolve()
        resolved_root = (
            root.expanduser().resolve() if root is not None else resolved_config.parent
        )
    resolution = resolve_project(root=root, config=config)
    resolved_root = resolution.root
    resolved_config = resolution.config
    ...
except ConfigError as exc:
    result = _result_for_config_error(exc, root=resolved_root, config=resolved_config)
    ...
```

The manual `if config is not None: ...` block exists purely so the `except ConfigError` handler has
*something* to pass to `_result_for_config_error` if `resolve_project()` itself raises before
returning. But this manual computation skips every validation `resolve_project` does (no
`root.is_dir()` check, no "config must be inside root" check, no "config must exist" check) — it's
a strictly weaker parallel implementation of the same rule, and if `resolve_project`'s actual
algorithm changes later, this fallback won't follow.

Read `_result_for_config_error`'s signature first (find it in `cli.py`) to confirm what it accepts
when `root`/`config` are `None` — it should already handle that gracefully (this is the normal case
when `resolve_project` fails on its very first line, before any resolution happens).

Simplify `check()` to not pre-compute anything: on `ConfigError`, pass the raw, unresolved CLI
inputs where sensible, or simply omit `root=`/`config=` from the `_result_for_config_error` call
if `_result_for_config_error` handles `None` cleanly (check `_write_config_error`/`ScanResult`'s
handling of `None` root/config before deciding). The end state should be: no `resolved_root`/
`resolved_config` local variables computed by hand-copying `resolve_project`'s logic — either pass
nothing extra to the error path, or pass only the raw un-resolved `root`/`config` parameters
`check()` already received as arguments (no `.expanduser().resolve()` re-derivation).

Do not change the happy-path behavior (`resolution = resolve_project(...)` and everything after it
stays the same) — only the `ConfigError`-handling fallback changes.

## Verify

- [ ] FR#6: `grep -n "resolved_config = config.expanduser" src/house_lint/cli.py` returns no
      matches.
- [ ] AC#6: `uv run pytest tests/integration/test_cli.py -v -k config` passes — this file's
      config-error integration tests are the real regression check for this change (confirm the
      exact test names by reading the file first; adjust the `-k` filter to match what's there).
- [ ] `uv run pytest -q` reports all tests passing.
