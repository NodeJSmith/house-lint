# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: A file declaring an unusual codec aborts the whole run

Status: open
Recorded: 2026-11-02 (57)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/python_source.py

Issue:
A `.py` file starting `# coding: undefined` raises a plain `UnicodeError` while decoding, which `parse_python` doesn't catch. It reaches `scan_source`'s boundary and becomes an internal error with `stop=True`, so the run ends with exit 4 and every file sorted after it goes unscanned, losing their findings. A stdlib probe shows the same for `punycode` on Python 3.11 and 3.12. Other encoding problems report a per-file `decode-error` and the run continues.

Why deferred:
Nobody writes this declaration by accident, the failure is loud, the file can be excluded, and master behaves the same way. Fixing it changes output (exit 4 becomes 3 for that file), which this change's posture rules out.

Recommended follow-up:
If it ever happens, catch `UnicodeError` instead of `UnicodeDecodeError` in `parse_python`, in its own PR.

Acceptance criteria:
- A file declaring `# coding: undefined` reports `decode-error`, and files sorted after it are still scanned.

## KI-002: Two load failures are pinned as never cached only at unit level

Status: open
Recorded: 2026-11-02 (57)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- tests/integration/test_scan_pipeline.py

Issue:
Pipeline tests pin "reported and never cached" for read errors and oversized files. Path-escape and non-regular failures are tested only on `load_file`. Discovery filters both before a read, so in a run they need a file swapped between discovery and the read.

Why deferred:
All four share the loop's one `LoadFailure` arm, so a test per kind can't fail while that holds, and reaching these two through the pipeline would mean faking the race.

Recommended follow-up:
Add pipeline cases if a change gives load failures separate paths through the loop.

Acceptance criteria:
- Each load failure kind that takes its own path through the loop has a pipeline test showing no cache entry is written.
