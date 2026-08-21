# Configuration

Configure `house-lint` in `[tool.house-lint]` in `pyproject.toml`.

```toml
[tool.house-lint]
include = ["src", "tests", "scripts", "tools", "examples"]
exclude = []
select = ["HSL001", "HSL002", "HSL003", "HSL004"]
ignore = []
extend-select = []
extend-ignore = []
```

`include` contains literal root-relative files or directories, not globs. An empty array intentionally selects no roots for a full scan. `exclude` uses root-relative Git-ignore-style patterns. Unknown keys, absolute paths, parent traversal, invalid patterns, duplicate IDs, and `HSL900` in `select`, `ignore`, `extend-select`, `extend-ignore`, or `per-file-ignores` are configuration errors. Unlike the rest of this schema, `extend-select`/`extend-ignore`/`per-file-ignores` are hyphenated by design, matching Ruff's spelling for the same concepts.

## Per-file rule overrides

`[tool.house-lint.per-file-ignores]` maps root-relative Git-ignore-style glob patterns to rule IDs to drop for matching files, without changing the global selection for everything else:

```toml
[tool.house-lint.per-file-ignores]
"tests/**" = ["HSL002"]
"legacy/*.py" = ["HSL001", "HSL003"]
```

Applied after the base selection and `extend-select`/`extend-ignore` resolve, per file: a rule dropped by `per-file-ignores` for a matching file is not detected for that file at all, so a `# house-lint: ignore[...]` pragma naming it there is flagged the same way as suppressing an already-disabled rule. `HSL900` can never appear in a `per-file-ignores` value.

## Discovery and precedence

1. `--root` fixes the project boundary.
2. `--config` selects an exact configuration. Without `--root`, its parent is the root; with `--root`, it must be inside the root.
3. With `--root` and no `--config`, only `<root>/pyproject.toml` is considered.
4. Without either option, the command searches upward from the current directory for the nearest `pyproject.toml` containing `[tool.house-lint]`. If none exists, it uses the nearest ancestor containing `.git` or any `pyproject.toml`; otherwise it uses the current directory.
5. The base selection is configured `select` minus configured `ignore`, or a CLI `--select` wholesale override when given.
6. `extend-select`/`extend-ignore` (config and CLI, unioned together) layer additively on top of that base, regardless of whether the base came from config or `--select`. `extend-ignore` removes rules from the whole base, not just from `extend-select` — `select = ["HSL001"]` with `extend-ignore = ["HSL001"]` drops HSL001 entirely, it isn't limited to canceling out `extend-select` additions.
7. CLI `--ignore` is applied last and always wins over everything above. `HSL900` is always added.

The root `.gitignore` and every nested `.gitignore` between the root and each discovered file are loaded and combined with git's own precedence — a closer `.gitignore` can override a farther one, including via negation (`!pattern`). Built-in excludes are `.git/`, `.venv/`, `.nox/`, `__pycache__/`, `site-packages/`, and `node_modules/`; configured excludes are added afterwards. `--no-gitignore` disables `.gitignore` handling at every level.

An ignored directory is skipped without being enumerated, which is what keeps a large `.venv/` or `node_modules/` cheap to exclude. The reported `files skipped` count follows from that: one pruned directory counts as one skip, however many files it contains.

Exclusion attaches to the directory, so a negation cannot re-include anything beneath one that is already excluded — `exclude = ["src/generated/", "!src/generated/foo.py"]` leaves `foo.py` excluded, matching git. This holds for built-in excludes, configured `exclude`, and `.gitignore` alike, and it holds however the file is reached: naming `src/generated/foo.py` on the command line skips it just as a full scan does.

`per-file-ignores` patterns are matched against each file's resolved location under the root, not the spelling used to reach it. Naming `src/../tests/a.py`, or a path reached through a symlinked directory, matches the same patterns as `tests/a.py` would. Findings still report the path as typed.

