import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from house_lint import discovery
from house_lint.config import STANDALONE_CONFIG_NAMES, ConfigError
from house_lint.discovery import DiscoveryError, ProjectResolution, discover_files, resolve_project
from house_lint.results import LintError

PY_CONTENT = "x = 1\n"


def test_builtin_excludes_matches_ruffs_default_exclude_list_plus_house_lint_extras() -> None:
    # Pins `BUILTIN_EXCLUDES` to Ruff's full default exclude list (25 entries, per
    # `ruff check --isolated --show-settings` against the locked ruff version -- `--isolated`
    # matters because this repo's own `pyproject.toml` sets `exclude = ["design"]`, which replaces
    # Ruff's defaults rather than extending them) plus house-lint's own extras: `__pycache__/`
    # and `.house-lint-cache/` (from `cache.CACHE_DIRNAME`) -- 27 total. Ruff has no concept of
    # house-lint's own cache directory, so it is not part of the Ruff-parity set; it is load-
    # bearing for a different reason -- once the default include scans from the project root,
    # a default scan would otherwise walk into house-lint's own `.house-lint-cache/` and
    # enumerate its version marker, `.gitignore`, and cached `<hash>.json` entries. A silent shrink of this tuple would widen the default scan surface
    # without any other test catching it.
    assert discovery.BUILTIN_EXCLUDES == (
        ".bzr/",
        ".direnv/",
        ".eggs/",
        ".git/",
        ".git-rewrite/",
        ".hg/",
        ".house-lint-cache/",
        ".ipynb_checkpoints/",
        ".mypy_cache/",
        ".nox/",
        ".pants.d/",
        ".pyenv/",
        ".pytest_cache/",
        ".pytype/",
        ".ruff_cache/",
        ".svn/",
        ".tox/",
        ".venv/",
        ".vscode/",
        "__pycache__/",
        "__pypackages__/",
        "_build/",
        "buck-out/",
        "dist/",
        "node_modules/",
        "site-packages/",
        "venv/",
    )
    assert len(discovery.BUILTIN_EXCLUDES) == 27


def test_no_path_scan_discovers_files_anywhere_under_root(tmp_path: Path) -> None:
    # Default include is now `(".",)` -- a root scan must find Python files in non-standard
    # directories (e.g. `packages/`), not just the previously hardcoded roots, while builtin
    # excludes still prune vendored/tooling directories like `.venv/`.
    expected: list[Path] = []
    for root_name in ("src", "tests", "scripts", "tools", "examples", "packages", "lib"):
        path = tmp_path / root_name / f"{root_name}.py"
        path.parent.mkdir(parents=True)
        path.write_text(PY_CONTENT)
        expected.append(path)
    top_level = tmp_path / "top.py"
    top_level.write_text(PY_CONTENT)
    expected.append(top_level)
    for excluded_dir in (".venv", "__pycache__", "node_modules", ".house-lint-cache"):
        excluded_path = tmp_path / excluded_dir / "excluded.py"
        excluded_path.parent.mkdir(parents=True)
        excluded_path.write_text(PY_CONTENT)

    result = discover_files(tmp_path)

    assert result.files == tuple(sorted(expected))
    for excluded_dir in (".venv", "__pycache__", "node_modules", ".house-lint-cache"):
        assert not any(excluded_dir in path.parts for path in result.files), (
            f"{excluded_dir} should be pruned by BUILTIN_EXCLUDES"
        )


