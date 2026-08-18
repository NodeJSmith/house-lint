"""Strict TOML configuration and project/configuration resolution."""

import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pathspec import GitIgnoreSpec

from .rule_catalog import DEFAULT_SELECT, ORDINARY_RULES

DEFAULT_INCLUDE = ("src", "tests", "scripts", "tools", "examples")
_PREFIX = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class ConfigError(ValueError):
    """Raised when a configuration cannot be used for a scan."""


@dataclass(frozen=True)
class TokenFamily:
    prefixes: tuple[str, ...]
    scopes: tuple[str, ...]
    hash: str = "forbidden"
    min_digits: int = 1
    max_digits: int | None = None
    suffix: str = "none"
    case_sensitive: bool = True
    not_followed_by_time: bool = False


@dataclass(frozen=True)
class HSL101Options:
    tokens: tuple[TokenFamily, ...] = ()
    max_findings_per_file: int = 200


@dataclass(frozen=True)
class HSL102Options:
    max_lines: int = 800


@dataclass(frozen=True)
class HSL103Options:
    allowed: tuple[str, ...] = ("exc", "*_exc")


@dataclass(frozen=True)
class LintConfig:
    include: tuple[str, ...] = DEFAULT_INCLUDE
    exclude: tuple[str, ...] = ()
    enabled_rules: tuple[str, ...] = (*DEFAULT_SELECT, "HSL900")
    hsl101: HSL101Options = HSL101Options()
    hsl102: HSL102Options = HSL102Options()
    hsl103: HSL103Options = HSL103Options()


DetectorOptions = HSL101Options | HSL102Options | HSL103Options | None
DetectorInput = tuple[str, DetectorOptions]


def selected_detector_inputs(config: LintConfig) -> tuple[DetectorInput, ...]:
    """Return enabled ordinary rules with their already-validated options."""
    options: dict[str, DetectorOptions] = {
        "HSL001": None,
        "HSL002": None,
        "HSL003": None,
        "HSL004": None,
        "HSL101": config.hsl101,
        "HSL102": config.hsl102,
        "HSL103": config.hsl103,
    }
    return tuple(
        (rule_id, options[rule_id]) for rule_id in config.enabled_rules if rule_id in options
    )


def default_config(
    *, cli_select: Iterable[str] | None = None, cli_ignore: Iterable[str] | None = None
) -> LintConfig:
    """Build built-in configuration with the same CLI selection semantics as TOML."""
    enabled_rules = _effective_rule_selection(DEFAULT_SELECT, (), cli_select, cli_ignore)
    if "HSL101" in enabled_rules:
        raise ConfigError("HSL101 requires tokens when selected")
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
) -> tuple[str, ...]:
    """Apply the one selection precedence algorithm shared by defaults and TOML."""
    configured = _ids(list(configured_select), "select")
    configured_ignored = _ids(list(configured_ignore), "ignore")
    selected = (
        _ids(list(cli_select), "--select")
        if cli_select is not None
        else tuple(rule_id for rule_id in configured if rule_id not in configured_ignored)
    )
    cli_ignored = _ids(list(cli_ignore or ()), "--ignore")
    return tuple(sorted(set(selected) - set(cli_ignored))) + ("HSL900",)


def _validate_include(values: tuple[str, ...]) -> tuple[str, ...]:
    for value in values:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or not value:
            raise ConfigError("include paths must be non-empty root-relative paths")
        if any(char in value for char in "*?[]!"):
            raise ConfigError("include paths must be literal root-relative paths, not globs")
    return values


def _validate_exclude(values: tuple[str, ...]) -> tuple[str, ...]:
    if any(Path(value).is_absolute() or ".." in Path(value).parts for value in values):
        raise ConfigError("exclude patterns must be root-relative")
    try:
        GitIgnoreSpec.from_lines(values)
    except (TypeError, ValueError, re.error) as exc:
        raise ConfigError(f"exclude contains invalid Git-ignore patterns: {exc}") from exc
    return values


def _strict_keys(table: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"{name} contains unknown keys: {', '.join(sorted(unknown))}")


