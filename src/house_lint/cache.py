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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from house_lint import __version__
from house_lint.config import HSL101Options, HSL102Options, HSL103Options
from house_lint.results import Finding, LintError
from house_lint.source import MAX_SOURCE_BYTES, read_regular_file_bytes

CACHE_DIRNAME = ".house-lint-cache"


def default_cache_base(root: Path) -> Path:
    """Default cache base directory: `<root>/.house-lint-cache/` (before version-namespacing)."""
    return root / CACHE_DIRNAME


def versioned_cache_dir(base: Path) -> Path:
    """Version-namespace a cache base directory, so an upgrade invalidates stale entries.

    Applies uniformly to the default base and to a user-supplied `--cache-dir` override —
    the override changes *where* the cache lives, not whether it's still safe across upgrades.
    """
    return base / __version__


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


def _finding_to_payload(finding: Finding) -> dict[str, Any]:
    data = finding.to_dict()
    del data["path"]
    return data


def _finding_from_payload(data: dict[str, Any], *, path: str) -> Finding:
    return Finding(path=path, **data)


def _error_to_payload(err: LintError) -> dict[str, Any]:
    data = err.to_dict()
    del data["path"]
    return data


def _error_from_payload(data: dict[str, Any], *, path: str) -> LintError:
    return LintError(path=path, **data)


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
        return CachedFileResult(
            findings=tuple(
                _finding_from_payload(item, path=relative_path) for item in payload["findings"]
            ),
            errors=tuple(
                _error_from_payload(item, path=relative_path) for item in payload["errors"]
            ),
            suppressed_count=payload["suppressed_count"],
            files_scanned=payload["files_scanned"],
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
    """
    marker = base / ".gitignore"
    try:
        if not marker.exists():
            marker.write_text("*\n", encoding="utf-8")
    except OSError as exc:
        if debug:
            print(f"debug: cache self-ignore marker write failed: {exc}", file=sys.stderr)


def _prune_stale_version_dirs(cache_dir: Path, *, debug: bool = False) -> None:
    """Remove sibling version directories under `cache_dir`'s base, best-effort.

    `versioned_cache_dir` namespaces the cache by `__version__` so an upgrade invalidates stale
    entries, but nothing else ever deletes the old version's directory — left alone, upgrades
    accumulate `<base>/<old-version>/`, `<base>/<older-version>/`, etc. forever. Run only here,
    at the point a cache entry is actually about to be written (not on every read), so a plain
    read-only invocation of this house-lint version never triggers deletion of another version's
    directory. This narrows, but does not eliminate, the risk window: a *concurrent* process
    actively writing under a different version during an overlapping run can still have its
    directory removed by this call — best-effort here means "safe to fail," not "race-free."
    """
    base = cache_dir.parent
    try:
        siblings = [child for child in base.iterdir() if child.is_dir() and child != cache_dir]
    except OSError:
        return
    for sibling in siblings:
        try:
            shutil.rmtree(sibling)
        except OSError as exc:
            if debug:
                print(f"debug: cache prune of stale version dir failed: {exc}", file=sys.stderr)


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

    Writes atomically (temp file + `os.replace`) so an interrupted process or two concurrent
    house-lint runs writing the same entry can never leave a partially-written, corrupted file
    in place of a real one.
    """
    path = _entry_path(cache_dir, content_hash, config_hash)
    payload = {
        "findings": [_finding_to_payload(finding) for finding in result.findings],
        "errors": [_error_to_payload(err) for err in result.errors],
        "suppressed_count": result.suppressed_count,
        "files_scanned": result.files_scanned,
    }
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _prune_stale_version_dirs(cache_dir, debug=debug)
        _write_self_ignore_marker(cache_dir.parent, debug=debug)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        if debug:
            print(f"debug: cache write failed: {exc}", file=sys.stderr)


__all__ = [
    "CACHE_DIRNAME",
    "CachedFileResult",
    "default_cache_base",
    "hash_effective_config",
    "hash_file_content",
    "read_cached_result",
    "versioned_cache_dir",
    "write_cached_result",
]
