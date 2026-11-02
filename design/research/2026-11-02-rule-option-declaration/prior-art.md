---
topic: "How comparable linters declare per-rule options, tie them to rule definitions, and feed them into cache keys"
date: 2026-11-02
status: Draft
---

# Prior Art: per-rule option declaration and cache keys

## The Problem

house-lint adds a rule by editing several parallel structures: the rule catalog, the detector registry, an options dict in `selected_detector_inputs`, per-rule config parsing, and a cache hash with one parameter per rule. Milestone 005's D5 and Breakdown item 3 (#58) plan to replace this with rule-declared option types and rule-declared cache-hash contributions. The 2026-10-30 engine brief favoured one `Rule` declaration but didn't read how peers do options. This brief fills that gap from their source.

## Bottom Line

- **One declaration per rule is normal.** ESLint and oxlint hold metadata, options, and detector together. Biome nearly does. Ruff ties id, metadata, and detector through one table. Nobody hand-syncs separate lists: they use a macro, codegen, or plugin registration.
- **No peer lets a rule declare cache inputs.** Ruff and ESLint hash all settings, including options of disabled rules. Pylint, flake8, and oxlint have no result cache. So the "each rule declares its hash contribution" part of #58 has no precedent and solves a problem peers don't treat as one.
- **Filename dependence is handled by the key's structure.** Ruff keys entries by relative path, so no rule declares it.
- **Typed peers parse with a library house-lint doesn't have.** Ruff, Biome, and oxlint use serde. ESLint's generic JSON Schema loses types at the use site, and its own RFC documents defaults spreading into several places.

## How We Do It Today

- **Rule list:** `rule_catalog.RULES` holds metadata and enablement. `registry._DETECTORS` holds dispatch, and an import-time check keeps those two in sync. `config.selected_detector_inputs` holds a third list that nothing checks, so a missing entry silently drops the rule.
- **Options:** `HSL101Options`-`HSL103Options` live in `config.py`. `_rule_options` parses all three by hand. The rule modules import their types from `config`, and `registry` casts through adapters.
- **Cache:** `hash_effective_config` takes one parameter per rule and hashes all three option blocks whether or not the rule is enabled. It folds in the filename only when HSL101 has a `filenames` scope. `docs/configuration.md` documents hashing all of them as deliberate.

## Comparison

| | One declaration for id, metadata, detector, options type? | Who parses and validates options | Where defaults and types live | Options hashed for the cache | Extra per-rule cache inputs | Rule list sync |
|---|---|---|---|---|---|---|
| **Ruff** | No. A table links code to violation struct; options are a separate struct the rule doesn't name | serde on a central options struct, then hand-written `into_settings` per group. Typed | Options struct centrally; resolved `Settings` in the rule module; a field in `LinterSettings` | All settings, disabled rules included; `#[cache_key(ignore)]` opts out output-only knobs | None. Path is the entry key | Proc macro over `codes.rs` |
| **ESLint** | Yes (`meta` plus `create`); id is the registration key | Generic Ajv over `meta.schema`. Untyped at the use site | On the rule: `meta.schema`, `meta.defaultOptions` | Whole resolved per-file config, plus ESLint and Node versions | None | Plugin map |
| **Pylint** | Per checker, not per rule | Generic argparse-based manager over the `options` tuple | On the checker; read from a flat namespace | No result cache | n/a | `register()` per module |
| **flake8** | Entry point plus `add_options`/`parse_options` | Plugin, by hand | On the plugin; values stored as class state | No result cache found (two files searched) | n/a; parameter names declare what the engine passes | Entry points |
| **Biome** | Mostly. Macro plus `impl Rule { type Options }`; config side is generated | serde on a typed options struct | A separate crate, paired by naming convention | Unknown | Unknown | Codegen plus file discovery, with a generated `TypeId` check |
| **oxlint** | Yes. The rule struct is its options | Rule-owned but thin: serde with a one-line `from_configuration` | On the rule struct | No result cache (open request) | n/a | Codegen |

## Patterns Found

