from collections.abc import Iterable
from pathlib import Path

import pytest

from house_lint import discovery
from house_lint.config import ConfigError
from house_lint.discovery import DiscoveryError, discover_files, resolve_project
from house_lint.results import LintError


def test_full_scan_applies_builtin_gitignore_configured_excludes_and_sorting(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("ignored/\n")
    for relative in ("src/z.py", "src/a.py", "ignored/x.py", ".venv/v.py", "notes.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src", "ignored", ".venv"), excludes=("src/z.py",))

    assert result.files == (tmp_path / "src/a.py",)
    assert result.errors == ()
    assert result.files_skipped == 3


def test_no_path_scan_uses_all_documented_default_include_roots(tmp_path: Path) -> None:
    expected: list[Path] = []
    for root_name in ("src", "tests", "scripts", "tools", "examples"):
        path = tmp_path / root_name / f"{root_name}.py"
        path.parent.mkdir(parents=True)
        path.write_text("x = 1\n")
        expected.append(path)
    (tmp_path / "outside.py").write_text("x = 1\n")

    result = discover_files(tmp_path)

    assert result.files == tuple(sorted(expected))
    assert result.files_skipped == 0


def test_explicit_paths_are_strict_and_directories_recursive(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    py_file = source / "a.py"
    py_file.write_text("x = 1\n")
    (source / "readme.md").write_text("text\n")

    result = discover_files(tmp_path, explicit=(source, py_file, py_file))

    assert result.files == (py_file,)
    assert result.files_skipped == 2

    with pytest.raises(ValueError, match="does not exist"):
        discover_files(tmp_path, explicit=(tmp_path / "missing.py",))
    with pytest.raises(ValueError, match="Python"):
        discover_files(tmp_path, explicit=(source / "readme.md",))


def test_gitignore_can_be_disabled_but_builtins_remain(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "ignored.py").write_text("x = 1\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "kept-out.py").write_text("x = 1\n")

    result = discover_files(tmp_path, include=("ignored.py", ".venv"), use_gitignore=False)

    assert result.files == (tmp_path / "ignored.py",)


def test_root_gitignore_cannot_negate_builtin_excludes(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("!.venv/\n")
    excluded = tmp_path / ".venv" / "kept-out.py"
    excluded.parent.mkdir()
    excluded.write_text("x = 1\n")

    result = discover_files(tmp_path, include=(".venv",))

    assert result.files == ()
    assert result.files_skipped == 1


def test_invalid_root_gitignore_pattern_reports_a_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src" / "kept.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("ignored/\n")
    from_lines = discovery.GitIgnoreSpec.from_lines

    def fail_gitignore(lines: Iterable[str]) -> discovery.GitIgnoreSpec:
        values = list(lines)
        if values == ["ignored/"]:
            raise ValueError("invalid pattern")
        return from_lines(values)

    monkeypatch.setattr(discovery.GitIgnoreSpec, "from_lines", fail_gitignore)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == ".gitignore"
    assert result.errors[0].operation == "parse"


def test_invalid_root_gitignore_keeps_configured_excludes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = tmp_path / "src" / "kept.py"
    skipped = tmp_path / "src" / "skip.py"
    kept.parent.mkdir(parents=True)
    kept.write_text("x = 1\n")
    skipped.write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("invalid/\n")
    from_lines = discovery.GitIgnoreSpec.from_lines

    def fail_gitignore(lines: Iterable[str]) -> discovery.GitIgnoreSpec:
        values = list(lines)
        if values == ["invalid/"]:
            raise ValueError("invalid pattern")
        return from_lines(values)

    monkeypatch.setattr(discovery.GitIgnoreSpec, "from_lines", fail_gitignore)

    result = discover_files(tmp_path, include=("src",), excludes=("src/skip.py",))

    assert result.files == (kept,)
    assert result.files_skipped == 1
    assert result.errors[0].path == ".gitignore"
    assert result.errors[0].operation == "parse"


def test_nested_gitignore_is_not_loaded(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("ignored.py\n")
    ignored = source / "ignored.py"
    ignored.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (ignored,)


def test_unreadable_root_gitignore_reports_an_error_and_keeps_reachable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src" / "kept.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n")
    ignore = tmp_path / ".gitignore"
    ignore.write_text("ignored/\n")
    read_text = Path.read_text

    def fail_read_text(self: Path, *, encoding: str) -> str:
        if self == ignore:
            raise OSError("permission denied")
        return read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == ".gitignore"
    assert result.errors[0].operation == "read"


def test_direct_symlink_file_is_safe_only_when_target_is_in_root(tmp_path: Path) -> None:
    inside = tmp_path / "inside.py"
    inside.write_text("x = 1\n")
    outside = tmp_path.parent / "outside-house-lint.py"
    outside.write_text("x = 1\n")
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


def test_walked_file_symlinks_are_not_selected(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    target = tmp_path / "target.py"
    target.write_text("x = 1\n")
    (source / "link.py").symlink_to(target)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == ()
    assert result.files_skipped == 1


def test_empty_full_scan_is_explicitly_clean(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("x = 1\n")

    result = discover_files(tmp_path, include=())

    assert result.files == ()
    assert result.files_skipped == 0
    assert result.errors == ()


def test_missing_implicit_include_root_is_an_empty_scan(tmp_path: Path) -> None:
    result = discover_files(tmp_path, include=("missing",))

    assert result.files == ()
    assert result.errors == ()


def test_nested_directory_symlink_reports_error_and_keeps_reachable_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "kept.py").write_text("x = 1\n")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "hidden.py").write_text("x = 1\n")
    (source / "linked").symlink_to(linked, target_is_directory=True)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source / "kept.py",)
    assert len(result.errors) == 1
    assert isinstance(result.errors[0], LintError)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == "src/linked"


def test_walker_error_reports_failed_directory_and_keeps_reachable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    kept = source / "kept.py"
    kept.write_text("x = 1\n")
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
        (source / name).write_text("x = 1\n")
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
