"""Detect imports inside function bodies."""

import ast

from house_lint.analysis import CandidateFinding, candidate_for_statement
from house_lint.source import SourceFile


def detect(source: SourceFile) -> list[CandidateFinding]:
    """Return HSL002 candidates for imports reached at function depth."""
    if source.error is not None or source.tree is None:
        return []
    visitor = _LazyImportVisitor()
    visitor.visit(source.tree)
    return [_candidate(source, node) for node in visitor.imports]


class _LazyImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []
        self.function_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_Import(self, node: ast.Import) -> None:
        if self.function_depth:
            self.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.function_depth:
            self.imports.append(node)


def _candidate(source: SourceFile, node: ast.Import | ast.ImportFrom) -> CandidateFinding:
    return candidate_for_statement(source, "HSL002", "import inside function body", node)
