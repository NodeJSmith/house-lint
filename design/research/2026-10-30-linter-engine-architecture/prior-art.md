---
topic: "How mature linters structure their engine, and whether house-lint's port-derived structure fits its goal"
date: 2026-10-30
status: Draft
---

# Prior Art: linter engine architecture

## The Problem

house-lint started as a port of hassette's lint scripts, so its structure came from that port rather than from a design for its current goal: per-file rules, cross-file rules, first-class TypeScript, and new-code gating. Milestone 005 reshapes the engine, but it took the existing module boundaries as a starting point. The question here is which of those boundaries a from-scratch design would keep, and which would be fought over time.

## Bottom Line

The skeleton matches the peers: a per-file bundle, then rules, then suppression last, with a per-file cache around that and discovery and reporting outside. #56's extraction is the right move and nothing here changes it.

Three things differ in ways that matter, and none of them is inside #56:
1. **Discovery** is a real early-design artifact. A constraint set when it cost almost nothing was never re-weighed, and discovery grew into the largest module.
2. **Rule declaration** is spread over several places, and milestone 005's D3 adds another.
3. **Suppression for cross-file rules** (parent D14, D15) carries more machinery than any peer surveyed.

## How We Do It Today

Figures are from the code at the time of this brief.

- **Pipeline:** `cli._scan`, about 185 lines, serial. `scanner.py` (176 lines) handles one file.
- **One-file unit:** `SourceFile` is a lazy, memoized bundle of bytes, tokens, AST, and statements. It is Python-only.
- **Rules:** seven function detectors, 584 lines in total. Each walks for itself, about 3-5 full walks per file. Adding a rule touches the catalog, the registry, `selected_detector_inputs`, options and cache hashing, docs, and tests. Omitting the `selected_detector_inputs` entry silently drops the rule.
- **Suppression:** runs after all detectors, with statement-owner matching. HSL900 can't be disabled and fails the run.
- **Cache:** per-file, keyed on content and config hash, namespaced by a source fingerprint. 597 lines, under 100 of them key and lookup logic.
- **Discovery:** 1,031 lines, reimplementing git's ignore rules on `pathspec`, plus 749 lines of parity and fuzz suites.

## Area-by-area comparison

