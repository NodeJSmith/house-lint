# Known Issues

Durable issues discovered during orchestration that were intentionally not fixed in this run.

## KI-001: ConfigError fallback root/config in JSON output are not canonicalized (`.resolve()` missing)

Status: fixed
Run: 100
Source: T07
Reason not fixed now: n/a — fixed in follow-up pass
Observed in: T07 (code-reviewer second re-review, integration-reviewer second re-review)
Affected files:
- src/house_lint/cli.py:178-179
- src/house_lint/cli.py:203

Issue:
`check()`'s pre-`resolve_project` fallback values (`resolved_root`, `resolved_config`) call only
`.expanduser()`, not `.expanduser().resolve()` as `resolve_project()` itself does
(`src/house_lint/discovery.py:308-343`). `ScanResult.__post_init__`
(`src/house_lint/results.py:107-111`) only calls `.absolute()`, which does not collapse `..`
segments or resolve symlinks. As a result, on the three `ConfigError` branches that fire *inside*
`resolve_project()` before it returns a `ProjectResolution` (root is not a directory, explicit
config outside root, explicit config does not exist), the `root`/`config` fields in the JSON error
output are only `cwd`-absolute, not canonical — diverging from `resolve_project`'s documented
guarantee and from every other success/error path in the same command. Reproduced live by both
reviewers:

```
$ house-lint check --root /tmp/hltest/subdir/../nonexistent-dir --format json
{"root":"/tmp/hltest/subdir/../nonexistent-dir", ...}

$ mkdir -p /tmp/x/real_root /tmp/x/outside && ln -s /tmp/x/real_root /tmp/x/link_root
$ cd /tmp/x && house-lint check --root ./link_root --config ./outside/cfg.toml --format json
{"root": "/tmp/x/link_root", ...}   # resolve_project would have produced /tmp/x/real_root
```

Confirmed this fallback value is used only for JSON error reporting (`_result_for_config_error` →
`ScanResult`) and not for any subsequent filesystem access or authorization decision within
`check()` — the process returns exit code 2 immediately after. The output is a valid, usable
(non-canonical) path, not a crash, hang, or silent failure with no explanation. Severity Gate does
not trip: no user-visible breakage without an error message (exit code 2 with a JSON error body is
emitted either way), no data loss, no security/auth exposure (the value isn't reused for any
access-control check), and the core `check` workflow is not blocked — only the exact path string in
one edge-case error report is non-canonical.

Why deferred:
The task's 2-pass fixer-loop budget for T07 is exhausted; this pass is a terminal
classify-only pass with no code changes permitted. The fix itself is small and well-understood (add
`.resolve()` back to both fallback lines, mirroring `resolve_project`'s canonicalization), but
applying it requires a follow-up fixer dispatch outside this run's remaining budget for this task.

Recommended follow-up:
Add `.resolve()` alongside `.expanduser()` on both `src/house_lint/cli.py:178-179` and the
`except ConfigError` fallback derivation at `cli.py:203`, matching `resolve_project`'s
canonicalization. `.resolve()` is safe on a non-existent path (`strict=False` by default), so this
is a behavior-preserving fix for the directory-existence/config-missing cases. Consider also
tightening `test_json_auto_discovery_config_error_preserves_resolved_root`
(`tests/integration/test_cli.py:258`) to assert exact equality (matches sibling tests) and adding a
symlink/`..`-segment regression test for the explicit `--root`/`--config` branches, per
integration-reviewer's secondary observation.

Acceptance criteria:
- `house-lint check --root <path-with-.. or-symlink> --format json` on a `ConfigError` branch that
  fires before `resolve_project` returns reports a fully resolved (`.expanduser().resolve()`)
  `root`/`config`, matching the value `resolve_project` itself would have produced.
- `grep -n "root.expanduser()" src/house_lint/cli.py` and the corresponding `config.expanduser()`
  line both include a chained `.resolve()` call.

Resolution:
Added `.resolve()` to both fallback lines (`src/house_lint/cli.py:178-179`), matching
`resolve_project`'s canonicalization. `resolved_config.parent` at `cli.py:203` now inherits the
resolved value with no separate change needed. Added
`test_json_root_not_a_directory_reports_canonical_root`
(`tests/integration/test_cli.py`) using a `--root ../notadir` relative path with a `..` segment to
exercise the pre-`resolve_project` fallback specifically (the existing sibling tests all pass
already-absolute paths, so `ScanResult.__post_init__`'s `.absolute()` call masked the bug there).
Verified RED without the fix, GREEN with it. Full suite (175 tests), ruff, and pyright all clean.
