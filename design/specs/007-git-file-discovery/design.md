# Design: Take Discovery's File List From Git

**Date:** 2026-10-30
**Status:** ratified
**Mode:** sketch
**Issues:** #64
**Prior art:** design/research/2026-10-30-git-ls-files-file-discovery/prior-art.md

## Summary

`src/house_lint/discovery.py` is 1,031 lines, and much of it reimplements git's ignore rules on `pathspec`. Another 749 lines of parity and fuzz tests keep that reimplementation honest. It works: the fuzz suite shows zero divergence. The case for replacing it is the cost of owning git's semantics, not a correctness bug, and #57 and #59 are about to build on discovery.

The choice to replace is settled, and this ledger decides how. Inside a git repo, each directory discovery is asked to scan is listed with `git ls-files -z --cached --others --exclude-standard`. Outside a repo, or under `--no-gitignore`, discovery does a plain `os.walk` with no gitignore handling. D9 decides how what they return is filtered. Symlink handling, root containment, the file limit, built-in excludes, and configured `exclude` all stay.

These follow from the settled choice and need no decision. Each is a behaviour change that the `BREAKING CHANGE:` footer has to name:

- A tracked file matching an ignore pattern is now scanned.
- A tree that isn't a repo no longer honours `.gitignore`.
- A scan rooted below the repo root now honours the repo's `.gitignore` files above it and `.git/info/exclude`. Today nothing above the root is read.

The footer also names every break the decisions below accept, which their `**Ratified:**` sentences state, as one `BREAKING CHANGE:` footer with a `####` list per `.claude/rules/changelog-quality.md`.

**Homes:** the git listing and repo detection are new module-level functions in `src/house_lint/discovery.py`, called from `_FileSelector` in place of the gitignore half of `_walk`. There's no new module, because the listing is about 30 lines and only discovery calls it. The `git check-ignore` probe from D2 is a function beside the listing, called by the requested-path handling when a listing comes back empty, and its failures follow D1. D9's split makes the requested-path handling what remains of `_consider`, and the discovered-path filter a new `_FileSelector` method. `exclude` and built-in matching (D3) replace `_patterns` and `_ignored` with a compiled `GitIgnoreSpec` per set and one module-level function that checks a path and its ancestors. The `!` rejection lives in `config.py`'s `_validate_exclude` and raises `ConfigError`, as the `per-file-ignores` check beside it does. The per-directory matcher stack is deleted: `_load_gitignore_lines`, `_is_gitignore_excluded`, `_directory_gitignore_context`, `_own_matcher`, and `_own_gitignore_lines`, about 165 lines. D3 decides whether a further 280 or so go.

