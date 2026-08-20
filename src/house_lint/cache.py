"""Flat, version-namespaced per-file result cache.

House-lint is a single-file analyzer with no cross-file dependencies, so a flat cache keyed
by (file content hash, effective config hash) is semantically correct — unlike a dependency-
graph cache (e.g. mypy's `.mypy_cache`), there is no invalidation-graph to track. The cache
directory is namespaced by house-lint's own version, so an upgrade invalidates stale entries
automatically without an explicit migration step.

Cache entries are addressed purely by content and config hashes, not by file path — two files
with identical content and an identical effective rule set produce the same entry. Cached
findings and errors are therefore stored without their `path` field; `read_cached_result` takes
the caller-supplied `relative_path` of the file actually being scanned and re-attaches it to
each reconstructed finding/error.
"""

import hashlib
import json
import os
import shutil
import sys
from contextlib import suppress
from dataclasses import asdict, dataclass
from enum import Enum, auto
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from house_lint import __version__
from house_lint.config import HSL101Options, HSL102Options, HSL103Options
from house_lint.results import Finding, LintError
from house_lint.source import MAX_SOURCE_BYTES

CACHE_DIRNAME = ".house-lint-cache"
_VERSION_DIR_MARKER = ".house-lint-version"


class CacheReporter:
    """Where every best-effort cache failure in a single scan is reported.

    A cache failure must never fail the scan — but "never fail" and "never signal" are different
    guarantees, and this module used to conflate them: every failure branch printed only under
    `--debug`. The runs this tool is built for (CI, pre-commit) never pass `--debug`, so an
    unwritable cache directory made every scan silently pay the full re-analysis cost with
    nothing to explain why.

    The first failure of a run is therefore always visible. The rest are `--debug`-only: a broken
    cache directory fails once per scanned file, and printing all of them by default would bury
    the single fact worth reporting under thousands of near-identical lines.

    Held per scan rather than in module state so concurrent scans, and tests, cannot see each
    other's "have I warned yet" flag. Routing every failure site through one object is also what
    keeps the guarantee enforceable: adding a silent `except: pass` to this module now means
    visibly bypassing this class rather than merely forgetting a `debug` check.
    """

    def __init__(self, *, debug: bool = False) -> None:
        self.debug = debug
        self._reported = False

    def failure(self, message: str) -> None:
        """Report one failed cache operation. Loud the first time, `--debug`-only after that."""
        if not self._reported:
            self._reported = True
            print(f"warning: {message}", file=sys.stderr)
            return
        if self.debug:
            print(f"debug: {message}", file=sys.stderr)


def default_cache_base(root: Path) -> Path:
    """Default cache base directory: `<root>/.house-lint-cache/` (before version-namespacing)."""
    return root / CACHE_DIRNAME


def default_cache_base_is_safe(base: Path) -> bool:
    """Whether house-lint may create and write its own default cache base at `base`.

    The default base lives inside the scanned project, so its path is controlled by whoever
    wrote that project: a repository can ship `.house-lint-cache` as a symlink pointing anywhere,
    and `prepare_cache_dir`'s `mkdir(parents=True, exist_ok=True)` follows it. house-lint would
    then write its version marker, its cache entries, and a wildcard `.gitignore` into the
    directory the link names — outside the checkout, at a location the repository chose. A plain
    `house-lint check` on a freshly cloned repository must never do that, so a symlinked default
    base disables caching for the run instead.

    Only the default base is checked. A `--cache-dir` names a directory the user picked
    deliberately, and house-lint neither self-ignores nor second-guesses it.
    """
    return not base.is_symlink()


@lru_cache(maxsize=1)
def code_identity() -> str:
    """Fingerprint house-lint's own Python sources, for use in the cache namespace.

    `__version__` alone is not enough to invalidate results across a code change: it only moves
    when a release is cut, so editing a detector in a working checkout and re-running replays
    the previous detector's findings for every file whose content and config are unchanged. For
    a linter, silently serving a stale "clean" result after a rule fix defeats the point. It
    also matters across checkouts: two working copies at the same version sharing one
    `--cache-dir` would otherwise trade results.

    Hashes file contents rather than mtimes so the value is stable across machines and fresh
    clones — a released install therefore keeps exactly one cache directory. Read once per
    process; on any read failure (a zipped or otherwise non-file distribution) this returns the
    constant `"unknown"`, so the namespace falls back to `<version>-unknown` and invalidation
    reverts to tracking the version alone rather than failing the scan.
    """
    package_root = Path(__file__).parent
    digest = hashlib.sha256()
    try:
        for source in sorted(package_root.rglob("*.py")):
            digest.update(source.relative_to(package_root).as_posix().encode("utf-8"))
            digest.update(source.read_bytes())
    except OSError:
        return "unknown"
    return digest.hexdigest()[:16]


