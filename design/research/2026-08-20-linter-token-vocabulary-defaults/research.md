---
topic: "linter built-in defaults + user customization for token vocabularies"
date: 2026-08-20
status: Draft
---

# Prior Art: Built-in Token Vocabulary Defaults in Linters

## The Problem

house-lint's HSL101 rule detects spec tokens (AC-NNN, FR-NNN, WP-NNN, etc.) in comments,
docstrings, and filenames. Currently it ships with zero built-in token families — every user must
configure the full vocabulary manually. Adding a new token type (like KI-NNN for known issues)
means editing every project's `pyproject.toml`. The goal is to ship built-in defaults so upgrading
the linter version automatically includes new token types, while still letting users customize.

The design question: when a user provides their own token config, does it replace or extend the
built-ins?

## How We Do It Today

HSL101Options has `tokens: tuple[TokenFamily, ...] = ()` — an empty default. Selecting HSL101
without configuring tokens raises ConfigError. TokenFamily has no name/ID field, so families are
anonymous positional entries. The detector (`spec_tokens.detect`) treats all families identically
regardless of origin. house-lint already has `select`/`extend-select`/`ignore`/`extend-ignore` for
rule-level selection (modeled after Ruff), but nothing analogous for the token vocabulary within a
rule.

## Patterns Found

### Pattern 1: Paired Replace/Extend Verbs for Flat Lists

**Used by**: Ruff (`select`/`extend-select`), flake8 (`select`/`extend-select`)

**How it works**: Two options per axis. The bare form (`select`) fully replaces defaults. The
`extend-` form adds to whatever defaults resolved to. Ruff computes precedence across CLI > local
config > inherited config.

**Strengths**: The option name itself signals intent (replace vs. extend). Both use cases are
explicitly supported. Well-understood in the Python linting ecosystem.

