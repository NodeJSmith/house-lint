import re
from pathlib import Path

import yaml


def _is_passed_to_hook(path: str, *, exists: bool) -> bool:
    """Model pre-commit's existing-Python-file filter declared by this hook."""
    return exists and bool(re.search(r"\.py$", path))


def test_house_lint_hook_filters_python_files_and_batches_serially() -> None:
    metadata = yaml.safe_load(Path(".pre-commit-hooks.yaml").read_text())
    hook = next(item for item in metadata if item["id"] == "house-lint")

    assert hook["entry"] == "house-lint check"
    assert hook["types"] == ["python"]
    assert hook["require_serial"] is True
    assert re.search(hook["files"], "src/package.py")
    assert not re.search(hook["files"], "README.md")
    assert not re.search(hook["files"], "src/package.pyi")
    assert _is_passed_to_hook("src/package.py", exists=True)
    assert not _is_passed_to_hook("README.md", exists=True)
    assert not _is_passed_to_hook("src/deleted.py", exists=False)
