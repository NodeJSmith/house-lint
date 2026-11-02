---
topic: "How mature linters give cross-file rules their input and report their results"
date: 2026-11-02
status: Draft
---

# Prior Art: project-rule input and results

## The Problem

house-lint #59 adds a project phase that runs after the per-file loop. A separate opt-in phase is already settled (`design/research/2026-10-30-linter-engine-architecture/prior-art.md`). What's open is what a project rule receives, how much of it stays in memory, how a result with several locations travels to the reporter, how enabled rules decide which files load, and what an ignore does to a cross-file rule's input.

## Bottom Line

- **Retention:** retaining every file's content for the run is normal for content-comparing rules. Pylint and jscpd do it, and neither caps it. Peers avoid the cost by paying it only when the rule is enabled, and only oxlint bounds memory, for a rule class (import graphs) that doesn't fit duplicate detection.
- **Multi-location shape:** primary-plus-related is the reporter contract (LSP, SARIF, Ruff). Internally the symmetric N-ary group is the honest shape for duplicates (CPD), and it avoids Pylint's wrongly anchored R0801.
- **File selection:** deriving scan scope from enabled rules is established (Biome, oxlint). Always-on directive validation doesn't drive language selection in any peer found.
- **Ignores:** input-level removal is what Pylint and jscpd do for cross-file analysis, which agrees with parent D21.

## How We Do It Today

Discovery selects `.py` by suffix regardless of the enabled rules (`language_for`). The file loop in `pipeline.run_file_phase` loads each file once, derives a cache key, and drops the bytes at the end of the iteration. `FILE_RULES` in `rule_catalog.py` is the one rule table, and per-file-ignores narrow enabled rules per file (`config.per_file_enabled_rules`). `CandidateFinding` and `Finding` hold one location. With `select = []`, HSL900 still runs: a probe on `a.py` gave `files_scanned: 1` and an HSL900 finding.

## Answers

**1. What a project rule receives.**
- **Pylint (retain whole files, bounded by nothing).** `SimilaritiesChecker.process_module` calls `Symilar.append_stream`, which builds a `LineSet` holding `_real_lines` (every line of the file) plus `_stripped_lines`, and appends it to `self.linesets` (`pylint/checkers/symilar.py` ~L346-383, `LineSet` ~L660-720). All linesets live until `close()` runs after the last file; `open()` resets them. The class docstring warns "This computation may be memory / CPU intensive, so you should disable it". In `-j` mode the same objects are the map data: `get_map_data()` returns `self.linesets`, `reduce_map_data()` concatenates them and calls `close()` (`pylint/lint/parallel.py` L90, L129). Memory was a real problem: PR #10881 (merged Mar 2026) cut memory 12-27% by reusing the already-parsed AST instead of re-parsing, and reports "oom killed on cpython" before / 90s after; pandas 1.2 GB to 0.9 GB, sentry (7.6k files) 1.85 GB after. No cap or streaming was added; whole-run retention stayed.
- **Biome (retains text of every indexed file, scan mode chosen by rules).** `ProjectScanComputer::compute` (`crates/biome_service/src/configuration.rs` ~L1651-1760) returns `ScanKind::NoScanner | KnownFiles | Project | TypeAware`: `Project` only if a rule in the `Project` domain is enabled (including via `overrides`), `TypeAware` if `Types`. In `open_file_internal` (`crates/biome_service/src/workspace/server.rs` ~L832-960) the file text is read into `Arc<str>` and stored in the workspace database; a per-file `files.maxSize` limit gates it (`Settings::get_max_file_size`). A project rule is still a per-file query (`type Query = ResolvedImports<AnyJsImportLike>` in `crates/biome_js_analyze/src/lint/suspicious/no_import_cycles.rs`) over a shared module graph of resolved imports, not other files' text.
- **oxlint (module records are kept, source text is deliberately dropped).** `crates/oxc_linter/src/service/runtime.rs`: `ModuleRecord`s sit in `modules_by_path` for the whole run; each file's source/semantic (`ModuleContent`) is released after its group is linted. The code comments name the memory problem directly: files are processed in groups of `threads*4`, sorted deepest-path-first so leaf modules finish and free memory early, with the group size "empirical based on AFFiNE". Cross-module mode is only switched on when the `import` plugin is enabled (`apps/oxlint/src/lint.rs` L398). Dependencies pulled in only for the graph get `content: None`.
- **ESLint (single file only).** Maintainer answer in discussion #18676: "rules only run in the context of one file." No project input exists. typescript-eslint hands rules a whole `ts.Program` via `parserServices.program` (secondary: typescript-eslint.io custom-rules docs), which is type info, not a rule-visible file set.
- **elm-review (per-file summaries, folded).** Per the author's write-up, each module yields a `ProjectContext` summary via `fromModuleToProject`, the module context is discarded, and summaries are folded; a module sees only its imports' summaries. Secondary (blog), design reference only.
- **jscpd v5 (tokens for all files).** `SourceFile { id, format, tokens: Vec<Token> }` (`rust/crates/cpd-core/src/models.rs` ~L402); `detect_prepared` builds one hash-window map over all prepared sources (`rust/crates/cpd-core/src/detect.rs` ~L466-530) with a comment that raising an occurrence cap "uses more memory". v4 offered an optional LevelDB/Redis store for token maps (`README-v4.md`: `--store leveldb` for large repositories, default memory).
- **Wrappers get paths only.** MegaLinter runs jscpd with `cli_lint_mode: project` and `lint_all_files: true` (`megalinter/descriptors/copypaste.megalinter-descriptor.yml`): one invocation on `.`, the tool does its own discovery. pre-commit: `pass_filenames: false` runs the hook once with no filenames; the hook is skipped when no files match unless `always_run` (`pre_commit/commands/run.py` L168, L188). Semgrep: CE analyses one function at a time; cross-file is a separate Pro binary that "does not currently run on diff-aware (pull request) scans" (secondary: docs.semgrep.dev).
- **qlty** has its own Rust, tree-sitter duplication engine: fingerprint index over all nodes in the repo (secondary: docs.qlty.sh/duplication).

