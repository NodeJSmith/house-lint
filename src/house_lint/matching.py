"""Pairing one rule's findings at a head commit with its findings at a base commit.

`house-lint check --base` scans two trees and reports only the head findings left unpaired here.
A leaf module, importing only result types, so the rule modules that supply their own matcher can
build on these passes without importing the gate.
"""

from collections import defaultdict, deque
from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import Protocol, TypeAlias

from house_lint.results import Finding, location_sort_key

SpanText: TypeAlias = Mapping[Finding, str]
"""One scan's findings mapped to their span text with all whitespace removed. A finding absent
from it keys on its message alone: it has no location, or its file couldn't be loaded or decoded.
Each scan has its own, because a head and a base finding can be equal as values yet cover
different text."""

RenameMap: TypeAlias = Mapping[str, str]
"""Root-relative head path to root-relative base path, holding only renamed files."""

KeyFunction: TypeAlias = Callable[[Finding, SpanText], Hashable]
"""A finding's pairing key, given the span text of the scan it came from."""


class BaseMatcher(Protocol):
    """Return the head findings of one rule that pair with none of its base findings."""

    def __call__(
        self,
        head: Sequence[Finding],
        base: Sequence[Finding],
        *,
        head_text: SpanText,
        base_text: SpanText,
        renames: RenameMap,
    ) -> tuple[Finding, ...]: ...


def exact_passes(
    head: Sequence[Finding],
    base: Sequence[Finding],
    *,
    key: KeyFunction,
    head_text: SpanText,
    base_text: SpanText,
    renames: RenameMap,
) -> tuple[list[Finding], list[Finding]]:
    """Pair findings one to one on equal `key`: first within the same file, then in any file.

    Each pass visits the unpaired head findings in (path, line) order and takes the first unpaired
    base finding, in the same order, with an equal key. A head file's base path comes through
    `renames`. Among identical findings in one file the count is exact, but which one is left
    unpaired is first-come, so a finding added above identical old ones reports as the last of
    them. Returns the unpaired head and base findings, each in (path, line) order.
    """
    head_left = sorted(head, key=location_sort_key)
    base_left = sorted(base, key=location_sort_key)
    for same_file in (True, False):
        buckets: defaultdict[tuple[str | None, Hashable], deque[int]] = defaultdict(deque)
        for index, finding in enumerate(base_left):
            path = finding.path if same_file else None
            buckets[(path, key(finding, base_text))].append(index)
        paired: set[int] = set()
        unpaired_head: list[Finding] = []
        for finding in head_left:
            path = renames.get(finding.path, finding.path) if same_file else None
            candidates = buckets.get((path, key(finding, head_text)))
            if candidates:
                paired.add(candidates.popleft())
                continue
            unpaired_head.append(finding)
        head_left = unpaired_head
        base_left = [finding for index, finding in enumerate(base_left) if index not in paired]
    return head_left, base_left


def message_and_text(finding: Finding, text: SpanText) -> Hashable:
    return (finding.message, text.get(finding))


def match_by_key(
    head: Sequence[Finding],
    base: Sequence[Finding],
    *,
    head_text: SpanText,
    base_text: SpanText,
    renames: RenameMap,
) -> tuple[Finding, ...]:
    """The default matcher: equal message and whitespace-free span text, the same file first.

    The key holds no path or line, so a finding that moves, or is reformatted, still pairs.
    """
    new, _ = exact_passes(
        head,
        base,
        key=message_and_text,
        head_text=head_text,
        base_text=base_text,
        renames=renames,
    )
    return tuple(new)


def strip_whitespace(text: str) -> str:
    return "".join(text.split())


__all__ = [
    "BaseMatcher",
    "KeyFunction",
    "RenameMap",
    "SpanText",
    "exact_passes",
    "match_by_key",
    "message_and_text",
    "strip_whitespace",
]
