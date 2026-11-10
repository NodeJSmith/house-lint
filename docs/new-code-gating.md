# New-code gating

```bash
house-lint check --base origin/main
```

`--base <ref>` reports only the findings the `HEAD` commit introduced since its merge-base with `<ref>`, so a project with a backlog can still fail a PR that adds to it.

## What gets scanned

house-lint extracts both commits from git into temporary directories and scans each one, so uncommitted and untracked files never count.

Both scans use the config committed at `HEAD`. A PR that turns on a rule is judged only on the new instances it adds. An uncommitted config edit is ignored, even one that leaves the file unparsable. An explicit `--config` is read from the same path in `HEAD`, and is a usage error when it isn't committed there. A committed config that is a symlink to a file outside the commit is a usage error too, since following it would read an uncommitted file.

## What counts as new

A finding at `HEAD` counts as old when it pairs with a finding at the merge-base: the same rule, message, and code, ignoring whitespace.

- Moving code to another file or reformatting it keeps a finding old. Editing inside a flagged statement reports it again.
- Matching counts copies, so a second identical finding beside an old one is new. Among identical findings in one file the count is exact, but the one reported may be an older copy rather than the line the branch added.
- `HSL102` reports a file only if it has more lines than it had at the merge-base, following renames.
- `HSL104` reports only the copies a PR added.

See the [rule reference](rules.md) for both rules.

## Running it in CI

The gate needs the merge-base in the clone, so check out full history. In GitHub Actions, `github.base_ref` is set on `pull_request` events:

```yaml
# .github/workflows/lint.yml
on: pull_request

jobs:
  house-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v8
      - run: uvx --from 'house-lint[duplicates]' house-lint check --base "origin/${{ github.base_ref }}" --extend-select HSL102,HSL104
```

`--base` takes no paths, so in a pre-commit hook set `pass_filenames: false`.

## Limits and failures

- `--base` can't be combined with `--no-git` or with paths. Either is a usage error (exit code 2).
- A ref that names no commit, a missing merge-base, or an error from either scan fails the run with exit code 3. An error says which scan it came from (`base run:` or `head run:`), so a parse error that exists only at the merge-base also fails the gate.
- Both scans share the project's cache, so file rules are fast on a repeated run. `HSL104` isn't cached, so jscpd runs on both commits every time.
- `--fail-on-empty` judges the head commit's scan.

## Output

A `--base` run adds a `base` object to JSON output and a `base:` line to text output. The README's [output section](../README.md#output-and-exit-codes) lists their fields.
