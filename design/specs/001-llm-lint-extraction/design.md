# Design: Standalone Python House-Style Linter

**Date:** 2026-08-10
**Status:** approved
**Scope-mode:** hold
**Research:** `design/specs/001-llm-lint-extraction/research.md`

## Problem

Hassette contains seven custom Python checks that encode recurring code-quality failures Jessica wants to prevent across all of her Python projects. They currently exist as repository-local scripts with duplicated file reads and AST parsing, Hassette-specific scan roots, separate process entry points, and incompatible exemption comments. Reusing them requires copying code and creates immediate drift.

The new package must make this house style installable through `uvx`, `pipx`, and pre-commit-like hooks. It is intentionally opinionated and publicly available, but v0.1 does not attempt to accommodate other teams' conventions or infer whether code was authored by an LLM.

## Goals

- Publish an installable Python 3.11+ package with one Cyclopts CLI.
- Ship all seven portable checks with four enabled by default and three opt-in.
- Run `house-lint check` from a repository root with deterministic configuration, discovery, reporting, and exit behavior.
- Support explicit filenames for pre-commit-style invocation.
- Replace checker-specific annotations with one statement- and file-aware suppression grammar requiring rule IDs and reasons.
- Produce deterministic human and JSON results without hiding incomplete scans.
- Preserve or deliberately generalize every relevant Hassette checker behavior through a documented matrix and characterization tests.
- Validate the package against Hassette and one other Jessica-owned Python project without modifying either consumer repository.

## Non-Goals

- Modify Hassette dependencies, hooks, CI, annotations, scripts, or tests.
- Preserve Hassette's old script paths or checker-specific suppression comments.
- Support plugins, arbitrary user-authored lint rules, or a generic regex DSL.
- Integrate as a Ruff, Flake8, or Pylint plugin.
- Detect code authorship or claim that findings prove LLM generation.
- Support TypeScript, frontend, Markdown, documentation-site, release, schema, or Hassette architecture checks.
- Include duplicate-code detection, Java/PMD tooling, autofix, SARIF, hosted CI actions, or editor integrations.

## User Scenarios

### Jessica: Repository maintainer
- **Goal:** enforce the same Python house style across projects
- **Context:** local development from a repository root

#### Run a full repository check

1. **Run `uvx house-lint check`**
   - Sees: selected root, config path or `<none>`, enabled rule IDs, deterministic findings/errors, and final counts.
   - Decides: whether to fix a finding or add a reasoned suppression.
   - Then: receives an exit status distinguishing clean, findings, usage/config errors, incomplete scans, and internal failures.

2. **Add project configuration**
   - Sees: documented `[tool.house-lint]` keys and rule-specific options.
   - Decides: which opt-in rules to enable and their project vocabulary or thresholds.
   - Then: later invocations discover the same root and configuration deterministically.

### Hook runner: pre-commit caller
- **Goal:** check only passed Python files
- **Context:** a hook invokes the installed console script with staged filenames

#### Check changed files

1. **Invoke `house-lint check path/a.py path/b.py`**
   - Sees: stable text output or JSON and one process exit code.
   - Decides: nothing; the hook consumes the result.
   - Then: only qualifying, in-root, non-excluded Python files are scanned.

## Functional Requirements

- **FR#1** The console script and `python -m house_lint` expose equivalent `check` and `rules` commands.
- **FR#2** With no paths, `check` scans configured include roots; with paths, it scans only qualifying passed Python files.
- **FR#3** Explicit `--root` fixes the project boundary and limits config discovery to that directory; without it, upward config/project discovery begins at the current working directory.
- **FR#4** Configuration precedence is CLI options over `[tool.house-lint]` over built-in defaults.
- **FR#5** `HSL001` flags its fixed built-in AI-writing tells in Python comments and docstrings, excluding ordinary string literals.
- **FR#6** `HSL002` flags imports inside function and async-function bodies.
- **FR#7** `HSL003` flags top-level `TYPE_CHECKING` guards followed by later top-level imports.
- **FR#8** `HSL004` flags uppercase module constants after the first class/function unless the preserved dependency heuristic exempts them.
- **FR#9** `HSL101` is opt-in and flags configured spec-token families only in their declared comment, docstring, and filename scopes.
- **FR#10** `HSL102` is an opt-in file-length rule that flags Python files whose `splitlines()` count exceeds configured `max_lines`.
- **FR#11** `HSL103` is opt-in and flags bound exception variables not named `exc` or ending in `_exc`.
- **FR#12** Machine-readable findings contain rule ID, root-relative path, start/end location, and message; compact text diagnostics render the start location; all findings are deterministically ordered.
- **FR#13** Operational failures are represented separately from lint findings and cannot produce a clean result.
- **FR#14** The CLI supports deterministic text and JSON output containing scan metadata, findings, errors, and summary counts.
- **FR#15** A trailing `ignore[...]` comment attaches to its containing Python statement and suppresses owned findings for its listed enabled rule IDs.
- **FR#16** A comment-only `ignore-next[...]` attaches to the next Python statement and suppresses owned findings for its listed enabled rule IDs.
- **FR#17** A top-of-file `ignore-file[...]` suppresses all findings for its listed enabled rule IDs in that file.
- **FR#18** Every suppression requires at least one explicit rule ID and a meaningful reason.
- **FR#19** Malformed, misplaced, unknown, disabled, duplicate/conflicting, and unused suppressions produce `HSL900` findings.
- **FR#20** The package respects explicitly loaded project-root `.gitignore` patterns and configured excludes.
- **FR#21** The CLI returns the documented exit category with incomplete-scan and internal-error categories taking precedence over lint findings.
- **FR#22** `house-lint rules` lists all built-in rules, enablement mode (`default`, `opt-in`, or `always`), and concise descriptions in text or JSON.

