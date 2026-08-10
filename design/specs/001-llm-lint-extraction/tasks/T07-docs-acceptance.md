---
task_id: "T07"
title: "Document and validate the package"
status: "planned"
depends_on: ["T03", "T04", "T05", "T06"]
implements: ["AC#1", "AC#7", "AC#8"]
---

## Summary

Complete user documentation, pre-commit metadata, full-suite verification, and read-only acceptance runs against Hassette and `claude-code-recall`. Record every behavioral delta and false-positive decision without changing either consumer repository.

## Target Files

- create: `README.md`
- create: `CHANGELOG.md`
- create: `.pre-commit-hooks.yaml`
- create: `tests/integration/test_pre_commit_hook.py`
- create: `docs/rules.md`
- create: `docs/configuration.md`
- create: `docs/suppressions.md`
- create: `design/specs/001-llm-lint-extraction/acceptance.md`
- modify: `pyproject.toml`
- read: `design/specs/001-llm-lint-extraction/design.md`
- read: `/home/jessica/source/hassette/pyproject.toml`
- read: `/home/jessica/source/hassette/prek.toml`
- read: `/home/jessica/source/hassette/tools/check_llm_cruft.py`
- read: `/home/jessica/source/hassette/tools/check_lazy_imports.py`
- read: `/home/jessica/source/hassette/tools/check_type_checking_position.py`
- read: `/home/jessica/source/hassette/tools/check_constants_position.py`
- read: `/home/jessica/source/hassette/tools/check_spec_tokens.py`
- read: `/home/jessica/source/hassette/tools/check_file_size.py`
- read: `/home/jessica/source/hassette/tools/check_exception_names.py`
- read: `/home/jessica/source/claude-code-recall/pyproject.toml`

## Prompt

Write documentation matching the final CLI/config/rule/suppression contracts and explicitly positioning the package as Jessica's opinionated house style. Add pre-commit hook metadata that passes Python filenames to the strict CLI. Run the complete unit/integration suite. For each read-only consumer acceptance run, copy the consumer repository into `/tmp/<acceptance-root>/project` while excluding `.git`, virtual environments, caches, and build output; place the temporary config at `<acceptance-root>/pyproject.toml`; invoke `house-lint` with the temporary directory as `--root` and `project` as the explicit scan path. Never edit either consumer. Record copy exclusions, commands, versions, configuration, counts, findings, original-script comparison, matrix-documented deltas, and false-positive triage in `acceptance.md`. Update packaging metadata and changelog consistently.

## Focus

- Do not turn acceptance into a migration of Hassette annotations/hooks.
- Package name availability was verified only at design time; check again before publication metadata claims availability.
- README/help/examples must agree exactly with strict paths, root-only gitignore, all exits, JSON nullability, seven rule defaults/options, and suppression grammar.
- `.pre-commit-hooks.yaml` should filter Python files so deleted/non-Python paths are not passed; do not weaken CLI strictness.
- Add an integration test that loads `.pre-commit-hooks.yaml`, asserts the `house-lint` hook entry invokes `house-lint check`, and proves its Python-file filtering contract against representative filenames.

## Verify

- [ ] AC#1: `uv run pytest tests/unit tests/integration` exits 0, covering source/results, config/discovery, all seven rule modules, registry/suppressions, CLI/reporters, and pre-commit metadata.
- [ ] AC#7: Using the documented temporary-root arrangement, run `uv run house-lint check project --root <temporary-root> --config <temporary-root>/pyproject.toml --format json` against Hassette; it emits parseable JSON and the `Hassette` section records arrangement, command, config, exit, counts, original-script comparison, and every preserve/generalize/drop delta.
- [ ] AC#8: Using the same temporary-root arrangement against `claude-code-recall`, run `uv run house-lint check project --root <temporary-root> --config <temporary-root>/pyproject.toml --format json`; it emits parseable JSON without exit 4 and the acceptance section records arrangement, command, config, exit, counts, and a legitimate-hit/false-positive disposition for every finding.
