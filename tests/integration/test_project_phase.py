"""Whole-run pins for project rules, using rules registered by the test.

No shipped rule is a project rule yet, so `register_project_rules` adds rows to the table. Everything
else runs for real through `cli.check`.
"""

import gc
import json
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from _source_helpers import PYTHON_ONLY

from house_lint import cli, pipeline
from house_lint import source as source_module
from house_lint.language import Language
from house_lint.project_rule import Location, ProjectCandidate, ProjectRuleFailure
from house_lint.rule_catalog import (
    ProjectRule,
    configurable_project_rule,
    project_rule,
)
from house_lint.rules.options import RuleOptions
from house_lint.source import MAX_SOURCE_BYTES, LoadedFile

PY_RULE = "HSL198"
TS_RULE = "HSL199"
TYPESCRIPT = frozenset({Language.TYPESCRIPT})
CLEAN = "value = 1\n"
LAZY_IMPORT = "def example():\n    import module\n"
SPAN = (1, 1, 1, 5)


class ThresholdOptions(RuleOptions, frozen=True, forbid_unknown_fields=True):
    threshold: int = 1


@dataclass
class Recorder:
    """A project rule's `run`: records each call, then raises `raises` or reports `reports`.

    With `reports` set, it returns one candidate with that message, located in every file it was
    given. Without it, the rule finds nothing.
    """

    reports: str | None = None
    raises: Exception | None = None
    calls: list[tuple[LoadedFile, ...]] = field(default_factory=list)
    options: list[Any] = field(default_factory=list)
    order: list[str] | None = None
    rule_id: str = ""

    def __call__(self, files: tuple[LoadedFile, ...], options: Any) -> list[ProjectCandidate]:
        self.calls.append(files)
        self.options.append(options)
        if self.order is not None:
            self.order.append(self.rule_id)
        if self.raises is not None:
            raise self.raises
        if self.reports is None:
            return []
        return [ProjectCandidate(self.reports, tuple(Location(file, *SPAN) for file in files))]

    @property
    def paths(self) -> list[str]:
        [files] = self.calls
        return [file.relative_path for file in files]


def row(rule_id: str, run: Any, languages: frozenset[Language] = PYTHON_ONLY) -> ProjectRule[Any]:
    return project_rule(rule_id, "Test rule", "Registered by a test", "opt-in", run, languages)


def scan(
    capsys: pytest.CaptureFixture[str], root: Path, *select: str, **kwargs: Any
) -> tuple[int, dict[str, Any]]:
    code = cli.check(root=root, select=list(select), format="json", **kwargs)
    return code, json.loads(capsys.readouterr().out)


def rule_ids_and_paths(result: dict[str, Any]) -> list[tuple[str, str]]:
    return [(finding["rule_id"], finding["path"]) for finding in result["findings"]]


