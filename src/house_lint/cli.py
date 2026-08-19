"""Cyclopts process boundary: sequential file discovery loop and result output."""

import sys
import traceback
from pathlib import Path

from cyclopts import App, CycloptsError

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
from house_lint.scanner import scan_file

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


def _scan(
    paths: tuple[Path, ...],
    *,
    root: Path,
    config_path: Path | None,
    config: LintConfig,
    use_gitignore: bool,
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

    findings: list[Finding] = []
    errors = list(discovered.errors)
    detector_inputs = selected_detector_inputs(config)
    compiled_per_file_ignores = compile_per_file_ignores(config.per_file_ignores)
    suppressed_count = 0
    files_scanned = 0
    for path in discovered.files:
        file_enabled_rules = config.enabled_rules
        file_detector_inputs = detector_inputs
        if compiled_per_file_ignores:
            relative = path.relative_to(root).as_posix()
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
        if file_result.stop:
            break
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
        result = _scan(
            tuple(paths or ()),
            root=resolution.root,
            config_path=resolution.config,
            config=lint_config,
            use_gitignore=not no_gitignore,
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
