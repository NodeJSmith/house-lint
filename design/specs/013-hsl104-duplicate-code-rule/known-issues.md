# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: A `.py` symlink to a target without a `.py` suffix drops out of HSL104

Status: open
Recorded: 2026-11-09 (39)
Source: other
Reason not fixed now: needs-decision
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
