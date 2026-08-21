"""Strict, deterministic Python file discovery."""

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple

from pathspec import GitIgnoreSpec

# `_DIR_MARK` is the named regex group pathspec's own aggregate matcher uses internally to tell a
# directory-boundary match from an exact-candidate match; `_match_patterns` needs the same
# distinction to avoid the ambiguity documented there. Underscore-prefixed and outside pathspec's
# documented API surface, same risk profile as `GitIgnoreSpecPattern`'s own `.pattern`/`.include`/
# `.match_file()` attributes (see `Key Constraints` in the design doc; mitigated by the
# `pathspec<2` pin).
from pathspec.patterns.gitignore.spec import (
    _DIR_MARK,  # pyright: ignore[reportPrivateUsage]
    GitIgnoreSpecPattern,
)

from house_lint.config import DEFAULT_INCLUDE, ConfigError, get_house_lint_table, load_toml
from house_lint.results import LintError

BUILTIN_EXCLUDES = (".git/", ".venv/", ".nox/", "__pycache__/", "site-packages/", "node_modules/")
MAX_DISCOVERED_FILES = 100_000
_CONTENTS_GLOB = re.compile(r"(?<!\*\*)/\*\*(/?)\Z")
# `LintError.kind`/`.operation` are typed `str` in `results.py` (the public, schema-versioned
# result type), so nothing there enforces this vocabulary -- these `Literal` aliases are this
# module's own closed set of the `kind`/`operation` values it actually passes to `_error`, so a
# typo (e.g. `"trversal"`) is a pyright error here rather than a silently-unvalidated string.
_ErrorKind = Literal["path", "traversal", "budget"]
_ErrorOperation = Literal[
    "stat", "read", "parse", "resolve", "walk", "containment", "qualify", "discover", "combine"
]
# Collapses a run of consecutive `**` path segments to a single `**` -- see
# `_is_anchored_pattern`'s docstring for why `is_anchored` needs this collapse rather than a bare
# textual slash check. Not a revival of the old flatten/prefix pipeline's `_DOUBLE_STAR_RUN`
# (deleted per this feature's AC#8): that one fed a full pattern-rewrite-and-reparse step; this one
# only feeds a boolean classification, never rewrites a pattern that reaches `GitIgnoreSpec`.
# The leading `**` must be its own complete path segment -- `(?<![^/])` requires it be preceded by
# `/` or nothing -- so a fused segment like `foo**` in `foo**/**` is never swept into the collapse.
# Without that guard, `foo**/**` collapses to `foo**` (no slash) and is misclassified as
# unanchored, which then lets `_match_patterns` match `foo*`-prefixed directories at any depth
# instead of only at the pattern's own directory, as real git does (verified against
# `git check-ignore`; see the fused-segment scenario in the parity suite).
_DOUBLE_STAR_RUN = re.compile(r"(?<![^/])\*\*(?:/\*\*)+")


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


