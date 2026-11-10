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

LineMap: TypeAlias = Mapping[str, Mapping[int, int]]
"""Root-relative head path to each unchanged head line's 1-based base line, from a diff of the
file's two versions. Covers the files with a head finding that the gate could read at both
commits."""

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
        line_map: LineMap,
    ) -> tuple[Finding, ...]: ...


def exact_passes(
    head: Sequence[Finding],
    base: Sequence[Finding],
    *,
    key: KeyFunction,
    head_text: SpanText,
    base_text: SpanText,
    renames: RenameMap,
    line_map: LineMap,
) -> tuple[list[Finding], list[Finding]]:
    """Pair findings one to one on equal `key`: first within the same file, then in any file.

    Within a file, a head finding first pairs with an equal-keyed base finding on the line its own
    line came from (`line_map`), so a finding inserted above identical old ones is the one left
    unpaired. The rest then pair first-fit: each unpaired head finding, in (path, line) order,
    takes the first unpaired base finding with an equal key, in the same file and then in any. A
    head file's base path comes through `renames`. Returns the unpaired head and base findings,
    each in (path, line) order.
    """
    head_sorted = sorted(head, key=location_sort_key)
    base_sorted = sorted(base, key=location_sort_key)
    head_keys = [key(finding, head_text) for finding in head_sorted]
    base_keys = [key(finding, base_text) for finding in base_sorted]
    head_paired: set[int] = set()
    base_paired: set[int] = set()
    on_line: dict[tuple[str, Hashable, int | None], deque[int]] = defaultdict(deque)
    for index, finding in enumerate(base_sorted):
        on_line[(finding.path, base_keys[index], finding.line)].append(index)
    for index, finding in enumerate(head_sorted):
        base_line = line_map.get(finding.path, {}).get(finding.line or 0)
        if base_line is None:
            continue
        path = renames.get(finding.path, finding.path)
        candidates = on_line.get((path, head_keys[index], base_line))
        if candidates:
            base_paired.add(candidates.popleft())
            head_paired.add(index)
    for same_file in (True, False):
        buckets: defaultdict[tuple[str | None, Hashable], deque[int]] = defaultdict(deque)
        for index, finding in enumerate(base_sorted):
            if index not in base_paired:
                path = finding.path if same_file else None
                buckets[(path, base_keys[index])].append(index)
        for index, finding in enumerate(head_sorted):
            if index in head_paired:
                continue
            path = renames.get(finding.path, finding.path) if same_file else None
            candidates = buckets.get((path, head_keys[index]))
            if candidates:
                base_paired.add(candidates.popleft())
                head_paired.add(index)
    return (
        [finding for index, finding in enumerate(head_sorted) if index not in head_paired],
        [finding for index, finding in enumerate(base_sorted) if index not in base_paired],
    )


def message_and_text(finding: Finding, text: SpanText) -> Hashable:
    return (finding.message, text.get(finding))


def match_by_key(
    head: Sequence[Finding],
    base: Sequence[Finding],
    *,
    head_text: SpanText,
    base_text: SpanText,
    renames: RenameMap,
    line_map: LineMap,
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
        line_map=line_map,
    )
    return tuple(new)


def strip_whitespace(text: str) -> str:
    return "".join(text.split())


__all__ = [
    "BaseMatcher",
    "KeyFunction",
    "LineMap",
    "RenameMap",
    "SpanText",
    "exact_passes",
    "match_by_key",
    "message_and_text",
    "strip_whitespace",
]
