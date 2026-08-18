"""Detect module constants placed after the first class or function."""

import ast
import re

from house_lint.analysis import CandidateFinding, append_candidate, candidate_for_statement
from house_lint.source import SourceFile

CONSTANT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
DUNDER_NAME = re.compile(r"^__.+__$")


def detect(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    """Return HSL004 candidates using the preserved derived-binding heuristic."""
    if source.error is not None or source.tree is None:
        return []
    bound_names, first_definition = _module_bindings(source.tree)
    if first_definition is None:
        return []

    findings: list[CandidateFinding] = []
    for index, node in enumerate(source.tree.body):
        if index <= first_definition or not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names = _target_names(node)
        if not names or not all(_is_constant(name) for name in names):
            continue
        if _references_earlier_binding(node, bound_names, index):
            continue
        append_candidate(
            findings,
            candidate_for_statement(
                source, "HSL004", "constant defined after the first class or function", node
            ),
            source,
            limit,
        )
    return findings


def _module_bindings(tree: ast.Module) -> tuple[dict[str, int], int | None]:
    bound_names: dict[str, int] = {}
    first_definition: int | None = None
    for index, node in enumerate(tree.body):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            bound_names[node.name] = index
            if first_definition is None:
                first_definition = index
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name in _target_names(node):
                bound_names.setdefault(name, index)
    return bound_names, first_definition


def _target_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    if isinstance(node, ast.AnnAssign):
        return (
            [node.target.id] if node.value is not None and isinstance(node.target, ast.Name) else []
        )
    names: list[str] = []
    for target in node.targets:
        collected = _names_in_target(target)
        if collected is None:
            return []
        names.extend(collected)
    return names


def _names_in_target(target: ast.expr) -> list[str] | None:
    if isinstance(target, ast.Name):
        return [target.id]
    if not isinstance(target, (ast.Tuple, ast.List)):
        return None
    names: list[str] = []
    for element in target.elts:
        collected = _names_in_target(element)
        if collected is None:
            return None
        names.extend(collected)
    return names


def _is_constant(name: str) -> bool:
    return (
        len(name) >= 2 and not DUNDER_NAME.fullmatch(name) and bool(CONSTANT_NAME.fullmatch(name))
    )


def _references_earlier_binding(
    node: ast.Assign | ast.AnnAssign, bound_names: dict[str, int], index: int
) -> bool:
    expressions: list[ast.expr] = [node.value] if node.value is not None else []
    if isinstance(node, ast.AnnAssign):
        expressions.append(node.annotation)
    return any(
        isinstance(child, ast.Name) and bound_names.get(child.id, index) < index
        for expression in expressions
        for child in ast.walk(expression)
    )
