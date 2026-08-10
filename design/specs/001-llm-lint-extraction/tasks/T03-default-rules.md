---
task_id: "T03"
title: "Implement the four default rules"
status: "planned"
depends_on: ["T01"]
implements: ["FR#5", "FR#6", "FR#7", "FR#8"]
---

## Summary

Port the four default Hassette detectors into pure rule modules consuming cached `SourceFile` data and returning provenance-bearing candidates. Preserve characterization behavior while leaving all suppression decisions to the later shared engine.

## Target Files

- create: `src/house_lint/rules/__init__.py`
- create: `src/house_lint/rules/llm_cruft.py`
- create: `src/house_lint/rules/lazy_imports.py`
- create: `src/house_lint/rules/type_checking_position.py`
- create: `src/house_lint/rules/constants_position.py`
- create: `tests/unit/rules/test_llm_cruft.py`
- create: `tests/unit/rules/test_lazy_imports.py`
- create: `tests/unit/rules/test_type_checking_position.py`
- create: `tests/unit/rules/test_constants_position.py`
- read: `src/house_lint/source.py`
- read: `src/house_lint/analysis.py`
- read: `design/specs/001-llm-lint-extraction/design.md`
- read: `/home/jessica/source/hassette/tools/check_llm_cruft.py`
- read: `/home/jessica/source/hassette/tools/check_lazy_imports.py`
- read: `/home/jessica/source/hassette/tools/check_type_checking_position.py`
- read: `/home/jessica/source/hassette/tools/check_constants_position.py`
- read: `/home/jessica/source/hassette/tests/unit/tools/test_check_llm_cruft.py`
- read: `/home/jessica/source/hassette/tests/unit/tools/test_check_lazy_imports.py`
- read: `/home/jessica/source/hassette/tests/unit/tools/test_check_type_checking_position.py`
- read: `/home/jessica/source/hassette/tests/unit/tools/test_check_constants_position.py`

## Prompt

Implement `HSL001-HSL004` according to the **Per-Rule Behavior Matrix**. Preserve fixed LLM-cruft patterns and semantic comment/docstring scope; lazy-import function-depth detection; top-level `TYPE_CHECKING` ordering; and the uppercase/dunder/derived-binding constants heuristic including annotation references. Detectors must not read files, print, parse configuration, apply suppressions, or expose Hassette annotations. Return private `CandidateFinding` values with explicit statement/file/no-owner provenance.

## Focus

- Ordinary strings are outside HSL001.
- Preserve malformed-source behavior through SourceFile atomic gating, not detector-specific exception swallowing.
- HSL004 remains stylistic under postponed annotations; preserve the Hassette heuristic without claiming runtime semantic proof.
- Port characterization cases and expected distinctions, not imports or real-repo green parametrization tied to Hassette.

## Verify

- [ ] FR#5: Characterization tests prove divider/filler matches, case behavior, docstring/comment scope, and ordinary-string exclusion for HSL001.
- [ ] FR#6: Characterization tests prove function/async/method/nested import detection and safe module-level imports for HSL002.
- [ ] FR#7: Characterization tests prove both guard spellings, correct final placement, later-import violations, and top-level-only scope for HSL003.
- [ ] FR#8: Characterization tests prove constant naming, ordering, derived values/annotations, assignment shapes, and dunder/lowercase exclusions for HSL004.
