from pathlib import Path

import pytest

from house_lint.results import Finding, LintError, RelatedLocation, RuleInfo, RuleList, ScanResult


def test_finding_and_error_serialize_their_exact_schema_fields():
    finding = Finding(
        "HSL001",
        "src/app.py",
        3,
        5,
        3,
        12,
        "message",
        (
            RelatedLocation("src/other.py", 7, 1, 9, 4),
            RelatedLocation("gen.py", None, None, None, None),
        ),
    )
    error = LintError(
        "syntax-error",
        "syntax",
        "src/broken.py",
        4,
        2,
        4,
        3,
        "analysis",
        "ast-parse",
        None,
        "invalid syntax",
    )

    assert finding.to_dict() == {
        "rule_id": "HSL001",
        "path": "src/app.py",
        "line": 3,
        "column": 5,
        "end_line": 3,
        "end_column": 12,
        "message": "message",
        "related": [
            {"path": "src/other.py", "line": 7, "column": 1, "end_line": 9, "end_column": 4},
            {"path": "gen.py", "line": None, "column": None, "end_line": None, "end_column": None},
        ],
    }
    assert error.to_dict() == {
        "code": "syntax-error",
        "kind": "syntax",
        "path": "src/broken.py",
        "line": 4,
        "column": 2,
        "end_line": 4,
        "end_column": 3,
        "phase": "analysis",
        "operation": "ast-parse",
        "rule_id": None,
        "message": "invalid syntax",
    }


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: Finding("HSL001", "src/app.py", 1, None, 1, 2, "message"), "all null"),
        (lambda: Finding("HSL001", "src/app.py", True, 1, 1, 2, "message"), "all null"),
        (lambda: Finding("HSL001", "src/app.py", 1.5, 1, 1, 2, "message"), "all null"),
        (lambda: RelatedLocation("src/app.py", 1, None, 1, 2), "all null"),
        (lambda: RelatedLocation("src/app.py", 2, 1, 1, 1), "precede"),
        (
            lambda: Finding("HSL001", "src/app.py", 2, 1, 1, 2, "message"),
            "end must not precede",
        ),
        (
            lambda: LintError(
                "syntax-error",
                "syntax",
                "src/app.py",
                1,
                None,
                1,
                2,
                "analysis",
                "ast-parse",
                None,
                "message",
            ),
            "all null",
        ),
        (
            lambda: LintError(
                "syntax-error",
                "syntax",
                "src/app.py",
                True,
                1,
                1,
                2,
                "analysis",
                "ast-parse",
                None,
                "message",
            ),
            "all null",
        ),
        (
            lambda: LintError(
                "syntax-error",
                "syntax",
                "src/app.py",
                1.5,
                1,
                1,
                2,
                "analysis",
                "ast-parse",
                None,
                "message",
            ),
            "all null",
        ),
        (
            lambda: LintError(
                "syntax-error",
                "syntax",
                "src/app.py",
                2,
                1,
                1,
                2,
                "analysis",
                "ast-parse",
                None,
                "message",
            ),
            "end must not precede",
        ),
    ],
)
def test_public_dtos_reject_invalid_locations(factory, message):
    with pytest.raises(ValueError, match=message):
        factory()


def test_scan_result_normalizes_relative_root_and_config_paths():
    result = ScanResult(Path("project"), Path("project/pyproject.toml"), (), 0)

    assert result.root == Path("project").absolute()
    assert result.config == Path("project/pyproject.toml").absolute()


def test_scan_result_serializes_schema_v1_with_nulls_and_sorted_values(tmp_path):
    result = ScanResult(
        root=tmp_path,
        config=None,
        enabled_rules=("HSL002", "HSL001"),
        files_scanned=2,
        findings=(
            Finding("HSL002", "b.py", 2, 5, 2, 10, "later"),
            Finding("HSL101", "a.py", None, None, None, None, "filename"),
        ),
        suppressed_count=3,
        errors=(
            LintError(
                "read-error", "read", "b.py", None, None, None, None, "read", "open", None, "read"
            ),
            LintError(
                "syntax-error", "syntax", "b.py", 2, 1, 2, 2, "analysis", "ast-parse", None, "bad"
            ),
        ),
    )

    assert result.to_dict() == {
        "schema_version": 3,
        "root": str(tmp_path),
        "config": None,
        "enabled_rules": ["HSL001", "HSL002"],
        "files_scanned": 2,
        "findings": [
            {
                "rule_id": "HSL101",
                "path": "a.py",
                "line": None,
                "column": None,
                "end_line": None,
                "end_column": None,
                "message": "filename",
                "related": [],
            },
            {
                "rule_id": "HSL002",
                "path": "b.py",
                "line": 2,
                "column": 5,
                "end_line": 2,
                "end_column": 10,
                "message": "later",
                "related": [],
            },
        ],
        "errors": [
            {
                "code": "read-error",
                "kind": "read",
                "path": "b.py",
                "line": None,
                "column": None,
                "end_line": None,
                "end_column": None,
                "phase": "read",
                "operation": "open",
                "rule_id": None,
                "message": "read",
            },
            {
                "code": "syntax-error",
                "kind": "syntax",
                "path": "b.py",
                "line": 2,
                "column": 1,
                "end_line": 2,
                "end_column": 2,
                "phase": "analysis",
                "operation": "ast-parse",
                "rule_id": None,
                "message": "bad",
            },
        ],
        "summary": {"finding_count": 2, "error_count": 2, "suppressed_count": 3},
    }

    assert not result.is_clean


def test_clean_scan_result_is_only_clean_without_findings_or_errors(tmp_path):
    result = ScanResult(tmp_path, None, (), 0)

    assert result.is_clean


def test_rule_list_serializes_schema_v1():
    assert RuleList((RuleInfo("HSL001", "Cruft", "description", "default"),)).to_dict() == {
        "schema_version": 1,
        "rules": [
            {"id": "HSL001", "name": "Cruft", "description": "description", "enablement": "default"}
        ],
    }


def test_findings_that_differ_only_in_related_serialize_in_a_stable_order(tmp_path):
    first = Finding("HSL001", "a.py", 1, 1, 1, 2, "same", (RelatedLocation("b.py", 5, 1, 5, 2),))
    second = Finding("HSL001", "a.py", 1, 1, 1, 2, "same", (RelatedLocation("b.py", 3, 1, 3, 2),))

    def serialize(findings: tuple[Finding, ...]) -> dict[str, object]:
        return ScanResult(
            root=tmp_path,
            config=None,
            enabled_rules=("HSL001",),
            files_scanned=2,
            findings=findings,
        ).to_dict()

    forward = serialize((first, second))

    assert forward == serialize((second, first))
    assert [finding["related"][0]["line"] for finding in forward["findings"]] == [3, 5]
