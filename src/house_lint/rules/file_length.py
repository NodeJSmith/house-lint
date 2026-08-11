"""Detect files whose splitlines() count exceeds the configured threshold."""

from house_lint.analysis import CandidateFinding, SourceKind, append_candidate
from house_lint.config import HSL102Options
from house_lint.source import SourceFile


def detect(
    source: SourceFile, options: HSL102Options, *, limit: int | None = None
) -> list[CandidateFinding]:
    """Return one file-owned HSL102 candidate when the file is too long."""
    if source.error is not None:
        return []
    line_count = len(source.text.splitlines())
    if line_count <= options.max_lines:
        return []
    findings: list[CandidateFinding] = []
    append_candidate(
        findings,
        CandidateFinding(
            "HSL102",
            source.relative_path,
            f"{line_count} lines (threshold: {options.max_lines})",
            None,
            None,
            None,
            None,
            SourceKind.FILE,
        ),
        source,
        limit,
    )
    return findings


__all__ = ["detect"]
