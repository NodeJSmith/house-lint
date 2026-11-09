"""HSL104: code copied across a project, found by jscpd and grouped into clusters of copies."""

import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from msgspec import Meta

from house_lint.jscpd import ClonePair, Fragment, find_clones
from house_lint.project_rule import Location, ProjectCandidate
from house_lint.rules.options import RuleOptions
from house_lint.source import LoadedFile

DEFAULT_MIN_TOKENS = 30
DEFAULT_MIN_LINES = 5
DEFAULT_MIN_OCCURRENCES = 3
# Far past any real block, so a bound only catches a typo'd value.
MAX_MIN_TOKENS = 100_000
MAX_MIN_LINES = 100_000
MAX_MIN_OCCURRENCES = 100_000


class HSL104Options(RuleOptions, frozen=True):
    min_tokens: Annotated[int, Meta(ge=1, le=MAX_MIN_TOKENS)] = DEFAULT_MIN_TOKENS
    min_lines: Annotated[int, Meta(ge=1, le=MAX_MIN_LINES)] = DEFAULT_MIN_LINES
    min_occurrences: Annotated[int, Meta(ge=2, le=MAX_MIN_OCCURRENCES)] = DEFAULT_MIN_OCCURRENCES


@dataclass(frozen=True)
class Occurrence:
    """One copy: a file's overlapping fragments merged into one 1-based span.

    `end_line` is the span's last line; `end_column` is exclusive.
    """

    path: Path
    line: int
    column: int
    end_line: int
    end_column: int

    @property
    def line_count(self) -> int:
        return self.end_line - self.line + 1


def run(
    files: tuple[LoadedFile, ...], options: HSL104Options, *, debug: bool
) -> list[ProjectCandidate]:
    """Report each cluster of at least `min_occurrences` copies as one candidate."""
    # jscpd is given resolved paths, so a `..` or symlinked spelling can't make a file a copy of
    # itself; each finding is still reported against the input record's own spelling.
    by_path: dict[Path, LoadedFile] = {}
    for file in files:
        by_path.setdefault(file.path.resolve(), file)
    report = find_clones(tuple(by_path), min_tokens=options.min_tokens, min_lines=options.min_lines)
    if debug:
        # A first clue when a known copy isn't reported: jscpd names no file it skipped.
        print(
            f"house-lint: debug: HSL104: jscpd analyzed {report.analyzed} of {len(by_path)} files "
            f"(a file under min_tokens={options.min_tokens} tokens isn't counted)",
            file=sys.stderr,
        )

    def reported_order(occurrence: Occurrence) -> tuple[str, int, int]:
        return (by_path[occurrence.path].relative_path, occurrence.line, occurrence.column)

    # Ordered by reported path, then position, both within a cluster and across clusters, so the
    # output is deterministic.
    found = [
        sorted(cluster, key=reported_order)
        for cluster in clusters(report.pairs)
        if len(cluster) >= options.min_occurrences
    ]
    found.sort(key=lambda cluster: reported_order(cluster[0]))
    return [candidate(cluster, by_path) for cluster in found]


def clusters(pairs: Iterable[ClonePair]) -> list[list[Occurrence]]:
    """Group clone pairs into clusters of copies.

    Fragments in one file whose line ranges overlap merge into one occurrence first, so a partial
    copy joins its cluster and no region counts twice. Pairs then join occurrences transitively,
    because jscpd reports each later copy against one earlier copy only.
    """
    pair_list = list(pairs)
    occurrences_by_path = merge_fragments(
        fragment for pair in pair_list for fragment in (pair.first, pair.second)
    )
    parent = {
        occurrence: occurrence
        for occurrences in occurrences_by_path.values()
        for occurrence in occurrences
    }

    def root(occurrence: Occurrence) -> Occurrence:
        while parent[occurrence] != occurrence:
            parent[occurrence] = parent[parent[occurrence]]
            occurrence = parent[occurrence]
        return occurrence

    for pair in pair_list:
        first = root(occurrence_of(pair.first, occurrences_by_path[pair.first.path]))
        second = root(occurrence_of(pair.second, occurrences_by_path[pair.second.path]))
        parent[first] = second
    grouped: dict[Occurrence, list[Occurrence]] = defaultdict(list)
    for occurrence in parent:
        grouped[root(occurrence)].append(occurrence)
    return list(grouped.values())


def merge_fragments(fragments: Iterable[Fragment]) -> dict[Path, list[Occurrence]]:
    """Merge each file's fragments whose line ranges overlap, keyed by path, in position order.

    Overlap is decided by lines alone, as in the tuning pass's grouping, so fragments sharing a
    line merge even when their columns don't meet. An occurrence spans from its earliest fragment
    start to its latest fragment end. The result's spans in one file never overlap, which is what
    lets `occurrence_of` find a fragment's occurrence by its start line.
    """
    fragments_by_path: dict[Path, list[Fragment]] = defaultdict(list)
    for fragment in fragments:
        fragments_by_path[fragment.path].append(fragment)
    merged: dict[Path, list[Occurrence]] = {}
    for path, spans in fragments_by_path.items():
        occurrences: list[Occurrence] = []
        for fragment in sorted(spans, key=lambda f: (f.line, f.column)):
            last = occurrences[-1] if occurrences else None
            if last is not None and fragment.line <= last.end_line:
                end = max(
                    (last.end_line, last.end_column), (fragment.end_line, fragment.end_column)
                )
                occurrences[-1] = Occurrence(path, last.line, last.column, *end)
                continue
            occurrences.append(
                Occurrence(
                    path, fragment.line, fragment.column, fragment.end_line, fragment.end_column
                )
            )
        merged[path] = occurrences
    return merged


def occurrence_of(fragment: Fragment, occurrences: list[Occurrence]) -> Occurrence:
    """Return the occurrence, among its own file's, whose merged span holds `fragment`."""
    for occurrence in occurrences:
        if occurrence.line <= fragment.line <= occurrence.end_line:
            return occurrence
    raise AssertionError(f"no merged occurrence holds {fragment}")


def candidate(cluster: list[Occurrence], by_path: dict[Path, LoadedFile]) -> ProjectCandidate:
    """One candidate per cluster. The message counts places, not copies of one block, because
    overlapping fragments can join two different blocks into one cluster."""
    counts = sorted({occurrence.line_count for occurrence in cluster})
    lines = str(counts[0]) if len(counts) == 1 else f"{counts[0]}-{counts[-1]}"
    return ProjectCandidate(
        f"duplicated code of {lines} lines in {len(cluster)} places",
        tuple(
            Location(
                by_path[occurrence.path],
                occurrence.line,
                occurrence.column,
                occurrence.end_line,
                occurrence.end_column,
            )
            for occurrence in cluster
        ),
    )


__all__ = ["HSL104Options", "clusters", "run"]
