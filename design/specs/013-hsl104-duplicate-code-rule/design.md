# Design: HSL104 duplicate-code rule

**Date:** 2026-11-09
**Status:** ratified
**Mode:** sketch
**Issues:** #39
**Parent:** design/specs/005-cross-file-rules-and-duplicate-code/design.md

## Summary

Add `HSL104`, an opt-in project rule that reports Python code copied across a project. It wraps jscpd 5.4.1 and does not hand-build a detector. This is Breakdown item 7 of the parent milestone ledger. Read that ledger together with the 2026-11-09 Addendum in `design/specs/012-typescript-analysis-and-pragmas/design.md`, which narrows the rule to Python and drops item 6 (#61).

The engine work this rule needs has landed:
- the pipeline module (`pipeline.py`, spec 006)
- bytes-only `LoadedFile` (spec 009)
- rule-declared options (spec 008)
- project rules and the project phase (spec 010)
- project-rule pragmas and `related` (spec 011)

`PROJECT_RULES` in `rule_catalog.py` is still empty, so `HSL104` is its first row.

What this change adds:
- **jscpd adapter** (new `src/house_lint/jscpd.py`, D2):
  - finds the binary and checks its version
  - writes a jscpd config listing the input files
  - runs jscpd with a timeout and parses its JSON pairs into typed fragments
- **The rule** (new `src/house_lint/rules/duplicate_code.py`):
  - `HSL104Options` (D4)
  - groups pairs into clusters (D1)
  - drops clusters under the occurrence minimum
  - builds one `ProjectCandidate` per cluster (D5)
- **Catalog row:** one `configurable_project_rule(...)` row for `HSL104` in `rule_catalog.py`, opt-in, `languages={Language.PYTHON}`.
- **Full-scope pass for explicit paths** (D3, home: `pipeline.py`). Parent D10 promised it. Spec 010 left it to #39 (its Assumed list), and today project inputs come from the discovered files only.
- **Text reporter:** a cap on the `(also at ...)` suffix (D9, home: `reporters/text.py`).
- **Packaging:** a `duplicates` extra in `pyproject.toml` pinning `jscpd==5.4.1`.
- **Defaults:** the option defaults (D4, D8). The tuning record is `tuning-pass.md`.
- **Docs:** `docs/rules.md`, `docs/configuration.md`, `docs/suppressions.md` if needed, the README rules table, pre-push hook guidance, and `CLAUDE.md` (the "planned (#39)" line, the Layout list gaining `jscpd.py`, and the project-rule notes under "Adding a rule").

Out of scope:
- finding identity and `--base` (#52, parent D16)
- TypeScript (012 Addendum)
- a group id (spec 011 D3)
- jscpd's own `jscpd:ignore` markers, left undocumented as in the parent's Assumed list
- a docstring filter and generated-file banner detection (parent D19)
- caching project-rule results (parent D11)
- a candidate budget for project rules

## Posture

- **Failure handling:** fail loudly, carried from the parent posture. When `HSL104` is enabled and jscpd is missing, the wrong version, times out, exits non-zero, or emits output that doesn't parse, the rule raises `ProjectRuleFailure` with a message naming the `house-lint[duplicates]` extra. Other rules' findings still report, and the run fails. There are no retries and no fallback detector, and the subprocess has an explicit timeout.
- **Compatibility:** break freely, though this change is additive: an opt-in rule and an optional extra. The JSON schema doesn't change, since `related` already ships and there is no group id. Projects that don't enable `HSL104` see identical findings, file counts, and messages.
- **Scope:** parent Breakdown item 7 (the runner and its `duplicates` extra, the rule, the full-scope pass for explicit paths, and docs), plus one addition ratified at the sketch challenge: the text reporter's cap on related locations (D9). The tuning pass ran during the sketch (D8). Nothing beyond that (see Summary, out of scope).
- **Rollout:** ship outright. The rule is opt-in, so no flag or staging is needed.
- **Hardening:** project defaults. Parent D9 accepted no integrity check on the files jscpd reads. It's a CLI, so errors reach the terminal and no logging is added.
- **Altitude:** build. One session builds it, and this ledger cites files.

## Decisions

### D1: How are jscpd's pairs grouped into clusters?

**Situation:** jscpd reports pairs of fragments, not clusters, and it reports each later copy against one earlier copy only. Probed this session with three identical functions in `a.py`, `b.py`, and `c.py`: jscpd 5.4.1 reported `a–b` and `a–c` and never `b–c`. Fragments of the same code also overlap. When copy C holds only the second half of a block that A and B share in full, jscpd reports `A:1-30 / B:1-30` and `A:10-30 / C`. Parent D17 counts occurrences per cluster, with a default minimum of 3. Parent D16 notes that whatever grouping is chosen, #52 has to cope with merged spans. The parent's hassette measurements (`followup-tool-selection.md`) used `probe/scripts/clusters.py`. That script merges overlapping fragments within a file into one node and then union-finds the pairs.

**Deciding factor:** a 3-copy duplicate is counted as 3, including partial copies, with no region counted twice.

| | A: Merge overlapping fragments within a file into one occurrence, then group pairs transitively (the probe's `clusters.py`) | B: Group pairs transitively by exact fragment span; no merging | C: A cluster is a set where every pair was reported (clique) |
|---|---|---|---|
| Three identical copies (star-shaped pairs) | 1 cluster of 3 | 1 cluster of 3 | Never formed: `b–c` is never reported |
| Partial third copy (A and B full, C half) | 1 cluster of 3 | Two clusters of 2, both under the minimum, so nothing reports | Not formed |
| One region counted more than once | Never | Yes: `A:1-30` and `A:10-30` are separate occurrences, so A can report twice | n/a |
| Chaining: a merged span joins clusters through a thin overlap | Common: on hassette at 30 tokens, 230 of 483 merges overlap by under 5 lines, and 65 of 150 groups of 3+ depend on such a merge. In the classified sample, the groups it forms were judged real (copied tests with small variations; `tuning-pass.md`, Chaining) | No | No |
| An occurrence's span | The union of its fragments, which can be longer than any single match | The exact matched fragment | Exact |
| Matches the parent's tuning measurements | Yes | No | No |

A pair whose two sides merge into the same occurrence (a self-overlapping repeat in one file) adds nothing. Two non-overlapping copies in one file are two occurrences.

**Recommendation:** A. It is the only option that counts partial copies correctly without double-counting, and it is what the parent's numbers were measured with, so D17's thresholds carry over.
**Pick B instead if** you'd rather report exact matched spans and accept that partial third copies go unreported and one region can report twice. C is listed only to record why it fails: jscpd's star-shaped output never forms a clique.
**Reversibility:** easy (internal to the rule; changes counts, not interfaces)
**Ratified:** Chose merging overlapping fragments within a file and grouping pairs transitively over exact-span grouping and cliques, so partial copies count and no region counts twice, matching the parent's measurements, accepting that an occurrence's span can be longer than any single match and that overlapping fragments can chain clusters. At the sketch challenge (Finding 14), merging only overlaps of at least `min_lines` was measured on hassette and rejected: it split 65 of 150 groups and dropped 3 of the 12 sample groups classified real, while removing none classified false (`tuning-pass.md`, Chaining).

### D2: Where do the jscpd invocation and the rule live?

**Situation:** The parent's Summary names "the jscpd adapter and an external-tool runner: a new module". The only external tool today is git, run by `discovery.run_git` with no timeout. #53's dead-code half may reuse jscpd's `--dead-code` mode. Nothing else plans another external tool.

**Deciding factor:** each piece has one obvious home, with no layer that has a single consumer.

| | A: `jscpd.py` (find, version-check, run, parse into typed fragments) plus `rules/duplicate_code.py` (options, clustering, candidates) | B: A generic `external_tool.py` runner (lookup, version check, timeout) plus a jscpd adapter plus the rule module | C: All of it in `rules/duplicate_code.py` |
|---|---|---|---|
| Layers between "why did HSL104 fail?" and the answer | 2 | 3 | 1 |
| Reuse by #53's dead-code mode | Imports the adapter | Imports the adapter | Has to extract the jscpd half first |
| Generic code with one consumer | None | The runner | None |
| Clustering testable without a subprocess | Yes: the rule takes parsed fragments | Yes | Only by splitting functions inside the module |
| Rule module size | Small | Small | Larger; mixes subprocess handling with grouping |

**Recommendation:** A. It separates the subprocess from the grouping logic and leaves jscpd reusable for #53, without a generic runner that only one tool uses. It supersedes the "external-tool runner" in the parent's Summary. That was never a ratified decision.
**Pick B instead if** you expect a second external tool (not jscpd) soon. **Pick C instead if** you'd rather defer the split until #53 actually needs jscpd.
**Reversibility:** easy
**Ratified:** Chose a `jscpd.py` adapter plus `rules/duplicate_code.py` over a generic external-tool runner or a single module, so the subprocess and the grouping each have one home and #53 can reuse the adapter, accepting that a second external tool would need its own adapter.

### D3: With explicit paths, what files do project rules see, and what counts as scanned?

**Situation:** Parent D10 (ratified) says that with explicit paths, project rules analyze the full configured scope, and only findings with a location in the given paths are reported. Today `run_scan` calls `discover_files(..., explicit=paths)` once, and `run_file_phase` adds each loaded file to project inputs, so a hook run hands jscpd only the staged files. Two details are open:

1. **Input set.** Explicit paths already skip excluded files (`discovery._consider_requested`). But a path outside a narrowed `include` (say `include = ["src"]` and the user passes `scripts/x.py`) is accepted explicitly and isn't in the full-scope pass.
2. **Counts.** `files_scanned` appears in text and JSON output. Files loaded only for project rules raise the question of whether they count.

**Deciding factor:** a hook run reports exactly what a full run reports for the files it was given, and the summary line describes those files.

| | A: Project input = full-scope discovery ∪ the explicit set; report only findings located in the explicit set; `files_scanned` counts the explicit set only | B: Project input = full-scope discovery only; same filter and count | C: Like A, but `files_scanned` also counts the files loaded only for project rules |
|---|---|---|---|
| A named file outside `include` | Analyzed and reported on | Can't be in a cluster, so it silently reports nothing | Same as A |
| Matches a full run for the named files | Yes, when the named files are in scope | Same | Same |
| `files scanned N` after `house-lint check a.py` | 1 | 1 | Hundreds, for one named file |
| `files_scanned` unchanged for projects without `HSL104` | Yes | Yes | Yes |
| Second discovery pass | Only when explicit paths are given and at least one named file is in an enabled project rule's input | Same | Same |
| A file that fails to load in the extra set | Left out of the input, with a `--debug` line | Same | Same |

In every option, files loaded only for project rules get no file rules, no cache entry, and no HSL900. The parent D21 applies-check (per-file-ignores) still decides whether each one enters a project rule's input, and each file is still read once.

**Mechanism (reworked at the sketch challenge, Finding 1, which converged Findings 2-8):**
1. **The pass stays.** Without it, a pre-push entry would have to run with `pass_filenames: false` and report the whole backlog (150 groups of 3+ on hassette, `tuning-pass.md`) on every push until `--base` (#52) lands.
2. **Home.** A project-side step in `pipeline.py`, called from `run_scan` after `run_file_phase`, extends each project rule's input. It runs `discover_files` over the configured scope (no `explicit`) for the enabled project rules' languages. It skips files whose resolved path is in the first pass's `discovered.files` (their `.resolved`, which `run_scan` holds; `FilePhaseResult` doesn't change), loads the rest with `source.load_file`, applies the same per-file applies-check as the file phase, and appends each file to the inputs of the rules that apply to it. `run_file_phase`, `FilePhaseResult`, and `files_scanned` don't change.
3. **Trigger.** The step runs only when explicit paths are given, the file phase did not abort, and at least one named file is in an enabled project rule's input. A project rule with no named file in its input does not run, the same as a rule with no input today.
4. **Identity.** Files are matched by resolved path, the identity discovery already uses (`DiscoveredFile.resolved`). The named file's spelling is the one reported. The jscpd adapter gives jscpd resolved paths and maps jscpd's names back on them, so a `..` or symlinked spelling can't make a file a copy of itself.
5. **Errors in unnamed files.** Two kinds, handled differently:
   - **Per-file problems are dropped.** A file in the extra set that fails to load is left out of the input, with a `--debug` line, and the second pass's `discovered.errors` list is dropped too. HSL104 still matches a full run, where a failing file isn't in jscpd's input either.
   - **A failed pass is an error.** If the second `discover_files` raises `DiscoveryError`, that becomes a project-rule error for each project rule that would have run, and file-rule results still report.
6. **Report filter.** `expand_candidates` takes the set of reportable files (every input file when no explicit paths were given), keyed by the `LoadedFile` objects the file phase produced, since a `Location` holds its input record. `related` is built from every location of a candidate. A location outside the set gets no finding, no pragma check, and no suppressed count, so the summary describes only the named files.
7. **Accepted limitations.**
   - The extra files' bytes are loaded only to satisfy the project-rule input type (`project_rule.Location.file: LoadedFile`). That is cheap at hassette's scale, about 1,100 files.
   - A push large enough for pre-commit to split the file list runs the step and jscpd once per batch, serially (`require_serial: true`).

**Recommendation:** A. A file the user named is always analyzed, and the count keeps meaning "the files this run reports on".
**Pick B instead if** you'd rather treat a named file outside `include` as out of scope for project rules too. **Pick C instead if** you want the summary to show how much the project rule read.
**Reversibility:** easy
**Ratified:** Chose project input as the full scope plus the named files, reporting and counting only the named files, over full scope alone or counting every loaded file, so a named file is always analyzed and `files_scanned` keeps meaning the files reported on, accepting a second discovery pass whenever explicit paths meet an enabled project rule. The mechanism (the project-side step, resolved-path identity, dropping errors from unnamed files, the reportable-file filter) was reworked and approved at the sketch challenge (Finding 1), accepting that the extra files' bytes are loaded unused and that a split hook batch repeats the pass.

### D4: Which options does `HSL104` expose?

**Situation:** Parent D17 names two options: a minimum number of occurrences (default 3) and a token floor (default from the tuning pass, D8). jscpd also takes `--min-lines` (default 5), and the subprocess needs a timeout (D6). Existing options are snake_case fields on a frozen `RuleOptions` subclass with `Annotated[..., Meta(...)]` bounds (`HSL102Options.max_lines` in `rules/file_length.py`), set under `[tool.house-lint.rules.HSL102]`.

**Deciding factor:** only knobs a project has a reason to turn, each bounded.

| | A: `min_tokens`, `min_occurrences` | B: A + `min_lines` | C: B + `timeout_seconds` |
|---|---|---|---|
| Covers parent D17 | Yes | Yes | Yes |
| Knob with a known reason to change | Both: test-heavy repos raise the floor; a team that wants pairs sets 2 | Measured at the sketch challenge (Finding 15): at 30 tokens on hassette, moving `min_lines` from 5 to 8 cuts pairs 58%, and from 5 to 1 adds 51% (`tuning-pass.md`, Line floor) | A timeout only matters on a huge repo, and nothing has measured one |
| Config surface to document and validate | 2 fields | 3 | 4 |
| Adding a field later | Additive | Additive | n/a |

Bounds: `min_occurrences` ≥ 2, since a cluster of one is not a duplicate. `min_tokens` ≥ 1. `min_lines` ≥ 1. Each has an upper bound in the style of `MAX_LINES_LIMIT`. The upper values are a build-time call, recorded under the Build section's calls.

**Recommendation:** A. Both fields come from parent D17. `min_lines` stays at jscpd's default of 5, and the timeout is a constant (D6).
**Pick B instead if** you expect to want short-but-wide matches flagged. **Pick C instead if** you run house-lint on a repo much larger than hassette.
**Reversibility:** easy
**Ratified:** Chose `min_tokens`, `min_occurrences`, and `min_lines` (default 5, jscpd's own, bounded ≥ 1 with an upper limit) over the two-field set or adding a timeout, so a project can tune both of jscpd's size floors, accepting a third field to document and validate. The timeout stays a constant (D6). Kept at the sketch challenge (Finding 15), after measuring that the line floor shapes the output as much as the token floor at the 30-token default.

### D5: What span and message does each finding carry?

**Situation:** Each cluster becomes one `ProjectCandidate`, and the pipeline expands it to one finding per occurrence, with the others in `related` (spec 010 D2, spec 011). The text reporter appends `(also at path:line:col, ...)` (spec 011 D4), so the message doesn't need to list locations. `validate_location` requires all four coordinates or none, 1-based. jscpd reports `startLoc`/`endLoc` columns as 0-based UTF-8 byte offsets, end exclusive (Assumed, jscpd output). Probed: a renamed function's match started at column 12, the `(` after `def handle_a`. A match can start mid-line. Under D1 A, an occurrence is the union of its fragments: from the earliest fragment start to the latest fragment end, each with its own column (on a tie, the smaller start column and the larger end column), matching `clusters.py`'s `max(end)` merge.

**Deciding factor:** the finding points the editor at the duplicated block, and the message says what is duplicated.

| | A: Span from the earliest fragment start to the latest fragment end, columns converted to 1-based; message `duplicated block of N lines, M copies` | B: Whole lines: column 1 at the start line to the end of the end line; same message | C: Like A, with the message giving jscpd's token count |
|---|---|---|---|
| Editor jump lands on | The exact first matched token, possibly mid-line | Start of the line | Same as A |
| Faithful to jscpd | Yes | Rounded to lines | Yes |
| Message for a merged occurrence | Line count is exact | Same | No single token count exists for a merged occurrence |
| Pragma ownership (spec 011 D5) | Keyed on the start line | Same | Same |

Copies of one cluster can differ in length (partial copies, D1), so N is this occurrence's own line count. The candidates and the locations inside each one are ordered by path and then line, so the output is deterministic.

**Recommendation:** A. It reports what jscpd matched, and the message stays meaningful for merged occurrences.
**Pick B instead if** mid-line starts look wrong in your editor. **Pick C instead if** you'd rather report token counts and accept that merged occurrences have none.
**Reversibility:** easy
**Ratified:** Chose the exact jscpd span converted to 1-based, with the message `duplicated block of N lines, M copies`, over whole-line spans or a token-count message, so the editor lands on the matched code and the message holds for merged copies, accepting that a span can start mid-line.

### D6: What is the jscpd timeout?

**Situation:** The posture requires an explicit timeout. On hassette, jscpd over Python and TypeScript took 3.3–3.9s, and up to 12s under load (`followup-tool-selection.md`, wall time).

**Deciding factor:** never fires on a slow but healthy run, and still bounds a hang.

| | A: 120s constant | B: 30s constant | C: 600s constant |
|---|---|---|---|
| Headroom over the worst measured run (12s) | 10× | 2.5× | 50× |
| How long a hung jscpd blocks a hook | 2 min | 30s | 10 min |
| Risk of a false timeout on a big repo or loaded machine | Low | Real | Negligible |

**Recommendation:** A, as a module constant in `jscpd.py`. A timeout raises `ProjectRuleFailure` naming the limit.
**Pick B instead if** a hook should never stall more than half a minute. **Pick C instead if** you run house-lint on repos far larger than hassette.
**Reversibility:** easy
**Ratified:** Chose a fixed 120-second constant over 30 or 600 seconds, so a loaded machine never trips it while a hang still ends within two minutes, accepting that a hung jscpd can stall a hook for that long.

### D7: Does the test suite run real jscpd?

**Situation:** The rule's behaviour depends on what jscpd 5.4.1 actually emits: star-shaped pairs, 0-based columns, and exit 0 when duplicates are found. Those facts were checked by hand this session. The failure paths (missing binary, wrong version, timeout, non-zero exit, bad JSON) are awkward to trigger with the real tool. CI runs on Linux runners whose glibc is above jscpd's 2.34 floor.

**Deciding factor:** the behaviour pinned is the real tool's, not a stub's idea of it.

| | A: Add `jscpd==5.4.1` to the `dev` group, matching the extra's pin; real-jscpd tests pin the end-to-end behaviour, and a fake binary covers the failure paths | B: Fake binary only, replaying recorded JSON | C: Real jscpd only, with tests skipped when it's missing |
|---|---|---|---|
| Catches a wrong assumption about jscpd's output | Yes | No: the recording embeds the assumption | Yes |
| Failure paths covered | Yes | Yes | Only "missing" |
| CI and dev install | +1 wheel (about 20MB) | None | Same as A, or silently skipped |
| Silent skips | None | None | Possible |

**Recommendation:** A.
**Pick B instead if** the dev install size matters. **Pick C instead if** you want jscpd optional for contributors too.

At ratification, C's contributor argument was dropped, since the maintainer is the only developer. A variant C′ was weighed: real jscpd always installed, with no skips and no fake. It tests only the missing-binary error. The behaviour to pin is each loud failure the posture lists: missing binary, wrong version, timeout, non-zero exit, and unparsable output, each naming the extra. The fake never stands in for jscpd's real output.
**Reversibility:** easy
**Ratified:** Chose real jscpd in the `dev` group for behaviour tests plus a small fake binary for the error paths, over a fake-only suite or real-only (C′), so the tests pin jscpd's actual output and every loud-failure message the posture promises, accepting one ~20MB dev wheel and a short fake script.

### D8: What is the default `min_tokens`?

**Situation:** Parent D17 fixes the method. Run jscpd on hassette (generated files excluded, weak mode, no gap merging) at 30, 40, and 50 tokens. Keep groups of 3+ copies using D1's grouping, and hand-classify a sample of 15 per floor. The default is the lowest floor whose sample is at least two-thirds real duplication. The pass ran at sketch time, so the default is ratified, not a build-time call. Setup, per-floor counts, and every sampled group with its class are in `tuning-pass.md` in this directory. The sample was pooled and shuffled, so the classifier never saw which floor a group came from.

**Deciding factor:** the parent's two-thirds-real bar.

| | A: 30 | B: 40 | C: 50 (jscpd's default) |
|---|---|---|---|
| Sample real share | 12/15 (80%) | 6/15 (40%) | 6/15 (40%) |
| Groups of 3+ on hassette | 150 | 64 | 28 |
| Typical finding | Repeated test setup that a fixture or `parametrize` would remove | About half deliberate repetition: async/sync wrappers, per-method docstrings, overloads, import lists | Same as B |
| Meets the parent's bar | Yes, with margin (10 needed) | No | No |

**Recommendation:** A. It is the only floor that clears the bar. Lower floors are cleaner because the large groups that survive higher floors are mostly deliberate repetition.
**Pick C instead if** a small backlog matters more than precision until `--base` (#52) exists, accepting that about half the findings need a pragma rather than a fix.
**Reversibility:** easy (the default is config)
**Ratified:** Chose a default `min_tokens` of 30 over 40 or 50, since it is the only floor whose sample cleared parent D17's two-thirds-real bar (80%), accepting a larger whole-backlog finding count (150 groups of 3+ on hassette) until `--base` gates new code.

### D9: How many related locations does a text finding list?

**Situation:** Spec 011 D4 has the text reporter append every related location: `a.py:3:1: HSL104 <message> (also at b.py:10:1, c.py:7:5)`. A cluster of N copies prints N lines, each listing N−1 locations. At the 30-token default, hassette has a 21-copy cluster (`tuning-pass.md` G39), which prints 21 lines of 20 locations each. JSON is unaffected. Spec 011 D4 weighed only a 10-copy cluster. Added at the sketch challenge (Finding 13).

**Deciding factor:** a text line stays readable at any cluster size, while JSON keeps the full cluster.

| | A: Cap the text suffix at 3 locations, then `and N more`; JSON unchanged | B: Keep spec 011 D4's rendering |
|---|---|---|
| The 21-copy cluster | Each line names 3 others plus `and 17 more` | Each line lists 20 |
| JSON | Unchanged | Unchanged |
| Scope | Amends spec 011 D4 for every project rule (only HSL104 exists) | In scope as is |
| Reversibility | Easy; text output isn't a versioned contract | Easy |
| What peers do | pylint R0801 and PMD CPD print one report per group, each location once | Same |

**Recommendation (at the challenge):** B, as cosmetic and deferrable until the first hassette run.
**Pick A instead if** you read HSL104 output in a terminal.
**Reversibility:** easy
**Ratified:** Chose capping the text reporter's `(also at ...)` suffix at the first 3 related locations in their existing order, followed by `and N more`, over spec 011 D4's full list, so a line stays readable for any cluster size, accepting a change to the text reporter that supersedes spec 011 D4's rendering for large clusters. JSON keeps every location. A cluster with 3 or fewer other locations renders exactly as before. The cap of 3 is a build-time constant in `reporters/text.py`.

## Assumed

Carried from the parent ledger (`design/specs/005-cross-file-rules-and-duplicate-code/design.md`) and sibling pickups:

- **D8, provisioning:** a `duplicates` extra pins `jscpd==5.4.1`. The rule looks for the binary next to house-lint's interpreter (`sys.executable`'s directory), then on `PATH`. It refuses any version but 5.4.1, with an error naming the extra and showing the `uvx --from 'house-lint[duplicates]' house-lint check` form. Evidence: parent D8.
- **D9, content:** jscpd reads the real files, with no integrity check. A file edited mid-run, or one with a bare `\r`, form feed, or BOM, may put a location where a pragma doesn't claim it, and that shows as a visible finding. Evidence: parent D9.
- **D10, explicit paths:** project rules analyze the full configured scope, and with explicit paths only findings located in those paths report. Docs recommend a pre-push entry with `args: [--select, HSL104]` (as parent D10 wrote it, so pre-push runs only HSL104), `stages: [pre-push]`, and `additional_dependencies: ["jscpd==5.4.1"]`. The hook is `language: python`, so pre-commit's environment has no jscpd without that line. There is no new hook id. D3 settles the details. Evidence: parent D10.
- **D11, caching:** nothing from the project phase is cached. jscpd runs every scan that enables `HSL104`. Evidence: parent D11. `run_project_phase` already caches nothing.
- **D12, findings:** one finding per occurrence, with `related`. The group id is superseded: spec 011 D3 dropped it, so this change adds no group field and no `schema_version` bump.
- **D13, suppression:** a pragma hides only its own occurrence, and suppressed copies still count toward the cluster's size. This holds by construction, because the rule counts occurrences before `pipeline.expand_candidates` applies pragmas. Evidence: parent D13, `pipeline.expand_candidates`.
- **Unused pragmas:** a pragma naming `HSL104` is never reported as unused. Evidence: spec 010 D9, and `docs/suppressions.md` "A pragma naming a project rule is never reported as unused".
- **Pragma ownership:** an occurrence's owner is found by its start line, through `suppressions.claims_for`. Evidence: spec 011 D5.
- **D16, identity:** this change adds no identity. That is #52's. Evidence: parent D16.
- **D17, matching:** the minimum occurrence count defaults to 3. jscpd always runs `--mode weak` with no `--max-gap-lines` and no identifier or literal normalization. Evidence: parent D17.
- **D18:** the ID is `HSL104`, opt-in. Evidence: parent D18.
- **D19:** docstrings count as code, and generated files are handled with `exclude` and `per-file-ignores`. Evidence: parent D19.
- **D21:** per-file-ignores keep a file out of a project rule's input entirely. Evidence: parent D21, and `pipeline.run_file_phase`, which adds a file to a rule's input only when `rule_id in file_enabled_rules`.
- **Languages:** the rule covers Python only. Evidence: the 012 Addendum, superseding parent D7. Python is the only language the catalog row declares.

Facts about the code and the tool:

- **Rule shape:** a project rule is `(files: tuple[LoadedFile, ...], options) -> list[ProjectCandidate]`, and it raises `ProjectRuleFailure` for failures the user can act on. A rule with no files is not run. Evidence: `project_rule.py`, `pipeline.run_project_phase`, `rule_catalog.configurable_project_rule`.
- **Paths:** a `Location` holds the `LoadedFile` from the rule's own input, so jscpd's names must map back to input records, keyed on resolved path (D3 mechanism, point 4). `LoadedFile` carries no resolved path, so the adapter resolves each input path for keying. A path jscpd reports that isn't in the input is a rule failure, per the posture. Evidence: `project_rule.Location` docstring.
- **jscpd input:** jscpd 5.4.1 takes an explicit file list through a `-c` config with absolute `path` entries, which replaces any project `.jscpd.json`. It needs `--no-gitignore` (`"gitignore": false`) and `--absolute`, and writes `jscpd-report.json` to the configured output directory. The jscpd config and report live in a fresh `tempfile.TemporaryDirectory` per run, which is also jscpd's cwd and is removed afterwards. An `OSError` launching the binary raises `ProjectRuleFailure` naming the `duplicates` extra. Evidence: `followup-tool-selection.md` Q3, and re-probed this session.
- **jscpd output:** pairs only, star-shaped for 3+ copies. Each side has `name`, `start`, `end`, and `startLoc`/`endLoc` `{line, column}`, `startLoc`/`endLoc` columns are 0-based UTF-8 byte offsets, end exclusive: a match ending a 17-byte line reports 17, and a match after a 2-byte `é` starts at column 3. Both convert by `+1`, matching house-lint's AST spans (`analysis.statement_key`). jscpd exits 0 when it finds duplicates. With no clones it also exits 0 and writes `jscpd-report.json` with an empty `duplicates` list. `statistics.total.sources` can be 0 for inputs under its size floor, so it is not a health check. A missing report after exit 0 is a failure. Evidence: probes this session (three identical files gave `a–b` and `a–c` only, with a mid-line start at column 12 for the `(` after a 12-character `def handle_a`) and at the sketch challenge (end column, non-ASCII start, zero-clone report). Real-jscpd tests (D7) pin a hand-written 1-based span including a non-ASCII line, a zero-clone run, and a single-file run.
- **Location coordinates:** `validate_location` requires all four coordinates or none, each ≥ 1. Evidence: `results.validate_location`.
- **`min_lines` semantics:** the option is passed straight to jscpd's `--min-lines`. On hassette at `min_lines` 5, the shortest reported fragment was 6 lines, so jscpd's floor looks exclusive. The build checks this against a fixture, and the docs describe the option by what jscpd does. Evidence: `tuning-pass.md`, Line floor.
- **Option conventions:** options are snake_case fields with `Annotated` `Meta` bounds, and cross-field checks go in `__post_init__`. Evidence: `rules/file_length.py` `HSL102Options`, CLAUDE.md "Adding a rule".
- **Explicit paths:** explicit paths skip excluded files but bypass `include`. Evidence: `discovery._FileSelector._consider_requested`, and `discover_files` using `explicit or include`.
- **Release timing:** this change ships to master, but the maintainer won't release a version containing `HSL104` until `--base` (#52) lands. So no released version recommends the pre-push entry without new-code gating, and the docs carry no backlog caveat. Evidence: the maintainer's decision at the sketch challenge (Finding 18).
- **The hook file:** `.pre-commit-hooks.yaml` declares one `house-lint` hook with `types: [python]`. Parent D10 adds no hook id.

## Build

- [ ] Implementation and tests committed
- [ ] Docs
- [ ] Ship-time challenge

**Calls made during the build:**
