# Design: Cross-file rules and duplicate-code detection

**Date:** 2026-10-18
**Status:** ratified
**Mode:** sketch
**Issues:** #39, #52

## Summary

house-lint's charter is checks that catch common AI coding issues. Copy-pasted code across modules is one of the most common, and catching it means comparing files against each other. Today every rule is a Python detector that sees one file at a time. The engine around those rules assumes that shape everywhere:
- discovery accepts only `.py` files
- `SourceFile` tokenizes and parses Python
- the result cache is per-file and its correctness argument is "no cross-file dependencies"
- suppression handling, including HSL900's unused-pragma check, runs per file
- the scan pipeline lives inside the CLI's `_scan` function

None of that was a deliberate design principle. It's what the port from hassette's scripts produced.

This milestone reshapes the engine so rules can see the whole project and non-Python files. On that engine it adds a duplicate-code rule for Python and TypeScript that wraps jscpd 5.4.1 rather than hand-building a detector. The tool choice and its evidence are in `design/research/2026-10-18-duplicate-code-detection/followup-tool-selection.md`. The earlier `research.md` in the same directory explains why PMD CPD is not used: on Python and TypeScript it matches exact copies only.

Three known consumers shape the engine:
- **#39**, duplicate code: a project-level rule over Python and TypeScript, with findings at several locations.
- **#52**, `--base` new-code gating: needs a stable identity per finding, and scanning a base-ref tree (extracted to a temp directory) as well as the working tree.
- **#53**, complexity and dead code: complexity is per-file Python; dead code is project-level and could reuse jscpd's `--dead-code` mode.

D1 decides which of these this milestone delivers. The engine is designed for all three either way.

New homes:
- the scan pipeline: a pipeline module the CLI calls (D2)
- project-level rules: the rule catalog and a second registry (D3)
- language-neutral loaded files (D4)
- rule-declared option schemas (D5)
- language tags on discovered files (D6)
- the jscpd adapter and an external-tool runner: a new module (D8, D9)
- project-phase pragma resolution: the suppression engine, which keeps the per-file cache unchanged (D11, D14)
- a tree-sitter-based TypeScript analysis layer that the suppression engine uses for pragmas (D15, D20)
- finding identity: the result model, designed in #52's issue (D16)

## Posture

- **Failure handling:** fail loudly. When the duplicate rule is enabled and jscpd is missing, the wrong version, times out, exits non-zero, or emits output that doesn't parse, the scan reports an operational error with an install hint and the run fails. There is no fallback detector and no retries. Every subprocess call has an explicit timeout.
- **Compatibility:** break freely. The maintainer is the only user and every consuming project pins its version. The CLI, config schema, JSON output, exit codes, and suppression syntax may all change when that improves the design. No compatibility shims. Breaking changes are marked `feat!` with a `BREAKING CHANGE:` footer. Python-only projects that don't enable the new rule should still see the same findings.
- **Scope:** the complete duplicate-code feature, plus the engine refactor it needs. The engine is designed for #52 and #53 as known consumers. Whether they are delivered in this milestone is D1. Identifier and literal normalization in jscpd stays off: in 5.4.1 it's blind, and it measured at about 4x noise on hassette.
- **Rollout:** ship incrementally. Each Breakdown issue lands on master on its own with tests, lint, and types green. Engine refactors preserve behaviour. The rule ships opt-in.
- **Hardening:** project defaults. A wrapped tool reads the real files itself, with no integrity check (see D9).
- **Altitude:** milestone. The engine refactor reshapes most core modules, so the issues will be built after earlier issues have moved code around. The ledger names concepts, modules, and classes, and ends in a Breakdown.

## Decisions

### D1: Which consumers does this milestone deliver?

**Deciding factor:** hassette can actually drop PMD at the end of the milestone.

