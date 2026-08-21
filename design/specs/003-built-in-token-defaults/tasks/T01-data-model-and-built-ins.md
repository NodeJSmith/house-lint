---
task_id: "T01"
title: "Replace hash field with separator and add built-in families"
status: "done"
depends_on: []
implements: ["FR#1", "FR#2", "FR#3", "FR#4"]
---

## Summary

Reshape `TokenFamily` to replace the `hash` field with the new `separator` enum, mark the
dataclass `kw_only=True`, define the three built-in token family constants, and change
`HSL101Options.tokens` default from `()` to `BUILTIN_TOKEN_FAMILIES`. Update `_token_family`
validation and `_rule_options` merge logic so that selecting HSL101 without user tokens works
(built-ins suffice) and user tokens concatenate with built-ins.

## Target Files

- modify: `src/house_lint/config.py`
- modify: `tests/unit/test_config.py`

## Prompt

In `src/house_lint/config.py`:

1. On `TokenFamily` (line 34): add `kw_only=True` to the `@dataclass` decorator. Remove the
   `hash: str = "forbidden"` field (line 37). Add `separator: str = "none"` in its place.

2. After `TokenFamily`, define the three built-in family constants and the tuple (see design doc
   `## Architecture → Built-in family definitions` for exact values):
   - `BUILTIN_SPEC` — prefixes AC/FR/NFR/WP, separator `"hash-optional"`, digits 1-4, suffix
     `"optional-lower-alpha"`
   - `BUILTIN_TASK` — prefix T, same as spec + `not_followed_by_time=True`
   - `BUILTIN_KNOWN_ISSUES` — prefix KI, separator `"dash"`, digits 1-4
   - `BUILTIN_TOKEN_FAMILIES` tuple of the three

3. Change `HSL101Options.tokens` default from `()` to `BUILTIN_TOKEN_FAMILIES` (line 47).

4. In `_token_family` (line 272): update `_strict_keys` allowed set — replace `"hash"` with
   `"separator"`. Replace the `hash_mode` validation (lines 305-307) with a `separator`
   membership check against `{"none", "hash", "hash-optional", "dash", "dash-optional"}`. Update
   the `return TokenFamily(...)` call (lines 321-330) to pass `separator=` instead of the old
   positional `hash_mode`.

5. In `_rule_options` (line 333): after parsing user tokens, implement the merge:
   `BUILTIN_TOKEN_FAMILIES + user_families`, then enforce `MAX_TOKEN_FAMILIES` upper bound on the
   merged tuple.

6. Remove the `"HSL101 requires tokens when selected"` checks in `default_config` (lines
   144-145) and `load_config` (lines 437-438).

In `tests/unit/test_config.py`:

- Rename and update the three tests that assert `ConfigError` for HSL101 without tokens — they
  should now assert success with built-in families:
  - `test_default_config_extend_select_still_enforces_hsl101_token_requirement`
  - `test_default_config_rejects_cli_hsl101_without_tokens`
  - `test_hsl101_requires_tokens_only_when_selected`
- Update `test_token_family_is_typed_and_validated` — replace `hash = "optional"` with
  `separator = "hash-optional"` in the TOML fixture. The merge order is
  `BUILTIN_TOKEN_FAMILIES + user_families`, so the user-configured family moves from `tokens[0]`
  to `tokens[3]` (after the 3 built-ins). Update both assertions: `tokens[3].prefixes == ("AC",)`
  and `tokens[3].separator == "hash-optional"`.
- Add new tests:
  - Assert each built-in family has expected prefixes, scopes, separator (FR#1)
  - Assert selecting HSL101 with no user tokens succeeds and produces built-in families (FR#2)
  - Assert user tokens + built-ins union correctly (FR#3 via AC#2 shape)
  - Assert merged tuple exceeding `MAX_TOKEN_FAMILIES` raises `ConfigError`
  - Assert unknown `separator` value raises `ConfigError`

## Focus

- `_rule_options` (config.py:333) currently initializes `tokens: tuple[TokenFamily, ...] = ()`
  locally and always passes it explicitly to `HSL101Options(...)` — it never falls through to the
  dataclass default. The merge logic must replace that local initialization with the
  `BUILTIN_TOKEN_FAMILIES + user_families` concatenation.
- The `kw_only=True` change means the `return TokenFamily(...)` call in `_token_family` (line
  321) must switch from positional to keyword arguments.
- `test_suppressions.py` also constructs `TokenFamily` positionally — that's T03's scope, not
  this task's. This task only touches `test_config.py`.
- The existing raw-array check (`if not token_values or len(token_values) > MAX_TOKEN_FAMILIES`)
  at config.py:341 stays for the user-supplied tokens array alone. The new upper-bound check on
  the merged tuple is a separate check after concatenation.

## Verify

- [ ] FR#1: `BUILTIN_SPEC`, `BUILTIN_TASK`, `BUILTIN_KNOWN_ISSUES` exist in `config.py` with
  correct prefixes, scopes, and separator values matching the design doc
- [ ] FR#2: `default_config(cli_extend_select=("HSL101",))` returns a `LintConfig` with
  `hsl101.tokens` containing the three built-in families (no `ConfigError`)
- [ ] FR#3: User tokens + built-ins union correctly — a config with user-defined families
  produces `hsl101.tokens` containing both built-in and user families
- [ ] FR#4: `TokenFamily` has a `separator` field replacing `hash`; `_token_family` validates
  separator against the five legal values