def test_a_project_rule_gets_the_scanned_files_and_reports_one_finding_per_location(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(CLEAN)
    (tmp_path / "b.py").write_text("other = 2\n")
    run = Recorder("duplicated block")
    register_project_rules(row(PY_RULE, run))

    code, result = scan(capsys, tmp_path, PY_RULE)

    assert code == 1
    assert run.paths == ["a.py", "b.py"]
    assert [file.content for file in run.calls[0]] == [CLEAN.encode(), b"other = 2\n"]
    assert result["findings"] == [
        {
            "rule_id": PY_RULE,
            "path": path,
            "line": 1,
            "column": 1,
            "end_line": 1,
            "end_column": 5,
            "message": "duplicated block",
            "related": [{"path": other, "line": 1, "column": 1, "end_line": 1, "end_column": 5}],
        }
        for path, other in (("a.py", "b.py"), ("b.py", "a.py"))
    ]
    assert result["files_scanned"] == 2


def test_project_findings_join_file_findings_in_the_same_totals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(LAZY_IMPORT)
    register_project_rules(row(PY_RULE, Recorder("project")))

    code, result = scan(capsys, tmp_path, "HSL002", PY_RULE)

    assert code == 1
    assert sorted(rule_ids_and_paths(result)) == [("HSL002", "a.py"), (PY_RULE, "a.py")]
    assert result["summary"]["finding_count"] == 2


def test_rules_run_in_enabled_order_on_files_in_discovery_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    for name in ("c.py", "a.py", "b.py"):
        (tmp_path / name).write_text(CLEAN)
    order: list[str] = []
    first = Recorder(order=order, rule_id="HSL197")
    second = Recorder(order=order, rule_id=PY_RULE)
    register_project_rules(row(PY_RULE, second), row("HSL197", first))

    assert scan(capsys, tmp_path, PY_RULE, "HSL197")[0] == 0

    assert order == ["HSL197", PY_RULE]
    assert first.paths == second.paths == ["a.py", "b.py", "c.py"]


def test_no_loaded_file_outlives_its_iteration_without_a_project_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("a.py", "b.py", "c.py", "d.py"):
        (tmp_path / name).write_text(CLEAN)
    real_open = pipeline.open_source
    refs: list[weakref.ref[LoadedFile]] = []
    most_alive = 0

    def spying_open(*args: Any, **kwargs: Any) -> Any:
        nonlocal most_alive
        gc.collect()
        most_alive = max(most_alive, sum(ref() is not None for ref in refs))
        loaded = real_open(*args, **kwargs)
        assert isinstance(loaded, LoadedFile)
        refs.append(weakref.ref(loaded))
        return loaded

    monkeypatch.setattr(pipeline, "open_source", spying_open)

    assert scan(capsys, tmp_path, "HSL002", no_cache=True)[0] == 0

    # At most one file is alive when the next is read: the loop's own variable still holds it.
    assert len(refs) == 4
    assert most_alive == 1


@pytest.mark.parametrize("cache_state", ["cold", "warm"])
def test_a_file_is_read_once_and_a_cache_hit_still_gives_the_rule_its_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    register_project_rules,
    cache_state: str,
) -> None:
    (tmp_path / "a.py").write_text(LAZY_IMPORT)
    run = Recorder()
    register_project_rules(row(PY_RULE, run))
    if cache_state == "warm":
        scan(capsys, tmp_path, "HSL002", PY_RULE)
        run.calls.clear()
    real_read = source_module.read_regular_file_bytes
    reads: list[str] = []

    def counting_read(path: Path, *, max_bytes: int) -> bytes | None:
        reads.append(path.name)
        return real_read(path, max_bytes=max_bytes)

    monkeypatch.setattr(source_module, "read_regular_file_bytes", counting_read)

    code, result = scan(capsys, tmp_path, "HSL002", PY_RULE)

    assert code == 1
    assert rule_ids_and_paths(result) == [("HSL002", "a.py")]
    assert reads == ["a.py"]
    assert [file.content for file in run.calls[0]] == [LAZY_IMPORT.encode()]


def test_nothing_from_the_project_phase_is_cached(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(CLEAN)
    run = Recorder("first")
    register_project_rules(row(PY_RULE, run))

    scan(capsys, tmp_path, PY_RULE)
    run.reports = None
    code, result = scan(capsys, tmp_path, PY_RULE)

    assert len(run.calls) == 2
    assert code == 0
    assert result["findings"] == []


def test_a_per_file_ignored_file_leaves_that_rules_input_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.house-lint.per-file-ignores]\n"gen.py" = ["{PY_RULE}"]\n'
    )
    (tmp_path / "gen.py").write_text(CLEAN)
    (tmp_path / "kept.py").write_text(CLEAN)
    ignoring = Recorder()
    other = Recorder()
    register_project_rules(row(PY_RULE, ignoring), row("HSL197", other))

    code, result = scan(capsys, tmp_path, PY_RULE, "HSL197")

    assert code == 0
    assert ignoring.paths == ["kept.py"]
    assert other.paths == ["gen.py", "kept.py"]
    assert result["files_scanned"] == 2


def test_a_rule_with_no_files_does_not_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.house-lint.per-file-ignores]\n"*.py" = ["{PY_RULE}"]\n'
    )
    (tmp_path / "a.py").write_text(CLEAN)
    run = Recorder()
    register_project_rules(row(PY_RULE, run))

    assert scan(capsys, tmp_path, PY_RULE)[0] == 0

    assert run.calls == []


