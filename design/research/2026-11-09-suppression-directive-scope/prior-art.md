---
topic: "How linters scope inline suppression directives: line-scoped vs statement-scoped, for TypeScript and multi-line diagnostics"
date: 2026-11-09
status: Draft
---

# Prior Art: Suppression Directive Scope

## The Problem

house-lint gives Python pragmas statement ownership. A trailing `ignore` owns its innermost statement, `ignore-next` owns the next statement in its block, and a project-rule occurrence is claimed when the innermost statement containing its start line equals the pragma's owner. Spec 005 D15 planned the same for TypeScript through tree-sitter, with a per-grammar table of "owner node kinds": statements, class members, object methods, JSX elements, decorators, and `export` wrappers. The 2026-10-30 engine brief flagged that Ruff's and ESLint's directives are believed to be line-scoped and left #61 to check. This brief checks it, and also covers which line a multi-line diagnostic is matched on and how tools treat suppressions in files that fail to parse.

## How We Do It Today

- **Python:** statement ownership, built on `ast` and tokens (`suppressions.py`, `analysis.occurrence_owner`).
- **TypeScript:** no analysis layer. A TypeScript occurrence of a project rule can't be suppressed, and TypeScript files get no HSL900 (`pipeline.run_file_phase`, `PragmaClaims.claims`).

## Patterns Found

### Pattern 1: Line-scoped, matched on the diagnostic's start position

**Used by:** ESLint, Biome, oxlint, Deno lint, Semgrep, ast-grep, Ruff
**How it works:** A trailing directive targets its own line, and a comment-only "next line" directive targets the following line. A diagnostic is suppressed if and only if its start lies on a targeted line. End lines and enclosing nodes are ignored. oxlint states the reason in its code: matching on the start stops a directive from swallowing an enclosing function's diagnostic, which overlap would do.
**Strengths:**
- Easy to explain.
- Needs no per-grammar table.
- Behaves identically on a partial AST.
**Weaknesses:** When a diagnostic starts on an interior line of a multi-line construct (JSX attributes, chained calls), the comment has to sit on that exact line. Biome #9469 and react-doctor #158 report this, and it is most often hit in TSX.
**Example:** https://github.com/oxc-project/oxc/blob/main/crates/oxc_linter/src/disable_directives.rs
**Read:**
- eslint `lib/linter/apply-disable-directives.js`
- biome `crates/biome_analyze/src/suppressions.rs`
- oxc `crates/oxc_linter/src/disable_directives.rs`
- deno_lint `src/context.rs` and `src/ignore_directives.rs`
- semgrep `src/reporting/Nosemgrep.ml`
- ast-grep `crates/config/src/combined.rs`
- ruff `crates/ruff_linter/src/directives.rs`

### Pattern 2: Line scoping plus a small lexical remap

**Used by:** Ruff, ast-grep
**How it works:** The line semantics are kept, but a few multi-line constructs are mapped onto one canonical line:
- Ruff maps offsets inside multi-line strings, f-strings, and backslash continuations onto the construct's last line.
- ast-grep lets a trailing comment adopt the start line of its same-line previous sibling.

Both use tokens or the immediate sibling, not a statement taxonomy.
**Strengths:** Cheap, and it fixes the worst multi-line cases.
**Weaknesses:** Diagnostics that start mid-expression are still left unresolved.
**Read:** ruff `directives.rs` and `noqa.rs`; ast-grep `combined.rs` (test `test_ignore_multiline_node_same_line`).

### Pattern 3: Region or block scoping

**Used by:**
- ESLint `disable`/`enable`: the region between the markers, matched on the diagnostic's start position (`applyDirectives` compares each directive's location with the problem's start line and column).
- oxlint block `disable`: matched by span overlap. Its line directives match on the start only.
- Biome `biome-ignore-start`/`end`: the region between the markers.
- jscpd `jscpd:ignore-start`/`end` and PMD CPD `CPD-OFF`/`CPD-ON`: matched by markers.
- Pylint: the block of the def, class, or module whose opening line carries `disable=`. Pylint is the only peer with real AST block scoping, and only for those three constructs.

**Strengths:** Covers spans of any size without grammar knowledge, except in Pylint's case.
**Weaknesses:** Needs unterminated and unused checks.
**Read:**
- eslint `apply-disable-directives.js`
- biome `suppressions.rs` (`RangeSuppressions`)
- pylint `utils/file_state.py`
- secondary for jscpd and CPD: their docs

