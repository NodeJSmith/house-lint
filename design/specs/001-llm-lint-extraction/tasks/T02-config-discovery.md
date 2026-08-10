---
task_id: "T02"
title: "Implement configuration and file discovery"
status: "done"
depends_on: ["T01"]
implements: ["FR#2", "FR#3", "FR#4", "FR#20", "AC#6"]
---

## Summary

Implement the normative TOML schema, rule-selection precedence, project-root/config resolution, strict explicit paths, directory traversal, root `.gitignore`, configured exclusions, and incomplete traversal behavior.

## Target Files

- create: `src/house_lint/config.py`
- create: `src/house_lint/discovery.py`
- create: `tests/unit/test_config.py`
- create: `tests/unit/test_discovery.py`
- read: `src/house_lint/results.py`
- read: `/home/jessica/source/hassette/tools/lint_helpers.py`
- read: `/home/jessica/source/hassette/tests/unit/tools/test_lint_helpers.py`
- read: `design/specs/001-llm-lint-extraction/design.md`

## Prompt

Implement explicit configuration dataclasses/validators and discovery functions exactly as specified in **Configuration and Discovery** and the normative schema table. Root/config precedence must distinguish explicit `--root`, explicit `--config`, upward discovery, and cwd fallback. Implement strict files, recursive explicit directories, resolved containment, no directory-symlink traversal, safe directly passed file symlinks, deterministic deduplication, built-in excludes, root-only `.gitignore`, configured excludes, and `--no-gitignore` support. Use an error-reporting walker; traversal failures preserve reachable files and produce exit-3-class errors. Enforce the 100,000-file guardrail.

## Focus

- Config owns effective selection and typed rule options; registry/detectors never parse raw TOML.
- `--select` replaces configured selection; CLI `--ignore` subtracts last. Unknown/duplicate IDs and `HSL900` selection are invalid.
- Rule-specific tables are validated even when disabled. HSL101 requires tokens only when selected; HSL102/HSL103 have defaults.
- `include` contains root-relative paths, not globs; `exclude` contains Git-ignore-style patterns.
- Pre-commit filtering is external metadata; do not add tolerant CLI path semantics.

## Verify

- [ ] FR#2: Tests prove no-path full scans and strict passed-file/directory scans select only the documented files.
- [ ] FR#3: Tests prove every explicit/implicit root and config precedence branch, including out-of-root explicit config rejection.
- [ ] FR#4: Tests prove built-in/config/CLI selection and option precedence, omission/empty behavior, and strict unknown-key validation.
- [ ] FR#20: Tests prove root `.gitignore`, configured excludes, built-in excludes, and `--no-gitignore` behavior without nested ignore discovery.
- [ ] AC#6: The discovery/config suite locally proves precedence, strict paths, traversal errors, sorting/deduplication, excludes, and explicit empty scans.
