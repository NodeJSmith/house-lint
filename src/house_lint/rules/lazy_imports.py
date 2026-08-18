"""Detect imports inside function bodies."""

import ast

from house_lint.analysis import CandidateFinding, append_candidate, candidate_for_statement
from house_lint.source import SourceFile


def detect(source: SourceFile, *, limit: int | None = None) -> list[CandidateFinding]:
    """Return HSL002 candidates for imports reached at function depth."""
    if source.error is not None or source.tree is None:
        return []
    visitor = _LazyImportVisitor(source, limit)
    visitor.visit(source.tree)
    return visitor.findings


class _LazyImportVisitor(ast.NodeVisitor):
    def __init__(self, source: SourceFile, limit: int | None) -> None:
        self.findings: list[CandidateFinding] = []
        self.function_depth = 0
        self.source = source
        self.limit = limit

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
            self._append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.function_depth:
            self._append(node)

    def _append(self, node: ast.Import | ast.ImportFrom) -> None:
        append_candidate(self.findings, _candidate(self.source, node), self.source, self.limit)


def _candidate(source: SourceFile, node: ast.Import | ast.ImportFrom) -> CandidateFinding:
    return candidate_for_statement(source, "HSL002", "import inside function body", node)
