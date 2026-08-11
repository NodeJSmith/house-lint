from house_lint.analysis import SourceKind
from house_lint.config import HSL102Options
from house_lint.rules.file_length import detect
from house_lint.source import SourceFile


def test_flags_only_files_strictly_over_configured_splitlines_threshold(write_sample) -> None:
    exact = write_sample("x = 1\n" * 3)
    assert detect(SourceFile(exact, exact.parent), HSL102Options(max_lines=3)) == []

    over = write_sample("x = 1\n" * 4)
    [finding] = detect(SourceFile(over, over.parent), HSL102Options(max_lines=3))

    assert finding.message == "4 lines (threshold: 3)"
    assert (finding.line, finding.column, finding.end_line, finding.end_column) == (
        None,
        None,
        None,
        None,
    )
    assert finding.source_kind is SourceKind.FILE
    assert finding.owner is None


def test_legacy_file_size_exemption_comment_does_not_suppress_finding(write_sample) -> None:
    path = write_sample("# file-size-exempt: legacy annotation\nx = 1\ny = 2\nz = 3\n")

    findings = detect(SourceFile(path, path.parent), HSL102Options(max_lines=3))

    assert [finding.message for finding in findings] == ["4 lines (threshold: 3)"]
