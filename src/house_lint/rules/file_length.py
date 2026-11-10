"""Detect files whose splitlines() count exceeds the configured threshold."""

import re
from collections.abc import Sequence
from typing import Annotated

from msgspec import Meta

from house_lint.analysis import CandidateFinding, SourceKind, append_candidate
from house_lint.matching import RenameMap, SpanText
from house_lint.python_source import PythonSource
from house_lint.results import Finding
from house_lint.rules.options import RuleOptions

DEFAULT_MAX_LINES = 800
MAX_LINES_LIMIT = 10_000_000
# `format_message`'s output, parsed back by `line_count_of` for `--base`'s ratchet.
MESSAGE_PATTERN = re.compile(r"(\d+) lines \(threshold: \d+\)")


class HSL102Options(RuleOptions, frozen=True):
    max_lines: Annotated[int, Meta(ge=1, le=MAX_LINES_LIMIT)] = DEFAULT_MAX_LINES


def detect(
    source: PythonSource, options: HSL102Options, *, limit: int | None = None
) -> list[CandidateFinding]:
    """Return one file-owned HSL102 candidate when the file is too long."""
    line_count = len(source.text.splitlines())
    if line_count <= options.max_lines:
        return []
    findings: list[CandidateFinding] = []
    append_candidate(
        findings,
        CandidateFinding(
            "HSL102",
            source.relative_path,
            format_message(line_count, options.max_lines),
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


def format_message(line_count: int, max_lines: int) -> str:
    return f"{line_count} lines (threshold: {max_lines})"


def line_count_of(finding: Finding) -> int:
    """The line count in an HSL102 finding's message, as `format_message` wrote it."""
    matched = MESSAGE_PATTERN.fullmatch(finding.message)
    if matched is None:
        raise ValueError(f"not an HSL102 message: {finding.message!r}")
    return int(matched.group(1))


def match_new(
    head: Sequence[Finding],
    base: Sequence[Finding],
    *,
    head_text: SpanText,
    base_text: SpanText,
    renames: RenameMap,
) -> tuple[Finding, ...]:
    """Report a too-long head file only if it has more lines than it had at base.

    Both runs use the head's config, so a file with no base finding was at or under the threshold
    there, or was unreadable, excluded, or suppressed: its head finding is new. The base file is
    found through `renames`.
    """
    base_counts = {finding.path: line_count_of(finding) for finding in base}
    new: list[Finding] = []
    for finding in head:
        base_count = base_counts.get(renames.get(finding.path, finding.path))
        if base_count is None or line_count_of(finding) > base_count:
            new.append(finding)
    return tuple(new)


__all__ = ["detect", "format_message", "line_count_of", "match_new"]
