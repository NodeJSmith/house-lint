---
topic: "How multi-language linters separate language-neutral file loading from per-language parsing"
date: 2026-11-02
status: Draft
---

# Prior Art: loaded files, parse failures, and carrying a file's language

## The Problem

house-lint's `SourceFile` reads, decodes (PEP 263), tokenizes, and parses Python in one class. TypeScript analysis through tree-sitter (#61) and a project phase that wraps jscpd (#59, #39) need files that are read but never parsed as Python. Milestone 005's D4 ratified splitting loading from analysis, naming the loaded file's contents as "bytes, text, hash, language". That leaves three questions: what the loaded form actually carries and who decodes, how a parse failure is modelled, and how a file's language travels from discovery to rules.

This builds on `design/research/2026-10-30-linter-engine-architecture/prior-art.md`, which classifies the one-file unit as matching peers with a known gap, and doesn't repeat it.

## How We Do It Today

- `SourceFile` holds bytes, text, tokens, tree, statements, and comments behind lazy memoized properties, with an `error` field. `load()` reads and decodes on every file, including cache hits. `_analyze()` tokenizes and parses on first access to `error`.
- `scan_source` checks `source.error` before running any rule, so a failed file reaches no detector. Every detector checks again anyway (`parsed_tree`, or `source.error` in HSL001, HSL101, and HSL102).
- The cache key comes from `content_bytes` through `cache.hash_source_content`, which also rejects oversized content.
- Discovery tests `path.suffix != ".py"` in two places and carries no language.

## Patterns Found

### Pattern 1: Decode once in the driver; the parser receives text

**Used by**: ESLint, Ruff, Biome, oxlint, ast-grep, flake8, Pylint
**How it works**: The component that reads the file decodes it before any language code runs. ESLint reads with `encoding: "utf8"`. Ruff and Biome use `read_to_string`. oxlint validates UTF-8 and passes `&str`. flake8 uses `tokenize.open` with a silent latin-1 fallback. Pylint's AST library uses `detect_encoding` and then `open(encoding=)`.
**Strengths**: One decode site and one error path. It works cleanly for tools that treat every language as UTF-8.
**Weaknesses**: Non-UTF-8 input. Ruff and oxlint reject it, and flake8 can silently decode it wrongly. Every peer that decodes in the driver has one decoder for all languages; none of them has both PEP 263 and a second language with different rules.
**Example**: https://github.com/astral-sh/ruff/blob/79daf466f18d3d499f90535a7f59e0650a7ad11f/crates/ruff_linter/src/source_kind.rs#L127
**Read**: ESLint `lib/eslint/eslint-helpers.js` L1283; Ruff `source_kind.rs`; Biome `biome_fs/src/fs.rs` L290; oxlint `utils/mod.rs` L190; flake8 `processor.py` L341; Pylint's AST library `builder.py` L48; ast-grep `crates/core/src/tree_sitter/mod.rs`.

### Pattern 2: Bytes as the loaded form; decoding is a later step

**Used by**: mypy. ESLint's types allow it (`File.body: string | Uint8Array`), but nothing in ESLint core reads `fileType`, so that is a declared extension point rather than behaviour.
**How it works**: The loader returns bytes, the hash comes from those bytes, and `decode_python_encoding` turns them into text on demand.
**Strengths**: The cache key and the parsed content come from one buffer by construction, and a parser that wants bytes gets them without re-encoding.
**Weaknesses**: Each consumer has to decode. Pylint shows the failure this invites: decoded text for the AST, then a second read of the path for token checkers.
**Example**: https://github.com/python/mypy/blob/1e6f0205907233d0efb604dd4b232038e58d5826/mypy/build.py#L3221
**Read**: mypy `build.py` L3221, `util.py` L161, `fscache.py` L265; ESLint `rewrite/packages/core/src/types.ts` L1161, L1247, plus a grep of `fileType` in `eslint/lib`; Pylint `utils/utils.py` L160.

### Pattern 3: A parse result the rules never see fail

**Used by**: ESLint (`{ok: true, ast}` or `{ok: false, errors}`), oxlint (`Result`), Pylint (`get_ast` returns `None`, then an early return before `FileState`), flake8 (exception caught in `run_checks`, then return)
**How it works**: The parse yields a usable tree or diagnostics. On failure nothing later is built, so a rule can assume a valid tree.
**Strengths**: Rules carry no "did the parse work" check, and no half-built state exists to inspect.
**Weaknesses**: Checks that need no tree are lost on broken files.
**Example**: https://github.com/eslint/eslint/blob/11c66ba08381cb7cff7679d662c9f6654b6fe712/lib/services/parser-service.js
**Read**: ESLint `parser-service.js`, `linter.js` L1034; oxlint `service/runtime.rs` L925-L960, L1128-L1160; Pylint `pylinter.py` L958-L998, L1127-L1167; flake8 `checker.py` L482-L498.