def _bounded_int(value: Any, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise ConfigError(f"{name} must be a positive integer no greater than {maximum}")
    return value


def _token_family(raw: Any, index: int) -> TokenFamily:
    name = f"HSL101.tokens[{index}]"
    table = _table(raw, name)
    _strict_keys(
        table,
        {
            "prefixes",
            "scopes",
            "hash",
            "min_digits",
            "max_digits",
            "suffix",
            "case_sensitive",
            "not_followed_by_time",
        },
        name,
    )
    prefixes = _strings(table.get("prefixes"), f"{name}.prefixes")
    if not prefixes or len(prefixes) > 32 or len(set(prefixes)) != len(prefixes):
        raise ConfigError(f"{name}.prefixes must contain 1 to 32 unique values")
    if any(len(item) > 12 or not _PREFIX.fullmatch(item) for item in prefixes):
        raise ConfigError(f"{name}.prefixes contains an invalid prefix")
    scopes = _strings(table.get("scopes"), f"{name}.scopes")
    if (
        not scopes
        or len(set(scopes)) != len(scopes)
        or not set(scopes) <= {"comments", "docstrings", "filenames"}
    ):
        raise ConfigError(f"{name}.scopes contains an invalid scope")
    hash_mode = table.get("hash", "forbidden")
    if hash_mode not in {"forbidden", "optional", "required"}:
        raise ConfigError(f"{name}.hash is invalid")
    min_digits = table.get("min_digits", 1)
    if isinstance(min_digits, bool) or not isinstance(min_digits, int) or not 1 <= min_digits <= 12:
        raise ConfigError(f"{name}.min_digits is invalid")
    max_digits = table.get("max_digits")
    if max_digits is not None and (
        isinstance(max_digits, bool)
        or not isinstance(max_digits, int)
        or not min_digits <= max_digits <= 12
    ):
        raise ConfigError(f"{name}.max_digits is invalid")
    suffix = table.get("suffix", "none")
    if suffix not in {"none", "optional-lower-alpha"}:
        raise ConfigError(f"{name}.suffix is invalid")
    for key in ("case_sensitive", "not_followed_by_time"):
        if key in table and type(table[key]) is not bool:
            raise ConfigError(f"{name}.{key} must be a boolean")
    return TokenFamily(
        prefixes,
        scopes,
        hash_mode,
        min_digits,
        max_digits,
        suffix,
        table.get("case_sensitive", True),
        table.get("not_followed_by_time", False),
    )


def _rule_options(raw: dict[str, Any]) -> tuple[HSL101Options, HSL102Options, HSL103Options]:
    rules = _table(raw.get("rules", {}), "rules")
    _strict_keys(rules, {"HSL101", "HSL102", "HSL103"}, "rules")
    hsl101_raw = _table(rules.get("HSL101", {}), "rules.HSL101")
    _strict_keys(hsl101_raw, {"tokens", "max_findings_per_file"}, "rules.HSL101")
    tokens: tuple[TokenFamily, ...] = ()
    if "tokens" in hsl101_raw:
        token_values = _array(hsl101_raw["tokens"], "HSL101.tokens")
        if not token_values or len(token_values) > 32:
            raise ConfigError("HSL101.tokens must contain 1 to 32 families")
        tokens = tuple(_token_family(item, i) for i, item in enumerate(token_values))
    hsl101 = HSL101Options(
        tokens,
        _bounded_int(hsl101_raw.get("max_findings_per_file", 200), "max_findings_per_file", 10_000),
    )
    hsl102_raw = _table(rules.get("HSL102", {}), "rules.HSL102")
    _strict_keys(hsl102_raw, {"max_lines"}, "rules.HSL102")
    hsl102 = HSL102Options(_bounded_int(hsl102_raw.get("max_lines", 800), "max_lines", 10_000_000))
    hsl103_raw = _table(rules.get("HSL103", {}), "rules.HSL103")
    _strict_keys(hsl103_raw, {"allowed"}, "rules.HSL103")
    allowed = _strings(hsl103_raw.get("allowed", ["exc", "*_exc"]), "allowed")
    if not allowed or len(set(allowed)) != len(allowed):
        raise ConfigError("allowed must be non-empty and contain no duplicates")
    for item in allowed:
        if "*" in item and (
            not item.startswith("*") or item.count("*") != 1 or not _IDENTIFIER.fullmatch(item[1:])
        ):
            raise ConfigError("allowed wildcard entries must be a leading * and identifier suffix")
        if "*" not in item and not _IDENTIFIER.fullmatch(item):
            raise ConfigError("allowed entries must be identifiers")
    return hsl101, hsl102, HSL103Options(allowed)


def get_house_lint_table(document: dict[str, Any]) -> dict[str, Any] | None:
    """Return the house-lint table when this TOML document contains one."""
    tool = document.get("tool")
    if not isinstance(tool, dict):
        return None
    house_lint = cast(dict[str, Any], tool).get("house-lint")
    return cast(dict[str, Any], house_lint) if isinstance(house_lint, dict) else None


def load_config(
    path: Path, *, cli_select: Iterable[str] | None = None, cli_ignore: Iterable[str] | None = None
) -> LintConfig:
    """Load and validate one TOML configuration file."""
    try:
        with path.open("rb") as stream:
            document: dict[str, Any] = tomllib.load(stream)
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    house = get_house_lint_table(document)
    if house is None:
        raise ConfigError("config lacks [tool.house-lint]")
    _strict_keys(house, {"include", "exclude", "select", "ignore", "rules"}, "tool.house-lint")
    include = _validate_include(_strings(house.get("include", list(DEFAULT_INCLUDE)), "include"))
    exclude = _validate_exclude(_strings(house.get("exclude", []), "exclude"))
    enabled = _effective_rule_selection(
        house.get("select", list(DEFAULT_SELECT)),
        house.get("ignore", []),
        cli_select,
        cli_ignore,
    )
    options = _rule_options(house)
    if "HSL101" in enabled and not options[0].tokens:
        raise ConfigError("HSL101 requires tokens when selected")
    return LintConfig(include, exclude, tuple(sorted(enabled)), *options)
