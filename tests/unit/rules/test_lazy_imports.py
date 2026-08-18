import pytest

from house_lint.analysis import (
    MAX_CANDIDATES_PER_FILE,
    CandidateBudgetExceeded,
    SourceKind,
    StatementKey,
)
from house_lint.rules.lazy_imports import detect
from house_lint.source import SourceFile


def test_detects_imports_at_function_depth_including_async_methods_and_nested(write_sample) -> None:
    path = write_sample("""\
        import os

        def outer():
            import json
            async def inner():
                from pathlib import Path

        class Example:
            def method(self):
                import sys
    """)

    findings = detect(SourceFile(path, path.parent), None)

    assert [(finding.line, finding.message) for finding in findings] == [
        (4, "import inside function body"),
        (6, "import inside function body"),
        (10, "import inside function body"),
    ]
    assert all(finding.source_kind is SourceKind.STATEMENT for finding in findings)
    assert findings[0].owner == StatementKey(4, 5, 4, 16)


def test_limits_materialized_candidates_when_requested(write_sample) -> None:
    body = "\n".join(f"    import mod_{i}" for i in range(MAX_CANDIDATES_PER_FILE + 2))
    path = write_sample(f"def example():\n{body}\n")

    with pytest.raises(CandidateBudgetExceeded):
        detect(SourceFile(path, path.parent), None, limit=MAX_CANDIDATES_PER_FILE)
