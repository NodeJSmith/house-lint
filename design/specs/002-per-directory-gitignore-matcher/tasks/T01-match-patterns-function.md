---
task_id: "T01"
title: "Add _match_patterns free function and pattern-tuple builder"
status: "done"
depends_on: []
implements: ["FR#2", "FR#3"]
---

## Summary

Add the foundational matching logic that all subsequent tasks build on: a `_match_patterns` free function that evaluates a candidate path against a single directory's compiled pattern tuple, and a builder function that parses `.gitignore` lines into that tuple format. This task introduces no changes to the existing pipeline — it adds new code alongside it, so the existing tests continue to pass throughout.

## Target Files

- modify: `src/house_lint/discovery.py` — add `_match_patterns` free function, pattern-tuple type alias, and builder function
- modify: `tests/unit/test_discovery.py` — add unit tests for `_match_patterns`
- read: `design/specs/002-per-directory-gitignore-matcher/design.md` — Architecture section

## Prompt

Add the following to `src/house_lint/discovery.py`, after the existing `_ignored` function (line 281) and before the `_FileSelector` class (line 284):

1. **Type alias**: `IgnorePatterns = tuple[tuple[GitIgnoreSpecPattern, bool], ...]` — a tuple of `(pattern, is_dir_only)` pairs from a single `.gitignore` file. `is_dir_only` is `True` when the raw pattern text (after stripping a leading `!`) ends with `/`.

2. **`_match_patterns(patterns: IgnorePatterns, relative_path: str, is_dir: bool) -> bool | None`**: Module-level free function implementing tri-state gitignore matching for a single directory's patterns.
   - Iterate `patterns` in **reverse** (last pattern wins, per git's rule).
   - For each `(pattern, is_dir_only)`:
     - If `is_dir_only` and not `is_dir`: skip this pattern entirely.
     - Build `probe = relative_path + "/"` if `is_dir` else `relative_path`.
     - Call `pattern.match_file(probe)`. If result is not `None` (a match):
       - If `pattern.include` is `True`: return `True` (ignored).
       - If `pattern.include` is `False`: return `False` (whitelisted/negated).
   - If no pattern matched: return `None` (no opinion).

3. **`_build_patterns(lines: tuple[str, ...]) -> IgnorePatterns`**: Parse `.gitignore` lines into a pattern tuple.
   - Apply `_normalize_contents_glob` to each non-comment, non-blank line (the transformation that rewrites trailing `/**` to `/**/*`). Since `_normalize_contents_glob` will be deleted as a standalone function in T02, inline the transformation logic here: use the existing `_CONTENTS_GLOB` regex and the same substitution `r"/**/*\1"`.
   - Parse via `GitIgnoreSpec.from_lines(normalized_lines)` and extract `.patterns`.
   - For each `GitIgnoreSpecPattern`, determine `is_dir_only`: strip a leading `!` from `pattern.pattern`, then check if the result ends with `/`.
   - Return a tuple of `(pattern, is_dir_only)` pairs.
   - On parse failure (`TypeError`, `ValueError`, `re.error`): return an empty tuple (error reporting is the caller's responsibility, matching the existing `_load_gitignore_lines` pattern).

Note: `_build_patterns` deliberately uses `GitIgnoreSpec.from_lines()` and extracts `.patterns` rather than trying to construct `GitIgnoreSpecPattern` objects directly — the `from_lines` path is the only reliable entry point for gitignore-syntax parsing.

Add unit tests to `tests/unit/test_discovery.py`:

- `_match_patterns` with a directory-only pattern and `is_dir=True` → matches
- `_match_patterns` with a directory-only pattern and `is_dir=False` → skipped (returns `None`)
- `_match_patterns` with last-match-wins: `["*.py", "!a.py"]` → `a.py` returns `False`
- `_match_patterns` with negation winning: `["a.py", "!a.py"]` → returns `False`
- `_match_patterns` with empty tuple → returns `None`
- `_match_patterns` with file probe against non-directory-only pattern → matches
- `_build_patterns` with valid lines → correct tuple length and `is_dir_only` flags
- `_build_patterns` with unparsable lines → returns empty tuple

## Focus

- `include=True` on `GitIgnoreSpecPattern` means "this is an ignore pattern" (not negated). `include=False` means "this is a `!`-prefixed negation." Counterintuitive — verify in tests.
- Probe with trailing `/` for `is_dir=True`: `pattern.match_file("src/")` correctly matches directory-only patterns like `**/`, while `pattern.match_file("src")` does not.
- The `_CONTENTS_GLOB` regex at `discovery.py:20` and `_normalize_contents_glob` at line 171 are the reference for the inline normalization in `_build_patterns`. In T02, the standalone function will be deleted, but the regex may be kept or inlined.
- Follow the existing free-function pattern (`_ignored` at line 264) — no class, no method.
- No changes to existing behavior in this task. All existing tests must continue to pass.

## Verify

- [ ] FR#2: Unit test confirms `_match_patterns` skips a directory-only pattern when `is_dir=False` and matches when `is_dir=True`
- [ ] FR#3: Unit test confirms `_match_patterns` returns the result of the last matching pattern (reverse iteration)
