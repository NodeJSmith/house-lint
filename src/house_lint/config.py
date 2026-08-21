"""Strict TOML configuration and project/configuration resolution."""

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from pathspec import GitIgnoreSpec

from house_lint.rule_catalog import DEFAULT_SELECT, ORDINARY_RULES

DEFAULT_INCLUDE = ("src", "tests", "scripts", "tools", "examples")
_PREFIX = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

DEFAULT_MAX_FINDINGS_PER_FILE = 200
MAX_FINDINGS_PER_FILE_LIMIT = 10_000
DEFAULT_MAX_LINES = 800
MAX_LINES_LIMIT = 10_000_000
MAX_TOKEN_FAMILIES = 32
MAX_PREFIXES_PER_FAMILY = 32
MAX_PREFIX_LENGTH = 12
MAX_DIGITS_BOUND = 12
_BUILTIN_MAX_DIGITS = 4

VALID_SCOPES: tuple[str, ...] = ("comments", "docstrings", "filenames")

# Single source of truth for separator/suffix vocabularies: the keys validate configured
# values, and house_lint.rules.spec_tokens uses the values to build detection regexes.
SEPARATOR_REGEX: Mapping[str, str] = MappingProxyType(
    {
        "none": "",
        "hash": "#",
        "hash-optional": "#?",
        "dash": "-",
        "dash-optional": "-?",
    }
)
SUFFIX_REGEX: Mapping[str, str] = MappingProxyType(
    {
        "none": "",
        "optional-lower-alpha": "[a-z]?",
    }
)


class ConfigError(ValueError):
    """Raised when a configuration cannot be used for a scan."""


@dataclass(frozen=True, kw_only=True)
class TokenFamily:
    prefixes: tuple[str, ...]
    scopes: tuple[str, ...]
    separator: str = "none"
    min_digits: int = 1
    max_digits: int | None = None
    suffix: str = "none"
    case_sensitive: bool = True
    not_followed_by_time: bool = False


BUILTIN_SPEC = TokenFamily(
    prefixes=("AC", "FR", "NFR", "WP"),
    scopes=VALID_SCOPES,
    separator="hash-optional",
    max_digits=_BUILTIN_MAX_DIGITS,
    suffix="optional-lower-alpha",
)

BUILTIN_TASK = TokenFamily(
    prefixes=("T",),
    scopes=VALID_SCOPES,
    separator="hash-optional",
    max_digits=_BUILTIN_MAX_DIGITS,
    suffix="optional-lower-alpha",
    not_followed_by_time=True,
)

BUILTIN_KNOWN_ISSUES = TokenFamily(
    prefixes=("KI",),
    scopes=VALID_SCOPES,
    separator="dash",
    max_digits=_BUILTIN_MAX_DIGITS,
)

BUILTIN_TOKEN_FAMILIES: tuple[TokenFamily, ...] = (
    BUILTIN_SPEC,
    BUILTIN_TASK,
    BUILTIN_KNOWN_ISSUES,
)


@dataclass(frozen=True)
class HSL101Options:
    tokens: tuple[TokenFamily, ...] = BUILTIN_TOKEN_FAMILIES
    max_findings_per_file: int = DEFAULT_MAX_FINDINGS_PER_FILE


@dataclass(frozen=True)
class HSL102Options:
    max_lines: int = DEFAULT_MAX_LINES


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
    per_file_ignores: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )


DetectorOptions = HSL101Options | HSL102Options | HSL103Options | None
DetectorInput = tuple[str, DetectorOptions]


def selected_detector_inputs(
    config: LintConfig, *, enabled_rules: tuple[str, ...] | None = None
) -> tuple[DetectorInput, ...]:
    """Return enabled ordinary rules with their already-validated options.

    `enabled_rules` overrides `config.enabled_rules` when given, for callers that resolved a
    per-file effective rule set (see `per_file_enabled_rules`) rather than the global one.
    """
    options: dict[str, DetectorOptions] = {
        "HSL001": None,
        "HSL002": None,
        "HSL003": None,
        "HSL004": None,
        "HSL101": config.hsl101,
        "HSL102": config.hsl102,
        "HSL103": config.hsl103,
    }
    rules = config.enabled_rules if enabled_rules is None else enabled_rules
    return tuple((rule_id, options[rule_id]) for rule_id in rules if rule_id in options)