def test_a_python_file_that_fails_to_parse_stays_in_the_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "broken.py").write_text("def broken(\n")
    (tmp_path / "undecodable.py").write_bytes(b"# coding: ascii\nvalue = '\xff'\n")
    run = Recorder()
    register_project_rules(row(PY_RULE, run))

    code, result = scan(capsys, tmp_path, PY_RULE)

    assert code == 3
    assert run.paths == ["broken.py", "undecodable.py"]
    assert {error["path"] for error in result["errors"]} == {"broken.py", "undecodable.py"}


def test_a_file_that_fails_to_load_is_left_out_and_the_rule_still_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(CLEAN)
    (tmp_path / "huge.py").write_bytes(b"#" * (MAX_SOURCE_BYTES + 1))
    run = Recorder("found")
    register_project_rules(row(PY_RULE, run))

    code, result = scan(capsys, tmp_path, PY_RULE)

    assert code == 3
    assert run.paths == ["a.py"]
    assert rule_ids_and_paths(result) == [(PY_RULE, "a.py")]
    assert [error["code"] for error in result["errors"]] == ["source-too-large"]


def test_an_aborted_file_phase_skips_the_project_phase(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    register_project_rules,
    fail_detectors_for,
) -> None:
    (tmp_path / "a.py").write_text(CLEAN)
    (tmp_path / "b.py").write_text(CLEAN)
    run = Recorder()
    register_project_rules(row(PY_RULE, run))
    fail_detectors_for("a.py")

    code, result = scan(capsys, tmp_path, "HSL002", PY_RULE)

    assert code == 4
    assert run.calls == []
    assert [error["kind"] for error in result["errors"]] == ["internal"]


def test_a_rule_failure_reports_its_own_message_and_the_next_rule_still_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(CLEAN)
    failing = Recorder(raises=ProjectRuleFailure("jscpd is not installed: npm install jscpd"))
    following = Recorder("found")
    register_project_rules(row("HSL197", failing), row(PY_RULE, following))

    code, result = scan(capsys, tmp_path, "HSL197", PY_RULE)

    assert code == 3
    assert result["errors"] == [
        {
            "code": "project-rule-error",
            "kind": "project-rule",
            "path": None,
            "line": None,
            "column": None,
            "end_line": None,
            "end_column": None,
            "phase": "project",
            "operation": "rule-run",
            "rule_id": "HSL197",
            "message": "jscpd is not installed: npm install jscpd",
        }
    ]
    assert rule_ids_and_paths(result) == [(PY_RULE, "a.py")]


def test_a_rule_bug_is_an_internal_error_that_ends_the_phase_and_keeps_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(LAZY_IMPORT)
    earlier = Recorder("earlier")
    buggy = Recorder(raises=RuntimeError("secret detail"))
    later = Recorder()
    register_project_rules(row("HSL196", earlier), row("HSL197", buggy), row(PY_RULE, later))

    code, result = scan(capsys, tmp_path, "HSL002", "HSL196", "HSL197", PY_RULE)

    assert code == 4
    assert later.calls == []
    assert sorted(rule_ids_and_paths(result)) == [("HSL002", "a.py"), ("HSL196", "a.py")]
    [error] = result["errors"]
    assert (error["kind"], error["phase"], error["operation"]) == (
        "internal",
        "project",
        "rule-run",
    )
    assert error["rule_id"] == "HSL197"
    assert "secret detail" not in error["message"]


def test_a_rule_whose_output_cant_be_reported_is_an_internal_error_that_keeps_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    # A valid candidate first, to show a rule that fails partway leaves none of its findings. The
    # `Any` is how bad output gets past the type checker, as when it comes from parsed JSON.
    def malformed(files: tuple[LoadedFile, ...], options: Any) -> list[ProjectCandidate]:
        locations: Any = ("not a location",)
        valid = ProjectCandidate("valid", (Location(files[0], *SPAN),))
        return [valid, ProjectCandidate("malformed", locations)]

    (tmp_path / "a.py").write_text(LAZY_IMPORT)
    register_project_rules(row(PY_RULE, malformed))

    code, result = scan(capsys, tmp_path, "HSL002", PY_RULE)

    assert code == 4
    assert rule_ids_and_paths(result) == [("HSL002", "a.py")]
    assert [(error["kind"], error["rule_id"]) for error in result["errors"]] == [
        ("internal", PY_RULE)
    ]


