from house_lint.analysis import CandidateFinding, SourceKind, statement_key
from house_lint.config import HSL101Options, TokenFamily
from house_lint.rules.lazy_imports import detect as detect_lazy_imports
from house_lint.rules.llm_cruft import detect as detect_llm_cruft
from house_lint.rules.spec_tokens import detect as detect_spec_tokens
from house_lint.source import SourceFile
from house_lint.suppressions import apply_suppressions


def _source(write_sample, text: str) -> SourceFile:
    path = write_sample(text)
    return SourceFile(path, path.parent)


def _candidate(
    source: SourceFile, rule_id: str, statement_line: int, finding_line: int | None = None
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


def test_trailing_ignore_suppresses_all_owned_rule_candidates(write_sample) -> None:
    source = _source(
        write_sample,
        "value = (\n    1\n)  # house-lint: ignore[HSL001] - generated value\n",
    )
    candidates = (_candidate(source, "HSL001", 1), _candidate(source, "HSL001", 1, 2))
    result = apply_suppressions(source, candidates, {"HSL001", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 2
    assert not hasattr(result, "visible_candidates")


def test_trailing_ignore_suppresses_a_simple_statement(write_sample) -> None:
    source = _source(write_sample, "value = 1  # house-lint: ignore[HSL001] - generated value\n")

    result = apply_suppressions(source, (_candidate(source, "HSL001", 1),), {"HSL001", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_trailing_ignore_owns_the_last_statement_on_a_semicolon_separated_line(
    write_sample,
) -> None:
    source = _source(
        write_sample,
        "def load() -> None:\n    value = 1; import module  # house-lint: ignore[HSL002] - generated import\n",
    )
    candidates = tuple(detect_lazy_imports(source, None))

    result = apply_suppressions(source, candidates, {"HSL002", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_trailing_ignore_owns_interior_comment_findings_in_multiline_statements(
    write_sample,
) -> None:
    source = _source(
        write_sample,
        "value = (  # house-lint: ignore[HSL101] - generated value\n    1  # T01\n)\n",
    )
    candidates = tuple(
        detect_spec_tokens(
            source, HSL101Options((TokenFamily(("T",), ("comments",), min_digits=2),))
        )
    )

    result = apply_suppressions(source, candidates, {"HSL101", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_trailing_ignore_suppresses_hsl001_inline_comment_in_multiline_statement(
    write_sample,
) -> None:
    source = _source(
        write_sample,
        "value = (\n    1\n)  # house-lint: ignore[HSL001] - Please note that generated value\n",
    )
    candidates = tuple(detect_llm_cruft(source, None))

    result = apply_suppressions(source, candidates, {"HSL001", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_header_ignore_does_not_own_comment_findings_in_its_body(write_sample) -> None:
    source = _source(
        write_sample,
        "if condition:  # house-lint: ignore[HSL101] - generated branch\n    # T01\n    pass\n",
    )
    candidates = tuple(
        detect_spec_tokens(
            source, HSL101Options((TokenFamily(("T",), ("comments",), min_digits=2),))
        )
    )

    result = apply_suppressions(source, candidates, {"HSL101", "HSL900"})

    assert [finding.rule_id for finding in result.findings] == ["HSL101", "HSL900"]
    assert result.suppressed_count == 0


def test_ignore_next_stays_in_its_lexical_suite(write_sample) -> None:
    source = _source(
        write_sample,
        "if condition:\n    # house-lint: ignore-next[HSL002] - circular import\n    # ordinary comment\n\n    import package\n",
    )
    result = apply_suppressions(source, (_candidate(source, "HSL002", 5),), {"HSL002", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_ignore_next_owns_a_statement_in_a_match_case_suite(write_sample) -> None:
    source = _source(
        write_sample,
        "match value:\n    case _:\n        # house-lint: ignore-next[HSL002] - circular import\n        import package\n",
    )

    result = apply_suppressions(source, (_candidate(source, "HSL002", 4),), {"HSL002", "HSL900"})

    assert result.findings == ()
    assert result.suppressed_count == 1


def test_ignore_next_cannot_leave_its_lexical_suite(write_sample) -> None:
    source = _source(
        write_sample,
        "if condition:\n    pass\n    # house-lint: ignore-next[HSL002] - circular import\n\nvalue = 1\n",
    )
    result = apply_suppressions(source, (_candidate(source, "HSL002", 5),), {"HSL002", "HSL900"})

    assert [finding.rule_id for finding in result.findings] == ["HSL002", "HSL900"]
    assert result.suppressed_count == 0


def test_ignore_next_requires_a_comment_only_line(write_sample) -> None:
    source = _source(
        write_sample,
        "value = 1  # house-lint: ignore-next[HSL002] - circular import\nnext_value = 2\n",
    )
    result = apply_suppressions(source, (_candidate(source, "HSL002", 2),), {"HSL002", "HSL900"})

    assert [finding.rule_id for finding in result.findings] == ["HSL002", "HSL900"]
    assert result.suppressed_count == 0


def test_file_ignore_suppresses_statement_file_and_filename_candidates(write_sample) -> None:
    source = _source(
        write_sample,
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


def test_file_ignore_rejects_a_non_docstring_string_before_the_pragma(write_sample) -> None:
    source = _source(
        write_sample,
        '"""Docs."""\n"not a docstring"\n# house-lint: ignore-file[HSL001] - generated module\n',
    )
    result = apply_suppressions(source, (_candidate(source, "HSL001", 2),), {"HSL001", "HSL900"})

    assert [finding.rule_id for finding in result.findings] == ["HSL001", "HSL900"]
    assert result.suppressed_count == 0


def test_invalid_and_conflicting_pragmas_emit_hsl900_without_hiding_candidates(
    write_sample,
) -> None:
    source = _source(
        write_sample,
        "# house-lint: ignore-file[HSL001] - generated module\nvalue = 1  # house-lint: ignore[HSL001] - valid reason\n# house-lint: ignore[HSL900] - valid reason\n",
    )
    candidates = (_candidate(source, "HSL001", 2),)
    result = apply_suppressions(source, candidates, {"HSL001", "HSL900"})

    assert [finding.rule_id for finding in result.findings] == [
        "HSL001",
        "HSL900",
        "HSL900",
        "HSL900",
    ]
    assert result.suppressed_count == 0


def test_invalid_pragma_ids_reasons_and_disabled_or_unknown_rules_are_diagnostics(
    write_sample,
) -> None:
    source = _source(
        write_sample,
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

    assert len(result.findings) == 6
    assert all(finding.rule_id == "HSL900" for finding in result.findings)
    assert "unknown" in result.findings[4].message
    assert "disabled" in result.findings[5].message


def test_empty_all_and_glob_ids_are_malformed(write_sample) -> None:
    source = _source(
        write_sample,
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


def test_misplaced_and_unconsumed_pragmas_are_diagnostics(write_sample) -> None:
    source = _source(
        write_sample,
        "# house-lint: ignore-file[HSL001] - valid reason\n"
        "value = 1\n"
        "# house-lint: ignore-file[HSL001] - valid reason\n"
        "# house-lint: ignore[HSL001] - valid reason\n"
        "# house-lint: ignore-next[HSL001] - valid reason\n",
    )
    result = apply_suppressions(source, (), {"HSL001", "HSL900"})

    assert len(result.findings) == 4
    assert all(finding.rule_id == "HSL900" for finding in result.findings)