**2. Multi-location diagnostics.**
- **Primary plus related is the dominant structured shape.** LSP `Diagnostic.relatedInformation: DiagnosticRelatedInformation[]` (`{location, message}`; spec text: "e.g. when duplicating a symbol in a scope"). SARIF 2.1.0 `result.locations` plus `relatedLocations` ("each of which represents a location relevant to understanding the result"). Ruff's `Diagnostic` (`crates/ruff_db/src/diagnostic/mod.rs`) holds `annotations: Vec<Annotation>` with `is_primary`, `primary_span()`/`secondary_annotations()`, plus `SubDiagnostic`s with their own annotations; the renderer sorts by primary span only (`RenderingSortKey`). Biome `RuleDiagnostic` has one `span`, `Detail { range }` labels (same-file `TextRange`, no path) and text `note`s (`crates/biome_analyze/src/rule.rs` ~L1629-1820).
- **N-ary text blob (Pylint).** R0801 is one message with no node; locations are `==a:[0:12]` lines inside the message string, sorted, then the code. Ran: with `a.py` and `b.py` duplicated and an unrelated `c.py`, pylint 4.1.3 reported it as `c.py:1:0: R0801 Similar lines in 2 files ==a:[0:12] ==b:[0:12]`; the header location is whichever file was last processed. Ranges are zero-based.
- **N-ary structured group (PMD CPD XML).** `<duplication lines tokens>` containing N `<file path line endline .../>` plus one `<codefragment>` (docs.pmd-code.org CPD report formats). Fully symmetric, no primary.
- **Pairwise (jscpd).** `CpdClone { fragment_a, fragment_b, token_count, kind, similarity }` (`cpd-core/src/models.rs` ~L224); JSON uses `firstFile`/`secondFile`. Its SARIF reporter maps A to `locations[0]` and B to `relatedLocations[0]`, with the primary message linking `[file:line](0)` because "GitHub code scanning only surfaces related locations that the primary message links to this way" (`cpd-reporter/src/sarif.rs` ~L196-215). Three copies are N-1 or more pairs.
- **Biome cycles:** one diagnostic per import node, the cycle path is note text, not structured locations (`no_import_cycles.rs` `diagnostic()`).

