"""Strict, deterministic Python file discovery."""

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pathspec import GitIgnoreSpec

from house_lint.config import DEFAULT_INCLUDE, ConfigError, get_house_lint_table, load_toml
from house_lint.results import LintError

BUILTIN_EXCLUDES = (".git/", ".venv/", ".nox/", "__pycache__/", "site-packages/", "node_modules/")
# Stands in for "an ancestor of this directory is excluded", where nothing beneath it can be
# re-included — see `_FileSelector._combined_gitignore_spec`.
IGNORE_EVERYTHING = ("**",)
MAX_DISCOVERED_FILES = 100_000
_GITIGNORE_METACHARS = re.compile(r"([!#*?\[\]\\])")
_CONTENTS_GLOB = re.compile(r"(?<!\*\*)/\*\*(/?)\Z")
# Two or more whole `**` segments in a row, which git reads as a single `**`.
_DOUBLE_STAR_RUN = re.compile(r"\*\*(?:/\*\*)+")


class DiscoveryError(ValueError):
    """A strict discovery failure with its public result representation."""

    def __init__(self, error: LintError) -> None:
        super().__init__(error.message)
        self.error = error


@dataclass(frozen=True)
class DiscoveryResult:
    """Selected files, plus the resolved target discovery actually validated for each.

    `files` holds unresolved paths, because those are what the user named and what findings are
    reported against. `resolved_paths` maps each of them to the `resolve()` result that passed the
    containment check, so the scan can read *that* target rather than resolving the symlink a
    second time and possibly following it somewhere else — see `SourceFile.__init__`.
    """

    files: tuple[Path, ...]
    files_skipped: int = 0
    errors: tuple[LintError, ...] = ()
    resolved_paths: Mapping[Path, Path] = field(default_factory=lambda: dict[Path, Path]())


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
        # Checked before `is_file()`, which follows symlinks: git does not read a symlinked
        # `.gitignore` at all, so following one would apply patterns git never applies. With
        # `src/.gitignore -> patterns` containing `*.py`, discovery would skip `src/a.py` while
        # `git check-ignore` still reports it as included.
        if path.is_symlink():
            return ()
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


def _strip_unescaped_trailing_whitespace(text: str) -> str:
    """Trim trailing spaces/tabs, except one quoted by a backslash escape.

    Mirrors gitwildmatch's own trailing-whitespace rule ("trailing spaces are ignored unless
    they are quoted with backslash") so a nested pattern's rewrite doesn't discard whitespace
    that `GitIgnoreSpec` would otherwise treat as significant.

    What decides the question is the *parity* of the backslash run before the whitespace, not
    whether a single backslash sits there: backslashes quote each other pairwise, so an even
    run leaves the space unquoted and git strips it. `a\\\\ ` (two backslashes, one space) is
    the case that separates the two readings — git reduces it to `a\\\\`, which names `a\\`,
    while treating the lone preceding backslash as an escape keeps the space and names `a\\ `
    instead. Checked against real `git check-ignore`; see the parity suite.
    """
    end = len(text)
    while end > 0 and text[end - 1] in " \t":
        backslashes = 0
        index = end - 2
        while index >= 0 and text[index] == "\\":
            backslashes += 1
            index -= 1
        if backslashes % 2 == 1:
            break
        end -= 1
    return text[:end]


def _collapse_double_star_run(core: str) -> str:
    """Reduce every run of consecutive `**` segments in `core` to a single `**`.

    git reads a run of `**` segments as one: `**/**/` ignores exactly what `**/` ignores, and
    `**/**/b.py` matches exactly what `**/b.py` matches (checked against real `git check-ignore`;
    see the collapse family in the parity suite). Collapsing here means the branches below only
    ever see the canonical one-segment spelling, so the `core == "**"` case covers the whole
    family rather than the single spelling someone happened to write down.

    Without this, a repeated form reached the generic slash-containing branch instead — `**/**/`
    became `<prefix>/**/**/`, which `GitIgnoreSpec` matches against an immediate regular file
    (`<prefix>/a.py`) that git leaves alone, silently hiding it from the linter.
    `_normalize_contents_glob` cannot repair that downstream: it deliberately skips a `/**`
    preceded by another `*`.
    """
    return _DOUBLE_STAR_RUN.sub("**", core)


