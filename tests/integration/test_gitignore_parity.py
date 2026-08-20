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

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from _git_harness import git_ignored, init_repository

from house_lint.discovery import DiscoveryResult, discover_files

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

PY_CONTENT = "x = 1\n"


@dataclass(frozen=True)
class Scenario:
    """One parity case.

    `ignores` maps an owning directory (root-relative posix, `""` for the repository root) to
    that directory's `.gitignore` lines. `files` are the Python files to create. `include` is
    the discovery root set, mirroring `[tool.house-lint] include`.

    `symlinked_ignores` has the same shape as `ignores`, but writes the lines to a sibling file
    and leaves `.gitignore` as a symlink to it. Git does not follow a symlinked ignore file, so
    these scenarios pin that discovery does not either.
    """

    name: str
    ignores: dict[str, list[str]]
    files: tuple[str, ...]
    include: tuple[str, ...] = ("src",)
    symlinked_ignores: dict[str, list[str]] = field(default_factory=dict[str, list[str]])


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
    # git collapses a run of consecutive `**` segments into one, so each of these means exactly
    # what its single-`**` counterpart above means. house-lint rewrites nested patterns rather
    # than handing them to git, and only the one-segment spelling used to be recognised — the
    # repeated form fell through to the generic slash-containing branch and produced a pattern
    # that swallowed the immediate file this family exists to spare.
    Scenario(
        "nested '**/**/' collapses to '**/' and spares an immediate regular file",
        {"src": ["**/**/"]},
        ("src/a.py", "src/sub/b.py"),
    ),
    Scenario(
        "nested '**/**' collapses to '**' and covers files too",
        {"src": ["**/**"]},
        ("src/a.py", "src/sub/b.py"),
    ),
    Scenario(
        "a longer '**' run collapses the same way",
        {"src": ["**/**/**/"]},
        ("src/a.py", "src/sub/b.py"),
    ),
    Scenario(
        "'**/**/<name>' collapses to '**/<name>'",
        {"src": ["**/**/b.py"]},
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
        "negation beside its own directory exclusion in one file cannot resurrect",
        {"": ["src/generated/", "!src/generated/foo.py"]},
        ("src/generated/foo.py", "src/other.py"),
    ),
    Scenario(
        "negation cannot resurrect a file from a '**/'-ignored discovery root",
        {"": ["**/", "!b.py"]},
        ("src/a.py", "src/b.py", "src/sub/b.py"),
    ),
    # --- A trailing `/**` names a directory's contents, so a negation can still re-include
    # something underneath it. The star in `a/*/**`'s middle segment must not exempt it, and
    # `a/**/` composes the embedded-slash and directory-only forms in one pattern.
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
        "embedded slash combined with the directory-only contents glob",
        {"src": ["a/**/"]},
        ("src/a/y.py", "src/a/sub/x.py", "src/b.py"),
    ),
    # --- Ordinary per-directory semantics that must keep working.
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
    # Backslashes quote each other pairwise, so it is the parity of the run before the space
    # that decides whether the space survives — not merely whether a backslash precedes it.
    # An even run leaves the space unquoted and git strips it; an odd run escapes it.
    Scenario(
        "an even backslash run leaves trailing whitespace unquoted",
        {"src": ["dir\\\\ "]},
        ("src/a.py", "src/dir\\/b.py"),
    ),
    Scenario(
        "an odd backslash run quotes trailing whitespace",
        {"src": ["dir\\ "]},
        ("src/a.py", "src/dir /b.py"),
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
    # --- Symlinked ignore files. Git reads `.gitignore` with `lstat` and skips it when it is a
    # symlink; `Path.is_file()` follows one, so discovery would apply patterns git never applies.
    Scenario(
        "a symlinked nested .gitignore is not read",
        {},
        ("src/a.py", "src/sub/b.py"),
        symlinked_ignores={"src": ["*.py"]},
    ),
    Scenario(
        "a symlinked root .gitignore is not read",
        {},
        ("src/a.py",),
        symlinked_ignores={"": ["*.py"]},
    ),
    Scenario(
        "a symlinked nested .gitignore cannot negate a real ancestor ignore",
        {"": ["*.py"]},
        ("src/a.py",),
        symlinked_ignores={"src": ["!*.py"]},
    ),
)


