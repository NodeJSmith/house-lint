"""Whole-run pins for pragmas that suppress project-rule findings, and for `related`.

`register_project_rules` adds a stand-in rule that reports one candidate at fixed lines, so these
pin the engine apart from HSL104 and jscpd. Everything else runs for real through `cli.check`.
"""

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
from _source_helpers import PYTHON_ONLY

from house_lint import cli
from house_lint import pipeline as pipeline_module
from house_lint import source as source_module
from house_lint.language import Language
from house_lint.project_rule import Location, ProjectCandidate
from house_lint.rule_catalog import ProjectRule, project_rule
from house_lint.source import LoadedFile

RULE = "HSL198"
OTHER_RULE = "HSL197"
REASON = "deliberate copy kept in sync"


DECORATED = f"""\
    import functools


    # house-lint: ignore-next[{RULE}] - {REASON}
    @functools.cache
    def build():
        return 1
    """

DECORATED_TRAILING = f"""\
    import functools


    @functools.cache
    def build():  # house-lint: ignore[{RULE}] - {REASON}
        return 1
    """

DEF_LINE_PRAGMA = f"""\
    def build():  # house-lint: ignore[{RULE}] - {REASON}
        value = 1
        return value
    """

CONTINUATION = f"""\
    total = sum(  # house-lint: ignore[{RULE}] - {REASON}
        [1, 2, 3]
    )
    """

DECORATOR_LINE_IN_CLASS = f"""\
    import functools


    class Builder:
        @functools.cache  # house-lint: ignore[{RULE}] - {REASON}
        def build(self):
            return 1
    """

EXCEPT_LINE = f"""\
    try:
        run()
    except ValueError:  # house-lint: ignore[{RULE}] - {REASON}
        pass
    """

DECORATED_CLASS = f"""\
    import functools


    # house-lint: ignore-next[{RULE}] - {REASON}
    @functools.total_ordering
    class Builder:
        value = 1
    """

CLASS_LINE_PRAGMA = f"""\
    class Builder:  # house-lint: ignore[{RULE}] - {REASON}
        value = 1
    """

DECORATED_ASYNC = f"""\
    import functools


    @functools.cache
    async def build():  # house-lint: ignore[{RULE}] - {REASON}
        return 1
    """


def at_lines(*sites: tuple[str, int | None]) -> Any:
    """A project rule's `run`: one candidate located at each (file name, line) site it was given.

    A site whose file isn't in the rule's input is skipped. A `None` line is a whole-file location.
    """

    def run(files: tuple[LoadedFile, ...], options: Any) -> list[ProjectCandidate]:
        by_name = {file.relative_path: file for file in files}
        locations = tuple(
            Location(by_name[name], line, 1 if line else None, line, 2 if line else None)
            for name, line in sites
            if name in by_name
        )
        return [ProjectCandidate("duplicated block", locations)] if locations else []

    return run


def row(rule_id: str, run: Any, languages: frozenset[Language] = PYTHON_ONLY) -> ProjectRule[Any]:
    return project_rule(rule_id, "Test rule", "Registered by a test", "opt-in", run, languages)


def write(root: Path, name: str, content: str) -> None:
    (root / name).write_text(textwrap.dedent(content))


def scan(
    capsys: pytest.CaptureFixture[str], root: Path, *select: str, **kwargs: Any
) -> tuple[int, dict[str, Any]]:
    code = cli.check(root=root, select=list(select), format="json", **kwargs)
    return code, json.loads(capsys.readouterr().out)


def sites(result: dict[str, Any]) -> list[tuple[str, int | None]]:
    return [(finding["path"], finding["line"]) for finding in result["findings"]]


@pytest.mark.parametrize(
    ("content", "line", "suppressed"),
    [
        pytest.param(DECORATED, 5, True, id="ignore-next-above-decorator"),
        pytest.param(
            DECORATED_TRAILING, 4, True, id="trailing-on-def-line-claims-occurrence-on-decorator"
        ),
        pytest.param(DEF_LINE_PRAGMA, 2, False, id="def-line-pragma-misses-occurrence-in-body"),
        pytest.param(DEF_LINE_PRAGMA, 1, True, id="def-line-pragma-claims-occurrence-on-def-line"),
        pytest.param(CONTINUATION, 2, True, id="occurrence-on-continuation-line-is-its-statements"),
        pytest.param(
            DECORATOR_LINE_IN_CLASS, 5, False, id="decorator-line-pragma-in-class-misses-the-def"
        ),
        pytest.param(
            EXCEPT_LINE, 3, True, id="except-line-pragma-claims-occurrence-on-except-line"
        ),
        pytest.param(EXCEPT_LINE, 2, False, id="except-line-pragma-misses-occurrence-in-try-body"),
        pytest.param(DECORATED_CLASS, 5, True, id="ignore-next-above-decorated-class"),
        pytest.param(CLASS_LINE_PRAGMA, 2, False, id="class-line-pragma-misses-occurrence-in-body"),
        pytest.param(
            DECORATED_ASYNC, 4, True, id="trailing-on-async-def-claims-occurrence-on-decorator"
        ),
    ],
)
def test_a_statement_pragma_claims_an_occurrence_through_its_owner(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    register_project_rules,
    content: str,
    line: int,
    suppressed: bool,
) -> None:
    write(tmp_path, "a.py", content)
    register_project_rules(row(RULE, at_lines(("a.py", line))))

    code, result = scan(capsys, tmp_path, RULE)

    assert sites(result) == ([] if suppressed else [("a.py", line)])
    assert result["summary"]["suppressed_count"] == (1 if suppressed else 0)
    assert code == (0 if suppressed else 1)


