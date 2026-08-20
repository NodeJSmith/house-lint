# Design: Built-in Token Defaults for HSL101

**Date:** 2026-08-20
**Status:** approved
**Scope-mode:** hold
**Research:** design/research/2026-08-20-linter-token-vocabulary-defaults/research.md

## Problem

Adding a new token type to house-lint's spec token detection (HSL101) requires editing every
project's `pyproject.toml`. A linter version bump should be the only mechanism needed — the tool
should ship its own vocabulary and let users extend it, not force every consumer to maintain a
copy.

## Goals

- Selecting HSL101 with zero token configuration produces findings from built-in families.
- Adding a new built-in token family (e.g. KI-NNN) requires only a house-lint version bump, not
  per-project config changes.
- Users can add custom token families that stack on top of built-ins.

## Non-Goals

- Full replacement mode (`replace-tokens = true`) — defer until a real use case surfaces.
- Config-file inheritance (`extend` a base pyproject.toml) — a separate concern per the
  ESLint/Ruff precedent (see research brief).
- Traceability in output (showing which family matched a finding) — nice-to-have, not core.
- Per-family suppression (`ignore-tokens`) — defer until a real collision surfaces. No current
  consumer defines custom tokens, so no collision exists to suppress.

## User Scenarios

### Developer: upgrades house-lint on an existing project

- **Goal:** get KI-NNN detection without changing config
- **Context:** project already uses HSL101 (enabled via `extend-select`) with manual token
  families configured under `[tool.house-lint.rules.HSL101]`

#### Transparent upgrade

1. **Bumps house-lint version in `pyproject.toml`**
   - Sees: no config errors, no new config keys needed
   - Decides: nothing — the upgrade is transparent
   - Then: next lint run detects KI-NNN tokens alongside existing custom families

### Developer: adds house-lint to a new project

- **Goal:** get spec token detection with minimal config
- **Context:** new project, no existing house-lint config

#### Zero-config HSL101

1. **Adds `extend-select = ["HSL101"]` to `pyproject.toml`**
   - Sees: no `ConfigError` about missing tokens
   - Decides: nothing further needed
   - Then: lint run detects AC, FR, NFR, WP, T, KI tokens from built-in families

## Functional Requirements

- **FR#1** HSL101 ships three built-in token families: `"spec"` (AC, FR, NFR, WP), `"task"` (T),
  and `"known-issues"` (KI), each with fixed matching rules.
- **FR#2** When HSL101 is selected and no user `tokens` are configured, the built-in families are
  the active vocabulary — no `ConfigError` is raised.
- **FR#3** When HSL101 is selected and user `tokens` are configured, the final vocabulary is the
  union of built-in families and user-defined families.
- **FR#4** The `hash` field is replaced by `separator` (`"none"`, `"hash"`, `"hash-optional"`,
  `"dash"`, `"dash-optional"`). The regex generator uses this to build the separator portion of
  the token pattern.
- **FR#5** The `"known-issues"` built-in family matches `KI-NNN` with separator `"dash"`,
  digits 1-4, no suffix.
- **FR#6** The `"spec"` built-in family matches AC, FR, NFR, WP tokens with separator
  `"hash-optional"`, digits 1-4, suffix `"optional-lower-alpha"`.
- **FR#7** The `"task"` built-in family matches T tokens with the same rules as `"spec"` plus
  `not_followed_by_time = true`.

## Edge Cases