@pytest.mark.parametrize(
    ("raises", "expected"),
    [
        (ProjectRuleFailure("jscpd is missing"), "HSL197: jscpd is missing"),
        (RuntimeError("secret detail"), "HSL197: an unexpected internal error occurred"),
    ],
)
def test_text_output_names_the_project_rule_that_failed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    register_project_rules,
    raises: Exception,
    expected: str,
) -> None:
    (tmp_path / "a.py").write_text(CLEAN)
    register_project_rules(row("HSL197", Recorder(raises=raises)))

    assert cli.check(root=tmp_path, select=["HSL197"], format="text") >= 3

    assert expected in capsys.readouterr().err


def test_enabling_a_rule_brings_its_language_in_and_only_that_language(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text("def broken(\n")
    (tmp_path / "b.ts").write_text("const a = 1;\n")
    (tmp_path / "c.tsx").write_text("export const C = () => <div />;\n")
    run = Recorder()
    register_project_rules(row(TS_RULE, run, TYPESCRIPT))

    code, result = scan(capsys, tmp_path, TS_RULE)

    assert code == 0
    assert run.paths == ["b.ts", "c.tsx"]
    assert result["files_scanned"] == 2
    assert result["errors"] == []


def test_a_typescript_file_gets_no_parsing_cache_entry_or_pragma_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "b.ts").write_text("// house-lint: nonsense\nnot python at all (\n")
    run = Recorder()
    register_project_rules(row(TS_RULE, run, TYPESCRIPT))

    code, result = scan(capsys, tmp_path, TS_RULE)

    assert code == 0
    assert result["findings"] == []
    assert result["errors"] == []
    assert list((tmp_path / ".house-lint-cache").rglob("*.json")) == []


def test_python_and_typescript_rules_each_get_only_their_own_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(LAZY_IMPORT)
    (tmp_path / "b.ts").write_text("const a = 1;\n")
    python_run = Recorder()
    typescript_run = Recorder()
    register_project_rules(
        row(PY_RULE, python_run),
        row(TS_RULE, typescript_run, TYPESCRIPT),
    )

    code, result = scan(capsys, tmp_path, "HSL002", PY_RULE, TS_RULE)

    assert code == 1
    assert python_run.paths == ["a.py"]
    assert typescript_run.paths == ["b.ts"]
    assert rule_ids_and_paths(result) == [("HSL002", "a.py")]
    assert result["files_scanned"] == 2


def test_with_only_python_rules_a_typescript_file_is_not_scanned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.py").write_text(CLEAN)
    (tmp_path / "b.ts").write_text("const a = 1;\n")

    code, result = scan(capsys, tmp_path, "HSL002")

    assert code == 0
    assert result["files_scanned"] == 1


def test_an_explicit_path_no_enabled_rule_handles_is_an_error_saying_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "b.ts").write_text("const a = 1;\n")

    code, result = scan(capsys, tmp_path, "HSL002", paths=[tmp_path / "b.ts"])

    assert code == 3
    assert "any enabled rule handles" in result["errors"][0]["message"]


def test_selecting_no_rules_is_a_zero_file_scan_that_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.py").write_text("# house-lint: ignore-file[HSL002] - stale pragma here\n")

    code, result = scan(capsys, tmp_path)

    assert code == 0
    assert result["files_scanned"] == 0
    assert result["findings"] == []
    assert "no enabled rule handles any language" in result["zero_file_diagnostic"]
    assert scan(capsys, tmp_path, fail_on_empty=True)[0] == 1


