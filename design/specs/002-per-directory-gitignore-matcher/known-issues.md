# Known Issues

Durable issues discovered during orchestration that were intentionally not fixed in this run.

## KI-001: Root-directory `.gitignore` combine-error reports path as "." instead of ".gitignore"

Status: open
Run: 106
Source: T02
Reason not fixed now: out-of-scope
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
