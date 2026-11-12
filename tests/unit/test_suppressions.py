import re
import tokenize

import pytest
from _source_helpers import PYTHON_ONLY

from house_lint.analysis import CandidateFinding, SourceKind, statement_key
from house_lint.python_source import PythonSource
from house_lint.rule_catalog import project_rule
from house_lint.rules.exception_names import HSL103Options
from house_lint.rules.exception_names import detect as detect_exception_names
from house_lint.rules.lazy_imports import detect as detect_lazy_imports
from house_lint.rules.llm_cruft import detect as detect_llm_cruft
from house_lint.rules.spec_tokens import HSL101Options, TokenFamily
from house_lint.rules.spec_tokens import detect as detect_spec_tokens
from house_lint.suppressions import (
    SuppressionResult,
    _Claim,
    _Pragma,
    _redundant_claims,
    apply_suppressions,
    scan_pragmas,
)

PARITY_RULE = "HSL198"
WITH_HSL901 = {"HSL001", "HSL002", "HSL900", "HSL901"}
PARITY_SAMPLES = [
    f"value = 1  # house-lint: ignore[{PARITY_RULE}] - deliberate copy\n",
    f"value = 1  # house-lint: ignore[HSL002,{PARITY_RULE}] - deliberate copy\n",
    f"# house-lint: ignore-next[{PARITY_RULE}] - deliberate copy\nvalue = 1\n",
    f"# house-lint: ignore-file[{PARITY_RULE}] - generated module\nvalue = 1\n",
    f"value = 1\n# house-lint: ignore-file[{PARITY_RULE}] - misplaced here\n",
    f"value = 1  # house-lint: ignore[{PARITY_RULE}] - x\n",
    f"value = 1  # house-lint: ignore[{PARITY_RULE},HSL900] - deliberate copy\n",
]


def _candidate(
    source: PythonSource, rule_id: str, statement_line: int, finding_line: int | None = None
) -> CandidateFinding:
    statement = next(
        statement for statement in source.statements if statement.lineno == statement_line
    )
    owner = statement_key(statement)
    line = finding_line or statement_line
    return CandidateFinding(
        rule_id,
        source.relative_path,
        "test finding",
        line,
        1,
        line,
        2,
        SourceKind.STATEMENT,
        owner,
    )


