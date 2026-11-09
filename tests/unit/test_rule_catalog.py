from pathlib import Path

import msgspec
import pytest
from _source_helpers import parse_file

from house_lint.config import LintConfig, selected_detector_inputs
from house_lint.language import Language
from house_lint.project_rule import Location, ProjectCandidate
from house_lint.rule_catalog import (
    DEFAULT_SELECT,
    FILE_RULES,
    ORDINARY_RULES,
    PROJECT_RULES,
    is_known_rule,
    is_project_rule,
    project_rule,
    rule_ids,
    rule_languages,
    rule_metadata,
    rules_by_id,
)
from house_lint.rules.file_length import HSL102Options
from house_lint.source import LoadedFile


def test_rule_catalog_has_fixed_metadata_and_derived_selections() -> None:
    assert rule_ids() == (
        "HSL001",
        "HSL002",
        "HSL003",
        "HSL004",
        "HSL101",
        "HSL102",
        "HSL103",
        "HSL104",
        "HSL900",
    )
    assert rule_metadata("HSL900").enablement == "always"
    assert is_known_rule("HSL001")
    assert not is_known_rule("HSL999")
    assert DEFAULT_SELECT == ("HSL001", "HSL002", "HSL003", "HSL004")
    assert ORDINARY_RULES == set(rule_ids()) - {"HSL900"}


def test_a_repeated_rule_id_is_an_error_naming_it() -> None:
    row = FILE_RULES["HSL001"]

    with pytest.raises(RuntimeError, match="more than one row for HSL001"):
        rules_by_id((row, FILE_RULES["HSL002"], row))


def test_a_rule_id_repeated_across_rule_kinds_is_an_error() -> None:
    row = project_rule(
        "HSL001", "Name", "Description", "opt-in", lambda files, options: [], frozenset()
    )

    with pytest.raises(RuntimeError, match="more than one row for HSL001"):
        rules_by_id((FILE_RULES["HSL001"], row))


def test_hsl104_is_the_only_project_rule_and_every_rule_handles_python() -> None:
    assert list(PROJECT_RULES) == ["HSL104"]
    assert is_project_rule("HSL104")
    assert rule_languages("HSL104") == {Language.PYTHON}
    assert not is_project_rule("HSL001")
    for rule_id in FILE_RULES:
        assert rule_languages(rule_id) == {Language.PYTHON}


def test_the_always_on_rule_declares_no_languages() -> None:
    assert rule_languages("HSL900") == frozenset()
    assert not is_project_rule("HSL900")


def test_a_project_candidate_needs_a_location() -> None:
    with pytest.raises(ValueError, match="at least one location"):
        ProjectCandidate("message", ())


def test_a_location_is_validated_like_a_finding() -> None:
    file = LoadedFile(Path("/root/a.py"), "a.py", b"")

    with pytest.raises(ValueError, match="all null or valid"):
        Location(file, 1, None, 1, 1)
    with pytest.raises(ValueError, match="must not precede"):
        Location(file, 2, 1, 1, 1)


@pytest.mark.parametrize(
    "rule", [rule for rule in FILE_RULES.values() if rule.parse_options], ids=lambda rule: rule.id
)
def test_every_configurable_rule_rejects_an_unknown_option(rule) -> None:
    with pytest.raises(msgspec.ValidationError, match="unknown field"):
        rule.parse_options({"not_an_option": 1})


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
    findings = rule.detect(parse_file(path), rule_options)
    assert [finding.rule_id for finding in findings] == ["HSL102"]
