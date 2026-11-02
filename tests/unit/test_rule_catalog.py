from house_lint.config import LintConfig, selected_detector_inputs
from house_lint.rule_catalog import (
    DEFAULT_SELECT,
    FILE_RULES,
    ORDINARY_RULES,
    is_known_rule,
    rule_ids,
    rule_metadata,
)
from house_lint.rules.file_length import HSL102Options
from house_lint.source import SourceFile


def test_rule_catalog_has_fixed_metadata_and_derived_selections() -> None:
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


def test_every_ordinary_rule_runs_on_files() -> None:
    assert set(FILE_RULES) == ORDINARY_RULES


def test_only_hsl101_to_hsl103_take_options() -> None:
    assert {rule.id for rule in FILE_RULES.values() if rule.parse_options} == {
        "HSL101",
        "HSL102",
        "HSL103",
    }


def test_selected_inputs_pair_each_enabled_file_rule_with_its_options(write_sample) -> None:
    options = HSL102Options(max_lines=1)
    config = LintConfig(
        enabled_rules=("HSL102", "HSL900"),
        rule_options={**LintConfig().rule_options, "HSL102": options},
    )

    inputs = selected_detector_inputs(config)

    assert inputs == ((FILE_RULES["HSL102"], options),)
    path = write_sample("first\nsecond\n")
    [(rule, rule_options)] = inputs
    findings = rule.detect(SourceFile(path, path.parent), rule_options)
    assert [finding.rule_id for finding in findings] == ["HSL102"]