def _load_gitignore_lines(
    path: Path, on_error: Callable[[_ErrorOperation, str], None]
) -> tuple[str, ...]:
    """Read and validate a `.gitignore` file's pattern lines, reporting stat/read/parse failures.

    Returns raw lines rather than a parsed spec: each directory's lines are compiled into their
    own pattern tuple by `_build_patterns`, which applies its own trailing-`/**` rewrite before
    parsing — so a line valid here could in principle become invalid after that rewrite. Parsing
    here first — then discarding the result — exists purely for error attribution: without it, a
    bad line surfacing only once `_build_patterns` runs would be attributed to whatever directory
    triggered that build, not the specific `.gitignore` file at fault.
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


def _normalized_gitignore_line(line: str) -> str:
    """Rewrite a trailing `/**` so it cannot match the directory whose contents it names.

    git reads `build/**` as "everything inside build" and never matches `build` itself;
    `GitIgnoreSpec` matches the directory too. Spelling the pattern `build/**/*` means the same
    thing to both, since `**` matches zero or more directories but the trailing `*` still demands
    a path component.

    Shared by `_build_patterns` (per-directory `.gitignore` lines) and `_patterns` (the
    root-anchored `exclude_spec` lines) — both build a spec from raw gitignore-syntax lines and
    both need the same fix, since configured `exclude` accepts the same syntax
    `docs/configuration.md` documents as following git's semantics.
    """
    if not line or line.startswith("#"):
        return line
    return _CONTENTS_GLOB.sub(r"/**/*\1", line)


def _is_anchored_pattern(text: str) -> bool:
    """Whether a gitignore pattern needs the full multi-segment path for correct matching.

    git's rule (gitignore(5)): a separator at the beginning or middle of the pattern anchors it
    to the `.gitignore`'s own directory; a pattern with no such separator may match at any depth
    below it. A bare textual "does the pattern's core contain a slash" check gets a run of
    consecutive `**` segments wrong, though: git collapses `**/**/` to mean exactly what `**/`
    means (a single, unanchored "any depth" pattern) even though the raw text has a slash between
    the two `**` (verified against real `git check-ignore`; see the collapse family in the parity
    suite). Collapsing first, via `_DOUBLE_STAR_RUN`, is what makes the check correct for that case
    without reaching into pathspec's compiled regex the way an earlier version of this function
    did — the earlier version parsed the regex source pathspec's compiler emitted, an
    implementation detail of pathspec's own internals rather than something gitignore syntax
    itself defines, and riskier than the below-API-surface attribute reliance `Key Constraints`
    documents (an attribute can stay stable in name and type while pathspec's regex-formatting
    internals drift, silently misclassifying a pattern with no exception to catch it).

    Patterns starting with `**/` (e.g. `**/sub/deep.py`, `**/x/**foo`) are gitignore-unanchored
    but are classified as `is_anchored=True` here because `_match_patterns` uses this flag to
    decide whether to pass the full multi-segment path or truncate to the last segment. These
    patterns need the full path for correct matching — pathspec's compiled regex already handles
    the any-depth matching via a `(?:.+/)?` prefix, so passing the full path is both safe and
    necessary. See the leading-`**/` family in the parity suite for differential verification.

    `text` is the pattern as pathspec parsed it (post `_normalized_gitignore_line` rewrite) --
    same source `is_dir_only` reads. That rewrite only ever adds slashes to an already-anchored
    trailing `/**`, so operating on the normalized text gives the same answer as the pre-rewrite
    raw line in every case.
    """
    core = text.removeprefix("!")
    if core.endswith("/") and core != "/":
        core = core[:-1]
    core = _DOUBLE_STAR_RUN.sub("**", core)
    return "/" in core


def _patterns(excludes: tuple[str, ...]) -> tuple[GitIgnoreSpec, GitIgnoreSpec]:
    return (
        GitIgnoreSpec.from_lines(BUILTIN_EXCLUDES),
        GitIgnoreSpec.from_lines(_normalized_gitignore_line(value) for value in excludes),
    )


def _ignored(root: Path, path: Path, *specs: GitIgnoreSpec, is_dir: bool) -> bool:
    """Match `path`, relative to `root`, against each spec.

    Scoped to the two static, root-anchored specs (`builtin_spec` and `exclude_spec`) — nested
    `.gitignore` patterns are matched separately, directory-relative, by
    `_FileSelector._is_gitignore_excluded`, which is not one of these `*specs` and is checked
    independently at each call site.

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


