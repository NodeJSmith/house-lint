"""Whole-run scan pipeline: discovery, the file phase, and result assembly."""

from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from house_lint.cache import (
    CachedFileResult,
    CacheReporter,
    default_cache_base,
    default_cache_base_is_safe,
    hash_effective_config,
    hash_source_content,
    prepare_cache_dir,
    prune_stale_cache_dirs,
    read_cached_result,
    versioned_cache_dir,
    write_cached_result,
)
from house_lint.config import (
    CompiledPerFileIgnores,
    DetectorInput,
    LintConfig,
    compile_per_file_ignores,
    per_file_enabled_rules,
    selected_detector_inputs,
)
from house_lint.discovery import DiscoveryError, DiscoveryResult, discover_files
from house_lint.results import Finding, LintError, ScanResult
from house_lint.scanner import FileScanResult, open_source, scan_source
from house_lint.source import SourceFile

CacheKey: TypeAlias = tuple[str, str]


@dataclass(frozen=True)
class FilePhaseResult:
    """The file phase's totals for the run. No `SourceFile` outlives its loop iteration."""

    findings: tuple[Finding, ...]
    errors: tuple[LintError, ...]
    suppressed_count: int
    files_scanned: int


class RunCache:
    """One run's use of the cache: when it reads, when it writes, and when it cleans up.

    The cache functions called here report failures through `reporter` instead of raising, so a
    cache failure costs caching, never the scan. Every decision that depends on an earlier one in
    the same run lives on this object.
    """

    def __init__(
        self, cache_dir: Path, *, self_ignore: bool, read_enabled: bool, debug: bool
    ) -> None:
        self.cache_dir = cache_dir
        self.self_ignore = self_ignore
        self.read_enabled = read_enabled
        self.debug = debug
        self.reporter = CacheReporter(debug=debug)
        # Named for what it checks, not for whether caching happens: `read_enabled` (--no-cache)
        # and a mid-run write failure also govern that. Only the default base is vetted, because
        # it is the only base whose path the scanned project itself controls.
        self.base_is_safe = not self_ignore or default_cache_base_is_safe(cache_dir)
        self.wrote_entry = False
        self.writes_failed = False

    def prepare(self) -> None:
        """Set up the cache directory, or report why this run won't use it."""
        if self.base_is_safe:
            prepare_cache_dir(self.cache_dir, self_ignore=self.self_ignore, reporter=self.reporter)
            return
        self.reporter.failure(
            f"caching disabled: the default cache directory {self.cache_dir} or its parent "
            f"is a symlink"
        )

    def lookup(self, key: CacheKey | None, *, relative_path: str) -> CachedFileResult | None:
        """Return this file's replayable cached result, if there is one."""
        if key is None or not self.base_is_safe or not self.read_enabled:
            return None
        content_hash, config_hash = key
        cached = read_cached_result(
            self.cache_dir,
            content_hash,
            config_hash,
            relative_path=relative_path,
            reporter=self.reporter,
        )
        # A cached *error* is not replayable under `--debug`: the traceback is printed by
        # `scan_source`, which a hit skips. Re-scanning those files keeps `--debug` output
        # independent of cache state, at the cost of re-analyzing the few files that failed —
        # clean files still hit the cache, so the diagnostic mode stays fast.
        if cached is not None and self.debug and cached.errors:
            return None
        return cached

    def store(
        self, key: CacheKey | None, file_result: FileScanResult, *, relative_path: str
    ) -> None:
        """Write one scanned file's result, unless this run has already ruled writes out.

        Writes aren't gated on `read_enabled`: --no-cache only disables reads, so a run under it
        still leaves the cache warm for the next one.
        """
        # No key means an unhashable file (non-regular, unreadable, or oversized). That is never
        # cached and is not a cache failure, so it must stay apart from the circuit breaker below:
        # one such file must not disable caching for the rest of the run.
        if key is None or not self.base_is_safe:
            return
        # A cache directory that rejected one write rejects every later one for the rest of this
        # process (unwritable, full, or removed mid-run by a concurrent prune). Retrying per file
        # would mean up to MAX_DISCOVERED_FILES pointless attempts and one near-duplicate --debug
        # line each, burying the single fact worth reporting.
        if self.writes_failed:
            return
        content_hash, config_hash = key
        written = write_cached_result(
            self.cache_dir,
            content_hash,
            config_hash,
            CachedFileResult(
                file_result.findings,
                file_result.errors,
                file_result.suppressed_count,
                file_result.files_scanned,
            ),
            self_ignore=self.self_ignore,
            reporter=self.reporter,
        )
        if written:
            self.wrote_entry = True
            return
        self.writes_failed = True
        # Through the reporter rather than a bare print, so every cache diagnostic in the pipeline
        # goes through one object. The failing write above has already reported, so this lands on
        # the `--debug`-only branch — which is where a follow-on "and here is what that failure
        # means for the rest of the run" line belongs.
        self.reporter.failure(
            f"cache writes disabled for the rest of this run after the first failure "
            f"(at {relative_path})"
        )

    def finish(self) -> None:
        """Sweep superseded namespaces, only if this run contributed an entry of its own."""
        if self.wrote_entry:
            prune_stale_cache_dirs(self.cache_dir, reporter=self.reporter)