- Explicit `tokens = []` in config: continues to raise `ConfigError` ("must contain 1 to 32
  families") as it does today. An explicitly empty array is a misconfiguration, not "no user
  tokens" — omitting the `tokens` key entirely is how to use only built-ins.
- A user supplies 32 custom families (the previous maximum): the merged tuple exceeds
  `MAX_TOKEN_FAMILIES` (32) once built-ins are added. `ConfigError` at merge step 2 with a
  message indicating the combined count exceeds the limit. This is a minor breaking change — the
  effective user cap drops to 32 minus active built-ins (29 with all three built-ins active).
- Existing projects that configure `hash` in their token families: `ConfigError` on the unknown
  key `hash` — this is a breaking change, but only 3 projects use house-lint and none define
  custom tokens.

## Acceptance Criteria

- **AC#1** `extend-select = ["HSL101"]` with no `[tool.house-lint.rules.HSL101]` table produces
  findings for AC, FR, NFR, WP, T, and KI tokens in comments, docstrings, and filenames.
- **AC#2** Adding `tokens = [{prefixes = ["JIRA"], scopes = ["comments"], separator = "dash",
  min_digits = 1}]` alongside built-in families causes both JIRA-NNN and built-in tokens to be
  detected.
- **AC#3** `uv run pytest` passes with all new and updated tests.
- **AC#4** `uv run pyright` passes (strict mode, scoped to `src/`).
- **AC#5** `uv run ruff check .` passes.

## Key Constraints

- The `hash` field is removed entirely, not deprecated. No backward-compatibility shim — the 3
  projects using house-lint do not define custom tokens.
- Built-in family definitions live in `config.py` as module-level constants, not in a separate
  data file — consistent with `DEFAULT_INCLUDE`.
- The detector (`spec_tokens.detect`) must not change its signature or acquire awareness of
  built-in vs. user-defined families. The merge happens entirely in `config.py`.

## Dependencies and Assumptions

- No external dependencies. All changes are internal to house-lint.
- Assumes the 3 current consumers of house-lint do not define custom token families at all
  (confirmed by the user), so the `hash` removal and built-in merge are non-breaking in practice.

## Architecture

The merge logic follows the **additive vocabulary layering** pattern from cspell/Vale/codespell
(see research brief, Pattern 3): built-in families are always active, user families add on top.

### Data model changes

`TokenFamily` gains:
- `separator: str = "none"` — replaces `hash`, values: `"none"`, `"hash"`, `"hash-optional"`,
  `"dash"`, `"dash-optional"`

`TokenFamily.hash` is removed.

### Built-in family definitions

Three module-level constants in `config.py`:

```python
BUILTIN_SPEC = TokenFamily(
    prefixes=("AC", "FR", "NFR", "WP"),
    scopes=("comments", "docstrings", "filenames"),
    separator="hash-optional",
    min_digits=1,
    max_digits=4,
    suffix="optional-lower-alpha",
)

BUILTIN_TASK = TokenFamily(
    prefixes=("T",),
    scopes=("comments", "docstrings", "filenames"),
    separator="hash-optional",
    min_digits=1,
    max_digits=4,
    suffix="optional-lower-alpha",
    not_followed_by_time=True,
)

BUILTIN_KNOWN_ISSUES = TokenFamily(
    prefixes=("KI",),
    scopes=("comments", "docstrings", "filenames"),
    separator="dash",
    min_digits=1,
    max_digits=4,
)

BUILTIN_TOKEN_FAMILIES: tuple[TokenFamily, ...] = (
    BUILTIN_SPEC, BUILTIN_TASK, BUILTIN_KNOWN_ISSUES,
)
```

### Config merge logic

The default value of `HSL101Options.tokens` changes from `()` to `BUILTIN_TOKEN_FAMILIES`. This
covers the `default_config` path (used when no `[tool.house-lint]` table exists, e.g. CLI-only
`--select HSL101`) — it produces an `HSL101Options` via the dataclass default, which now includes
built-in families. The `_rule_options` path (called from `load_config`) performs its own merge
logic described below, since it must handle user tokens.

In `_rule_options`, after parsing user `tokens`:

1. Concatenate: `BUILTIN_TOKEN_FAMILIES + user_families`.
2. Enforce `MAX_TOKEN_FAMILIES` upper bound on the final tuple. The existing raw-array check
   (`config.py:341`, "1 to 32") stays for the user-supplied `tokens` array alone — it validates
   user input before merging, while step 2 validates the merged result.

The resulting `HSL101Options.tokens` tuple is passed to `detect()` unchanged — the detector sees
a flat list with no concept of origin.

### Regex generation update

In `spec_tokens.py`, `_token_expression` replaces the `hash_part` logic:

```python
_SEP_REGEX = {
    "none": "",
    "hash": "#",
    "hash-optional": "#?",
    "dash": "-",
    "dash-optional": "-?",
}
sep_part = _SEP_REGEX[family.separator]
```

### Cache impact

`hash_effective_config` in `cache.py` uses `asdict(hsl101)`, which serializes all `TokenFamily`
fields. Adding `separator` (and removing `hash`) naturally changes the
serialized form, invalidating old cache entries. No cache code changes needed.

## Implementation Preferences

No specific implementation preferences — follow codebase conventions.

## Replacement Targets

- `TokenFamily.hash` field (`config.py:37`) — replaced by `separator`.
- `hash_part` computation in `_token_expression` (`spec_tokens.py:140`) — replaced by a
  `_SEP_REGEX` lookup on `family.separator`.
- `hash` validation in `_token_family` (`config.py:305-307`) — replaced by `separator`
  validation (membership check against the five legal values).
- `hash` in `_token_family`'s `_strict_keys` allowed set (`config.py:280`) — replaced by
  `separator`.
- `"HSL101 requires tokens"` error in `default_config` (`config.py:144-145`) and `load_config`
  (`config.py:437-438`) — removed; `HSL101Options.tokens` now defaults to
  `BUILTIN_TOKEN_FAMILIES`, so selecting HSL101 without user tokens produces a non-empty
  vocabulary via the dataclass default.

## Convention Examples

### Frozen dataclass config type

**Source:** `src/house_lint/config.py`

```python
@dataclass(frozen=True)
class HSL102Options:
    max_lines: int = DEFAULT_MAX_LINES
