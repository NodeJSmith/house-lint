# Design: TypeScript analysis and suppression pragmas

**Date:** 2026-11-09
**Status:** abandoned
**Mode:** sketch
**Issues:** #61
**Parent:** design/specs/005-cross-file-rules-and-duplicate-code/design.md
**Prior art:** design/research/2026-11-09-suppression-directive-scope/prior-art.md

## Summary

Only Python has an analysis layer today. A TypeScript file is loaded, handed to any project rule that asked for it, and counted, and nothing else happens to it:
- `pipeline.run_file_phase` skips non-Python files before the cache lookup ("Only Python has an analysis layer").
- `pipeline.PragmaClaims.claims` returns False for them, so a project-rule occurrence in a `.ts`/`.tsx` file can never be suppressed.
- `suppressions.py` reads pragmas from Python `tokenize` comments and owns them through `ast` statements. Its `scan_pragmas` rejects any rule that doesn't handle Python.

This change adds a TypeScript analysis layer on tree-sitter. With it, `.ts` and `.tsx` files get `house-lint:` pragmas that HSL900 validates in the file phase and that can suppress project-rule occurrences in the project phase. It is the parent's Breakdown item 6. No TypeScript rule ships here. `HSL104` is #39, so the behaviour is pinned with a stand-in TypeScript project rule (`tests/conftest.py`'s `register_project_rules`), as #60 did.

**In scope:**
- tree-sitter and its TypeScript grammar as core dependencies
- the TypeScript analysis layer
- TypeScript pragma placement and ownership
- HSL900 for TypeScript files, in the file phase and cached
- project-rule suppression in TypeScript files
- docs

