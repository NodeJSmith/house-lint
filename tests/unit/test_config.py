from pathlib import Path

import pytest

from house_lint.config import (
    BUILTIN_KNOWN_ISSUES,
    BUILTIN_SPEC,
    BUILTIN_TASK,
    BUILTIN_TOKEN_FAMILIES,
    MAX_TOKEN_FAMILIES,
    ConfigError,
    compile_per_file_ignores,
    default_config,
    get_house_lint_table,
    load_config,
    per_file_enabled_rules,
)


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


def test_default_config_extend_select_hsl101_succeeds_with_builtin_tokens() -> None:
    config = default_config(cli_extend_select=("HSL101",))

    assert config.hsl101.tokens == BUILTIN_TOKEN_FAMILIES


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


@pytest.mark.parametrize("key", ["select", "ignore", "extend-select", "extend-ignore"])
@pytest.mark.parametrize("value", ["5", '"HSL001"'])
def test_selection_keys_reject_a_non_array_value_as_a_config_error(
    key: str, value: str, tmp_path: Path
) -> None:
    """`_effective_rule_selection` converts with `list(...)` before validating, so a raw TOML
    value reaching it unchecked turns `select = 5` into a `TypeError` — an internal-error exit
    rather than the documented config-error one — and splits `select = "HSL001"` into single
    characters, reported as an unknown rule ID instead of a type problem."""
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(f"[tool.house-lint]\n{key} = {value}\n")

    with pytest.raises(ConfigError, match=f"{key} must be an array"):
        load_config(config_path)


def test_per_file_ignores_removes_rules_only_for_matching_files(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        '[tool.house-lint]\nselect = ["HSL001", "HSL002"]\n'
        '[tool.house-lint.per-file-ignores]\n"tests/**" = ["HSL002"]\n'
    )

    config = load_config(config_path)

    assert config.per_file_ignores == {"tests/**": ("HSL002",)}
    compiled = compile_per_file_ignores(config.per_file_ignores)
    assert per_file_enabled_rules(config.enabled_rules, compiled, "tests/test_foo.py") == (
        "HSL001",
        "HSL900",
    )
    assert per_file_enabled_rules(config.enabled_rules, compiled, "src/foo.py") == (
        "HSL001",
        "HSL002",
        "HSL900",
    )


