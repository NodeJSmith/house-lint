"""`house-lint check --base`: only the findings `HEAD` introduced since its merge-base."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from house_lint import cli

LAZY_JSON = "def load():\n    import json\n    return json\n"
LAZY_OS = "def where():\n    import os\n    return os\n"
BLOCK = """\
    total = value + other + third
    if total > 10:
        result = total * 2
    else:
        result = total - 1
    items = [result, value, other, third]
    return sorted(items, key=lambda x: -x)
"""


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False, timeout=30
    )
    if completed.returncode != 0:
        pytest.fail(f"git {' '.join(args)} failed: {completed.stderr}")
    return completed.stdout.strip()


def commit(repo: Path, files: dict[str, str | None], message: str = "change") -> str:
    """Write (or, for None, delete) each file, then commit everything."""
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "commit.gpgsign", "false")
    return root


def branch(repo: Path, name: str = "feature") -> None:
    git(repo, "switch", "-q", "-c", name)


def gate(
    capsys: pytest.CaptureFixture[str], root: Path, ref: str = "main", **kwargs: Any
) -> tuple[int, dict[str, Any]]:
    code = cli.check(base=ref, root=root, format="json", **kwargs)
    return code, json.loads(capsys.readouterr().out)


def reported(result: dict[str, Any]) -> list[tuple[str, str]]:
    return [(finding["rule_id"], finding["path"]) for finding in result["findings"]]


def test_only_a_finding_the_branch_added_is_reported(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base_commit = commit(repo, {"old.py": LAZY_JSON})
    branch(repo)
    head_commit = commit(repo, {"old.py": LAZY_JSON + LAZY_OS, "clean.py": "x = 1\n"})

    code, result = gate(capsys, repo)

    assert code == 1
    assert [(f["path"], f["line"]) for f in result["findings"]] == [("old.py", 5)]
    assert result["base"] == {
        "ref": "main",
        "base_commit": base_commit,
        "head_commit": head_commit,
        "preexisting_count": 1,
    }
    assert result["root"] == str(repo)
    assert result["files_scanned"] == 2


def test_a_branch_with_only_old_findings_passes_and_says_what_it_left_out(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"old.py": LAZY_JSON})
    branch(repo)
    commit(repo, {"other.py": "x = 1\n"})

    code = cli.check(base="main", root=repo)
    output = capsys.readouterr().out

    assert code == 0
    assert "base: main (merge-base " in output
    assert "1 pre-existing finding not reported" in output
    assert output.rstrip().endswith("summary: 0 findings, 0 errors, 0 suppressed")


def test_the_working_tree_is_never_read(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(repo, {"old.py": LAZY_JSON})
    branch(repo)
    commit(repo, {"new.py": LAZY_OS})
    # Uncommitted: fixing the new finding, and adding another.
    (repo / "new.py").write_text("x = 1\n")
    (repo / "untracked.py").write_text(LAZY_JSON.replace("load", "other"))

    code, result = gate(capsys, repo)

    assert code == 1
    assert reported(result) == [("HSL002", "new.py")]


def test_a_fix_on_main_after_the_fork_leaves_the_branch_debt_old(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"old.py": LAZY_JSON})
    branch(repo)
    commit(repo, {"other.py": "x = 1\n"})
    git(repo, "switch", "-q", "main")
    commit(repo, {"old.py": "import json\n"})
    git(repo, "switch", "-q", "feature")

    code, result = gate(capsys, repo)

    assert code == 0
    assert result["base"]["preexisting_count"] == 1


def test_moving_old_findings_into_a_new_module_passes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"big.py": LAZY_JSON + LAZY_OS})
    branch(repo)
    # Blank lines first, so the moved finding's line number changes too.
    commit(repo, {"big.py": LAZY_JSON, "split.py": "\n\n" + LAZY_OS})

    code, result = gate(capsys, repo)

    assert code == 0
    assert result["findings"] == []


def test_a_finding_added_beside_identical_old_ones_counts_once(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"m.py": LAZY_OS + LAZY_OS.replace("where", "there")})
    branch(repo)
    added = LAZY_OS.replace("where", "first")
    commit(repo, {"m.py": added + LAZY_OS + LAZY_OS.replace("where", "there")})

    code, result = gate(capsys, repo)

    # Exactly one is new. Which identical one is reported is first-come, not the added line.
    assert code == 1
    assert reported(result) == [("HSL002", "m.py")]
    assert result["base"]["preexisting_count"] == 2


def test_editing_inside_a_flagged_span_reports_it_again(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"old.py": LAZY_JSON})
    branch(repo)
    commit(repo, {"old.py": LAZY_JSON.replace("import json", "import json as j")})

    code, result = gate(capsys, repo)

    assert code == 1
    assert reported(result) == [("HSL002", "old.py")]


def test_a_file_is_new_under_hsl102_only_when_it_grew(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = (
        '[tool.house-lint]\nselect = ["HSL102"]\n[tool.house-lint.rules.HSL102]\nmax_lines = 3\n'
    )
    commit(repo, {"pyproject.toml": config, "grows.py": "x = 1\n" * 5, "same.py": "y = 1\n" * 5})
    branch(repo)
    commit(
        repo,
        {
            "grows.py": "x = 1\n" * 6,
            "same.py": None,
            "moved/same.py": "y = 1\n" * 5,
            "added.py": "z = 1\n" * 4,
        },
    )

    code, result = gate(capsys, repo)

    assert code == 1
    assert sorted(reported(result)) == [("HSL102", "added.py"), ("HSL102", "grows.py")]


def test_the_config_committed_at_head_governs_both_scans(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"pyproject.toml": "[tool.house-lint]\nselect = []\n", "old.py": LAZY_JSON})
    branch(repo)
    # Adopting a rule with a backlog doesn't fail on the backlog.
    commit(repo, {"pyproject.toml": '[tool.house-lint]\nselect = ["HSL002"]\n'})
    # An uncommitted config edit is ignored.
    (repo / "pyproject.toml").write_text("[tool.house-lint]\nselect = []\n")

    code, result = gate(capsys, repo)

    assert code == 0
    assert result["enabled_rules"] == ["HSL002", "HSL900"]
    assert result["config"] == str(repo / "pyproject.toml")
    assert result["base"]["preexisting_count"] == 1


def test_a_project_below_the_repository_top_level_is_gated_from_its_own_root(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = '[tool.house-lint]\nselect = ["HSL002"]\n'
    commit(repo, {"pkg/pyproject.toml": config, "pkg/a.py": LAZY_JSON, "outside.py": LAZY_OS})
    branch(repo)
    commit(repo, {"pkg/a.py": None, "pkg/sub/a.py": LAZY_JSON, "pkg/b.py": LAZY_OS})

    code, result = gate(capsys, repo / "pkg")

    assert code == 1
    assert reported(result) == [("HSL002", "b.py")]
    assert result["root"] == str(repo / "pkg")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"paths": [Path("a.py")]}, "takes no paths"),
        ({"no_git": True}, "--no-git"),
    ],
)
def test_paths_and_no_git_are_usage_errors(
    repo: Path, capsys: pytest.CaptureFixture[str], kwargs: dict[str, Any], message: str
) -> None:
    commit(repo, {"a.py": "x = 1\n"})

    code, result = gate(capsys, repo, **kwargs)

    assert code == 2
    assert message in result["errors"][0]["message"]


@pytest.mark.parametrize("ref", ["no-such-branch", "--output=/tmp/x"])
def test_a_ref_that_names_no_commit_fails_the_run(
    repo: Path, capsys: pytest.CaptureFixture[str], ref: str
) -> None:
    commit(repo, {"a.py": "x = 1\n"})

    code, result = gate(capsys, repo, ref)

    assert code == 3
    assert result["errors"][0]["message"].startswith(f"--base {ref!r} does not name a commit; ")
    assert "fetch full history" in result["errors"][0]["message"]


def test_no_merge_base_fails_the_run(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(repo, {"a.py": "x = 1\n"})
    git(repo, "switch", "-q", "--orphan", "unrelated")
    commit(repo, {"b.py": "x = 1\n"})

    code, result = gate(capsys, repo)

    assert code == 3
    assert "have no merge-base" in result["errors"][0]["message"]


def test_outside_a_repository_fails_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")

    code, result = gate(capsys, tmp_path)

    assert code == 3
    assert "needs a git repository" in result["errors"][0]["message"]


def test_an_error_from_the_base_scan_fails_the_gate_and_says_where_it_came_from(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"broken.py": "def (:\n"})
    branch(repo)
    commit(repo, {"broken.py": "x = 1\n"})

    code, result = gate(capsys, repo)

    assert code == 3
    [err] = result["errors"]
    assert err["path"] == "broken.py"
    assert err["message"].startswith("base run: ")


def test_both_scans_share_the_project_cache_and_leave_no_extraction_behind(
    repo: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    monkeypatch.setenv("TMPDIR", str(temporary))
    monkeypatch.setattr("tempfile.tempdir", None)
    commit(repo, {".gitignore": ".house-lint-cache/\n", "old.py": LAZY_JSON})
    branch(repo)
    commit(repo, {"new.py": LAZY_OS})

    first = gate(capsys, repo)
    second = gate(capsys, repo)

    assert first == second
    # old.py is identical at both commits, so one entry serves both scans, plus new.py's.
    assert len(list((repo / ".house-lint-cache").rglob("*.json"))) == 2
    assert list(temporary.iterdir()) == []


def test_hsl104_reports_only_the_copy_the_branch_added(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = '[tool.house-lint]\nselect = ["HSL104"]\n'
    copies = {f"{name}.py": f"def handle_{name}(value, other, third):\n{BLOCK}" for name in "abc"}
    commit(repo, {"pyproject.toml": config, **copies})
    branch(repo)
    commit(repo, {"d.py": f"def handle_d(value, other, third):\n{BLOCK}"})

    code, result = gate(capsys, repo)

    assert code == 1
    assert reported(result) == [("HSL104", "d.py")]
    assert result["base"]["preexisting_count"] == 3


def test_renaming_a_file_with_a_copy_passes_hsl104(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = '[tool.house-lint]\nselect = ["HSL104"]\n'
    copies = {f"{name}.py": f"def handle_{name}(value, other, third):\n{BLOCK}" for name in "abc"}
    commit(repo, {"pyproject.toml": config, **copies})
    branch(repo)
    git(repo, "mv", "a.py", "z.py")
    commit(repo, {})

    code, result = gate(capsys, repo)

    assert code == 0
    assert result["base"]["preexisting_count"] == 3


def test_an_explicit_config_is_read_from_the_head_commit_at_the_same_path(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = '[tool.house-lint]\nselect = ["HSL002"]\n'
    commit(repo, {"lint/custom.toml": config, "a.py": "x = 1\n"})
    branch(repo)
    commit(repo, {"b.py": LAZY_OS})
    (repo / "lint" / "custom.toml").write_text("[tool.house-lint]\nselect = []\n")

    code, result = gate(capsys, repo, config=repo / "lint" / "custom.toml")

    assert code == 1
    assert reported(result) == [("HSL002", "b.py")]
    assert result["config"] == str(repo / "lint" / "custom.toml")


def test_an_explicit_config_not_committed_at_head_is_a_usage_error(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"a.py": "x = 1\n"})
    branch(repo)
    commit(repo, {"b.py": LAZY_OS})
    (repo / "house-lint.toml").write_text("[house-lint]\nselect = []\n")

    code, result = gate(capsys, repo, config=repo / "house-lint.toml")

    assert code == 2
    assert result["errors"][0]["message"] == (
        "--config house-lint.toml is not committed at HEAD; --base reads the config from the commit"
    )


def test_a_config_outside_the_repository_is_a_usage_error(
    repo: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit(repo, {"a.py": "x = 1\n"})
    outside = tmp_path / "elsewhere" / "house-lint.toml"
    outside.parent.mkdir()
    outside.write_text("[house-lint]\n")
    monkeypatch.chdir(repo)

    # No --root: the config's directory becomes the root, as in a normal run.
    code = cli.check(base="main", config=outside, format="json")
    result = json.loads(capsys.readouterr().out)

    assert code == 2
    assert "--config to be inside the git repository" in result["errors"][0]["message"]


def test_fail_on_empty_judges_the_head_scan(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(repo, {"a.py": "x = 1\n"})
    branch(repo)
    commit(repo, {"a.py": None, "notes.txt": "no python here\n"})

    code, result = gate(capsys, repo, fail_on_empty=True)

    assert code == 1
    assert result["files_scanned"] == 0


def test_span_text_is_decoded_as_the_python_layer_decodes_it(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    latin = "# -*- coding: latin-1 -*-\n# \xe9t\xe9\n" + LAZY_JSON
    bom = "\ufeff" + LAZY_OS
    (repo / "latin.py").write_bytes(latin.encode("latin-1"))
    (repo / "bom.py").write_bytes(bom.encode("utf-8"))
    commit(repo, {})
    branch(repo)
    # Moved down a line each: the findings pair on their decoded text, not their position.
    (repo / "latin.py").write_bytes(latin.replace("def load", "\ndef load").encode("latin-1"))
    (repo / "bom.py").write_bytes(("\ufeff\n" + LAZY_OS).encode("utf-8"))
    commit(repo, {})

    code, result = gate(capsys, repo)

    assert code == 0
    assert result["base"]["preexisting_count"] == 2


def test_command_line_selection_reaches_both_scans(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"old.py": LAZY_JSON})
    branch(repo)
    commit(repo, {"new.py": "def where():\n    import os\n    return os\nLIMIT = 3\n"})

    code, result = gate(capsys, repo, ignore=["HSL002"])

    assert code == 1
    assert reported(result) == [("HSL004", "new.py")]
    assert result["base"]["preexisting_count"] == 0


def test_an_hsl900_finding_pairs_like_any_other(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = "x = 1  # house-lint: ignore[HSL002]\n"
    commit(repo, {"old.py": stale})
    branch(repo)
    commit(repo, {"old.py": "\n" + stale, "new.py": "y = 2  # house-lint: ignore[HSL004]\n"})

    code, result = gate(capsys, repo)

    assert code == 1
    assert reported(result) == [("HSL900", "new.py")]


def test_an_error_from_the_head_scan_says_so(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {"a.py": "x = 1\n"})
    branch(repo)
    commit(repo, {"a.py": "def (:\n"})

    code, result = gate(capsys, repo)

    assert code == 3
    [err] = result["errors"]
    assert err["path"] == "a.py"
    assert err["message"].startswith("head run: ")


def test_a_line_finding_on_a_non_ascii_line_keys_on_its_whole_line(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # HSL002 is off, so its pragma is an HSL900 finding.
    config = '[tool.house-lint]\nselect = ["HSL003"]\n'
    old = "x = 1  # house-lint: ignore[HSL002] \u2014 \u00e9t\u00e9 one\n"
    commit(repo, {"pyproject.toml": config, "a.py": old})
    branch(repo)
    # The same pragma and message, differing only in the line's last characters.
    commit(repo, {"a.py": old + old.replace("one", "two")})

    code, result = gate(capsys, repo)

    assert code == 1
    assert [(f["path"], f["line"]) for f in result["findings"]] == [("a.py", 2)]
