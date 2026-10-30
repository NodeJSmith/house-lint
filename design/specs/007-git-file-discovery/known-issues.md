# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: A hand-built `.git/index` with an absolute path crashes the run

Status: open
Recorded: 2026-10-30 (64)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/discovery.py
- src/house_lint/pipeline.py

Issue:
git prints an absolute index entry as is, `self.root / relative` then discards the root, and `_is_behind_symlink` sees no change. `discover_files` returns a path outside the root, and the run exits 4 at the `relative_to(root)` call in `pipeline.py`, before the file is read.

Why deferred:
It needs a crafted index, which `git add` can't produce. The failure is loud and `--no-git` gets past it.

Recommended follow-up:
If house-lint starts scanning untrusted archives, or the code around that `relative_to` call moves, reject absolute and `..` entries in `_consider_discovered`. Today that unrelated call is the only thing preventing a read outside the root.

Acceptance criteria:
- A test that fakes `git_listing` with an absolute entry and a `..` entry selects neither, and the run scans the rest of the tree.

## KI-002: On native Windows, `_is_behind_symlink` can silently drop a directory's files

Status: open
Recorded: 2026-10-30 (64)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/discovery.py

Issue:
The check compares a path with its `realpath`. On native Windows, `realpath` normalizes case and 8.3 names, so an index entry whose case differs from the disk reads as "behind a symlink", and that directory's files are dropped with no error.

Why deferred:
It can't happen on Linux or WSL2. CI is ubuntu-only and the test suite doesn't run on native Windows, so a fix couldn't be verified there.

Recommended follow-up:
If native Windows use is planned, walk the path's components with `os.path.islink`, cached per directory.

Acceptance criteria:
- On native Windows, a tracked file whose index case differs from the disk is still selected.

## KI-003: `check <dir>` can report an empty scan where naming the file lints it

Status: open
Recorded: 2026-10-30 (64)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/discovery.py

Issue:
Two shapes still disagree: a gitignored directory holding a tracked placeholder such as `.gitkeep`, and a directory whose contents are ignored by a file-level pattern such as `build/*.py`. `check build/` reports an empty scan with exit 0, and `check build/gen.py` lints the file. The shapes `build/`, `build/*`, and a nested `*` agree.

Why deferred:
It only happens when someone names such a directory by hand, and it shows as an empty scan rather than a wrong result. No single git probe closes both shapes, and `ls-files --others --ignored` costs up to two more processes and can enumerate a large ignored tree.

Recommended follow-up:
If this comes up in practice, decide between dropping the `check-ignore` probe so that directories always follow git's listing, and adding a second listing for an empty result.

Acceptance criteria:
- `check <dir>` and `check <dir>/<file>` agree for both shapes, or the docs state the simpler rule that replaces the probe.
