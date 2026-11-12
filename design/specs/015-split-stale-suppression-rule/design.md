# Design: Split stale-suppression diagnostics out of HSL900

**Date:** 2026-11-10
**Status:** built
**Mode:** sketch
**Issues:** #91

## Summary

`house-lint check --select HSL102` reports `HSL900 unused suppression for disabled rule HSL002` for every pragma naming a rule the run didn't select, and exits 1. A per-rule gate (`--select <rule> --base`, used by hassette#3997 and hass-hassette#60, and the README's `--select HSL104` pre-push hook) therefore fails on any pragma for another rule. HSL900 can't be deselected, so there is no way out short of the run selecting every rule.

HSL900 bundles two kinds of check. Broken pragmas: malformed syntax, no IDs, invalid or duplicate IDs, bad action or reason, wrong placement, unknown rule ID, a rule that doesn't handle Python. Stale or redundant pragmas: an ID whose rule ran and found nothing to suppress (`unused suppression for X`), and an ID whose rule isn't enabled for this file (`unused suppression for disabled rule X`), and two pragmas claiming the same target or finding (`conflicting suppression for X`). Only the stale checks depend on rule selection; overlap handling is D3 and D4.

The change moves the stale and overlap diagnostics (D3, D4) into a new rule (ID: D1; tier: D2) that is an ordinary rule: selectable, ignorable, and nameable in `per-file-ignores`, like any other. HSL900 keeps the broken-pragma checks and stays always on and unsuppressible, which is where spec 001's reason for locking it ("suppression errors could recursively hide their own invalidity", `design/specs/001-llm-lint-extraction/design.md:260`) actually applies. A per-rule gate then leaves the stale rule out, as `ruff check --select F401` leaves out RUF100.

Homes:
- The moved diagnostics stay in `src/house_lint/suppressions.py`, emitted under the new rule's ID only when that ID is in the file's applicable rules (the `enabled_rules` that `scan_pragmas` and `apply_suppressions` already receive). Since `applicable_rules` (`config.py:107`) applies per-file-ignores, excluding the new rule for a path needs no new code path.
- A pragma naming a rule not enabled for the file is still not accepted, whether or not the stale rule runs, so the empty-claim "unused" path (`suppressions.py:118`) never sees it. The diagnostic is the only thing gated.
- The new rule's row, and an ID constant for it next to `ALWAYS_ON_RULE_ID`, live in `src/house_lint/rule_catalog.py` (D5 decides the row's shape).
- D3's overlap handling, D4's survivor ordering, and D6's claim pass over the new rule's diagnostics live in `src/house_lint/suppressions.py`, beside the claim code they extend (`_collect_claims`, `_conflicting_claims`, `_owns`). `_diagnostic`, hard-wired to `ALWAYS_ON_RULE_ID` today, takes the rule ID.

Out of scope: new diagnostic categories beyond the stale and overlap diagnostics, and autofix.

## Posture

- **Failure handling:** loud, the project default. The diagnostics are deterministic; nothing retries.
- **Compatibility:** break freely. No shims (CLAUDE.md). No config can name HSL900 today, so no config needs migrating. The PR is `feat!`, and its description ends with one `BREAKING CHANGE:` footer, a `####` header, and one bullet per break:
  1. HSL900 no longer reports unused, disabled-rule, or overlapping pragmas; they move to the opt-in HSL901 (D2). Add `extend-select = ["HSL901"]` to keep them; JSON consumers filtering `rule_id == "HSL900"` for stale pragmas see them under HSL901 or not at all.
  2. Overlapping pragmas now suppress their finding instead of failing closed, so a run that exited 1 on an overlap can exit 0, and the `conflicting suppression for X` message is gone (D3, D4).
  3. A CLI `--select` replaces the config's `select`, `ignore`, `extend-select`, and `extend-ignore` (D7). A gate that relied on config `extend-ignore` under `--select` must pass `--ignore` or `--extend-ignore` instead.
  `Rule`'s required `languages` field (D5) is internal: CLAUDE.md's breaking surface doesn't include the Python catalog, so it isn't listed.
