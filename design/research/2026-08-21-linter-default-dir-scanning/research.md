---
topic: "Python linter default directory scanning"
date: 2026-08-21
status: Draft
---

# Prior Art: Python Linter Default Directory Scanning

## The Problem

When a Python linter runs without explicit path arguments or `include` configuration, how should it determine which directories contain scannable source files? The wrong default produces silent zero-results — the tool runs, finds nothing, exits 0, and the user has no signal that their project layout didn't match the tool's expectations. This is especially acute in monorepos, non-standard layouts, and projects that don't follow the `src/` convention.

## How We Do It Today

house-lint uses a hardcoded default include list: `DEFAULT_INCLUDE = ("src", "tests", "scripts", "tools", "examples")`. If none of these directories exist (e.g., a monorepo with `packages/` or `apps/`), `discover_files()` silently returns an empty result with zero errors, zero skipped files — indistinguishable from "checked everything, found no Python files." There is no warning, no fallback, and no way to distinguish "nothing matched" from "nothing exists to match."

## Patterns Found

### Pattern 1: Scan-the-given-path, filtered by gitignore + hardcoded excludes

**Used by**: Ruff, Black, Flake8
**How it works**: The tool does not hardcode `src/`/`tests/` as scan roots. It recursively walks whatever path is passed on the CLI (often defaulting to `.`), collecting all files matching target extensions. Along the way it prunes directories matched by a fixed "obviously not source" list (`.git`, `.venv`, `node_modules`, `__pycache__`, build/dist dirs) and, in modern tools (Ruff, Black), respects `.gitignore` files at every directory level. Files passed explicitly on the CLI bypass the excludes as an escape hatch.
**Strengths**: Works for any project layout without configuration — flat, `src/`-based, monorepo, whatever. Gitignore integration means build artifacts and generated code are excluded automatically. Very low config burden.
**Weaknesses**: Stray vendored or generated directories without `.gitignore` entries get scanned unintentionally. The tool has no concept of "this project's source lives here" — first-party import classification (`src`, `src_paths`) must be configured separately from discovery, a documented source of user confusion (Ruff discussion #15538).
**Example**: https://docs.astral.sh/ruff/settings/

### Pattern 2: No default scan root — the tool refuses to guess

**Used by**: mypy, Black (requires explicit path), isort, Bandit
**How it works**: The tool has no fallback directory. If the user doesn't pass a path on the CLI and doesn't set an equivalent config option, there is nothing to check. Config only substitutes for a missing CLI argument, not a smarter default.
**Strengths**: Zero ambiguity about what got checked. Forces the scope decision to be made once, explicitly, in CI or config.
**Weaknesses**: This is exactly the shape that produces silent-zero-results when a CI step doesn't pass a path, or a path is typo'd. No searched documentation surfaces a "0 files scanned" warning for mypy, Black, or Bandit. Safety depends entirely on the invoking script getting the path right.
**Example**: https://mypy.readthedocs.io/en/stable/config_file.html

### Pattern 3: Config-file-location as implicit root

**Used by**: Pyright/Pylance, Ruff's project-root detection for nested configs
**How it works**: The tool walks up the filesystem from the file being analyzed (or from cwd) to find the nearest config file, and treats that directory as the project root. If no `include` is set, the root path itself becomes the scan scope.
**Strengths**: Handles "invoked from a subdirectory" naturally. Editors benefit especially — the file being edited determines which config applies. Nested-config inheritance gives monorepos a way to share defaults.
**Weaknesses**: Genuinely independent per-package monorepos break this model. Pyright's maintainers explicitly declined per-file nearest-`pyproject.toml` resolution (issue #10498, closed as not planned).
**Example**: https://github.com/microsoft/pyright/blob/main/docs/configuration.md

### Pattern 4: Package-marker heuristic (`__init__.py`)

**Used by**: Pylint's `--recursive=y` mode
**How it works**: Instead of hardcoding directory names, Pylint identifies packages by `__init__.py` presence and treats any directory containing one as scannable. Piggybacks on Python's own language-level convention.
**Strengths**: Layout-agnostic — doesn't care whether packages live under `src/`, at the repo root, or nested arbitrarily.
**Weaknesses**: Namespace packages (PEP 420, no `__init__.py`) and modern implicit namespace packages are under-detected. [no source found] confirming current Pylint behavior with namespace packages.
**Example**: https://pylint.readthedocs.io/en/v2.17.7/user_guide/usage/run.html

### Pattern 5: `src`/`src_paths` as import-classification hint, decoupled from discovery

**Used by**: Ruff, isort
**How it works**: A `src`/`src_paths` setting helps import-sorting decide first-party vs. third-party classification. It is explicitly *not* used for file discovery — that's a separate mechanism. Ruff defaults to `[".", "src"]`; when unset, falls back to a `detect-same-package` heuristic.
**Strengths**: Cleanly separates "what files do I check" from "which imports are mine."
**Weaknesses**: Two independently-configured mechanisms can drift out of sync, producing invocation-directory-dependent confusion (Ruff discussion #15538).
**Example**: https://docs.astral.sh/ruff/settings/

## Anti-Patterns

- **Hardcoded directory-name lists as default scan roots.** No major Python tool does this. Pyright's monorepo issue (#10498) shows even config-location-as-root breaking down for multi-package repos — a hardcoded name list is even more brittle.
- **Two separate heuristics for related decisions without visible coupling.** Ruff's `src` (classification) vs. config-root (discovery) are independently configured, and users get inconsistent results without understanding why.
- **Zero-file scan with no distinguishing signal.** None of the searched tools' official docs describe a built-in "0 files scanned" warning. The silent-zero-results failure mode is industry-wide, not unique to house-lint.

## Relevance to Us

house-lint's `DEFAULT_INCLUDE` is the only approach in this survey that uses a hardcoded list of directory names as default scan roots. Every other tool either scans from `.` (filtering out non-source directories) or requires explicit paths. Our approach is more opinionated but also more fragile — it silently produces zero results for any project that doesn't match the assumed layout.

The strongest pattern from the ecosystem is Pattern 1 (Ruff/Black): scan from the project root, use gitignore + hardcoded excludes to skip non-source directories. house-lint already reimplements gitignore-based exclusion in its discovery layer, so the infrastructure is already there.

Pattern 3 (config-file-location as root) is partially relevant — issue #34 is adding standalone config file discovery, and the location of the config file could naturally define the scan root.

The silent-zero-results problem is not solved well anywhere in the ecosystem. Adding a diagnostic when no files are discovered would put house-lint ahead of the field, not just at parity.

## Recommendation

**Switch the default from a hardcoded directory list to "scan from project root, filtered by gitignore + excludes"** (Pattern 1). This is what Ruff, the dominant modern Python linter, does, and it's what users expect. Keep `include` as an opt-in narrowing mechanism for users who want to restrict scope, but make the unconfigured default "scan everything under the project root that isn't gitignored or in the hardcoded exclude list."

Separately, **add a diagnostic warning when zero files are discovered** — this addresses the silent-zero-results problem regardless of which default-discovery approach is chosen, and puts house-lint ahead of every tool surveyed.

## Sources

### Reference implementations
- https://docs.astral.sh/ruff/settings/ — Ruff's default exclude list and src setting
- https://github.com/astral-sh/ruff/blob/main/docs/configuration.md — Ruff config file discovery and force-exclude
- https://github.com/microsoft/pyright/blob/main/docs/configuration.md — Pyright config-file-as-root pattern
- https://pylint.readthedocs.io/en/v2.17.7/user_guide/usage/run.html — Pylint recursive mode with __init__.py heuristic

### Blog posts & writeups
- https://docs.bswen.com/blog/2026-03-29-ruff-config-discovery/ — Ruff hierarchical config discovery explained
- https://hynek.me/articles/testing-packaging/ — Origin of the src/ layout convention
- https://blog.hashhackers.com/blog/bandit-guide/ — Bandit usage patterns
- https://techbeatly.com/how-to-use-bandit-to-scan-your-python-code-for-security-vulnerabilities/ — Bandit explicit path requirement

### Documentation & standards
- https://flake8.pycqa.org/en/latest/user/configuration.html — Flake8 configuration and default excludes
- https://mypy.readthedocs.io/en/stable/config_file.html — mypy files/packages/modules config
- https://black.readthedocs.io/en/stable/usage_and_configuration/file_collection_and_discovery.html — Black file collection and gitignore integration
- https://pycqa.github.io/isort/docs/configuration/config_files.html — isort src_paths configuration
- https://docs.pytest.org/en/stable/reference/customize.html — pytest rootdir/testpaths separation

### GitHub issues & discussions
- https://github.com/astral-sh/ruff/discussions/15538 — Ruff src vs. discovery confusion
- https://github.com/astral-sh/ruff/discussions/9226 — Ruff detect-same-package heuristic
- https://github.com/microsoft/pyright/issues/10498 — Pyright monorepo root detection (declined)
