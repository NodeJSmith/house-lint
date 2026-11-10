# Rules

`house-lint` has fixed rule IDs. Four rules are enabled by default, four are opt-in, and `HSL900` is always enabled.

Finding messages are human-readable display text and may change. Machine consumers should identify findings by rule ID and location rather than message text.

| ID | Mode | Behavior |
| --- | --- | --- |
| `HSL001` | Default | Flags divider comments and fixed built-in filler phrases in comments/docstrings. Ordinary string literals are excluded. |
| `HSL002` | Default | Flags `import` and `from … import` inside function, async-function, method, and nested-function bodies. |
| `HSL003` | Default | Flags a top-level `if TYPE_CHECKING:` or `if typing.TYPE_CHECKING:` — aliased imports of either name included — followed later by a top-level import. |
| `HSL004` | Default | Flags uppercase module constants after the first top-level class/function. Dunder names and constants referencing an earlier top-level binding are exempt. |
| `HSL101` | Opt-in | Flags built-in and/or configured token families in comments, docstrings, and/or filename segments. Ordinary strings are excluded. |
| `HSL102` | Opt-in | Flags a file when `len(text.splitlines())` is strictly greater than `max_lines`. |
| `HSL103` | Opt-in | Flags `except … as name` bindings outside the configured exact/suffix policy. |
| `HSL104` | Opt-in | Flags each copy of a block of code that appears at least three times across the project. Needs the `duplicates` extra. |
| `HSL900` | Always | Reports invalid suppression pragmas. It cannot be selected, ignored, or suppressed. |

## Default rules

### HSL001 — AI-writing cruft

The rule flags bare decorative runs of at least four `-=#*~_` characters, decorative wrapped labels, and these case-insensitive phrases: “it is important to note,” “it should be noted,” “it is worth noting,” “please note that,” “needless to say,” “due to the fact that,” “in order to,” “as mentioned above/previously/earlier,” and forms of “leverage,” “utilize,” and “facilitate,” including `leveraged`, `utilized`, and `facilitated`.

### HSL002 — Lazy imports

Move imports to module scope where possible. If a circular dependency requires a local import, use a statement-aware `ignore[HSL002]` suppression with a reason; do not use Hassette's former `# lazy-import:` annotation.

### HSL003 — TYPE_CHECKING position

Put top-level type-only imports after every regular module import. Nested guards are outside this rule's scope. Aliased spellings count as the same guard: `import typing as t` with `if t.TYPE_CHECKING:`, and `from typing import TYPE_CHECKING as TC` with `if TC:`, are both recognized.

### HSL004 — Constants position

Put module constants before behavior. The preserved derived-binding heuristic exempts a later constant when its value—or an annotated assignment's annotation—references a name bound by an earlier top-level class, function, or assignment. This is stylistic, not a runtime dependency proof, including in modules with postponed annotations.

## Opt-in rules

### HSL101 — Spec tokens

