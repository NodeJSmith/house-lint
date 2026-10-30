"""Whole-run scan pipeline: discovery, the per-file loop, the cache, and result assembly."""

from pathlib import Path

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
from house_lint.discovery import DiscoveryError, discover_files
from house_lint.results import Finding, ScanResult
from house_lint.scanner import FileScanResult, open_source, scan_source
from house_lint.source import SourceFile


def _cache_keys(
    source: SourceFile, config: LintConfig, file_enabled_rules: tuple[str, ...]
) -> tuple[str, str] | tuple[None, None]:
    """Compute this file's (content_hash, config_hash) cache key, or (None, None) if the file
    can't be safely hashed — that just means this file's result is never cached, not a failure.

    The content hash comes from `source.content_bytes`: the single buffer the detectors will
    analyze. Deriving the key from the same bytes as the findings is what makes a cache entry
    honest — it cannot describe content that was never scanned under that key.
    """
    content_hash = hash_source_content(source.content_bytes)
    if content_hash is None:
        return None, None
    config_hash = hash_effective_config(
        file_enabled_rules, config.hsl101, config.hsl102, config.hsl103, filename=source.path.name
    )
    return content_hash, config_hash


def _persist_cache_entry(
    cache_dir: Path,
    content_hash: str,
    config_hash: str,
    file_result: FileScanResult,
    *,
    self_ignore: bool,
    reporter: CacheReporter,
) -> bool:
    """Persist one scanned file's result. Returns whether it was durably written.

    Called only for a file that already has a cache key, so the returned `False` carries one
    meaning — the write failed — and is safe to latch a circuit breaker on. An earlier version
    of this helper also returned `False` for "this file isn't cacheable", which would have let a
    single unhashable file disable caching for the rest of the run; `_scan` screens that case out
    before calling here, and the two must not be recombined.
    """
    return write_cached_result(
        cache_dir,
        content_hash,
        config_hash,
        CachedFileResult(
            file_result.findings,
            file_result.errors,
            file_result.suppressed_count,
            file_result.files_scanned,
        ),
        self_ignore=self_ignore,
        reporter=reporter,
    )


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
    """Run the complete file pipeline, retaining all completed-file results.

    `cache_dir` is the location as the user gave it, or None for house-lint's own default. A given
    path means the user chose it, never that it was vetted: it skips the self-ignore marker and
    the symlink check.
    """
    cache_self_ignore = cache_dir is None
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

    cache_reporter = CacheReporter(debug=debug)
    # Named for what it checks, not for whether caching happens: `read_cache` (--no-cache) and a
    # mid-run write failure below also govern that. `cache_self_ignore` is true exactly when
    # `resolved_cache_dir` sits under house-lint's own default base, which is the only base
    # whose path the scanned project itself controls and so the only one worth vetting.
    cache_base_is_safe = not cache_self_ignore or default_cache_base_is_safe(resolved_cache_dir)
    if discovered.files:
        # Both branches run once per scan, and only when there is something to scan — so a run
        # that discovers nothing neither creates a cache directory in the project nor reports on
        # one it was never going to use.
        if cache_base_is_safe:
            prepare_cache_dir(
                resolved_cache_dir, self_ignore=cache_self_ignore, reporter=cache_reporter
            )
        else:
            cache_reporter.failure(
                f"caching disabled: the default cache directory {resolved_cache_dir} or its parent "
                f"is a symlink"
            )

    findings: list[Finding] = []
    errors = list(discovered.errors)
    detector_inputs = selected_detector_inputs(config)
    compiled_per_file_ignores = compile_per_file_ignores(config.per_file_ignores)
    suppressed_count = 0
    files_scanned = 0
    wrote_cache_entry = False
    cache_writes_failed = False
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
            # Indexed, not `.get()`: `DiscoveryResult.files` is built from `resolved_paths`'
            # own keys, so a miss means that invariant broke. Falling back to `None` there would
            # silently re-resolve the path and reopen the symlink-retarget window this threading
            # exists to close, so a `KeyError` is the wanted outcome.
            path,
            root=root,
            resolved_path=discovered.resolved_paths[path],
            debug=debug,
        )
        if isinstance(loaded, FileScanResult):
            # open_source only returns a result for a process-boundary abort, which is fatal to
            # the run and never cached.
            errors.extend(loaded.errors)
            files_scanned += loaded.files_scanned
            break
        source = loaded
        # Computed unconditionally (even under --no-cache) because a write still happens on a
        # miss regardless of read_cache — --no-cache only disables the read below, not the
        # write further down, so the hashes are needed either way.
        content_hash, config_hash = _cache_keys(source, config, file_enabled_rules)
        cached = (
            read_cached_result(
                resolved_cache_dir,
                content_hash,
                config_hash,
                relative_path=relative,
                reporter=cache_reporter,
            )
            if (
                cache_base_is_safe
                and read_cache
                and content_hash is not None
                and config_hash is not None
            )
            else None
        )
        # A cached *error* is not replayable under `--debug`: the traceback is printed by
        # `scan_source`, which a hit skips, so the first debug run showed the exception and every
        # identical one after it showed only the structured line. Re-scanning those files keeps
        # `--debug` output independent of cache state, at the cost of re-analyzing the few files
        # that failed — clean files still hit the cache, so the diagnostic mode stays fast.
        if cached is not None and not (debug and cached.errors):
            findings.extend(cached.findings)
            errors.extend(cached.errors)
            suppressed_count += cached.suppressed_count
            files_scanned += cached.files_scanned
            continue
        file_result = scan_source(
            source,
            enabled_rules=file_enabled_rules,
            detector_inputs=file_detector_inputs,
            debug=debug,
        )
        findings.extend(file_result.findings)
        errors.extend(file_result.errors)
        suppressed_count += file_result.suppressed_count
        files_scanned += file_result.files_scanned
        if file_result.stop:
            # A stop result is a non-deterministic process-boundary failure, not a reproducible
            # scan outcome, so it must never be replayed from cache on a later hit.
            break
        if content_hash is None or config_hash is None:
            # Unhashable file (non-regular, unreadable, or oversized). Never cached, and not a
            # cache failure — it must not trip the circuit breaker below.
            continue
        if not cache_base_is_safe:
            # Decided once, before the loop, and already reported there.
            continue
        if cache_writes_failed:
            # A cache directory that rejected one write rejects every later one for the rest of
            # this process (unwritable, full, or removed mid-run by a concurrent prune). Retrying
            # per file would mean up to MAX_DISCOVERED_FILES pointless attempts and one
            # near-duplicate --debug line each, burying the single fact worth reporting.
            continue
        if _persist_cache_entry(
            resolved_cache_dir,
            content_hash,
            config_hash,
            file_result,
            self_ignore=cache_self_ignore,
            reporter=cache_reporter,
        ):
            wrote_cache_entry = True
        else:
            cache_writes_failed = True
            # Through the reporter rather than a bare print, so every cache diagnostic in the
            # pipeline goes through one object. The failing write above has already reported,
            # so this lands on the `--debug`-only branch — which is where a follow-on
            # "and here is what that failure means for the rest of the run" line belongs.
            cache_reporter.failure(
                f"cache writes disabled for the rest of this run after the first failure "
                f"(at {relative})"
            )
    if wrote_cache_entry:
        prune_stale_cache_dirs(resolved_cache_dir, reporter=cache_reporter)
    return ScanResult(
        root,
        config_path,
        config.enabled_rules,
        files_scanned,
        discovered.files_skipped,
        tuple(findings),
        suppressed_count,
        tuple(errors),
    )


__all__ = ["run_scan"]
