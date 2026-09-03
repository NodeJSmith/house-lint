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
    flag_names, module_names = _type_checking_names(tree)
    findings: list[CandidateFinding] = []
    for index, node in enumerate(tree.body):
        if not isinstance(node, ast.If) or not _is_type_checking_guard(
            node.test, flag_names, module_names
        ):
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


def _type_checking_names(tree: ast.Module) -> tuple[frozenset[str], frozenset[str]]:
    """Top-level names bound to `typing.TYPE_CHECKING`, and names bound to the `typing` module.

    `import typing as t` and `from typing import TYPE_CHECKING as TC` are the same guard in
    different spellings; recognizing only the canonical names made every aliased guard invisible
    to this rule.
    """
    flag_names = {"TYPE_CHECKING"}
    module_names = {"typing"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "typing" and alias.asname is not None:
                    module_names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == "typing":
            for alias in node.names:
                if alias.name == "TYPE_CHECKING" and alias.asname is not None:
                    flag_names.add(alias.asname)
    return frozenset(flag_names), frozenset(module_names)


def _is_type_checking_guard(
    test: ast.expr, flag_names: frozenset[str], module_names: frozenset[str]
) -> bool:
    if isinstance(test, ast.Name):
        return test.id in flag_names
    return (
        isinstance(test, ast.Attribute)
        and test.attr == "TYPE_CHECKING"
        and isinstance(test.value, ast.Name)
        and test.value.id in module_names
    )


__all__ = ["detect"]
