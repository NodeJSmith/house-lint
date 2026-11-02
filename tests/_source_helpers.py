"""Build the objects a rule test needs from a file on disk."""

from pathlib import Path

from house_lint.python_source import ParseFailure, PythonSource, parse_python
from house_lint.results import LintError, ScanResult
from house_lint.source import LoadedFile, LoadFailure, load_file


def assert_reported_as_error(error: LintError, root: Path) -> None:
    """A failed file's error makes the run's result not clean."""
    result = ScanResult(root, None, (), 0, errors=(error,))
    assert not result.is_clean
    assert result.to_dict()["summary"]["error_count"] == 1


def load(path: Path, root: Path | None = None) -> LoadedFile:
    """Load a file that is expected to load."""
    loaded = load_file(path, path.parent if root is None else root, resolved_path=path.resolve())
    assert not isinstance(loaded, LoadFailure), loaded.error
    return loaded


def parse_file(path: Path, root: Path | None = None) -> PythonSource:
    """Load and parse a file that is expected to be valid Python."""
    source = parse_python(load(path, root))
    assert not isinstance(source, ParseFailure), source.error
    return source
