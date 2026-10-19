# Research Brief: Which duplicate detector house-lint should wrap, and the engine work to host it (#39 follow-up)

---
proposal: "Pick a proven external duplicate-code detector that covers Python and TypeScript, and sketch the minimum engine changes house-lint needs to host a cross-file, tool-wrapping rule (#39, reused by #53)."
date: 2026-10-18
status: Draft
flexibility: Leaning
motivation: "hassette's PMD CPD gate is noisy, misses renamed copies, and needs a JVM. house-lint's charter now includes cross-file checks and first-class TypeScript, and the owner prefers wrapping a proven tool over hand-building a detector."
constraints: "Cross-file checks are in scope. Single-file analysis, read-once, and the per-file cache are porting-era details, not principles (worktree CLAUDE.md:18, :67, :75). TypeScript is first-class. Prefer a proven tool. Do not edit the repo."
non-goals: "SARIF output from house-lint (spec 001). Hand-building a full detector unless no tool is good enough."
depth: normal
---

**Initiated by**: a follow-up to `research.md` in this directory. The owner's corrected premises (above) override that brief's Option A, which was a native Python-only detector.

## Bottom line

**Wrap jscpd 5.4.1 (the Rust engine), pinned, and installed from PyPI.** Of the tools probed, it is the only one that clears every requirement:
- Python and TypeScript from a single tool.
- Official PyPI wheels, so `uvx` users need no Node and no JVM.
- JSON output with line and column spans for each occurrence.
- An explicit file list, passed through a config file.
- Configurable thresholds.
- Optional Type-2 and Type-3 matching that works in both languages.
- About 3-4s on all of hassette.

PMD CPD matches only exact copies in TypeScript as well as Python, so it has no remaining advantage beyond hassette's familiarity with it.

What the evidence supports, and what it does not:

- **Probed: jscpd's default mode matches PMD exactly** on every corpus case: exact copies match, renamed and literal-changed copies do not, and an inserted or deleted statement splits the match into two parts. The gains are all opt-in:
  - `--max-gap-lines` merges the split halves into one Type-3 match.
  - `--similarity` matches renamed and literal-changed functions through their syntax tree.
  - `--ignore-identifiers --ignore-literals` gives Type-2 matching.
  - PMD's equivalent flags are accepted but change nothing for Python or TypeScript.
- **Probed on hassette: blind identifier normalization is too noisy to turn on by default.** It roughly quadruples the clusters, from 258 to 1,076, and the added ones are mostly test functions with the same shape. jscpd 5.4.1's `--ignore-identifiers` is blind. A *consistent* mode is documented on jscpd's master branch, but the 5.4.1 CLI rejects `--ignore-identifiers=consistent`.
- **Probed on hassette: at default thresholds, about two-thirds of sampled clusters are real duplication.** That is 10 of 15 hand-classified clusters. 3 are deliberate boilerplate and 2 are signature-only. At jscpd's default 50 tokens, it flags only 19% of the 189 clusters hassette's gate currently reports. Most of the misses are 5-8 line test fragments under 50 tokens. At 20 tokens it flags 83%. This is a threshold choice for the sketch, not a capability gap.
- **Required, probed: house-lint must pass `--mode weak`.** In jscpd's default `mild` mode, a Python comment inside a copied block splits the match, and a house-lint pragma is a comment. TypeScript comments never become tokens.
- **Risk:** jscpd v5 has one maintainer and moves fast: 23 releases since June 2026, and the meaning of `--ignore-identifiers` is about to change. Pin an exact version.

The engine work is the larger cost. It takes a project-rule phase, language-tagged discovery, a split of suppression handling into two phases, and a subprocess runner. (This brief also proposed a project-level cache entry; spec 005 rejects it in D11.) #53 reuses all of it, and jscpd also ships a `--dead-code` mode for Python, JS and TS.

## Q1. Tool comparison, Python and TypeScript

### Probe corpus

The corpus is committed under `probe/corpus/pairs/{py,ts}/<case>/{a,b}.*`, built by `probe/scripts/build_pairs.sh`. Each case is one pair of files: a 15-line Python function or a 19-line TypeScript function.

