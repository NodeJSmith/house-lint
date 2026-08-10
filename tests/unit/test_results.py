from house_lint.results import Finding, LintError, RuleInfo, RuleList, ScanResult


def test_finding_from_span_normalizes_root_relative_posix_path_and_columns(tmp_path):
    path = tmp_path / "src" / "module.py"
    path.parent.mkdir()

    finding = Finding.from_span(
        "HSL002", path, tmp_path, 4, 2, 4, 11, "import inside function body"
    )

    assert finding.path == "src/module.py"
    assert (finding.line, finding.column, finding.end_line, finding.end_column) == (4, 3, 4, 12)


def test_finding_from_span_preserves_lexical_symlink_path(tmp_path):
    target = tmp_path / "pkg" / "real.py"
    target.parent.mkdir()
    target.write_text("value = 1\n")
    link = tmp_path / "link.py"
    link.symlink_to(target)

    finding = Finding.from_span("HSL002", link, tmp_path, 1, 0, 1, 7, "import")

    assert finding.path == "link.py"


def test_finding_and_error_serialize_their_exact_schema_fields():
    finding = Finding("HSL001", "src/app.py", 3, 5, 3, 12, "message")
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


def test_scan_result_serializes_schema_v1_with_nulls_and_sorted_values(tmp_path):
    result = ScanResult(
        root=tmp_path,
        config=None,
        enabled_rules=("HSL002", "HSL001"),
        files_scanned=2,
        files_skipped=1,
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
        "schema_version": 1,
        "root": str(tmp_path),
        "config": None,
        "enabled_rules": ["HSL001", "HSL002"],
        "files_scanned": 2,
        "files_skipped": 1,
        "findings": [
            {
                "rule_id": "HSL101",
                "path": "a.py",
                "line": None,
                "column": None,
                "end_line": None,
                "end_column": None,
                "message": "filename",
            },
            {
                "rule_id": "HSL002",
                "path": "b.py",
                "line": 2,
                "column": 5,
                "end_line": 2,
                "end_column": 10,
                "message": "later",
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
    result = ScanResult(tmp_path, None, (), 0, 0)

    assert result.is_clean


def test_rule_list_serializes_schema_v1():
    assert RuleList((RuleInfo("HSL001", "Cruft", "description", "default"),)).to_dict() == {
        "schema_version": 1,
        "rules": [
            {"id": "HSL001", "name": "Cruft", "description": "description", "enablement": "default"}
        ],
    }
