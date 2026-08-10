---
task_id: "T04"
title: "Implement the three opt-in rules"
status: "planned"
depends_on: ["T01", "T02"]
implements: ["FR#9", "FR#10", "FR#11", "AC#4"]
---

## Summary

Implement configurable spec-token families, file-length thresholds, and exception-name policy as opt-in built-ins. Preserve Hassette behavior where the matrix requires it while using typed validated options and private candidate provenance.

## Target Files

- create: `src/house_lint/rules/spec_tokens.py`
- create: `src/house_lint/rules/file_length.py`
- create: `src/house_lint/rules/exception_names.py`
- create: `tests/unit/rules/test_spec_tokens.py`
- create: `tests/unit/rules/test_file_length.py`
- create: `tests/unit/rules/test_exception_names.py`
- read: `src/house_lint/config.py`
- read: `src/house_lint/source.py`
- read: `design/specs/001-llm-lint-extraction/design.md`
- read: `/home/jessica/source/hassette/tools/check_spec_tokens.py`
- read: `/home/jessica/source/hassette/tools/check_file_size.py`
- read: `/home/jessica/source/hassette/tools/check_exception_names.py`
- read: `/home/jessica/source/hassette/tests/unit/tools/test_check_spec_tokens.py`
- read: `/home/jessica/source/hassette/tests/unit/tools/test_check_file_size.py`
- read: `/home/jessica/source/hassette/tests/unit/tools/test_check_exception_names.py`

## Prompt

Implement HSL101 token families using the exact constrained schema and limits in **Spec-Token Configuration**, compiling internal regexes only after validation. Preserve comment/docstring/filename scopes, data-string exclusion, filename segmentation, and the time guard. Implement HSL102 as a `splitlines()` file-length rule with strict `>` threshold and file ownership. Implement HSL103 with typed allowed-name/pattern options and AST exception-handler detection. Return private candidates; do not apply suppressions.

## Focus

- No raw regex configuration or generic matching engine.
- Filename findings have no source owner and null public locations.
- File-length findings have file ownership for suppression but null public locations; do not synthesize line 1.
- HSL103 allowed patterns are exact names or a single leading `*` suffix matcher such as `*_exc`; no other glob syntax is supported.
- HSL101 enforces its configured `max_findings_per_file` default of 200; this is distinct from the global 10,000-candidate safety budget across all rules, which T06 orchestration enforces.
- HSL102's module/public name is file length even though the source script was named file size.
- AC#4 covers all seven detector behavior suites: run this task's three suites together with T03's four suites.

## Verify

- [ ] FR#9: Tests prove configured prefixes/hash/digits/suffix/case/time/scopes, filename null locations, limits, and data-string exclusion for HSL101.
- [ ] FR#10: Tests prove exact/over-threshold `splitlines()` behavior and configurable `max_lines` for HSL102.
- [ ] FR#11: Tests prove unbound, `exc`, `*_exc`, disallowed, multiple, and nested handlers for HSL103.
- [ ] AC#4: `uv run pytest tests/unit/rules/test_llm_cruft.py tests/unit/rules/test_lazy_imports.py tests/unit/rules/test_type_checking_position.py tests/unit/rules/test_constants_position.py tests/unit/rules/test_spec_tokens.py tests/unit/rules/test_file_length.py tests/unit/rules/test_exception_names.py` exits 0 and test names/expectations encode every intentional preserve/generalize/drop delta; T07 records cross-repository comparison separately.