| Area | Peers converge on | house-lint | Classification |
|---|---|---|---|
| 1. Pipeline | Library core for one file; outer driver owns discovery, cache, and printing (Ruff, Pylint, flake8) | `scanner.py` is the core; `_scan` is the driver, stuck in the CLI | **Matches** once #56 lands |
| 2. One-file unit | One immutable bundle built once per file; language as an object (ESLint) | Same bundle, Python-only; parent D4 splits it | **Matches**, with a known gap and a plan |
| 3a. Rule dispatch | One shared walk (Ruff, Pylint, ESLint) | Each rule walks | **Differs, fine at our scale.** Ruff's deferred-visit logic has produced its own recurring bugs. Not profiled here |
| 3b. Rule declaration | One source of truth | Several places, with a silent-drop hazard; D3 adds a second registry | **Likely to be fought** |
| 4. Cross-file | Decline it, or a separate opt-in phase (Biome, oxlint, Pylint) | Opt-in project phase (parent D3) | **Matches.** ESLint's regret doesn't transfer: it comes from a plugin API it can't break, and house-lint has neither |
| 5a. Suppression position | Last stage over one list (Ruff, Pylint) | Same | **Matches** |
| 5b. Unused suppression, cross-file | Excluded (Pylint), or selectable and non-failing (Ruff, Pylint) | Full verdicts, always on, with re-parsing and a completeness flag (parent D14) | **More machinery than any peer; reconsider** |
| 6. Cache | Per-file result, settings, and version (Ruff, ESLint) | Same; project phase uncached, avoiding ESLint's stale-result problem | **Matches, and it pays.** Measured below |
| 7. Discovery | Dedicated library (Ruff, Biome), `pathspec` with accepted divergences (Black), or declined (flake8) | Hand-built reimplementation | **Early-design artifact.** It works, and no peer carries this |
| `--base` | Baseline file (ESLint, basedpyright) or diff filter (riff) | Two-tree run planned (#52) | **Unprecedented among mature linters.** Precedent is hassette's own script |

## Measured: does the cache earn its size?

`house-lint check` on hassette (1,483 files, three runs each, default rules):
- Cold, including cache writes: 7.2-7.7s
- Warm: 0.85-0.90s
- Process startup alone (`house-lint rules`): 0.17s

So it pays about 8x. Most of its 597 lines are validation and write safety, more than a peer likely needs, but the module has been touched by one commit and no fixes. It costs nothing to leave alone.

## Findings that matter

### Discovery: an early constraint outlived its context

The first spec listed "Do not shell out to Git for discovery or ignore matching" under Key Constraints, with no rationale recorded (`design/specs/001-llm-lint-extraction/design.md`). At that point discovery read only the root `.gitignore`, so the constraint cost almost nothing. Nested `.gitignore` support was added later, and `design/specs/002-per-directory-gitignore-matcher` and the 2026-08-20 research both carried the constraint forward as given, the latter noting it rules out the git option "without an explicit reversal". The constraint's cost grew with the feature, and I found no point where it was re-weighed against that cost.

From scratch, `git ls-files --cached --others --exclude-standard` inside a repo, and a plain walk outside one, gets exact git fidelity at a fraction of the code:
- **Peer precedent for the limitation:** Ruff's `--respect-gitignore` only works inside a git repo and that's accepted (#5930). flake8 declined the feature and points to pre-commit, which passes tracked files.
- **#52 fit:** a `git archive` extraction contains only tracked files, so it needs no ignore handling at all.
- **Costs:** a subprocess and a `git` dependency for gitignore handling, and behaviour changes: a tracked file matching an ignore pattern would now be scanned, and non-repo trees stop honouring `.gitignore`. Submodule handling is unverified.
- **What stays:** symlink and root containment, the file limit, config excludes, and config discovery. How many lines would go hasn't been measured.

Against replacing it: it works, showing zero divergence in its fuzz suite, and it's sunk cost either way. The case for replacing it is the ongoing cost of owning git's semantics, and that #57 (D6) and #59 (D10's two passes) are about to build on it.

### Rule declaration: D3 adds to the spread

Adding a rule already means editing several parallel structures kept in sync by an import-time check, and one of them (`selected_detector_inputs`) isn't covered by that check. Parent D3 adds "a second registry", and D5 adds declared option types. A from-scratch design has one `Rule` declaration: id, metadata, enablement, kind, languages, options type, and detector. #58 and #59 are where that gets decided. Evidence is thinner here: option-declaration internals of Ruff and ESLint weren't read in this pass, so the case rests mostly on house-lint's own documented hazard.

### Suppression: the machinery exists for a verdict peers skip

Pylint excludes `duplicate-code` and `cyclic-import` from useless-suppression outright (`INCOMPATIBLE_WITH_USELESS_SUPPRESSION`). mypy hit the same conflict between cached results and unused-ignore reporting (#2960). Parent D14's deferred verdicts, per-file carve-outs, and completeness flag exist largely to report unused project-rule pragmas. Dropping that one verdict would remove much of #60, at the cost of stale pragmas on duplicates going unreported. That's worth weighing at #60's pickup. Separately, D15's per-grammar owner node kinds exist for statement ownership. Ruff's and ESLint's directives are line-scoped as far as I know, though this pass didn't verify it, so #61's pickup should check. Tree-sitter stays either way, because finding comments reliably is why D15 needed it.

### `--base`: don't let it drive engine shape yet

No mature linter surveyed runs its pipeline over two trees. Finding identity has already been raised in review three times (#52's own note), and peers sidestep it with per-file-per-rule counts (elm-review) or code-and-range without line numbers (basedpyright). #52's sketch should weigh a baseline before committing. #56 doesn't depend on this: the project phase alone justifies the extraction.

## Patterns Found

### Pattern 1: Library core with an outer driver

**Used by**: Ruff, Pylint, flake8, ESLint (`Linter` vs `ESLint`)
**How it works**: The core takes a prepared one-file bundle and returns diagnostics. Discovery, cache lookup, parallelism, and printing live outside it. Ruff's `ruff_linter::check_path` is the clearest case, and its `ruff` crate owns discovery and cache.
**Strengths**: Per-file work is independent, so caching and parallelism sit outside the rules.
**Weaknesses**: A cross-file stage has no obvious home. ESLint's team calls its API split confusing.
**Example**: https://github.com/astral-sh/ruff/blob/main/crates/ruff_linter/src/linter.rs

### Pattern 2: Cross-file as a separate opt-in phase

**Used by**: Biome (scanner, project rules), oxlint (`import/*` gate), Pylint (map/reduce plus `close`), Semgrep (separate Pro engine)
**How it works**: The cross-file cost is paid only when a rule that needs it is enabled. Biome and oxlint build a module graph that rules query. Pylint's per-file phase emits summaries that a reduce step combines.
**Strengths**: Users who don't want the cost don't pay it, and the phase is nameable and testable.
**Weaknesses**: Pylint's `duplicate-code` reported nothing under `--jobs` until 2023.
**Example**: https://biomejs.dev/internals/architecture/ ; https://oxc.rs/docs/guide/usage/linter/multi-file-analysis

### Pattern 3: Decline cross-file in the linter

**Used by**: Ruff (moved to `ty`), ESLint ("rules only run in the context of one file"), Semgrep OSS
**How it works**: The core stays single-file, and whole-program work goes to a separate engine or to plugins.
**Strengths**: The core stays simple.
**Weaknesses**: Users carry the gap. Ruff users hand-list classes in config (#7866), and typescript-eslint describes "a square peg into a round hole".
**Example**: https://github.com/astral-sh/ruff/issues/7447 ; https://github.com/eslint/eslint/discussions/16557

### Pattern 4: Suppression last, with unused-suppression over the same list

**Used by**: Ruff (`check_noqa`, RUF100), Pylint (I0021), ESLint
**How it works**: Every checker reports into one per-file list, then suppression runs. A directive matching nothing is reported, usually behind a switch.
**Strengths**: A suppression is judged against the complete set of diagnostics for that file.
**Weaknesses**: Anything reported outside that pass can't be matched, so Pylint carves out cross-file messages.
**Example**: https://github.com/astral-sh/ruff/blob/main/crates/ruff_linter/src/checkers/noqa.rs ; https://github.com/PyCQA/pylint/blob/main/pylint/constants.py

### Pattern 5: Delegate gitignore, or accept the limit

**Used by**: Ruff and Biome (library), Black (`pathspec`, with known divergences), flake8 (declined)
**How it works**: No surveyed tool reimplements git's per-directory semantics by hand. Ruff's gitignore support needs a repo. flake8 relies on callers passing tracked files.
**Strengths**: No semantics to own.
**Weaknesses**: Black's divergences silently skip files (#5306), which is the failure that matters most for a linter, since a skipped file looks clean.
**Example**: https://github.com/psf/black/pull/5306 ; https://github.com/PyCQA/flake8/issues/638 ; https://github.com/astral-sh/ruff/issues/5930

### Pattern 6: Baseline instead of a second tree

**Used by**: ESLint bulk suppressions, basedpyright, elm-review
**How it works**: One run records existing violations, matched by counts or by code and range without line numbers. Later runs subtract them, and stale entries are pruned or fail the run.
**Strengths**: One run, and no second tree to extract.
**Weaknesses**: Counts can't tell a moved issue from a fixed-and-reintroduced one, and it adds a committed file.
**Example**: https://eslint.org/docs/latest/use/suppressions ; https://docs.basedpyright.com/latest/benefits-over-pyright/baseline/

## Anti-Patterns

- **Per-file cache with cross-file rules.** ESLint serves stale results and only documented it in 2026 (https://github.com/eslint/eslint/issues/21312). Parent D11 already avoids this.
- **Unused-suppression checks over incomplete context.** Pylint and mypy both hit this.
- **Discovery that silently skips files.** Black's traversal dropped subtrees under some negations (#5306).
- **Line-number baselines.** They break when code shifts (https://github.com/astral-sh/ruff/issues/1149).

## Relevance to Us

**Keep:** the pipeline extraction, the bundle-plus-language split (D4), the opt-in project phase (D3's concept), suppression position, the per-file cache, and uncached project rules (D11).

**Fine at our scale, leave alone:** per-rule walks and the cache's safety code.

**Reconsider before building on them:**
- Discovery: reverse the no-git constraint, ideally before #59.
- Rule declaration: one `Rule` table at #58 and #59 rather than a second registry.
- Unused verdicts for project rules, and statement ownership in TypeScript, at #60 and #61.
- A baseline versus two trees, at #52.

**Where the planned work has no precedent:** wrapping jscpd as an engine-level rule. No mature linter surveyed does this. That makes it unproven, not wrong, and the 2026-10-18 research already covered the tool choice.

## Recommendation

Proceed with #56 as sketched. File an issue reversing the discovery constraint, and carry the other three items into the pickups they belong to, using each pickup's existing ability to supersede a parent decision.

**Coverage gaps:** about 20 sources were read directly and about 20 only as search snippets. Not found or not read: Biome's per-language services, oxlint's `ContextHost`, Ruff and ESLint option-declaration internals, and any Rome-to-Biome retrospective. Line-scoping of Ruff and ESLint directives comes from background knowledge rather than this pass. The URLs were not live-verified.

## Sources

### Reference implementations
- https://github.com/astral-sh/ruff/blob/main/crates/ruff_linter/src/linter.rs — `check_path`, ordering of checkers and `check_noqa`
- https://github.com/astral-sh/ruff/blob/main/crates/ruff_linter/src/checkers/noqa.rs — suppression and RUF100
- https://github.com/astral-sh/ruff/blob/main/crates/ruff/src/cache.rs — cache keying
- https://github.com/pylint-dev/pylint/blob/main/pylint/lint/pylinter.py — run shape
- https://github.com/pylint-dev/pylint/blob/main/pylint/checkers/base_checker.py — map/reduce hooks
- https://github.com/PyCQA/pylint/blob/main/pylint/constants.py — `INCOMPATIBLE_WITH_USELESS_SUPPRESSION`
- https://github.com/python/mypy/blob/master/mypy/build.py — graph-based build
- https://github.com/scdor/riff — diff-filtering wrapper for Ruff

### Issues, PRs, and design discussions
- https://github.com/astral-sh/ruff/issues/7447 — multi-file analysis built outside Ruff first
- https://github.com/astral-sh/ruff/issues/18432 — duplicates and dead code
- https://github.com/astral-sh/ruff/issues/7866 — users hand-list classes in config
- https://github.com/astral-sh/ruff/issues/1149 — baseline and line shift
- https://github.com/astral-sh/ruff/issues/5930 — gitignore requires a repo
- https://github.com/eslint/eslint/discussions/16557 — rewrite discussion
- https://github.com/eslint/eslint/discussions/18676 — single-file stance
- https://github.com/eslint/eslint/issues/21312 — `--cache` stale with cross-file rules
- https://github.com/eslint/rewrite/blob/main/decisions/001-rewrite-core.md — rewrite ADR
- https://github.com/python/mypy/issues/2960 — unused ignores vs cache
- https://github.com/PyCQA/flake8/issues/638 — gitignore declined
- https://github.com/psf/black/pull/5306 — `pathspec` divergences from git

### Documentation
- https://biomejs.dev/internals/architecture/ — scanner and project rules
- https://biomejs.dev/blog/biome-v2-1/ — scanner scope
- https://oxc.rs/docs/guide/usage/linter/multi-file-analysis — gated multi-file analysis
- https://eslint.org/docs/latest/extend/languages — language objects
- https://eslint.org/docs/latest/use/suppressions — bulk suppressions
- https://docs.basedpyright.com/latest/benefits-over-pyright/baseline/ — baseline without line numbers
- https://pylint.readthedocs.io/en/stable/faq.html — disabling whole-codebase checks
- https://pylint.readthedocs.io/en/latest/user_guide/messages/information/useless-suppression.html — off by default