class IgnorePattern(NamedTuple):
    """One compiled `.gitignore` pattern, paired with whether it is directory-only and whether
    it is anchored to its owning directory.

    `is_dir_only` is `True` when the raw pattern text (after stripping a leading `!`) ends with
    `/` -- gitwildmatch's own directory-only marker. `is_anchored` controls whether
    `_match_patterns` passes the full multi-segment `relative_path` or truncates to the last
    segment. This diverges from gitignore's own "anchored" concept: patterns starting with `**/`
    are gitignore-unanchored (match at any depth) but get `is_anchored=True` here because they
    need the full path for correct multi-segment matching -- pathspec's compiled regex already
    handles the any-depth behavior via its `(?:.+/)?` prefix. See `_is_anchored_pattern` and
    `_match_patterns` for the full reasoning.

    `is_anchored` is derived by `_is_anchored_pattern` from the pattern's own text (after
    collapsing consecutive `**` runs to one via `_DOUBLE_STAR_RUN`), not from pathspec's compiled
    regex. A run of consecutive `**` segments has to be collapsed first because git treats
    `**/**/` as exactly `**/` (a single, unanchored "any depth" pattern) even though the raw text
    has a "middle" slash between the two `**` -- but that collapsing rule is small, documented in
    `gitignore(5)`, and computed directly from the pattern text house-lint already has, rather
    than reverse-engineered from the literal string pathspec's regex compiler happens to emit for
    an unanchored pattern (an implementation detail of pathspec's internals, not something
    gitignore syntax defines, and a narrower, more mechanical reimplementation than the deleted
    flatten/prefix pipeline's full pattern-rewrite job).

    A `NamedTuple` rather than a `@dataclass`: zero runtime cost over a plain tuple, stays
    positionally compatible with the free-function convention `_match_patterns`/`_ignored`
    already establish, but named-field access removes the risk of two adjacent `bool` fields
    (`is_dir_only`, `is_anchored`) being silently transposed or a new field being inserted in the
    wrong position -- exactly the class of drift `IgnorePatterns` already suffered once (a plain
    2-tuple grew this `is_anchored` field without the design doc catching it in review).
    """

    pattern: GitIgnoreSpecPattern
    is_dir_only: bool
    is_anchored: bool


IgnorePatterns = tuple[IgnorePattern, ...]
"""One directory's own compiled `.gitignore` patterns. See `IgnorePattern` for the per-pattern
fields."""

_GitignoreStack = tuple[tuple[Path, IgnorePatterns], ...]
"""Accumulated per-directory matchers from root down through some directory, innermost last --
paired with the directory that owns each matcher, since a candidate must be probed relative to
that owning directory rather than to root. See `_FileSelector._directory_gitignore_context`."""


def _match_patterns(patterns: IgnorePatterns, relative_path: str, is_dir: bool) -> bool | None:
    """Tri-state match of `relative_path` against one directory's own `.gitignore` patterns.

    `relative_path` is relative to the directory that owns `patterns` (the directory containing
    the `.gitignore` these were parsed from), not necessarily the discovery root -- the caller is
    responsible for that relative-path threading when probing a stack of per-directory matchers.
    It can span multiple segments (e.g. `"a/sub"`) when the owning directory is more than one
    level above the candidate being checked.

    Iterates in reverse so the last matching pattern wins, per git's own precedence rule. A
    directory-only pattern (`is_dir_only=True`) is only eligible when `is_dir=True`; it is skipped
    entirely otherwise, even if its regex would technically match the file-form probe. The probe
    itself carries a trailing slash for directories -- `pattern.match_file("src/")` matches
    directory-only patterns like `**/` where `pattern.match_file("src")` does not.

    An unanchored pattern (`is_anchored=False`, e.g. `cache`, `*.py`, `*`) is only probed with the
    *last* segment of `relative_path`, never the full multi-segment path. gitignore gives such a
    pattern "matches a component name at any depth" semantics, which `_FileSelector.
    _is_gitignore_excluded`'s own ancestor-by-ancestor walk already provides one level at a time --
    every intermediate directory along the way gets its own, separate call to this function.
    Handing an unanchored pattern the *full* multi-segment path instead double-applies that "any
    depth" behavior through pathspec's regex too (its `(?:.+/)?` prefix lets the pattern match
    starting partway through the string), which can let a plain ignore pattern owned by an outer
    directory (e.g. `cache`) match straight through an intermediate directory whose exclusion
    status a closer, more specific negation (`!cache/`) already resolved differently -- confirmed
    against real `git check-ignore`: `["cache", "!cache/"]` does not ignore `src/cache/c.py`.

    An anchored pattern (`is_anchored=True`, e.g. `a/**/`, `/a.py`, `sub/x.py`, `**/sub/deep.py`)
    keeps the full path. For slash-containing patterns this is straightforward (their embedded slash
    pins them to a specific depth). For leading-`**/` patterns it is also correct: pathspec's regex
    already handles any-depth matching via `(?:.+/)?`, so no truncation is needed and the full path
    is required for multi-segment suffixes to match. See `_is_anchored_pattern`'s docstring. But the same
    intermediate-ancestor ambiguity can still surface here: an anchored, ambiguous (no trailing
    slash) pattern like `src/sub` matches a *prefix* of a deeper probe too (`src/sub` followed by
    `/`, with more path remaining), for the identical reason `cache` does. A match is only accepted
    if it covers the probe in full, or if it stops short at a position that is *not* a directory
    boundary (`ps_d` unset -- see `_DIR_MARK`) -- the latter covers `*`/`**`, whose degenerate
    regex (a bare `.`) matches a single arbitrary character via `re.search` and has no boundary
    concept at all, so any match from it always counts regardless of position.

    Returns `True` when the winning pattern is an ignore, `False` when it is a `!`-prefixed
    negation, and `None` when nothing in `patterns` has an opinion -- the caller then falls back to
    the next-outer directory's patterns, matching git's closest-`.gitignore`-wins precedence.

    `include=True` on a `GitIgnoreSpecPattern` means "this is an ignore pattern," not "this
    pattern includes/keeps the file" -- the name is the opposite of what it suggests, verified
    empirically against `pathspec`. `include=False` means the pattern is a negation.
    """
    for pattern, is_dir_only, is_anchored in reversed(patterns):
        if is_dir_only and not is_dir:
            continue
        probe_path = relative_path if is_anchored else relative_path.rpartition("/")[2]
        probe = f"{probe_path}/" if is_dir else probe_path
        result = pattern.match_file(probe)
        if result is None:
            continue
        # `RegexMatchResult.match` is a bare `re.Match` in pathspec's own stub, unparameterized
        # over `AnyStr` -- pyright treats every access through it as partially unknown. Every
        # match here is over a `str` probe, so `.groupdict()`/`.end()` are the ordinary
        # `str`-flavored `re.Match` methods.
        match_end: int = result.match.end()  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        match_groups: dict[str, str | None] = result.match.groupdict()  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if match_groups.get(_DIR_MARK) and match_end < len(probe):
            continue
        return pattern.include
    return None


