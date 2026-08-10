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