## Edge Cases

- No explicit root/config and no ancestor `.git` or `pyproject.toml` marker exists: use the current working directory as root and built-in defaults.
- An explicit `--config` is missing, unreadable, invalid TOML, or lacks valid rule configuration: return configuration exit code without scanning.
- Explicit paths are strict: missing, out-of-root, or existing non-Python files are path errors. Explicit directories are valid and recursively expanded; discovered ignored, excluded, non-Python, and duplicate files are deterministically filtered. Resolve the root and every explicit path before containment checks. Do not follow directory symlinks. Accept a directly passed symlinked Python file only when its resolved target remains inside the root. State when no files remain.
- A full scan has no existing configured include roots or finds no Python files: succeed with an explicit empty-scan message and zero files.
- A selected file uses a PEP 263 encoding cookie: decode through `tokenize.open()` semantics.
- A selected file cannot be read or decoded, contains invalid Python, or cannot be tokenized: record a structured error, continue independent files, and return incomplete-scan status.
- A requested directory cannot be fully enumerated because of permission, disappearance, symlink, or other traversal failure: record a `traversal` error for that directory, continue other reachable paths, retain their findings, and return incomplete-scan status.
- A rule does not require every parsed representation: tokenization/AST failure still makes the selected Python file incomplete rather than allowing a partial green scan.
- Multiple findings for one rule belong to one statement: one matching statement suppression consumes all of them.
- A suppression lists multiple rule IDs: each enabled ID must consume at least one owned finding or that ID receives an unused diagnostic.
- A disabled-rule ID appears in a suppression: report it as unused through `HSL900`.
- Both statement and file suppression target the same finding, or two pragmas claim the same statement/rule: report conflicting ownership rather than choosing silently.
- Pragma-looking text inside strings or docstrings is not parsed as a suppression.
- A trailing pragma is on a multiline statement continuation: attach it to the AST statement containing that comment position.
- `ignore-next` is separated from the next statement by blank lines or ordinary comments: it still attaches to the next statement in the same lexical suite; crossing into or out of another suite is invalid.
- `ignore-file` may follow a shebang, encoding cookie, blank lines, ordinary leading comments, a module docstring, and `__future__` imports; appearance after any other statement is wrong placement.
- File-length findings have no statement owner and can only be suppressed by `ignore-file[HSL102]`.
- Spec-token configuration exceeds prefix/spec/finding limits or uses invalid prefixes/scopes: reject configuration before scanning.
- A selected path is not a regular file, exceeds 10 MiB, discovery exceeds 100,000 qualifying files, or any file exceeds 10,000 retained candidate findings: record a `budget`/path error, stop processing the affected file or scan, retain completed results, and return exit 3.
- Constants in modules using postponed annotations remain subject to the preserved annotation-reference heuristic; the rule is stylistic, not a runtime dependency proof.

## Acceptance Criteria

- **AC#1** `uv run pytest` passes unit and CLI integration tests for all seven rules, discovery, configuration, source parsing, reporters, exit codes, and suppressions. (FR#1-FR#22)
- **AC#2** A subprocess fixture invoking `house-lint check` on a clean repository exits 0 and emits valid deterministic text and JSON summaries. (FR#1, FR#12-FR#14, FR#21)
- **AC#3** Fixture repositories for findings, invalid config, syntax/decode/read errors, and simulated internal failure produce exit codes 1, 2, 3, and 4 respectively. (FR#13, FR#21)
- **AC#4** Ported Hassette characterization cases pass for the seven detector behaviors, with only matrix-documented changes. (FR#5-FR#11)
- **AC#5** Suppression characterization tests prove same-statement, next-statement, and file attachment; multiple IDs/findings; mandatory reasons; and all `HSL900` error cases. (FR#15-FR#19)
- **AC#6** Discovery tests prove root/config precedence, explicit-path behavior, root `.gitignore`, configured excludes, stable sorting/deduplication, and empty scans. (FR#2-FR#4, FR#20)
- **AC#7** A read-only acceptance command against `/home/jessica/source/hassette` completes deterministically and records findings plus every documented delta from the original scripts. (FR#5-FR#14)
- **AC#8** A read-only acceptance command against `/home/jessica/source/claude-code-recall` completes deterministically without crashes, and its findings are recorded as legitimate house-style hits or rule false positives. (FR#5-FR#14)
- **AC#9** `house-lint rules --format json` returns all `HSL001-HSL004`, `HSL101-HSL103`, and `HSL900` entries with stable IDs and enablement modes. (FR#22)

## Key Constraints