def test_trailing_ignore_suppresses_all_owned_rule_candidates(parse_sample) -> None:
    source = parse_sample(
        "value = (\n    1\n)  # house-lint: ignore[HSL001] - generated value\n",
    )
    candidates = (_candidate(source, "HSL001", 1), _candidate(source, "HSL001", 1, 2))
    result = apply_suppressions(source, candidates, {"HSL001", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 2
    assert not hasattr(result, "visible_candidates")


def test_trailing_ignore_suppresses_a_simple_statement(parse_sample) -> None:
    source = parse_sample("value = 1  # house-lint: ignore[HSL001] - generated value\n")

    result = apply_suppressions(source, (_candidate(source, "HSL001", 1),), {"HSL001", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_trailing_ignore_owns_the_last_statement_on_a_semicolon_separated_line(
    parse_sample,
) -> None:
    source = parse_sample(
        "def load() -> None:\n    value = 1; import module  # house-lint: ignore[HSL002] - generated import\n",
    )
    candidates = tuple(detect_lazy_imports(source, None))

    result = apply_suppressions(source, candidates, {"HSL002", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_trailing_ignore_owns_interior_comment_findings_in_multiline_statements(
    parse_sample,
) -> None:
    source = parse_sample(
        "value = (  # house-lint: ignore[HSL101] - generated value\n    1  # T01\n)\n",
    )
    candidates = tuple(
        detect_spec_tokens(
            source,
            HSL101Options((TokenFamily(prefixes=("T",), scopes=("comments",), min_digits=2),)),
        )
    )

    result = apply_suppressions(source, candidates, {"HSL101", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_trailing_ignore_suppresses_hsl001_inline_comment_in_multiline_statement(
    parse_sample,
) -> None:
    source = parse_sample(
        "value = (\n    1\n)  # house-lint: ignore[HSL001] - Please note that generated value\n",
    )
    candidates = tuple(detect_llm_cruft(source, None))

    result = apply_suppressions(source, candidates, {"HSL001", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_header_ignore_does_not_own_comment_findings_in_its_body(parse_sample) -> None:
    source = parse_sample(
        "if condition:  # house-lint: ignore[HSL101] - generated branch\n    # T01\n    pass\n",
    )
    candidates = tuple(
        detect_spec_tokens(
            source,
            HSL101Options((TokenFamily(prefixes=("T",), scopes=("comments",), min_digits=2),)),
        )
    )

    result = apply_suppressions(source, candidates, {"HSL101", "HSL900", "HSL901"})

    assert [finding.rule_id for finding in result.findings] == ["HSL101", "HSL901"]
    assert result.suppressed_count == 0


def test_ignore_next_stays_in_its_lexical_suite(parse_sample) -> None:
    source = parse_sample(
        "if condition:\n    # house-lint: ignore-next[HSL002] - circular import\n    # ordinary comment\n\n    import package\n",
    )
    result = apply_suppressions(source, (_candidate(source, "HSL002", 5),), {"HSL002", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_ignore_next_owns_a_statement_in_a_match_case_suite(parse_sample) -> None:
    source = parse_sample(
        "match value:\n    case _:\n        # house-lint: ignore-next[HSL002] - circular import\n        import package\n",
    )

    result = apply_suppressions(source, (_candidate(source, "HSL002", 4),), {"HSL002", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_ignore_next_cannot_leave_its_lexical_suite(parse_sample) -> None:
    source = parse_sample(
        "if condition:\n    pass\n    # house-lint: ignore-next[HSL002] - circular import\n\nvalue = 1\n",
    )
    result = apply_suppressions(source, (_candidate(source, "HSL002", 5),), {"HSL002", "HSL900"})

    assert [finding.rule_id for finding in result.findings] == ["HSL002", "HSL900"]
    assert result.suppressed_count == 0


def test_ignore_next_suppresses_a_standalone_comment_finding_in_its_gap(parse_sample) -> None:
    source = parse_sample(
        "# house-lint: ignore-next[HSL001] - decorative divider\n# ------\nvalue = 1\n",
    )
    candidates = tuple(detect_llm_cruft(source, None))

    result = apply_suppressions(source, candidates, {"HSL001", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_ignore_next_does_not_reach_a_standalone_comment_above_the_pragma(parse_sample) -> None:
    source = parse_sample(
        "# ------\n# house-lint: ignore-next[HSL001] - decorative divider\nvalue = 1\n",
    )
    candidates = tuple(detect_llm_cruft(source, None))

    result = apply_suppressions(source, candidates, {"HSL001", "HSL900", "HSL901"})

    assert [finding.rule_id for finding in result.findings] == ["HSL001", "HSL901"]
    assert result.suppressed_count == 0


def test_ignore_next_gap_reaches_through_a_decorator_prologue(parse_sample) -> None:
    source = parse_sample(
        "# house-lint: ignore-next[HSL001] - decorative divider\n"
        "# ------\n"
        "@decorator\n"
        "def build() -> None:\n"
        "    pass\n",
    )
    candidates = tuple(detect_llm_cruft(source, None))

    result = apply_suppressions(source, candidates, {"HSL001", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_ignore_next_suppresses_filler_triggered_by_its_own_reason_text(parse_sample) -> None:
    source = parse_sample(
        "# house-lint: ignore-next[HSL001] - in order to keep this readable\nvalue = 1\n",
    )
    candidates = tuple(detect_llm_cruft(source, None))

    result = apply_suppressions(source, candidates, {"HSL001", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_ignore_next_requires_a_comment_only_line(parse_sample) -> None:
    source = parse_sample(
        "value = 1  # house-lint: ignore-next[HSL002] - circular import\nnext_value = 2\n",
    )
    result = apply_suppressions(source, (_candidate(source, "HSL002", 2),), {"HSL002", "HSL900"})

    assert [finding.rule_id for finding in result.findings] == ["HSL002", "HSL900"]
    assert result.suppressed_count == 0


def test_file_ignore_suppresses_statement_file_and_filename_candidates(parse_sample) -> None:
    source = parse_sample(
        '#!/usr/bin/env python\n# coding: utf-8\n\n# generated module\n"""Docs."""\n'
        "from __future__ import annotations\n# house-lint: ignore-file[HSL101, HSL102] - generated module\n"
        "value = 1\n",
    )
    candidates = (
        _candidate(source, "HSL101", 8),
        CandidateFinding(
            "HSL101", source.relative_path, "filename", None, None, None, None, SourceKind.FILENAME
        ),
        CandidateFinding(
            "HSL102", source.relative_path, "length", None, None, None, None, SourceKind.FILE
        ),
    )
    result = apply_suppressions(source, candidates, {"HSL101", "HSL102", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 3


def test_file_ignore_rejects_a_non_docstring_string_before_the_pragma(parse_sample) -> None:
    source = parse_sample(
        '"""Docs."""\n"not a docstring"\n# house-lint: ignore-file[HSL001] - generated module\n',
    )
    result = apply_suppressions(source, (_candidate(source, "HSL001", 2),), {"HSL001", "HSL900"})

    assert [finding.rule_id for finding in result.findings] == ["HSL001", "HSL900"]
    assert result.suppressed_count == 0


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        (
            {"HSL001", "HSL900"},
            [(3, "HSL900", "HSL900 cannot be suppressed")],
        ),
        (
            {"HSL001", "HSL900", "HSL901"},
            [
                (3, "HSL900", "HSL900 cannot be suppressed"),
                (2, "HSL901", "redundant suppression for HSL001"),
            ],
        ),
    ],
    ids=["hsl901-off", "hsl901-on"],
)
def test_overlapping_pragmas_suppress_and_only_hsl901_reports_the_redundant_one(
    parse_sample, enabled: set[str], expected: list[tuple[int, str, str]]
) -> None:
    source = parse_sample(
        "# house-lint: ignore-file[HSL001] - generated module\n"
        "value = 1  # house-lint: ignore[HSL001] - valid reason\n"
        "# house-lint: ignore[HSL900] - valid reason\n",
    )
    candidates = (_candidate(source, "HSL001", 2),)
    result = apply_suppressions(source, candidates, enabled)

    assert [
        (finding.line, finding.rule_id, finding.message) for finding in result.findings
    ] == expected
    assert result.suppressed_count == 1


def test_invalid_pragma_ids_reasons_and_disabled_or_unknown_rules_are_diagnostics(
    parse_sample,
) -> None:
    source = parse_sample(
        "# house-lint: ignore-next[HSL01] - valid reason\n"
        "value = 1\n"
        "# house-lint: ignore-next[HSL001,HSL001] - valid reason\n"
        "value = 2\n"
        "# house-lint: ignore-next[HSL900] - valid reason\n"
        "value = 3\n"
        "# house-lint: ignore-next[HSL001] - !!\n"
        "value = 4\n"
        "# house-lint: ignore-next[HSL999] - valid reason\n"
        "value = 5\n"
        "# house-lint: ignore-next[HSL002] - valid reason\n"
        "value = 6\n",
    )
    result = apply_suppressions(source, (), {"HSL001", "HSL900"})

    assert len(result.findings) == 5
    assert all(finding.rule_id == "HSL900" for finding in result.findings)
    assert "unknown" in result.findings[4].message

    with_stale = apply_suppressions(source, (), {"HSL001", "HSL900", "HSL901"})

    assert with_stale.findings[:5] == result.findings
    assert [
        (finding.rule_id, finding.line, finding.message) for finding in with_stale.findings[5:]
    ] == [("HSL901", 11, "unused suppression for disabled rule HSL002")]


def test_empty_all_and_glob_ids_are_malformed(parse_sample) -> None:
    source = parse_sample(
        "# house-lint: ignore-next[] - valid reason\nvalue = 1\n"
        "# house-lint: ignore-next[all] - valid reason\nvalue = 2\n"
        "# house-lint: ignore-next[HSL*] - valid reason\nvalue = 3\n",
    )

    result = apply_suppressions(source, (), {"HSL001", "HSL900"})

    assert [finding.message for finding in result.findings] == [
        "malformed suppression rule IDs",
        "malformed suppression rule IDs",
        "malformed suppression rule IDs",
    ]


def test_misplaced_and_unconsumed_pragmas_are_diagnostics(parse_sample) -> None:
    source = parse_sample(
        "# house-lint: ignore-file[HSL001] - valid reason\n"
        "value = 1\n"
        "# house-lint: ignore-file[HSL001] - valid reason\n"
        "# house-lint: ignore[HSL001] - valid reason\n"
        "# house-lint: ignore-next[HSL001] - valid reason\n",
    )
    result = apply_suppressions(source, (), {"HSL001", "HSL900", "HSL901"})

    assert [(finding.line, finding.rule_id) for finding in result.findings] == [
        (3, "HSL900"),
        (4, "HSL900"),
        (5, "HSL900"),
        (1, "HSL901"),
    ]
    assert result.findings[3].message == "unused suppression for HSL001"


def test_ignore_next_still_owns_across_a_form_feed_earlier_in_the_file(parse_sample) -> None:
    source = parse_sample(
        'text = "a\fb"\n'
        "def load() -> None:\n"
        "    # house-lint: ignore-next[HSL002] - lazy by design\n"
        "    import module\n",
    )
    candidates = tuple(detect_lazy_imports(source, None))

    result = apply_suppressions(source, candidates, {"HSL002", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_trailing_ignore_on_one_handler_leaves_sibling_handlers_visible(parse_sample) -> None:
    source = parse_sample(
        "try:\n"
        "    value = 1\n"
        "except ValueError as first:\n"
        "    pass\n"
        "except KeyError as second:  # house-lint: ignore[HSL103] - vetted name\n"
        "    pass\n",
    )
    candidates = tuple(detect_exception_names(source, HSL103Options()))
    assert len(candidates) == 2

    result = apply_suppressions(source, candidates, {"HSL103", "HSL900"})

    assert [(finding.rule_id, finding.line) for finding in result.findings] == [("HSL103", 3)]
    assert result.suppressed_count == 1


def test_ignore_next_above_a_decorator_owns_the_decorated_statement(parse_sample) -> None:
    source = parse_sample(
        "# house-lint: ignore-next[HSL001] - vetted shape\n"
        "@staticmethod\n"
        "def load() -> None:\n"
        "    pass\n",
    )

    result = apply_suppressions(source, (_candidate(source, "HSL001", 3),), {"HSL001", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_ignore_file_between_decorator_and_def_is_misplaced(parse_sample) -> None:
    source = parse_sample(
        "@staticmethod\n"
        "# house-lint: ignore-file[HSL001] - vetted shape\n"
        "def load() -> None:\n"
        "    pass\n",
    )

    result = apply_suppressions(source, (_candidate(source, "HSL001", 3),), {"HSL001", "HSL900"})

    assert result.suppressed_count == 0
    assert any(
        finding.rule_id == "HSL900" and "misplaced ignore-file" in finding.message
        for finding in result.findings
    )


@pytest.mark.parametrize("content", PARITY_SAMPLES)
def test_the_project_phase_accepts_exactly_the_pragmas_the_file_phase_accepts(
    parse_sample, register_project_rules, content: str
) -> None:
    register_project_rules(
        project_rule(
            PARITY_RULE, "Test rule", "Registered by a test", "opt-in", lambda *_: [], PYTHON_ONLY
        )
    )
    source = parse_sample(content)

    def accepted(enabled_rules: tuple[str, ...]) -> list[tuple[int, str]]:
        return [
            (entry.pragma.token.start[0], entry.pragma.action)
            for entry in scan_pragmas(source, enabled_rules).accepted
            if entry.rule_id == PARITY_RULE
        ]

    file_phase = accepted(("HSL001", "HSL002", PARITY_RULE))
    assert accepted((PARITY_RULE,)) == file_phase


def _hsl901_reports(result: SuppressionResult) -> list[tuple[int | None, str]]:
    return [
        (finding.line, finding.message)
        for finding in result.findings
        if finding.rule_id == "HSL901"
    ]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param(
            "def load() -> None:\n"
            "    # house-lint: ignore-next[HSL002] - lazy by design\n"
            "    import module  # house-lint: ignore[HSL002] - lazy by design\n",
            [(3, "redundant suppression for HSL002")],
            id="ignore-next-beats-trailing",
        ),
        pytest.param(
            "def load() -> None:\n"
            "    # house-lint: ignore-next[HSL002] - lazy by design\n"
            "    # house-lint: ignore-next[HSL002] - lazy by design\n"
            "    import module\n",
            [(3, "redundant suppression for HSL002")],
            id="earlier-ignore-next-survives",
        ),
        pytest.param(
            "# house-lint: ignore-file[HSL002] - lazy module\n"
            "def load() -> None:\n"
            "    # house-lint: ignore-next[HSL002] - lazy by design\n"
            "    import module\n",
            [(3, "redundant suppression for HSL002")],
            id="ignore-file-beats-statement",
        ),
        pytest.param(
            "# house-lint: ignore-next[HSL002] - lazy by design\n"
            "# house-lint: ignore-next[HSL002] - lazy by design\n"
            "value = 1\n",
            [(2, "redundant suppression for HSL002"), (1, "unused suppression for HSL002")],
            id="unused-overlap-reports-survivor-as-unused",
        ),
    ],
)
def test_hsl901_reports_all_but_the_broadest_earliest_overlapping_pragma(
    parse_sample, content: str, expected: list[tuple[int, str]]
) -> None:
    source = parse_sample(content)
    candidates = tuple(detect_lazy_imports(source, None))

    result = apply_suppressions(source, candidates, WITH_HSL901)

    assert sorted(_hsl901_reports(result)) == sorted(expected)
    assert all(finding.rule_id == "HSL901" for finding in result.findings)


@pytest.mark.parametrize(
    "content",
    [
        (
            "# house-lint: ignore-file[HSL002] - lazy module\n"
            "def load() -> None:\n"
            "    # house-lint: ignore-next[HSL002] - lazy by design\n"
            "    import first  # house-lint: ignore[HSL002] - lazy by design\n"
            "    import second  # house-lint: ignore[HSL002] - lazy by design\n"
        ),
        (
            "def load() -> None:\n"
            "    # house-lint: ignore-next[HSL002] - lazy by design\n"
            "    # house-lint: ignore-next[HSL002] - lazy by design\n"
            "    import first  # house-lint: ignore[HSL002] - lazy by design\n"
            "    import second  # house-lint: ignore[HSL002] - lazy by design\n"
            "    import third\n"
        ),
        (
            "# house-lint: ignore-next[HSL001] - decorative divider\n"
            "# house-lint: ignore-next[HSL001] - decorative divider\n"
            "# ------\n"
            "value = 1\n"
        ),
        (
            "# house-lint: ignore-file[HSL002] - lazy module\n"
            "def load() -> None:\n"
            "    import first  # house-lint: ignore[HSL002, HSL001] - lazy by design\n"
        ),
        (
            "# house-lint: ignore-next[HSL001] - decorative divider\n"
            "# ------\n"
            "# house-lint: ignore-next[HSL001] - decorative divider\n"
            "@decorator\n"
            "def build() -> None:\n"
            "    pass\n"
        ),
    ],
    ids=[
        "file-and-statements",
        "stacked-statement-pragmas",
        "ignore-next-window",
        "multi-id-pragma",
        "decorated-def-windows",
    ],
)
def test_deleting_every_redundant_pragma_leaves_the_suppressed_set_unchanged(
    parse_sample, content: str
) -> None:
    def scan(text: str):
        source = parse_sample(text)
        candidates = (*detect_lazy_imports(source, None), *detect_llm_cruft(source, None))
        return apply_suppressions(source, tuple(candidates), WITH_HSL901)

    before = scan(content)
    redundant = {
        (finding.line, finding.message.removeprefix("redundant suppression for "))
        for finding in before.findings
        if finding.message.startswith("redundant suppression")
    }
    assert redundant, "each sample must contain an overlap"
    after = scan(_without_ids(content, redundant))

    assert after.suppressed_count == before.suppressed_count
    assert [f for f in after.findings if f.rule_id != "HSL901"] == [
        f for f in before.findings if f.rule_id != "HSL901"
    ]
    assert not any(f.message.startswith("redundant") for f in after.findings)


def test_ignore_file_hsl901_claims_every_hsl901_report_but_no_hsl900_report(
    parse_sample,
) -> None:
    source = parse_sample(
        "# house-lint: ignore-file[HSL901] - pragmas kept for a migration\n"
        "# house-lint: ignore-file[HSL103] - disabled rule\n"
        "value = 1  # house-lint: ignore[HSL001] - nothing here\n"
        "other = 2  # house-lint: ignore[HSL999] - unknown rule\n",
    )

    result = apply_suppressions(source, (), WITH_HSL901)

    assert [(finding.rule_id, finding.message) for finding in result.findings] == [
        ("HSL900", "unknown suppression rule HSL999")
    ]
    assert result.suppressed_count == 0


def test_statement_pragma_naming_hsl901_claims_reports_on_its_own_statement_only(
    parse_sample,
) -> None:
    source = parse_sample(
        "# house-lint: ignore-file[HSL103] - disabled rule\n"
        "# house-lint: ignore-next[HSL001] - kept for later\n"
        "value = 1  # house-lint: ignore[HSL901] - kept deliberately\n"
        "other = 2  # house-lint: ignore[HSL103, HSL901] - kept deliberately\n"
        "third = 3  # house-lint: ignore[HSL001] - nothing here\n",
    )

    result = apply_suppressions(source, (), WITH_HSL901)

    assert _hsl901_reports(result) == [
        (1, "unused suppression for disabled rule HSL103"),
        (5, "unused suppression for HSL001"),
    ]


def test_a_pragma_naming_hsl901_is_never_itself_reported(parse_sample) -> None:
    source = parse_sample(
        "# house-lint: ignore-file[HSL901] - pragmas kept for a migration\n"
        "value = 1  # house-lint: ignore[HSL901] - kept deliberately\n",
    )

    assert apply_suppressions(source, (), WITH_HSL901).findings == ()
    assert apply_suppressions(source, (), {"HSL001", "HSL900"}).findings == ()


def test_stale_reports_need_hsl901_to_run(parse_sample) -> None:
    source = parse_sample(
        "# house-lint: ignore-file[HSL103] - disabled rule\n"
        "value = 1  # house-lint: ignore[HSL001] - nothing here\n",
    )

    assert apply_suppressions(source, (), {"HSL001", "HSL900"}).findings == ()
    assert [
        finding.rule_id for finding in apply_suppressions(source, (), WITH_HSL901).findings
    ] == [
        "HSL901",
        "HSL901",
    ]


def test_suppressed_hsl901_reports_spend_no_diagnostic_budget(parse_sample) -> None:
    source = parse_sample(
        "# house-lint: ignore-file[HSL901] - pragmas kept for a migration\n"
        "# house-lint: ignore-file[HSL103] - disabled rule\n"
        "value = 1  # house-lint: ignore[HSL101] - disabled rule\n"
        "other = 2  # house-lint: ignore[HSL102] - disabled rule\n",
    )

    result = apply_suppressions(source, (), WITH_HSL901, limit=1)

    assert result.findings == ()


def test_a_claim_is_redundant_only_when_the_kept_claim_owns_all_its_candidates(
    parse_sample,
) -> None:
    source = parse_sample(
        "# house-lint: ignore-file[HSL002] - lazy module\n"
        "value = 1  # house-lint: ignore[HSL002] - lazy by design\n"
        "other = 2  # house-lint: ignore[HSL002] - lazy by design\n",
    )
    file_pragma, first, second = [
        _Pragma(action, ("HSL002",), token, None)
        for action, token in zip(
            ("ignore-file", "ignore", "ignore"),
            (t for t in source.tokens if t.type == tokenize.COMMENT),
            strict=True,
        )
    ]
    # Overlapping claims that don't nest, which today's pragma actions can't produce: the
    # file pragma would be kept by scope, but deleting the trailing ones would lose candidate 2.
    claims = [
        _Claim(file_pragma, "HSL002", ("file", "ignore-file"), (0, 1)),
        _Claim(first, "HSL002", ("a", "ignore"), (1,)),
        _Claim(second, "HSL002", ("b", "ignore"), (1, 2)),
    ]

    assert _redundant_claims(claims) == {1}

    # A chain across two groups: `first` owns nothing and shares `second`'s target, so it's
    # redundant to `second`, which is redundant to the file claim through candidate 2. Deleting
    # both still leaves every candidate owned.
    chain = [
        _Claim(file_pragma, "HSL002", ("file", "ignore-file"), (2, 3)),
        _Claim(first, "HSL002", ("a", "ignore"), ()),
        _Claim(second, "HSL002", ("a", "ignore"), (2,)),
    ]

    assert _redundant_claims(chain) == {1, 2}


def _without_ids(content: str, reports: set[tuple[int | None, str]]) -> str:
    """Remove each reported rule ID from the pragma on its line, keeping line numbers in place.

    A pragma left with no IDs is deleted: a standalone one becomes a blank line, which
    `ignore-next` allows between itself and its statement; a trailing one is cut from its code.
    """
    kept = []
    for number, line in enumerate(content.splitlines(keepends=True), start=1):
        rule_ids = {rule_id for report_line, rule_id in reports if report_line == number}
        match = re.search(r"#\s*house-lint: [a-z-]+\[(?P<ids>[^]]*)\]", line)
        if not rule_ids or match is None:
            kept.append(line)
            continue
        listed = [item.strip() for item in match["ids"].split(",")]
        remaining = [item for item in listed if item not in rule_ids]
        if remaining:
            kept.append(
                line[: match.start("ids")] + ", ".join(remaining) + line[match.end("ids") :]
            )
        elif line.lstrip().startswith("#"):
            kept.append("\n")
        else:
            kept.append(line[: match.start()] + "\n")
    return "".join(kept)
