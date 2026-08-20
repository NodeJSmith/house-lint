# Research Brief: Fix Silent Under-Linting from Directory-Only Negation (Issue #27)

---
proposal: "Replace the flatten/prefix/compensate gitignore pipeline with a per-directory matcher stack that evaluates directory-only patterns against real is_dir, fixing the silent under-linting bug."
date: 2026-08-20
status: Draft
flexibility: Exploring
motivation: "Both fix the correctness bug (silent under-linting of files git would lint) AND simplify the architecture (the flatten/prefix/compensate machinery is complex and fragile)."
constraints: "Existing parity and fuzz test suites must pass. No shelling out to git for discovery (CLAUDE.md constraint). Over-linting xfail may remain."
non-goals: "Chasing the benign over-linting divergence specifically (if the new approach also fixes it, great). Not a full gitignore reimplementation beyond what house-lint already covers."
depth: deep
---

**Initiated by**: Issue #27 -- "Discovery silently skips files git would lint (directory-only negation)"

## Context

### What prompted this

When a root `.gitignore` contains `**` followed by `!**/` (a directory-only negation that re-includes directories so git descends into them), house-lint prunes the entire subtree instead of walking into it. Every file underneath silently vanishes from the scan, producing zero findings -- indistinguishable from a clean run. The asymmetry matters: over-linting is visible and silenceable with one `exclude` entry; under-linting is invisible.

The bug was found when the fuzz suite's corner-body pool learned to compose repeated `**` segments (it had never done so before), and an earlier guarantee that "the divergence always errs toward over-linting, never toward silently skipping" was falsified. That guarantee was the entire justification for the current architecture.

### Current state

`discovery.py` (765 lines) uses a **hybrid** approach: traversal-based directory pruning during `os.walk` (Pattern 1 behavior) combined with flattened, root-anchored `pathspec.GitIgnoreSpec` matching (Pattern 2 behavior). The bugs live in the seam where the two halves disagree.

**The flatten/prefix/compensate pipeline** (~130 lines, lines 115-242) exists solely to make pathspec work for nested `.gitignore` files:

- `_escape_gitignore_literal` (115-123) -- escapes metacharacters in literal directory names before re-embedding in pattern text
- `_strip_unescaped_trailing_whitespace` (126-150) -- mirrors gitwildmatch's trailing-space rule for the rewrite round-trip
- `_collapse_double_star_run` (153-168) -- collapses `**/**/` runs to match git's behavior
- `_normalize_contents_glob` (171-191) -- rewrites trailing `/**` to `/**/*` so it can't match the directory itself
- `_prefix_pattern` (194-242) -- rewrites a nested-directory pattern into a root-anchored equivalent

**`_combined_gitignore_spec`** (526-587) walks the ancestor chain root-to-directory, prefixes each nested `.gitignore`'s lines via `_prefix_pattern`, and builds one root-anchored `GitIgnoreSpec`. It short-circuits to `IGNORE_EVERYTHING = ("**",)` when an ancestor is already excluded -- this is where the two halves' agreement is hand-enforced.

**`_traversable_dirs`** (632-674) asks pathspec "is this directory ignored?" via `_ignored(..., is_dir=True)`. When pathspec says yes, the directory is pruned from `os.walk` and nothing beneath it is ever visited.

**The bug mechanism**: `GitIgnoreSpec.from_lines(("**", "!**/")).match_file("src")` returns `True` (still ignored). git reports `.gitignore:2:!**/` re-including `src` and descends into it. The root cause is that `pathspec` classifies directory-only patterns from pattern text rather than accepting a caller-supplied `is_dir` from the filesystem. Passing `src/` (with trailing slash) does not change pathspec's answer either -- pathspec's internal priority system (which assigns directory patterns priority 1 and file patterns priority 2) does not implement git's "last match wins" rule. There is no shape of question house-lint can ask pathspec that returns the correct verdict.

### Key constraints

