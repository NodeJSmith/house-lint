"""Whole-run scan pipeline: discovery, the file phase, and result assembly."""

import sys
import traceback
from collections import defaultdict
from collections.abc import Mapping
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
    applicable_rules,
    compile_per_file_ignores,
    discovered_languages,
    selected_detector_inputs,
    selected_project_rules,
)
from house_lint.discovery import DiscoveryError, DiscoveryResult, discover_files
from house_lint.language import Language, language_for
from house_lint.project_rule import Location, ProjectCandidate, ProjectRuleFailure
from house_lint.python_source import PythonSource, parse_python
from house_lint.results import (
    Finding,
    LintError,
    ScanResult,
    error,
    internal_error,
)
from house_lint.scanner import FileScanResult, failure_result, open_source, scan_source
from house_lint.source import LoadedFile, LoadFailure
from house_lint.suppressions import project_suppressions

CacheKey: TypeAlias = tuple[str, str]


@dataclass(frozen=True)
class FilePhaseResult:
    """The file phase's totals for the run, and what the project phase needs from it.

    A loaded file outlives its loop iteration only in `project_inputs`, which holds each enabled
    project rule's files by rule ID, in discovery order. It is empty when no project rule is
    enabled. `aborted` means a process-boundary failure ended the loop early.
    """

    findings: tuple[Finding, ...]
    errors: tuple[LintError, ...]
    suppressed_count: int
    files_scanned: int
    project_inputs: Mapping[str, tuple[LoadedFile, ...]]
    aborted: bool


@dataclass(frozen=True)
class ProjectPhaseResult:
    """The project phase's additions to the run's totals."""

    findings: tuple[Finding, ...]
    errors: tuple[LintError, ...]
    suppressed_count: int


class RunCache:
    """One run's use of the cache: when it reads, when it writes, and when it cleans up.

    The cache functions called here report failures through `reporter` instead of raising, so a
    cache failure costs caching, never the scan. Every decision that depends on an earlier one in
    the same run lives on this object.
    """

    def __init__(
        self, cache_dir: Path, *, self_ignore: bool, read_enabled: bool, reporter: CacheReporter
    ) -> None:
        self.cache_dir = cache_dir
        self.self_ignore = self_ignore
        self.read_enabled = read_enabled
        self.reporter = reporter
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

    def lookup(self, key: CacheKey, *, relative_path: str) -> CachedFileResult | None:
        """Return this file's cached result, if this run reads the cache and there is one.

        The caller decides whether to replay it: see the `--debug` rule in `run_file_phase`.
        """
        if not self.base_is_safe or not self.read_enabled:
            return None
        content_hash, config_hash = key
        return read_cached_result(
            self.cache_dir,
            content_hash,
            config_hash,
            relative_path=relative_path,
            reporter=self.reporter,
        )

    def store(self, key: CacheKey, file_result: FileScanResult, *, relative_path: str) -> None:
        """Write one scanned file's result, unless this run has already ruled writes out.

        Writes aren't gated on `read_enabled`: --no-cache only disables reads, so a run under it
        still leaves the cache warm for the next one.
        """
        if not self.base_is_safe:
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
    use_git: bool,
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
            discovered_languages(config.enabled_rules),
            include=config.include,
            explicit=paths,
            excludes=config.exclude,
            use_git=use_git,
        )
    except DiscoveryError as exc:
        return ScanResult(
            root,
            config_path,
            config.enabled_rules,
            0,
            errors=(exc.error,),
        )
    # Built after discovery, so the constructor's symlink check only runs once discovery has
    # succeeded and a discovery error is what gets reported.
    cache = RunCache(
        resolved_cache_dir,
        self_ignore=cache_dir is None,
        read_enabled=read_cache,
        reporter=CacheReporter(debug=debug),
    )
    if discovered.files:
        # Only when there is something to scan — so a run that discovers nothing neither creates
        # a cache directory in the project nor reports on one it was never going to use.
        cache.prepare()
    file_phase = run_file_phase(discovered, root=root, config=config, cache=cache, debug=debug)
    project_phase = run_project_phase(file_phase, config=config, debug=debug)
    cache.finish()
    return ScanResult(
        root,
        config_path,
        config.enabled_rules,
        file_phase.files_scanned,
        file_phase.findings + project_phase.findings,
        file_phase.suppressed_count + project_phase.suppressed_count,
        file_phase.errors + project_phase.errors,
    )


