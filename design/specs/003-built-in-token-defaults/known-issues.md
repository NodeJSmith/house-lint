# Known Issues

Durable issues discovered during orchestration that were intentionally not fixed in this run.

## KI-001: test_default_separator_forbids_a_separator_and_default_cap_is_200 conflates two concerns and breaks the sibling naming convention

Status: resolved
Resolution: Split into `test_none_separator_forbids_any_separator` and
`test_default_max_findings_per_file_is_200`, each asserting exactly one concern and following the
`test_<value>_separator_<behavior>` sibling naming shape. Verified by code-reviewer and
integration-reviewer with no findings.
Run: 108
Source: cross-file-review
Reason not fixed now: out-of-scope
Observed in: T03, commit 26a7f9a (pre-existing structure predates this feature; the cross-file fixer pass only corrected stale `hash_mode` terminology in the name)
Affected files:
- tests/unit/rules/test_spec_tokens.py

Issue:
The test at line 138 bundles two unrelated assertions (default separator forbids a separator; the
200-finding-per-file cap) under one name. Sibling separator tests in the same file follow a
`test_<value>_separator_<behavior>` naming shape (`test_dash_separator_requires_the_dash`,
`test_hash_optional_separator_matches_with_and_without_hash`,
`test_dash_optional_separator_matches_with_and_without_dash`); this test's name doesn't match that
shape and conflates two concerns.

Why deferred:
This is a pre-existing test-structure issue that predates this feature (the cross-file fixer pass
in this run only repaired the stale `hash_mode` reference in the name, not the underlying dual-
concern structure). Splitting the test into two is a scope expansion beyond the specific stale-
terminology fix this orchestration run was scoped to make.

Recommended follow-up:
Split into `test_none_separator_forbids_any_separator` and
`test_default_max_findings_per_file_is_200`, matching the sibling naming pattern.

Acceptance criteria:
- Two separate tests exist, each named per the `test_<value>_separator_<behavior>` /
  descriptive-cap-test convention, each asserting exactly one concern.
