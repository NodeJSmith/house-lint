"""Human-readable deterministic result reporters."""

from house_lint.config import DEFAULT_INCLUDE, is_standalone_config
from house_lint.results import RuleList, ScanResult


def zero_file_guidance(
    result: ScanResult,
    *,
    include_is_default: bool,
    include: tuple[str, ...],
    explicit_paths: bool,
) -> str:
    """Return the extra clause to append to the "empty scan" message, or `""` when suppressed.

    Shared by `render_text` and `render_json` so the two reporters can never describe the same
    zero-file scan differently. Suppressed for the two intentional-empty-scan cases FR#9 names —
    `include = []` explicitly configured (`not include_is_default and not include`) or explicit
    CLI paths given — in which case the caller still shows the base "empty scan" message on its
    own, just without this guidance. `include_is_default` (not bare `include` emptiness) is what
    decides the first case: the default include (`DEFAULT_INCLUDE`) is never empty today, but
    checking presence-of-key rather than that coincidence is what keeps a typo'd explicit
    `include = ["test"]` (FR#8's most common real trigger) from being silently swallowed by the
    same check.
    """
    if explicit_paths or (not include_is_default and not include):
        return ""
    config = result.config
    if config is None:
        return (
            "; no config file found: create one with an include list, "
            "or pass explicit paths (house-lint <path>)"
        )
    if is_standalone_config(config):
        return f"; check the include list in {config.name}'s [house-lint] table"
    return "; check the include list in pyproject.toml's [tool.house-lint] table"


def render_text(
    result: ScanResult,
    *,
    include_is_default: bool = True,
    include: tuple[str, ...] = DEFAULT_INCLUDE,
    explicit_paths: bool = False,
) -> str:
    """Render scan metadata, visible findings, and a stable final summary."""
    data = result.to_dict()
    lines = [
        f"root: {data['root'] or '<none>'}",
        f"config: {data['config'] or '<none>'}",
        f"enabled rules: {', '.join(data['enabled_rules']) or '<none>'}",
        f"files: scanned {data['files_scanned']}, skipped {data['files_skipped']}",
    ]
    if result.is_zero_file_scan:
        guidance = zero_file_guidance(
            result,
            include_is_default=include_is_default,
            include=include,
            explicit_paths=explicit_paths,
        )
        lines.append(f"empty scan: no Python files selected{guidance}")
    for finding in data["findings"]:
        location = (
            f"{finding['path']}:{finding['line']}:{finding['column']}:"
            if finding["line"] is not None
            else f"{finding['path']}:"
        )
        lines.append(f"{location} {finding['rule_id']} {finding['message']}")
    summary = data["summary"]
    lines.append(
        "summary: "
        f"{summary['finding_count']} findings, {summary['error_count']} errors, "
        f"{summary['suppressed_count']} suppressed"
    )
    return "\n".join(lines)


def render_rule_list_text(rules: RuleList) -> str:
    """Render all stable rule metadata one rule per line."""
    return "\n".join(
        f"{rule.id} [{rule.enablement}] {rule.name}: {rule.description}" for rule in rules.rules
    )
