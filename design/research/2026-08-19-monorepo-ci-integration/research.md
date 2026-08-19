---
topic: "Python linter monorepo support and CI/pre-commit integration"
date: 2026-08-19
status: Draft
---

# Prior Art: Python Linter Monorepo Support and CI/Pre-commit Integration

## The Problem

A linter that works well on a single project may fail in monorepos (config inheritance, per-package scoping, partial runs) and pre-commit hooks (incremental vs whole-repo, dependency isolation, entry point conventions). These integration patterns determine whether a tool gets adopted in real CI pipelines or stays a manual-invocation tool. Caching strategies determine whether repeated runs are fast enough to stay in the commit loop.

## How We Do It Today

House-lint has no explicit monorepo support. `resolve_project()` finds exactly one project root per invocation by walking up from cwd to the nearest `pyproject.toml` with `[tool.house-lint]`, falling back to `.git` or cwd. `DEFAULT_INCLUDE = ("src", "tests", "scripts", "tools", "examples")` assumes a conventional single-package layout. A monorepo would need one invocation per package via `--root`/`--config`, scripted externally.

Pre-commit integration exists via `.pre-commit-hooks.yaml` exposing `id: house-lint`, `entry: house-lint check`, `language: python`, `types: [python]`. README documents a `language: system` local-hook variant. No `pass_filenames` control or per-directory hook instantiation.

No CI integration beyond house-lint's own development CI (GitHub Actions running ruff/pyright/pytest on itself). No result caching — every invocation does full discovery and sequential per-file scanning.

CLI is Cyclopts-based with structured exit codes (0 clean, 1 findings, 2 config error, 3 lint errors, 4 internal error).

## Patterns Found

### Pattern 1: Hierarchical "closest config wins" with explicit `extend` inheritance