def _trailing_whitespace_trimmed(line: str) -> str:
    """Trim trailing spaces/tabs from `line`, except one quoted by a backslash escape.

    Mirrors gitwildmatch's own trailing-whitespace rule ("trailing spaces are ignored unless
    they are quoted with backslash"). `GitIgnoreSpec.from_lines` does not implement this rule for
    every backslash parity -- verified empirically: an even run of backslashes immediately before
    a trailing space is still read as escaping that space, keeping a space real git strips. Run
    before handing lines to `GitIgnoreSpec.from_lines` in `_build_patterns` to correct for it.

    What decides the question is the *parity* of the backslash run before the whitespace, not
    whether a single backslash sits there: backslashes quote each other pairwise, so an even run
    leaves the space unquoted and git strips it. `a\\\\ ` (two backslashes, one space) is the case
    that separates the two readings — git reduces it to `a\\\\`, which names `a\\`, while treating
    the lone preceding backslash as an escape keeps the space and names `a\\ ` instead. Checked
    against real `git check-ignore`; see the parity suite.
    """
    if not line.strip() or line.startswith("#"):
        return line
    end = len(line)
    while end > 0 and line[end - 1] in " \t":
        backslashes = 0
        index = end - 2
        while index >= 0 and line[index] == "\\":
            backslashes += 1
            index -= 1
        if backslashes % 2 == 1:
            break
        end -= 1
    return line[:end]