house-lint reimplements git's ignore rules on top of [`pathspec`](https://pypi.org/project/pathspec/) rather than shelling out to git. Two test suites check that reimplementation against real `git check-ignore`: `tests/integration/test_gitignore_parity.py` runs a curated table of pattern shapes, and `tests/integration/test_gitignore_fuzz.py` generates random combinations. The second runs on every CI run and skips locally unless `CI` is set, since it makes thousands of real `git check-ignore` calls; run it by hand with `CI=1 uv run pytest -s tests/integration/test_gitignore_fuzz.py` (`-s` prints the rates below).

Two divergences are known, both in the same family: `pathspec` decides directory-only patterns by inspecting the pattern text rather than by being told whether the candidate is a directory, so it cannot distinguish "this pattern matched this entry" from "it matched an ancestor". Closing either would mean owning the pattern-to-regex compiler rather than delegating whole-path matching to `pathspec`.

- **Over-linting.** A negated directory-only pattern (`!sub/`) re-includes everything beneath it, whereas git re-includes only the `sub` entry itself and re-evaluates each descendant against the remaining patterns. It changes the outcome only when such a negation sits under a broader ignore that also covers the descendants.
- **Under-linting.** A directory-only negation may fail to re-include a directory git descends into. `GitIgnoreSpec.from_lines(("**", "!**/")).match_file("src")` returns `True`, while git reports `.gitignore:2:!**/` re-including `src` and walks it. house-lint asks `pathspec` exactly that question when deciding whether to prune, so it prunes a subtree git walks and every file underneath vanishes from the scan. Passing `"src/"` does not change `pathspec`'s answer.

An earlier version of this section claimed the divergence "always errs toward linting a file git would ignore, never toward silently skipping one, so it cannot hide a finding." **That is not true**, and it was the whole justification for accepting the current design. The second case above hides findings exactly the way that sentence promised it could not — it was found once the fuzz suite's corner pool learned to compose repeated `**` segments, which it had never done before. The direction guarantee is now a measured property with a recorded ceiling rather than an invariant: `test_gitignore_fuzz.py` still fails on any under-linting divergence *outside* the named class, and separately caps how much the named class may account for, so it cannot quietly widen.

Over-linting is visible and silenced with one `exclude` entry; under-linting is indistinguishable from a clean run. That asymmetry is why the under-linting case is tracked as a defect to remove rather than a trade to keep. The rates below come from three declared pattern distributions:

| `.gitignore` content | divergence rate | skips a file git lints |
|---|---|---|
| plain names and globs, no negation | 0.00% (0/1500) | never |
| the same, 5% of patterns negated | 0.33% (5/1500) | never |
| corner-hunting pool, 30% negated | 2.47% (37/1500) | 1 (known directory-negation defect) |

A rate is meaningless without the distribution that produced it, which is why all three are declared in the test rather than summarised as one number. Regenerate them there and update this table in the same change.

