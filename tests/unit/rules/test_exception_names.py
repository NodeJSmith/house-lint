import pytest

from house_lint.analysis import MAX_CANDIDATES_PER_FILE, CandidateBudgetExceeded, SourceKind
from house_lint.rules.exception_names import HSL103Options, detect


def test_allows_unbound_exc_and_suffix_exception_bindings(parse_sample) -> None:
    source = parse_sample(
        "try:\n    pass\nexcept ValueError:\n    pass\n"
        "try:\n    pass\nexcept TypeError as exc:\n    pass\n"
        "try:\n    pass\nexcept OSError as retry_exc:\n    pass\n"
    )

    assert detect(source, HSL103Options()) == []


def test_detects_disallowed_multiple_and_nested_exception_bindings(parse_sample) -> None:
    source = parse_sample(
        "try:\n    pass\nexcept ValueError as err:\n    pass\n"
        "try:\n    pass\nexcept TypeError as exc:\n    pass\nexcept OSError as error:\n    pass\n"
        "def run():\n    try:\n        pass\n    except RuntimeError as e:\n        pass\n"
    )

    findings = detect(source, HSL103Options())

    assert [(finding.line, finding.message) for finding in findings] == [
        (3, "exception handler bound to 'err'"),
        (9, "exception handler bound to 'error'"),
        (14, "exception handler bound to 'e'"),
    ]
    assert all(finding.source_kind is SourceKind.STATEMENT for finding in findings)
    # Owned by each handler's own span, not the enclosing try's -- sibling handlers must not
    # share an owner key, or one suppression silences all of them.
    assert [(finding.owner.start_line, finding.owner.end_line) for finding in findings] == [
        (3, 4),
        (9, 10),
        (14, 15),
    ]


def test_detects_disallowed_except_star_binding(parse_sample) -> None:
    source = parse_sample("try:\n    pass\nexcept* ValueError as err:\n    pass\n")

    [finding] = detect(source, HSL103Options())

    assert (finding.line, finding.message) == (3, "exception handler bound to 'err'")


def test_uses_exact_allowed_names_and_single_leading_star_suffix_patterns(parse_sample) -> None:
    source = parse_sample("try:\n    pass\nexcept ValueError as caught_error:\n    pass\n")

    assert detect(source, HSL103Options(("caught_error",))) == []
    [finding] = detect(source, HSL103Options(("*_exc",)))
    assert finding.message == "exception handler bound to 'caught_error'"


def test_limits_materialized_candidates_when_requested(parse_sample) -> None:
    handlers = "\n".join(
        f"try:\n    pass\nexcept ValueError as err{i}:\n    pass"
        for i in range(MAX_CANDIDATES_PER_FILE + 2)
    )
    source = parse_sample(f"{handlers}\n")

    with pytest.raises(CandidateBudgetExceeded):
        detect(source, HSL103Options(), limit=MAX_CANDIDATES_PER_FILE)
