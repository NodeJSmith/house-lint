"""The jscpd adapter: real jscpd 5.4.1 for its output, a fake binary for each failure."""

import sys
import tomllib
from pathlib import Path

import pytest

from house_lint import jscpd
from house_lint.jscpd import ClonePair, Fragment, find_clones
from house_lint.project_rule import ProjectRuleFailure

EXTRA = "house-lint[duplicates]"
BLOCK = """\
    total = value + other + third
    if total > 10:
        result = total * 2
    else:
        result = total - 1
    items = [result, value, other, third]
    return sorted(items, key=lambda x: -x)
"""
FAKE = """\
import json, os, pathlib, sys, time

mode = os.environ["FAKE_JSCPD"]
if sys.argv[1:] == ["--version"]:
    print("jscpd 5.3.0" if mode == "old" else "jscpd 5.4.1")
    sys.exit(0)
config = json.loads(pathlib.Path(sys.argv[2]).read_text())
report = pathlib.Path(config["output"]) / "jscpd-report.json"
side = {"name": "/elsewhere/x.py", "startLoc": {"line": 1, "column": 0},
        "endLoc": {"line": 2, "column": 0}}
if mode == "hang":
    time.sleep(30)
elif mode == "fail":
    print("Error: path does not exist: /gone.py", file=sys.stderr)
    sys.exit(1)
elif mode == "garbage":
    report.write_text("{not json")
elif mode == "wrong-shape":
    report.write_text('{"duplicates": [{"firstFile": {}}]}')
elif mode == "stranger":
    report.write_text(json.dumps({"duplicates": [{"firstFile": side, "secondFile": side}]}))
# "no-report" exits 0 without writing a report.
"""


def write_copies(directory: Path, *names: str, head: str = "def handle_{name}") -> list[Path]:
    paths: list[Path] = []
    for name in names:
        path = directory / f"{name}.py"
        path.write_text(f"{head.format(name=name)}(value, other, third):\n{BLOCK}")
        paths.append(path.resolve())
    return paths


@pytest.fixture
def fake_jscpd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Put a fake jscpd next to a stand-in interpreter; the returned function picks its mode."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    binary = bin_dir / "jscpd"
    binary.write_text(f"#!{sys.executable}\n{FAKE}")
    binary.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(bin_dir / "python"))

    def use(mode: str) -> None:
        monkeypatch.setenv("FAKE_JSCPD", mode)

    return use


def test_three_copies_come_back_as_pairs_against_the_first_with_1_based_columns(
    tmp_path: Path,
) -> None:
    a, b, c = write_copies(tmp_path, "a", "b", "c")

    pairs = find_clones([a, b, c], min_tokens=30, min_lines=5)

    # `def handle_a` is 12 bytes, so the match starts at the `(`; the last line is 42 bytes long.
    span = (1, 13, 8, 43)
    assert pairs == [
        ClonePair(Fragment(a, *span), Fragment(b, *span)),
        ClonePair(Fragment(a, *span), Fragment(c, *span)),
    ]


def test_columns_count_utf8_bytes(tmp_path: Path) -> None:
    paths = write_copies(tmp_path, "a", "b", head="def {name}é")

    [pair] = find_clones(paths, min_tokens=30, min_lines=5)

    assert pair.first.column == len("def aé".encode()) + 1


