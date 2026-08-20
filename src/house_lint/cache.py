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
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from house_lint import __version__
from house_lint.config import HSL101Options, HSL102Options, HSL103Options
from house_lint.results import Finding, LintError
from house_lint.source import MAX_SOURCE_BYTES, read_regular_file_bytes

CACHE_DIRNAME = ".house-lint-cache"
_VERSION_DIR_MARKER = ".house-lint-version"


def default_cache_base(root: Path) -> Path:
    """Default cache base directory: `<root>/.house-lint-cache/` (before version-namespacing)."""
    return root / CACHE_DIRNAME


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


def hash_file_content(path: Path) -> str | None:
    """Hash a file's raw bytes for cache-key purposes, or None if it can't be safely cached.

    Reuses `SourceFile`'s nonblocking-read and regular-file guard (via
    `read_regular_file_bytes`) so hashing can't stall on a raced FIFO. Any failure here just
    means this file is treated as a cache miss for this run — `SourceFile`'s own loading still
    runs the real scan and reports a proper `LintError` if warranted.
    """
    try:
        content = read_regular_file_bytes(path, max_bytes=MAX_SOURCE_BYTES)
    except OSError:
        return None
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
    cache_dir: Path, content_hash: str, config_hash: str, *, relative_path: str, debug: bool = False
) -> CachedFileResult | None:
    """Return the cached result for this (content, config) pair, or None on a miss.

    A missing entry (the common case — nothing has cached this file/config pair yet) is a
    silent miss. An entry that exists but can't be read or parsed is also treated as a miss —
    a stale or corrupted cache entry must never fail a scan, only fall back to re-analyzing —
    but that case is unusual enough to report under `--debug`, matching how other best-effort
    I/O in this codebase (e.g. `scan_file`'s internal-error path) stays silent by default but
    diagnosable on request.
    """
    path = _entry_path(cache_dir, content_hash, config_hash)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        if debug:
            print(f"debug: cache read failed for {relative_path}: {exc}", file=sys.stderr)
        return None
    try:
        payload = json.loads(raw)
        suppressed_count = payload["suppressed_count"]
        files_scanned = payload["files_scanned"]
        # Dataclasses don't enforce annotations at runtime, so a corrupted-but-valid-JSON entry
        # (e.g. `"suppressed_count": "1"`) would otherwise construct successfully here and only
        # fail later when the caller accumulates it (`suppressed_count += cached.suppressed_count`
        # in `cli.py`), turning what should be a graceful cache miss into an internal-error exit.
        if not isinstance(suppressed_count, int) or isinstance(suppressed_count, bool):
            raise TypeError("suppressed_count must be an int")
        if not isinstance(files_scanned, int) or isinstance(files_scanned, bool):
            raise TypeError("files_scanned must be an int")
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
        if debug:
            print(f"debug: cache entry for {relative_path} is corrupted: {exc}", file=sys.stderr)
        return None


def _write_self_ignore_marker(base: Path, *, debug: bool = False) -> None:
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
    marker = base / ".gitignore"
    try:
        if not marker.exists():
            marker.write_text("*\n", encoding="utf-8")
    except OSError as exc:
        if debug:
            print(f"debug: cache self-ignore marker write failed: {exc}", file=sys.stderr)


def _write_version_dir_marker(cache_dir: Path, *, debug: bool = False) -> None:
    """Mark `cache_dir` as a house-lint-owned version directory, best-effort.

    `_prune_stale_version_dirs` only deletes sibling directories carrying this marker — without
    it, a `--cache-dir` pointed at a pre-existing shared directory (e.g. `~/.cache`) would have
    every unrelated sibling directory recursively deleted on the first cache write, since nothing
    would distinguish "an old house-lint version directory" from "someone else's data."
    """
    marker = cache_dir / _VERSION_DIR_MARKER
    try:
        if not marker.exists():
            marker.write_text("", encoding="utf-8")
    except OSError as exc:
        if debug:
            print(f"debug: cache version-dir marker write failed: {exc}", file=sys.stderr)


def _prune_stale_version_dirs(cache_dir: Path, *, debug: bool = False) -> None:
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
    except OSError:
        return
    for sibling in siblings:
        if not (sibling / _VERSION_DIR_MARKER).is_file():
            continue
        try:
            shutil.rmtree(sibling)
        except OSError as exc:
            if debug:
                print(f"debug: cache prune of stale version dir failed: {exc}", file=sys.stderr)


def prepare_cache_dir(cache_dir: Path, *, self_ignore: bool, debug: bool = False) -> None:
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
        if debug:
            print(f"debug: cache directory could not be created: {exc}", file=sys.stderr)
        return
    _write_version_dir_marker(cache_dir, debug=debug)
    if self_ignore:
        _write_self_ignore_marker(cache_dir.parent, debug=debug)


def prune_stale_cache_dirs(cache_dir: Path, *, debug: bool = False) -> None:
    """Sweep superseded namespaces, once per scan and only after a real write has happened.

    Kept separate from `prepare_cache_dir` so that a run which writes nothing — every file a
    cache hit — never deletes anything. That matters because the deletion is not race-free: a
    concurrent house-lint process on a different version or build, sharing a `--cache-dir`, can
    have its in-progress namespace removed. Tying the sweep to "this run actually wrote an
    entry" keeps that window as narrow as it was before the per-run bookkeeping was hoisted out
    of `write_cached_result`.
    """
    _prune_stale_version_dirs(cache_dir, debug=debug)


def write_cached_result(
    cache_dir: Path,
    content_hash: str,
    config_hash: str,
    result: CachedFileResult,
    *,
    debug: bool = False,
) -> None:
    """Write a cache entry, best-effort. A failed write must never fail the scan itself —
    but is reported under `--debug`, so `house-lint check --debug` can diagnose "why isn't
    caching working" for a broken cache directory or permissions issue.

    Assumes `prepare_cache_dir` has already run for this `cache_dir`; if it has not (or failed),
    the write simply fails and is reported under `--debug` like any other write failure.

    Writes atomically (temp file + `os.replace`) so an interrupted process or two concurrent
    house-lint runs writing the same entry can never leave a partially-written, corrupted file
    in place of a real one. The temp file is named per-PID, so a failure part-way through would
    otherwise strand a file no later run could ever recognise or clean up — hence the unlink on
    the error path.
    """
    path = _entry_path(cache_dir, content_hash, config_hash)
    payload = {
        "findings": [_finding_to_payload(finding) for finding in result.findings],
        "errors": [_error_to_payload(err) for err in result.errors],
        "suppressed_count": result.suppressed_count,
        "files_scanned": result.files_scanned,
    }
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        if debug:
            print(f"debug: cache write failed: {exc}", file=sys.stderr)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


__all__ = [
    "CACHE_DIRNAME",
    "CachedFileResult",
    "code_identity",
    "default_cache_base",
    "hash_effective_config",
    "hash_file_content",
    "prepare_cache_dir",
    "prune_stale_cache_dirs",
    "read_cached_result",
    "versioned_cache_dir",
    "write_cached_result",
]
