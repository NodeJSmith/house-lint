from pathlib import Path

import pytest

from house_lint.analysis import (
    MAX_CANDIDATES_PER_FILE,
    CandidateBudgetExceeded,
    SourceKind,
    StatementKey,
)
from house_lint.config import BUILTIN_TASK, HSL101Options, TokenFamily
from house_lint.rules.spec_tokens import detect
from house_lint.source import SourceFile


def test_detects_configured_comment_and_docstring_tokens_with_provenance(write_sample) -> None:
    path = write_sample(
        '"""Implement FR#6a before release.\nT05: planning label\n"""\nvalue = 1  # AC1 and WP04\n'
    )
    options = HSL101Options(
        (
            TokenFamily(
                prefixes=("AC", "FR", "WP"),
                scopes=("comments", "docstrings"),
                separator="hash-optional",
                suffix="optional-lower-alpha",
            ),
            TokenFamily(
                prefixes=("T",),
                scopes=("docstrings",),
                min_digits=2,
                not_followed_by_time=True,
            ),
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
    path = write_sample('data = "FR#6 and T05"\n# FR6 FR#6a FR#6A fr#6 T05:30 T05: label\n')
    options = HSL101Options(
        (
            TokenFamily(
                prefixes=("FR",),
                scopes=("comments",),
                separator="hash",
                suffix="optional-lower-alpha",
            ),
            TokenFamily(
                prefixes=("T",), scopes=("comments",), min_digits=2, not_followed_by_time=True
            ),
        )
    )

    findings = detect(SourceFile(path, path.parent), options)

    assert [(finding.line, finding.message) for finding in findings] == [
        (2, "spec token FR#6a in comment"),
        (2, "spec token T05 in comment"),
    ]


def test_comment_token_on_multiline_statement_uses_statement_provenance(write_sample) -> None:
    path = write_sample("def prepare() -> None:\n    value = (\n        1\n    )  # AC1\n")
    options = HSL101Options((TokenFamily(prefixes=("AC",), scopes=("comments",)),))

    [finding] = detect(SourceFile(path, path.parent), options)

    assert finding.source_kind is SourceKind.STATEMENT
    assert finding.owner == StatementKey(2, 5, 4, 6)


def test_respects_case_and_maximum_digits_for_each_configured_scope(write_sample) -> None:
    path = write_sample('"""fr01 is prose but outside the configured scope."""\n# fr01 FR012\n')
    options = HSL101Options(
        (
            TokenFamily(
                prefixes=("FR",),
                scopes=("comments",),
                min_digits=2,
                max_digits=2,
                case_sensitive=False,
            ),
        )
    )

    findings = detect(SourceFile(path, path.parent), options)

    assert [(finding.line, finding.message) for finding in findings] == [
        (2, "spec token fr01 in comment")
    ]


def test_detects_whole_filename_segments_without_source_owner(tmp_path: Path) -> None:
    path = tmp_path / "test-T05_AC1.py"
    path.write_text("value = 1\n")
    options = HSL101Options(
        (TokenFamily(prefixes=("AC", "T"), scopes=("filenames",), min_digits=1),)
    )

    findings = detect(SourceFile(path, tmp_path), options)

    assert [
        (finding.message, finding.line, finding.column, finding.end_line, finding.end_column)
        for finding in findings
    ] == [
        ("spec token T05 in filename", None, None, None, None),
        ("spec token AC1 in filename", None, None, None, None),
    ]
    assert all(
        finding.source_kind is SourceKind.FILENAME and finding.owner is None for finding in findings
    )


def test_detects_dash_separated_family_as_joined_segment_in_filename(tmp_path: Path) -> None:
    # Regression test: a naive implementation that splits the filename on
    # "._-" before matching would see only "notes", "KI", "001", and "fix"
    # as separate segments and never see the dash-joined "KI-001" token, so
    # a dash-separated family (e.g. the built-in known-issues family) would
    # never match. The pattern must be applied directly against the whole
    # filename so the separator is preserved.
    path = tmp_path / "notes-KI-001-fix.py"
    path.write_text("value = 1\n")
    options = HSL101Options(
        (TokenFamily(prefixes=("KI",), scopes=("filenames",), separator="dash"),)
    )

    findings = detect(SourceFile(path, tmp_path), options)

    assert [finding.message for finding in findings] == ["spec token KI-001 in filename"]


def test_limits_findings_per_file(write_sample) -> None:
    path = write_sample("# AC1 AC2 AC3\n")
    options = HSL101Options(
        (TokenFamily(prefixes=("AC",), scopes=("comments",)),), max_findings_per_file=2
    )

    findings = detect(SourceFile(path, path.parent), options)

    assert [finding.message for finding in findings] == [
        "spec token AC1 in comment",
        "spec token AC2 in comment",
    ]


def test_none_separator_forbids_any_separator(write_sample) -> None:
    path = write_sample("# AC#1 AC001\n")
    options = HSL101Options((TokenFamily(prefixes=("AC",), scopes=("comments",)),))

    findings = detect(SourceFile(path, path.parent), options)

    assert [finding.message for finding in findings] == ["spec token AC001 in comment"]


def test_default_max_findings_per_file_is_200(write_sample) -> None:
    path = write_sample("# " + " ".join(f"AC{number:03d}" for number in range(1, 202)) + "\n")
    options = HSL101Options((TokenFamily(prefixes=("AC",), scopes=("comments",)),))

    findings = detect(SourceFile(path, path.parent), options)

    assert len(findings) == 200
    assert findings[0].message == "spec token AC001 in comment"
    assert findings[-1].message == "spec token AC200 in comment"


def test_limits_materialized_candidates_when_requested(write_sample) -> None:
    # range starts at 1 (not 0) for readable token text, so +3 here produces the same
    # MAX_CANDIDATES_PER_FILE + 2 total tokens as the other rules' budget-cutoff tests
    tokens = " ".join(f"AC{i}" for i in range(1, MAX_CANDIDATES_PER_FILE + 3))
    path = write_sample(f"# {tokens}\n")
    options = HSL101Options(
        (TokenFamily(prefixes=("AC",), scopes=("comments",)),), max_findings_per_file=20_000
    )

    with pytest.raises(CandidateBudgetExceeded):
        detect(SourceFile(path, path.parent), options, limit=MAX_CANDIDATES_PER_FILE)


@pytest.mark.parametrize(
    ("separator", "prefix", "sample", "expected_messages"),
    [
        ("dash", "KI", "# KI-001 KI001 KI#001\n", ["spec token KI-001 in comment"]),
        (
            "hash-optional",
            "FR",
            "# FR#6 FR6\n",
            ["spec token FR#6 in comment", "spec token FR6 in comment"],
        ),
        (
            "dash-optional",
            "KI",
            "# KI-001 KI001\n",
            ["spec token KI-001 in comment", "spec token KI001 in comment"],
        ),
    ],
)
def test_separator_mode_matches_expected_tokens(
    write_sample, separator: str, prefix: str, sample: str, expected_messages: list[str]
) -> None:
    path = write_sample(sample)
    options = HSL101Options(
        (TokenFamily(prefixes=(prefix,), scopes=("comments",), separator=separator),)
    )

    findings = detect(SourceFile(path, path.parent), options)

    assert [finding.message for finding in findings] == expected_messages


def test_builtin_task_family_detects_task_tokens_but_not_time_strings(write_sample) -> None:
    path = write_sample("# T05 planned, but not T05:30\n")
    options = HSL101Options((BUILTIN_TASK,))

    findings = detect(SourceFile(path, path.parent), options)

    assert [finding.message for finding in findings] == ["spec token T05 in comment"]
