---
task_id: "T01"
title: "Extract a single-source-of-truth rule catalog; derive config's rule lists from it"
status: "done"
depends_on: []
implements: ["FR#1", "AC#1"]
---

## Target Files

- create: `src/house_lint/rule_catalog.py`
- modify: `src/house_lint/registry.py`
- modify: `src/house_lint/config.py`
- modify: `src/house_lint/cli.py`
- modify: `src/house_lint/suppressions.py`
- create: `tests/unit/test_rule_catalog.py`
- modify: `tests/unit/test_registry.py`

## Prompt

**The problem being fixed:** `src/house_lint/registry.py` currently defines `RuleMetadata` and a
`_RULES` mapping (one entry per rule ID including `HSL900`) plus `_DETECTORS` (dispatch functions,
one per rule ID *except* `HSL900`). `src/house_lint/config.py` independently hardcodes its own
rule-ID lists: `DEFAULT_SELECT = ("HSL001", "HSL002", "HSL003", "HSL004")` and `ORDINARY_RULES =
frozenset((*DEFAULT_SELECT, "HSL101", "HSL102", "HSL103"))`. These currently agree, and there's
even an existing test assertion checking `ORDINARY_RULES == set(rule_ids()) - {"HSL900"}`
(`tests/unit/test_registry.py:32`) — but that's a value-equality check that could be silently
deleted or drift without anyone noticing at the point a new rule is added. The fix is structural:
make one module the actual source of truth so there is nothing left to keep "in sync."

**Why `config.py` can't just import from `registry.py`:** `registry.py` already imports from
`config.py` (`DetectorInput, DetectorOptions, HSL101Options, HSL102Options, HSL103Options`) to type
its `_DETECTORS` dispatch table. If `config.py` imported rule-ID data back from `registry.py`,
that's a circular import. The fix is a new leaf module with zero internal imports that both
`config.py` and `registry.py` depend on one-directionally.

### 1. Create `src/house_lint/rule_catalog.py`

A leaf module (no imports from other `house_lint` modules — same layering as `results.py` and
`source.py`) containing:

```python
"""Canonical catalog of built-in house rules — the single source of truth for IDs and enablement."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class RuleMetadata:
    """Fixed metadata for one built-in house rule."""

    id: str
    name: str
    description: str
    enablement: str


RULES: Mapping[str, RuleMetadata] = MappingProxyType(
    {
        "HSL001": RuleMetadata("HSL001", "AI-writing cruft", "AI-writing tells", "default"),
        "HSL002": RuleMetadata("HSL002", "Lazy imports", "Imports inside functions", "default"),
        "HSL003": RuleMetadata(
            "HSL003",
            "TYPE_CHECKING position",
            "TYPE_CHECKING blocks followed by imports",
            "default",
        ),
        "HSL004": RuleMetadata(
            "HSL004", "Constants position", "Constants after definitions", "default"
        ),
        "HSL101": RuleMetadata("HSL101", "Spec tokens", "Configured spec tokens", "opt-in"),
        "HSL102": RuleMetadata(
            "HSL102", "File length", "Files exceeding the line limit", "opt-in"
        ),
        "HSL103": RuleMetadata("HSL103", "Exception names", "Exception binding names", "opt-in"),
        "HSL900": RuleMetadata(
            "HSL900", "Suppression diagnostics", "Invalid suppression pragmas", "always"
        ),
    }
)

DEFAULT_SELECT: tuple[str, ...] = tuple(
    rule.id for rule in RULES.values() if rule.enablement == "default"
)
ORDINARY_RULES: frozenset[str] = frozenset(
    rule.id for rule in RULES.values() if rule.enablement != "always"
)


def is_known_rule(rule_id: str) -> bool:
    """Return whether a rule ID belongs to the fixed built-in registry."""
    return rule_id in RULES


def rule_ids() -> tuple[str, ...]:
    """Return built-in rule IDs in their stable display order."""
    return tuple(RULES)


def rule_metadata(rule_id: str) -> RuleMetadata:
    """Return display metadata for one known built-in rule."""
    return RULES[rule_id]


__all__ = [
    "DEFAULT_SELECT",
    "ORDINARY_RULES",
    "RULES",
    "RuleMetadata",
    "is_known_rule",
    "rule_ids",
    "rule_metadata",
]
```

Note this deliberately drops the `ownership_scope` field that `RuleMetadata` currently has in
`registry.py` — it was verified during the audit to have zero readers anywhere in `src/` or
`tests/`, so it's not being carried forward into the rebuilt dataclass.

### 2. Update `src/house_lint/registry.py`

