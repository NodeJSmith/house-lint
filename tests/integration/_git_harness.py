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


def git_env() -> dict[str, str]:
    """Neutralise every ignore source outside the repository under test."""
    return os.environ | {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "HOME": os.devnull,
    }


def init_repository(root: Path) -> None:
    """Create a repository at `root` with every out-of-tree ignore source disabled."""
    for command in (["git", "init", "-q", "."], ["git", "config", "core.excludesFile", ""]):
        subprocess.run(
            command,
            cwd=root,
            check=True,
            capture_output=True,
            env=git_env(),
            timeout=GIT_TIMEOUT_SECONDS,
        )


def git_ignored(root: Path, relatives: tuple[str, ...]) -> set[str]:
    """Return which of `relatives` git itself would ignore."""
    completed = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=root,
        input="\n".join(relatives),
        capture_output=True,
        text=True,
        env=git_env(),
        check=False,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    # `check-ignore` exits 1 when nothing matches, which is not a failure for us.
    if completed.returncode not in (0, 1):
        pytest.fail(f"git check-ignore failed: {completed.stderr}")
    return {line.strip() for line in completed.stdout.splitlines() if line.strip()}
