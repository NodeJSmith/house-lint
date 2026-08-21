---
task_id: "T03"
title: "Add integration test and update documentation"
status: "done"
depends_on: ["T01", "T02"]
implements: ["FR#3", "AC#1", "AC#2", "AC#3", "AC#4", "AC#5"]
---

## Summary

Add an end-to-end integration test that runs house-lint with `extend-select = ["HSL101"]` and no
token config, confirming built-in families produce findings. Update all three documentation files
to reflect the new built-in defaults, the `separator` field replacing `hash`, and the changed
effective cap. Run the full test suite, pyright, and ruff to confirm everything passes.

## Target Files

- modify: `tests/integration/test_cli.py`
- modify: `docs/configuration.md`
- modify: `README.md`
- modify: `docs/rules.md`
- read: `design/specs/003-built-in-token-defaults/design.md`

## Prompt

In `tests/integration/test_cli.py`:

1. Add an integration test (follow the existing test patterns in this file): create a temp
   project with a `pyproject.toml` containing only `[tool.house-lint]` and
   `extend-select = ["HSL101"]` (no `[tool.house-lint.rules.HSL101]` table at all). Write a
   sample `.py` file with `# AC1 FR#2a T05 KI-001 WP03` in a comment. Run house-lint and assert
   findings for AC1, FR#2a, T05, KI-001, and WP03 (AC#1).

2. Add a second integration test: same setup but with a user-defined token family
   (`tokens = [{prefixes = ["JIRA"], scopes = ["comments"], separator = "dash", min_digits = 1}]`).
   Assert both JIRA-NNN and built-in tokens are detected (AC#2).

In `docs/configuration.md`:

3. Rewrite the HSL101 token families section (starting at line 94). The opening paragraph
   currently states "HSL101 has no default token vocabulary" — replace with a description of the
   three built-in families and zero-config usage. Document the `separator` field values replacing
   `hash`. Update the TOML example to use `separator` instead of `hash`. Update "at most 32
   families" to note the effective cap is 32 minus the number of active built-in families (29
   with the current three). Also update the `hash` description in the field reference
   ("``hash`` is ``forbidden``, ``optional``, or ``required``") to describe `separator` with its
   five values.

In `README.md`:

4. Update the HSL101 TOML example (currently shows `hash = "optional"`) to use `separator`.
   Remove or rewrite the claim at line 74 that "HSL101 requires at least one token family
   whenever you select it."

In `docs/rules.md`:

5. Update "Select HSL101 and configure one or more token families" to reflect built-in defaults.
   Update the `hash` field reference. Revise the source-compatibility table — the "Hard-coded
   vocabulary" entry in the Dropped column needs updating since this design reintroduces built-in
   defaults. Check whether the "Generalized" column also needs adjusting.

After all changes:

6. Run `uv run pytest` and confirm all tests pass (AC#3).
7. Run `uv run pyright` and confirm no errors (AC#4).
8. Run `uv run ruff check .` and confirm no errors (AC#5).

## Focus

- The existing integration test at `test_cli.py:597` configures HSL101 with explicit token
  families in TOML — use this as a pattern reference for test structure, but the new test's
  pyproject.toml should have NO `[tool.house-lint.rules.HSL101]` table at all (testing zero-config).
- `docs/configuration.md:113` has exact field descriptions inline ("hash is forbidden, optional,
  or required; min_digits is 1–12; ...") — update the `hash` portion to describe `separator`.
- `docs/rules.md:40` describes HSL101 options inline — update the hash reference there too.
- `docs/rules.md:60` has a source-compatibility table with Dropped/Generalized columns —
  "Hard-coded vocabulary" is listed as Dropped, which this design reverses.

## Verify

- [ ] FR#3: Integration test confirms user tokens + built-ins both produce findings in the same
  run (AC#2 scenario)
- [ ] AC#1: Integration test passes with `extend-select = ["HSL101"]` and no token config,
  detecting AC1, FR#2a, T05, KI-001, WP03
- [ ] AC#2: Integration test passes with user-defined JIRA family alongside built-ins
- [ ] AC#3: `uv run pytest` exits 0
- [ ] AC#4: `uv run pyright` exits 0
- [ ] AC#5: `uv run ruff check .` exits 0
