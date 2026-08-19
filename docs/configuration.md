# Configuration

Configure `house-lint` in `[tool.house-lint]` in `pyproject.toml`.

```toml
[tool.house-lint]
include = ["src", "tests", "scripts", "tools", "examples"]
exclude = []
select = ["HSL001", "HSL002", "HSL003", "HSL004"]
ignore = []
extend-select = []
extend-ignore = []
```

`include` contains literal root-relative files or directories, not globs. An empty array intentionally selects no roots for a full scan. `exclude` uses root-relative Git-ignore-style patterns. Unknown keys, absolute paths, parent traversal, invalid patterns, duplicate IDs, and `HSL900` in `select`, `ignore`, `extend-select`, or `extend-ignore` are configuration errors. Unlike the rest of this schema, `extend-select`/`extend-ignore` are hyphenated by design, matching Ruff's spelling for the same additive-selection concept.

## Discovery and precedence

1. `--root` fixes the project boundary.
2. `--config` selects an exact configuration. Without `--root`, its parent is the root; with `--root`, it must be inside the root.
3. With `--root` and no `--config`, only `<root>/pyproject.toml` is considered.
4. Without either option, the command searches upward from the current directory for the nearest `pyproject.toml` containing `[tool.house-lint]`. If none exists, it uses the nearest ancestor containing `.git` or any `pyproject.toml`; otherwise it uses the current directory.
5. The base selection is configured `select` minus configured `ignore`, or a CLI `--select` wholesale override when given.
6. `extend-select`/`extend-ignore` (config and CLI, unioned together) layer additively on top of that base, regardless of whether the base came from config or `--select`. `extend-ignore` removes rules from the whole base, not just from `extend-select` — `select = ["HSL001"]` with `extend-ignore = ["HSL001"]` drops HSL001 entirely, it isn't limited to canceling out `extend-select` additions.
7. CLI `--ignore` is applied last and always wins over everything above. `HSL900` is always added.

The root `.gitignore` and every nested `.gitignore` between the root and each discovered file are loaded and combined with git's own precedence — a closer `.gitignore` can override a farther one, including via negation (`!pattern`). Built-in excludes are `.git/`, `.venv/`, `.nox/`, `__pycache__/`, `site-packages/`, and `node_modules/`; configured excludes are added afterwards. `--no-gitignore` disables `.gitignore` handling at every level.

## Rule options

```toml
[tool.house-lint.rules.HSL102]
max_lines = 800

[tool.house-lint.rules.HSL103]
allowed = ["exc", "*_exc"]
```

`HSL102.max_lines` must be an integer from 1 through 10,000,000. `HSL103.allowed` must be a non-empty unique array of identifiers or patterns containing exactly one leading `*` followed by an identifier suffix.

## HSL101 token families

`HSL101` has no default token vocabulary. Select it only with a non-empty `tokens` array:

```toml
[tool.house-lint.rules.HSL101]
max_findings_per_file = 200

[[tool.house-lint.rules.HSL101.tokens]]
prefixes = ["AC", "FR", "NFR", "WP"]
scopes = ["comments", "docstrings", "filenames"]
hash = "optional"
min_digits = 1
max_digits = 12
suffix = "optional-lower-alpha"
case_sensitive = true
not_followed_by_time = false
```

Each family requires unique `prefixes` (1–32 uppercase values, up to 12 characters each) and unique `scopes` drawn from `comments`, `docstrings`, and `filenames`. You may configure at most 32 families. `hash` is `forbidden`, `optional`, or `required`; `min_digits` is 1–12; `max_digits`, when present, is from `min_digits` through 12; `suffix` is `none` or `optional-lower-alpha`; and both boolean options must be TOML booleans. `max_findings_per_file` is a positive integer no greater than 10,000.

Rule tables do not have `enabled` keys. Selection is owned exclusively by top-level selection and CLI overrides; disabled rule tables are still validated.
