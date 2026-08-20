"""Differential parity between house-lint's discovery and real `git check-ignore`.

house-lint reimplements git's ignore semantics on top of `pathspec` rather than shelling out to
git, so every rule it reimplements is a chance to drift. The unit tests in
`tests/unit/test_discovery.py` pin the behaviour house-lint *intends*; these pin it against the
only authority that matters, by building a real repository and asking git itself.

Each scenario declares the `.gitignore` files to write and the Python files to create, and the
test asserts that the set of files house-lint skips is exactly the set git ignores. A scenario
needs no expected-value literal — git supplies it — so adding a regression case costs one table
entry.

Skipped wholesale when git is unavailable. Git config is neutralised (`GIT_CONFIG_GLOBAL`,
`GIT_CONFIG_SYSTEM`, `core.excludesFile`) so a developer's own global ignore rules can never
change the outcome.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from house_lint.discovery import discover_files

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

PY_CONTENT = "x = 1\n"


@dataclass(frozen=True)
class Scenario:
    """One parity case.

    `ignores` maps an owning directory (root-relative posix, `""` for the repository root) to
    that directory's `.gitignore` lines. `files` are the Python files to create. `include` is
    the discovery root set, mirroring `[tool.house-lint] include`.
    """

    name: str
    ignores: dict[str, list[str]]
    files: tuple[str, ...]
    include: tuple[str, ...] = ("src",)


SCENARIOS = (
    # --- The nested-`**/` family. `**/` is directory-only: git ignores every directory below
    # its owner but leaves an immediate regular file alone.
    Scenario(
        "nested '**/' spares an immediate regular file",
        {"src": ["**/"]},
        ("src/a.py", "src/sub/b.py"),
    ),
    Scenario(
        "root '**/' ignores every nested directory",
        {"": ["**/"]},
        ("src/a.py", "src/sub/b.py"),
    ),
    Scenario(
        "nested '**' without a trailing slash covers files too",
        {"src": ["**"]},
        ("src/a.py", "src/sub/b.py"),
    ),
    # --- Directory-only patterns must not match a same-named regular file, and a directory-form
    # negation must be able to cancel an earlier file-form match (last matching line wins).
    Scenario(
        "directory-only pattern does not match a same-named .py file",
        {"": ["b.py/"]},
        ("src/a.py", "src/b.py"),
    ),
    Scenario(
        "directory-form negation cancels an earlier unanchored ignore",
        {"": ["cache", "!cache/"]},
        ("src/a.py", "src/cache/c.py"),
    ),
    Scenario(
        "nested directory-form negation cancels its own earlier ignore",
        {"src": ["sub/", "!sub/"]},
        ("src/a.py", "src/sub/b.py"),
    ),
    # --- A file inside an ignored directory can never be re-included by a later negation,
    # including when the ignored directory is the discovery root itself.
    Scenario(
        "negation cannot resurrect a file from an ignored discovery root",
        {"": ["src/", "!*.py"]},
        ("src/a.py", "src/sub/b.py"),
    ),
    Scenario(
        "negation cannot resurrect a file from an ignored child directory",
        {"": ["gen/", "!*.py"]},
        ("src/a.py", "src/gen/g.py"),
    ),
    Scenario(
        "negation cannot resurrect a file from a '**/'-ignored discovery root",
        {"": ["**/", "!b.py"]},
        ("src/a.py", "src/b.py", "src/sub/b.py"),
    ),
    # --- Ordinary per-directory semantics that must keep working.
    # --- A trailing `/**` names a directory's contents, so a negation can still re-include
    # something underneath it. The star in `a/*/**`'s middle segment must not exempt it.
    Scenario(
        "contents glob after a single-star segment still allows a negation underneath",
        {"": ["src/a/*/**", "!src/a/sub/keep.py"]},
        ("src/a/sub/keep.py", "src/a/sub/drop.py"),
    ),
    Scenario(
        "contents glob allows a negation underneath",
        {"": ["src/gen/**", "!src/gen/keep.py"]},
        ("src/gen/keep.py", "src/gen/drop.py"),
    ),
    Scenario(
        "nested pattern without a slash matches at any depth below its owner",
        {"src": ["a.py"]},
        ("src/a.py", "src/b.py", "src/sub/a.py"),
    ),
    Scenario(
        "nested leading-slash pattern is anchored to its own directory",
        {"src": ["/a.py"]},
        ("src/a.py", "src/sub/a.py"),
    ),
    Scenario(
        "closer .gitignore negation overrides a farther ignore",
        {"": ["*.py"], "src": ["!a.py"]},
        ("src/a.py", "src/sub/b.py"),
    ),
    Scenario(
        "wildcard-everything with re-included files",
        {"src": ["*", "!keep.py", "!.gitignore"]},
        ("src/keep.py", "src/drop.py"),
    ),
    Scenario(
        "trailing whitespace is insignificant unless backslash-escaped",
        {"src": ["a.py "]},
        ("src/a.py", "src/b.py"),
    ),
    Scenario(
        "directory names containing glob metacharacters stay literal",
        {"src": ["other/"]},
        ("src/s[1]/a.py", "src/other/a.py"),
    ),
    Scenario(
        "deeply nested .gitignore files compose",
        {"": ["*.py"], "src": ["!*.py"], "src/sub": ["b.py"]},
        ("src/a.py", "src/sub/a.py", "src/sub/b.py"),
    ),
)


def _init_repository(root: Path) -> None:
    subprocess.run(
        ["git", "init", "-q", "."],
        cwd=root,
        check=True,
        capture_output=True,
        env=_git_env(),
    )
    subprocess.run(
        ["git", "config", "core.excludesFile", ""],
        cwd=root,
        check=True,
        capture_output=True,
        env=_git_env(),
    )


def _git_env() -> dict[str, str]:
    """Neutralise every ignore source outside the repository under test."""
    return os.environ | {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "HOME": os.devnull,
    }


def _git_ignored(root: Path, relatives: tuple[str, ...]) -> set[str]:
    completed = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=root,
        input="\n".join(relatives),
        capture_output=True,
        text=True,
        env=_git_env(),
        check=False,
    )
    # `check-ignore` exits 1 when nothing matches, which is not a failure for us.
    if completed.returncode not in (0, 1):
        pytest.fail(f"git check-ignore failed: {completed.stderr}")
    return {line.strip() for line in completed.stdout.splitlines() if line.strip()}


def _build(root: Path, scenario: Scenario) -> None:
    for relative in scenario.files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PY_CONTENT)
    for owner, lines in scenario.ignores.items():
        path = (root / owner / ".gitignore") if owner else (root / ".gitignore")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_discovery_matches_git_check_ignore(scenario: Scenario, tmp_path: Path) -> None:
    _build(tmp_path, scenario)
    _init_repository(tmp_path)

    result = discover_files(tmp_path, include=scenario.include)
    selected = {path.relative_to(tmp_path.resolve()).as_posix() for path in result.files}
    house_lint_skipped = {relative for relative in scenario.files if relative not in selected}
    git_ignored = _git_ignored(tmp_path, scenario.files)

    assert result.errors == ()
    assert house_lint_skipped == git_ignored


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known pathspec/git divergence: a negated directory-only pattern ('!sub/') re-includes "
        "everything beneath it in pathspec, while git re-includes only the 'sub' entry itself "
        "and re-evaluates each descendant. Closing this would mean matching path components "
        "against git's precedence by hand instead of delegating whole-path matching to "
        "pathspec. It errs toward linting a file git would ignore, never toward skipping one, "
        "so it cannot hide a finding. Strict xfail: if this starts passing, the limitation is "
        "gone and the note in docs/configuration.md should go with it."
    ),
)
def test_negated_directory_pattern_does_not_re_include_nested_directories(tmp_path: Path) -> None:
    scenario = Scenario(
        "negated directory pattern re-includes only the directory itself",
        {"src": ["**/", "!sub/"]},
        ("src/a.py", "src/sub/a.py", "src/sub/deep/a.py"),
    )
    _build(tmp_path, scenario)
    _init_repository(tmp_path)

    result = discover_files(tmp_path, include=scenario.include)
    selected = {path.relative_to(tmp_path.resolve()).as_posix() for path in result.files}
    house_lint_skipped = {relative for relative in scenario.files if relative not in selected}

    assert house_lint_skipped == _git_ignored(tmp_path, scenario.files)


def test_harness_detects_a_real_divergence(tmp_path: Path) -> None:
    """Guard the guard: prove the comparison fails when discovery and git genuinely disagree.

    Without this, a bug that made `discover_files` return nothing (or the scenario table go
    empty) would turn every parity case green for the wrong reason.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(PY_CONTENT)
    (tmp_path / ".gitignore").write_text("a.py\n")
    _init_repository(tmp_path)

    git_ignored = _git_ignored(tmp_path, ("src/a.py",))
    assert git_ignored == {"src/a.py"}

    # Discovery agrees here, so an inverted expectation must fail.
    selected = {
        path.relative_to(tmp_path.resolve()).as_posix()
        for path in discover_files(tmp_path, include=("src",)).files
    }
    assert selected == set()