def _normalize_contents_glob(pattern: str) -> str:
    """Rewrite a trailing `/**` so it cannot match the directory whose contents it names.

    git reads `build/**` as "everything inside build" and never matches `build` itself;
    `GitIgnoreSpec` matches the directory too. The difference is invisible until a later
    negation re-includes something underneath, because house-lint prunes `build/` at walk time
    and then never consults the negation inside it — git, by contrast, still descends. Spelling
    the pattern `build/**/*` means the same thing to both, since `**` matches zero or more
    directories but the trailing `*` still demands a path component.

    Runs on every pattern that reaches a spec (see `_spec_for_lines` and `_patterns`), so it
    also sees `_prefix_pattern`'s output. The lookbehind matches `**` specifically rather than a
    single `*`, so it skips only `a/**/**` — where rewriting has no evidence behind it — while
    still rewriting `a/*/**`, an ordinary pattern whose preceding segment just happens to end in
    a star. `_prefix_pattern` handles a bare `**` line itself rather than emitting
    `<prefix>/**/**` and relying on this to clean it up: the two `**` fixes are deliberately
    split that way, and neither subsumes the other.
    """
    if not pattern or pattern.startswith("#"):
        return pattern
    return _CONTENTS_GLOB.sub(r"/**/*\1", pattern)


def _prefix_pattern(prefix: str, line: str) -> str:
    """Rewrite a gitignore pattern owned by `prefix` into an equivalent root-anchored pattern.

    `prefix` is the pattern's owning directory, relative to root, posix-style, no trailing slash,
    with each path segment already escaped via `_escape_gitignore_literal` (e.g. "src/sub").
    Mirrors git's own per-directory pattern semantics: a pattern with no other slash matches at
    any depth under its directory (`_prefix_pattern("src", "foo.py") == "src/**/foo.py"`, which
    `GitIgnoreSpec` matches against both "src/foo.py" and "src/sub/foo.py" — "**" matches zero or
    more directories), one with an embedded (or leading) slash is anchored to that directory, and
    a leading "!" negates independent of anchoring.

    Only *unescaped trailing* whitespace is insignificant per gitwildmatch — a leading space is
    part of the pattern (matches a filename that itself starts with a space), and "#"/"!" only
    carry their special meaning as the pattern's literal first character. Blindly stripping the
    whole line (as an earlier version of this function did) silently dropped a leading space from
    the matched filename and could misidentify a leading-whitespace-prefixed "#"/"!" as
    comment/negation syntax that real gitignore parsing (verified against `GitIgnoreSpec` directly)
    does not treat as such — so only `.strip()`'s result is used to test for an all-whitespace
    (blank) line; the pattern body itself is built from the unstripped `line`.
    """
    if not line.strip():
        return line
    if line.startswith("#"):
        return line
    negated = line.startswith("!")
    body = _strip_unescaped_trailing_whitespace(line[1:] if negated else line)
    if body in ("", "/"):
        # A bare "/" (or an empty pattern after stripping "!") has no defined gitignore meaning;
        # treat it as inert rather than accidentally suppressing the whole owning directory.
        return line
    has_trailing_slash = body.endswith("/") and body != "/"
    core = _collapse_double_star_run(body[:-1] if has_trailing_slash else body)
    if core.startswith("/"):
        anchored_core = f"{prefix}/{core[1:]}"
    elif "/" in core:
        anchored_core = f"{prefix}/{core}"
    elif core == "**":
        # `**` is the one no-slash pattern the general expansion below gets wrong: it would
        # produce `<prefix>/**/**`, which `GitIgnoreSpec` matches against `<prefix>` itself and
        # — in the directory-only `**/` form — against an immediate regular file
        # (`<prefix>/a.py`) that git leaves alone. Naming an explicit segment (`*`) after the
        # `**` keeps "at any depth" while still requiring a path component to be there.
        # `_normalize_contents_glob` cannot repair this downstream: it deliberately skips a
        # `/**` preceded by another `*`, so the bad form has to not be produced here.
        anchored_core = f"{prefix}/**/*"
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
        GitIgnoreSpec.from_lines(_normalize_contents_glob(value) for value in excludes),
        root_gitignore_lines,
        tuple(errors),
    )