Select `HSL101` to detect its three built-in token families (`AC`/`FR`/`NFR`/`WP`, `T`, `KI`) with no further configuration. Optionally configure additional token families on top of the built-ins. A family declares uppercase prefixes, scopes, and optional separator/digit/suffix/case/time controls. Filename matching uses `.`, `_`, and `-` segments. See [configuration](configuration.md#hsl101-token-families).

### HSL102 — File length

`max_lines` defaults to `800`; the rule reports only files above that number. File-length findings have no statement owner and no line of their own, so only `ignore-file[HSL102]` can suppress them — unlike a standalone-comment finding (see [suppressions](suppressions.md#ownership)), which also lacks a statement owner but can still be reached by `ignore-next`.

Under [`--base`](new-code-gating.md), a file is reported only if it has more lines than it had at the merge-base, following renames. A file that was under the limit, or that's new, counts as growing from under it. A file that shrank or didn't change passes.

### HSL103 — Exception names

`allowed` defaults to `['exc', '*_exc']`. Exact names and a single leading suffix wildcard are supported. Configure it only when your project adopts this naming policy.

### HSL104 — Duplicate code

Flags code copied across the project, the kind a fixture, helper, or `parametrize` would remove. It runs [jscpd](https://github.com/kucherenko/jscpd) 5.4.1 over every Python file in scope, so it needs the `duplicates` extra (`uv add --dev 'house-lint[duplicates]'`, or `uvx --from 'house-lint[duplicates]' house-lint check`). It refuses any other jscpd version. When jscpd is missing or the wrong version, the run reports a `project-rule-error` naming the extra. When jscpd fails or takes more than two minutes, a fixed limit, the error says so instead. jscpd picks a file's language from its suffix, so a `.py` symlink to a file without that suffix fails the rule too, naming the file; exclude the symlink or rename its target. Either way, every other rule still reports. jscpd names no file it skipped, so when a copy you expect isn't reported, `--debug` prints how many files jscpd analyzed; a file with fewer than `min_tokens` tokens isn't counted.

- **What matches.** Exact token sequences, with comments ignored. Docstrings count as code, and identifiers and literals must match exactly, so a copy with a renamed variable is two shorter matches or none. A match needs at least `min_tokens` tokens (default 30) and must span more than `min_lines` lines (default 5).
- **Groups.** jscpd reports matching pairs, and HSL104 joins them into groups of places: overlapping matches in one file count as one place, and a partial copy joins its group. Since overlapping matches can join two different blocks, a group of N places isn't always one block copied N times. Each place is its own finding, listing the others as `related`. Only groups of at least `min_occurrences` places (default 3) report, so a block copied once is left alone unless you set it to 2.
- **The message** is `duplicated code of N lines in M places`, or a range such as `5-8 lines` when the places differ in length. The location spans from the first matched token to the end of the last.
- **Suppressing.** A pragma suppresses only the copy in its own file, and that copy still counts toward `min_occurrences`. For generated files, use `exclude` or `per-file-ignores`; a file per-file-ignored for `HSL104` is left out of its comparison entirely.
- **Explicit paths.** `house-lint check a.py` still compares `a.py` against the whole configured scope, but reports only copies in `a.py`, and `files scanned` counts only the files named. A named file outside `include` is compared too. See [pre-commit](../README.md#pre-commit) for running it before a push. Turn it on there with `--select`, not with `extend-select` in config, as that section explains.
- **Every run.** Nothing `HSL104` finds is cached: jscpd runs on every scan that enables it.
- **Under [`--base`](new-code-gating.md).** Only the copies a PR added are reported. Each place at `HEAD` pairs with a place at the merge-base holding the same code, or, failing that, code that contains it or is contained in it, so a rename that makes jscpd match copies over different lines still passes. An old copy extended with more copied lines stays old.

jscpd's own `jscpd:ignore` comments are not supported; use house-lint pragmas.

## Source compatibility matrix

The rules preserve Hassette detector intent while standardizing discovery, error handling, output, and suppressions.

| Rule | Preserved | Generalized | Dropped |
| --- | --- | --- | --- |
| `HSL001` | Divider/filler patterns and prose-only scope | Unified suppressions | No-exemption policy |
| `HSL002` | Function-depth detection | Unified suppressions | `# lazy-import:` and raw-line attachment |
| `HSL003` | Guard forms and later-import detection | Unified suppressions | No-suppression behavior |
| `HSL004` | Uppercase/dunder/derived-binding heuristic | Unified suppressions | `# constant-after-def:` |
| `HSL101` | Prose/filename scope, time guard, and built-in vocabulary | Additional configurable token families and suppressions | No-suppression policy |
| `HSL102` | `splitlines()` and strict threshold | Configurable threshold and file suppression | `# file-size-exempt:` and warning-only semantics |
| `HSL103` | `exc`/`*_exc` detection | Configurable allowed names and suppressions | No-suppression behavior |