- The package encodes Jessica's house style; do not generalize defaults to accommodate unrelated conventions.
- Runtime dependencies are allowed only when they replace a solved problem better than bespoke code. Use Cyclopts and `pathspec`; do not add Pydantic or a project-root library in v0.1.
- Do not create a plugin API, generic regex engine, autofix framework, compatibility layer, or Hassette migration path.
- Do not silently swallow selected-file read, decode, tokenize, parse, configuration, or internal errors.
- Do not derive suppression semantics from physical-line substring matching.
- Do not scan ordinary Python string literals for prose/spec-token findings.
- Do not shell out to Git for discovery or ignore matching.

## Dependencies and Assumptions

- Python 3.11+ is available.
- Cyclopts supports variadic path parameters, console/module entry points, and explicit process-exit handling; this was confirmed against its documentation and a read-only spike during blind-spot review.
- `pathspec` compiles and matches supplied Git-ignore-style patterns but does not discover ignore files. V0.1 deliberately loads only the project-root `.gitignore` plus configured excludes.
- The selected names are distribution/CLI `house-lint`, import package `house_lint`, pragma prefix `house-lint:`, and rule namespace `HSL`. On 2026-08-10, both the PyPI JSON endpoint for `house-lint` and GitHub's `NodeJSmith/house-lint` repository endpoint returned 404. This confirms no visible project occupied either name at verification time, though only publication/reservation prevents a later race.
- Text/JSON schemas, rule IDs, exit codes, configuration keys, and pragma grammar become compatibility surfaces. Pre-1.0 changes remain possible but require documented migration notes.
- General file suppressions can hide broad classes of findings. Mandatory IDs/reasons, top-of-file placement, conflict detection, and unused diagnostics mitigate that accepted cost.
- The two acceptance repositories are available locally during implementation; acceptance does not modify them or become part of package unit-test isolation.

## Architecture

### Package Structure

```text
pyproject.toml
src/house_lint/
  __init__.py
  __main__.py
  cli.py
  config.py
  discovery.py
  results.py                 # public result DTOs and schema conversion
  analysis.py                # private candidate/provenance/statement types
  registry.py
  source.py
  suppressions.py
  rules/
    llm_cruft.py
    lazy_imports.py
    type_checking_position.py
    constants_position.py
    spec_tokens.py
    file_length.py
    exception_names.py
  reporters/
    text.py
    json.py
tests/
  unit/
  integration/
```

`cli.py` owns Cyclopts declaration and the sole conversion from `ScanResult` to process output/exit status. Rule modules never print or terminate the process.

### Core Models

Use frozen standard-library dataclasses with a hard module boundary:

- `results.py` owns public `Finding`, `LintError`, `ScanResult`, rule-list DTOs, and explicit schema-version conversion. Reporters import only this module.
- `analysis.py` owns private `CandidateFinding`, `StatementKey`, source-kind/owner types, and analysis state. These types are never serialized or imported by reporters/configuration.
- Registry metadata remains private to `registry.py`; typed rule-option dataclasses remain private to `config.py` or their rule module.

Public DTO details:

- `Finding`: `rule_id`, root-relative POSIX `path`, nullable `line`, `column`, `end_line`, `end_column`, and message. Statement/source findings require all four 1-based location fields; file-level findings (including filename HSL101 and file-length HSL102) require all four to be `null`. Internal AST/token zero-based columns are normalized at construction.
- `LintError`: stable diagnostic `code`, kind (`config`, `path`, `traversal`, `budget`, `read`, `decode`, `tokenize`, `syntax`, `internal`), optional root-relative POSIX path, nullable start/end location, phase, operation, optional rule ID, and safe redacted message.
- `ScanResult`: nullable root, nullable config path, enabled rule IDs, scanned/skipped counts, findings, suppressed count, errors, and derived summary. Root is non-null once root resolution succeeds; usage/config errors that occur before root resolution use `null`.

File accounting is deterministic: `files_scanned` counts selected Python files that reached rule execution; `files_skipped` counts unique existing files encountered during explicit-directory or full discovery that were filtered because they are non-Python, ignored, configured/built-in excluded, or duplicate resolved paths. Missing/out-of-root/explicit non-Python arguments, special/oversized files, traversal failures, and read/decode/tokenize/parse failures are errors, not skipped files. Explicit files filtered by ignore/exclude contribute to `files_skipped`. Directory entries that are themselves directories are not counted. A file contributes to exactly one of scanned, skipped, or error accounting.

JSON output is a versioned object with `schema_version: 1`, not serialized dataclasses by accident:

```json
{
  "schema_version": 1,
  "root": "/repo",
  "config": "/repo/pyproject.toml",
  "enabled_rules": ["HSL001"],
  "files_scanned": 12,
  "files_skipped": 2,
  "findings": [],
  "errors": [],
  "summary": {
    "finding_count": 0,
    "error_count": 0,
    "suppressed_count": 0
  }
}
```

Every documented field is emitted even when its value is `null`. A non-empty source finding is:

```json
{"rule_id":"HSL002","path":"src/app.py","line":12,"column":5,"end_line":12,"end_column":24,"message":"import inside function body"}
```

A filename finding is:

```json
{"rule_id":"HSL101","path":"tests/T01_example.py","line":null,"column":null,"end_line":null,"end_column":null,"message":"spec token T01 in filename"}
```

An error is:

```json
{"code":"syntax-error","kind":"syntax","path":"src/broken.py","line":4,"column":8,"end_line":4,"end_column":9,"phase":"analysis","operation":"ast-parse","rule_id":null,"message":"invalid syntax"}
```

`house-lint rules --format json` emits `{"schema_version":1,"rules":[...]}` where each rule object contains `id`, `name`, `description`, and `enablement` (`default`, `opt-in`, or `always`). Schema version 1 may add fields; consumers must ignore unknown fields. Removing fields, renaming fields, changing field types/nullability, or changing location/path semantics requires a schema-version increment.

Sort findings by `(path, line, column, rule_id, message)` and errors by `(path-or-empty, line-or-zero, kind, message)`.

### Source Processing

`SourceFile` reads each selected file once and lazily caches decoded text, lines, tokens/comments, AST, docstring spans, and statements. Decode with `tokenize.open()` to honor PEP 263 cookies. Parse with the selected runtime's `ast.parse(..., filename=...)`. A selected Python file must be fully readable, tokenizable, and parseable before rules run; failures create `LintError` and no rule findings for that file.

V0.1 safety limits are fixed guardrails, not configuration: only regular files are scanned; each file is at most 10 MiB; one invocation discovers at most 100,000 qualifying files; one file retains at most 10,000 candidate findings across rules. Exceeding a limit produces a structured `budget` or path error and exit 3 rather than relying on process exhaustion. Process files sequentially and release each `SourceFile` after its candidates are suppressed/serialized so full parsed source representations do not accumulate across the repository.

This centralizes behavior currently repeated in Hassette's individual `check_file()` functions and preserves `lint_helpers.docstring_spans()` semantics.

### Registry and Rules

The static registry contains:

| ID | Default | Name |
|---|---:|---|
| `HSL001` | on | AI-writing cruft |
| `HSL002` | on | Lazy imports |
| `HSL003` | on | `TYPE_CHECKING` position |
| `HSL004` | on | Constants position |
| `HSL101` | off | Spec tokens |
| `HSL102` | off | File length |
| `HSL103` | off | Exception names |
| `HSL900` | always | Suppression diagnostics |

`HSL900` cannot be disabled or suppressed; otherwise suppression errors could recursively hide their own invalidity.

The registry is deliberately not a generic `RuleDefinition` framework. It contains a narrow immutable metadata table (`id`, `name`, `description`, `enablement`, ownership scope) and a separate explicit ID-to-detector dispatch map. `config.py` owns TOML/CLI precedence, effective rule selection, explicit per-rule validators, and construction of typed option dataclasses. Detectors receive already-validated options and never inspect raw TOML or decide whether they are enabled. Add abstraction only if a future concrete rule shape demonstrates repeated protocol needs.

Rules consume `SourceFile` and typed rule configuration and return private `CandidateFinding` values with explicit provenance. Statement rules assign a `StatementKey`, file-wide rules assign file ownership, and filename findings assign no source owner. The suppression engine runs once after all enabled rules for a file, partitions candidates into visible and suppressed sets, adds `HSL900` candidates, and only then converts visible candidates to public `Finding` DTOs.

### Per-Rule Behavior Matrix

| Rule | Preserve | Generalize | Drop |
|---|---|---|---|
| `HSL001` | Existing divider/filler patterns; comment/docstring-only scope; ordinary strings excluded | Unified statement/file suppression | Hassette's no-exemption policy |
| `HSL002` | AST function-depth detection including async/method/nested imports | Unified statement/file suppression | `# lazy-import:` syntax and raw-line attachment |
| `HSL003` | Top-level guard forms and later-import detection | Unified statement/file suppression | No-suppression behavior |
| `HSL004` | Uppercase/dunder/derived-binding heuristic including annotations | Unified statement/file suppression; document heuristic under future annotations | `# constant-after-def:` syntax |
| `HSL101` | Comment/docstring/filename scopes, data-string exclusion, filename segmentation, time guard | Configured constrained token families and unified suppression | Hardcoded-only vocabulary, raw regex configuration, no-suppression policy |
| `HSL102` | `splitlines()` line count and strict `>` threshold | Configurable threshold and general file suppression | `# file-size-exempt:` syntax and warning-text/exit mismatch |
| `HSL103` | `exc`/`*_exc` AST detection | Unified statement/file suppression | No-suppression policy |
| Shared helpers | Docstring/comment extraction intent, deterministic ordering/deduplication | Shared source cache, project discovery, pathspec matching, structured errors | Hardcoded Hassette roots and silent tokenize/read/parse failures |

### Suppression Grammar and Ownership

Suppressions are recognized only in `tokenize.COMMENT` tokens:

```python
call()  # house-lint: ignore[HSL001,HSL004] - required reason

# house-lint: ignore-next[HSL002] - required reason
from package import value

# house-lint: ignore-file[HSL001,HSL102] - generated compatibility module
```

Grammar rules:

- Prefix is exactly `house-lint:`.
- Actions are `ignore`, `ignore-next`, and `ignore-file`.
- Brackets contain one or more comma-separated canonical IDs; whitespace around commas is allowed; duplicate IDs are invalid; globs, categories, `all`, and `HSL900` are invalid.
- The closing bracket must be followed by ` - ` and a reason containing at least three alphanumeric characters. This rejects empty and punctuation-only reasons without judging prose quality.
- `ignore` must be trailing within an AST statement span.
- `ignore-next` must be on a comment-only line and attaches to the next statement in the same lexical suite.
- `ignore-file` must appear before the first statement other than a module docstring or `__future__` import and applies to every enabled listed rule in the file.
- A statement pragma consumes all candidate findings for each listed rule whose span is owned by that statement. A file pragma consumes all candidate findings for each listed rule in the file.
- Every listed ID must consume at least one finding. Unknown, disabled, unconsumed, misplaced, malformed, duplicate, or conflicting IDs produce `HSL900` at the pragma location.
- When statement and file pragmas both claim the same rule/finding, or multiple pragmas claim the same owner/rule, ownership conflicts fail closed: none of the conflicting pragmas suppresses that candidate, the original finding remains visible, and each conflicting pragma produces `HSL900`.

### Spec-Token Configuration

No raw regex escape hatch in v0.1. `HSL101` accepts constrained token families:

```toml
[tool.house-lint.rules.HSL101]
max_findings_per_file = 200

[[tool.house-lint.rules.HSL101.tokens]]
prefixes = ["AC", "FR", "NFR", "WP"]
hash = "optional"
min_digits = 1
suffix = "optional-lower-alpha"
scopes = ["comments", "docstrings", "filenames"]
case_sensitive = true

[[tool.house-lint.rules.HSL101.tokens]]
prefixes = ["T"]
min_digits = 2
not_followed_by_time = true
scopes = ["comments", "docstrings", "filenames"]
```

Validate at startup: at most 32 token families, at most 32 prefixes per family, each prefix at most 12 characters and matching `[A-Z][A-Z0-9_]*`, supported enum values/scopes only, positive bounded digit counts, and positive finding cap. Compile internal regexes once after validation. Filename matching preserves segmentation on `.`, `_`, and `-`; case sensitivity is explicit per family rather than silently differing by scope.

Normative token-family table schema:

| Key | Type | Required/default | Validation |
|---|---|---|---|
| `prefixes` | array of strings | required, non-empty | at most 32 unique values; each matches `[A-Z][A-Z0-9_]*` and is at most 12 characters |
| `scopes` | array of strings | required, non-empty | unique subset of `comments`, `docstrings`, `filenames` |
| `hash` | string enum | default `forbidden` | `forbidden`, `optional`, or `required` |
| `min_digits` | integer | default `1` | 1 through 12; booleans invalid |
| `max_digits` | integer or absent | default absent (unbounded within token text) | when present, at least `min_digits` and at most 12 |
| `suffix` | string enum | default `none` | `none` or `optional-lower-alpha` |
| `case_sensitive` | boolean | default `true` | exact TOML boolean only |
| `not_followed_by_time` | boolean | default `false` | exact TOML boolean only |

These are the only allowed token-family keys in schema version 1.

### Configuration and Discovery

Precedence:

1. Resolve `--root` when supplied; it fixes the project boundary.
2. Resolve `--config PATH` when supplied. Without `--root`, the explicit config's parent becomes the project root. With `--root`, the config must be inside that root. Missing, unreadable, out-of-root, or invalid explicit config is a configuration error.
3. Without explicit config, an explicit root reads only `<root>/pyproject.toml` when it contains `[tool.house-lint]`; never search above the explicit boundary.
4. Without explicit root or config, search upward from the current working directory for the nearest `pyproject.toml` containing `[tool.house-lint]`; if none exists, use the nearest ancestor containing `.git` or any `pyproject.toml`, else the current working directory.
5. CLI `--select`/`--ignore` override configured selection; configured selection overrides defaults. `HSL900` remains enabled.

The top-level config surface is:

```toml
[tool.house-lint]
include = ["src", "tests", "scripts", "tools", "examples"]
exclude = []
select = ["HSL001", "HSL002", "HSL003", "HSL004"]
ignore = []
```

`include` is an array of root-relative file or directory paths, not globs. Directories are recursively scanned. An omitted `include` uses the shown defaults; an explicit empty array selects no full-scan roots. `exclude` is an array of root-relative Git-ignore-style patterns. `select` replaces the default selection; `ignore` subtracts from it. Unknown or duplicate IDs are configuration errors, and `HSL900` cannot appear in either list.

Normative configuration schema:

| Key | Type | Omitted default | Empty/invalid behavior |
|---|---|---|---|
| `tool.house-lint.include` | array of strings | `src`, `tests`, `scripts`, `tools`, `examples` | empty means an intentional empty full scan; absolute or parent-traversing paths are invalid |
| `tool.house-lint.exclude` | array of strings | empty, added after built-in excludes | empty adds nothing; invalid Git-ignore patterns are config errors |
| `tool.house-lint.select` | array of rule IDs | `HSL001-HSL004` | empty selects no ordinary rules; unknown, duplicate, or `HSL900` IDs are invalid |
| `tool.house-lint.ignore` | array of rule IDs | empty | subtracts after selection; unknown, duplicate, or `HSL900` IDs are invalid |
| `rules.HSL101.tokens` | non-empty array of token-family tables | no default | required whenever HSL101 is selected; invalid/empty families are config errors |
| `rules.HSL101.max_findings_per_file` | positive integer | `200` | zero, negative, boolean, or above `10_000` is invalid |
| `rules.HSL102.max_lines` | positive integer | `800` | zero, negative, boolean, or above `10_000_000` is invalid |
| `rules.HSL103.allowed` | array of exact names or suffix-wildcard patterns | `["exc", "*_exc"]` | each wildcard entry must contain exactly one leading `*` followed by a valid identifier suffix; empty, duplicate, or other wildcard placement is invalid |

Rule tables do not contain `enabled`; effective enablement has one owner: top-level selection plus CLI overrides. A rule-specific table may exist while its rule is disabled, and is still validated so latent invalid configuration cannot be ignored. Unknown top-level or rule-table keys are configuration errors in schema version 1.

Selection algorithm: compute the configured set by taking configured `select` or its default and subtracting configured `ignore`. When CLI `--select` is present, it replaces that entire configured result; otherwise retain the configured set. Finally subtract CLI `--ignore`. `HSL900` is added after selection and cannot be removed. Selected HSL101 requires a valid token configuration; HSL102/HSL103 use their documented defaults when their tables are omitted.

With no positional paths, scan existing configured include roots. With positional paths, resolve relative to root, deduplicate, and scan only those qualifying files. Directories passed explicitly are recursively expanded because Cyclopts callers commonly pass either files or directories; all results remain root-bound.

Directory discovery uses an error-reporting walker rather than an expression that silently drops enumeration failures. Every requested include/explicit directory is either fully enumerated or contributes a structured `traversal` error and exit 3; reachable sibling paths continue scanning.

Manual and hook invocations share this strict path contract. The package's pre-commit hook metadata filters eligible Python files before invocation, following the normal pre-commit model; the CLI does not add a separate tolerant hook mode or silently discard invalid explicit arguments.

Load patterns only from the root `.gitignore`, built-in excludes, and `[tool.house-lint].exclude`. `pathspec` performs matching. Do not recursively discover nested `.gitignore` files or shell out to Git in v0.1. Built-in excludes are `.git/`, `.venv/`, `.nox/`, `__pycache__/`, `site-packages/`, and `node_modules/`.

`--no-gitignore` disables only loading the root `.gitignore`; built-in and configured excludes still apply.

### CLI and Exit Contract

Cyclopts commands:

```text
house-lint check [PATH ...] [--config PATH] [--root PATH]
                 [--format text|json] [--select IDS] [--ignore IDS]
                 [--no-gitignore] [--debug]
house-lint rules [--format text|json]
```

Text findings use only the start position for compact editor-compatible diagnostics:

```text
path/to/file.py:12:5: HSL002 import inside function body
```

Filename findings omit a fabricated location:

```text
tests/T01_example.py: HSL101 spec token T01 in filename
```

Text output starts with root/config/enabled rules/file count and ends with finding/error/suppressed counts. JSON follows schema version 1. Both formats include results gathered before an incomplete scan was detected.

The JSON `root` field is an absolute path string after successful root resolution and `null` only when usage/config failure prevents resolution. The JSON `config` field is an absolute path string when configuration was loaded and `null` when no configuration file applies or resolution did not complete. Text uses `<none>` for null values.

Exit codes:

- `0`: completed scan with no visible findings or errors; an empty full scan is clean but explicit.
- `1`: completed scan with lint or `HSL900` findings.
- `2`: CLI usage or configuration error; scanning does not start.
- `3`: incomplete scan due to path/read/decode/tokenize/syntax errors, even when findings also exist.
- `4`: unexpected internal exception caught at the CLI boundary.

Exit precedence is `4 > 3 > 2 > 1 > 0`; code 2 occurs pre-scan, so it cannot coexist with scan results.

Normal errors always include stable code, phase, operation, and available path/rule context without source text or secrets. `--debug` additionally writes the caught traceback to stderr for exit 4 and chained exception details for operational errors; it never corrupts JSON stdout.

Output/exit contract:

| Exit | Text mode | JSON mode | Partial results |
|---:|---|---|---|
| `0` | complete summary on stdout | valid schema-v1 object on stdout | complete, no findings/errors |
| `1` | findings and complete summary on stdout | valid schema-v1 object on stdout | complete findings, no operational errors |
| `2` | usage/config diagnostic on stderr; no normal stdout | valid schema-v1 check-result object on stdout with resolved-or-null root/config, zero files/findings, and the config/usage error in `errors` | scan not started, zero findings |
| `3` | reachable-file findings/summary on stdout and operational diagnostics on stderr | one valid schema-v1 object on stdout containing partial findings and all errors | explicitly incomplete |
| `4` | safe internal-error diagnostic on stderr; any fully assembled prior results on stdout | one valid schema-v1 object on stdout containing internal error and any fully assembled prior results | explicitly incomplete; traceback only on stderr with `--debug` |