**Used by**: Ruff (mirrors ESLint's cascading config model).
**How it works**: The tool walks up from each file and uses the nearest config file with the tool's section. A subpackage's config can declare `extend = "../pyproject.toml"` to inherit the parent config and override specific fields. `pyproject.toml` files without the tool's section are skipped during discovery.
**Strengths**: Per-package customization without config duplication; explicit `extend` makes inheritance visible; "closest wins" is predictable.
**Weaknesses**: Subpackages must remember to declare `extend`; bare `pyproject.toml` without the tool section is silently skipped rather than inheriting.
**Example**: https://docs.astral.sh/ruff/configuration/

### Pattern 2: Isolated hosted hook vs local/system-language hook

**Used by**: Home Assistant core (ruff as hosted hook, mypy/pylint as `language: script` local hooks via `script/run-in-env.sh`).
**How it works**: Fast, dependency-free tools (ruff) run as hosted hooks in pre-commit's isolated virtualenv. Import/dependency-aware tools (mypy, pylint) run as local hooks with `language: system`/`script`, activating the project's real environment. Both participate in pre-commit's `files`/`types`/`pass_filenames` filtering.
**Strengths**: Fast tools get maximal caching/isolation; dependency-aware tools get correctness by using the real environment.
**Weaknesses**: Local hooks aren't portable across machines; mixing both shapes adds cognitive overhead.
**Example**: https://raw.githubusercontent.com/home-assistant/core/dev/.pre-commit-config.yaml

### Pattern 3: `pass_filenames` incrementality gated by cross-file analysis

**Used by**: pre-commit framework, Ruff hooks (files passed normally), HA core's mypy/pylint hooks (`require_serial: true`).
**How it works**: Pre-commit passes changed filenames by default (`pass_filenames: true`). For single-file linters this is safe and fast. For cross-file tools this is unsound: a signature change in file A won't flag a broken call in unchanged file B. Mitigations: `pass_filenames: false` with whole-project analysis (slower), or accept the tradeoff for PR-time feedback paired with periodic full scans.
**Strengths**: Incremental runs are the main lever for keeping pre-commit fast in large repos.
**Weaknesses**: Silently unsound for cross-file analysis — the single most-repeated caveat across all sources.
**Example**: https://pre-commit.com/, https://jaredkhan.com/blog/mypy-pre-commit

### Pattern 4: Root-level dispatch to per-package pre-commit configs

**Used by**: `sub-pre-commit`, `run-in-subdirectory` (PyPI), Mookme (JS).
**How it works**: A meta-hook in the root `.pre-commit-config.yaml` is registered once per subfolder with `files: "^<subfolder>/.*"` scoping. It receives filtered changed filenames, `cd`s into the subfolder, and re-invokes pre-commit against that subfolder's own config.
**Strengths**: Each package's linter config behaves as if standalone; different packages can use different toolchains.
**Weaknesses**: Layer of indirection; nested pre-commit invocations complicate caching and error reporting.
**Example**: https://github.com/ddanier/sub-pre-commit

### Pattern 5: Path-filtered per-module CI scans with result-namespace separation

**Used by**: Semgrep CI.
**How it works**: Each module gets its own CI workflow gated by path filters. Scans use `--subdir <path>` (auto-derives project name) or `--include`/`--exclude` with explicit `SEMGREP_REPO_DISPLAY_NAME` per module.
**Strengths**: CI cost scales with changes, not repo size; per-module namespaces prevent finding collisions.
**Weaknesses**: Some analyses (full history scans) can't be path-split; N workflow files to maintain.
**Example**: https://docs.semgrep.dev/kb/semgrep-ci/scan-monorepo-in-parts

### Pattern 6: Cache shape follows analysis shape

**Used by**: Ruff (`.ruff_cache` — flat per-file, version-namespaced), Mypy (`.mypy_cache` — dependency-graph incremental).
**How it works**: Ruff's cache is per-file result memoization keyed by (ruff version, file content/config), namespaced by release version so upgrades naturally invalidate. Mypy's cache stores a module dependency graph for true incremental rechecking. `--no-cache` / `--no-incremental` disable reading but still write (keep cache warm).
**Strengths**: Version-namespaced flat caches are simple and safe to delete. Dependency-graph caches give correct incremental behavior for cross-file analysis.
**Weaknesses**: More powerful caches have more invalidation bugs (mypy's cache poisoning from inconsistent `--follow-imports` or third-party package state).
**Example**: https://github.com/astral-sh/ruff/issues/17619

## Anti-Patterns

- **Flake8's "first setup.cfg wins" config discovery.** Stops at the first `setup.cfg` regardless of whether it has a `[flake8]` section, forcing config duplication across monorepo subpackages. (https://mail.python.org/archives/list/code-quality@python.org/thread/CMM4CS3M5F7IK2ZDVUVZS3SZIG2HLS7Q/)

- **Naive `pass_filenames` for cross-file tools.** A changed function signature is invisible if the caller file wasn't staged. (https://jaredkhan.com/blog/mypy-pre-commit, https://semgrep.dev/blog/2023/semgrep-speed/)

- **Hosted pre-commit hooks for dependency-aware linters in monorepos.** pre-commit's isolated env doesn't get real dependencies, causing import resolution failures. (https://github.com/pre-commit/pre-commit/issues/2951)

- **Assuming pylint can run once across multiple packages with different virtualenvs.** It can't — needs per-package invocation. (https://pylint.pycqa.org/en/stable/user_guide/installation/pre-commit-integration.html)

## Emerging Trends

- **Speed making "incremental-only" less necessary.** HA's own architecture discussion on adopting Ruff frames the motivation around raw speed enabling broader, less-scoped runs. As linters get faster, "just run it on everything" becomes viable even for large repos, reducing the need for bespoke incremental tooling. (https://github.com/home-assistant/architecture/discussions/863)

## Relevance to Us

House-lint is currently a **single-file, no-cross-file-analysis tool** — this is a significant advantage for pre-commit integration. Since each rule operates independently on one file's AST, `pass_filenames: true` (pre-commit's default) is semantically correct, not just a performance shortcut. The cross-file unsoundness caveat (Pattern 3's anti-pattern) does not apply.

**What aligns well:**
- The existing `.pre-commit-hooks.yaml` with `language: python` is the right shape for a dependency-free, single-file linter. This is the Ruff model (Pattern 2), not the Pylint model.
- Structured exit codes already enable CI integration (exit 0 = clean, exit 1 = findings).
- `--root`/`--config` flags already enable external scripting for multi-package invocations.

**Gaps worth addressing:**
- **No caching.** For a single-file linter, a flat per-file cache keyed by (house-lint version, file content hash, effective config) is straightforward and would make repeated runs near-instant. Ruff's version-namespaced `.ruff_cache` directory is the model to follow.
- **No `require_serial`/batching guidance.** The pre-commit hook should document whether `require_serial: true` is recommended (batches all files into one invocation, amortizing startup cost) vs the default (one invocation per file).
- **No hierarchical config for monorepos.** Not urgent for house-lint's current audience, but if adopted by projects with multiple HA integrations in subdirectories, per-package config with `extend` would be needed. The current `resolve_project()` walk-up logic is a natural base for this.
- **No nested `.gitignore` support.** Currently reads root `.gitignore` only. `pathspec` already supports this; it's a discovery-walk change.

## Recommendation

1. **Add a flat per-file cache** as the highest-impact integration improvement. Key by (house-lint version, file content hash, effective rule config hash). Version-namespace the cache directory (`.house-lint-cache/<version>/`). Support `--no-cache` and `--cache-dir` flags. This is low-complexity and makes repeated pre-commit runs near-free.

2. **Add `require_serial: true` to the pre-commit hook definition.** One `house-lint check` invocation with N files is cheaper than N invocations with one file each (amortizes config loading, import costs). This is what HA core does for its heavier linters.

3. **Defer monorepo config inheritance.** The current single-root model with `--root`/`--config` is adequate. If demand appears, follow Ruff's `extend` model (explicit inheritance, closest-config-wins). Don't build hierarchical discovery speculatively.

4. **Document CI integration patterns.** A GitHub Actions example showing `house-lint check --format json` with exit code handling would lower the adoption barrier without requiring code changes.

5. **Extend `.gitignore` support to nested files** when touching the discovery module next. Low-effort, high-correctness improvement.

## Sources

Note: these URLs were not live-verified.

### Reference implementations
- https://github.com/astral-sh/ruff-pre-commit — Ruff's pre-commit hook repo
- https://raw.githubusercontent.com/home-assistant/core/dev/.pre-commit-config.yaml — HA core's pre-commit config
- https://github.com/ddanier/sub-pre-commit — Per-subfolder pre-commit dispatch
- https://github.com/astral-sh/ruff/issues/17619 — Ruff cache structure/issues
- https://github.com/astral-sh/ruff/issues/5132 — Ruff cache behavior
- https://github.com/astral-sh/ruff/issues/15018 — Ruff cache issues

### Blog posts & writeups
- https://blog.therightchoyce.com/2021/06/07/splitting-python-code-into-multiple-services-inside-of-a-single-folder-monorepo-using-vs-code-while-ensuring-automatic-pylint-coverage/ — Pylint monorepo experience
- https://medium.com/opendoor-labs/our-python-monorepo-d34028f2b6fa — Opendoor shared lint config monorepo
- https://jaredkhan.com/blog/mypy-pre-commit — Mypy pre-commit unsoundness (via search summary)
- https://semgrep.dev/blog/2023/semgrep-speed/ — Semgrep incremental scanning tradeoffs

### Documentation & standards
- https://docs.astral.sh/ruff/configuration/ — Ruff hierarchical config discovery
- https://flake8.pycqa.org/en/latest/user/configuration.html — Flake8 config (non-hierarchical)
- https://pylint.pycqa.org/en/stable/user_guide/installation/pre-commit-integration.html — Pylint pre-commit guidance
- https://docs.semgrep.dev/kb/semgrep-ci/scan-monorepo-in-parts — Semgrep monorepo scanning
- https://pre-commit.com/ — pre-commit framework docs
- https://mypy.readthedocs.io/en/stable/command_line.html — Mypy incremental mode
- https://mail.python.org/archives/list/code-quality@python.org/thread/CMM4CS3M5F7IK2ZDVUVZS3SZIG2HLS7Q/ — Flake8 monorepo config limitation
- https://github.com/pre-commit/pre-commit/issues/2120 — pylint + pre-commit + poetry
- https://github.com/pre-commit/pre-commit/issues/2951 — Monorepo mypy dep resolution failure
- https://github.com/home-assistant/architecture/discussions/863 — HA ruff adoption rationale
