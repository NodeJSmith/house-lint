---
task_id: "T05"
title: "update configuration documentation"
status: "planned"
depends_on: ["T01", "T02", "T03", "T04"]
implements: ["FR#1", "FR#2", "FR#3", "FR#6", "FR#8"]
---

## Target Files

- modify: `docs/configuration.md`
- modify: `tests/unit/test_discovery.py`

## Prompt

Update `docs/configuration.md` to document the new standalone config file support, root-based default scanning, and zero-file diagnostic.

### 1. Standalone config files

Add a new section near the top (before or after the existing TOML example) documenting:
- `house-lint.toml` and `.house-lint.toml` are recognized as standalone config files
- They use `[house-lint]` as the top-level table (not `[tool.house-lint]`)
- Show an example:

```toml
[house-lint]
select = ["HSL001", "HSL002", "HSL003", "HSL004"]
include = ["src", "tests"]
```

### 2. Update discovery precedence

Update the "Discovery and precedence" section (item 3 and 4):
- Item 3: `--root` without `--config` checks `house-lint.toml` → `.house-lint.toml` → `pyproject.toml` in root
- Item 4: Upward walk checks `house-lint.toml` → `.house-lint.toml` → `pyproject.toml` (with `[tool.house-lint]`) at each level

### 3. Update default include

Change the existing `include` documentation:
- The default example should show the new behavior: when `include` is not configured, house-lint scans from the project root, filtered by gitignore and builtin excludes
- The old default directory list should be mentioned as an example of what users might configure if they want to narrow scope
- Update the opening TOML example to show `include` as an optional narrowing mechanism, not as a default that mirrors the built-in behavior

### 4. Zero-file diagnostic

Add a note documenting the warning that fires when no Python files are found, and when it does not fire (`include = []`, explicit paths).

### 5. Discovery-order parity test

Add a test in `tests/unit/test_discovery.py` that asserts the discovery precedence order (`house-lint.toml` → `.house-lint.toml` → `pyproject.toml`) matches what `docs/configuration.md` claims. Follow the naming convention of `test_no_path_scan_uses_all_documented_default_include_roots` — pin the documented behavior by name so future discovery changes force a doc update.

## Verify

- [ ] FR#1: Documentation describes standalone config file discovery order
- [ ] FR#2: Documentation describes `--root` standalone config discovery
- [ ] FR#3: Documentation shows `[house-lint]` table format for standalone files
- [ ] FR#6: Documentation describes root-based default scanning
- [ ] FR#8: Documentation describes the zero-file diagnostic warning
- [ ] Discovery-order parity test exists and pins the doc-claimed precedence
