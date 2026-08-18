"""Strict, deterministic Python file discovery."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from pathspec import GitIgnoreSpec

from house_lint.config import DEFAULT_INCLUDE, ConfigError, get_house_lint_table, load_toml
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
        try:
            is_file = ignore.is_file()
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
                    "stat",
                    None,
                    str(exc),
                )
            )
            is_file = False
        if is_file:
            try:
                gitignore_spec = GitIgnoreSpec.from_lines(
                    ignore.read_text(encoding="utf-8").splitlines()
                )
            except (OSError, UnicodeDecodeError) as exc:
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
    return any(spec.match_file(relative) or spec.match_file(f"{relative}/") for spec in specs)


@dataclass
class _FileSelector:
    root: Path
    builtin_spec: GitIgnoreSpec
    exclude_spec: GitIgnoreSpec
    gitignore_spec: GitIgnoreSpec
    errors: list[LintError]
    selected: dict[Path, Path] = field(default_factory=lambda: dict[Path, Path]())
    files_skipped: int = 0
    limit_reached: bool = False

    def select(self, requested: tuple[Path, ...], *, explicit_paths: bool) -> None:
        seen_arguments: set[Path] = set()
        for path in requested:
            if self.limit_reached:
                break
            argument = path if path.is_absolute() else self.root / path
            if explicit_paths and argument in seen_arguments:
                continue
            seen_arguments.add(argument)
            self._consider(argument, explicit_paths=explicit_paths)

    def result(self) -> DiscoveryResult:
        return DiscoveryResult(
            tuple(sorted(self.selected.values())), self.files_skipped, tuple(self.errors)
        )

    def _consider(self, path: Path, *, explicit_paths: bool) -> None:
        if self.limit_reached:
            return
        try:
            exists = path.exists()
        except OSError as exc:
            self._filesystem_error(path, "stat", str(exc), explicit_paths=explicit_paths)
            return
        if not exists:
            if explicit_paths:
                raise DiscoveryError(
                    self._error(path, "path", "stat", f"path does not exist: {path}")
                )
            return
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError) as exc:
            self._filesystem_error(path, "resolve", str(exc), explicit_paths=explicit_paths)
            return
        if not _inside(self.root, resolved):
            if explicit_paths:
                raise DiscoveryError(
                    self._error(path, "path", "containment", f"path is outside root: {path}")
                )
            self.files_skipped += 1
            return
        try:
            is_symlink = path.is_symlink()
            is_dir = path.is_dir()
            is_file = path.is_file()
        except OSError as exc:
            self._filesystem_error(path, "stat", str(exc), explicit_paths=explicit_paths)
            return
        if is_symlink and is_dir:
            self.errors.append(
                self._error(path, "traversal", "walk", "directory symlink is not traversed")
            )
            return
        if is_symlink and not explicit_paths:
            self.files_skipped += 1
            return
        if is_dir:
            self._walk(path)
            return
        if not is_file or path.suffix != ".py":
            if explicit_paths:
                raise DiscoveryError(
                    self._error(path, "path", "qualify", f"explicit path is not a Python file: {path}")
                )
            self.files_skipped += 1
            return
        if _ignored(self.root, path, self.builtin_spec, self.exclude_spec, self.gitignore_spec):
            self.files_skipped += 1
            return
        if resolved in self.selected:
            self.files_skipped += 1
            return
        if len(self.selected) >= MAX_DISCOVERED_FILES:
            self.errors.append(
                self._error(
                    self.root,
                    "budget",
                    "discover",
                    f"discovery exceeds {MAX_DISCOVERED_FILES} files",
                )
            )
            self.limit_reached = True
            return
        self.selected[resolved] = path

    def _walk(self, directory: Path) -> None:
        def onerror(error: OSError) -> None:
            failed_path = Path(error.filename) if error.filename is not None else directory
            self.errors.append(self._error(failed_path, "traversal", "walk", str(error)))

        for current, dirs, names in os.walk(
            directory, topdown=True, followlinks=False, onerror=onerror
        ):
            if self.limit_reached:
                break
            current_path = Path(current)
            kept_dirs: list[str] = []
            for item in sorted(dirs):
                child = current_path / item
                try:
                    is_symlink = child.is_symlink()
                except OSError as exc:
                    self.errors.append(self._error(child, "traversal", "stat", str(exc)))
                    continue
                if is_symlink:
                    self.errors.append(
                        self._error(child, "traversal", "walk", "directory symlink is not traversed")
                    )
                    continue
                kept_dirs.append(item)
            dirs[:] = kept_dirs
            for name in sorted(names):
                self._consider(current_path / name, explicit_paths=False)
                if self.limit_reached:
                    break

    def _filesystem_error(
        self, path: Path, operation: str, message: str, *, explicit_paths: bool
    ) -> None:
        error = self._error(path, "path" if explicit_paths else "traversal", operation, message)
        if explicit_paths:
            raise DiscoveryError(error)
        self.errors.append(error)

    def _error(self, path: Path, kind: str, operation: str, message: str) -> LintError:
        try:
            relative = path.relative_to(self.root).as_posix()
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
    requested = explicit or tuple(root / item for item in include)
    selector = _FileSelector(
        root,
        builtin_spec,
        exclude_spec,
        gitignore_spec,
        list(pattern_errors),
    )
    selector.select(requested, explicit_paths=bool(explicit))
    return selector.result()


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
                data = load_toml(pyproject)
                if get_house_lint_table(data) is not None:
                    return ProjectResolution(candidate, pyproject)
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
        data = load_toml(candidate)
        if get_house_lint_table(data) is not None:
            return ProjectResolution(resolved_root, candidate)
    return ProjectResolution(resolved_root, None)
