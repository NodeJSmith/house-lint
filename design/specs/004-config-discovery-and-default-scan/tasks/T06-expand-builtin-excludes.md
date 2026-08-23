---
task_id: "T06"
title: "expand BUILTIN_EXCLUDES to match Ruff's default exclude list"
status: "done"
depends_on: []
implements: ["FR#10"]
---

## Target Files

- modify: `src/house_lint/discovery.py`
- modify: `tests/unit/test_discovery.py`
- modify: `docs/configuration.md`

## Prompt

Expand `BUILTIN_EXCLUDES` in `discovery.py` (line 26) to match Ruff's full default exclude list plus house-lint's existing extras (`__pycache__/`, `site-packages/`). This is a prerequisite for the scan-from-root default change — without it, switching to `DEFAULT_INCLUDE = (".",)` would scan thousands of vendored `.py` files in common directories like `venv/`, `.tox/`, and `.mypy_cache/`.

### 1. Update `BUILTIN_EXCLUDES` in `discovery.py`

Replace the current 6-entry tuple:

```python
BUILTIN_EXCLUDES = (".git/", ".venv/", ".nox/", "__pycache__/", "site-packages/", "node_modules/")
```

With the full 22-entry tuple (alphabetically sorted for readability):

```python
BUILTIN_EXCLUDES = (
    ".bzr/",
    ".direnv/",
    ".eggs/",
    ".git/",
    ".git-rewrite/",
    ".hg/",
    ".mypy_cache/",
    ".nox/",
    ".pants.d/",
    ".pytype/",
    ".ruff_cache/",
    ".svn/",
    ".tox/",
    ".venv/",
    "__pycache__/",
    "__pypackages__/",
    "_build/",
    "buck-out/",
    "dist/",
    "node_modules/",
    "site-packages/",
    "venv/",
)
```

### 2. Update documentation

In `docs/configuration.md`, update the "Built-in excludes" sentence to list all 22 entries instead of the current 6.

### 3. Update tests

Check `tests/unit/test_discovery.py` for any tests that assert the exact contents of `BUILTIN_EXCLUDES` or test specific exclude behavior. Update as needed to reflect the expanded list. Add a test that confirms all 22 entries are present (pin the count/contents by name, following the repo's convention of test-pinning documented behavior).

## Verify

- [ ] FR#10: `BUILTIN_EXCLUDES` contains all 27 entries
- [ ] AC#10: The list matches Ruff's actual current default exclude list (verified via `ruff check --isolated --show-settings`) plus house-lint's existing extras (`__pycache__/`, `.house-lint-cache/`)
- [ ] `docs/configuration.md` reflects the expanded list
- [ ] Existing tests pass with the expanded list
