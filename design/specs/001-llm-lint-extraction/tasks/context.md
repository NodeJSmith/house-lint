# Context: Standalone Python House-Style Linter

## Problem & Motivation

Seven useful house-style checks live as Hassette-local scripts, so reusing them across Jessica's Python projects requires copying code and causes drift. The new `house-lint` package centralizes those detectors, source parsing, configuration, suppression semantics, discovery, reporting, and process behavior. It is intentionally opinionated and publicly available without attempting convention neutrality or LLM-authorship detection. V0.1 must be installable with `uvx`/`pipx`, usable from hooks, and validated read-only against Hassette and `claude-code-recall`.

## Visual Artifacts

None.

## Key Decisions

1. Use distribution/CLI `house-lint`, import package `house_lint`, pragma prefix `house-lint:`, and stable `HSL###` IDs.
2. Ship seven static built-in rules: `HSL001-HSL004` default, `HSL101-HSL103` opt-in, and always-on `HSL900` suppression diagnostics.
3. Use Cyclopts for CLI ergonomics and `pathspec` for explicitly loaded root `.gitignore` plus configured exclusions.
4. Parse each source once through a cached `SourceFile`; rules return private candidates with statement/file/no-owner provenance.
5. Apply statement-aware `ignore`, `ignore-next`, and general top-of-file `ignore-file` suppressions before converting candidates to public findings; conflicts fail closed.
6. Keep public result DTOs/schema separate from private analysis types. JSON schema version 1 is explicit and valid for all exits.
7. Keep strict explicit-path semantics; pre-commit hook metadata filters Python inputs. Do not add a tolerant hook mode.
8. Treat traversal/read/decode/tokenize/parse/budget failures as incomplete scans with exit 3 while preserving reachable-file findings.
9. Use atomic per-file parse gating in v0.1; malformed files do not receive partial rule execution.
10. Use fixed guardrails: regular files only, 10 MiB/file, 100,000 files/invocation, 10,000 candidate findings/file.

## Constraints & Anti-Patterns

- Do not modify Hassette or `claude-code-recall`; they are read-only acceptance targets.
- Do not add plugins, arbitrary regex rules, autofix, PMD duplicate detection, Ruff/Flake8/Pylint integration, SARIF, or editor/hosted-CI integrations.
- Do not silently swallow file, traversal, configuration, parse, or internal errors.
- Do not implement suppression attachment using raw line substring matching.
- Do not scan ordinary Python strings for AI-writing or spec-token findings.
- Do not shell out to Git or recursively compose nested `.gitignore` files.
- Do not add Pydantic or a project-root dependency; use dataclasses and explicit validation.
- Do not let rules read files, inspect raw TOML, print, or exit.
- Do not introduce a generic `RuleDefinition` mini-framework; use narrow metadata and explicit dispatch/validators.

## Design Doc References

## Functional Requirements — the 22 observable rule, CLI, suppression, discovery, reporting, and exit behaviors.
## Edge Cases — strict paths, atomic parse gating, suppression conflicts, budgets, and incomplete scans.
## Architecture — package boundaries, public/private models, source cache, registry, rules, suppressions, configuration, CLI, and reporters.
## Per-Rule Behavior Matrix — exact Hassette behavior preserved, generalized, or dropped.
## Test Strategy — required unit, subprocess integration, and read-only two-project acceptance coverage.
## Documentation Updates — README, rule/config/suppression references, and changelog obligations.

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
    return 1 if violations else 0
```

### AST detector returns deterministic findings

**Source:** `/home/jessica/source/hassette/tools/check_type_checking_position.py:44-62`

```python
for i, node in enumerate(tree.body):
    if not (isinstance(node, ast.If) and is_type_checking_guard(node.test)):
        continue
    later_import = next(
        (later for later in tree.body[i + 1 :] if isinstance(later, (ast.Import, ast.ImportFrom))),
        None,
    )
```

### Token-aware text scanning excludes ordinary strings

**Source:** `/home/jessica/source/hassette/tools/check_llm_cruft.py:78-95`

```python
for tok in tokenize.generate_tokens(io.StringIO(source).readline):
    if tok.type != tokenize.COMMENT:
        continue
    body = comment_body(tok.string)
```

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
