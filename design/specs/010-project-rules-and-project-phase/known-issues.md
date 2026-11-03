# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: A config with no rule handling Python fails every pre-commit run that stages a Python file

Status: open
Recorded: 2026-11-02 (59)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- .pre-commit-hooks.yaml
- src/house_lint/discovery.py

Issue:
With `select = []`, or only TypeScript rules once one exists, `house-lint check` on a directory exits 0 with a zero-file note, but `house-lint check a.py` exits 3. The published hook matches `\.py$` and always passes filenames, so any commit staging a Python file fails.

Why deferred:
The failure is loud and the user can fix it, every shipped rule handles Python, and silently skipping an explicit path would hide a mistyped or wrong-language path.

Recommended follow-up:
Settle the hook's file filter when #39 or #61 adds a rule for another language.

Acceptance criteria:
- A project enabling only TypeScript rules can run the published hook without exit 3 on a commit staging a Python file.

## KI-002: A per-file-ignored file still fails the run when it can't be read

Status: open
Recorded: 2026-11-02 (59)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/pipeline.py

Issue:
Every discovered file is read before anything asks whether a rule wants it, so an unreadable or over-10 MiB file exits 3 even when every rule per-file-ignores it. Enabling a TypeScript rule would bring large generated `.ts` bundles into this.

Why deferred:
The error names the file and `exclude` fixes it, and the rule that a file every rule ignores is still scanned and counted would need a second path through the loop.

Recommended follow-up:
Skip the read for a non-Python file no project rule holds, if oversized generated TypeScript that can't be excluded turns up in a real repo.

Acceptance criteria:
- An oversized `.ts` file that is per-file-ignored for every enabled rule doesn't fail the run.

## KI-003: A new importer of the rule tables needs a matching line in `register_project_rules`

Status: open
Recorded: 2026-11-02 (59)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- tests/conftest.py

Issue:
`register_project_rules` patches `config.PROJECT_RULES`, `config.ORDINARY_RULES`, and `rule_catalog.RULES` by hand. A new module importing a rule table by name would see the shipped empty table under test.

Why deferred:
Only `config.py` imports the tables by name today, and reading them through the module would change production imports to suit a fixture and still leave derived tables patched by hand.

Recommended follow-up:
Revisit if #39 or #60 reshapes how `rule_catalog` exposes its tables.

Acceptance criteria:
- Registering a project rule in a test needs a patch in one place.
