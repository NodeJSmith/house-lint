---
proposal: "Add a duplicate-code detection rule to house-lint (#39), its first cross-file rule. Decide whether to port hassette's PMD CPD integration, wrap PMD differently, build a native AST-based detector, wrap another tool, or do less."
date: 2026-10-18
status: Draft
flexibility: Exploring
motivation: "hassette's PMD CPD gate (tools/check_duplicate_code.py) gives noisy and wrong matches (false positives, plus real duplicates it misses), its dup-ignore-start/end and dup-ignore-file suppression is clunky, and it recently appeared to miss matches in new files. #53 (dead code) will reuse whatever cross-file infrastructure this builds."
constraints: "Must fit house-lint's architecture: per-file detectors, flat per-file cache, read-once invariant, statement-based pragmas validated by HSL900. Pure Python and CPD parity are not required, but both are reported on because they decide whether hassette can drop CPD."
non-goals: "None stated. From spec 001: no SARIF, no Ruff/Flake8/Pylint plugin integration, no AI-authorship claims."
depth: deep
---

# Research Brief: Duplicate-code detection for house-lint (#39)

> **Superseded recommendation (2026-10-18).** This brief's Option A (build a native detector) assumed house-lint's single-file, read-once, Python-only design was a principle. The maintainer has since clarified that those are porting-era implementation details: cross-file checks and first-class TypeScript are in scope, and wrapping a proven tool is preferred over hand-building a detector. See `followup-tool-selection.md`, which recommends wrapping jscpd 5.4.1. The PMD findings below (exact-match only on Python, and per the follow-up on TypeScript too) still hold.

**Initiated by**: house-lint issue #39 (`gh issue view 39 --repo NodeJSmith/house-lint`). The question is which of PMD-as-is, PMD-reconfigured, a native AST detector, another tool, or a smaller option house-lint should adopt.

## Bottom line

Build a native, statement-granular fingerprint detector inside house-lint, and design it together with #52 (`--base`). The case for that comes from three findings:

1. **PMD CPD does exact token matching only on Python.** Probes run against hassette's pinned PMD 7.26.0 show that renaming one identifier or changing one numeric or string literal breaks a match. PMD's docs agree: `--ignore-identifiers` and `--ignore-literals` apply only to Java and C++. hassette's script docstring and session `564fb908` say the opposite, which looks like a misreading of CPD's line ranges (see Pain points, item 1). This one limitation drives much of the reported pain:
   - Type-2 copies are invisible to the gate.
   - `MIN_TOKENS` had to drop to 20 to stitch broken runs back together.
   - That low threshold then floods the gate with signature-only matches. 30 suppression markers exist only to silence "pytest test function signature" matches.
2. **Wrapping any external detector conflicts with house-lint's core invariants.** An external process reads every file a second time, which breaks the read-once guarantee the cache relies on. Its output is also line ranges that start mid-statement, while house-lint's pragmas are statement-owned. PMD adds the Java download that spec 001 deferred.
3. **#52 needs a stable, content-based identity for each duplicate finding.** That is the documented fix for this morning's "new duplicate hidden by count-matching" flaw (session `b6454510`). A native detector produces that identity as a by-product; a wrapper has to reconstruct it from whatever the external tool reports.

The main open risk is precision. Nobody has measured how a statement-level, exact-by-default detector compares to CPD's 189 current hassette clusters. A throwaway prototype run against hassette should come before committing to defaults.

House-lint cannot fully replace CPD in hassette either way. CPD also scans 302 TypeScript files (about 46K lines, 26 TS suppression markers), and house-lint is Python-only.

## Context

### What prompted this

The user reported three pain points with hassette's gate. Each is matched to evidence below.

1. **Noisy or wrong matches.**
   - False positives: 30 of the roughly 200 Python `dup-ignore-start` markers give the reason "pytest test function signature — Python has no way to share a function signature". Session `8947bbff` (2026-09-08) found a cluster that came back because PMD's clone region ran past `dup-ignore-end` into the next module-level `def`.
   - False negatives: see the next section.
2. **Clunky suppression.** hassette has 199 start/end marker pairs and 15 file-level markers across 93 files, and several `.claude/rules/*` files exist only to explain them. Three incidents stand out:
   - An unclosed marker from #2245 made every `duplicate-code` run fail on a parser error for about a month, until #2310 fixed it (session `4ec0b2f1`).
   - The autofixer's prompt forbids adding markers, so it gives up on legitimate cases (session `54a23733`).
   - Several marker reasons describe the tokenizer ("token-matches…") rather than a design decision.
