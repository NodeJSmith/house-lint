# Known Issues

Durable issues discovered during orchestration that were intentionally not fixed in this run.

## KI-001: Root-directory `.gitignore` combine-error reports path as "." instead of ".gitignore"

Status: resolved — fixed during known issues walkthrough
Run: 106
Source: T02
Observed in: T02
Affected files:
- src/house_lint/discovery.py

Issue:
`_own_matcher` (discovery.py:630) reports post-normalization `_build_patterns` reparse
failures via `self._error(directory, "traversal", "combine", error)` (discovery.py:650).
`_error` (discovery.py:711) computes the reported path as
`path.relative_to(self.root).as_posix()`. For every non-root directory this yields the
directory's relative path (e.g. `"src"`, matching
`test_nested_gitignore_normalization_failure_is_reported_as_a_combine_error`), but when
`directory == self.root` (the root's own `.gitignore`), `self.root.relative_to(self.root)`
is `Path(".")`, so the reported `LintError.path` is `"."` rather than the human-readable
`".gitignore"` that the file's other error path (`_gitignore_error`, discovery.py:71,
used by `_load_gitignore_lines`'s pre-validation) reports for the same file. The error IS
still surfaced to the user with a message explaining what failed — only the `path` field is
uninformative for this one narrow case.

Trigger requires a root `.gitignore` line that parses successfully in its raw form (passes
`_load_gitignore_lines`'s pre-validation) but fails only after `_build_patterns`'s
trailing-`/**`/whitespace normalization rewrite — the docstring at discovery.py:639-643
calls this scenario "rare, but... stays live." No test exercises the root-directory case
specifically; the nested-directory version of this error path is tested and correct.

Why deferred:
Cosmetic path-formatting fix in a rarely-triggered fallback error-reporting branch, not a
bug in the fused stack-build-and-check pipeline T02 was scoped to build. The fix (special-
casing `directory == self.root` in `_error`, or passing an explicit label through
`_own_matcher`) is a small addition beyond T02's approved scope, not a defect in what T02
implemented.

Recommended follow-up:
In `_error` (or at the `_own_matcher` call site), special-case `directory == self.root` so
the reported path reads `".gitignore"` instead of `"."`, matching `_gitignore_error`'s
convention for the same file.

Acceptance criteria:
- A root `.gitignore` line that fails only after `_build_patterns`'s normalization rewrite
  produces a `LintError` with `path == ".gitignore"`, not `"."`.
- A regression test covering the root-directory combine-error case (mirroring
  `test_nested_gitignore_normalization_failure_is_reported_as_a_combine_error` but for
  `self.root`) is added and passes.

## KI-002: `_match_patterns`'s prefix-ambiguity guard has no dedicated unit test

Status: resolved — fixed during known issues walkthrough
Run: 106
Source: impl-review
Observed in: T02 (commit 8991e76)
Affected files:
- tests/unit/test_discovery.py

Issue:
`_match_patterns`'s prefix-ambiguity guard (the `match_end < len(probe)` / `_DIR_MARK` check,
`discovery.py:271-272`) — the logic that stops an anchored-but-ambiguous pattern like `src/sub`
(no trailing slash) from false-positive-matching as a directory-boundary prefix of a deeper probe
such as `src/sub/deep` — is exercised only indirectly, through the parity suite's `"src/sub"`-shaped
scenarios and the fuzz suite's corner-body/negation-owner combinations (both showing 0/1500
divergences). No hand-written unit test isolates this specific edge case the way the other
`is_anchored` cases do (e.g. `test_build_patterns_marks_a_collapsed_double_star_run_as_not_anchored`).

Why deferred:
Not a correctness gap — the differential fuzz/parity coverage already proves the guard works
(0% divergence). This is a coverage-locality improvement (make the reasoning in the docstring
verifiable without running the fuzz suite), not a defect, and doesn't block shipping this feature.

Recommended follow-up:
Add a unit test in `tests/unit/test_discovery.py` that directly calls `_match_patterns` with an
anchored, no-trailing-slash pattern (e.g. `src/sub`) against a deeper probe (`src/sub/deep`) and
asserts the guard correctly returns `None` (no opinion) rather than a false match.

Acceptance criteria:
- A new unit test isolates the `_DIR_MARK`/prefix-ambiguity guard and passes.

## KI-003: No root-level test case for trailing-whitespace-trim symmetry

Status: resolved — fixed during known issues walkthrough
Run: 106
Source: impl-review
Observed in: T02 (commit 8991e76)
Affected files:
- tests/unit/test_discovery.py