def _build_patterns(lines: tuple[str, ...], on_error: Callable[[str], None]) -> IgnorePatterns:
    """Parse one directory's raw `.gitignore` lines into a compiled `IgnorePatterns` tuple.

    Applies `_trailing_whitespace_trimmed`'s backslash-parity fix, then
    `_normalized_gitignore_line`'s trailing-`/**`-to-`/**/*` rewrite, before parsing (see that
    function's docstring for why: git reads `build/**` as "everything inside build" and never
    matches `build` itself, while `GitIgnoreSpec` matches the directory too).

    `GitIgnoreSpec.from_lines()` is used to parse rather than constructing `GitIgnoreSpecPattern`
    objects directly -- it is the only reliable entry point for gitignore-syntax parsing (comment
    lines, blank lines, escaping, etc.).

    On parse failure, returns `()` and calls `on_error(<message>)` instead of raising -- mirrors
    `_load_gitignore_lines`'s callback convention: this function owns neither `self.errors` nor
    the directory being processed, so the caller (`_own_matcher`) supplies the callback and
    attributes the failure. Each source's own raw lines are already validated by
    `_load_gitignore_lines`, so a failure here is only possible if this function's own
    normalization step produces something unparsable -- rare, but a valid original line could in
    principle become invalid once rewritten, so this stays live rather than being treated as
    unreachable.
    """
    normalized = tuple(
        _normalized_gitignore_line(_trailing_whitespace_trimmed(line)) for line in lines
    )
    try:
        spec = GitIgnoreSpec.from_lines(normalized)
    except (TypeError, ValueError, re.error) as exc:
        on_error(str(exc))
        return ()
    built: list[IgnorePattern] = []
    for pattern in spec.patterns:
        # `.pattern` is typed `str | bytes | re.Pattern | None` upstream (a `RegexPattern` may in
        # principle hold a compiled regex instead of source text), but every pattern here was
        # built from `GitIgnoreSpec.from_lines()`'s own line-parsing path, which always stores the
        # original `str` line -- never `bytes`, a compiled `re.Pattern`, or `None`.
        raw: object = pattern.pattern  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        text = raw if isinstance(raw, str) else ""
        is_dir_only = text.removeprefix("!").endswith("/")
        is_anchored = _is_anchored_pattern(text)
        built.append(IgnorePattern(pattern, is_dir_only, is_anchored))
    return tuple(built)


