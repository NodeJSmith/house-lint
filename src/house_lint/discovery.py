"""Strict, deterministic Python file discovery."""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pathspec import GitIgnoreSpec

from house_lint.config import DEFAULT_INCLUDE, ConfigError, get_house_lint_table, load_toml
from house_lint.results import LintError

BUILTIN_EXCLUDES = (".git/", ".venv/", ".nox/", "__pycache__/", "site-packages/", "node_modules/")
MAX_DISCOVERED_FILES = 100_000
_GITIGNORE_METACHARS = re.compile(r"([!#*?\[\]\\])")


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


def _gitignore_error(operation: str, message: str) -> LintError:
    return LintError(
        code="traversal-error",
        kind="traversal",
        path=".gitignore",
        line=None,
        column=None,
        end_line=None,
        end_column=None,
        phase="discovery",
        operation=operation,
        rule_id=None,
        message=message,
    )


def _load_gitignore_lines(path: Path, on_error: Callable[[str, str], None]) -> tuple[str, ...]:
    """Read and validate a `.gitignore` file's pattern lines, reporting stat/read/parse failures.

    Returns raw lines rather than a parsed spec: nested `.gitignore` files get their lines
    rewritten (see `_prefix_pattern`) and combined with their ancestors' before the final parse,
    so negation in a closer `.gitignore` can override a less-specific ignore the way git itself
    resolves precedence. Parsing here first — then discarding the result — exists purely for
    error attribution: without it, a bad line surviving into the merged multi-file list would
    only be blamed on the directory being combined, not on the specific `.gitignore` at fault.
    """
    try:
        is_file = path.is_file()
    except OSError as exc:
        on_error("stat", str(exc))
        return ()
    if not is_file:
        return ()
    try:
        lines = tuple(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError) as exc:
        on_error("read", str(exc))
        return ()
    try:
        GitIgnoreSpec.from_lines(lines)
    except (TypeError, ValueError, re.error) as exc:
        on_error("parse", str(exc))
        return ()
    return lines


def _escape_gitignore_literal(segment: str) -> str:
    """Escape characters gitwildmatch treats as pattern syntax within a literal path segment.

    `_prefix_pattern` embeds real directory names into a pattern string that gets re-parsed by
    `GitIgnoreSpec`. Without this, a directory literally named e.g. "sub[1]" or "!important"
    would have its `[`/`]`/`!` read back as wildcard or negation syntax instead of literal
    characters, silently changing which files the rewritten pattern matches.
    """
    return _GITIGNORE_METACHARS.sub(r"\\\1", segment)


