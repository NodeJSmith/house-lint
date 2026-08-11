from pathlib import Path

from house_lint.analysis import SourceKind, StatementKey
from house_lint.config import HSL101Options, TokenFamily
from house_lint.rules.spec_tokens import detect
from house_lint.source import SourceFile


def test_detects_configured_comment_and_docstring_tokens_with_provenance(write_sample) -> None:
    path = write_sample(
        '"""Implement FR#6a before release.\nT05: planning label\n"""\n'
        'value = 1  # AC1 and WP04\n'
    )
    options = HSL101Options(
        (
            TokenFamily(("AC", "FR", "WP"), ("comments", "docstrings"), "optional", suffix="optional-lower-alpha"),
            TokenFamily(("T",), ("docstrings",), min_digits=2, not_followed_by_time=True),
        )
    )

    findings = detect(SourceFile(path, path.parent), options)

    assert [(finding.line, finding.message) for finding in findings] == [
        (1, "spec token FR#6a in docstring"),
        (2, "spec token T05 in docstring"),
        (4, "spec token AC1 in comment"),
        (4, "spec token WP04 in comment"),
    ]
    assert all(finding.source_kind is SourceKind.STATEMENT for finding in findings)


def test_respects_hash_digits_suffix_case_time_and_ordinary_strings(write_sample) -> None:
    path = write_sample(
        'data = "FR#6 and T05"\n'
        '# FR6 FR#6a FR#6A fr#6 T05:30 T05: label\n'
    )
    options = HSL101Options(
        (
            TokenFamily(("FR",), ("comments",), "required", suffix="optional-lower-alpha"),
            TokenFamily(("T",), ("comments",), min_digits=2, not_followed_by_time=True),
        )
    )

    findings = detect(SourceFile(path, path.parent), options)

    assert [(finding.line, finding.message) for finding in findings] == [
        (2, "spec token FR#6a in comment"),
        (2, "spec token T05 in comment"),
    ]


def test_comment_token_on_multiline_statement_uses_statement_provenance(write_sample) -> None:
    path = write_sample(
        "def prepare() -> None:\n"
        "    value = (\n"
        "        1\n"
        "    )  # AC1\n"
    )
    options = HSL101Options((TokenFamily(("AC",), ("comments",)),))

    [finding] = detect(SourceFile(path, path.parent), options)

    assert finding.source_kind is SourceKind.STATEMENT
    assert finding.owner == StatementKey(2, 5, 4, 6)


def test_respects_case_and_maximum_digits_for_each_configured_scope(write_sample) -> None:
    path = write_sample('"""fr01 is prose but outside the configured scope."""\n# fr01 FR012\n')
    options = HSL101Options(
        (TokenFamily(("FR",), ("comments",), min_digits=2, max_digits=2, case_sensitive=False),)
    )

    findings = detect(SourceFile(path, path.parent), options)

    assert [(finding.line, finding.message) for finding in findings] == [
        (2, "spec token fr01 in comment")
    ]


def test_detects_whole_filename_segments_without_source_owner(tmp_path: Path) -> None:
    path = tmp_path / "test-T05_AC1.py"
    path.write_text("value = 1\n")
    options = HSL101Options(
        (TokenFamily(("AC", "T"), ("filenames",), min_digits=1),)
    )

    findings = detect(SourceFile(path, tmp_path), options)

    assert [
        (finding.message, finding.line, finding.column, finding.end_line, finding.end_column)
        for finding in findings
    ] == [
        ("spec token T05 in filename", None, None, None, None),
        ("spec token AC1 in filename", None, None, None, None),
    ]
    assert all(finding.source_kind is SourceKind.FILENAME and finding.owner is None for finding in findings)


def test_limits_findings_per_file(write_sample) -> None:
    path = write_sample("# AC1 AC2 AC3\n")
    options = HSL101Options((TokenFamily(("AC",), ("comments",)),), max_findings_per_file=2)

    findings = detect(SourceFile(path, path.parent), options)

    assert [finding.message for finding in findings] == [
        "spec token AC1 in comment",
        "spec token AC2 in comment",
    ]


def test_default_hash_mode_forbids_hash_and_default_cap_is_200(write_sample) -> None:
    path = write_sample("# AC#1 " + " ".join(f"AC{number:03d}" for number in range(1, 202)) + "\n")
    options = HSL101Options((TokenFamily(("AC",), ("comments",)),))

    findings = detect(SourceFile(path, path.parent), options)

    assert len(findings) == 200
    assert findings[0].message == "spec token AC001 in comment"
    assert findings[-1].message == "spec token AC200 in comment"
