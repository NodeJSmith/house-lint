---
task_id: "T06"
title: "Build CLI, reporters, and exit handling"
status: "planned"
depends_on: ["T01", "T02", "T03", "T04", "T05"]
implements: ["FR#1", "FR#13", "FR#14", "FR#21", "FR#22", "AC#2", "AC#3", "AC#9"]
---

## Summary

Build the Cyclopts console/module interface, scan orchestration, deterministic text and JSON reporters, rule listing, structured stream ownership, debug behavior, and documented exit precedence.

## Target Files

- create: `src/house_lint/__main__.py`
- create: `src/house_lint/cli.py`
- create: `src/house_lint/reporters/__init__.py`
- create: `src/house_lint/reporters/text.py`
- create: `src/house_lint/reporters/json.py`
- create: `tests/integration/test_cli.py`
- create: `tests/integration/test_reporters.py`
- read: `src/house_lint/config.py`
- read: `src/house_lint/discovery.py`
- read: `src/house_lint/registry.py`
- read: `src/house_lint/results.py`
- read: `src/house_lint/suppressions.py`
- read: `design/specs/001-llm-lint-extraction/design.md`

## Prompt

Implement equivalent console and `python -m house_lint` Cyclopts commands `check` and `rules` against the console-script metadata established in T01. Orchestrate discovery, sequential SourceFile processing, detector dispatch, suppression, public conversion, and reporters. Implement exact repeatable/comma-separated select/ignore semantics. Text findings use start locations and filename findings omit fabricated locations. JSON emits the complete schema-v1 check-result for exits 0-4 and rule-list schema. Enforce stdout/stderr ownership, partial results, debug tracebacks, and exit precedence `4 > 3 > 2 > 1 > 0`. Add fixture-repository subprocess tests.

Enforce the invocation-level 10,000-candidate-per-file budget while accumulating detector output. T01 provides the constant/error primitive and per-file source guardrails; this orchestration owns candidate counting and conversion to a structured budget error.

## Focus

- Rule modules never print/exit; CLI is the only process boundary.
- JSON stdout must remain parseable for all exits; hints/debug/tracebacks never enter stdout.
- Exit 2 uses the same check-result schema with nullable root/config and zero scan counts.
- Text-mode hooks block on every nonzero exit; category distinctions aid diagnosis/machine callers.
- Internal errors need stable code/phase/operation/context and safe message; traceback only with `--debug`.

## Verify

- [ ] FR#1: Subprocess tests prove console and module entry points expose equivalent `check` and `rules` behavior.
- [ ] FR#13: Integration tests prove the global candidate limit and caught internal failures produce structured errors, preserve completed-file results, and never emit a clean status.
- [ ] FR#14: Snapshot/structural tests prove deterministic text and complete schema-v1 JSON for clean, finding, filename, and error results.
- [ ] FR#21: Subprocess tests prove exit precedence, partial findings on exit 3/4, stdout/stderr ownership, and debug behavior.
- [ ] FR#22: Text/JSON rule listing includes every stable ID, description, and default/opt-in/always enablement.
- [ ] AC#2: A clean fixture repository exits 0 with deterministic valid text and JSON summaries.
- [ ] AC#3: Fixture cases locally produce exits 1, 2, 3, and simulated 4 with the documented result shapes.
- [ ] AC#9: `house-lint rules --format json` returns schema version 1 and all HSL001-HSL004, HSL101-HSL103, and HSL900 entries.
