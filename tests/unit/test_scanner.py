import pytest

from house_lint import scanner
from house_lint.config import LintConfig, selected_detector_inputs
from house_lint.source import SourceFile


def _scan(path, enabled_rules: tuple[str, ...]) -> scanner.FileScanResult:
    source = SourceFile(path, path.parent)
    source.load()
    return scanner.scan_source(
        source,
        enabled_rules=enabled_rules,
        detector_inputs=selected_detector_inputs(LintConfig(), enabled_rules=enabled_rules),
        debug=False,
    )


def test_scan_runs_each_selected_rule_and_no_other(write_sample) -> None:
    path = write_sample("# ======\ndef example():\n    import thing\n")

    result = _scan(path, ("HSL002", "HSL900"))

    assert [finding.rule_id for finding in result.findings] == ["HSL002"]


def test_budget_overflow_keeps_earlier_detectors_and_the_overflowing_prefix(
    write_sample, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_sample("# ======\ndef example():\n    import first\n    import second\n")
    monkeypatch.setattr(scanner, "MAX_CANDIDATES_PER_FILE", 2)

    result = _scan(path, ("HSL001", "HSL002", "HSL900"))

    assert [finding.rule_id for finding in result.findings] == ["HSL001", "HSL002"]
    assert [error.operation for error in result.errors] == ["candidate-count"]