| | A: #39 and engine only | B: #39, engine, and #52 `--base` | C: #39, engine, #52, and #53 |
|---|---|---|---|
| hassette can switch off PMD | No. hassette's duplicate gate reports only *new* clusters over a backlog of 189. A whole-backlog rule can't replace it without `--base` or a mass suppression pass. | Yes. `--base` replaces the new-code gate and the roughly 700-line per-repo scripts #52 describes. | Yes |
| Milestone size | Smallest | Medium. #52 is one issue on top of an engine already built for it. | Largest. Complexity is independent per-file work, and dead code needs its own accuracy evaluation of jscpd `--dead-code`. |
| Identity design (#52's need) validated by a real consumer | No, designed blind | Yes | Yes |
| Engine validated against a second project rule | No | No | Yes (dead code) |
| Risk of the milestone never closing | Low | Low | Higher. #53's dead-code accuracy is unknown. |

**Recommendation:** B. Without `--base`, the duplicate rule can't replace the gate hassette actually runs, so the milestone wouldn't deliver its practical payoff. #52's identity requirement also checks the engine's design against a real consumer.
**Pick A instead if** you're willing to adopt the rule in hassette as a whole-backlog check, with suppressions or a raised threshold, before `--base` exists. **Pick C instead if** a second project rule matters more to you than a shorter milestone. #53's complexity half needs none of this engine and can ship anytime.
**Reversibility:** easy (#52 and #53 remain separate issues either way)
**Ratified:** Chose B over A and C, so hassette can replace its new-code PMD gate by the milestone's end, accepting a medium-sized milestone with #53 left to a later one.

### D2: Where does the scan pipeline live?

The pipeline covers discovery, the per-file phase, cache reads and writes, and result assembly. It lives entirely in the CLI's `_scan` function. A project phase and a second suppression phase have to join it.

**Deciding factor:** each phase has one obvious home that a reader can find without reading the CLI.

| | A: Add the new phases to the CLI's `_scan` | B: Extract a pipeline module first; the CLI calls it |
|---|---|---|
| Reader load | `_scan` grows past about 300 lines, mixing argument handling with engine phases | Phases become named steps in an engine module, and the CLI only parses arguments and renders output |
| Testability | Engine behaviour is reachable only through the CLI | Phases are testable directly. #52 can call the pipeline twice (base and head) without the CLI. |
| Cost | None now | One behaviour-preserving refactor issue, pinned by the existing CLI integration tests |
| Fit with #52 | `--base` has to run the pipeline on two trees from inside the CLI function | Natural: run the pipeline over two file sets |

**Recommendation:** B, because #52 needs to run the pipeline twice and the CLI function is already the largest unit in the codebase.
**Pick A instead if** the milestone drops #52 (D1 A) and you'd rather defer the refactor.
**Reversibility:** easy
**Ratified:** Chose B over A, so each phase has a named home and #52 can run the pipeline twice, accepting one behaviour-preserving refactor issue up front.

### D3: How are rule kinds modeled?

**Deciding factor:** a per-file rule stays as simple as it is today, and a project rule gets the whole file set.

| | A: Two kinds, file rules and project rules, each with its own interface and registry | B: One interface; every rule receives the whole file set | C: Two kinds, plus a facts phase where file rules emit per-file facts that project rules consume |
|---|---|---|---|
| Existing seven rules | Unchanged | All rewritten to iterate the file set | Unchanged |
| Per-file caching of file rules | Unchanged | Lost or re-invented | Unchanged |
| Duplicate rule (wraps jscpd) | Receives the file set and runs the tool | Same | Facts unused; the tool does its own analysis |
| A future native cross-file rule (native dead code, import graph) | Must re-derive per-file data itself | Same | Reuses cached per-file facts |
| Machinery to build now | Second registry, catalog field for scope and languages | Rewrite all rules | A + a facts schema and cache with no consumer yet |

**Recommendation:** A. Project rules receive the scanned file set: each file's root-relative path, language, content hash, and the loaded bytes (D4). They return candidates that can carry several locations. The catalog records each rule's scope and the languages it handles, and an import-time check covers each registry the way the current one does. C's facts phase has no consumer in this milestone: jscpd covers both duplicate code and dead code, so it waits until a native cross-file rule needs it.
**Pick C instead if** you expect a native cross-file rule soon, such as native dead code or import-cycle detection, rather than more tool wrappers.

**Planned evolution.** The plausible native cross-file rules all fit C's collect/analyze shape rather than B's every-rule-sees-everything shape:
- the same helper or dataclass reimplemented across modules
- one-caller wrappers
- leftover compatibility re-exports
- import cycles and layering violations
- `mock.patch` targets that no longer exist
- source modules without a mirrored test file

When the first one is built, it adds the facts phase on top of A. Nothing in this milestone defines a facts schema.
**Reversibility:** easy (C is A plus an added phase)
**Ratified:** Chose A over B and C, so per-file rules and their caching stay unchanged while project rules get the whole file set, accepting that the first native cross-file rule will add a facts phase later.

### D4: How are non-Python files represented?

`SourceFile` reads, decodes, tokenizes, and parses Python in one class. Its single read is also what makes cache keys honest. TypeScript files need to be read (for pragmas and hashing) but never parsed as Python.

**Deciding factor:** one read path for every language, so the read-once guarantee and cache keying stay in one place.

| | A: Split loading from analysis: a language-neutral loaded file (bytes, text, hash, language), with Python tokens and tree derived on top | B: A separate text-only source type for non-Python files, alongside `SourceFile` | C: Keep `SourceFile`; skip Python parsing when the language isn't Python |
|---|---|---|---|
| Read-once and hash in one place | Yes | No, two read paths | Yes |
| Python analysis stays where it is | Yes, as a layer | Yes | Mixed into conditionals |
| Type safety (no Python tree on a TypeScript file) | Enforced by type | Enforced by type | Optional fields everywhere |
| Constructors for loading content (disk today; bytes if #52 ever loads from git objects) | One | Two | One |

**Recommendation:** A, because it keeps one read-and-hash path for every language. #52 runs the pipeline on a temp extraction (D9), so loading from git objects is optional. If it's ever wanted, A has a single place to add it.
**Pick B instead if** splitting `SourceFile` turns out to ripple through every rule's signature in a way the refactor can't absorb.
**Reversibility:** hard (every rule and the scanner depend on the source type)
**Ratified:** Chose A over B and C, so every language shares one read-and-hash path, accepting a refactor that touches every rule's source type.

### D5: How are per-rule options declared?

Rule options are plumbed through a hard-coded tuple of three dataclasses (`HSL101Options` through `HSL103Options`), a config-parsing branch per rule, and cast adapters in the registry. The duplicate rule needs its own options (thresholds, tool settings), and #53 will add more.

**Deciding factor:** adding a rule's options is one local change.

| | A: Add a fourth hard-coded member | B: Each rule declares its options type in the catalog or registry; config parsing and dispatch look it up |
|---|---|---|
| Files touched per new configurable rule | Config parser, options tuple, union type, registry adapter | The rule's own options type and its registration |
| Validation and error messages | Hand-written per rule, as today | Generic, driven by the declared type |
| Refactor cost | None | Migrate three rules' options |
| Casts in the registry | Grows by one | Gone |

**Recommendation:** B. The posture allows the internal break, and #53 would otherwise add a fifth and sixth member to the same tuple.
**Pick A instead if** you want the engine issues to stay narrowly about cross-file rules.

At the sketch challenge (Finding 12), the migration moved into its own Breakdown issue that lands before the rule-kinds issue, so that issue stays about cross-file rules while the codebase keeps a single options model.
**Reversibility:** easy
**Ratified:** Chose B over A, so adding a configurable rule is one local change with generic validation, accepting a migration of the three existing rules' options.

### D6: How does discovery handle languages?

**Deciding factor:** projects that enable only Python rules see no change in file counts or messages.

| | A: Tag each file with a language by suffix; discover only languages some enabled rule handles | B: Always discover every supported language | C: Each rule declares its own include globs |
|---|---|---|---|
| Python-only projects | Unchanged | `files_scanned` and `files_skipped` change, and TypeScript files get read for no rule | Unchanged |
| Explicit non-Python path when no enabled rule handles it | Error, as today | Accepted silently | Depends on globs |
| Config surface | None new | None new | New per-rule key |
| Gitignore and exclude handling | Shared, as today | Shared | Rule globs interact with excludes |

**Recommendation:** A. The language set to discover is derived from the enabled rules' declared languages (D3), so turning on the duplicate rule is what brings TypeScript files into the scan.
**Pick C instead if** you expect rules that want different subsets of the same language. Nothing planned does.
**Reversibility:** easy
**Ratified:** Chose A over B and C, so enabling a rule is what brings its languages into the scan and Python-only projects are unaffected, accepting that discovery depends on the enabled-rule set.

### D7: Which languages does the duplicate rule cover?

**Deciding factor:** TypeScript is first-class, without promising languages nothing checks.

| | A: Python, TypeScript (`.ts`, `.tsx`) | B: A + JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`) | C: Every language jscpd supports |
|---|---|---|---|
| Covers hassette and hass-hassette | Yes | Yes | Yes |
| Pragma comment syntaxes needed | `#`, `//` | `#`, `//` (same as A) | Many |
| Extra test and probe work | Baseline | Small: same tokenizer family, same comment syntax | Large |
| Risk of flagging vendored or build output | Low | Higher: built `.js` bundles, unless excluded | High |

**Recommendation:** A. TypeScript is the stated need, and nothing has measured jscpd's JavaScript behaviour. Adding JavaScript later is a one-line language addition plus probes.
**Pick B instead if** a project you use house-lint in has hand-written JavaScript you want covered now.
**Reversibility:** easy
**Ratified:** Chose A over B and C, so TypeScript is first-class while only probed languages are promised, accepting that JavaScript waits for a later one-language addition.

### D8: How is jscpd provisioned?

jscpd 5.4.1 ships official PyPI wheels for 8 platforms. Its Linux wheels need glibc 2.34+, and there's no source distribution.

**Deciding factor:** only projects that want duplicate checking carry jscpd, and the rule never runs an unexpected jscpd version.

| | A: Optional extra (`house-lint[duplicates]`) pinned to an exact version; the rule looks for the binary next to house-lint's interpreter, then on `PATH`, and refuses any other version | B: Hard dependency | C: `PATH` only; the user installs jscpd themselves |
|---|---|---|---|
| Projects without duplicate checking install jscpd | No | Yes | No |
| house-lint installs on glibc < 2.34 (not a target: every machine runs ≥ 2.35) | Yes | No | Yes |
| `uvx` usage | `uvx --with 'house-lint[duplicates]'` or `uvx 'house-lint[duplicates]'` | Plain `uvx house-lint` | Separate install step |
| Version drift (jscpd changes meaning across releases) | Prevented by pin plus check | Prevented by pin | User-controlled; needs the same version check |
| Missing tool when the rule is enabled | Loud error naming the extra | Can't happen | Loud error naming the install |

**Recommendation (revised at ratification):** A, because not every project wants duplicate checking, so jscpd stays opt-in alongside the opt-in rule (D18). The version check, not the pin alone, is what guarantees behaviour: a `PATH` jscpd at another version fails loudly. The draft's reason, older-distro installs, doesn't apply to this project's machines.
**Pick B instead if** you'd rather every install carry jscpd and skip the extra in install commands.
**Reversibility:** easy
**Ratified:** Chose A over B and C, so only projects that want duplicate checking install jscpd and the rule only ever runs the pinned version, accepting an extra in install commands. Older-distro support wasn't the deciding factor: every target machine runs glibc ≥ 2.35, e.g. Ubuntu 22.04.

### D9: What content does a wrapped tool analyze?

The read-once guarantee today means one buffer feeds detection, the cache key, and the stored entry. An external tool given paths reads the files again itself.

**Deciding factor:** the tool's findings describe the content house-lint used, at a cost and complexity peers accept.

| | A: Write the loaded buffers to a private temp tree and run the tool there, mapping paths back | B: Run on the real paths, then re-hash every file and fail if any changed | C: Run on the real paths, no check |
|---|---|---|---|
| Findings match hashed bytes | Guaranteed | Detected after the fact; the run fails | Not guaranteed |
| Works for content that isn't on disk (#52 base tree) | Yes | No, needs a separate extraction | No |
| Extra cost | Writing about 1,300 files, a few MB, per run on hassette | Re-reading every file | None |
| Path mapping | Needed, since the tool reports temp paths | None | None |
| Edits during a run (editor save, formatter) | Unaffected | Run fails, user re-runs | Silent mismatch |

**Recommendation (revised after a peer check):** C.

None of the peers do A:
- Ruff, flake8, Pylint, and wemake read each file once in-process and never hand analysis to an external tool.
- Multi-linter wrappers (pre-commit, prek, MegaLinter, trunk, qlty) pass real paths with no integrity check.
- hassette's base-ref gate extracts the base commit with `git archive` and runs PMD on the extraction.

The two arguments for A don't hold up:
- **The mid-run-edit harm is small and visible.** Nothing from the project phase is cached (D11, D14). A file edited between house-lint's read and jscpd's read can make jscpd's locations disagree with the in-memory bytes used for pragma resolution. A pragma may then miss its occurrence for that one run, and the next run is consistent again.
- **#52 doesn't need it.** #52 can extract the base tree to a temp directory and run the normal pipeline there, where files can't change mid-run.

**Pick B instead if** you want a loud failure on a mid-run edit; the re-read costs milliseconds. **Pick A instead if** #52 should load base content from git objects rather than an extraction.
**Reversibility:** easy
**Ratified:** Chose C over A and B, matching how every peer that wraps external tools behaves, accepting that a file edited mid-run can make jscpd's locations disagree with the bytes used for pragma resolution for that one run. Also accepted as a known limitation (sketch challenge, Finding 16): in files with a bare `\r`, form feeds, or a BOM, jscpd's start line can disagree with house-lint's line model, so a pragma may miss. That shows up as a visible finding, not a silent wrong result. This also removed the posture's raised hardening point.

### D10: How do project rules behave with explicit paths and hooks?

Pre-commit hooks pass only staged files. A cross-file rule run over a partial file list misses every duplicate whose other copy wasn't staged.

**Deciding factor:** a hook run never silently reports less than a full run would for the files it covers.

| | A: Project rules always analyze the full configured scope; with explicit paths, only findings that have a location in those paths are reported | B: Skip project rules when explicit paths are given | C: A separate flag or hook id that runs only project rules over the full scope |
|---|---|---|---|
| Hook correctness | Correct for staged files | Rule silently absent from hooks | Correct |
| Hook cost | Full jscpd run per commit when the rule is enabled (about 3-4s on hassette, measured on a loaded machine) | None | Same as A, but only in the hook that opts in |
| Config and CLI surface | None new | None new | New flag plus a second hook entry |
| Discovery work | Two discovery passes when paths are given (full scope for project rules, explicit set for reporting and file rules) | One | One per hook |

**Recommendation:** A. Under A, a finding in a staged file whose copy lives in an unstaged file is reported, which is what a hook should catch. The cost is opt-in: it applies only to projects that enable the rule. Docs recommend running it at pre-push for large repos, which is where hassette runs its duplicate gate today.
**Pick C instead if** the per-commit cost proves unacceptable and you'd rather split the hook than move it to pre-push.

Pre-push placement doesn't change the analysis, because pre-push hooks also receive file names (the files changed in the pushed commits) unless `pass_filenames: false` is set. Under A, a pre-push entry with `--select HSL104` and `stages = ["pre-push"]` sits alongside the normal pre-commit entry with no new flag. With `pass_filenames: false`, it reports the whole backlog. Peer precedent:
- Pylint's duplicate check and mypy are known to miss cross-file problems when given only staged files, and are usually run with `pass_filenames: false`.
- jscpd's own `--changed` mode is A's semantics.
**Reversibility:** easy
**Ratified:** Chose A over B and C, so a hook run reports every duplicate involving the files it was given with no new flag or hook id, accepting a full jscpd run per hook invocation (docs recommend a pre-push entry).

### D11: Are project-rule results cached?

**Deciding factor:** repeated local runs stay fast without a stale-cache risk, but only where the speed-up is real.

| | A: Cache each project rule's pre-suppression candidates, keyed on the sorted set of (relative path, content hash), the rule's options, and the tool version | B: Never cache project rules |
|---|---|---|
| Repeated `check` on an unchanged tree | jscpd skipped | jscpd every run (about 3-4s on hassette) |
| Hook and CI runs (the tree always changed) | Always miss | Always run |
| Staleness risk | None: any change alters the key | n/a: nothing persisted |
| Machinery | A second key scheme and entry type | None |
| Adding it later | n/a | Purely additive |

**Recommendation (revised at the sketch challenge):** B. Once D14 stopped persisting project-phase state, the cache entry would only skip jscpd on repeat runs of an unchanged tree. Hooks always miss, and the docs place the rule at pre-push (D10).
**Pick A instead if** repeated manual runs on large repos turn out to feel slow.
**Reversibility:** easy
**Ratified:** Chose B over A, so the project phase persists nothing and the cache keeps one key scheme, accepting a full jscpd run on every scan that enables `HSL104`. Revised at the sketch challenge (Finding 1); originally ratified as A.

### D12: How are findings with several locations represented?

**Deciding factor:** editors, the text reporter, and suppressions all work per location, while JSON consumers can still see the whole cluster.

| | A: One finding per occurrence; the message names the other locations | B: One finding per occurrence, plus a structured `related` list of the other occurrences and a shared group id in JSON | C: One finding per cluster with all locations |
|---|---|---|---|
| Text output and editor jump-to-line | Each occurrence on its own line | Same as A | Needs a multi-location rendering |
| Per-occurrence suppression (D13) | Natural | Natural | Needs per-location suppression inside one finding |
| Machine consumers (#52, CI summaries) can group a cluster | Only by parsing messages | Yes | Yes |
| Schema change | None | Additive fields, `schema_version` bump | New finding shape, `schema_version` bump |

**Recommendation:** B, because per-occurrence findings keep suppressions and editors simple, and the structured group gives #52 and CI something to key on without parsing messages.
**Pick A instead if** you want zero JSON schema change in this milestone. **Pick C instead if** cluster-level reporting matters more than editor navigation.
**Reversibility:** easy
**Ratified:** Chose B over A and C, so each copy is its own editor-friendly finding while JSON consumers can group a cluster, accepting a `schema_version` bump.

### D13: What does suppressing one copy of a duplicate do?

**Deciding factor:** a suppression only ever hides what it sits on, so it can't silently hide code elsewhere.

| | A: Hides that occurrence only; suppressed occurrences still count toward the cluster's size | B: The cluster is hidden only when every occurrence is suppressed (hassette's rule) | C: Suppressed occurrences leave the cluster; the rest report only if they still meet the minimum count |
|---|---|---|---|
| Marking one of two copies | Hides it; the other copy still reports | Nothing hides until both are marked | Hides both, since the other copy drops below the minimum |
| A suppression affecting code it doesn't sit on | Never | Never, but all-or-nothing | Yes |
| Unused-suppression check (HSL900) | Per occurrence: unused when its occurrence isn't reported | Per cluster, harder to explain | Per occurrence, but its effects reach other files |
| Effect for designed parallel structure | Mark every copy, as in hassette | Same | Mark one copy |

**Recommendation:** A. It ends in the same place as hassette's every-copy rule, a deliberate pair needs both copies marked, but with per-location semantics that match every other rule.
**Pick C instead if** marking every copy of intentional parallel structure is more friction than the risk of one pragma hiding another file's duplicate.

For example, with 4 copies and a minimum of 3, marking 2 copies leaves the cluster at size 4. The 2 unmarked copies still report, each naming all 3 others. Only marking all 4 silences the cluster.
**Reversibility:** easy
**Ratified:** Chose A over B and C, so a pragma only ever hides the copy it sits on and suppressed copies still count toward cluster size, accepting that intentional parallel code needs every copy marked.

### D14: How are suppressions for project rules resolved?

Pragmas are parsed per file, in the per-file phase. Project-rule candidates only exist after every file is processed, so HSL900's "unused" verdict for a project-rule pragma can't be made per file.

**Deciding factor:** keep the single read and today's per-file cache unchanged, while still reporting unused project-rule pragmas.

| | A: Persist pending claims, with owner spans, in per-file cache entries; finalize after the project phase | B: After the project phase, re-read files that have project candidates from disk | C: Disable the per-file cache when any project rule is enabled | D: Resolve project-rule pragmas fresh in the project phase from the bytes already in memory |
|---|---|---|---|---|
| Single read | Kept | Broken (reads disk again) | Kept | Kept: every file is loaded before the cache lookup, so its bytes are in memory even on a hit |
| Per-file cache | Entries grow by claims plus a statement-span index | Unchanged | Lost when the rule is enabled | Unchanged |
| Unused project-rule pragmas reported, including in clean files | Yes | Only in files with candidates | Yes | Yes |
| Cache-key exposure (parser versions for cached claims) | Yes | None | None | None |
| Per-run cost | None | Disk re-reads | Every file re-analyzed | Re-parse only the few files involved |
| Reversibility | Hard | Easy | Easy | Easy |

**Recommendation (revised at the sketch challenge):** D. It has three steps:
1. **Per-file phase.** This works as today, cache included. Pragmas that name a project rule still get structural HSL900 checks (malformed, unknown rule, misplaced), but the per-file engine neither judges them unused nor treats their rule as disabled for that file. Those verdicts move to the project phase.
2. **Project phase.** After the project rules run, re-parse pragmas from the in-memory bytes of every file that either has a project candidate or contains a house-lint pragma naming a project rule. A byte search finds the second case, which catches stale pragmas in files with no candidates.
3. **Matching.** An occurrence's owner is the innermost statement containing its start line. A pragma claims the occurrence only when that owner equals the pragma's own owner statement. This is the equality rule that file rules use today, not containment, so a pragma on a `def` line can't hide a duplicate deep inside the function body. TypeScript uses the same rule over tree-sitter statement nodes (D15). Unused project-rule claims are reported here, but only for a project rule whose output is marked complete. Each project rule reports an explicit completeness flag, mirroring the per-file engine's `candidates_complete`. A tool failure or a hit candidate budget marks the output incomplete, so the run carries the operational error and no false "unused suppression" diagnostics for that rule. Peers differ: ESLint aborts the run, mypy only reports unused ignores for files it fully checked, and Pylint's end-of-run duplicate check produces long-standing false `useless-suppression` warnings. (Sketch challenge, Finding 6.)

The original A rejected re-deriving claims because it would break the single read, but the code loads every file's bytes before the cache lookup, so that premise was wrong. The behaviour to pin first: today's suppression and HSL900 results for file rules don't change.
**Pick A instead if** a later rule needs project-phase work so expensive that persisting it per file pays off.
**Reversibility:** easy
**Ratified:** Chose D over A, B, and C, so the per-file cache stays unchanged and complete HSL900 coverage holds for project rules with no persisted project-phase state, accepting a re-parse of the few files involved on every run. Revised at the sketch challenge (Findings 1, 3, 4, 13); originally ratified as A.

### D15: How do TypeScript files get suppression pragmas?

The pragma grammar and ownership rules are built on Python tokens and statements. There's no TypeScript statement model.

**Deciding factor:** TypeScript duplicates can be suppressed with the same grammar and the same ownership rule as Python, found reliably.

| | A: Same pragma grammar in `//` comments, owned by lines: trailing `ignore` owns its line, a comment-only `ignore-next` owns the next non-blank, non-comment line, `ignore-file` sits in the prologue | B: Statement ownership via a TypeScript parser (tree-sitter) | C: No TypeScript pragmas; use `exclude` and `per-file-ignores` |
|---|---|---|---|
| Matches Python's behaviour | Close: line ownership, not statement ownership | Exact: statement ownership with D14's equality rule | No |
| New dependency | None | A parser with native wheels | None |
| HSL900 validation (malformed, unknown, unused) | Yes | Yes | n/a |
| Granularity | Line | Statement | File |
| Finds comments reliably (strings, template literals, regex, JSX) | No: hassette #1911 abandoned this approach | Yes | n/a |

**Recommendation (revised at ratification):** B. It is the only option that finds TypeScript comments reliably, and it gives TypeScript the same statement ownership and D14 equality rule as Python. The draft recommended A. The evidence below is why it was rejected.
**Pick C instead if** you'd rather ship TypeScript coverage before any TypeScript suppression exists.

**Evidence against A.** hassette PR #1911 tried TypeScript comment checking (section dividers in `.ts`/`.tsx`) with a parser-free scanner. It closed as a draft, concluding a TypeScript parser was needed to find comments reliably. Strings, template literals, regex literals, and JSX all contain `//` or `/*` sequences that aren't comments. `tree-sitter` 0.26.0 and `tree-sitter-typescript` 0.23.2 (MIT) ship PyPI wheels for manylinux (glibc 2.17+), musllinux, macOS, and Windows, and `tree-sitter` also has an sdist.

**Design under B.** A TypeScript analysis layer sits alongside the Python one on top of the language-neutral loaded file (D4). It provides comment tokens and statement spans. The suppression engine uses the same pragma grammar in `//` comments with statement ownership: trailing `ignore` owns its statement, comment-only `ignore-next` owns the next statement in the same block, and `ignore-file` sits in the prologue. Matching follows D14: an occurrence's innermost owner statement must equal the pragma's owner statement, the same rule as Python. Where the parser is installed is D20.

**Owner node kinds and parse errors (sketch challenge, Finding 8).** Tree-sitter has no "statement" abstraction, so the TypeScript layer defines owner node kinds per grammar. They cover top-level statements, class members, object-literal methods, JSX elements, decorators, and `export` wrappers. `.ts` uses the `typescript` grammar and `.tsx` uses `tsx`. A file whose tree contains `ERROR` nodes keeps its duplicate findings and has its pragmas disabled. It is not a file error, because the grammar lags new TypeScript syntax and valid files must not fail hooks. For the same reason, the one HSL900 diagnostic saying its pragmas are disabled is emitted only when the file contains a house-lint pragma: HSL900 fails the run, so it is reserved for a suppression that actually stopped working, and a file with no pragmas gets no diagnostic. (PR #62 review.)

No maintained non-tree-sitter TypeScript parser is installable from PyPI: oxc and swc have no Python bindings, and `esprima` is JavaScript-only and unmaintained since 2018. The `tree-sitter-typescript` grammar package was last released 2024-11. The grammar must therefore come from an actively maintained, pinned package, such as `tree-sitter-language-pack` or `ast-grep-py`, both released 2026-10. Breakdown issue 6 chooses between them after checking install size.
**Reversibility:** easy
**Ratified:** Chose B over A and C, so TypeScript comments and statement ownership are found reliably and behave like Python's, accepting a tree-sitter dependency. Parser-free comment scanning was already tried and abandoned in hassette #1911.

### D16: How is a finding's identity defined, and where is it designed?

#52 needs a stable identity per finding, so it can tell a new finding from one that moved, and so a new duplicate across files that already have duplicates isn't treated as old.

**Requirements (settled here):**
- **Identity names one finding, so one occurrence**, not a cluster. This matches SARIF fingerprints, GitHub code scanning, and Code Climate, which all fingerprint per result.
- **Duplicate occurrences key on a hash of their own whitespace-normalized text**, with no path, so moves don't change it. The goal is that an occurrence's identity changes only when its own text changes, not when a sibling's matched span grows.
- **Open for #52: stability under span merging.** An own-text hash alone doesn't meet that goal. Pair-to-cluster grouping (breakdown item 7) merges overlapping spans within a file, so a new copy that matches a longer span in one existing file grows that file's merged occurrence and changes its hash, and `--base` would report an old occurrence as new. #52's sketch must define identity from a fragment that overlap merging doesn't change, or specify a correspondence that keeps the prior span. (PR #62 review.)
- **`--base` compares counts per identity (a multiset), not sets.** With 3 copies at base and 4 at head, one is new. This is what fixes #52's flaw 1.
- **D12's group id is separate from identity.** It exists only to group a cluster's findings within one run and is documented as unstable across runs.

**Deciding factor:** identity is designed once, against the consumer that defines its requirements.

| | A: Identity field and duplicate key in the rule issue; other rules' keys and the comparison in #52's issue | B: All identity work in #52's issue; the rule issue ships the group id only | C: Shared cluster-fragment hash; #52 compares counts |
|---|---|---|---|
| Catches a new 4th copy | Yes | Yes | Yes |
| Stable when a sibling's matched span grows | Only once #52 handles span merging (see requirements) | Same as A | No: every occurrence's key changes |
| Field designed against every rule's key at once | No | Yes | No |
| Rule issue size | Larger | Smaller | Larger |
| Duplicate findings carry identity before `--base` exists | Yes | No | Yes |

**Recommendation (revised at the sketch challenge):** B. The requirements above are fixed here. #52's issue designs the field shape and every rule's key against its comparison. The original cluster-fragment key let a new copy of an existing cluster pass, which is the exact case #52 exists to catch.
**Pick A instead if** you want identity in JSON as soon as the rule ships, before `--base` exists.
**Reversibility:** easy
**Ratified:** Chose B over A and C, so identity is designed once, with its consumer, as a per-occurrence own-text key compared by count, accepting that duplicate findings carry no identity until #52 lands. Revised at the sketch challenge (Findings 2, 5, 10, 11); originally ratified as a cluster-fragment key defined in the rule issue.

### D17: What are the duplicate rule's default thresholds and matching mode?

From the research:
- jscpd's defaults are 50 tokens and 5 lines, and it reports pairs.
- hassette's gate uses 20 tokens and only 3+ copies. Its 20-token floor exists because CPD breaks matches at every rename.
- At 50 tokens jscpd flags 19% of hassette's current 189 pair-or-larger clusters, and at 20 tokens 83%.
- At 50 tokens, about two-thirds of a 15-cluster sample was real duplication.
- `--max-gap-lines` barely changed hassette's result (247 vs 258 clusters).

The rule-of-three guidance says to extract on the third copy, because two copies aren't enough evidence. A pair alert asks "extract now?" when the answer is often "not yet", and under D13 that answer costs a pragma on both copies.

**Deciding factor:** findings worth acting on by default, matching rule-of-three guidance, with everything configurable.

| | A: jscpd defaults (pairs, 50 tokens) | C: Pairs, tuned token floor | D: 3+ copies, tuned token floor | E: D, plus pairs above a large-size threshold |
|---|---|---|---|---|
| Matches rule-of-three guidance | No | No | Yes | Yes, except for large copy-paste |
| Large copied block with 2 copies | Flagged | Flagged | Missed (until #52's `--base` catches a third copy) | Flagged |
| Pragmas needed for not-yet-worth-extracting pairs | Many | Many | None | Few |
| Options | None new | None new | Minimum occurrences (default 3), token floor | D + a large-pair threshold |
| Work before shipping | None | Tuning pass | Tuning pass at 3+ | Tuning pass + large-pair threshold |

**Recommendation:** D. The minimum-occurrences default is 3 and configurable. The token floor is set by a tuning pass on hassette at 30, 40, and 50 tokens with 3+ copies, picking the lowest floor whose hand-classified sample is at least two-thirds real duplication. The earlier measurements counted pairs, so they don't transfer. Gap merging is off. jscpd always runs in weak mode, since otherwise a house-lint pragma comment inside a copied block splits the match. Identifier and literal normalization stays off, per Posture.
**Pick E instead if** missing large two-copy blocks matters more than one more option. **Pick C instead if** catching the first copy matters more than rule-of-three.
**Reversibility:** easy (defaults are config)
**Ratified:** Chose D over A, C, and E, so the rule follows rule-of-three guidance and doesn't demand pragmas on pairs that aren't worth extracting yet, accepting that two-copy blocks, even large ones, go unreported by default.

### D18: Rule ID and enablement

**Deciding factor:** follows the existing tiers. **Recommendation:** `HSL104`, opt-in, since it needs the optional extra (D8) and changes hook cost (D10). **Pick default-on instead if** D8 becomes a hard dependency.
**Reversibility:** easy
**Ratified:** Chose `HSL104` opt-in over a default-on ID, so projects enable it deliberately after installing the extra, accepting one `extend-select` line per project.

### D19: Docstrings and generated files

**Deciding factor:** minimal special-casing. **Recommendation:** count docstrings as code. Duplicated docstring paragraphs are themselves an AI-coding smell, and filtering them would mean re-parsing occurrences. Handle generated files with the existing `exclude` and `per-file-ignores`; add no banner detection. **Pick a docstring filter instead if** the tuning pass (D17) shows docstring-only clones are a large share of the noise.
**Reversibility:** easy
**Ratified:** Chose counting docstrings and relying on existing excludes over a docstring filter or banner detection, so no special cases are built, accepting that repeated docstring paragraphs will be reported.

### D20: Where is the TypeScript parser installed?

Any scan that includes TypeScript files needs tree-sitter, for pragmas and statement spans (D15). Today only `HSL104` brings TypeScript into a scan (D6). Future per-file TypeScript rules would need the parser but not jscpd. An example is HSL001's AI-writing tells in TypeScript comments, which is what hassette #1911 wanted.

**Deciding factor:** TypeScript support isn't tied to the duplicate rule, and installs stay simple.

| | A: Core dependency (`tree-sitter` plus a maintained TypeScript grammar package, see D15) | B: Inside the `duplicates` extra, next to jscpd | C: Its own `typescript` extra, which `duplicates` pulls in |
|---|---|---|---|
| Install size for Python-only projects | Adds the parser wheels, a few MB | Unchanged | Unchanged |
| A future per-file TypeScript rule | Works with a plain install | Needs the `duplicates` extra, which is unrelated | Needs the `typescript` extra |
| Number of extras to explain | One (`duplicates`) | One | Two |
| Install risk | Low: wheels for every target platform, plus an sdist | Low | Low |
| Missing-parser error path to build | None | Folded into `HSL104`'s existing missing-extra error | Yes, a separate one for future TypeScript rules |

**Recommendation:** A. The wheels are small and universally available. It makes TypeScript a first-class language rather than a duplicate-rule add-on, and it removes a missing-dependency error path. Imports stay at module level, per the no-lazy-imports convention, so every run pays the parser's small import cost.
**Pick C instead if** you want a Python-only install to stay at two runtime dependencies. **Pick B instead if** you're sure no per-file TypeScript rule is coming.
**Reversibility:** easy
**Ratified:** Chose A over B and C, so TypeScript analysis is part of every install and future per-file TypeScript rules need no extra, accepting a few MB of parser wheels and their import cost on every run, including Python-only ones. Accepted as a known limitation at the sketch challenge (Finding 15), with the error-path row corrected. The decision rests on first-class TypeScript and the no-lazy-imports convention.

### D21: What decides whether a rule applies to a file?

Today "applies" means one thing: the per-file enabled rules after per-file-ignores. The cache key, detector selection, and HSL900's disabled-rule diagnostic all use it. Language (D3, D6) and per-file-ignores for a project rule (D10, D19) add two more notions.

**Deciding factor:** D19's reliance on per-file-ignores for generated files actually removes those files from `HSL104`'s output.

| | A: One `applies(rule, file)` check from global selection, per-file-ignores, and file language; a per-file-ignored file leaves that project rule's input entirely | B: The same check, but a per-file-ignored file stays in the tool's input; only its own occurrences are dropped, and twins still report naming it |
|---|---|---|
| Generated file disappears from `HSL104` output | Yes | No: hand-written twins still name it |
| Consistent with `exclude` | Yes: a rule-scoped exclude | No |
| Cluster counts | Exclude the ignored file | Include it |
| A file rule on a file whose language it doesn't handle | Never dispatches. A pragma naming such a rule gets its own HSL900 message. | Same |

**Recommendation:** A. Discovery, detector dispatch, cache keys, the project file set, and HSL900 all call the one check.
**Pick B instead if** you want hand-written code that duplicates a generated file to keep reporting.
**Reversibility:** easy
**Ratified:** Chose A over B, so "applies" has one definition shared by both rule kinds and per-file-ignores act as a rule-scoped exclude for project rules, accepting that a per-file-ignored file's twins lose that copy from their cluster count. Added at the sketch challenge (Finding 7).

## Assumed

- jscpd 5.4.1 emits JSON with per-occurrence `start` and `end` line and column for both sides of each pair. It accepts an explicit file list through a `-c` config with absolute `path` entries, which fully replaces any project `.jscpd.json`. It needs `--no-gitignore` and `--absolute` to respect house-lint's own discovery. Evidence: probes in `followup-tool-selection.md` Q3.
- jscpd reports pairs, not clusters. Grouping pairs into clusters over overlapping spans is house-lint's job. Evidence: `followup-tool-selection.md` Q3.
- jscpd's `mild` mode tokenizes Python comments, so a pragma inside a copied block splits a match; `weak` mode doesn't. TypeScript comments are never tokens. Evidence: `followup-tool-selection.md` bottom line.
- jscpd's inline `jscpd:ignore-start` markers change detection, which HSL900 can't validate. They're left undocumented, and house-lint pragmas are the supported suppression. Evidence: `followup-tool-selection.md` Q3.
- jscpd 5.4.1 is on PyPI, MIT-licensed, with wheels for manylinux_2_34 (x86_64 and aarch64), musllinux, macOS, and Windows, and no sdist. Evidence: PyPI JSON for `jscpd`, checked 2026-10-18.
- PMD CPD 7.26.0 matches exact token sequences only on Python and TypeScript; its normalization flags are no-ops there. Evidence: probes in `research.md` and `followup-tool-selection.md`, reproduced in this session.
- The result cache is namespaced by house-lint's version and a fingerprint of its own sources, so cache-format changes need no migration. Evidence: the cache module's namespacing and the CLAUDE.md Gotchas.
- hassette runs its duplicate gate at pre-push, as a new-code gate over a backlog. Evidence: hassette's `prek.toml` and duplicate-code script, cited in `research.md`.
- Running a cross-file rule over a partial file list is unsound. Evidence: `design/research/2026-08-19-monorepo-ci-integration/research.md`.

## Breakdown

**Done when:** a project can enable `HSL104` to get duplicate-code findings across Python and TypeScript, suppressible with house-lint pragmas in both languages and validated by HSL900. `house-lint check --base <ref>` reports only duplicates (and other findings) introduced since that ref. hassette and hass-hassette have follow-up issues filed to replace their PMD scripts with it. Projects that don't enable `HSL104` see the same findings as before.

1. **Extract the scan pipeline from the CLI**: move discovery, the per-file phase, cache handling, and result assembly into a pipeline module the CLI calls. Add characterization tests pinning current suppression and HSL900 behaviour first. Implements: D2. After: none. Issue: #56
2. **Language-neutral loaded files and language-tagged discovery**: split loading (bytes, text, hash, language) from Python analysis; discovery tags each file with its language and keeps today's Python-only file set until issue 4 derives it from enabled rules. Implements: D4. After: 1. Issue: #57
3. **Rule-declared options and cache-hash inputs**: replace the hard-coded options tuple with rule-declared option types, migrating HSL101-103 with no behaviour change. Each rule declares what it contributes to the per-file config hash: its options, plus any extra input such as HSL101's filename when a family scopes to filenames. The config hash is assembled from the registry rather than from one parameter per rule. Implements: D5. After: 1. Issue: #58
4. **Rule kinds and the project phase**: add project rules (catalog scope and languages, second registry, file-set interface, multi-location candidates) and a project phase in the pipeline; add one rule-applies-to-file check covering selection, per-file-ignores, and language; discovery derives the languages it discovers from the enabled rules' declared languages through that check. Implements: D3, D6, D21. After: 2, 3. Issue: #59
5. **Project-rule suppression and multi-location findings**: per-file pragma handling defers verdicts for project-rule pragmas; the project phase resolves them from in-memory bytes with equality ownership and reports unused ones only for complete rule output; no project-phase state is cached; `related` locations and group id on findings, with a `schema_version` bump. Implements: D11, D12, D13, D14. After: 4. Issue: #60
6. **TypeScript analysis and pragmas**: add tree-sitter and a maintained TypeScript grammar package as core dependencies (choosing the package per D15 after checking install size), plus a TypeScript analysis layer (comments, owner node kinds per grammar) over the language-neutral loaded file; the suppression engine reads TypeScript pragmas with statement ownership and full HSL900 validation, and disables pragmas in files with `ERROR` nodes, with one HSL900 diagnostic only when such a file contains a pragma. Implements: D15, D20. After: 2, 5. Issue: #61
7. **External tool runner and `HSL104` duplicate-code rule**: provisioning via the optional extra and version check; running the tool on the real files with a timeout; explicit-path and hook behaviour; pair-to-cluster grouping; tuning pass for defaults; docs (rules, configuration, pre-commit guidance). Implements: D7, D8, D9, D10, D17, D18, D19. After: 5, 6. Issue: #39
8. **`--base` new-code gating (#52)**: sketch on its own issue, using this ledger as prior work. It designs the finding identity field and every rule's key to D16's requirements, builds on the pipeline (D2), and runs the pipeline on a temp extraction of the base tree (D9's rationale). Implements: D1, D16. After: 7. Issue: #52
