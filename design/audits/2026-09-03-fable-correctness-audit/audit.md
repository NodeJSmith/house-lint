# Correctness Audit — house-lint (full codebase)

**Date:** 2026-09-03
**Driver:** Reasoning-driven adversarial review (Fable 5), same shape as the ccrecall 2026-09-02 and hassette 2026-09-02 audits: main-model deep pass on the scan/cache core, four parallel audit arms over discovery, config/CLI/reporting, the rule engine, and suppressions/source — every finding verified against code with file:line and a concrete failure scenario, the load-bearing ones confirmed with runtime probes. Cross-checked against the open issue backlog (#17, #18, #25, #32, #36, #39) and the per-spec known-issues docs so nothing already tracked is re-filed. Two arms independently derived F1 and F3, which is corroboration, not duplication.
**Prior audits:** 2026-08 codebase audit (12 findings, resolved in #8); the gitignore parity/fuzz differential work (spec 002, decision record #26). This audit covers what those left: cross-subsystem seams, the line-number invariant, and adversarial input shapes.

## TL;DR

The hardened surfaces held: the cache's key-derivation honesty, its symlink/O_EXCL defenses and corrupted-entry degradation, config validation strictness, HSL900's unsuppressibility, the pragma conflict machinery, and the per-directory gitignore stack itself all survived adversarial analysis clean. The real findings cluster at three seams:

1. **The line-number model is inconsistent** (F1): `SourceFile.lines` comes from `str.splitlines()`, which splits on ~8 characters the tokenizer/AST do not count as line breaks. One legal Python file with a form feed or U+2028 desynchronizes every line-indexed decision after it — probe-confirmed to abort an entire HSL101 run (exit 4, remaining files silently unscanned), to break valid `ignore-next` suppressions under the default rule set, and to silently misread docstring lines. One-line fix.
2. **Configured `exclude` bypasses the gitignore engine the project built** (F2): it still uses pathspec's aggregate matcher — the documented reason the per-directory stack exists — so the standard whitelist idiom silently prunes every subdirectory. Silent under-linting, with zero parity coverage on that path.
3. **The ownership model has a hole exactly where HSL001 lives** (F3): findings on comment-only lines have no statement owner, so no line pragma can ever suppress them — and attempting to gets punished with a spurious "unused suppression" finding. Only `ignore-file` works; the docs claim otherwise.

## Findings

### F1 — HIGH: `SourceFile.lines` line model diverges from the tokenizer/AST line model (probe-confirmed, three manifestations)

`source.py:242` — `self._lines = self.text.splitlines()`. `str.splitlines()` splits on `\v`, `\f`, `\x1c`–`\x1e`, `\x85`, ` `, ` `; Python's tokenizer and AST count lines only on real newlines. Every consumer indexes that list with tokenizer/AST line numbers: `suppressions.py:194,202,208,251,257`, `analysis.py:120,132,141,151`, `rules/spec_tokens.py:47`, `rules/llm_cruft.py:59`. Any such character before line N — legal anywhere in Python source, including a PEP 8-sanctioned `\f` page break or a literal `\f`/` ` byte inside a string — shifts every index ≥ N. Probe-confirmed manifestations, descending:

1. **Run-aborting crash (HSL101 enabled).** `comment_owner_for_line` (`analysis.py:151`) does `source.lines[line - 1].index(comment)`; the shifted line lacks the comment text → `ValueError` → internal error with `stop=True` → `cli._scan:353-356` **breaks the whole run**. Verified end-to-end: a 4-file scan with one form-feed file exits 4 after scanning 2 files; the rest are silently unscanned.
2. **Suppressions silently break (default rule set — HSL900 is always on).** `suppressions.py:257` judges "alone on a comment-only line" against the wrong line. Probe: `\f` in a line-1 string turns a *valid* `# house-lint: ignore-next[HSL002] - reason` into `HSL900 misplaced ignore-next suppression` **plus** the resurfaced HSL002 finding (clean control: 0 findings, 1 suppressed). The suppressions arm also confirmed the silent inverse: a shifted blank line can satisfy `_comments_or_blanks_only` across a line that really holds a statement, misattributing ownership with no diagnostic.
3. **Silent wrong-line scans.** `spec_tokens.py:47` and `llm_cruft.py:59` scan `source.lines[line - 1]` for docstring content — the wrong text after a shift, so false negatives/positives with no signal; `candidate_for_line`'s end-column misreads the same way.

**Fix direction:** one line — build `lines` by splitting on `"\n"` (universal-newline decoding in `load()` has already normalized `\r\n`/`\r`, so this matches the tokenizer exactly). Regression tests: `\f`-in-string, `\f` page break, and ` ` shapes against the comment-owner, ignore-next, and docstring paths.

### F2 — HIGH: configured `exclude` still uses pathspec's aggregate matcher and silently skips files git would lint

`discovery.py:244-262` (`_ignored`) delegates builtin/exclude matching to `GitIgnoreSpec.match_file()` — the aggregate model whose priority-scheme defect is the documented reason the per-directory stack was built (docs/configuration.md:101). Probe-confirmed: with `exclude = ["*", "!*/", "!*.py"]` (the standard "whitelist only .py" gitignore idiom), the aggregate reports directory `sub/` as ignored despite `!*/`, so `_traversable_dirs` (discovery.py:839) prunes every subdirectory — house-lint lints only root-level files while `git check-ignore` with identical patterns lints all 8 nested `.py` files. Docs claim exclude follows git semantics (configuration.md:15, 81, 85). Silent under-linting — the top-severity class for a linter. Secondary asymmetry on the same path: `_patterns` (discovery.py:237-241) skips the `_trailing_whitespace_trimmed` backslash-parity fix `_build_patterns` applies to gitignore lines. No parity/fuzz coverage exists for configured excludes — the differential suites only exercise `.gitignore` files.

**Fix direction:** route configured excludes through the same `IgnorePatterns`/`_match_patterns` machinery as a root-anchored virtual `.gitignore`; add an exclude-path `Scenario` family to `test_gitignore_parity.py`.

### F3 — MED-HIGH: findings on comment-only lines cannot be suppressed by any line pragma (docs say otherwise)

Independently derived by both the rules and suppressions arms. `analysis.py:139-146` (`statement_owner_for_line`, standalone branch): a comment-only line's owner must be a statement starting or ending on that very line — which a comment-only line by definition lacks, so the branch is dead code and every standalone-comment candidate is `NO_OWNER`. `suppressions._owns` (`suppressions.py:281-284`) matches by owner key, so: trailing `ignore` on that line → "misplaced"; `ignore-next` above it → owns the *next statement's* key → the finding stays **and** the pragma is flagged `HSL900 unused suppression` (probe-confirmed both ways). Only `ignore-file` works. This is the dominant HSL001 shape (divider/filler comments on their own lines) plus HSL101 hits in standalone comments — and a pragma whose own reason text contains filler generates a standalone-comment HSL001 on the pragma line with the same no-local-escape property. `docs/suppressions.md` claims HSL102 is the only no-owner case.

**Fix direction:** let `ignore-next` claim NO_OWNER line candidates between the pragma and its next statement (or attach standalone-comment findings to the following statement — which also revives the dead branch), and align the doc.

### F4 — MED: upward config walk doesn't stop at the `.git` boundary — an ancestor config hijacks root and scan scope

`discovery.py:963-971` (`resolve_project`): the walk returns at the first directory with a *recognized config*; `.git` only ever sets a fallback marker, so the walk continues through repo boundaries. Probe-confirmed: `parent/house-lint.toml` + `parent/repo/.git/`, run from inside `repo` → root resolves to `parent`, and with #37's root-wide default scan it linted sibling directories **outside the repo** (exit 0, foreign files scanned). Any repo without its own config — exactly the new-project case — silently lints a foreign parent tree if `~/` or `~/source/` ever accumulates a recognized config. Documented as-is (configuration.md:74), so a design gap rather than drift — but #37 multiplied the blast radius. Distinct from #18 (hierarchical *merge*); this is boundary *termination*.

**Fix direction:** stop the upward walk at the first directory containing `.git` (still checking that directory itself for configs), matching git/pytest/ripgrep conventions.

### F5 — MED: one HSL103 suppression silences every handler of the same `try`

`exception_names.py:31` passes the whole `try` statement as each candidate's owner, so all handlers share one owner key. Probe-confirmed: two badly-named handlers, one trailing `ignore[HSL103]` on the second → both suppressed, zero findings. Silent over-suppression — the failure class the pragma system exists to prevent.

**Fix direction:** synthesize a per-handler `StatementKey` from the `ast.ExceptHandler`'s own span (it isn't an `ast.stmt`, so it can't be the owner directly).

### F6 — MED pair: HSL001 divider detection misfires in both directions

- **False positive:** `llm_cruft.py:15` `DIVIDER_WRAPPED` matches `# -*- coding: utf-8 -*-` and emacs modelines (`-*-` is three divider chars each side) — probe-confirmed, and compounded by F3 (comment-only line → only `ignore-file` can silence it).
- **False negative:** `llm_cruft.py:50` `comment.lstrip("#")` strips all leading `#`, so a classic `########` divider row reduces to `""` and passes silently — the single most common divider style.

**Fix direction:** exempt `-*- ... -*-` shapes (or require the flanking runs be one repeated character); strip only the first `#` before divider matching.

### F7 — MED: a typo'd `include` entry among valid ones vanishes with no signal

`discovery.py:510-514` (`_consider`): a nonexistent path raises only for explicit CLI paths; config `include` entries run with `explicit_paths=False`, so `include = ["src", "tets"]` silently lints `src` and drops `tets` — no error, no `files_skipped` increment (probe-confirmed). The zero-file diagnostic only fires when the *whole* scan is empty, so a partial typo permanently un-lints a directory invisibly in CI. Extends the motivation behind #36, which covers only the all-empty case.

**Fix direction:** emit a `LintError` for a configured include root that doesn't exist — config entries are user-authored claims, unlike walked paths.

### F8 — LOW-MED: aliased TYPE_CHECKING guards are invisible to HSL003

`type_checking_position.py:44` accepts only `TYPE_CHECKING` / literal `typing.TYPE_CHECKING`. `import typing as t; if t.TYPE_CHECKING:` and `from typing import TYPE_CHECKING as TC` produce no finding (probe-confirmed). False negative in exactly the codebases the tool targets.

### F9 — LOW-MED: decorator lines break both pragma placement contracts (mirrored pair)

- `ignore-next` above a decorated `def`/`class` is diagnosed **"misplaced"** — `suppressions.py:206-208` matches against `statement.lineno` (the `def` line, per AST) and the intervening decorator line defeats `_comments_or_blanks_only`. The accepted placement — between decorator and `def` — is the unnatural one. Probe-confirmed; bites any rule attaching findings to a decorated statement (e.g. HSL103 on a decorated class).
- Mirror: `ignore-file` is **accepted** between the decorator and `def` of the file's first statement (`suppressions.py:264` compares against `statement.lineno`), violating the documented "before the first statement" contract. Probe-confirmed, harmless over-acceptance.

**Fix direction:** use `min(decorator lineno, statement.lineno)` as the statement's start in both checks.

### F10 — LOW: near-miss pragma spellings are completely silent

`_is_pragma` (`suppressions.py:155`) sniffs only `#\s*house-lint:`. `## house-lint:`, `# house_lint:`, `# House-lint:`, `# house-lint :` are not diagnosed at all. For a prophylactic suppression (an `ignore-file` for a rule with no current findings) the typo is invisible — no HSL900, no resurfaced finding.

**Fix direction:** widen the sniff (case-insensitive, `house[-_]lint`, optional space before the colon) so HSL900 reports near-misses as malformed; keep the strict grammar in `_PRAGMA`.

### F11 — LOW cluster: rule-engine edges

- `registry.py:87-88`: dead `len(candidates) > limit` guard (unreachable — `append_candidate` raises first), and a latent multi-input loss path where a later detector's `CandidateBudgetExceeded` drops earlier inputs' candidates from the same call. Production calls one input at a time, so latent — but the contract invites the broken usage (and `test_registry.py` uses the multi-input form).
- `spec_tokens.py:31`: HSL101's `max_findings_per_file` cap truncates in family/scope iteration order, not report order; the budget-exceeded partial list bypasses `_ordered`. Cosmetic under pathological counts.
- Semicolon compound lines: a trailing `ignore` owns only the last statement on the line (`analysis.py:146` takes `max`), so the first statement's finding survives with a confusing unused-suppression diagnostic. Fail-loud; semicolons are rare.

### F12 — LOW cluster: config/CLI/reporting edges

- **Explicit relative CLI paths anchor at `--root`, not cwd** (probe-confirmed: `check proj/src/a.py --root proj` looks up `proj/proj/src/a.py`) — loud in that case (exit 3), silently wrong if `root/<arg>` also exists; contract undocumented.
- **Zero-file guidance names `pyproject.toml` for a custom `--config` file** (`reporters/text.py:61` hardcodes the name; the standalone branch already uses `config.name`).
- **`--select "HSL001,"` misreported** as "unknown or forbidden rule ID" (empty items pass through `_flatten_ids`, `cli.py:58-61`); a repeated `--select HSL001` errors as duplicate IDs.
- **JSON reporter emits lone-surrogate escapes** for undecodable filenames (`"bad_\udcff.py"`) — syntactically JSON, invalid Unicode; strict consumers (Go, serde, some jq builds) reject the document. Marginal for a personal tool.
- **Aside:** open issue **#36 appears already implemented** (`--fail-on-empty` shipped at `cli.py:418`, works by probe) — likely closable. Cyclopts auto-generates `--no-no-gitignore`/`--no-no-cache` flags and renders raw docstrings in `check --help` — cosmetic UX, adjacent to #32.

### F13 — LOW cluster: discovery accounting and contract asymmetries

- **`files_skipped` distortions:** overlapping include roots (`["src", "src/pkg"]`) double-walk and inflate the count (dedup at discovery.py:474 applies only to explicit args); a healthy file symlink counts a skip (discovery.py:540-542) while a broken one counts nothing (discovery.py:510).
- **Explicit directory-symlink argument** (`discovery.py:535-539`) degrades to an error record instead of the `DiscoveryError` hard-fail every other explicit-path failure raises.
- **Case-insensitive filesystems:** matching is case-sensitive throughout; git honors `core.ignorecase` (default-true on macOS/Windows), and `suffix != ".py"` drops `FOO.PY` there. Unprobeable on this ext4 host — a documented-limitation gap for a PyPI package run in mac/Windows CI, not a confirmed repro.

### F14 — LOW (doc drift): suppressions doc examples and docstrings describe impossible behavior

- `docs/suppressions.md`'s own `ignore-next` example (above a module-level import) can never suppress anything — HSL002 only fires inside function bodies — so verbatim use produces `unused suppression`.
- `statement_owner_for_line`'s docstring describes the standalone-comment resolution F3 shows is impossible; the doc's "only HSL102 lacks owners" claim is wrong the same way.
- Fold the F3/F9 contract changes into the doc when fixing.

## Clean areas

- **Cache core** (main-model pass): key honesty (content hash derived from the exact scanned buffer; oversized files read as `max_bytes + 1` correctly fail the cacheability check rather than keying on a truncated prefix; the `filename` fold exactly matches what `_filename_candidates` consumes — the basename), `python_version` and per-file effective-rule-set folding, corrupted-entry validation degrading to misses, symlink defenses on the default base (both levels), `O_EXCL` marker/temp creation, PID-reuse retry, prune gated on a real write, write-failure circuit breaker, `--debug` error-replay asymmetry.
- **Scan loop seams:** read-exactly-once holds end to end; resolved-vs-typed path split is coherent; `stop` results never cached.
- **Suppressions/HSL900** (arm 4): unsuppressibility is airtight (no bypass constructible); conflict handling fails closed; pragma grammar diagnoses loudly; pragma-shaped text in strings inert; `_suites` grammar complete for 3.11+ (TryStar, Match); budget interplay correct, including `candidates_complete=False` disabling unused-suppression false positives during overflow recovery; null bytes fail closed on 3.11 and 3.14 (verified on both).
- **Source model:** BOM/CRLF/lone-CR normalized before tokenize and line-splitting; FIFO/symlink races (O_NONBLOCK/O_NOFOLLOW), oversize budget, undecodable files fail closed.
- **Config validation** (arm 2): strict keys at every level, bool-as-int rejection, selection precedence matches the documented algorithm, HSL900 rejected everywhere user-facing, per-file-ignores validation, token-family caps re-checked post-merge; exit-code mapping 0/1/2/3/4 verified by probe; CycloptsError → exit 2 with `--format json` honored; reporters deterministic; `results.py` location validation sound.
- **Rule engine** (arm 3): registry/catalog sync check does what it claims; spec-token regex boundaries survived probes (ISO-timestamp guard, `max_digits` backtracking, filename `_`-separator semantics); HSL102 threshold exact; `lazy_imports` handles async/nesting; HSL103 `allowed` validation strict; syntax-error handling centralized and fail-closed across all seven detectors.
- **Discovery** (arm 1): the per-directory gitignore stack itself — 18-set differential probe, 0 divergences (dir-only negations, `foo**/**`, whitelist idiom via `.gitignore`); CRLF/lone-CR gitignore parity; unreadable/undecodable gitignore error paths; `..`-spelling resolution; budget cap; excluded-ancestor rule; `--no-gitignore` short-circuit. KI-001 and the pathspec wildmatch decision record (#26) confirmed already tracked.

## Disposition (2026-09-03)

Mechanical fixes landed on `fix/2026-09-audit-findings` (RED→GREEN commit pairs for the substantive ones); findings needing a real design decision were filed as issues.

| Finding | Disposition |
|---|---|
| F1 line-model divergence | **Fixed** — `SourceFile.lines` splits on `\n`; regression tests for `\f`-in-string, `\f` page break, U+2028 |
| F2 exclude aggregate matcher | **Fixed** — excludes routed through `_build_patterns`/`_match_patterns`; two configured-exclude parity families added |
| F3 comment-only suppression hole | **Issue #40** (ownership-model choice) — includes F14's doc-claim alignment |
| F4 config walk crosses `.git` | **Issue #41** (breaking change to documented contract) |
| F5 HSL103 shared owner | **Fixed** — candidates keyed per `ast.excepthandler`; except-clause lines refine to their handler |
| F6 divider FP/FN | **Fixed** — backreferenced flanking runs (modelines exempt); comment probed with leading `#` kept (`########` caught) |
| F7 silent missing include root | **Issue #42** (error-vs-warning choice) |
| F8 aliased TYPE_CHECKING | **Fixed** — top-level `typing`/`TYPE_CHECKING` aliases resolved |
| F9 decorator placement | **Fixed** — `_statement_start_line` counts decorators in `ignore-next` and `ignore-file` checks |
| F10 near-miss pragma sniff | **Issue #43** (sniff-breadth choice) |
| F11 registry edges | **Fixed** — dead guard removed; overflow preserves earlier inputs' candidates |
| F12 config/CLI edges | Guidance names `config.name` (**fixed**); `--fail-on-empty` verified shipped → **#36 closed**; anchoring → **#44**; select hygiene → **#45** (strictness is test-pinned — an attempted fix was reverted); JSON surrogates → **#46** |
| F13 accounting cluster | **Issue #47** |
| F14 doc drift | `ignore-next` example **fixed**; ownership claims ride with #40 |
