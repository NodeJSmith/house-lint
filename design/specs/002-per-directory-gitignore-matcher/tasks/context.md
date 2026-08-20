# Context: Per-Directory Gitignore Matcher

## Problem & Motivation

house-lint silently skips files that git would lint when nested `.gitignore` files use directory-only negations. `pathspec`'s aggregate `GitIgnoreSpec.match_file()` classifies directory-only patterns from pattern text rather than accepting a caller-supplied `is_dir`, and its internal priority system does not implement git's last-match-wins rule. The earlier guarantee that "the divergence always errs toward over-linting" was falsified, and `pathspec` issue #89 is labeled Will Not Fix — the fix is architectural. The flatten/prefix/compensate pipeline (~130 lines) exists solely to work around pathspec's flattened matching model and is being replaced with a cleaner per-directory evaluation that maps directly to git's own model.

## Visual Artifacts

None.

## Key Decisions

1. **Keep pathspec for glob-to-regex compilation only.** Use `GitIgnoreSpecPattern` individually (`.match_file()`, `.include`, `.pattern`), not the aggregate `GitIgnoreSpec.match_file()`. These are `__slots__`-based attributes — not part of the documented API contract, mitigated by upper-bound pinning (`pathspec>=0.12,<2`).
2. **Per-directory pattern tuples, not a dataclass.** Each directory's `.gitignore` produces a `tuple[tuple[GitIgnoreSpecPattern, bool], ...]`. `_match_patterns` is a module-level free function (matching the existing `_ignored()` convention), not a method on a class.
3. **Fused stack-build-and-check.** The function that builds the matcher stack simultaneously checks whether each ancestor directory is excluded, mirroring `_combined_gitignore_spec`'s current behavior. Walked and explicit paths call the same function — no separate `_has_gitignore_excluded_ancestor`.
4. **Relative-path threading.** Each matcher is probed with the candidate's path relative to that matcher's owning directory (not bare entry names). This is what makes slash-containing patterns in non-root `.gitignore` files work.
5. **`_ignored()` scoped to static specs only.** After the change, `_ignored()` receives only `builtin_spec` and `exclude_spec`. The three call sites that currently pass `combined_gitignore_spec` become `_ignored(...) or stack_ignored(...)`.
6. **`include=True` means ignore (not negated).** `include=False` means the pattern is a `!`-prefixed negation. The naming is counterintuitive — verified empirically.
7. **Trailing `/` probe for directories.** `pattern.match_file("src/")` correctly matches directory-only patterns; `pattern.match_file("src")` does not. This is the key insight.

## Constraints & Anti-Patterns

- Do NOT shell out to git for file discovery.
- Do NOT introduce new dependencies.
- Do NOT use `GitIgnoreSpec.match_file()` on aggregate specs for gitignore patterns — the bug lives there.
- Do NOT create a `DirectoryMatcher` dataclass — use a type alias + free function.
- Do NOT cache the stack evaluation result per-directory (unlike the deleted `combined_gitignore_spec_cache`). Cache only per-directory pattern tuples.
- Do NOT evaluate ancestor A's own `.gitignore` when deciding whether A itself is pruned — only A's ancestors' matchers decide.
- `_normalize_contents_glob` transformation (trailing `/**` → `/**/*`) is still needed — apply it when building pattern tuples, not as a standalone function.
- `_has_excluded_ancestor` stays as-is — it handles only `builtin_spec`/`exclude_spec`.
- Non-goals: chasing the over-linting divergence specifically, performance optimization, replacing pathspec entirely.

## Design Doc References

- `## Problem` — the bug mechanism and pathspec's Will Not Fix
- `## Architecture → Proposed change` — full per-directory stack evaluation design
- `## Architecture → What gets deleted / What gets added` — exact function/constant lists
- `## Key Constraints` — pathspec API surface, `_normalize_contents_glob` handling
- `## Replacement Targets` — table of old → new mappings
- `## Test Strategy` — required test types, existing tests to adapt, new coverage, tests to remove
- `## Smoke Test` — parity suite + fuzz suite verification

## Convention Examples

### Frozen dataclass pattern

**Source:** `src/house_lint/discovery.py`

```python
@dataclass(frozen=True)
class DiscoveryResult:
    files: tuple[Path, ...]
    files_skipped: int = 0
    errors: tuple[LintError, ...] = ()
    resolved_paths: Mapping[Path, Path] = field(default_factory=lambda: dict[Path, Path]())
```

### Tri-state return with None for "no opinion"

**Source:** `src/house_lint/discovery.py` (`_ignored` returns bool; the new `_match_patterns` free function extends this to tri-state with `None`)

```python
def _ignored(root: Path, path: Path, *specs: GitIgnoreSpec, is_dir: bool) -> bool:
    relative = path.relative_to(root).as_posix()
    probe = f"{relative}/" if is_dir else relative
    return any(spec.match_file(probe) for spec in specs)
```

### Cache-per-directory pattern

**Source:** `src/house_lint/discovery.py` (`_own_gitignore_lines` method)

```python
def _own_gitignore_lines(self, directory: Path) -> tuple[str, ...]:
    if directory in self.own_gitignore_lines_cache:
        return self.own_gitignore_lines_cache[directory]
    ignore = directory / ".gitignore"
    def on_error(operation: str, message: str) -> None:
        self.errors.append(self._error(ignore, "traversal", operation, message))
    lines = _load_gitignore_lines(ignore, on_error)
    self.own_gitignore_lines_cache[directory] = lines
    return lines
```

### Parity test scenario structure

**Source:** `tests/integration/test_gitignore_parity.py`

```python
Scenario(
    "directory-form negation cancels an earlier unanchored ignore",
    {"": ["cache", "!cache/"]},
    ("src/a.py", "src/cache/c.py"),
),
```
