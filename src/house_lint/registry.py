"""Static built-in rule metadata and detector dispatch."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, cast

from .analysis import CandidateFinding
from .config import DetectorInput, DetectorOptions, HSL101Options, HSL102Options, HSL103Options
from .source import SourceFile


class Detector(Protocol):
    def __call__(
        self, source: SourceFile, options: DetectorOptions, *, limit: int | None = None
    ) -> list[CandidateFinding]: ...


@dataclass(frozen=True)
class RuleMetadata:
    """Fixed metadata for one built-in house rule."""

    id: str
    name: str
    description: str
    enablement: str
    ownership_scope: str


_RULES: Mapping[str, RuleMetadata] = MappingProxyType(
    {
        "HSL001": RuleMetadata(
            "HSL001", "AI-writing cruft", "AI-writing tells", "default", "statement"
        ),
        "HSL002": RuleMetadata(
            "HSL002", "Lazy imports", "Imports inside functions", "default", "statement"
        ),
        "HSL003": RuleMetadata(
            "HSL003",
            "TYPE_CHECKING position",
            "TYPE_CHECKING blocks followed by imports",
            "default",
            "statement",
        ),
        "HSL004": RuleMetadata(
            "HSL004", "Constants position", "Constants after definitions", "default", "statement"
        ),
        "HSL101": RuleMetadata(
            "HSL101", "Spec tokens", "Configured spec tokens", "opt-in", "mixed"
        ),
        "HSL102": RuleMetadata(
            "HSL102", "File length", "Files exceeding the line limit", "opt-in", "file"
        ),
        "HSL103": RuleMetadata(
            "HSL103", "Exception names", "Exception binding names", "opt-in", "statement"
        ),
        "HSL900": RuleMetadata(
            "HSL900", "Suppression diagnostics", "Invalid suppression pragmas", "always", "no-owner"
        ),
    }
)


def _hsl001(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    from .rules import llm_cruft

    return llm_cruft.detect(source, limit=limit)


def _hsl002(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    from .rules import lazy_imports

    return lazy_imports.detect(source, limit=limit)


def _hsl003(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    from .rules import type_checking_position

    return type_checking_position.detect(source, limit=limit)


def _hsl004(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    from .rules import constants_position

    return constants_position.detect(source, limit=limit)


def _hsl101(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    from .rules import spec_tokens

    return spec_tokens.detect(source, cast("HSL101Options", options), limit=limit)


def _hsl102(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    from .rules import file_length

    return file_length.detect(source, cast("HSL102Options", options), limit=limit)


def _hsl103(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    from .rules import exception_names

    return exception_names.detect(source, cast("HSL103Options", options), limit=limit)


_DETECTORS: Mapping[str, Detector] = MappingProxyType(
    {
        "HSL001": _hsl001,
        "HSL002": _hsl002,
        "HSL003": _hsl003,
        "HSL004": _hsl004,
        "HSL101": _hsl101,
        "HSL102": _hsl102,
        "HSL103": _hsl103,
    }
)


def detect_candidates(
    source: SourceFile, detector_inputs: tuple[DetectorInput, ...], *, limit: int | None = None
) -> list[CandidateFinding]:
    """Run selected detectors, bounding their materialized output when requested."""
    candidates: list[CandidateFinding] = []
    for rule_id, options in detector_inputs:
        if rule_id not in _DETECTORS:
            continue
        detector_limit = None if limit is None else limit - len(candidates)
        candidates.extend(_DETECTORS[rule_id](source, options, limit=detector_limit))
        if limit is not None and len(candidates) > limit:
            break
    return candidates


def is_known_rule(rule_id: str) -> bool:
    """Return whether a rule ID belongs to the fixed built-in registry."""
    return rule_id in _RULES


def rule_ids() -> tuple[str, ...]:
    """Return built-in rule IDs in their stable display order."""
    return tuple(_RULES)


def rule_metadata(rule_id: str) -> RuleMetadata:
    """Return display metadata for one known built-in rule."""
    return _RULES[rule_id]


__all__ = ["detect_candidates", "is_known_rule", "rule_ids", "rule_metadata"]
