"""Cyclopts process boundary: argument parsing, config loading, and result output."""

import sys
import traceback
from pathlib import Path

from cyclopts import App, CycloptsError

from house_lint.config import ConfigError, LintConfig, default_config, load_config
from house_lint.discovery import resolve_project
from house_lint.pipeline import run_scan
from house_lint.reporters import (
    EMPTY_SCAN_MESSAGE,
    render_json,
    render_rule_list_json,
    render_rule_list_text,
    render_text,
    zero_file_guidance,
)
from house_lint.results import (
    LintError,
    RuleInfo,
    RuleList,
    ScanResult,
    error,
    internal_error,
)
from house_lint.rule_catalog import rule_ids, rule_metadata

app = App(
    name="house-lint", help="Lint checks that catch the habits AI coding assistants leave behind."
)


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
    result: ScanResult,
    output_format: str,
    *,
    errors_to_stderr: bool,
    debug: bool,
    zero_file_note: str | None,
    shadowed: tuple[Path, ...] = (),
) -> None:
    rendered = (
        render_json(
            result,
            zero_file_note=zero_file_note,
            shadowed=shadowed,
        )
        if output_format == "json"
        else render_text(
            result,
            zero_file_note=zero_file_note,
            shadowed=shadowed,
        )
    )
    print(rendered)
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
        print(render_json(result, zero_file_note=None))
        return
    for err in result.errors:
        print(_render_error(err), file=sys.stderr)


def _exit_code(result: ScanResult, *, fail_on_empty: bool) -> int:
    if any(err.kind == "internal" for err in result.errors):
        return 4
    if result.errors:
        return 3
    if result.findings:
        return 1
    if fail_on_empty and result.is_zero_file_scan:
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
    fail_on_empty: bool = False,
) -> int:
    """Scan configured roots or explicit Python paths.

    `fail_on_empty` makes a zero-file scan exit 1 instead of 0 — for CI pipelines that gate on
    the exit code and would otherwise see a zero-file scan (e.g. a typo'd `include`, or a rename
    that orphaned the configured paths) pass silently. Off by default: an empty scan is a normal,
    non-error outcome for interactive/exploratory use (a brand-new project, a directory that
    hasn't grown any Python files yet).
    """
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
    resolved_shadowed: tuple[Path, ...] = ()
    # Default until the try block below resolves the real config — read back by the
    # `_write_result` call after the try/except, which needs a value even when an exception
    # aborts the try body before `lint_config` is reassigned (e.g. a config load that raises
    # something other than `ConfigError`). `LintConfig()`'s own default (`include` DEFAULT_INCLUDE)
    # is the correct fallback: it's exactly what the zero-file diagnostic should assume when the
    # real resolution never completed.
    lint_config = LintConfig()
    try:
        # Best-effort fallback for error reporting: resolve_project() below performs
        # this same resolution and overwrites these on success, but if it raises before
        # returning, the except handlers still need a resolved root/config to report.
        resolved_root = root.expanduser().resolve() if root is not None else None
        resolved_config = config.expanduser().resolve() if config is not None else None
        resolution = resolve_project(root=root, config=config)
        resolved_root = resolution.root
        resolved_config = resolution.config
        resolved_shadowed = resolution.shadowed
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
        result = run_scan(
            tuple(paths or ()),
            root=resolution.root,
            config_path=resolution.config,
            config=lint_config,
            use_gitignore=not no_gitignore,
            read_cache=not no_cache,
            cache_dir=cache_dir,
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
    code = _exit_code(result, fail_on_empty=fail_on_empty)
    zero_file_note: str | None = None
    if result.is_zero_file_scan:
        guidance = zero_file_guidance(
            result,
            include=lint_config.include,
            explicit_paths=bool(paths),
        )
        zero_file_note = f"{EMPTY_SCAN_MESSAGE}{guidance}"
    _write_result(
        result,
        format,
        errors_to_stderr=format == "text" and code >= 3,
        debug=debug,
        zero_file_note=zero_file_note,
        shadowed=resolved_shadowed,
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
