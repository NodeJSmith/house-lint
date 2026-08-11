from house_lint.config import (
    DEFAULT_SELECT,
    ORDINARY_RULES,
    HSL102Options,
    LintConfig,
    selected_detector_inputs,
)
from house_lint.registry import detect_candidates, is_known_rule, rule_ids, rule_metadata
from house_lint.source import SourceFile


def test_registry_has_fixed_metadata_and_explicit_dispatch(write_sample) -> None:
    path = write_sample("def example():\n    import thing\n")
    source = SourceFile(path, path.parent)

    assert rule_ids() == (
        "HSL001",
        "HSL002",
        "HSL003",
        "HSL004",
        "HSL101",
        "HSL102",
        "HSL103",
        "HSL900",
    )
    assert rule_metadata("HSL900").enablement == "always"
    assert is_known_rule("HSL001")
    assert not is_known_rule("HSL999")
    assert DEFAULT_SELECT == ("HSL001", "HSL002", "HSL003", "HSL004")
    assert ORDINARY_RULES == set(rule_ids()) - {"HSL900"}
    assert [candidate.rule_id for candidate in detect_candidates(source, selected_detector_inputs(LintConfig()))] == [
        "HSL002"
    ]


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