def run_scan(
    paths: tuple[Path, ...],
    *,
    root: Path,
    config_path: Path | None,
    config: LintConfig,
    use_gitignore: bool,
    read_cache: bool,
    cache_dir: Path | None,
    debug: bool,
) -> ScanResult:
    """Run the complete pipeline, retaining all completed-file results.

    `cache_dir` is the location as the user gave it, or None for house-lint's own default. A given
    path means the user chose it, never that it was vetted: it skips the self-ignore marker and
    the symlink check.
    """
    cache_base = default_cache_base(root) if cache_dir is None else cache_dir.expanduser().resolve()
    resolved_cache_dir = versioned_cache_dir(cache_base)
    try:
        discovered = discover_files(
            root,
            include=config.include,
            explicit=paths,
            excludes=config.exclude,
            use_gitignore=use_gitignore,
        )
    except DiscoveryError as exc:
        return ScanResult(
            root,
            config_path,
            config.enabled_rules,
            0,
            0,
            errors=(exc.error,),
        )
    cache = RunCache(
        resolved_cache_dir,
        self_ignore=cache_dir is None,
        read_enabled=read_cache,
        debug=debug,
    )
    files = run_file_phase(discovered, root=root, config=config, cache=cache, debug=debug)
    return ScanResult(
        root,
        config_path,
        config.enabled_rules,
        files.files_scanned,
        discovered.files_skipped,
        files.findings,
        files.suppressed_count,
        files.errors,
    )