**3. Which files get loaded.**
- **Biome: selected by enabled rules and per-language feature flags.** No project/type rules enabled gives `NoScanner` (or `KnownFiles`, config/ignore files only: `is_required_during_scan`) in `is_ignored_by_scanner` (`workspace/server.rs` ~L1826-1960). In the CLI traversal a file whose language has the feature disabled returns `FileStatus::Ignored` before it is read; a file with no handler returns `UnhandledDiagnostic` ("Biome doesn't know how to process this file", warning, VERBOSE-tagged) unless `files.ignoreUnknown` is true (`crates/biome_cli/src/runner/process_file.rs` L182-205, `crates/biome_service/src/projects.rs` L247). Unreadable files in the index path are silently skipped (`open_file_internal` returns `Default` when `!is_readable()`).
- **Ruff: by extension, not by rules.** Directory walk includes `*.py` (+ `.pyi`, `.ipynb` and pyproject/ruff.toml); an explicit path is accepted whatever its name (`ResolvedFile::Root`, `resolver.rs` ~L728). `check.rs` L48-60 then silently drops anything whose `SourceType` is not Python or pyproject/ruff TOML: ran with `n.txt` passed explicitly, no output or error. Ruff does not skip reading when few rules are on: ran `--select RUF100 bad.py` and got an `invalid-syntax` diagnostic.
- **ESLint: by `files` globs in config, not by rules.** An explicit file matching no config object yields a result with warning `File ignored because no matching configuration was supplied.` (`lib/eslint/eslint-helpers.js` L683; ran with `b.ts`: warning, exit still reports 0 errors; `--no-warn-ignored` suppresses). Issue #19361: CLI-only rules never applied to `.ts` because only `.js/.cjs/.mjs` are configured by default; maintainer called it an "oversight".
- **oxlint:** walker takes an extension list (`apps/oxlint/src/walk.rs`); import-plugin mode additionally parses dependencies it reaches by resolution regardless of the walk (runtime.rs doc comment on `ProcessedModule.content`).
- **Pylint:** the checker set is derived from enabled messages (`prepare_checkers`, `pylinter.py` L693: a checker is needed only if one of its messages is enabled), but the linter itself is always in the list and files are always parsed.
- **jscpd:** format detected from extension/filename (`walker.rs` L278); no known format means silently skipped; `ignore` globs applied at walk time, before tokenizing.
- **MegaLinter:** a linter is active by detected file extensions or `active_only_if_file_found`, copy-paste (`lint_all_files: true`) runs on the whole workspace.

**4. Directive validation with no ordinary rules.**
- **ESLint: yes, directives are validated independent of rules.** Ran: `rules: {}`, `linterOptions.reportUnusedDisableDirectives: "error"`, file with `// eslint-disable-next-line no-undef` gave `Unused eslint-disable directive`. The file is read because a config object matches its language; zero rules is irrelevant. (Config: `lib/linter/linter.js` L365-407.)
- **Ruff: it is an ordinary rule.** `RUF100` is gated on `iter_enabled_rules().any(|r| r.lint_source().is_noqa())` (`crates/ruff_linter/src/linter.rs` L333-337). Ran `--select RUF100` on `x = 1  # noqa: E501`: "Unused noqa directive (non-enabled: E501)". `--select ""` is an error ("Unknown rule selector"), so there is no "zero rules" state; with RUF100 the only rule, files are still parsed.
- **Pylint:** `useless-suppression` (I0021) is a normal, off-by-default message; no separate always-on path found.
- **Biome/oxlint:** not verified here for the no-rules case.
- No peer found that reads files solely to validate directives with nothing else selected, except ESLint where the language is selected by config, not by rules. Closest design: directives are tied to "file is selected for its language", never to "rule is enabled".

