# Rules

`house-lint` has fixed rule IDs. Four rules are enabled by default, three are opt-in, and `HSL900` is always enabled.

Finding messages are human-readable display text and may change. Machine consumers should identify findings by rule ID and location rather than message text.

| ID | Mode | Behavior |
| --- | --- | --- |
| `HSL001` | Default | Flags divider comments and fixed built-in filler phrases in comments/docstrings. Ordinary string literals are excluded. |
| `HSL002` | Default | Flags `import` and `from … import` inside function, async-function, method, and nested-function bodies. |
| `HSL003` | Default | Flags a top-level `if TYPE_CHECKING:` or `if typing.TYPE_CHECKING:` followed later by a top-level import. |
| `HSL004` | Default | Flags uppercase module constants after the first top-level class/function. Dunder names and constants referencing an earlier top-level binding are exempt. |
| `HSL101` | Opt-in | Flags configured token families in comments, docstrings, and/or filename segments. Ordinary strings are excluded. |
| `HSL102` | Opt-in | Flags a file when `len(text.splitlines())` is strictly greater than `max_lines`. |
| `HSL103` | Opt-in | Flags `except … as name` bindings outside the configured exact/suffix policy. |
| `HSL900` | Always | Reports invalid suppression pragmas. It cannot be selected, ignored, or suppressed. |

## Default rules

### HSL001 — AI-writing cruft

The rule flags bare decorative runs of at least four `-=#*~_` characters, decorative wrapped labels, and these case-insensitive phrases: “it is important to note,” “it should be noted,” “it is worth noting,” “please note that,” “needless to say,” “due to the fact that,” “in order to,” “as mentioned above/previously/earlier,” and forms of “leverage,” “utilize,” and “facilitate,” including `leveraged`, `utilized`, and `facilitated`.

### HSL002 — Lazy imports

Move imports to module scope where possible. If a circular dependency requires a local import, use a statement-aware `ignore[HSL002]` suppression with a reason; do not use Hassette's former `# lazy-import:` annotation.

### HSL003 — TYPE_CHECKING position

Put top-level type-only imports after every regular module import. Nested guards are outside this rule's scope.

### HSL004 — Constants position

Put module constants before behavior. The preserved derived-binding heuristic exempts a later constant when its value—or an annotated assignment's annotation—references a name bound by an earlier top-level class, function, or assignment. This is stylistic, not a runtime dependency proof, including in modules with postponed annotations.

## Opt-in rules

### HSL101 — Spec tokens

Select `HSL101` and configure one or more token families. A family declares uppercase prefixes, scopes, and optional hash/digit/suffix/case/time controls. Filename matching uses `.`, `_`, and `-` segments. See [configuration](configuration.md#hsl101-token-families).

### HSL102 — File length

`max_lines` defaults to `800`; the rule reports only files above that number. File-length findings have no statement owner, so only `ignore-file[HSL102]` can suppress them.

### HSL103 — Exception names

`allowed` defaults to `['exc', '*_exc']`. Exact names and a single leading suffix wildcard are supported. Configure it only when your project adopts this naming policy.

## Source compatibility matrix

The rules preserve Hassette detector intent while standardizing discovery, error handling, output, and suppressions.

| Rule | Preserved | Generalized | Dropped |
| --- | --- | --- | --- |
| `HSL001` | Divider/filler patterns and prose-only scope | Unified suppressions | No-exemption policy |
| `HSL002` | Function-depth detection | Unified suppressions | `# lazy-import:` and raw-line attachment |
| `HSL003` | Guard forms and later-import detection | Unified suppressions | No-suppression behavior |
| `HSL004` | Uppercase/dunder/derived-binding heuristic | Unified suppressions | `# constant-after-def:` |
| `HSL101` | Prose/filename scope and time guard | Constrained configured families and suppressions | Hard-coded vocabulary and no-suppression policy |
| `HSL102` | `splitlines()` and strict threshold | Configurable threshold and file suppression | `# file-size-exempt:` and warning-only semantics |
| `HSL103` | `exc`/`*_exc` detection | Configurable allowed names and suppressions | No-suppression behavior |
