# Design: One rule declaration, with typed options

**Date:** 2026-11-02
**Status:** built
**Mode:** sketch
**Issues:** #58
**Parent:** design/specs/005-cross-file-rules-and-duplicate-code/design.md
**Prior art:** design/research/2026-11-02-rule-option-declaration/prior-art.md

## Summary

Adding a rule today means editing parallel structures: `rule_catalog.RULES`, `registry._DETECTORS`, the options dict in `config.selected_detector_inputs` (`config.py:148`), `LintConfig`'s per-rule fields, `_rule_options` (`config.py:399`), and `cache.hash_effective_config`'s per-rule parameters (`cache.py:154`). Nothing checks `selected_detector_inputs`, so a rule missing from it silently never runs. The option types live in `config.py`, and `registry.py` casts through adapters to reach them.

This change gives each rule one declaration, moves each option type into its rule's module, and migrates HSL101-103. The duplicate rule (#39) and #53 then add options with one local change.

**In scope:** the declaration, option types and parsing, the config hash inputs, HSL101-103's migration, and the tests and docs that name the old shapes (`CLAUDE.md` "Layout" and "Adding a rule", `docs/configuration.md:126`, the `cache.py` module and `hash_effective_config` docstrings, the `README.md` if it names them).

**Out of scope:** rule `kind` and `languages` and the project phase (#59), and the loaded-file split (#57). #57 is open and touches the same rule modules, so whichever lands second rebases.

**New homes (each follows from the decision named):**
- The `Rule` declaration and the table of rules: `rule_catalog.py` (D1).
- Each option type and the constants it validates against: its rule's module under `rules/` (D1, D2). For HSL101, `spec_tokens.py` gains `TokenFamily`, `BUILTIN_*`, `VALID_SCOPES`, `SEPARATOR_REGEX`, `SUFFIX_REGEX`, and the `MAX_*` limits and patterns it validates with. Those dict keys stay the single source of truth for valid values, so validate against them rather than restating them in a `Literal`.
- Resolved options for a run: one immutable `LintConfig` mapping with an entry for every file rule, `None` for HSL001-004. It replaces the `hsl101`-`hsl103` fields (D1, D3).
- Choosing what runs for a file: `config.selected_detector_inputs` stays the one place, deriving from the table. It walks the enabled IDs in order, keeps those that are file rules, which leaves out HSL900 (`config.py:285`), and returns (`FileRule`, options) pairs with options read from `LintConfig` by ID. `DetectorInput` becomes that pair, and the `DetectorOptions` union and the options dict at `config.py:148` are deleted.
- Dispatch: the existing loop in `_scan_ready_source` (`scanner.py:110`) calls each pair's detector directly. `detect_candidates`, which that loop already calls with one input at a time, is deleted along with `registry.py`, and with it the silent skip for an unknown ID. Behaviour to keep: when a detector overflows the candidate budget, findings from earlier detectors and the overflowing detector's partial prefix are both kept (`_recover_candidate_budget`).
- The cache key's options: `pipeline._cache_keys` passes `LintConfig`'s whole options mapping to `hash_effective_config` (D3), which always folds in the basename and drops the HSL101 check at `cache.py:192` (D4).
- Turning a config table into options: `config.py`'s `_rule_options` loops over the table and calls each row's `parse` (D2).

**Must hold in the build:**
- Option `Struct`s and `TokenFamily` inherit one base declaring `frozen=True, forbid_unknown_fields=True`. Without the flag, a typo'd key is silently accepted.
- D3 serializes with `msgspec.to_builtins`, because `dataclasses.asdict` and `json.dumps` both raise on a `Struct`.
- `_rule_options` converts `msgspec.ValidationError` to `ConfigError`, prefixed with `rules.<ID>`. HSL101's `parse` raises `msgspec.ValidationError` for the combined-family cap, so that surfaces the same way.
- Before replacing `_strict_keys(rules, {...})` (`config.py:401`), add a test that `[tool.house-lint.rules.HSL001]` and `[tool.house-lint.rules.HSL900]` are rejected as unknown keys. Only that literal set enforces this today.

## Posture

- **Failure handling:** unchanged. Bad config raises `ConfigError` and exits as it does today.
- **Compatibility:** break freely. The CLI, config schema, messages, and cache keys may change wherever that improves the design, as CLAUDE.md allows, and internal names break with no shims. Set by the user at the posture check.
- **Scope:** the declaration and the HSL101-103 migration only. `kind`, `languages`, and project rules stay with #59.
- **Rollout:** ships directly. New error wording and a new runtime dependency are user-visible, so the PR isn't `chore:`. It gets a `BREAKING CHANGE:` footer only if a config that loads today stops loading.
- **Hardening:** project defaults.
- **Altitude:** `build`. One session builds it, so the ledger cites files.

## Decisions

### D1: What does a rule's declaration look like?

**Situation:** A rule's facts are spread over the catalog (metadata, enablement), the registry (detector), and config (options type, parsing). The parent's D5 says each rule declares its options type "in the catalog or registry", and the parent's D3 adds a second registry for project rules. Both keep the parallel lists. A table holding detectors also forces a dependency change: the catalog would import the rule modules, and three of those import `config`, which imports the catalog. So under B or C the option types must leave `config.py`.

**Deciding factor:** adding a rule is one local change, with no second list to forget.

| | A: Parent D5 as written: keep catalog and registry, add options to one | B: One central table in `rule_catalog.py`; each row names metadata, detector, and options | C: Each rule module exports its own `RULE`; the catalog lists the modules |
|---|---|---|---|
| Lists to keep in sync | Two plus an import-time check; #59 adds another | One | One |
| Silent-drop hazard | Fixed only if `selected_detector_inputs` derives from the registry | Gone | Gone |
| See every rule at a glance (`house-lint rules`, display order) | Yes, in the catalog | Yes | No: the metadata is spread over seven files, with one list of modules |
| Everything about a rule in one file | No | Options and detector yes, metadata no | Yes |
| Touches to add a rule | Three places | Rule file plus one row | Rule file plus one line |
| HSL900 (metadata, no detector) | Fits today's catalog | A row type with no detector, in the same table | Needs a home outside `rules/`: `suppressions.py` imports the catalog, so it can't export a `RULE` |
| Peers | None keep hand-synced lists | Ruff (table; generated) | ESLint, oxlint, Biome (declared per rule) |

**Recommendation:** B. It removes every parallel list and keeps today's catalog as the one place to read all rules. C wins only on locality of metadata, and it makes HSL900 a special case. `registry.py` goes away. This supersedes the "second registry" in parent D3. Parent D3's two kinds of rule still stand.

**Row shape (sketch challenge, Finding 3).** Every rule has a metadata row, including HSL900, so `RULES` stays a mapping from ID to non-generic `Rule` in display order. A generic `FileRule` subtype adds the detector and options type (`None` for HSL001-004), and D5's factory builds `FileRule` rows. The file rules are derived from `RULES`, annotated `FileRule[Any]`. #59 adds a sibling `ProjectRule` with its own detector signature, rather than optional fields or a `kind` flag, and adds `languages` to the row. Rejected: one `Rule` with optional fields, which makes every consumer branch on them.
**Pick C instead if** you want a rule's description beside its code more than one table of all rules. **Pick A instead if** you want #58 to stay exactly as the parent wrote it and leave the table to #59.
**Reversibility:** easy
**Ratified:** Chose one central table in `rule_catalog.py` over keeping the catalog and registry or per-module `RULE` exports, so adding a rule is one local change with no second list to forget, accepting that a rule's metadata sits apart from its code. Supersedes the "second registry" in parent D3. At the sketch challenge (Finding 3), pinned the row shape as a metadata row for every rule with a `FileRule` subtype and a sibling `ProjectRule` at #59, over optional fields or a separate HSL900 constant, accepting one subclass now.

### D2: Who parses and validates a rule's options?

**Situation:** `_rule_options` and `_token_family` (`config.py:342-442`) hand-validate about 100 lines for three rules. Parent D5 ratified "generic, driven by the declared type" before anyone checked what that needs in Python. HSL101 has cross-field rules: unique prefixes, `max_digits >= min_digits`, and a cap on families after built-ins merge. Defaults are written twice for HSL102 and HSL103 (`config.py:115`, `:120`, `:428`, `:432`).

**Deciding factor:** adding a rule's options means declaring a type, with validation, defaults, and errors coming from one mechanism.

| | A: Each rule writes a parse function on shared validators (`_bounded_int`, `_strings`, moved to a leaf module) | B: House-lint builds a validator driven by dataclass field metadata | C: msgspec: each rule declares a frozen `Struct`, converted strictly by one generic call |
|---|---|---|---|
| New rule's option code | A dataclass plus a hand-written parser | A dataclass with field metadata | A `Struct` with annotated constraints |
| Unknown keys, types, bounds | Hand-written per rule | Generic, built here | Generic: verified by probe, including rejecting `true` for an integer |
| Cross-field and post-merge rules | Inline in the parser | Needs a hook anyway | `__post_init__`, verified by probe; a row's optional `parse` hook covers the rest (see below) |
| Defaults in one place | Only if the parser reads them from the dataclass | Yes | Yes: field defaults, and `{}` converts to them |
| Runtime dependencies | 2 | 2 | 3. No transitive dependencies; a C extension with wheels for CPython 3.11-3.14 on Linux, macOS, and Windows, plus an sdist. Last release 2026-09-29 |
| Startup | None | None | About 22ms import, measured, against about 170ms startup in the prior brief |
| Validation code in house-lint | Stays about the same size, moved | Replaced by a validator nobody else maintains | Mostly deleted; declarative `Struct`s remain |
| Error messages | Unchanged | Rewritten | Change to msgspec's: ``Expected `int` >= 1 - at `$.max_lines` ``, prefixed with `rules.HSL102` |
| Fit with parent D5 | Supersedes "generic" | Holds | Holds |
| Peers | flake8 (by hand); oxlint's rule-owned shape, without serde | ESLint and Pylint have a generic engine, but untyped | Ruff, Biome, oxlint (typed structs with library parsing) |

**Recommendation:** C. It's the only option that delivers parent D5's "generic" without house-lint owning a validator, and it matches CLAUDE.md's preference for wrapping a proven tool over hand-building. With messages free to change, A's main advantage is gone. The new dependency is small, and msgspec has few contributors beyond its main author, so that's the risk being accepted: if it stalls, the `Struct`s translate back to dataclasses and parsers.
**Pick A instead if** you want to keep runtime dependencies at two, or dislike a C extension in a linter's install. **Pick B instead if** you want generic validation with no dependency and accept owning it.

**HSL101's merge (sketch challenge, Finding 2).** A row's factory takes an optional `parse` that defaults to the generic conversion, so `_rule_options` stays one loop with no rule-specific branch. HSL101's parser converts, applies the combined cap, and prepends the built-in families. `spec_tokens` keeps reading `options.tokens`, and directly constructed `HSL101Options(...)` still means "only these families". Rejected: a `families` property that always adds built-ins. It keeps `tokens` to one meaning, but a simulation failed 4 of 106 tests that need to isolate a family (`tests/unit/rules/test_spec_tokens.py`).
**Reversibility:** easy (options types are a small, declarative surface)
**Ratified:** Chose msgspec `Struct`s converted by one generic call over per-rule parse functions or an in-house validator, so a rule's options are a declared type with validation and defaults from one mechanism, accepting a third runtime dependency with few contributors beyond its main author and new error wording. At the sketch challenge (Finding 2), added an optional per-row `parse` hook for HSL101's merge over a `families` property, so family tests stay isolated, accepting that `tokens` means extras in TOML and the merged list on the object, as it does today. The user kept msgspec when the challenge asked whether to reopen it (Finding 1), and accepted that if it stalls or a CPython release lacks a wheel, the `Struct`s get translated back to dataclasses and parsers by hand (Finding 12).

### D3: Which options go into the per-file config hash?

**Situation:** `hash_effective_config` hashes all three option blocks whether or not the rule is enabled, and `docs/configuration.md:126` documents that as deliberate. Parent Breakdown item 3 says each rule declares what it contributes to the hash. A missed contribution would replay stale results, which is the worst failure a linter cache can have.

**Deciding factor:** a new option can't be left out of the key by mistake.

| | A: Hash every rule's options, always | B: Hash only enabled rules' options | C: Each rule declares its contribution (parent Breakdown item 3) |
|---|---|---|---|
| Stale result if an author forgets | Impossible | Impossible | Possible |
| Cache kept when a disabled rule's options change | No | Yes | Yes |
| How often that case happens | Only when someone edits a disabled rule's options | Same | Same |
| Machinery | None: serialize the mapping | An enabled filter | A per-rule field and tests for it |
| Peers | Ruff, ESLint | None | None |

**Recommendation:** A. It's today's documented behaviour and what both peers with a cache do, and the case it gives up is rare and costs one cold run. This supersedes the "each rule declares" wording in parent Breakdown item 3.
**Pick B instead if** you often toggle options on rules you've disabled. **Pick C instead if** you expect a rule whose options are expensive to hash.
**Reversibility:** easy
**Ratified:** Chose hashing every rule's options over hashing only enabled rules or per-rule declarations, so a new option can't be left out of the key by mistake, accepting one cold run when a disabled rule's options change. Supersedes the "each rule declares" wording in parent Breakdown item 3. Whether project-rule options join the hash is left to #59 (sketch challenge, Finding 10).

### D4: How does the cache key cover a rule that reads the filename?

**Situation:** HSL101 can match tokens in the file's name, so `hash_effective_config` folds the basename in only when an enabled HSL101 family scopes to `filenames` (`cache.py:192`). That's rule-specific knowledge inside the cache module, and it's the one reason a rule might need to declare a cache input. Separately, parent D6 tags language by suffix, so identical bytes in `a.py` and `a.ts` could otherwise collide on one entry once TypeScript arrives.

**Deciding factor:** the cache knows nothing about any specific rule.

| | A: Always fold in the basename | B: A rule declares that it reads the filename | C: Always fold in the root-relative path |
|---|---|---|---|
| Rule knowledge in the cache | None | None, but a new `Rule` field exists for one rule | None |
| Stale result if an author forgets | Impossible | Possible | Impossible |
| Extra entries, measured | 0 of 1,485 on hassette; 12 of 72 on house-lint | 0 | Every same-content pair, such as empty `__init__.py` files across packages |
| Covers same bytes with a different suffix | Yes | No | Yes |
| Covers a future rule reading directories | No | If it declares | Yes |
| Peers | Ruff keys on relative path | None | Ruff |

**Recommendation:** A. It deletes the special case, can't go stale, costs almost nothing, and closes the suffix collision before #59. No rule reads directories, and a future one can move to C then.
**Pick C instead if** you expect path-dependent rules soon. **Pick B instead if** the few extra entries matter on a repo with many same-content, different-name files.
**Reversibility:** easy
**Ratified:** Chose always folding in the basename over a rule-declared flag or the full path, so the cache holds no rule-specific knowledge and can't go stale by omission, accepting that identical files under different names no longer share an entry, and that the cache relies on language following from the filename's suffix, so a future configurable language mapping must add itself to the hash (sketch challenge, Finding 11). Supersedes the "extra input such as HSL101's filename" wording in parent Breakdown item 3.

### D5: How does the table keep a detector and its options type paired?

**Situation:** File rules have different option types, so `FileRule` is generic and the collection of them is annotated with `FileRule[Any]`. A probe under Pyright strict found that writing a generic class's constructor directly under an annotation like that accepts a detector and options type that don't match, because the expected `Any` wins. A small factory function, `rule(...) -> FileRule[Any]`, does report the mismatch.

**Deciding factor:** a mismatch fails the type check, with no cast in the dispatch path. **Recommendation:** build `FileRule` rows through a factory, which replaces the three `_hsl10x` cast adapters. **Pick direct `FileRule(...)` rows plus a runtime test instead if** the factory reads as ceremony for seven rows.
**Reversibility:** easy
**Ratified:** Chose a small factory function over direct rows plus a runtime test, so a mismatched row fails the type check in the editor with no test to maintain, accepting about three lines that protect only against an author's mistake in an internal table. The user delegated this call, asking for the best developer experience without overkill.

## Assumed

- Parent D5's direction holds: each rule declares its options type and config parsing looks it up. Evidence: parent D5, against `config.py:399`.
- Parent D3's two rule kinds hold, and #59 builds on this table. Evidence: parent D3; #59 is open and nothing has landed for it.
- Cache key changes need no migration. The namespace includes a hash of house-lint's own sources, so this change gets a fresh directory. Evidence: `cache.py:115-123`, `cache.py:134`.
- The catalog can import the rule modules once they stop importing `config`: rule modules otherwise import only `analysis` and `source`, and neither imports `config` or the catalog. Evidence: the import lines of `rules/*.py`, `analysis.py`, and `source.py`.
- `scanner.py` is the only `src/` caller of `detect_candidates`. Evidence: `scanner.py:10`, and a search of `src/`.
- D1, D3, and D4 don't depend on msgspec. Reversing D2 changes the option types, the conversion call in `_rule_options`, and D3's serialization. Evidence: `config.py:124-158`, `cache.py:154-157`, `cache.py:185-187`.
- Option tables for disabled rules are still validated, and `rules.HSL001` stays an unknown key because that rule has no options. Evidence: `config.py:401`.

## Build

- [x] Implementation and tests committed
- [x] Docs
- [x] Ship-time challenge

**Calls made during the build:**
- The stored row holds `parse_options` rather than the options type: `configurable_rule(...)` names the type in each row and turns it into the default parser, after which nothing reads the type again, so storing both would leave a dead field and two optional fields to keep in sync. `parse_options` is `None` for HSL001-004, where D1 sketched an options type of `None`.
- Two factories, `file_rule` and `configurable_rule`, rather than D5's one: a single signature with an optional options type can't reject a detector that needs options paired with none. A probe confirmed Pyright reports both mismatch shapes.
- `FILE_RULES` is the written table and `RULES` adds HSL900 to it, where D1 derives `FILE_RULES` from `RULES`. Filtering by `isinstance` yields `FileRule[Unknown]`, which Pyright strict rejects without a cast. Consumers see the same two mappings.
- The base class is `RuleOptions` in `rules/options.py`, a leaf module, because the catalog imports the rule modules and they need the base. Subclasses repeat `frozen=True` because Pyright doesn't inherit it through msgspec's dataclass transform and reports a subclass that omits it. `forbid_unknown_fields` is inherited at runtime, and tests pin it.
- Failure-injection tests patch `config.FILE_RULES` (the `fail_detectors_for` fixture and two subprocess preludes), since `scanner.detect_candidates` is gone and rows hold direct function references.
- Ship-time challenge changes: `configurable_rule` binds its options type to `RuleOptions`, `enablement` is a `Literal`, `rules_by_id` raises on a repeated ID, the cache key serializes with `order="deterministic"` so a set-valued option stays stable across processes, the family cap lives only in `parse_options`, and msgspec is capped at `<0.23`.
- No `BREAKING CHANGE:` footer: 99 configs run through the old and new code gave identical accept/reject verdicts and resolved values, so no config that loads today stops loading. Only error wording changed.