**5. Per-file ignores vs cross-file input.**
- **Pylint removes input in both ways.** `ignore-paths`/`ignore` drop the file before any checker (`expand_modules` marks `isignored`, `_iterate_file_descrs` skips it, `pylinter.py` ~L1039). Ran: `--ignore-paths='.*b[.]py'` removed `b.py` from duplicate-code input even when passed as an explicit argument (a `\.` in the pattern did not work; the option help says backslash is treated as the path separator). A file-level `# pylint: disable=duplicate-code` also removes the file: `LineSet`/`stripped_lines` take `line_enabled_callback=linter._is_one_message_enabled` and skip every line where `R0801` is disabled (`symilar.py` L577, L633-637; ran: file with the pragma on line 1 produced no report). Issue #214 (2014, closed): "duplicate-code can't be disabled" for ranges, which is where the per-line removal came from; users still report the limitation on partial overlaps.
- **Biome: top-level `files.includes` removes from the index; `overrides`/`linter.includes` only filter diagnostics.** `is_ignored_by_scanner` returns true for top-level-ignored paths; `get_file_features` checks per-feature ignores separately. But `ProjectScanComputer` also looks at `overrides` to decide whether to scan, so an override enabling a project rule forces a global scan. A dependency of an indexed file is accepted unless already indexed (the `IndexRequestKind::Dependency` branch does not re-check ignores), so an ignored file can still enter the graph through an import.
- **oxlint:** ignore is applied in the walker only; `runtime.rs` has no ignore handling, so (inference from the code and the `content: None` doc comment) an ignored file imported by a linted one is still read for the graph, not linted.
- **Ruff:** `per-file-ignores` is a compiled per-path rule filter in the linter settings (`LinterSettings.per_file_ignores`); output filter. Ruff has no cross-file rules, so the question does not arise. `exclude` removes the file; explicit paths bypass it unless `force-exclude` (`check.rs` L94-100).
- **ESLint:** `ignores` removes the file from linting; no cross-file rules.
- **jscpd:** `ignore` globs applied during the walk; the file never reaches tokenizing (input removal). In-file `jscpd:ignore-start/end` pragmas exist (MegaLinter descriptor links them).
- **Wrapper forwarding (MegaLinter):** has to push its own `EXCLUDED_DIRECTORIES` and gitignored dirs into each project-mode tool (`cli_lint_mode_project_exclude_*` in `megalinter/Linter.py` L160-192). For jscpd, passing `--ignore` on the CLI would replace the config's ignore list wholesale, so `JsCpdLinter.manage_excluded_directories_config` merges globs into a generated config instead. Evidence that "wrapper owns exclusion, tool re-applies it" is fragile and per-tool.

## Patterns Found

### Pattern 1: Retain per-file content for the whole run, reduce at the end
**Used by**: Pylint `similar`, jscpd (tokens), Biome (`Project` scan).
**How it works**: Each file's loaded form is appended to a run-wide list/db; the project check runs after the last file. Parallel mode in Pylint ships those same objects back to the main process as map data.
**Strengths**: simplest; rule gets everything; no summary protocol.
**Weaknesses**: memory grows with repo size; Pylint users hit OOM on cpython-sized trees until a 2026 fix; cost can be paid when the rule is not wanted (issue 3443).
**Example**: https://github.com/pylint-dev/pylint/blob/main/pylint/checkers/symilar.py
**Read**: Pylint `symilar.py`, `parallel.py`, PR 10881; Biome `workspace/server.rs open_file_internal`; jscpd `cpd-core/src/models.rs`, `detect.rs`.

### Pattern 2: Keep a derived graph/summary, drop source after its group is processed
**Used by**: oxlint, elm-review, Biome (module graph holds resolved imports; text kept separately).
**How it works**: Build a compact per-file record (module record, summary context); lint files in bounded groups and release source/AST between groups; project rules read only the records.
**Strengths**: bounded peak memory; fits import-graph style rules.
**Weaknesses**: needs a summary schema per rule class, ordering heuristics (oxlint sorts deepest paths first); does not fit duplicate detection, which needs content or tokens.
**Example**: https://github.com/oxc-project/oxc/blob/main/crates/oxc_linter/src/service/runtime.rs
**Read**: oxlint `runtime.rs`; elm-review is secondary: blog only.

### Pattern 3: Paths only; the external tool re-discovers files
**Used by**: MegaLinter (jscpd in `project` mode), pre-commit (`pass_filenames: false`), Semgrep Pro/CI, qlty (own engine, whole repo).
**How it works**: The orchestrator tells the tool where to run; the tool walks, filters and parses itself. Selection and excludes must be forwarded or duplicated.
**Strengths**: no content in the orchestrator; zero coupling.
**Weaknesses**: two ignore systems that must be reconciled (MegaLinter builds a generated config because jscpd `--ignore` replaces config ignores wholesale); tool's language detection differs from the host's; explicit-path semantics differ per tool.
**Example**: https://github.com/oxsecurity/megalinter (`megalinter/linters/JsCpdLinter.py`)
**Read**: MegaLinter `Linter.py`, `JsCpdLinter.py`, copypaste descriptor; pre-commit `run.py`; Semgrep and qlty are secondary: docs.

