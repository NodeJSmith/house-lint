import pytest

from house_lint.analysis import CandidateBudgetExceeded, SourceKind, StatementKey
from house_lint.rules.constants_position import detect
from house_lint.source import SourceFile


def test_detects_misplaced_uppercase_assignments_and_excludes_dunders_and_lowercase(
    write_sample,
) -> None:
    path = write_sample("""\
        class Example:
            pass

        FOO = 1
        BAR: int = 2
        __all__ = ["Example"]
        local_value = 3
        X = 4
    """)

    findings = detect(SourceFile(path, path.parent), None)

    assert [(finding.line, finding.message) for finding in findings] == [
        (4, "constant defined after the first class or function"),
        (5, "constant defined after the first class or function"),
    ]
    assert all(finding.source_kind is SourceKind.STATEMENT for finding in findings)
    assert findings[0].owner == StatementKey(4, 1, 4, 8)


def test_ignores_constants_without_definitions_or_before_the_first_definition(write_sample) -> None:
    no_definitions = write_sample("FOO = 1\nBAR = 2\n")
    assert detect(SourceFile(no_definitions, no_definitions.parent), None) == []

    before_definition = write_sample("FOO = 1\n\nclass Example:\n    pass\n")
    assert detect(SourceFile(before_definition, before_definition.parent), None) == []


def test_exempts_constants_derived_from_earlier_bindings_in_values_and_annotations(
    write_sample,
) -> None:
    path = write_sample("""\
        class Handle:
            pass

        def build():
            return (1, 2)

        _COLUMNS = build()
        _INSERT_SQL = f"INSERT ({_COLUMNS})"
        HANDLE_VAR: dict[str, Handle] = {}
    """)

    assert detect(SourceFile(path, path.parent), None) == []


def test_exempts_annotation_references_with_postponed_annotations(write_sample) -> None:
    path = write_sample("""\
        from __future__ import annotations

        class Handle:
            pass

        HANDLE_VAR: dict[str, Handle] = {}
    """)

    assert detect(SourceFile(path, path.parent), None) == []


def test_handles_unpacking_and_skips_unsupported_assignment_targets(write_sample) -> None:
    path = write_sample("""\
        class Example:
            pass

        FOO, BAR = 1, 2
        module.VALUE = 3
    """)

    findings = detect(SourceFile(path, path.parent), None)

    assert [(finding.line, finding.message) for finding in findings] == [
        (4, "constant defined after the first class or function")
    ]


def test_limits_materialized_candidates_when_requested(write_sample) -> None:
    constants = "\n".join(f"X{i} = {i}" for i in range(10_002))
    path = write_sample(f"class Example:\n    pass\n\n{constants}\n")

    with pytest.raises(CandidateBudgetExceeded):
        detect(SourceFile(path, path.parent), None, limit=10_000)
