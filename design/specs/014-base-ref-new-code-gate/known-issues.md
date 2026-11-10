# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: Which scan an error came from is only in its message prefix

Status: open
Recorded: 2026-11-10 (52)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/new_code.py
- README.md

Issue:
Under `--base`, errors from the base and head scans share `code`, `phase`, `operation`, and `path`. Only the message differs, through the `base run:`/`head run:` prefix that `labelled` adds. A consumer that wants to treat base-only errors differently has to match the message text.

Why deferred:
No consumer branches on the side today (hassette treats any error as failure), and the README documents the prefix. A structured field is a JSON schema change with no user.

Recommended follow-up:
If a wrapper needs to let base-only errors through, add a `run: "base" | "head"` field to gate errors.

Acceptance criteria:
- A gate error's JSON carries its run as a field, and the README documents it.

## KI-002: A failed gate emits no `base` object

Status: open
Recorded: 2026-11-10 (52)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/new_code.py
- src/house_lint/results.py

Issue:
`base` appears in JSON output only when the comparison completed. A gate that fails during setup produces JSON that looks like an ordinary run with an error, so a consumer keying on the presence of `base` can't tell a failed gate from a plain run.

Why deferred:
Exit 3 and the `errors` array are authoritative. Emitting `base` on failure would make its commit fields nullable for every consumer of a successful gate.

Recommended follow-up:
If a consumer needs to recognize a failed gate from JSON alone, emit `base` with the `ref` and null commits on failure.

Acceptance criteria:
- A failed gate's JSON names the requested ref, and the README documents the nullable fields.

## KI-003: Every gate setup failure reports operation `resolve`

Status: open
Recorded: 2026-11-10 (52)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/new_code.py
- README.md

Issue:
An unresolvable ref, running outside a repository, and a failed `read-tree`, `checkout-index`, or rename `diff` all become `error("git", "base", "resolve", ...)`. The `operation` field can't tell a bad ref from a failed extraction, and the README's `git-error` row doesn't mention running outside a repository.

Why deferred:
The message says what failed, and consumers use the exit code and `code`, not `operation`.

Recommended follow-up:
If `operation` should be a reliable machine field, report each step under its own operation (`resolve`, `extract`, `renames`) and widen the README row.

Acceptance criteria:
- Each gate setup step's failure carries a distinct `operation`, pinned by a test per step.

## KI-004: Under `--base`, `shadowed_config` reflects the working tree

Status: open
Recorded: 2026-11-10 (52)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/cli.py

Issue:
The CLI computes the shadowed-config list from the working tree before `run_gate`, while the gate loads its config from HEAD. When the working tree's config sources differ from HEAD's, the output can name a shadowed config for a resolution the gate didn't use.

Why deferred:
The note is informational and nothing fails on it. The mismatch needs competing config sources that differ between the working tree and HEAD.

Recommended follow-up:
Omit `shadowed_config` under `--base`, or compute it from the HEAD extraction.

Acceptance criteria:
- `--base` output makes no shadowed-config claim about a resolution it didn't use.

## KI-005: A filesystem failure during extraction is a generic internal error

Status: open
Recorded: 2026-11-10 (52)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/new_code.py

Issue:
`run_gate` catches `GitFailure` and `GateFailure` only. An `OSError` while creating or writing the temporary extractions (a full `/tmp`, a quota) reaches the CLI's internal-error boundary: exit 4, with the cause visible only under `--debug`.

Why deferred:
It's rare, and it still fails loudly with a non-zero exit.

Recommended follow-up:
If the gate runs on constrained CI runners where this happens, wrap extraction `OSError`s in a `GateFailure` naming the path and errno.

Acceptance criteria:
- A simulated write failure during extraction exits 3 with a `git-error` naming the path, not exit 4.

## KI-006: HSL102's base matcher parses its own message

Status: open
Recorded: 2026-11-10 (52)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- src/house_lint/rules/file_length.py

Issue:
`line_count_of` recovers line counts from the message through `MESSAGE_PATTERN`, as D7 ratified. Changing `format_message` without the pattern raises `ValueError` mid-gate. Any absence of a base finding at the mapped path (under threshold, unreadable, excluded, or suppressed at base) counts as growth.

Why deferred:
D7 ratified the round-trip. The message and its parser live in one module, and a test pins it.

Recommended follow-up:
If another consumer needs HSL102's counts as data, carry the line count on `Finding` instead.

Acceptance criteria:
- The HSL102 matcher reads counts without parsing the message.