Remove the local `RuleMetadata` class, `_RULES` mapping, and the `is_known_rule`/`rule_ids`/
`rule_metadata` functions — they now live in `rule_catalog.py`. Import what dispatch still needs:
`from .rule_catalog import ORDINARY_RULES` (for the check below). Keep `Detector`, `_DETECTORS`,
`detect_candidates`, and the 7 `_hslNNN` wrapper functions as-is.

Add an import-time consistency check right after `_DETECTORS` is defined:

```python
if set(_DETECTORS) != set(ORDINARY_RULES):
    raise RuntimeError(
        "registry._DETECTORS is out of sync with rule_catalog.ORDINARY_RULES — "
        "every ordinary rule needs exactly one dispatch function"
    )
```

Use a plain `if`/`raise`, not `assert` — asserts are stripped under `python -O` and this check must
always run. `registry.py`'s `__all__` should now only export `detect_candidates` (the metadata
accessors move with `rule_catalog`).

### 3. Update `src/house_lint/config.py`

Replace the hardcoded `DEFAULT_SELECT`/`ORDINARY_RULES` definitions with an import:
`from .rule_catalog import DEFAULT_SELECT, ORDINARY_RULES`. Everything downstream in `config.py`
that references these names keeps working unchanged since the values are identical.

### 4. Update call sites

- `src/house_lint/cli.py:20` currently does
  `from house_lint.registry import detect_candidates, rule_ids, rule_metadata`. Split it:
  `from house_lint.registry import detect_candidates` and
  `from house_lint.rule_catalog import rule_ids, rule_metadata`.
- `src/house_lint/suppressions.py:19` currently does `from .registry import is_known_rule`. Change
  to `from .rule_catalog import is_known_rule`.

### 5. Split `tests/unit/test_registry.py`

The existing test `test_registry_has_fixed_metadata_and_explicit_dispatch` mixes pure-metadata
assertions with dispatch assertions. Split it:

- Create `tests/unit/test_rule_catalog.py` with the metadata-only assertions: `rule_ids()` returns
  the 8 IDs in order, `rule_metadata("HSL900").enablement == "always"`, `is_known_rule("HSL001")` /
  `not is_known_rule("HSL999")`, `DEFAULT_SELECT == ("HSL001", "HSL002", "HSL003", "HSL004")`. Since
  `ORDINARY_RULES` and `rule_ids()` now both derive from the same `RULES` mapping, the old
  `ORDINARY_RULES == set(rule_ids()) - {"HSL900"}` assertion becomes definitionally true — you can
  keep it as one line confirming the derivation is wired correctly, or drop it; either is fine.
- Keep `tests/unit/test_registry.py` focused on dispatch: `detect_candidates` behavior (the
  `test_registry_has_fixed_metadata_and_explicit_dispatch` test's `detect_candidates(...)`
  assertion, and `test_dispatch_receives_selected_typed_options_without_lint_config`, both already
  present). Add a new test confirming the import-time check actually protects something — e.g.
  `test_detectors_cover_every_ordinary_rule` asserting `set(registry._DETECTORS) ==
  set(rule_catalog.ORDINARY_RULES)` directly (this exercises the same invariant the `RuntimeError`
  guards, as a normal test in addition to the import-time enforcement — belt-and-suspenders here is
  fine since the RuntimeError is the real enforcement and this is just a readable regression test).
  Update this file's imports (`registry.RuleMetadata` no longer exists; `"RuleMetadata" not in
  registry.__all__"` assertion should become moot/removed since it's no longer in `registry.py` at
  all — check `rule_catalog.__all__` instead if you want an equivalent assertion there).

## Verify

- [ ] FR#1: `uv run pytest tests/unit/test_rule_catalog.py tests/unit/test_registry.py -v` passes.
- [ ] AC#1: `grep -rn "ownership_scope" src/ tests/` returns no matches.
- [ ] AC#1: Manually confirm the import-time check works — temporarily comment out one line of
      `_DETECTORS` in `registry.py` (e.g. the `"HSL002": _hsl002,` entry) and run
      `uv run python -c "import house_lint.registry"` — it must raise `RuntimeError`. Restore the
      line afterward and confirm `uv run pytest -q` is green again.
- [ ] `grep -rn "DEFAULT_SELECT\s*=\|ORDINARY_RULES\s*=" src/house_lint/*.py` shows these are
      defined exactly once (in `rule_catalog.py`), not re-defined in `config.py`.
- [ ] `uv run pytest -q` reports all tests passing.
- [ ] `uv run pyright` (strict, `src/` only) is clean.
