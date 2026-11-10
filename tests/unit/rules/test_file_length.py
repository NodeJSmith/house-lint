from house_lint.analysis import SourceKind
from house_lint.results import Finding
from house_lint.rules.file_length import (
    HSL102Options,
    detect,
    format_message,
    line_count_of,
    match_new,
)


def test_flags_only_files_strictly_over_configured_splitlines_threshold(parse_sample) -> None:
    exact = parse_sample("x = 1\n" * 3)
    assert detect(exact, HSL102Options(max_lines=3)) == []

    over = parse_sample("x = 1\n" * 4)
    [finding] = detect(over, HSL102Options(max_lines=3))

    assert finding.message == "4 lines (threshold: 3)"
    assert (finding.line, finding.column, finding.end_line, finding.end_column) == (
        None,
        None,
        None,
        None,
    )
    assert finding.source_kind is SourceKind.FILE
    assert finding.owner is None


def test_legacy_file_size_exemption_comment_does_not_suppress_finding(parse_sample) -> None:
    source = parse_sample("# file-size-exempt: legacy annotation\nx = 1\ny = 2\nz = 3\n")

    findings = detect(source, HSL102Options(max_lines=3))

    assert [finding.message for finding in findings] == ["4 lines (threshold: 3)"]


# No budget-cutoff test here: detect() emits at most one file-owned candidate per call
# (see src/house_lint/rules/file_length.py), so the append_candidate safety limit can
# never be exceeded by this rule regardless of source size.


def hsl102(path: str, lines: int, threshold: int = 800) -> Finding:
    return Finding("HSL102", path, None, None, None, None, format_message(lines, threshold))


def ratchet(head: list[Finding], base: list[Finding], renames=None) -> tuple[Finding, ...]:
    return match_new(head, base, head_text={}, base_text={}, renames=renames or {})


def test_the_line_count_round_trips_through_the_message() -> None:
    assert line_count_of(hsl102("a.py", 912)) == 912


def test_a_file_over_the_threshold_is_new_only_when_it_grew() -> None:
    grew, same, shrank = hsl102("a.py", 900), hsl102("b.py", 850), hsl102("c.py", 820)
    base = [hsl102("a.py", 899), hsl102("b.py", 850), hsl102("c.py", 830)]

    assert ratchet([grew, same, shrank], base) == (grew,)


def test_a_file_with_no_base_finding_is_new() -> None:
    # Added over the threshold, or grown past it: both count as growing from under it.
    added = hsl102("new.py", 801)

    assert ratchet([added], []) == (added,)


def test_a_renamed_file_is_compared_with_its_base_path() -> None:
    moved, moved_and_grew = hsl102("pkg/a.py", 900), hsl102("pkg/b.py", 901)
    base = [hsl102("a.py", 900), hsl102("b.py", 900)]

    result = ratchet([moved, moved_and_grew], base, {"pkg/a.py": "a.py", "pkg/b.py": "b.py"})

    assert result == (moved_and_grew,)