### Pattern 1: Hash all resolved settings

**Used by**: Ruff, ESLint
**How it works**: The cache key is a hash of the full config. Ruff's derive hashes every field of every plugin's settings. ESLint stringifies the file's resolved config. Neither tracks which rule uses what.
**Strengths**: Can't be wrong by omission, and a new option joins the key with no extra code.
**Weaknesses**: Over-invalidates when an unrelated option changes. That only costs on a config change, and no issue asking for per-rule keys turned up in a title search of Ruff's tracker.
**Example**: https://github.com/astral-sh/ruff/blob/HEAD/crates/ruff_macros/src/cache_key.rs

### Pattern 2: Filename dependence through key structure

**Used by**: Ruff
**How it works**: Entries are looked up by path relative to the package root, so a rule that behaves differently per path is covered without declaring anything. `per_file_ignores` is in the hashed settings. Ruff also records only files with no diagnostics.
**Strengths**: No per-rule metadata.
**Weaknesses**: Identical content under different paths gets separate entries, and files with findings are always re-linted.
**Example**: https://github.com/astral-sh/ruff/blob/HEAD/crates/ruff/src/cache.rs

### Pattern 3: The rule holds its typed options

**Used by**: oxlint, Biome
**How it works**: oxlint's rule struct holds its configuration, and a defaulted `from_configuration` means a rule without options writes nothing. Biome's `Rule` names `type Options`, and the engine passes it type-erased.
**Strengths**: Typed use site, defaults beside the type, unknown keys rejected.
**Weaknesses**: Needs serde and a generated registry. Biome pairs types across crates by naming convention, so it needed a generated `TypeId` list and a `debug_assert_eq!` to catch mismatches.
**Example**: https://github.com/oxc-project/oxc/blob/HEAD/crates/oxc_linter/src/rules/eslint/eqeqeq.rs ; https://github.com/biomejs/biome/blob/HEAD/crates/biome_configuration/src/generated/linter_options_check.rs

### Pattern 4: Schema on the rule, generic validation

**Used by**: ESLint, Pylint
**How it works**: The rule declares a data description, and the engine validates and reports errors.
**Strengths**: Uniform errors, and docs tooling can read the schema.
**Weaknesses**: Untyped at the use site. ESLint's RFC records defaults living in schema `default`, `normalizeOptions` helpers, inline logic, and typescript-eslint's `applyDefault`, and the PR thread records an ordering bug between default merging and validation.
**Example**: https://github.com/eslint/rfcs/blob/main/designs/2023-rule-options-defaults/README.md

### Pattern 5: Central settings struct importing every rule module

**Used by**: Ruff
**How it works**: `LinterSettings` has a field per plugin, with a separate user-facing serde struct per plugin elsewhere.
**Strengths**: One value, trivially hashed and passed to every rule.
**Weaknesses**: An option group touches several central files, and `options.rs` warns "Don't add new options to this type". That's a cost at Ruff's scale, and no source calls it a regret.
**Example**: https://github.com/astral-sh/ruff/blob/HEAD/crates/ruff_linter/src/settings/mod.rs

## Anti-Patterns

- **Defaults in more than one place** (ESLint, per its RFC). house-lint's HSL103 already does this: the `allowed` default is in the dataclass and again in `_rule_options`.
- **Options paired to rules by naming convention** (Biome), which then needs a mismatch check.
- **Parsed options as class state** (flake8 and pyflakes), which makes them process-global.

## Relevance to Us

One `Rule` entry per rule fits every peer that has settled this. Macros and codegen exist because Rust has no first-class table of heterogeneous typed values, and Python can just use a tuple.

Per-rule cache declarations would be new machinery no peer carries. Hashing all options is what house-lint already does and documents.

For the filename, a probe on the project's two real codebases measured the cost of always folding in the basename. On hassette it adds 0 entries to 1,485. On house-lint it adds 12 to 72.