### Pattern 4: Error-recovering parse, with rules gated by what they need

**Used by**: Ruff, Biome
**How it works**: The parser always returns a tree plus errors. Ruff runs token, line, and path rules regardless and gates AST rules on `has_valid_syntax()`. Biome's JS linter has no early return on parse errors.
**Strengths**: Line-level checks keep working on half-written files.
**Weaknesses**: Needs a recovering parser, and someone has to keep the gate correct. Python's `ast` doesn't recover.
**Example**: https://github.com/astral-sh/ruff/blob/79daf466f18d3d499f90535a7f59e0650a7ad11f/crates/ruff_linter/src/linter.rs#L197
**Read**: Ruff `linter.rs` L120-L260; Biome `workspace/server.rs` L1658-L1730, `file_handlers/javascript.rs` L1279-L1420 (individual rules not read).

### Pattern 5: Language as a pure function of the path

**Used by**: Ruff (`SourceType::from(path)` plus an extension map, computed once and passed as an argument), oxlint (`SourceType::from_path`, called at several sites)
**How it works**: No tag lives on a discovered-file record. A function returns an enum, and the first consumer uses it to pick a loader.
**Strengths**: Nothing to keep in sync, and a file can't carry a stale tag.
**Weaknesses**: Anything the path can't decide needs a second input.
**Example**: https://github.com/astral-sh/ruff/blob/79daf466f18d3d499f90535a7f59e0650a7ad11f/crates/ruff_linter/src/settings/types.rs#L518
**Read**: Ruff `diagnostics.rs` L215-L272, `settings/types.rs` L505-L530; oxlint `service/runtime.rs` L323-L355, L695, L990.

### Pattern 6: Language stored once, computed at open

**Used by**: Biome (`DocumentFileSource` on the document), ESLint (the `language` key of the matched config object)
**How it works**: Biome calls a pure `from_path`, refines the result with project and editor input, stores it, and dispatches on it later. ESLint selects it by `files` globs, and rules list `meta.languages`.
**Strengths**: One decision point, with inputs beyond the path.
**Weaknesses**: More state. Biome's need comes from a long-lived workspace with editor hints, and I found no evidence a one-shot CLI needs it.
**Example**: https://github.com/biomejs/biome/blob/ca4e6f341aa14b391d9df23d16c1a7a590f05f2d/crates/biome_service/src/workspace/server.rs#L883-L958
**Read**: Biome `biome_languages/src/lib.rs` L53-L370, `workspace/server.rs` L840-L1020; ESLint `lib/config/config.js` L262-L310, L478-L520.

## Anti-Patterns