def test_a_rule_can_only_report_on_files_it_was_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.house-lint.per-file-ignores]\n"gen.py" = ["{PY_RULE}"]\n'
    )
    (tmp_path / "gen.py").write_text(CLEAN)
    (tmp_path / "kept.py").write_text(CLEAN)
    register_project_rules(row(PY_RULE, Recorder("found")))

    code, result = scan(capsys, tmp_path, PY_RULE)

    assert code == 1
    assert rule_ids_and_paths(result) == [(PY_RULE, "kept.py")]


def test_a_rule_with_no_languages_gets_the_note_saying_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(CLEAN)
    register_project_rules(row(PY_RULE, Recorder(), frozenset()))

    code, result = scan(capsys, tmp_path, PY_RULE)

    assert code == 0
    assert "no enabled rule handles any language" in result["zero_file_diagnostic"]


def test_an_explicit_path_with_no_rules_selected_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.py").write_text(CLEAN)

    code, result = scan(capsys, tmp_path, paths=[tmp_path / "a.py"])

    assert code == 3
    assert "any enabled rule handles" in result["errors"][0]["message"]


def test_a_pragma_naming_a_rule_that_doesnt_handle_python_gets_its_own_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(f"# house-lint: ignore-file[{TS_RULE}] - generated code\n")
    register_project_rules(row(TS_RULE, Recorder(), TYPESCRIPT))

    expected = f"{TS_RULE} does not handle Python files"
    for select in (("HSL002",), ("HSL002", TS_RULE)):
        code, result = scan(capsys, tmp_path, *select)
        assert code == 1
        assert [finding["message"] for finding in result["findings"]] == [expected]


def test_pragmas_naming_an_enabled_project_rule_are_neither_unused_nor_conflicting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(
        f"# house-lint: ignore-file[{PY_RULE}] - generated code\n"
        f"# house-lint: ignore-file[{PY_RULE}] - second one has no claim to conflict with\n"
    )
    register_project_rules(row(PY_RULE, Recorder()))

    code, result = scan(capsys, tmp_path, PY_RULE)

    assert code == 0
    assert result["findings"] == []


def test_a_pragma_naming_a_disabled_project_rule_is_reported_and_never_replayed_from_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "a.py").write_text(f"# house-lint: ignore-file[{PY_RULE}] - generated code\n")
    register_project_rules(row(PY_RULE, Recorder()))

    assert scan(capsys, tmp_path, "HSL002", PY_RULE)[0] == 0
    code, result = scan(capsys, tmp_path, "HSL002")

    assert code == 1
    assert [finding["message"] for finding in result["findings"]] == [
        f"unused suppression for disabled rule {PY_RULE}"
    ]


def test_project_rule_options_come_from_config_and_join_the_cache_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    config = tmp_path / "pyproject.toml"
    (tmp_path / "a.py").write_text(CLEAN)
    run = Recorder()
    register_project_rules(
        configurable_project_rule(
            PY_RULE,
            "Test rule",
            "Registered by a test",
            "opt-in",
            run,
            PYTHON_ONLY,
            ThresholdOptions,
        )
    )

    config.write_text(f"[tool.house-lint.rules.{PY_RULE}]\nthreshold = 5\n")
    assert scan(capsys, tmp_path, PY_RULE)[0] == 0
    config.write_text(f"[tool.house-lint.rules.{PY_RULE}]\nthreshold = 6\n")
    assert scan(capsys, tmp_path, PY_RULE)[0] == 0

    assert run.options == [ThresholdOptions(threshold=5), ThresholdOptions(threshold=6)]
    assert len(list((tmp_path / ".house-lint-cache").rglob("*.json"))) == 2


def test_an_invalid_project_rule_option_is_a_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], register_project_rules
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.house-lint.rules.{PY_RULE}]\nthreshold = "many"\n'
    )
    (tmp_path / "a.py").write_text(CLEAN)
    register_project_rules(
        configurable_project_rule(
            PY_RULE,
            "Test rule",
            "Registered by a test",
            "opt-in",
            Recorder(),
            PYTHON_ONLY,
            ThresholdOptions,
        )
    )

    code, result = scan(capsys, tmp_path, PY_RULE)

    assert code == 2
    assert PY_RULE in result["errors"][0]["message"]