The closest Python equivalent to serde-style parsing is msgspec. A probe found a 22ms import with no dependencies, and strict conversion that rejects booleans as integers. It handled all of HSL101's and HSL103's validation shapes, with `__post_init__` covering the cross-field checks. Its cost is a third runtime dependency, and validation messages change wording.

## Recommendation

Adopt one `Rule` table, keep hashing all options, and handle the filename in the key rather than through a rule declaration. For parsing, a declared typed struct with library conversion matches what the typed peers do. A small hand-written parse function per rule, built on shared validators, is the no-dependency alternative. The pickup ledger weighs the two.

**Coverage gaps:** read directly from source for all six peers, but not exhaustively. flake8's "no cache" comes from searching two files. Biome's cache is unverified (issue titles only). Not read: Ruff's `OptionsMetadata` derive and the `declare_oxc_lint!` internals. Whether third-party rule edits invalidate ESLint's cache wasn't verified. Only one retrospective-style source turned up, the ESLint RFC. The URLs were not live-verified.

## Sources

### Reference implementations
- https://github.com/astral-sh/ruff/blob/HEAD/crates/ruff_linter/src/settings/mod.rs — `LinterSettings`
- https://github.com/astral-sh/ruff/blob/HEAD/crates/ruff_workspace/src/options.rs — per-plugin options structs
- https://github.com/astral-sh/ruff/blob/HEAD/crates/ruff_workspace/src/settings.rs — `cache_key(ignore)` fields
- https://github.com/astral-sh/ruff/blob/HEAD/crates/ruff_linter/src/codes.rs — rule table
- https://github.com/astral-sh/ruff/blob/HEAD/crates/ruff_macros/src/cache_key.rs — `CacheKey` derive
- https://github.com/astral-sh/ruff/blob/HEAD/crates/ruff/src/cache.rs — per-package cache, path-keyed entries
- https://github.com/astral-sh/ruff/blob/HEAD/crates/ruff/src/diagnostics.rs — clean files only
- https://github.com/eslint/eslint/blob/HEAD/lib/config/config.js — schema compile and default merge
- https://github.com/eslint/eslint/blob/HEAD/lib/cli-engine/lint-result-cache.js — config hash
- https://github.com/eslint/eslint/blob/HEAD/lib/rules/semi.js — rule module shape
- https://github.com/pylint-dev/pylint/blob/HEAD/pylint/checkers/base_checker.py — checker declaration
- https://github.com/pylint-dev/pylint/blob/HEAD/pylint/config/arguments_manager.py — generic option registration
- https://github.com/pylint-dev/pylint/blob/HEAD/pylint/lint/caching.py — stats persistence only
- https://github.com/PyCQA/flake8/blob/HEAD/src/flake8/options/manager.py — plugin options
- https://github.com/PyCQA/flake8/blob/HEAD/src/flake8/plugins/pyflakes.py — `parse_options` as class state
- https://github.com/biomejs/biome/blob/HEAD/crates/biome_analyze/src/rule.rs — `declare_lint_rule!`, `type Options`
- https://github.com/biomejs/biome/blob/HEAD/crates/biome_configuration/src/generated/linter_options_check.rs — generated type check
- https://github.com/oxc-project/oxc/blob/HEAD/crates/oxc_linter/src/rule.rs — `Rule` trait, `from_configuration`
- https://github.com/oxc-project/oxc/blob/HEAD/crates/oxc_linter/src/rules/eslint/eqeqeq.rs — rule struct as options
- https://github.com/oxc-project/oxc/blob/HEAD/crates/oxc_linter/src/generated/rules_enum.rs — generated registry

### Issues and design discussions
- https://github.com/eslint/rfcs/blob/main/designs/2023-rule-options-defaults/README.md — `meta.defaultOptions` RFC
- https://github.com/eslint/eslint/pull/17656 — validation-order bug
- https://github.com/oxc-project/oxc/issues/21672 — request for caching
- https://github.com/biomejs/biome/issues/1328 — "Cache computed rules"

### Documentation
- https://github.com/PyCQA/flake8/blob/HEAD/docs/source/plugin-development/plugin-parameters.rst — plugin parameters