def _build(root: Path, scenario: Scenario) -> None:
    for relative in scenario.files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PY_CONTENT)
    for owner, lines in scenario.ignores.items():
        path = (root / owner / ".gitignore") if owner else (root / ".gitignore")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
    for owner, lines in scenario.symlinked_ignores.items():
        directory = (root / owner) if owner else root
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "ignore-patterns"
        target.write_text("\n".join(lines) + "\n")
        (directory / ".gitignore").symlink_to(target.name)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_discovery_matches_git_check_ignore(scenario: Scenario, tmp_path: Path) -> None:
    _build(tmp_path, scenario)
    init_repository(tmp_path)

    result = discover_files(tmp_path, include=scenario.include)
    selected = {path.relative_to(tmp_path.resolve()).as_posix() for path in result.files}
    house_lint_skipped = {relative for relative in scenario.files if relative not in selected}
    ignored_by_git = git_ignored(tmp_path, scenario.files)

    assert result.errors == ()
    assert house_lint_skipped == ignored_by_git


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_explicit_paths_match_git_check_ignore(scenario: Scenario, tmp_path: Path) -> None:
    """The same table, but reaching each file directly instead of walking to it.

    An explicit path skips `_traversable_dirs` entirely and leans on
    `_combined_gitignore_spec` alone, so walk-time pruning cannot mask a wrong answer here.
    That makes this the stricter half of the pair: `house-lint check src/generated/foo.py` has
    to reach the same verdict git does with no directory traversal to help it.
    """
    _build(tmp_path, scenario)
    init_repository(tmp_path)

    result = discover_files(tmp_path, explicit=tuple(tmp_path / item for item in scenario.files))
    selected = {path.relative_to(tmp_path.resolve()).as_posix() for path in result.files}
    house_lint_skipped = {relative for relative in scenario.files if relative not in selected}

    assert result.errors == ()
    assert house_lint_skipped == git_ignored(tmp_path, scenario.files)


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
    init_repository(tmp_path)

    result = discover_files(tmp_path, include=scenario.include)
    selected = {path.relative_to(tmp_path.resolve()).as_posix() for path in result.files}
    house_lint_skipped = {relative for relative in scenario.files if relative not in selected}

    assert house_lint_skipped == git_ignored(tmp_path, scenario.files)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known pathspec/git divergence, same directory-negation family as the test above but "
        "pointing the other way — and this one *under*-lints. pathspec will not let a "
        "directory-only negation win for a directory path: "
        "GitIgnoreSpec.from_lines(('**', '!**/')).match_file('src') returns True, while git "
        "reports '.gitignore:2:!**/ src' re-including the directory and descends into it. "
        "house-lint asks pathspec exactly that when deciding whether to prune, so it prunes a "
        "subtree git walks and every file underneath vanishes from the scan. Passing 'src/' "
        "does not change pathspec's answer, so no shape of question fixes it here; deciding it "
        "means owning the matcher (see design/research/"
        "2026-08-20-gitignore-style-exclusion-inclusion/). Strict xfail: if this starts "
        "passing, the limitation is gone and docs/configuration.md should say so."
    ),
)
def test_negated_directory_pattern_re_includes_a_directory_git_descends_into(
    tmp_path: Path,
) -> None:
    scenario = Scenario(
        "directory-only negation re-includes the directory git walks",
        {"": ["**", "!**/"], "src": ["!**"]},
        ("src/a.py", "src/sub/b.py"),
    )
    _build(tmp_path, scenario)
    init_repository(tmp_path)

    result = discover_files(tmp_path, include=scenario.include)
    selected = {path.relative_to(tmp_path.resolve()).as_posix() for path in result.files}
    house_lint_skipped = {relative for relative in scenario.files if relative not in selected}

    assert house_lint_skipped == git_ignored(tmp_path, scenario.files)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_explicit_directory_arguments_match_git_check_ignore(
    scenario: Scenario, tmp_path: Path
) -> None:
    """The same table again, reached by naming a directory explicitly.

    `house-lint check src/` takes a third route: `_consider`'s directory branch, which is the
    one place neither the include-root walk nor an explicit *file* exercises. That branch is
    exactly what the excluded-ancestor fix had to patch, so leaving it to hand-written unit
    tests would reproduce the blind spot this harness exists to close.
    """
    _build(tmp_path, scenario)
    init_repository(tmp_path)

    result = discover_files(tmp_path, explicit=tuple(tmp_path / item for item in scenario.include))
    selected = {path.relative_to(tmp_path.resolve()).as_posix() for path in result.files}
    house_lint_skipped = {relative for relative in scenario.files if relative not in selected}

    assert result.errors == ()
    assert house_lint_skipped == git_ignored(tmp_path, scenario.files)


@pytest.mark.parametrize("broken", ["skips-everything", "skips-nothing"])
def test_harness_detects_a_real_divergence(
    broken: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard the guard: prove the comparison actually fails when discovery disagrees with git.

    A parity suite that could not fail would be worse than no suite, because it reads as
    evidence. Confirming a case where the two agree does not establish that — it exercises the
    same concordant path every scenario already does. So this substitutes a deliberately broken
    `discover_files` in both directions (selecting nothing, and selecting everything) and
    asserts the comparison raises. Both directions matter: a stub that skipped everything would
    satisfy a suite whose scenarios all expect skips, and one that skipped nothing would satisfy
    a suite whose scenarios all expect selections.
    """
    files = ("src/a.py", "src/b.py")
    _build(
        tmp_path,
        Scenario("guard", {"": ["a.py"]}, files),
    )
    init_repository(tmp_path)

    ignored_by_git = git_ignored(tmp_path, files)
    assert ignored_by_git == {"src/a.py"}, "fixture must produce a genuine mix of ignored and not"

    def broken_discovery(root: Path, **_: object) -> DiscoveryResult:
        if broken == "skips-everything":
            return DiscoveryResult(())
        return DiscoveryResult(tuple(sorted(root.resolve() / item for item in files)))

    # `tests` is not an importable package, so patch this module's own globals — which is what
    # `test_discovery_matches_git_check_ignore` resolves `discover_files` through.
    monkeypatch.setitem(globals(), "discover_files", broken_discovery)

    with pytest.raises(AssertionError):
        test_discovery_matches_git_check_ignore(Scenario("guard", {"": ["a.py"]}, files), tmp_path)
