# Configuration

Configure `house-lint` in `[tool.house-lint]` in `pyproject.toml`, or in a standalone
`house-lint.toml`/`.house-lint.toml` file — see [Standalone config files](#standalone-config-files).

```toml
[tool.house-lint]
exclude = []
select = ["HSL001", "HSL002", "HSL003", "HSL004"]
ignore = []
extend-select = []
extend-ignore = []
```

`exclude` uses root-relative Git-ignore-style patterns without negation. Unknown keys, absolute paths, parent traversal, invalid patterns, a `!` pattern in `exclude`, duplicate IDs, and `HSL900` in `select`, `ignore`, `extend-select`, `extend-ignore`, or `per-file-ignores` are configuration errors. Unlike the rest of this schema, `extend-select`/`extend-ignore`/`per-file-ignores` are hyphenated by design, matching Ruff's spelling for the same concepts.

## Default scan scope

When `include` is not set, house-lint scans from the project root — the entire tree, filtered by
git's ignore rules and the built-in excludes (see [File selection](#file-selection)) — rather than
a fixed list of directory names. This matches the dominant pattern among Python
linters (Ruff, Black): scan everything by default, then exclude what shouldn't be linted.

Set `include` to narrow the scan to specific root-relative files or directories — useful for a
monorepo where house-lint should only look at some packages, or any layout where you want to
restrict the scan below what git and the built-in excludes already remove:

```toml
[tool.house-lint]
include = ["src", "tests"]
```

`include` contains literal root-relative files or directories, not globs. An empty array
(`include = []`) intentionally selects no roots for a full scan, producing a clean empty result
without the [zero-file diagnostic](#zero-file-diagnostic)'s guidance clause below.

## Standalone config files

house-lint also recognizes two standalone config filenames: `house-lint.toml` and
`.house-lint.toml`. Standalone files use `[house-lint]` as the top-level table, not
`[tool.house-lint]` — they aren't embedded in a multi-tool manifest, so there's no `[tool.*]`
namespace to nest under:

```toml
[house-lint]
select = ["HSL001", "HSL002", "HSL003", "HSL004"]
include = ["src", "tests"]
```

Every other key (`exclude`, `ignore`, `extend-select`, `extend-ignore`, `per-file-ignores`,
`rules`) works identically to the `[tool.house-lint]` form — only the table path differs.
`--config path/to/house-lint.toml` also works: the standalone format is detected by filename
(`house-lint.toml` or `.house-lint.toml`), not by inspecting file contents. See
[Discovery and precedence](#discovery-and-precedence) for how a standalone file is found relative
to `pyproject.toml`.

## Per-file rule overrides

`[tool.house-lint.per-file-ignores]` maps root-relative Git-ignore-style glob patterns to rule IDs to drop for matching files, without changing the global selection for everything else:

```toml
[tool.house-lint.per-file-ignores]
"tests/**" = ["HSL002"]
"legacy/*.py" = ["HSL001", "HSL003"]
```

Applied after the base selection and `extend-select`/`extend-ignore` resolve, per file: a rule dropped by `per-file-ignores` for a matching file is not detected for that file at all, so a `# house-lint: ignore[...]` pragma naming it there is flagged the same way as suppressing an already-disabled rule. `HSL900` can never appear in a `per-file-ignores` value.

`per-file-ignores` patterns are matched against each file's resolved location under the root, not the spelling used to reach it. Naming `src/../tests/a.py`, or a path reached through a symlinked directory, matches the same patterns as `tests/a.py` would. Findings still report the path as typed.

## Discovery and precedence

1. `--root` fixes the project boundary.
2. `--config` selects an exact configuration. Without `--root`, its parent is the root; with `--root`, it must be inside the root.
3. With `--root` and no `--config`, the root directory is checked for `house-lint.toml` → `.house-lint.toml` → `pyproject.toml` (with `[tool.house-lint]`), in that order — the first recognized file is used.
4. Without either option, the command searches upward from the current directory. At each directory level, it checks `house-lint.toml` → `.house-lint.toml` → `pyproject.toml` (with `[tool.house-lint]`), in that order, and stops at the first directory where any of the three is recognized. The walk also stops at the first ancestor containing `.git`, after checking that directory itself for a recognized config first. This means a config that only exists further up an outer, unrelated ancestor tree is never picked up for a nested repository that has none of its own. If nothing is recognized by the time the walk stops (at a `.git` boundary or the filesystem root), it uses the nearest ancestor containing `.git` or any `pyproject.toml`; otherwise it uses the current directory.

When more than one recognized config source exists at the winning directory level (e.g. both `house-lint.toml` and `pyproject.toml` with `[tool.house-lint]`), the shadowed file(s) are named in default output — appended to the `config:` line in text output, or under a `shadowed_config` key (also outside the `check` output's `schema_version: 2` contract) in JSON output. This is not gated behind `--debug`.
5. The base selection is configured `select` minus configured `ignore`, or a CLI `--select` wholesale override when given.
6. `extend-select`/`extend-ignore` (config and CLI, unioned together) layer additively on top of that base, regardless of whether the base came from config or `--select`. `extend-ignore` removes rules from the whole base, not just from `extend-select` — `select = ["HSL001"]` with `extend-ignore = ["HSL001"]` drops HSL001 entirely, it isn't limited to canceling out `extend-select` additions.
7. CLI `--ignore` is applied last and always wins over everything above. `HSL900` is always added.

## File selection

Inside a git repository, house-lint asks git which files exist. For each directory it's asked to scan, it runs `git ls-files --cached --others --exclude-standard` and takes the answer as is:

- A tracked file is scanned even if it matches an ignore pattern, because git only ignores untracked files.
- An untracked file is skipped if `.gitignore`, `.git/info/exclude`, or your global ignore file hides it. Your global ignore file can make a local run differ from CI, but only for files `git status` doesn't show you on that machine either.
- Ignore files above the scan root count, so a scan rooted in a subdirectory follows the repository's rules.
- Submodules and nested clones aren't scanned unless you name one directly. Each is another project, with its own lint config.

"Inside a repository" means a `.git` entry exists at the directory or somewhere above it. Git itself can't answer that question: it exits 128 for "not a repository" and for a `safe.directory` refusal alike, and treating a refusal as "not a repository" would quietly lint gitignored files. So inside a repository, any git failure stops the run with exit code 3, a `git-error`, and git's message: git missing, a refusal, a `.git` entry git rejects, or a warning on stderr. Passing `--no-git` scans without git.

Outside a repository, or with `--no-git`, house-lint walks the directory and reads no ignore files of any kind. A `.gitignore` in a tree that isn't a repository has no effect, and `--no-git` also turns off `.git/info/exclude` and global excludes.

Built-in and configured excludes apply on top in every case. Built-in excludes are `.bzr/`, `.direnv/`, `.eggs/`, `.git/`, `.git-rewrite/`, `.hg/`, `.house-lint-cache/`, `.ipynb_checkpoints/`, `.mypy_cache/`, `.nox/`, `.pants.d/`, `.pyenv/`, `.pytest_cache/`, `.pytype/`, `.ruff_cache/`, `.svn/`, `.tox/`, `.venv/`, `.vscode/`, `__pycache__/`, `__pypackages__/`, `_build/`, `buck-out/`, `dist/`, `node_modules/`, `site-packages/`, and `venv/`. A tracked file under one of these stays out, and so does one under a configured `exclude`.

Paths you name, on the command line or in `include`, are handled differently:

- A named file doesn't start a git process, so a pre-commit run needs no git call. A named file that is untracked and gitignored is linted, even though a full scan skips it.
- A named directory is listed through git. If git lists nothing and reports the directory itself as ignored, house-lint walks it instead, so `check build/` agrees with `check build/gen.py`. An ignored directory holding a tracked file lists only its tracked files.
- A named path that matches a built-in or configured exclude is skipped. `exclude` is how a project keeps a hook from linting a generated file it has committed.

`exclude` patterns follow gitignore syntax, minus negation: a `!` pattern is a configuration error. Exclusion attaches to the directory too, so `exclude = ["src/generated/"]` covers everything beneath it, however a file is reached. `tests/integration/test_exclude_parity.py` checks a table of pattern shapes against `git check-ignore`. One known divergence remains: in a pattern ending with a doubled backslash and a space (`pkg\\\\ `), git drops the space and `exclude` keeps it.

An excluded path costs a string comparison and never touches the filesystem, so a symlink inside an excluded directory can't produce an error. A walk prunes an excluded directory without entering it. Git still lists an excluded tree it doesn't ignore itself, so filtering a large one takes time. A gitignored `.venv/` costs nothing, because git never lists it.

## Zero-file diagnostic

When a scan discovers zero Python files and no errors occurred, `check` exits `0` by default but appends a diagnostic. Pass `--fail-on-empty` to exit `1` instead — useful in CI, where a zero-file scan usually means a typo'd `include` or a rename that orphaned the configured paths, and a silent `0` exit would let that slip past a pipeline gating on the exit code. `--fail-on-empty` is off by default because an empty scan is a normal outcome for interactive/exploratory use — a brand-new project, or a directory that hasn't grown any Python files yet.

The text reporter's existing `empty scan: no Python files selected` summary line grows a guidance clause naming what to check, based on which config format resolved:

- No config file found: create one with an `include` list, or pass explicit paths (`house-lint check <path>`)
- `pyproject.toml` resolved: check the `include` list in its `[tool.house-lint]` table
- A standalone config resolved: check the `include` list in its `[house-lint]` table

The JSON reporter carries the same message under a `zero_file_diagnostic` key, present only when the scan found zero files — it has no equivalent in ordinary output. This key is a presentation-layer addition layered on top of the `check` output's `schema_version: 2` contract, not part of it: it can change shape or be removed without a schema version bump, unlike the versioned fields in `to_dict()`'s output.

The guidance clause is suppressed in exactly two cases — the base "empty scan" message still appears in both, just without it: `include = []` explicitly configured (an intentional empty scan), and explicit paths given on the command line. A typo'd explicit `include` (e.g. `include = ["tset"]` for a `tests/` directory) is *not* suppressed — a config that names a directory that doesn't exist is the most common real trigger for this diagnostic, and it should not be silently swallowed.

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
