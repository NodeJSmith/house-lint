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

    lines = render_text(result).splitlines()
    empty_scan_lines = [line for line in lines if line.startswith("empty scan:")]
    assert empty_scan_lines == [
        (
            "empty scan: no Python files selected; no config file found: create one with an "
            "include list, or pass explicit paths (house-lint <path>)"
        )
    ]
    assert json.loads(render_json(result))["findings"] == []


def test_zero_file_guidance_references_pyproject_when_that_config_was_used(
    tmp_path: Path,
) -> None:
    config = tmp_path / "pyproject.toml"
    result = ScanResult(tmp_path, config, ("HSL001", "HSL900"), 0, 0)

    text = render_text(result)

    assert "pyproject.toml's [tool.house-lint] table" in text


def test_zero_file_guidance_references_standalone_config_when_that_config_was_used(
    tmp_path: Path,
) -> None:
    config = tmp_path / "house-lint.toml"
    result = ScanResult(tmp_path, config, ("HSL001", "HSL900"), 0, 0)

    text = render_text(result)

    assert "house-lint.toml's [house-lint] table" in text


def test_zero_file_guidance_suppressed_for_intentional_empty_include(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)

    text = render_text(result, include_is_default=False, include=())

    lines = text.splitlines()
    empty_scan_lines = [line for line in lines if line.startswith("empty scan:")]
    assert empty_scan_lines == ["empty scan: no Python files selected"]


def test_zero_file_guidance_suppressed_for_explicit_cli_paths(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)

    text = render_text(result, explicit_paths=True)

    lines = text.splitlines()
    empty_scan_lines = [line for line in lines if line.startswith("empty scan:")]
    assert empty_scan_lines == ["empty scan: no Python files selected"]


def test_zero_file_guidance_not_suppressed_for_typo_d_nonempty_include(tmp_path: Path) -> None:
    """A typo'd explicit `include` (non-default, non-empty) is the most common real trigger
    (FR#8) and must still surface guidance, not just the base message."""
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)

    text = render_text(result, include_is_default=False, include=("test",))

    assert "no config file found" in text


def test_json_reporter_includes_zero_file_diagnostic(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)

    data = json.loads(render_json(result))

    assert data["zero_file_diagnostic"].startswith("empty scan: no Python files selected")
    assert "no config file found" in data["zero_file_diagnostic"]


def test_json_reporter_zero_file_diagnostic_unadorned_when_suppressed(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)

    data = json.loads(render_json(result, explicit_paths=True))

    assert data["zero_file_diagnostic"] == "empty scan: no Python files selected"


def test_json_reporter_omits_zero_file_diagnostic_when_files_were_scanned(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL002",), 1, 0)

    data = json.loads(render_json(result))

    assert "zero_file_diagnostic" not in data


def test_json_reporter_escapes_non_ascii_while_text_reporter_keeps_it_raw(tmp_path: Path) -> None:
    result = ScanResult(
        tmp_path,
        None,
        ("HSL002",),
        1,
        0,
        (Finding("HSL002", "src/café.py", None, None, None, None, "café finding"),),
    )

    assert "café" not in render_json(result)
    assert "\\u00e9" in render_json(result)
    assert "café" in render_text(result)
