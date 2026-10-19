---
audience: personal tool, semver-disciplined
developers: solo
peers: Ruff, flake8, Pylint, wemake-python-styleguide
data-sensitivity: internal
---

# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this project is

`house-lint` flags habits AI assistants leave in code: filler phrases in comments, function-local imports, constants defined below the first function, runaway file length, and leaked planning tokens. Duplicate-code detection is planned (#39). It started as a port of hassette's ad hoc lint scripts so the maintainer's other projects could share them, and that remains its main audience. It flags patterns, not provenance: never infer or report whether code was AI-written.

Before adding a rule, ask two questions:
1. Can you name where AI assistants produce this pattern, ideally with an instance from a real repo?
2. Does Ruff, Pylint, or the type checker already flag it?

If the answer to 2 is yes, configure or wrap that tool instead of reimplementing it. If the answer to 1 is no, it's a general style preference and doesn't belong here. Generating a plausible-sounding rule is cheap, so the bar is evidence that the pattern recurs. Rules are opinionated, not a general plugin framework.

Today every rule is a Python, single-file detector, and parts of the engine assume that: the per-file result cache, and read-once loading. Those are implementation details from the port, not design principles. Cross-file checks (duplicate code #39, complexity and dead code #53) and first-class TypeScript support are in scope. The ratified plan for the engine work is the milestone ledger at `design/specs/005-cross-file-rules-and-duplicate-code/design.md`.

When a concrete rule or a ratified spec needs the engine to grow, grow the engine, and don't reject an approach because it breaks a single-file assumption. For unrelated changes, keep the diff small. Prefer wrapping a proven external tool over hand-building a complex analyzer.

It's published on PyPI, but the maintainer is the only known user and every consuming project pins its version. Breaking the CLI flags, config schema, JSON output, exit codes, or suppression syntax is acceptable when you can say what the break buys; the cost is one coordinated version bump.
- **No shims.** Don't keep old flags, config keys, or pragma spellings alive. Rename them, and update `docs/` and `README.md` in the same PR.
- **Mark it.** Use `feat!` or `fix!`, and put the `BREAKING CHANGE:` footer in a commit message on the branch, not only in the PR body. release-please reads commits, so a footer that exists only in the PR description is silently dropped (see `.claude/rules/changelog-quality.md`).

## Commands

Everything runs through `uv run`. There is no Makefile or justfile.

- Test: `uv run pytest`
- Lint: `uv run ruff check .`
- Format: `uv run ruff format`
- Type check: `uv run pyright` (strict mode, `src/` only; tests are not type-checked)
- Build: `uv build` (backend is `uv_build`; don't switch it)

CI (`.github/workflows/ci.yml`) runs the tests on Python 3.11 through 3.14, and Ruff, Pyright, and the build on 3.11. `prek.toml` runs Ruff, Pyright (at pre-push), house-lint on itself, and some hygiene hooks, but not pytest or the build. Install the hooks with `prek install -t pre-commit -t pre-push`.

## Layout

`src/house_lint/`:
- `cli.py`: the cyclopts app, plus the `_scan` loop that runs discovery, cache reads and writes, and result aggregation
- `config.py`: config discovery, validation, and per-rule option dataclasses (`HSL10xOptions`)
- `discovery.py`: file discovery and gitignore handling
- `source.py`: `SourceFile`, which loads, decodes, tokenizes, and parses one file
- `scanner.py`: loading one file, running its detectors, and applying suppressions
- `registry.py`: rule dispatch
- `rule_catalog.py`: rule metadata
- `suppressions.py`: pragma parsing and HSL900
- `cache.py`: the result cache
- `results.py`: the result model
- `reporters/`: text and JSON output
- `rules/`: one module per rule

Tests:
- `tests/unit/`, with `tests/unit/rules/` mirroring `src/house_lint/rules/`.
- `tests/integration/`: CLI, reporters, the pre-commit hook, and gitignore parity and fuzz suites.
- `tests/conftest.py` provides `write_sample(content) -> Path`, which dedents content and writes it to `tmp_path/sample.py`. Use it in rule tests instead of ad hoc file writes.

`design/` holds research briefs (`design/research/`), sketch ledgers and specs (`design/specs/`), and audits. Check there for prior decisions before re-deciding something.

## Adding a rule

Today a rule is a detector `(source: SourceFile, options, *, limit=None) -> list[CandidateFinding]` in `rules/<name>.py`. Adding one touches:

1. `rule_catalog.py`: the `RULES` entry. This is the single source of truth for ID, display info, and default or opt-in tier.
2. `registry.py`: the `_DETECTORS` entry. An import-time check raises `RuntimeError` if `_DETECTORS` and `rule_catalog.ORDINARY_RULES` diverge. A rule with options needs a small cast adapter, like `_hsl102`.
3. `config.py`: an entry in `selected_detector_inputs`'s options dict, even for a rule with no options (`None`). A rule missing from it is silently dropped and never runs, and the registry check doesn't catch that.
4. For a configurable rule, also add:
   - an options dataclass, a `LintConfig` field, the `DetectorOptions` union member, and `_rule_options` parsing
   - the options in `cache.hash_effective_config` and `cli._cache_keys`, or a changed option will replay stale cached results
5. `docs/rules.md` and, for options, `docs/configuration.md`. Also update the README rules table.
6. Tests under `tests/unit/rules/`.

If a step here doesn't match the code, the code wins; fix this list in the same PR.

IDs: `HSL001`-`HSL0xx` are on by default, `HSL1xx` are opt-in, and `HSL900` is reserved. The first cross-file rule will need a project-level phase that this detector signature doesn't provide. Design that as part of #39 rather than forcing it into the per-file shape.

## Gotchas

- **HSL900 can't be disabled or suppressed.** It validates the `ignore`, `ignore-next`, and `ignore-file` pragmas that silence every other rule, including flagging unused ones.
- **Discovery reimplements git's ignore rules on `pathspec`.** It never shells out to git. It reads the root `.gitignore` and every nested one, and uses a per-directory matcher stack where the walker supplies `is_dir`. Any change to pattern handling needs a case in `tests/integration/test_gitignore_parity.py`. That suite compares discovery against real `git check-ignore`, and a case costs one `Scenario` entry with no expected values. See `docs/configuration.md` and `design/research/2026-08-20-gitignore-style-exclusion-inclusion/`.
- **Ignored directories are pruned, not enumerated**, so `files_skipped` counts one skip per pruned directory.
- **Default scope is the whole tree** from the root, minus gitignored paths and built-in excludes. `include` narrows it to literal root-relative paths.
- **Each scanned file is read exactly once**, by `SourceFile.load()`. The cache key comes from that same buffer (`content_bytes` → `cache.hash_source_content`), so a cache entry can never describe content other than what was scanned. A second read of a scanned path lets the file change between the read that was parsed and the read that was hashed, so a cache entry could describe the wrong content. `test_each_scanned_file_is_read_exactly_once` catches it, and you should not weaken that test. A rule that reads files outside `SourceFile.load()`, such as an external tool given paths, must either not be cached or hash what it reads.
- **The cache is per-file and namespaced** by `<version>-<hash of house-lint's own sources>`, so editing rule code invalidates it without a version bump. Its key includes the Python version. house-lint writes a self-ignoring `.gitignore` only into its default cache directory, never into a user-supplied `--cache-dir`.

## Conventions

- **Commits and PR titles** follow Conventional Commits, enforced in CI on PR titles. PR titles become changelog entries; see `.claude/rules/changelog-quality.md`, which also explains why `.claude/` changes are typed `chore:` here.
- **Releases** are automated by release-please. Don't hand-edit `CHANGELOG.md` or bump `version` in `pyproject.toml`.
- **CodeRabbit** has `auto_review.enabled: false`. After opening a PR or pushing to one, comment `@coderabbitai full review` to trigger it.
