"""JSON reporters for public result DTOs."""

import json

from house_lint.config import DEFAULT_INCLUDE
from house_lint.reporters.text import zero_file_guidance
from house_lint.results import RuleList, ScanResult


def render_json(
    result: ScanResult,
    *,
    include_is_default: bool = True,
    include: tuple[str, ...] = DEFAULT_INCLUDE,
    explicit_paths: bool = False,
) -> str:
    """Render a schema-v1 check result as one deterministic JSON line.

    On a zero-file scan (see `zero_file_guidance`), adds a `zero_file_diagnostic` key mirroring
    the text reporter's "empty scan" message. This key is a presentation-layer addition on top
    of `ScanResult.to_dict()`, not part of its `schema_version: 1` contract -- it is absent
    whenever the zero-file condition does not hold, which is every other call site's shape today.
    """
    data = result.to_dict()
    if result.is_zero_file_scan:
        guidance = zero_file_guidance(
            result,
            include_is_default=include_is_default,
            include=include,
            explicit_paths=explicit_paths,
        )
        data["zero_file_diagnostic"] = f"empty scan: no Python files selected{guidance}"
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def render_rule_list_json(rules: RuleList) -> str:
    """Render a schema-v1 rule list as one deterministic JSON line."""
    return json.dumps(rules.to_dict(), sort_keys=True, separators=(",", ":"))
