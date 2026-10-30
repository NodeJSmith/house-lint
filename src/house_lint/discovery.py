"""Strict, deterministic Python file discovery."""

import os
import stat
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pathspec import GitIgnoreSpec

from house_lint.cache import CACHE_DIRNAME
from house_lint.config import (
    DEFAULT_INCLUDE,
    PYPROJECT_CONFIG_NAME,
    STANDALONE_CONFIG_NAMES,
    ConfigError,
    get_house_lint_table,
    get_standalone_table,
    load_toml,
)
from house_lint.results import LintError

BUILTIN_EXCLUDES = (
    ".bzr/",
    ".direnv/",
    ".eggs/",
    ".git/",
    ".git-rewrite/",
    ".hg/",
    # Not part of Ruff's default exclude list -- house-lint's own default cache directory
    # (`cache.CACHE_DIRNAME`). A plain walk would otherwise descend into `.house-lint-cache/`, which
    # git only skips because of the `.gitignore` house-lint writes there. Kept as a reference to
    # the constant, not a duplicated literal, so the two can never drift.
    f"{CACHE_DIRNAME}/",
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
GIT_MARKER_NAME = ".git"
MAX_DISCOVERED_FILES = 100_000

_BUILTIN_SPEC = GitIgnoreSpec.from_lines(BUILTIN_EXCLUDES)
_ConfigTableGetter = Callable[[dict[str, Any]], dict[str, Any] | None]
# Name/table-getter pairs for `_recognized_configs`, in discovery-precedence order. Module-level
# like `BUILTIN_EXCLUDES` above: the pairing never changes per call, so it's built once rather
# than reconstructed on every ancestor directory `_recognized_configs` checks.
_CONFIG_CANDIDATES: tuple[tuple[str, _ConfigTableGetter], ...] = (
    *((name, get_standalone_table) for name in STANDALONE_CONFIG_NAMES),
    (PYPROJECT_CONFIG_NAME, get_house_lint_table),
)
# `core.fsmonitor` names a command in the scanned tree's own `.git/config`, and `ls-files` would
# otherwise run it, so a tree extracted from an untrusted archive could choose what runs here.
_GIT_COMMAND = ("git", "-c", "core.fsmonitor=false")
_LS_FILES_ARGS = ("ls-files", "-z", "--cached", "--others", "--exclude-standard")
_CHECK_IGNORE_ARGS = ("check-ignore", "-q", ".")
_GIT_FAILURE_HINT = "pass --no-gitignore to scan without git"
# `LintError.kind`/`.operation` are typed `str` in `results.py` (the public, schema-versioned
# result type), so nothing there enforces this vocabulary -- these `Literal` aliases are this
# module's own closed set of the `kind`/`operation` values it actually passes to `_error`, so a
# typo (e.g. `"trversal"`) is a pyright error here rather than a silently-unvalidated string.
_ErrorKind = Literal["path", "traversal", "budget"]
_ErrorOperation = Literal["stat", "resolve", "walk", "containment", "qualify", "discover", "git"]


class DiscoveryError(ValueError):
    """A strict discovery failure with its public result representation."""

    def __init__(self, error: LintError) -> None:
        super().__init__(error.message)
        self.error = error


class GitFailure(Exception):
    """git could not answer for a directory that has a `.git` entry at or above it."""


@dataclass(frozen=True)
class DiscoveryResult:
    """Selected files, plus the resolved target discovery actually validated for each.

    `files` holds unresolved paths, because those are what the user named and what findings are
    reported against. `resolved_paths` maps each of them to the `resolve()` result that passed the
    containment check, so the scan can read *that* target rather than resolving the symlink a
    second time and possibly following it somewhere else — see `SourceFile.__init__`.
    """

    files: tuple[Path, ...]
    errors: tuple[LintError, ...] = ()
    resolved_paths: Mapping[Path, Path] = field(default_factory=lambda: dict[Path, Path]())


@dataclass(frozen=True)
class ProjectResolution:
    root: Path
    config: Path | None
    shadowed: tuple[Path, ...] = ()
    """Recognized config sources at the same directory as `config` that lost to it on
    discovery precedence (`house-lint.toml` > `.house-lint.toml` > `pyproject.toml`). Only
    ever non-empty when `config` is not `None`. Surfaced in default (non-debug) output — see
    `cli.py` and `reporters/text.py`'s `shadowed_config_note`."""


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def inside_repository(directory: Path) -> bool:
    """Whether a `.git` entry exists at or above `directory`.

    Decided from the filesystem because git exits 128 both for "not a repository" and for a
    `safe.directory` refusal. Asking git could not tell those apart, and a refusal mistaken for
    "not a repository" would silently scan gitignored files.
    """
    return any(
        os.path.lexists(candidate / GIT_MARKER_NAME)
        for candidate in (directory, *directory.parents)
    )


def run_git(
    directory: Path, args: tuple[str, ...], *, accepted: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[bytes]:
    """Run git in `directory`, treating anything but a clean answer as a failure.

    Non-empty stderr fails even on an accepted exit status, because `ls-files` warns and exits 0
    on an unreadable directory while leaving that directory's files out of its list.

    The environment is inherited on purpose: inside `git commit <paths>`, a hook's `GIT_INDEX_FILE`
    points at the index being committed.
    """
    try:
        completed = subprocess.run(
            (*_GIT_COMMAND, *args), cwd=directory, capture_output=True, check=False
        )
    except OSError as exc:
        raise GitFailure(f"could not run git: {exc}; {_GIT_FAILURE_HINT}") from exc
    detail = completed.stderr.decode(errors="replace").strip()
    if completed.returncode not in accepted or detail:
        reason = detail or f"exit status {completed.returncode}"
        raise GitFailure(f"git {args[0]} failed: {reason}; {_GIT_FAILURE_HINT}")
    return completed


def git_listing(directory: Path) -> list[str]:
    """Tracked and unignored untracked paths in `directory`, relative to it, in sorted order.

    `-z` is required: without it git quotes non-ASCII names. Working directory rather than a
    pathspec restricts the list, so a directory name holding glob characters stays literal.
    Sorted so the file limit keeps the same files however git orders its two sources.
    """
    output = run_git(directory, _LS_FILES_ARGS).stdout
    return sorted(os.fsdecode(entry) for entry in output.split(b"\0") if entry)


def git_ignores(directory: Path) -> bool:
    """Whether git reports `directory` itself as ignored. Exit 1 means "no", not a failure."""
    return run_git(directory, _CHECK_IGNORE_ARGS, accepted=(0, 1)).returncode == 0


@dataclass
class _FileSelector:
    root: Path
    exclude_spec: GitIgnoreSpec
    errors: list[LintError]
    use_gitignore: bool = True
    selected: dict[Path, Path] = field(default_factory=lambda: dict[Path, Path]())
    limit_reached: bool = False
    excluded_dirs: dict[str, bool] = field(default_factory=lambda: dict[str, bool]())
    behind_symlink_dirs: dict[str, bool] = field(default_factory=lambda: dict[str, bool]())

    def select(self, requested: tuple[Path, ...], *, explicit_paths: bool) -> None:
        seen_arguments: set[Path] = set()
        for path in requested:
            if self.limit_reached:
                break
            argument = path if path.is_absolute() else self.root / path
            if explicit_paths and argument in seen_arguments:
                continue
            seen_arguments.add(argument)
            self._consider_requested(argument, explicit_paths=explicit_paths)

    def result(self) -> DiscoveryResult:
        # `selected` is keyed by resolved path so a symlink and its target deduplicate; the scan
        # needs the reverse direction, from the path it reports to the target it may read.
        resolved_paths = {path: resolved for resolved, path in self.selected.items()}
        return DiscoveryResult(tuple(sorted(resolved_paths)), tuple(self.errors), resolved_paths)

    def _excluded(self, relative: str, *, is_dir: bool) -> bool:
        """Whether root-relative `relative`, or a directory above it, matches an exclude pattern.

        Every ancestor is checked in directory form, so a directory-only pattern matches what
        lies beneath the directory. `exclude` has no negation, so any match at any level is final.
        A directory's verdict is cached: every file in it shares that ancestor walk, and
        `match_file` runs each pattern's regex.
        """
        if is_dir and relative in self.excluded_dirs:
            return self.excluded_dirs[relative]
        parent = relative.rpartition("/")[0]
        probe = f"{relative}/" if is_dir else relative
        excluded = (bool(parent) and self._excluded(parent, is_dir=True)) or _matches_exclude(
            self.exclude_spec, probe
        )
        if is_dir:
            self.excluded_dirs[relative] = excluded
        return excluded

    def _requested_is_excluded(self, resolved: Path, *, is_dir: bool) -> bool:
        return resolved != self.root and self._excluded(
            resolved.relative_to(self.root).as_posix(), is_dir=is_dir
        )

    def _consider_requested(self, path: Path, *, explicit_paths: bool) -> None:
        """Evaluate a path someone asked for: named on the CLI, in `include`, or the root."""
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
            return
        # Excludes match the resolved location, not the spelling used to reach it, the rule
        # `per-file-ignores` documents (`docs/configuration.md`): `relative_to(root)` keeps a `..`
        # as a literal part, so `src/../tests` would otherwise match patterns naming `src`.
        if is_dir:
            if not self._requested_is_excluded(resolved, is_dir=True):
                self._scan_directory(resolved)
            return
        if not is_file or path.suffix != ".py":
            if explicit_paths:
                raise DiscoveryError(
                    self._error(
                        path, "path", "qualify", f"explicit path is not a Python file: {path}"
                    )
                )
            return
        if not self._requested_is_excluded(resolved, is_dir=False):
            self._select(resolved, path)

    def _scan_directory(self, directory: Path) -> None:
        """Find the files in a requested directory: through git in a repository, else a walk."""
        if not self.use_gitignore or not inside_repository(directory):
            self._walk(directory)
            return
        try:
            listed = git_listing(directory)
            # `ls-files` has nothing to say about an ignored directory, but someone asked for
            # this one. `check build/gen.py` lints the file, so `check build/` has to as well.
            walk_instead = not listed and git_ignores(directory)
        except GitFailure as exc:
            raise DiscoveryError(self._error(directory, "traversal", "git", str(exc))) from exc
        if walk_instead:
            self._walk(directory)
            return
        prefix = self._relative_prefix(directory)
        for name in listed:
            if self.limit_reached:
                return
            # git marks an untracked nested repository with a trailing slash.
            self._consider_discovered(prefix + name.removesuffix("/"))

    def _walk(self, directory: Path) -> None:
        def onerror(err: OSError) -> None:
            failed_path = Path(err.filename) if err.filename is not None else directory
            self.errors.append(self._error(failed_path, "traversal", "walk", str(err)))

        for current, dirs, names in os.walk(
            directory, topdown=True, followlinks=False, onerror=onerror
        ):
            if self.limit_reached:
                break
            prefix = self._relative_prefix(Path(current))
            dirs[:] = self._traversable_dirs(prefix, dirs)
            for name in sorted(names):
                self._consider_discovered(prefix + name)
                if self.limit_reached:
                    break

    def _relative_prefix(self, directory: Path) -> str:
        return "" if directory == self.root else f"{directory.relative_to(self.root).as_posix()}/"

    def _consider_discovered(self, relative: str) -> None:
        """Evaluate a root-relative path from a git listing or a walk.

        Excludes run first, on the string alone: an excluded path never touches the filesystem
        and never produces an error, however large the excluded tree git listed. A listing also
        holds entries for submodules and nested clones, which the regular-file check drops rather
        than recursing into, and tracked files already deleted from disk.

        Neither source follows a symlinked directory, so the only way a path leaves the root is a
        tracked file whose parent became a symlink after it was added. git reports that file as
        deleted, so it is dropped the same way.
        """
        if self._excluded(relative, is_dir=False):
            return
        path = self.root / relative
        if self._is_behind_symlink(relative.rpartition("/")[0]):
            return
        try:
            mode = path.lstat().st_mode
            is_symlink_to_dir = stat.S_ISLNK(mode) and path.is_dir()
        except (FileNotFoundError, NotADirectoryError):
            return
        except OSError as exc:
            self.errors.append(self._error(path, "traversal", "stat", str(exc)))
            return
        if is_symlink_to_dir:
            # git lists a symlink as a file, so a symlinked `.venv` only matches the directory-only
            # `.venv/` once this check has shown what it points at.
            if not self._excluded(relative, is_dir=True):
                self.errors.append(
                    self._error(path, "traversal", "walk", "directory symlink is not traversed")
                )
            return
        if not stat.S_ISREG(mode) or path.suffix != ".py":
            return
        self._select(path, path)

    def _is_behind_symlink(self, relative_dir: str) -> bool:
        """Whether any component of root-relative `relative_dir` is a symlink.

        `self.root` is already resolved, so `realpath` changes the path only if a symlink lies
        below it. Cached because every file in a directory shares the answer.
        """
        cached = self.behind_symlink_dirs.get(relative_dir)
        if cached is not None:
            return cached
        directory = self.root / relative_dir
        behind = os.path.realpath(directory) != str(directory)
        self.behind_symlink_dirs[relative_dir] = behind
        return behind

    def _select(self, resolved: Path, reported: Path) -> None:
        if resolved in self.selected:
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
        self.selected[resolved] = reported

    def _traversable_dirs(self, prefix: str, dirs: list[str]) -> list[str]:
        """Return child directory names a walk should descend into.

        Exclusion is checked before the symlink stat, not after: a symlinked directory that is
        also excluded (a symlinked `.venv/`) is dropped silently and never emits the "directory
        symlink is not traversed" error, which is reserved for a directory symlink this walk
        would otherwise have descended into.
        """
        kept: list[str] = []
        for item in sorted(dirs):
            relative = prefix + item
            if self._excluded(relative, is_dir=True):
                continue
            child = self.root / relative
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
            kept.append(item)
        return kept

    def _filesystem_error(
        self, path: Path, operation: _ErrorOperation, message: str, *, explicit_paths: bool
    ) -> None:
        err = self._error(path, "path" if explicit_paths else "traversal", operation, message)
        if explicit_paths:
            raise DiscoveryError(err)
        self.errors.append(err)

    def _error(
        self, path: Path, kind: _ErrorKind, operation: _ErrorOperation, message: str
    ) -> LintError:
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


def _matches_exclude(exclude_spec: GitIgnoreSpec, probe: str) -> bool:
    return _BUILTIN_SPEC.match_file(probe) or exclude_spec.match_file(probe)


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
    requested = explicit or tuple(root / item for item in include)
    selector = _FileSelector(
        root, GitIgnoreSpec.from_lines(excludes), [], use_gitignore=use_gitignore
    )
    selector.select(requested, explicit_paths=bool(explicit))
    return selector.result()


def _recognized_configs(directory: Path) -> tuple[Path, ...]:
    """Every config source at `directory` recognized as valid -- existing, parseable, and
    carrying the table its file type expects -- in discovery-precedence order: `house-lint.toml`,
    `.house-lint.toml` (both `[house-lint]`), then `pyproject.toml` (`[tool.house-lint]`).

    A recognized standalone file without the expected table (e.g. a `house-lint.toml`
    incidentally left over from another tool, with no `[house-lint]` table) is not included here
    -- it is silently skipped, falling through to the next name in precedence order, per FR#1.
    Shared by both `resolve_project` sites that need this three-file check: the upward walk and
    the `--root`-without-`--config`/fallback-marker path, so precedence can't drift between them.

    A candidate that fails to parse never blocks a *recognized* candidate elsewhere at the same
    directory -- a malformed loser is silently skipped just like a missing table would be. But if
    nothing at this directory is recognized and at least one candidate failed to parse, that parse
    failure is re-raised rather than swallowed: an unreadable config with no valid alternative is a
    configuration error, not "no config here" (pins the pre-existing malformed-ancestor-pyproject
    behavior).

    Callers that also need to know whether `directory / "pyproject.toml"` exists at all --
    regardless of its table -- for `found_marker` fallback purposes must stat it separately; this
    function only reports it when it is a recognized house-lint config source.
    """
    found: list[Path] = []
    parse_error: ConfigError | None = None
    for name, table_getter in _CONFIG_CANDIDATES:
        candidate = directory / name
        if not candidate.is_file():
            continue
        try:
            document = load_toml(candidate)
        except ConfigError as exc:
            parse_error = parse_error or exc
            continue
        if table_getter(document) is not None:
            found.append(candidate)
    if not found and parse_error is not None:
        raise parse_error
    return tuple(found)


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
            recognized = _recognized_configs(candidate)
            if recognized:
                return ProjectResolution(candidate, recognized[0], recognized[1:])
            if (candidate / PYPROJECT_CONFIG_NAME).is_file():
                found_marker = found_marker or candidate
            # Stop at the first repo boundary crossed, after this directory's own recognized-config
            # check above has already run -- otherwise an outer, unrelated ancestor's config could
            # silently apply to a nested repo that has none of its own.
            if (candidate / GIT_MARKER_NAME).exists():
                found_marker = found_marker or candidate
                break
        resolved_root = found_marker or start
    if config is not None:
        resolved_config = config.expanduser().resolve()
        if not _inside(resolved_root, resolved_config):
            raise ConfigError("explicit config must be inside root")
        if not resolved_config.is_file():
            raise ConfigError(f"config does not exist: {config}")
        return ProjectResolution(resolved_root, resolved_config)
    recognized = _recognized_configs(resolved_root)
    if recognized:
        return ProjectResolution(resolved_root, recognized[0], recognized[1:])
    return ProjectResolution(resolved_root, None)
