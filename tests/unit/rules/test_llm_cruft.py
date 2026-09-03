import pytest

from house_lint.analysis import (
    MAX_CANDIDATES_PER_FILE,
    CandidateBudgetExceeded,
    SourceKind,
    StatementKey,
)
from house_lint.rules.llm_cruft import detect
from house_lint.source import SourceFile


def test_detects_dividers_and_filler_in_comments_and_docstrings(write_sample) -> None:
    path = write_sample("""\
        # ======
        # In Order To prepare the pool
        \"\"\"We leverage the pool in order to batch.\"\"\"
        value = 1
    """)

    findings = detect(SourceFile(path, path.parent), None)

    assert [(finding.line, finding.message) for finding in findings] == [
        (1, "section-divider comment"),
        (2, "filler - use 'to'"),
        (3, "filler - use 'to'"),
        (3, "filler - use 'use'"),
    ]
    assert all(finding.rule_id == "HSL001" for finding in findings)
    assert findings[0].source_kind is SourceKind.NO_OWNER
    assert findings[0].owner is None
    assert findings[2].source_kind is SourceKind.STATEMENT
    assert findings[2].owner == StatementKey(3, 1, 3, 46)


def test_multiline_docstring_reports_the_matching_line_and_keeps_statement_owner(
    write_sample,
) -> None:
    path = write_sample('"""Explain the operation.\nPlease note that it is temporary.\n"""\n')

    [finding] = detect(SourceFile(path, path.parent), None)

    assert (finding.line, finding.column, finding.end_line, finding.end_column) == (2, 1, 2, 34)
    assert finding.source_kind is SourceKind.STATEMENT
    assert finding.owner == StatementKey(1, 1, 3, 4)


def test_preserves_divider_thresholds_and_excludes_ordinary_strings(write_sample) -> None:
    path = write_sample("""\
        # ---
        # --- Helpers ---
        label = "in order to proceed"
    """)

    findings = detect(SourceFile(path, path.parent), None)

    assert [(finding.line, finding.message) for finding in findings] == [
        (2, "section-divider comment")
    ]


def test_comment_on_a_statement_keeps_statement_provenance(write_sample) -> None:
    path = write_sample("value = 1  # Please note that this is temporary\n")

    [finding] = detect(SourceFile(path, path.parent), None)

    assert finding.source_kind is SourceKind.STATEMENT
    assert finding.owner == StatementKey(1, 1, 1, 10)


def test_comment_on_a_multiline_statement_uses_its_narrowest_owner(write_sample) -> None:
    path = write_sample(
        "def prepare() -> None:\n"
        "    value = (\n"
        "        1\n"
        "    )  # Please note that this is temporary\n"
    )

    [finding] = detect(SourceFile(path, path.parent), None)

    assert finding.source_kind is SourceKind.STATEMENT
    assert finding.owner == StatementKey(2, 5, 4, 6)


def test_standalone_body_comment_has_no_owner(write_sample) -> None:
    path = write_sample(
        "def prepare() -> None:\n    # Please note that this is temporary\n    value = 1\n"
    )

    [finding] = detect(SourceFile(path, path.parent), None)

    assert finding.source_kind is SourceKind.NO_OWNER
    assert finding.owner is None


@pytest.mark.parametrize(
    ("comment", "message"),
    [
        ("It is important to note this", "filler - drop it; state the fact directly"),
        ("It should be noted this", "filler - drop it; state the fact directly"),
        ("It is worth noting this", "filler - drop it; state the fact directly"),
        ("Please note that this", "filler - drop 'please note that'"),
        ("Needless to say, this", "filler - drop it"),
        ("Due to the fact that this", "filler - use 'because'"),
        ("As mentioned earlier, this", "filler - name the thing directly"),
        ("Leveraged this", "filler - use 'use'"),
        ("Utilizing this", "filler - use 'use'"),
        ("Utilized this", "filler - use 'use'"),
        ("Facilitating this", "filler - use 'help' or be specific"),
        ("Facilitated this", "filler - use 'help' or be specific"),
    ],
)
def test_detects_remaining_retained_filler_patterns_in_comments(
    write_sample, comment: str, message: str
) -> None:
    path = write_sample(f"# {comment}\nvalue = 1\n")

    [finding] = detect(SourceFile(path, path.parent), None)

    assert (finding.line, finding.message) == (1, message)


def test_ignores_ordinary_comments(write_sample) -> None:
    path = write_sample("# resolve the owner app from the confirmed app_key\nvalue = 1\n")

    assert detect(SourceFile(path, path.parent), None) == []


def test_limits_materialized_candidates_when_requested(write_sample) -> None:
    path = write_sample("\n".join("# utilize this" for _ in range(MAX_CANDIDATES_PER_FILE + 2)))

    with pytest.raises(CandidateBudgetExceeded):
        detect(SourceFile(path, path.parent), None, limit=MAX_CANDIDATES_PER_FILE)


def test_coding_cookies_and_modelines_are_not_dividers(write_sample) -> None:
    path = write_sample(
        "#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n# -*- mode: python -*-\nvalue = 1\n"
    )

    assert detect(SourceFile(path, path.parent), None) == []


def test_all_hash_divider_rows_are_flagged(write_sample) -> None:
    path = write_sample("########\nvalue = 1\n#### section ####\nother = 2\n")

    findings = detect(SourceFile(path, path.parent), None)

    assert [(finding.line, finding.message) for finding in findings] == [
        (1, "section-divider comment"),
        (3, "section-divider comment"),
    ]
