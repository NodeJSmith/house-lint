"""Strict, deterministic Python file discovery."""

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pathspec import GitIgnoreSpec

from house_lint.config import DEFAULT_INCLUDE, ConfigError, get_house_lint_table
from house_lint.results import LintError

BUILTIN_EXCLUDES = (".git/", ".venv/", ".nox/", "__pycache__/", "site-packages/", "node_modules/")
MAX_DISCOVERED_FILES = 100_000


class DiscoveryError(ValueError):
    """A strict discovery failure with its public result representation."""

    def __init__(self, error: LintError) -> None:
        super().__init__(error.message)
        self.error = error


@dataclass(frozen=True)
class DiscoveryResult:
    files: tuple[Path, ...]
    files_skipped: int = 0
    errors: tuple[LintError, ...] = ()


@dataclass(frozen=True)
class ProjectResolution:
    root: Path
    config: Path | None


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _patterns(
    root: Path, excludes: tuple[str, ...], use_gitignore: bool
) -> tuple[GitIgnoreSpec, GitIgnoreSpec, GitIgnoreSpec, tuple[LintError, ...]]:
    exclude_spec = GitIgnoreSpec.from_lines(excludes)
    gitignore_spec = GitIgnoreSpec.from_lines(())
    errors: list[LintError] = []
    if use_gitignore:
        ignore = root / ".gitignore"
        if ignore.is_file():
            try:
                gitignore_spec = GitIgnoreSpec.from_lines(
                    ignore.read_text(encoding="utf-8").splitlines()
                )
            except OSError as exc:
                errors.append(
                    LintError(
                        "traversal-error",
                        "traversal",
                        ".gitignore",
                        None,
                        None,
                        None,
                        None,
                        "discovery",
                        "read",
                        None,
                        str(exc),
                    )
                )
            except (TypeError, ValueError, re.error) as exc:
                errors.append(
                    LintError(
                        "traversal-error",
                        "traversal",
                        ".gitignore",
                        None,
                        None,
                        None,
                        None,
                        "discovery",
                        "parse",
                        None,
                        str(exc),
                    )
                )
    return (
        GitIgnoreSpec.from_lines(BUILTIN_EXCLUDES),
        exclude_spec,
        gitignore_spec,
        tuple(errors),
    )


def _ignored(root: Path, path: Path, *specs: GitIgnoreSpec) -> bool:
    relative = path.relative_to(root).as_posix()
    return any(
        spec.match_file(relative) or spec.match_file(f"{relative}/") for spec in specs
    )


