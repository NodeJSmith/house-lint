"""Human-readable deterministic result reporters."""

from pathlib import Path

from house_lint.config import config_table_name, is_standalone_config
from house_lint.results import RuleList, ScanResult

EMPTY_SCAN_MESSAGE = "empty scan: no Python files selected"
"""The base zero-file-scan sentence, before `zero_file_guidance`'s clause is appended.

Single source of truth for callers building a `zero_file_note` (`cli.py`, and tests asserting
zero-file output) — previously duplicated as a bare string literal in more than one place.
"""


def shadowed_config_note(shadowed: tuple[Path, ...]) -> str:
    """Return the extra clause naming shadowed config files, or `""` when none were shadowed.

    Used by `render_text` to build its `config:` line. `render_json` renders the same
    `shadowed` tuple independently, as a `shadowed_config` list rather than a text clause, so
    the two reporters can never disagree about *which* sources were shadowed even though their
    output shapes differ. Surfaced in the default (non-debug) `config:` line/field, not gated
    behind `--debug`, since a project accumulating an incidentally-named `house-lint.toml`
    alongside `pyproject.toml`'s `[tool.house-lint]` has no other way to learn which one won
    without already knowing to pass `--debug`.
    """
    if not shadowed:
        return ""
    names = ", ".join(str(path) for path in shadowed)
    return f" (shadows {names})"


def zero_file_guidance(
    result: ScanResult,
    *,
    include: tuple[str, ...],
    explicit_paths: bool,
) -> str:
    """Return the extra clause to append to the "empty scan" message, or `""` when suppressed.

    Shared by `render_text` and `render_json` so the two reporters can never describe the same
    zero-file scan differently. Suppressed for the two intentional-empty-scan cases FR#9 names —
    `include = []` explicitly configured, or explicit CLI paths given — in which case the caller
    still shows the base "empty scan" message on its own, just without this guidance.
    `DEFAULT_INCLUDE` is never empty, so a bare `not include` check only ever fires for an
    explicit `include = []`; a typo'd non-empty explicit `include` (FR#8's most common real
    trigger) is untouched by this check and still gets guidance.
    """
    if explicit_paths or not include:
        return ""
    config = result.config
    if config is None:
        return (
            "; no config file found: create one with an include list, "
            "or pass explicit paths (house-lint check <path>)"
        )
    # `config.name` in both branches: an explicit `--config custom.toml` still uses the
    # `[tool.house-lint]` table but is not `pyproject.toml`, and guidance naming a file that
    # isn't involved sends the reader to the wrong place.
    table = config_table_name(is_standalone_config(config))
    return f"; check the include list in {config.name}'s [{table}] table"


def render_text(
    result: ScanResult,
    *,
    zero_file_note: str | None,
    shadowed: tuple[Path, ...] = (),
) -> str:
    """Render scan metadata, visible findings, and a stable final summary.

    `zero_file_note` is the full "empty scan: ..." line to append, or `None` to omit it —
    precomputed by the caller (see `zero_file_guidance`) rather than derived here, so the
    reporter doesn't need `include`/`explicit_paths` just to decide what to print. It has no
    default (unlike `shadowed`) because its two states — "not a zero-file scan" vs. "zero-file
    scan, guidance suppressed" — are not equivalent, and a silently-assumed default could pick
    the wrong one. `shadowed` defaults to `()` safely instead: an omitted value just means
    "nothing shadowed," which is never a misleading assumption.
    """
    data = result.to_dict()
    lines = [
        f"root: {data['root'] or '<none>'}",
        f"config: {data['config'] or '<none>'}{shadowed_config_note(shadowed)}",
        f"enabled rules: {', '.join(data['enabled_rules']) or '<none>'}",
        f"files: scanned {data['files_scanned']}, skipped {data['files_skipped']}",
    ]
    if zero_file_note is not None:
        lines.append(zero_file_note)
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
