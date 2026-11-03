# Design: Suppressions and multi-location findings for project rules

**Date:** 2026-11-03
**Status:** built
**Mode:** sketch
**Issues:** #60
**Parent:** design/specs/005-cross-file-rules-and-duplicate-code/design.md
**Prior art:** design/research/2026-10-30-linter-engine-architecture/prior-art.md

## Summary

#59 added project rules and a project phase (`pipeline.run_project_phase`), but nothing can suppress what they find. Today a pragma naming an enabled project rule passes HSL900's structural checks, makes no claim (`suppressions._collect_claims`, the `is_project_rule` branch), and hides nothing. Each `ProjectCandidate` location becomes a bare `Finding` (`pipeline.py`, the `rule_findings` comprehension), so a JSON consumer can't tell which findings came from the same candidate. `ScanResult.suppressed_count` counts only file-phase suppressions.

This change lets house-lint pragmas suppress individual project-rule occurrences in Python files, and gives every finding a `related` list, with JSON moving to `schema_version` 3.

**In scope:** project-phase pragma resolution for Python files, `related` on `Finding`, the JSON schema bump, rendering `related` in text output, counting project-phase suppressions, and docs. No shipped project rule exists (`PROJECT_RULES` stays empty), so the behaviour below is pinned with a stand-in rule.

