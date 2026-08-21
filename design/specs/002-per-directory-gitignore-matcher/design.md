# Design: Per-Directory Gitignore Matcher

**Date:** 2026-08-20
**Status:** archived
**Scope-mode:** hold
**Research:** design/research/2026-08-20-gitignore-style-exclusion-inclusion/research-brief.md

## Problem

house-lint silently skips files that git would lint when nested `.gitignore` files use directory-only negations. `pathspec`'s aggregate `GitIgnoreSpec.match_file()` classifies directory-only patterns from pattern text rather than accepting a caller-supplied `is_dir`, and its internal priority system does not implement git's last-match-wins rule. `GitIgnoreSpec.from_lines(("**", "!**/")).match_file("src")` returns `True` (still ignored), while git reports `.gitignore:2:!**/` re-including `src` and descends into it. house-lint asks pathspec exactly that question at `_traversable_dirs` when deciding whether to prune a directory, so it prunes a subtree git walks and every file underneath vanishes from the scan — indistinguishable from a clean run.

The earlier guarantee that "the divergence always errs toward over-linting, never toward silently skipping" was falsified, and that guarantee was the entire justification for the current architecture. `pathspec` issue #89 is labeled Will Not Fix, so the fix is architectural.

## Goals

- Fix the under-linting bug: house-lint must never silently skip a file git would lint due to directory-only negation handling.
- Simplify `discovery.py`: delete the flatten/prefix/compensate pipeline that exists solely to work around pathspec's flattened matching model.
- Delete the compensation machinery and replace it with a cleaner per-directory evaluation that maps directly to git's own model.

## Non-Goals

