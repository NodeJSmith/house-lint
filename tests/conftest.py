import os
import textwrap
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from _source_helpers import parse_file

from house_lint import config
from house_lint.python_source import PythonSource
from house_lint.rule_catalog import FileRule


@pytest.fixture
def write_sample(tmp_path: Path) -> Callable[[str], Path]:
    def _write(content: str) -> Path:
        target = tmp_path / "sample.py"
        target.write_text(textwrap.dedent(content))
        return target

    return _write


@pytest.fixture
def parse_sample(write_sample: Callable[[str], Path]) -> Callable[[str], PythonSource]:
    """Write dedented content to `sample.py` and return it parsed, for handing to a detector.

    A test that depends on the file's name writes it itself and calls `parse_file`.
    """

    def _parse(content: str) -> PythonSource:
        return parse_file(write_sample(content))

    return _parse


@pytest.fixture
def fail_detectors_for(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Make every file rule raise when it reaches one root-relative path."""

    def wrap(rule: FileRule, path: str) -> FileRule:
        def detect(source, options, **kwargs):
            if source.relative_path == path:
                raise RuntimeError("simulated failure")
            return rule.detect(source, options, **kwargs)

        return replace(rule, detect=detect)

    def install(path: str) -> None:
        rules = {rule_id: wrap(rule, path) for rule_id, rule in config.FILE_RULES.items()}
        monkeypatch.setattr(config, "FILE_RULES", rules)

    return install


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