`design/research/2026-08-20-gitignore-style-exclusion-inclusion/` surveys how git, ripgrep's `ignore` crate, fd, and the Node ecosystem handle this. The short version: matching the full path against one flattened, root-anchored pattern set is structurally unable to express "this directory was never entered," which is the rule git's own documentation calls out ("It is not possible to re-include a file if a parent directory of that file is excluded"). The same defect has been independently rediscovered in `pathspec` ([#81](https://github.com/cpburnz/python-pathspec/issues/81)), `node-ignore`, and other tools. The architectural fix is a per-directory matcher stack evaluated during traversal, with the walker supplying `is_dir` — not another patch.

## Caching

There is no TOML key for caching — it's controlled entirely by CLI flags, since it's a run-to-run performance concern rather than a project convention.

Each file's result is cached under `<root>/.house-lint-cache/<version>-<source fingerprint>/`, flat and keyed by two hashes: the file's raw content, and the file's *effective* rule set for that run (`select`/`ignore`/`extend-select`/`extend-ignore`/`per-file-ignores` and CLI overrides already resolved, plus all three `HSL101`/`HSL102`/`HSL103` option tables, whether or not each of those rules is currently enabled — simpler than tracking which options are actually load-bearing, at the cost of some extra cache invalidation when an unused rule's options change). The running interpreter's major/minor version is always folded in as well, since `ast.parse` accepts different grammar across the Python versions house-lint supports (`type Alias = int` is a `SyntaxError` before 3.12) — without it, a cache shared between venvs could replay a stale `SyntaxError`, or a stale success, that the interpreter actually running the scan would not produce. Switching interpreters is therefore a full cache miss by design. The file's own name is folded in too whenever an enabled `HSL101` token family scopes to `"filenames"`, since that's the one detector whose output depends on the filename rather than purely the content. house-lint is a single-file analyzer with no cross-file dependencies, so this flat scheme is sufficient — there is no dependency graph to invalidate. A cache hit skips tokenization, parsing, and rule execution for that file entirely.

The directory name carries both house-lint's version and a hash of its own Python sources. The version alone would not be enough: it only moves when a release is cut, so editing a detector in a working checkout and re-running would replay the previous detector's results for every unchanged file. The source fingerprint is content-based, so a released install keeps exactly one cache directory across machines and fresh clones.

Superseded directories house-lint created are pruned so they do not accumulate — but only by a run that actually writes a cache entry. A scan where every file is a cache hit deletes nothing, which keeps the sweep from removing a namespace that a concurrent house-lint of a different version is still writing to.

house-lint writes a self-ignoring `.gitignore` into its own default `.house-lint-cache/` base so the cache stays invisible to `git status`. It never writes one into a `--cache-dir` you supply, since that directory may hold unrelated data — or be a project root, where a wildcard ignore would hide the whole project.

`--no-cache` disables reading from the cache but still writes to it, keeping it warm for the next run — the same semantics as Ruff's `--no-cache`. `--cache-dir <path>` overrides the base directory (the namespace segment is still appended underneath it).

## Rule options

```toml
[tool.house-lint.rules.HSL102]
max_lines = 800

[tool.house-lint.rules.HSL103]
allowed = ["exc", "*_exc"]
```

`HSL102.max_lines` must be an integer from 1 through 10,000,000. `HSL103.allowed` must be a non-empty unique array of identifiers or patterns containing exactly one leading `*` followed by an identifier suffix.

## HSL101 token families

`HSL101` ships three built-in token families — `spec` (`AC`, `FR`, `NFR`, `WP`), `task` (`T`), and
`known-issues` (`KI`) — so selecting it with no `[tool.house-lint.rules.HSL101]` table at all
still detects those tokens in comments, docstrings, and filenames. Add a `tokens` array only to
extend the vocabulary; user-defined families stack on top of the built-ins rather than replacing
them:

```toml
[tool.house-lint.rules.HSL101]
max_findings_per_file = 200

[[tool.house-lint.rules.HSL101.tokens]]
prefixes = ["JIRA"]
scopes = ["comments", "docstrings", "filenames"]
separator = "dash"
min_digits = 1
max_digits = 12
suffix = "optional-lower-alpha"
case_sensitive = true
not_followed_by_time = false
```

Each family requires unique `prefixes` (1–32 uppercase values, up to 12 characters each) and unique `scopes` drawn from `comments`, `docstrings`, and `filenames`. You may configure at most 32 families in total once the three active built-in families are counted — 29 user-defined families at most with all built-ins active. `separator` is `none`, `hash`, `hash-optional`, `dash`, or `dash-optional`; `min_digits` is 1–12; `max_digits`, when present, is from `min_digits` through 12; `suffix` is `none` or `optional-lower-alpha`; and both boolean options must be TOML booleans. `max_findings_per_file` is a positive integer no greater than 10,000.

Rule tables do not have `enabled` keys. Selection is owned exclusively by top-level selection and CLI overrides; disabled rule tables are still validated.