def run_file_phase(
    discovered: DiscoveryResult,
    *,
    root: Path,
    config: LintConfig,
    cache: RunCache,
    debug: bool,
) -> FilePhaseResult:
    """Load, look up, scan, and store each discovered file.

    `cache` arrives prepared, and the caller finishes it.
    A process-boundary failure ends the loop early, keeping every result completed before it.
    """
    detector_inputs = selected_detector_inputs(config)
    compiled_per_file_ignores = compile_per_file_ignores(config.per_file_ignores)
    project_inputs: dict[str, list[LoadedFile]] = {
        rule.id: [] for rule, _options in selected_project_rules(config)
    }
    results: list[FileScanResult | CachedFileResult] = []
    aborted = False
    for file in discovered.files:
        relative = file.path.relative_to(root).as_posix()
        language = language_for(file.path)
        # Discovery selects only files whose language an enabled rule handles.
        assert language is not None
        # Pattern matching runs on the file's resolved location, reporting on the spelling the
        # user typed. `file.path` preserves the argument as given, so an accepted path
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
        match_relative = file.resolved.relative_to(root).as_posix()
        file_enabled_rules, file_detector_inputs = _resolve_file_rules(
            match_relative, language, config, detector_inputs, compiled_per_file_ignores
        )
        # The file is read exactly once per scan, here. Everything below — the cache key, the
        # detectors, and the entry written back — derives from that one buffer, so a cache entry
        # can never describe bytes that were not the ones scanned.
        loaded = open_source(file.path, root=root, resolved_path=file.resolved, debug=debug)
        if isinstance(loaded, FileScanResult):
            # open_source only returns a result for a process-boundary abort, which is fatal to
            # the run and never cached.
            results.append(loaded)
            aborted = True
            break
        if isinstance(loaded, LoadFailure):
            # No `LoadedFile` means no cache key, so a load failure is reported and never cached.
            results.append(failure_result(loaded, debug=debug))
            continue
        # Ahead of the cache lookup and the parse: a hit has the bytes in hand, and a project rule
        # still takes a Python file that fails to parse, since it is given bytes, not a parse.
        for rule_id, inputs in project_inputs.items():
            if rule_id in file_enabled_rules:
                inputs.append(loaded)
        # Only Python has an analysis layer, so a file in any other language gets no parsing, cache
        # entry, file rule, or HSL900. It counts as scanned because project rules can use it.
        if language is not Language.PYTHON:
            results.append(FileScanResult(files_scanned=1))
            continue
        # Computed unconditionally (even under --no-cache) because `store` still writes on a miss.
        key = _cache_keys(loaded, config, file_enabled_rules)
        cached = cache.lookup(key, relative_path=relative)
        # A cached *error* is not replayable under `--debug`: the traceback is printed by
        # `scan_source`, which a hit skips. Re-scanning those files keeps `--debug` output
        # independent of cache state, at the cost of re-analyzing the few files that failed —
        # clean files still hit the cache, so the diagnostic mode stays fast.
        if cached is not None and not (debug and cached.errors):
            results.append(cached)
            continue
        file_result = scan_source(
            loaded,
            enabled_rules=file_enabled_rules,
            detector_inputs=file_detector_inputs,
            debug=debug,
        )
        results.append(file_result)
        if file_result.stop:
            # A stop result is a non-deterministic process-boundary failure, not a reproducible
            # scan outcome, so it must never be replayed from cache on a later hit.
            aborted = True
            break
        cache.store(key, file_result, relative_path=relative)
    return FilePhaseResult(
        tuple(finding for result in results for finding in result.findings),
        discovered.errors + tuple(err for result in results for err in result.errors),
        sum(result.suppressed_count for result in results),
        sum(result.files_scanned for result in results),
        {rule_id: tuple(inputs) for rule_id, inputs in project_inputs.items()},
        aborted,
    )


def run_project_phase(
    file_phase: FilePhaseResult, *, config: LintConfig, debug: bool
) -> ProjectPhaseResult:
    """Run each enabled project rule on its files, then apply pragmas. Nothing here is cached.

    An aborted file phase leaves an arbitrarily partial input, and the run is already reporting an
    internal error, so no rule runs. A rule with no files doesn't run either: an external tool
    handed an empty list of paths could fall back to scanning its working directory.

    A `ProjectRuleFailure` costs that rule's findings and the next rule still runs. Any other
    exception, from the rule or from resolving its pragmas, is a bug at the process boundary, so it
    ends the phase, keeping results so far.
    """
    if file_phase.aborted:
        return ProjectPhaseResult(findings=(), errors=(), suppressed_count=0)
    findings: list[Finding] = []
    errors: list[LintError] = []
    suppressed_count = 0
    sources = PythonSources()
    for rule, options in selected_project_rules(config):
        files = file_phase.project_inputs[rule.id]
        if not files:
            continue
        # The rule's run and its pragma resolution both happen inside the `try`, so a rule's bad
        # output counts as its failure and a rule that fails partway leaves no findings behind.
        try:
            occurrences = expand_candidates(rule.id, rule.run(files, options))
            visible = visible_occurrences(rule.id, occurrences, sources)
        except ProjectRuleFailure as exc:
            errors.append(error("project-rule", "project", "rule-run", str(exc), rule_id=rule.id))
            continue
        except Exception:  # noqa: BLE001 - this is the process-boundary internal-error path.
            if debug:
                traceback.print_exc(file=sys.stderr)
            errors.append(internal_error("project", "rule-run", rule_id=rule.id))
            break
        findings.extend(occurrence.finding for occurrence in visible)
        suppressed_count += len(occurrences) - len(visible)
    return ProjectPhaseResult(
        findings=tuple(findings), errors=tuple(errors), suppressed_count=suppressed_count
    )


