"""Detect files whose splitlines() count exceeds the configured threshold."""

from house_lint.analysis import CandidateFinding, SourceKind
from house_lint.config import HSL102Options
from house_lint.source import SourceFile


def detect(source: SourceFile, options: HSL102Options) -> list[CandidateFinding]:
    """Return one file-owned HSL102 candidate when the file is too long."""
    if source.error is not None:
        return []
    line_count = len(source.text.splitlines())
    if line_count <= options.max_lines:
        return []
    return [
        CandidateFinding(
            "HSL102",
            source.relative_path,
            f"{line_count} lines (threshold: {options.max_lines})",
            None,
            None,
            None,
            None,
            SourceKind.FILE,
        )
    ]


__all__ = ["detect"]
