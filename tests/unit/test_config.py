from pathlib import Path

import pytest

from house_lint.config import ConfigError, default_config, get_house_lint_table, load_config


def test_defaults_and_cli_selection_precedence(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        '[tool.house-lint]\nselect = ["HSL001", "HSL002"]\nignore = ["HSL002"]\n'
    )

    config = load_config(config_path, cli_select=("HSL003",), cli_ignore=("HSL003",))

    assert config.enabled_rules == ("HSL900",)
    assert config.include == ("src", "tests", "scripts", "tools", "examples")


def test_extend_select_adds_to_configured_select_without_replacing_it(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text('[tool.house-lint]\nselect = ["HSL001"]\nextend-select = ["HSL002"]\n')

    config = load_config(config_path)

    assert config.enabled_rules == ("HSL001", "HSL002", "HSL900")


def test_cli_extend_select_adds_one_rule_without_losing_the_rest_of_select(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text('[tool.house-lint]\nselect = ["HSL001", "HSL002"]\n')

    config = load_config(config_path, cli_extend_select=("HSL003",))

    assert config.enabled_rules == ("HSL001", "HSL002", "HSL003", "HSL900")


def test_extend_select_also_layers_on_top_of_a_cli_select_override(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text('[tool.house-lint]\nselect = ["HSL001"]\nextend-select = ["HSL002"]\n')

    config = load_config(config_path, cli_select=("HSL003",))

    assert config.enabled_rules == ("HSL002", "HSL003", "HSL900")


def test_extend_ignore_subtracts_from_extend_select_and_configured_select(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        '[tool.house-lint]\nselect = ["HSL001", "HSL002"]\n'
        'extend-select = ["HSL003"]\nextend-ignore = ["HSL002", "HSL003"]\n'
    )

    config = load_config(config_path)

    assert config.enabled_rules == ("HSL001", "HSL900")


def test_cli_ignore_always_wins_over_extend_select(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text('[tool.house-lint]\nselect = ["HSL001"]\n')

    config = load_config(config_path, cli_extend_select=("HSL002",), cli_ignore=("HSL002",))

    assert config.enabled_rules == ("HSL001", "HSL900")


def test_default_config_extend_select_layers_on_top_of_default_select() -> None:
    assert default_config(cli_extend_select=("HSL102",)).enabled_rules == (
        "HSL001",
        "HSL002",
        "HSL003",
        "HSL004",
        "HSL102",
        "HSL900",
    )


def test_default_config_extend_select_still_enforces_hsl101_token_requirement() -> None:
    with pytest.raises(ConfigError, match="HSL101 requires tokens"):
        default_config(cli_extend_select=("HSL101",))


def test_extend_select_and_extend_ignore_reject_duplicate_and_always_on_ids(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text('[tool.house-lint]\nextend-select = ["HSL001", "HSL001"]\n')
    with pytest.raises(ConfigError, match="duplicate rule IDs"):
        load_config(config_path)

    config_path.write_text('[tool.house-lint]\nextend-ignore = ["HSL900"]\n')
    with pytest.raises(ConfigError, match="unknown or forbidden rule ID"):
        load_config(config_path)


def test_default_config_uses_the_shared_selection_precedence() -> None:
    assert default_config(
        cli_select=("HSL002", "HSL003"), cli_ignore=("HSL003",)
    ).enabled_rules == (
        "HSL002",
        "HSL900",
    )


def test_default_config_rejects_cli_hsl101_without_tokens() -> None:
    with pytest.raises(ConfigError, match="HSL101 requires tokens"):
        default_config(cli_select=("HSL101",))


def test_get_house_lint_table_detects_only_a_valid_house_lint_table() -> None:
    table = {"select": ["HSL001"]}

    assert get_house_lint_table({"tool": {"house-lint": table}}) is table
    assert get_house_lint_table({"tool": {"house-lint": []}}) is None
    assert get_house_lint_table({"tool": []}) is None


def test_selection_omission_empty_and_cli_precedence(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text("[tool.house-lint]\n")
    assert load_config(path).enabled_rules == ("HSL001", "HSL002", "HSL003", "HSL004", "HSL900")

    path.write_text("[tool.house-lint]\nselect = []\nignore = []\n")
    assert load_config(path).enabled_rules == ("HSL900",)

    path.write_text('[tool.house-lint]\nselect = ["HSL001"]\nignore = ["HSL001"]\n')
    config = load_config(path, cli_select=("HSL002", "HSL003"), cli_ignore=("HSL003",))
    assert config.enabled_rules == ("HSL002", "HSL900")


@pytest.mark.parametrize(
    "key, values",
    [("select", '["HSL001", "HSL001"]'), ("ignore", '["HSL900"]')],
)
def test_rule_selection_rejects_duplicate_and_always_on_ids(
    tmp_path: Path, key: str, values: str
) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(f"[tool.house-lint]\n{key} = {values}\n")

    with pytest.raises(ConfigError, match="rule ID"):
        load_config(path)


def test_hsl101_requires_tokens_only_when_selected(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.house-lint]\nselect = ["HSL001"]\n[tool.house-lint.rules.HSL101]\n')
    assert load_config(path).enabled_rules == ("HSL001", "HSL900")

    path.write_text('[tool.house-lint]\nselect = ["HSL101"]\n[tool.house-lint.rules.HSL101]\n')
    with pytest.raises(ConfigError, match="tokens"):
        load_config(path)


def test_invalid_disabled_rule_table_is_still_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[tool.house-lint]\nselect = ["HSL001"]\n'
        "[tool.house-lint.rules.HSL102]\nmax_lines = false\n"
    )
    with pytest.raises(ConfigError, match="max_lines"):
        load_config(path)


def test_unknown_keys_and_bad_include_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.house-lint]\nunknown = true\ninclude = ["../other"]\n')
    with pytest.raises(ConfigError, match="unknown"):
        load_config(path)


@pytest.mark.parametrize(
    "table",
    [
        "[tool.house-lint.rules.HSL101]\nunknown = true\n",
        "[tool.house-lint.rules.HSL102]\nunknown = true\n",
        "[tool.house-lint.rules.HSL103]\nunknown = true\n",
        '[[tool.house-lint.rules.HSL101.tokens]]\nprefixes = ["AC"]\nscopes = ["comments"]\nunknown = true\n',
    ],
)
def test_rule_tables_and_token_families_reject_unknown_keys(tmp_path: Path, table: str) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.house-lint]\nselect = ["HSL001"]\n' + table)

    with pytest.raises(ConfigError, match="unknown"):
        load_config(path)


def test_include_rejects_glob_metacharacters(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.house-lint]\ninclude = ["src/**/*.py"]\n')

    with pytest.raises(ConfigError, match="literal"):
        load_config(path)


def test_exclude_rejects_invalid_git_pattern(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.house-lint]\nexclude = ["\\\\"]\n')

    with pytest.raises(ConfigError, match="invalid Git-ignore"):
        load_config(path)


def test_exclude_rejects_parent_traversal(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.house-lint]\nexclude = ["../generated/"]\n')

    with pytest.raises(ConfigError, match="root-relative"):
        load_config(path)


def test_exclude_rejects_absolute_pattern(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.house-lint]\nexclude = ["/generated/"]\n')

    with pytest.raises(ConfigError, match="root-relative"):
        load_config(path)


def test_token_family_is_typed_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[tool.house-lint]\nselect = ["HSL101"]\n'
        "[[tool.house-lint.rules.HSL101.tokens]]\n"
        'prefixes = ["AC"]\nscopes = ["comments"]\nhash = "optional"\n'
    )
    config = load_config(path)
    assert config.hsl101.tokens[0].prefixes == ("AC",)
    assert config.hsl101.tokens[0].hash == "optional"