Issue:
`_build_patterns` now applies `_trailing_whitespace_trimmed` uniformly to every directory's lines,
including the root `.gitignore` (via `_own_matcher(self.root, self.root_gitignore_lines)`).
Previously, only nested `.gitignore` lines went through the equivalent
`_strip_unescaped_trailing_whitespace` step; root lines went straight to
`GitIgnoreSpec.from_lines()`. This is a genuine, well-justified correctness fix, but the existing
backslash-parity tests (`test_nested_gitignore_pattern_preserves_an_escaped_trailing_space`, the
even/odd backslash-run parity scenarios) all exercise the nested-directory case only, not the root
`.gitignore` case specifically.

Why deferred:
Low risk since both paths share the same `_build_patterns` code — this is a symmetry/coverage gap,
not a known-broken path. Does not block shipping.

Recommended follow-up:
Add a root-level test case mirroring the existing nested-directory backslash-parity tests, applied
via `_own_matcher(self.root, ...)` instead of a nested directory.

Acceptance criteria:
- A new unit test exercises the backslash-parity trimming behavior for the root `.gitignore`
  specifically and passes.

## KI-004: AC#2's literal wording is stale relative to what T03 actually implemented

Status: open
Run: 106
Source: impl-review
Reason not fixed now: out-of-scope
Observed in: T03 (commit c540a26)
Affected files:
- design/specs/002-per-directory-gitignore-matcher/design.md

Issue:
AC#2 reads: "`MAX_KNOWN_DIRECTORY_NEGATION_DIVERGENCES` in `test_gitignore_fuzz.py:57` is set to
`0` and the fuzz suite passes." T03's own task file explicitly authorized removing the constant
entirely once its only consumer (`_is_known_directory_negation_defect` and the now-vacuous ceiling
test) was deleted, and the implementation followed that path — the constant no longer exists in
`test_gitignore_fuzz.py`, replaced by per-`Distribution.max_divergence_rate = 0.0`. Functionally
equivalent (arguably cleaner — no unused constant left behind) but AC#2's grep-based literal
wording no longer matches the code.

Why deferred:
Documentation/AC-wording mismatch, not a code defect. The substantive intent (0% divergence
ceiling for the known defect class) is satisfied and verified (0/1500 in the fuzz suite).

