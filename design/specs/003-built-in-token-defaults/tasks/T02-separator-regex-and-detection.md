---
task_id: "T02"
title: "Update regex generation for separator field"
status: "planned"
depends_on: ["T01"]
implements: ["FR#4", "FR#5", "FR#6", "FR#7"]
---

## Summary

Replace the `hash_part` regex logic in `spec_tokens.py` with a `_SEP_REGEX` lookup dict that maps
the five `separator` values to their regex fragments. Update all `TokenFamily` construction in
`test_spec_tokens.py` to use keyword arguments and the new `separator` field. Add tests verifying
the three built-in families' matching behavior, including the `task` family's time guard.

## Target Files

- modify: `src/house_lint/rules/spec_tokens.py`
- modify: `tests/unit/rules/test_spec_tokens.py`
- modify: `tests/unit/test_suppressions.py`

## Prompt

In `src/house_lint/rules/spec_tokens.py`:

1. In `_token_expression` (line 134): replace the `hash_part` computation (line 140):
   ```python
   hash_part = {"forbidden": "", "optional": "#?", "required": "#"}[family.hash]
   ```
   with a `_SEP_REGEX` dict lookup (define the dict at module level):
   ```python
   _SEP_REGEX = {
       "none": "",
       "hash": "#",
       "hash-optional": "#?",
       "dash": "-",
       "dash-optional": "-?",
   }
   ```
   Use `sep_part = _SEP_REGEX[family.separator]` and replace `{hash_part}` with `{sep_part}` in
   the token regex assembly (line 147).

In `tests/unit/rules/test_spec_tokens.py`:

2. Convert all `TokenFamily(...)` calls to keyword arguments (required by `kw_only=True` from
   T01). The two calls that pass explicit `hash`-related positional values (lines 22-27 and 47)
   must use `separator=` instead:
   - `"optional"` → `separator="hash-optional"`
   - `"required"` → `separator="hash"`
   - Other calls that relied on the `hash` default (`"forbidden"`) → `separator="none"` (or omit
     since `"none"` is the default).

3. Add new tests:
   - `separator="dash"` matches `KI-001` but not `KI001` or `KI#001` (FR#5)
   - `separator="hash-optional"` matches both `FR#6` and `FR6` (FR#6)
   - `separator="dash-optional"` matches both `KI-001` and `KI001`
   - Built-in `task` family time guard: `T05` detected but `T05:30` is not (FR#7)

In `tests/unit/test_suppressions.py`:

4. Convert the two positional `TokenFamily(("T",), ("comments",), min_digits=2)` calls (lines 82
   and 114) to fully keyword form: `TokenFamily(prefixes=("T",), scopes=("comments",),
   min_digits=2)`.

## Focus

- The `_SEP_REGEX` dict should be a module-level constant, not defined inside the function — it's
  used by `_token_expression` which is called per-family per-file.
- `_content_pattern` and `_filename_pattern` are `@lru_cache`-decorated and take a `TokenFamily`
  as the key. `TokenFamily` is frozen (hashable), so the cache works. The new `separator` field
  doesn't change this.
- The time guard test for FR#7 should use the `BUILTIN_TASK` constant (imported from config) to
  test the actual built-in rather than constructing a manual `TokenFamily`.

## Verify

- [ ] FR#4: `_SEP_REGEX` dict maps all five separator values to correct regex fragments
- [ ] FR#5: A test confirms `separator="dash"` matches `KI-001` and rejects `KI001`/`KI#001`
- [ ] FR#6: A test confirms `separator="hash-optional"` matches both `FR#6` and `FR6`
- [ ] FR#7: A test confirms the built-in `task` family detects `T05` but not `T05:30`