- CLAUDE.md: file discovery does not shell out to git. Rules out `git check-ignore --stdin`.
- The parity suite (26 scenarios, 78+ parametrized assertions) and fuzz suite (3 distributions x 1500 trials) must continue to pass.
- Two strict xfail markers in the parity suite must be resolved (the under-linting one removed, the over-linting one re-evaluated).
- `pathspec` issue #89 ("fails to match directories if they don't end with a slash") is labeled **Will Not Fix** by the maintainer -- this will not be fixed upstream.

## Feasibility Analysis

### What would need to change

| Area | Files affected | Effort | Risk |
|------|---------------|--------|------|
| Matcher replacement | `discovery.py` (rewrite ~200 lines, delete ~130 lines of compensation) | Med | Core logic change; well-guarded by existing test suites |
| Test xfail cleanup | `test_gitignore_parity.py` (2 xfails), `test_gitignore_fuzz.py` (divergence ceiling) | Low | Mechanical -- remove xfails, drop `MAX_KNOWN_DIRECTORY_NEGATION_DIVERGENCES` to 0 |
| Documentation | `docs/configuration.md` (remove under-linting bullet, regenerate rates), `CLAUDE.md` (update divergence note) | Low | Text-only |
| No new files needed | The replacement logic lives in `discovery.py` where the current logic lives | -- | -- |

### What already supports this

- **house-lint is already halfway to Pattern 1.** It prunes during `os.walk`, already passes explicit `is_dir` into `_ignored`, and already short-circuits ancestor exclusion. The traversal scaffold exists.
- **pathspec exposes per-pattern building blocks.** Each `GitIgnoreSpecPattern` object has `.regex` (compiled), `.include` (bool), and `.pattern` (raw text). The `match_file` method on individual patterns returns a `RegexMatchResult` or `None`. This means pathspec can stay as the glob-to-regex compiler.
- **Directory-only detection is trivial.** A pattern is directory-only when its raw text (after stripping a leading `!`) ends with `/`. No need to inspect pathspec internals.
- **The test infrastructure is purpose-built for this change.** The parity suite differentially checks against real `git check-ignore` -- a new implementation that passes the parity suite is correct by construction. The fuzz suite catches regressions nobody thought to write down. The harness self-test ensures the suite can't go vacuous.

### What works against this

- **The flatten/prefix/compensate pipeline is load-bearing today.** Every nested `.gitignore` pattern currently goes through `_prefix_pattern` to become root-anchored. Replacing this with per-directory matching means each directory owns its own pattern list, matched against directory-relative paths, which is a different data flow.
- **Caching strategy changes.** Currently `combined_gitignore_spec_cache` and `spec_by_lines_cache` cache flattened specs keyed on accumulated line tuples. A per-directory stack would cache per-directory matchers instead, with a different sharing strategy for siblings.
- **The IGNORE_EVERYTHING short-circuit is a correctness invariant.** Git's rule is that a negation cannot re-include a file whose parent directory was excluded. The current code enforces this by short-circuiting to `IGNORE_EVERYTHING` when an ancestor is excluded. The replacement must preserve this invariant -- it's the reason the matcher stack can't just naively check each directory independently.

## Options Evaluated

### Option A: Per-directory matcher stack with pathspec as glob-to-regex compiler

**How it works**: Replace `GitIgnoreSpec` (pathspec's aggregate spec with its broken priority system) with house-lint's own per-directory evaluator. Keep `pathspec` only for what it does correctly: compiling gitignore glob patterns into Python regexes via `GitIgnoreSpecPattern`.

Each directory in the walk gets its own list of `(pattern_regex, include, is_dir_only)` tuples, built from that directory's `.gitignore` file. The evaluator walks candidates in **reverse** (last pattern wins, per git's rule) and applies an `is_dir` gate: a directory-only pattern is only allowed to match when the candidate is confirmed to be a directory by the filesystem (`DirEntry.is_dir()` from `os.walk`). This is the `matched_stripped` insight from ripgrep's `ignore` crate -- separate "does the regex match this path text?" from "is this directory-only pattern eligible for this candidate?"