def test_files_with_no_clones_give_no_pairs(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    first.write_text("value = 1\n")
    second = tmp_path / "second.py"
    second.write_text("def other():\n    return 2\n")

    assert find_clones([first.resolve(), second.resolve()], min_tokens=30, min_lines=5) == []


def test_copies_within_one_file_are_found(tmp_path: Path) -> None:
    path = tmp_path / "one.py"
    path.write_text(
        f"def first(value, other, third):\n{BLOCK}\n\ndef second(value, other, third):\n{BLOCK}"
    )

    [pair] = find_clones([path.resolve()], min_tokens=30, min_lines=5)

    assert (pair.first.path, pair.second.path) == (path.resolve(), path.resolve())
    assert (pair.first.line, pair.second.line) == (1, 11)


def test_a_block_must_span_more_than_min_lines(tmp_path: Path) -> None:
    lines = "".join(f"alpha_{i} = compute(beta_{i}, gamma_{i}, delta_{i})\n" for i in range(5))
    paths: list[Path] = []
    for name in ("a", "b"):
        path = tmp_path / f"{name}.py"
        path.write_text(lines)
        paths.append(path.resolve())

    assert find_clones(paths, min_tokens=10, min_lines=5) == []
    assert len(find_clones(paths, min_tokens=10, min_lines=4)) == 1


def test_a_projects_own_jscpd_config_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".jscpd.json").write_text('{"minTokens": 1000, "exitCode": 1}')
    (tmp_path / ".gitignore").write_text("*.py\n")
    paths = write_copies(tmp_path, "a", "b")

    assert len(find_clones(paths, min_tokens=30, min_lines=5)) == 1


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("old", "jscpd 5.4.1 is required, found 5.3.0"),
        ("fail", "jscpd exited with status 1: Error: path does not exist: /gone.py"),
        ("garbage", "jscpd's report could not be read"),
        ("wrong-shape", "jscpd's report could not be read"),
        ("stranger", "jscpd reported a file it was not given: /elsewhere/x.py"),
        ("no-report", "jscpd wrote no jscpd-report.json"),
    ],
)
def test_each_failure_is_loud_and_names_the_extra(
    tmp_path: Path, fake_jscpd, mode: str, reason: str
) -> None:
    fake_jscpd(mode)

    with pytest.raises(ProjectRuleFailure) as raised:
        find_clones(write_copies(tmp_path, "a", "b"), min_tokens=30, min_lines=5)

    assert reason in str(raised.value)
    assert EXTRA in str(raised.value)


def test_a_hung_jscpd_times_out(
    tmp_path: Path, fake_jscpd, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_jscpd("hang")
    monkeypatch.setattr(jscpd, "TIMEOUT_SECONDS", 1)

    with pytest.raises(ProjectRuleFailure, match="did not finish within 1 seconds") as raised:
        find_clones(write_copies(tmp_path, "a", "b"), min_tokens=30, min_lines=5)

    assert EXTRA in str(raised.value)


def test_a_missing_jscpd_names_the_extra_and_the_uvx_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setattr(sys, "executable", str(empty / "python"))
    monkeypatch.setenv("PATH", str(empty))

    with pytest.raises(ProjectRuleFailure, match="jscpd was not found") as raised:
        find_clones(write_copies(tmp_path, "a", "b"), min_tokens=30, min_lines=5)

    assert EXTRA in str(raised.value)
    assert "uvx --from 'house-lint[duplicates]' house-lint check" in str(raised.value)


def test_the_default_timeout_is_two_minutes() -> None:
    assert jscpd.TIMEOUT_SECONDS == 120


def test_a_file_over_jscpds_default_size_limit_is_still_compared(tmp_path: Path) -> None:
    small = write_copies(tmp_path, "a")
    padding = "".join(f"value_{index} = {index}\n" for index in range(80_000))
    big = tmp_path / "big.py"
    big.write_text(f"{padding}def handle_big(value, other, third):\n{BLOCK}")
    assert big.stat().st_size > 1_048_576

    assert len(find_clones([*small, big.resolve()], min_tokens=30, min_lines=5)) == 1


def test_the_pinned_version_matches_the_extra_the_dev_group_and_the_docs() -> None:
    repo = Path(__file__).parents[2]
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text())
    pin = f"jscpd=={jscpd.JSCPD_VERSION}"

    assert pyproject["project"]["optional-dependencies"]["duplicates"] == [pin]
    assert pin in pyproject["dependency-groups"]["dev"]
    assert f'additional_dependencies: ["{pin}"]' in (repo / "README.md").read_text()