def test_a_decorator_line_pragma_at_module_level_is_misplaced_and_claims_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(
        tmp_path,
        "a.py",
        f"""\
        import functools


        @functools.cache  # house-lint: ignore[{RULE}] - {REASON}
        def build():
            return 1
        """,
    )
    register_project_rules(row(RULE, at_lines(("a.py", 4))))

    code, result = scan(capsys, tmp_path, RULE)

    assert code == 1
    assert [(finding["rule_id"], finding["line"]) for finding in result["findings"]] == [
        (RULE, 4),
        ("HSL900", 4),
    ]
    assert result["summary"]["suppressed_count"] == 0


def test_an_occurrence_past_the_end_of_the_file_stays_visible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(tmp_path, "a.py", f"value = 1  # house-lint: ignore[{RULE}] - {REASON}\n")
    register_project_rules(row(RULE, at_lines(("a.py", 40))))

    code, result = scan(capsys, tmp_path, RULE)

    assert code == 1
    assert sites(result) == [("a.py", 40)]
    assert result["errors"] == []


def test_ignore_file_claims_every_occurrence_in_its_file_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(tmp_path, "a.py", f"# house-lint: ignore-file[{RULE}] - {REASON}\nvalue = 1\n")
    write(tmp_path, "b.py", "value = 1\n")
    register_project_rules(row(RULE, at_lines(("a.py", 2), ("a.py", None), ("b.py", 1))))

    code, result = scan(capsys, tmp_path, RULE)

    assert code == 1
    assert sites(result) == [("b.py", 1)]
    assert result["summary"]["suppressed_count"] == 2


def test_a_whole_file_occurrence_needs_ignore_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(tmp_path, "a.py", f"value = 1  # house-lint: ignore[{RULE}] - {REASON}\n")
    register_project_rules(row(RULE, at_lines(("a.py", None))))

    code, result = scan(capsys, tmp_path, RULE)

    assert code == 1
    assert sites(result) == [("a.py", None)]


def test_a_pragma_claims_only_occurrences_of_the_rules_it_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(tmp_path, "a.py", f"value = 1  # house-lint: ignore[{OTHER_RULE}] - {REASON}\n")
    register_project_rules(row(RULE, at_lines(("a.py", 1))), row(OTHER_RULE, at_lines(("a.py", 1))))

    code, result = scan(capsys, tmp_path, RULE, OTHER_RULE)

    assert code == 1
    assert [(finding["rule_id"], finding["line"]) for finding in result["findings"]] == [(RULE, 1)]
    assert result["summary"]["suppressed_count"] == 1


def test_two_pragmas_claiming_one_occurrence_hide_it_once_with_no_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(
        tmp_path,
        "a.py",
        f"""\
        # house-lint: ignore-file[{RULE}] - {REASON}
        # house-lint: ignore-next[{RULE}] - {REASON}
        value = 1  # house-lint: ignore[{RULE}] - {REASON}
        """,
    )
    register_project_rules(row(RULE, at_lines(("a.py", 3))))

    code, result = scan(capsys, tmp_path, RULE)

    assert code == 0
    assert result["findings"] == []
    assert result["summary"]["suppressed_count"] == 1


def test_project_suppressions_join_file_suppressions_in_the_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(
        tmp_path,
        "a.py",
        f"""\
        def example():
            import module  # house-lint: ignore[HSL002,{RULE}] - {REASON}
        """,
    )
    register_project_rules(row(RULE, at_lines(("a.py", 2))))

    code, result = scan(capsys, tmp_path, "HSL002", RULE)

    assert code == 0
    assert result["summary"]["suppressed_count"] == 2