def discover_files(
    root: Path,
    *,
    include: tuple[str, ...] = DEFAULT_INCLUDE,
    explicit: tuple[Path, ...] = (),
    excludes: tuple[str, ...] = (),
    use_gitignore: bool = True,
) -> DiscoveryResult:
    """Discover qualifying files, or raise for strict explicit path failures."""
    root = root.expanduser().resolve()
    builtin_spec, exclude_spec, gitignore_spec, pattern_errors = _patterns(
        root, excludes, use_gitignore
    )
    selected: dict[Path, Path] = {}
    skipped = 0
    errors = list(pattern_errors)
    limit_reached = False

    def make_error(path: Path, kind: str, operation: str, message: str) -> LintError:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = None
        return LintError(
            code=f"{kind}-error",
            kind=kind,
            path=relative,
            line=None,
            column=None,
            end_line=None,
            end_column=None,
            phase="discovery",
            operation=operation,
            rule_id=None,
            message=message,
        )

    requested = explicit or tuple(root / item for item in include)

    def consider(path: Path, *, strict: bool) -> None:
        nonlocal limit_reached, skipped
        if limit_reached:
            return
        lexical = path
        if not lexical.exists():
            if strict:
                error = make_error(path, "path", "stat", f"path does not exist: {path}")
                raise DiscoveryError(error)
            return
        resolved = lexical.resolve()
        if not _inside(root, resolved):
            if strict:
                error = make_error(path, "path", "containment", f"path is outside root: {path}")
                raise DiscoveryError(error)
            skipped += 1
            return
        if lexical.is_symlink() and lexical.is_dir():
            errors.append(
                make_error(lexical, "traversal", "walk", "directory symlink is not traversed")
            )
            return
        if lexical.is_symlink() and not strict:
            skipped += 1
            return
        if lexical.is_dir():
            walk_directory(lexical)
            return
        if not lexical.is_file() or lexical.suffix != ".py":
            if strict:
                error = make_error(
                    path, "path", "qualify", f"explicit path is not a Python file: {path}"
                )
                raise DiscoveryError(error)
            skipped += 1
            return
        if _ignored(root, lexical, builtin_spec, exclude_spec, gitignore_spec):
            skipped += 1
            return
        if resolved in selected:
            skipped += 1
            return
        if len(selected) >= MAX_DISCOVERED_FILES:
            errors.append(
                make_error(
                    root,
                    "budget",
                    "discover",
                    f"discovery exceeds {MAX_DISCOVERED_FILES} files",
                )
            )
            limit_reached = True
            return
        selected[resolved] = lexical

    def walk_directory(directory: Path) -> None:
        def onerror(error: OSError) -> None:
            failed_path = Path(error.filename) if error.filename is not None else directory
            errors.append(make_error(failed_path, "traversal", "walk", str(error)))

        for current, dirs, names in os.walk(
            directory, topdown=True, followlinks=False, onerror=onerror
        ):
            if limit_reached:
                break
            current_path = Path(current)
            kept_dirs: list[str] = []
            for item in sorted(dirs):
                child = current_path / item
                if child.is_symlink():
                    errors.append(
                        make_error(child, "traversal", "walk", "directory symlink is not traversed")
                    )
                    continue
                kept_dirs.append(item)
            dirs[:] = kept_dirs
            for name in sorted(names):
                consider(current_path / name, strict=False)
                if limit_reached:
                    break

    seen_arguments: set[Path] = set()
    for path in requested:
        if limit_reached:
            break
        argument = path if path.is_absolute() else root / path
        if explicit and argument in seen_arguments:
            continue
        seen_arguments.add(argument)
        consider(path if path.is_absolute() else root / path, strict=bool(explicit))
    return DiscoveryResult(tuple(sorted(selected.values())), skipped, tuple(errors))


def resolve_project(
    *, root: Path | None = None, config: Path | None = None, cwd: Path | None = None
) -> ProjectResolution:
    """Resolve project boundary and applicable config according to the CLI contract."""
    if root is not None:
        resolved_root = root.expanduser().resolve()
        if not resolved_root.is_dir():
            raise ConfigError(f"root is not a directory: {root}")
    elif config is not None:
        resolved_root = config.expanduser().resolve().parent
    else:
        start = (cwd or Path.cwd()).expanduser().resolve()
        found_marker: Path | None = None
        for candidate in (start, *start.parents):
            pyproject = candidate / "pyproject.toml"
            if pyproject.is_file():
                try:
                    with pyproject.open("rb") as stream:
                        data: dict[str, Any] = tomllib.load(stream)
                    if get_house_lint_table(data) is not None:
                        return ProjectResolution(candidate, pyproject)
                except (OSError, tomllib.TOMLDecodeError) as exc:
                    raise ConfigError(f"invalid project configuration: {exc}") from exc
                found_marker = found_marker or candidate
            if (candidate / ".git").exists():
                found_marker = found_marker or candidate
        resolved_root = found_marker or start
    if config is not None:
        resolved_config = config.expanduser().resolve()
        if not _inside(resolved_root, resolved_config):
            raise ConfigError("explicit config must be inside root")
        if not resolved_config.is_file():
            raise ConfigError(f"config does not exist: {config}")
        return ProjectResolution(resolved_root, resolved_config)
    candidate = resolved_root / "pyproject.toml"
    if candidate.is_file():
        try:
            with candidate.open("rb") as stream:
                data = tomllib.load(stream)
            if get_house_lint_table(data) is not None:
                return ProjectResolution(resolved_root, candidate)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"invalid project configuration: {exc}") from exc
    return ProjectResolution(resolved_root, None)
