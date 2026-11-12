"""Strict TOML configuration and project/configuration resolution."""

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import msgspec
from pathspec import GitIgnoreSpec

from house_lint.language import Language
from house_lint.rule_catalog import (
    ALWAYS_ON_RULE_ID,
    DEFAULT_SELECT,
    FILE_RULES,
    ORDINARY_RULES,
    PROJECT_RULES,
    FileRule,
    ProjectRule,
    rule_languages,
)
from house_lint.rules.options import RuleOptions

DEFAULT_INCLUDE = (".",)
# pathspec 1.1.1 compiles a trailing `**/` so it also matches the directory before it, which would
# take in files directly inside `src/` for `src/**/` where gitignore matches only what lies beneath.
# `/**/*/` means the same in gitignore and compiles correctly. `**` must be a whole segment
# (`foo**/` is already right) and an escaped trailing space is left alone. Delete this, and
# `compile_git_ignore_spec`, once the `pathspec` floor in `pyproject.toml` reaches a release with
# the fix.
_TRAILING_DOUBLE_STAR_DIRECTORY = re.compile(r"(?<=[^/])(?:/\*\*)+/ *$")

STANDALONE_CONFIG_NAMES: tuple[str, ...] = ("house-lint.toml", ".house-lint.toml")
PYPROJECT_CONFIG_NAME = "pyproject.toml"


class ConfigError(ValueError):
    """Raised when a configuration cannot be used for a scan."""


@dataclass(frozen=True)
class LintConfig:
    include: tuple[str, ...] = DEFAULT_INCLUDE
    exclude: tuple[str, ...] = ()
    enabled_rules: tuple[str, ...] = (*DEFAULT_SELECT, ALWAYS_ON_RULE_ID)
    # Every rule's resolved options by ID, None for a rule that takes none.
    rule_options: Mapping[str, RuleOptions | None] = field(
        default_factory=lambda: _rule_options({})
    )
    per_file_ignores: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )


DetectorInput = tuple[FileRule[Any], RuleOptions | None]
ProjectRuleInput = tuple[ProjectRule[Any], RuleOptions | None]


def selected_detector_inputs(
    config: LintConfig, *, enabled_rules: tuple[str, ...] | None = None
) -> tuple[DetectorInput, ...]:
    """Return enabled file rules with their already-validated options.

    `enabled_rules` overrides `config.enabled_rules` when given, for callers that resolved a
    per-file effective rule set (see `applicable_rules`) rather than the global one.
    """
    rules = config.enabled_rules if enabled_rules is None else enabled_rules
    return tuple(
        (FILE_RULES[rule_id], config.rule_options[rule_id])
        for rule_id in rules
        if rule_id in FILE_RULES
    )


def selected_project_rules(config: LintConfig) -> tuple[ProjectRuleInput, ...]:
    """Return enabled project rules with their options, in `enabled_rules` order."""
    return tuple(
        (PROJECT_RULES[rule_id], config.rule_options[rule_id])
        for rule_id in config.enabled_rules
        if rule_id in PROJECT_RULES
    )


CompiledPerFileIgnores = tuple[tuple[GitIgnoreSpec, frozenset[str]], ...]


def compile_git_ignore_spec(lines: Iterable[str]) -> GitIgnoreSpec:
    """Compile configured Git-ignore-style patterns, correcting the shape pathspec gets wrong."""
    return GitIgnoreSpec.from_lines(
        _TRAILING_DOUBLE_STAR_DIRECTORY.sub("/**/*/", line) for line in lines
    )


def compile_per_file_ignores(
    per_file_ignores: Mapping[str, tuple[str, ...]],
) -> CompiledPerFileIgnores:
    """Precompile per-file-ignores glob patterns once, for repeated per-file matching."""
    return tuple(
        (compile_git_ignore_spec((pattern,)), frozenset(rule_ids))
        for pattern, rule_ids in per_file_ignores.items()
    )


