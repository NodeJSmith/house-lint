"""Cyclopts process boundary: sequential file discovery loop and result output."""

import sys
import traceback
from pathlib import Path

from cyclopts import App, CycloptsError

from house_lint.cache import (
    CachedFileResult,
    default_cache_base,
    hash_effective_config,
    hash_file_content,
    prepare_cache_dir,
    prune_stale_cache_dirs,
    read_cached_result,
    versioned_cache_dir,
    write_cached_result,
)
from house_lint.config import (
    ConfigError,
    LintConfig,
    compile_per_file_ignores,
    default_config,
    load_config,
    per_file_enabled_rules,
    selected_detector_inputs,
)
from house_lint.discovery import DiscoveryError, discover_files, resolve_project
from house_lint.reporters import (
    render_json,
    render_rule_list_json,
    render_rule_list_text,
    render_text,
)
from house_lint.results import (
    Finding,
    LintError,
    RuleInfo,
    RuleList,
    ScanResult,
    error,
    internal_error,
)
from house_lint.rule_catalog import rule_ids, rule_metadata
from house_lint.scanner import FileScanResult, scan_file

app = App(name="house-lint", help="Opinionated Python house-style linter.")


def _flatten_ids(values: list[str] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    return tuple(item.strip() for value in values for item in value.split(","))


def _render_error(err: LintError) -> str:
    """Render the stable error context required by the text CLI contract."""
    location = f"{err.path}: " if err.path else ""
    return f"error: {location}[{err.code} {err.phase}/{err.operation}] {err.message}"


def _result_for_config_error(
    exc: ConfigError, *, root: Path | None = None, config: Path | None = None
) -> ScanResult:
    return ScanResult(root, config, (), 0, 0, errors=(error("config", "config", "load", str(exc)),))


def _write_result(
    result: ScanResult, output_format: str, *, errors_to_stderr: bool, debug: bool
) -> None:
    print(render_json(result) if output_format == "json" else render_text(result))
    if errors_to_stderr:
        for err in result.errors:
            print(_render_error(err), file=sys.stderr)
    if debug:
        for err in result.to_dict()["errors"]:
            print(
                f"debug: {err['kind']} error during {err['phase']}/{err['operation']}: "
                f"{err['message']}",
                file=sys.stderr,
            )


def _write_config_error(result: ScanResult, output_format: str) -> None:
    if output_format == "json":
        print(render_json(result))
        return
    for err in result.errors:
        print(_render_error(err), file=sys.stderr)


def _exit_code(result: ScanResult) -> int:
    if any(err.kind == "internal" for err in result.errors):
        return 4
    if result.errors:
        return 3
    if result.findings:
        return 1
    return 0


def _requested_format(arguments: list[str]) -> str:
    """Return the explicitly requested output format before Cyclopts parses it."""
    for index, argument in enumerate(arguments):
        if argument.startswith("--format="):
            return argument.partition("=")[2]
        if argument == "--format" and index + 1 < len(arguments):
            return arguments[index + 1]
    return "text"


def _cache_keys(
    path: Path, config: LintConfig, file_enabled_rules: tuple[str, ...]
) -> tuple[str, str] | tuple[None, None]:
    """Compute this file's (content_hash, config_hash) cache key, or (None, None) if the file
    can't be safely hashed — that just means this file's result is never cached, not a failure.
    """
    content_hash = hash_file_content(path)
    if content_hash is None:
        return None, None
    config_hash = hash_effective_config(
        file_enabled_rules, config.hsl101, config.hsl102, config.hsl103, filename=path.name
    )
    return content_hash, config_hash


def _write_cache_entry(
    cache_dir: Path,
    content_hash: str | None,
    config_hash: str | None,
    file_result: FileScanResult,
    *,
    debug: bool,
) -> bool:
    """Persist `file_result` for future runs — unless it can't be hashed, or it's a `stop=True`
    internal-error abort. `stop` signals a non-deterministic process-boundary failure, not a
    reproducible scan outcome, so it must never be replayed from cache on a later hit.

    Returns whether a write was attempted, which is what gates the once-per-scan prune: a run
    that writes nothing must not delete another process's namespace.
    """
    if content_hash is None or config_hash is None or file_result.stop:
        return False
    write_cached_result(
        cache_dir,
        content_hash,
        config_hash,
        CachedFileResult(
            file_result.findings,
            file_result.errors,
            file_result.suppressed_count,
            file_result.files_scanned,
        ),
        debug=debug,
    )
    return True


def _scan(
    paths: tuple[Path, ...],
    *,
    root: Path,
    config_path: Path | None,
    config: LintConfig,
    use_gitignore: bool,
    read_cache: bool,
    cache_dir: Path,
    cache_self_ignore: bool,
    debug: bool,
) -> ScanResult:
    """Run the complete file pipeline, retaining all completed-file results."""
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

    if discovered.files:
        # Once per scan, and only when there is something to scan — so a run that discovers
        # nothing never creates a cache directory in the project.
        prepare_cache_dir(cache_dir, self_ignore=cache_self_ignore, debug=debug)

    findings: list[Finding] = []
    errors = list(discovered.errors)
    detector_inputs = selected_detector_inputs(config)
    compiled_per_file_ignores = compile_per_file_ignores(config.per_file_ignores)
    suppressed_count = 0
    files_scanned = 0
    wrote_cache_entry = False
    for path in discovered.files:
        relative = path.relative_to(root).as_posix()
        file_enabled_rules = config.enabled_rules
        file_detector_inputs = detector_inputs
        if compiled_per_file_ignores:
            file_enabled_rules = per_file_enabled_rules(
                config.enabled_rules, compiled_per_file_ignores, relative
            )
            if file_enabled_rules != config.enabled_rules:
                # Suppression handling only flags pragmas naming a disabled rule (see
                # apply_suppressions/_collect_claims in suppressions.py) — it does not filter
                # candidate findings by enabled_rules. detector_inputs must be recomputed here
                # so a per-file-ignored rule's detector never runs for this file; skipping this
                # recompute would let its findings leak through unfiltered.
                file_detector_inputs = selected_detector_inputs(
                    config, enabled_rules=file_enabled_rules
                )
        # Computed unconditionally (even under --no-cache) because a write still happens on a
        # miss regardless of read_cache — --no-cache only disables the read below, not the
        # write further down, so the hashes are needed either way.
        content_hash, config_hash = _cache_keys(path, config, file_enabled_rules)
        cached = (
            read_cached_result(
                cache_dir, content_hash, config_hash, relative_path=relative, debug=debug
            )
            if read_cache and content_hash is not None and config_hash is not None
            else None
        )
        if cached is not None:
            findings.extend(cached.findings)
            errors.extend(cached.errors)
            suppressed_count += cached.suppressed_count
            files_scanned += cached.files_scanned
            continue
        file_result = scan_file(
            path,
            root=root,
            enabled_rules=file_enabled_rules,
            detector_inputs=file_detector_inputs,
            debug=debug,
        )
        findings.extend(file_result.findings)
        errors.extend(file_result.errors)
        suppressed_count += file_result.suppressed_count
        files_scanned += file_result.files_scanned
        # Re-hash post-scan to narrow a TOCTOU race: scan_file() reads the file's bytes
        # independently of the read that produced content_hash above, so if the file changed
        # in between (e.g. an editor autosaving mid-scan), file_result reflects content that
        # content_hash no longer describes. Writing it under that stale key would let a later
        # run replay these findings against content they were never derived from. This closes
        # the common case, not every case — content that changes and then reverts to the exact
        # original bytes within this window would still slip through undetected, but that's not
        # a practical concern here. The scan result itself is still correct and used for
        # findings/errors above — only the cache write is skipped.
        if content_hash is None or hash_file_content(path) == content_hash:
            wrote_cache_entry |= _write_cache_entry(
                cache_dir, content_hash, config_hash, file_result, debug=debug
            )
        elif debug:
            print(
                f"debug: skip cache write for {relative}: content changed during scan",
                file=sys.stderr,
            )
        if file_result.stop:
            break
    if wrote_cache_entry:
        prune_stale_cache_dirs(cache_dir, debug=debug)
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


@app.command
def check(
    paths: list[Path] | None = None,
    *,
    config: Path | None = None,
    root: Path | None = None,
    format: str = "text",
    select: list[str] | None = None,
    ignore: list[str] | None = None,
    extend_select: list[str] | None = None,
    extend_ignore: list[str] | None = None,
    no_gitignore: bool = False,
    no_cache: bool = False,
    cache_dir: Path | None = None,
    debug: bool = False,
) -> int:
    """Scan configured roots or explicit Python paths."""
    if format not in {"text", "json"}:
        result = _result_for_config_error(ConfigError("--format must be text or json"))
        _write_config_error(result, "text")
        return 2
    cli_select = _flatten_ids(select)
    cli_ignore = _flatten_ids(ignore)
    cli_extend_select = _flatten_ids(extend_select)
    cli_extend_ignore = _flatten_ids(extend_ignore)
    resolved_root: Path | None = None
    resolved_config: Path | None = None
    try:
        # Best-effort fallback for error reporting: resolve_project() below performs
        # this same resolution and overwrites these on success, but if it raises before
        # returning, the except handlers still need a resolved root/config to report.
        resolved_root = root.expanduser().resolve() if root is not None else None
        resolved_config = config.expanduser().resolve() if config is not None else None
        resolution = resolve_project(root=root, config=config)
        resolved_root = resolution.root
        resolved_config = resolution.config
        lint_config = (
            default_config(
                cli_select=cli_select,
                cli_ignore=cli_ignore,
                cli_extend_select=cli_extend_select,
                cli_extend_ignore=cli_extend_ignore,
            )
            if resolution.config is None
            else load_config(
                resolution.config,
                cli_select=cli_select,
                cli_ignore=cli_ignore,
                cli_extend_select=cli_extend_select,
                cli_extend_ignore=cli_extend_ignore,
            )
        )
        cache_base = (
            cache_dir.expanduser().resolve()
            if cache_dir is not None
            else default_cache_base(resolution.root)
        )
        resolved_cache_dir = versioned_cache_dir(cache_base)
        result = _scan(
            tuple(paths or ()),
            root=resolution.root,
            config_path=resolution.config,
            config=lint_config,
            use_gitignore=not no_gitignore,
            read_cache=not no_cache,
            cache_dir=resolved_cache_dir,
            cache_self_ignore=cache_dir is None,
            debug=debug,
        )
    except ConfigError as exc:
        if resolved_root is None:
            resolved_root = resolved_config.parent if resolved_config is not None else Path.cwd()
        result = _result_for_config_error(exc, root=resolved_root, config=resolved_config)
        _write_config_error(result, format)
        return 2
    except Exception:  # noqa: BLE001 - this is the process-boundary internal-error path.
        result = ScanResult(
            resolved_root,
            resolved_config,
            (),
            0,
            0,
            errors=(internal_error("cli", "scan"),),
        )
        if debug:
            traceback.print_exc(file=sys.stderr)
    code = _exit_code(result)
    _write_result(
        result,
        format,
        errors_to_stderr=format == "text" and code >= 3,
        debug=debug,
    )
    return code


@app.command
def rules(*, format: str = "text") -> int:
    """List every built-in rule and its enablement mode."""
    rule_list = RuleList(
        tuple(
            RuleInfo(metadata.id, metadata.name, metadata.description, metadata.enablement)
            for rule_id in rule_ids()
            for metadata in (rule_metadata(rule_id),)
        )
    )
    if format == "json":
        print(render_rule_list_json(rule_list))
        return 0
    if format == "text":
        print(render_rule_list_text(rule_list))
        return 0
    print("error: --format must be text or json", file=sys.stderr)
    return 2


def main() -> None:
    """Run Cyclopts with command return values mapped to process exit status."""
    try:
        app(result_action="sys_exit", exit_on_error=False, print_error=False)
    except CycloptsError as exc:
        result = _result_for_config_error(ConfigError(str(exc)))
        _write_config_error(result, _requested_format(sys.argv[1:]))
        raise SystemExit(2) from exc


__all__ = ["app", "check", "main", "rules"]
