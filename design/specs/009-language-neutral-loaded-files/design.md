# Design: Language-neutral loaded files

**Date:** 2026-11-02
**Status:** ratified
**Mode:** sketch
**Issues:** #57
**Parent:** design/specs/005-cross-file-rules-and-duplicate-code/design.md
**Prior art:** design/research/2026-11-02-loaded-file-and-language/prior-art.md

## Summary

`SourceFile` (`src/house_lint/source.py:42`) reads, decodes, tokenizes, and parses Python in one class, with lazy properties and an `error` field. TypeScript files (#61) and project rules (#59, #39) need files that are read but never parsed as Python. This change splits loading from Python analysis, so every language shares one read path, and gives house-lint a language model. Nothing a user can see changes.

**In scope:** the split, the failure model, the language model, migrating every rule, `analysis.py`, `suppressions.py`, `scanner.py`, `pipeline.py`, and the tests, and updating the docs and comments that name `SourceFile`.

**Out of scope:** TypeScript, discovering any file that isn't `.py`, the rule-applies-to-file check, and rule `languages` (#59, #61). Python's PEP 263 handling, the 10 MiB limit, `O_NOFOLLOW`, and the containment re-check stay exactly as they are. They guard real failures, and removing any of them would be a behaviour change.

**New homes (each follows from the decision named):**
- The loaded file, the function that loads it, and `LoadFailure`: `source.py`, which keeps `read_regular_file_bytes` and `MAX_SOURCE_BYTES` (D1, D2).
- Python decoding, tokens, tree, statements, comments, docstring spans, and `ParseFailure`: a new Python layer (D1, D2, D4).
- The language model and the suffix table: a new leaf module, `language.py`, imported by `discovery.py` (D3).
- The per-file record of path and resolved target: `DiscoveredFile` in `discovery.py`, replacing `DiscoveryResult.resolved_paths` (D3).
- The content hash stays in `cache.hash_source_content` (D1).

**Must hold in the build:**
- A load failure (path escape, non-regular, oversized, read error) is reported and never cached, as today. A decode, tokenize, or syntax failure has a cache key and is cached, as today, because its bytes were read.
- Error codes, kinds, phases, operations, and messages don't change, including `decode-error`'s phase `read`. The internal-error labels stay `source-load` for the read and `source-analyze` for decoding and parsing.
- `--debug` still prints the preserved traceback for a source error, and a cached error is still re-scanned under `--debug` (`pipeline.py:267`).
- The file loop: `open_source` calls `load_file` inside the `source-load` boundary. On a load failure, the preserved exception prints under `--debug` when there is one, and the file contributes `FileScanResult(errors=(error,))` with `files_scanned` 0 and no cache lookup or store. Otherwise the loop hashes the bytes and looks up the cache. On a miss, or a cached error under `--debug`, `scan_source` calls `parse_python` inside its `source-analyze` boundary, so a hit never decodes. A parse failure gives the same `FileScanResult` shape and is stored. `LoadedFile` holds `path`, `relative_path` (with the `path.name` fallback at `source.py:72-74`, so a path escape reports as today), and the bytes. `PythonSource` copies `path` and `relative_path` once at construction, with no reference back to `LoadedFile`. With `tree` no longer optional, the guards at `suppressions.py:237`, `:281`, and `source.py:280` go too.
- `resolved_path` becomes a required input to loading, and only an input: nothing outside `source.py` reads it. Only tests omit it today, and the fallback re-resolves the path, which the comment at `source.py:51` exists to warn against.

**References to update in the same change:** `CLAUDE.md` (Layout, Adding a rule, the read-once gotcha), `cache.py:144`, `:149`, `:172-175`, `:178`, `discovery.py:105`, `pipeline.py:38`, `:225`, `:247`, `:296`, `scanner.py` docstrings, `tests/unit/test_cache.py:62`, `tests/unit/test_discovery.py:149`, `tests/integration/test_cli.py:1309-1316`.

## Posture

- **Failure handling:** unchanged. A file that can't be read or parsed gives the same `LintError` as today.
- **Compatibility:** internal names break freely with no shims. CLI output, JSON, exit codes, and the content of cache entries stay identical.
- **Scope:** the split and the language model only. The Python-only file set stays exactly as it is.
- **Rollout:** ships directly. The PR is typed `chore:`, because nothing is user-visible.
- **Hardening:** project defaults. `test_each_scanned_file_is_read_exactly_once` keeps pinning the single read.
- **Altitude:** `build`. One session builds it, so the ledger cites files.

## Decisions

### D1: What does the loaded file carry?

**Situation:** Parent D4 lists "bytes, text, hash, language", and parent D3 gives project rules a content hash. Decoding isn't language-neutral, though. Python decodes through PEP 263 (`source.py:111`), while tree-sitter takes UTF-8 bytes and reports byte offsets, so slicing a decoded `str` with them goes silently wrong after any non-ASCII character. The hash has one consumer, the per-file cache key (`pipeline.py:300`), because parent D11 stopped caching project rules. Today every file is decoded at load, even on a cache hit that never reads the text.

**Deciding factor:** the loaded file holds only what every language needs, and each language decodes by its own rules.

| | A: Parent D4: bytes, text, hash | B: Bytes only; each language layer decodes; the hash stays in `cache.py` | C: Text only; bytes re-encoded on demand |
|---|---|---|---|
| Where PEP 263 lives | Loader, which must branch on language | Python layer | Loader, which must branch on language |
| Tree-sitter input (#61) | Bytes, ready | Bytes, ready | Re-encoded, with a cost per file |
| Cache key comes from the buffer that was read | Yes | Yes | No: a non-UTF-8 file re-encodes differently |
| Undecodable file in a project rule's input (#59) | Load failure, so it drops out of the file set | Stays in: the wrapped tool reads the real file anyway | Load failure |
| Decoding on a cache hit | Every file | None | Every file |
| Consumer for a hash on the loaded file | Only the file loop, which already calls `hash_source_content` | n/a | n/a |
| Second consumer that must remember to decode | No | Yes, risking Pylint's double read if one re-reads instead. The read-once test catches that | No |
| Peers | None store a hash; text is the norm, with one decoder | mypy | Ruff, Biome, oxlint, ast-grep: UTF-8-only tools |

**Recommendation:** B. Peers can decode up front because they have one decoder, and house-lint has two rules. It supersedes "text" and "hash" in parent D4 and "content hash" in parent D3. Parent D4's split itself stands.
**Pick A instead if** you expect several consumers of decoded text outside the language layers. **Pick C instead if** you'd drop PEP 263 and go UTF-8-only like Ruff, which is a behaviour change this posture rules out.
**Reversibility:** easy
**Ratified:** Chose bytes only, with each language layer decoding and the hash staying in `cache.py`, over parent D4's bytes, text, and hash or a text-only form, so each language decodes by its own rules and cache hits skip decoding, accepting that each language layer must decode from the one buffer rather than re-read. Supersedes "text" and "hash" in parent D4 and "content hash" in parent D3.

### D2: How is a failed file represented?

**Situation:** `SourceFile` always exists and carries an `error` field. `text` raises if it's set, `tokens` returns `()`, and `tree` returns `None`. All seven detectors guard against this (`analysis.parsed_tree` in four, `source.error` in three), yet `scan_source` returns before any detector when `error` is set (`scanner.py:80-83`), so those guards never fire in a run. Analysis is lazy, but `scan_source` triggers it first on every cache miss, so laziness saves nothing once a cache hit stops needing the object.

**Deciding factor:** a rule can't be handed a file that failed, so no rule needs a check for it.

| | A: Today's shape, split: objects with an `error` field and lazy analysis | B: Results: loading gives a loaded file or a failure, and parsing gives a Python source or a failure | C: B for loading; the Python layer keeps `error` and lazy analysis |
|---|---|---|---|
| Guards in detectors | Stay (7) | Deleted, along with `parsed_tree` and its `assert` | Stay (7) |
| Python source with no tree or tokens | Representable | Not representable | Representable |
| File loop | As today | A load failure ends that file's iteration before the cache key, so `hash_source_content` no longer takes `None` or checks size | Same as B for loading |
| Analysis cost on a miss | Same | Same: every miss analyzes today | Same |
| Tests to rewrite | Few | The failure tests in `tests/unit/test_source.py`; rule tests don't exercise the guards | Some |
| Fits #61, where an `ERROR` tree keeps findings | Yes | Yes: tree-sitter always returns a tree, so that layer isn't a failure | Yes |
| Peers | None with a non-recovering parser | ESLint, oxlint, Pylint, flake8. Ruff and Biome differ because their parsers recover, and `ast` doesn't | None |

**Recommendation:** B. It deletes branches rather than moving them, and it's already how a run behaves. Each layer returns its own failure type, `LoadFailure` and `ParseFailure`, and each holds the `LintError` and the preserved exception for `--debug`. The names make the cache rule visible: a load failure is never cached and a parse failure is. The data flow enforces this too, since a cache key needs a `LoadedFile`'s bytes.
**Pick A instead if** you want the smallest diff and accept the dead guards. **Pick C instead if** you expect a rule that must run on a file `ast` can't parse, such as a line-length check on broken Python. Nothing planned does.
**Reversibility:** easy
**Ratified:** Chose results, where loading and parsing each return a valid object or a failure, over error fields on both layers or on the Python layer alone, so a rule can't be handed a failed file and the seven detector guards go away, accepting a rewrite of the failure tests in `tests/unit/test_source.py`. At the sketch challenge (Finding 7), chose two failure types over one type with a stated rule or a `cacheable` field, so each layer's return type names its own failure and the cache rule reads from the name, accepting a second type with identical fields.

### D3: How does a file's language get from discovery to the rules?

**Situation:** Discovery tests `path.suffix != ".py"` in two places (`discovery.py:282`, `:366`). Parent D6 and issue #57 say discovery tags each file with its language, and #59 then discovers only the languages enabled rules handle. `DiscoveryResult` already holds two aligned fields, `files` and `resolved_paths`. In this issue, nothing would read a stored tag, because every file is Python.

**Deciding factor (revised at the sketch challenge):** one source of truth for language, with no stored copy of something the code already treats as derived. Test churn doesn't count against an option.

| | A: `DiscoveredFile(path, resolved, language)`, and `LoadedFile` references the record | B: `DiscoveredFile(path, resolved)`, plus `language_for(path)` in a leaf `language.py` | C: `DiscoveredFile(path, resolved)` and a `.py` helper; #59 adds the language model | D: A third aligned mapping on `DiscoveryResult` | E: `language_for(path)`, keeping `files` and `resolved_paths` |
|---|---|---|---|---|---|
| Stored copies of language | One | None | None | One | None |
| Matches issue text ("tags") | Yes | The effect, not the word | No | Yes | The effect, not the word |
| `resolved_paths` indexing invariant | Removed | Removed | Removed | Stays, with a second like it | Stays |
| Unread names in this PR | `language` field | None | None | `languages` | None |
| `.py` check in one place | Yes | Yes | Yes | Yes | Yes |
| Home of the language model | `source.py`, imported by discovery | Leaf module the catalog can import at #59 with no cycle | #59 decides | `source.py` | Leaf module |
| Overlap with the cache key's filename input (`pipeline.py:303-305`) | Two truths | None: the rule sits on `language_for` | None until #59 | Two truths | None |
| What #59 must add | Table entries | Table entries, plus a `language_for` call where it builds its file set | The model, the table, the function, and the helper swap | Table entries | Table entries, plus a call |
| Test changes | About 58 references | About 58 references | About 58 references | Few | Few |
| Peers | Biome, whose tag takes editor and project input | Ruff, oxlint | n/a | None | Ruff, oxlint |

**Recommendation (revised at the sketch challenge):** B. `DiscoveryResult.files` becomes a tuple of `DiscoveredFile(path, resolved)`, and `resolved_paths` and its indexing invariant (`pipeline.py:247-250`) are deleted. `language.py` holds a `Language` enum with only `PYTHON` for now, and `language_for(path) -> Language | None` over a suffix table, which replaces the two `.py` checks. It reads the path as reported, the same spelling discovery tests today, so a symlink named `a.py` stays Python. Nothing stores the result, so `LoadedFile` has no language field. A comment on `language_for` states the rule that any input beyond the path must join `hash_effective_config`, and the docstring at `cache.py:172-175` points to it. This supersedes "language" in parent D4 and the "tag" wording in parent D6. D6's ratified outcome, discovering by enabled rules' languages, stands.
**Pick A instead if** you expect a non-path input to language soon, such as a config extension map or a `package.json` `type`. **Pick C instead if** a one-member enum isn't worth shipping ahead of #59. **Pick D or E instead if** you want to keep the two aligned discovery fields.
**Reversibility:** easy
**Ratified:** Chose `DiscoveredFile(path, resolved)` plus a derived `language_for(path)` in a leaf `language.py` over a stored language tag, a model deferred to #59, or keeping the aligned fields, so language has one source of truth and the indexing invariant goes away, accepting about 58 test references to change, a one-member enum until #59, and a `language_for` call in #59 where it builds its file set. Revised at the sketch challenge (Finding 1): first ratified as a stored tag, which then drew findings for its second copy, its home, and its overlap with the cache key.

### D4: What are the new types called, and where does the Python layer live?

**Situation:** `SourceFile` is named in all seven rule modules, `analysis.py`, `suppressions.py`, `rule_catalog.py`, `scanner.py`, `pipeline.py`, and 67 test constructor calls. Under D1 and D2 it stops being a thing you construct from a path. #61 adds a TypeScript layer beside it.

**Deciding factor:** a name says which language it is, once a second language exists. **Recommendation:** `LoadedFile` and `load_file` in `source.py`, and `PythonSource` and `parse_python` in a new `python_source.py`, with #61 adding `typescript_source.py` beside it. `SourceFile` is removed outright. **Pick keeping the name `SourceFile` for the Python layer instead if** you'd rather avoid the rename and accept a generic name on a Python-only type.
**Reversibility:** easy
**Ratified:** Chose `LoadedFile` in `source.py` and `PythonSource` in a new `python_source.py` over keeping the name `SourceFile`, so each name says which layer and language it is once TypeScript arrives, accepting a rename across every rule module and test.

## Assumed

- Parent D4's split of loading from analysis holds, with one read path for every language. Evidence: parent D4; `source.py:94` is the only read of a scanned file.
- `test_each_scanned_file_is_read_exactly_once` stays valid, since it patches `source.read_regular_file_bytes`, which stays in `source.py`. Evidence: `tests/integration/test_cli.py:573-580`.
- A cache hit needs only the bytes and the file's name. Evidence: `pipeline.py:261-269`, `pipeline.py:300-305`.
- Every cache miss analyzes the file. Evidence: `scanner.py:80`, where reading `source.error` runs `_analyze`.
- Every current detector is Python-only, so `PythonSource` can replace `SourceFile` in the detector signature. Evidence: the `Detector` protocol at `rule_catalog.py:37`, and parent D21, which keeps a rule from dispatching on a language it doesn't handle.
- No rule test reaches a detector's error guard. Evidence: a search of `tests/unit/rules/` for `.error` and syntax failures.
- Choosing what to do with loaded files per language, and retaining them for the project phase, stay with #59. Evidence: spec 006 D6, parent Breakdown item 4.
- Decoding later changes no output, because decoding is deterministic on the bytes and each file reports at most one source error. Evidence: `source.py:110-129`, `scanner.py:80-83`.

## Build

- [x] Implementation and tests committed
- [x] Docs: `CLAUDE.md` and the docstrings the ledger lists; nothing in `docs/` or `README.md` names these internals
- [ ] Ship-time challenge

**Calls made during the build:**
- `PythonSource` is a frozen dataclass whose `lines`, `comments`, and `docstring_spans` are `cached_property`: the failure-producing steps run once in `parse_python`, while rules that don't read the derived forms still don't pay for them.
- `LoadedFile`'s bytes field is `content`, and `Token` moves to `python_source.py`, since only the Python layer has tokens.
- `hash_source_content` takes `bytes` and returns `str`, and `RunCache.lookup` and `store` take a non-optional key, because a load failure now ends the iteration before any key exists. `MAX_SOURCE_BYTES` leaves `cache.py`.
- A load failure's `--debug` print happens inside `open_source`'s boundary, so an exception while printing it is labelled `source-load` rather than today's `source-analyze`. That only arises if writing to stderr itself fails.
- Tests build these objects through a new `tests/_source_helpers.py` (`load`, `parse_file`), and `test_source.py` splits by layer into `test_source.py` and `test_python_source.py`. A checked-in helper replaces 67 constructor calls with one recipe.
- `open_source` and `scan_source` keep their names, since the ledger's file loop names them.
