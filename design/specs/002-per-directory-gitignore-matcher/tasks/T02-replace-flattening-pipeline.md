---
task_id: "T02"
title: "Replace flattening pipeline with per-directory stack evaluation"
status: "planned"
depends_on: ["T01"]
implements: ["FR#1", "FR#4", "FR#5", "FR#6", "AC#1", "AC#3", "AC#6", "AC#8"]
---

## Summary

The core architectural change. Replace `_combined_gitignore_spec` with a fused stack-build-and-check method that walks root-to-leaf, checking each ancestor's exclusion status before folding in its matcher. Delete the entire flatten/prefix/compensate pipeline. Update `_ignored()` call sites to separate static specs from the new gitignore stack evaluation. Update `_FileSelector` caches. Remove the under-linting xfail marker so the parity suite passes cleanly. Remove unit tests for deleted functions. Add unit tests for the fused stack-build-and-check.

## Target Files

- modify: `src/house_lint/discovery.py` — delete compensation pipeline, add fused stack method, update call sites and caches
- modify: `tests/unit/test_discovery.py` — remove tests for deleted functions, add tests for stack evaluation
- read: `design/specs/002-per-directory-gitignore-matcher/design.md` — Architecture section
- modify: `tests/integration/test_gitignore_parity.py` — remove the under-linting xfail marker so the suite passes cleanly
- read: `src/house_lint/cli.py` — confirm no breakage to public imports

## Prompt

### Delete from `src/house_lint/discovery.py`:

1. **Constants and regexes** (at top of file):
   - `IGNORE_EVERYTHING` (line 17)
   - `_GITIGNORE_METACHARS` regex (line 19)
   - `_CONTENTS_GLOB` regex (line 20) — only if `_build_patterns` from T01 inlined the transformation; keep if T01 reuses it
   - `_DOUBLE_STAR_RUN` regex (line 22)

2. **Standalone functions** (lines 115-242):
   - `_escape_gitignore_literal` (115-123)
   - `_strip_unescaped_trailing_whitespace` (126-150)
   - `_collapse_double_star_run` (153-168)
   - `_normalize_contents_glob` (171-191) — the standalone function; the transformation logic now lives inline in `_build_patterns` from T01
   - `_prefix_pattern` (194-242)

3. **Methods on `_FileSelector`**:
   - `_combined_gitignore_spec` (526-587) — replaced by fused stack method
   - `_spec_for_lines` (589-618) — no longer needed

4. **Fields on `_FileSelector`**:
   - `combined_gitignore_spec_cache` (298-300)
   - `spec_by_lines_cache` (301-303)
   - `reported_spec_failures` (305-307)

5. **Update `_patterns` function** (245-261): The `_normalize_contents_glob` call at line 258 for excludes should be replaced with the inline equivalent or use `_build_patterns` if appropriate. The excludes are root-anchored static patterns, not per-directory — handle them separately from the gitignore stack.

### Add to `_FileSelector`:

1. **`own_matcher_cache: dict[Path, IgnorePatterns]`** — caches compiled pattern tuples per directory path. An empty tuple for directories with no/empty/unparseable `.gitignore`.

2. **Fused stack-build-and-check method** (e.g., `_gitignore_excluded`): Takes `(directory: Path, relative_path: str, is_dir: bool) -> bool`. Walks root-to-leaf through `self._ancestor_chain(directory)`:
   - At each ancestor A, probe A **as a directory** against the matchers built from A's ancestors only (root through A's parent) using `_match_patterns`. If the result is `True` (A is ignored-as-a-directory), return `True` — the candidate is excluded regardless of any negation.
   - If A is not excluded, fold in A's own `.gitignore` matcher (from `own_matcher_cache`, building via `_build_patterns` + `_own_gitignore_lines` on cache miss).
   - After walking all ancestors, probe the candidate (`relative_path`, `is_dir`) against the full stack, innermost to outermost (first opinion wins).
   - The root `.gitignore`'s patterns use root-relative paths. Non-root matchers use paths relative to their owning directory.
   - Respect `self.use_gitignore` — short-circuit to `False` (not excluded) when disabled.

3. **Update the three `_ignored()` call sites**:
   - `_consider` directory branch (around line 398-410): Change from `_ignored(self.root, resolved, self.builtin_spec, self.exclude_spec, self._combined_gitignore_spec(resolved.parent), is_dir=True)` to `_ignored(self.root, resolved, self.builtin_spec, self.exclude_spec, is_dir=True) or self._gitignore_excluded(resolved.parent, resolved.name, is_dir=True)`.
   - `_consider` file branch (around line 427-436): Same pattern — split static specs from gitignore stack.
   - `_traversable_dirs` (around line 663-670): Same pattern.