def applicable_rules(
    base_enabled_rules: tuple[str, ...],
    compiled_per_file_ignores: CompiledPerFileIgnores,
    relative_path: str,
    language: Language,
) -> tuple[str, ...]:
    """Return the rules in `base_enabled_rules` that apply to this one file.

    A rule drops out when a per-file-ignores pattern matches this root-relative path, or when it
    doesn't handle the file's language. The always-on rule validates pragmas rather than handling
    a language, and per-file-ignores can't name it, so it stays.

    Detector dispatch, the cache key, each project rule's input, and the suppression rules all read
    this result. Discovery runs before there are files to ask about, so it reads
    `discovered_languages` instead.
    """
    ignored: set[str] = set()
    for spec, rule_ids in compiled_per_file_ignores:
        if spec.match_file(relative_path):
            ignored |= rule_ids
    return tuple(
        rule_id
        for rule_id in base_enabled_rules
        if rule_id not in ignored
        and (rule_id == ALWAYS_ON_RULE_ID or language in rule_languages(rule_id))
    )


def discovered_languages(enabled_rules: tuple[str, ...]) -> frozenset[Language]:
    """Return the languages to discover: those the enabled rules handle.

    This reads the same `languages` field as `applicable_rules`, without its per-file-ignores: a
    file every rule ignores is still scanned, counted, and has its pragmas validated.
    """
    return frozenset(language for rule_id in enabled_rules for language in rule_languages(rule_id))


def default_config(
    *,
    cli_select: Iterable[str] | None = None,
    cli_ignore: Iterable[str] | None = None,
    cli_extend_select: Iterable[str] | None = None,
    cli_extend_ignore: Iterable[str] | None = None,
) -> LintConfig:
    """Build built-in configuration with the same CLI selection semantics as TOML."""
    enabled_rules = _effective_rule_selection(
        DEFAULT_SELECT,
        (),
        cli_select,
        cli_ignore,
        cli_extend_select=cli_extend_select,
        cli_extend_ignore=cli_extend_ignore,
    )
    return LintConfig(enabled_rules=enabled_rules)


