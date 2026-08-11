import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from house_lint import cli


def _run(
    root: Path, *args: str, module: bool = False, prelude: str | None = None
) -> subprocess.CompletedProcess[str]:
    command = (
        [sys.executable, "-c", prelude]
        if prelude is not None
        else [sys.executable, "-m", "house_lint"]
        if module
        else [str(shutil.which("house-lint"))]
    )
    environment = os.environ | {"PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    return subprocess.run(
        command + list(args), cwd=root, env=environment, text=True, capture_output=True, check=False
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "clean.py").write_text("value = 1\n")
    return tmp_path


def test_console_and_module_have_equivalent_rule_listing(repository: Path) -> None:
    console = _run(repository, "rules", "--format", "json")
    module = _run(repository, "rules", "--format", "json", module=True)

    assert console.returncode == module.returncode == 0
    assert console.stderr == module.stderr == ""
    assert json.loads(console.stdout) == json.loads(module.stdout)
    assert [item["id"] for item in json.loads(console.stdout)["rules"]] == [
        "HSL001",
        "HSL002",
        "HSL003",
        "HSL004",
        "HSL101",
        "HSL102",
        "HSL103",
        "HSL900",
    ]

    text = _run(repository, "rules")
    assert text.returncode == 0
    assert text.stderr == ""
    assert [line.split()[0] for line in text.stdout.splitlines()] == [
        "HSL001",
        "HSL002",
        "HSL003",
        "HSL004",
        "HSL101",
        "HSL102",
        "HSL103",
        "HSL900",
    ]
    assert "[default]" in text.stdout
    assert "[opt-in]" in text.stdout
    assert "[always]" in text.stdout


def test_clean_check_is_equivalent_and_json_is_parseable(repository: Path) -> None:
    console = _run(repository, "check", "--root", str(repository), "--format", "json")
    module = _run(repository, "check", "--root", str(repository), "--format", "json", module=True)

    assert console.returncode == module.returncode == 0
    assert console.stderr == module.stderr == ""
    assert json.loads(console.stdout) == json.loads(module.stdout)
    assert json.loads(console.stdout)["summary"] == {
        "finding_count": 0,
        "error_count": 0,
        "suppressed_count": 0,
    }

    text = _run(repository, "check", "--root", str(repository))
    assert text.returncode == 0
    assert text.stderr == ""
    assert text.stdout.splitlines() == [
        f"root: {repository.resolve()}",
        "config: <none>",
        "enabled rules: HSL001, HSL002, HSL003, HSL004, HSL900",
        "files: scanned 1, skipped 0",
        "summary: 0 findings, 0 errors, 0 suppressed",
    ]


def test_check_selects_repeatable_comma_separated_rule_ids(repository: Path) -> None:
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001,HSL002",
        "--select",
        "HSL003",
        "--ignore",
        "HSL001,HSL003",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["enabled_rules"] == ["HSL002", "HSL900"]
    assert [finding["rule_id"] for finding in result["findings"]] == ["HSL002"]


@pytest.mark.parametrize(
    ("option", "value"),
    [("--select", "HSL001,"), ("--select", "HSL001,,HSL002"), ("--ignore", " ")],
)
def test_empty_cli_rule_id_elements_are_usage_errors(repository: Path, option: str, value: str) -> None:
    completed = _run(repository, "check", "--root", str(repository), option, value)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "unknown or forbidden rule ID" in completed.stderr


def test_config_and_syntax_errors_have_documented_stream_ownership(repository: Path) -> None:
    bad_config = repository / "bad.toml"
    bad_config.write_text("[tool.house-lint\n")
    config_failure = _run(repository, "check", "--config", str(bad_config), "--format", "json")
    assert config_failure.returncode == 2
    assert config_failure.stderr == ""
    assert json.loads(config_failure.stdout)["files_scanned"] == 0

    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    (repository / "src" / "broken.py").write_text("def broken()\n    pass\n")
    incomplete = _run(repository, "check", "--root", str(repository), "--format", "json")
    result = json.loads(incomplete.stdout)
    assert incomplete.returncode == 3
    assert incomplete.stderr == ""
    assert result["errors"][0]["kind"] == "syntax"

    text_incomplete = _run(repository, "check", "--root", str(repository), "--format", "text")
    assert text_incomplete.returncode == 3
    assert "src/finding.py:2:5: HSL002 import inside function body" in text_incomplete.stdout
    assert "summary:" in text_incomplete.stdout
    assert "error: src/broken.py: [syntax-error analysis/ast-parse]" in text_incomplete.stderr


def test_json_parser_usage_error_has_a_schema_result(repository: Path) -> None:
    completed = _run(repository, "check", "--format", "json", "--root")

    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert completed.stderr == ""
    assert result["root"] is None
    assert result["config"] is None
    assert result["files_scanned"] == result["files_skipped"] == 0
    assert result["findings"] == []
    assert result["errors"][0]["kind"] == "config"


