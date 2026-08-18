"""Canonical catalog of built-in house rules — the single source of truth for IDs and enablement."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class RuleMetadata:
    """Fixed metadata for one built-in house rule."""

    id: str
    name: str
    description: str
    enablement: str


RULES: Mapping[str, RuleMetadata] = MappingProxyType(
    {
        "HSL001": RuleMetadata("HSL001", "AI-writing cruft", "AI-writing tells", "default"),
        "HSL002": RuleMetadata("HSL002", "Lazy imports", "Imports inside functions", "default"),
        "HSL003": RuleMetadata(
            "HSL003",
            "TYPE_CHECKING position",
            "TYPE_CHECKING blocks followed by imports",
            "default",
        ),
        "HSL004": RuleMetadata(
            "HSL004", "Constants position", "Constants after definitions", "default"
        ),
        "HSL101": RuleMetadata("HSL101", "Spec tokens", "Configured spec tokens", "opt-in"),
        "HSL102": RuleMetadata("HSL102", "File length", "Files exceeding the line limit", "opt-in"),
        "HSL103": RuleMetadata("HSL103", "Exception names", "Exception binding names", "opt-in"),
        "HSL900": RuleMetadata(
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


def is_known_rule(rule_id: str) -> bool:
    """Return whether a rule ID belongs to the fixed built-in registry."""
    return rule_id in RULES


def rule_ids() -> tuple[str, ...]:
    """Return built-in rule IDs in their stable display order."""
    return tuple(RULES)


def rule_metadata(rule_id: str) -> RuleMetadata:
    """Return display metadata for one known built-in rule."""
    return RULES[rule_id]


__all__ = [
    "DEFAULT_SELECT",
    "ORDINARY_RULES",
    "RULES",
    "RuleMetadata",
    "is_known_rule",
    "rule_ids",
    "rule_metadata",
]
