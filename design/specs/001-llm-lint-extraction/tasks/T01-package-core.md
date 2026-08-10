---
task_id: "T01"
title: "Create package core and result contracts"
status: "done"
depends_on: []
implements: ["FR#12", "FR#13"]
---

## Summary

Create the installable package skeleton and the hard boundary between public result DTOs and private analysis provenance. Implement the schema-v1 conversion contract, deterministic ordering, and shared source representation with structured operational failures and fixed resource guardrails.

## Target Files

- create: `pyproject.toml`
- create: `src/house_lint/__init__.py`
- create: `src/house_lint/results.py`
- create: `src/house_lint/analysis.py`
- create: `src/house_lint/source.py`
- create: `tests/conftest.py`
- create: `tests/unit/test_results.py`
- create: `tests/unit/test_source.py`
- read: `design/specs/001-llm-lint-extraction/design.md`
- read: `/home/jessica/source/hassette/tools/lint_helpers.py`
- read: `/home/jessica/source/hassette/tests/unit/tools/test_lint_helpers.py`
- read: `/home/jessica/source/hassette/tests/unit/tools/conftest.py`

## Prompt

Create a Python 3.11+ `uv_build` project named `house-lint` with Cyclopts/pathspec runtime dependencies and pytest development tooling. Define the console-script metadata for `house-lint = house_lint.cli:main`; T06 will create that target function. Implement `results.py` public frozen dataclasses for `Finding`, `LintError`, `ScanResult`, and rule-list DTOs plus explicit schema-v1 dictionaries. Implement `analysis.py` private candidate/owner/statement types. Implement `SourceFile` in `source.py` using `tokenize.open()`, cached text/lines/tokens/AST/docstrings/statements, atomic per-file parse gating, structured errors, sequential-lifetime assumptions, and the fixed regular-file/10 MiB source guardrails. Define the shared 10,000-candidate limit constant/error shape, but leave enforcement while accumulating detector output to T06. Keep reporters/configuration from importing private analysis types.

Follow the design sections **Core Models**, **Source Processing**, and **CLI and Exit Contract**. Port/adapt shared helper characterization cases rather than importing Hassette modules.

## Focus

- Public source locations are 1-based; filename findings have all location fields null.
- JSON emits every field, including nulls; unknown fields may be added in schema v1 but types/nullability cannot change.
- `LintError` includes stable code/kind/path/location/phase/operation/rule/message.
- Do not retain parsed representations across files; `SourceFile` is per-file and releasable after suppression/report conversion.
- Atomic gating means AST/tokenization failure yields no rule candidates for that file.

## Verify

- [ ] FR#12: Unit tests prove deterministic finding/error ordering, root-relative POSIX paths, 1-based source spans, null filename spans, and exact schema-v1 finding/error serialization.
- [ ] FR#13: Unit tests prove source-level read/decode/tokenize/syntax/regular-file/10-MiB failures become structured `LintError` values and cannot be represented as a clean `ScanResult`; T06 verifies invocation-level candidate-budget and exit behavior.
