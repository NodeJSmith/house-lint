# Design: `--base` new-code gating

**Date:** 2026-11-09
**Status:** built
**Mode:** sketch
**Issues:** #52
**Parent:** design/specs/005-cross-file-rules-and-duplicate-code/design.md
**Prior art:** design/research/2026-10-30-linter-engine-architecture/prior-art.md

## Summary

hassette and hass-hassette each keep a copy of the same CI scripts (`tools/check_file_size_regressions.py`, `tools/check_duplicate_code.py`, `tools/lint_helpers.py`; 1,159 lines in hassette) so a PR fails only on findings it introduced, not on the backlog. This change makes that a house-lint feature: `house-lint check --base <ref>` reports only the findings the checked-out commit introduced since its merge-base with `<ref>`, for every rule. Afterwards both repos can delete their scripts and PMD.

How a run works:
1. Resolve the base commit (D3) and the head commit (D2), and the rename map between them.
2. Extract each commit's tracked files into its own temporary directory (D11). The working tree is never read.
3. Load the head tree's config (D4) and run the normal pipeline, `pipeline.run_scan`, once on each extraction, sharing the project's cache (D8).
4. Match head findings to base findings rule by rule (D5, D6, D7). Report only the head findings with no match.

Both scans always run (D3).

What's in scope: `--base` on `check`, matching for every shipped rule, HSL102's grew-past-threshold check, JSON and text output for the gate, docs with a CI example, and filing the follow-up issues in hassette and hass-hassette that parent ledger's Done-when requires. Out of scope: the PR-label override (`lint:*-allowed`), which stays a thin wrapper in each repo, and a committed baseline file (D1).

New homes:
- `src/house_lint/new_code.py` (new): resolving refs and the rename map, extracting the two trees, running the pipeline on each, matching findings, and building the gate's result. `cli.check` calls it when `--base` is given.
- `src/house_lint/matching.py` (new): the exact-match passes and the default matcher, a leaf module that rule modules can import without a cycle (D10).
- The per-rule matching kind: a `base_match` field on the catalog rows in `rule_catalog.py`, defaulting to the generic key match (D10). HSL102 and HSL104 rows name their own.
- HSL102's ratchet comparison: `rules/file_length.py`, next to its detector (D7).
- HSL104's containment matching: `rules/duplicate_code.py`, next to the clustering it compensates for (D6).
- The cache location for a run: an explicit location type in `pipeline.py` that replaces `run_scan`'s `cache_dir: Path | None`, from which `run_scan` derives the `self_ignore` it passes to `RunCache` today. `cache.py`'s functions keep their explicit `self_ignore` bool, which `RunCache` passes from the type (D8).
- The gate's output: a `base` section on `ScanResult` in `results.py`, rendered by `reporters/json.py` and `reporters/text.py`. It holds the `--base` ref as given, the resolved base and head commit SHAs (D3), and the count of head findings matched as pre-existing. The gate result's `root` and `config` are the real project's, never an extraction's. Paths in errors from either run are rewritten from extraction-relative to project-relative, and each error says whether it came from the base or the head run. `files_scanned` and `suppressed_count` are the head run's, and `--fail-on-empty` judges the head scan, since the head is what the gate reports on. The text reporter prints the new findings as a normal run does, then one summary line naming the base commit and the pre-existing count.
- No identity field is added to `Finding`: matching is computed per run from the two scans and the trees' text (D5), so parent D16's identity-field design is superseded and nothing is stored.

