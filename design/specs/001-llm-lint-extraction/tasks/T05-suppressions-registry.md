---
task_id: "T05"
title: "Add registry and unified suppressions"
status: "planned"
depends_on: ["T01", "T02", "T03", "T04"]
implements: ["FR#15", "FR#16", "FR#17", "FR#18", "FR#19", "AC#5"]
---

## Summary

Wire the seven rules through narrow static metadata/dispatch tables and implement the shared token- and AST-aware suppression lifecycle. Convert visible candidates to public findings only after fail-closed ownership and HSL900 diagnostics are resolved.

## Target Files

- create: `src/house_lint/registry.py`
- create: `src/house_lint/suppressions.py`
- create: `tests/unit/test_registry.py`
- create: `tests/unit/test_suppressions.py`
- read: `src/house_lint/analysis.py`
- read: `src/house_lint/config.py`
- read: `src/house_lint/source.py`
- read: `src/house_lint/rules/*.py`
- read: `/home/jessica/source/hassette/tools/check_lazy_imports.py`
- read: `/home/jessica/source/hassette/tools/check_constants_position.py`
- read: `/home/jessica/source/hassette/tools/check_file_size.py`

## Prompt

Implement a narrow immutable rule metadata table and explicit dispatch map; do not create a plugin-oriented descriptor framework. Parse only `tokenize.COMMENT` pragmas with exact `house-lint:` grammar. Implement trailing same-statement `ignore`, same-suite next-statement `ignore-next`, and prologue-only general `ignore-file`. Require canonical IDs and reasons with at least three alphanumeric characters. Track consumption per listed ID. Unknown, disabled, duplicate, unconsumed, malformed, misplaced, and conflicting suppression ownership create unsuppressible HSL900 candidates. On conflicts, leave original findings visible. Convert remaining candidates to public DTOs after suppression.

## Focus

- Statement ownership comes from private candidates; never infer it later from public coordinates.
- File suppression can target all ordinary rule IDs, including filename/file-length candidates.
- HSL900 is always enabled and cannot name itself in a pragma.
- `ignore-file` allows shebang, encoding cookie, blank/comments, docstring, and future imports before it; no other statement.
- Multiple IDs each require consumption; disabled IDs are unused, not dormant.

## Verify

- [ ] FR#15: Tests prove trailing pragmas attach to containing simple/multiline statements and consume all owned same-rule findings.
- [ ] FR#16: Tests prove comment-only `ignore-next` attaches across blanks/comments only within the same lexical suite.
- [ ] FR#17: Tests prove valid prologue placement and file-wide suppression for statement, filename, and file-length findings.
- [ ] FR#18: Tests prove explicit IDs, comma grammar, forbidden HSL900/all/globs, and meaningful mandatory reasons.
- [ ] FR#19: Tests prove every malformed/misplaced/unknown/disabled/duplicate/conflicting/unused case yields visible HSL900 and conflicts leave originals visible.
- [ ] AC#5: The complete suppression characterization suite passes for all forms, ownership shapes, multi-ID/multi-finding cases, and diagnostics.