def test_explicit_paths_are_strict_and_directories_recursive(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    py_file = source / "a.py"
    py_file.write_text(PY_CONTENT)
    (source / "readme.md").write_text("text\n")

    result = discover_files(tmp_path, explicit=(source, py_file, py_file))

    assert result.files == (py_file,)

    with pytest.raises(ValueError, match="does not exist"):
        discover_files(tmp_path, explicit=(tmp_path / "missing.py",))
    with pytest.raises(ValueError, match="Python"):
        discover_files(tmp_path, explicit=(source / "readme.md",))


def test_resolve_error_reports_traversal_and_keeps_other_include_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    kept = tmp_path / "kept" / "source.py"
    kept.parent.mkdir()
    kept.write_text(PY_CONTENT)
    resolve = Path.resolve

    def fail_broken_resolve(self: Path, *, strict: bool = False) -> Path:
        if self == broken:
            raise OSError("permission denied")
        return resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_broken_resolve)

    result = discover_files(tmp_path, include=("broken", "kept"))

    assert result.files == (kept,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == "broken"
    assert result.errors[0].operation == "resolve"


def test_direct_symlink_file_is_safe_only_when_target_is_in_root(tmp_path: Path) -> None:
    inside = tmp_path / "inside.py"
    inside.write_text(PY_CONTENT)
    outside = tmp_path.parent / "outside-house-lint.py"
    outside.write_text(PY_CONTENT)
    try:
        inside_link = tmp_path / "inside-link.py"
        outside_link = tmp_path / "outside-link.py"
        inside_link.symlink_to(inside)
        outside_link.symlink_to(outside)

        assert discover_files(tmp_path, explicit=(inside_link,)).files == (inside_link,)
        with pytest.raises(ValueError, match="outside root"):
            discover_files(tmp_path, explicit=(outside_link,))
    finally:
        outside.unlink()


def test_result_maps_each_file_to_the_resolved_target_it_validated(tmp_path: Path) -> None:
    """The scan reads `resolved_paths[file]`, not its own fresh `resolve()`, so containment is
    checked and the read is performed against the same target — see `SourceFile.__init__`."""
    source = tmp_path / "src"
    source.mkdir()
    plain = source / "a.py"
    plain.write_text("value = 1\n")
    link = source / "link.py"
    link.symlink_to(plain)

    result = discover_files(tmp_path, explicit=(plain, link))

    assert set(result.resolved_paths) == set(result.files)
    for reported, resolved in result.resolved_paths.items():
        assert resolved == reported.resolve()

    # `selected` is keyed by resolved path, so passing both keeps `plain` and drops `link` as a
    # duplicate — leaving every surviving entry with `resolved == reported` and the mapping's
    # whole reason to exist unexercised. Reaching the symlink alone is the only case where the
    # reported path and the validated target actually differ.
    link_only = discover_files(tmp_path, explicit=(link,))

    assert link_only.files == (link,)
    assert link_only.resolved_paths == {link: plain}


def test_walked_file_symlinks_are_not_selected(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    target = tmp_path / "target.py"
    target.write_text(PY_CONTENT)
    (source / "link.py").symlink_to(target)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == ()


def test_empty_full_scan_is_explicitly_clean(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=())

    assert result.files == ()
    assert result.errors == ()


def test_empty_root_with_default_include_is_an_empty_scan(tmp_path: Path) -> None:
    # With `include=(".",)` a missing root is impossible -- the root always exists -- so the
    # empty-scan case that matters now is a root with no Python files at all.
    result = discover_files(tmp_path)

    assert result.files == ()
    assert result.errors == ()


def test_nested_directory_symlink_reports_error_and_keeps_reachable_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "kept.py").write_text(PY_CONTENT)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "hidden.py").write_text(PY_CONTENT)
    (source / "linked").symlink_to(linked, target_is_directory=True)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source / "kept.py",)
    assert len(result.errors) == 1
    assert isinstance(result.errors[0], LintError)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == "src/linked"


def test_a_symlinked_excluded_directory_is_pruned_without_a_traversal_error(
    tmp_path: Path,
) -> None:
    # A directory this walk was never going to descend into regardless (here, house-lint's own
    # default `.house-lint-cache/`, a BUILTIN_EXCLUDES entry) must be pruned silently, even when
    # it happens to be a symlink -- not surface the "directory symlink is not traversed" error
    # `test_nested_directory_symlink_reports_error_and_keeps_reachable_files` pins for a
    # non-excluded symlinked directory. A cloned repository controls the path a project-relative
    # `.house-lint-cache` resolves to, so a scan must succeed cleanly even when that path is a
    # symlink pointing outside the checkout.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "kept.py").write_text(PY_CONTENT)
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    (tmp_path / ".house-lint-cache").symlink_to(outside, target_is_directory=True)

    result = discover_files(tmp_path)

    assert result.files == (tmp_path / "src" / "kept.py",)
    assert result.errors == ()