def versioned_cache_dir(base: Path) -> Path:
    """Namespace a cache base directory by house-lint's version and source fingerprint.

    Applies uniformly to the default base and to a user-supplied `--cache-dir` override — the
    override changes *where* the cache lives, not whether it's still safe across upgrades. Stale
    namespaces do not accumulate: `_prune_stale_version_dirs` removes the siblings house-lint
    itself created the next time an entry is written.
    """
    return base / f"{__version__}-{code_identity()}"


def hash_source_content(content: bytes | None) -> str | None:
    """Hash already-read source bytes for cache-key purposes, or None if they can't be cached.

    Takes the buffer rather than a path on purpose: the caller passes the very bytes the scan
    analyzes (`SourceFile.content_bytes`), so an entry can only ever be keyed by the content its
    findings were derived from. Re-reading the path here instead would reintroduce a window in
    which the key and the findings describe different content.

    None means "don't cache this file this run", not a failure: an unreadable or non-regular
    file has no bytes, and an oversized one is not worth an entry — `SourceFile` still reports a
    proper `LintError` for both.
    """
    if content is None or len(content) > MAX_SOURCE_BYTES:
        return None
    return hashlib.sha256(content).hexdigest()


def hash_effective_config(
    enabled_rules: tuple[str, ...],
    hsl101: HSL101Options,
    hsl102: HSL102Options,
    hsl103: HSL103Options,
    *,
    filename: str,
    python_version: tuple[int, int] | None = None,
) -> str:
    """Hash the config inputs that can change a file's scan outcome, given fixed content.

    `enabled_rules` is the per-file effective set (after `per-file-ignores`, `extend-select`,
    etc. have already resolved it), not the raw configured selection.

    `filename` (the file's own basename, e.g. `path.name`) is folded in only when an enabled
    HSL101 token family scopes to `"filenames"` — that's the one detector in this codebase whose
    output depends on the file's name rather than purely its content, since it matches spec
    tokens against the filename itself (see `_filename_candidates` in rules/spec_tokens.py).
    Without this, two files with identical content but different names could otherwise collide
    on the same cache entry and silently swap each other's filename-derived findings.

    `python_version` (major, minor) defaults to the running interpreter's `sys.version_info[:2]`
    and is always folded into the hash. `SourceFile._analyze` parses source with `ast.parse`,
    whose accepted grammar differs across the Python versions this project supports (e.g. `type
    Alias = int` is a `SyntaxError` before 3.12) — without this, a cache shared across venvs of
    different Python versions could replay a stale `SyntaxError` (or a stale success) that no
    longer matches the interpreter actually running the scan. Accepting it as a parameter (rather
    than reading `sys.version_info` internally) keeps this directly testable.
    """
    payload: dict[str, object] = {
        "enabled_rules": sorted(enabled_rules),
        "hsl101": asdict(hsl101),
        "hsl102": asdict(hsl102),
        "hsl103": asdict(hsl103),
        "python_version": list(
            python_version if python_version is not None else sys.version_info[:2]
        ),
    }
    if "HSL101" in enabled_rules and any("filenames" in family.scopes for family in hsl101.tokens):
        payload["filename"] = filename
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedFileResult:
    """A cacheable per-file scan outcome — everything `FileScanResult` carries except `stop`.

    `stop` (the process-boundary internal-error signal) is deliberately excluded: internal
    errors are non-deterministic failures, not something a re-run with the same content and
    config should replay from cache.
    """

    findings: tuple[Finding, ...] = ()
    errors: tuple[LintError, ...] = ()
    suppressed_count: int = 0
    files_scanned: int = 0


def _entry_path(cache_dir: Path, content_hash: str, config_hash: str) -> Path:
    return cache_dir / f"{content_hash}-{config_hash}.json"