def test_a_file_that_fails_to_parse_keeps_its_occurrences_visible(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(tmp_path, "a.py", f"# house-lint: ignore-file[{RULE}] - {REASON}\ndef broken(\n")
    register_project_rules(row(RULE, at_lines(("a.py", 2))))

    code, result = scan(capsys, tmp_path, RULE)

    assert code == 3
    assert sites(result) == [("a.py", 2)]
    assert [(error["path"], error["kind"]) for error in result["errors"]] == [("a.py", "tokenize")]


def test_a_typescript_occurrence_is_not_suppressed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(tmp_path, "b.ts", f"// house-lint: ignore-file[{RULE}] - {REASON}\nconst a = 1;\n")
    register_project_rules(row(RULE, at_lines(("b.ts", 2)), frozenset({Language.TYPESCRIPT})))

    code, result = scan(capsys, tmp_path, RULE)

    assert code == 1
    assert sites(result) == [("b.ts", 2)]


@pytest.mark.parametrize("cache_state", ["cold", "warm"])
def test_suppression_reads_each_file_once_and_works_on_a_cache_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    register_project_rules,
    cache_state: str,
) -> None:
    write(tmp_path, "a.py", f"value = 1  # house-lint: ignore[{RULE}] - {REASON}\n")
    write(tmp_path, "b.py", "value = 1\n")
    register_project_rules(
        row(RULE, at_lines(("a.py", 1), ("b.py", 1))),
        row(OTHER_RULE, at_lines(("a.py", 1))),
    )
    if cache_state == "warm":
        scan(capsys, tmp_path, RULE, OTHER_RULE)
    real_read = source_module.read_regular_file_bytes
    reads: list[str] = []

    def counting_read(path: Path, *, max_bytes: int) -> bytes | None:
        reads.append(path.name)
        return real_read(path, max_bytes=max_bytes)

    parses: list[str] = []
    real_parse = pipeline_module.parse_python

    def counting_parse(loaded: LoadedFile) -> Any:
        parses.append(loaded.relative_path)
        return real_parse(loaded)

    monkeypatch.setattr(source_module, "read_regular_file_bytes", counting_read)
    monkeypatch.setattr(pipeline_module, "parse_python", counting_parse)

    code, result = scan(capsys, tmp_path, RULE, OTHER_RULE)

    assert code == 1
    assert sorted(reads) == ["a.py", "b.py"]
    assert parses == ["a.py", "b.py"]
    assert [(finding["rule_id"], finding["path"]) for finding in result["findings"]] == [
        (OTHER_RULE, "a.py"),
        (RULE, "b.py"),
    ]


def test_a_crash_while_resolving_pragmas_is_that_rules_internal_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    register_project_rules,
) -> None:
    write(tmp_path, "a.py", "value = 1\n")
    register_project_rules(row(OTHER_RULE, at_lines(("a.py", 1))), row(RULE, at_lines(("a.py", 1))))

    def crash(*args: Any) -> Any:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(pipeline_module, "claims_for", crash)

    code, result = scan(capsys, tmp_path, OTHER_RULE, RULE)

    assert code == 4
    assert result["findings"] == []
    [error] = result["errors"]
    assert (error["kind"], error["rule_id"]) == ("internal", OTHER_RULE)


def test_each_finding_relates_every_other_location_of_its_candidate_including_suppressed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(tmp_path, "a.py", "value = 1\n")
    write(tmp_path, "b.py", f"value = 1  # house-lint: ignore[{RULE}] - {REASON}\n")
    write(tmp_path, "c.py", "value = 1\n")
    register_project_rules(row(RULE, at_lines(("c.py", 1), ("a.py", 1), ("b.py", 1))))

    code, result = scan(capsys, tmp_path, RULE)

    assert code == 1
    related = {
        finding["path"]: [location["path"] for location in finding["related"]]
        for finding in result["findings"]
    }
    assert related == {"a.py": ["c.py", "b.py"], "c.py": ["a.py", "b.py"]}
    assert result["findings"][0]["related"][0] == {
        "path": "c.py",
        "line": 1,
        "column": 1,
        "end_line": 1,
        "end_column": 2,
    }
    assert result["schema_version"] == 3


def test_text_output_names_the_other_locations_on_the_findings_own_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(tmp_path, "a.py", "value = 1\n")
    write(tmp_path, "b.py", "value = 1\n")
    register_project_rules(row(RULE, at_lines(("a.py", 1), ("b.py", None))))

    assert cli.check(root=tmp_path, select=[RULE], format="text") == 1

    lines = capsys.readouterr().out.splitlines()
    assert f"a.py:1:1: {RULE} duplicated block (also at b.py)" in lines
    assert f"b.py: {RULE} duplicated block (also at a.py:1:1)" in lines


def test_a_pragma_in_a_per_file_ignored_file_is_a_disabled_rule_pragma_in_both_phases(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    write(
        tmp_path, "pyproject.toml", f'[tool.house-lint.per-file-ignores]\n"gen.py" = ["{RULE}"]\n'
    )
    write(tmp_path, "gen.py", f"# house-lint: ignore-file[{RULE}] - {REASON}\nvalue = 1\n")
    write(tmp_path, "kept.py", "value = 1\n")
    register_project_rules(row(RULE, at_lines(("gen.py", 2), ("kept.py", 1))))

    code, result = scan(capsys, tmp_path, "HSL002", RULE)

    assert code == 1
    assert [(finding["rule_id"], finding["path"]) for finding in result["findings"]] == [
        ("HSL900", "gen.py"),
        (RULE, "kept.py"),
    ]
    assert result["findings"][0]["message"] == f"unused suppression for disabled rule {RULE}"
    assert result["summary"]["suppressed_count"] == 0
