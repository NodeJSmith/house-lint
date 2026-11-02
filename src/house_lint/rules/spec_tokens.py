"""Detect configured planning tokens in prose and filename segments."""

import ast
import re
from collections.abc import Iterator, Mapping
from functools import lru_cache
from types import MappingProxyType
from typing import Annotated, Any

import msgspec
from msgspec import Meta

from house_lint.analysis import (
    CandidateFinding,
    SourceKind,
    append_candidate,
    candidate_for_line,
    comment_owner_for_line,
    docstring_owner_for_line,
)
from house_lint.rules.options import RuleOptions
from house_lint.source import SourceFile

DEFAULT_MAX_FINDINGS_PER_FILE = 200
MAX_FINDINGS_PER_FILE_LIMIT = 10_000
MAX_TOKEN_FAMILIES = 32
MAX_PREFIXES_PER_FAMILY = 32
MAX_PREFIX_LENGTH = 12
MAX_DIGITS_BOUND = 12
_BUILTIN_MAX_DIGITS = 4
_PREFIX_PATTERN = r"^[A-Z][A-Z0-9_]*\Z"

VALID_SCOPES: tuple[str, ...] = ("comments", "docstrings", "filenames")

# Single source of truth for separator/suffix vocabularies: the keys validate configured
# values, and the values build detection regexes.
SEPARATOR_REGEX: Mapping[str, str] = MappingProxyType(
    {
        "none": "",
        "hash": "#",
        "hash-optional": "#?",
        "dash": "-",
        "dash-optional": "-?",
    }
)
SUFFIX_REGEX: Mapping[str, str] = MappingProxyType(
    {
        "none": "",
        "optional-lower-alpha": "[a-z]?",
    }
)

_Digits = Annotated[int, Meta(ge=1, le=MAX_DIGITS_BOUND)]
_Prefix = Annotated[str, Meta(max_length=MAX_PREFIX_LENGTH, pattern=_PREFIX_PATTERN)]


class TokenFamily(RuleOptions, frozen=True, kw_only=True):
    prefixes: Annotated[tuple[_Prefix, ...], Meta(min_length=1, max_length=MAX_PREFIXES_PER_FAMILY)]
    scopes: Annotated[tuple[str, ...], Meta(min_length=1)]
    separator: str = "none"
    min_digits: _Digits = 1
    max_digits: _Digits | None = None
    suffix: str = "none"
    case_sensitive: bool = True
    not_followed_by_time: bool = False

    def __post_init__(self) -> None:
        if len(set(self.prefixes)) != len(self.prefixes):
            raise ValueError("prefixes must be unique")
        if len(set(self.scopes)) != len(self.scopes) or not set(self.scopes) <= set(VALID_SCOPES):
            raise ValueError(f"scopes must be unique values from {', '.join(VALID_SCOPES)}")
        if self.separator not in SEPARATOR_REGEX:
            raise ValueError(f"separator must be one of {', '.join(SEPARATOR_REGEX)}")
        if self.suffix not in SUFFIX_REGEX:
            raise ValueError(f"suffix must be one of {', '.join(SUFFIX_REGEX)}")
        if self.max_digits is not None and self.max_digits < self.min_digits:
            raise ValueError("max_digits must not be less than min_digits")


BUILTIN_SPEC = TokenFamily(
    prefixes=("AC", "FR", "NFR", "WP"),
    scopes=VALID_SCOPES,
    separator="hash-optional",
    max_digits=_BUILTIN_MAX_DIGITS,
    suffix="optional-lower-alpha",
)

BUILTIN_TASK = TokenFamily(
    prefixes=("T",),
    scopes=VALID_SCOPES,
    separator="hash-optional",
    max_digits=_BUILTIN_MAX_DIGITS,
    suffix="optional-lower-alpha",
    not_followed_by_time=True,
)

BUILTIN_KNOWN_ISSUES = TokenFamily(
    prefixes=("KI",),
    scopes=VALID_SCOPES,
    separator="dash",
    max_digits=_BUILTIN_MAX_DIGITS,
)

BUILTIN_TOKEN_FAMILIES: tuple[TokenFamily, ...] = (
    BUILTIN_SPEC,
    BUILTIN_TASK,
    BUILTIN_KNOWN_ISSUES,
)


class HSL101Options(RuleOptions, frozen=True):
    """`tokens` is every family the rule matches. A config table's `tokens` lists only additions to
    the built-ins, and `parse_options` does the merge, so a directly built value means exactly the
    families it names."""

    tokens: Annotated[tuple[TokenFamily, ...], Meta(min_length=1)] = BUILTIN_TOKEN_FAMILIES
    max_findings_per_file: Annotated[int, Meta(ge=1, le=MAX_FINDINGS_PER_FILE_LIMIT)] = (
        DEFAULT_MAX_FINDINGS_PER_FILE
    )


def parse_options(raw: Any) -> HSL101Options:
    """Convert HSL101's config table, adding any configured families to the built-ins."""
    parsed = msgspec.convert(raw, HSL101Options)
    if "tokens" not in raw:
        return parsed
    tokens = BUILTIN_TOKEN_FAMILIES + parsed.tokens
    if len(tokens) > MAX_TOKEN_FAMILIES:
        raise msgspec.ValidationError(
            f"tokens combined with built-in families must not exceed {MAX_TOKEN_FAMILIES}"
        )
    return msgspec.structs.replace(parsed, tokens=tokens)


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


def _owner_for_line(
    source: SourceFile, line: int, scope: str, text: str
) -> ast.stmt | ast.excepthandler | None:
    if scope == "comment":
        return comment_owner_for_line(source, line, text)
    return docstring_owner_for_line(source, line)


def _filename_candidates(
    source: SourceFile, family: TokenFamily, seen: set[tuple[str, int | None, str]]
) -> Iterator[CandidateFinding]:
    pattern = _filename_pattern(family)
    for match in pattern.finditer(source.path.name):
        segment = match.group(0)
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
    token = _token_expression(family)
    return re.compile(f"(?<![A-Za-z0-9_]){token}(?![A-Za-z0-9_])")


@lru_cache
def _filename_pattern(family: TokenFamily) -> re.Pattern[str]:
    # Filenames commonly use "_" (alongside "." and "-") as a segment
    # separator, so unlike content matching, "_" must count as a boundary
    # rather than a word character. Forbidding only adjacent letters/digits
    # still prevents matching inside a longer alphanumeric run (e.g. the "AC1"
    # in "xAC1y"), while allowing "notes-KI-001-fix.py" and "test-T05_AC1.py"
    # to match without the separator being stripped away first.
    token = _token_expression(family)
    return re.compile(f"(?<![A-Za-z0-9]){token}(?![A-Za-z0-9])")


def _token_expression(family: TokenFamily) -> str:
    prefixes = "|".join(
        re.escape(prefix) for prefix in sorted(family.prefixes, key=len, reverse=True)
    )
    if not family.case_sensitive:
        prefixes = f"(?i:{prefixes})"
    separator_part = SEPARATOR_REGEX[family.separator]
    maximum = "" if family.max_digits is None else str(family.max_digits)
    digits = (
        f"[0-9]{{{family.min_digits},{maximum}}}" if maximum else f"[0-9]{{{family.min_digits},}}"
    )
    suffix = SUFFIX_REGEX[family.suffix]
    time_guard = "(?!:[0-9])" if family.not_followed_by_time else ""
    return f"(?:{prefixes}){separator_part}{digits}{suffix}{time_guard}"


__all__ = ["detect"]
