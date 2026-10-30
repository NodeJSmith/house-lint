"""Differential parity between configured `exclude` and real `git check-ignore`.

`exclude` documents gitignore syntax but matches patterns itself, through `pathspec`, so a
`pathspec` upgrade could quietly change what a pattern means. Everything `.gitignore` decides comes
from git directly and has nothing to compare against.

Each scenario declares the patterns and the Python files to create, and the test asserts that the
set of files `exclude` drops is exactly the set git ignores when the same lines are in a root
`.gitignore`. A scenario needs no expected-value literal — git supplies it — so adding a regression
case costs one table entry.

Skipped wholesale when git is unavailable. The autouse `isolated_git_environment` fixture hides the
developer's git settings and ignore files, so they can never change the outcome or point the
comparison at a different repository.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from _git_harness import git_ignored, init_repository

from house_lint.discovery import DiscoveryResult, discover_files

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

PY_CONTENT = "x = 1\n"
NESTED_FILES = ("src/a.py", "src/sub/b.py", "src/sub/deep/c.py")


@dataclass(frozen=True)
class Scenario:
    """One parity case: `patterns` as `exclude` and as a root `.gitignore`, over `files`."""

    name: str
    patterns: tuple[str, ...]
    files: tuple[str, ...] = NESTED_FILES


SCENARIOS = (
    Scenario("unanchored name at any depth", ("a.py",), ("src/a.py", "src/sub/a.py", "src/b.py")),
    Scenario("glob at any depth", ("*.py",)),
    Scenario("everything", ("*",)),
    Scenario("directory name at any depth", ("sub/",)),
    Scenario("anchored directory", ("src/sub/",)),
    Scenario("anchored name without a slash at the end", ("src/sub",)),
    Scenario("anchored file", ("src/a.py",)),
    Scenario("one-level glob below an anchor", ("src/*",)),
    Scenario("every directory, sparing a file at the root", ("*/",), ("a.py", "src/b.py")),
    Scenario("'**/' spares a file at the root", ("**/",), ("a.py", "src/b.py")),
    Scenario("'**' covers files too", ("**",), ("a.py", "src/b.py")),
    Scenario("'**/**/' collapses to '**/'", ("**/**/",), ("a.py", "src/b.py")),
    Scenario("'**/**' collapses to '**'", ("**/**",), ("a.py", "src/b.py")),
    Scenario("'**/**/<name>' collapses to '**/<name>'", ("**/**/b.py",)),
    Scenario("trailing '/**' names a directory's contents", ("src/sub/**",)),
    Scenario("'/**/' in the middle", ("src/**/c.py",)),
    Scenario(
        "a '**' fused to a literal segment does not un-anchor the pattern",
        ("foo**/**",),
        ("foobar/x.py", "sub/foobar/y.py"),
    ),
    Scenario(
        "leading '**/' keeps its multi-segment suffix",
        ("**/sub/deep.py",),
        ("src/sub/deep.py", "src/other/sub/deep.py", "src/sub/shallow.py", "src/deep.py"),
    ),
    Scenario(
        "leading '**/' with a fused interior segment matches at any depth",
        ("**/x/**foo",),
        ("x/yfoo/a.py", "sub/x/yfoo/b.py"),
    ),
    Scenario(
        "directory-only pattern does not match a same-named .py file",
        ("b.py/",),
        ("src/a.py", "src/b.py"),
    ),
    Scenario(
        "unescaped trailing whitespace is insignificant", ("a.py ",), ("src/a.py", "src/b.py")
    ),
    Scenario("comment line matches nothing", ("#a.py",), ("#a.py", "a.py")),
    Scenario("escaped leading hash is literal", (r"\#a.py",), ("#a.py", "a.py")),
    Scenario("escaped leading bang is literal", (r"\!a.py",), ("!a.py", "a.py")),
    Scenario("character class", ("[ab].py",), ("a.py", "b.py", "c.py")),
    Scenario("question mark", ("?.py",), ("a.py", "ab.py")),
)


def _build(root: Path, scenario: Scenario) -> None:
    for relative in scenario.files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PY_CONTENT)
    (root / ".gitignore").write_text("\n".join(scenario.patterns) + "\n")
    init_repository(root)


def _dropped(result: DiscoveryResult, root: Path, files: tuple[str, ...]) -> set[str]:
    selected = {path.relative_to(root.resolve()).as_posix() for path in result.files}
    return {relative for relative in files if relative not in selected}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_excludes_match_git_on_a_full_scan(scenario: Scenario, tmp_path: Path) -> None:
    _build(tmp_path, scenario)

    result = discover_files(tmp_path, excludes=scenario.patterns, use_git=False)

    assert result.errors == ()
    assert _dropped(result, tmp_path, scenario.files) == git_ignored(tmp_path, scenario.files)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_excludes_match_git_for_named_files(scenario: Scenario, tmp_path: Path) -> None:
    """Reaching each file directly, with no directory-level pruning to help."""
    _build(tmp_path, scenario)

    result = discover_files(
        tmp_path,
        explicit=tuple(tmp_path / item for item in scenario.files),
        excludes=scenario.patterns,
        use_git=False,
    )

    assert result.errors == ()
    assert _dropped(result, tmp_path, scenario.files) == git_ignored(tmp_path, scenario.files)


def test_doubled_backslash_before_trailing_space_is_the_known_divergence(tmp_path: Path) -> None:
    """Documented in `docs/configuration.md`. If this starts failing, `pathspec` fixed it: move
    the pattern into `SCENARIOS` and delete the sentence there."""
    files = ("pkg\\/a.py",)
    pattern = "pkg\\\\ "
    _build(tmp_path, Scenario("divergence", (pattern,), files))

    result = discover_files(tmp_path, excludes=(pattern,), use_git=False)

    assert git_ignored(tmp_path, files) == set(files)
    assert _dropped(result, tmp_path, files) == set()


@pytest.mark.parametrize("broken", ["drops-everything", "drops-nothing"])
def test_harness_detects_a_real_divergence(
    broken: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard the guard: prove the comparison actually fails when `exclude` disagrees with git.

    A parity suite that could not fail would be worse than no suite, because it reads as
    evidence. So this substitutes a deliberately broken `discover_files` in both directions and
    asserts the comparison raises: a stub that dropped everything would satisfy a suite whose
    scenarios all expect drops, and one that dropped nothing would satisfy the opposite.
    """
    files = ("src/a.py", "src/b.py")
    scenario = Scenario("guard", ("a.py",), files)

    def broken_discovery(root: Path, **_: object) -> DiscoveryResult:
        if broken == "drops-everything":
            return DiscoveryResult(())
        return DiscoveryResult(tuple(sorted(root.resolve() / item for item in files)))

    # `tests` is not an importable package, so patch this module's own globals — which is what
    # `test_excludes_match_git_on_a_full_scan` resolves `discover_files` through.
    monkeypatch.setitem(globals(), "discover_files", broken_discovery)

    with pytest.raises(AssertionError):
        test_excludes_match_git_on_a_full_scan(scenario, tmp_path)
