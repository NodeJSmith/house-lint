# house-lint

> Lint checks that catch the habits AI coding assistants leave behind.

AI coding assistants write code that runs and passes review at a glance, but they leave the same habits behind:
- filler phrases in comments ("it is important to note", "leverage")
- imports tucked inside functions
- constants defined halfway down the module
- files that keep growing
- planning-ticket IDs (`AC1`, `T05`) leaking into the code
- the same block pasted into a third module

Reviewers stop reading comments that all sound the same, and nobody notices the import buried in a function until it breaks a mock. `house-lint` flags these habits; the [rules table](#rules) shows what it checks today.

It complements Ruff rather than replacing it. A check belongs here when it targets a pattern AI-assisted code produces and Ruff and the type checker don't already catch it. The rules are opinionated, and you choose which ones to enable. They started as scripts in one project and now live here, so every project gets the same checks from one versioned tool.

Today every rule analyzes one Python file at a time. Next come checks that compare files across a project, starting with duplicate-code detection for Python and TypeScript ([#39](https://github.com/NodeJSmith/house-lint/issues/39)), then complexity and dead code ([#53](https://github.com/NodeJSmith/house-lint/issues/53)).

## Quick start

You need Python 3.11 or later.

```bash
uvx house-lint check
```

With no configuration, house-lint scans the whole project tree from the project root (the nearest directory at or above the current one with a house-lint config, or failing that, with a `pyproject.toml` or `.git`), skips anything git ignores, and runs the four default rules. The output lists the root, configuration, enabled rules, file counts, findings, and a summary.

To pin it as a development dependency:

```bash
uv add --dev house-lint
uv run house-lint check
```

## Rules

| ID | Default | Checks |
| --- | --- | --- |
| `HSL001` | On | AI-writing tells in comments and docstrings (never ordinary strings) |
| `HSL002` | On | Imports inside function or async-function bodies |
| `HSL003` | On | Top-level `TYPE_CHECKING` import blocks placed before regular imports |
| `HSL004` | On | Module constants defined after the first class or function, except derived bindings |
| `HSL101` | Off | Planning and spec tokens (built-in families like `AC1`, `T05`, `KI-001`, plus your own such as `JIRA-123`) in comments, docstrings, and filenames |
| `HSL102` | Off | Files longer than a configured line limit (default 800) |
| `HSL103` | Off | Bound exception names outside an allowed policy (default `exc` or `*_exc`) |
| `HSL900` | Always | Invalid, unused, conflicting, or malformed suppressions; cannot be disabled |

`house-lint rules --format json` prints the installed rule metadata. The [rule reference](docs/rules.md) describes exactly what each rule flags and what it leaves alone.

## Configuration

Configure house-lint in `pyproject.toml` under `[tool.house-lint]`, or in a standalone `house-lint.toml` or `.house-lint.toml`:

```toml
[tool.house-lint]
include = ["src", "tests"]        # optional; default is the whole tree
exclude = ["generated/"]          # gitignore-style patterns, no negation
extend-select = ["HSL102", "HSL103"]

[tool.house-lint.rules.HSL102]
max_lines = 600

[tool.house-lint.per-file-ignores]
"tests/**" = ["HSL002"]
```

- `select` sets the base selection and `ignore` removes rules from it. `extend-select` and `extend-ignore` add and remove on top of that base.
- On the command line, `--select` replaces the configured base. `--extend-select` and `--extend-ignore` combine with the configured values. A CLI `--ignore` is applied last and always wins.
- Each flag occurrence takes one comma-separated list. Selection is strict: unknown, duplicate, or empty rule IDs are usage errors.

[Configuration](docs/configuration.md) covers config file discovery, precedence, scan scope, excludes, rule options, and `HSL101` token families.

## Suppressions

Every suppression names the rule and gives a reason of at least three alphanumeric characters:

```python
# house-lint: ignore-file[HSL102] - generated compatibility module


def load_plugin():
    import plugin  # house-lint: ignore[HSL002] - avoids a circular import


# house-lint: ignore-next[HSL004] - kept beside the loader it configures
RETRIES = 3

try:
    callback()
except OSError as error:  # house-lint: ignore[HSL103] - compatibility callback signature
    raise
```

- `ignore` covers the statement it sits on. An `except` clause is its own statement, so its pragma goes on the `except` line.
- `ignore-next` on its own line covers the next statement in the same block, skipping comments and blank lines.
- `ignore-file` covers the whole file for the listed rules. It must come before the first statement other than a docstring or `__future__` import.

`HSL900` flags suppressions that are malformed, misplaced, unused, duplicated, or that name an unknown or disabled rule, so stale suppressions don't pile up. See [suppressions](docs/suppressions.md).

## Paths and roots

```bash
house-lint check                      # configured scope, or the whole tree
house-lint check src/service.py tests # only these files and directories
```

- Explicit paths are strict: a missing path, a path outside the root, or a non-Python file is an error.
- An explicit file that matches a built-in or configured exclude is skipped. An explicit file that is only gitignored is linted.
- `--root` fixes the project boundary. Without `--config`, house-lint looks in the root for `house-lint.toml`, then `.house-lint.toml`, then a `pyproject.toml` with `[tool.house-lint]`.
- `--config` names an exact configuration file, and its directory becomes the root unless `--root` is given.

Inside a git repository, house-lint takes its file list from `git ls-files`, so tracked files are scanned and untracked ignored files aren't. A git failure there stops the run with exit code 3. Outside a repository, or with `--no-git`, it walks the directory and reads no ignore files. See [File selection](docs/configuration.md#file-selection).

## Pre-commit

The published hook runs house-lint on staged Python files, serially rather than in parallel batches:

```yaml
repos:
  - repo: https://github.com/NodeJSmith/house-lint
    rev: v0.2.0
    hooks:
      - id: house-lint
```

Or run an installed copy as a local hook:

```yaml
repos:
  - repo: local
    hooks:
      - id: house-lint
        name: house-lint
        entry: house-lint check
        language: system
        types: [python]
        require_serial: true
```

## Caching

`check` caches each file's result under `<root>/.house-lint-cache/`. The key covers the file's content, the rules and rule options in effect, and the Python version; [configuration](docs/configuration.md#caching) lists every input. A hit skips parsing and rule execution.

The cache directory is namespaced by house-lint's version and a fingerprint of its own source, so upgrading starts from a clean cache. Old directories are pruned.

- `--no-cache` skips reading the cache but still refreshes it.
- `--cache-dir` moves it.

A cache failure never fails a scan. house-lint prints one warning for the first failure in a run, and `--debug` shows the rest.

## Output and exit codes

`--format json` prints one schema-versioned JSON object to stdout (`schema_version` 2) with the root, config, enabled rules, file counts, findings, errors, and summary. Match on rule IDs, locations, and error `code` values. Finding `message` text is for people and may change.

| Exit | Meaning |
| ---: | --- |
| 0 | Clean scan |
| 1 | Findings, including `HSL900` |
| 2 | Usage or configuration error; nothing was scanned |
| 3 | Incomplete scan: path, traversal, git, budget, read, decode, tokenize, or syntax error |
| 4 | Unexpected internal error |

When several apply, the highest code wins.

| Error code | Meaning |
| --- | --- |
| `config-error` | Invalid CLI arguments, configuration, or root |
| `path-error` | Invalid explicit path or source path |
| `traversal-error` | Discovery filesystem failure; the rest of the scan still ran |
| `git-error` | git failed inside a repository; nothing was scanned |
| `budget-error` | A discovery or candidate-count safety limit was exceeded |
| `source-too-large` | A source file exceeds the 10 MiB read limit |
| `read-error` | A source file could not be read |
| `decode-error` | A source file could not be decoded |
| `tokenize-error` | A source file could not be tokenized |
| `syntax-error` | A source file could not be parsed as Python |
| `internal-error` | An unexpected failure reached the CLI boundary |

## Development

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

CI runs the tests on Python 3.11 through 3.14, plus Ruff, Pyright, and a package build. This repo also runs house-lint on itself through `prek.toml`. [Release Please](https://github.com/googleapis/release-please) cuts releases from Conventional Commits and publishes to PyPI. See the [changelog](CHANGELOG.md) for compatibility notes.
