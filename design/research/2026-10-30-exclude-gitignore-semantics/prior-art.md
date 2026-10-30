---
topic: "How comparable linters implement a configured gitignore-style exclude, and whether they guard against divergence from git"
date: 2026-10-30
status: Draft
---

# Prior Art: Gitignore-Style `exclude` Without Asking Git

## The Problem

house-lint's `exclude` promises Git-ignore-style patterns, but a Python library evaluates them instead of git. Three review rounds on PR #71 found places where the two disagree: `src/**/` silently drops files directly in `src/`, an escaped trailing space diverges in both directions, `build/**` matches the directory itself, and the ancestor walk is only correct without negation.

The question is whether that pattern means the mechanism is wrong, and how much machinery comparable projects spend on the same gap.

## How We Do It Today

`_FileSelector._excluded` in `src/house_lint/discovery.py` compiles patterns with `pathspec.GitIgnoreSpec` and probes each ancestor in directory form; any match is final because `!` is rejected, both at config load and in `discover_files`. `tests/integration/test_exclude_parity.py` compares a hand-listed table of `Scenario` entries against real `git check-ignore`. `docs/configuration.md:104` records one divergence, a doubled backslash before a trailing space.

Decision D3 of `design/specs/007-git-file-discovery/design.md` deleted about 280 lines of hand-built matcher, which existed largely for negation. No consuming project uses negation, and the only `exclude` in hassette, hass-hassette, otf-api, and house-lint is `["design"]`.

## Local Probe

The decisive result came from running the shapes rather than from the survey. Files are `src/a.py` and `src/sub/b.py`; each cell lists what is excluded.

| Pattern | git | pathspec 1.1.1 (released) | pathspec master (unreleased 1.2.0) |
|---|---|---|---|
| `src/**/` | `src/sub/`, `b.py` | everything | same as git |
| `src/**` | `a.py`, `src/sub/`, `b.py` | also `src/` itself | same as git |
| `src/sub/**/` | nothing | `src/sub/`, `b.py` | same as git |
| `src/**/**/` | `src/sub/`, `b.py` | everything | same as git |
| `src/**/*/` | `src/sub/`, `b.py` | same as git | same as git |

So the open bug is a pathspec bug already fixed upstream, though no issue or changelog entry names this shape. `src/**/*/` is correct on every version, which is why rewriting to it is safe both before and after the upgrade.

## Patterns Found

### Pattern 1: Define your own semantics and don't promise gitignore

**Used by**: mypy and Pylint (regex), Flake8 and Bandit (fnmatch), Ruff (globset), ESLint (minimatch), Biome 2.x.

**How it works**: The configured exclude has its own grammar. Real `.gitignore` handling is a separate feature behind a flag (`respect-gitignore`, `skip_gitignore`). No parity claim means no divergence bugs.

**Strengths**: Cheapest in machinery, and nothing to break.

