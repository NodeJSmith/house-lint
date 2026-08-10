from house_lint.analysis import SourceKind, StatementKey
from house_lint.rules.lazy_imports import detect
from house_lint.source import SourceFile


def test_detects_imports_at_function_depth_including_async_methods_and_nested(write_sample) -> None:
    path = write_sample('''\
        import os

        def outer():
            import json
            async def inner():
                from pathlib import Path

        class Example:
            def method(self):
                import sys
    ''')

    findings = detect(SourceFile(path, path.parent))

    assert [(finding.line, finding.message) for finding in findings] == [
        (4, "import inside function body"),
        (6, "import inside function body"),
        (10, "import inside function body"),
    ]
    assert all(finding.source_kind is SourceKind.STATEMENT for finding in findings)
    assert findings[0].owner == StatementKey(4, 5, 4, 16)