### Pattern 4: Scan scope derived from enabled rules
**Used by**: Biome (`ProjectScanComputer`), oxlint (`has_import()` toggles cross-module), pre-commit (hook skipped when no files match), Pylint (checker only runs when a message is enabled).
**How it works**: A function over the resolved config picks the minimum loading mode. Disabling the project rules returns to the cheap mode.
**Strengths**: a user who disables the rule pays nothing; matches an opt-in phase.
**Weaknesses**: the computation must include overrides and CLI selectors (Biome's does, with `with_rule_selectors`); easy to under-compute.
**Example**: https://github.com/biomejs/biome/blob/main/crates/biome_service/src/configuration.rs
**Read**: Biome `configuration.rs`; oxlint `apps/oxlint/src/lint.rs` L398; Pylint `pylinter.py prepare_checkers`; pre-commit `run.py`.

### Pattern 5: Primary location plus related locations, N-ary as note text
**Used by**: LSP, SARIF, Ruff, Biome, jscpd SARIF; Pylint puts everything in message text.
**How it works**: One diagnostic is anchored at one location (so sorting, suppression and baselines work per location); the rest are structured related spans or free text. jscpd emits pairs, so groups of 3 become several diagnostics.
**Strengths**: fits existing `file:line` reporters and suppression by line.
**Weaknesses**: choosing the primary is arbitrary; Pylint's no-node message lands on an unrelated file (ran: reported on `c.py`), which breaks per-file ignore and baseline matching.
**Example**: https://github.com/astral-sh/ruff/blob/main/crates/ruff_db/src/diagnostic/mod.rs
**Read**: Ruff `ruff_db/src/diagnostic/mod.rs`; Biome `biome_analyze/src/rule.rs`; jscpd `sarif.rs`; Pylint ran; LSP and SARIF specs read; PMD docs read.

### Pattern 6: Input-level ignore (file leaves the phase) vs output-level ignore
**Used by**: input-level: Pylint (`ignore-paths`, per-file `disable=` removes lines), jscpd (`ignore` at walk), Biome top-level `files.includes`. Output-level: Ruff `per-file-ignores`, Biome `overrides`/`linter.includes`, ESLint rule-level `ignores`.
**How it works**: Input-level means a duplicate partner in an ignored file is not found; output-level means the partner is still compared and only reported diagnostics are filtered.
**Strengths**: input-level matches "this file is not part of the analysis"; output-level lets a file be a reference for others.
**Weaknesses**: graph-style tools leak ignored files back in as dependencies (Biome `Dependency` branch; oxlint inferred).
**Example**: https://github.com/pylint-dev/pylint/blob/main/pylint/checkers/symilar.py (L633-637)
**Read**: Pylint (source + ran), jscpd `walker.rs`, Biome `server.rs is_ignored_by_scanner`, Ruff `settings/mod.rs` (struct only; no cross-file rules), oxlint (inference).

### Pattern 7: Accepting a limitation by documentation
**Used by**: ESLint ("rules only run in the context of one file", discussion 18676); Semgrep CE (single-function by design, cross-file is Pro-only and skipped on diff scans); Pylint docstring ("may be memory / CPU intensive, so you should disable it").
**How it works**: the tool keeps the per-file core and states the limit rather than build project infrastructure.
**Strengths**: core stays fast.
**Weaknesses**: users reach for plugins that rebuild the module graph themselves (oxlint docs cite `eslint-plugin-import` taking tens of seconds).
**Example**: https://github.com/eslint/eslint/discussions/18676
**Read**: ESLint discussion (read); Semgrep docs (secondary); Pylint docstring (read in source).

## Anti-Patterns

- **Anchoring a project diagnostic to an unrelated file.** Pylint R0801 attaches to the last-processed module with locations in text; reproduced: `c.py:1:0 R0801 ... ==a:[0:12] ==b:[0:12]` for a duplicate between `a.py` and `b.py`. Per-location suppression and file-scoped reporting cannot work on it.
- **Paying the project phase's cost when it is disabled or unrequested.** Pylint issue 3443 (an hour on 500k LOC despite similarity being disabled, tied to `reports=yes`); Biome and oxlint avoid it by deriving scan mode from enabled rules.
- **Wrapper overrides replacing tool config.** jscpd `--ignore` replaces config ignores; MegaLinter had to merge into a generated config (`JsCpdLinter.manage_excluded_directories_config`).
- **Silent drop of explicit paths.** Ruff silently ignores an explicit non-Python path; ESLint warns; Biome emits a VERBOSE-tagged warning unless `ignoreUnknown`. The first gives the user no signal.

## Emerging Trends

- Rust-native duplicate engines replacing the wrapped JS tool: jscpd v5 is now a Rust binary (npm per-platform packages; v4 TypeScript maintained on `master-v4`), qlty has its own tree-sitter engine. A wrapper pinned to the jscpd CLI should pin the major and expect the report shape (`firstFile`/`secondFile`, SARIF) to be the stable surface.
- Bounded-memory group processing in oxlint is the one deliberate design for large repos.

## Gaps

- Biome and oxlint behavior of unused-suppression reporting with zero enabled rules: not tested (read code paths for file selection only).
- oxlint: whether an ignored file imported by a linted file is read for the graph is inferred from `runtime.rs` having no ignore handling and the `content: None` doc comment; not run.
- Biome memory: confirmed text is kept for scanned files and per-file size limit exists, but did not find the default `maxSize` value or any issue discussing project-scan memory.
- Semgrep Pro and qlty: closed or docs-only; marked secondary above. typescript-eslint: docs only.
- Pylint: where exactly the R0801 header location is chosen (last file processed) was observed by running, not traced in `add_message`.
- Did not read Ruff's `per-file-ignores` application site beyond the settings struct; irrelevant to cross-file input since Ruff has none.

## Relevance to Us

- House-lint's bytes are needed by the wrapped tool's neighbours, not the tool: jscpd reads real paths (parent D9), and #60's pragma resolution reads the retained bytes (parent D14). Retaining `LoadedFile`s only while a project rule is enabled matches Patterns 1 and 4.
- Nothing peers do justifies a run-wide memory cap. The existing per-file 10 MiB limit matches Biome's `maxSize`.
- Parent D12's one finding per occurrence sidesteps primary-picking, so the internal candidate can stay symmetric until #60 builds `related`.
- No peer lets a directive validator pull a language into a scan, which argues against HSL900 declaring languages that drive discovery.

## Recommendation

Retain only when a project rule is enabled, with no new cap. Use a symmetric location group internally. Derive discovery's languages from enabled ordinary rules. Treat per-file-ignores as input removal, as parent D21 ratified. Decisions are in `design/specs/010-*/design.md`.

## Sources

### Pylint symilar.py and parallel.py
- **URL**: https://github.com/pylint-dev/pylint/blob/main/pylint/checkers/symilar.py
- **Type**: reference implementation
- **Opened**: yes (clone 6909288b2b, plus ran pylint 4.1.3)
- **Key takeaway**: Per-file `LineSet` (full lines) retained for the run, N-ary result as message text, per-file disable and `ignore-paths` both remove input.
- **Relevance**: closest analogue of a native cross-file rule living next to per-file rules.

### Pylint PR 10881 (duplicate-code quadratic time and memory)
- **URL**: https://github.com/pylint-dev/pylint/pull/10881
- **Type**: PR with benchmark table
- **Opened**: yes (via `gh api`)
- **Key takeaway**: retention of all files is the known cost; mitigated by reuse of parsed AST and a rolling hash, not by bounding.
- **Relevance**: evidence that this peer accepted whole-run retention and fixed constants instead.

### Pylint issue 214 and 3443
- **URL**: https://github.com/pylint-dev/pylint/issues/214 ; https://github.com/pylint-dev/pylint/issues/3443
- **Type**: issue threads
- **Opened**: yes
- **Key takeaway**: 214: cannot disable duplicate-code for part of a span (open for years, now closed with workarounds). 3443: similarity cost was paid even when disabled via `reports=yes` (fixed by checker activation rules).
- **Relevance**: shows pitfalls of coupling the project checker's cost to report settings and of per-region suppression.

### oxlint runtime.rs
- **URL**: https://github.com/oxc-project/oxc/blob/main/crates/oxc_linter/src/service/runtime.rs
- **Type**: reference implementation
- **Opened**: yes (f8982585aa)
- **Key takeaway**: keep module records, release source and semantic per group; group size and sort order chosen to bound memory.
- **Relevance**: the only peer whose code discusses bounding retention.

### oxlint multi-file analysis docs
- **URL**: https://oxc.rs/docs/guide/usage/linter/multi-file-analysis
- **Type**: documentation
- **Opened**: yes
- **Key takeaway**: cross-module mode is opt-in by enabling the `import` plugin and at least one `import/*` rule.
- **Relevance**: opt-in phase triggered by rule selection.

### Biome configuration.rs / scanner.rs / server.rs
- **URL**: https://github.com/biomejs/biome/blob/main/crates/biome_service/src/configuration.rs
- **Type**: reference implementation
- **Opened**: yes (a00eb5a222)
- **Key takeaway**: `ProjectScanComputer` picks the cheapest `ScanKind` that the enabled rules need; file text kept in a db for indexed files.
- **Relevance**: model for deriving file loading from enabled rules.

### Ruff resolver.rs, check.rs, diagnostics.rs, ruff_db Diagnostic
- **URL**: https://github.com/astral-sh/ruff/blob/main/crates/ruff_workspace/src/resolver.rs
- **Type**: reference implementation
- **Opened**: yes (f10755306e; also ran ruff)
- **Key takeaway**: file selection by extension, explicit unhandled paths dropped silently; diagnostic with primary and secondary annotations plus sub-diagnostics.
- **Relevance**: Q2, Q3, Q4 behavior.

### ESLint eslint-helpers.js and discussions
- **URL**: https://github.com/eslint/eslint/discussions/18676 ; https://github.com/eslint/eslint/issues/19361
- **Type**: code + maintainer statements
- **Opened**: yes (896b8ad4f2; ran eslint 9.39)
- **Key takeaway**: single-file rules by design; unmatched explicit file gets a warning result; unused-directive reporting works with zero rules.
- **Relevance**: Q3, Q4.

### jscpd (Rust v5) cpd-core, cpd-finder, cpd-reporter
- **URL**: https://github.com/kucherenko/jscpd/tree/master/rust/crates
- **Type**: reference implementation
- **Opened**: yes (0b519608c4)
- **Key takeaway**: pairwise clones, SARIF primary plus relatedLocations, ignore at walk time, format from extension. v4 (TypeScript) lives on `master-v4`.
- **Relevance**: the wrapped tool; JSON shape `firstFile`/`secondFile` is the contract a wrapper must adapt.

### MegaLinter Linter.py, JsCpdLinter.py, copypaste descriptor
- **URL**: https://github.com/oxsecurity/megalinter
- **Type**: reference implementation
- **Opened**: yes (2017d86b1b)
- **Key takeaway**: project-mode wrapper passes a path and re-forwards its excludes into the tool's own config.
- **Relevance**: "paths only" wrapper and its exclusion-forwarding cost.

### pre-commit run.py and hook docs
- **URL**: https://pre-commit.com/ ; https://github.com/pre-commit/pre-commit/blob/main/pre_commit/commands/run.py
- **Type**: reference implementation + docs
- **Opened**: yes
- **Key takeaway**: `pass_filenames: false` plus `always_run` is the sanctioned shape for whole-repo hooks; with no matching files and no `always_run` the hook is skipped.
- **Relevance**: a project phase on staged-file runs.

### LSP 3.17 and SARIF 2.1.0 specs
- **URL**: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/ ; https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/sarif-v2.1.0-errata01-os-complete.html
- **Type**: standard
- **Opened**: yes (relevant sections)
- **Key takeaway**: both model "primary plus related" with a message per related location.
- **Relevance**: Q2 external contract if house-lint ever emits SARIF/LSP.

### PMD CPD report formats
- **URL**: https://docs.pmd-code.org/latest/pmd_userdocs_cpd_report_formats.html
- **Type**: documentation
- **Opened**: yes
- **Key takeaway**: N-ary duplication group, symmetric.
- **Relevance**: Q2 alternative shape.

### Implementing multi-file analysis for linters (elm-review)
- **URL**: https://jfmengels.net/multi-file-analysis/
- **Type**: blog post by tool author
- **Opened**: yes
- **Key takeaway**: per-file summary extraction and folding, each module sees only its dependencies' summaries.
- **Relevance**: summary-based alternative to retaining content.

### Semgrep cross-file docs; qlty duplication docs; typescript-eslint custom rules
- **URL**: https://docs.semgrep.dev/semgrep-code/semgrep-pro-engine-intro ; https://docs.qlty.sh/duplication ; https://typescript-eslint.io/developers/custom-rules
- **Type**: documentation
- **Opened**: yes (docs text only, no source)
- **Key takeaway**: see Answers 1.
- **Relevance**: secondary confirmation.


Clone SHAs read: pylint 6909288b2b, ruff f10755306e, biome a00eb5a222, oxc f8982585aa, jscpd 0b519608c4, eslint 896b8ad4f2, megalinter 2017d86b1b, pre-commit 8cb5845c83.