JSON stdout is always parseable for every exit when `--format json` is selected; no logs, hints, or tracebacks are written to stdout. Text-mode pre-commit callers treat every nonzero exit as blocking and display both captured streams; exit-code distinctions are primarily for diagnosis and machine callers, not different hook pass/fail outcomes.

`--select` and `--ignore` are repeatable and each occurrence accepts one comma-separated ID list; whitespace around IDs is ignored. Values across occurrences are flattened in argument order. Unknown IDs, duplicate IDs within or across occurrences, and any attempt to select/ignore `HSL900` are usage errors. Supplying `--select` replaces configured selection; `--ignore` then subtracts from the resulting set.

## Implementation Preferences

- Use Python 3.11+ and `uv_build` with a `src/house_lint` layout.
- Use Cyclopts for command parsing/help and explicit exit handling.
- Use `pathspec` for matching explicitly loaded Git-ignore-style patterns.
- Use standard-library frozen dataclasses for internal/public result models and explicit JSON dictionaries with `schema_version`.
- Use `tomllib`, `ast`, `tokenize`, and `pathlib` for configuration and Python analysis.
- Use pytest for unit and subprocess integration tests.
- Prefer pure detector functions returning findings over classes unless state is required.
- Parse each selected source once through `SourceFile`; do not let rules read files or print independently.
- Keep the static rule registry explicit; no dynamic loading or entry points.

## Replacement Targets

No code in this standalone repository is being replaced. Hassette scripts are source references only and remain outside this design's blast radius.

## Convention Examples

### Shared runner returns status instead of exiting internally

**Source:** `/home/jessica/source/hassette/tools/lint_helpers.py:32-68`

```python
def run_check(paths, repo_root, check, *, summary, ok, footer=None):
    violations = []
    for path in paths:
        rel = path.relative_to(repo_root)
        for lineno, message in check(path):
            violations.append((rel, lineno, message))
    # Formatting omitted: the helper returns a status for main() to exit with.
    return 1 if violations else 0
```

Follow the separation of detection from process exit, but replace printing/tuples with `ScanResult` and reporters.

### AST detector returns deterministic findings

**Source:** `/home/jessica/source/hassette/tools/check_type_checking_position.py:44-62`

```python
def check_file(path: Path) -> list[tuple[int, str]]:
    source = path.read_text()
    tree = ast.parse(source)
    violations = []
    for i, node in enumerate(tree.body):
        if not (isinstance(node, ast.If) and is_type_checking_guard(node.test)):
            continue
        later_import = next(
            (later for later in tree.body[i + 1 :] if isinstance(later, (ast.Import, ast.ImportFrom))),
            None,
        )
        if later_import is not None:
            violations.append((node.lineno, "if TYPE_CHECKING block followed by imports"))
    return violations
```

Preserve straightforward pure traversal; consume a cached tree and return typed `CandidateFinding` objects with explicit ownership.

### Token-aware text scanning excludes ordinary strings

**Source:** `/home/jessica/source/hassette/tools/check_llm_cruft.py:78-95`

```python
for tok in tokenize.generate_tokens(io.StringIO(source).readline):
    if tok.type != tokenize.COMMENT:
        continue
    body = comment_body(tok.string)
    if DIVIDER_RULE.match(body) or DIVIDER_WRAPPED.match(body):
        hits.add((tok.start[0], "section-divider comment"))

for start, end in docstring_spans(ast.parse(source)):
    for lineno in range(start, end + 1):
        ...
```

Retain semantic scopes, but source comments/docstrings from `SourceFile`.

### Characterization fixture keeps detector tests small

**Source:** `/home/jessica/source/hassette/tests/unit/tools/conftest.py:11-20`

```python
@pytest.fixture
def write_sample(tmp_path: Path) -> Callable[[str], Path]:
    def _write(content: str) -> Path:
        target = tmp_path / "sample.py"
        target.write_text(textwrap.dedent(content))
        return target
    return _write
```

Adapt this fixture to construct `SourceFile` instances and isolated fixture repositories.

## Alternatives Considered

- **Keep repository-local scripts:** rejected because reuse requires copying and suppression/discovery behavior drifts.
- **Build a Ruff/Flake8/Pylint plugin:** rejected because it couples the package to a host, Ruff does not support third-party rules, and the desired CLI/config contract is standalone.
- **Use Semgrep, ast-grep, Vale, or pre-commit pygrep:** rejected as the primary implementation because none combines exact Python comment/docstring/filename scopes with these AST house rules and one suppression contract. They remain alternatives for generic text policies.
- **Build a generic regex rule engine:** rejected because spec-token families cover the known configurable need without accepting arbitrary regex performance and compatibility burden.
- **Standard-library-only CLI and ignore matching:** rejected because Cyclopts and `pathspec` solve those concerns more robustly and are acceptable dependencies.
- **Pydantic result/config models:** rejected because explicit validation and simple dataclasses are sufficient; schema export is not a v0.1 requirement.
- **Recursive nested `.gitignore` semantics:** rejected for v0.1 complexity; root `.gitignore` plus config excludes is deterministic and accepted.

## Test Strategy

### Required Test Types