- **Reading the path twice for two consumers.** Pylint decodes for the AST and re-opens the path for `tokenize_module`.
- **Slicing a decoded `str` with tree-sitter byte offsets.** `parse` takes bytes, and offsets and columns are byte counts, so the slice goes silently wrong once non-ASCII precedes the node (py-tree-sitter #31, with the same bug reported in two blog posts).
- **Silent fallback decode.** flake8's latin-1 fallback never fails, but can decode wrongly without saying so.

## Gaps

- Biome's individual lint rules on error trees were not read, and neither was how Ruff's suppression handling treats a failed parse. Neither affects the recommendation: house-lint uses `ast`, which doesn't recover.
- `@eslint/json` and `@eslint/markdown` were not read (separate repos). The `Language` contract and the JS language's `parse` were.
- ast-grep's parse-failure and language handling were not traced.

## Relevance to Us

- **Bytes on the loaded file, decode in the language layer.** Peers decode in the driver, but they have one decoder. house-lint has two rules: PEP 263 for Python, and UTF-8 bytes for tree-sitter, whose offsets are byte counts. mypy is the precedent for bytes first, and it matches the existing guarantee that the cache key comes from the scanned buffer. The Pylint double read is the risk to guard, and `test_each_scanned_file_is_read_exactly_once` already does.
- **A result the rules never see fail.** This is what house-lint already does in practice: `scan_source` stops before any detector on a failed file, and `ast` can't recover, so Ruff's model isn't available. The per-detector error checks are dead code in production.
- **Language as a function of the path.** Ruff and oxlint do this with no stored tag, and Biome's reason for storing one doesn't apply to a one-shot CLI. Parent D6's "tag" can be derived rather than stored.
- **A hash on the file set.** Parent D3 lists a content hash, but D11 stopped caching project rules, so nothing consumes one there.

## Recommendation

Carry bytes on the loaded file, decode inside each language layer, parse eagerly into either a valid source or an error, and derive language from the path through one function. Drop the parent's `text`, `hash`, and `language` from the loaded file.

## Sources

### Reference implementations
- https://github.com/eslint/eslint/blob/11c66ba08381cb7cff7679d662c9f6654b6fe712/lib/services/parser-service.js — parse dispatch and union handling
- https://github.com/eslint/eslint/blob/11c66ba08381cb7cff7679d662c9f6654b6fe712/lib/linter/vfile.js — `VFile`
- https://github.com/eslint/eslint/blob/11c66ba08381cb7cff7679d662c9f6654b6fe712/lib/eslint/eslint-helpers.js#L1283 — file read
- https://github.com/eslint/eslint/blob/11c66ba08381cb7cff7679d662c9f6654b6fe712/lib/config/config.js#L478-L520 — language as config key
- https://github.com/eslint/rewrite/blob/9aa97f2d08bfc04be62a918f1b2a67082cf03f96/packages/core/src/types.ts#L1161 — `Language`, `File`, `ParseResult` types
- https://github.com/astral-sh/ruff/blob/79daf466f18d3d499f90535a7f59e0650a7ad11f/crates/ruff/src/diagnostics.rs#L182 — `lint_path`
- https://github.com/astral-sh/ruff/blob/79daf466f18d3d499f90535a7f59e0650a7ad11f/crates/ruff_linter/src/source_kind.rs#L15 — `SourceKind`
- https://github.com/astral-sh/ruff/blob/79daf466f18d3d499f90535a7f59e0650a7ad11f/crates/ruff_linter/src/linter.rs#L120-L260 — rule gating on syntax errors
- https://github.com/astral-sh/ruff/blob/79daf466f18d3d499f90535a7f59e0650a7ad11f/crates/ruff_linter/src/settings/types.rs#L505-L530 — extension mapping
- https://github.com/biomejs/biome/blob/ca4e6f341aa14b391d9df23d16c1a7a590f05f2d/crates/biome_languages/src/lib.rs#L53 — `DocumentFileSource`
- https://github.com/biomejs/biome/blob/ca4e6f341aa14b391d9df23d16c1a7a590f05f2d/crates/biome_service/src/workspace/server.rs#L840-L1020 — `open_file`
- https://github.com/biomejs/biome/blob/ca4e6f341aa14b391d9df23d16c1a7a590f05f2d/crates/biome_service/src/file_handlers/mod.rs#L452 — `Capabilities`
- https://github.com/oxc-project/oxc/blob/564559dd11ed452996edb27f5d45ab65f6704f60/crates/oxc_linter/src/service/runtime.rs#L323-L355 — source type and text
- https://github.com/oxc-project/oxc/blob/564559dd11ed452996edb27f5d45ab65f6704f60/crates/oxc_linter/src/context/host.rs#L130-L195 — `ContextHost`
- https://github.com/pylint-dev/pylint/blob/6909288b2bed530142f83abdb220a39eee2905c2/pylint/lint/pylinter.py#L958-L998 — `_check_file`
- https://github.com/pylint-dev/astroid/blob/3d0bc5800477403f0bf903769f67e421777ef84e/astroid/builder.py#L48-L58 — `open_source_file`
- https://github.com/PyCQA/flake8/blob/1c7fec1973ed3a7c192dbe1e96906fbbd1dbe49e/src/flake8/checker.py#L482-L498 — `run_checks`
- https://github.com/python/mypy/blob/1e6f0205907233d0efb604dd4b232038e58d5826/mypy/build.py#L3221 — bytes, hash, decode
- https://github.com/ast-grep/ast-grep/blob/e83806463b5fb5c34dd7808e82f4c8ae52333f52/crates/core/src/tree_sitter/mod.rs#L63-L100 — `StrDoc`
- https://github.com/tree-sitter/py-tree-sitter/blob/2e556e540dd4ccce0e24c92d4413ef9ac85284a5/tree_sitter/__init__.pyi#L246-L262 — parser and node API

### Issues
- https://github.com/astral-sh/ruff/issues/6791 — non-UTF-8 sources, normalize early
- https://github.com/astral-sh/ruff/issues/11876 — closed as not planned
- https://github.com/tree-sitter/py-tree-sitter/issues/31 — byte offsets vs `str` slicing

### Blog posts & documentation
- https://www.sahuja.in/notes/tree-sitter-byte-offsets — byte-offset pitfall
- https://blog.divyampatro.dev/clearcode-part-2-ast-aware-indexing-vector-stores-and-hybrid-retrieval — same pitfall in practice
- https://tree-sitter.github.io/tree-sitter/using-parsers/2-basic-parsing.html — columns are byte counts
- https://github.com/eslint/eslint/blob/11c66ba08381cb7cff7679d662c9f6654b6fe712/docs/src/extend/languages.md#L77 — language docs
