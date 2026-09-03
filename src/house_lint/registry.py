"""Static built-in rule metadata and detector dispatch."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol, cast

from house_lint.analysis import CandidateBudgetExceeded, CandidateFinding
from house_lint.config import (
    DetectorInput,
    DetectorOptions,
    HSL101Options,
    HSL102Options,
    HSL103Options,
)
from house_lint.rule_catalog import ORDINARY_RULES
from house_lint.rules import (
    constants_position,
    exception_names,
    file_length,
    lazy_imports,
    llm_cruft,
    spec_tokens,
    type_checking_position,
)
from house_lint.source import SourceFile


class Detector(Protocol):
    def __call__(
        self, source: SourceFile, options: DetectorOptions, *, limit: int | None = None
    ) -> list[CandidateFinding]: ...


# HSL101-103 accept their own narrow HSL10xOptions type, which the protocol's
# DetectorOptions can't be assigned to without a cast — hence the adapters
# below. detect_candidates always pairs each rule_id with the matching options
# type, so the casts are sound.
def _hsl101(
    source: SourceFile, options: DetectorOptions, *, limit: int | None = None
) -> list[CandidateFinding]:
    return spec_tokens.detect(source, cast("HSL101Options", options), limit=limit)


def _hsl102(
    source: SourceFile, options: DetectorOptions, *, limit: int | None = None
) -> list[CandidateFinding]:
    return file_length.detect(source, cast("HSL102Options", options), limit=limit)


def _hsl103(
    source: SourceFile, options: DetectorOptions, *, limit: int | None = None
) -> list[CandidateFinding]:
    return exception_names.detect(source, cast("HSL103Options", options), limit=limit)


# HSL001-004 accept options: object (they ignore it), so their detect functions
# satisfy the Detector protocol directly and are referenced here as-is.
_DETECTORS: Mapping[str, Detector] = MappingProxyType(
    {
        "HSL001": llm_cruft.detect,
        "HSL002": lazy_imports.detect,
        "HSL003": type_checking_position.detect,
        "HSL004": constants_position.detect,
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
        try:
            candidates.extend(_DETECTORS[rule_id](source, options, limit=detector_limit))
        except CandidateBudgetExceeded as exceeded:
            # The detector's exception carries only its own partial prefix. Prepend what earlier
            # inputs in this call already produced, so a multi-input caller recovering from the
            # overflow loses nothing it had collected.
            raise CandidateBudgetExceeded(
                exceeded.path, candidates=tuple(candidates) + exceeded.candidates
            ) from exceeded
    return candidates


__all__ = ["detect_candidates"]