3. **"Matches missed in new files."** The morning session is `b6454510` (hass-hassette, 2026-10-18, porting the CPD gate into hass-hassette PR #17). The documented problem there is narrower than "new files":
   - Codex P1 on `tools/check_duplicate_code.py:468`: "When the merge base already contains a different duplicate cluster spanning the same files with equal or greater per-file occurrence counts, this comparison classifies a brand-new head cluster as pre-existing."
   - Claude confirmed it in-session: "A new 3-way duplicate in `test_discovery.py` could hide behind the existing cluster there."
   - The user chose to "land this PR with the known gaps" and filed house-lint #52 as the fix.
   - I found no session with a live repro of a brand-new *file* escaping the scan. The searches covered `search` and `search-messages` across 10-17 and 10-18 for duplicate, cpd, PMD, check_duplicate_code, dup-ignore, new file, missed, and gate missed, plus a grep of all 20 sessions started 10-18. If the user remembers something more specific, it is not in the transcripts.

### Current state: hassette

`tools/check_duplicate_code.py` is 736 lines. hass-hassette carries a copy since PR #17. It works as follows:

- **Detector.** It downloads PMD 7.26.0 with a pinned sha256 into `~/.cache/hassette-dev-tools/pmd/` and needs Java 21. It runs `pmd cpd --file-list … --language python --minimum-tokens 20 --format xml` (lines 422-461), plus a second run for TypeScript.
- **Scan scope.** Python is limited to `PYTHON_SCAN_PATHS = ["src/hassette", "tests", "codegen", "tools", "scripts"]` (line 134).
- **Post-processing.** Fragments are dropped if they are generated, `dup-ignore-file`, imports/docstrings only, globbed, or under 5 lines. The rest go through a union-find over PMD's pairwise blocks, and only clusters of 3 or more occurrences are reported (lines 479-541). A cluster is suppressed only when every occurrence sits inside a `dup-ignore-start/end` range (line 541).
- **Gate** (`--gate-new-code`). It scans the head working tree. Only if that finds violations does it scan a `git archive` of the merge-base. A head cluster counts as pre-existing if some base cluster covers its per-file occurrence counts (`new_code_violations`, lines 562-593). A PR label `lint:duplicate-code-allowed` overrides the result (#3039 / #3041).
- **Where it runs.** CI runs a backlog step (non-blocking) and a gate step (blocking) in `.github/workflows/lint.yml:420-475`. There is also a pre-push prek hook (`prek.toml:309`).
- **Measured on hassette `main`** (subagent run, outputs in the scratchpad `cpd-plain.txt` / `cpd-gate.txt`):

| Run | Wall time | Result |
|---|---|---|
| Plain scan | 19.6s | 189 clusters |
| Gate (base scan included) | 38.7s | — |

  - Scope is about 227K Python lines plus 46K TypeScript lines.
  - Cluster fragments by location: 612 in `tests/`, 54 in `src/`, 55 in `frontend/`.
  - PMD has OOM'd locally (sessions `4ec0b2f1`, `49655223`).
  - The gate crashes when re-scanning from a temp dir where mise doesn't trust hassette's `mise.toml` (session `74a147b8`).

### Current state: house-lint

These facts come from the architecture subagent, with paths under `src/house_lint/`.

- **Scan loop.** The scan is one sequential loop in `cli._scan` (cli.py:216-400), with no multiprocessing. For each file it runs `open_source` → `SourceFile.load()` (the one read) → `_cache_keys` (content hash + config hash). Then either a cache hit `continue`s (cli.py:337-342), or `scan_source` → detectors → `apply_suppressions` → `_persist_cache_entry`.
- **Rules.** Every rule is a per-file `Detector`. `registry.py:70-74` raises at import if `set(_DETECTORS) != set(ORDINARY_RULES)`.
- **Cache hits skip parsing.** `SourceFile._analyze` is lazy, and the hit path never touches `.tree`/`.tokens`. Any per-file data a cross-file rule needs must therefore be stored in the cache entry, or warm runs lose the benefit of the cache.
- **Cache key and entries.** The cache is namespaced by `<version>-<hash of house-lint sources>`. Entries are keyed by `<content_hash>-<config_hash>`, are path-free, and store only `{findings, errors, suppressed_count, files_scanned}` (cache.py:197-250).
- **The single-file assumption is written down.** cache.py:3-5 says: "House-lint is a single-file analyzer with no cross-file dependencies, so a flat cache keyed by (file content hash, effective config hash) is semantically correct". `docs/configuration.md:121` repeats it.
- **Suppressions.** Pragmas are statement-owned (suppressions.py:24, 303-341). HSL900's "unused" check runs per file inside `apply_suppressions`, from that file's own candidates (suppressions.py:91).
- **Findings.** `Finding` (results.py:25-47) has exactly one location. The only reporters are text and JSON (`schema_version: 1`).
- **Pre-commit hook.** `.pre-commit-hooks.yaml` uses default `pass_filenames`. hassette's hook passes staged files (`prek.toml:70`). `design/research/2026-08-19-monorepo-ci-integration/research.md:41-46` already flags cross-file analysis under `pass_filenames` as "silently unsound".
- **Baseline cost on hassette** (measured with hassette's installed house-lint 0.2.0, about 1,100 files):

| Run | Wall time |
|---|---|
| Cold, `--no-cache` | 8.9s (two runs) |
| Warm | 1.65s |

### Key constraints

- **Prior deferral.** `design/specs/001-llm-lint-extraction/brief.md:70-71` lists under *Deferred*: "PMD duplicate-code detection and its Java/tool-download requirements" and "Whole-file and paired block suppressions". `design.md:33` puts "duplicate-code detection, Java/PMD tooling … SARIF" in non-goals.
  - The brief records the Java/download dependency as the reason. It never evaluated detection quality.
  - Block-range suppression was deferred in the same breath. Any option that keeps hassette's paired-marker semantics reopens that decision.
- **Dependencies.** Runtime deps are `cyclopts` and `pathspec`, with Python ≥3.11 (CI covers 3.11-3.14). Everything else is stdlib `ast` and `tokenize`.
- **#52 depends on #39.** #52 (open, priority:high) proposes `house-lint check --base`. In its words: "Duplicate-code findings would key on a hash of the normalized fragment rather than file counts". Once #52 ships, hassette and hass-hassette delete `check_duplicate_code.py`, `lint_helpers.py` and the PMD/JVM dependency.

## Pain points traced to mechanisms

Each item gives what the evidence shows and how confident the claim is.

1. **CPD on Python does not normalize identifiers or literals.**
   - *Direct evidence: probes against the pinned PMD 7.26.0*, in `/tmp/claude-1000/-home-jessica-source-house-lint/b3ee79d5-9f12-4028-b35d-95fdfb82d26b/scratchpad/cpdprobe{,2,3}/`:
     - An exact copy matched (6 lines, 36 tokens).
     - A copy with only numeric literals changed (`total + 5` → `total + 9`, …) produced no match at `--minimum-tokens 15`.
     - A copy with one identifier renamed (`scale` → `factor`) produced no match.
     - A copy with only string literals changed matched only up to the first differing literal ("5 line (26 tokens)").
     - Adding `--ignore-identifiers --ignore-literals` changed nothing.
   - *Docs agree:* the PMD CPD options table lists both flags as applying to "Java, C++" only.
   - *This contradicts* the script's docstring (lines 11-20) and the conclusion of session `564fb908` ("PMD's default Python/TS tokenizer already treats differing literal values *and* differing identifier names as equivalent … `total = total + 5` matches `total = total + 9`, `def test_alpha():` matches `def test_beta():`").
   - *Inferred explanation:* CPD reports the line span of the matching token run. A run that ends just before a differing literal, or starts just after a differing function name, still reports that line as part of the match. My string-literal probe shows exactly that: line 5 is "included" even though the match breaks inside it. TypeScript was not probed.
2. **The low threshold produces signature-only false positives.** *Supported.*
   - Because runs break at every renamed name or literal, `564fb908` found that clusters only connected after lowering `--minimum-tokens` to 20. At that setting, 1,355 candidates came out (698 after filters).
   - At 20 tokens, a parameter list shared by two differently named tests is enough for a match, since the run starts right after the name. That fits the 30 "pytest test function signature" markers.
3. **New duplicates hidden by count-matching.** *Direct:* `b6454510`, Codex P1, house-lint #52 flaw 1. Gate identity is (file, occurrence count), not content.
4. **Moves and splits reported as all-new.** *Direct:* hassette #2803 (open). Same root cause as item 3.
5. **Unscanned new directories.** *Direct from code.*
   - `client/` and `wire/` (uv workspace members, about 5.2K lines in 39 files) and `examples/`/`docker/` are not in `PYTHON_SCAN_PATHS`. New files there are never checked.
   - No session records this, but it literally matches "missed in new files".
   - house-lint's discovery (whole tree minus gitignore) would not have this failure mode. hassette's `[tool.house-lint] include` already lists `wire` and `client`.
6. **A first copy is invisible by design.** *Direct:* `MIN_OCCURRENCES = 3` (line 138). Copying a block once into a new file makes a pair, which is never reported (docstring lines 22-27 explain this choice).
7. **Clusters split apart.** *Inferred from the probe:* CPD reports overlapping fragments with different extents for the same region (`a.py:1-8`, `a.py:5-8`, `a.py:1-5` in one probe). The union-find keys on exact `(file, start, end)`, so overlapping-but-unequal fragments become separate nodes. That can split one real 3-way clone into pairs. This is consistent with `564fb908`'s observation that "a pair only links if its own pairwise token run crosses `--minimum-tokens`".
8. **Region overrun past markers.** *Direct, one instance:* `8947bbff`. CPD token runs end mid-statement, past a marker boundary, so a fully marked cluster comes back.

## How each candidate detects clones

| Candidate | Algorithm | Python clone types | Normalization | Suppression | Runtime and dependencies |
|---|---|---|---|---|---|
| PMD CPD 7.26 | Karp-Rabin over JavaCC tokens | **Type-1 only** (probe and docs) | None for Python. The ignore flags are Java/C++ only. | `CPD-OFF`/`CPD-ON` comments (Python supported, TS not listed) | JVM (Java 21 in hassette), ~50MB download, 19.6s plain on hassette, OOM reports |
| Pylint `symilar` / R0801 | Hash of stripped lines, rolling window of `min-similarity-lines`, merges successive matches | Type-1, line-level | Ignores comments (naive `#` split), docstrings, imports (AST), signatures; no identifiers | Block-level disable was broken for 10 years (pylint #214, closed 2024; users report it persists) | Pulls in pylint/astroid; slow on large repos (arid benchmarks claim 190-250x slower than arid) |
| jscpd v5 | Rabin-Karp over per-language tokens; Rust rewrite, prebuilt binary | Type-1 default. Type-2 via `--ignore-identifiers consistent\|blind` and `--ignore-literals`. Near-Type-3 via `--max-gap-lines` (as documented; not tested here). | Configurable | `jscpd:ignore-start/end` | Self-contained binary, multi-language including TS, SARIF/JSON output. hassette rejected jscpd on 2026-08-08 for exact matching; whether v5's normalization flags existed then is unknown. |
| arid (PyPI, Rust) | Normalize → intern lines → suffix array + LCP → maximal repeats | Type-1, line-level. Identifier-renaming detection is an explicit non-goal. | Comments, docstrings, imports, signatures | `# arid: disable/enable` regions, stale-suppression audit, baselines | Rust wheel. First release 2026-08-13, 27 releases and a v2 since: young and fast-moving. |
| Native AST (proposed) | Per-statement normalized hash; windows over statement sequences in each block body; global dict join; merge adjacent windows into maximal runs | Type-1 by default. Type-2 via an opt-in *consistent* alpha-renaming. Type-3 via gap merging (deferrable). | Chosen by house-lint: skip imports, docstrings, signatures | House-lint pragmas, statement-owned | Stdlib `ast` only; per-file fingerprints are cacheable |

**Precision of blind normalization** (Inferred). No empirical numbers were found. jscpd ships both `consistent` and `blind` modes, which suggests blind is noisy. Blind renaming collapses dataclasses, test arrange blocks and CLI boilerplate into identical shapes, the same kind of code that already dominates hassette's 612 test fragments. Consistent renaming, which requires a one-to-one name mapping, is the safer form of Type-2.

## Feasibility Analysis

### What would need to change in house-lint, for any in-process option

| Area | Files | Effort | Risk |
|---|---|---|---|
| Rule kinds | `rule_catalog.py`, `registry.py` | Low | The import-time `_DETECTORS == ORDINARY_RULES` check crashes on a naive new ID. Needs a second "project rule" registry, or a scope field on `RuleMetadata`. |
| Options | `config.py` (`_rule_options` allow-list, `LintConfig`, `DetectorOptions`, `selected_detector_inputs`), `cache.hash_effective_config` | Low-Med | Options are wired through a hard-coded 3-tuple, and each new rule repeats that plumbing. |
| Per-file facts in the cache | `cache.py`, `scanner.FileScanResult`, `cli._scan` | Med | Facts must be path-free (entries are shared by content), keyed by rule options and the Python version (3.12 changed f-string tokenization; AST is steadier), and present on hits. Otherwise the rule forces parsing on every warm run. |
| Cross-file pass | `cli._scan` after the loop (cli.py:389-391) | Med | It must handle an early `break` on a stop result, and add its own suppressed count. It needs its own bound, since `MAX_CANDIDATES_PER_FILE` is per file. |
| Suppressions / HSL900 | `suppressions.py` | **High** | Per-file "unused" is computed before cross-file candidates exist, so a pragma naming the duplicate rule would always be "unused". Claim collection has to be split from finalization, and claims cached per file. Clone regions also need a statement-ownership policy. |
| Multi-location findings | `results.py`, `reporters/text.py`, `reporters/json.py`, cache payload | Med | A `related` field changes `schema_version: 1`. Emitting one finding per occurrence avoids that. |
| Explicit-path invocations | `cli.py`, hook docs | Med | prek passes staged files. Either index the whole configured scope and report only findings touching the given paths (cheap with a warm cache), or skip project rules when explicit paths are given and ship a `pass_filenames: false` hook. |
| Docs | `docs/configuration.md:121`, `docs/rules.md`, `docs/suppressions.md`, CLAUDE.md, cache docstring | Low | — |
| Tests | new rule tests, multi-file fixture helper, read-once test extended to the cross-file pass | Med | No shared multi-file fixture exists yet. |

### What already supports this

- `SourceFile` already caches `.tree` and `.tokens`, so the miss path can fingerprint without extra parsing.
- Cache entries are content-keyed and path-free, which is exactly what per-file fingerprints need. The namespace hash auto-invalidates old entries when rule code changes.
- The scan is sequential, so there is no pickling or IPC concern.
- Discovery covers the whole project by default, which removes hassette's scan-path drift (pain point 5).
- `per-file-ignores`, `ignore-file`, and generated-file exclusion via `exclude` cover most of hassette's automatic exclusions. The "Do not edit this file directly" banner check has no house-lint equivalent; hassette could use `exclude` globs instead.
- `test_gitignore_parity.py` is a precedent for differential tests against a reference tool.

### What works against this

- Every invariant (cache docstring, read-once, HSL900 unused, single-location `Finding`) assumes one file. This is a real architectural extension, not a rule addition.
- Statement-owned pragmas cannot express "this range of lines" without the paired block suppression that spec 001 deferred.
- Python-only scope means hassette's TypeScript duplication stays with some other tool.

## Options Evaluated

### Option A: Native statement-sequence fingerprint detector, with generic project-rule infrastructure (recommended direction)

**How it works.** A project rule has two halves:
- `collect(source, options) -> Facts`, which runs per file on a cache miss. Its output is stored in the cache entry: path-free line spans plus hashes.
- `analyze(facts_by_path, options) -> candidates`, which runs fresh after the loop on every scan.

For #39, `collect` walks each block body (module, class, function, `if`/`for`/`with`/`try` suites) and computes one normalized hash per statement. The hash comes from the statement's AST with positions stripped and docstrings dropped; imports and `def` signatures are excluded by default, as pylint's `ignore-imports`/`ignore-signatures` do. `analyze` builds a global map from k-statement window hash to locations, merges adjacent windows into maximal runs, applies a minimum size (statements and/or tokens/lines) and a minimum occurrence count, and emits one candidate per occurrence. Each candidate is owned by the statement where the run starts, and its message names the sibling locations.

Normalization defaults to exact names and literals (Type-1 at statement granularity). An opt-in `normalize = "consistent"` alpha-renames locals per window for Type-2. Gap-tolerant Type-3 merging can wait until the evidence shows it is needed.

#53's dead-code rule uses the same two halves: per-file definitions and references in `collect`, a global join in `analyze`.

**Suppression and HSL900.**
- Each occurrence is a separate candidate in its own file, so `ignore`, `ignore-next` and `ignore-file` keep working unchanged.
- `apply_suppressions` splits in two: claim collection, which is per file and cached, and finalization, which runs after `analyze` for project-rule IDs.
- One semantic decision is open. hassette requires *every* occurrence to be marked before a cluster is silenced. The per-occurrence model is more natural: an unmarked occurrence still reports, and marked ones still count toward the threshold. That choice belongs in sketch.
- Because runs start and end on statement boundaries, the marker-overrun false positive (pain point 8) cannot happen.
- `ignore-next` on a `def` naturally covers a whole function.

**Identity for #52.** A clone group's identity is the hash of its normalized statement sequence. It survives moves, renames and reformatting, which fixes pain points 3 and 4.

**Pros**
- Fixes the root causes of pain points 1-5, 7 and 8 rather than filtering their output. Pain point 6 becomes a configurable threshold.
- Pure Python with no new dependencies, and honours the spec-001 Java deferral.
- Fits the cache, and the read-once invariant holds by construction: facts come from the one buffer.
- Delivers the #52 identity and the #53 infrastructure in one design.

**Cons**
- Not CPD-equivalent, in either direction: different units (statements, not tokens) and different defaults. The hassette backlog count will change, and some of the 199 marker pairs become stale. HSL900 would flag stale house-lint pragmas, but the old `dup-ignore` comments simply become dead text that needs a cleanup pass.
- Misses copies that are not statement-aligned, such as long duplicated expressions inside one statement. Single-statement clones above a size threshold could be handled by also hashing large compound statements as units.
- The suppression/HSL900 split is the most invasive refactor in the codebase to date.

**Effort.** Large overall. The detector itself is medium (a few hundred lines). Most of the cost is the first-ever project-rule plumbing: cache facts, suppression finalization, explicit-path semantics, docs, and multi-file tests.

**Performance** (*Inferred estimate, not measured*).
- Cold: the per-file cost is one extra AST walk with hashing, on top of a 8.9s cold scan that already tokenizes and parses everything. Probably 1-3s more on hassette.
- Warm: the join over roughly 100-200K statement hashes is a dict build, which should be well under a second.
- Either way it should beat PMD's 19.6s and 38.7s, but this needs confirming in the prototype.

**Dependencies.** None.

### Option B: Port or wrap PMD CPD (user's (a) and (b))

**How it works.**
- *(a) Port as-is:* move `check_duplicate_code.py`'s logic into a house-lint project rule that downloads PMD, shells out with the discovered file list, parses the XML, clusters, and maps fragments to findings.
- *(b) Reconfigured:* the same, but use CPD's native `CPD-OFF`/`CPD-ON` instead of post-filtering. Hash CPD's `<codefragment>` text for a content identity (the fix #2803 proposes). Raise `--minimum-tokens` and add a signature filter to cut the false positives.

**Pros**
- Proven on hassette for two months. Its known backlog and triage history carry over.
- Gives a TypeScript scan too, though that would be outside house-lint's Python charter.

**Cons**
- **Cannot fix the main false-negative cause.** Python Type-2 normalization does not exist in CPD, so a renamed copy is never seen. Raising the threshold to cut noise makes misses worse, which is the trade-off `564fb908` was forced into.
- Breaks the read-once invariant by design: PMD reads every file itself. Cached per-file facts are impossible, so every scan pays the full PMD cost (about 20s on hassette, and #52's base comparison doubles it).
- Reintroduces the Java-21 and ~50MB download that spec 001 deferred, inside a pip-installed linter, along with known install/OOM failures (`4ec0b2f1`, `74a147b8`).
- Token runs start and end mid-statement. Mapping them to statement-owned pragmas needs ad hoc rounding, which is where pain point 8 came from.
- `CPD-OFF` removes tokens from matching entirely. That changes "every occurrence must be marked" into "marked code never participates", and TS has no `CPD-OFF` per the docs.

**Effort.** Medium for the wrapper, plus the same project-rule infrastructure as A. The result is worse than A on most axes.

**Dependencies.** JRE 21 and the PMD distribution.

### Option C: Wrap another tool (user's (d))

- **jscpd v5** is the strongest external candidate.
  - It offers Type-2 via `consistent` renaming, gap merging, SARIF, a self-contained binary, and TypeScript coverage.
  - As a house-lint dependency it has Option B's architectural problems: a second read, no cacheable facts, line-based regions. It also needs a binary distribution story for a pip package.
  - Better used directly by hassette for `frontend/src` than inside house-lint.
- **arid** has the closest Python-specific design (normalizes imports, docstrings and signatures; suppression audit; baselines).
  - Same wrapper problems. It is Type-1 only by stated design.
  - Two months old with 27 releases, so the interface is likely to keep moving.
- **Pylint symilar** is line-based Type-1, slow, and needs a heavy dependency. Its block-level suppression has a decade of bug reports (#214). Not recommended.

**Effort.** Medium, plus the infrastructure. **Dependencies.** A Rust binary or pylint.

### Option D: Do less (don't build #39 yet)

**How it works.** Leave house-lint single-file. Fix the hassette and hass-hassette script where it is cheap:
- Hash `<codefragment>` for gate identity (fixes pain points 3 and 4).
- Add `client`, `wire` and `examples` to `PYTHON_SCAN_PATHS`, or derive the scan list from `[tool.house-lint] include` (fixes 5).
- Key union-find on overlapping spans rather than exact tuples (likely fixes 7).
- Raise `MIN_TOKENS` and drop signature-only fragments with the existing `ast` helper (reduces 2).

Optionally, try jscpd v5 with `--ignore-identifiers consistent` as a straight replacement for CPD across Python and TS.

**Pros**
- Small and local, and hits the documented flaws quickly.
- Keeps house-lint's invariants intact, which defers the hardest refactor.

**Cons**
- Leaves the Type-1-only blindness (pain point 1) unless jscpd works out.
- Keeps two 700-line script copies in sync by hand, the exact problem #52 was filed to remove.
- Blocks #52's duplicate half and #53's dead-code rule.

**Effort.** Small to medium.

### Hybrid worth naming

Option A for Python in house-lint, plus jscpd v5, or CPD restricted to TypeScript, for hassette's `frontend/src`. This is the only path on which hassette drops PMD *and* keeps a TS duplicate gate.

## Fit assessment

| Criterion | A: native | B: PMD | C: jscpd/arid wrapper | D: do less |
|---|---|---|---|---|
| Per-file detector + cross-file pass | Clean collect/analyze split | Subprocess sidecar; no per-file half | Same as B | N/A |
| Cache | Facts cached; join always fresh | Uncacheable; full cost every run | Same as B | Unchanged |
| Read-once | Holds | Violated (second read) | Violated | Unchanged |
| Pragmas / HSL900 | Statement-aligned; needs suppression split | Mid-statement regions; rounding needed | Line regions; rounding needed | Paired markers stay |
| Spec-001 Java deferral | Honoured | Contradicts it | Swaps the JVM for a binary | N/A |
| Python Type-2 | Opt-in consistent renaming | **Impossible** | jscpd yes, arid no | Only via jscpd |
| #52 stable identity | Native | Via codefragment hash | Via output text | Script-local only |
| #53 reuse | Direct | None | None | None |
| Pure Python | Yes | No | No | — |
| CPD parity | No (by design) | Yes | No | Yes |
| TS coverage | No | Yes, outside charter | jscpd yes | Yes |
| Cost on hassette | ~+1-3s cold, under 1s warm (estimate) | ~20s, ~40s with base | Unmeasured; fast per vendor claims | ~20-40s |

## Concerns

### Technical risks
- **Precision is unmeasured.** Statement-level Type-1 with imports and signatures excluded could under-report compared with CPD's 189 clusters, or report different ones. Without a prototype run on hassette, defaults like minimum statements, minimum occurrences (2 vs hassette's 3) and normalization are guesses.
- **Suppression refactor.** Splitting `apply_suppressions` touches the most carefully specified code in the repo. Spec 001 called for characterization tests around statement ownership, and those must be pinned before the split.
- **Cache staleness.** Facts must be keyed by the duplicate rule's options and the Python version, and stay path-free. A mistake silently produces wrong duplicates on warm runs. The read-once test needs extending, as must the cache tests for a facts payload on both hit and miss.
- **Explicit-path semantics.** If the rule ever runs over only the staged files, its results are silently unsound (`research.md:41-46`).

### Complexity risks
- This introduces a second rule kind, a second suppression phase, and multi-location messages. #53 and #52 both build on it, so getting the abstraction wrong costs three issues, not one.
- Per-occurrence vs all-occurrences suppression semantics will surprise someone coming from hassette's markers.

### Maintenance risks
- Normalization rules (what counts as the "same" statement) become a public behavioural contract under release-please. Changing them churns every downstream backlog and #52 identity.
- If hassette keeps a TS duplicate tool, it maintains two duplicate policies with different suppression syntaxes.

## Open Questions

- [ ] **Morning session scope.** Was "missed in new files" the Codex P1 count-matching flaw from `b6454510`, or a different observation? No transcript shows a brand-new file escaping the scan. Pain points 5 and 6 are code-level candidates that no session records.
- [ ] **TypeScript in hassette.** Is it acceptable to keep a separate TS duplicate gate (jscpd v5, or CPD TS-only), or drop TS duplicate gating? This decides whether "hassette drops PMD" is fully achievable.
- [ ] **Suppression semantics.** Per-occurrence suppression vs hassette's every-occurrence rule. Do suppressed occurrences still count toward the threshold?
- [ ] **Default occurrence threshold.** 2 or 3? hassette's docstring argues 3 separates designed parallel structure from copy-paste debt, but that means a first copy is never seen (pain point 6).
- [ ] **Default enablement.** A cross-file rule with a real backlog probably wants opt-in. Confirm.
- [ ] **Explicit-path mode.** Whole-scope index with path-filtered reporting, or skip project rules when explicit paths are given and ship a second hook ID?
- [ ] **Multi-location output.** One finding per occurrence (no schema change), or a `related` field (`schema_version` bump)?
- [ ] **CPD's TypeScript behaviour.** Not probed. `564fb908` claims TS normalizes too; given the Python result, verify before relying on CPD for TS.
- [ ] **jscpd v5's Python tokenizer quality** with `--ignore-identifiers consistent`. Documented, but not tested here.

## Recommendation

Pursue **Option A**, scoped jointly with #52's finding identity, but **prototype before committing**.

- *Supported:* PMD on Python is Type-1-only. Probes and docs agree, and that limitation explains most of the noise and the misses.
- *Direct:* the documented "new duplicate hidden" flaw needs content identity (#52, `b6454510`).
- *Supported:* B and C each break the read-once invariant and the per-file cache, and B reintroduces the dependency spec 001 deferred.
- *Inferred:* A will match or beat CPD's recall on real copy-paste while cutting signature and boilerplate false positives. Nothing has measured that yet.

If the prototype shows statement-level detection misses too much, fall back to adding token-window fingerprints over `source.tokens` inside the same collect/analyze infrastructure. Do not fall back to PMD.

Treat Option D's cheap script fixes (content-hash identity, scan paths) as worth doing in hassette now only if #39 plus #52 is more than a few weeks out. Otherwise they are throwaway work.

### Suggested next steps
1. **Prototype the detector against hassette.** Build a throwaway branch or script, outside the house-lint architecture, and compare its clusters with `cpd-plain.txt` (189 clusters). Hand-classify a sample of the clusters each side finds and the other misses, then set the default thresholds and normalization from that.
2. **Sketch #39 and #52 together** via `/mine-sketch`. Settle the project-rule interface (collect/analyze), facts caching, suppression finalization, per-occurrence semantics, explicit-path mode, and the identity hash.
3. **Pin suppression behaviour first.** Add characterization tests around `apply_suppressions` and HSL900 unused-detection before splitting it.
4. **Decide hassette's TS plan** separately: try jscpd v5 on `frontend/src`.
5. **Fix the hassette docstring.** Correct the false normalization claim in hassette's `tools/check_duplicate_code.py` (lines 11-20). It is currently steering decisions. That means a hassette issue or PR, not a drive-by edit.

## Sources

- PMD CPD docs (options "Applies to" table, CPD-OFF/ON, Karp-Rabin): https://pmd.github.io/pmd/pmd_userdocs_cpd.html
- PMD PythonCpdLexer: https://raw.githubusercontent.com/pmd/pmd/main/pmd-python/src/main/java/net/sourceforge/pmd/lang/python/cpd/PythonCpdLexer.java
- Pylint similarity checker source: https://raw.githubusercontent.com/pylint-dev/pylint/main/pylint/checkers/symilar.py
- Pylint #214 (R0801 can't be disabled): https://github.com/pylint-dev/pylint/issues/214
- jscpd README and v5 docs: https://github.com/kucherenko/jscpd/blob/master/README.md, https://github.com/kucherenko/jscpd/blob/master/docs/rust.md
- arid (PyPI JSON metadata and README): https://pypi.org/project/arid/
- Ruff #17724 (detect code duplication, open): https://github.com/astral-sh/ruff/issues/17724
- SonarSource duplication thresholds: https://community.sonarsource.com/t/how-can-i-define-the-number-of-token-needed-for-duplicated-code/19597
- AdaCore Python copy-paste detector: https://www.adacore.com/blog/a-usable-copy-paste-detector-in-few-lines-of-python
- Winnowing (Schleimer et al. 2003): https://sschleimer.warwick.ac.uk/Maths/winnowing.pdf
- Clone-detection survey: https://ar5iv.arxiv.org/html/2006.15682
- Session handles: `b6454510` (2026-10-18, hass-hassette PR #17), `564fb908`, `45acc4a6`, `5ceab93d` (2026-08-08), `8947bbff` (2026-09-08), `4ec0b2f1`, `49655223` (2026-09-18/19), `74a147b8` (2026-10-10), `54a23733`, `f74c83c6` (2026-10-17)
- GitHub: house-lint #39, #52, #53; hassette #1867, #2803, #3039/#3041, #2310, #2311, #2782, #2245
