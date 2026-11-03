# Design: Project rules and the project phase

**Date:** 2026-11-02
**Status:** built
**Mode:** sketch
**Issues:** #59
**Parent:** design/specs/005-cross-file-rules-and-duplicate-code/design.md
**Prior art:** design/research/2026-11-02-project-rule-input/prior-art.md

## Summary

Every rule today runs once per file, and `pipeline.run_file_phase` drops each file's bytes at the end of its loop iteration (`pipeline.py:38`). Duplicate-code detection (#39) has to compare files against each other. This change adds a second kind of rule that receives the scanned file set, a phase that runs after the file loop, and one check for whether a rule applies to a file. Discovery stops assuming Python: it finds the languages the enabled rules handle.

**In scope:** the `ProjectRule` row, the project phase, the multi-location candidate, the applies check, language-driven discovery, and tests using a test-registered rule.

**Out of scope, each with the issue that owns it:** suppressing project findings, `related`, group id, and `schema_version` (#60; parent D12-D14), TypeScript analysis and pragmas (#61), jscpd and two-pass discovery for explicit paths (#39; parent D8-D10). No shipped rule is a project rule when this lands, so `PROJECT_RULES` ships empty. No candidate budget for project rules: nothing produces candidates yet, and #60 or #39 adds one if a real rule needs it (parent D14 mentions one).

**New homes (each follows from the decision named):**
- `ProjectRule`, its factory, and `PROJECT_RULES`: `rule_catalog.py`, as the sibling of `FileRule` that spec 008 D1 set out. `RULES` merges both tables, and `DEFAULT_SELECT`, `ORDINARY_RULES`, and `is_known_rule` keep deriving from it. HSL900 stays a plain `Rule` with no languages (D5).
- `languages` on rows: `FileRule` and `ProjectRule` both carry it. `file_rule` and `configurable_rule` set it to Python, because their `Detector` protocol takes a `PythonSource`. The project factory takes it as an argument.
- `Location`, `ProjectCandidate`, and `ProjectRuleFailure`: a new language-neutral module, `project_rule.py`, importing only `results` and `source`. `analysis.py` doesn't fit, since it imports the Python layer and #61 would inherit that (D2, D3).
- The applies check: `config.py`, replacing `per_file_enabled_rules` (`config.py:93`) with one that also takes the file's language. `pipeline._resolve_file_rules` calls it for detector dispatch and the cache key, and for each project rule's input. Project-rule options parse through the same loop as file rules (`config._rule_options`, `config.py:248`).
- The project phase: `pipeline.run_project_phase`, called from `run_scan` after `run_file_phase` and before `cache.finish`. It adds to the same finding and error totals.
- Retained files: `FilePhaseResult` gains each project rule's input and whether the file phase aborted. Its docstring at `pipeline.py:38` changes (D1).
- The languages to discover: a `discover_files` argument, computed by a function in `config.py` beside the applies check, so a change to one is seen beside the other (D5, D6). It replaces the two `language_for(path) is None` tests (`discovery.py:289`, `:373`).
- Skipping analysis for a language with no analysis layer: `run_file_phase` (D7).
- Giving a pragma that names an enabled project rule no unused verdict: `suppressions._collect_claims`, after its disabled-rule check (D9).

**Must hold in the build:**
- With no project rule enabled, no file's bytes are kept past its loop iteration, and `test_each_scanned_file_is_read_exactly_once` still passes, unweakened.
- A file is read once whether or not a project rule is enabled, and on a cache hit its bytes are still available to the project phase (the read comes before the lookup, `pipeline.py:239-251`).
- A per-file-ignored file leaves that project rule's input entirely (parent D21).
- A Python file that fails to decode or parse stays in a project rule's input, because `LoadedFile` is bytes and the wrapped tool reads the real file (spec 009 D1).
- Nothing from the project phase is cached (parent D11).
- A pragma on a Python file naming a rule that doesn't handle Python gets its own HSL900 message, distinct from "disabled" (parent D21). A pragma naming an enabled project rule that does apply is never reported as unused (D9).
- Output order stays deterministic: project rules run in `enabled_rules` order, and each input keeps discovery order.

## Posture

- **Failure handling:** fail loudly, carried from the parent. A project rule that fails produces a reported error and a failing exit, with no retries or fallback.
- **Compatibility:** break freely and mark breaks with `feat!`, per `CLAUDE.md`. Projects that enable only Python rules keep the same file set and findings (parent D6 and the issue's acceptance line).
- **Scope:** the minimal engine, verified with a test-registered rule and no shipped project rule. Pragmas for project rules, `related`, and group id stay with #60, TypeScript analysis with #61, and two-pass discovery for explicit paths with #39. No project-rule candidate budget.
- **Rollout:** ships directly.
- **Hardening:** project defaults, with no run-wide memory cap. Hassette's 1,527 Python files total 10.5 MB, and the cost applies only when a project rule is enabled.
- **Altitude:** `build`. One session builds it, so the ledger cites files.

## Decisions

### D1: What does a project rule receive, and what stays in memory?

**Situation:** The file loop reads each file once and lets go of its bytes after scanning it. A project rule runs after the loop, so something has to survive. Two consumers are known: jscpd (#39), which reads the real files itself and needs only paths (parent D9), and #60's pragma resolution, which re-parses pragmas from in-memory bytes in any file with a project candidate (parent D14, as narrowed by D9). `CLAUDE.md` allows an uncached external tool to read files itself, but #60's resolution runs inside house-lint.

**Deciding factor:** every known consumer has what it needs without a second read, and a run with no project rule pays nothing.

| | A: `tuple[LoadedFile, ...]`, retained only for files in an enabled project rule's input | B: Paths only; a rule or #60 re-reads what it wants | C: Paths, plus bytes only for files containing a house-lint pragma |
|---|---|---|---|
| Run with no project rule | No retention | No retention | No retention |
| Memory with a project rule, hassette | About 10.5 MB | About none | Bytes of files with pragmas |
| Theoretical ceiling | 100,000 files at 10 MiB each, uncapped | None | Same, in a repo where every file has a pragma |
| Single read of a scanned file | Kept | Broken: a file changes between the parse and the re-read | Kept for pragmas; a native rule re-reads |
| #60's pragma resolution | Works as parent D14 wrote it, for files with candidates | Supersedes parent D14 and re-opens its option B | Works, with a byte search during the file loop |
| A native content rule later | Works | Re-reads | Re-reads |
| Machinery | A list per rule | None | A byte search in the file loop, plus a second input shape |
| Peers | Pylint keeps every file's lines, jscpd keeps tokens, and neither caps it | MegaLinter and pre-commit, which do no pragma resolution of their own | None |

**Recommendation:** A. It's what parent D3 and D14 assume, and it's the peer norm for rules that compare content. Peers avoid paying when the rule is off, which A does, and the only peer that bounds retention (oxlint) does it for import graphs, which don't fit duplicates. A rule that needs a file's language calls `language_for(file.path)`, per spec 009 D3.
**Pick B instead if** you'd accept a second read of a file with a project candidate at #60. **Pick C instead if** a repo you care about makes 10 MB-scale retention a real problem.
**Reversibility:** easy
**Ratified:** Chose `LoadedFile`s retained only for files in an enabled project rule's input over paths only or bytes only for pragma files, so every known consumer works without a second read and a run with no project rule pays nothing, accepting uncapped retention while a project rule is enabled.

### D2: What does a project rule return?

**Situation:** `CandidateFinding` has one location plus Python-flavoured provenance (`SourceKind`, `owner`, `analysis.py:31`). The issue requires candidates that carry several locations. Parent D12 reports one finding per occurrence, and the `related` list and group id belong to #60, so #59 needs a shape and a place where it expands.

**Deciding factor:** a duplicate's copies are equals, so nothing has to invent a primary, and #60 can add to it without reshaping.

| | A: `ProjectCandidate(message, locations)`; the phase expands it to one `Finding` per location | B: Rule returns `CandidateFinding`s with a new group key | C: Primary location plus related locations |
|---|---|---|---|
| Invents a primary | No | No | Yes, arbitrarily for a duplicate |
| Represents a group of one or a split group | A group is one object | Any key can be mistyped or lone | One object |
| Python provenance on a language-neutral rule | None | `source_kind` and `owner` are filled by nothing until #60 computes them | None |
| What #60 adds | At expansion, `related` is the other locations and the group id comes from the candidate | Rebuilds groups from keys | Reshapes for one finding per occurrence |
| Today's reporters | Unchanged: they see `Finding`s | Unchanged | Unchanged, after expansion |
| Peers | PMD CPD's N-ary group, symmetric | None | LSP, SARIF, Ruff (as reporter contracts). Pylint's text-blob form anchors its report on an unrelated file |

**Recommendation:** A. `Location` is the input's `LoadedFile`, line, column, end line, and end column, validated by the shared `results.validate_location`, and a candidate must have at least one. At #59 every finding from one candidate shares its message. The primary-plus-related contracts are about what a reporter emits, which parent D12 already answers.
**Pick B instead if** you want one candidate type for both kinds of rule. **Pick C instead if** you expect a project rule with a true primary site, such as an import cycle reported at one import.
**Reversibility:** easy
**Ratified:** Chose a symmetric `ProjectCandidate` with a tuple of locations, expanded to one `Finding` per location, over group keys on `CandidateFinding` or a primary plus related locations, so no primary is invented and #60 can add to it without reshaping, accepting a second candidate type alongside `CandidateFinding`.
**Amended at the ship-time challenge:** `ProjectCandidate` has no `rule_id`, since the phase stamps the ID of the rule that ran, and `Location` takes the input's `LoadedFile` rather than a path string, so a wrong ID can't reach output and a wrong path is hard to write. The phase doesn't check that a location's file came from the rule's input. This forecloses a rule reporting on a file outside its input.

### D3: How does a project rule report failure?

**Situation:** A file rule that raises gets a generic `internal_error` that hides its message and exits 4 (`scanner.py:124-131`, `cli.py:96-99`). Parent Posture requires a missing or failing jscpd to report "an operational error with an install hint".

**Deciding factor:** a rule's expected failure reaches the user with its own message, and a bug stays distinct from an expected failure.

| | A: Rule raises `ProjectRuleFailure(message)`; any other exception is an internal error | B: Rule returns a result object of candidates, errors, and a complete flag | C: Any exception is an internal error |
|---|---|---|---|
| Install hint reaches the user | Yes | Yes | No: `internal_error` hides details |
| Exit code | 3 for a failure, 4 for a bug | 3 | 4 for everything |
| Representable bad state | None | Incomplete with no error, or errors marked complete | None |
| Every rule's return statement | A list | A wrapper object | A list |
| Precedent here | `CandidateBudgetExceeded` is raised and caught at the boundary | None | `_scan_ready_source` |
| Peers | ESLint aborts on rule errors | unknown | ESLint aborts on rule errors |

**Recommendation:** A. A failure carries no partial candidates, since jscpd has none to give. The engine turns it into a `LintError` with `rule_id` set, phase `project`, and kind `project-rule`, then runs the remaining project rules. An unexpected exception follows the existing boundary: `internal_error`, no further project rules, and results so far are kept.
**Pick B instead if** you expect a rule to want to report several independent errors from one run. **Pick C instead if** you'd rather rules print their own hints, which breaks JSON output.
**Reversibility:** easy
**Ratified:** Chose raising `ProjectRuleFailure(message)`, with any other exception an internal error, over a returned result object or treating every exception as internal, so an expected failure reaches the user with its own message and a bug stays distinct from it, accepting that a failed rule reports no partial candidates and can report only one error per run.

### D4: What happens to a project rule when a file in its input fails to load?

**Situation:** A `LoadFailure` (oversized, unreadable, non-regular) has no `LoadedFile`, is reported as an error, and the loop moves on (`pipeline.py:245-248`). The run already exits 3. Spec 009 left this question to #59. A process-boundary abort (`stop`) ends the loop early, leaving most files unscanned.

**Deciding factor:** one bad file doesn't silently cost a project-wide check, and the failing exit stays visible.

| | A: The rule runs on what loaded | B: Skip project rules when any file failed to load | C: Pass failures to the rule too |
|---|---|---|---|
| A repo with one 11 MiB generated file | Duplicate check still runs; run exits 3 until the file is excluded | No duplicate findings at all until it's excluded | Same as A, plus a rule decision |
| Findings may miss a pair involving the failed file | Yes, with its error reported | n/a | Yes, or the tool still reads it |
| Machinery | None | A check and an explanatory error | A second input shape |
| Peers | Biome skips unreadable files when indexing; others unknown | unknown | unknown |

**Recommendation:** A. It costs nothing, and the run already fails with the file named. A file-phase abort is different: with `stop` set, the project phase doesn't run, because its input would be arbitrarily partial and the run is already reporting an internal error.
**Pick B instead if** you'd rather never see project findings from a partial file set. **Pick C instead if** you expect a rule that can do something useful with a file house-lint couldn't read.
**Reversibility:** easy
**Ratified:** Chose running each project rule on the files that loaded, skipping the project phase only on a process-boundary abort, over skipping project rules on any load failure or passing failures to the rule, so one bad file doesn't cost a project-wide check, accepting that findings may miss a pair involving a file whose load error is reported.

### D5: Which rules decide the languages discovery looks for?

**Situation:** Parent D6 ratified "discover only languages some enabled rule handles". But HSL900, which validates pragmas, is always enabled. Today `select = []` still scans Python: a probe gave `files_scanned: 1` and an HSL900 finding, because discovery ignores rule selection. Letting only ordinary rules decide changes that one case. Letting HSL900 decide means that at #61, every Python-only project starts scanning TypeScript, contradicting D6.

**Deciding factor:** one sentence explains why a language is scanned, with no special case, and a project enabling only Python rules sees no change.

| | A: Only ordinary rules decide; HSL900 validates every scanned file with an analysis layer | B: Ordinary rules plus an always-on Python floor | C: HSL900 declares its languages and decides too |
|---|---|---|---|
| Projects enabling any Python rule | Unchanged | Unchanged | Unchanged until #61, then TypeScript files are scanned for no rule |
| `select = []` | Discovers nothing: zero-file scan, no HSL900 findings. A break, marked `feat!` | Unchanged | Unchanged |
| A future project enabling only TypeScript rules | Scans TypeScript only | Also parses every `.py` file | Scans both |
| Stale pragma in a Python file when only TypeScript rules are on | Unreported until a Python rule is on | Reported | Reported |
| Special cases | None | A floor constant | HSL900's row means "validates" and also "pulls a language in" |
| Issue's "Python-only file set must stay unchanged" | Holds for every project with a rule enabled; not for `select = []` | Holds literally | Holds until #61 |
| Peers | Biome and oxlint derive scope from enabled rules; ESLint selects language by config, not rules; Ruff has no zero-rule state | None | None found |

**Recommendation:** A. A project with no rules enabled has nothing for pragmas to suppress, and today every pragma there is just reported as naming a disabled rule. HSL900 stays impossible to disable: it runs on every scanned file whose language has an analysis layer, and D7's skip branch is the one place that's decided. Two follow-ons: the zero-file note says no enabled rule handles any language rather than telling the user to check `include`, and the PR is `feat!` with a `BREAKING CHANGE:` footer.
**Pick B instead if** you want `select = []` to keep validating pragmas, accepting that Python is always parsed. **Pick C instead if** you want every pragma in the tree validated regardless of selection, and accept scanning TypeScript everywhere at #61.
**Reversibility:** easy
**Ratified:** Chose letting only ordinary rules decide the discovered languages, with HSL900 validating every scanned file whose language has an analysis layer, over an always-on Python floor or having HSL900 declare languages, so one sentence explains why a language is scanned and a project enabling only Python rules sees no change, accepting that `select = []` becomes a zero-file scan with no HSL900 findings, a break marked `feat!`.

### D6: Does discovery call the applies check?

**Situation:** Parent D21 says discovery, dispatch, cache keys, the project file set, and HSL900 all call one check, and the issue says discovery derives its languages "through that check". The check includes per-file-ignores, though, and calling it per file during discovery would stop discovering a file that every rule ignores. That file is scanned today, counts in `files_scanned`, and has its pragmas validated.

**Deciding factor:** the Python-only file set stays unchanged. **Recommendation:** discovery takes the union of enabled ordinary rules' `languages` as a set, computed from the same `languages` field, and doesn't call the check. Everything after discovery does. Supersedes "discovery" in parent D21 and "through that check" in the issue; parent D21's outcome for dispatch, cache keys, the file set, and HSL900 stands. An explicit path whose language isn't in the set errors as today, with its message changed from "not a Python file" to say no enabled rule handles it. **Pick calling the check per file instead if** you want a file every rule ignores to leave the scan and `files_scanned`, accepting that its pragmas stop being validated.
**Reversibility:** easy
**Ratified:** Chose having discovery take the union of enabled ordinary rules' `languages` as a set over calling the applies check per file, so the Python-only file set stays unchanged, accepting that discovery and the check share a `languages` field rather than one call, and that a file every rule ignores is still scanned. Supersedes "discovery" in parent D21 and "through that check" in the issue.

### D7: Does #59 add a second language?

**Situation:** `Language` has only `PYTHON` (`language.py:7`). With one language, the code that derives discovery from rule languages can't be shown to work: nothing can demonstrate that enabling a rule brings a new language in. A `.ts` file reaching `scan_source` today would be parsed as Python, fail, and report a syntax error (spec 009's build notes). So any second language needs the file loop to skip analysis for it. Spec 009 D3 expected #59 to add table entries, and left the parsing dispatch as a pickup for #61.

**Deciding factor:** the issue's core behaviour is verified on the real path, with no user-visible change.

| | A: Add `TYPESCRIPT` (`.ts`, `.tsx`) and skip analysis for it | B: Python only; tests patch a fake language into the table | C: Python only; #61 adds the language and the skip |
|---|---|---|---|
| "Enabling a rule brings its language in" proven | Yes, with a real suffix | Yes, on a patched private table | No: only Python versus nothing |
| User-visible change, no rule declaring TypeScript | None: `check a.ts` still errors | None | None |
| Production code with no shipped consumer | One enum member, two table entries, one skip branch | The skip branch | None |
| What #61 adds | The analysis layer, deleting the skip | The language, the entries, the layer | All of that plus the skip, on an engine never exercised with two languages |
| Speculation | The suffixes: none, parent D7 ratified them. The skip's behaviour is a placeholder #61 replaces | The skip, as in A | None |

**Recommendation:** A. A TypeScript file brought in by a project rule is loaded once, retained for that rule, and counted in `files_scanned`, with no parsing, cache entry, or file-rule dispatch. HSL900 doesn't reach it until #61 gives it comment tokens.
**Pick B instead if** you'd rather production code not name a language nothing analyzes yet. **Pick C instead if** you consider the Python-versus-nothing test sufficient for now.
**Reversibility:** easy
**Ratified:** Chose adding `TYPESCRIPT` (`.ts`, `.tsx`) with analysis skipped for it over tests patching a fake language or leaving the language to #61, so enabling a rule bringing its language in is verified on the real path with no user-visible change, accepting an enum member, two table entries, and a skip branch with no shipped consumer until #39 or #61.

### D8: Do project-rule options join the per-file cache key?

**Situation:** `hash_effective_config` hashes every rule's options so a new option can't be left out (spec 008 D3), and that decision left project-rule options to #59. No per-file result depends on a project rule's options, so including them only costs a cold run when one changes, about 7 seconds on hassette.

**Deciding factor:** no filter to maintain, and the same mechanism for every rule. **Recommendation:** include them: `LintConfig.rule_options` holds every rule's options and the hash is unchanged. Peers (Ruff, ESLint) hash settings wholesale. **Pick filtering to file rules instead if** you expect to tune project-rule options often on large repos.
**Reversibility:** easy
**Ratified:** Chose including project-rule options in the per-file cache hash over filtering it to file rules, so there's no filter to maintain and every rule uses the same mechanism, accepting a cold run when a project-rule option changes, about 7 seconds on hassette.

### D9: Does HSL900 report an unused pragma for a project rule?

**Situation:** Parent D14 planned, at #60, to call a pragma naming a project rule unused once that rule had run. That verdict needs a byte search for stale pragmas, a re-parse of those files, and a flag for whether the rule's output was complete. HSL900's existing checks are the peer norm: Biome, ESLint, Ruff, Pylint, and mypy all report bad or unused suppressions. The end-of-run verdict for a cross-file rule is not: Pylint excludes `duplicate-code` from `useless-suppression`, the 2026-10-30 brief rates this "More machinery than any peer; reconsider", and Biome's project-domain suppressions have produced false reports (biomejs/biome#9258).

**Deciding factor:** #59 and #60 aren't shaped around a verdict the closest peer chose not to have.

| | A: Drop the project-rule "unused" verdict now | B: Keep it and decide at #60's pickup |
|---|---|---|
| #59 shaped around the verdict | No | Yes, until #60 decides |
| Stale pragma on a duplicate | Unreported, and hides the duplication if it returns | Reported |
| False "unused" reports | Can't happen | Possible on partial input, as in Biome and mypy |
| `_collect_claims` at #59 | A pragma naming an enabled project rule makes no claim and gets no verdict | Reports it as unused until #60 |
| Peers | Pylint | Biome |

**Recommendation:** A. Everything else in parent D14 stands: structural HSL900 checks, re-parsing from in-memory bytes for files with a project candidate, and owner matching. The disabled-rule check stays per-file, as Ruff's "non-enabled" case does, which is why project-rule IDs stay in the per-file key.
**Pick B instead if** catching stale duplicate pragmas matters enough to carry that machinery.
**Reversibility:** easy
**Ratified:** Chose dropping HSL900's unused verdict for project rules over keeping it for #60's pickup, so this work and #60 aren't shaped around a verdict the closest peer chose not to have, accepting that a stale pragma on a duplicate goes unreported and hides the duplication if it returns. Supersedes parent D14's stale-pragma byte search, its completeness flag, and its move of the disabled-rule verdict to the project phase.

## Assumed

- Two rule kinds, with no facts phase yet. Evidence: parent D3, still consistent with `rule_catalog.py:51`.
- One table with a sibling `ProjectRule`, not a second registry. Evidence: spec 008 D1, superseding that part of parent D3.
- A rule's file input has no hash or language field: `LoadedFile` is path, relative path, and bytes, and language comes from `language_for`. Evidence: spec 009 D1 and D3, `source.py:38`, superseding that part of parent D3.
- Enabling a rule is what brings its languages into the scan. Evidence: parent D6.
- A per-file-ignored file leaves that project rule's input. Evidence: parent D21.
- Project-phase results are never cached. Evidence: parent D11.
- Project rules see only the discovered file set when explicit paths are given, until #39 adds the full-scope pass. No shipped rule is affected. Evidence: parent D10, Breakdown item 7.
- A pragma naming a project rule can't arise in shipped use until #39, since `PROJECT_RULES` is empty. Actually suppressing a project finding stays with #60. Evidence: `suppressions.py:136-150`, parent D14, Breakdown item 5.
- The per-file key still includes enabled project-rule IDs, because the disabled-rule check reads them (D9). Evidence: `pipeline.py:288`, `suppressions.py:139`.
- A project-rule error exits 3 without changes to `_exit_code`. Evidence: `cli.py:96-100`.
- No test or doc pins the "not a Python file" message. Evidence: a search of `src`, `tests`, `docs`, and `README.md` found only `discovery.py:293`.
- The pre-commit hook's `files: \.py$` stays for now. Its owner is #39's hook guidance or #61. Evidence: `.pre-commit-hooks.yaml`, spec 009's build notes.

## Build

- [x] Implementation and tests committed
- [x] Docs
- [x] Ship-time challenge

**Calls made during the build:**
- `ProjectRule.run` is `(files: tuple[LoadedFile, ...], options) -> list[ProjectCandidate]`, with no root and no `limit`: `LoadedFile` already carries the absolute and relative paths, and the posture has no candidate budget.
- Two factories, `project_rule` and `configurable_project_rule`: this mirrors the file-rule pair, so Pyright rejects a run function and options type that disagree.
- `discover_files` takes `languages` as a required second positional argument, with no default: a Python default would hide a caller that forgot to pass it.
- A project rule with an empty input doesn't run: a tool handed no paths could fall back to scanning its working directory.
- Text output prints an error's `rule_id` before its message, so two failing project rules can be told apart, including a rule that crashes. An internal error from a project rule carries `rule_id` too.
- A pragma's language check comes before its disabled check: enabling a rule that doesn't handle Python wouldn't make the pragma useful, and the cached result stays valid when that rule is toggled.
- The base zero-file message becomes `empty scan: no files selected`: a TypeScript-only scan makes "no Python files" untrue. The no-rules guidance applies even with explicit directories or `include = []`, since it's the actual cause.
- The explicit-path error reads "not a regular file any enabled rule handles", because the same branch also rejects non-regular files.
- A rule's findings are built inside the same `try` as its `run`, so anything wrong with its output is that rule's internal error, none of its findings are kept, and the run's other results are.
- The Assumed line saying no test pins the old message was off by one: `test_explicit_paths_are_strict_and_directories_recursive` matched "Python". It now matches the new message.