Recommended follow-up:
Update AC#2's wording in `design.md` to describe the actual mechanism (`Distribution.max_divergence_rate
== 0.0` for all three distributions, verified via the fuzz suite) rather than the now-removed
named constant.

Acceptance criteria:
- design.md's AC#2 text accurately describes the current fuzz-ceiling mechanism.

## KI-005: design.md's `IgnorePatterns` Architecture description is stale (2-tuple vs shipped 3-tuple)

Status: open
Run: 106
Source: cross-file-review
Reason not fixed now: out-of-scope
Observed in: T02 (commit 8991e76)
Affected files:
- design/specs/002-per-directory-gitignore-matcher/design.md
- design/specs/002-per-directory-gitignore-matcher/tasks/T01-match-patterns-function.md

Issue:
design.md's Architecture section and T01's task file both specify
`IgnorePatterns = tuple[tuple[GitIgnoreSpecPattern, bool], ...]` — a 2-element
`(pattern, is_dir_only)` pair. The shipped implementation (`src/house_lint/discovery.py:181`)
uses a 3-element `tuple[GitIgnoreSpecPattern, bool, bool]` — `(pattern, is_dir_only, is_anchored)`.
The third field (`is_anchored`) drives real, load-bearing logic in `_match_patterns`: it decides
whether an unanchored pattern gets truncated to its last path segment, and gates the `_DIR_MARK`/
prefix-ambiguity guard KI-002 documents. This is a justified, necessary extension (confirmed by
0/1500 fuzz divergence) to correctly implement FR#3 (last-match-wins) and avoid a prefix-ambiguity
false-positive class the design doc didn't anticipate — not a bug, but the design doc's
"Architecture" description of `IgnorePatterns` no longer matches what shipped.

Why deferred:
Documentation drift in the design doc, not a code defect. The extension itself is correct and
tested; only the design doc's Architecture section description is stale.

Recommended follow-up:
Update design.md's Architecture section (and, if kept for reference, T01's Prompt) to describe the
3-tuple with `is_anchored`, matching what `discovery.py` actually implements.

Acceptance criteria:
- design.md's Architecture section accurately describes `IgnorePatterns` as the 3-tuple shipped
  in `discovery.py`.

## KI-006: `_consider`'s directory and file branches duplicate the same three-way ignore check

Status: resolved — fixed during known issues walkthrough
Run: 106
Source: clean-code
Observed in: pre-existing (predates 06e2d9b, the base commit for this feature); carried through
unchanged in T02 (commit 8991e76), which swapped the gitignore-matching call inside the shared
shape from `_combined_gitignore_spec` to `_gitignore_excluded` without changing the shape itself.
Affected files:
- src/house_lint/discovery.py

Issue:
`_FileSelector._consider`'s directory branch (discovery.py:456-464) and file branch
(discovery.py:479-485) both evaluate the identical three-way condition —
`self._has_excluded_ancestor(resolved.parent) or _ignored(self.root, resolved, self.builtin_spec,
self.exclude_spec, is_dir=...) or self._gitignore_excluded(resolved.parent, resolved.name,
is_dir=...)` — differing only in the `is_dir` literal passed through and in what happens on a
non-match (walk the directory vs. continue to dedup/budget checks). This is a lazy-checker-shaped
Copy-Paste Duplication finding: the same three-call boolean expression appears twice with only one
parameter varying, and could be extracted into a single `_is_path_ignored(self, resolved, *,
is_dir)` helper called from both branches.

Why deferred:
This is pruning-invariant logic — the exact code path the parity suite (`test_gitignore_parity.py`)
and fuzz suite (`test_gitignore_fuzz.py`) exist to pin against real `git check-ignore`, and each
branch carries its own extensive, branch-specific justification comment (e.g. the directory
branch's `resolved != self.root` guard and the "discovery root reached from include" explanation
have no file-branch equivalent). The duplication predates this feature branch entirely — T02's
approved scope was replacing the flattening pipeline's matching mechanism, not consolidating this
pre-existing structural shape — and extracting a shared helper here touches the one part of the
module where a mechanical-looking refactor has the most room to silently change which `is_dir`
value or which post-match branch fires. Not something to fix opportunistically inside a clean-code
pass.

Recommended follow-up:
Extract the shared three-way condition into a `_is_path_ignored(self, resolved: Path, *, is_dir:
bool) -> bool` helper on `_FileSelector`, called from both the directory and file branches of
`_consider`, with each branch's distinct surrounding comments preserved at their original call
sites. Run the full parity and fuzz suites (`uv run pytest tests/integration/test_gitignore_parity.py`
and `CI=1 uv run pytest -s tests/integration/test_gitignore_fuzz.py`) after the extraction to
confirm zero divergence is unchanged.

Acceptance criteria:
- `_consider`'s directory and file branches call a single shared helper for the three-way ignore
  check instead of repeating it inline.
- All 29 parity scenarios and all three fuzz distributions continue passing with 0% divergence,
  matching the current baseline.

## KI-007 (not a bug): leading `**/` patterns correctly get `is_anchored=True`

Status: resolved -- not a bug (issue #30)
Run: N/A -- found while addressing PR #29 review feedback, not during an orchestration run
Source: PR review response (surfaced while verifying a codex-connector finding on the same
function, `_is_anchored_pattern`)
Observed in: pre-existing -- `_is_anchored_pattern` has only ever implemented the textual-slash
check plus the consecutive-`**`-run collapse (`_DOUBLE_STAR_RUN`); it has never special-cased a
leading `**/` on its own.
Affected files:
- src/house_lint/discovery.py

Original issue:
git treats any pattern beginning with `**/` as unanchored -- matching at any depth -- regardless
of what follows the leading `**/`, even when a later segment is itself complex or fused (verified
against real `git check-ignore`: `**/x/**foo` ignores `nested/deep/x/yfoo/a.py`, not just
`x/yfoo/a.py`). `_is_anchored_pattern` only recognizes the narrower case where the *entire*
pattern collapses to a bare `**` via `_DOUBLE_STAR_RUN` (consecutive `**` segments). It has no
rule for "the pattern starts with `**/` and something else follows that isn't part of the same
run" -- for a pattern like `**/x/**foo`, the textual slash between `x` and `**foo` makes
`_is_anchored_pattern` return `True` (anchored), so `_match_patterns` threads the full
multi-segment path instead of truncating to the last segment.

Resolution (issue #30):
The described behavior is correct, not a bug. `_is_anchored_pattern` returning `True` for
patterns like `**/x/**foo` and `**/sub/deep.py` is pragmatically correct because `is_anchored`
controls path truncation in `_match_patterns`, not gitignore's semantic concept of anchoring.
These patterns need the full multi-segment path passed to `match_file` for correct matching --
pathspec's compiled regex already handles the any-depth matching via a `(?:.+/)?` prefix baked
into the regex for every leading-`**/` pattern.

Marking them as `is_anchored=False` would truncate the probe to the last path segment (e.g.
`deep.py` instead of `sub/deep.py`), which can't match the pattern's multi-segment structure.
This was verified empirically: adding `if core.startswith("**/"): return False` broke both the
existing `**/sub/deep.py` parity scenario and the new `**/x/**foo` scenario, while the original
code passes all parity tests including the new scenario.

A parity scenario (`"leading '**/' with a fused interior segment matches at any depth"`) was
added to pin the correct behavior differentially against real git. Unit tests document the
`is_anchored=True` classification and why it is correct. The docstring on `_is_anchored_pattern`
was updated to explain the semantic divergence between gitignore's "anchored" concept and
house-lint's use of `is_anchored` for path truncation.
