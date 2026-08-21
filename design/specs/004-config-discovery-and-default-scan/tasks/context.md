# Context: Standalone Config Discovery & Root-Based Default Scan

## Problem & Motivation
house-lint only discovers `pyproject.toml` for config and uses a hardcoded default include list (`src`, `tests`, `scripts`, `tools`, `examples`) that silently produces zero results for non-standard layouts. Prior art research found no major Python linter uses this pattern; the dominant approach is scan-from-root filtered by gitignore + excludes.

## Key Decisions
1. Standalone config files use `[house-lint]` as the top-level table (not `[tool.house-lint]`), since they are not embedded in a multi-tool manifest.
2. Discovery order at each directory level: `house-lint.toml` → `.house-lint.toml` → `pyproject.toml` (with `[tool.house-lint]`).
3. Default include changes from `("src", "tests", "scripts", "tools", "examples")` to `(".",)` — scan from root, filtered by gitignore + expanded builtin excludes. This is a breaking change (`feat!`).
4. `BUILTIN_EXCLUDES` expands from 6 to 22 entries, matching Ruff's full default exclude list plus house-lint's extras (`__pycache__/`, `site-packages/`). This is a prerequisite for the scan-from-root change.
5. Zero-file diagnostic extends the existing `render_text()` "empty scan" message with context-aware guidance (not a separate stderr warning). Suppressed only for `include = []` or explicit CLI paths.
6. Config shadowing is surfaced via `--debug` when multiple config files exist at the same directory level.

## Constraints
- Do NOT implement nested/hierarchical config inheritance.
- Do NOT change exclude semantics or the gitignore reimplementation.
- `_validate_include()` must accept `"."` as a valid include entry.
- Standalone file detection by `--config` uses filename matching (`house-lint.toml` or `.house-lint.toml`), not content inspection.
- The zero-file diagnostic must not fire for intentional empty scans (`include = []`).