| Case | What b changes |
|---|---|
| `exact` | Nothing |
| `renamed` | Function name and every local renamed consistently |
| `literal` | Numbers and strings changed |
| `insert` | One statement added in the middle |
| `delete` | One `if` statement removed from the middle |
| `sigfp` | Two tests with one identical long parameter list on a single line, and different bodies |
| `sigml` | The same, but the signature is formatted one parameter per line, as ruff/prettier output it. This matches hassette's 30 "pytest test function signature" markers. |

For `sigfp` and `sigml` the ideal result is no match.

### jscpd 5.4.1 and PMD CPD 7.26.0 (probed)

All runs used `--min-tokens 30`, and jscpd also used `--min-lines 3`. Scripts are `probe/scripts/probe_jscpd.sh` and `probe/scripts/probe_cpd.sh`, with outputs under `probe/results/` (regenerated by the scripts; not committed).

Cell values:
- **FULL**: the whole function matched.
- **split**: two partial matches around the change.
- A line range: the only part that matched.
- **-**: no match.

| Case | PMD default | PMD `--ignore-identifiers --ignore-literals` | jscpd default | jscpd `--ignore-identifiers --ignore-literals` | jscpd `--max-gap-lines 2` | jscpd `--similarity` |
|---|---|---|---|---|---|---|
| py exact | FULL | FULL | FULL | FULL | FULL | FULL |
| py renamed | - | - | - | **FULL** | - | **FULL** |
| py literal | - | - | - | **FULL** | - | **FULL** |
| py insert | split | split | split | split | **FULL** (0.91) | split, plus FULL similar |
| py delete | split | split | split | split | **FULL** | split |
| py sigfp | - | - | - | - | - | - |
| py sigml | - | - | - | **FP 1-11** | - | - |
| ts exact | FULL | FULL | FULL | FULL | FULL | FULL |
| ts renamed | - | - | - | 1-16 of 19 | - | **FULL** |
| ts literal | 1-5 | 1-5 | 1-5 | **FULL** | 1-5 | **FULL** |
| ts insert | split | split | split | split | **FULL** | split, plus FULL similar |
| ts delete | split | split | split | split | split (a 3-line gap is more than 2) | **FULL** similar |
| ts sigfp | - | - | - | - | - | - |
| ts sigml | - | - | - | - | - | - |

**Threshold sensitivity on the signature cases.** Signature false positives depend on the threshold, not the tool:

