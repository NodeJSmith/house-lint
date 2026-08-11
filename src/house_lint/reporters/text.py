"""Human-readable deterministic result reporters."""

from house_lint.results import RuleList, ScanResult


def render_text(result: ScanResult) -> str:
    """Render scan metadata, visible findings, and a stable final summary."""
    data = result.to_dict()
    lines = [
        f"root: {data['root'] or '<none>'}",
        f"config: {data['config'] or '<none>'}",
        f"enabled rules: {', '.join(data['enabled_rules']) or '<none>'}",
        f"files: scanned {data['files_scanned']}, skipped {data['files_skipped']}",
    ]
    if data["files_scanned"] == 0 and not data["findings"] and not data["errors"]:
        lines.append("empty scan: no Python files selected")
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
