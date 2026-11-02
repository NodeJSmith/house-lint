import pytest

from house_lint.analysis import (
    MAX_CANDIDATES_PER_FILE,
    CandidateBudgetExceeded,
    SourceKind,
    StatementKey,
)
from house_lint.rules.type_checking_position import detect


def test_detects_both_top_level_guards_followed_by_later_imports(parse_sample) -> None:
    source = parse_sample("""\
        from typing import TYPE_CHECKING
        import typing

        if TYPE_CHECKING:
            from a import A
        import os

        if typing.TYPE_CHECKING:
            from b import B
        from pathlib import Path
    """)

    findings = detect(source, None)

    assert [(finding.line, finding.message) for finding in findings] == [
        (4, "if TYPE_CHECKING block followed by imports"),
        (8, "if TYPE_CHECKING block followed by imports"),
    ]
    assert all(finding.source_kind is SourceKind.STATEMENT for finding in findings)
    assert findings[0].owner == StatementKey(4, 1, 5, 20)


def test_ignores_final_and_nested_type_checking_guards(parse_sample) -> None:
    source = parse_sample("""\
        from typing import TYPE_CHECKING

        def function():
            if TYPE_CHECKING:
                from a import A
            import os

        if TYPE_CHECKING:
            from pathlib import Path
    """)

    assert detect(source, None) == []


def test_ignores_final_and_nested_qualified_type_checking_guards(parse_sample) -> None:
    source = parse_sample("""\
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

    assert detect(source, None) == []


def test_reports_only_guards_with_later_top_level_imports(parse_sample) -> None:
    source = parse_sample("""\
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

    findings = detect(source, None)

    assert [(finding.line, finding.message) for finding in findings] == [
        (4, "if TYPE_CHECKING block followed by imports")
    ]


def test_limits_materialized_candidates_when_requested(parse_sample) -> None:
    guards = "if TYPE_CHECKING:\n    from a import A\n" * (MAX_CANDIDATES_PER_FILE + 2)
    source = parse_sample(f"from typing import TYPE_CHECKING\n\n{guards}import os\n")

    with pytest.raises(CandidateBudgetExceeded):
        detect(source, None, limit=MAX_CANDIDATES_PER_FILE)


def test_detects_guards_through_typing_module_and_flag_aliases(parse_sample) -> None:
    source = parse_sample(
        "import typing as t\n"
        "from typing import TYPE_CHECKING as TC\n"
        "if t.TYPE_CHECKING:\n"
        "    pass\n"
        "if TC:\n"
        "    pass\n"
        "import os\n"
    )

    findings = detect(source, None)

    assert [finding.line for finding in findings] == [3, 5]


def test_ignores_type_checking_alias_from_relative_typing_import(parse_sample) -> None:
    source = parse_sample("from .typing import TYPE_CHECKING as TC\nif TC:\n    pass\nimport os\n")

    assert detect(source, None) == []
