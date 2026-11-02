"""The languages house-lint knows, and how a file's language is decided."""

from enum import Enum
from pathlib import Path


class Language(Enum):
    PYTHON = "python"


_LANGUAGE_BY_SUFFIX = {".py": Language.PYTHON}


def language_for(path: Path) -> Language | None:
    """Return the language of the file at `path`, or None if house-lint doesn't handle it.

    Decided from the path as reported, not its resolved target, so a symlink named `a.py` is Python.

    A cached result is valid for a file's name and content, and the name is what fixes the language
    here. Any input beyond the path, such as a config extension map, must join
    `cache.hash_effective_config`, or a cached result could outlive the language it was scanned as.
    """
    return _LANGUAGE_BY_SUFFIX.get(path.suffix)


__all__ = ["Language", "language_for"]
