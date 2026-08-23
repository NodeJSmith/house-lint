"""JSON reporters for public result DTOs."""

import json
from pathlib import Path

from house_lint.results import RuleList, ScanResult


def render_json(
    result: ScanResult,
    *,
    zero_file_note: str | None,
    shadowed: tuple[Path, ...] = (),
) -> str:
    """Render a schema-v1 check result as one deterministic JSON line.

    `zero_file_note` (see `house_lint.reporters.text.zero_file_guidance`), when not `None`,
    is added under a `zero_file_diagnostic` key mirroring the text reporter's "empty scan"
    message. When config resolution shadowed another config source, adds a `shadowed_config` key
    listing the shadowed paths. Both keys are presentation-layer additions on top of
    `ScanResult.to_dict()`, not part of its `schema_version: 1` contract -- each is absent
    whenever its triggering condition does not hold, which is every other call site's shape
    today.
    """
    data = result.to_dict()
    if zero_file_note is not None:
        data["zero_file_diagnostic"] = zero_file_note
    if shadowed:
        data["shadowed_config"] = [str(path) for path in shadowed]
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def render_rule_list_json(rules: RuleList) -> str:
    """Render a schema-v1 rule list as one deterministic JSON line."""
    return json.dumps(rules.to_dict(), sort_keys=True, separators=(",", ":"))