- Chasing the over-linting divergence specifically. If the new approach also fixes it, accept the fix; do not expand scope to pursue it.
- Full gitignore reimplementation beyond what house-lint already covers.
- Replacing pathspec entirely — it stays as the glob-to-regex compiler.
- Performance optimization — correctness is the goal. Performance is expected to be comparable and can be measured after. The gitignore side's ancestor-exclusion memoization (previously an incidental side effect of the now-deleted `combined_gitignore_spec_cache`) is not replaced with a dedicated cache; the fused stack-build function re-walks the ancestor chain per file processed during any scan — walked or explicit-path — with each level's compiled pattern tuple served from `own_matcher_cache`. This is an accepted trade-off under the correctness-first scope — the existing `excluded_ancestor_cache` for `builtin_spec`/`exclude_spec` stays as-is (see Architecture), and any per-ancestor gitignore verdict cache can be added later if profiling shows a need.
  **Update (PR #29 review):** that need surfaced. A review finding on the fused stack-build function noted the per-file re-walk becomes quadratic-ish in directories with many files at depth, and a synthetic benchmark confirmed it: 2,000 files under 30 nested `.gitignore`-bearing directories took 28.9s before caching versus 3.3s after (200 files/20 levels: 1.05s → 0.21s). `_directory_gitignore_context` (see Architecture) now caches the ancestor-exclusion verdict and matcher stack per directory — every file sharing a directory reuses one walk instead of repeating it — closing the escape hatch this Non-Goal reserved.

## User Scenarios

### Developer: runs house-lint on a project with nested `.gitignore` files

- **Goal:** get findings for every Python file git would lint
- **Context:** a project where a root `.gitignore` uses `**` with `!**/` to exclude everything but re-include directories, and a nested `.gitignore` re-includes files inside

#### Silent under-linting (the bug)

1. **Runs `house-lint check`**
   - Sees: zero findings for files under the re-included directory
   - Decides: assumes the code is clean
   - Then: findings are silently hidden — the linter failed at its only job

#### Correct behavior (after fix)

1. **Runs `house-lint check`**
   - Sees: findings for every file git would lint, including files under directories re-included by directory-only negations
   - Decides: reviews and addresses the findings
   - Then: no silent gaps in coverage

## Functional Requirements

- **FR#1** When a directory-only negation pattern (e.g. `!**/`, `!sub/`) re-includes a directory that git descends into, house-lint must also descend into it and lint files underneath.
- **FR#2** A directory-only pattern must only match when the candidate is confirmed to be a directory by the filesystem (known from `os.walk`'s directory/file classification, or from `Path.is_dir()` for explicit paths), not inferred from pattern text.
- **FR#3** Pattern precedence must follow git's last-match-wins rule: the last matching pattern in the `.gitignore` file determines the outcome, with deeper `.gitignore` files overriding shallower ones.
- **FR#4** A negation cannot re-include a file whose parent directory was excluded — the pruning invariant must hold (git's documented rule: "It is not possible to re-include a file if a parent directory of that file is excluded").
- **FR#5** Explicit paths (`house-lint check src/file.py`) and walked paths must reach the same verdict for the same file under the same `.gitignore` configuration.
- **FR#6** `--no-gitignore` must skip all nested `.gitignore` file reads entirely — no filesystem I/O for ignore files when disabled.

## Edge Cases

- A directory whose name contains gitignore metacharacters (`sub[1]`, `!important`) must be matched literally, not as glob syntax. The current `_escape_gitignore_literal` handles this for the prefix pipeline; the replacement must handle it for directory-relative matching (pathspec's per-pattern regex compilation already escapes correctly when the pattern is parsed fresh from `.gitignore` lines — the escaping was only needed for the rewrite step).
- A symlinked `.gitignore` is not read (git uses `lstat` and skips symlinks). The existing `_load_gitignore_lines` check must be preserved.
- The `builtin_spec` (`.git/`, `.venv/`, etc.) and `exclude_spec` (configured excludes) are static root-anchored specs, conceptually different from per-directory `.gitignore` patterns. They continue to be evaluated via `_ignored()` as today — the per-directory stack applies only to gitignore patterns.
- An empty `.gitignore` file produces no patterns and must not affect matching.
- A `.gitignore` file that fails to parse (reported via `_load_gitignore_lines`) produces no patterns for that directory.

## Acceptance Criteria

- **AC#1** `test_negated_directory_pattern_re_includes_a_directory_git_descends_into` (currently strict xfail at `test_gitignore_parity.py:319`) passes without the xfail marker. (FR#1, FR#2)
- **AC#2** `MAX_KNOWN_DIRECTORY_NEGATION_DIVERGENCES` in `test_gitignore_fuzz.py:57` is set to `0` and the fuzz suite passes. (FR#1)
- **AC#3** All 29 parametrized parity scenarios pass across all three test functions (`test_discovery_matches_git_check_ignore`, `test_explicit_paths_match_git_check_ignore`, `test_explicit_directory_arguments_match_git_check_ignore`). (FR#3, FR#4, FR#5)
- **AC#4** The fuzz suite's three distributions all pass within their documented ceilings. (FR#3)
- **AC#5** The harness self-test (`test_harness_detects_a_real_divergence`) continues to pass. (regression guard)
- **AC#6** `uv run ruff check .` and `uv run pyright` pass. (code quality)
- **AC#7** `uv run pytest` passes (full test suite, not just integration). (regression guard)
- **AC#8** The functions `_prefix_pattern`, `_escape_gitignore_literal`, `_strip_unescaped_trailing_whitespace`, `_collapse_double_star_run`, `_normalize_contents_glob`, and the constant `IGNORE_EVERYTHING` are deleted from `discovery.py`. (simplification goal)
- **AC#9** If `test_negated_directory_pattern_does_not_re_include_nested_directories` (`test_gitignore_parity.py:287`) also starts passing, its strict xfail marker is removed in the same change; if it still fails, it is left unchanged. (over-linting xfail governance)
- **AC#10** `pyproject.toml` pins `pathspec>=1.0,<2` (lower bound at the first release providing `patterns.gitignore.spec`; upper-bound cap on the undocumented-internals dependency). (dependency safety)

## Key Constraints

- Do not shell out to git for file discovery (CLAUDE.md constraint).
- Do not introduce new dependencies — pathspec stays, used differently (individual pattern compilation rather than aggregate spec matching).
- `pathspec`'s `GitIgnoreSpecPattern` is used below the documented API surface (`.include`, `.pattern`, `.match_file()` on individual pattern objects). These are `__slots__`-based attributes on a `RegexPattern` subclass (not `@dataclass`-decorated, but structurally stable) and appear stable, but this is not the "parse a spec, call `match_file`" contract. `is_anchored` is derived textually from `.pattern` (collapsing consecutive `**` runs itself, per `_is_anchored_pattern`) rather than from `.regex`'s compiled-regex source text — the earlier regex-text-sniffing approach depended on the literal string pathspec's compiler happens to emit, a deeper, undocumented reliance on pathspec's internal formatting than mere attribute existence, with no exception to catch it if that formatting ever drifted. `_match_patterns`'s prefix-ambiguity guard still relies on the `_DIR_MARK` named regex group inside `.match_file()`'s returned match object (see `discovery.py`'s import comment); that reliance is unchanged by this fix. Mitigated by pinning (`pathspec>=1.0,<2` in `pyproject.toml`) so a breaking major bump can't silently reach end users; the lower bound is set at 1.0 because `patterns.gitignore.spec` (source of `GitIgnoreSpecPattern` and `_DIR_MARK`) doesn't exist before that release.
- The `_normalize_contents_glob` transformation (rewriting trailing `/**` to `/**/*`) is currently applied at spec-build time via `_spec_for_lines`. With the per-directory approach, this normalization must happen when parsing each directory's `.gitignore` lines, before individual patterns are compiled. The transformation itself is still needed — it prevents `build/**` from matching the `build` directory itself, which would cause pruning that blocks negations underneath.

## Dependencies and Assumptions

- **pathspec API stability**: The per-pattern `match_file()`, `.include`, and `.pattern` attributes on `GitIgnoreSpecPattern` are structural (`__slots__`-based attributes on `RegexPattern`), not incidental. A pathspec major version bump could break them. Acceptable risk — the attributes have been stable across releases, and the alternative (reimplementing gitwildmatch glob-to-regex) is far worse.
- **pathspec issue #93** (wildmatch divergences: `foo**/bar` matching `foobar`, bracket-class edge cases) affects glob-to-regex compilation and would be inherited by the new approach. Out of scope — these are pre-existing and the parity suite would catch regressions if they ever manifest.
- **pathspec issue #131** (ReDoS in regex translation): crafted `**` chains can cause pathological regex behavior. Affects all approaches that keep pathspec. There is no timeout, `.gitignore` line-count cap, or per-scan cap on the number of distinct `.gitignore` files read — `MAX_DISCOVERED_FILES` bounds matched `.py` files only, not directories walked or gitignore files parsed. house-lint's threat model is trusted input only (the developer's own repos, or repos they've chosen to clone and lint), not a defense against adversarial `.gitignore` trees. Out of scope for this change; not being actively monitored by any mechanism in the code.

## Architecture

### Current state

`discovery.py` uses a hybrid approach: traversal-based directory pruning during `os.walk` combined with flattened, root-anchored `pathspec.GitIgnoreSpec` matching. The flatten/prefix/compensate pipeline (`_escape_gitignore_literal`, `_strip_unescaped_trailing_whitespace`, `_collapse_double_star_run`, `_normalize_contents_glob`, `_prefix_pattern`, all at lines 115-242) exists solely to make pathspec work for nested `.gitignore` files by rewriting each pattern into a root-anchored equivalent. `_combined_gitignore_spec` (526-587) walks the ancestor chain, prefixes each nested `.gitignore`'s lines, and builds one root-anchored `GitIgnoreSpec`. It short-circuits to `IGNORE_EVERYTHING` when an ancestor is already excluded.

### Proposed change

Replace `GitIgnoreSpec`'s aggregate matching with house-lint's own per-directory evaluator. Keep `pathspec` only for what it does correctly: compiling gitignore glob patterns into Python regexes via `GitIgnoreSpecPattern`.

**Per-directory pattern data**: Each directory's `.gitignore` produces a `tuple[tuple[GitIgnoreSpecPattern, bool], ...]` — a tuple of `(pattern, is_dir_only)` pairs. Directory-only detection is trivial: the raw pattern text (after stripping a leading `!`) ends with `/`. This is a type alias, not a dataclass — the existing `_ignored()` free-function pattern (`discovery.py:264-281`) is the convention for matching logic, and a single-field frozen dataclass adds indirection without buying anything.

**`_match_patterns(patterns, relative_path, is_dir) -> bool | None`**: Module-level free function. Tri-state — `True` (ignored), `False` (whitelisted/negated), `None` (no opinion). `relative_path` is the candidate's path relative to the matcher's owning directory (e.g., for patterns from `src/.gitignore`, probing `src/a/sub/` passes `a/sub`). Iterates patterns in **reverse** (last match wins). For each pattern, calls `pattern.match_file(probe)` where `probe` is `relative_path + "/"` if `is_dir` else `relative_path`. A directory-only pattern (`is_dir_only=True`) is only eligible when `is_dir=True` — if `is_dir=False`, it is skipped even if the regex would match. When a pattern matches: if `pattern.include` is `True`, return `True` (ignored); if `False`, return `False` (whitelisted). If no pattern matches, return `None`.

Directories with no `.gitignore`, empty `.gitignore`, or unparsable `.gitignore` produce an empty pattern tuple — `_match_patterns` on an empty tuple always returns `None`, so no special-case branch is needed.

**Stack evaluation**: Replace `_combined_gitignore_spec` with a method that builds a stack of per-directory pattern tuples, one per directory from root to the current directory. Each matcher is probed with the candidate's path **relative to that matcher's owning directory** — not a bare entry name. When the walker is inside `src/a/` evaluating child `sub`, the probes are: `src/a`'s matcher (if any) receives `sub`, `src`'s matcher receives `a/sub`, root's matcher receives `src/a/sub`. This is how git works: each `.gitignore`'s patterns are anchored relative to the directory that contains the `.gitignore` file. The evaluator walks the stack from innermost to outermost: the first matcher with an opinion wins (git's precedence: deeper `.gitignore` overrides shallower).

This relative-path threading is what makes slash-containing patterns in non-root `.gitignore` files work correctly. For example, `src/.gitignore` with `a/**/` compiles to a regex that matches `a/sub/` but not bare `sub/`. When the walker evaluates `src/a/sub`, it probes `src`'s matcher with `a/sub/` (relative to `src`), which correctly matches. Without this, the pattern would silently fail to match and the parity suite would catch the regression.

**Pruning invariant**: Preserved by the walker itself — if a directory was pruned at `_traversable_dirs`, nothing beneath it is ever visited, so the pruning invariant (negation cannot re-include under an excluded parent) holds without `IGNORE_EVERYTHING`. The difference is that the pruning decision is now correct because the evaluator knows `!**/` is a directory-only negation and applies it when `is_dir=True`.

**`_has_excluded_ancestor`**: Stays as-is. It handles `builtin_spec` and `exclude_spec` (static root-anchored specs, not per-directory). These are conceptually different from `.gitignore` patterns and continue to be evaluated independently.

**`_ignored()` contract change**: The current `_ignored(root, path, *specs, is_dir)` receives the combined gitignore spec as one of `*specs` alongside `builtin_spec` and `exclude_spec`. After this change, `_ignored()` is scoped to the two static specs only — it no longer receives a gitignore spec. The three call sites (`discovery.py:398-410`, `427-436`, `663-670`) that currently pass `combined_gitignore_spec` into `_ignored` change to: `_ignored(root, path, builtin_spec, exclude_spec, is_dir=...) or stack_ignored(...)`, where `stack_ignored` is the new per-directory stack evaluation. This keeps `_ignored` narrowly scoped while the stack evaluator handles gitignore patterns through its own tri-state logic.

**Explicit-path code path**: `_consider`'s file and directory branches currently call `_combined_gitignore_spec` to get a single spec. They will instead call the stack evaluator — but with an additional ancestor-exclusion guard that the walk path gets for free from `_traversable_dirs`'s directory-by-directory pruning.

For walked paths, the pruning invariant (FR#4) is structural: `_traversable_dirs` checks each directory as a directory before descending, so a file under an excluded directory is never visited. For explicit paths, this structural protection is absent — `house-lint check src/generated/foo.py` reaches straight into the stack evaluator without going through directory-by-directory pruning. The current code handles this via `_combined_gitignore_spec`'s `IGNORE_EVERYTHING` short-circuit: it walks each ancestor, checks whether it's excluded by the patterns accumulated so far, and returns a match-everything spec if so.

The replacement fuses "build the matcher stack" and "check whether an ancestor is already excluded" into one function, mirroring what `_combined_gitignore_spec` does today. The stack-build function walks root-to-leaf; at each ancestor directory A, it probes A **as a directory** against the matchers built from A's ancestors only (root through A's parent) — A's own `.gitignore`, even if one exists, is never consulted when deciding whether A itself is pruned, mirroring `_traversable_dirs`'s rule that "real git never reads ignore files inside a directory it never descends into." If A is ignored-as-a-directory, the function stops: the file is excluded regardless of any negation, even one in the same `.gitignore` file (scenario: `{"": ["src/generated/", "!src/generated/foo.py"]}`). If A is not excluded, A's own `.gitignore` matcher is folded in and the walk continues to the next level.

Built this way, the walked path and the explicit path call the **same** stack-build function, and `_traversable_dirs`'s walk-time pruning becomes a redundant-but-useful fast skip rather than a second source of truth. This eliminates the open question — no separate `_has_gitignore_excluded_ancestor` method is needed. The parity suite's `test_explicit_paths_match_git_check_ignore` and `test_explicit_directory_arguments_match_git_check_ignore` catch any divergence between walked and explicit paths.

**Caching**: Per-directory compiled pattern tuples cached by directory path (`own_matcher_cache`). The combined-spec cache and spec-by-lines cache are deleted along with the flattening logic.

**Update (PR #29 review) — `directory_gitignore_context_cache`**: The fused stack-build-and-check function originally re-walked the ancestor chain on every call (once per file), with only each level's pattern tuple served from `own_matcher_cache`. A review finding plus a synthetic benchmark (see Non-Goals) showed this degrading badly at scale, since every file in a directory recomputes an identical ancestor verdict and stack. The ancestor-walk half of that function was split out into `_directory_gitignore_context(directory) -> (excluded_as_dir, stack)`, cached per directory on `directory_gitignore_context_cache: dict[Path, tuple[bool, _GitignoreStack]]`. `_is_gitignore_excluded` now calls this cached helper and only repeats the final candidate-vs-stack match per file — the part that actually varies per file.

**`_normalize_contents_glob`**: This transformation is still needed — it prevents `build/**` from matching the `build` directory itself. Applied when parsing each directory's `.gitignore` lines, before individual `GitIgnoreSpecPattern` objects are compiled from them.

### What gets deleted

- `_escape_gitignore_literal` (115-123)
- `_strip_unescaped_trailing_whitespace` (126-150)
- `_collapse_double_star_run` (153-168) and `_DOUBLE_STAR_RUN` regex (22)
- `_normalize_contents_glob` (171-191) and `_CONTENTS_GLOB` regex (20) — the transformation moves into the matcher builder; the standalone function is deleted
- `_prefix_pattern` (194-242) and `_GITIGNORE_METACHARS` regex (19)
- `IGNORE_EVERYTHING` constant (17)
- `_combined_gitignore_spec` method (526-587) — replaced by per-directory stack evaluation
- `_spec_for_lines` method (589-618) — no longer needed
- `combined_gitignore_spec_cache` and `spec_by_lines_cache` fields on `_FileSelector`
- `reported_spec_failures` field — error reporting moves to the matcher builder

### What gets added

- `_match_patterns(patterns, relative_path, is_dir) -> bool | None` free function (tri-state matching against a single directory's compiled pattern tuple)
- A function to build a pattern tuple from `.gitignore` lines (applies `_normalize_contents_glob` inline, compiles via `GitIgnoreSpecPattern`)
- Fused stack-build-and-check method on `_FileSelector` that walks root-to-leaf, checking each ancestor's exclusion status before folding in its matcher — serves both walked and explicit paths
- `own_matcher_cache: dict[Path, tuple[...]]` field on `_FileSelector` (caches compiled pattern tuples per directory)

## Implementation Preferences

- Keep `pathspec` for glob-to-regex compilation. Do not reimplement gitwildmatch.
- Use `GitIgnoreSpecPattern` from `pathspec` — parse lines via `GitIgnoreSpec.from_lines()` and extract the `.patterns` list, or compile individual patterns. The `.match_file()` method on individual patterns returns `RegexMatchResult | None`.
- `include=True` on a `GitIgnoreSpecPattern` means the pattern is an ignore (not negated). `include=False` means the pattern is a negation (`!`-prefixed). This is the opposite of what the name suggests — verified empirically.
- Probe paths with trailing `/` for directories: `pattern.match_file("src/")` correctly matches directory-only patterns like `**/`, while `pattern.match_file("src")` does not. This is the key insight that makes per-pattern evaluation work.
- Follow the existing `_FileSelector` dataclass pattern — add fields to it rather than creating a separate class hierarchy.

## Replacement Targets

| Target | Replaced by | Action |
|---|---|---|
| `_prefix_pattern` + helpers (115-242) | `_match_patterns` free function with per-directory pattern tuples | Delete outright |
| `IGNORE_EVERYTHING` constant (17) | Walker pruning invariant (already exists) | Delete outright |
| `_combined_gitignore_spec` method (526-587) | Per-directory stack evaluation | Replace |
| `_spec_for_lines` method (589-618) | Matcher builder | Replace |
| `combined_gitignore_spec_cache` field | `own_matcher_cache` | Replace |
| `spec_by_lines_cache` field | (not needed) | Delete |
| `reported_spec_failures` field | Error reporting in matcher builder | Replace |

## Convention Examples

### Frozen dataclass pattern

**Source:** `src/house_lint/discovery.py`

```python
@dataclass(frozen=True)
class DiscoveryResult:
    files: tuple[Path, ...]
    files_skipped: int = 0
    errors: tuple[LintError, ...] = ()
    resolved_paths: Mapping[Path, Path] = field(default_factory=lambda: dict[Path, Path]())
```

### Tri-state return with None for "no opinion"

**Source:** `src/house_lint/discovery.py` (`_ignored` returns bool; the new `_match_patterns` free function extends this to tri-state with `None`)

```python
def _ignored(root: Path, path: Path, *specs: GitIgnoreSpec, is_dir: bool) -> bool:
    relative = path.relative_to(root).as_posix()
    probe = f"{relative}/" if is_dir else relative
    return any(spec.match_file(probe) for spec in specs)
```

### Cache-per-directory pattern

**Source:** `src/house_lint/discovery.py` (`_own_gitignore_lines` method)

```python
def _own_gitignore_lines(self, directory: Path) -> tuple[str, ...]:
    if directory in self.own_gitignore_lines_cache:
        return self.own_gitignore_lines_cache[directory]
    ignore = directory / ".gitignore"
    def on_error(operation: str, message: str) -> None:
        self.errors.append(self._error(ignore, "traversal", operation, message))
    lines = _load_gitignore_lines(ignore, on_error)
    self.own_gitignore_lines_cache[directory] = lines
    return lines
```

### Parity test scenario structure

**Source:** `tests/integration/test_gitignore_parity.py`

```python
Scenario(
    "directory-form negation cancels an earlier unanchored ignore",
    {"": ["cache", "!cache/"]},
    ("src/a.py", "src/cache/c.py"),
),
```

## Alternatives Considered

**Option B: Swap in `gitignorefile` package.** Handles `is_dir` correctly (confirmed from source), but effectively dormant (last release 2022), has its own negation bugs (9 xfails in cross-library test corpus), API mismatch with house-lint's walker (owns tree-walking internally), and lists only Python 3.6-3.12. Taking a dependency on an unmaintained package to fix a correctness bug is the wrong trade.

**Option C: Minimal patch at the pruning decision.** Add a secondary `is_dir`-aware check after pathspec says "ignored." Fixes the symptom with ~20-30 lines, but leaves the compensation machinery in place, creates a dual-evaluation-path maintenance hazard, and does not address the architectural root cause. If the goal included "simplify the architecture," this does not.

## Test Strategy

### Required Test Types

Integration tests (existing parity + fuzz suites — purpose-built for this exact change). Unit tests (new, for `_match_patterns` tri-state logic independent of filesystem traversal).

### Existing Tests to Adapt

- `tests/integration/test_gitignore_parity.py:303-334`: Remove the strict xfail marker from `test_negated_directory_pattern_re_includes_a_directory_git_descends_into`. Re-evaluate the over-linting xfail at line 275 — if per-directory evaluation also fixes it, remove that xfail too.
- `tests/integration/test_gitignore_fuzz.py:57`: Set `MAX_KNOWN_DIRECTORY_NEGATION_DIVERGENCES = 0`. Remove `_is_known_directory_negation_defect` and its references if the defect class no longer exists. Update the divergence rate ceilings if the rates change.
- `tests/unit/test_discovery.py`: Tests for deleted functions (`_prefix_pattern`, `_escape_gitignore_literal`, `_strip_unescaped_trailing_whitespace`, `_collapse_double_star_run`, `_normalize_contents_glob`) must be removed. Tests for `_ignored` and `_combined_gitignore_spec` must be updated or replaced to test the new `_match_patterns` and fused stack-build-and-check.

### New Test Coverage

- Unit tests for `_match_patterns()`: directory-only pattern with `is_dir=True` (matches), directory-only pattern with `is_dir=False` (skipped), last-match-wins with mixed patterns, negation winning over ignore, no-opinion (returns `None`), empty pattern tuple. (FR#2, FR#3)
- Unit test for the fused stack-build-and-check: innermost matcher wins, outermost fallback, no opinion from any, ancestor exclusion short-circuits before reading descendant's `.gitignore`. (FR#3, FR#4)
- The integration parity and fuzz suites already cover FR#1, FR#4, FR#5 comprehensively.

### Tests to Remove

- Unit tests for `_prefix_pattern` and its helpers (see Replacement Targets).
- Unit tests for `_normalize_contents_glob` as a standalone function (the transformation is inlined into the pattern-tuple builder).

## Smoke Test

Run the full parity suite and check the under-linting xfail flips:

```
uv run pytest tests/integration/test_gitignore_parity.py -v
```

The test `test_negated_directory_pattern_re_includes_a_directory_git_descends_into` must pass (not xfail). All other parity scenarios must continue passing.

Then run the fuzz suite and confirm the divergence ceiling holds at 0:

```
CI=1 uv run pytest -s tests/integration/test_gitignore_fuzz.py
```

The `adversarial` distribution must show 0 under-linting divergences.

## Documentation Updates

- `docs/configuration.md` lines 49-66: Remove the under-linting bullet. If the over-linting divergence is also fixed, remove that bullet too and simplify the divergence section. Regenerate the divergence-rate table from `CI=1 uv run pytest -s tests/integration/test_gitignore_fuzz.py`.
- `CLAUDE.md` gitignore gotcha section: Update to reflect the fix. Remove the stale note about the "always errs toward over-linting" guarantee (already flagged as false). Update the "Two divergences are known" text.

## Impact

### Changed Files

- modify `src/house_lint/discovery.py` — delete flatten/prefix/compensate pipeline, add `_match_patterns` free function and fused stack-build-and-check, update `_FileSelector` caches and methods, update `_ignored()` call sites
- modify `tests/integration/test_gitignore_parity.py` — remove xfail markers (under-linting; over-linting if it also passes)
- modify `tests/integration/test_gitignore_fuzz.py` — set divergence ceiling to 0, remove known-defect classification if no longer needed
- modify `tests/unit/test_discovery.py` — remove tests for deleted functions, add tests for `_match_patterns`, update tests for stack evaluation
- modify `docs/configuration.md` — remove under-linting bullet, regenerate divergence rates
- modify `CLAUDE.md` — update gitignore divergence notes
- modify `pyproject.toml` — add version pin `pathspec>=1.0,<2`

### Behavioral Invariants

- All 29 parity scenarios must continue passing across all three test functions (walk, explicit file, explicit directory).
- The harness self-test must continue proving the comparison can fail in both directions.
- `--no-gitignore` must continue skipping all `.gitignore` file I/O.
- `builtin_spec` and `exclude_spec` behavior must be unchanged — these are not part of the per-directory stack.
- `files_skipped` counting: one skip per pruned directory (not per file inside it).
- Symlinked `.gitignore` files are not read.

### Blast Radius

Limited to house-lint's file discovery. No external consumers — `discover_files` is the primary public API, and its return type (`DiscoveryResult`) is unchanged. Other public names (`DiscoveryError`, `resolve_project`) are not touched by this change. The change is purely internal to how ignore decisions are made during traversal.

## Open Questions

(None — all questions resolved during research and discovery.)