Pattern matching is done against **directory-relative** paths (not root-relative), eliminating the entire `_prefix_pattern` pipeline. Each directory's matcher is evaluated in the context of its ancestors: the walker asks each level "does your `.gitignore` have an opinion on this entry?" from innermost to outermost, and the first match wins (git's precedence: deeper `.gitignore` overrides shallower).

The `IGNORE_EVERYTHING` invariant (a negation can't resurrect a file whose parent was excluded) is preserved by the walker itself: if a directory was pruned, nothing beneath it is ever visited. This is already how `_traversable_dirs` works -- the difference is that the pruning decision is now correct because the evaluator knows that `!**/` is a directory-only negation and applies it when `is_dir=True`.

**What gets deleted** (~130 lines):
- `_prefix_pattern` and its helpers (`_escape_gitignore_literal`, `_strip_unescaped_trailing_whitespace`, `_collapse_double_star_run`)
- `_normalize_contents_glob`
- `IGNORE_EVERYTHING`
- The flattening logic inside `_combined_gitignore_spec`

**What gets added** (~80-120 lines):
- A `DirectoryMatcher` (or similar) that wraps a list of parsed patterns from a single `.gitignore`, with a `matches(relative_name, is_dir) -> bool | None` method (tri-state: ignored, whitelisted, no opinion)
- A stack evaluator in `_traversable_dirs` / `_ignored` that walks the matcher stack innermost-to-outermost
- Per-directory caching (keyed on directory path, sharing matchers for siblings that have no `.gitignore` of their own)

**Pros**:
- Fixes the under-linting bug by construction: directory-only patterns are gated on real `is_dir`
- Likely fixes the over-linting divergence too: per-directory evaluation with last-wins matches git's "re-include only the named entry, then re-evaluate descendants" behavior
- Net code deletion: removes ~130 lines of compensation, adds ~80-120 lines of cleaner logic
- Keeps pathspec for what it's good at (glob-to-regex), avoids reimplementing gitwildmatch
- Test infrastructure is already purpose-built to validate this change
- No new dependencies

**Cons**:
- Largest code change of the three options -- touches the core matching logic
- Per-directory matchers are a new abstraction that must be understood and maintained
- Needs careful handling of the explicit-path code path (`_consider` for explicit files/directories, `_has_excluded_ancestor`) to ensure it uses the same stack evaluation
- pathspec's `GitIgnoreSpecPattern` is used below the documented API surface (`.regex`, `.include` on individual pattern objects); while these attributes appear stable (they're dataclass fields), this is not the "parse a spec, call match_file" contract

**Effort estimate**: Medium -- the test infrastructure does the heavy verification lifting, but the matcher replacement touches core logic that requires careful reasoning about git's precedence rules.

**Dependencies**: None new. pathspec stays; it's used differently (individual pattern parsing rather than aggregate spec matching).

### Option B: Swap in `gitignorefile` package

**How it works**: Replace pathspec's gitignore matching with the `gitignorefile` PyPI package (`excitoon/gitignorefile`), which implements per-directory `.gitignore` cascading internally via its `Cache` class and resolves `is_dir` via real filesystem calls (`path.isdir()`).

**Confirmed from source**: `gitignorefile`'s `_IgnoreRule.match()` checks `not self.__directory_only or m.group(1) is not None or is_dir`, and when the caller doesn't supply `is_dir` explicitly, it resolves via a real filesystem call. This is the exact correctness gap pathspec has.

**Pros**:
- Handles `is_dir` correctly (confirmed from source) -- fixes the core bug
- Potentially simpler integration if the API maps well to discovery's needs

