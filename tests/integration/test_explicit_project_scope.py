"""With explicit paths, project rules analyze the whole configured scope and report on named files.

A stand-in rule records its input and reports one candidate located in every file it was given, so
these pin the pipeline apart from HSL104 and jscpd. One test at the end runs HSL104 for real.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from _source_helpers import PYTHON_ONLY

from house_lint import cli, pipeline
from house_lint import scanner as scanner_module
from house_lint import source as source_module
from house_lint.discovery import DiscoveryError
from house_lint.project_rule import Location, ProjectCandidate
from house_lint.results import error
from house_lint.rule_catalog import project_rule
from house_lint.source import LoadedFile, LoadFailure

RULE = "HSL198"
OTHER_RULE = "HSL197"
CLEAN = "value = 1\n"
LAZY_IMPORT = "def example():\n    import module\n"  # an HSL002 finding
SPAN = (1, 1, 1, 5)


class Everywhere:
    """A project rule's `run`: one candidate located at line 1 of every file it was given."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self, files: tuple[LoadedFile, ...], options: Any, *, debug: bool
    ) -> list[ProjectCandidate]:
        self.calls.append([file.relative_path for file in files])
        return [ProjectCandidate("everywhere", tuple(Location(file, *SPAN) for file in files))]


@pytest.fixture
def everywhere(register_project_rules) -> Everywhere:
    run = Everywhere()
    register_project_rules(
        project_rule(RULE, "Test rule", "Registered by a test", "opt-in", run, PYTHON_ONLY)
    )
    return run


def scan(capsys: pytest.CaptureFixture[str], root: Path, *paths: str, **kwargs: Any) -> Any:
    select = kwargs.pop("select", [RULE])
    code = cli.check(
        [root / path for path in paths], root=root, select=select, format="json", **kwargs
    )
    return code, json.loads(capsys.readouterr().out)


def write(root: Path, *names: str, content: str = CLEAN) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def reported(result: dict[str, Any]) -> list[tuple[str, list[str]]]:
    return [
        (finding["path"], [location["path"] for location in finding["related"]])
        for finding in result["findings"]
    ]


def test_a_named_file_is_analyzed_with_the_whole_scope_and_only_it_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], everywhere: Everywhere
) -> None:
    write(tmp_path, "a.py", "b.py", "c.py")

    code, result = scan(capsys, tmp_path, "b.py")

    assert code == 1
    assert everywhere.calls == [["b.py", "a.py", "c.py"]]
    assert reported(result) == [("b.py", ["a.py", "c.py"])]
    assert result["files_scanned"] == 1


def test_without_explicit_paths_every_file_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], everywhere: Everywhere
) -> None:
    write(tmp_path, "a.py", "b.py")

    code, result = scan(capsys, tmp_path)

    assert code == 1
    assert [path for path, _related in reported(result)] == ["a.py", "b.py"]


def test_a_named_file_outside_include_is_still_analyzed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], everywhere: Everywhere
) -> None:
    write(tmp_path, "pyproject.toml", content='[tool.house-lint]\ninclude = ["src"]\n')
    write(tmp_path, "src/a.py", "scripts/x.py")

    code, result = scan(capsys, tmp_path, "scripts/x.py")

    assert code == 1
    assert everywhere.calls == [["scripts/x.py", "src/a.py"]]
    assert reported(result) == [("scripts/x.py", ["src/a.py"])]


def test_a_rule_no_named_file_reaches_does_not_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    everywhere: Everywhere,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        content=f'[tool.house-lint.per-file-ignores]\n"gen.py" = ["{RULE}"]\n',
    )
    write(tmp_path, "gen.py", "a.py")
    discoveries: list[None] = []
    real_discover = pipeline.discover_files

    def counting_discover(*args: Any, **kwargs: Any) -> Any:
        discoveries.append(None)
        return real_discover(*args, **kwargs)

    monkeypatch.setattr(pipeline, "discover_files", counting_discover)

    code, result = scan(capsys, tmp_path, "gen.py", select=[RULE, "HSL002"])

    assert code == 0
    assert everywhere.calls == []
    assert len(discoveries) == 1
    assert result["files_scanned"] == 1


def test_a_per_file_ignored_file_stays_out_of_the_widened_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], everywhere: Everywhere
) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        content=f'[tool.house-lint.per-file-ignores]\n"gen.py" = ["{RULE}"]\n',
    )
    write(tmp_path, "a.py", "gen.py", "z.py")

    scan(capsys, tmp_path, "a.py")

    assert everywhere.calls == [["a.py", "z.py"]]


def test_pragmas_in_unnamed_files_are_neither_checked_nor_counted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], everywhere: Everywhere
) -> None:
    pragma = f"# house-lint: ignore-file[{RULE}] - generated copy\n{CLEAN}"
    write(tmp_path, "a.py", "b.py", content=pragma)
    write(tmp_path, "c.py")

    code, result = scan(capsys, tmp_path, "a.py", "c.py")

    assert code == 1
    assert reported(result) == [("c.py", ["a.py", "b.py"])]
    assert result["summary"]["suppressed_count"] == 1


