# Design: Extract the scan pipeline from the CLI

**Date:** 2026-10-30
**Status:** ratified
**Mode:** sketch
**Issues:** #56
**Parent:** design/specs/005-cross-file-rules-and-duplicate-code/design.md

## Summary

`check` in `src/house_lint/cli.py` calls `_scan` (`cli.py:218-402`), which holds the whole scan: discovery, the per-file loop, cache reads and writes, and result assembly. The milestone adds a project phase (#59), so the pipeline needs a home outside the CLI. This change moves it there without changing behaviour, and first adds characterization tests pinning suppression and HSL900 results through a whole run.

**In scope:** the characterization tests (D5, D2's cache pins, and pins for a relative and a `~` `--cache-dir`), the move, retargeting the tests that patch CLI internals, and updating references to the old location.

**Out of scope:** the loaded-file split (#57), the options rework (#58), the project phase (#59), and any fix for something noticed along the way.

**New homes:**
- The pipeline and its entry point: a new module (D1), split into named phases (D2), with inputs set by D3.
- `_cache_keys`, `_persist_cache_entry` and `_resolve_file_rules` (`cli.py:143-215`) move with it.
- Cache-location resolution (`cli.py:474-479`), including `expanduser().resolve()` on a given path, moves with it.
- The per-run cache object (D2) lives in the pipeline module. It stays out of `cache.py`, which handles entry storage and doesn't know `FileScanResult`.

**What stays in `cli.py`:** argument parsing, config loading, the internal-error boundary (D4), `_write_result`, `_exit_code`, and the `rules` command.

**References to update in the same change:**
- `CLAUDE.md:45` and `CLAUDE.md:74` (layout entry, `cli._cache_keys`)
- `src/house_lint/cli.py:1` (module docstring)
- `src/house_lint/source.py:47`
- `src/house_lint/cache.py:291` and `:305`
- `src/house_lint/discovery.py:866`
- `tests/unit/test_cache.py:252` and `:295`
- `tests/unit/test_discovery.py:1213`
- `tests/integration/test_cli.py:567` (comment naming which modules import a file reader)

**Tests that patch CLI internals:** `tests/integration/test_cli.py:546` (`cli.scan_source`) and `:1337` (`cli._scan`).

## Posture

- **Failure handling:** unchanged. Per-file failures still come back as results, and anything else propagates to `check`'s existing boundary. No new handling.
- **Compatibility:** internal only. The new module is not public API (`__init__.py` exports only `__version__`). Output, exit codes, and the content of cache entries stay identical.
- **Scope:** the extraction and its characterization tests only. Nothing noticed along the way gets fixed here.
- **Rollout:** ships directly. The PR is typed `chore:` rather than `refactor:`, because nothing is user-visible and `refactor:` would add a changelog line for PyPI users (`.claude/rules/changelog-quality.md`).
- **Hardening:** project defaults.
- **Altitude:** `build`. One session builds this right after the sketch, so the ledger cites files.

## Decisions

### D1: Where does the pipeline live?

**Deciding factor:** a reader can tell the whole-run pipeline from the one-file scan by where it sits.

| | A: New `pipeline.py`, entry point `run_scan` | B: Grow `scanner.py` |
|---|---|---|
| One home per concern | `scanner.py` stays one file (`open_source`, `scan_source`); `pipeline.py` is the whole run | One module holds both, and its docstring ("Per-file scan orchestration") has to widen |
| Module size after the move | About 300 lines and 176 lines | About 480 lines, within the typical range |
| Fit with parent D2 | Literal: "a pipeline module the CLI calls" | Holds in spirit: a module the CLI calls |
| Existing `scanner.*` test patches (`test_cli.py:1192`, `:1208`, `:1248`, `:1288`, `:1319`) | Keep working, because the code they patch doesn't move | Keep working |
| Project phase (#59) home | Sits next to the file phase in `pipeline.py`, with no per-file code around it | Sits in a module named for scanning, beside the per-file helpers |

**Recommendation:** A, because the one-file and whole-run scopes stay separate and the module names say which is which.
**Pick B instead if** you'd rather not have two modules whose names both suggest "the scan".
**Reversibility:** easy
**Ratified:** Chose a new `pipeline.py` with entry point `run_scan` over growing `scanner.py`, so the one-file and whole-run scopes stay separate, accepting two modules whose names both suggest scanning.

### D2: How far is the pipeline split into named phases?

Parent D2's reason for extracting was "phases become named steps", but it doesn't say how far to take that. The file loop (`cli.py:275-390`) interleaves scanning with cache logic. Six pieces of cache state take part: `cache_dir`, `cache_self_ignore`, `cache_base_is_safe`, `cache_reporter`, `wrote_cache_entry`, `cache_writes_failed`.

**Deciding factor:** each phase is a named step a reader can find, which is what parent D2 ratified the extraction for.

| | A: Move `_scan` as one function | B: Named phase functions; the cache state stays as loop locals | C: Named phases, plus a small per-run cache object owning that state |
|---|---|---|---|
| Length of the longest function | About 185 lines | File phase about 115 lines, with six `continue`/`break` exits | File phase about 40 lines: load, look up, scan, store |
| Cache rules gathered in one place (read gating, write circuit breaker, prune only after a write) | No | No | Yes |
| Where #59's project phase goes | Needs the split first | Between two named calls | Between two named calls |
| Risk of a behaviour change in this PR | Lowest | Low | Moderate: cache conditions get reordered behind two methods. Existing tests catch only two of eight changed cache rules, so this needs the cache pins below |
| New abstraction | None | None | One class with one caller |
| Diff to review | Almost entirely a move | Move plus a split | Move plus a rewrite of the loop body |

**Recommendation:** C. A leaves the milestone's next issue to do the splitting, and B still leaves the loop mostly about caching. Gathering the state also keeps the write-breaker flags and the rules that read them together.

**Cache pins.** These land before the move, because a probe of today's `_scan` changed each rule and the full suite still passed. A whole run must show that:
- reads continue after a failed write disables writes
- pruning still happens after a run stops early, if an entry was written
- a stop result is never written to the cache
- an unhashable file doesn't disable caching for later files
- after one failed write, no further writes are attempted that run
- a failed prepare doesn't disable reads or writes that would otherwise succeed

The move and the cache object land as separate commits, each with tests green.

**Pick B instead if** you want this PR's diff to stay easy to check as a move, and are fine with the loop staying long. **Pick A instead if** you'd rather #59 decides the split once the project phase's real needs are known.
**Reversibility:** easy
**Ratified:** Chose C over A and B, so each phase is a named step and the file loop reads as load, look up, scan, store with the cache rules in one place, accepting one new class with one caller, about six more cache pins, and a loop rewrite landed as its own commit after the move. Revised at the sketch challenge (Finding 4): the original reasoning wrongly claimed existing tests pinned these rules.

### D3: What does the entry point take for the cache?

`check` currently resolves the cache location and passes `_scan` two values that must agree: the resolved `cache_dir`, and `cache_self_ignore` (true exactly when the user gave no `--cache-dir`). The other inputs (`paths`, `root`, `config_path`, `config`, `use_gitignore`, `read_cache`, `debug`) carry over as they are.

**Deciding factor:** the CLI only parses arguments and renders (parent D2).

| | A: Same as `_scan`: resolved `cache_dir` plus `cache_self_ignore` | B: `cache_dir: Path \| None` as the user gave it; the pipeline resolves the location and derives self-ignore | C: A request dataclass carrying all the inputs |
|---|---|---|---|
| CLI imports cache internals (`default_cache_base`, `versioned_cache_dir`) | Yes | No | Depends on which form it carries |
| Two values that must agree | Yes | No | Yes, unless combined with B |
| Diff to `check` | Smallest | Removes the cache-resolution lines | Adds a construction site |
| New type | None | None | One dataclass, built once |

**Recommendation:** B, because it removes the pairing and leaves `check` with no cache logic. A given path means the user chose it, never that it was vetted: as today, it skips the self-ignore write and the symlink check. This ledger doesn't define how a second run gets its cache. #52's sketch decides that, and the entry point is private and cheap to change.
**Pick A instead if** you want the location logic to stay visible in `check`. **Pick C instead if** you expect the input list to grow a lot in #59 or #52.
**Reversibility:** easy
**Ratified:** Chose B over A and C, so the pipeline takes `cache_dir: Path | None` as the user gave it and `check` holds no cache logic, accepting that a given path is never vetted and that #52's sketch has to settle how a second run gets its cache. Revised at the sketch challenge (Finding 1): removed a route for #52 to share the project's cache, whose symlink check would not have reached the base run.

### D4: Where does the internal-error boundary sit?

**Deciding factor:** no behaviour change, with one boundary per process. **Recommendation:** it stays in `check`. The pipeline lets unexpected exceptions propagate, and `check` keeps producing `internal_error("cli", "scan")` with exit 4, so the JSON `phase` value stays `cli`. The per-file `stop` results in `scanner.py` are unaffected, and so is `DiscoveryError`, which the pipeline still turns into a result as `_scan` does today (`cli.py:239`). `test_cli.py:1337` retargets to the name `check` now calls.
**Pick the pipeline as the boundary instead if** you want a direct caller to always get a `ScanResult` back. #52 can add that when it needs a base-run failure reported differently, so nothing has to be decided for it now.
**Reversibility:** easy
**Ratified:** Chose keeping the boundary in `check` over moving it into the pipeline, so internal-error output and exit codes don't change, accepting that a direct caller of `run_scan` sees unexpected exceptions rather than a `ScanResult`.

### D5: What must the characterization tests pin?

Parent D14 names this as the behaviour to pin first, because #60 reworks per-file pragma handling: today's suppression and HSL900 results for file rules must not change. `tests/unit/test_suppressions.py` pins `apply_suppressions` directly. Through a whole run, only the budget cases (`test_cli.py:1036-1184`) and one per-file-ignore case (`:239`) cover it, and nothing checks suppression across a warm-cache replay.

**Deciding factor:** the pins catch a change in what a user sees from a run, both for this move and for #60.

| | A: Per-case whole-run pins, cold and warm | B: One combined scenario, cold and warm | C: Warm-cache pins only for what's already covered at unit level |
|---|---|---|---|
| Cases covered | Each pragma form (`ignore`, `ignore-next`, `ignore-file`) and each HSL900 class (malformed, unknown rule, rule disabled by selection, rule disabled by per-file-ignores, misplaced, unused, conflicting) | The same cases, in one file | Pragma forms and one or two HSL900 classes |
| Pinned for each case | Exact findings (rule, path, line, message), `suppressed_count`, and exit code; the warm run equals the cold run | Same, for the file as a whole | Warm run equals cold run |
| A failure says which case broke | Yes | Only by reading the diff | Partly |
| Guards #60's per-file pragma rework | Yes | Yes | Weakly: relies on the unit tests staying valid through #60 |
| Test volume | About a dozen cases over one assertion | One large scenario | Few |

**Recommendation:** A, because #60 changes the code around these unit tests and needs a pin that doesn't depend on those internals. Each case is a small input over a shared assertion, so the volume is cheap. The pins land in a commit before the move and must pass unchanged across it.
**Pick B instead if** you want a smaller test file and are fine diagnosing failures from the diff. **Pick C instead if** you trust the unit tests to survive #60.
**Reversibility:** easy
**Ratified:** Chose A over B and C, so a failure names the case that broke and the pins survive #60's rework of the code around the unit tests, accepting about a dozen more whole-run test cases.

### D6: What does the file phase return?

Each `SourceFile` is a loop local today (`cli.py:313`). Parent D14 has the project phase re-parse pragmas from in-memory bytes, which only holds within an iteration, and `test_each_scanned_file_is_read_exactly_once` forbids re-reading them later.

**Deciding factor:** name the interface without pulling #59's decision into a no-behaviour-change PR. **Recommendation:** the file phase returns the run's totals and keeps no sources. Retention is #59's to settle against parent D14. **Pick a retaining output instead if** #59's sketch has already settled on keeping every source.
**Reversibility:** easy
**Ratified:** Chose returning totals with no retained sources over defining a retaining output now, so memory is unchanged and D14's mismatch has an owner in #59, accepting that #59 changes this private return shape. Added at the sketch challenge (Finding 3).

## Assumed

- Parent D2 holds: the pipeline lives in a module the CLI calls, and the CLI only parses arguments and renders output. Evidence: `design/specs/005-cross-file-rules-and-duplicate-code/design.md` D2, checked against `cli.py`; no sibling issue has landed.
- `check` is `_scan`'s only caller. Evidence: `cli.py:480`, and the only other reference in code is the patch at `test_cli.py:1337`.
- Moving the code needs no cache migration. The namespace includes a fingerprint of house-lint's own sources, so the new module invalidates old entries on its own. Evidence: `cache.py:118`, `cache.py:127`.
- The read-once guarantee is unaffected, since `SourceFile.load()` remains the only read and `test_each_scanned_file_is_read_exactly_once` patches `source.read_regular_file_bytes`, not the CLI. Evidence: `test_cli.py:557-586`.
- The `scanner.*` patches in `test_cli.py` keep working, since `scanner.py`'s contents don't move under D1 A or B. Evidence: `test_cli.py:1192`, `:1208`, `:1248`, `:1288`, `:1319`.

## Build

- [x] Implementation and tests committed
- [x] Docs: `CLAUDE.md` layout and option references; nothing in `docs/` or `README.md` names the old location
- [ ] Ship-time challenge

**Calls made during the build:**
- `_persist_cache_entry` folded into `RunCache.store` instead of moving as its own function: its one caller is `store`, and its only work was building the `CachedFileResult`. Its rule, that an unhashable file must not trip the write breaker, is now a comment on `store`'s guard.
- `_cache_keys` returns `None` rather than `(None, None)`: `lookup` and `store` then each check one value for "no key".
- The cache location resolves before discovery and `RunCache` is built after it: this keeps today's order, so an unresolvable `--cache-dir` still outranks a discovery error, and the symlink check still runs after discovery.
- `run_file_phase` collects each file's result and totals them at the end instead of keeping four accumulators: the three tally sites become one append each. The abort result from `open_source` now goes through the same totals; its findings and suppressed count are always empty, so the totals don't change.
- The `--debug` rule against replaying a cached error sits in the file loop, not on `RunCache`, and `RunCache` takes a ready-made reporter: the rule exists because `scan_source` prints tracebacks, so it sits beside that call and reads the same flag. Set at the ship-time challenge (Finding 2).
- `run_scan` calls `prepare` and `finish`, and `run_file_phase` is only the loop: `run_scan` then shows the cache's whole lifecycle. The order of side effects is unchanged. Set at the ship-time challenge (Finding 3). The loop's body is about 60 lines, not D2's estimate of 40, because the reasoning comments moved with it.
- Three pins beyond the ledger's list: a run that stops before writing doesn't prune, a run whose only write failed doesn't prune, and an unresolvable `--cache-dir` is an internal error even when discovery would also fail. Each guards an ordering or gate the loop rewrite could have changed. A fourth, that a symlinked default cache is neither read nor written, was added at the ship-time challenge (Finding 5), because removing that read gate passed the rest of the suite. A fifth, that a run discovering nothing leaves the cache untouched, covers the guard Finding 3's change moved, which had no test.
