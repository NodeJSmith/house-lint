"""Pairing a rule's head findings with its base findings for `--base`."""

from house_lint.matching import match_by_key
from house_lint.results import Finding


def finding(path: str, line: int, message: str = "import inside function body") -> Finding:
    return Finding("HSL002", path, line, 5, line, 16, message)


def new(head, base, *, head_text=None, base_text=None, renames=None, line_map=None):
    return match_by_key(
        head,
        base,
        head_text=head_text or {},
        base_text=base_text or {},
        renames=renames or {},
        line_map=line_map or {},
    )


def test_findings_with_equal_text_pair_wherever_they_moved() -> None:
    old = finding("a.py", 3)
    moved = finding("b.py", 40)

    assert new([moved], [old], head_text={moved: "importjson"}, base_text={old: "importjson"}) == ()


def test_identity_is_a_multiset_so_one_more_copy_of_a_finding_is_new() -> None:
    base = [finding("a.py", 3), finding("a.py", 9)]
    head = [finding("a.py", 3), finding("a.py", 9), finding("a.py", 20)]
    text = {item: "importjson" for item in head + base}

    assert new(head, base, head_text=text, base_text=text) == (head[2],)


def test_a_swap_with_different_text_is_new() -> None:
    old = finding("a.py", 3)
    swapped = finding("a.py", 9)

    result = new([swapped], [old], head_text={swapped: "importos"}, base_text={old: "importjson"})

    assert result == (swapped,)


def test_a_finding_equal_as_a_value_but_different_text_is_new() -> None:
    # The same rule, path, span, and message at both commits, over different code.
    shared = finding("a.py", 3)

    assert new([shared], [shared], head_text={shared: "importos"}, base_text={shared: "x"}) == (
        shared,
    )


def test_the_same_file_pairs_first_so_the_reported_finding_is_the_added_one() -> None:
    base = [finding("b.py", 1)]
    head = [finding("a.py", 1), finding("b.py", 1)]
    text = {item: "importjson" for item in head + base}

    assert new(head, base, head_text=text, base_text=text) == (head[0],)


def test_a_renamed_file_counts_as_the_same_file() -> None:
    base = [finding("old.py", 1)]
    added, renamed = finding("a.py", 1), finding("new.py", 1)
    text = {item: "importjson" for item in [added, renamed, *base]}

    with_rename = new(
        [added, renamed], base, head_text=text, base_text=text, renames={"new.py": "old.py"}
    )
    without = new([added, renamed], base, head_text=text, base_text=text)

    assert with_rename == (added,)
    # Without it, `a.py` comes first in the any-file pass and takes `old.py`'s finding.
    assert without == (renamed,)


def test_a_finding_without_text_keys_on_its_message_alone() -> None:
    old = finding("a.py", 1, "constant defined after the first class or function")
    head = finding("b.py", 7, "constant defined after the first class or function")

    assert new([head], [old]) == ()
    assert new([finding("b.py", 7, "other message")], [old]) == (
        finding("b.py", 7, "other message"),
    )


def test_within_a_file_the_line_map_decides_which_identical_finding_is_new() -> None:
    base = [finding("m.py", 2), finding("m.py", 5)]
    head = [finding("m.py", 2), finding("m.py", 5), finding("m.py", 8)]
    text = {item: "importos" for item in head + base}

    # A function was inserted at the top: old lines 2 and 5 are now 5 and 8.
    result = new(head, base, head_text=text, base_text=text, line_map={"m.py": {5: 2, 8: 5}})

    assert result == (head[0],)
