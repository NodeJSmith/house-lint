---
task_id: "T09"
title: "Document and suppress registry.py's intentional lazy imports"
status: "done"
depends_on: []
implements: ["FR#8", "AC#8"]
---

## Target Files

- modify: `src/house_lint/registry.py`

## Prompt

`src/house_lint/registry.py`'s 7 `_hslNNN` dispatch functions each do `from .rules import
<module>` inside the function body — this is exactly the pattern house-lint's own `HSL002` rule
("Lazy imports") flags. It's deliberate (per CLAUDE.md: "a detector function in `_DETECTORS` that
lazily imports each `rules/<name>.py` module — avoids import cost for unused rules"), but there's
no inline documentation of that rationale, and no suppression pragma ready for when self-linting is
eventually turned on for this repo.

house-lint's suppression pragma syntax (from `src/house_lint/suppressions.py:24`):
`# house-lint: <ignore|ignore-next|ignore-file>[<RULE_ID>] - <reason>`. Use `ignore-next` (comment
on its own line, applying to the line that follows) rather than a trailing `ignore` comment on the
same line as the import — a trailing comment with this rationale text pushes every one of the 7
real import lines past this project's 100-character `ruff` line-length limit (measured 114-127
characters depending on module name), so `ignore-next` on its own line is the only form that fits
cleanly without needing a case-by-case wrap decision:

```python
def _hsl001(
    source: SourceFile, options: object, *, limit: int | None = None
) -> list[CandidateFinding]:
    # house-lint: ignore-next[HSL002] - lazy-loaded to avoid import cost for unused rules
    from .rules import llm_cruft

    return llm_cruft.detect(source, options, limit=limit)
```

Apply the same `ignore-next` comment line to all 7 (`_hsl001` through `_hsl004`, `_hsl101` through
`_hsl103`) — the rationale text is identical across all 7 since it's the same reason every time.
Confirm each resulting comment line stays under 100 characters (it will — it's ~91 characters
including the 4-space indent) and run `uv run ruff format --check` to confirm no reformatting is
needed.

Note: this task adds the pragma and rationale only. It does not add a `[tool.house-lint]` self-lint
section to this project's own `pyproject.toml` — that's explicitly out of scope (see
`tasks/context.md`).

## Verify

- [ ] FR#8: `grep -c "house-lint: ignore-next\[HSL002\]" src/house_lint/registry.py` equals 7.
- [ ] AC#8: `uv run ruff format --check src/house_lint/registry.py` passes (comments don't break
      formatting).
- [ ] `uv run pytest -q` reports all tests passing (this is a comment-only change, so this should
      be a no-op confirmation).
