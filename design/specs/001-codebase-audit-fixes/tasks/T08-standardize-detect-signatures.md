---
task_id: "T08"
title: "Standardize all 7 rule detect() signatures to accept options"
status: "planned"
depends_on: []
implements: ["FR#7", "AC#7"]
---

## Target Files

- modify: `src/house_lint/rules/llm_cruft.py`
- modify: `src/house_lint/rules/lazy_imports.py`
- modify: `src/house_lint/rules/type_checking_position.py`
- modify: `src/house_lint/rules/constants_position.py`
- modify: `src/house_lint/registry.py`
- modify: `tests/unit/rules/test_llm_cruft.py`
- modify: `tests/unit/rules/test_lazy_imports.py`
- modify: `tests/unit/rules/test_type_checking_position.py`
- modify: `tests/unit/rules/test_constants_position.py`

## Prompt

CLAUDE.md and the `Detector` Protocol in `src/house_lint/registry.py` (`__call__(self, source,
options, *, limit=None) -> list[CandidateFinding]`) both document a universal detector signature.
But 4 of the 7 rule `detect()` functions currently omit `options` entirely:
`llm_cruft.detect(source, *, limit=None)`, `lazy_imports.detect(source, *, limit=None)`,
`type_checking_position.detect(source, *, limit=None)`, `constants_position.detect(source, *,
limit=None)`. `registry.py`'s corresponding `_hsl001`, `_hsl002`, `_hsl003`, `_hsl004` wrapper
functions currently accept `options: object` and silently drop it before calling the real
detector — that's the gap being closed.

For each of the 4 rule files, change the signature to:

```python
def detect(source: SourceFile, options: object, *, limit: int | None = None) -> list[CandidateFinding]:
```

(matching `constants_position.py`'s and the others' actual body — just add the parameter; these
rules genuinely don't use `options` today, so the parameter stays unused in the body, which is
correct and intentional — the goal is signature consistency with the documented Protocol, not
adding new behavior.)

Then update `registry.py`'s 4 wrapper functions to pass `options` through instead of dropping it,
e.g.:

```python
def _hsl001(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    from .rules import llm_cruft

    return llm_cruft.detect(source, options, limit=limit)
```

Apply the same pattern to `_hsl002`, `_hsl003`, `_hsl004`.

Check whether ruff flags the now-unused `options` parameter in any of the 4 rule files (this
project's ruff config may or may not warn on unused function parameters) — if it does, this project
already has a convention for intentionally-unused parameters (check other files, e.g. whether
`exception_names.py`/`file_length.py`/`spec_tokens.py` name any unused parameters with a leading
underscore, or whether ruff is configured to ignore this for `detect()`-shaped functions). Match
whatever the existing convention is; if there isn't one, leave the parameter named `options`
un-prefixed and see if `uv run ruff check .` complains — fix only if it does.

**Update the 4 test files too — this is required, not optional.** Making `options` a required
positional parameter breaks every existing call site in these files that doesn't pass one. Confirmed
by grep: `tests/unit/rules/test_llm_cruft.py` (9 call sites), `tests/unit/rules/test_lazy_imports.py`
(1 call site), `tests/unit/rules/test_type_checking_position.py` (4 call sites),
`tests/unit/rules/test_constants_position.py` (6 call sites) — all currently call
`detect(SourceFile(path, path.parent))` or `detect(SourceFile(path, path.parent), limit=...)` with
no `options` argument. Update every one of these calls to pass `None` as the second positional
argument (e.g. `detect(SourceFile(path, path.parent), None)` and
`detect(SourceFile(path, path.parent), None, limit=10_000)`), matching how a rule with no typed
options is invoked. Grep each file yourself for `detect(` to find every call site — do not rely on
the counts above being exhaustive if the file has changed since this task was written.

## Verify

- [ ] FR#7: For each of the 7 rule files, read the full `detect()` signature (some span multiple
      lines — a single-line grep won't catch it) and confirm `options` is present as the second
      positional parameter.
- [ ] AC#7: `uv run pytest -q` reports all tests passing — this is the real check that every
      `detect()` call site across the 4 updated test files was found and fixed, not just the ones
      listed above.
- [ ] `uv run ruff check .` is clean.
- [ ] `uv run pyright` (strict, `src/` only) is clean.
