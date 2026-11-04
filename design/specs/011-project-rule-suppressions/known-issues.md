# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: A project rule can report a location in a file outside its own input

Status: open
Recorded: 2026-11-03 (60)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/pipeline.py
- src/house_lint/project_rule.py

Issue:
`Location.file` can be any `LoadedFile`, and the project phase doesn't check that it is one of the files handed to `rule.run`. `suppressions.claims_for` scans pragmas with only that rule enabled, which matches the file phase only for files in the rule's input. A rule that builds its own `LoadedFile`, or reports a file per-file-ignores removed from its input, gets a pragma honoured that the file phase reported as "unused suppression for disabled rule".

Why deferred:
No project rule exists yet, and the consequence is a silently honoured pragma, not data loss.

Recommended follow-up:
If #39's jscpd wrapper maps tool output back to files on its own, reject a location whose file isn't (by identity) one of the rule's inputs.

Acceptance criteria:
- A rule reporting a location in a file outside its input fails visibly, pinned by a test.

## KI-002: A crash while resolving pragmas is reported as the rule's `rule-run` error

Status: open
Recorded: 2026-11-03 (60)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/pipeline.py

Issue:
Pragma resolution runs inside the rule's `try` in `run_project_phase`, so a bug there is reported as `internal_error("project", "rule-run", rule_id=...)`. Without `--debug`, triage points at the rule rather than at pragma resolution.

Why deferred:
The crash is loud either way, `--debug` prints the traceback, and the ledger ratified this boundary.

Recommended follow-up:
If project-phase crashes need triaging from CI logs without `--debug`, report a crash outside `rule.run` with its own operation name.

Acceptance criteria:
- A crash in pragma resolution names a resolution operation, pinned by updating `test_a_crash_while_resolving_pragmas_is_that_rules_internal_error`.

## KI-003: The project phase keeps every parsed file until it ends

Status: open
Recorded: 2026-11-03 (60)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/pipeline.py

Issue:
`PragmaClaims` holds one `PythonSource` (text, tokens, tree) per Python file with a project-rule finding until the phase returns, so that each file is parsed at most once per run.

Why deferred:
The maintainer's repos are small enough that this is negligible, and scoping the memo per rule would break "at most one parse per file per run".

Recommended follow-up:
Revisit after #39 lands, if its findings span thousands of files and memory becomes measurable.

Acceptance criteria:
- Peak memory of a project-phase run is measured on a large repo and found acceptable, or the memo releases trees it no longer needs.

## KI-004: A trailing pragma on a method's decorator line belongs to the enclosing class

Status: open
Recorded: 2026-11-03 (60)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/analysis.py
- docs/suppressions.md

Issue:
A trailing `ignore` on a decorator line inside a class keeps its file-rule owner, the class (D5's last pin). It doesn't claim the decorated method's occurrence, but it silently claims any occurrence owned by the class itself, such as one starting on a blank or comment line directly in the class body. With no unused verdict for project rules, nothing reports it.

Why deferred:
It needs an unusual coincidence of placement, and it follows from ratified D5 and unchanged file-phase ownership.

Recommended follow-up:
If it bites, document it in docs/suppressions.md or make a decorator-line pragma naming a project rule misplaced.

Acceptance criteria:
- The behaviour is either documented or diagnosed.

## KI-005: `summary.suppressed_count` has no written definition

Status: open
Recorded: 2026-11-03 (60)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- README.md

Issue:
`suppressed_count` now sums suppressed file-phase findings and suppressed project-rule occurrences. No doc defines the field; that gap predates this change.

Why deferred:
The maintainer is the only consumer, and the unit (suppressed findings) is unchanged in substance.

Recommended follow-up:
Define it in README's JSON paragraph when an outside consumer appears.

Acceptance criteria:
- README states what `summary.suppressed_count` counts.

## KI-006: Text `(also at ...)` isn't delimited

Status: open
Recorded: 2026-11-03 (60)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/reporters/text.py

Issue:
The text reporter appends `(also at ...)` after a free-text message, so a script can't reliably split it off.

Why deferred:
Text output is for people; scripts read the JSON reporter, which carries `related` as structured data.

Recommended follow-up:
Delimit the suffix if a consumer starts parsing text output.

Acceptance criteria:
- A script can separate a text finding's message from its `(also at ...)` suffix.
