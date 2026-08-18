"""Static built-in rule metadata and detector dispatch."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol, cast

from .analysis import CandidateFinding
from .config import DetectorInput, DetectorOptions, HSL101Options, HSL102Options, HSL103Options
from .rule_catalog import ORDINARY_RULES
from .source import SourceFile


class Detector(Protocol):
    def __call__(
        self, source: SourceFile, options: DetectorOptions, *, limit: int | None = None
    ) -> list[CandidateFinding]: ...


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

if set(_DETECTORS) != set(ORDINARY_RULES):
    raise RuntimeError(
        "registry._DETECTORS is out of sync with rule_catalog.ORDINARY_RULES — "
        "every ordinary rule needs exactly one dispatch function"
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


__all__ = ["detect_candidates"]
