"""Detect top-level TYPE_CHECKING blocks followed by imports."""

import ast

from house_lint.analysis import (
    CandidateFinding,
    append_candidate,
    candidate_for_statement,
    parsed_tree,
)
from house_lint.source import SourceFile


def detect(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    """Return HSL003 candidates for misplaced top-level type-checking guards."""
    tree = parsed_tree(source)
    if tree is None:
        return []
    findings: list[CandidateFinding] = []
    for index, node in enumerate(tree.body):
        if not isinstance(node, ast.If) or not _is_type_checking_guard(node.test):
            continue
        if any(isinstance(later, (ast.Import, ast.ImportFrom)) for later in tree.body[index + 1 :]):
            append_candidate(
                findings,
                candidate_for_statement(
                    source, "HSL003", "if TYPE_CHECKING block followed by imports", node
                ),
                source,
                limit,
            )
    return findings


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id == "typing"
    )


__all__ = ["detect"]