**Out of scope:** language-tagged discovery (#57), project-rule passes (#59), and config discovery (`resolve_project`), which keeps its own `.git` boundary rule. `pathspec` stays a dependency, because `config.py` validates patterns and `per-file-ignores` uses it.

**Also updated by this change:**
- `docs/configuration.md`: the discovery paragraphs, including the promise that an ignored directory is "skipped without being enumerated", which no longer holds for an excluded tree git lists; and the reimplementation section with its divergence table.
- `README.md`: the skipped-file notes, and the "without shelling out to git" sentence.
- `CLAUDE.md`: the Gotchas on discovery, and the layout note.
- `design/specs/001-llm-lint-extraction/design.md`: a dated `## Addendum` entry recording that its "do not shell out to Git" constraint is reversed here. Its body stays frozen.

## Posture

- **Failure handling:** loud failure. A git failure inside a repo stops the run with a discovery error. No retries, no timeout, no silent fallback.
- **Compatibility:** break freely; every consumer pins its version. No shims. Ships as `feat!` with a `BREAKING CHANGE:` footer in the PR description.
- **Scope:** minimal. Replace gitignore handling only. No submodule recursion, no new flags, no git-based speedups. Symlink, containment, and file-limit behaviour stays as it is.
- **Rollout:** ships outright, with no flag or migration. Lands before #57 and #59.
- **Hardening:** project defaults. No new logging or metrics, since a person watches this CLI. No `ls-files` timeout, which accepts a hang on slow bind mounts like the one Mega-Linter reports on WSL2.
- **Altitude:** `build`; one session builds it, before the milestone work moves discovery.

## Decisions

### D1: How does discovery tell "not a repo" from "git failed"?

**Situation:** git exits 128 for "not a repository" and for a `safe.directory` ownership refusal, so a tool that reads the exit code can't tell them apart. If a refusal falls through to a plain walk, the run silently scans gitignored files, and a CI run with a different owner gets different results from a laptop. editorconfig-checker and osemgrep both fall back silently, and editorconfig-checker #261 is the resulting bug report. ansible-lint told them apart by matching English error text.

**Deciding factor:** a broken git can never quietly change what gets scanned.

| | A: Look for a `.git` entry walking up from the directory; inside one, any git failure stops the run | B: Run `git rev-parse` with `LC_ALL=C`, match "not a git repository" in stderr | C: A, plus `-c safe.directory=<dir>` on the listing call | D: Any git failure warns and walks |
|---|---|---|---|---|
| Dubious ownership in Docker CI | Stops, showing git's message | Stops, showing git's message | Lists normally | Walks, scanning gitignored files |
| git not installed, inside a repo | Stops, and says `--no-gitignore` walks instead | Needs its own branch to detect | Stops | Walks |
| Depends on git's message text | No | Yes | No | No |
| git needed outside a repo | No | Yes, one probe per run | No | Yes |
| Bypasses a git security check | No | No | Yes: `ls-files` in someone else's repo can read their config, including `core.fsmonitor` | No |
| Machinery | About 5 lines, same marker idea as `resolve_project` | About 15 lines | A plus one argument | About 5 lines |
| What peers do | pre-commit hard-fails; nobody uses a marker | ansible-lint matched text | ansible-lint, until it removed git | editorconfig-checker, osemgrep, Mega-Linter |

**Recommendation:** A, because it decides from the filesystem and turns every other outcome into a visible error. The error carries git's stderr and names `--no-gitignore` as the way to scan without git. A failure to start git (`OSError`, including a missing binary) is the same discovery error, with the same hint. The listing call passes `-c core.fsmonitor=false`, because `ls-files` otherwise runs a command from the scanned tree's own `.git/config` (probed), which matters for a tree extracted from an untrusted archive. Non-empty stderr on exit 0 is also a recorded discovery error carrying git's text, because `ls-files` warns and exits 0 on an unreadable directory while leaving its files out; a stray git warning therefore stops the run too, and `--no-gitignore` is the way past it. A `.git` entry git itself rejects (empty directory with no repo above it, a dangling `.git` file, across a filesystem boundary) also stops the run, which is correct for something that looks like a repo and isn't.
**Pick B instead if** you expect a stray `.git` in ancestors to be common enough that stopping is wrong. **Pick C instead if** you run house-lint in containers with mismatched ownership often and would rather not set `safe.directory` there. **Pick D instead if** you'd rather lint too much than stop a run.
**Reversibility:** easy
**Ratified:** Chose A over B, C, and D, so a broken git can never quietly change what gets scanned in a repo visible on the filesystem, accepting that a stray or rejected `.git` entry or a stray git warning stops the run until `--no-gitignore` is passed, that an exported `GIT_DIR` with no `.git` entry gets the plain walk, and that an empty `.git` directory below a real repo lists through that repo.

### D2: What happens to a file named explicitly, including one git ignores?

**Situation:** `house-lint check a.py b.py` is how pre-commit calls the tool. Today a named file that matches a `.gitignore` is skipped, and fix #48 made a named file and a full scan agree on that. `git ls-files` returns nothing for an untracked ignored path, so it can't answer the question for named files. Staged files are tracked, and tracked files are scanned under this design, so hook runs are unaffected either way. The case that differs is a person or shell glob naming an untracked gitignored file. A requested directory is listed through git; when the listing comes back empty and `git check-ignore` reports the directory as ignored, it gets the plain walk instead, whether it's named, in `include`, or the root. Otherwise `check build/` would report zero files with exit 0 while `check build/gen.py` lints the file. An ignored directory holding a tracked file isn't reported as ignored (probed), so it lists tracked files only.

**Deciding factor:** hook runs need no git process, and a person who names a file gets it linted.

| | A: Named files skip the gitignore check; built-in and configured excludes still apply | B: Filter named files with `git check-ignore --stdin` | C: Named files skip everything, excludes included |
|---|---|---|---|
| git processes in a pre-commit run | 0 | 1 | 0 |
| `check ignored.py` | Linted | Skipped, as today | Linted |
| Shell glob `check **/*.py` over a gitignored `build/` | Lints `build/` | Skips `build/` | Lints `build/` and `.venv/` |
| `check build/`, all untracked | Linted, by the plain walk | Zero files | Linted, by the plain walk |
| Named file and full scan agree | No, for untracked gitignored files; yes for excludes | Yes | No |
| Hook mode works without git | Yes | No | Yes |
| What peers do | No tool splits it this way; Ruff's `--force-exclude` is the nearest analogue | Super-linter, per file | pre-commit, ansible-lint, osemgrep, editorconfig-checker |

**Recommendation:** A. It keeps the common case free of git and of a subprocess, and matches what peers do for gitignore. Excludes keep applying because they're house-lint's own config, and `exclude` is how a project keeps a hook from linting a generated file it has committed. This partly reverses #48's agreement for untracked gitignored files, which the docs must state.
**Pick B instead if** you often pass shell globs that sweep in gitignored files and want today's result. **Pick C instead if** you want "named means linted" with no exceptions and accept hooks linting committed generated files.
**Reversibility:** easy
**Ratified:** Chose A over B and C, so hook runs need no git process and a named file gets linted while excludes keep applying, accepting that a named untracked gitignored file or directory is linted where a full scan skips it, at the cost of one `git check-ignore` when a requested directory's listing is empty, and that a gitignored file named in `include` is linted the same way.

### D3: What does configured `exclude` support once gitignore handling is git's job?

**Situation:** `exclude` and the built-in excludes run through the same hand-built matcher as `.gitignore` files: `_match_patterns`, `IgnorePattern`, `_is_anchored_pattern`, `_build_patterns`, `_trailing_whitespace_trimmed`, `_normalized_gitignore_line`, `_patterns`, and `_ignored`, about 280 lines. It exists largely for negation, because `GitIgnoreSpec`'s aggregate matcher gets `["*", "!*/", "!*.py"]` wrong. Keeping full semantics for `exclude` keeps the hardest code this change set out to delete, and most of the parity suite. In the projects checked (house-lint, hassette, hass-hassette, otf-api), the only `exclude` is `["design"]`, and nothing uses a negation.

**Deciding factor:** delete the matcher unless something needs it.

| | A: Gitignore patterns without negation; `!` is a config error; match the path and each ancestor (in directory form, with a trailing `/`) with `GitIgnoreSpec` | B: Keep negation, but use `GitIgnoreSpec`'s aggregate matcher and document its divergences | C: Keep the hand-built matcher and full git semantics |
|---|---|---|---|
| Lines removed from `discovery.py` | About 445 of 1,031 | About 445 | About 165 |
| `exclude = ["src/generated/"]`, globs, `**` | Work | Work | Work |
| Whitelist idiom `["*", "!*/", "!*.py"]` | Config error naming the problem | Silently prunes every subdirectory | Works |
| Directory excluded, then a negation inside it | Can't be written | Depends on pattern order | Stays excluded, as git |
| Parity tests still needed | A small table | A divergence table | Most of the suite |
| Current projects affected | None | None | None |
| What peers do | Unknown; the briefs didn't check Ruff's `exclude` | Black accepts `pathspec` divergences | Nobody surveyed hand-builds this |

**Recommendation:** A. Negation's only job was overriding a broader pattern, and nothing uses it. Without it, the aggregate's priority defect can't occur, so the simple matcher agrees with git for the shapes in D4's parity table; a pattern with an escaped trailing space is a known divergence, recorded in the docs. A rejected pattern fails loudly where B would silently scan the wrong files.
**Pick B instead if** you have a config in mind that needs negation and can accept the divergence. **Pick C instead if** you want `exclude` to keep its documented "follows git" promise in full, accepting that this change then saves far less code.
**Reversibility:** easy (a later change could re-add negation; nothing stored depends on it)
**Ratified:** Chose A over B and C, to delete the hand-built matcher while `exclude` agrees with git for the shapes the parity table checks, accepting that `!` patterns in `exclude` become a config error and that a pattern with an escaped trailing space diverges from git.

### D4: What stays checked against real git?

**Situation:** `test_gitignore_parity.py` (482 lines), `test_gitignore_fuzz.py` (267 lines), and `_git_harness.py` compare discovery's verdicts with `git check-ignore`. Once git supplies the list, comparing git with git proves nothing for `.gitignore`, but `exclude` still matches patterns itself and documents gitignore syntax.

**Deciding factor:** keep a check only where house-lint still makes a verdict itself.

| | A: Keep a parity check for `exclude` only; delete the gitignore rows and the fuzz suite | B: Delete all of it; rely on unit tests of `exclude` | C: Keep the fuzz suite pointed at `exclude` too |
|---|---|---|---|
| Test lines removed | Most of 749 | All 749 | About half |
| A `pathspec` upgrade changes `exclude` matching | Caught | Caught only for shapes unit tests name | Caught, including unforeseen shapes |
| CI cost | Small | None | Thousands of `git check-ignore` calls |
| Harness kept | Yes, about 90 lines | No | Yes |

**Recommendation:** A. A short table keeps the "same syntax as git" claim honest at little cost, and `pathspec` upgrades are the realistic way it breaks.
**Pick B instead if** you'd rather document `exclude` as `pathspec` syntax and stop promising git's. **Pick C instead if** you want coverage of pattern shapes nobody thought to list and accept the CI cost.
**Reversibility:** easy
**Ratified:** Chose A over B and C, to keep `exclude`'s "same syntax as git" claim checked at little CI cost, accepting that a pattern shape nobody listed goes uncaught, and that this ledger doesn't size the test rewrite around `.gitignore` fixtures and `files_skipped` asserts, which the build finds from test failures.

### D5: What happens to `files_skipped`?

**Situation:** `files_skipped` appears in the JSON output and in the text line `files: scanned N, skipped M`. Today it mixes non-Python files, symlinks, duplicates, and pruned directories at one each, so it's already not a meaningful number. git doesn't report what it ignored, so the count can't stay the same. A chunk of `_traversable_dirs`'s docstring exists only to keep it stable across runs.

**Deciding factor:** don't report a number that can't mean anything.

| | A: Remove it from JSON and text; bump `schema_version` to 2 | B: Keep it, counting only what discovery itself drops after listing | C: Add `git ls-files --others --ignored --exclude-standard` to count ignored files |
|---|---|---|---|
| Meaning | n/a | Non-Python files and excluded files, not gitignored ones | Closer to today's, still mixed |
| Same number in a repo and in a plain walk | n/a | No | No |
| Extra git processes | 0 | 0 | 1 per listing, and it can enumerate a huge `.venv` |
| Output contract | Breaking, one version bump | Same key, new meaning | Same key, new meaning |
| Interaction with #60's planned schema bump | #60 moves to the next number | None | None |
| What peers do | editorconfig-checker and ansible-lint report no count | osemgrep keeps a typed list, because it filters candidates itself | Mega-Linter runs this call, to subtract rather than to count |

**Recommendation:** A. No consumer reads it, and a key that silently changes meaning is worse than one that's gone. Only the `check` output's `schema_version` moves to 2; the `rules` output stays at 1, since its document doesn't change. The docs and `reporters/json.py` wording about "the `schema_version: 1` contract" is updated to name the `check` schema.
**Pick B instead if** a script of yours reads the key. **Pick C instead if** you want to see how much gitignore removed and accept the cost.
**Reversibility:** easy
**Ratified:** Chose A over B and C, so the output stops reporting a number that can't mean anything, accepting a `check` `schema_version` bump to 2 and that any script reading the key breaks.

### D6: Do global excludes count?

**Situation:** `--exclude-standard` reads the user's global ignore file as well as `.gitignore` and `.git/info/exclude`, so a scan could differ between machines. It can only affect untracked files, because `--cached` lists tracked files regardless of any ignore rule. A clean CI checkout has no untracked source files, so CI results don't change. The difference shows up only on a developer's machine, for a file that machine's git already hides from `git status`. `-c core.excludesFile=` turns the global file off and leaves `.git/info/exclude` active (checked locally).

**Deciding factor:** match what `git status` shows, with the least machinery.

| | A: Accept git's answer as is | B: Pass `-c core.excludesFile=` |
|---|---|---|
| Same result for tracked files everywhere | Yes | Yes |
| Untracked file a global rule hides | Not scanned, matching `git status` | Scanned, unlike `git status` |
| Machinery | None | One argument, plus a comment explaining it |
| What peers do | Every tool read | None found |

**Recommendation:** A. Peers all accept it, and the difference can't reach CI.
**Pick B instead if** you want local runs to scan untracked files your global ignore hides.
**Reversibility:** easy
**Ratified:** Chose A over B, to match what `git status` shows with no extra machinery, accepting that an untracked file hidden by a user's global ignore isn't scanned on that machine.

### D7: Are submodules and nested clones scanned?

**Situation:** the walker descends into submodules and nested clones today. `ls-files` prints a submodule as a bare path (`vendor`) and an untracked nested repo as `nested/`, and `--recurse-submodules` errors when combined with `--others` (checked locally). Handling it means a second git call, and the code that adds it also has to keep the two lists' results in step.

**Deciding factor:** do what peers do, unless a real project needs more.

| | A: Don't scan them; drop directory entries silently | B: Second `git ls-files --cached --recurse-submodules` call and merge |
|---|---|---|
| Submodule contents | Not scanned (a change from today) | Scanned, tracked files only |
| Git processes per listing | 1 | 2 |
| Vendored third-party code in findings | No | Yes |
| What peers do | editorconfig-checker, pre-commit `--all-files` | ansible-lint, which later removed it |

**Recommendation:** A. A submodule is another project with its own lint config, and a `check vendor/` named directly still lists it. Directory entries are told from directory symlinks by checking for a symlink first, so "directory symlink is not traversed" still fires as it does today.
**Pick B instead if** one of your projects keeps first-party code in a submodule.
**Reversibility:** easy
**Ratified:** Chose A over B, to keep one git call per listing and third-party code out of findings, accepting that a full scan no longer covers submodules or nested clones, and that an empty scan caused by Python living only in submodules still points at `include`.

### D8: What does `--no-gitignore` do?

**Situation:** the flag exists today and turns off `.gitignore` handling at every level. After this change the plain walk already exists for trees that aren't repos.

**Deciding factor:** one code path for "no git".

**Recommendation:** make `--no-gitignore` run the same plain walk used outside a repo: no git process, no ignore files of any kind, built-in and configured excludes still applied. It's also the way past a git failure that D1 names in its error.
**Pick differently if** you want the flag to mean something narrower, such as listing through git but including ignored files.
**Reversibility:** easy
**Ratified:** Chose the plain walk over listing through git with ignored files included, so there is one code path for "no git" and a way past any git failure, accepting that the flag also turns off `.git/info/exclude` and global excludes.

### D9: How are discovered files filtered?

**Situation:** `_consider` mixes two jobs: handling a path someone asked for (named on the CLI, in `include`, or the root), and deciding on a path discovery found. A flat git list breaks that mix three ways. Its directory branch recurses into submodules and nested clones. It checks symlinks before excludes (`discovery.py` `_consider`), the reverse of the rule `_traversable_dirs` documents, so `.venv/lib64 -> lib` in an unignored `.venv` becomes exit 3. It also runs excludes after every stat call: on 50,000 unignored files, `ls-files` took 0.09 s and the stat calls 2.4 s.

**Deciding factor:** the listing path can't recurse, and an excluded path never touches the filesystem or produces an error.

| | A: Split requested from discovered; one filter for git and walk | B: Add a filter for git output only; the walk keeps `_consider` | C: Keep one `_consider`; filter excludes first and add a "don't recurse" flag |
|---|---|---|---|
| Submodule entry | Dropped | Dropped | Dropped, by the flag |
| `vendor/link -> ../x` under `exclude = ["vendor"]` | Dropped | Dropped | Dropped, if the pre-filter uses the listed path |
| 50,000 unignored files in an excluded tree | String match only | String match only | String match only |
| Same file set from git and walk, for the same paths | One filter guarantees it | Two filters can drift | One filter, with branches |
| `explicit_paths` boolean | Only on requested paths | Stays, and a second path appears | Stays, plus a second boolean |
| Size of `discovery.py` | Smallest: `_consider` shrinks | Largest | Middle |
| What peers do | Closest to opengrep's own filter over git's candidates | Unknown | Unknown |

**Recommendation:** A. A requested path keeps today's logic: exists, containment, symlink, then a directory is listed or walked and a file is selected, with excludes matched against the resolved location. A discovered path, from the git list or a plain walk's files, is checked in this order: excludes by string match on the path as listed plus its ancestors, with no filesystem call; `lstat`, where missing means dropped; a directory symlink records today's "directory symlink is not traversed" error and a file symlink is dropped; a plain directory is dropped, so a listing can't recurse; a suffix other than `.py` is dropped; then the file is selected, subject to the file limit. A discovered path needs no `resolve()` or containment check, because neither git nor the walk goes through a symlinked directory and both start from a resolved directory. The plain walk keeps directory-level pruning, so an excluded `node_modules` stays cheap outside a repo.
**Pick B instead if** you want the plain walk left unchanged in this PR. **Pick C instead if** you'd rather keep one function at the cost of a second boolean.
**Reversibility:** easy
**Ratified:** Chose A over B and C, so a listing can't recurse and an excluded path never touches the filesystem, accepting that `_consider` is split in the same change that replaces the matcher.

## Assumed

- `--cached` lists a tracked file deleted on disk, which the `lstat` step in D9 drops, so no second git call is needed. Evidence: checked in a scratch repo.
- `_FileSelector.selected` is keyed by path, so an unmerged file listed twice during a conflict needs no `--deduplicate`. Evidence: `src/house_lint/discovery.py` `_consider`'s `resolved in self.selected` check.
- `-z` is required, because without it git quotes non-ASCII names; names are decoded with `os.fsdecode`. Evidence: `git-ls-files(1)` and the brief's local check.
- Running git with its working directory set to the directory being listed restricts the output to that subtree, relative to it, so no pathspec handling is needed and `include` paths containing glob characters stay literal. Evidence: local check recorded in the brief, Q6.
- A built-in or configured excluded directory that is requested directly is rejected before any listing. Evidence: `_consider`'s directory branch.
- Listing from inside a pre-commit hook gives the right result with git's inherited environment, including the temporary index of `git commit <paths>`. Evidence: a real pre-commit hook in a scratch repo listed the expected files from the root and from a subdirectory, with `GIT_INDEX_FILE` both relative and absolute. A hook whose scan root is a different repo from the one committing would inherit the wrong `GIT_DIR`; that is accepted, since nothing does this.
- `ls-files` takes about 3 ms on this repo, so #59's two discovery passes (spec 005 D10) add no meaningful cost. Evidence: timed in this worktree.
- Spec 005's D6 ("gitignore and exclude handling shared, as today") still holds at the level it cares about: every language shares one list. Its ledger is not edited; #57's and #59's pickup sketches read the landed code. Evidence: `design/specs/005-cross-file-rules-and-duplicate-code/design.md`.
- A discovery failure already has a run-stopping path: `DiscoveryError` becomes a `ScanResult` carrying the structured error. Evidence: `src/house_lint/pipeline.py` `run_scan`.

## Build

- [x] Implementation and tests committed
- [x] Docs
- [ ] Ship-time challenge

**Calls made during the build:**
- A tracked file whose parent directory became a symlink is dropped: D9 says a discovered path needs no containment check because neither source goes through a symlinked directory, but `--cached` does list such a file, and it read a file outside the root (reproduced). Posture keeps containment and D9's ratified behaviour is unaffected. A per-directory `realpath` comparison covers it, and `git status` calls that file deleted, so it follows the "missing means dropped" step.
- A directory symlink rechecks excludes in directory form before recording its error: git lists a symlink as a file, so a symlinked `.venv` misses the directory-only `.venv/` on D9's first string match and would otherwise become exit 3.
- Exclude matching is a `_FileSelector` method rather than the module-level function the Summary names: it needs a per-directory verdict cache, since each `match_file` costs about 6 µs per spec.
- Discovered paths travel as root-relative strings, with a `Path` built only after excludes: on 50,000 files in an excluded tree, `pathlib` was most of the 1.2 s, and this takes it to 0.18 s. The unexcluded case went from 2.42 s to 1.61 s.
- A git failure is `traversal-error` with operation `git`, the kind closest to "couldn't enumerate", and doesn't add an error code.
- The repository check uses `os.path.lexists`, so a dangling `.git` symlink counts as an entry git rejects, which D1 wants to stop the run. `resolve_project`'s `.exists()` is out of scope.
- D3's known divergence is narrower than its wording: a singly escaped trailing space agrees with git. The one that differs is a doubled backslash before a trailing space. The docs say that, and `test_exclude_parity.py` pins it.
- The listing is sorted so the file limit keeps the same files whichever order git emits.
- `test_gitignore_parity.py` is renamed `test_exclude_parity.py`, since `exclude` is all it checks. The CI and `pyproject.toml` comments about the fuzz suite's `CI` gate are gone with the suite.