**Cons**:
- **Effectively dormant**: last release 1.1.2, September 2022. Zero commits in 2024, 2025, or 2026. 8 open issues unaddressed. 11 stars, 4 forks.
- **Has its own negation bugs**: the `file-matcher-python` test corpus shows `gitignorefile` XFAILing 9 negation test cases in its "negation-#7" block -- incorrect handling of `!/build/allow.log` mixed with directory ignores.
- **API mismatch**: `gitignorefile.Cache` owns tree-walking and `.gitignore` discovery internally. This conflicts with house-lint's own `_FileSelector` which already owns the walk and has its own caching, symlink handling, and error reporting. Adopting `gitignorefile` means either (a) delegating the walk to it (losing house-lint's symlink safety, containment checks, and error attribution) or (b) fighting the API to use it as a pure matcher (which it wasn't designed for).
- **Python version support**: classifiers list 3.6-3.12 only (house-lint tests on 3.11-3.14 per CI)
- **Takes a dependency on an unmaintained package**: if `gitignorefile` has a bug that affects house-lint, there's no upstream to fix it.
- **Does not delete the compensation machinery cleanly**: because the API mismatch requires adapter code, the net complexity reduction is uncertain.

**Effort estimate**: Medium -- the integration work is comparable to Option A, but with higher ongoing risk from the unmaintained dependency.

**Dependencies**: Adds `gitignorefile>=1.1.2` (unmaintained).

### Option C: Minimal patch -- evaluate directory-only patterns manually at the pruning decision

**How it works**: Keep the existing flatten/prefix/compensate pipeline intact. At the single point where the bug manifests -- `_traversable_dirs`'s call to `_ignored(..., is_dir=True)` -- add a secondary check: after pathspec says "ignored," inspect the winning pattern to see if it's a directory-only negation that should have overridden. If so, don't prune.

Concretely: when `_ignored` returns `True` for a directory, re-evaluate by iterating the combined spec's `.patterns` list in reverse, calling each pattern's `.match_file()` individually, and applying the `is_dir` gate. If a directory-only negation (`include=False`, pattern text ends with `/`) matches, the directory is not pruned.

**Pros**:
- Smallest change -- ~20-30 lines added, no lines deleted
- Fixes the under-linting bug specifically
- No new abstractions, no dependency changes
- Easy to review and reason about

**Cons**:
- **Does not fix the over-linting divergence** -- per-directory evaluation semantics remain flattened
- **Leaves the compensation machinery in place** -- the architectural complexity that makes the codebase fragile stays
- **Dual evaluation paths**: the normal matching still goes through `GitIgnoreSpec.match_file()`, while the directory pruning decision now has a special override. Two code paths answering the same question is a maintenance hazard.
- **Band-aid over an architectural gap**: the research doc and issue both characterize the root cause as architectural (hybrid Pattern 1 + Pattern 2), and this patches over a single symptom without addressing the structure. Future bugs from the same root cause would require more patches of the same shape.
- **Relies on accessing pathspec internals** (iterating `.patterns`, calling `.match_file()` on individual patterns) in the same way Option A does, but without the benefit of owning the evaluation logic cleanly.

**Effort estimate**: Small -- but the architectural debt stays, and the fix is narrow enough that related bugs (if they exist) remain unfixed.

**Dependencies**: None.

## Concerns

### Technical risks

- **Explicit-path code path**: `_consider` handles explicit paths (files and directories passed directly to `house-lint check`). It uses `_combined_gitignore_spec` and `_has_excluded_ancestor` -- both must be updated to use the same per-directory evaluation as the walk path, or explicit paths will diverge from walked paths. The parity suite's `test_explicit_paths_match_git_check_ignore` and `test_explicit_directory_arguments_match_git_check_ignore` catch this, but the coupling is worth noting.
- **pathspec internal API stability**: Options A and C both use `GitIgnoreSpecPattern.regex`, `.include`, and `.match_file()` on individual pattern objects. These are dataclass fields (stable by convention), but pathspec's documented API is `GitIgnoreSpec.match_file()` on aggregate specs. A pathspec version bump could break individual-pattern access. Mitigation: pin pathspec version in CI; the attributes are structural (a `RegexPattern` subclass), not incidental.
- **pathspec has other open bugs**: Issue #93 (wildmatch divergences: `foo**/bar`, bracket-class edge cases), issue #129 (re-includes files under excluded directory), issue #131 (ReDoS in regex translation). Options A and C both keep pathspec for regex compilation. The ReDoS issue (#131) is worth monitoring -- crafted `**` chains can cause pathological regex behavior regardless of which evaluation strategy house-lint uses.

### Complexity risks

- Option A introduces a new `DirectoryMatcher` abstraction and changes the caching strategy. While the test suite provides strong regression coverage, the new code must correctly implement git's precedence rules (last match wins, directory-only gating, ancestor exclusion). Getting this wrong would be caught by the parity suite, but the review burden is real.
- The research doc warns that adopting the "correct" architecture is not a correctness guarantee by itself -- ruff #17392 is an open bug where gitignore fidelity differs between `ruff check src/foo` and `ruff check src/`, on the same `ignore`-crate lineage. The differential suites stay necessary regardless of which engine wins.

### Maintenance risks

- Option A: lower long-term maintenance because the compensation machinery is deleted. New `.gitignore` edge cases map to git's own precedence model rather than requiring more `_prefix_pattern` patches.
- Option B: taking a dependency on a dormant package creates a ticking maintenance bomb. When it breaks (Python version, negation edge case), the fix is either forking the package or doing Option A anyway.
- Option C: higher long-term maintenance because the dual-evaluation-path pattern invites future patches of the same shape, and the compensation machinery remains a source of bugs.

## Open Questions

- [ ] **Does pathspec's `GitIgnoreSpecPattern` handle all of gitignore's glob syntax correctly for the regex compilation step?** Options A and C both rely on pathspec for glob-to-regex, but issue #93 documents wildmatch-level divergences (`foo**/bar` matching `foobar`, bracket-class edge cases). If these affect patterns house-lint's users actually write, the per-directory matcher would inherit them. Unknown -- would need targeted parity tests for the #93 cases.
- [ ] **Can the per-directory evaluation handle `_has_excluded_ancestor` cleanly?** The current `_has_excluded_ancestor` handles `builtin_spec` and `exclude_spec` (static root-anchored specs, not per-directory). These are conceptually different from `.gitignore` patterns and may need to stay as-is alongside the per-directory stack. Unknown -- needs design work.
- [ ] **What's the performance impact of per-directory evaluation vs. flattened spec matching?** The current approach builds one spec per directory and calls `match_file` once. Per-directory evaluation walks the matcher stack for each entry. For deeply nested trees with many `.gitignore` files, this could be measurably slower. Likely negligible for typical projects, but worth measuring. Speculative -- no profile data.
- [ ] **Should the over-linting xfail also be fixed in the same change?** Per-directory evaluation with last-wins semantics should fix both divergences (the over-linting `!sub/` case and the under-linting `!**/` case), but the scope expansion adds review burden. The issue says "don't chase it specifically" -- consider leaving the over-linting xfail for a separate verification pass if it happens to pass.
- [ ] **pathspec ReDoS (issue #131)**: crafted `**` chains can cause pathological regex behavior in pathspec's regex translation. This affects all options that keep pathspec. Worth monitoring but likely out of scope for this fix.

## Recommendation

**Option A (per-directory matcher stack with pathspec as glob-to-regex compiler)** is the right approach. The reasoning:

1. **The bug is architectural, and the fix should be too.** The existing research doc, the issue, and the code all point to the same root cause: flattening nested `.gitignore` files into one root-anchored spec loses the per-directory evaluation semantics that git's matching relies on. Patching over the symptom (Option C) leaves the architecture fragile and invites future bugs from the same root cause.

2. **The test infrastructure makes this safe.** The parity suite (26 scenarios, 78+ assertions, differential against real git) and fuzz suite (4500 trials across 3 distributions) are purpose-built to catch regressions. A new implementation that passes these suites is correct by construction. The strict xfail markers provide an automatic signal when the bug is fixed.

3. **It's a net simplification.** Deleting ~130 lines of compensation machinery (`_prefix_pattern`, `_collapse_double_star_run`, `_normalize_contents_glob`, `_escape_gitignore_literal`, `_strip_unescaped_trailing_whitespace`, `IGNORE_EVERYTHING`) and replacing them with ~80-120 lines of cleaner per-directory evaluation reduces both total lines and reader load. The new code maps directly to git's own model rather than compensating for a library's mismatch.

4. **Option B (gitignorefile) is not viable.** The package is dormant (no commits since 2022), has its own negation bugs, and its API mismatch with house-lint's existing walker would require substantial adapter code. Taking a dependency on an unmaintained package to fix a correctness bug is the wrong trade.

5. **Option C (minimal patch) is viable but unsatisfying.** It fixes the specific symptom with minimal code, but leaves the compensation machinery in place and creates a dual-evaluation-path maintenance hazard. If the goal were purely "close the issue," this would work. If the goal includes "simplify the architecture," it does not. The issue's motivation explicitly names both.

Confidence: **Supported** -- no single source states this is the right approach, but the convergence of the existing research doc's recommendation, the issue's proposed fix, the `ignore` crate's proven design, and the verification from pathspec's individual-pattern API all point to Option A. The test infrastructure reduces the risk to "does it pass the suites."

### Suggested next steps

1. **Write a design doc via `/mine-define`** for Option A, including the `DirectoryMatcher` data structure, the stack evaluation algorithm, the caching strategy, and how `_has_excluded_ancestor` / explicit paths integrate.
2. **Prototype the per-directory evaluator in a branch**, running the parity and fuzz suites after each step. The strict xfail markers will flip to passing when the bug is fixed -- that's the signal.
3. **Regenerate the fuzz suite's divergence rates** post-fix (`CI=1 uv run pytest -s tests/integration/test_gitignore_fuzz.py`) and update `docs/configuration.md` and `CLAUDE.md` to match.

## Sources

- [pathspec issue #81 -- GitIgnoreSpec behaviors differ from git](https://github.com/cpburnz/python-pathspec/issues/81) (closed; unclear if fixed or closed without code change)
- [pathspec issue #89 -- fails to match directories without trailing slash](https://github.com/cpburnz/python-pathspec/issues/89) (open, labeled Will Not Fix)
- [pathspec issue #93 -- pattern interpretation differences from Git](https://github.com/cpburnz/python-pathspec/issues/93) (open)
- [pathspec issue #129 -- re-includes files under excluded directory](https://github.com/cpburnz/python-pathspec/issues/129) (open)
- [pathspec issue #131 -- ReDoS in regex translation](https://github.com/cpburnz/python-pathspec/issues/131) (open)
- [excitoon/gitignorefile on GitHub](https://github.com/excitoon/gitignorefile) (last release 2022-09-04)
- [gitignorefile source -- _IgnoreRule.match()](https://raw.githubusercontent.com/excitoon/gitignorefile/master/gitignorefile/__init__.py)
- [BurntSushi/ripgrep -- ignore crate gitignore.rs](https://github.com/BurntSushi/ripgrep/blob/master/crates/ignore/src/gitignore.rs) (matched_stripped reference design)
- [ignore crate docs on docs.rs](https://docs.rs/ignore/latest/ignore/)
- [elifarley/file-matcher-python](https://github.com/elifarley/file-matcher-python) (cross-library gitignore test corpus)
- [ruff issue #17392 -- gitignore fidelity bug](https://github.com/astral-sh/ruff/issues/17392) (analogous bug on ignore-crate lineage)
- Existing research: `/home/jessica/source/house-lint/.claude/worktrees/27/design/research/2026-08-20-gitignore-style-exclusion-inclusion/research.md`
