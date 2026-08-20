# Context: Built-in Token Defaults for HSL101

## Problem & Motivation

house-lint's HSL101 rule detects spec tokens (AC-NNN, FR-NNN, etc.) in comments, docstrings, and
filenames, but currently ships with zero built-in token families. Every project must configure the
full vocabulary manually in `pyproject.toml`. Adding a new token type (like KI-NNN for known
issues) means editing every consumer's config. The goal is to ship built-in defaults so a version
bump is the only mechanism needed.

## Visual Artifacts

None.

## Key Decisions

1. **Additive-only vocabulary** — built-in families are always active; user-configured families
   add on top (union). No per-family suppression mechanism in v1 (deferred to Non-Goals).
2. **Single `separator` field** — replaces the old `hash` field with five literal values
   (`"none"`, `"hash"`, `"hash-optional"`, `"dash"`, `"dash-optional"`). Rejected alternatives:
   extending the `hash` name (misleading), and a two-field split (creates one illegal state).
3. **`kw_only=True` on `TokenFamily`** — prevents positional-argument fragility when fields
   change. All call sites switch to keyword arguments.
4. **`HSL101Options.tokens` defaults to `BUILTIN_TOKEN_FAMILIES`** — covers the `default_config`
   path (CLI-only `--select HSL101`, no pyproject.toml table). The `_rule_options` path performs
   its own merge (concatenation + cap enforcement).
5. **`hash` removed entirely** — no backward-compat shim. The 3 consumers don't define custom
   tokens.

## Constraints & Anti-Patterns

- Do NOT implement `ignore-tokens`, `name` field on `TokenFamily`, or any per-family suppression
  mechanism — these are Non-Goals.
- Do NOT implement full replacement mode (`replace-tokens = true`).
- The detector (`spec_tokens.detect`) must NOT change its signature or acquire awareness of
  built-in vs. user-defined families. The merge happens entirely in `config.py`.
- Built-in family definitions live in `config.py` as module-level constants, not a separate file.
- `_rule_options` validates the HSL101 table unconditionally, even when HSL101 is not selected.

## Design Doc References

- `## Architecture` — data model changes, built-in definitions, merge logic, regex update
- `## Replacement Targets` — exact locations of code being replaced
- `## Test Strategy` — existing tests to adapt, new coverage needed
- `## Documentation Updates` — docs/configuration.md, README.md, docs/rules.md changes
- `## Edge Cases` — explicit empty tokens, cap overflow, hash removal

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