def test_each_file_is_read_once_when_the_input_widens(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    everywhere: Everywhere,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(tmp_path, "a.py", "b.py", "c.py")
    real_read = source_module.read_regular_file_bytes
    reads: list[str] = []

    def counting_read(path: Path, *, max_bytes: int) -> bytes | None:
        reads.append(path.name)
        return real_read(path, max_bytes=max_bytes)

    monkeypatch.setattr(source_module, "read_regular_file_bytes", counting_read)

    scan(capsys, tmp_path, "a.py")

    assert sorted(reads) == ["a.py", "b.py", "c.py"]


def test_an_unnamed_file_that_fails_to_load_is_left_out(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    everywhere: Everywhere,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(tmp_path, "a.py", "b.py", "c.py")
    real_open = pipeline.open_source

    def failing_open(path: Path, **kwargs: Any) -> Any:
        if path.name == "b.py":
            return LoadFailure(error("read", "read", "bounded-read", "unreadable", path="b.py"))
        return real_open(path, **kwargs)

    monkeypatch.setattr(pipeline, "open_source", failing_open)

    code = cli.check([tmp_path / "a.py"], root=tmp_path, select=[RULE], format="json", debug=True)
    captured = capsys.readouterr()

    assert code == 1
    assert everywhere.calls == [["a.py", "c.py"]]
    assert json.loads(captured.out)["errors"] == []
    assert "left out of project rules: b.py: unreadable" in captured.err


def test_an_internal_error_loading_an_unnamed_file_stops_the_project_rules(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    everywhere: Everywhere,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(tmp_path, "a.py", "b.py")
    real_load = source_module.load_file

    def exploding_load(path: Path, root: Path, *, resolved_path: Path) -> Any:
        if path.name == "b.py":
            raise RuntimeError("boom")
        return real_load(path, root, resolved_path=resolved_path)

    monkeypatch.setattr(scanner_module, "load_file", exploding_load)

    code, result = scan(capsys, tmp_path, "a.py")

    assert code == 4
    assert everywhere.calls == []
    assert [failure["code"] for failure in result["errors"]] == ["internal-error"]


def test_a_failed_widening_pass_is_an_error_for_each_rule_and_file_rules_still_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    everywhere: Everywhere,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(tmp_path, "a.py", content=LAZY_IMPORT)
    real_discover = pipeline.discover_files

    def failing_second_pass(*args: Any, **kwargs: Any) -> Any:
        # The first pass discovers the named paths; the widening pass gets no `explicit`.
        if not kwargs.get("explicit"):
            raise DiscoveryError(error("git", "discovery", "git-ls-files", "git broke"))
        return real_discover(*args, **kwargs)

    monkeypatch.setattr(pipeline, "discover_files", failing_second_pass)

    code, result = scan(capsys, tmp_path, "a.py", select=[RULE, "HSL002"])

    assert code == 3
    assert everywhere.calls == []
    assert [finding["rule_id"] for finding in result["findings"]] == ["HSL002"]
    [failure] = result["errors"]
    assert (failure["rule_id"], failure["message"]) == (RULE, "git broke")


def test_hsl104_on_a_named_file_reports_its_copy_of_unnamed_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = (
        "(value, other, third):\n"
        "    total = value + other + third\n"
        "    if total > 10:\n"
        "        result = total * 2\n"
        "    else:\n"
        "        result = total - 1\n"
        "    items = [result, value, other, third]\n"
        "    return sorted(items, key=lambda x: -x)\n"
    )
    for name in ("a", "b", "c"):
        write(tmp_path, f"{name}.py", content=f"def handle_{name}{body}")

    code, result = scan(capsys, tmp_path, "b.py", select=["HSL104"])

    assert code == 1
    assert result["errors"] == []
    [finding] = result["findings"]
    assert (finding["path"], finding["line"], finding["column"]) == ("b.py", 1, 13)
    assert finding["message"] == "duplicated code of 8 lines in 3 places"
    assert [location["path"] for location in finding["related"]] == ["a.py", "c.py"]
    assert result["files_scanned"] == 1


def test_hsl104_without_jscpd_fails_loudly_and_other_rules_still_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setattr(sys, "executable", str(empty / "python"))
    monkeypatch.setenv("PATH", str(empty))
    write(tmp_path, "a.py", content=LAZY_IMPORT)

    code, result = scan(capsys, tmp_path, select=["HSL104", "HSL002"])

    assert code == 3
    assert [finding["rule_id"] for finding in result["findings"]] == ["HSL002"]
    [failure] = result["errors"]
    assert failure["rule_id"] == "HSL104"
    assert "house-lint[duplicates]" in failure["message"]
