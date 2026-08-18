import pytest

from house_lint.analysis import CandidateBudgetExceeded, SourceKind, StatementKey
from house_lint.rules.type_checking_position import detect
from house_lint.source import SourceFile


def test_detects_both_top_level_guards_followed_by_later_imports(write_sample) -> None:
    path = write_sample("""\
        from typing import TYPE_CHECKING
        import typing

        if TYPE_CHECKING:
            from a import A
        import os

        if typing.TYPE_CHECKING:
            from b import B
        from pathlib import Path
    """)

    findings = detect(SourceFile(path, path.parent))

    assert [(finding.line, finding.message) for finding in findings] == [
        (4, "if TYPE_CHECKING block followed by imports"),
        (8, "if TYPE_CHECKING block followed by imports"),
    ]
    assert all(finding.source_kind is SourceKind.STATEMENT for finding in findings)
    assert findings[0].owner == StatementKey(4, 1, 5, 20)


def test_ignores_final_and_nested_type_checking_guards(write_sample) -> None:
    path = write_sample("""\
        from typing import TYPE_CHECKING

        def function():
            if TYPE_CHECKING:
                from a import A
            import os

        if TYPE_CHECKING:
            from pathlib import Path
    """)

    assert detect(SourceFile(path, path.parent)) == []


def test_ignores_final_and_nested_qualified_type_checking_guards(write_sample) -> None:
    path = write_sample("""\
        import typing

        def function():
            if typing.TYPE_CHECKING:
                from a import A
            import os

        if typing.TYPE_CHECKING:
            from pathlib import Path

        class Example:
            pass
    """)

    assert detect(SourceFile(path, path.parent)) == []


def test_reports_only_guards_with_later_top_level_imports(write_sample) -> None:
    path = write_sample("""\
        import os
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            from pathlib import Path

        import sys

        if TYPE_CHECKING:
            from collections.abc import Iterator

        class Example:
            pass
    """)

    findings = detect(SourceFile(path, path.parent))

    assert [(finding.line, finding.message) for finding in findings] == [
        (4, "if TYPE_CHECKING block followed by imports")
    ]


def test_limits_materialized_candidates_when_requested(write_sample) -> None:
    guards = "if TYPE_CHECKING:\n    from a import A\n" * 10_002
    path = write_sample(f"from typing import TYPE_CHECKING\n\n{guards}import os\n")

    with pytest.raises(CandidateBudgetExceeded):
        detect(SourceFile(path, path.parent), limit=10_000)