| Threshold | PMD | jscpd |
|---|---|---|
| 20 tokens (hassette's setting) | False positive on `sigfp` and `sigml` in both languages | False positive on `sigml` in both languages |
| 50 tokens (jscpd default) | — | No match in any mode |

`sigfp` sits on one line, so jscpd's `--min-lines` filters it out.

Further facts, all probed:
- **jscpd 5.4.1's identifier normalization is blind.**
  - Its help reads: "Treat all identifiers as equal".
  - A probe with a deliberately inconsistent renaming still matched as `renamed` (`corpus/blind/`).
  - `--ignore-identifiers=consistent` errors: "unexpected value 'consistent'".
  - jscpd's master docs say the bare flag becomes consistent, "as PMD CPD does and jscpd did up to 5.4". That describes an unreleased change. As of 2026-10-18 the latest release on npm, crates.io, PyPI and GitHub is 5.4.1 (2026-10-09).
- **Comments split Python matches in the default mode.**
  - A pragma comment inside a copied Python block splits it in `--mode mild`, the default. `--mode weak` restores the full match.
  - `jscpd:ignore-start` / `jscpd:ignore-end` markers are still honoured under `weak`, in both languages (`probe/scripts/probe_comment.sh`, `probe_supp*.sh`).
  - jscpd's ignore markers remove the marked tokens from matching, the same as PMD's `CPD-OFF`. Marking one side stops that region matching. The unmarked flanks still match.
- **PMD's TypeScript matching is exact only.** The same is true of Python, per the prior brief. Its ignore flags are accepted for `--language typescript` but change nothing. That contradicts session `564fb908`'s claim that TypeScript normalizes.

### Other tools (probed by a worker subagent; outputs not retained)

| Tool | Languages | Renamed / literal | Insert / delete | Output with spans | Runtime and install | Verdict |
|---|---|---|---|---|---|---|
| lizard 1.24.1 `-Eduplicate` | py, ts | Matches, but cuts about 31 tokens off every match's end | Split | Text only | Pure Python | Truncated spans and a threshold hard-coded at 70 tokens in the CLI. No. |
| pylint 4.1.3 `symilar` | py | Neither | Split | Text | Pure Python, GPL | Python only, line-based. No. |
| arid 2.2.4 | py | Neither (by design) | Split | JSON | Rust wheel on PyPI | Python only. No. |
| qlty 0.657.0 `smells` | py, ts | Both FULL | No match | SARIF, columns always 0 | Rust binary from GitHub releases only. Needs a git repo plus `qlty init`. BSL 1.1 licence. | Licence and setup rule it out. |
| similarity-ts/py 0.5.0 | py, ts | FULL, but only with `--no-size-penalty` | FULL | Text | `cargo install` only | Python is within-file only. Defaults miss even exact copies. No. |
| treepeat 0.9.0 | py, ts | FULL at `-s 50` | FULL at `-s 50` | SARIF | Pure Python plus tree-sitter, PyPI | Takes a single PATH, not a file list. 18.9s and 835MB RSS on hassette `src/` alone (357 files), probed. 87 stars. No. |
| dupehound 0.1.2 | py, ts | Both FULL | ts insert only | JSON, whole functions | `cargo install` only | Function-granular, young. No. |

Not evaluated: polydup. Its last release is 2026-01 and crates.io shows 279 downloads.

### Runtime and the `uvx house-lint` install story

| | jscpd 5.4.1 | PMD CPD 7.26.0 |
|---|---|---|
| Runtime | Self-contained Rust binary, 46MB | JVM (Java 21 in hassette) plus about a 50MB distribution |
| PyPI | **Yes, official wheels** from the author, same day as the npm release: macOS x64/arm64, manylinux_2_34 x64/aarch64, musllinux_1_2 x64/aarch64, Windows x64/arm64. No sdist. Probed: `uv pip install jscpd==5.4.1` drops `bin/jscpd` and `bin/cpd` into the venv. The binary is identical to npm's (same size and RECORD sha). | No |
| Other channels | npm, crates.io, Homebrew, Nix | Download a zip and pin its sha |
| Licence | MIT | BSD-style |
| Maintenance | 6.4K stars, pushed 2026-10-18. The v5 Rust rewrite started June 2026 and has had 23 releases since. One maintainer. | Long-lived, multi-maintainer |

**Inference: a hard dependency would block some installs.** The Linux wheels need glibc ≥ 2.34 or musl, and there is no sdist. On hosts with older glibc (Debian 11, RHEL 8), adding `jscpd` as a hard dependency would make house-lint itself fail to install. This argues for an extra, `house-lint[duplicates]`, used as `uvx 'house-lint[duplicates]' check`, plus a clear error when the rule is enabled but the binary is missing.

## Q2. Real-repo run: hassette

**Scope.** hassette at `af158c9a` (2026-10-18):
- Python: 1,010 files, 211K lines, under `src/`, `tests/`, `client/` and `wire/`.
- TypeScript/TSX: 298 files, 39K lines, under `frontend/src`, excluding `*.d.ts`, `generated-types.ts`, `ws-types.ts` and `*.generated.ts`.
- `__pycache__`, `node_modules` and `snapshots` are pruned.
- jscpd received absolute paths through a config file, with `--no-gitignore --absolute`.
- PMD received the same lists through `--file-list`.

Scripts are `probe/scripts/hassette_*.sh` and outputs are in `probe/results/hassette/` (regenerated by the scripts; not committed). Clusters come from a union-find that merges overlapping fragments in the same file (`probe/scripts/clusters.py`).

| Run | Pairs | Clusters (2+ occurrences) | 3+ occurrences | Notes |
|---|---|---|---|---|
| jscpd default (50 tokens, 5 lines, mild) | 562 | 258 | 50 | src 295, tests 233, frontend 162 fragments |
| jscpd default, generated files excluded, `--mode weak` | 431 | 246 | 51 | The likely house-lint configuration |
| jscpd `--max-gap-lines 2` | 506 (49 similar) | 247 | 47 | Gap merging consolidates pairs |
| jscpd `--similarity` | 951 (389 similar) | 290 | 69 | 278 of the similar fragments are 7-line entity-model properties in `src/hassette/models/entities/`, which is noise |
| jscpd `--ignore-identifiers --ignore-literals` | 4,243 | 792 | 232 | Blind normalization |
| jscpd 30 tokens | 1,643 | 800 | 183 | |
| jscpd 20 tokens | 2,685 | 1,246 | 339 | |
| PMD Python, 20 tokens | 7,230 | 1,751 | 647 | Raw, no hassette filters |
| PMD TypeScript, 20 tokens | 583 | 110 | 44 | |
| PMD Python, 50 tokens | 343 | 163 | 26 | |
| PMD TypeScript, 50 tokens | 14 | 12 | 1 | |

**Generated files.** Five of hassette's generated sync facades are in scope: `api/sync.py`, `bus/sync.py`, `bus/sync_events.py`, `scheduler/sync.py` and `testing/_sync_facade.py`. They carry hassette's "Do not edit this file directly" banner, which its gate filters. Including them produced the two largest clusters (40 and 33 occurrences). house-lint would exclude them with `exclude` globs.

**Wall time.** The measurements are noisy: the machine is WSL2 with a load average of 7-13 on 12 threads throughout.
- jscpd over Python and TS together took 3.3-3.9s wall in the steadier runs (user 1.2s, sys 2.3s, 290MB RSS). The worker count made no difference. Single runs under load reached 12s.
- PMD took 1.2-1.5s per language in the same window, and 2.3-4.2s in an earlier, busier window.
- For scale, house-lint's own cold scan of hassette was 8.9s (prior brief).
- Neither tool is the bottleneck. A per-run cost of about 3-4s matters mostly for the pre-commit hook (see Q4).

### Hand classification

These are 15 clusters from the default-threshold runs. Clusters that involved generated files were dropped and replaced.

| # | Cluster | Class |
|---|---|---|
| 1 | `bus/bus.py` ×18: repeated `**opts` / `mode` / `backpressure` docstring paragraphs | Deliberate doc duplication (jscpd tokenizes docstrings and has no option to skip them) |
| 2 | `scheduler/scheduler.py` ×12: docstring paragraphs plus `self.schedule(func, trigger, name=…, …)` delegation | Designed delegation and boilerplate |
| 3 | `cache/{dummy,protocol,sync,wrapper}.py` ×7: `get` overloads plus expiry logic | Mixed. The expiry-check logic is real. |
| 4 | `app-detail.instances.test.tsx` ×10: `createAppSummary` + `setupApi` + `renderPage` setup | Real test duplication |
| 5 | `api/api.py:560-569` vs `:609-618`: the `call_service` / `_call_service` signature | **Signature-only false positive** |
| 6 | `test_app_lifecycle_service_reload.py` ×6: mock setup | Real test duplication |
| 7 | `table-card.test.tsx` ×3: the same render fixture | Real test duplication |
| 8 | `tests/unit/tools/test_check_*.py` ×3: `@guard_cases(CASES) def test_guard_behavior(...)` | Harness boilerplate |
| 9 | `log-detail-drawer.test.tsx` ×2: test arrange | Real, minor |
| 10 | `core/bus_service.py:565` vs `core/scheduler_dispatch.py:336`: `ExecutionRecord` built on predicate error | **Real src duplication** |
| 11 | `test_dispatch_unification.py` ×2: an identical SQL `INSERT INTO listeners` | Real test duplication |
| 12 | `test_app_manifest_retention.py` ×2: fixture signature plus 3 arrange lines | Mostly signature |
| 13 | `test_state_manager.py` ×2: `patch.object(...)` spy block | Real test duplication |
| 14 | `test_state_proxy.py` ×4: gated-snapshot arrange | Real test duplication |
| 15 | `unified-handler-row.test.tsx` ×2: Enter vs Space key tests | Real, a parametrize candidate |

Totals:
- 10 real, including #3. 2 of them are in src.
- 3 boilerplate or deliberate.
- 2 signature-driven.

The signature false positives that remain at 50 tokens come from long, multi-line typed signatures (#5). That is the same mechanism as hassette's 30 markers, just less frequent.

### Comparison with hassette's current gate (`cpd-plain.txt`, not committed, 189 clusters)

A gate cluster counts as "seen" if at least two of its occurrences overlap jscpd fragments (`probe/scripts/compare_gate.py`).

| jscpd run | Seen |
|---|---|
| Default (50 tokens) | 35 (19%) |
| `--mode weak` | 39 (21%) |
| 30 tokens | 115 (61%) |
| 20 tokens | 156 (83%) |
| Blind normalization | 112 (59%) |

What the default run misses:
- **Real, but under 50 tokens.** Most are short test arrange blocks of about 45 tokens, such as `test_logging_service.py:200-211` ×3.
- **Noise.** Mini app classes in `test_scheduler.py`.
- **Signature-only.** `apply_changes` in `core/app_handler.py` vs `core/app_lifecycle_changes.py`, which jscpd correctly skips.

The gate's 189 clusters are themselves dominated by short test fragments: 158 are in `tests/`. Choosing between 30 and 50 tokens means choosing how much test-arrange duplication the rule should flag.

## Q3. Machine-readable output and fit

| Need | jscpd 5.4.1 (probed unless noted) | PMD CPD 7.26.0 |
|---|---|---|
| Spans per occurrence | **JSON** `duplicates[]` with `firstFile` / `secondFile` `{name, start, end, startLoc{line,column}, endLoc{line,column}}`, plus `kind` (exact, renamed, similar), `tokens` and `fragment`. Output is pairwise, so house-lint must group pairs into clusters. | XML `<duplication>` with `<file path line endline column endcolumn>`. Groups of 2 or more, often overlapping. |
| Content fingerprint for #52 | **SARIF** `partialFingerprints["jscpdCloneHash/v1"]` per clone, with `relatedLocations`. The JSON report has no fingerprint. Per the docs, fingerprints differ between exact and normalized runs. Built-in `--baseline`, `--baseline-from-ref <ref>` and `--changed` overlap #52's goals. | None. hassette hashes `<codefragment>` itself. |
| Explicit file list | Positional `PATH...`, or **`"path": [...]` in a config passed with `-c`**. Relative entries resolve against the config's directory (probed: it errors otherwise), so use absolute paths. Passing `-c` fully replaces a project's own `.jscpd.json` (probed). | `--file-list <file>` |
| Respect house-lint's discovery | **Must pass `--no-gitignore`**, because jscpd honours `.gitignore` by default. Must pass `--absolute`: default report names are trimmed to ambiguous relative forms such as `commands.py` and `api/helpers.py`. | Fine |
| Minimum size | `--min-tokens` (default 50), `--min-lines` (default 5), `--max-lines`, `--max-size` (default 1MB per file) | `--minimum-tokens` |
| Ignore patterns | `--ignore` (file globs), `--ignore-pattern` (regex over code) | `--exclude`, `--exclude-file-list` |
| Inline suppression | `jscpd:ignore-start` / `jscpd:ignore-end` in any comment syntax, excluding the region from tokenization | `CPD-OFF` / `CPD-ON` (TypeScript not listed in the docs) |
| Comments and docstrings | Python comments are tokens in `mild` mode, so use `weak`. TypeScript comments are never tokens. Python docstrings are always tokens, and no flag skips them. | Python comments are not tokens |

**How spans map to house-lint pragmas.** This is an inference on top of the code facts in Q4.
- One finding per occurrence, owned by the file that contains it.
- A pragma's claim matches an occurrence if the occurrence's **start line falls inside the pragma's owner statement span**:
  - `ignore-next` on a `def` covers an occurrence that starts anywhere in that function, including mid-docstring, as cluster #1 does.
  - Trailing `ignore` uses the innermost statement on that line.
  - `ignore-file` is unchanged.
- jscpd's native markers change detection, not reporting, and HSL900 cannot see whether they are stale. The simplest coherent policy is to support only house-lint pragmas and leave jscpd's markers undocumented. Whether a suppressed occurrence still counts toward a cluster's occurrence threshold is open (see the decisions list).

**TypeScript pragmas.** A TypeScript pragma needs a `//` comment scanner (`_PRAGMA` at suppressions.py:24 matches only Python `#` comment tokens). There is no TypeScript statement model, so line-based ownership is the realistic first version:
- `ignore-file` in the prologue.
- `ignore` / `ignore-next` keyed to the next non-blank, non-comment line.

TypeScript pragma comments don't disturb jscpd's matching.

## Q4. What house-lint needs to host a cross-file, tool-wrapping rule

Code facts were read by a subagent and spot-checked. Paths are under `src/house_lint/` in the worktree.

**Today.**
- **Pipeline.** `cli._scan` → `discover_files` (cli.py:230) → per-file loop → `open_source` (the single read) → cache lookup (a hit `continue`s, cli.py:337-342) → `scan_source` → `apply_suppressions`, which emits HSL900 per file (scanner.py:110-120).
- **Discovery is Python-only.** It filters to `.py` at discovery.py:608, and an explicit non-`.py` path raises a `DiscoveryError`. There is no notion of language.
- **Registry.** `registry.py:70-74` raises unless `_DETECTORS` equals `ORDINARY_RULES`.
- **Unused-suppression check.** It runs inside the per-file `apply_suppressions` (suppressions.py:91), before any cross-file candidate exists.
- **Cache.** Entries are path-free, per file, keyed by content and config. The docstring asserts "single-file analyzer" (cache.py:1-8).
- **No subprocess precedent.** `src/` has no `subprocess` call, and the runtime dependencies are only `cyclopts` and `pathspec`.

**The minimum infrastructure, in dependency order.** **[reuse]** marks items #53 needs too.

1. **[reuse] Pin current behaviour.** Add characterization tests around `apply_suppressions` and HSL900's unused detection.
2. **[reuse] Language-tagged discovery.**
   - Parameterize the suffix check (discovery.py:608) and return `DiscoveredFile(path, language)`.
   - Feed only Python files to `open_source`.
   - Accept explicit `.ts`/`.tsx` paths only when an enabled project rule claims them. Discover TS files only then, so Python-only users see no change in `files_skipped` or messages.
3. **[reuse] A project-rule kind.**
   - Add `scope` and `languages` to `RuleMetadata`.
   - Add a second registry, `_PROJECT_RULES`, with its own import-time equality check.
   - The interface is `analyze(files: Sequence[ProjectFile(relpath, path, language, content_hash)], options) -> list[ProjectCandidate(rule_id, path, line, col, end_line, end_col, message, group_id)]`.
   - Generalize the options plumbing (config.py:384 allow-list, `selected_detector_inputs`) rather than adding a fourth hard-coded tuple member.
4. **[reuse] A project phase in `_scan`,** after the per-file loop.
   - Build the `ProjectFile` list from hashes already computed.
   - Run the enabled project rules, bounded separately from `MAX_CANDIDATES_PER_FILE`.
   - Handle the early `break` on a stop result (cli.py:353).
5. **[reuse] A subprocess runner** (`external_tool.py`). It owns:
   - binary location: the venv `scripts` dir first, which is where the jscpd wheel installs, then `PATH`
   - an exact version check (`jscpd --version` prints `jscpd 5.4.1`)
   - a temp config with the absolute path list
   - a timeout
   - non-zero exit and parse errors, reported as `LintError`
   - integrity: re-hash each file after the tool exits, and discard the result on any mismatch
   This replaces read-once with "identity is hashed from the bytes the analyzer saw". The stronger alternative is copying buffers into a temp dir, at the cost of remapping paths.
6. **[superseded] Project cache entries.** Spec 005's D11 (`design/specs/005-cross-file-rules-and-duplicate-code/design.md`) ratifies never caching project-rule results, so this item is dropped. The original proposal keyed entries on `sha256(sorted (relpath, content_hash), tool and version, rule options)`.
7. **[reuse] The suppression split.**
   - *Phase A*, per file, cached: parse pragmas and emit structural HSL900 diagnostics. This brief proposed storing pending project-rule claims in the cache entry; spec 005's D14 instead resolves them fresh in the project phase from the bytes already in memory, so nothing project-related is cached.
   - *Phase B*, after the project phase: match project candidates against pending claims using the span-ownership rule above, drop the suppressed ones, and emit "unused suppression" for pending claims that matched nothing.
8. **[39] The duplicate-code rule.**
   - Build the jscpd invocation: `-c <tmp> --no-gitignore --absolute --mode weak --reporters json --output <tmpdir>`, plus the threshold options.
   - Group pairs into clusters with a union-find over overlapping spans.
   - Emit one candidate per occurrence, naming its peers and a group id.
   - Make it opt-in.
9. **[39] TypeScript pragmas** (line-based, see Q3), and the hook surface: TS file types, or a second hook id.
10. **[reuse] Tests and docs.**
    - A multi-file fixture helper (`write_sample` writes only one file).
    - A test for the project phase's integrity check alongside `test_each_scanned_file_is_read_exactly_once`.
    - Update CLAUDE.md:75.

**Staged files (prek).** Running a cross-file rule over a partial file list is unsound (`design/research/2026-08-19-monorepo-ci-integration/research.md:41-46`). Three designs are viable:

| Design | Behaviour | Cost |
|---|---|---|
| A | Project rules always analyze the configured `include` scope. With explicit paths, only findings touching those paths are reported. | Two discovery calls in `_scan`. |
| B | Skip project rules when paths are given. | Simplest, but the rule silently disappears from the hook. |
| C | Ship a second hook id with `pass_filenames: false`. | A `--project-only` flag and a new hook entry. |

Under A, a commit always changes at least one hashed file, so the project cache always misses in a hook. Every commit pays a full jscpd run: about 3-4s on hassette, plus hashing, which is an inference from the timings above. hassette already runs its duplicate check pre-push, not pre-commit (`prek.toml:309`, prior brief). That points to A plus a pre-push placement, or C.

**What #53 can reuse.** Items 2-7 and 10 unchanged; dead-code findings have a single location, so the multi-occurrence questions don't arise. jscpd 5.4.1 also has `--dead-code` for JS, TS and Python. A probe run on hassette took 0.49s and reported 130 findings: 56 unused files, 44 unused exports, 15 unused symbols and 15 unused imports, each with a confidence score. Their accuracy was not assessed. Vulture is the Python-only alternative.

## Options

### Option A: wrap jscpd 5.4.1 as an opt-in project rule (recommended)

**How it works.**
- **Provisioning.** jscpd ships as the extra `house-lint[duplicates] = ["jscpd==5.4.1"]`. The rule finds the binary in the venv scripts dir or on `PATH`, and refuses any version other than the pinned one, with an install hint.
- **Default mode.** Exact, `--mode weak`, with thresholds set from a short tuning pass on hassette (30-50 tokens, 5 lines).
- **Type-3.** `--max-gap-lines` is the candidate, pending a precision check on hassette (Option A's open item).
- **Normalization.** Off until consistent identifier matching ships and is measured.
- **Suppression.** house-lint pragmas only, using the start-line ownership rule.
- **Identity for #52.** jscpd's SARIF fingerprint, or house-lint's own hash of the fragment text.

**Pros**
- One tool covers Python and TypeScript, so hassette can drop PMD and the JVM entirely.
- No Node and no JVM for users. Official wheels exist for 8 platforms.
- Spans include columns. The probed opt-in Type-2 and Type-3 modes exist for both languages.
- A built-in baseline and fingerprint, plus `--dead-code`, give #52 and #53 a head start.
- Speed on hassette is in the same range as PMD.

**Cons**
- **Supply risk.** One maintainer, a v5 rewrite that is four months old, very fast releases, and behaviour changes such as blind becoming consistent. Pinning and the version check are mandatory, and every upgrade needs a re-baseline.
- Docstrings count as code. Repeated docstring paragraphs (cluster #1) will show up, and filtering them means post-processing with `ast`.
- Signature false positives remain possible with long multi-line signatures.
- Python wheels need glibc ≥ 2.34, so it cannot be a hard dependency (see Q1).

**Effort.** Large overall. Most of it is the reusable engine work (items 1-7, 10). The rule itself is medium.

### Option B: wrap PMD CPD (lightweight comparison)

It is the same engine work, with PMD as the subprocess. Its only advantage is continuity with hassette's backlog. Against it:
- Exact matching only, in both languages (probed). Its ignore flags are inert.
- A JVM plus a download that can't come from PyPI.
- No fingerprint.
- `CPD-OFF` is not documented for TypeScript.

It loses to jscpd on every axis the owner listed.

**Do less, the fallback.** If the jscpd supply risk is judged too high, the prior brief's native detector remains the fallback for Python only. It would not cover TypeScript, which the owner has ruled out, so it is not a real alternative here.

## Concerns

- **Precision of the opt-in modes is barely measured.**
  - `--max-gap-lines 2` changed little on hassette: 247 clusters vs 258.
  - `--similarity` is dominated by 7-line entity-model properties.
  - Only default mode was hand-classified.
- **Probe coverage.** The 14-case corpus is synthetic and small. Its results agree with the hassette run, but rare tokenizer edge cases were not explored: f-strings, decorators, JSX-heavy `.tsx`.
- **Timings are noisy.** The machine was loaded. Re-measure on an idle machine before quoting hook latency.
- **Generated-file detection.** hassette relies on a banner. house-lint would need `exclude` globs, or a generated-marker option, which would be new.

## Open decisions for the sketch

- [ ] **Provisioning.** Extra (recommended) vs hard dependency vs PATH only. Exact pin vs a compatible range.
- [ ] **Defaults.** `min-tokens` (30 vs 50), `min-lines`, minimum occurrences (2 vs hassette's 3), and whether `--max-gap-lines` is on by default.
- [ ] **Normalization.** Keep blind `--ignore-identifiers` unexposed, and revisit when consistent mode ships?
- [ ] **Suppression semantics.** Per-occurrence or whole-cluster? Do suppressed occurrences count toward the threshold? Should jscpd's native markers be allowed?
- [ ] **Explicit paths and prek.** Design A, B or C. Pre-commit or pre-push.
- [ ] **TypeScript pragmas.** Line-based first, or wait for a statement model? `.ts`/`.tsx` only, or `.js` too?
- [ ] **Output.** One finding per occurrence (no schema change) vs a `related` field (`schema_version` bump).
- [ ] **Docstrings and generated files.** Accept docstring-only clones or filter them? Globs vs a banner-detection option?
- [ ] **#52 identity.** jscpd's `jscpdCloneHash/v1` vs a house-lint hash, and whether jscpd's `--baseline-from-ref` replaces part of #52.
- [ ] **Integrity.** A post-run hash check vs copying files to a temp dir.

## Recommendation

Adopt **Option A, jscpd 5.4.1 pinned and shipped as an extra**. It is the only probed tool that handles Python and TypeScript with machine-readable spans and a pip-native install. Its default mode is at least as good as PMD on every probe. On hassette, 10 of 15 sampled default-threshold clusters were real duplication.

Do not sell it as a Type-2 detector. The released normalization is blind and noisy (probed: about 4x the clusters, mostly test-shape noise). In practice this is PMD-equivalent exact matching with a better install story and opt-in Type-3. The largest cost and risk is in the engine, not the tool: the suppression split and the project phase. Those deserve the sketch's attention.

### Suggested next steps
1. `/mine-sketch` #39 with the engine items 1-7 as the backbone, settling the open decisions above. Include #52's identity question.
2. Tuning pass on hassette. Rerun `probe/scripts/hassette_nogen.sh` with `MINTOK=30` and `MINTOK=40`, each with and without `MAXGAP=2`, and hand-classify 15 clusters from each to fix the defaults.
3. Pin suppression behaviour with characterization tests before splitting `apply_suppressions`.
4. Watch jscpd for the release that ships consistent identifier matching, then re-run the `renamed` probe and the hassette normalization run.

## Sources

Committed under `probe/` (relative to this directory); see `probe/README.md`:
- `probe/corpus/`, the probe corpus
- `probe/scripts/`, the probe, run and clustering scripts
- `probe/cpdprobe{,2,3}/`, the PMD CPD probe inputs

The `results/` outputs (jscpd, PMD and hassette runs, including the cluster listings) and the other tools' outputs were not retained. The tables in this brief are the record of those runs, and `probe/scripts/` regenerates the jscpd and PMD ones.

Web and registry sources:
- jscpd README (npm 5.4.1 package), docs, changelog: https://github.com/kucherenko/jscpd, https://github.com/kucherenko/jscpd/blob/master/docs/rust.md, https://raw.githubusercontent.com/kucherenko/jscpd/master/rust/CHANGELOG.md
- jscpd on PyPI (wheel list), crates.io and GitHub APIs: https://pypi.org/project/jscpd/, https://crates.io/crates/jscpd
- PMD CPD docs: https://pmd.github.io/pmd/pmd_userdocs_cpd.html
- Other tools: https://pypi.org/project/lizard/, https://pypi.org/project/arid/, https://github.com/qltysh/qlty, https://github.com/mizchi/similarity, https://pypi.org/project/treepeat/
