---
task_id: "T04"
title: "Update documentation and add pathspec dependency pin"
status: "planned"
depends_on: ["T03"]
implements: ["AC#10"]
---

## Summary

Update the documentation to reflect the fixed divergence and add the pathspec upper-bound dependency pin. Remove the under-linting bullet from `docs/configuration.md`, regenerate the divergence-rate table, update `CLAUDE.md`'s gitignore gotcha section, and pin `pathspec>=0.12,<2` in `pyproject.toml`.

## Target Files

- modify: `docs/configuration.md` — remove under-linting bullet, update divergence section, regenerate rate table
- modify: `CLAUDE.md` — update gitignore divergence notes
- modify: `pyproject.toml` — add pathspec upper-bound pin
- read: `design/specs/002-per-directory-gitignore-matcher/design.md` — Documentation Updates section

## Prompt

### Update `docs/configuration.md` (lines 49-66):

1. **Remove the under-linting bullet** at lines 52-53 ("Under-linting. A directory-only negation may fail to re-include..."). If the over-linting divergence was also fixed (T03's AC#9 outcome), remove that bullet too (lines 51-52).

2. **Update the divergence summary** at line 49 ("Two divergences are known..."): Adjust the count and framing based on which divergences remain. If both are fixed, replace the section with a statement that the parity suites verify fidelity and note the divergence rates. If only under-linting is fixed, update "Two divergences" to "One divergence" and keep the over-linting bullet.

3. **Remove or update the paragraph at lines 54-56** that recants the over-linting-only guarantee. If both divergences are fixed, this paragraph is no longer needed. If only the under-linting is fixed, update to reflect that the under-linting defect is resolved.

4. **Regenerate the divergence-rate table** (lines 58-63): Run `CI=1 uv run pytest -s tests/integration/test_gitignore_fuzz.py` (if not already run in T03) and update the table with the new rates. The under-linting column should show "never" for all distributions. Update the prose at line 64 as needed.

5. **Update the architecture paragraph** at line 66 that references `design/research/2026-08-20-gitignore-style-exclusion-inclusion/`: Update to note that the architectural fix (per-directory matcher stack) has been implemented.

### Update `CLAUDE.md` (gitignore gotcha section):

1. Update the bullet about divergences ("Two divergences are known...") to reflect the current state — one or zero divergences remain.
2. Remove the text "Do not assume the old 'always errs toward over-linting' guarantee — it was false and has been removed" — this is now historical, not a current gotcha.
3. Keep the references to `docs/configuration.md` and the research directory.

### Update `pyproject.toml`:

Change the pathspec dependency from `pathspec>=0.12` to `pathspec>=0.12,<2`. This caps the dependency so a future breaking major version bump cannot silently reach end users who install house-lint via `pip`/`pipx`/`uv tool install`.

## Focus

- The divergence-rate table must be regenerated from the actual fuzz output, not hand-edited. Use `CI=1 uv run pytest -s tests/integration/test_gitignore_fuzz.py` — the `-s` flag prints the rates.
- The documentation must match the actual test outcomes from T03. If T03 left the over-linting xfail in place, the docs should still mention the over-linting divergence.
- `CLAUDE.md` is the project's CLAUDE.md (checked into the repo), not the user's global one. Read the current content before editing — the gitignore section is in the "Gotchas" area.
- The `pyproject.toml` change is one line. Verify the existing dependency format before editing.

## Verify

- [ ] AC#10: `grep 'pathspec' pyproject.toml` shows `pathspec>=0.12,<2`
