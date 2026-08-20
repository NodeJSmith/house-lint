"""Shared harness for the two differential tests that compare discovery against real git.

`test_gitignore_parity.py` (curated table) and `test_gitignore_fuzz.py` (randomized) both need a
throwaway repository and a way to ask `git check-ignore` what it would skip. Those two suites
exist to catch house-lint drifting from git; keeping one copy of the harness stops the harness
itself from drifting between them — the same failure mode one level up.

Not a `conftest.py`: these are plain helpers called from module-level functions, not fixtures
injected into test signatures. `tests/integration/` has no `__init__.py`, so pytest's default
prepend import mode puts it on `sys.path` and both modules can import this one by name.
"""

import os
import subprocess
from pathlib import Path

import pytest

# A git call that hangs (a credential helper waiting on stdin, a pager, a corrupt config) would
# otherwise be bounded only by CI's job-level timeout, which kills the whole matrix leg without
# saying which test stalled.
GIT_TIMEOUT_SECONDS = 30


# Variables that override `cwd` when locating the repository. An inherited value would point
# `git init` and `git check-ignore` at a different repository than the one built for the
# scenario, so the comparison would measure the wrong tree and still exit 0.
_REPOSITORY_POINTING_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def git_env() -> dict[str, str]:
    """Neutralise every ignore source outside the repository under test.

    Dropping the repository-pointing variables matters as much as the config ones: passing
    `cwd=root` is not enough on its own, since any of the three override it. These suites exist
    to catch house-lint drifting from git, so a harness that can silently compare against
    somebody else's repository defeats the only thing they are for.
    """
    inherited = {
        key: value for key, value in os.environ.items() if key not in _REPOSITORY_POINTING_VARIABLES
    }
    return inherited | {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "HOME": os.devnull,
    }


def init_repository(root: Path) -> None:
    """Create a repository at `root` with every out-of-tree ignore source disabled."""
    for command in (["git", "init", "-q", "."], ["git", "config", "core.excludesFile", ""]):
        # Not `check=True`: `CalledProcessError`'s message carries only the command and exit
        # status, and pytest never prints the captured stderr hanging off the exception. A CI
        # failure here would read "returned non-zero exit status 128" and say nothing about why.
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            env=git_env(),
            timeout=GIT_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            pytest.fail(f"{' '.join(command)} failed: {completed.stderr}")


def git_ignored(root: Path, relatives: tuple[str, ...]) -> set[str]:
    """Return which of `relatives` git itself would ignore.

    NUL-separated (`-z`) in both directions. Without it git applies its C-style quoting to any
    path it considers unusual — a filename containing a backslash comes back as
    `"src/dir\\\\/b.py"`, quotes and doubled escapes included — and the comparison then fails on
    the encoding rather than on the ignore decision. `-z` turns quoting off entirely, so
    newline-free paths (which every scenario uses) round-trip byte for byte.
    """
    completed = subprocess.run(
        ["git", "check-ignore", "-z", "--stdin"],
        cwd=root,
        input="\0".join(relatives),
        capture_output=True,
        text=True,
        env=git_env(),
        check=False,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    # `check-ignore` exits 1 when nothing matches, which is not a failure for us.
    if completed.returncode not in (0, 1):
        pytest.fail(f"git check-ignore failed: {completed.stderr}")
    return {entry for entry in completed.stdout.split("\0") if entry}