@dataclass
class _FileSelector:
    root: Path
    builtin_spec: GitIgnoreSpec
    exclude_spec: GitIgnoreSpec
    errors: list[LintError]
    use_gitignore: bool = True
    selected: dict[Path, Path] = field(default_factory=lambda: dict[Path, Path]())
    files_skipped: int = 0
    limit_reached: bool = False
    own_matcher_cache: dict[Path, IgnorePatterns] = field(
        default_factory=lambda: dict[Path, IgnorePatterns]()
    )
    excluded_ancestor_cache: dict[Path, bool] = field(default_factory=lambda: dict[Path, bool]())
    directory_gitignore_context_cache: dict[Path, tuple[bool, _GitignoreStack]] = field(
        default_factory=lambda: dict[Path, tuple[bool, _GitignoreStack]]()
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

    def _is_path_ignored(self, resolved: Path, *, is_dir: bool) -> bool:
        """Shared three-way ignore check used by both `_consider` branches.

        A resolved path is ignored if it has an excluded ancestor, matches the
        builtin/exclude spec, or is excluded by a `.gitignore`. Shared by the
        directory and file branches of `_consider`, which differ only in the
        `is_dir` value they pass through and in what happens on a non-match.
        """
        return resolved != self.root and (
            self._has_excluded_ancestor(resolved.parent)
            or _ignored(self.root, resolved, self.builtin_spec, self.exclude_spec, is_dir=is_dir)
            or self._is_gitignore_excluded(resolved.parent, resolved.name, is_dir=is_dir)
        )

    def _consider(self, path: Path, *, explicit_paths: bool) -> None:
        """Evaluate `path` for selection."""
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
            if self._is_path_ignored(resolved, is_dir=True):
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
        # Resolved, for the same reason the directory branch above is: `relative_to(root)` keeps
        # a `..` as a literal part, so `src/../tests/a.py` would walk `src` as an ancestor and
        # apply its `.gitignore` to a file under `tests`. The two branches have to agree —
        # fixing only one left `check src/../tests` and `check src/../tests/a.py` disagreeing
        # with each other as well as with git. `path` stays the reported spelling.
        if self._is_path_ignored(resolved, is_dir=False):
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
            dirs[:] = self._traversable_dirs(current_path, dirs)
            for name in sorted(names):
                self._consider(current_path / name, explicit_paths=False)
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

        The gitignore side of this already exists, inside `_is_gitignore_excluded`, for exactly
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

        Cached per directory so the walk pays O(depth) once per directory rather than once per
        file.
        """
        if directory in self.excluded_ancestor_cache:
            return self.excluded_ancestor_cache[directory]
        excluded = any(
            _ignored(self.root, ancestor, self.builtin_spec, self.exclude_spec, is_dir=True)
            for ancestor in self._ancestor_chain(directory)
        )
        self.excluded_ancestor_cache[directory] = excluded
        return excluded

    def _is_gitignore_excluded(self, directory: Path, relative_path: str, is_dir: bool) -> bool:
        """Whether `directory / relative_path` is excluded by the gitignore pattern stack from
        root through `directory`.

        Delegates the root-to-`directory` walk to `_directory_gitignore_context`, cached per
        `directory` — every file sharing a parent directory gets an identical ancestor verdict
        and matcher stack, so only this method's own final candidate-vs-stack match (below) is
        repeated per file. See that method's docstring for the ancestor-walk semantics (the
        directory-attributed-exclusion rule, the stack-building order) that used to live here.

        After the walk, probes the candidate itself against the full stack, innermost matcher
        first — the first one with an opinion wins, matching git's closest-`.gitignore`-wins,
        last-matching-line-wins precedence.

        Each matcher in the stack is probed with the candidate's path relative to *that matcher's
        own owning directory*, not to `directory` or root — a nested `.gitignore`'s patterns are
        anchored to the directory that contains it. This is what makes a slash-containing pattern
        in a non-root `.gitignore` (e.g. `src/.gitignore` with `a/**/`) match correctly against a
        deeper candidate.

        Short-circuits to `False` when `use_gitignore` is disabled, before any `.gitignore` is
        read — `--no-gitignore` skips that filesystem I/O entirely, not just its result.
        """
        if not self.use_gitignore:
            return False
        excluded, stack = self._directory_gitignore_context(directory)
        if excluded:
            return True
        candidate = directory / relative_path
        for owner, patterns in reversed(stack):
            verdict = _match_patterns(
                patterns, candidate.relative_to(owner).as_posix(), is_dir=is_dir
            )
            if verdict is not None:
                return verdict
        return False

    def _directory_gitignore_context(self, directory: Path) -> tuple[bool, _GitignoreStack]:
        """Whether `directory` itself is gitignore-excluded, plus the matcher stack accumulated
        from root through `directory` when it is not — cached per `directory`.

        When `directory` is excluded, the walk below stops at the excluding ancestor, so the
        returned stack is a partial prefix, not "root through `directory`" — harmless today
        because the one caller, `_is_gitignore_excluded`, always checks the `excluded` half of
        this return value first and never reads `stack` in that branch, but not a contract a
        future caller should rely on without the same check.

        Fuses what the deleted `_combined_gitignore_spec` used to do in one pass: building the
        stack of per-directory matchers, and checking whether any ancestor along the way is
        already excluded as a directory. Real git never reads ignore files inside a directory it
        never descends into, so once an ancestor is excluded, a nested negation further down must
        not be able to resurrect it — the same invariant `_traversable_dirs`'s walk-time pruning
        already gives normal tree walks for free, but an *explicit* path (`house-lint check
        src/ignored/foo.py`) reaches straight into `_is_gitignore_excluded` without going through
        that pruning, so this method has to enforce it independently.

        Walks root-to-leaf via `_ancestor_chain(directory)` (which ends at `directory` itself).
        At each ancestor A, probes A **as a directory** against the stack accumulated from A's
        ancestors only — A's own `.gitignore`, even if one exists, is never consulted when
        deciding whether A itself is pruned. If A is excluded, the walk stops there: `directory`
        is excluded regardless of any negation, even one sitting in the very same `.gitignore`
        file (`src/generated/` plus `!src/generated/foo.py` still excludes
        `src/generated/foo.py`, because git attributes the exclusion to the directory). If A is
        not excluded, A's own matcher (from `own_matcher_cache`, built via `_build_patterns` and
        `_own_gitignore_lines` on cache miss) is folded onto the stack before moving to the next
        ancestor.

        Every file in `directory` shares this same ancestor verdict and stack — caching by
        `directory` turns what was an O(ancestor depth) recomputation per *file* into one per
        *directory*, which is what `_is_gitignore_excluded` was doing before this was split out.
        """
        cached = self.directory_gitignore_context_cache.get(directory)
        if cached is not None:
            return cached
        stack: list[tuple[Path, IgnorePatterns]] = [(self.root, self._own_matcher(self.root))]
        excluded = False
        for ancestor in self._ancestor_chain(directory):
            verdict = None
            for owner, patterns in reversed(stack):
                verdict = _match_patterns(
                    patterns, ancestor.relative_to(owner).as_posix(), is_dir=True
                )
                if verdict is not None:
                    break
            if verdict:
                excluded = True
                break
            stack.append((ancestor, self._own_matcher(ancestor)))
        result = (excluded, tuple(stack))
        self.directory_gitignore_context_cache[directory] = result
        return result

    def _own_matcher(self, directory: Path) -> IgnorePatterns:
        """Compiled `.gitignore` pattern tuple for `directory`, cached by directory path.

        Every directory, root included, gets its lines from `_own_gitignore_lines` -- the same
        per-directory read-and-cache path the deleted `_combined_gitignore_spec` used. Root needs
        no special treatment for that read-and-cache path: this method's own `own_matcher_cache`
        guard already ensures `_own_gitignore_lines` is called at most once per directory, so
        there's no double-read to avoid by pre-loading root's lines elsewhere. Root *is*
        special-cased below, in `on_error`'s reported-path label -- root's `.gitignore` is
        reported as `root / ".gitignore"` rather than as the bare root directory, since a
        directory-only error would otherwise be indistinguishable from an error on the directory
        itself.

        `_build_patterns` cannot attribute a post-normalization parse failure to a directory or
        append to `self.errors` itself -- it owns neither. This method does, via the `on_error`
        callback it supplies -- the same callback convention `_own_gitignore_lines` already uses
        for `_load_gitignore_lines` -- and is called at most once per directory (guarded by
        `own_matcher_cache` below), so a failure here is reported exactly once per directory, same
        as the deleted `_spec_for_lines`'s "combine" errors were.
        """
        if directory in self.own_matcher_cache:
            return self.own_matcher_cache[directory]
        resolved_lines = self._own_gitignore_lines(directory)

        def on_error(message: str) -> None:
            reported = directory / ".gitignore" if directory == self.root else directory
            self.errors.append(self._error(reported, "traversal", "combine", message))

        built = _build_patterns(resolved_lines, on_error)
        self.own_matcher_cache[directory] = built
        return built

    def _own_gitignore_lines(self, directory: Path) -> tuple[str, ...]:
        ignore = directory / ".gitignore"

        def on_error(operation: _ErrorOperation, message: str) -> None:
            self.errors.append(self._error(ignore, "traversal", operation, message))

        return _load_gitignore_lines(ignore, on_error)

    def _traversable_dirs(self, current_path: Path, dirs: list[str]) -> list[str]:
        """Return child directory names to descend into, recording why each was dropped.

        Drops symlinked directories (never traversed) and directories already excluded by
        `builtin_spec`, `exclude_spec`, or the gitignore stack accumulated from root down to
        `current_path` (the parent) — deliberately *not* including the child's own, not-yet-read
        `.gitignore`. Checking a child against its own nested `.gitignore` before deciding
        whether to descend into it would let a negation inside that file "resurrect" files
        that should stay excluded because the directory itself is ignored — real git never
        reads ignore files inside a directory it never descends into. Skipping the
        directory here means `_own_gitignore_lines`/`_is_gitignore_excluded` are simply
        never called for it, so its nested `.gitignore` (if any) is never read at all.
        `_is_gitignore_excluded` already folds in `use_gitignore` (it short-circuits to `False`
        when disabled), and `builtin_spec`/`exclude_spec` inside `_ignored` apply
        unconditionally, matching how file-level ignoring already treats those two specs.
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
                self.root, child, self.builtin_spec, self.exclude_spec, is_dir=True
            ) or self._is_gitignore_excluded(current_path, item, is_dir=True):
                self.files_skipped += 1
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
    builtin_spec, exclude_spec = _patterns(excludes)
    requested = explicit or tuple(root / item for item in include)
    selector = _FileSelector(
        root,
        builtin_spec,
        exclude_spec,
        [],
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
