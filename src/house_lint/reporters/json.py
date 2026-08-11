"""JSON reporters for public result DTOs."""

import json

from house_lint.results import RuleList, ScanResult


def render_json(result: ScanResult) -> str:
    """Render a schema-v1 check result as one deterministic JSON line."""
    return json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":"))


def render_rule_list_json(rules: RuleList) -> str:
    """Render a schema-v1 rule list as one deterministic JSON line."""
    return json.dumps(rules.to_dict(), sort_keys=True, separators=(",", ":"))