- **Scope:** the split, plus the overlap redesign (D3, D4) and inline suppressibility of the new rule (D6), ratified mid-interview to match Ruff and Pylint, and the `--select` selection change (D7), ratified during the build. No new subcategories, no autofix.
- **Rollout:** ships outright in one PR. It unblocks the held 0.6.0 `--base` release (`design/specs/014-base-ref-new-code-gate/design.md:299`).
- **Hardening:** project defaults.
- **Altitude:** build; one session builds it, and the ledger cites files.

## Decisions

### D1: What ID does the stale-suppression rule get?

**Situation:** CLAUDE.md's ID convention is "HSL001-HSL0xx are on by default, HSL1xx are opt-in, and HSL900 is reserved." The new rule is part of the suppression machinery, not a code-pattern detector. D2 makes it opt-in, so the in-convention ID is HSL105 (the next opt-in ID); the slot next to HSL900 is HSL901.

**Deciding factor:** the ID tells a reader what family the rule belongs to.

| | A: HSL901 | B: HSL105 |
|---|---|---|
| Groups with HSL900 in listings and docs | yes, adjacent | no, sits among code-pattern rules |
| Fits the current ID convention | no; the convention line becomes "HSL9xx are suppression diagnostics: HSL900 always on, HSL901 opt-in" | yes, as written |
| Peers | Ruff: RUF100 and RUF102 adjacent in one family | n/a |
| Future opt-in code-pattern rules | keep the HSL1xx sequence unbroken | HSL105 is spent on a non-detector |

