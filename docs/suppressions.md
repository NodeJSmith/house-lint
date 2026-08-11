# Suppressions

Use suppressions only for a known exception to Jessica's house style. Each pragma appears in a Python comment token, names one or more enabled rule IDs, and gives a reason with at least three alphanumeric characters.

```python
value()  # house-lint: ignore[HSL001,HSL004] - generated compatibility wrapper

# house-lint: ignore-next[HSL002] - avoids a circular import
from package import value

# house-lint: ignore-file[HSL001,HSL102] - generated compatibility module
```

The prefix is exactly `house-lint:`. IDs are canonical comma-separated IDs; whitespace around commas is allowed. The closing bracket must be followed by ` - ` and the reason.

## Ownership

- `ignore[...]` is trailing within an AST statement span and owns findings from that statement.
- `ignore-next[...]` is alone on a comment-only line and owns the next statement in the same lexical suite. Blank lines and ordinary comments may intervene; a suite boundary may not.
- `ignore-file[...]` appears before the first statement other than a module docstring or `__future__` import and owns listed findings throughout the file.
- A statement pragma consumes every owned finding for each listed rule. A file pragma consumes every listed rule's findings in its file.
- File-length `HSL102` findings have no statement owner and require `ignore-file[HSL102]`.

## HSL900 diagnostics

`HSL900` reports every malformed pragma and each invalid listed ID. It reports when a pragma has no IDs, invalid/duplicate IDs, an invalid action or reason, wrong placement, an unknown or disabled ID, an unused ID, or a duplicate/conflicting claim on the same rule/finding. Conflicts fail closed: the original finding remains visible and every conflicting pragma is diagnosed.

Text that resembles a pragma inside a string or docstring is not a suppression. `HSL900` itself cannot be selected, ignored, or suppressed.
