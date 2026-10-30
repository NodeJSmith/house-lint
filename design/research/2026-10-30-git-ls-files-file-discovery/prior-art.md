---
topic: "Edge cases of taking a linter's file list from git ls-files"
date: 2026-10-30
status: Draft
---

# Prior Art: Taking a Linter's File List From `git ls-files`

## The Problem

house-lint is replacing its `pathspec`-based reimplementation of git's ignore rules with `git ls-files --cached --others --exclude-standard` inside a repo and a plain walk outside one (#64). The choice to replace is made. This brief covers how tools that list files through git handle what goes wrong: git missing or refusing the repo, explicit paths, deleted files, submodules, global excludes, sub-root scans, tracked-but-ignored files, and skip counts. It builds on `design/research/2026-10-30-linter-engine-architecture/prior-art.md`, which covers whether peers reimplement ignore rules.

## How We Do It Today

`discovery.py`'s `_FileSelector` walks with `os.walk`, reading every `.gitignore` from the scan root down into a per-directory matcher stack, and never calls git. Configured `exclude` and the built-in excludes use the same hand-built matcher, and the parity and fuzz suites test both against real `git check-ignore`.

## Relevance to Us

- **Two migrations ran the other way.** ansible-lint ran this exact command and replaced it with a `pathspec` walk (PR #3507, motive unknown). osemgrep dropped git-only listing when `.semgrepignore` gained negation, a reason that doesn't apply to a tool with no ignore file of its own. pre-commit and editorconfig-checker still list through git.
- **The reported bugs are mostly in failure handling and parsing.** They come from treating every git error as "not a repo", warning and continuing on a failed listing, and splitting output without `-z`. Separating detection from listing and failing loudly avoids the first two, and `-z` avoids the third.
- **Peers don't carry much of the machinery on the table.** Nobody strips global excludes, only ansible-lint ever recursed into submodules (later removed), and nobody reports a skip count when git alone does the filtering.
- **Hook mode needs no listing.** pre-commit passes explicit paths, and every peer that takes explicit paths lets them skip the git listing.
- **What the survey doesn't cover:** `exclude` keeps needing its own matching whichever way discovery goes. That is a house-lint question this brief doesn't answer.

## Recommendation

Proceed with the replacement. Detect the repo separately from listing, and treat a git failure inside a repo as a run-stopping error. Use `-z`, drop deleted files and directory entries (gitlinks, nested clones) with a filesystem check, and let explicit file paths skip the git listing. Accept global excludes and unrecursed submodules as peers do, and don't report a gitignore skip count.

Coverage gaps: gitleaks and CodeSpell source weren't read, ansible-lint's motive is unknown, and dubious-ownership behaviour couldn't be reproduced locally (no second user), so it relies on cited sources.

## Answers By Question

### 1. Repo detection and failure

| Tool | Detection | On git missing / git refuses repo |
|---|---|---|
| editorconfig-checker | None. Just runs `ls-files` | Any error silently falls back to a cwd walk (files.go L133-L141). Bug: #261 |
| opengrep/osemgrep | `git -C <dir> rev-parse --show-toplevel`; exit non-zero gives `None` (Git_wrapper.ml L495-L499) | **Silent fallback to a filesystem walk**, including for dubious ownership, because the repo check cannot tell it from "not a repo". Once detection succeeds, a later `ls-files` failure raises `Error "Could not get files from git ls-files"` (L405-L411). Other git commands print the `possible_reasons` block (L262-L283) |
| ansible-lint (historical) | Ran `ls-files` and treated `returncode == 128 and "fatal: not a git repository" in output` as "not a repo" | Not-a-repo: silent. Other git errors: `warn_or_fail` (warning, error under `--strict`; commit 8844e7b2). Git binary missing (`FileNotFoundError`): warning only when `-v` (PR #3507 diff, removed code). Dubious ownership avoided by `-c safe.directory=<cwd>` (PR #2890) |
| pre-commit | `git rev-parse --show-cdup` + `--is-inside-git-dir` | **Hard error**: `FatalError('git failed. Is it installed, and are you in a Git repository directory?')` (git.py L52-L73) |
| Mega-Linter | GitPython `Repo(...)` | `InvalidGitRepositoryError`/`GitCommandError` becomes a warning, falls back to a full listing (L967-L976). For the ignore list: warning, empty list, files linted anyway (L1027-L1034) |
| Super-linter | none | `fatal` on failure |

Bug reports caused by silent fallback: editorconfig-checker #261 (container without git scanned `.git/`); ansible-lint #3062 (nothing linted, CI green). Other tools hitting dubious ownership in container CI: opam https://github.com/ocaml/opam/issues/6963, and dorny/paths-filter PR #317 (retry with a temporary `HOME` holding a `safe.directory` entry). I could not find an opengrep or Semgrep issue specifically about the silent fallback. Not found, not absent.

### 2. Explicit paths on the command line, staged files from pre-commit

- **pre-commit**: `args.files` go through unchanged (run.py L266). Only an `os.path.lexists` filter applies (L75). Staged-file lists come from git, not from `ls-files`.
- **editorconfig-checker**: explicit paths skip git completely. Directories are walked, files are added (files.go L113-L129). Issue #50 comment (2023): ignores "are processed when not providing target paths on the command line, but not when target paths are provided".
- **ansible-lint**: since PR #3468, explicit file or folder args are never excluded ("treated as explicit and exclude rules will not apply").
- **opengrep/osemgrep**: explicit targets are "should not be filtered out even if they match some exclusion patterns" (Find_targets.ml L1015-L1030). `force_select_scanning_roots` re-adds them after filtering. A named file bypasses `.min.js` and size limits (L1135-L1147). One edge: git normalizes `libs/../README.md` to `README.md`, so an unnormalized explicit path can be treated as non-explicit (comment at L131-L134).
- **Mega-Linter**: skips the gitignore enumeration entirely when `files_to_lint` is non-empty (L1004-L1008).
- **Super-linter**: `check-ignore` per file when `IGNORE_GITIGNORED_FILES=true` (buildFileList.sh L493).
- None of them run `git check-ignore` on pre-commit-passed files. Ruff's `--force-exclude` is the only analogue in the set and is not git-based.

### 3. Tracked files deleted on disk

- editorconfig-checker: `os.Stat` + `IsRegular()` per path (files.go L141-L149).
- pre-commit: `os.path.lexists` (run.py L75). `lexists` also keeps broken symlinks.
- ansible-lint: a second command `ls-files --deleted -z`, subtracted (issue #1647, PR #1650, commit 5c1f4835 diff).
- opengrep: per-file access and kind filtering (`under_dropped_dir`, `filter_path`, `Skip_target.filter_file_access_permissions`).
- Verified locally: `git ls-files --cached` still lists a file removed with plain `rm`.
- A stat check is the common choice; the `--deleted` subtraction costs another git process and race window.

### 4. Submodules

- **Gitlink entries**: `--cached` prints the submodule path as an ordinary entry (verified locally: `vendor` appeared next to files). editorconfig-checker drops it via `IsRegular()`. pre-commit's classifier gives it directory tags, which hooks' `types` filters exclude. opengrep drops directory entries (`| Dir -> ()`).
- **Contents scanned?** ansible-lint is the only one that went for it (PR #3431), with an extra `--recurse-submodules` call because it "is incompatible with --others". Verified locally: `git ls-files --recurse-submodules -o` errors with `unsupported mode`. Other tools don't bother: editorconfig-checker and pre-commit (for `--all-files`) never list submodule contents. pre-commit uses `--ignore-submodules` on its diff calls (git.py L148).
- ansible-lint removed all of it again in #3507.

### 5. Global excludes (`core.excludesFile`) and ignore files above the scan root

- `--exclude-standard` includes `.git/info/exclude`, per-directory `.gitignore`, and the user's global exclusion file (git docs). I verified locally that a global ignore hides an untracked file, and that `-c core.excludesFile=` (empty) turns the global ignore off while leaving `.git/info/exclude` active.
- **No tool I read suppresses the global file.** editorconfig-checker, ansible-lint and Mega-Linter use bare `--exclude-standard`. opengrep uses `--exclude-standard` for untracked files (`git_list_untracked_files`).
- Reproducibility concern (my inference, no tool reports it): the same repo lints differently on a machine with a global ignore pattern that matches source files. I found no bug reports on that.
- **Ignore files above the scan root**: osemgrep states "An ignore file above the scanning root is not read" for non-VCS projects (Find_targets.ml L~880 comment). Inside a repo, git reads the repo's own files, which are above a subdirectory root, by definition.

### 6. Scan root that is a subdirectory of the repo

- Verified locally: running `git ls-files ...` from `sub/` lists only that subtree, with paths relative to cwd. Files outside it are not listed.
- opengrep passes the canonical scanning root as a pathspec argument with `-C <project_root>`, then relativizes against the process cwd (Find_targets.ml L660-L745). The result keeps the root as the user typed it. A large part of that function handles Windows 8.3 names, case, and symlinked roots. A comment says "git rejects a path that leads outside the directory it runs in".
- ansible-lint ran `ls-files` in cwd and then filtered directory args by `startswith` on the string path (superseded version of `expand_dirs_in_lintables`, seen in the exa excerpt). Later versions use `path_is_inside` with `resolve().relative_to()` instead (file_utils.py L598-L604 on main).
- pre-commit does `os.chdir(toplevel)` and re-relativizes explicit args (main.py `_adjust_args_and_chdir`).

### 7. Tracked-but-ignored files

- Because `--cached` is always included, they are scanned. Verified locally: a force-added `ign.py` appears.
- osemgrep makes this deliberate: git list "may include files that would be excluded by the gitignore mechanism but are nonetheless being tracked by git (it happens)" (Find_targets.ml L1022-L1028), and passes `~exclude_standard:false` for the tracked list.
- pre-commit `--all-files` scans them too.
- Mega-Linter's approach (`--others --ignored`) subtracts them from the candidate list only, so tracked-and-ignored files are still linted.

### 8. A `--no-gitignore` flag and a skipped-files count

- osemgrep has `respect_gitignore` in its config and keeps a typed `skipped` list with reasons (`Out.skipped_target`, `skipped_of_ignored`), so skip reasons survive the git path. It filters the git candidate list through its own ignore filter afterwards, so counting is possible. That is a design where git supplies candidates and the tool does the excluding.
- Mega-Linter has `IGNORE_GITIGNORED_FILES`. Super-linter has `IGNORE_GITIGNORED_FILES`.
- editorconfig-checker and ansible-lint: no equivalent flag and no skipped count (ansible-lint #3507 diff has none; ec has no gitignore opt-out).
- No tool I read reports a skipped count when git alone does the filtering, because git does not say what it ignored. To count, you would need a second call (`ls-files --others --ignored --exclude-standard`, as Mega-Linter does) or a walk. This is the key tradeoff for the skipped-files count.

### Bug classes and code size

| Class | Evidence |
|---|---|
| Quoted/non-ASCII names without `-z` | Verified locally: without `-z`, `é.py` prints as `"\303\251.py"`. editorconfig-checker still splits on `\n` with no `-z`, so such files get `Stat` errors and are skipped without a message (my reading of files.go L133-L149; I did not find a bug report). ansible-lint added `-z` and a umlaut test (`test_discover_lintables_umlaut`, seen in the #3507 diff). opengrep comments on `-z` unquoting (Git_wrapper.ml L401). Filenames containing newlines are the same class |
| Duplicate entries during merge conflicts | Verified locally: `c.py` appeared twice in `--cached` mid-merge. pre-commit adds `--deduplicate`. Needed for `--cached` |
| `GIT_DIR` / `GIT_INDEX_FILE` in hooks | pre-commit #300 (comment in `no_git_env`), #1849, #3773. These are about write operations; not found for `ls-files` |
| Dubious ownership | ansible-lint #2890, #3062; opam #6963; dorny/paths-filter #317; Mega-Linter changelog (betterleaks fix, https://github.com/nvuillam/mega-linter/blob/master/CHANGELOG.md). Common in container CI |
| Empty list from a failed or swallowed git call | qwen-code PR https://github.com/QwenLM/qwen-code/pull/12650 ("a failed or empty git ls-files ... let the shellcheck lane report success having checked nothing") |
| Performance / hangs | Mega-Linter's 120 s timeout, observed hangs on WSL2/Windows bind mounts (MegaLinter.py L1348-L1356). osemgrep comment: git "could potentially speedup things" but isn't relied on solely (L566-L571). Sparse checkout and worktrees: no tool-specific bug reports found. Git docs add `--sparse` and the `S` skip-worktree tag, so `--cached` can list entries not on disk |
| Locale-sensitive parsing | ansible-lint matched English text `"fatal: not a git repository"` in output to detect non-repo (removed code). Brittle under non-English locales (my observation) |

Code spent on it: editorconfig-checker about 15 lines; pre-commit about 70 lines total across `get_root`, `no_git_env`, `get_all_files` and the `lexists` filter; Mega-Linter about 40 lines for ignore listing plus the timeout; ansible-lint peaked at about 85 lines in `discover_lintables` before deletion; opengrep about 20 lines for the wrapper plus about 130 lines of root/relativization and filter code.

## Patterns Found

### Pattern 1: One `ls-files` call, silent walk fallback

**Used by**: editorconfig-checker; osemgrep (repo detection); ansible-lint (not-a-repo case only); Mega-Linter (warn then fall back)
**How it works**: Run `git ls-files --cached --others --exclude-standard`. On any error, walk the directory. The repo check and the failure check are the same call, so "not a repo" and "git refused" look identical.
**Strengths**: About 15 lines. Works with no git installed.
**Weaknesses**: Silently produces different file sets on machines with and without git working (editorconfig-checker #261). In dubious-ownership CI, produces an unfiltered walk, so gitignored directories like `node_modules` get scanned. In ansible-lint's variant, git errors that were not "not a repo" became a warning and zero files linted (#3062).
**Example**: https://github.com/editorconfig-checker/editorconfig-checker/blob/0d06e93c/pkg/files/files.go

### Pattern 2: Distinguish "not a repo" from "git failed", and fail on the second

**Used by**: pre-commit (hard-fails on both); ansible-lint (not-a-repo silent, others `warn_or_fail`); Super-linter (`fatal`)
**How it works**: Separate repo detection from listing. Treat only a positive "not a repository" as permission to walk. Everything else is a diagnosable error, optionally downgraded to a warning unless `--strict`.
**Strengths**: Removes the CI-silently-passes failure. ansible-lint's `--strict` split is cheap.
**Weaknesses**: git exits 128 for both "not a repo" and "dubious ownership", and ansible-lint told them apart by matching English text. I found no tool that uses a locale-independent check; a sturdier check would be `git rev-parse --is-inside-work-tree` plus a separate probe, but I have not verified how it reports ownership errors.
**Example**: https://github.com/ansible/ansible-lint/commit/8844e7b2

### Pattern 3: Per-command `safe.directory` override

**Used by**: ansible-lint (`-c safe.directory=<cwd>`); osemgrep adds `-c safe.bareRepository=explicit`; dorny/paths-filter retries with a temp `HOME`
**How it works**: Prefix every git call with `-c safe.directory=<path>`. No global config is changed.
**Strengths**: Fixes the container-ownership case with one constant.
**Weaknesses**: This bypasses a git security check for whatever directory the linter is pointed at (my observation; ansible-lint accepted it for cwd). A newer git version may handle `safe.directory` only from global or system config; I did not verify that for the `-c` form, though ansible-lint shipped it and kept it until removing git use.
**Example**: https://github.com/ansible/ansible-lint/commit/5c1f4835

### Pattern 4: Git supplies candidates; the tool's own filter decides

**Used by**: osemgrep/opengrep
**How it works**: Tracked files (`--cached`, no exclude-standard) and untracked non-ignored files (`--others --exclude-standard`) come from two git calls, are unioned, and then go through the tool's own ignore layer. Explicit roots are force-added. A typed skipped list records why files were dropped.
**Strengths**: Supports tracked-but-ignored files, a skipped-files report, and a tool-specific ignore file without reimplementing gitignore.
**Weaknesses**: The most code of any tool (the path-relativization alone is about 100 lines), and Semgrep's own comment explains the git-only approach was dropped for a walk "whatever the value of respect_git_ignore" when they added `.semgrepignore` negation. Only needed if you want layered ignore files.
**Example**: https://github.com/opengrep/opengrep/blob/main/src/targeting/Find_targets.ml

### Pattern 5: Explicit paths bypass discovery

**Used by**: pre-commit, editorconfig-checker, ansible-lint (since #3468), osemgrep, Mega-Linter
**How it works**: Named files never go through git or the ignore filter. Discovery runs only when the user gave a directory or nothing.
**Strengths**: Matches pre-commit's model (hooks receive explicit staged paths), so hook mode needs no git listing. Avoids per-file git calls.
**Weaknesses**: An explicitly named gitignored file gets linted, which can surprise users running `tool **/*.py` from a shell. Only editorconfig-checker issue #50 complains about this; the other tools made the opposite complaint (#3468, #2628: files silently skipped).
**Example**: https://github.com/ansible/ansible-lint/pull/3468

## Anti-Patterns

1. **Splitting `ls-files` output on newlines.** editorconfig-checker (files.go L152) quotes and breaks on non-ASCII and newline names. Every other tool reading `ls-files` output uses `-z`. [verified locally]
2. **Treating every git error as "not a repo".** editorconfig-checker #261; opengrep's `project_root_for_files_in_dir` does the same and carries a TODO about stderr noise. Result: a different file set on machines with the same repo.
3. **Matching git's English error text.** ansible-lint's `"fatal: not a git repository" in exc.output` (removed in #3507) breaks under non-English locales (my inference from the code, not a reported bug), and merged stderr into stdout (`stderr=subprocess.STDOUT`), so a warning on stderr could end up parsed as a filename.
4. **Warn-and-continue on a failed listing.** ansible-lint #3062, Mega-Linter's ignore-list fallback, and the qwen-code CI bug all end with zero or too many files scanned and a green build.
5. **No dedup with `--cached`.** Unmerged entries repeat during a merge conflict; pre-commit passes `--deduplicate` for this reason. Verified locally.

## Gaps (not determined)

- **gitleaks, reviewdog, CodeSpell, yamllint file discovery from git**: I did not read gitleaks or CodeSpell source. Reviewdog consumes diffs rather than listing files. Treated as not applicable; no finding.
- **Trivy**: only checked that it walks the filesystem and ignores `.gitignore` (doc and issue #3670); no code read.
- **Whether a hook-time `GIT_INDEX_FILE` (temporary index during `git commit <paths>`) changes `ls-files --cached` results**: not tested, no source found.
- **Why ansible-lint dropped git discovery**: PR #3507 and its three linked issues describe exclusion bugs, not git failures. The real reason is unknown.
- **Dubious-ownership behaviour of `git rev-parse` vs `ls-files`**: could not reproduce locally (no sudo for a different-owner repo). Relied on the cited sources.
- **Semgrep (pysemgrep) bug reports about the dubious-ownership fallback**: none found.

## Sources Found

### ansible-lint, PR #2890 (safe.directory) diff
- **URL**: https://github.com/ansible/ansible-lint/commit/5c1f4835 (PR https://github.com/ansible/ansible-lint/pull/2890; follow-up https://github.com/ansible/ansible-lint-action/issues/138)
- **Type**: reference implementation
- **Key takeaway**: Defines `GIT_CMD = ["git", "-c", f"safe.directory={os.getcwd()}"]` and prefixes every git call with it. The comment says it exists for "GHA runners executing containerized reusable actions, where the mounted volume might have different owner".
- **Relevance**: The only concrete in-linter fix for dubious ownership that I found. It also shows the original command: `ls-files --cached --others --exclude-standard -z`, then `ls-files --deleted -z`, with the second set subtracted from the first.

### ansible-lint, issue #3062 and PR #3064 (silent failure)
- **URL**: https://github.com/ansible/ansible-lint/issues/3062 and https://github.com/ansible/ansible-lint/pull/3064 (commit 8844e7b2)
- **Type**: bug report and fix
- **Key takeaway**: When git failed during discovery (user B running in user A's clone), ansible-lint printed a WARNING and linted nothing. CI reported success. The reporter asked for `--strict` to make it an error. The fix routes git errors through `warn_or_fail`, so they warn by default and fail under `--strict`.
- **Relevance**: The direct bug report caused by a soft failure of git file listing. It is also precedent for the warn-or-fail split.

### ansible-lint, PR #3431 (submodules) diff
- **URL**: https://github.com/ansible/ansible-lint/commit/02804cd4 (PR #3431)
- **Type**: reference implementation
- **Key takeaway**: Added a third git invocation, `ls-files --cached --exclude-standard --recurse-submodules -z`, because the code comment says "--recurse-submodules is incompatible with --others we need to run twice to get combined results". The three result sets were then merged and `--deleted` subtracted.
- **Relevance**: The only tool I found that actually handles the `--recurse-submodules` / `--others` conflict. It cost them a whole extra git call. I verified the incompatibility locally: `git ls-files --recurse-submodules -o` fails with `fatal: ls-files --recurse-submodules unsupported mode`.

### ansible-lint, PR #3507 (git discovery removed) and #3468
- **URL**: https://github.com/ansible/ansible-lint/commit/05aad968 (PR #3507, "Reimplement file exclusion logic", fixes #3477, #3284, #3492); https://github.com/ansible/ansible-lint/pull/3468 ("Do not ever exclude files given as arguments")
- **Type**: reference implementation
- **Key takeaway**: #3507 deletes the whole git-based `discover_lintables` (about 85 lines, including the test that simulated no-git and outside-repo by setting `PATH=""` and `GIT_DIR=""`). It replaces it with a recursive walk that uses `pathspec.GitIgnoreSpec` on the root `.gitignore` plus a built-in exclude list. #3468 made explicit CLI paths bypass all excludes. Current code: https://github.com/ansible/ansible-lint/blob/main/src/ansiblelint/file_utils.py (`get_all_files`).
- **Relevance**: A mature Python linter that tried the git approach for about two years and left. The PR bodies give no rationale ("Fixes: #3477, #3284, #3492"). The linked issues are about excludes being ignored (#3477) and content outside cwd being silently skipped (#3284). Those are exclusion-logic and scoping bugs, not git-failure bugs. I could not find a statement that git itself was the reason. Treat the motive as unknown.

### ansible-lint, PR #1650 / issue #1647 (deleted files, untracked files)
- **URL**: https://github.com/ansible/ansible-lint/issues/1647, https://github.com/ansible/ansible-lint/pull/1650
- **Type**: bug report and discussion
- **Key takeaway**: Plain `git ls-files` returned unstaged-deleted files, and the linter then tried to lint files that no longer existed. The fix was a second `git ls-files --deleted -z` subtracted from the first. The maintainer (ssbarnea) wrote: "the original reason why we started using git ... is related to gitignore. We did not want to force people to maintain a two list of excludes ... Parsing the .gitignore file is far from easy, so we ended up reusing git."
- **Relevance**: Direct answer to Q3, and the maintainer's stated motive matches house-lint's.

### editorconfig-checker, `pkg/files/files.go` and issue #261
- **URL**: https://github.com/editorconfig-checker/editorconfig-checker/blob/0d06e93c/pkg/files/files.go (L133-L153) and https://github.com/editorconfig-checker/editorconfig-checker/issues/261
- **Type**: reference implementation and bug report
- **Key takeaway**: Runs `git ls-files --cached --others --exclude-standard`, with no `-z` and a newline split. On any error from the command it falls back to a directory walk of cwd (comment: "It is not a git repository."). Each path is then `os.Stat`ed and kept only if `IsRegular()`. Explicit CLI paths (`config.PassedFiles`) never go through git. Issue #261: results differed between host and container because git was absent in the container, and "no warning is issued when Git binary is not available or the command fails for other reasons". The walk then scanned `.git/`. The fix (PR #427) added `.git` and `node_modules` to the default excludes. It did not add a warning.
- **Relevance**: House-lint's exact command, about 15 lines of code, with the silent fallback and the missing `-z`. It is a worked example of what happens without the details.

### pre-commit, `pre_commit/git.py` and `commands/run.py`
- **URL**: https://github.com/pre-commit/pre-commit/blob/main/pre_commit/git.py (L27-L46 `no_git_env`, L52-L73 `get_root`, L155 `get_all_files`) and https://github.com/pre-commit/pre-commit/blob/main/pre_commit/commands/run.py (L75 `Classifier`, L255-L270 file selection)
- **Type**: reference implementation
- **Key takeaway**: `get_all_files` is `git ls-files -z --deduplicate` (tracked only; no `--others`). `Classifier.__init__` filters with `os.path.lexists`. Staged files come from `git diff --staged --name-only -z --diff-filter=ACMRTUXB` (excludes deletions). `--files` args are passed through untouched. `get_root()` raises `FatalError('git failed. Is it installed, and are you in a Git repository directory?')`. `no_git_env` strips every `GIT_*` variable except an allowlist, but only for clone, init and commit operations, not for the listing calls.
- **Relevance**: The environment house-lint's hook mode runs in. pre-commit hands house-lint explicit repo-relative paths, so hook mode never needs `ls-files`.

### pre-commit issues #1849 and #3773 (GIT_DIR / GIT_INDEX_FILE)
- **URL**: https://github.com/pre-commit/pre-commit/issues/1849, https://github.com/pre-commit/pre-commit/issues/3773
- **Type**: bug reports
- **Key takeaway**: git exports `GIT_DIR` and `GIT_INDEX_FILE` to hooks. A hook that runs git commands inherits them and can touch the real repo's index (`error: invalid object`). #3773 says inside a submodule git runs the hook with absolute `GIT_DIR` and `GIT_INDEX_FILE` pointing at `.git/modules/...`.
- **Relevance**: Evidence that git env leakage is a live bug class under hooks. These bugs are about git commands that write or switch repos. A read-only `ls-files` that inherits `GIT_DIR` just lists that repo, which is probably what you want in a hook, but the same variable would redirect it away from the scan root if the root differs. I have not verified this case.

### opengrep / osemgrep `Find_targets.ml` and `Git_wrapper.ml`
- **URL**: https://github.com/opengrep/opengrep/blob/main/src/targeting/Find_targets.ml and https://github.com/opengrep/opengrep/blob/main/libs/git_wrapper/Git_wrapper.ml
- **Type**: reference implementation
- **Key takeaway**: See Q1-Q8. This is the most careful implementation I found (about 100 lines of path-relativization code around a 20-line `ls_files` wrapper). Details: `-c safe.bareRepository=explicit` on every call (Git_wrapper L57); `ls-files -z` with a comment on why (L391-L411); a `possible_reasons` message that lists "the current working directory is not marked as safe" (L262-L283); `project_root_for_files_in_dir` = `git -C dir rev-parse --show-toplevel`, any non-zero exit becomes `None` (L495-L499, with `TODO: somehow avoid error message on stderr in case this is not a git repo`).
- **Relevance**: Semgrep's design is closest to house-lint's goal. Its comment at Find_targets.ml L566-L577 matters: "We used to call 'git ls-files' when conf.respect_gitignore was true ... However this was not handling .semgrepignore and especially the new ability ... to negate gitignore decisions in a .semgrepignore, so I think it's simpler to just walk the filesystem ... That's what ripgrep does too." The current code keeps git for the tracked/untracked candidate list and filters it through its own ignore layer.

### Mega-Linter `megalinter/MegaLinter.py`
- **URL**: https://github.com/oxsecurity/megalinter/blob/main/megalinter/MegaLinter.py (L48 `GIT_LS_FILES_TIMEOUT_SECONDS`, L962-L980, L1000-L1030, `list_git_ignored_files` L1329+); PR https://github.com/nvuillam/mega-linter/pull/605
- **Type**: reference implementation
- **Key takeaway**: Uses `git ls-files --exclude-standard --ignored --others` to find ignored files and subtracts them. It skips that call when the caller gave an explicit file list ("the caller already chose the files, and the repo-wide enumeration of gitignored files can be expensive"). It wraps the call in a 120-second kill timeout, with a comment about a hang "observed stalling on I/O for hours" on a Windows workspace bind-mounted into a WSL2-backed container. Any git exception becomes a warning and an empty ignore list, so files that git would ignore get linted.
- **Relevance**: A WSL2-specific performance hazard (relevant to your machines), and an example of fail-open on git errors.

### Super-linter `lib/functions/buildFileList.sh`
- **URL**: https://github.com/super-linter/super-linter/blob/main/lib/functions/buildFileList.sh (L104, L493)
- **Type**: reference implementation
- **Key takeaway**: Full scan lists `git ls-tree -r --name-only HEAD` (committed files only) inside `set -o pipefail`, and calls `fatal` if it fails. Optional gitignore filtering runs `git check-ignore "$FILE"` per file.
- **Relevance**: A fail-closed example, and the only tool here that runs `check-ignore` per file. It never lints untracked files.

### git-ls-files documentation
- **URL**: https://git-scm.com/docs/git-ls-files (source: https://github.com/git/git/blob/master/Documentation/git-ls-files.adoc)
- **Type**: documentation
- **Key takeaway**: `--exclude-standard` = ".git/info/exclude, .gitignore in each directory, and the user's global exclusion file". `--recurse-submodules` "only support for the --cached and --stage modes". `--deduplicate` suppresses duplicates from "multiple stages during a merge". Without `-z`, names are quoted per `core.quotePath`.
- **Relevance**: Authoritative backing for Q4, Q5 and the bug classes.

### Contrast only
- **yamllint** uses `pathspec.GitIgnoreSpec` (PR https://github.com/adrienverge/yamllint/pull/803, merged 2026-01-13, "to follow Git's implementation"). It is the pathspec approach house-lint is leaving; the PR shows a pathspec-version divergence from git was still being fixed in 2026.
- **Trivy** walks the filesystem and does not honour `.gitignore`. Feature request https://github.com/aquasecurity/trivy/issues/3670 was closed stale and locked.
