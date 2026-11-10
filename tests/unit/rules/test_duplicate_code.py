"""HSL104: grouping jscpd's pairs into clusters, and the candidates a real jscpd run gives."""

from pathlib import Path

import msgspec
import pytest
from _source_helpers import load, write_file

from house_lint.jscpd import ClonePair, Fragment
from house_lint.project_rule import ProjectRuleFailure
from house_lint.results import Finding
from house_lint.rules.duplicate_code import (
    MAX_OPTION_VALUE,
    HSL104Options,
    Occurrence,
    clusters,
    match_new,
    run,
)
from house_lint.source import LoadedFile

A, B, C = Path("/p/a.py"), Path("/p/b.py"), Path("/p/c.py")
BLOCK = """\
    total = value + other + third
    if total > 10:
        result = total * 2
    else:
        result = total - 1
    items = [result, value, other, third]
    return sorted(items, key=lambda x: -x)
"""
SECOND = """\
def summarize(rows, limit, offset):
    picked = [row for row in rows if row.score > limit]
    picked.sort(key=lambda row: (row.score, row.name))
    names = [row.name.upper() for row in picked[offset:]]
    joined = ", ".join(names) + " and " + str(len(rows))
    return {"names": names, "joined": joined, "count": len(picked)}
"""


def pair(first: tuple[Path, int, int], second: tuple[Path, int, int]) -> ClonePair:
    """A pair given by (path, start line, end line); grouping reads lines only."""

    def fragment(path: Path, start: int, end: int) -> Fragment:
        return Fragment(path, start, 1, end, 10)

    return ClonePair(fragment(*first), fragment(*second))


def spans(found: list[list[Occurrence]]) -> list[set[tuple[str, int, int]]]:
    return [
        {(occurrence.path.name, occurrence.line, occurrence.end_line) for occurrence in cluster}
        for cluster in found
    ]


def test_star_shaped_pairs_form_one_cluster_of_three() -> None:
    found = clusters([pair((A, 1, 8), (B, 1, 8)), pair((A, 1, 8), (C, 1, 8))])

    assert spans(found) == [{("a.py", 1, 8), ("b.py", 1, 8), ("c.py", 1, 8)}]


def test_a_partial_third_copy_joins_the_cluster_without_counting_a_region_twice() -> None:
    found = clusters([pair((A, 1, 30), (B, 1, 30)), pair((A, 10, 30), (C, 5, 25))])

    assert spans(found) == [{("a.py", 1, 30), ("b.py", 1, 30), ("c.py", 5, 25)}]


def test_overlapping_fragments_merge_from_the_earliest_start_to_the_latest_end() -> None:
    first = ClonePair(Fragment(A, 1, 5, 10, 3), Fragment(B, 1, 1, 10, 3))
    second = ClonePair(Fragment(A, 1, 9, 12, 7), Fragment(C, 1, 1, 3, 1))
    third = ClonePair(Fragment(A, 10, 1, 12, 2), Fragment(C, 20, 1, 22, 1))

    [cluster] = clusters([first, second, third])

    assert Occurrence(A, 1, 5, 12, 7) in cluster


def test_a_pair_inside_one_merged_region_adds_nothing() -> None:
    found = clusters([pair((A, 1, 20), (A, 5, 25))])

    assert spans(found) == [{("a.py", 1, 25)}]


def test_two_separate_copies_in_one_file_are_two_occurrences() -> None:
    found = clusters([pair((A, 1, 8), (A, 20, 27))])

    assert spans(found) == [{("a.py", 1, 8), ("a.py", 20, 27)}]


def test_unrelated_pairs_stay_separate_clusters() -> None:
    found = clusters([pair((A, 1, 8), (B, 1, 8)), pair((A, 40, 50), (C, 1, 11))])

    assert sorted(spans(found), key=len) == [
        {("a.py", 1, 8), ("b.py", 1, 8)},
        {("a.py", 40, 50), ("c.py", 1, 11)},
    ]


def copies(directory: Path, *names: str) -> tuple[LoadedFile, ...]:
    return tuple(
        load(
            write_file(directory, f"{name}.py", f"def handle_{name}(value, other, third):\n{BLOCK}")
        )
        for name in names
    )


def test_a_cluster_becomes_one_candidate_located_at_every_copy(tmp_path: Path) -> None:
    files = copies(tmp_path, "c", "a", "b")

    [candidate] = run(files, HSL104Options(), debug=False)

    assert candidate.message == "duplicated code of 8 lines in 3 places"
    assert [
        (loc.file.relative_path, loc.line, loc.column, loc.end_line, loc.end_column)
        for loc in candidate.locations
    ] == [("a.py", 1, 13, 8, 43), ("b.py", 1, 13, 8, 43), ("c.py", 1, 13, 8, 43)]
    assert {loc.file for loc in candidate.locations} == set(files)


def test_a_cluster_under_the_occurrence_minimum_is_dropped(tmp_path: Path) -> None:
    files = copies(tmp_path, "a", "b")

    assert run(files, HSL104Options(), debug=False) == []
    assert len(run(files, HSL104Options(min_occurrences=2), debug=False)) == 1


