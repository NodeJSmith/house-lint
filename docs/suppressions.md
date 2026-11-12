# Suppressions

Use suppressions only for a known exception to the project's house style. Each pragma appears in a Python comment token, names one or more enabled rule IDs, and gives a reason with at least three alphanumeric characters.

```python
value()  # house-lint: ignore[HSL001,HSL004] - generated compatibility wrapper


def build() -> object:
    # house-lint: ignore-next[HSL002] - avoids a circular import
    from package import value

    return value


# house-lint: ignore-file[HSL001,HSL102] - generated compatibility module
```

The prefix is exactly `house-lint:`. IDs are canonical comma-separated IDs; whitespace around commas is allowed. The closing bracket must be followed by ` - ` and the reason.

## Ownership

- `ignore[...]` is trailing within an AST statement span and owns findings from that statement.
- `ignore-next[...]` is alone on a comment-only line and owns the next statement in the same lexical suite. Blank lines and ordinary comments may intervene; a suite boundary may not. A finding raised on one of those intervening comment lines — a divider, a filler-phrase comment — has no statement to attach to, but `ignore-next` still owns it: place the pragma above the comment, not the statement, to suppress it. This includes the pragma's own line: if its reason text itself trips a listed rule (a filler phrase named in the reason), `ignore-next` suppresses that too, the same way a trailing `ignore`'s own reason text is already covered by sharing its statement's owner.
- `ignore-file[...]` appears before the first statement other than a module docstring or `__future__` import and owns listed findings throughout the file.
- An `except … as name:` clause is its own owner, distinct from sibling handlers of the same `try`: a trailing `ignore[HSL103]` on one handler suppresses that handler alone.
- A decorated `def`/`class` starts at its first decorator line. `ignore-next` goes above the decorators; a pragma between a decorator and its `def` is misplaced, and an `ignore-file` there is no longer in the file prologue.
- A statement pragma consumes every owned finding for each listed rule. A file pragma consumes every listed rule's findings in its file.
- File-length `HSL102` findings have no statement owner and require `ignore-file[HSL102]`.

## Project rules

A project rule compares files, so one issue it finds can occur in several places. Each place is its own finding, and a pragma in that place's file suppresses that finding alone. The other places stay visible, and each still lists the suppressed one in its `related` locations.

- `ignore-file[...]` suppresses every occurrence of the listed rule in its file, including an occurrence with no line.
- `ignore[...]` and `ignore-next[...]` suppress an occurrence whose owner is the statement the pragma owns. An occurrence's owner is the innermost statement whose lines, from its first decorator to its last line, include the line the occurrence starts on. So an `ignore-next` above a decorated definition, or a trailing `ignore` on its `def` line, suppresses an occurrence starting on its decorator, and a pragma on a `def` or `class` line doesn't reach an occurrence starting inside its body.
- An occurrence starting on a line the file doesn't have, or with no line, has no owner. Only `ignore-file` suppresses it.
- Two pragmas that both reach one occurrence both suppress it. It counts once, and nothing is reported.
- A suppressed occurrence still counts toward a rule's own thresholds, such as `HSL104`'s `min_occurrences`.
- With explicit paths, only occurrences in the named files are reported, so only their pragmas are checked and counted as suppressed.
- `HSL901` never reports a pragma naming a project rule, by design: not as unused or redundant, and not when the run leaves the rule out. Finding stale ones would mean re-reading every file with such a pragma after the rule runs, and a project rule often runs in its own hook, as `HSL104` does before a push. Pylint likewise leaves `duplicate-code` out of its unused-suppression check.
- Only pragmas in Python files are read. An occurrence in a file of another language, or in a Python file that doesn't parse, can't be suppressed.

## Overlapping pragmas

Two pragmas overlap when they name the same rule and reach the same finding. An `ignore-file[HSL002]` plus a trailing `ignore[HSL002]` on a lazy import overlap, and so do a trailing `ignore` and an `ignore-next` for the same statement when that statement has a finding; with no finding, each is just unused. Every overlapping pragma suppresses what it owns, so the finding stays hidden and counts once.

When `HSL901` runs, it credits each finding to one of the pragmas suppressing it, as Ruff, ESLint, and Pylint do: the one reaching the most findings, then the broadest (file before `ignore-next` before `ignore`), then the earliest. Overlapping pragmas nest, so in practice the broadest wins: an `ignore-file` reaches everything a statement pragma for the same rule does, and an `ignore-next` everything a trailing `ignore` on its statement does. A pragma credited with none of the findings it suppresses is reported as `redundant suppression for X`. Deleting every pragma it reports, or the one ID it names on a pragma that lists several, never changes what is suppressed.

## HSL900: broken pragmas

`HSL900` reports a pragma house-lint can't use: malformed syntax, no IDs, invalid or duplicate IDs, an invalid action or reason, wrong placement, an unknown ID, or an ID whose rule doesn't handle Python files. A broken pragma suppresses nothing.

Text that resembles a pragma inside a string or docstring is not a suppression. `HSL900` always runs: it cannot be selected, ignored, named in `per-file-ignores`, or suppressed, because a pragma that could silence it could hide its own invalidity.

## HSL901: stale pragmas

`HSL901` is opt-in, like Ruff's `RUF100` and Pylint's `useless-suppression`. It reports a pragma that does nothing:

- `unused suppression for X`: rule X ran on the file and the pragma claimed none of its findings.
- `unused suppression for disabled rule X`: rule X doesn't run on this file, because the selection leaves it out or `per-file-ignores` drops it here. Such a pragma is never accepted, so it suppresses nothing whether or not `HSL901` runs. A pragma naming a project rule is never reported (see above).
- `redundant suppression for X`: it suppresses findings, but each is credited to an overlapping pragma (see above).

With `HSL901` off, stale pragmas go unreported, so a run selecting one rule, such as `house-lint check --select HSL102`, passes over pragmas for every other rule. To keep stale pragmas from piling up, add it to the project's config:

```toml
[tool.house-lint]
extend-select = ["HSL901"]
```

A CLI `--select` replaces the config's selection, `extend-select` included, so a per-rule run still leaves `HSL901` out. `--select HSL901` alone runs no other rule, so it reports every pragma naming another Python rule as naming a disabled one.

`HSL901` is an ordinary rule: `ignore`, `per-file-ignores`, and the CLI flags can turn it off, and a pragma can suppress its reports. `ignore-file[HSL901]` suppresses every `HSL901` report in its file. A statement pragma listing `HSL901` suppresses the reports on every pragma owning the same statement, itself included, so `value = 1  # house-lint: ignore[HSL002, HSL901] - kept for a migration` keeps an unused `HSL002` pragma quietly. A pragma naming `HSL901` is never itself reported: not as unused or redundant, and not while `HSL901` is off, when it suppresses nothing because there is nothing to suppress.