def _ignored(root: Path, path: Path, *specs: GitIgnoreSpec, is_dir: bool) -> bool:
    """Match `path`, relative to `root`, against each spec.

    All specs here are root-anchored, including the combined gitignore-hierarchy spec built by
    `_FileSelector._combined_gitignore_spec` — nested `.gitignore` patterns are rewritten to be
    root-anchored before that spec is built, so no directory-relative matching is needed here.

    `is_dir` selects which single form the path is matched in: git classifies a path once, as
    either a file or a directory, and then applies last-matching-line-wins within that one
    classification. Probing both forms and OR-ing them (as an earlier version did) breaks that:
    an ignore matching the directory form survives a negation that only matches the file form,
    so `["cache", "!cache/"]` wrongly excluded `cache/`, and a directory-only pattern like
    `b.py/` wrongly matched the regular file `b.py`. Callers always already know which kind of
    path they hold, so this is a parameter rather than another `stat` call.
    """
    relative = path.relative_to(root).as_posix()
    probe = f"{relative}/" if is_dir else relative
    return any(spec.match_file(probe) for spec in specs)


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
    excluded_ancestor_cache: dict[Path, bool] = field(default_factory=lambda: dict[Path, bool]())
    reported_spec_failures: set[tuple[tuple[str, ...], Path]] = field(
        default_factory=lambda: set[tuple[tuple[str, ...], Path]]()
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
        # `selected` is keyed by resolved path so a symlink and its target deduplicate; the scan
        # needs the reverse direction, from the path it reports to the target it may read.
        resolved_paths = {path: resolved for resolved, path in self.selected.items()}
        return DiscoveryResult(
            tuple(sorted(resolved_paths)), self.files_skipped, tuple(self.errors), resolved_paths
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
            # Walked in resolved form. Both ancestor walks key off `relative_to(root)`, which
            # keeps a `..` as a literal part, so `src/../tests` would enumerate `src` as an
            # ancestor of `tests` and apply its `.gitignore` — skipping files that `check tests`
            # selects. The resolved directory is the one whose ignore-file ancestors actually
            # govern it, and it is already the containment-checked path. This is the rule
            # `per-file-ignores` documents (`docs/configuration.md`): match the resolved
            # location, not the spelling used to reach it.
            #
            # A discovery root reached from `include` or an explicit argument is the one
            # directory `_traversable_dirs` never sees, because `_walk` starts *inside* it. Left
            # unchecked, an ignored root's files were only excluded when the patterns happened
            # to match the files as well — so `["src/", "!*.py"]` re-included every Python file
            # under an ignored `src/`, which git never does. Matched against the spec for the
            # parent, deliberately excluding this directory's own `.gitignore`, for the same
            # reason `_traversable_dirs` does.
            if resolved != self.root and (
                self._has_excluded_ancestor(resolved.parent)
                or _ignored(
                    self.root,
                    resolved,
                    self.builtin_spec,
                    self.exclude_spec,
                    self._combined_gitignore_spec(resolved.parent),
                    is_dir=True,
                )
            ):
                self.files_skipped += 1
                return
            self._walk(resolved)
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
        if self._has_excluded_ancestor(path.parent) or _ignored(
            self.root,
            path,
            self.builtin_spec,
            self.exclude_spec,
            combined_gitignore_spec
            if combined_gitignore_spec is not None
            else self._combined_gitignore_spec(path.parent),
            is_dir=False,
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

    def _ancestor_chain(self, directory: Path) -> list[Path]:
        """Every directory from the one just below root down to `directory`, root-first.

        Both ancestor walks below need this same sequence, and both need it to stop cleanly for a
        `directory` outside root — `relative_to` raises there, and the empty chain is the honest
        answer: nothing between the two to check.
        """
        try:
            relative_parts = directory.relative_to(self.root).parts
        except ValueError:
            return []
        chain: list[Path] = []
        current = self.root
        for part in relative_parts:
            current = current / part
            chain.append(current)
        return chain

    def _has_excluded_ancestor(self, directory: Path) -> bool:
        """Whether any directory between root and `directory` (inclusive) is itself excluded by
        `builtin_spec` or `exclude_spec`.

        The gitignore side of this already exists, inside `_combined_gitignore_spec`, for exactly
        the reason spelled out there: git attributes the exclusion to the directory, so a
        negation can never re-include a file whose parent directory is excluded. Configured
        `exclude` accepts the same Git-ignore syntax, negations included, and `docs/configuration.md`
        documents it as following git's semantics — but it is a static root-anchored spec matched
        only against the path in hand, so it had no equivalent.

        The gap showed up only on explicit paths. With `exclude = ["src/generated/",
        "!src/generated/foo.py"]` a normal walk prunes `generated` at `_traversable_dirs` and
        never reaches the negation, but `house-lint check src/generated/foo.py` goes straight to
        the file-level match, where the negation is the last matching line and wins — so the same
        file was skipped by a full scan and linted when named. Built-in excludes are folded in
        here too: they are directory patterns of the same shape (`.git/`, `.venv/`), and a
        configured negation must not resurrect a file out of one either.

        Cached per directory, like the combined gitignore spec, so the walk pays O(depth) once
        per directory rather than once per file.
        """
        if directory in self.excluded_ancestor_cache:
            return self.excluded_ancestor_cache[directory]
        excluded = any(
            _ignored(self.root, ancestor, self.builtin_spec, self.exclude_spec, is_dir=True)
            for ancestor in self._ancestor_chain(directory)
        )
        self.excluded_ancestor_cache[directory] = excluded
        return excluded

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

        Checks each ancestor against the lines accumulated from *its* ancestors before reading
        that ancestor's own `.gitignore` and folding its patterns in. Real git never reads ignore
        files inside a directory it doesn't descend into, so once an ancestor is already excluded,
        a nested negation further down must not be allowed to resurrect it. Normal tree walks
        never hit this — `_traversable_dirs` already prunes an ignored directory before this
        method is ever called for anything beneath it — but an *explicit* path (`house-lint check
        src/ignored/foo.py`) reaches straight in here without going through that walk-time pruning.

        An excluded ancestor therefore returns a match-everything spec rather than the patterns
        accumulated so far. Merely stopping the walk is not enough: the accumulated lines can
        themselves contain the resurrecting negation, since git allows `src/generated/` and
        `!src/generated/foo.py` to sit in the *same* file. Returning those lines would let the
        negation win for an explicit `src/generated/foo.py`, which git reports as ignored — it
        attributes the exclusion to the directory, and a negation can never re-include a file
        whose parent directory is excluded.
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
        for current in self._ancestor_chain(directory):
            if lines and _ignored(
                self.root,
                current,
                self._spec_for_lines(tuple(lines), current),
                is_dir=True,
            ):
                excluded = self._spec_for_lines(IGNORE_EVERYTHING, directory)
                self.combined_gitignore_spec_cache[directory] = excluded
                return excluded
            prefix = "/".join(
                _escape_gitignore_literal(segment)
                for segment in current.relative_to(self.root).parts
            )
            lines.extend(
                _prefix_pattern(prefix, line) for line in self._own_gitignore_lines(current)
            )
        spec = self._spec_for_lines(tuple(lines), directory)
        self.combined_gitignore_spec_cache[directory] = spec
        return spec

    def _spec_for_lines(self, lines: tuple[str, ...], directory: Path) -> GitIgnoreSpec:
        """Build (or reuse) the `GitIgnoreSpec` for an accumulated line tuple.

        `directory` is used only for error attribution if the lines fail to parse; it is not
        part of the cache key, since two directories that accumulate the same lines (e.g. a
        directory with no `.gitignore` of its own repeating its parent's accumulated lines) share
        one parsed spec.
        """
        cached_spec = self.spec_by_lines_cache.get(lines)
        if cached_spec is not None:
            return cached_spec
        try:
            spec = GitIgnoreSpec.from_lines(_normalize_contents_glob(line) for line in lines)
        except (TypeError, ValueError, re.error) as exc:
            # Each source's own lines are already validated in `_load_gitignore_lines`, but
            # `_prefix_pattern`'s rewrite of them is not independently re-validated — a valid
            # original line could in principle become invalid once prefixed, so this stays live.
            #
            # Reported once per (lines, directory) pair rather than once per call. A failing
            # ancestor's lines are re-walked by `_combined_gitignore_spec` for every directory
            # beneath it, so without this the same failure is appended once per descendant —
            # hundreds of identical entries in a large tree, all attributed to the one ancestor.
            # Keyed on the pair, not the lines alone, so a second directory whose accumulated
            # lines fail the same way is still reported against its own path.
            if (lines, directory) not in self.reported_spec_failures:
                self.reported_spec_failures.add((lines, directory))
                self.errors.append(self._error(directory, "traversal", "combine", str(exc)))
            return GitIgnoreSpec.from_lines(())
        self.spec_by_lines_cache[lines] = spec
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
                self.root,
                child,
                self.builtin_spec,
                self.exclude_spec,
                combined_gitignore_spec,
                is_dir=True,
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