def test_a_symlinked_builtin_excluded_directory_is_pruned_without_a_traversal_error(
    tmp_path: Path,
) -> None:
    # Unlike the CACHE_DIRNAME special-case above (dropped before either check, so it never
    # exercised this ordering), an ordinary BUILTIN_EXCLUDES entry (here `.venv/`) used to reach
    # `_traversable_dirs`'s symlink stat before its exclusion check, so a symlinked `.venv`,
    # `venv`, or `node_modules` under the root-wide default scan produced a "directory symlink
    # is not traversed" traversal error and made an otherwise-clean `check` exit 3 instead of
    # pruning the directory like its non-symlinked counterpart.
    (tmp_path / "a.py").write_text(PY_CONTENT)
    outside = tmp_path / "outside-venv"
    outside.mkdir()
    (tmp_path / ".venv").symlink_to(outside, target_is_directory=True)

    result = discover_files(tmp_path)

    assert result.files == (tmp_path / "a.py",)
    assert result.errors == ()


def test_walker_error_reports_failed_directory_and_keeps_reachable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    kept = source / "kept.py"
    kept.write_text(PY_CONTENT)
    restricted = source / "restricted"

    def failing_walk(
        directory: Path,
        *,
        topdown: bool,
        followlinks: bool,
        onerror: object,
    ) -> object:
        assert topdown is True
        assert followlinks is False
        yield str(directory), [], [kept.name]
        assert callable(onerror)
        onerror(PermissionError(13, "Permission denied", str(restricted)))

    monkeypatch.setattr(discovery.os, "walk", failing_walk)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == "src/restricted"


def test_strict_path_failure_exposes_lint_error_conversion(tmp_path: Path) -> None:
    missing = tmp_path / "missing.py"

    with pytest.raises(DiscoveryError) as raised:
        discover_files(tmp_path, explicit=(missing,))

    assert isinstance(raised.value.error, LintError)
    assert raised.value.error.kind == "path"


def test_file_guardrail_preserves_prior_files_and_reports_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for name in ("a.py", "b.py", "c.py"):
        (source / name).write_text(PY_CONTENT)
    monkeypatch.setattr(discovery, "MAX_DISCOVERED_FILES", 2)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source / "a.py", source / "b.py")
    assert result.errors[0].kind == "budget"


def test_root_and_config_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL001"]\n')
    child = tmp_path / "nested"
    child.mkdir()
    monkeypatch.chdir(child)

    resolution = resolve_project()

    assert resolution.root == tmp_path
    assert resolution.config == tmp_path / "pyproject.toml"


def test_explicit_config_must_be_inside_explicit_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = tmp_path / "outside.toml"
    config.write_text("[tool.house-lint]\n")
    with pytest.raises(ConfigError, match="inside root"):
        resolve_project(root=root, config=config)


def test_explicit_config_without_root_uses_config_parent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = project / "custom.toml"
    config_path.write_text("[tool.house-lint]\n")

    resolution = resolve_project(config=config_path)

    assert resolution == type(resolution)(project, config_path)