**Weaknesses**: Doubles the config surface (four options instead of two). Precedence gets
confusing once three or more layers stack (flake8 #284, #1687).

**Example**: https://docs.astral.sh/ruff/settings/

### Pattern 2: Per-Key Merge for Dict-Shaped Config

**Used by**: ESLint (`extends` + `rules`), commitlint, stylelint

**How it works**: A named baseline config supplies defaults. User config merges on top key-by-key:
specified keys override, unmentioned keys pass through. Turning off one entry uses a sentinel
(`"off"`, `null`, `0`) rather than requiring full redeclaration.

**Strengths**: Simpler than paired verbs — one merge operation. Naturally supports "turn off just
this one built-in" without redeclaring everything else.

**Weaknesses**: Needs a sentinel value for "off." Needs a separate knob for "discard all
built-ins at once."

**Example**: https://eslint.org/docs/latest/extend/shareable-configs

### Pattern 3: Additive Vocabulary Layering with Per-Entry Negation

**Used by**: cspell (dictionaries), Vale (vocabularies), codespell (dictionaries + ignore-words)

**How it works**: Built-in word/pattern lists are active by default with zero config. User adds
their own lists, and the runtime unions all active lists. Removing a specific built-in entry uses a
separate negation mechanism (cspell's `"!name"` prefix, Vale's `reject.txt`, codespell's
`-I`/`-L`). Users never need to enumerate built-in entries to add to them.

**Strengths**: Most natural for "named list of recognized tokens/words." Upgrading the tool
transparently adds new entries. Per-entry suppression avoids the "must redeclare everything to
remove one thing" trap.

**Weaknesses**: Needs clarity on "remove a whole category" vs. "suppress one entry." Traceability
of which list contributed a match requires thought.

**Example**: https://cspell.org/docs/dictionaries/custom-dictionaries ; https://vale.sh/docs/keys/vocab

### Pattern 4: Full Replace via Deny-list Rebuild

**Used by**: Pylint (`--disable=all --enable=X`)

**How it works**: No dedicated "replace" flag. The idiom is disable everything, then re-enable
exactly what you want.

**Strengths**: Minimal config surface — enable/disable are the only verbs.

**Weaknesses**: Less discoverable. Confusing once plugins add their own defaults (Pylint #2635).

**Example**: https://pylint.readthedocs.io/en/stable/user_guide/configuration/all-options.html

## Anti-Patterns

- **Three-tier precedence without clear resolution.** flake8 (#284) and Pylint (#2635) both have
  maintainer-acknowledged confusion from stacking tool built-in + plugin-contributed + user config.
  house-lint has no plugin system for tokens, so it can stay at a clean 2-tier model.
- **Any explicit value silently disables all defaults for that field.** Oxlint's nested-config
  behavior. Easy to implement accidentally ("if not None, skip defaults"). Produces
  action-at-a-distance surprises.
- **Requiring full redeclaration to remove one entry.** The exact pain point Vale's vocabulary
  design was built to avoid.

## Relevance to Us

house-lint's token vocabulary is structurally closest to **Pattern 3** (cspell/Vale/codespell) —
it's a named list of recognized token patterns, not a flat set of rule IDs. The vocabulary-linter
precedents unanimously converge on: built-ins active by default, user additions are additive
(union), per-entry removal via a separate mechanism.

house-lint already uses Pattern 1 (Ruff-style paired verbs) for rule-level selection. Extending
that same vocabulary to the token families within HSL101 would be consistent with house-lint's
existing UX. The key insight from the research is that both patterns can coexist: `tokens` (user
config) adds families on top of the built-in defaults (Pattern 3's additive layering), while a
separate mechanism handles suppression of specific built-in families.

The one architectural constraint: `TokenFamily` currently has no identity (no name field). Adding
a name gives built-in families a stable handle for per-entry negation, and also improves
traceability in lint output ("spec token AC1 in comment" could say which family matched).

## Recommendation

Adopt **Pattern 3** (additive vocabulary layering) as the primary model, consistent with
cspell/Vale/codespell — the tools that most closely match house-lint's "vocabulary of token
patterns" shape:

1. Ship built-in token families with stable names (e.g., `"spec"` for AC/FR/NFR/WP/T, `"known-issues"` for KI).
2. User-configured `tokens` entries **add** families on top of built-ins (union, not replace).
3. Per-family opt-out via a negation mechanism — either a `"!name"` prefix in an `ignore-tokens`
   list, or a per-family `enabled = false` flag.
4. Full replacement: an explicit `replace-tokens = true` flag (or naming the config key
   `only-tokens` to signal intent) for the rare case where a project wants zero built-ins.

This gives the "bump version, get KI-NNN" behavior without config changes, while still letting
projects customize. The 2-tier model (built-in + user) avoids the 3-tier precedence traps
documented in flake8/Pylint.

## Sources

### Reference implementations
- https://docs.astral.sh/ruff/settings/ — Ruff select/extend-select paired verbs
- https://flake8.pycqa.org/en/latest/user/violations.html — flake8 extend-select/extend-ignore
- https://cspell.org/docs/dictionaries/custom-dictionaries — cspell additive dictionary layering
- https://github.com/codespell-project/codespell — codespell built-in + ignore-words

### Documentation & standards
- https://eslint.org/docs/latest/use/configure/configuration-files — ESLint flat config merge
- https://eslint.org/docs/latest/extend/shareable-configs — ESLint extends + per-key override
- https://github.com/eslint/rfcs/blob/main/designs/2019-config-simplification/README.md — ESLint RFC on merge vs. replace tradeoff
- https://vale.sh/docs/keys/vocab — Vale vocabulary accept/reject design
- https://pylint.readthedocs.io/en/stable/user_guide/configuration/all-options.html — Pylint enable/disable
- https://commitlint.js.org/reference/configuration.html — commitlint extends + per-key merge
- https://oxc.rs/docs/guide/usage/linter/nested-config.html — Oxlint nested config (anti-pattern)

### Bug reports & design discussions
- https://github.com/pycqa/flake8/issues/284 — flake8 three-tier precedence confusion
- https://github.com/PyCQA/pylint/issues/2635 — Pylint plugin default override surprise
- https://eslint.org/blog/2025/03/flat-config-extends-define-config-global-ignores/ — ESLint flat-config extends reintroduction