@dataclass(frozen=True)
class Occurrence:
    """One location of a project candidate: the finding it reports, and the location it came from.

    The location keeps the file's retained bytes, which pragma resolution parses.
    """

    location: Location
    finding: Finding


class PythonSources:
    """The project phase's parses of retained Python files, at most one per file per run."""

    def __init__(self) -> None:
        # None marks a file that doesn't parse: it has no readable pragmas.
        self.parsed: dict[Path, PythonSource | None] = {}

    def get(self, file: LoadedFile) -> PythonSource | None:
        if file.path not in self.parsed:
            parsed = parse_python(file)
            self.parsed[file.path] = parsed if isinstance(parsed, PythonSource) else None
        return self.parsed[file.path]


def expand_candidates(rule_id: str, candidates: list[ProjectCandidate]) -> list[Occurrence]:
    """Return one occurrence per candidate location, in order.

    Each finding relates every other location of its candidate, so a suppressed location still
    appears in its siblings' `related`.
    """
    occurrences: list[Occurrence] = []
    for candidate in candidates:
        related = tuple(location.to_related() for location in candidate.locations)
        for index, location in enumerate(candidate.locations):
            finding = Finding(
                rule_id,
                location.file.relative_path,
                location.line,
                location.column,
                location.end_line,
                location.end_column,
                candidate.message,
                related[:index] + related[index + 1 :],
            )
            occurrences.append(Occurrence(location, finding))
    return occurrences


def visible_occurrences(
    rule_id: str, occurrences: list[Occurrence], sources: PythonSources
) -> list[Occurrence]:
    """Return the occurrences no pragma in their own file claims, in their original order.

    Only Python files have pragmas. A file that doesn't parse has none readable, so its
    occurrences stay visible; the file phase already reported why.
    """
    by_file: defaultdict[Path, list[Occurrence]] = defaultdict(list)
    for occurrence in occurrences:
        if language_for(occurrence.location.file.path) is Language.PYTHON:
            by_file[occurrence.location.file.path].append(occurrence)
    # By identity: `Occurrence` compares by value, and two occurrences can be equal.
    claimed: set[int] = set()
    for in_file in by_file.values():
        source = sources.get(in_file[0].location.file)
        if source is None:
            continue
        locations = [occurrence.location for occurrence in in_file]
        claimed.update(
            id(in_file[index]) for index in project_suppressions(source, rule_id, locations)
        )
    return [occurrence for occurrence in occurrences if id(occurrence) not in claimed]


def _cache_keys(
    loaded: LoadedFile, config: LintConfig, file_enabled_rules: tuple[str, ...]
) -> CacheKey:
    """Compute this file's (content_hash, config_hash) cache key.

    The content hash comes from `loaded.content`: the single buffer the detectors will analyze.
    Deriving the key from the same bytes as the findings is what makes a cache entry honest — it
    cannot describe content that was never scanned under that key.
    """
    config_hash = hash_effective_config(
        file_enabled_rules, config.rule_options, filename=loaded.path.name
    )
    return hash_source_content(loaded.content), config_hash


def _resolve_file_rules(
    match_relative: str,
    language: Language,
    config: LintConfig,
    detector_inputs: tuple[DetectorInput, ...],
    compiled_per_file_ignores: CompiledPerFileIgnores,
) -> tuple[tuple[str, ...], tuple[DetectorInput, ...]]:
    """Return this file's (enabled_rules, detector_inputs), narrowed to the rules that apply.

    Suppression handling only flags pragmas naming a disabled rule (see
    apply_suppressions/_collect_claims in suppressions.py) — it does not filter candidate
    findings by enabled_rules. detector_inputs must be recomputed when the set narrows, so a rule
    that doesn't apply to this file never runs on it; otherwise its findings would leak through
    unfiltered.
    """
    file_enabled_rules = applicable_rules(
        config.enabled_rules, compiled_per_file_ignores, match_relative, language
    )
    if file_enabled_rules == config.enabled_rules:
        return file_enabled_rules, detector_inputs
    return file_enabled_rules, selected_detector_inputs(config, enabled_rules=file_enabled_rules)


__all__ = ["run_scan"]