```

Note: `TokenFamily` should be marked `@dataclass(frozen=True, kw_only=True)` to prevent
positional-argument fragility. All existing call sites (production and test) must switch to
keyword arguments.

### Module-level default constant

**Source:** `src/house_lint/config.py`

```python
DEFAULT_INCLUDE = ("src", "tests", "scripts", "tools", "examples")
```

### Strict-keys validation pattern

**Source:** `src/house_lint/config.py`

```python
_strict_keys(
    table,
    {"prefixes", "scopes", "hash", "min_digits", "max_digits", "suffix",
     "case_sensitive", "not_followed_by_time"},
    name,
)
```

### ConfigError for invalid values

**Source:** `src/house_lint/config.py`

```python
if hash_mode not in {"forbidden", "optional", "required"}:
    raise ConfigError(f"{name}.hash is invalid")
```

## Alternatives Considered

**Paired replace/extend verbs (Ruff pattern):** `tokens`/`extend-tokens` mirroring
`select`/`extend-select`. Rejected because the vocabulary is dict-shaped (named families), not a
flat ID list. The additive-by-default model from cspell/Vale is a better fit. Full replacement
mode can be added later if needed.

**Per-key merge (ESLint pattern):** User config overrides individual built-in families by name.
Rejected as over-engineered for the current scale — only 3 built-in families, and the more common
operation is adding new families, not tweaking built-in ones.

**Extending `hash` field with new values:** Adding `"dash-optional"`, `"dash-required"` to the
existing `hash` field. Rejected — the field name becomes misleading. The chosen approach renames
the field to `separator` with five literal values covering exactly the legal states.

**Two-field split (`separator` + `separator_optional`):** Separate the character choice from
optionality as orthogonal axes. Rejected — creates one illegal combination
(`separator="none"` + `separator_optional=true`) requiring a dedicated validation branch and
test. A single enum field with five values erases that state entirely.

## Test Strategy

### Required Test Types

Unit tests for config parsing, merge logic, and regex generation (single-module changes). Integration
test for end-to-end CLI run (crosses CLI → config → detector boundary).

### Existing Tests to Adapt

- `tests/unit/test_config.py`:
  - `test_default_config_extend_select_still_enforces_hsl101_token_requirement` — update: HSL101
    with no tokens should now succeed (built-ins suffice); rename to reflect new behavior
  - `test_default_config_rejects_cli_hsl101_without_tokens` — update: same; rename
  - `test_hsl101_requires_tokens_only_when_selected` — update: the "selected without tokens"
    path now succeeds; rename
  - `test_token_family_is_typed_and_validated` — update: replace `hash` with `separator` in TOML
    fixture
- `tests/unit/rules/test_spec_tokens.py`:
  - All `TokenFamily(...)` calls switch to keyword arguments (`kw_only=True`). Two tests that
    passed explicit `hash`-related values update to use `separator`

### New Test Coverage

- Built-in family definitions: assert each family has the expected prefixes, scopes, separator
  settings (FR#1)
- Config merge: user tokens + built-ins union correctly (FR#3)
- Zero-config HSL101: selecting HSL101 with no tokens config succeeds (FR#2)
- Separator regex: `separator = "dash"` matches `KI-001` but not `KI001` or `KI#001` (FR#4, FR#5)
- Separator regex: `separator = "hash-optional"` matches both `FR#6` and `FR6` (FR#6)
- Separator validation: unknown `separator` value raises `ConfigError`
- Built-in `task` family time guard: `T05` is detected but `T05:30` is not (FR#7)
- Integration: CLI run with only `extend-select = ["HSL101"]` and no tokens config produces
  findings (AC#1)

### Tests to Remove

No tests to remove — existing tests are adapted, not deleted.

## Smoke Test

**Surface:** CLI terminal output.

**Scenario:** Create a Python file with `# AC1 FR#2a T05 KI-001 WP03` in a comment. Run
`house-lint check --select HSL101` (no token config in pyproject.toml).

**Success:** Exit code 1 with findings for AC1, FR#2a, T05, KI-001, and WP03 in the output.

## Documentation Updates

- `docs/configuration.md` (HSL101 token families section): rewrite the opening paragraph (which
  currently states "HSL101 has no default token vocabulary") to describe the three built-in
  families and zero-config usage. Document the `separator` field replacing
  `hash` and how user-defined families add on top of built-ins.
- `docs/configuration.md`: update the existing TOML example to use `separator` instead of `hash`.
  Update "at most 32 families" to reflect the effective cap accounting for active built-ins.
- `README.md`: update the HSL101 TOML example (currently shows `hash = "optional"`) to use
  `separator`. Remove or rewrite the claim at line 74 that "HSL101 requires
  at least one token family whenever you select it" — this is reversed by FR#2.
- `docs/rules.md`: update "Select HSL101 and configure one or more token families" to reflect
  that built-in families are active by default. Update the `hash` field reference. Revise the
  source-compatibility table row that lists "Hard-coded vocabulary" under HSL101's Dropped
  column — this design reintroduces built-in defaults, so the framing needs updating. Also
  check whether the adjacent "Generalized" column text needs adjusting (vocabulary is now
  built-in-plus-configured, not purely configured).

## Impact

### Changed Files

- **modify** `src/house_lint/config.py` — add `separator` to `TokenFamily` (replacing `hash`);
  mark `TokenFamily` `kw_only=True`; add `BUILTIN_TOKEN_FAMILIES` constants; update
  `_token_family` validation; update `_rule_options` merge logic; relax "requires tokens"
  checks; update `_strict_keys` allowed sets
- **modify** `src/house_lint/rules/spec_tokens.py` — replace `hash_part` with `sep_part` in
  `_token_expression`
- **modify** `tests/unit/test_config.py` — update 4 existing tests; add new tests for merge
  logic and separator validation
- **modify** `tests/unit/rules/test_spec_tokens.py` — update `TokenFamily` construction in all
  tests; add separator-specific tests
- **modify** `tests/unit/test_suppressions.py` — convert positional `TokenFamily(...)` to keyword
- **modify** `tests/integration/test_cli.py` — add integration test for zero-config HSL101
- **modify** `docs/configuration.md` — document built-in families, new fields
- **modify** `README.md` — update HSL101 example and remove "requires tokens" claim
- **modify** `docs/rules.md` — update HSL101 description, hash references, and
  source-compatibility table

### Behavioral Invariants

- All existing HSL001-HSL004 and HSL102-HSL103 behavior is unchanged.
- HSL900 suppression-pragma behavior is unchanged.
- Cache key computation continues to use `asdict(hsl101)` — no contract change.
- The detector protocol (`Detector.__call__` signature) is unchanged.

### Blast Radius

Any project carrying a `[tool.house-lint.rules.HSL101]` table is affected whether or not HSL101
is currently selected — `_rule_options` validates unconditionally. The 3 projects using house-lint
do not define custom tokens, so the `hash` removal is non-breaking in practice. Projects that
have `extend-select = ["HSL101"]` without token config will go from `ConfigError` to working — a
positive change.

## Open Questions

None — all decisions resolved during discovery.
