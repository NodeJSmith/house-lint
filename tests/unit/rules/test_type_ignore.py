from house_lint.analysis import SourceKind, StatementKey
from house_lint.rules.type_ignore import MESSAGE, detect


def test_flags_bracketed_type_ignore_in_any_spacing_and_after_another_comment(
    parse_sample,
) -> None:
    source = parse_sample("""\
        a: str = 1  # type: ignore[assignment]
        b: str = 1  #type:ignore[misc]  explained here
        c: str = 1  # noqa: E501  # type: ignore[assignment]
    """)

    findings = detect(source, None)

    assert [finding.line for finding in findings] == [1, 2, 3]
    assert all(finding.rule_id == "HSL105" for finding in findings)
    assert all(finding.message == MESSAGE for finding in findings)


def test_flags_empty_brackets_a_spaced_bracket_and_any_hash_pyright_honors(parse_sample) -> None:
    source = parse_sample("""\
        a: str = 1  # type:  ignore[]
        b: str = 1  # never write # type: ignore[misc] here
        c: str = 1  # type: ignore [misc]
    """)

    assert [finding.line for finding in detect(source, None)] == [1, 2, 3]


def test_leaves_bare_ignores_prose_and_strings_alone(parse_sample) -> None:
    source = parse_sample("""\
        a: str = 1  # type: ignore
        b: str = 1  # type: ignore  # reason [in brackets]
        c: str = 1  # pyright: ignore[reportAssignmentType]
        # Pyright disregards the codes in type: ignore[misc], so don't write them.
        d = "x  # type: ignore[misc]"
        \"\"\"Docstring mentioning # type: ignore[misc].\"\"\"
    """)

    assert detect(source, None) == []


def test_trailing_comment_is_owned_by_its_statement(parse_sample) -> None:
    source = parse_sample("value: str = 1  # type: ignore[assignment]\n")

    [finding] = detect(source, None)

    assert finding.source_kind is SourceKind.STATEMENT
    assert finding.owner == StatementKey(1, 1, 1, 15)