**Recommendation:** A, because the ID should say "this is about pragmas", and the convention line is one sentence to update.
**Pick B instead if** you want the number to always encode the tier and would rather not touch the convention.
**Reversibility:** hard (once released, the ID is in users' configs and pragmas-adjacent docs; renaming is another breaking change)
**Ratified:** Chose HSL901 over the in-convention ID (HSL105 under D2's opt-in tier), to make the ID name the suppression family, accepting a one-line rewrite of the ID convention in CLAUDE.md.

### D2: Is the stale-suppression rule on by default or opt-in?

**Situation:** Today stale pragmas are always reported. README line 100 says HSL900 exists "so stale suppressions don't pile up". Ruff's RUF100 and Pylint's `useless-suppression` are both opt-in.

**Deciding factor:** match the peers, as D3 and D6 do.

| | A: on by default | B: opt-in |
|---|---|---|
| Default run behavior vs today | unchanged | stale pragmas silently stop being reported |
| Per-rule gates (#91) | fixed: `--select X` leaves it out | fixed |
| Peers | differ (both opt-in) | match |
| Consuming projects' configs | no edit needed | each must add it to `extend-select` to keep today's coverage |

**Recommendation:** B, because Ruff and Pylint both make their stale-suppression rule opt-in, and #91 is fixed either way.
**Pick A instead if** you want default runs to keep reporting stale pragmas, as README line 100's "so stale suppressions don't pile up" promises, without every consuming config needing an edit.
**Reversibility:** easy (moving a rule between tiers is a one-row change, though itself breaking)
**Ratified:** Chose opt-in over on by default, to match Ruff's RUF100 and Pylint's useless-suppression, accepting that default runs stop reporting stale pragmas and each consuming project (house-lint's own config, hassette, hass-hassette) adds `extend-select = ["HSL901"]` to keep them.

### D3: How do overlapping pragmas on one finding behave, and which rule reports them?

**Situation:** Two pragmas can claim the same finding: `ignore-file[HSL002]` plus a trailing `ignore[HSL002]` on the same import, or two pragmas resolving to the same statement and rule. Today this fails closed (spec 001, `design/specs/001-llm-lint-extraction/design.md:303`; `_conflicting_claims`, `suppressions.py:394`): neither pragma suppresses, the finding stays visible, and each pragma gets `HSL900 conflicting suppression for X`. Overlaps never depend on rule selection, since a pragma for a disabled rule is never accepted. Ruff (checked 2026-11-10) doesn't fail closed: a file-level `# ruff: noqa: F401` plus a line `# noqa: F401` suppresses the finding, and with RUF100 on the extra pragmas are reported (`unused: F401`, `duplicated: F401`); with RUF100 off the run is clean. Pylint has no separate conflict check.

**Deciding factor:** match the peers, and never leave a finding the user suppressed visible with no explanation.

| | A: fail closed, report under HSL901 | B: suppress; HSL901 reports the overlap | C: fail closed, report under HSL900 |
|---|---|---|---|
| Finding with overlapping pragmas | visible | suppressed | visible |
| Explanation when HSL901 is off (the default) | none | not needed; nothing surprising is visible | always (HSL900) |
| Overlap noticed | only with HSL901 on | only with HSL901 on | always |
| Peers | differ (fail closed) | match Ruff and Pylint | differ |
| Engine | today's | drops the fail-closed exclusion from `suppressed`; overlap only adds a diagnostic | today's |

**Recommendation:** B, because it's the peer behavior and the only option where an opt-in rule can be off without leaving an unexplained visible finding.
**Pick C instead if** you want every overlap caught on every run regardless of selection. A is listed for completeness; it is the one combination that leaves a finding unexplained.
**Reversibility:** easy
**Ratified:** Chose B (overlapping pragmas suppress the finding; HSL901 reports the overlap when on) over failing closed under HSL900 or HSL901, to match Ruff and Pylint and never leave a suppressed finding visible unexplained, accepting that an overlap goes unnoticed while HSL901 is off; also accepting that HSL901 never reports stale or redundant project-rule pragmas (spec 010 D9), as `docs/suppressions.md` already states (challenge Finding 8).

### D4: Which of the overlapping pragmas does HSL901 report, and with what message?

**Situation:** Under D3, overlapping pragmas all suppress and HSL901 reports the overlap. Today every pragma in a conflict is reported as `conflicting suppression for X`, a word that no longer fits when nothing conflicts. Ruff reports only the redundant ones: with a file-level directive present, the line pragma gets `unused: F401`, and a code listed twice in one pragma gets `duplicated`.

**Deciding factor:** a report the user can act on in one step: delete the pragma it points at.

The `conflicting suppression for X` message is retired; the `unused suppression for X` and `unused suppression for disabled rule X` messages keep their text and change only rule ID.

| | A: every overlapping pragma, `redundant suppression for X` | B: all but one, picked by scope (file-level wins over statement; earlier wins among equals) |
|---|---|---|
| Can the user act by deleting each flagged pragma? | no; deleting all of them unsuppresses the finding | yes |
| Rule to explain | none | which one survives |
| Engine | reuses today's overlap sets, renames the message | adds an ordering over each overlap set |
| Peers | differ | match Ruff (the file directive survives; the line one is reported) |

**Recommendation:** B, because a report the user can act on by deleting what's flagged is the point of the rule, and Ruff does the same.
**Pick A instead if** you'd rather the user pick which pragma to keep, with fewer rules to document.
**Invariant:** B is safe because claim sets nest: an `ignore-file` claim contains any statement claim for the same rule, an `ignore-next` claim contains a trailing `ignore`'s on its statement, and same-target pragmas have identical or nested sets, the earlier containing the later (an earlier `ignore-next` window contains a later one's) (`_owns`, `suppressions.py:353-391`). So deleting every flagged pragma never changes what is suppressed; a test pins that the suppressed set is identical before and after removing the pragmas HSL901 flags as redundant.
**Reversibility:** easy
**Ratified:** Chose B (report all but one overlapping pragma, as `redundant suppression for X`; the broader scope survives, then the earlier) over reporting every one, to make each flagged pragma safe to delete as in Ruff (safe by the nesting invariant above, pinned by a test), accepting a survivor rule to document, and accepting that when overlapping pragmas all suppress nothing the survivor's `unused` report arrives alongside the `redundant` ones, and that on a multi-ID pragma the message names the pragma where the user should remove one ID (challenge Finding 5).

### D5: How does the catalog represent a rule that has no detector but handles Python?

**Situation:** `rule_catalog.py` has three shapes: `FileRule` (a detector, languages default Python), `ProjectRule` (a project run, declared languages), and a bare `Rule` used only by HSL900, which declares no languages (`rule_languages` returns `frozenset()` for it, `rule_catalog.py:269`). `applicable_rules` drops any rule that doesn't handle the file's language, special-casing only `ALWAYS_ON_RULE_ID` (`config.py:130`), and `discovered_languages` builds discovery from enabled rules' languages. The new rule emits from the suppression engine, has no detector, and applies to Python files only. As a bare `Rule` today it would be dropped from every file and `--select HSL901` alone would discover nothing.

**Deciding factor:** the rule's language is declared where every other rule's is, with no second special case.

| | A: `languages` on the base `Rule` | B: new `Rule` subclass for engine-emitted rules | C: `FileRule` with a no-op detector |
|---|---|---|---|
| New special cases in `config.py` | none; `rule_languages` reads one field for every rule | none, but `rule_languages`' isinstance check grows a third type | none |
| Catalog shapes | still three; `Rule` gains a required keyword-only field (HSL900 passes an empty set) | four | three, one row lies about having a detector |
| Detector dispatch | untouched (`selected_detector_inputs` reads `FILE_RULES` only) | untouched | runs a detector that returns nothing; the cache key and dispatch now include it |
| Reader load | one place to look for any rule's languages | a type that exists for one row | misleading |

**Recommendation:** A, because languages are a property of every rule, and moving the field to the base removes the isinstance branch instead of adding one.
**Pick B instead if** you expect more engine-emitted rules with their own extra fields. **Pick C instead if** never.
**Reversibility:** easy (internal)
**Ratified:** Chose A, made a required keyword-only `languages` field on the base `Rule` (challenge Finding 6): HSL900's row passes `languages=frozenset()`, HSL901's passes Python, the file-rule helpers supply Python, and the project-rule helpers pass it by keyword. Chosen over a new `Rule` subclass for engine-emitted rules or a no-op `FileRule`, to keep one place that declares every rule's languages and keep a forgotten `languages` a `TypeError` at import, accepting churn in every rule helper and HSL900's row.

### D6: Can a pragma suppress HSL901's findings?

**Situation:** Today no pragma can silence a suppression diagnostic: diagnostics are added after claims are resolved (`apply_suppressions`, `suppressions.py:106-121`), and `_parse_pragma` rejects any pragma naming HSL900 (`suppressions.py:240`). Ruff honors `# noqa: E501, RUF100`, file-level `# ruff: noqa: RUF100`, and per-file-ignores; Pylint honors `disable=...,useless-suppression`. Neither ever reports a pragma naming its stale rule as itself unused: `x = 1  # noqa: RUF100` and `# pylint: disable=useless-suppression` alone both pass clean (all checked 2026-11-10). house-lint's findings are deterministic per file content, so a pragma unused now stays unused until the code changes.

**Deciding factor:** match the peers, as D2 and D3 did.

| | A: no; config and CLI only | B: `ignore-file` only | C: any pragma action, like peers |
|---|---|---|---|
| Ways to turn it off | `select`/`ignore`/CLI/`per-file-ignores` | those plus a file-level pragma | those plus inline pragmas |
| Engine change | small: `_parse_pragma` rejects HSL901 as it rejects HSL900 | a claim pass over HSL901 diagnostics, file scope only | HSL901 diagnostics are built with their pragma's statement owner (not `NO_OWNER`) and the existing `_collect_claims`/`_owns` resolution runs over them once: `ignore-file[HSL901]` claims all in the file; a statement pragma listing HSL901 claims the HSL901 diagnostics on pragmas with the same statement owner |
| Self-reference (a pragma naming HSL901 that suppresses nothing) | can't arise | settled as in C | never reported, as Ruff and Pylint do, and as project-rule pragmas already are (spec 010 D9) |
| Peers | differ | partial | match |
| Known use case | path-level opt-out | same, kept in the file | a pragma deliberately kept while unused; none known here |

**Recommendation:** C, because the user has chosen peer behavior throughout, and the peers' answer to self-reference (never report it) makes it cheap: one claim pass over HSL901's own diagnostics.
**Pick A instead if** you'd rather not add an engine pass with no known inline use case. **Pick B instead if** you want only a file-level opt-out in the file.
**Reversibility:** easy (adding or removing pragma support later is local)
**Ratified:** Chose C (any pragma action can suppress HSL901), built by giving each HSL901 diagnostic its pragma's resolved owner (`_Pragma.owner`: the statement a trailing `ignore` or an `ignore-next` owns; an `ignore-file` pragma has none, so only `ignore-file[HSL901]` claims its diagnostics) and running the existing claim resolution over them once (challenge Finding 2): `ignore-file[HSL901]` claims every HSL901 diagnostic in the file, and a statement pragma listing HSL901 claims the HSL901 diagnostics on pragmas sharing its statement owner. Pragmas naming HSL901 never enter unused or overlap reporting (as project-rule pragmas don't, `suppressions.py:214`), and suppressed HSL901 diagnostics don't count in `suppressed_count`, since they were never candidates. Chosen over config-only or file-only, to match Ruff and Pylint with no new matching code, accepting that a statement-level HSL901 pragma covers every pragma on its statement, not only itself. (The sketch-time challenge's Finding 3 accepted that suppressed HSL901 reports could still spend the candidate budget; the build claims them first, so they don't, as the Build section records and a test pins.)

### D7: Does a CLI `--select` still take the config's `extend-select` and `extend-ignore`?

**Situation:** Raised during the build. `_effective_rule_selection` (`config.py`) layers config `extend-select`/`extend-ignore` on top of a CLI `--select`, as `docs/configuration.md` step 6 documents. So a consumer following D2 (`extend-select = ["HSL901"]`) still runs HSL901 under `--select HSL102`, and #91's per-rule gate fails on every pragma naming another rule. Ruff 0.16.2 (probed 2026-11-12): `ruff check --select E711` with `extend-select = ["RUF100"]` in config runs no RUF100.

**Deciding factor:** D2's advice to consumers has to leave per-rule gates working.

| | A: CLI `--select` replaces config `select`, `ignore`, `extend-select`, `extend-ignore` | B: keep; consumers list HSL901 in `select` | C: keep; gates pass `--ignore HSL901` |
|---|---|---|---|
| #91 gate under D2's advice | fixed | fixed only with `select` | needs an extra flag per gate |
| Consumer config | `extend-select = ["HSL901"]` | must list every default rule | as D2 |
| Code | config extends dropped under `--select`; CLI `--extend-*` still layer | none | none |
| Peers | match Ruff | n/a | n/a |

**Recommendation:** A, because it keeps D2's advice working and matches Ruff, inside a release already breaking.
**Pick B instead if** selection semantics must not change.
**Reversibility:** easy (one function), though itself breaking
**Ratified:** Chose A (a CLI `--select` replaces every configured selection key; CLI `--extend-select`/`--extend-ignore` still layer) over consumers listing HSL901 in `select` or gates passing `--ignore HSL901`, to keep D2's `extend-select` advice compatible with per-rule gates as in Ruff, accepting one more selection-semantics break in the same `feat!`.

## Assumed

- Rule selection already flows per file into the suppression engine: `scan_source` passes `file_enabled_rules`, the output of `applicable_rules`, which applies per-file-ignores. Evidence: `pipeline.py:354-358`, `config.py:107`.
- Any non-`always` rule is accepted in `select`, `ignore`, `extend-select`, `extend-ignore`, `per-file-ignores`, and the CLI flags, so the new rule needs no config-schema change. Evidence: `ORDINARY_RULES` (`rule_catalog.py:250`), `_ids` (`config.py:181`).
- `DEFAULT_SELECT` derives from enablement, so an opt-in row stays out of the default selection without a second list. Evidence: `rule_catalog.py:247`.
- house-lint's own config selects no rules explicitly, so it runs the default set and needs `extend-select = ["HSL901"]` to keep checking its own pragmas. Evidence: `pyproject.toml` `[tool.house-lint]`.
- Exactly one `always` rule remains (HSL900), so the startup assertion and `ALWAYS_ON_RULE_ID` hold. Evidence: `rule_catalog.py:255-261`.
- The cache key covers the file's applicable rule set, so selecting or ignoring the new rule invalidates cached results. Evidence: `_cache_keys` (`pipeline.py:571`), `hash_effective_config` (`cache.py:150`).
- A pragma naming a project rule is never reported unused (spec 010 D9); that's unchanged. A pragma naming a disabled project rule is a disabled-rule diagnostic like any other and moves with the rest. Evidence: `_collect_claims` (`suppressions.py:206`), CLAUDE.md Gotchas.
- `claims_for` scans pragmas with only the project rule enabled and discards the diagnostics, so gating the stale diagnostics doesn't change which occurrences it claims. Evidence: `suppressions.py:138-160`.
- `--base` pairs findings by rule ID, message, and span text through the default matcher, so the stale rule needs no `base_match`. Evidence: `Rule.base_match` default (`rule_catalog.py:68`), spec 014 D4.
- Selecting only the stale rule (`--select HSL901`) reports every pragma naming a known Python rule other than HSL901 as naming a disabled rule, since no other rule runs; unknown-rule and wrong-language pragmas stay HSL900. That matches Ruff's `--select RUF100`. Evidence: `scan_pragmas` disabled check (`suppressions.py:197`).

## Build

- [x] Implementation and tests committed, including `extend-select = ["HSL901"]` in house-lint's own `[tool.house-lint]` (`pyproject.toml:59`) so it keeps checking its own pragmas
- [x] Docs: `docs/suppressions.md` (split the HSL900 section; replace fail-closed conflicts with D3/D4 behavior; HSL901 suppressibility per D6), `docs/rules.md` (including the rule counts at `:3`: five opt-in rules), `docs/configuration.md` (`:15`, `:67`, `:81`, `:85`), the README rules table and line 100, CLAUDE.md (the ID convention line and the HSL900 gotcha), and dated `## Addendum` entries on spec 001 (archived; fail-closed conflicts) and spec 010 (built; D9 now scopes HSL901)
- [x] Ship-time challenge

**Calls made during the build:**
- Overlap groups are the old conflict groups (same target and rule, or one shared candidate): an `ignore-file` and a statement pragma that both claim nothing don't overlap, so each is reported `unused`. Nothing claims a shared target there, and deleting either changes nothing.
- HSL901 reports are claimed by pragmas naming HSL901 before they reach the diagnostic budget, so a file whose HSL901 reports are all suppressed never spends budget on them. D6 only accepted the opposite as a risk; it didn't require it.
- D4's survivor is picked from claim data, not from scope alone: the claim owning the most candidates survives, scope then position break ties, and a claim is flagged only when its candidates are a subset of the survivor's (ship-time challenge Finding 2). With today's nesting this flags exactly what "broader scope, then earlier" did; it keeps the safe-to-delete promise if `_owns` ever stops nesting, by reporting less instead of advising a wrong deletion.
- `scan_pragmas` builds disabled-rule reports only when HSL901 is enabled; `claims_for`'s single-rule scan therefore builds none.

## Addendum

**2026-11-12 (PR #93 review).** Review found the redundancy check quadratic in findings and `docs/rules.md` wrong about project-rule pragmas. Both mechanisms had drawn a finding at every gate, so they were checked against peers (`design/research/2026-11-12-stale-suppression-redundancy/prior-art.md`), and both now follow them:
- **D4's survivor groups are replaced by one-owner credit.** Each finding is credited to one owning pragma (most findings, then broadest scope, then earliest), as Ruff, ESLint, and Pylint credit a finding to one directive. A pragma credited with nothing is `redundant` if it suppresses findings and `unused` if it doesn't. It still keeps D4's safe-to-delete promise, and runs in time linear in the findings. One report changes: two pragmas that share a target and suppress nothing are both `unused`, where one was `redundant`.
- **HSL901 never reports a pragma naming a project rule,** including one the run leaves out, as Pylint exempts `duplicate-code` from `useless-suppression`. This supersedes the Assumed line that a disabled project-rule pragma "moves with the rest". It removes the README's advice to run HSL901 only in the `HSL104` pre-push hook. An `ignore[HSL104]` in a project that never runs `HSL104` now goes unreported.
