import json
from pathlib import Path

from house_lint.config import DEFAULT_INCLUDE
from house_lint.reporters import (
    EMPTY_SCAN_MESSAGE,
    render_json,
    render_rule_list_json,
    render_rule_list_text,
    render_text,
    shadowed_config_note,
    zero_file_guidance,
)
from house_lint.results import Finding, LintError, RuleInfo, RuleList, ScanResult


def _zero_file_note(
    result: ScanResult, *, include: tuple[str, ...] = DEFAULT_INCLUDE, explicit_paths: bool = False
) -> str:
    guidance = zero_file_guidance(result, include=include, explicit_paths=explicit_paths)
    return f"{EMPTY_SCAN_MESSAGE}{guidance}"


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

    assert render_text(result, zero_file_note=None).splitlines()[4:6] == [
        "T01_example.py: HSL101 filename finding",
        "src/app.py:12:5: HSL002 import inside function body",
    ]
    rendered = render_json(result, zero_file_note=None)
    assert json.loads(rendered) == result.to_dict()
    assert rendered == render_json(result, zero_file_note=None)


def test_rule_list_reporters_include_stable_enablement() -> None:
    rules = RuleList((RuleInfo("HSL001", "Cruft", "description", "default"),))

    assert render_rule_list_text(rules) == "HSL001 [default] Cruft: description"
    assert json.loads(render_rule_list_json(rules)) == rules.to_dict()


def test_text_reporter_appends_zero_file_note_when_given(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)
    note = _zero_file_note(result)

    lines = render_text(result, zero_file_note=note).splitlines()
    empty_scan_lines = [line for line in lines if line.startswith("empty scan:")]
    assert empty_scan_lines == [note]


def test_text_reporter_omits_zero_file_line_when_note_is_none(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)

    lines = render_text(result, zero_file_note=None).splitlines()

    assert not any(line.startswith("empty scan:") for line in lines)


def test_zero_file_guidance_references_pyproject_when_that_config_was_used(
    tmp_path: Path,
) -> None:
    config = tmp_path / "pyproject.toml"
    result = ScanResult(tmp_path, config, ("HSL001", "HSL900"), 0, 0)

    guidance = zero_file_guidance(result, include=DEFAULT_INCLUDE, explicit_paths=False)

    assert "pyproject.toml's [tool.house-lint] table" in guidance


def test_zero_file_guidance_references_standalone_config_when_that_config_was_used(
    tmp_path: Path,
) -> None:
    config = tmp_path / "house-lint.toml"
    result = ScanResult(tmp_path, config, ("HSL001", "HSL900"), 0, 0)

    guidance = zero_file_guidance(result, include=DEFAULT_INCLUDE, explicit_paths=False)

    assert "house-lint.toml's [house-lint] table" in guidance


def test_zero_file_guidance_suppressed_for_intentional_empty_include(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)

    assert zero_file_guidance(result, include=(), explicit_paths=False) == ""


def test_zero_file_guidance_suppressed_for_explicit_cli_paths(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)

    assert zero_file_guidance(result, include=DEFAULT_INCLUDE, explicit_paths=True) == ""


def test_zero_file_guidance_not_suppressed_for_typo_d_nonempty_include(tmp_path: Path) -> None:
    """A typo'd explicit `include` (non-default, non-empty) is the most common real trigger
    (FR#8) and must still surface guidance, not just the base message."""
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)

    guidance = zero_file_guidance(result, include=("test",), explicit_paths=False)

    assert "no config file found" in guidance


def test_shadowed_config_note_empty_when_nothing_shadowed() -> None:
    assert shadowed_config_note(()) == ""


def test_shadowed_config_note_lists_shadowed_paths(tmp_path: Path) -> None:
    shadowed = (tmp_path / "pyproject.toml",)

    note = shadowed_config_note(shadowed)

    assert note == f" (shadows {tmp_path / 'pyproject.toml'})"


def test_shadowed_config_note_joins_multiple_shadowed_paths(tmp_path: Path) -> None:
    shadowed = (tmp_path / "pyproject.toml", tmp_path / ".house-lint.toml")

    note = shadowed_config_note(shadowed)

    assert note == f" (shadows {tmp_path / 'pyproject.toml'}, {tmp_path / '.house-lint.toml'})"


def test_json_reporter_includes_shadowed_config_key(tmp_path: Path) -> None:
    config = tmp_path / "house-lint.toml"
    result = ScanResult(tmp_path, config, ("HSL002",), 1, 0)
    shadowed = (tmp_path / "pyproject.toml",)

    data = json.loads(render_json(result, zero_file_note=None, shadowed=shadowed))

    assert data["shadowed_config"] == [str(tmp_path / "pyproject.toml")]


def test_json_reporter_omits_shadowed_config_key_when_nothing_shadowed(tmp_path: Path) -> None:
    config = tmp_path / "house-lint.toml"
    result = ScanResult(tmp_path, config, ("HSL002",), 1, 0)

    data = json.loads(render_json(result, zero_file_note=None, shadowed=()))

    assert "shadowed_config" not in data


def test_json_reporter_includes_zero_file_diagnostic(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)
    note = _zero_file_note(result)

    data = json.loads(render_json(result, zero_file_note=note))

    assert data["zero_file_diagnostic"] == note
    assert data["zero_file_diagnostic"].startswith("empty scan: no Python files selected")
    assert "no config file found" in data["zero_file_diagnostic"]


def test_json_reporter_zero_file_diagnostic_unadorned_when_suppressed(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL001", "HSL900"), 0, 0)
    note = _zero_file_note(result, explicit_paths=True)

    data = json.loads(render_json(result, zero_file_note=note))

    assert data["zero_file_diagnostic"] == "empty scan: no Python files selected"


def test_json_reporter_omits_zero_file_diagnostic_when_files_were_scanned(tmp_path: Path) -> None:
    result = ScanResult(tmp_path, None, ("HSL002",), 1, 0)

    data = json.loads(render_json(result, zero_file_note=None))

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

    rendered_json = render_json(result, zero_file_note=None)
    assert "café" not in rendered_json
    assert "\\u00e9" in rendered_json
    assert "café" in render_text(result, zero_file_note=None)


def test_zero_file_guidance_names_a_custom_config_file(tmp_path: Path) -> None:
    config = tmp_path / "custom.toml"
    result = ScanResult(tmp_path, config, ("HSL001", "HSL900"), 0, 0)

    guidance = zero_file_guidance(result, include=DEFAULT_INCLUDE, explicit_paths=False)

    assert "custom.toml's [tool.house-lint] table" in guidance
    assert "pyproject.toml" not in guidance