def _table(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a table")
    return cast(dict[str, Any], value)


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be an array")
    return cast(list[Any], value)


def _strings(value: Any, name: str) -> tuple[str, ...]:
    values = _array(value, name)
    if any(not isinstance(item, str) for item in values):
        raise ConfigError(f"{name} must contain strings")
    return tuple(values)


def _ids(value: Any, name: str) -> tuple[str, ...]:
    values = _strings(value, name)
    if len(set(values)) != len(values):
        raise ConfigError(f"{name} contains duplicate rule IDs")
    if any(item not in ORDINARY_RULES for item in values):
        raise ConfigError(f"{name} contains unknown or forbidden rule ID")
    return values


def _effective_rule_selection(
    configured_select: Iterable[str],
    configured_ignore: Iterable[str],
    cli_select: Iterable[str] | None,
    cli_ignore: Iterable[str] | None,
    *,
    configured_extend_select: Iterable[str] = (),
    configured_extend_ignore: Iterable[str] = (),
    cli_extend_select: Iterable[str] | None = None,
    cli_extend_ignore: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Apply the one selection precedence algorithm shared by defaults and TOML.

    Order:
    1. `select`/`ignore` establish the base set — or a CLI `--select` gives a wholesale
       override, replacing every configured selection key (`select`, `ignore`, `extend-select`,
       `extend-ignore`) rather than adding to them, as Ruff's `--select` does. A per-rule run
       such as `--select HSL102` then runs exactly that rule, even when the config extends the
       selection.
    2. `extend-select`/`extend-ignore` layer on top of that base. Config and CLI variants of each
       are merged together (concatenated, not one overriding the other) before being applied;
       under a CLI `--select`, only the CLI variants apply.
    3. `extend-ignore` is subtractive against the *whole* pool from steps 1-2, not just
       against `extend-select`'s own additions — `select = ["HSL001"]` combined with
       `extend-ignore = ["HSL001"]` drops HSL001 entirely, the same as if it had never been
       selected.
    4. CLI `--ignore` is applied last and always wins over everything above.
    """
    configured = _ids(list(configured_select), "select")
    configured_ignored = _ids(list(configured_ignore), "ignore")
    configured_extend_selected = _ids(list(configured_extend_select), "extend-select")
    configured_extend_ignored = _ids(list(configured_extend_ignore), "extend-ignore")
    if cli_select is not None:
        selected = _ids(list(cli_select), "--select")
        configured_extend_selected = ()
        configured_extend_ignored = ()
    else:
        selected = tuple(rule_id for rule_id in configured if rule_id not in configured_ignored)
    extend_selected = configured_extend_selected + _ids(
        list(cli_extend_select or ()), "--extend-select"
    )
    extend_ignored = configured_extend_ignored + _ids(
        list(cli_extend_ignore or ()), "--extend-ignore"
    )
    extended = (set(selected) | set(extend_selected)) - set(extend_ignored)
    cli_ignored = _ids(list(cli_ignore or ()), "--ignore")
    return tuple(sorted(extended - set(cli_ignored))) + (ALWAYS_ON_RULE_ID,)


def _validate_include(values: tuple[str, ...]) -> tuple[str, ...]:
    for value in values:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or not value:
            raise ConfigError("include paths must be non-empty root-relative paths")
        if any(char in value for char in "*?[]!"):
            raise ConfigError("include paths must be literal root-relative paths, not globs")
    return values


def _validate_git_ignore_patterns(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    """Root-relative, syntactically valid Git-ignore-style patterns, shared by `exclude` and
    `per-file-ignores` — the two config keys that accept this pattern style."""
    if any(Path(value).is_absolute() or ".." in Path(value).parts for value in values):
        raise ConfigError(f"{name} patterns must be root-relative")
    try:
        GitIgnoreSpec.from_lines(values)
    except (TypeError, ValueError, re.error) as exc:
        raise ConfigError(f"{name} contains invalid Git-ignore patterns: {exc}") from exc
    return values


def validate_exclude(values: tuple[str, ...]) -> tuple[str, ...]:
    negated = [value for value in values if value.startswith("!")]
    if negated:
        raise ConfigError(f"exclude must not contain negated patterns: {', '.join(negated)}")
    return _validate_git_ignore_patterns(values, "exclude")


def _per_file_ignores(raw: Any) -> Mapping[str, tuple[str, ...]]:
    table = _table(raw, "per-file-ignores")
    result: dict[str, tuple[str, ...]] = {}
    for pattern, value in table.items():
        if not pattern:
            raise ConfigError("per-file-ignores keys must be non-empty Git-ignore-style patterns")
        if pattern.startswith("!"):
            raise ConfigError("per-file-ignores keys must not be negated patterns")
        _validate_git_ignore_patterns((pattern,), "per-file-ignores")
        result[pattern] = _ids(value, f"per-file-ignores.{pattern!r}")
    return MappingProxyType(result)


def _strict_keys(table: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"{name} contains unknown keys: {', '.join(sorted(unknown))}")


def _rule_options(raw: dict[str, Any]) -> Mapping[str, RuleOptions | None]:
    rules = _table(raw.get("rules", {}), "rules")
    rows = (*FILE_RULES.values(), *PROJECT_RULES.values())
    _strict_keys(rules, {rule.id for rule in rows if rule.parse_options}, "rules")
    options: dict[str, RuleOptions | None] = {}
    for rule in rows:
        if rule.parse_options is None:
            options[rule.id] = None
            continue
        try:
            options[rule.id] = rule.parse_options(rules.get(rule.id, {}))
        except msgspec.ValidationError as exc:
            raise ConfigError(f"rules.{rule.id}: {exc}") from exc
    return MappingProxyType(options)


def load_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file, raising ConfigError with a consistent message on failure."""
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"invalid project configuration: {exc}") from exc


def get_house_lint_table(document: dict[str, Any]) -> dict[str, Any] | None:
    """Return the house-lint table when this TOML document contains one."""
    tool = document.get("tool")
    if not isinstance(tool, dict):
        return None
    house_lint = cast(dict[str, Any], tool).get("house-lint")
    return cast(dict[str, Any], house_lint) if isinstance(house_lint, dict) else None


def get_standalone_table(document: dict[str, Any]) -> dict[str, Any] | None:
    """Return the top-level `[house-lint]` table from a standalone config file."""
    house_lint = document.get("house-lint")
    return cast(dict[str, Any], house_lint) if isinstance(house_lint, dict) else None


def is_standalone_config(path: Path) -> bool:
    """Return whether `path`'s filename identifies a standalone config file."""
    return path.name in STANDALONE_CONFIG_NAMES


def config_table_name(standalone: bool) -> str:
    """Return the bare TOML table name (no brackets) house-lint settings live under.

    Single source of truth for this fact, shared by `load_config`'s error/validation messages
    and `reporters.text.zero_file_guidance`'s user-facing guidance, so the two can never disagree
    about which table a standalone vs. `pyproject.toml` config uses.
    """
    return "house-lint" if standalone else "tool.house-lint"


def load_config(
    path: Path,
    *,
    cli_select: Iterable[str] | None = None,
    cli_ignore: Iterable[str] | None = None,
    cli_extend_select: Iterable[str] | None = None,
    cli_extend_ignore: Iterable[str] | None = None,
) -> LintConfig:
    """Load and validate one TOML configuration file."""
    standalone = is_standalone_config(path)
    document = load_toml(path)
    house = get_standalone_table(document) if standalone else get_house_lint_table(document)
    table_name = config_table_name(standalone)
    if house is None:
        raise ConfigError(f"config lacks [{table_name}]")
    _strict_keys(
        house,
        {
            "include",
            "exclude",
            "select",
            "ignore",
            "extend-select",
            "extend-ignore",
            "per-file-ignores",
            "rules",
        },
        table_name,
    )
    include = _validate_include(_strings(house.get("include", list(DEFAULT_INCLUDE)), "include"))
    exclude = validate_exclude(_strings(house.get("exclude", []), "exclude"))
    # Every raw TOML value goes through `_strings` here rather than reaching
    # `_effective_rule_selection` as-is: that function converts with `list(...)` before validating,
    # which turns `select = 5` into a `TypeError` (an internal-error exit, not the documented
    # config-error exit) and silently splits `select = "HSL001"` into single characters, reported
    # as an unknown rule ID instead of "must be an array". Its other callers pass tuples built in
    # this module, so the conversion stays where it is and the untrusted edge is checked here.
    enabled = _effective_rule_selection(
        _strings(house.get("select", list(DEFAULT_SELECT)), "select"),
        _strings(house.get("ignore", []), "ignore"),
        cli_select,
        cli_ignore,
        configured_extend_select=_strings(house.get("extend-select", []), "extend-select"),
        configured_extend_ignore=_strings(house.get("extend-ignore", []), "extend-ignore"),
        cli_extend_select=cli_extend_select,
        cli_extend_ignore=cli_extend_ignore,
    )
    per_file_ignores = _per_file_ignores(house.get("per-file-ignores", {}))
    rule_options = _rule_options(house)
    # `enabled` is already sorted with the always-on rule appended (see
    # `_effective_rule_selection`); re-sorting here would only differ from `default_config`'s
    # handling of the same value if an always-on rule ID ever stopped sorting last.
    return LintConfig(include, exclude, enabled, rule_options, per_file_ignores)


@dataclass(frozen=True)
class SelectionOverrides:
    """The command line's rule selection, applied over whichever config a run loads.

    Kept separate from loading so `--base` can apply the same flags to the config it reads from
    the head commit rather than the working tree.
    """

    select: tuple[str, ...] | None = None
    ignore: tuple[str, ...] | None = None
    extend_select: tuple[str, ...] | None = None
    extend_ignore: tuple[str, ...] | None = None

    def load(self, path: Path | None) -> LintConfig:
        """Load `path` with these overrides, or the built-in config when there is none."""
        if path is None:
            return default_config(
                cli_select=self.select,
                cli_ignore=self.ignore,
                cli_extend_select=self.extend_select,
                cli_extend_ignore=self.extend_ignore,
            )
        return load_config(
            path,
            cli_select=self.select,
            cli_ignore=self.ignore,
            cli_extend_select=self.extend_select,
            cli_extend_ignore=self.extend_ignore,
        )