def _prefix_pattern(prefix: str, line: str) -> str:
    """Rewrite a gitignore pattern owned by `prefix` into an equivalent root-anchored pattern.

    `prefix` is the pattern's owning directory, relative to root, posix-style, no trailing slash,
    with each path segment already escaped via `_escape_gitignore_literal` (e.g. "src/sub").
    Mirrors git's own per-directory pattern semantics: a pattern with no other slash matches at
    any depth under its directory (`_prefix_pattern("src", "foo.py") == "src/**/foo.py"`, which
    `GitIgnoreSpec` matches against both "src/foo.py" and "src/sub/foo.py" — "**" matches zero or
    more directories), one with an embedded (or leading) slash is anchored to that directory, and
    a leading "!" negates independent of anchoring.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return line
    negated = stripped.startswith("!")
    body = stripped[1:] if negated else stripped
    if body in ("", "/"):
        # A bare "/" (or an empty pattern after stripping "!") has no defined gitignore meaning;
        # treat it as inert rather than accidentally suppressing the whole owning directory.
        return line
    has_trailing_slash = body.endswith("/") and body != "/"
    core = body[:-1] if has_trailing_slash else body
    if core.startswith("/"):
        anchored_core = f"{prefix}/{core[1:]}"
    elif "/" in core:
        anchored_core = f"{prefix}/{core}"
    else:
        anchored_core = f"{prefix}/**/{core}"
    anchored = anchored_core + ("/" if has_trailing_slash else "")
    return f"!{anchored}" if negated else anchored


def _patterns(
    root: Path, excludes: tuple[str, ...], use_gitignore: bool
) -> tuple[GitIgnoreSpec, GitIgnoreSpec, tuple[str, ...], tuple[LintError, ...]]:
    errors: list[LintError] = []
    root_gitignore_lines: tuple[str, ...] = ()
    if use_gitignore:

        def on_error(operation: str, message: str) -> None:
            errors.append(_gitignore_error(operation, message))

        root_gitignore_lines = _load_gitignore_lines(root / ".gitignore", on_error)
    return (
        GitIgnoreSpec.from_lines(BUILTIN_EXCLUDES),
        GitIgnoreSpec.from_lines(excludes),
        root_gitignore_lines,
        tuple(errors),
    )


def _ignored(root: Path, path: Path, *specs: GitIgnoreSpec) -> bool:
    """Match `path`, relative to `root`, against each spec.

    All specs here are root-anchored, including the combined gitignore-hierarchy spec built by
    `_FileSelector._combined_gitignore_spec` — nested `.gitignore` patterns are rewritten to be
    root-anchored before that spec is built, so no directory-relative matching is needed here.
    """
    relative = path.relative_to(root).as_posix()
    return any(spec.match_file(relative) or spec.match_file(f"{relative}/") for spec in specs)


@dataclass
class _FileSelector:
    root: Path
    builtin_spec: GitIgnoreSpec
    exclude_spec: GitIgnoreSpec
    root_gitignore_lines: tuple[str, ...]
    errors: list[LintError]
    use_gitignore: bool = True
    selected: dict[Path, Path] = field(default_factory=lambda: dict[Path, Path]())
    files_skipped: int = 0
    limit_reached: bool = False
    own_gitignore_lines_cache: dict[Path, tuple[str, ...]] = field(
        default_factory=lambda: dict[Path, tuple[str, ...]]()
    )
    combined_gitignore_spec_cache: dict[Path, GitIgnoreSpec] = field(
        default_factory=lambda: dict[Path, GitIgnoreSpec]()
    )
    spec_by_lines_cache: dict[tuple[str, ...], GitIgnoreSpec] = field(
        default_factory=lambda: dict[tuple[str, ...], GitIgnoreSpec]()
    )

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

    def _consider(
        self,
        path: Path,
        *,
        explicit_paths: bool,
        combined_gitignore_spec: GitIgnoreSpec | None = None,
    ) -> None:
        """Evaluate `path` for selection.

        `combined_gitignore_spec` is precomputed once per directory by `_walk` and passed in for
        every file it discovers there, avoiding a redundant per-file rebuild. Callers outside a
        walk (`select`'s top-level `include`/`explicit` entries) leave it `None` and it's built
        lazily below, once, for that single path.
        """
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
                    self._error(
                        path, "path", "qualify", f"explicit path is not a Python file: {path}"
                    )
                )
            self.files_skipped += 1
            return
        if _ignored(
            self.root,
            path,
            self.builtin_spec,
            self.exclude_spec,
            combined_gitignore_spec
            if combined_gitignore_spec is not None
            else self._combined_gitignore_spec(path.parent),
        ):
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
        def onerror(err: OSError) -> None:
            failed_path = Path(err.filename) if err.filename is not None else directory
            self.errors.append(self._error(failed_path, "traversal", "walk", str(err)))

        for current, dirs, names in os.walk(
            directory, topdown=True, followlinks=False, onerror=onerror
        ):
            if self.limit_reached:
                break
            current_path = Path(current)
            combined_spec = self._combined_gitignore_spec(current_path)
            dirs[:] = self._traversable_dirs(current_path, dirs, combined_spec)
            for name in sorted(names):
                self._consider(
                    current_path / name,
                    explicit_paths=False,
                    combined_gitignore_spec=combined_spec,
                )
                if self.limit_reached:
                    break

    def _combined_gitignore_spec(self, directory: Path) -> GitIgnoreSpec:
        """Root-anchored spec combining the root `.gitignore` with every nested `.gitignore`
        between root and `directory`, ordered root-to-leaf (least to most specific) so
        `GitIgnoreSpec`'s own last-matching-line-wins semantics reproduce git's
        closest-directory-and-latest-line-wins precedence — including cross-level negation.

        Rebuilds the ancestor chain on every call rather than maintaining a push/pop stack synced
        to `os.walk`'s traversal order: `os.walk` backtracks between sibling subtrees with no
        explicit "leaving a directory" signal, so a manual stack would need the same
        relative-path bookkeeping this does anyway. `_own_gitignore_lines` memoizes each
        directory's own `.gitignore` read, and the combined spec itself is cached per directory,
        so the rebuild only repeats cheap dict lookups, not I/O or reparsing. A sibling
        directory with no `.gitignore` of its own accumulates the exact same line tuple as its
        parent, so `spec_by_lines_cache` is keyed on the accumulated lines themselves (not the
        directory) to skip `GitIgnoreSpec.from_lines` entirely on that repeat, while
        `combined_gitignore_spec_cache` still keeps the per-directory lookup itself O(1).
        """
        if directory in self.combined_gitignore_spec_cache:
            return self.combined_gitignore_spec_cache[directory]
        if not self.use_gitignore:
            # Short-circuit before any nested `.gitignore` is even read, not just before the
            # result is used — `--no-gitignore` should skip that filesystem I/O entirely.
            spec = GitIgnoreSpec.from_lines(())
            self.combined_gitignore_spec_cache[directory] = spec
            return spec
        lines: list[str] = list(self.root_gitignore_lines)
        try:
            relative_parts = directory.relative_to(self.root).parts
        except ValueError:
            relative_parts = ()
        current = self.root
        for part in relative_parts:
            current = current / part
            prefix = "/".join(
                _escape_gitignore_literal(segment)
                for segment in current.relative_to(self.root).parts
            )
            lines.extend(
                _prefix_pattern(prefix, line) for line in self._own_gitignore_lines(current)
            )
        lines_key = tuple(lines)
        cached_spec = self.spec_by_lines_cache.get(lines_key)
        if cached_spec is not None:
            spec = cached_spec
        else:
            try:
                spec = GitIgnoreSpec.from_lines(lines)
            except (TypeError, ValueError, re.error) as exc:
                # Each source's own lines are already validated in `_load_gitignore_lines`, but
                # `_prefix_pattern`'s rewrite of them is not independently re-validated — a valid
                # original line could in principle become invalid once prefixed, so this stays
                # live. The error is intentionally *not* cached by line content below: it must
                # still be reported once per offending directory, not just once per line tuple.
                self.errors.append(self._error(directory, "traversal", "combine", str(exc)))
                spec = GitIgnoreSpec.from_lines(())
            else:
                self.spec_by_lines_cache[lines_key] = spec
        self.combined_gitignore_spec_cache[directory] = spec
        return spec

    def _own_gitignore_lines(self, directory: Path) -> tuple[str, ...]:
        if directory in self.own_gitignore_lines_cache:
            return self.own_gitignore_lines_cache[directory]
        ignore = directory / ".gitignore"

        def on_error(operation: str, message: str) -> None:
            self.errors.append(self._error(ignore, "traversal", operation, message))

        lines = _load_gitignore_lines(ignore, on_error)
        self.own_gitignore_lines_cache[directory] = lines
        return lines

    def _traversable_dirs(
        self, current_path: Path, dirs: list[str], combined_gitignore_spec: GitIgnoreSpec
    ) -> list[str]:
        """Return child directory names to descend into, recording why each was dropped.

        Drops symlinked directories (never traversed) and directories already excluded by
        `combined_gitignore_spec` — the spec accumulated from root down to `current_path`
        (the parent), deliberately *not* including the child's own, not-yet-read
        `.gitignore`. Checking a child against its own nested `.gitignore` before deciding
        whether to descend into it would let a negation inside that file "resurrect" files
        that should stay excluded because the directory itself is ignored — real git never
        reads ignore files inside a directory it never descends into. Skipping the
        directory here means `_own_gitignore_lines`/`_combined_gitignore_spec` are simply
        never called for it, so its nested `.gitignore` (if any) is never read at all.
        `combined_gitignore_spec` already folds in `use_gitignore` (it's an empty spec when
        disabled), and `builtin_spec`/`exclude_spec` inside `_ignored` apply unconditionally,
        matching how file-level ignoring already treats those two specs.
        """
        kept: list[str] = []
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
            if _ignored(
                self.root, child, self.builtin_spec, self.exclude_spec, combined_gitignore_spec
            ):
                self.files_skipped += 1
                continue
            kept.append(item)
        return kept

    def _filesystem_error(
        self, path: Path, operation: str, message: str, *, explicit_paths: bool
    ) -> None:
        err = self._error(path, "path" if explicit_paths else "traversal", operation, message)
        if explicit_paths:
            raise DiscoveryError(err)
        self.errors.append(err)

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
    builtin_spec, exclude_spec, root_gitignore_lines, pattern_errors = _patterns(
        root, excludes, use_gitignore
    )
    requested = explicit or tuple(root / item for item in include)
    selector = _FileSelector(
        root,
        builtin_spec,
        exclude_spec,
        root_gitignore_lines,
        list(pattern_errors),
        use_gitignore=use_gitignore,
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
                document = load_toml(pyproject)
                if get_house_lint_table(document) is not None:
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
        document = load_toml(candidate)
        if get_house_lint_table(document) is not None:
            return ProjectResolution(resolved_root, candidate)
    return ProjectResolution(resolved_root, None)