def test_debug_operational_details_stay_on_stderr_for_json_output(repository: Path) -> None:
    (repository / "src" / "broken.py").write_text("def broken()\n    pass\n")

    completed = _run(
        repository, "check", "--root", str(repository), "--format", "json", "--debug"
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "syntax"
    assert "debug: syntax error during analysis/ast-parse:" in completed.stderr
    assert "SyntaxError:" in completed.stderr
    assert "def broken()" in completed.stderr


def test_invalid_check_format_writes_only_a_usage_diagnostic_to_stderr(repository: Path) -> None:
    completed = _run(repository, "check", "--root", str(repository), "--format", "xml")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "error: [config-error config/load] --format must be text or json\n"


def test_json_config_error_preserves_resolved_root_and_config(repository: Path) -> None:
    bad_config = repository / "bad.toml"
    bad_config.write_text("[tool.house-lint\n")

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--config",
        str(bad_config),
        "--format",
        "json",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert completed.stderr == ""
    assert result["root"] == str(repository.resolve())
    assert result["config"] == str(bad_config.resolve())
    assert result["files_scanned"] == result["files_skipped"] == 0
    assert result["findings"] == []
    assert result["errors"][0]["kind"] == "config"


def test_json_missing_explicit_config_preserves_resolved_root_and_config(repository: Path) -> None:
    missing_config = repository / "missing.toml"

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--config",
        str(missing_config),
        "--format",
        "json",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert result["root"] == str(repository.resolve())
    assert result["config"] == str(missing_config.resolve())
    assert result["files_scanned"] == result["files_skipped"] == 0


def test_source_checkout_module_entry_point_does_not_require_distribution_metadata(
    repository: Path,
) -> None:
    prelude = """
import importlib.metadata
import sys
import runpy

def missing_distribution(_: str) -> str:
    raise importlib.metadata.PackageNotFoundError

importlib.metadata.version = missing_distribution
sys.argv = ["house-lint", "rules", "--format", "json"]
runpy.run_module("house_lint", run_name="__main__")
"""

    completed = _run(repository, prelude=prelude)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["schema_version"] == 1


def test_candidate_budget_is_an_incomplete_subprocess_result(repository: Path) -> None:
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    prelude = """
from house_lint import cli

cli.MAX_CANDIDATES_PER_FILE = 0
cli.main()
"""

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        prelude=prelude,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "budget"
    assert result["files_scanned"] == 2


def test_hsl001_stops_at_the_candidate_budget(repository: Path) -> None:
    (repository / "src" / "overflow.py").write_text("\n".join("# utilize this" for _ in range(10_002)))

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "budget"
    assert len(result["findings"]) == 10_000
    assert {finding["rule_id"] for finding in result["findings"]} == {"HSL001"}


def test_suppression_diagnostics_respect_the_candidate_budget(repository: Path) -> None:
    (repository / "src" / "pragma.py").write_text("# house-lint: ignore[] - generated module\n")
    prelude = """
from house_lint import cli

cli.MAX_CANDIDATES_PER_FILE = 0
cli.main()
"""

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        prelude=prelude,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "budget"
    assert result["findings"] == []


def test_suppression_budget_preserves_the_bounded_candidate_prefix(repository: Path) -> None:
    (repository / "src" / "overflow.py").write_text(
        "\n".join("# utilize this" for _ in range(10_000))
        + "\n# house-lint: ignore[] - generated module\n"
    )

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "budget"
    assert len(result["findings"]) == 10_000
    assert {finding["rule_id"] for finding in result["findings"]} == {"HSL001"}


def test_detector_and_suppression_budget_preserve_the_bounded_candidate_prefix(repository: Path) -> None:
    (repository / "src" / "overflow.py").write_text(
        "\n".join("# utilize this" for _ in range(10_001))
        + "\n# house-lint: ignore[] - generated module\n"
    )

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "budget"
    assert len(result["findings"]) == 10_000
    assert {finding["rule_id"] for finding in result["findings"]} == {"HSL001"}


def test_budget_error_preserves_findings_from_completed_files(repository: Path) -> None:
    first = repository / "src" / "a.py"
    overflow = repository / "src" / "overflow.py"
    first.write_text("def example():\n    import module\n")
    overflow.write_text("\n".join("# utilize this" for _ in range(10_001)))

    completed = _run(
        repository,
        "check",
        str(first),
        str(overflow),
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001,HSL002",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert any(
        finding["path"] == "src/a.py" and finding["rule_id"] == "HSL002"
        for finding in result["findings"]
    )


def test_zero_capacity_detector_overflow_applies_known_suppressions(repository: Path) -> None:
    (repository / "src" / "overflow.py").write_text(
        "# house-lint: ignore-file[HSL001] - generated module\n"
        + "\n".join("# utilize this" for _ in range(10_000))
        + "\ndef example():\n    import package  # house-lint: ignore[HSL002] - lazy dependency\n"
    )

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001,HSL002",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["findings"] == []
    assert result["summary"]["suppressed_count"] == 10_000


def test_suppression_budget_applies_completed_suppressions(repository: Path) -> None:
    (repository / "src" / "overflow.py").write_text(
        "# house-lint: ignore-file[HSL001] - generated module\n"
        + "\n".join("# utilize this" for _ in range(9_999))
        + "\n# house-lint: ignore[] - generated module\n"
        + "# house-lint: ignore[] - generated module\n"
    )

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert [finding["rule_id"] for finding in result["findings"]] == ["HSL900"]
    assert result["summary"]["suppressed_count"] == 9_999


def test_budget_error_counts_the_file_when_rule_execution_begins(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = repository / "src" / "finding.py"
    source.write_text("def example():\n    import module\n")
    monkeypatch.setattr(cli, "MAX_CANDIDATES_PER_FILE", 0)

    code = cli.check(paths=[source], root=repository, format="json")
    result = json.loads(capsys.readouterr().out)

    assert code == 3
    assert result["files_scanned"] == 1


def test_subprocess_internal_failure_exits_four_with_parseable_json(repository: Path) -> None:
    prelude = """
from house_lint import cli

def fail(*_args: object) -> object:
    raise RuntimeError("simulated failure")

cli.detect_candidates = fail
cli.main()
"""

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--debug",
        prelude=prelude,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 4
    assert result["errors"][0]["kind"] == "internal"
    assert "Traceback" in completed.stderr


def test_subprocess_internal_error_precedes_incomplete_scan_and_preserves_findings(repository: Path) -> None:
    first = repository / "src" / "a.py"
    broken = repository / "src" / "b.py"
    failing = repository / "src" / "c.py"
    first.write_text("def example():\n    import module\n")
    broken.write_text("def broken()\n    pass\n")
    failing.write_text("value = 1\n")
    prelude = """
from house_lint import cli

original = cli.detect_candidates

def fail_c(source, detector_inputs, **kwargs):
    if source.relative_path == "src/c.py":
        raise RuntimeError("simulated failure")
    return original(source, detector_inputs, **kwargs)

cli.detect_candidates = fail_c
cli.main()
"""

    completed = _run(
        repository,
        "check",
        str(first),
        str(broken),
        str(failing),
        "--root",
        str(repository),
        "--format",
        "json",
        prelude=prelude,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 4
    assert [finding["path"] for finding in result["findings"]] == ["src/a.py"]
    assert {error["kind"] for error in result["errors"]} == {"syntax", "internal"}
    assert completed.stderr == ""


def test_internal_error_preserves_completed_results_and_writes_debug_to_stderr(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = repository / "src" / "a.py"
    second = repository / "src" / "b.py"
    first.write_text("def example():\n    import module\n")
    second.write_text("value = 1\n")
    original = cli.detect_candidates

    def fail_second(source: cli.SourceFile, detector_inputs: object, **kwargs: object) -> object:
        if source.relative_path == "src/b.py":
            raise RuntimeError("simulated failure")
        return original(source, detector_inputs, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cli, "detect_candidates", fail_second)

    code = cli.check(paths=[first, second], root=repository, format="json", debug=True)
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert code == 4
    assert result["findings"][0]["path"] == "src/a.py"
    assert result["errors"][0]["kind"] == "internal"
    assert result["errors"][0]["message"] == "an unexpected internal error occurred"
    assert "simulated failure" not in json.dumps(result)
    assert "Traceback" in captured.err
    assert "simulated failure" in captured.err
    assert "internal error during analysis/rule-dispatch" in captured.err
    assert result["files_scanned"] == 2


def test_source_construction_failure_preserves_completed_results(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = repository / "src" / "a.py"
    second = repository / "src" / "b.py"
    first.write_text("def example():\n    import module\n")
    second.write_text("value = 1\n")
    source_file = cli.SourceFile

    def fail_second(path: Path, root: Path) -> cli.SourceFile:
        if path == second:
            raise RuntimeError("simulated construction failure")
        return source_file(path, root)

    monkeypatch.setattr(cli, "SourceFile", fail_second)

    code = cli.check(paths=[first, second], root=repository, format="json")
    result = json.loads(capsys.readouterr().out)

    assert code == 4
    assert [finding["path"] for finding in result["findings"]] == ["src/a.py"]
    assert result["files_scanned"] == 1
    assert result["errors"][0]["operation"] == "source-load"


def test_cli_boundary_internal_error_preserves_resolved_context(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_scan(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("simulated scan setup failure")

    monkeypatch.setattr(cli, "_scan", fail_scan)

    code = cli.check(root=repository, format="json")
    result = json.loads(capsys.readouterr().out)

    assert code == 4
    assert result["root"] == str(repository.resolve())
    assert result["config"] is None
