"""The table of built-in rules: the single source of truth for what each rule is and how it runs.

Adding a rule takes its module under `rules/` and one row here.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Generic, Literal, Protocol, TypeVar

import msgspec

from house_lint.analysis import CandidateFinding
from house_lint.rules import (
    constants_position,
    exception_names,
    file_length,
    lazy_imports,
    llm_cruft,
    spec_tokens,
    type_checking_position,
)
from house_lint.rules.exception_names import HSL103Options
from house_lint.rules.file_length import HSL102Options
from house_lint.rules.options import RuleOptions
from house_lint.rules.spec_tokens import HSL101Options
from house_lint.source import SourceFile

OptionsT = TypeVar("OptionsT")
ConfigurableOptionsT = TypeVar("ConfigurableOptionsT", bound=RuleOptions)
Enablement = Literal["default", "opt-in", "always"]
OptionsT_contra = TypeVar("OptionsT_contra", contravariant=True)


class Detector(Protocol[OptionsT_contra]):
    def __call__(
        self, source: SourceFile, options: OptionsT_contra, *, limit: int | None = None
    ) -> list[CandidateFinding]: ...


@dataclass(frozen=True)
class Rule:
    """Fixed metadata for one built-in house rule."""

    id: str
    name: str
    description: str
    enablement: Enablement


@dataclass(frozen=True)
class FileRule(Rule, Generic[OptionsT]):
    """A rule that runs once per scanned file. `parse_options` is None when it takes no options."""

    detect: Detector[OptionsT]
    parse_options: Callable[[Any], OptionsT] | None = None


def file_rule(
    rule_id: str, name: str, description: str, enablement: Enablement, detect: Detector[None]
) -> FileRule[Any]:
    """Build the row for a file rule with no options."""
    return FileRule(rule_id, name, description, enablement, detect)


def configurable_rule(
    rule_id: str,
    name: str,
    description: str,
    enablement: Enablement,
    detect: Detector[ConfigurableOptionsT],
    options_type: type[ConfigurableOptionsT],
    *,
    parse: Callable[[Any], ConfigurableOptionsT] | None = None,
) -> FileRule[Any]:
    """Build the row for a file rule with options.

    The rows are held as `FileRule[Any]`, where a directly written `FileRule(...)` would accept a
    detector and options type that disagree. Solving `OptionsT` here makes that a type error.

    `parse` replaces the plain conversion of the rule's config table to `options_type`.
    """
    return FileRule(
        rule_id,
        name,
        description,
        enablement,
        detect,
        parse or (lambda raw: msgspec.convert(raw, options_type)),
    )


def rules_by_id(rows: Iterable[FileRule[Any]]) -> Mapping[str, FileRule[Any]]:
    """Key the table's rows by ID. A repeated ID would otherwise replace the earlier rule."""
    by_id: dict[str, FileRule[Any]] = {}
    for row in rows:
        if row.id in by_id:
            raise RuntimeError(f"rule table has more than one row for {row.id}")
        by_id[row.id] = row
    return MappingProxyType(by_id)


FILE_RULES: Mapping[str, FileRule[Any]] = rules_by_id(
    (
        file_rule("HSL001", "AI-writing cruft", "AI-writing tells", "default", llm_cruft.detect),
        file_rule(
            "HSL002", "Lazy imports", "Imports inside functions", "default", lazy_imports.detect
        ),
        file_rule(
            "HSL003",
            "TYPE_CHECKING position",
            "TYPE_CHECKING blocks followed by imports",
            "default",
            type_checking_position.detect,
        ),
        file_rule(
            "HSL004",
            "Constants position",
            "Constants after definitions",
            "default",
            constants_position.detect,
        ),
        configurable_rule(
            "HSL101",
            "Spec tokens",
            "Built-in and configured spec tokens",
            "opt-in",
            spec_tokens.detect,
            HSL101Options,
            parse=spec_tokens.parse_options,
        ),
        configurable_rule(
            "HSL102",
            "File length",
            "Files exceeding the line limit",
            "opt-in",
            file_length.detect,
            HSL102Options,
        ),
        configurable_rule(
            "HSL103",
            "Exception names",
            "Exception binding names",
            "opt-in",
            exception_names.detect,
            HSL103Options,
        ),
    )
)

RULES: Mapping[str, Rule] = MappingProxyType(
    {
        **FILE_RULES,
        "HSL900": Rule(
            "HSL900", "Suppression diagnostics", "Invalid suppression pragmas", "always"
        ),
    }
)

DEFAULT_SELECT: tuple[str, ...] = tuple(
    rule.id for rule in RULES.values() if rule.enablement == "default"
)
ORDINARY_RULES: frozenset[str] = frozenset(
    rule.id for rule in RULES.values() if rule.enablement != "always"
)
# Derived from RULES rather than a second hardcoded literal, so this and the RULES entry it
# names can never drift apart.
_always_on = tuple(rule.id for rule in RULES.values() if rule.enablement == "always")
if len(_always_on) != 1:
    raise RuntimeError(
        f"expected exactly one 'always' rule in RULES, found {_always_on} — "
        "config.py and suppressions.py assume a single always-on rule ID"
    )
ALWAYS_ON_RULE_ID: str = _always_on[0]


def is_known_rule(rule_id: str) -> bool:
    """Return whether a rule ID belongs to the fixed built-in table."""
    return rule_id in RULES


def rule_ids() -> tuple[str, ...]:
    """Return built-in rule IDs in their stable display order."""
    return tuple(RULES)


def rule_metadata(rule_id: str) -> Rule:
    """Return display metadata for one known built-in rule."""
    return RULES[rule_id]


__all__ = [
    "ALWAYS_ON_RULE_ID",
    "DEFAULT_SELECT",
    "FILE_RULES",
    "ORDINARY_RULES",
    "RULES",
    "Detector",
    "FileRule",
    "Rule",
    "configurable_rule",
    "file_rule",
    "is_known_rule",
    "rule_ids",
    "rule_metadata",
    "rules_by_id",
]
