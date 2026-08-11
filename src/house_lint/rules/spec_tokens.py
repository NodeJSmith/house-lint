"""Detect configured planning tokens in prose and filename segments."""

import ast
import re
from collections.abc import Iterator
from functools import lru_cache

from house_lint.analysis import (
    CandidateFinding,
    SourceKind,
    append_candidate,
    candidate_for_line,
    comment_owner_for_line,
    docstring_owner_for_line,
)
from house_lint.config import HSL101Options, TokenFamily
from house_lint.source import SourceFile


def detect(
    source: SourceFile, options: HSL101Options, *, limit: int | None = None
) -> list[CandidateFinding]:
    """Return HSL101 candidates for the configured token families."""
    if source.error is not None:
        return []

    findings: list[CandidateFinding] = []
    seen: set[tuple[str, int | None, str]] = set()

    def add(finding: CandidateFinding) -> bool:
        if len(findings) >= options.max_findings_per_file:
            return False
        append_candidate(findings, finding, source, limit)
        return True

    for family in options.tokens:
        pattern = _content_pattern(family)
        if "comments" in family.scopes:
            for line, comment in source.comments.items():
                for finding in _content_candidates(source, pattern, comment, line, "comment", seen):
                    if not add(finding):
                        return _ordered(findings)
        if "docstrings" in family.scopes:
            for start, end in source.docstring_spans:
                for line in range(start, end + 1):
                    for finding in _content_candidates(
                        source, pattern, source.lines[line - 1], line, "docstring", seen
                    ):
                        if not add(finding):
                            return _ordered(findings)
        if "filenames" in family.scopes:
            for finding in _filename_candidates(source, family, seen):
                if not add(finding):
                    return _ordered(findings)

    return _ordered(findings)


def _ordered(findings: list[CandidateFinding]) -> list[CandidateFinding]:
    ordered = sorted(
        enumerate(findings),
        key=lambda item: (
            item[1].line is None,
            item[1].line or 0,
            item[1].message if item[1].line is not None else "",
            item[0],
        ),
    )
    return [finding for _, finding in ordered]


def _content_candidates(
    source: SourceFile,
    pattern: re.Pattern[str],
    text: str,
    line: int,
    scope: str,
    seen: set[tuple[str, int | None, str]],
) -> Iterator[CandidateFinding]:
    for match in pattern.finditer(text):
        token = match.group(0)
        key = (scope, line, token)
        if key in seen:
            continue
        seen.add(key)
        yield candidate_for_line(
            source,
            "HSL101",
            f"spec token {token} in {scope}",
            line,
            _owner_for_line(source, line, scope, text),
        )


def _owner_for_line(source: SourceFile, line: int, scope: str, text: str) -> ast.stmt | None:
    if scope == "comment":
        return comment_owner_for_line(source, line, text)
    return docstring_owner_for_line(source, line)


def _filename_candidates(
    source: SourceFile, family: TokenFamily, seen: set[tuple[str, int | None, str]]
) -> Iterator[CandidateFinding]:
    pattern = _filename_pattern(family)
    for segment in re.split(r"[._-]", source.path.name):
        if not pattern.fullmatch(segment):
            continue
        key = ("filename", None, segment)
        if key in seen:
            continue
        seen.add(key)
        yield CandidateFinding(
            "HSL101",
            source.relative_path,
            f"spec token {segment} in filename",
            None,
            None,
            None,
            None,
            SourceKind.FILENAME,
        )


@lru_cache
def _content_pattern(family: TokenFamily) -> re.Pattern[str]:
    return re.compile(_token_expression(family, boundaries=True))


@lru_cache
def _filename_pattern(family: TokenFamily) -> re.Pattern[str]:
    return re.compile(_token_expression(family, boundaries=False))


def _token_expression(family: TokenFamily, *, boundaries: bool) -> str:
    prefixes = "|".join(re.escape(prefix) for prefix in sorted(family.prefixes, key=len, reverse=True))
    if not family.case_sensitive:
        prefixes = f"(?i:{prefixes})"
    hash_part = {"forbidden": "", "optional": "#?", "required": "#"}[family.hash]
    maximum = "" if family.max_digits is None else str(family.max_digits)
    digits = f"[0-9]{{{family.min_digits},{maximum}}}" if maximum else f"[0-9]{{{family.min_digits},}}"
    suffix = "[a-z]?" if family.suffix == "optional-lower-alpha" else ""
    time_guard = "(?!:[0-9])" if family.not_followed_by_time else ""
    token = f"(?:{prefixes}){hash_part}{digits}{suffix}{time_guard}"
    return f"(?<![A-Za-z0-9_]){token}(?![A-Za-z0-9_])" if boundaries else token


__all__ = ["detect"]
