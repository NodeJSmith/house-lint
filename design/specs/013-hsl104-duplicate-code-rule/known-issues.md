# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: A `.py` symlink to a target without a `.py` suffix dropped out of HSL104

Status: resolved — fixed before ship
Recorded: 2026-11-09 (39)
Source: other
Reason not fixed now: needs-decision (when recorded)
Affected files:
- src/house_lint/rules/duplicate_code.py
- src/house_lint/jscpd.py

Issue:
house-lint decides a file's language from the path as reported (`language_for`), so a symlink `c.py -> c.txt` is Python and enters HSL104's input. D3 (point 4) has the adapter hand jscpd the resolved path, and jscpd picks a file's format from that path's extension, so it silently skips `c.txt`. Probed: three copies, one reached only through such a symlink, report nothing, with no error.

Why deferred:
Handing jscpd the reported spelling instead contradicts D3's ratified resolved-path identity, which keeps a `..` or symlinked spelling from being named differently than jscpd reports it. The case needs a `.py` symlink to a non-`.py` file, which no known project has.

Recommended follow-up:
If it ever matters: pass jscpd the reported absolute path for files whose resolved suffix differs and map its names back on that, or report such a file as a rule failure instead of skipping it silently.

Acceptance criteria:
- A copy reached through a `.py` symlink to a non-`.py` target either counts toward its cluster or produces a visible error.

Resolution:
`jscpd.find_clones` now fails before running jscpd when any given (resolved) path lacks a `.py` suffix, naming the file (ship-time challenge, Finding 5, and the pre-commit review of its fix). This covers every target suffix, including unknown and missing ones, which jscpd drops without counting under any format.

## KI-002: The jscpd analysis timeout is a fixed 120 seconds

Status: open
Recorded: 2026-11-09 (39)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/jscpd.py

Issue:
D6 made the analysis timeout a constant, and a timeout fails HSL104 every run. With explicit paths the project rules still analyze the full scope, and nothing in the project phase is cached, so a scope whose jscpd run outgrows 120 seconds can only turn HSL104 off.

Why deferred:
D6 ratified a constant with about 15x headroom on hassette (7.7s full run); adding an option later is non-breaking.

Recommended follow-up:
Expose the timeout as an `HSL104` option if a repo ever approaches the limit.

Acceptance criteria:
- A scope that needs more than 120 seconds can raise the limit in config.

## KI-003: jscpd reads the files from disk separately from house-lint's read

Status: open
Recorded: 2026-11-09 (39)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/jscpd.py
- src/house_lint/rules/duplicate_code.py

Issue:
Pragmas are resolved from the bytes house-lint loaded, while jscpd reads the paths itself. A file saved between the two reads can put a finding where its pragma doesn't claim it, or the reverse, for that run.

Why deferred:
Assumed D9 (from parent D9) accepts it; the project phase caches nothing, so rerunning fixes it.

Recommended follow-up:
If it's ever seen, mention in docs/rules.md that an edit during a run can misplace one run's finding.

Acceptance criteria:
- A non-reproducible HSL104 finding traced to a mid-run edit is explained in the docs.

## KI-004: jscpd's report is read whole into memory

Status: open
Recorded: 2026-11-09 (39)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/jscpd.py

Issue:
`find_clones` decodes the whole report, and clustering builds an object per fragment. With `min_tokens` and `min_lines` near 1 on a large repo, the report could be large enough to exhaust memory, which ends the process with no house-lint message.

Why deferred:
It needs floors far below the defaults; the report's size at such settings was not measured.

Recommended follow-up:
Measure the report size at low floors on a large repo; if it's a risk, check the report's size before reading and fail naming `min_tokens`.

Acceptance criteria:
- Very low floors on a large repo either work or fail with a message naming the option.

## KI-005: Mapping jscpd's names back assumes it echoes each path byte for byte

Status: open
Recorded: 2026-11-09 (39)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/jscpd.py

Issue:
The lookup matches jscpd's reported name against `str(path)` exactly. On a platform where jscpd respells a path (Windows separators or prefixes, macOS Unicode normalization), every run would fail with "jscpd reported a file it was not given".

Why deferred:
CI and every known user run Linux, the failure is loud, and a fix couldn't be tested without non-Linux CI.

Recommended follow-up:
If macOS or Windows joins CI, normalize jscpd's names before the lookup and test it there.

Acceptance criteria:
- HSL104's tests pass on each platform in CI.

## KI-006: An internal error loading one unnamed file stops every project rule

Status: open
Recorded: 2026-11-09 (39)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/pipeline.py

Issue:
In `widen_project_inputs`, a process-boundary failure loading an extra file gives every project rule empty input and reports an internal error, so a hook naming one file can fail on a file the user didn't touch.

Why deferred:
It matches how the file phase treats the same failure, and it only happens on a bug, which should be visible.

Recommended follow-up:
If it's ever seen in practice, report the error and continue with that file left out.

Acceptance criteria:
- A process-boundary failure on an unnamed file leaves the project rules' findings for the named files intact.

## KI-007: A hook run doesn't report an unloadable unnamed file

Status: open
Recorded: 2026-11-09 (39)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/pipeline.py

Issue:
A full run reports an unreadable or oversized file as an error. A run with explicit paths leaves the same file out of the project rules' input with a message only under `--debug`. Both runs give HSL104 the same input; only the error channel differs.

Why deferred:
D3 point 5 ratified dropping these errors, which follows from a hook run reporting only on the files it was given.

Recommended follow-up:
If hook runs become the only runs, print a warning for an extra file that fails to load.

Acceptance criteria:
- A hook run tells the user when a file was left out of the comparison.
