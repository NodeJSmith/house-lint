import os
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def write_sample(tmp_path: Path) -> Callable[[str], Path]:
    def _write(content: str) -> Path:
        target = tmp_path / "sample.py"
        target.write_text(textwrap.dedent(content))
        return target

    return _write


@pytest.fixture(autouse=True)
def isolated_git_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide every git setting and ignore file outside the test's own directory.

    Git also reads a default global ignore file under `$XDG_CONFIG_HOME` or `$HOME/.config`
    even when `GIT_CONFIG_GLOBAL` is null, so both are redirected too.
    """
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path.parent / f"{tmp_path.name}-no-home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