### Pattern 4: Drop suppressed lines before duplicate detection

**Used by:** Pylint R0801 (`symilar.py`, via `line_enabled_callback`); jscpd and CPD regions.
**How it works:** Suppressed lines never reach the similarity algorithm, so no finished duplicate is ever matched to an owner.
**Weaknesses:** A disabled line inside a block splits or shrinks the duplicate instead of suppressing it.

### Pattern 5: Node scope for one construct only

**Used by:** react-doctor (after #158/#165)
**How it works:** Line scoping everywhere, except that a directive directly above a multi-line JSX element covers the element's whole range.
**Read:** secondary: the issue text only; the PR's code was not opened.

## Anti-Patterns

- **Matching by overlap with an enclosing node.** oxlint rejects this explicitly for line directives. A statement-containment rule has the same risk unless the owner is the innermost node and matching is by equality, as house-lint's Python rule is.
- **A suppression that silently does nothing.** This is the reported failure in Biome #9469 and react-doctor #158. An unused-suppression check is the mitigation; HSL900 already plays that role for file rules.

## Gaps

- I did not confirm whether Biome honours suppressions on a partially recovered tree when linting.
- I did not read the react-doctor JSX code, or the jscpd and CPD source.
- ESLint runs no rules on a parse failure (`linter.js`, `parseResult.ok`). This was confirmed from the code, not exercised.

## Relevance to Us

No peer has per-grammar statement-owner kinds. Seven of them use line scoping matched on the diagnostic's start. The one AST-block peer, Pylint, covers only def, class, and module. Pylint's duplicate checker removes suppressed lines before matching rather than matching owners afterwards.

Spec 005 D15's owner-kind table (statements, class members, object methods, JSX, decorators, `export` wrappers) is machinery no peer carries. The parent ratified it to make TypeScript behave exactly like Python. Peers accept that a line directive must sit on the line where the diagnostic starts. house-lint's text and JSON output already print that line.

For parse errors, ESLint and oxlint run no rules at all. house-lint already planned something more lenient: keep findings, disable pragmas. No peer rewards a byte search for directives.

## Recommendation

Use line scoping for TypeScript, matched on the occurrence's start line:
- A trailing `ignore` owns its own line.
- A comment-only `ignore-next` owns the next line that isn't blank or comment-only.
- `ignore-file` sits before the first token.

This is what ESLint, Biome, oxlint, and Deno do, and it needs no grammar table. The known cost is interior-line starts in multi-line JSX and chained expressions. Peers accept that cost, and react-doctor's JSX-only node scope is the one targeted mitigation if it ever bites. Keep tree-sitter for what D15 needed it for: finding comments reliably.

## Sources

### Reference implementations
- https://github.com/eslint/eslint/blob/main/lib/linter/apply-disable-directives.js — line directives become disable/enable pairs; matched on start
- https://github.com/biomejs/biome/blob/main/crates/biome_analyze/src/suppressions.rs — `biome-ignore` covers the next line; `text_range.contains(signal.start)`
- https://github.com/oxc-project/oxc/blob/main/crates/oxc_linter/src/disable_directives.rs — start-only matching, with the stated rationale
- https://github.com/denoland/deno_lint/blob/main/src/ignore_directives.rs — line-keyed `deno-lint-ignore`
- https://github.com/astral-sh/ruff/blob/main/crates/ruff_linter/src/directives.rs — `NoqaMapping` remaps for multi-line strings
- https://github.com/ast-grep/ast-grep/blob/main/crates/config/src/combined.rs — `ast-grep-ignore`, start line plus same-line sibling
- https://github.com/semgrep/semgrep/blob/develop/src/reporting/Nosemgrep.ml — first line and previous line
- https://github.com/pylint-dev/pylint/blob/main/pylint/utils/file_state.py — block scoping for def/class/module
- https://github.com/pylint-dev/pylint/blob/main/pylint/checkers/symilar.py — lines dropped before similarity

### Issues
- https://github.com/biomejs/biome/issues/9469 — multi-line JSX suppression has no effect
- react-doctor #158 — the same complaint, fixed with JSX-only node scope (not opened)

### Documentation
- https://biomejs.dev/analyzer/suppressions/
- https://eslint.org/docs/latest/use/configure/rules#using-configuration-comments