def test_per_file_ignores_cannot_target_hsl900_or_repeat_a_rule(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text('[tool.house-lint.per-file-ignores]\n"tests/**" = ["HSL900"]\n')
    with pytest.raises(ConfigError, match="unknown or forbidden rule ID"):
        load_config(config_path)

    config_path.write_text(
        '[tool.house-lint.per-file-ignores]\n"tests/**" = ["HSL001", "HSL001"]\n'
    )
    with pytest.raises(ConfigError, match="duplicate rule IDs"):
        load_config(config_path)


@pytest.mark.parametrize(
    "table",
    [
        '[tool.house-lint.per-file-ignores]\n"../outside" = ["HSL001"]\n',
        '[tool.house-lint.per-file-ignores]\n"/absolute" = ["HSL001"]\n',
        '[tool.house-lint.per-file-ignores]\n"" = ["HSL001"]\n',
    ],
)
def test_per_file_ignores_rejects_non_root_relative_or_empty_patterns(
    tmp_path: Path, table: str
) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(table)
    with pytest.raises(ConfigError, match="root-relative|non-empty"):
        load_config(config_path)


def test_per_file_ignores_rejects_negated_patterns(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text('[tool.house-lint.per-file-ignores]\n"!tests/**" = ["HSL001"]\n')
    with pytest.raises(ConfigError, match="must not be negated patterns"):
        load_config(config_path)


def test_per_file_ignores_rejects_non_array_values(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text('[tool.house-lint.per-file-ignores]\n"tests/**" = "HSL001"\n')
    with pytest.raises(ConfigError, match="must be an array"):
        load_config(config_path)


def test_per_file_ignores_rejects_invalid_gitignore_pattern(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text('[tool.house-lint.per-file-ignores]\n"\\\\" = ["HSL001"]\n')
    with pytest.raises(ConfigError, match="invalid Git-ignore"):
        load_config(config_path)


def test_default_config_has_no_per_file_ignores() -> None:
    assert default_config().per_file_ignores == {}


def test_default_config_uses_the_shared_selection_precedence() -> None:
    assert default_config(
        cli_select=("HSL002", "HSL003"), cli_ignore=("HSL003",)
    ).enabled_rules == (
        "HSL002",
        "HSL900",
    )


def test_default_config_cli_hsl101_without_tokens_succeeds_with_builtin_tokens() -> None:
    config = default_config(cli_select=("HSL101",))

    assert config.hsl101.tokens == BUILTIN_TOKEN_FAMILIES


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


def test_hsl101_succeeds_with_builtin_tokens_whether_or_not_selected(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.house-lint]\nselect = ["HSL001"]\n[tool.house-lint.rules.HSL101]\n')
    assert load_config(path).enabled_rules == ("HSL001", "HSL900")

    path.write_text('[tool.house-lint]\nselect = ["HSL101"]\n[tool.house-lint.rules.HSL101]\n')
    config = load_config(path)
    assert config.hsl101.tokens == BUILTIN_TOKEN_FAMILIES


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
        'prefixes = ["AC"]\nscopes = ["comments"]\nseparator = "hash-optional"\n'
    )
    config = load_config(path)
    assert config.hsl101.tokens[3].prefixes == ("AC",)
    assert config.hsl101.tokens[3].separator == "hash-optional"


def test_builtin_token_families_have_expected_shape() -> None:
    assert BUILTIN_SPEC.prefixes == ("AC", "FR", "NFR", "WP")
    assert BUILTIN_SPEC.scopes == ("comments", "docstrings", "filenames")
    assert BUILTIN_SPEC.separator == "hash-optional"
    assert BUILTIN_SPEC.suffix == "optional-lower-alpha"
    assert BUILTIN_SPEC.not_followed_by_time is False

    assert BUILTIN_TASK.prefixes == ("T",)
    assert BUILTIN_TASK.scopes == ("comments", "docstrings", "filenames")
    assert BUILTIN_TASK.separator == "hash-optional"
    assert BUILTIN_TASK.suffix == "optional-lower-alpha"
    assert BUILTIN_TASK.not_followed_by_time is True

    assert BUILTIN_KNOWN_ISSUES.prefixes == ("KI",)
    assert BUILTIN_KNOWN_ISSUES.scopes == ("comments", "docstrings", "filenames")
    assert BUILTIN_KNOWN_ISSUES.separator == "dash"
    assert BUILTIN_KNOWN_ISSUES.suffix == "none"

    assert BUILTIN_TOKEN_FAMILIES == (BUILTIN_SPEC, BUILTIN_TASK, BUILTIN_KNOWN_ISSUES)


def test_hsl101_selected_with_no_user_tokens_produces_builtin_families(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.house-lint]\nselect = ["HSL101"]\n')

    config = load_config(path)

    assert config.hsl101.tokens == BUILTIN_TOKEN_FAMILIES


def test_hsl101_user_tokens_union_with_builtins(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[tool.house-lint]\nselect = ["HSL101"]\n'
        "[[tool.house-lint.rules.HSL101.tokens]]\n"
        'prefixes = ["JIRA"]\nscopes = ["comments"]\nseparator = "dash"\nmin_digits = 1\n'
    )

    config = load_config(path)

    assert config.hsl101.tokens[:3] == BUILTIN_TOKEN_FAMILIES
    assert config.hsl101.tokens[3].prefixes == ("JIRA",)
    assert config.hsl101.tokens[3].separator == "dash"
    assert len(config.hsl101.tokens) == 4


def test_hsl101_tokens_exceeding_max_after_builtin_merge_raises_config_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pyproject.toml"
    # 3 built-ins + (MAX_TOKEN_FAMILIES - 2) user families = MAX_TOKEN_FAMILIES + 1, one over the
    # limit enforced after the built-in merge.
    user_families = "\n".join(
        f'[[tool.house-lint.rules.HSL101.tokens]]\nprefixes = ["Z{i}"]\nscopes = ["comments"]\n'
        for i in range(MAX_TOKEN_FAMILIES - 2)
    )
    path.write_text(f'[tool.house-lint]\nselect = ["HSL101"]\n{user_families}')

    with pytest.raises(ConfigError, match="must not exceed"):
        load_config(path)


def test_token_family_separator_rejects_unknown_value(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[tool.house-lint]\nselect = ["HSL101"]\n'
        "[[tool.house-lint.rules.HSL101.tokens]]\n"
        'prefixes = ["AC"]\nscopes = ["comments"]\nseparator = "invalid"\n'
    )

    with pytest.raises(ConfigError, match="separator is invalid"):
        load_config(path)