CompiledPerFileIgnores = tuple[tuple[GitIgnoreSpec, frozenset[str]], ...]


def compile_per_file_ignores(
    per_file_ignores: Mapping[str, tuple[str, ...]],
) -> CompiledPerFileIgnores:
    """Precompile per-file-ignores glob patterns once, for repeated per-file matching."""
    return tuple(
        (GitIgnoreSpec.from_lines((pattern,)), frozenset(rule_ids))
        for pattern, rule_ids in per_file_ignores.items()
    )


def per_file_enabled_rules(
    base_enabled_rules: tuple[str, ...],
    compiled_per_file_ignores: CompiledPerFileIgnores,
    relative_path: str,
) -> tuple[str, ...]:
    """Return `base_enabled_rules` minus any rules whose Git-ignore-style glob pattern matches
    this root-relative file path."""
    ignored: set[str] = set()
    for spec, rule_ids in compiled_per_file_ignores:
        if spec.match_file(relative_path):
            ignored |= rule_ids
    if not ignored:
        return base_enabled_rules
    return tuple(rule_id for rule_id in base_enabled_rules if rule_id not in ignored)


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
       override, replacing configured `select`/`ignore` entirely rather than adding to them.
    2. `extend-select`/`extend-ignore` layer on top of that base *regardless of its source*.
       Config and CLI variants of each are merged together (concatenated, not one overriding
       the other) before being applied, since — unlike `select` vs. `--select` — neither is
       meant to replace the other.
    3. `extend-ignore` is subtractive against the *whole* pool from steps 1-2, not just
       against `extend-select`'s own additions — `select = ["HSL001"]` combined with
       `extend-ignore = ["HSL001"]` drops HSL001 entirely, the same as if it had never been
       selected.
    4. CLI `--ignore` is applied last and always wins over everything above.
    """
    configured = _ids(list(configured_select), "select")
    configured_ignored = _ids(list(configured_ignore), "ignore")
    selected = (
        _ids(list(cli_select), "--select")
        if cli_select is not None
        else tuple(rule_id for rule_id in configured if rule_id not in configured_ignored)
    )
    extend_selected = _ids(list(configured_extend_select), "extend-select") + _ids(
        list(cli_extend_select or ()), "--extend-select"
    )
    extend_ignored = _ids(list(configured_extend_ignore), "extend-ignore") + _ids(
        list(cli_extend_ignore or ()), "--extend-ignore"
    )
    extended = (set(selected) | set(extend_selected)) - set(extend_ignored)
    cli_ignored = _ids(list(cli_ignore or ()), "--ignore")
    return tuple(sorted(extended - set(cli_ignored))) + ("HSL900",)


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


def _validate_exclude(values: tuple[str, ...]) -> tuple[str, ...]:
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


def _bounded_int(value: Any, name: str, maximum: int, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _token_family(raw: Any, index: int) -> TokenFamily:
    name = f"HSL101.tokens[{index}]"
    table = _table(raw, name)
    _strict_keys(
        table,
        {
            "prefixes",
            "scopes",
            "separator",
            "min_digits",
            "max_digits",
            "suffix",
            "case_sensitive",
            "not_followed_by_time",
        },
        name,
    )
    prefixes = _strings(table.get("prefixes"), f"{name}.prefixes")
    too_many_prefixes = len(prefixes) > MAX_PREFIXES_PER_FAMILY
    duplicate_prefixes = len(set(prefixes)) != len(prefixes)
    if not prefixes or too_many_prefixes or duplicate_prefixes:
        raise ConfigError(
            f"{name}.prefixes must contain 1 to {MAX_PREFIXES_PER_FAMILY} unique values"
        )
    if any(len(item) > MAX_PREFIX_LENGTH or not _PREFIX.fullmatch(item) for item in prefixes):
        raise ConfigError(f"{name}.prefixes contains an invalid prefix")
    scopes = _strings(table.get("scopes"), f"{name}.scopes")
    if not scopes or len(set(scopes)) != len(scopes) or not set(scopes) <= set(VALID_SCOPES):
        raise ConfigError(f"{name}.scopes contains an invalid scope")
    separator = table.get("separator", "none")
    if separator not in SEPARATOR_REGEX:
        raise ConfigError(f"{name}.separator is invalid")
    min_digits = _bounded_int(table.get("min_digits", 1), f"{name}.min_digits", MAX_DIGITS_BOUND)
    max_digits_raw = table.get("max_digits")
    max_digits = (
        _bounded_int(max_digits_raw, f"{name}.max_digits", MAX_DIGITS_BOUND, minimum=min_digits)
        if max_digits_raw is not None
        else None
    )
    suffix = table.get("suffix", "none")
    if suffix not in SUFFIX_REGEX:
        raise ConfigError(f"{name}.suffix is invalid")
    for key in ("case_sensitive", "not_followed_by_time"):
        if key in table and type(table[key]) is not bool:
            raise ConfigError(f"{name}.{key} must be a boolean")
    return TokenFamily(
        prefixes=prefixes,
        scopes=scopes,
        separator=separator,
        min_digits=min_digits,
        max_digits=max_digits,
        suffix=suffix,
        case_sensitive=table.get("case_sensitive", True),
        not_followed_by_time=table.get("not_followed_by_time", False),
    )


def _rule_options(raw: dict[str, Any]) -> tuple[HSL101Options, HSL102Options, HSL103Options]:
    rules = _table(raw.get("rules", {}), "rules")
    _strict_keys(rules, {"HSL101", "HSL102", "HSL103"}, "rules")
    hsl101_raw = _table(rules.get("HSL101", {}), "rules.HSL101")
    _strict_keys(hsl101_raw, {"tokens", "max_findings_per_file"}, "rules.HSL101")
    user_tokens: tuple[TokenFamily, ...] = ()
    if "tokens" in hsl101_raw:
        token_values = _array(hsl101_raw["tokens"], "HSL101.tokens")
        if not token_values or len(token_values) > MAX_TOKEN_FAMILIES:
            raise ConfigError(f"HSL101.tokens must contain 1 to {MAX_TOKEN_FAMILIES} families")
        user_tokens = tuple(_token_family(item, i) for i, item in enumerate(token_values))
    tokens = BUILTIN_TOKEN_FAMILIES + user_tokens
    # Re-check the same cap after merging: the check above only bounds the user-supplied
    # list, before built-in families are added on top.
    if len(tokens) > MAX_TOKEN_FAMILIES:
        raise ConfigError(
            f"HSL101.tokens combined with built-in families must not exceed {MAX_TOKEN_FAMILIES}"
        )
    hsl101 = HSL101Options(
        tokens,
        _bounded_int(
            hsl101_raw.get("max_findings_per_file", DEFAULT_MAX_FINDINGS_PER_FILE),
            "max_findings_per_file",
            MAX_FINDINGS_PER_FILE_LIMIT,
        ),
    )
    hsl102_raw = _table(rules.get("HSL102", {}), "rules.HSL102")
    _strict_keys(hsl102_raw, {"max_lines"}, "rules.HSL102")
    hsl102 = HSL102Options(
        _bounded_int(hsl102_raw.get("max_lines", DEFAULT_MAX_LINES), "max_lines", MAX_LINES_LIMIT)
    )
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


def load_config(
    path: Path,
    *,
    cli_select: Iterable[str] | None = None,
    cli_ignore: Iterable[str] | None = None,
    cli_extend_select: Iterable[str] | None = None,
    cli_extend_ignore: Iterable[str] | None = None,
) -> LintConfig:
    """Load and validate one TOML configuration file."""
    document = load_toml(path)
    house = get_house_lint_table(document)
    if house is None:
        raise ConfigError("config lacks [tool.house-lint]")
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
        "tool.house-lint",
    )
    include = _validate_include(_strings(house.get("include", list(DEFAULT_INCLUDE)), "include"))
    exclude = _validate_exclude(_strings(house.get("exclude", []), "exclude"))
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
    options = _rule_options(house)
    # `enabled` is already sorted with the always-on rule appended (see
    # `_effective_rule_selection`); re-sorting here would only differ from `default_config`'s
    # handling of the same value if an always-on rule ID ever stopped sorting last.
    return LintConfig(include, exclude, enabled, *options, per_file_ignores)