**Out of scope:**
- any TypeScript rule (#39)
- the pre-commit hook's file filter (spec 010 KI-001, owned by #39, since nothing brings TypeScript into a scan until a TypeScript rule exists)
- JavaScript
- an unused verdict for project-rule pragmas (spec 010 D9)

**New homes:**
- `typescript_source.py`, beside `python_source.py`. It holds `parse_typescript(loaded) -> TypeScriptSource | ParseFailure`, which picks the `typescript` or `tsx` grammar by suffix, decodes (D7), parses, and records whether the tree has errors (D5). `TypeScriptSource` carries the path, the lines, the tree's comment nodes, and that flag, and exposes them as language-neutral pragma sites (D6).
- Language dispatch in `scanner.scan_source`, which today calls `parse_python` on every file: Python parses and runs detectors and suppressions as now; TypeScript parses and runs suppressions only.
- `pipeline.run_file_phase` drops its non-Python skip, so every file whose language has an analysis layer is cached. Only a language with no analysis layer (none today) is skipped.
- `pipeline.PragmaClaims` parses a retained `LoadedFile` with the layer for its language.
- Pragma handling, split per D6.
- The grammar's identity in the per-file cache key (D4): `cache.hash_effective_config`, beside `python_version`.

**Must hold in the build:**
- Python findings, suppression, and every HSL900 result are unchanged, cached or not.
- Each scanned file is read exactly once. The project phase parses the retained `LoadedFile`, never the path.
- Nothing from the project phase is cached (parent D11).
- A Python-only project discovers no TypeScript files (parent D6, spec 010 D5) and never parses TypeScript.

## Posture

- **Failure handling:** fail loudly, carried from the parent. A TypeScript file with syntax errors keeps its project-rule findings and loses its pragmas, with one HSL900 only if it contains a pragma (parent D15). No retries, no fallback parser.
- **Compatibility:** break freely (parent, `CLAUDE.md`). This change is additive: a new pragma form in `.ts`/`.tsx` files. Python behaviour doesn't change, so the PR is `feat:`.
- **Scope:** minimal. It covers the TypeScript analysis layer, TypeScript pragmas with HSL900 validation, and suppressing project-rule occurrences in TypeScript. It includes no TypeScript rule and doesn't change the hook filter (both #39). Behaviour is pinned with a stand-in TypeScript project rule.
- **Rollout:** ships directly.
- **Hardening:** project defaults. A tree-sitter parse per TypeScript file per cache miss, and per file with a project finding.
- **Altitude:** `build`. One session builds it, and the ledger cites files.

## Decisions

### D1: How does a TypeScript pragma decide what it owns?

**Situation:** Python pragmas use statement ownership:
- A trailing `ignore` owns its innermost enclosing statement (`analysis.statement_owner_for_line`).
- A comment-only `ignore-next` owns the next statement in its block (`suppressions._next_owner`).
- A project-rule occurrence is claimed when the innermost statement around its start line (`analysis.occurrence_owner`) equals the pragma's owner (spec 011 D5).

Parent D15 ratified the same for TypeScript, through a per-grammar table of owner node kinds: statements, class members, object-literal methods, JSX elements, decorators, and `export` wrappers. Tree-sitter has no "statement" concept, so that table is new, grammar-specific code. The 2026-10-30 engine brief flagged that peers' directives might be line-scoped and left this pickup to check. The 2026-11-09 brief confirms it: ESLint, Biome, oxlint, Deno lint, Semgrep, ast-grep, and Ruff all scope a directive to a line and match a diagnostic on its start. Pylint is the only peer with AST scoping, and only for def, class, and module blocks. None has a per-grammar owner table. The one recurring complaint about line scoping is a multi-line JSX element whose diagnostic starts on an interior line (biomejs/biome#9469). react-doctor answered it by node-scoping JSX elements only.

Under line scoping, as option A defines it:
- A trailing `ignore` owns its own line.
- A comment-only `ignore-next` owns the next line that isn't blank or comment-only.
- `ignore-file` must come before the first non-comment token. A `#!` line may precede it.
- An occurrence is claimed when its start line equals the pragma's owned line.
- Misplacement keeps its HSL900 messages: a trailing `ignore` with no code before it, an `ignore-next` with code before it or no code line after it, and an `ignore-file` after the first token.

**Deciding factor:** a TypeScript pragma placed where a finding visibly starts suppresses it, with the least grammar-specific code.

| | A: Line ownership, matched on the occurrence's start line | B: Statement ownership via per-grammar owner node kinds (parent D15) | C: A, plus an `ignore-next` directly above a JSX element owning the whole element |
|---|---|---|---|
| Pragma on the line a finding starts (trailing) or the line above it (`ignore-next`) | Suppresses it | Suppresses it | Suppresses it |
| Occurrence starting on a continuation line of a multi-line statement | Pragma must sit on that line | Owned by the statement; a pragma on its first line claims it | Same as A, except within JSX |
| Occurrence starting on an interior line of a multi-line JSX element | Pragma must sit on that line, as a `{/* */}` comment (D3) | Owned by the innermost JSX element in the table | `ignore-next` above the element claims it |
| A pragma on a function or class line hiding occurrences inside its body | Never | Never (equality, not containment) | Never, outside JSX |
| Same rule as Python | No: Python stays statement-owned; docs explain both | Yes | No |
| Grammar-specific code | None beyond finding comments | An owner-kind table per grammar (`typescript`, `tsx`), plus decorator and `export` wrapper handling | One node kind (`jsx_element`) |
| Behaviour in a file with syntax errors | Moot: D5 disables pragmas there | Moot | Moot |
| Peers | ESLint, Biome, oxlint, Deno lint, Semgrep, ast-grep, Ruff | None (Pylint has AST blocks for def, class, and module only) | react-doctor |

**Recommendation:** A. It is the peer norm, and the finding's own reported line is where the pragma goes, which text and JSON output already print. B's owner table exists only to make TypeScript match Python's statement model, and no peer carries one. It also has to be kept correct across two grammars that lag the language. C fixes the one complaint peers report, but nothing has measured whether jscpd duplicates in TSX start on interior JSX lines often enough to need it. It can be added later without changing A's behaviour. Supersedes parent D15's statement ownership and owner node kinds. D15's use of tree-sitter, its pragma grammar, and its parse-error handling stand.
**Pick B instead if** TypeScript and Python pragmas must behave identically, accepting a per-grammar owner table no peer has. **Pick C instead if** you expect duplicate JSX blocks that start mid-element and would rather mark them once than per line.
**Reversibility:** easy (C is A plus one node kind; B replaces the owner lookup, not the pragma format)
**Ratified:** Chose line ownership matched on the occurrence's start line over parent D15's per-grammar statement ownership and over adding JSX element scope, so a TypeScript pragma works where the finding visibly starts with no grammar-specific owner table, as every surveyed peer does, accepting that TypeScript and Python pragmas own differently and that an occurrence starting mid-statement or mid-JSX needs its pragma on that exact line.

### D2: Which package provides the TypeScript grammar?

**Situation:** Parent D15 said the grammar "must come from an actively maintained, pinned package, such as `tree-sitter-language-pack` or `ast-grep-py`", because `tree-sitter-typescript` was last released in 2024-11, and left the choice to this issue by install size. Measured 2026-11-09:
- `tree-sitter-language-pack` 1.25.0 is a 2.4 MB wheel that downloads grammar bundles from a remote manifest into a cache directory at runtime. It exports `download`, `cache_dir`, and `DownloadManager`, and its wheel holds no grammars.
- `ast-grep-py` 0.51.1 is a 5.7 MB wheel per Python version. It bundles `tree-sitter-typescript` 0.23.2 (ast-grep's `crates/language/Cargo.toml`), the same grammar. Its API takes `str`, not bytes. It exposes comment and `ERROR` nodes but has no `has_error` or `is_missing`, so a probe of `const q = (1 + 2;` (a MISSING `)`) reported no error.
- `tree-sitter` 0.26.1 (0.7 MB) plus `tree-sitter-typescript` 0.23.2 (0.3 MB, abi3) has wheels for manylinux, musllinux, macOS, and Windows, and sdists. It exposes `has_error`, `is_missing`, byte positions, and comment nodes. Probes found comments correctly around string, regex, and template literals, and inside JSX, and still found comments in a file with `ERROR` nodes.

**Deciding factor:** the scan never needs the network, and syntax errors are detected completely (D5).

| | A: `tree-sitter` plus `tree-sitter-typescript==0.23.2`, pinned exactly | B: `ast-grep-py` | C: `tree-sitter-language-pack` |
|---|---|---|---|
| Grammar | 0.23.2 | 0.23.2 (bundled) | Whatever its manifest serves |
| Network at scan time | Never | Never | First use of each grammar, and after a cache clean |
| Detects MISSING-node syntax errors | Yes (`has_error`) | No | Yes |
| Install size | ~1 MB | ~5.7 MB | 2.4 MB plus downloaded bundles |
| API fit | Bytes in, byte positions out, matching `LoadedFile.content` | `str` only: decode first, then map columns | Its own wrapper API |
| Grammar freshness | Frozen at 2024-11 until house-lint bumps the pin | Same grammar; moves when ast-grep bumps it | Moves on its own schedule, outside the cache key |

**Recommendation:** A. The "maintained package" premise in D15 doesn't hold. B ships the same 0.23.2 grammar with a weaker API, and C fetches grammars over the network at run time, which a pre-commit hook can't rely on. A pins the exact grammar, so the cache key (D4) can name it. A grammar too old for new syntax shows up as D5's syntax-error handling, which parent D15 already accepts. This supersedes parent D15's package choice. D20's core dependency and module-level import stand.
**Pick B instead if** a fresher grammar matters and ast-grep starts shipping one, accepting the missed MISSING-node errors. **Pick C instead if** network access at scan time is acceptable.
**Reversibility:** easy (one dependency and one module)
**Ratified:** Chose `tree-sitter` plus an exactly pinned `tree-sitter-typescript==0.23.2` over `ast-grep-py` and `tree-sitter-language-pack`, so scans never touch the network and every syntax error, MISSING nodes included, is detected, accepting a grammar frozen at 2024-11 until house-lint bumps the pin.

### D3: Which TypeScript comment forms can hold a pragma?

**Situation:** Python has one comment form. TypeScript has `// line` comments and `/* block */` comments. Inside JSX children, `//` is plain text (`jsx_text` in the probe), so the only comment form there is `{/* ... */}`, which parses as a `comment` inside a `jsx_expression`. Under D1 A, a pragma on an interior JSX line can only be written that way.

**Deciding factor:** every line a finding can start on can carry a pragma, with no second pragma grammar.

| | A: `//` comments and single-line `/* */` comments; a JSX line holding only `{/* pragma */}` counts as comment-only for `ignore-next` | B: `//` only | C: `//` and `/* */`, with multi-line block pragmas allowed |
|---|---|---|---|
| Suppress a finding starting inside JSX children | Yes, `{/* house-lint: ignore-next[...] - ... */}` on the line above, or trailing on the same line | No: only `ignore-file` reaches it | Yes |
| Pragma text grammar | One, wrapped in `//` or `/* */` | One | One, but its reason can span lines |
| A `/* house-lint: ... */` spanning lines | HSL900 "malformed suppression pragma" | Not a pragma | Accepted |
| A `/* house-lint: ... */` with no `*/` on the line | n/a, the comment node includes it | n/a | n/a |
| Peers | ESLint and Biome accept both forms, and document `{/* */}` for JSX | Deno lint (`//` only) | ESLint (`/* eslint-disable */` blocks span lines) |

**Recommendation:** A. A TSX duplicate starting inside JSX is a likely `HSL104` case, and B would leave only `ignore-file` for it. Keeping block pragmas on one line keeps one shape and one reason format for every pragma.
**Pick B instead if** you'd accept `ignore-file` as the only answer for JSX until a real case shows up. **Pick C instead if** long reasons wrapped across lines matter.
**Reversibility:** easy
**Ratified:** Chose `//` comments and single-line `/* */` comments, with a JSX line holding only `{/* pragma */}` counting as comment-only, over `//` alone or multi-line block pragmas, so a finding starting inside JSX can carry its own pragma with one pragma shape, accepting that a block pragma spanning lines is reported as malformed.

### D4: Where do TypeScript pragmas get validated, and how is a cached result kept honest across a grammar change?

**Situation:** Spec 010 D5 ratified that HSL900 validates every scanned file whose language has an analysis layer. Once TypeScript has one, its pragmas get HSL900's structural checks: malformed, misplaced, unknown rule, wrong language, and disabled rule. The file phase is where those run for Python. Its results are cached per file, keyed on content and `cache.hash_effective_config`. That hash includes `python_version` because the accepted grammar differs between interpreters. Two things decide a TypeScript result, and neither is in the key today:
- **The grammar version**, which decides where `ERROR` nodes fall and therefore D5's verdict. It isn't in the key.
- **house-lint's own code**, which is in `cache.code_identity()`. But that hashes only house-lint's `*.py` sources, so a dependency bump with no source change keeps the namespace.

**Deciding factor:** a cached TypeScript result can never outlive the grammar that produced it, through the same cache path every language uses.

| | A: File phase, cached; the key gains the `tree-sitter` and `tree-sitter-typescript` versions for TypeScript files | B: File phase, never cached for TypeScript | C: Project phase only, when a project rule's input includes the file |
|---|---|---|---|
| HSL900 coverage | Every scanned TypeScript file (spec 010 D5) | Same | Only files in some project rule's input; a per-file-ignored file goes unvalidated |
| Stale result after a grammar upgrade | Impossible: the version is in the key | Impossible | Impossible |
| Cost per run | A parse per changed file | A parse per TypeScript file per run (tree-sitter is fast) | A parse per file in a project input |
| Cache path | One path for every language with an analysis layer | A language branch around lookup and store | Breaks spec 010 D5 |
| A future per-file TypeScript rule | Cached like Python | Needs A then | Needs A then |
| Peers | Ruff keys its cache on its own version, and pins its parser in-tree | unknown | unknown |

**Recommendation:** A. It keeps one file-phase path and needs only one more key input, read from the installed distributions' versions. Python files keep their current key, so their cached entries stay valid. B saves a key input but adds a branch every future TypeScript rule would have to remove. C contradicts spec 010 D5.
**Pick B instead if** you'd rather keep the cache Python-only until a TypeScript file rule exists.
**Reversibility:** easy
**Ratified:** Chose validating TypeScript pragmas in the file phase and caching the result, with the `tree-sitter` and `tree-sitter-typescript` versions in TypeScript files' cache key, over never caching TypeScript or validating only in the project phase, so every language with an analysis layer takes one cached path and no result outlives its grammar, accepting one more key input.

### D5: In a TypeScript file with syntax errors, what counts as a syntax error and as containing a pragma?

**Situation:** Parent D15 settled the behaviour. Such a file keeps its project-rule findings and has its pragmas disabled, and it is not a file error. One HSL900 is emitted only when the file contains a house-lint pragma, because HSL900 fails the run. Two definitions are left open:
- **What counts as a syntax error.** tree-sitter marks an unparsable span with an `ERROR` node, and a token it inserted to recover (a missing `)`) as a zero-width MISSING node. `has_error` covers both.
- **What counts as "contains a pragma".** Parent D14 planned a byte search for `house-lint:`, which spec 010 D9 dropped along with the stale-pragma check. A byte search also matches string literals, so a string like `"house-lint: ignore[...]"` would fail the run in a file with a syntax error. The probe showed that tree-sitter still yields comment nodes in a file with `ERROR` nodes.

**Deciding factor:** HSL900 fires only for a pragma that actually stopped working, never for text that merely looks like one.

| | A: `has_error` (ERROR or MISSING) disables pragmas; "contains a pragma" means a comment node whose text starts with `house-lint:` (after `//` or `/*` and spaces) | B: Same syntax-error test; "contains a pragma" is a byte search for `house-lint:` | C: Only `ERROR` nodes disable pragmas; MISSING-only files keep them |
|---|---|---|---|
| String literal containing `house-lint:` in a file with errors | No diagnostic | HSL900, failing the run | No diagnostic |
| Pragma comment inside an `ERROR` span | Counted, so it gets the diagnostic | Counted | Counted |
| A file whose only defect is a missing token | Pragmas disabled | Pragmas disabled | Pragmas honoured on a tree that recovered by inventing a token |
| Where the one diagnostic points | The first pragma comment | The first byte match | The first pragma comment |

**Recommendation:** A. Comment nodes are what D15 adopted tree-sitter for, and they still exist in broken files. MISSING nodes mean the tree guessed at the code, and owner lines (D1) on a guessed tree aren't trustworthy. The message names the cause, for example `pragmas disabled: TypeScript syntax error at line N`, so a user can tell an old grammar from a typo.
**Pick B instead if** you want a pragma inside a region the parser mangled into a non-comment still to be caught, accepting false failures from strings. **Pick C instead if** MISSING-only files are common in practice and you'd rather keep their pragmas.
**Reversibility:** easy
**Ratified:** Chose tree-sitter's `has_error` (ERROR or MISSING nodes) as the syntax-error test and comment nodes starting with `house-lint:` as "contains a pragma" over a byte search or ignoring MISSING nodes, so the one HSL900 fires only for a real pragma that stopped working and never for a string literal, accepting that a file whose only defect is a missing token loses its pragmas.

### D6: Where do the language-specific parts of pragma handling live?

**Situation:** `suppressions.py` (434 lines) mixes two kinds of code:
- **Language-neutral:** the pragma text grammar (`_PRAGMA` without its `#`), the ID, duplicate, reason, and HSL900-self checks (`_parse_pragma`), the rule checks (unknown, wrong language, disabled), file-rule claims, conflicts, unused verdicts, and `claims_for`'s ignore-file and owner matching.
- **Python-specific:** finding pragma comments in `tokenize` tokens, and ownership through `ast`. That covers `_trailing_owner`, `_next_owner`, `_suites`, `_comments_or_blanks_only`, `_is_file_prologue`, and `claims_for`'s call to `analysis.occurrence_owner`.

TypeScript needs the first kind unchanged and its own version of the second. Its owners are lines (D1), not `StatementKey`s.

**Deciding factor:** every HSL900 rule check lives in exactly one place for both languages.

| | A: `suppressions.py` keeps the neutral engine over a per-language list of pragma sites (text, position, owner, or a placement error) plus an occurrence-owner function; Python's site finder moves to a `python_pragmas.py` and TypeScript's goes in `typescript_source.py` | B: A new `typescript_suppressions.py` with its own scan, importing `_parse_pragma` and the rule checks from `suppressions.py` | C: `suppressions.py` branches on language inside `scan_pragmas` and `claims_for` |
|---|---|---|---|
| Rule checks and message text | One copy | One copy, called from two scans | One copy |
| Owner type | Opaque and hashable per language (`StatementKey` or a line) | Separate per module | A union branched on |
| `suppressions.py` imports `ast` and `tokenize` | No | Yes | Yes, plus tree-sitter |
| Python behaviour risk | A move, pinned by the existing suppression tests | None | Branches inside shared functions |
| Adding a third language | One site finder | A third scan module | A third branch in each function |

**Recommendation:** A. It gives the neutral engine one home and each language one site finder, which is the shape spec 009 set up with `python_source.py` beside a future `typescript_source.py`. The Python move is mechanical, and `tests/unit/test_suppressions.py` pins it. B duplicates the scan loop that spec 011 D1 made the single place a pragma is accepted. C grows two functions by a language branch each.
**Pick B instead if** you want this PR to leave every Python line in `suppressions.py` untouched. **Pick C instead if** you expect TypeScript to stay the only second language.
**Reversibility:** easy (internal modules)
**Ratified:** Chose a language-neutral engine in `suppressions.py` over per-language pragma sites, with Python's site finder moved to `python_pragmas.py` and TypeScript's in `typescript_source.py`, over a separate TypeScript scan module or language branches, so every HSL900 rule check has one home for both languages, accepting a mechanical move of Python's ownership code pinned by the existing suppression tests.

### D7: What happens to a TypeScript file that isn't valid UTF-8?

**Situation:** tree-sitter parses bytes. But HSL900's columns are character positions, and reporting a pragma's line needs the line's text, so the layer has to decode. Spec 009 D1 leaves decoding to each language layer. `python_source.parse_python` returns a `ParseFailure` (a cached file error) when a file won't decode. TypeScript has no encoding declaration. Files are UTF-8 by convention, and the compiler strips a BOM.

**Deciding factor:** a file house-lint can't read correctly is reported, not silently given wrong columns.

**Recommendation:** decode as UTF-8, stripping a leading BOM. A file that fails to decode is a `ParseFailure`, a file error, cached, as for Python. Project rules still receive its bytes (spec 010's Must-hold). The file error makes it visible, matching Python, and such files are rare.
**Pick "treat it like a syntax error (D5)" instead if** a stray non-UTF-8 TypeScript file shouldn't fail a hook.
**Reversibility:** easy
**Ratified:** Chose decoding TypeScript as UTF-8 with a leading BOM stripped, with a decode failure a cached file error as for Python, over treating it like a syntax error, so a file house-lint can't read correctly is reported rather than given wrong columns, accepting that a stray non-UTF-8 `.ts` file fails the run.

## Assumed

- `.ts` uses the `typescript` grammar and `.tsx` uses `tsx`. Evidence: parent D15; `language.py` maps both suffixes to `Language.TYPESCRIPT`.
- tree-sitter and the grammar are core dependencies, imported at module level. Evidence: parent D20.
- The pragma text grammar is the same as Python's: `house-lint: ignore|ignore-next|ignore-file[IDS] - reason`, with the same ID, duplicate, reason-length, and HSL900-can't-be-suppressed checks. Evidence: parent D15, `suppressions._PRAGMA`, `suppressions._parse_pragma`.
- No unused verdict for a pragma naming a project rule. Evidence: spec 010 D9.
- A pragma naming a rule that doesn't handle the file's language gets its own HSL900 message ("`<ID>` does not handle TypeScript files"), checked before the disabled-rule check. Evidence: parent D21, `suppressions.scan_pragmas`, spec 010's build calls.
- No shipped file rule handles TypeScript. So in a TypeScript file, a pragma naming one gets that language message, and file-rule claims, conflicts, and unused verdicts never arise. Evidence: `rule_catalog.py`, where `file_rule` and `configurable_rule` set Python.
- Two pragmas claiming one project-rule occurrence both claim it, with no diagnostic. Evidence: spec 011 D2.
- An occurrence with no line can be claimed only by `ignore-file`. A line outside the file has no owner and stays visible. Evidence: spec 011 D5 and `suppressions.claims_for`.
- A suppressed occurrence still counts toward its cluster and appears in its siblings' `related`. Evidence: parent D13, spec 011.
- Every file in a project rule's input is retained as a `LoadedFile`, available on a cache hit. Evidence: spec 010 D1, `pipeline.run_file_phase` (`project_inputs`).
- TypeScript files enter a scan only when an enabled rule handles TypeScript, so Python-only projects never parse TypeScript. Evidence: parent D6, spec 010 D5, `config.discovered_languages`.
- `tree-sitter` 0.26.1 loads `tree-sitter-typescript` 0.23.2 (grammar ABI 14). Evidence: probe in this session.
- Two tests pin today's no-TypeScript behaviour and flip: `test_a_typescript_occurrence_is_not_suppressed` (`tests/integration/test_project_suppressions.py:312`) and `test_a_typescript_file_gets_no_parsing_cache_entry_or_pragma_validation` (`tests/integration/test_project_phase.py:419`). Evidence: the issue deep dive.
- Docs that describe pragmas or the analysis layer: `docs/suppressions.md`, `docs/configuration.md`, `README.md`, and the `CLAUDE.md` Layout and Gotchas ("Only Python has an analysis layer"). Evidence: a search of the repo.

## Build

- [ ] Implementation and tests committed
- [ ] Docs
- [ ] Ship-time challenge

**Calls made during the build:**

## Addendum

### 2026-11-09: TypeScript pulled out of house-lint; this pickup is abandoned

The decisions above were ratified, but the pickup was stopped during the sketch-time challenge, before any code was written. TypeScript is now out of house-lint's scope, and #61 is closed.

**Why:** most of this change rebuilds, inside a Python tool, the pragma machinery that the JavaScript ecosystem gives a rule for free: parsing, disable comments, and unused-directive reporting in an ESLint or oxlint plugin. The prior-art check (`design/research/2026-11-09-suppression-directive-scope/prior-art.md`) found that no peer shares one suppression engine across languages. Duplicates never cross languages, since jscpd compares within a format, so splitting by language loses nothing. TypeScript checks for the maintainer's projects (hassette has about 300 `.ts`/`.tsx` files) belong in a JavaScript-native tool, written later if needed, or in jscpd run directly.

**What this supersedes in the parent ledger** (`design/specs/005-cross-file-rules-and-duplicate-code/design.md`):
- **D7:** `HSL104` covers Python only.
- **D15 and D20:** no tree-sitter dependency, and no TypeScript analysis layer or pragmas.
- **Done-when:** "across Python and TypeScript" and "in both languages" now read as Python only.
- **Breakdown item 6:** dropped. Item 7 (#39) loses its dependency on it.

**What stays:** the language-neutral plumbing that issues 2 and 4 built: a bytes-only `LoadedFile`, rules declaring their languages, discovery derived from enabled rules, and the project phase. `Language.TYPESCRIPT` stays in `language.py`. No rule declares it, so `.ts` files are never discovered. It remains the second language the plumbing's tests use.
