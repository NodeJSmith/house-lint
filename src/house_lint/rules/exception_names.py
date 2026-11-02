"""Detect exception bindings that do not follow the configured name policy."""

import ast
import re
from typing import Annotated

from msgspec import Meta

from house_lint.analysis import CandidateFinding, append_candidate, candidate_for_line
from house_lint.python_source import PythonSource
from house_lint.rules.options import RuleOptions

_ALLOWED_ENTRY = re.compile(r"\*?[A-Za-z_][A-Za-z0-9_]*")


class HSL103Options(RuleOptions, frozen=True):
    allowed: Annotated[tuple[str, ...], Meta(min_length=1)] = ("exc", "*_exc")

    def __post_init__(self) -> None:
        if len(set(self.allowed)) != len(self.allowed):
            raise ValueError("allowed must contain no duplicates")
        for entry in self.allowed:
            if not _ALLOWED_ENTRY.fullmatch(entry):
                raise ValueError(
                    f"allowed entry {entry!r} must be an identifier, or a leading * and an "
                    "identifier suffix"
                )


def detect(
    source: PythonSource, options: HSL103Options, *, limit: int | None = None
) -> list[CandidateFinding]:
    """Return HSL103 candidates for disallowed bound exception names."""
    tree = source.tree
    findings: list[CandidateFinding] = []
    for try_statement in ast.walk(tree):
        if not isinstance(try_statement, (ast.Try, ast.TryStar)):
            continue
        for handler in try_statement.handlers:
            if handler.name is None or _is_allowed(handler.name, options.allowed):
                continue
            append_candidate(
                findings,
                candidate_for_line(
                    source,
                    "HSL103",
                    f"exception handler bound to '{handler.name}'",
                    handler.lineno,
                    # The handler, not the enclosing try: siblings must not share an owner key,
                    # or one trailing ignore[HSL103] silences every handler of the statement.
                    handler,
                ),
                source,
                limit,
            )
    return findings


def _is_allowed(name: str, allowed: tuple[str, ...]) -> bool:
    return any(
        name == pattern if not pattern.startswith("*") else name.endswith(pattern[1:])
        for pattern in allowed
    )


__all__ = ["detect"]