def run_file_phase(
    discovered: DiscoveryResult,
    *,
    root: Path,
    config: LintConfig,
    cache: RunCache,
    debug: bool,
) -> FilePhaseResult:
    """Load, look up, scan, and store each discovered file, preparing and finishing the cache.

    A process-boundary failure ends the loop early, keeping every result completed before it.
    """
    if discovered.files:
        # Only when there is something to scan — so a run that discovers nothing neither creates
        # a cache directory in the project nor reports on one it was never going to use.
        cache.prepare()
    detector_inputs = selected_detector_inputs(config)
    compiled_per_file_ignores = compile_per_file_ignores(config.per_file_ignores)
    results: list[FileScanResult | CachedFileResult] = []
    for path in discovered.files:
        relative = path.relative_to(root).as_posix()
        # Pattern matching runs on the file's resolved location, reporting on the spelling the
        # user typed. `discovered.files` preserves the argument as given, so an accepted path
        # containing `..` — `src/../tests/a.py`, with both directories present — reaches
        # `relative` as `src/../tests/a.py`, which a configured `"tests/**"` never matches:
        # house-lint then runs a rule the config disabled for everything under `tests/`.
        #
        # Resolved, not collapsed lexically. A purely lexical `..` collapse is only correct when
        # no traversed component is a symlink: with `link/ -> x/y/`, the OS reads `link/../foo.py`
        # as `x/foo.py` while the lexical form reads `foo.py`, so a pattern written for the
        # file's real location silently stops matching. Resolving cannot drift from the file
        # actually opened, and it is already the identity discovery itself uses — `selected` is
        # keyed by resolved path, which is what makes a symlink and its target deduplicate. A
        # per-file-ignore therefore follows the file, not the spelling that reached it.
        match_relative = discovered.resolved_paths[path].relative_to(root).as_posix()
        file_enabled_rules, file_detector_inputs = _resolve_file_rules(
            match_relative, config, detector_inputs, compiled_per_file_ignores
        )
        # The file is read exactly once per scan, here. Everything below — the cache key, the
        # detectors, and the entry written back — derives from that one buffer, so a cache entry
        # can never describe bytes that were not the ones scanned.
        loaded = open_source(
            path,
            root=root,
            # Indexed, not `.get()`: `DiscoveryResult.files` is built from `resolved_paths`'
            # own keys, so a miss means that invariant broke. Falling back to `None` there would
            # silently re-resolve the path and reopen the symlink-retarget window this threading
            # exists to close, so a `KeyError` is the wanted outcome.
            resolved_path=discovered.resolved_paths[path],
            debug=debug,
        )
        if isinstance(loaded, FileScanResult):
            # open_source only returns a result for a process-boundary abort, which is fatal to
            # the run and never cached.
            results.append(loaded)
            break
        source = loaded
        # Computed unconditionally (even under --no-cache) because `store` still writes on a miss.
        key = _cache_keys(source, config, file_enabled_rules)
        cached = cache.lookup(key, relative_path=relative)
        if cached is not None:
            results.append(cached)
            continue
        file_result = scan_source(
            source,
            enabled_rules=file_enabled_rules,
            detector_inputs=file_detector_inputs,
            debug=debug,
        )
        results.append(file_result)
        if file_result.stop:
            # A stop result is a non-deterministic process-boundary failure, not a reproducible
            # scan outcome, so it must never be replayed from cache on a later hit.
            break
        cache.store(key, file_result, relative_path=relative)
    cache.finish()
    return FilePhaseResult(
        tuple(finding for result in results for finding in result.findings),
        discovered.errors + tuple(err for result in results for err in result.errors),
        sum(result.suppressed_count for result in results),
        sum(result.files_scanned for result in results),
    )


def _cache_keys(
    source: SourceFile, config: LintConfig, file_enabled_rules: tuple[str, ...]
) -> CacheKey | None:
    """Compute this file's (content_hash, config_hash) cache key, or None if the file can't be
    safely hashed — that just means this file's result is never cached, not a failure.

    The content hash comes from `source.content_bytes`: the single buffer the detectors will
    analyze. Deriving the key from the same bytes as the findings is what makes a cache entry
    honest — it cannot describe content that was never scanned under that key.
    """
    content_hash = hash_source_content(source.content_bytes)
    if content_hash is None:
        return None
    config_hash = hash_effective_config(
        file_enabled_rules, config.hsl101, config.hsl102, config.hsl103, filename=source.path.name
    )
    return content_hash, config_hash


def _resolve_file_rules(
    match_relative: str,
    config: LintConfig,
    detector_inputs: tuple[DetectorInput, ...],
    compiled_per_file_ignores: CompiledPerFileIgnores,
) -> tuple[tuple[str, ...], tuple[DetectorInput, ...]]:
    """Return this file's (enabled_rules, detector_inputs), narrowed by per-file-ignores.

    Suppression handling only flags pragmas naming a disabled rule (see
    apply_suppressions/_collect_claims in suppressions.py) — it does not filter candidate
    findings by enabled_rules. detector_inputs must be recomputed here so a per-file-ignored
    rule's detector never runs for this file; skipping this recompute would let its findings
    leak through unfiltered.
    """
    if not compiled_per_file_ignores:
        return config.enabled_rules, detector_inputs
    file_enabled_rules = per_file_enabled_rules(
        config.enabled_rules, compiled_per_file_ignores, match_relative
    )
    if file_enabled_rules == config.enabled_rules:
        return file_enabled_rules, detector_inputs
    return file_enabled_rules, selected_detector_inputs(config, enabled_rules=file_enabled_rules)


__all__ = ["run_scan"]