4. **Update `_walk`** (455-475): The current `combined_spec = self._combined_gitignore_spec(current_path)` call and its threading into `_consider` and `_traversable_dirs` changes. The fused stack method is called per-entry rather than per-directory — or, for performance parity, pre-build the ancestor stack once per directory and thread it.

### Update `tests/unit/test_discovery.py`:

- **Remove** tests for `_prefix_pattern` (around line 314-337) and `_normalize_contents_glob` (around line 340-356).
- **Remove** tests that reference `_combined_gitignore_spec`, `_spec_for_lines`, `spec_by_lines_cache`, or `reported_spec_failures` — update to test the new fused stack method.
- **Add** unit tests for the fused stack-build-and-check:
  - Innermost matcher wins over outermost
  - Outermost fallback when inner has no opinion
  - Ancestor exclusion short-circuits before reading descendant's `.gitignore`
  - `use_gitignore=False` returns `False` immediately

### Remove the under-linting xfail marker from `tests/integration/test_gitignore_parity.py`:

Remove the entire `@pytest.mark.xfail(strict=True, reason=...)` decorator (lines 303-318) from `test_negated_directory_pattern_re_includes_a_directory_git_descends_into`. The code change in this task fixes the underlying bug, so the test must now pass as a regular test. Without removing the marker, `strict=True` would report XPASS as a test failure, causing the entire parity suite to fail.

### Update `_load_gitignore_lines` docstring:

The docstring at line 80-88 references `_prefix_pattern` — update to reference `_build_patterns` instead, or remove the reference since the rewrite is no longer needed.

## Focus

- The fused stack-build-and-check is the most correctness-critical piece. It must mirror `_combined_gitignore_spec`'s ancestor-exclusion check: at each ancestor A, probe A as a directory against matchers from A's ancestors ONLY (not including A's own `.gitignore`). This is what prevents a self-negating pattern inside an excluded directory from resurrecting it.
- `_walk`'s current per-directory batching (`combined_spec` computed once, threaded to all files) should be preserved for the new stack evaluation where possible. The ancestor chain can be pre-walked once per directory.
- The `_patterns` function's `_normalize_contents_glob` call for excludes (line 258) is for the `exclude_spec`, which is root-anchored and stays as a `GitIgnoreSpec`. Keep the transformation for excludes — either inline it or keep the regex.
- `_own_gitignore_lines` and `_load_gitignore_lines` stay as-is — they handle I/O and error reporting.
- `_ancestor_chain` (477-493) stays as-is — it's used by both `_has_excluded_ancestor` and the new fused method.
- After this task, run `uv run pytest tests/integration/test_gitignore_parity.py -v` — all 29 parametrized scenarios should pass across all three test functions, and `test_negated_directory_pattern_re_includes_a_directory_git_descends_into` should pass as a regular test (xfail marker removed in this task).
- Run `uv run ruff check .` and `uv run pyright` after all changes.

## Verify

- [ ] FR#1: `uv run pytest tests/integration/test_gitignore_parity.py -v` passes — `test_negated_directory_pattern_re_includes_a_directory_git_descends_into` passes as a regular test (xfail marker removed in this task)
- [ ] FR#4: Unit test confirms ancestor exclusion short-circuits: a directory excluded by an ancestor's patterns is excluded regardless of negations in descendant `.gitignore` files
- [ ] FR#5: Both `test_explicit_paths_match_git_check_ignore` and `test_explicit_directory_arguments_match_git_check_ignore` pass across all 29 scenarios
- [ ] FR#6: Unit test confirms `use_gitignore=False` returns `False` from the fused stack method without reading any `.gitignore` files
- [ ] AC#1: `test_negated_directory_pattern_re_includes_a_directory_git_descends_into` passes as a regular test (xfail marker removed, test produces correct result)
- [ ] AC#3: All 29 parametrized parity scenarios pass across all three test functions
- [ ] AC#6: `uv run ruff check .` and `uv run pyright` pass
- [ ] AC#8: `grep -n '_prefix_pattern\|_escape_gitignore_literal\|_strip_unescaped_trailing_whitespace\|_collapse_double_star_run\|_normalize_contents_glob\|IGNORE_EVERYTHING' src/house_lint/discovery.py` returns no function definitions or constant assignments (only comments referencing old names are acceptable)