def test_explicit_root_accepts_an_in_root_explicit_config(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path = root / "config.toml"
    config_path.write_text("[tool.house-lint]\n")

    resolution = resolve_project(root=root, config=config_path)

    assert resolution == type(resolution)(root, config_path)


def test_explicit_root_loads_only_its_own_config(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path = root / "pyproject.toml"
    config_path.write_text("[tool.house-lint]\n")

    resolution = resolve_project(root=root)

    assert resolution == type(resolution)(root, config_path)


def test_explicit_root_does_not_search_parent_config(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.house-lint]\n")
    root = tmp_path / "root"
    root.mkdir()

    resolution = resolve_project(root=root)

    assert resolution.root == root
    assert resolution.config is None


def test_upward_search_prefers_configured_pyproject_over_fallback_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.house-lint]\n")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "pyproject.toml").write_text("[project]\nname = 'nested'\n")
    cwd = nested / "src"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    resolution = resolve_project()

    assert resolution.root == tmp_path
    assert resolution.config == tmp_path / "pyproject.toml"


@pytest.mark.parametrize("marker", [".git", "pyproject.toml"])
def test_upward_search_falls_back_to_nearest_project_marker(tmp_path: Path, marker: str) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    if marker == ".git":
        (root / marker).mkdir()
    else:
        (root / marker).write_text("[project]\nname = 'root'\n")

    resolution = resolve_project(cwd=nested)

    assert resolution == type(resolution)(root, None)


def test_upward_search_stops_at_git_boundary_ignoring_outer_ancestor_config(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL001"]\n')
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "src"
    nested.mkdir()

    resolution = resolve_project(cwd=nested)

    assert resolution == type(resolution)(repo, None)


def test_invalid_ancestor_pyproject_is_a_configuration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.house-lint\n")
    child = tmp_path / "nested"
    child.mkdir()
    monkeypatch.chdir(child)

    with pytest.raises(ConfigError, match="invalid project configuration"):
        resolve_project()


def test_without_markers_falls_back_to_the_current_working_directory(tmp_path: Path) -> None:
    cwd = tmp_path / "unmarked" / "nested"
    cwd.mkdir(parents=True)

    resolution = resolve_project(cwd=cwd)

    assert resolution.root == cwd
    assert resolution.config is None


def test_upward_walk_finds_standalone_house_lint_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "house-lint.toml"
    config_path.write_text('[house-lint]\nselect = ["HSL001"]\n')
    child = tmp_path / "nested"
    child.mkdir()

    resolution = resolve_project(cwd=child)

    assert resolution == type(resolution)(tmp_path, config_path)


def test_upward_walk_finds_standalone_dot_house_lint_toml(tmp_path: Path) -> None:
    config_path = tmp_path / ".house-lint.toml"
    config_path.write_text('[house-lint]\nselect = ["HSL001"]\n')
    child = tmp_path / "nested"
    child.mkdir()

    resolution = resolve_project(cwd=child)

    assert resolution == type(resolution)(tmp_path, config_path)


def test_house_lint_toml_takes_precedence_over_pyproject_in_same_directory(
    tmp_path: Path,
) -> None:
    standalone = tmp_path / "house-lint.toml"
    standalone.write_text('[house-lint]\nselect = ["HSL001"]\n')
    (tmp_path / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL002"]\n')

    resolution = resolve_project(cwd=tmp_path)

    assert resolution.config == standalone
    assert resolution.shadowed == (tmp_path / "pyproject.toml",)


def test_house_lint_toml_without_table_falls_through_to_pyproject(tmp_path: Path) -> None:
    (tmp_path / "house-lint.toml").write_text('[not-house-lint]\nfoo = "bar"\n')
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.house-lint]\nselect = ["HSL001"]\n')

    resolution = resolve_project(cwd=tmp_path)

    assert resolution == type(resolution)(tmp_path, pyproject)


def test_root_without_config_finds_standalone_config_in_root_directory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path = root / "house-lint.toml"
    config_path.write_text('[house-lint]\nselect = ["HSL001"]\n')

    resolution = resolve_project(root=root)

    assert resolution == type(resolution)(root, config_path)


def test_root_without_config_finds_dot_standalone_config_in_root_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path = root / ".house-lint.toml"
    config_path.write_text('[house-lint]\nselect = ["HSL001"]\n')

    resolution = resolve_project(root=root)

    assert resolution == type(resolution)(root, config_path)


def test_explicit_config_path_to_standalone_house_lint_toml_resolves_correctly(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = project / "house-lint.toml"
    config_path.write_text('[house-lint]\nselect = ["HSL001"]\n')

    resolution = resolve_project(config=config_path)

    assert resolution == type(resolution)(project, config_path)


def test_malformed_lower_precedence_config_does_not_block_a_valid_winner(
    tmp_path: Path,
) -> None:
    winner = tmp_path / "house-lint.toml"
    winner.write_text('[house-lint]\nselect = ["HSL001"]\n')
    (tmp_path / ".house-lint.toml").write_text("not valid toml {{{\n")

    resolution = resolve_project(cwd=tmp_path)

    assert resolution.config == winner
    assert resolution.shadowed == ()


def test_malformed_sole_config_at_a_directory_is_still_a_configuration_failure(
    tmp_path: Path,
) -> None:
    (tmp_path / "house-lint.toml").write_text("not valid toml {{{\n")

    with pytest.raises(ConfigError, match="invalid project configuration"):
        resolve_project(cwd=tmp_path)


@pytest.mark.parametrize(
    "resolve",
    [
        lambda root: resolve_project(cwd=root),
        lambda root: resolve_project(root=root),
    ],
    ids=["upward-walk", "root-without-config"],
)
def test_discovery_order_matches_documented_precedence(
    tmp_path: Path, resolve: Callable[[Path], ProjectResolution]
) -> None:
    """`docs/configuration.md`'s "Discovery and precedence" section claims the order
    `house-lint.toml` -> `.house-lint.toml` -> `pyproject.toml` (with `[tool.house-lint]`), for
    both the upward walk (item 4) and `--root` without `--config` (item 3). Pins
    `STANDALONE_CONFIG_NAMES`' own ordering plus `_recognized_configs`' pyproject-last placement
    together, so a future reordering of either forces this test -- and the doc it backs -- to be
    updated in the same change. Follows the naming convention
    `test_no_path_scan_discovers_files_anywhere_under_root` sets for pinning a doc-claimed default
    to a test named after it.
    """
    assert STANDALONE_CONFIG_NAMES == ("house-lint.toml", ".house-lint.toml")
    (tmp_path / "house-lint.toml").write_text('[house-lint]\nselect = ["HSL001"]\n')
    (tmp_path / ".house-lint.toml").write_text('[house-lint]\nselect = ["HSL002"]\n')
    (tmp_path / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL003"]\n')

    resolution = resolve(tmp_path)

    assert resolution.config == tmp_path / "house-lint.toml"
    assert resolution.shadowed == (
        tmp_path / ".house-lint.toml",
        tmp_path / "pyproject.toml",
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`tmp_path` as a real repository, with every ignore source outside it disabled."""
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    _git(tmp_path, "init", "-q", ".")
    return tmp_path


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails the test if discovery starts any process."""

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"unexpected subprocess: {args}")

    monkeypatch.setattr(discovery.subprocess, "run", fail)


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=root, check=True, capture_output=True)


def _write(root: Path, *relatives: str) -> tuple[Path, ...]:
    paths = tuple(root / relative for relative in relatives)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PY_CONTENT)
    return paths


def _fake_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    bin_dir = tmp_path.parent / f"{tmp_path.name}-bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text(f"#!/bin/sh\n{script}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bin_dir))


def _raises_git_error(root: Path) -> LintError:
    with pytest.raises(DiscoveryError) as raised:
        discover_files(root)
    error = raised.value.error
    assert (error.kind, error.phase, error.operation) == ("traversal", "discovery", "git")
    assert "--no-gitignore" in error.message
    return error


def test_git_listing_honours_gitignore_and_scans_tracked_and_untracked(repo: Path) -> None:
    (repo / ".gitignore").write_text("ignored/\nignored.py\n")
    tracked, untracked = _write(repo, "src/tracked.py", "src/untracked.py")
    _write(repo, "ignored/a.py", "src/ignored.py")
    (repo / "notes.txt").write_text("text\n")
    _git(repo, "add", "src/tracked.py")

    assert discover_files(repo).files == (tracked, untracked)


def test_tracked_file_matching_an_ignore_pattern_is_scanned(repo: Path) -> None:
    tracked = _write(repo, "src/generated.py")[0]
    _git(repo, "add", "src/generated.py")
    (repo / ".gitignore").write_text("generated.py\n")

    assert discover_files(repo).files == (tracked,)


def test_nested_gitignore_negation_comes_from_git(repo: Path) -> None:
    (repo / ".gitignore").write_text("*.py\n")
    (repo / "src").mkdir()
    (repo / "src" / ".gitignore").write_text("!keep.py\n")
    keep = _write(repo, "src/keep.py", "src/drop.py")[0]

    assert discover_files(repo).files == (keep,)


def test_scan_rooted_below_the_repository_honours_ignore_rules_above_it(repo: Path) -> None:
    (repo / ".gitignore").write_text("from_gitignore.py\n")
    (repo / ".git" / "info" / "exclude").write_text("from_info_exclude.py\n")
    kept = _write(
        repo, "pkg/kept.py", "pkg/from_gitignore.py", "pkg/from_info_exclude.py", "other.py"
    )[0]

    assert discover_files(repo / "pkg").files == (kept,)


def test_global_excludes_hide_an_untracked_file_but_not_a_tracked_one(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path_factory.mktemp("home")
    (home / "ignore").write_text("*.py\n")
    (home / "config").write_text(f"[core]\nexcludesFile = {home / 'ignore'}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "config"))
    tracked = _write(repo, "tracked.py", "untracked.py")[0]
    _git(repo, "add", "-f", "tracked.py")

    assert discover_files(repo).files == (tracked,)


def test_listing_inherits_the_index_a_commit_hook_runs_under(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `git commit <paths>` hands its hooks a temporary index through `GIT_INDEX_FILE`.
    in_real_index, in_hook_index = _write(repo, "real.py", "hook.py")
    _git(repo, "add", "real.py")
    (repo / ".gitignore").write_text("*.py\n")
    monkeypatch.setenv("GIT_INDEX_FILE", str(repo.parent / f"{repo.name}-hook-index"))
    _git(repo, "add", "-f", "hook.py")

    assert discover_files(repo).files == (in_hook_index,)
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert discover_files(repo).files == (in_real_index,)


def test_tree_outside_a_repository_ignores_gitignore_without_running_git(
    tmp_path: Path, no_subprocess: None
) -> None:
    (tmp_path / ".gitignore").write_text("*.py\n")
    kept = _write(tmp_path, "a.py")[0]

    assert discover_files(tmp_path).files == (kept,)


def test_rejected_git_directory_stops_the_run_with_gits_message(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    _write(tmp_path, "a.py")

    error = _raises_git_error(tmp_path)

    assert "not a git repository" in error.message.lower()
    assert error.path == "."


def test_dangling_git_symlink_is_a_repository_marker(tmp_path: Path) -> None:
    (tmp_path / ".git").symlink_to(tmp_path / "missing")
    _write(tmp_path, "a.py")

    _raises_git_error(tmp_path)


def test_missing_git_binary_stops_the_run_inside_a_repository(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(repo, "a.py")
    monkeypatch.setenv("PATH", "")

    error = _raises_git_error(repo)

    assert "could not run git" in error.message


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("echo 'detected dubious ownership' >&2; exit 128", "detected dubious ownership"),
        ("exit 1", "exit status 1"),
        ("echo 'could not open directory' >&2; exit 0", "could not open directory"),
    ],
    ids=["refusal", "silent-failure", "warning-on-success"],
)
def test_git_failures_stop_the_run_and_carry_gits_message(
    repo: Path, monkeypatch: pytest.MonkeyPatch, script: str, expected: str
) -> None:
    _write(repo, "a.py")
    _fake_git(repo, monkeypatch, script)

    assert expected in _raises_git_error(repo).message


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory")
def test_unreadable_directory_stops_the_run_instead_of_losing_its_files(repo: Path) -> None:
    _write(repo, "a.py", "locked/b.py")
    (repo / "locked").chmod(0)
    try:
        assert "locked" in _raises_git_error(repo).message
    finally:
        (repo / "locked").chmod(stat.S_IRWXU)


def test_listing_does_not_run_the_scanned_trees_fsmonitor(repo: Path) -> None:
    marker = repo.parent / f"{repo.name}-fsmonitor-ran"
    hook = repo.parent / f"{repo.name}-fsmonitor"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    kept = _write(repo, "a.py")[0]
    _git(repo, "add", "a.py")
    _git(repo, "config", "core.fsmonitor", str(hook))

    # Guard the guard: a plain listing of this tree does run it.
    _git(repo, "ls-files")
    assert marker.exists()
    marker.unlink()

    assert discover_files(repo).files == (kept,)
    assert not marker.exists()


def test_named_untracked_gitignored_file_is_linted_without_running_git(
    repo: Path, no_subprocess: None
) -> None:
    (repo / ".gitignore").write_text("ignored.py\n")
    ignored = _write(repo, "ignored.py")[0]

    assert discover_files(repo, explicit=(ignored,)).files == (ignored,)


def test_gitignored_file_named_in_include_is_linted(repo: Path) -> None:
    (repo / ".gitignore").write_text("ignored.py\n")
    ignored = _write(repo, "ignored.py")[0]

    assert discover_files(repo, include=("ignored.py",)).files == (ignored,)


def test_named_file_keeps_builtin_and_configured_excludes(repo: Path) -> None:
    generated, venv = _write(repo, "generated/a.py", ".venv/b.py")

    result = discover_files(repo, explicit=(generated, venv), excludes=("generated/",))

    assert result.files == ()


@pytest.mark.parametrize("request_kind", ["explicit", "include", "root"])
def test_requested_directory_git_reports_ignored_gets_the_plain_walk(
    repo: Path, request_kind: str
) -> None:
    (repo / ".gitignore").write_text("build/\n")
    generated = _write(repo, "build/gen.py")[0]
    build = repo / "build"

    if request_kind == "explicit":
        result = discover_files(repo, explicit=(build,))
    elif request_kind == "include":
        result = discover_files(repo, include=("build",))
    else:
        result = discover_files(build)

    assert result.files == (generated,)
    assert result.errors == ()


def test_ignored_directory_holding_a_tracked_file_lists_only_tracked_files(repo: Path) -> None:
    tracked = _write(repo, "build/tracked.py", "build/untracked.py")[0]
    _git(repo, "add", "build/tracked.py")
    (repo / ".gitignore").write_text("build/\n")

    assert discover_files(repo, explicit=(repo / "build",)).files == (tracked,)


def test_empty_listing_of_an_unignored_directory_is_an_empty_scan(repo: Path) -> None:
    (repo / "empty").mkdir()

    assert discover_files(repo, explicit=(repo / "empty",)).files == ()


def test_excluded_directory_requested_directly_starts_no_git_process(
    repo: Path, no_subprocess: None
) -> None:
    (repo / "vendor").mkdir()

    result = discover_files(repo, explicit=(repo / "vendor",), excludes=("vendor",))

    assert result.files == ()


def test_requested_path_matches_excludes_at_its_resolved_location(tmp_path: Path) -> None:
    kept = _write(tmp_path, "tests/a.py")[0]
    (tmp_path / "src").mkdir()
    spelled = tmp_path / "src" / ".." / "tests"

    assert discover_files(tmp_path, explicit=(spelled,), excludes=("src/",)).files == (kept,)
    assert discover_files(tmp_path, explicit=(spelled,), excludes=("tests/",)).files == ()


def test_nested_clone_and_submodule_entries_are_dropped(repo: Path) -> None:
    kept = _write(repo, "kept.py")[0]
    _write(repo, "nested/inner.py", "vendor/inner.py")
    _git(repo / "nested", "init", "-q", ".")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{'a' * 40},vendor")

    result = discover_files(repo)

    assert result.files == (kept,)
    assert result.errors == ()


def test_nested_clone_named_directly_is_listed(repo: Path) -> None:
    inner = _write(repo, "nested/inner.py")[0]
    _git(repo / "nested", "init", "-q", ".")

    assert discover_files(repo, explicit=(repo / "nested",)).files == (inner,)


def test_no_gitignore_walks_a_repository_without_git_but_keeps_excludes(
    repo: Path, no_subprocess: None
) -> None:
    (repo / ".gitignore").write_text("ignored.py\n")
    (repo / ".git" / "info" / "exclude").write_text("info.py\n")
    expected = _write(repo, "ignored.py", "info.py")
    _write(repo, "excluded/a.py", ".venv/b.py")

    result = discover_files(repo, excludes=("excluded/",), use_gitignore=False)

    assert result.files == expected


def test_no_gitignore_gets_past_a_rejected_git_directory(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    kept = _write(tmp_path, "a.py")[0]

    assert discover_files(tmp_path, use_gitignore=False).files == (kept,)


def test_excluded_paths_produce_no_error_when_a_symlink_lives_inside_them(repo: Path) -> None:
    kept = _write(repo, "kept.py", ".venv/lib/a.py", "vendor/real/b.py")[0]
    (repo / ".venv" / "lib64").symlink_to("lib", target_is_directory=True)
    (repo / "vendor" / "link").symlink_to("real", target_is_directory=True)
    _git(repo, "add", "-f", ".")

    result = discover_files(repo, excludes=("vendor",))

    assert result.files == (kept,)
    assert result.errors == ()


def test_excluded_paths_never_touch_the_filesystem(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = _write(repo, "kept.py", "vendor/a.py", "vendor/deep/b.py")[0]
    lstat = os.lstat
    seen: list[str] = []

    def spy_lstat(path: str | os.PathLike[str], **kwargs: object) -> os.stat_result:
        seen.append(os.fspath(path))
        return lstat(path, **kwargs)

    # `Path.lstat` and `os.path.realpath` both reach the filesystem through these two.
    monkeypatch.setattr(os, "lstat", spy_lstat)
    monkeypatch.setattr(Path, "lstat", lambda self: spy_lstat(self))

    assert discover_files(repo, excludes=("vendor",)).files == (kept,)
    assert str(kept) in seen
    assert not any("vendor" in path for path in seen)


def test_tracked_file_deleted_from_disk_is_dropped(repo: Path) -> None:
    kept, deleted, _gone = _write(repo, "kept.py", "deleted.py", "gone/a.py")
    _git(repo, "add", ".")
    deleted.unlink()
    (repo / "gone" / "a.py").unlink()
    (repo / "gone").rmdir()
    (repo / "gone").write_text("now a file\n")

    result = discover_files(repo)

    assert result.files == (kept,)
    assert result.errors == ()


def test_tracked_file_behind_a_directory_replaced_by_a_symlink_stays_inside_root(
    repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # git still lists `real/a.py` from its index, and `git status` calls it deleted. Reading it
    # would read a file outside the root, and `real` being ignored means nothing else reports it.
    kept = _write(repo, "kept.py", "real/a.py")[0]
    _git(repo, "add", ".")
    (repo / "real" / "a.py").unlink()
    (repo / "real").rmdir()
    outside = tmp_path_factory.mktemp("outside")
    (outside / "a.py").write_text(PY_CONTENT)
    (repo / "real").symlink_to(outside, target_is_directory=True)
    (repo / ".gitignore").write_text("real\n")

    result = discover_files(repo)

    assert result.files == (kept,)
    assert result.errors == ()


def test_symlinks_behave_the_same_through_git_and_a_walk(repo: Path) -> None:
    kept, target = _write(repo, "src/kept.py", "target/t.py")
    (repo / "src" / "file_link.py").symlink_to(target)
    (repo / "src" / "dir_link").symlink_to(repo / "target", target_is_directory=True)
    (repo / ".venv").symlink_to(repo / "target", target_is_directory=True)
    (repo / "dangling.py").symlink_to(repo / "missing.py")

    listed = discover_files(repo)
    walked = discover_files(repo, use_gitignore=False)

    assert listed.files == walked.files == (kept, target)
    assert listed.errors == walked.errors
    assert [(error.path, error.message) for error in listed.errors] == [
        ("src/dir_link", "directory symlink is not traversed")
    ]


def test_directory_only_exclude_does_not_match_a_same_named_python_file(repo: Path) -> None:
    kept = _write(repo, "src/b.py")[0]

    assert discover_files(repo, excludes=("b.py/",)).files == (kept,)
    assert discover_files(repo, excludes=("b.py/",), use_gitignore=False).files == (kept,)


def test_a_duplicate_listing_of_an_unmerged_file_selects_it_once(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = _write(repo, "a.py")[0]
    monkeypatch.setattr(discovery, "git_listing", lambda _directory: ["a.py", "a.py"])

    assert discover_files(repo).files == (kept,)


@pytest.mark.parametrize("name", ["sub[1]", "!important", "café", "with space"])
def test_unusual_directory_names_are_literal_through_git(repo: Path, name: str) -> None:
    # `sub1` is what `sub[1]` would match if git read the name as a glob.
    kept = _write(repo, f"{name}/a.py", "sub1/b.py")[0]

    assert discover_files(repo, explicit=(repo / name,)).files == (kept,)


def test_file_limit_applies_to_a_listing(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second, _third = _write(repo, "src/a.py", "src/b.py", "src/c.py")
    monkeypatch.setattr(discovery, "MAX_DISCOVERED_FILES", 2)

    result = discover_files(repo)

    assert result.files == (first, second)
    assert [error.kind for error in result.errors] == ["budget"]
