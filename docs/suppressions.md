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
- A pragma naming a project rule is never reported as unused, because the rule's findings don't exist yet when the file is checked.
- Only pragmas in Python files are read. An occurrence in a file of another language, or in a Python file that doesn't parse, can't be suppressed.

## HSL900 diagnostics

`HSL900` reports every malformed pragma and each invalid listed ID. It reports when a pragma has no IDs, invalid/duplicate IDs, an invalid action or reason, wrong placement, an unknown or disabled ID, an ID whose rule doesn't handle Python files, an unused ID, or a duplicate/conflicting claim on the same rule/finding. For project rules, it checks the pragma itself but reports neither unused IDs nor overlapping claims. Conflicts fail closed: the original finding remains visible and every conflicting pragma is diagnosed.

Text that resembles a pragma inside a string or docstring is not a suppression. `HSL900` itself cannot be selected, ignored, or suppressed.