**Out of scope:** a group id (D3, superseding that part of parent D12), an unused verdict for project-rule pragmas (dropped by spec 010 D9), TypeScript pragmas (#61; until then a TypeScript occurrence can't be suppressed), finding identity (#52, parent D16), jscpd and `HSL104` (#39), and a candidate budget for project rules.

**How it works** (the inherited rules it rests on are in Assumed):
1. The file phase behaves as today. A pragma naming a project rule gets the same checks and makes no claim there. Nothing new is cached.
2. The project phase runs each project rule and expands its candidates into one finding per location, as today. For each Python file among that rule's findings, it takes the file's retained `LoadedFile` and parses it with `python_source.parse_python`, at most once per file across all rules. A file that doesn't parse has no readable pragmas, so its findings stay visible.
3. Each finding's owner statement comes from D5's lookup. A pragma claims the finding when the pragma is accepted (as in the file phase), names the rule, and either is an `ignore-file` or its owner key equals `analysis.statement_key` of the finding's owner. A finding with no owner can only be claimed by `ignore-file`.
4. A claimed finding is dropped and counted in `suppressed_count`.
5. Resolution for a rule runs inside that rule's existing `try` in `run_project_phase`. An unexpected exception there is that rule's internal error, as a crash in `rule.run` already is (spec 010's build calls). None of the rule's findings are kept, and the phase stops.

**New homes:**
- `analysis.occurrence_owner(source, line)`: D5's lookup. It returns the statement, or `None`, and sits beside `statement_owner_for_line`, sharing its innermost-statement choice and except-clause refinement (`analysis.innermost_owner`, `analysis.refine_to_handler`); file-rule behaviour is unchanged (ship-time challenge, Finding 8).
- `suppressions.py` (sketch challenge, Finding 5):
  - `_collect_claims`'s pragma walk (parse, placement, rule checks) becomes a side-effect-free `scan_pragmas(source, enabled_rules)`. It returns the accepted pragmas, each with its owner key, and the HSL900 diagnostics.
  - The file phase builds claims from the accepted pragmas, skipping those that name a project rule as it does today, and reports the diagnostics.
  - A new `claims_for(source, rule_id)` returns whether a pragma claims an occurrence starting on a given line (ship-time challenge, Finding 4, replacing a per-file `project_suppressions` that returned claimed indexes). It calls `scan_pragmas` with `(rule_id,)` as the enabled set and ignores the diagnostics. That enabled set is correct because a file is in rule R's input only if R applies to it (`pipeline.run_file_phase`).
  - So a pragma is accepted in one phase exactly when it is accepted in the other, and no diagnostic is reported twice.
- Parsing, scanning each file's pragmas once per rule, and applying the claims: `pipeline.run_project_phase`, through a per-run `PragmaClaims`, with `expand_candidates` dropping claimed locations as it builds findings. `ProjectPhaseResult` gains a `suppressed_count`, and `run_scan` adds it to the file phase's.
- `results.py` (D3, sketch challenge Finding 7):
  - a public location type for `related` entries: a small frozen path-plus-span dataclass that validates through `results.validate_location`
  - the `related` field on `Finding`, defaulting to empty; `Finding` keeps its flat location fields
- Rendering `related`: `reporters/text.py` (D4).

**Must hold in the build:**
- File-rule findings, their suppression, and every HSL900 result are unchanged, cached or not.
- Each scanned file is still read exactly once. The project phase parses the retained `LoadedFile`, never the path.
- Nothing from the project phase is cached (parent D11).
- D5's "Behaviour to pin" cases hold.
- Each finding from a candidate lists every other location of that candidate in `related`, suppressed or not (parent D13), in the candidate's location order.

## Posture

- **Failure handling:** fail loudly, carried from the parent. A file with candidates that fails to re-parse has no readable pragmas, so its findings stay visible. The file phase already reported its parse error, so it isn't reported twice.
- **Compatibility:** break freely and mark the break `feat!`, per `CLAUDE.md`. JSON moves to `schema_version` 3. File-rule findings, suppression, and HSL900 behaviour don't change.
- **Scope:** minimal and Python-only. TypeScript occurrences report unsuppressed until #61. No unused verdict (spec 010 D9), no project-rule candidate budget. `PROJECT_RULES` stays empty, so behaviour is pinned with a stand-in rule.
- **Rollout:** ships directly.
- **Hardening:** project defaults. The re-parse costs one Python parse for each file with at least one project finding.
- **Altitude:** `build`. One session builds it, so the ledger cites files.

## Decisions

### D1: Which pragmas can suppress a project-rule occurrence?

**Situation:** Parent D14 resolves statement-level pragmas (`ignore`, `ignore-next`) for project rules by re-parsing, in the project phase, every Python file that has a project finding, then matching owners. The maintainer's comment on #60 asks whether cross-file suppression needs this much machinery. Spec 010 D9 already removed the largest part (the unused verdict, the stale-pragma byte search, the completeness flag). What's left is the re-parse and owner matching. The lighter alternative is file-level suppression only: `ignore-file[HSL104]` or `per-file-ignores`, with statement-level pragmas naming a project rule rejected by HSL900.

**Deciding factor:** a deliberate copy can be silenced without also silencing an unrelated duplicate in the same file.

| | A: Statement and file pragmas, resolved by re-parsing files with findings (parent D14) | B: File-level only: `ignore-file` and `per-file-ignores`; HSL900 rejects `ignore`/`ignore-next` naming a project rule |
|---|---|---|
| Silence one deliberate copy in a file that also has an accidental duplicate | Yes | No: the whole file goes quiet for that rule |
| Same pragma forms as file rules | Yes | No: a rule-kind-specific HSL900 message |
| New machinery in the project phase | A parse per file with findings, plus owner matching | None: the file phase records which project rules a file's `ignore-file` names, carried in its cached result |
| Per-file cache entry | Unchanged | Grows by one field |
| What #61 needs for TypeScript pragmas | Statement ownership per grammar (parent D15) | Comment finding and the file prologue only |
| Peers | PMD CPD (`CPD-OFF`/`CPD-ON`) and jscpd (`jscpd:ignore-start`) suppress regions. Pylint couldn't suppress `duplicate-code` below file level for 8 years (pylint#214, labelled high priority), added it in 2.13, and users still report it unreliable | Pylint before 2.13 |
| How often a file mixes a deliberate copy with an accidental one | Unknown, but sub-file suppression is the most-requested gap in Pylint's duplicate checker | Same |

**Recommendation:** A. Region suppression is what every dedicated duplicate tool offers, and Pylint's long-running issue shows what users hit without it. The cost left after spec 010 D9 is one parse per file with findings, uncached, using a claim walk the file phase already has.
**Pick B instead if** you'd accept whole-file silencing for duplicates, and want #61 to drop TypeScript statement ownership as well.
**Reversibility:** easy (B removes a step; A can be restored later without a schema change)
**Ratified:** Chose statement and file pragmas, resolved by re-parsing files with findings, over file-level suppression only, so one deliberate copy can be silenced without hiding an unrelated duplicate in the same file, accepting an uncached parse per file with findings and statement ownership for TypeScript at #61. Also accepted as a known limitation (sketch challenge, Finding 10): a project-phase parse failure isn't re-reported, which relies on the file phase having parsed the same retained bytes and reported or replayed the failure.

### D2: What happens when two pragmas claim the same occurrence?

**Situation:** For file rules, two pragmas that claim one candidate (for example a trailing `ignore[HSL101]` and an `ignore-next[HSL101]` above the same statement, or an `ignore-file[HSL101]` plus either) both get a "conflicting suppression" HSL900, and neither suppresses (`suppressions._conflicting_claims`). That verdict depends on a candidate existing, and project-rule candidates exist only in the project phase. Today the project phase emits no HSL900, and spec 010 D9 kept pragma verdicts out of it.

**Deciding factor:** no new HSL900 verdict from the project phase for a case that does no harm.

| | A: Both claim; the occurrence is hidden and counted once; no diagnostic | B: As for file rules: both get a conflict HSL900 from the project phase, and the occurrence stays visible | C: The file phase rejects any two pragmas for one project rule that could overlap (same owner, or either is `ignore-file`), with or without an occurrence |
|---|---|---|---|
| Same outcome as file rules | No | Yes | No: flags pairs file rules accept until a candidate appears |
| HSL900 from the project phase | None | New: uncached, and only in files with findings | None |
| False reports | None | None | A pair with no occurrence is still flagged |
| What the user sees | The outcome both pragmas asked for | Two diagnostics and an unsuppressed finding | Two diagnostics |
| How often | Rare: redundant pragmas on one statement | Same | Same |

**Recommendation:** A. Both pragmas asked for the same thing, and the result is that thing. B adds a project-phase verdict path for a rare, harmless case, which spec 010 D9 already chose not to carry for the unused verdict.
**Pick B instead if** you want HSL900 to treat redundant pragmas identically for both rule kinds. **Pick C instead if** you want redundant project-rule pragmas caught even before any duplicate exists.
**Reversibility:** easy
**Ratified:** Chose letting both pragmas claim, hiding and counting the occurrence once with no diagnostic, over a project-phase conflict verdict or a static file-phase check, so the project phase emits no HSL900, accepting that redundant project-rule pragmas are treated differently from redundant file-rule ones.

### D3: What do `related` and the group id look like in JSON?

**Situation:** Parent D12 adds a structured `related` list and a run-scoped group id to findings, with a `schema_version` bump. It doesn't fix the shape. File-rule findings have no siblings. A project candidate may have one location (a rule with a single site) or several (a duplicate cluster). Parent D16 says the group id is for grouping within one run only and is documented as unstable across runs.

**Deciding factor:** one finding shape a consumer can read without checking which kind of rule produced it.

| | A: Every finding has `related` (a list, empty for file rules) and `group_id` (an integer, null for file rules). Every project candidate gets an id, numbered from 1 in run order | B: Both keys appear only on project-rule findings | C: Like A, but `group_id` is set only for candidates with 2+ locations | D: `related` on every finding, no group id until a consumer needs one |
|---|---|---|---|---|
| Consumer code | Reads the same keys on every finding | Must check for the key | Same as A | Same as A, with one key |
| A single-location project finding | `related: []`, its own `group_id` | Same | `group_id: null`, indistinguishable from a file rule | `related: []` |
| Rule for when `group_id` is set | "Came from a project rule" | Same | "Has siblings", which `related` already says | n/a |
| A consumer named today for the group id | None: parent D16 gave #52 its own identity and called the group id separate from it; no CI summary exists | Same | Same | n/a; a consumer derives a cluster from a finding's location plus `related` |
| Code to carry | Id counter, numbering rules, field, docs | Same | A's plus a branch | None |
| `related` entry | `path`, `line`, `column`, `end_line`, `end_column`, matching `Finding`'s location keys | Same | Same | Same |
| Peers | SARIF `relatedLocations` is an optional array on every result | unknown | unknown | SARIF, as A |

**Recommendation (revised at the sketch challenge, Finding 6):** D. Neither consumer parent D12 named will read a group id (parent D16 gave #52 its own identity), and `related` on every finding keeps one finding shape. Supersedes the group-id part of parent D12. The draft recommended A for uniform keys with a group id on every project finding.
**Pick A instead if** you plan JSON tooling that groups clusters before #52 lands. **Pick B instead if** you'd rather keep file-rule output byte-for-byte except for `schema_version`. **Pick C instead if** a group of one should read as "no group".
**Reversibility:** easy (another `schema_version` bump)
**Ratified:** Chose `related` on every finding with no group id over adding a `group_id` (on every finding, on project findings only, or only for multi-location candidates), so consumers read one finding shape and no field ships without a consumer, accepting a `schema_version` bump if a consumer later needs a group key. Revised at the sketch challenge (Finding 6); originally ratified as A.

### D4: How does text output show `related`?

**Situation:** The text reporter prints one line per finding, `path:line:col: RULE message` (`reporters/text.py`, `render_text`). Editors and `grep` rely on one finding per line in that form. Parent D12 rejected relying on the message to name the other locations for JSON consumers, but didn't say what text output shows.

**Deciding factor:** a reader of text output can find the other copies, without breaking one-finding-per-line parsing.

| | A: The reporter appends them: `a.py:3:1: HSL104 <message> (also at b.py:10:1, c.py:7:5)` | B: The reporter ignores `related`; each rule puts what it wants in its message | C: Indented continuation lines under the finding, one per related location |
|---|---|---|---|
| One finding per line for editors and `grep` | Yes | Yes | No: a continuation line starting `b.py:10:1:` reads as another finding unless prefixed |
| Every project rule gets it without writing it | Yes | No | Yes |
| Text and JSON can disagree | No | Yes | No |
| A 10-copy cluster | One long line | Up to the rule | 10 extra lines per finding |
| Peers | unknown | Pylint's `duplicate-code` puts the files and the duplicated lines in a multi-line message | rustc's and Ruff's notes, for one primary site |

**Recommendation:** A. It keeps the line-per-finding contract and gives every project rule the same rendering, so `HSL104`'s message can say what is duplicated rather than where. Each related entry renders as `path:line:col`, or `path` alone when it has no line, matching how a finding's own location renders.
**Pick B instead if** you expect project rules whose other locations aren't worth showing. **Pick C instead if** readable output for large clusters matters more than editor parsing.
**Reversibility:** easy
**Ratified:** Chose the reporter appending related locations on the finding's own line over leaving it to each rule's message or adding continuation lines, so text output names the other copies and keeps one finding per line, accepting long lines for large clusters. Also accepted as a known limitation (sketch challenge, Finding 8): a cluster of n copies yields n(n-1) `related` entries in JSON and n very long text lines, with no project-rule candidate budget; per-file-ignores keep generated and vendored clusters out of the input.

### D5: How is an occurrence's owner statement found?

**Situation:** Parent D14 makes an occurrence's owner "the innermost statement containing its start line". The draft reused `analysis.statement_owner_for_line` for that, a function built to attach a comment to a statement, and three of its properties misfire for occurrences. A decorated `def`/`class` is keyed from its `def` line (`analysis.statement_key`), so an occurrence starting on `@decorator` has no owner at module level, and inside a class its owner is the class, which lets a pragma on the `class` line hide it. An `ignore-next` above the decorator is accepted by the file phase (placement uses the decorated start, `suppressions._statement_start_line`) and then matches nothing, silently. It indexes `source.lines[line - 1]` unchecked, so a line past the end of the file raises `IndexError` outside the per-rule `try` in `run_project_phase` and costs the run, where parent D9 accepted a mismatched location as a visible finding. And it chooses containment or line equality from the text before a comment column, so an occurrence's indentation would decide the rule. Supersedes the owner lookup in parent D14 step 3; its equality rule between owners stands. Added at the sketch challenge (Findings 1–4).

**Deciding factor:** a pragma placed where a duplicate visibly starts suppresses that duplicate and nothing else, and a location that disagrees with the file never costs the run.

| | A: A line-only, occurrence-only lookup: innermost statement whose decorated span (first decorator to end line) contains the start line, with the except-clause refinement; out-of-range lines have no owner | B: Reuse `statement_owner_for_line` and patch decorators, bounds, and the column branch separately | C: Line equality: a pragma claims an occurrence when its owner's decorated start line equals the occurrence's start line |
|---|---|---|---|
| `ignore-next` above a decorated duplicate | Suppresses it | Suppresses it once patched | Suppresses it |
| A `class`-line pragma hiding body occurrences | Only for an occurrence starting on a comment or blank line in the body, which jscpd's weak mode doesn't produce | Same as A once patched | Never |
| Duplicate starting on a continuation line of a multi-line statement | Owned by that statement | Same as A | `ignore-file` only |
| Location past end of file | Visible finding | Visible finding once patched | Visible finding |
| File-rule pragma behaviour | Unchanged: separate function | Risk of change through the shared function | Unchanged |
| New code | One lookup in `analysis.py` | Three patches to a shared function | Removes the occurrence lookup |
| Peers | PMD CPD and jscpd suppress regions with markers; owner-matching semantics among peers: unknown | Same | Same |

**Recommendation:** A. One rule covers all three problems, keeps containment for duplicates that start mid-statement (jscpd token windows can start anywhere), and leaves the file-rule path untouched.
**Behaviour to pin:**
- An `ignore-next` above a decorated definition, or a trailing `ignore` on its `def`/`class` line, suppresses an occurrence that starts on its decorator.
- A pragma on a `def` or `class` line doesn't hide an occurrence that starts on a statement inside its body.
- An occurrence starting on a continuation line of a multi-line statement is owned by that statement.
- An occurrence whose start line is past the end of the parsed file has no owner, stays a visible finding, and doesn't fail the run (parent D9's accepted limitation).
- A trailing `ignore` on a decorator line keeps its file-rule owner (the enclosing statement, or HSL900 "misplaced" at module level), so it doesn't claim the decorated definition's occurrence.

**Pick B instead if** you want parent D14's lookup kept literally. **Pick C instead if** you'd rather delete the occurrence lookup and accept `ignore-file` for duplicates starting mid-statement.
**Reversibility:** easy
**Ratified:** Chose a line-only, occurrence-only lookup over decorated spans, with out-of-range lines owning nothing, over patching the comment-owner function or matching on start lines, so a pragma where a duplicate visibly starts suppresses it and a mismatched location never costs the run, accepting a second owner lookup beside the file-rule one.

## Assumed

- Pragmas naming a project rule get structural, language, and disabled-rule checks in the file phase, and no unused verdict. Evidence: spec 010 D9, superseding parent D14's stale-pragma byte search, completeness flag, and move of the disabled-rule verdict. `suppressions._collect_claims`.
- Owner matching uses equality with the pragma's owner statement, not containment. Evidence: parent D14.
- A suppression hides only its own occurrence, and suppressed occurrences still count toward the cluster and appear in siblings' `related`. Evidence: parent D13.
- One finding per location, with a structured `related` list. Evidence: parent D12 (its group id is superseded by D3). The expansion point already exists: `ProjectCandidate.locations` (`project_rule.py`), expanded in `pipeline.run_project_phase`. Evidence: spec 010 D2.
- Nothing from the project phase is cached. Evidence: parent D11, spec 010 Assumed.
- Each file in a project rule's input is retained as a `LoadedFile` and available on a cache hit, so the project phase can parse it without a second read. Evidence: spec 010 D1, `pipeline.run_file_phase` (`project_inputs`).
- A file in a project rule's input passed that rule's applies check, so a pragma there naming the rule isn't a disabled-rule pragma. Evidence: parent D21, `config.applicable_rules`.
- The cache is namespaced by house-lint's version and a hash of its own sources, so adding fields to `Finding`'s cached payload needs no migration. Evidence: parent Assumed, `cache.py` `_finding_to_payload`/`_finding_from_payload`.
- `schema_version` 2 is named in `results.py` (`ScanResult.to_dict`), `reporters/json.py`'s docstring, `README.md`, and `docs/configuration.md` (twice). Evidence: a search of `src`, `docs`, and `README.md`.
- The issue body still describes the unused verdict and completeness flag. Spec 010 D9 dropped them, so the body is stale on those points. Evidence: issue #60 body.

## Build

- [x] Implementation and tests committed
- [x] Docs
- [x] Ship-time challenge

**Calls made during the build:**
- `RelatedLocation` is the name of the location type for `related` entries: it says what the type is for, and `Location` is taken by `project_rule`.
- An accepted `ignore-file` makes `claims_for` claim every occurrence without an owner lookup: with `(rule_id,)` as the enabled set, every accepted pragma names that rule.
- The decorated start line now has one home, `analysis.decorated_start_line`, used by `occurrence_owner` and by `suppressions.py`'s placement checks (it replaces `suppressions._statement_start_line`). The except-clause refinement moved to `analysis.refine_to_handler`, shared by both owner lookups, so the two can't drift.
- The cache payload leaves `related` out, as it leaves out the finding's own path (ship-time challenge, Finding 5). Cached results come from the file phase, whose findings can't have related locations (`CandidateFinding` has no such field), and a stored path could go stale when a content-keyed entry is replayed for a moved file.
- `PragmaClaims` keys its parse cache by `LoadedFile.path`, the file's identity, rather than by the whole `LoadedFile` with its bytes, and its claim checks by rule and path.
- The new whole-run pins live in `tests/integration/test_project_suppressions.py`, beside `test_project_phase.py`, which was already long.

## Addendum

### 2026-11-03: `refine_to_handler` folded into `innermost_owner`
The clean-code pass before the PR folded `analysis.refine_to_handler`, whose only caller was `innermost_owner`, into it. The except-clause refinement is unchanged and still shared by both owner lookups through `innermost_owner`. `expand_candidates` now takes the run's `PragmaClaims` directly instead of a callback.
