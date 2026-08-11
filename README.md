# house-lint

> Jessica's opinionated Python house-style linter for comments, imports, module layout, and deliberately configured project conventions.

`house-lint` packages the checks Jessica uses across her Python projects. It is intentionally not a general-purpose style framework, an LLM-authorship detector, or a replacement for Ruff.

## Quick start

You need Python 3.11 or later.

```bash
uvx house-lint check
```

You see the selected root, configuration, enabled rules, file counts, findings, and a final summary. The default scan looks for `src`, `tests`, `scripts`, `tools`, and `examples` under the project root.

Install it in a project when you want a pinned development dependency:

```bash
uv add --dev house-lint
uv run house-lint check
```

## Rules

The four default rules are `HSL001`–`HSL004`. `HSL900` always reports invalid suppressions.

| ID | Default | Checks |
| --- | --- | --- |
| `HSL001` | On | AI-writing tells in comments and docstrings, never ordinary strings |
| `HSL002` | On | Imports inside function or async-function bodies |
| `HSL003` | On | Top-level `TYPE_CHECKING` guards followed by imports |
| `HSL004` | On | Module constants after the first class/function, except derived bindings |
| `HSL101` | Off | Configured planning/spec tokens in comments, docstrings, and filenames |
| `HSL102` | Off | Files whose `splitlines()` count exceeds a configured limit |
| `HSL103` | Off | Bound exception names outside the allowed policy |
| `HSL900` | Always | Invalid, unused, conflicting, or malformed suppressions |

List the installed rule metadata:

```bash
house-lint rules --format json
```

The command writes a schema-versioned JSON object containing all eight IDs and their enablement modes.

See [the rule reference](docs/rules.md) for exact rule behavior.

## Configuration

Put configuration in your project `pyproject.toml`:

```toml
[tool.house-lint]
include = ["src", "tests"]
exclude = ["generated/"]
select = ["HSL001", "HSL002", "HSL003", "HSL004", "HSL101", "HSL102", "HSL103"]

[tool.house-lint.rules.HSL102]
max_lines = 800

[tool.house-lint.rules.HSL103]
allowed = ["exc", "*_exc"]

[[tool.house-lint.rules.HSL101.tokens]]
prefixes = ["AC", "FR", "NFR", "WP"]
hash = "optional"
min_digits = 1
suffix = "optional-lower-alpha"
scopes = ["comments", "docstrings", "filenames"]
case_sensitive = true
```

`HSL101` requires at least one token family whenever you select it. `HSL102` defaults to `800` lines and `HSL103` defaults to `exc` or `*_exc` when selected.

CLI selection overrides configuration:

```bash
house-lint check --select HSL002,HSL103 --ignore HSL103
```

Each `--select` or `--ignore` occurrence accepts one comma-separated list. Selection is strict: unknown, duplicate, empty, and `HSL900` IDs are usage errors.

Read [configuration](docs/configuration.md) for discovery, precedence, validation, excludes, and token-family options.

## Paths, roots, and Git ignores

With no paths, `check` scans configured include roots. With paths, it scans only those explicit Python files or recursively expanded explicit directories:

```bash
house-lint check src/service.py tests
```

Explicit paths are strict. Missing, out-of-root, and non-Python file arguments are errors; ignored or excluded explicit Python files are counted as skipped. `--root` fixes the project boundary and only considers `<root>/pyproject.toml`. Without `--root`, discovery starts at the current directory. `--config` selects an exact configuration file; without `--root`, its parent becomes the root.

The linter loads only the selected root's `.gitignore`, plus built-in and configured excludes. It does not search nested `.gitignore` files or shell out to Git. Use `--no-gitignore` to disable only the root `.gitignore`.

## Suppressions

Suppress a finding only with a rule ID and a meaningful reason (at least three alphanumeric characters):

```python
def load_plugin():
    import plugin  # house-lint: ignore[HSL002] - avoids a circular import


# house-lint: ignore-next[HSL103] - compatibility callback signature
try:
    callback()
except OSError as error:
    raise

# house-lint: ignore-file[HSL102] - generated compatibility module
```

`ignore` attaches to its containing statement. A comment-only `ignore-next` attaches to the next statement in the same suite, even across ordinary comments and blank lines. A top-of-file `ignore-file` applies to the listed enabled rules throughout the file. `HSL900` cannot be suppressed.

Read [suppressions](docs/suppressions.md) before adding one; malformed, misplaced, disabled, unknown, unused, duplicate, and conflicting pragmas produce `HSL900`.

## Pre-commit

Run the installed CLI from a local pre-commit hook:

```yaml
repos:
  - repo: local
    hooks:
      - id: house-lint
        name: house-lint
        entry: house-lint check
        language: system
        types: [python]
        files: \.py$
```

The distributed `.pre-commit-hooks.yaml` exposes the same `house-lint` hook for a published repository. Both forms filter to existing `*.py` files before invocation. The CLI remains strict, so manual explicit paths still fail for missing or non-Python files.

## Output and exits

Use JSON for machine consumers:

```bash
house-lint check --format json
```

JSON stdout is always one parseable schema-version-1 object. It always includes `root`, `config`, `enabled_rules`, file counts, findings, errors, and summary counts. `root` and `config` are absolute strings when available and `null` otherwise; filename and file-level findings have all location fields set to `null`.

Finding `message` values are human-readable display text, not stable machine keys. Machine consumers should use rule IDs and locations for findings, and error `code` values for operational failures.

| Exit | Meaning |
| ---: | --- |
| 0 | Complete scan with no visible findings or errors |
| 1 | Complete scan with lint findings, including `HSL900` |
| 2 | CLI usage or configuration error; scanning did not start |
| 3 | Incomplete scan: path, traversal, budget, read, decode, tokenize, or syntax error |
| 4 | Unexpected internal error caught at the CLI boundary |

Exit precedence is `4 > 3 > 2 > 1 > 0`. In JSON mode, diagnostics stay in the JSON result on stdout; `--debug` writes additional details only to stderr.

`errors[*].code` is the stable machine-readable error taxonomy. `kind`, `phase`, and `operation` provide context and may gain new values without changing an existing error code.

| Code | Meaning |
| --- | --- |
| `config-error` | CLI argument or configuration loading failure |
| `path-error` | Invalid root, explicit path, or selected source path |
| `traversal-error` | Discovery or root `.gitignore` filesystem failure |
| `budget-error` | Discovery or candidate-count safety limit exceeded |
| `source-too-large` | A selected source file exceeds the 10 MiB read limit |
| `read-error` | A selected source file could not be read |
| `decode-error` | A selected source file could not be decoded |
| `tokenize-error` | A selected source file could not be tokenized |
| `syntax-error` | A selected source file could not be parsed as Python |
| `internal-error` | An unexpected failure crossed the CLI boundary |

## Non-goals

This package does not infer whether code was written by an LLM, provide autofixes or plugins, support arbitrary user rules, scan non-Python sources, or migrate existing repository-local annotations and hooks.

## Development

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

See [the changelog](CHANGELOG.md) for compatibility notes.
