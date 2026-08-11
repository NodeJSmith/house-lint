import json
from pathlib import Path

from house_lint.reporters import (
    render_json,
    render_rule_list_json,
    render_rule_list_text,
    render_text,
)
from house_lint.results import Finding, LintError, RuleInfo, RuleList, ScanResult


def test_reporters_render_deterministic_text_and_complete_json(tmp_path: Path) -> None:
    result = ScanResult(
        tmp_path,
        None,
        ("HSL002", "HSL900"),
        1,
        0,
        (
            Finding("HSL101", "T01_example.py", None, None, None, None, "filename finding"),
            Finding("HSL002", "src/app.py", 12, 5, 12, 20, "import inside function body"),
        ),
        2,
        (
            LintError(
                "syntax-error",
                "syntax",
                "src/broken.py",
                1,
                1,
                1,
                2,
                "analysis",
                "ast-parse",
                None,
                "invalid syntax",
            ),
        ),
    )

    assert render_text(result).splitlines()[4:6] == [
        "T01_example.py: HSL101 filename finding",
        "src/app.py:12:5: HSL002 import inside function body",
    ]
    assert json.loads(render_json(result)) == result.to_dict()
    assert render_json(result) == render_json(result)


def test_rule_list_reporters_include_stable_enablement() -> None:
    rules = RuleList((RuleInfo("HSL001", "Cruft", "description", "default"),))

    assert render_rule_list_text(rules) == "HSL001 [default] Cruft: description"
    assert json.loads(render_rule_list_json(rules)) == rules.to_dict()


def test_text_reporter_makes_clean_empty_scans_explicit(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)

    assert "empty scan: no Python files selected" in render_text(result).splitlines()