Where the project root is a subdirectory of the repository (a config below the git top level), each extraction holds the whole repository and uses the same repo-relative subdirectory as its root. Every git call `new_code` makes (`rev-parse`, `merge-base`, the rename map's `diff`, `read-tree`, and `checkout-index`) runs with its directory at the git top level (`git rev-parse --show-toplevel`), because `checkout-index --prefix` run from a subdirectory extracts only that subtree, which would leave a config above the root missing from the HEAD extraction (D4). The rename map's paths are repo-relative, so they are translated to root-relative and filtered to the root before matching. Discovery inside an extraction runs with `use_git=False`: the extraction holds only tracked files, so a plain walk matches what `git ls-files --cached` would list.

## Posture

- **Failure handling:** fail loudly, as the parent ledger does. A ref that doesn't resolve, no merge-base (a shallow clone), running outside a git repository, an extraction failure, or an error from either run's scan is reported as an error and fails the run. No retries.
- **Compatibility:** break freely, as the parent ledger does. Every consumer pins its version, so the JSON output may change shape.
- **Scope:** minimal. `--base <ref>` on `check`, the HSL102 grew-past-threshold check, docs with a CI example, and the follow-up issues in hassette and hass-hassette. The PR-label override stays out, per the issue.
- **Rollout:** ship outright as a `feat`. It unblocks the first release containing HSL104 (spec 013 Assumed, release timing).
- **Hardening:** project defaults. Temporary extractions are always removed, including after an error, except when the process is killed (D11).
- **Altitude:** build. One session builds it, and the ledger cites files.

## Decisions

### D1: Two trees, a committed baseline file, or a diff filter?

**Situation:** The prior-art brief found that no mature linter runs its pipeline over two trees and diffs the results. ESLint (bulk suppressions), basedpyright, and elm-review record existing violations in a committed baseline file and subtract it on later runs. Third-party wrappers such as `riff` drop findings outside the lines a diff changed. hassette's scripts already use two trees: a `git archive` of the merge-base, scanned beside the head. Parent D1 put #52 in the milestone, and parent D9 assumed a temp extraction of the base tree.

**Deciding factor:** hassette's PR gate is replaced with no per-repo state to maintain.

| | A: Two trees: scan the merge-base and the head, then match | B: Committed baseline file: record findings once, subtract on later runs | C: Diff filter: report findings on lines the diff changed |
|---|---|---|---|
| State the repo keeps | None | A baseline file, regenerated as the backlog shrinks; stale entries pruned or failing the run | None |
| Gate on a PR's own changes against its merge-base | Yes | Only if the baseline on the base branch is current | Yes |
| Identity problem | Must match findings across trees (D5, D6) | Same matching, against the file's entries | None, but line overlap misjudges: reformatting an old finding flags it, and a one-line edit that creates a new duplicate elsewhere is missed. hassette dropped this proxy for exactly that. |
| HSL102 "grew past threshold" | Compare line counts at both commits | The baseline must store line counts | Can't express it |
| Cost per run | Two scans (the cache shares most work, D8) | One scan | One scan plus a diff |
| Peers | hassette's own scripts only | ESLint, basedpyright, elm-review | `riff` |

**Recommendation:** A. hassette's gate compares a PR to its merge-base and its backlog moves every week. A baseline file would need regenerating on every merge to `main`, and a diff filter is the proxy hassette already replaced because it misjudged both ways. A costs a second scan and has to solve matching, which B also has to solve.
**Pick B instead if** you want gating with no git history available (a tarball, a shallow CI clone) and accept maintaining a committed file. **Pick C instead if** speed matters more than catching a new duplicate created by a small edit.

A baseline generated rather than committed (in CI or a pre-push hook) still comes from a base scan, so it is A with the base result serialized. Saving the base scan in CI would need an artifact keyed by the merge-base SHA and cross-workflow download glue in each repo, and local hooks would still scan the base. The matcher compares the two scans plus the base tree's text, so a later `--baseline <file>` could supply the base side from a stored scan without changing A.
**Reversibility:** hard (B would replace the extraction machinery and the gate's whole contract)
**Ratified:** Chose two trees over a committed baseline file and a diff filter, so a PR is gated against its merge-base with no state kept in the repo, accepting a second scan per run; a generated `--baseline <file>` stays a possible later addition if CI time matters.

### D2: What is the head side?

**Situation:** Flaw 2 in the issue: hassette's local gates scan the working tree while diffing against `HEAD`, so uncommitted edits hide or add findings relative to the commit being pushed. The issue proposes `--head`, defaulting to `HEAD`. hassette's CI checks out the PR's head commit, so `HEAD` is already the head there. Its pre-push hooks run from the pushed branch.

**Deciding factor:** the result describes exactly the commit being pushed or tested, with the smallest CLI.

| | A: Always the `HEAD` commit, extracted from git objects; no `--head` flag | B: `--head <ref>` defaulting to `HEAD`, extracted from git objects | C: The working tree |
|---|---|---|---|
| Unaffected by uncommitted edits (an acceptance criterion) | Yes | Yes | No (flaw 2) |
| hassette CI and pre-push | Works: CI checks out the head SHA | Works | Wrong locally |
| CLI surface | One flag | Two flags | One flag |
| Gating a ref other than the checkout | Check it out first | Yes | No |
| Extractions per run | Two | Two | One |

**Recommendation:** A. Neither consumer gates a ref other than the checkout, and `--head` can be added later without breaking anything.
**Pick B instead if** you expect to gate refs you haven't checked out, such as a hook that pushes a branch other than the current one. **Pick C instead if** you want local runs to include uncommitted work and accept flaw 2.
**Reversibility:** easy (B is additive)
**Ratified:** Chose the `HEAD` commit extracted from git objects with no `--head` flag over a `--head` option and the working tree, so the result describes exactly the checked-out commit with one new flag, accepting that gating another ref means checking it out first.

### D3: What is the base commit?

**Situation:** hassette resolves `merge-base(base, head)` rather than using the base ref directly. The base branch's tip includes other PRs merged since this branch forked, and their findings would otherwise look removed (harmless) or their fixes would make this branch's untouched debt look new (harmful).

**Deciding factor:** only the head's own commits can introduce findings.

| | A: `git merge-base <ref> HEAD` | B: The ref itself |
|---|---|---|
| A finding fixed on `main` after the fork, still present on the branch | Old (it was there at the fork) | New: the branch is blamed for code it didn't touch |
| Matches hassette's scripts | Yes | No |
| Shallow clone without the fork point | Loud error (posture) | Works if the ref's commit is fetched |

**Recommendation:** A, because B blames the branch for debt `main` fixed after the fork. The ref is resolved first with `git rev-parse --verify --end-of-options <ref>^{commit}`, which gives Posture's "ref doesn't resolve" error and keeps a value starting with `-` from reading as an option. Every later git call uses the resolved SHAs, and the output names the base commit compared (sketch challenge, Finding 17). Both scans always run, even when the head has no findings, so an error present only at the base is still reported (sketch challenge, Finding 13).
**Pick B instead if** you gate a branch already rebased onto the ref, where the two are the same, and want to skip the merge-base lookup.
**Reversibility:** easy
**Ratified:** Chose `git merge-base <ref> HEAD` over the ref itself, so only the head's own commits can introduce findings, accepting a loud failure when a shallow clone lacks the fork point, and (sketch challenge, Finding 14) that a scan error present only at the merge-base fails the gate, so a PR fixing it has to merge without the gate. At the sketch challenge (Finding 13), chose always running both scans over skipping the base when the head is clean, accepting a second scan on clean runs.

### D4: Which config governs the two runs?

**Situation:** Each commit has its own `pyproject.toml` or `house-lint.toml`. A PR may change the config: enable a rule, lower `max_lines`, or add an exclude.

**Deciding factor:** a PR is judged on the code it changed, not on whether it changed the config.

| | A: The head tree's config for both runs | B: Each tree's own config |
|---|---|---|
| PR enables a rule that has a backlog | Backlog is old; only new instances fail | Every backlog finding is new and the PR fails |
| PR lowers HSL102's `max_lines` | Files that didn't grow pass (ratchet, D7) | Every file newly over the limit fails |
| PR adds an exclude | Both runs exclude it | Base still scans the excluded files: harmless |
| An explicit `--config` path | Its repo-relative path is loaded from the HEAD extraction and used for both runs | n/a |
| Peers | SonarQube's new-code period uses the current quality profile (background knowledge, not in the brief) | unknown |

**Recommendation:** A. Under B, adopting a rule in a repo with a backlog fails the very PR that adopts it, which is when a new-code gate matters most.

**Where the head config is read (sketch challenge, Finding 3).** `cli.check` resolves the root and config in the working tree only to learn their repo-relative paths. `new_code` then loads the config from the same path inside the HEAD extraction, with that extraction's mapped root passed explicitly, so `resolve_project`'s upward walk never runs inside an extraction. With no config at that path in HEAD, both runs use the default config, as a normal run with no config does. An explicit `--config` outside the repository has no repo-relative path, so `--base` rejects it as a usage error (exit 2). An uncommitted config edit never affects the gate.
**Pick B instead if** you want config changes themselves gated, so a PR that tightens a threshold has to fix what it newly flags.
**Reversibility:** easy
**Ratified:** Chose the head tree's config for both runs over each tree's own, so a PR is judged on the code it changed and adopting a rule doesn't fail on its backlog, accepting that config changes themselves are never gated. At the sketch challenge (Finding 3), chose loading that config from the HEAD extraction with an explicit root, defaulting when HEAD has none, over failing on a config missing from HEAD and using the working tree's config, accepting that an uncommitted new config is silently ignored.

**Ratified (ship-time challenge, Finding 8):** Chose a usage error (exit 2) when an explicit `--config` has no file at its path in HEAD over falling back to the built-in config, so a config the user named never silently gives way to defaults, accepting that a CI step passing a config only some branches carry must drop the flag on the others. A discovered config keeps the built-in default.

**Ratified (comb, run 1):** Chose rejecting an explicit `--config` outside the repository as a usage error (exit 2) over silently running with the default config, so the gate never runs under a config the user didn't ask for, accepting that an out-of-repo config can't be used with `--base`.

### D5: How are ordinary findings matched across the two trees?

**Situation:** This covers every rule except HSL102 (D7) and HSL104 (D6): HSL001-HSL004, HSL101, HSL103, and HSL900. Their messages are often generic. HSL002's is `import inside function body`, HSL004's is `constant defined after the first class or function`. So a message alone can't tell two findings apart. Parent D16 settled that identity is per finding and compared as a multiset: with 3 matching findings at base and 4 at head, one is new. Line numbers can't be part of identity, because they shift when unrelated code moves (the Ruff #1149 anti-pattern in the brief).

**Deciding factor:** a new instance is caught even in a file or rule with a backlog, while moving or reformatting old code is not reported.

| | A: Rule, message, and the finding's span text with all whitespace removed. No path. Ties prefer the same file. | B: Rule, path, and message, counted per file (elm-review or ESLint bulk-suppression style) | C: A with the rename-mapped path added to the key |
|---|---|---|---|
| New lazy `import json` in a file that already has one lazy import | New | New (the file's count rises) | New |
| Swap: fix one lazy import and add one with different text (`import os` for `import json`) | New | Passes in the same file (count unchanged) | New |
| Swap with identical text: fix `import json` in `f()`, add `import json` in `g()` or another file | Passes: the debt moved | Passes in the same file, new in another | Passes in the same file, new in another |
| Split an oversized file, moving its old findings to a new module | Old | Every moved finding is new | Every moved finding is new |
| Reindent or rewrap a flagged statement | Old | Old | Old |
| Edit inside a flagged span (for example, a misplaced constant's value) | New: touching it re-reports it | Old | New |
| Moves a finding to another file within the same PR | Old | New | New |
| Needs the source text of each finding | Yes, loaded from the extraction after the scan (see Span text below) | No | Yes |
| Peers | GitHub code scanning hashes the primary line's content (background knowledge) | elm-review, ESLint bulk suppressions | unknown |

**Pairing order (every rule but HSL102).** Pairing is one-to-one and runs in passes per rule, each pass over all still-unpaired head and base findings in (path, line) order:
1. Equal key, same file. The base path comes through the rename map, which every run computes because HSL102 needs it (D7).
2. Equal key, any file.
3. HSL104 only: containment (D6), same file.
4. HSL104 only: containment, any file. Ties go to the smallest text-length difference, then path.

**Span text.** After both scans, `new_code` loads each file that has a finding from its run's extraction through `source.load_file`, so root containment and the size bound apply as in the scan. It decodes the bytes the way that file's language layer does: Python through `python_source`'s decoding, including the encoding cookie and BOM, and other languages as UTF-8. It then slices the finding's span by house-lint's 1-based line and column model and removes all whitespace. A file that fails to load or decode keys its findings on message alone. These are the only reads outside the scans. The extractions are private and immutable, so a second read can't see different bytes, which is the concern behind the read-once rule (CLAUDE.md Gotchas).

Head findings left unpaired are new. Because exact matches pair before containment runs, an unchanged copy always pairs with itself, so a short new copy can't take a long old one and leave an untouched file reported in its place. A finding with no location keys on its message alone. HSL900 is matched like any other rule. Matching hides no error: an error from either run is always reported.

**Recommendation:** A. B passes a swap, and both B and C punish exactly the file splits HSL102 asks for. A's cost is that editing inside a flagged span reports it again. That is a defensible "you touched it" rule, and it only applies to code the PR changed.
**Pick B instead if** you'd rather not re-report touched findings and can accept swaps passing and file splits failing. **Pick C instead if** you want a finding that moves to another file reported as new.
**Reversibility:** easy (the match is internal; no key is stored)
**Ratified:** Chose rule, message, and whitespace-free span text with no path over per-file counts and a path-keyed text key, so a new instance is caught even beside a backlog while moves, file splits, and reformatting pass, accepting that editing inside a flagged span reports it again.

**Ratified (ship-time challenge, Finding 2):** Chose first-come pairing within a file over a difflib line map that maps each head line to its base line, so the matchers keep three inputs and no per-file diff or read cache, accepting that among identical findings in one file the count is exact but the reported one may be an older copy, since no peer tracks which identical occurrence a branch added.

**Ratified (comb, run 1):** Chose reloading each flagged file from its run's extraction through `load_file` and the language layer's decoding over capturing span text inside the scan, so the pipeline, `Finding`, and the cache stay unchanged for normal runs, accepting a second read of the immutable extractions and a message-only key for a file that fails to load or decode.

### D6: How are duplicate-code occurrences (HSL104) matched?

**Situation:** Parent D16 set the goal that an occurrence's identity changes only when its own text changes. It left open how to keep that under span merging. `rules/duplicate_code.py` merges a file's overlapping jscpd fragments into one occurrence (`merge_fragments`), so an occurrence's span depends on its siblings:
- A new fourth copy that matches an existing copy over lines 1-7, where the old copies matched over 1-5, grows that existing occurrence to 1-7.
- jscpd reports each later copy against one earlier copy only (spec 013's probe with three identical files reported `a–b` and `a–c`, never `b–c`). Which earlier copy depends on the order jscpd reads files. Renaming a file can change that order, so copy C gets matched against B over 1-6 instead of A over 1-5. B's and C's merged spans change with no copy added.

A plain hash of each occurrence's text changes in both cases, so old occurrences are reported as new. In the rename case the gate fails with no new code. hassette's script compares per-file occurrence counts per cluster, which passes a new copy added to files that already hold copies (flaw 1).

**Deciding factor:** the gate fails exactly when a copy is added, never on a rename or a regrouping.

| | A: Containment: a head occurrence matches an unpaired base HSL104 occurrence whose whitespace-free text contains it or is contained in it, one to one, after exact matches (D5's pairing order) | B: Equality on a hash of the occurrence's whitespace-free text (parent D16's starting point) | C: Per-file counts per cluster (hassette's script) |
|---|---|---|---|
| New fourth copy of a three-copy block | The fourth is new; the three old ones match | The fourth is new, plus any old occurrence whose span grew | Missed when the new copy lands in a file that already holds one (flaw 1) |
| Renaming a file reorders jscpd's pairs and regroups spans | Passes | Fails with no new copy | Passes |
| Moving a copy to another file | Old | Old | New |
| Reformatting a copy | Old | Old | Old |
| An old occurrence the PR extends with new lines | Old (it contains its base text) | New | Old |
| Pair grows to three copies at `min_occurrences = 3` | All three new (none reported at base) | Same | Same |
| Complexity | Two extra passes after D5's exact passes | Same multiset as D5 | Cluster bookkeeping |

**Recommendation:** A. It is the "correspondence that keeps the prior span" that D16 named as one way out, and it's the only option that passes a pure rename. It supersedes D16's requirement that duplicate occurrences key on a hash of their own text. It keeps D16's per-occurrence identity, path-independence, and count comparison, which becomes the one-to-one pairing. The case it lets through: an old occurrence extended with new copied lines stays old. That is debt growing in place, and HSL104 can't separate it from editing the copy.
**Pick B instead if** you'd rather keep one matching rule for every rule and accept a false failure when a rename regroups jscpd's pairs. **Pick C instead if** you want hassette's exact current behaviour, flaw 1 included.
**Reversibility:** easy
**Ratified:** Chose containment matching over an own-text hash and per-file cluster counts, so the gate fails exactly when a copy is added and passes renames and regroupings, superseding parent D16's own-text-hash requirement and accepting that an old copy extended with more copied lines stays old. At the sketch challenge (Finding 1), the pairing order was fixed as D5's passes, exact before containment, keeping the run-wide rename map and D10's catalog field, so the finding reported as new is the copy the PR added.

### D7: Where do HSL102's line counts come from?

**Situation:** The issue fixes the semantics: an HSL102 finding is new only if the file has more lines than it did at base. A file added with too many lines counts as growing from 0. Renames are followed, and shrinking or an unchanged file passes. hassette's script recovers the head count by parsing the message `"N lines (threshold: M)"`, and the base count with `git show`. The base file needn't have had a finding, since it may have been under the threshold.

**Deciding factor:** one source of truth for the base file's state, with no file read for the line count and no new field on every finding for one rule.

| | A: The base scan decides. When both scans flag the file, `rules/file_length.py` reads both counts back from the message it formats | B: The base scan decides. When both flag it, reload both files through `load_file` and `parse_python` | C: Count lines from both extractions directly; 0 when the base path is missing | D: A structured line count on `Finding` |
|---|---|---|---|---|
| Extra file reads | None | Two per file both scans flag | One per head HSL102 finding, outside `load_file` | None |
| Base file unreadable, excluded, or suppressed at base | No base finding, so new (stated) | Same | Inferred as 0, which can disagree with the base scan | Same as A |
| Symlink containment and decoding | n/a | Vetted path, the detector's decoder | Unvetted path, a second decoder | n/a |
| Message parsing | Yes, but the format and its parser live in one module and round-trip | None | None | None |
| `Finding` changes | None | None | None | A field for one rule |

**Recommendation:** A. HSL102 matches on the rename-mapped path (D5's pairing order doesn't apply to it). A head finding with no base HSL102 finding at that path is new: under D4 both runs use the head config, so the base file was at or under the threshold, or unreadable, excluded, or suppressed there. When both have a finding, it is new only if the head count is larger. `file_length.py` formats `"N lines (threshold: M)"` and parses it back through one helper, so the two can't drift. The rename map comes from `git diff -z --name-status -M -l0 <base> <head>` through the existing `discovery.run_git`. `-l0` lifts the rename limit, so git never prints the warning that `run_git`'s strict stderr rule would turn into a failure on a large PR. `-z` and `-M` pin the output against user config. The rename map's paths are repo-relative and are translated to root-relative before HSL102 looks up a base finding. A rename below git's similarity threshold reads as a delete plus an add, so the added file has no base finding and its HSL102 finding is new (sketch challenge, Finding 5).
**Pick B instead if** you'd rather never derive a count from message text, even house-lint's own. **Pick C instead if** you want the ratchet to hold for a base file suppressed or unreadable at base. **Pick D instead if** JSON consumers should read the count without parsing.
**Reversibility:** easy
**Ratified:** Chose letting the base scan decide, with counts read back from HSL102's own message when both scans flag the file, over reloading files, counting from the extractions, and a field on `Finding`, so the base state has one source of truth and no file is read for the count, accepting a message round-trip inside `file_length.py` and that a base file suppressed or unreadable at base makes its head finding new. The rename map's invocation is pinned through the strict `run_git` rather than a warning-tolerant path, accepting slower rename detection on huge PRs and that a rename below git's similarity threshold counts as a new file. Revised at the sketch challenge (Finding 2); originally ratified as counting from the extractions.

**Ratified (comb, run 1):** Kept letting the base scan decide, with the deciding factor narrowed to no file read for the line count (D5's span-text reads are separate) and the rename map translated to root-relative paths before lookup, so subdirectory roots match, accepting nothing new.

### D8: How do the two runs use the cache?

**Situation:** `run_scan` takes `cache_dir: Path | None` as the user gave it. `None` means house-lint's default, `default_cache_base(root)`, which lives under the scanned root. `run_scan` derives `self_ignore = cache_dir is None` and passes it to `RunCache`, and that one flag also decides whether the base gets the symlink check (`default_cache_base_is_safe`). Run on an extraction, the default would be a cache inside the temp directory, deleted after every run. Cache entries are keyed on content and config only, not path (`cache.hash_effective_config`, `read_cached_result` takes the scanned path), so both runs can safely share one cache. Spec 006 D3 left this to #52. The issue's comment asks that the flag be split rather than passed by whichever behaviour is wanted. Measured on hassette (prior-art brief): 7.2-7.7s cold, 0.85-0.90s warm.

**Deciding factor:** a pre-push `--base` run is fast after the first, with every location still vetted the same way.

| | A: Both runs use the project's own cache: the real root's default, or `--cache-dir`. `run_scan` takes a resolved location saying whether it's house-lint's managed default (self-ignore marker and symlink check) or a user-given path (neither). | B: No cache under `--base`: neither read nor write | C: Head shares the project cache; base runs uncached |
|---|---|---|---|
| Repeat pre-push on hassette | Both runs mostly warm | Two cold scans, about 15s | Head warm, base cold |
| CI (fresh runner) | Cold, as today | Cold | Cold |
| Symlink check where it applies | Yes: the real root's default is checked | n/a | Yes for head |
| The `self_ignore` conflation | Replaced by an explicit location type | Still there, plus a new "no writes" path | Both |
| New code | The location type | A disable-writes path in `RunCache` | Both |

**Recommendation:** A. The location type is the split the issue asked for, and it makes the base run's cache as safe as a normal run's.
**Pick B instead if** you want `--base` to leave no trace in the project's cache. **Pick C instead if** base entries crowding the cache turns out to matter. It's pruned by namespace only, so entries for old merge-bases stay until the next version bump.
**Reversibility:** easy
**Ratified:** Chose both runs sharing the project's own cache through an explicit location type over no cache and a head-only cache, so repeat `--base` runs are warm and every location is vetted as in a normal run, accepting base-tree entries in the cache until the next namespace prune. At the sketch challenge (Finding 11), the type stops at `run_scan` and `RunCache`, over threading it through `cache.py` or using two loose parameters, since `cache.py`'s functions already take `self_ignore` explicitly.

### D9: What happens to explicit paths with `--base`?

**Situation:** `check` accepts paths. hassette's gates run with `pass_filenames: false`, scanning the whole tree. A pre-push hook that passes filenames would hand `--base` the changed files.

**Deciding factor:** smallest correct surface.

**Recommendation:** reject explicit paths with `--base` as a usage error (exit 2) that says to set `pass_filenames: false`. Also reject `--no-git`, since `--base` needs git. Comparing the whole project already reports only what changed.
**Pick accepting paths instead if** a consumer wants to gate a subset of files. Paths would then filter the reported new findings, the way `widen_project_inputs` limits project-rule reporting.
**Reversibility:** easy (accepting them later is additive)
**Ratified:** Chose rejecting explicit paths and `--no-git` with `--base` as a usage error over accepting paths as a filter, so the gate has the smallest correct surface, accepting that hooks must set `pass_filenames: false`.

### D10: Where does each rule's matching kind live?

**Situation:** Three matching kinds exist: the generic key match (D5), containment (D6), and the line-count ratchet (D7). The repo's convention is that `rule_catalog.py` is the single source of truth per rule (CLAUDE.md, Adding a rule).

**Deciding factor:** adding a rule never needs an edit outside its module and its catalog row.

**Recommendation:** add an optional `base_match` field to the catalog's `FileRule` and `ProjectRule` rows, holding a whole matcher: a callable taking the rule's head findings, its base findings, the span text as a mapping from `Finding` (a frozen dataclass, so hashable) to its whitespace-free text, with findings keyed on message alone absent from it (D5), and the rename map as a mapping from root-relative head path to root-relative base path, holding only renamed files, and returning the head findings that are new. The exact passes (D5's passes 1-2) and the default matcher live in a leaf module, `src/house_lint/matching.py`, which imports only result types, so both `new_code.py` and rule modules import it without a cycle (`new_code` imports `rule_catalog`, which imports every rule module). The default matcher runs passes 1-2. `duplicate_code.py` supplies a matcher that calls `matching`'s exact passes, then its containment passes 3-4. `file_length.py` supplies the ratchet (D7). `new_code.py` looks the field up and never names a rule ID.
**Pick a table in `new_code.py` keyed by rule ID instead if** you'd rather keep the catalog free of `--base` concerns, accepting a second place to update when a rule needs special matching.
**Reversibility:** easy
**Ratified:** Chose an optional `base_match` field on catalog rows over a rule-ID table in `new_code.py`, so a rule's matching lives in its module and its row, accepting one more catalog field that only `--base` reads.

**Ratified (comb, run 1):** Chose the field holding a whole matcher, with the exact passes and default matcher in a leaf `matching.py`, over `duplicate_code.py` importing `new_code.py` and over passing the exact-pass function in as a parameter, so imports run one way (gate → catalog → rules → matching) and the exact passes have one owner, accepting one more small module.

### D11: How is each commit's tree extracted?

**Situation:** hassette uses `git archive <ref> | tar -x`. Probed this session: `git archive` leaves out paths marked `export-ignore` in `.gitattributes`, so a repo that marks `tests/` that way would silently drop them from both scans. Reading the commit into a temporary index file (`GIT_INDEX_FILE=<tmp> git read-tree <commit>`) and then `git checkout-index -a --prefix=<dir>/` extracted every tracked file, symlinks included. It left the real index and working tree untouched (the probe's dirty `a.py` stayed dirty and unstaged). `git worktree add` registers metadata in the repo and runs `post-checkout` hooks.

**Deciding factor:** every tracked file, with no side effects on the user's repository.

**Recommendation:** temporary index plus `checkout-index`, into a `tempfile.TemporaryDirectory` removed on every exit path. Both calls go through `discovery.run_git`, which gains an optional `env` overlay so these two calls can set `GIT_INDEX_FILE` while every other call keeps the inherited environment. `run_git`'s strict stderr rule stays: a warning from extraction fails the run, per Posture. Submodules come out as empty directories, as in a CI checkout without submodules. Smudge filters (Git LFS) run as they would on a checkout.
**Pick `git archive` instead if** you want export-ignored paths left out of gating on purpose.
**Reversibility:** easy
**Ratified:** Chose a temporary index plus `checkout-index` over `git archive`, so every tracked file is extracted with no side effects on the user's repository, accepting that submodules come out empty and smudge filters run. Also accepted at the sketch challenge: extraction assumes a full, non-sparse clone and an ordinary hook environment, with no completeness check (Finding 15), and a killed run can leave its extractions in the system temp directory (Finding 16).

**Ratified (comb, run 1):** Chose routing both extraction calls through `run_git` with an optional `env` overlay and its strict stderr rule over tolerating extraction stderr or a separate subprocess helper, so git has one call path and one failure rule, accepting that a smudge filter writing to stderr (Git LFS) fails the gate.

## Assumed

- Both trees are scanned with the existing pipeline, unchanged: `pipeline.run_scan` runs discovery, the file phase, project-rule widening, and the project phase, and returns a `ScanResult`. Pragmas are applied within each run, so a PR that deletes a pragma exposes its finding as new. Evidence: `src/house_lint/pipeline.py:187`; parent D2.
- Cache entries are keyed on content and config only, and replayed under the path being scanned, so content scanned in an extraction hits entries written by a working-tree run. Evidence: `src/house_lint/cache.py:150` (`hash_effective_config`, filename only), `cache.py:265` (`read_cached_result` takes `relative_path`), `cache.py:322`.
- Findings carry rule ID, path, 1-based span, message, and `related` locations, and no identity or group id. Evidence: `src/house_lint/results.py` (`Finding`); spec 011 D3 dropped the group id.
- An HSL104 occurrence is a merged span of overlapping jscpd fragments in one file, and jscpd reports each later copy against one earlier copy. Evidence: `src/house_lint/rules/duplicate_code.py` (`clusters`, `merge_fragments`); spec 013's probe of `a.py`, `b.py`, `c.py`.
- HSL102's line count is `len(source.text.splitlines())` on the decoded file. Evidence: `src/house_lint/rules/file_length.py:21` (`detect`).
- The issue's output contract: exit codes keep their meanings, computed from the new findings and every error, and JSON gains the base ref and the count of pre-existing findings left out. Evidence: issue #52, Proposal.
- hassette's CI fetches full history (`fetch-depth: 0`) and checks out the PR's head SHA, so `HEAD` is the head and the merge-base is available. Evidence: hassette `.github/workflows/lint.yml`, `duplicate-code` and `file-sizes` jobs.
- An extraction contains no `.git`, so discovery inside it would plain-walk anyway. `use_git=False` makes that explicit. Evidence: `src/house_lint/discovery.py:140` (`inside_repository`).
- jscpd runs on the real file paths it is given (parent D9), here the extraction's paths, which nothing edits mid-run. Evidence: `src/house_lint/jscpd.py:108` (`find_clones`).

## Build

- [x] Implementation and tests committed
- [x] Docs (README new-code gating section, docs/rules.md HSL102/HSL104 notes, CLAUDE.md layout)
- [x] Follow-up issues filed in hassette and hass-hassette (NodeJSmith/hassette#3997, NodeJSmith/hass-hassette#60)
- [x] Ship-time challenge (17 findings; 6 rare ones accepted as KI-001 to KI-006 in known-issues.md)

**Calls made during the build:**
- Matchers take separate `head_text` and `base_text` mappings, not D10's single one: `Finding` is a frozen dataclass compared by value, so a head and a base finding with the same rule, path, span, and message but different code would collide in one mapping.
- HSL104's exact passes key on span text alone, not D5's message-and-text key: its message counts places, so adding a copy changes every occurrence's message, and keying on it would push unchanged copies into containment and lose the exact-first ordering D6 relies on.
- Without `--config`, the head commit's config comes from `resolve_project(root=<mapped root>)` in the HEAD extraction (the root's own config sources, no upward walk), rather than reusing the working tree's discovered path. A config committed at HEAD but deleted or renamed in the working tree still governs, per D4's "an uncommitted config edit never affects the gate". An explicit `--config` is read from its repository path in HEAD (a usage error when absent there, per the ship-time challenge, Finding 8).
- D4's out-of-repository `--config` check is `inside_repository(config.parent)`, run before any git call. `resolve_project` already rejects a config outside an explicit `--root`, so this check is only reached when `--config` sets the root.
- Span text is sliced by UTF-8 byte columns. Line findings (`analysis.candidate_for_line`, used by HSL001, HSL101, and HSL103) and HSL900's diagnostics reported character columns, which cut a key short on a non-ASCII line, so a new finding differing only in the cut-off tail paired as old. Per the ship-time challenge (Finding 7), every finding now reports UTF-8 byte columns, as AST and jscpd findings already did, through `PythonSource.byte_column`. This changes the end column those rules report on non-ASCII lines.
- An error's run is named by prefixing its message (`base run:` or `head run:`) rather than adding a field to `LintError`, keeping the error schema unchanged.
- A root directory absent from a commit is created empty in that commit's extraction, so the commit scans as zero files rather than failing on a missing root.
- `run_git` gained a `hint` parameter beside `env`, so `--base` failures end with fetch-history advice instead of "pass --no-git", which `--base` rejects.
- The CLI's `shadowed` config note under `--base` still comes from the working tree's resolution: `run_gate` doesn't return the HEAD resolution's shadowed list, since the note is informational and the two differ only when the working tree's config sources differ from HEAD's.
- The gate's reported config path is built from the project's own paths (the explicit `--config`, or the root plus the discovered file's name), not by un-mapping the resolved path from the extraction, which a symlinked config could take outside it.
- The release containing `--base` waits for #91 (HSL900 skips pragmas naming rules excluded by command-line selection), filed from the ship-time challenge (Finding 5): without it, a per-rule `--select <rule> --base` gate fails on any newly added pragma for another rule, which blocks hassette's migration.
- The PR ships as `feat!` with a `BREAKING CHANGE:` footer: the byte-column change moves `column` and `end_column` on non-ASCII lines for HSL001, HSL101, HSL103, and HSL900, which is a JSON output change. With `bump-minor-pre-major`, the release is 0.6.0 either way.