def test_copies_of_different_lengths_report_a_line_range(tmp_path: Path) -> None:
    files = (
        load(
            write_file(tmp_path, "a.py", f"def handle_a(value, other, third):\n{BLOCK}\n{SECOND}")
        ),
        load(
            write_file(tmp_path, "b.py", f"def handle_b(value, other, third):\n{BLOCK}\n{SECOND}")
        ),
        load(write_file(tmp_path, "c.py", f"x = 0\n\n\n{SECOND}")),
    )

    [candidate] = run(files, HSL104Options(), debug=False)

    counts = sorted(
        location.end_line - location.line + 1
        for location in candidate.locations
        if location.line is not None and location.end_line is not None
    )
    assert counts[0] < counts[-1]
    assert candidate.message == f"duplicated code of {counts[0]}-{counts[-1]} lines in 3 places"
    c_location = next(loc for loc in candidate.locations if loc.file.relative_path == "c.py")
    assert c_location.line == 4


def test_no_copies_gives_no_candidates(tmp_path: Path) -> None:
    files = (
        load(write_file(tmp_path, "a.py", "value = 1\n")),
        load(write_file(tmp_path, "b.py", SECOND)),
    )

    assert run(files, HSL104Options(min_occurrences=2), debug=False) == []


def test_debug_reports_how_many_files_jscpd_analyzed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = (*copies(tmp_path, "a", "b"), load(write_file(tmp_path, "tiny.py", "value = 1\n")))

    run(files, HSL104Options(), debug=True)

    assert "HSL104: jscpd analyzed 2 of 3 files" in capsys.readouterr().err


@pytest.mark.parametrize("target", ["c.txt", "c.foo", "cnoext"])
def test_a_py_symlink_to_a_file_without_the_suffix_fails_loudly(
    tmp_path: Path, target: str
) -> None:
    files = copies(tmp_path, "a", "b")
    write_file(tmp_path, target, f"def handle_c(value, other, third):\n{BLOCK}")
    (tmp_path / "c.py").symlink_to(tmp_path / target)

    with pytest.raises(ProjectRuleFailure, match=f"read .*{target} as Python"):
        run((*files, load(tmp_path / "c.py")), HSL104Options(), debug=False)


def test_a_symlinked_spelling_of_a_file_is_not_a_copy_of_itself(tmp_path: Path) -> None:
    [real] = copies(tmp_path, "a")
    (tmp_path / "link.py").symlink_to(tmp_path / "a.py")
    link = load(tmp_path / "link.py")

    assert run((real, link), HSL104Options(min_occurrences=2), debug=False) == []


@pytest.mark.parametrize(
    "table",
    [
        {"min_occurrences": 1},
        {"min_tokens": 0},
        {"min_lines": 0},
        {"min_tokens": MAX_OPTION_VALUE + 1},
        {"timeout_seconds": 5},  # the timeout is a constant, not an option
    ],
)
def test_options_outside_their_bounds_are_rejected(table: dict[str, int]) -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.convert(table, HSL104Options)


def copy_at(path: str, line: int, end_line: int, places: int = 3) -> Finding:
    message = f"duplicated code of {end_line - line + 1} lines in {places} places"
    return Finding("HSL104", path, line, 1, end_line, 10, message)


def added(head: dict[Finding, str], base: dict[Finding, str], renames=None) -> tuple[Finding, ...]:
    return match_new(list(head), list(base), head_text=head, base_text=base, renames=renames or {})


def test_a_fourth_copy_is_the_only_new_occurrence_though_the_message_changed() -> None:
    base = {copy_at(name, 1, 5): "blockAB" for name in ("a.py", "b.py", "c.py")}
    head = {copy_at(name, 1, 5, places=4): "blockAB" for name in ("a.py", "b.py", "c.py", "d.py")}

    assert added(head, base) == (copy_at("d.py", 1, 5, places=4),)


def test_an_occurrence_whose_span_grew_or_shrank_pairs_by_containment() -> None:
    # A rename reorders jscpd's pairs, so the copies are matched over different lines.
    base = {
        copy_at("a.py", 1, 5): "block",
        copy_at("b.py", 1, 5): "block",
        copy_at("c.py", 1, 5): "block",
    }
    head = {
        copy_at("a.py", 1, 5): "block",
        copy_at("b.py", 1, 6): "block+",
        copy_at("renamed.py", 2, 5): "lock",
    }

    assert added(head, base, renames={"renamed.py": "c.py"}) == ()


def test_a_short_new_copy_cannot_take_a_long_old_one_and_blame_an_untouched_file() -> None:
    long_text = "x" * 200
    base = {
        copy_at("a.py", 1, 40, places=2): long_text,
        copy_at("b.py", 1, 40, places=2): long_text,
    }
    short = copy_at("c.py", 1, 5)
    head = {short: "x" * 30, **{copy_at(f, 1, 40): long_text for f in ("a.py", "b.py")}}

    assert added(head, base) == (short,)


def test_containment_prefers_the_closest_length() -> None:
    base = {copy_at("a.py", 1, 9): "abcdefghij", copy_at("b.py", 1, 3): "abcd"}
    head = {copy_at("c.py", 1, 3): "abc", copy_at("d.py", 1, 9): "abcdefghi"}

    assert added(head, base) == ()


def test_a_copy_unrelated_to_every_base_occurrence_is_new() -> None:
    base = {copy_at("a.py", 1, 5): "block"}
    other = copy_at("b.py", 1, 5)

    assert added({other: "different"}, base) == (other,)