- **Unit:** every detector, config validator, source representation, suppression parser/ownership rule, discovery function, reporter serializer, and exit mapping.
- **Integration:** subprocess execution through console and module entry points, fixture repositories, text/JSON output, and process exits.
- **Acceptance:** read-only runs against Hassette and `claude-code-recall` to prove realistic repository compatibility and triage false positives.

### Existing Tests to Adapt

- `/home/jessica/source/hassette/tests/unit/tools/test_check_llm_cruft.py`
- `/home/jessica/source/hassette/tests/unit/tools/test_check_lazy_imports.py`
- `/home/jessica/source/hassette/tests/unit/tools/test_check_type_checking_position.py`
- `/home/jessica/source/hassette/tests/unit/tools/test_check_constants_position.py`
- `/home/jessica/source/hassette/tests/unit/tools/test_check_spec_tokens.py`
- `/home/jessica/source/hassette/tests/unit/tools/test_check_file_size.py`
- `/home/jessica/source/hassette/tests/unit/tools/test_check_exception_names.py`
- `/home/jessica/source/hassette/tests/unit/tools/test_lint_helpers.py`

These files are copied/adapted into this repository; the originals are not modified.

### New Test Coverage

- `SourceFile` encoding, tokenize/parse failure, cache, docstrings, comments, statement/suite ownership. (FR#5-FR#13)
- All suppression forms and `HSL900` lifecycle cases. (FR#15-FR#19)
- Config defaults, opt-ins, token-family limits, threshold validation, and precedence. (FR#4, FR#9-FR#11)
- Root/config/path discovery, ignore matching, deduplication, and empty scans. (FR#2-FR#4, FR#20)
- Text/JSON schemas, ordering, summaries, and all exit categories. (FR#12-FR#14, FR#21-FR#22)
- Read-only acceptance scripts or documented commands for the two target projects. (AC#7-AC#8)

### Tests to Remove

No tests are removed.

## Documentation Updates

- `README.md`: positioning as Jessica's opinionated house style, installation, quick start, default/opt-in rules, CLI examples, config, pre-commit integration, suppressions, exits, and non-goals.
- `docs/rules.md`: stable rule IDs, exact behavior, defaults, messages, configuration, and preserved/generalized source semantics.
- `docs/configuration.md`: discovery/precedence, include/exclude/gitignore behavior, constrained spec-token schema, and validation limits.
- `docs/suppressions.md`: grammar, statement/suite/file ownership, mandatory reasons, examples, and every `HSL900` case.
- `CHANGELOG.md`: pre-1.0 compatibility changes and migration notes.
- `.pre-commit-hooks.yaml`: distributable hook metadata invoking strict `house-lint check` with Python-file filtering.
- CLI help generated through Cyclopts docstrings must agree with README examples.

## Impact

### Changed Files

- `pyproject.toml` — create package metadata, Cyclopts/pathspec dependencies, console script, pytest tooling, and build configuration.
- `README.md` — create user-facing package documentation.
- `CHANGELOG.md` — create release history.
- `.pre-commit-hooks.yaml` — create distributable Python-file-filtered hook metadata.
- `src/house_lint/__init__.py` — create package metadata/public version boundary.
- `src/house_lint/__main__.py` — create module entry point.
- `src/house_lint/cli.py` — create Cyclopts commands and process boundary.
- `src/house_lint/config.py` — create TOML loading, typed config, and validation.
- `src/house_lint/discovery.py` — create root/config/file/ignore discovery.
- `src/house_lint/results.py` — create public findings, errors, scan/rule DTOs, and schema conversion.
- `src/house_lint/analysis.py` — create private candidate, provenance, statement-owner, and analysis models.
- `src/house_lint/source.py` — create cached decode/tokenize/AST/source model.
- `src/house_lint/suppressions.py` — create pragma parsing, attachment, consumption, and diagnostics.
- `src/house_lint/registry.py` — create static built-in rule registry.
- `src/house_lint/rules/*.py` — create seven detector modules.
- `src/house_lint/reporters/*.py` — create deterministic text and JSON output.
- `tests/unit/**` — create detector and infrastructure unit tests.
- `tests/integration/**` — create CLI subprocess and fixture-repository tests.
- `docs/rules.md` — create rule reference.
- `docs/configuration.md` — create configuration reference.
- `docs/suppressions.md` — create suppression reference.
- `design/specs/001-llm-lint-extraction/acceptance.md` — create read-only two-project validation record during implementation.

### Behavioral Invariants

- Rule IDs remain independent of package branding.
- Default rules are exactly `HSL001-HSL004`; opt-in rules are exactly `HSL101-HSL103`; `HSL900` is always enabled.
- Ordinary Python string literals remain outside LLM-cruft and spec-token scopes.
- No selected file failure is silently converted into a clean result.
- Output ordering is deterministic for identical inputs and configuration.
- Hassette behavior changes only where the per-rule matrix explicitly says generalize or drop; this is a source-reference invariant, not a Hassette integration guarantee.

### Blast Radius

The implementation changes only this new repository. Local acceptance reads Hassette and `claude-code-recall` but does not write to them. Future consumer adoption is separate work and may require configuration and suppression rewrites.

## Open Questions

No open questions remain. The design intentionally keeps atomic per-file parse gating for v0.1: malformed files return an incomplete-scan error and do not receive partial rule execution.