**Weaknesses**: Users keep asking for gitignore behaviour (Ruff #6262 and #8267 have been open for years), and docs can under-specify (Ruff #1545, Bandit #975).

**Example**: https://docs.astral.sh/ruff/settings/

### Pattern 2: "Gitignore-like" with a stated grammar and load-time validation

**Used by**: ty; partly Biome, through its `useBiomeIgnoreFolder` lint rule.

**How it works**: The docs say "gitignore like", list the supported tokens, and reject shapes with no defined meaning (`./b**/`, `***`) when the config loads.

**Strengths**: A confusing shape fails loudly instead of silently changing the scan.

**Weaknesses**: Both projects wrote their own glob engines, so this isn't a pathspec-based design.

**Example**: https://docs.astral.sh/ty/exclusions/

### Pattern 3: Delegate to a gitignore implementation and send bugs upstream

**Used by**: Ruff's `respect-gitignore` (the `ignore` crate), isort's `skip_gitignore` (git), Black's `.gitignore` handling (`GitIgnoreSpec`), Prettier (the `ignore` npm package).

**How it works**: When the promise is "same as git", hand the decision to the closest implementation. Black closed a divergence report as a dependency bug (#3228) and documented a behaviour change on its pathspec v1 bump (26.1.0).

**Strengths**: Little local code, and upstream fixes arrive for free. pathspec's maintainer accepts git divergences as bugs when given a reproduction.

**Weaknesses**: Semantics move with upstream releases. Black still needed a hand-written scan for directory negation (PR #5306, open) and leaves one divergence as-is.

**Example**: https://github.com/psf/black/releases/tag/26.1.0

### Pattern 4: Differential testing with git as the oracle

**Used by**: pathspec's own tests; the outside corpus behind pathspec #137 (9,852 cases from 43 real repositories, which reduced to two root causes).

**How it works**: Run `git check-ignore` over (rules, path) pairs and compare.

**Strengths**: Finds divergences nobody listed. None of the ten peers surveyed does this for their own exclude, so house-lint's parity table is already more checking than they do.

**Weaknesses**: A hand-listed table covers only the cases someone thought of, and it missed `<segment>/**/`.

**Example**: https://github.com/cpburnz/python-pathspec/issues/137

### Pattern 5: Rewrite directory patterns before compiling

**Used by**: proposed in Ruff #8267 (open since 2023, described there as "not elegant"); house-lint's deleted normalizer.

**How it works**: Rewrite or duplicate a pattern so its spelling compiles to the wanted meaning.

**Strengths**: Small and local.

**Weaknesses**: Each rewrite is a shape-specific exception, and it outlives its reason unless something removes it.

**Example**: https://github.com/astral-sh/ruff/issues/8267

## Anti-Patterns

- **Claiming gitignore syntax without a verified boundary.** Prettier says it uses gitignore syntax, and #14427 shows directory negation differing from git.
- **Matching one pattern against several surfaces.** Ruff tests a pattern against basename and path, so `foo*.py` excludes `foo/bar/baz.py` (#6262).

## Relevance to Us

No peer guards against these divergences at all, so house-lint's matcher plus parity table is already at the heavy end, not under-built. The convergence doesn't indicate a wrong mechanism: delegating to `GitIgnoreSpec` is the common design, and the mismatches trace to pathspec bugs that its maintainer fixes. Rebuilding a hand-written matcher would go against both D3 and what peers do.

Dropping the gitignore promise (Pattern 1) isn't needed either. With 1.2.0, the promise becomes more accurate on its own.

## Recommendation

Keep the mechanism and the promise. Treat `src/**/` as an upstream bug with a short-lived local workaround: rewrite a trailing `**/` run to `**/*/` before compiling, add parity scenarios, and delete the rewrite once the `pathspec` floor reaches a release containing the fix. The parity table will show whether the rewrite is still needed.

Documenting the divergence and waiting for 1.2.0 is also defensible, since 1.2.0 has no release date and no consumer uses this shape. It leaves silent scan loss in the meantime for a saving of one line.

Coverage gaps: no blog posts or talks on this specific topic turned up, the `ignore` crate and isort source were read only through search highlights, and the gitignore-conformance corpus named in pathspec #137 wasn't fetched.

## Sources

These URLs were not live-verified.

### Reference implementations

- https://github.com/cpburnz/python-pathspec/blob/master/CHANGES_1.in.rst — unreleased 1.2.0 fixes, including #137/#138, #135, and #142
- https://github.com/psf/black/pull/5306 — hand-written last-match-wins scan over pathspec, with one divergence left as-is
- https://github.com/PyCQA/isort/blob/main/isort/settings.py — `skip_gitignore` asks git

### Issue trackers

- https://github.com/cpburnz/python-pathspec/issues/137 — `X/**` matches the bare directory; found with a conformance corpus
- https://github.com/cpburnz/python-pathspec/issues/134 — forward and reverse evaluation disagree on negation
- https://github.com/cpburnz/python-pathspec/issues/89 — directory matching without a trailing slash, Will Not Fix
- https://github.com/psf/black/issues/4944 — pathspec's author explains the v1 pattern split
- https://github.com/psf/black/issues/3228 — divergence report closed as a dependency bug
- https://github.com/astral-sh/ruff/issues/8267 — `/**/*` suffix expansion proposal
- https://github.com/astral-sh/ruff/issues/6262 — `*` in `exclude` matches across `/`
- https://github.com/charliermarsh/ruff/issues/1545 — docs don't state which globset options are on
- https://github.com/PyCQA/bandit/issues/975 — exclusion syntax undocumented
- https://github.com/prettier/prettier/issues/14427 — directory negation differs from git

### Documentation

- https://docs.astral.sh/ruff/settings/ — `exclude` documented as globset globs
- https://docs.astral.sh/ty/exclusions/ — "gitignore like" grammar with load-time rejection
- https://biomejs.dev/linter/rules/use-biome-ignore-folder/json/ — lint rule for the directory-versus-contents shape
- https://prettier.io/docs/ignore — claims gitignore syntax
- https://eslint.org/docs/latest/use/configure/ignore — minimatch semantics
- https://mypy.readthedocs.io/en/stable/command_line.html — `exclude` is a regex
- https://flake8.pycqa.org/en/stable/user/options.html — `exclude` is fnmatch
- https://github.com/psf/black/releases/tag/26.1.0 — behaviour change documented on the pathspec v1 bump