def _require_text(
    data: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Reject a payload whose text fields are not strings, before it reaches a dataclass.

    `Finding`/`LintError` are plain dataclasses: they validate their location fields (via
    `results._validate_location`) but accept any type for the rest. A corrupted-but-valid-JSON
    entry carrying, say, an integer `message` therefore constructs fine and only fails later,
    when `ScanResult.to_dict()` sorts findings and hits `int < str`. That happens while the
    result is being rendered — outside `check()`'s exception boundary — so the command crashes
    with a traceback instead of treating the entry as the documented cache miss.

    Returns the payload so callers can validate and unpack in one expression.
    """
    if not isinstance(data, dict):
        raise TypeError("cache entry item must be an object")
    payload = cast(dict[str, Any], data)
    for name in required:
        if not isinstance(payload.get(name), str):
            raise TypeError(f"{name} must be a string")
    for name in optional:
        value = payload.get(name)
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{name} must be a string or null")
    return payload


def _finding_to_payload(finding: Finding) -> dict[str, Any]:
    data = finding.to_dict()
    del data["path"]
    return data


def _finding_from_payload(data: dict[str, Any], *, path: str) -> Finding:
    return Finding(path=path, **_require_text(data, ("rule_id", "message")))


def _error_to_payload(err: LintError) -> dict[str, Any]:
    data = err.to_dict()
    del data["path"]
    return data


def _error_from_payload(data: dict[str, Any], *, path: str) -> LintError:
    return LintError(
        path=path,
        **_require_text(data, ("code", "kind", "phase", "operation", "message"), ("rule_id",)),
    )


def read_cached_result(
    cache_dir: Path,
    content_hash: str,
    config_hash: str,
    *,
    relative_path: str,
    reporter: CacheReporter,
) -> CachedFileResult | None:
    """Return the cached result for this (content, config) pair, or None on a miss.

    A missing entry (the common case — nothing has cached this file/config pair yet) is a
    silent miss. An entry that exists but can't be read or parsed is also treated as a miss —
    a stale or corrupted cache entry must never fail a scan, only fall back to re-analyzing —
    but that case is unusual enough to go through `reporter`, which makes the run's first such
    failure visible without `--debug`.
    """
    path = _entry_path(cache_dir, content_hash, config_hash)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as exc:
        # Corruption (or a foreign write) can leave bytes that are not valid UTF-8. That raises
        # here, at decode time, rather than in the parse block below — and `UnicodeDecodeError`
        # is a `ValueError`, so the `OSError` handler does not catch it either. Without this
        # branch the exception escapes `_scan` entirely, aborting the run with an internal error
        # instead of the cache miss this function promises.
        reporter.failure(f"cache entry for {relative_path} is corrupted: {exc}")
        return None
    except OSError as exc:
        reporter.failure(f"cache read failed for {relative_path}: {exc}")
        return None
    try:
        payload = json.loads(raw)
        suppressed_count = payload["suppressed_count"]
        files_scanned = payload["files_scanned"]
        # Dataclasses don't enforce annotations at runtime, so a corrupted-but-valid-JSON entry
        # (e.g. `"suppressed_count": "1"`) would otherwise construct successfully here and only
        # fail later when the caller accumulates it (`suppressed_count += cached.suppressed_count`
        # in `cli.py`), turning what should be a graceful cache miss into an internal-error exit.
        # Negative values are the same class of corruption reaching the same accumulation: no
        # real scan produces one, and `"files_scanned": -5` would silently lower the run's
        # reported totals rather than degrading to a miss.
        if (
            not isinstance(suppressed_count, int)
            or isinstance(suppressed_count, bool)
            or suppressed_count < 0
        ):
            raise TypeError("suppressed_count must be a non-negative int")
        if (
            not isinstance(files_scanned, int)
            or isinstance(files_scanned, bool)
            or files_scanned < 0
        ):
            raise TypeError("files_scanned must be a non-negative int")
        return CachedFileResult(
            findings=tuple(
                _finding_from_payload(item, path=relative_path) for item in payload["findings"]
            ),
            errors=tuple(
                _error_from_payload(item, path=relative_path) for item in payload["errors"]
            ),
            suppressed_count=suppressed_count,
            files_scanned=files_scanned,
        )
    except (ValueError, KeyError, TypeError) as exc:
        reporter.failure(f"cache entry for {relative_path} is corrupted: {exc}")
        return None


def _write_marker_if_absent(
    marker: Path, content: str, *, reporter: CacheReporter, description: str
) -> None:
    """Create `marker` with `content` unless it already exists, best-effort.

    Both of house-lint's cache markers are write-once and must never fail a scan, so they share
    this shape. `description` names the marker in the failure line, which is the only part a
    reader of stderr needs to tell the two apart.

    Created with `O_CREAT | O_EXCL`, not an `exists()` test followed by `write_text`. The two are
    not equivalent for a path the scanned project controls: `exists()` follows symlinks, so a
    *dangling* symlink at the marker path reports false and the subsequent write follows the link
    and creates the file it names. `default_cache_base_is_safe` does not close this — it only
    rejects a symlinked base, and a real `.house-lint-cache/` directory holding a dangling
    `.gitignore` symlink passes it. A plain `house-lint check` on a freshly cloned repository
    would then write `*` to a path that repository chose, anywhere on the filesystem. `O_EXCL`
    fails with `EEXIST` on a symlink whether or not its target exists, which is exactly the
    "create only if nothing is here" test this needs, in one unraceable syscall.
    """
    # Split across two `try` blocks rather than one: only the open can raise `FileExistsError`
    # for the reason this function cares about, and merging them would let a write-time
    # `FileExistsError` take the silent "already there, nothing to do" path.
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # Already present — including as a symlink, which is the case `exists()` missed. Either
        # way this marker is not ours to write, and "unless it already exists" is satisfied.
        return
    except OSError as exc:
        reporter.failure(f"cache {description} marker write failed: {exc}")
        return
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    except OSError as exc:
        reporter.failure(f"cache {description} marker write failed: {exc}")


def _write_self_ignore_marker(base: Path, *, reporter: CacheReporter) -> None:
    """Write a `.gitignore` containing `*` into the cache *base* directory, once.

    Mirrors how pytest/mypy self-ignore their own cache directories: a downstream project that
    runs house-lint gets an untracked, `git status`-invisible `.house-lint-cache/` without having
    to add it to their own `.gitignore` by hand. Written at `base` (the unversioned
    `.house-lint-cache/` directory), not the version-namespaced subdirectory, since that's the
    path a `git status` in the scanned repo would actually flag. Best-effort: a failed marker
    write must never fail the scan.

    Only ever called for house-lint's own default base (see `prepare_cache_dir`). A
    `--cache-dir` names a directory the user already owns — writing `*` into it would change
    Git's behaviour for every unrelated sibling, and pointing it at a project root would hide
    the entire project from `git status`.
    """
    _write_marker_if_absent(
        base / ".gitignore", "*\n", reporter=reporter, description="self-ignore"
    )


def _write_version_dir_marker(cache_dir: Path, *, reporter: CacheReporter) -> None:
    """Mark `cache_dir` as a house-lint-owned version directory, best-effort.

    `_prune_stale_version_dirs` only deletes sibling directories carrying this marker — without
    it, a `--cache-dir` pointed at a pre-existing shared directory (e.g. `~/.cache`) would have
    every unrelated sibling directory recursively deleted on the first cache write, since nothing
    would distinguish "an old house-lint version directory" from "someone else's data."
    """
    _write_marker_if_absent(
        cache_dir / _VERSION_DIR_MARKER, "", reporter=reporter, description="version-dir"
    )


def _prune_stale_version_dirs(cache_dir: Path, *, reporter: CacheReporter) -> None:
    """Remove sibling version directories under `cache_dir`'s base, best-effort.

    `versioned_cache_dir` namespaces the cache so an upgrade (or a source change) invalidates
    stale entries, but nothing else ever deletes the superseded directory — left alone, those
    namespaces accumulate under `<base>/` forever. Reached only via `prune_stale_cache_dirs`,
    which a scan calls once and only after it has actually written an entry, so a run of pure
    cache hits never deletes anything. A *concurrent* process actively writing under a different
    namespace during an overlapping run can still have its directory removed by this call —
    best-effort here means "safe to fail," not "race-free."

    Only siblings carrying `_VERSION_DIR_MARKER` are eligible — a directory without it was never
    created by house-lint's own versioned-cache writes, so it's left untouched regardless of how
    it got there (a pre-existing directory under a shared `--cache-dir`, something else entirely).
    """
    base = cache_dir.parent
    try:
        siblings = [child for child in base.iterdir() if child.is_dir() and child != cache_dir]
    except OSError as exc:
        reporter.failure(f"cache prune could not list {base}: {exc}")
        return
    for sibling in siblings:
        if not (sibling / _VERSION_DIR_MARKER).is_file():
            continue
        try:
            shutil.rmtree(sibling)
        except OSError as exc:
            reporter.failure(f"cache prune of stale version dir failed: {exc}")


def prepare_cache_dir(cache_dir: Path, *, self_ignore: bool, reporter: CacheReporter) -> None:
    """Create and mark the cache directory, once per scan.

    This is per-run bookkeeping, not per-entry: creating the directory, marking it as
    house-lint-owned, and (for house-lint's own default base only) dropping the self-ignore
    marker. Doing it inside `write_cached_result` meant three extra filesystem calls for every
    single file scanned, which is real overhead in the one code path whose entire purpose is to
    be faster.

    Deliberately does *not* prune — see `prune_stale_cache_dirs`, which must stay tied to an
    actual write. `self_ignore` must be true only when `cache_dir` sits beneath house-lint's own
    default `.house-lint-cache/` base, never for a user-supplied `--cache-dir`. Best-effort
    throughout: a failure here costs caching, never the scan.
    """
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        reporter.failure(f"cache directory could not be created: {exc}")
        return
    _write_version_dir_marker(cache_dir, reporter=reporter)
    if self_ignore:
        _write_self_ignore_marker(cache_dir.parent, reporter=reporter)


def prune_stale_cache_dirs(cache_dir: Path, *, reporter: CacheReporter) -> None:
    """Sweep superseded namespaces, once per scan and only after a real write has happened.

    Kept separate from `prepare_cache_dir` so that a run which writes nothing — every file a
    cache hit — never deletes anything. That matters because the deletion is not race-free: a
    concurrent house-lint process on a different version or build, sharing a `--cache-dir`, can
    have its in-progress namespace removed. Tying the sweep to "this run actually wrote an
    entry" keeps that window as narrow as it was before the per-run bookkeeping was hoisted out
    of `write_cached_result`.
    """
    _prune_stale_version_dirs(cache_dir, reporter=reporter)


class _WriteOutcome(Enum):
    """Why one atomic entry write ended the way it did — specifically, whether retrying helps."""

    WRITTEN = auto()
    DIRECTORY_MISSING = auto()
    FAILED = auto()


def _write_entry(path: Path, payload: dict[str, Any], *, reporter: CacheReporter) -> _WriteOutcome:
    """Write one entry atomically (temp file + `os.replace`).

    Atomicity means an interrupted process, or two concurrent house-lint runs writing the same
    entry, can never leave a partially-written file in place of a real one. The temp file is
    named per-PID, so a failure part-way through would otherwise strand a file no later run could
    recognise or clean up — hence the unlink on the error path.
    """
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, path)
    except FileNotFoundError as exc:
        reporter.failure(f"cache write failed, directory is gone: {exc}")
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        return _WriteOutcome.DIRECTORY_MISSING
    except OSError as exc:
        reporter.failure(f"cache write failed: {exc}")
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        return _WriteOutcome.FAILED
    return _WriteOutcome.WRITTEN


def write_cached_result(
    cache_dir: Path,
    content_hash: str,
    config_hash: str,
    result: CachedFileResult,
    *,
    self_ignore: bool,
    reporter: CacheReporter,
) -> bool:
    """Write a cache entry, best-effort. A failed write must never fail the scan itself — but it
    is reported through `reporter`, so "why isn't caching working" is answerable from a plain
    `house-lint check` and diagnosable in full under `--debug`.

    Returns whether the entry was durably persisted. Callers need that distinction rather than
    "a write was attempted": `prune_stale_cache_dirs` is gated on this run having contributed a
    real entry, and a run whose every write fails must not delete another process's namespace
    while contributing nothing of its own.

    Assumes `prepare_cache_dir` has already run for this `cache_dir` — and restores that
    precondition once if it has been undone mid-run. A concurrent house-lint process on a
    different namespace, sharing the same `--cache-dir`, can `rmtree` this one out from under an
    in-progress scan (see `_prune_stale_version_dirs`). Since `prepare_cache_dir` runs once per
    scan and is never retried, one raced prune would otherwise cost every remaining write in the
    run. Re-preparing and retrying bounds the damage to the single entry in flight. `self_ignore`
    is what that re-preparation needs, and must carry the same value the scan's own
    `prepare_cache_dir` call used.

    Only a vanished directory is retried. A permissions or out-of-space failure will not fix
    itself between two adjacent calls, so retrying it would just pay twice for the same failure.
    """
    path = _entry_path(cache_dir, content_hash, config_hash)
    payload = {
        "findings": [_finding_to_payload(finding) for finding in result.findings],
        "errors": [_error_to_payload(err) for err in result.errors],
        "suppressed_count": result.suppressed_count,
        "files_scanned": result.files_scanned,
    }
    outcome = _write_entry(path, payload, reporter=reporter)
    if outcome is not _WriteOutcome.DIRECTORY_MISSING:
        return outcome is _WriteOutcome.WRITTEN
    prepare_cache_dir(cache_dir, self_ignore=self_ignore, reporter=reporter)
    return _write_entry(path, payload, reporter=reporter) is _WriteOutcome.WRITTEN


__all__ = [
    "CACHE_DIRNAME",
    "CacheReporter",
    "CachedFileResult",
    "code_identity",
    "default_cache_base",
    "default_cache_base_is_safe",
    "hash_effective_config",
    "hash_source_content",
    "prepare_cache_dir",
    "prune_stale_cache_dirs",
    "read_cached_result",
    "versioned_cache_dir",
    "write_cached_result",
]
