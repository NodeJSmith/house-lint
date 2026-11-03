from pathlib import Path

import pytest
from _source_helpers import PYTHON_ONLY

from house_lint.discovery import discover_files
from house_lint.language import Language, language_for


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.py", Language.PYTHON),
        ("pkg/a.b.py", Language.PYTHON),
        ("a.ts", Language.TYPESCRIPT),
        ("a.tsx", Language.TYPESCRIPT),
        ("a.d.ts", Language.TYPESCRIPT),
        ("a.js", None),
        ("a.pyi", None),
        ("a.PY", None),
        ("a.txt", None),
        ("py", None),
        (".py", None),
    ],
)
def test_language_follows_the_suffix(name: str, expected: Language | None) -> None:
    assert language_for(Path(name)) is expected


def test_language_reads_the_reported_path_not_the_symlink_target(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("value = 1\n")
    link = tmp_path / "link.py"
    link.symlink_to(target)

    assert language_for(link) is Language.PYTHON
    assert [file.path for file in discover_files(tmp_path, PYTHON_ONLY, use_git=False).files] == []
    assert [
        file.path for file in discover_files(tmp_path, PYTHON_ONLY, explicit=(link,)).files
    ] == [link]
