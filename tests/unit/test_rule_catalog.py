from house_lint.rule_catalog import (
    DEFAULT_SELECT,
    ORDINARY_RULES,
    is_known_rule,
    rule_ids,
    rule_metadata,
)


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
