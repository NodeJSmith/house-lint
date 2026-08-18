from house_lint import registry, rule_catalog
from house_lint.config import HSL102Options, LintConfig, selected_detector_inputs
from house_lint.registry import detect_candidates
from house_lint.source import SourceFile


def test_registry_dispatches_selected_rules_via_detect_candidates(write_sample) -> None:
    path = write_sample("def example():\n    import thing\n")
    source = SourceFile(path, path.parent)

    assert [
        candidate.rule_id
        for candidate in detect_candidates(source, selected_detector_inputs(LintConfig()))
    ] == ["HSL002"]


def test_dispatch_receives_selected_typed_options_without_lint_config(write_sample) -> None:
    path = write_sample("first\nsecond\n")
    source = SourceFile(path, path.parent)
    options = HSL102Options(max_lines=1)

    detector_inputs = selected_detector_inputs(
        LintConfig(enabled_rules=("HSL102", "HSL900"), hsl102=options)
    )
    findings = detect_candidates(source, detector_inputs)

    assert detector_inputs == (("HSL102", options),)
    assert [finding.rule_id for finding in findings] == ["HSL102"]


def test_detectors_cover_every_ordinary_rule() -> None:
    assert set(registry._DETECTORS) == set(rule_catalog.ORDINARY_RULES)
