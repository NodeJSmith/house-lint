"""Detect configured planning tokens in prose and filename segments."""

import ast
import re
from functools import lru_cache

from house_lint.analysis import (
    CandidateFinding,
    SourceKind,
    candidate_for_line,
    docstring_owner_for_line,
    statement_owner_for_line,
)
from house_lint.config import HSL101Options, TokenFamily
from house_lint.source import SourceFile


def detect(source: SourceFile, options: HSL101Options) -> list[CandidateFinding]:
    """Return HSL101 candidates for the configured token families."""
    if source.error is not None:
        return []

    findings: list[CandidateFinding] = []
    seen: set[tuple[str, int | None, str]] = set()
    for family in options.tokens:
        pattern = _content_pattern(family)
        if "comments" in family.scopes:
            for line, comment in source.comments.items():
                findings.extend(
                    _content_candidates(source, pattern, comment, line, "comment", seen)
                )
        if "docstrings" in family.scopes:
            for start, end in source.docstring_spans:
                for line in range(start, end + 1):
                    findings.extend(
                        _content_candidates(source, pattern, source.lines[line - 1], line, "docstring", seen)
                    )
        if "filenames" in family.scopes:
            findings.extend(_filename_candidates(source, family, seen))

    ordered = sorted(
        enumerate(findings),
        key=lambda item: (
            item[1].line is None,
            item[1].line or 0,
            item[1].message if item[1].line is not None else "",
            item[0],
        ),
    )
    return [finding for _, finding in ordered[: options.max_findings_per_file]]


def _content_candidates(
    source: SourceFile,
    pattern: re.Pattern[str],
    text: str,
    line: int,
    scope: str,
    seen: set[tuple[str, int | None, str]],
) -> list[CandidateFinding]:
    findings: list[CandidateFinding] = []
    for match in pattern.finditer(text):
        token = match.group(0)
        key = (scope, line, token)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            candidate_for_line(
                source,
                "HSL101",
                f"spec token {token} in {scope}",
                line,
                _owner_for_line(source, line, scope),
            )
        )
    return findings


def _owner_for_line(source: SourceFile, line: int, scope: str) -> ast.stmt | None:
    if scope == "comment":
        return statement_owner_for_line(source, line)
    return docstring_owner_for_line(source, line)


def _filename_candidates(
    source: SourceFile, family: TokenFamily, seen: set[tuple[str, int | None, str]]
) -> list[CandidateFinding]:
    pattern = _filename_pattern(family)
    findings: list[CandidateFinding] = []
    for segment in re.split(r"[._-]", source.path.name):
        if not pattern.fullmatch(segment):
            continue
        key = ("filename", None, segment)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            CandidateFinding(
                "HSL101",
                source.relative_path,
                f"spec token {segment} in filename",
                None,
                None,
                None,
                None,
                SourceKind.FILENAME,
            )
        )
    return findings


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
