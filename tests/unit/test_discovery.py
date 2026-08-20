from collections.abc import Iterable
from pathlib import Path

import pytest

from house_lint import discovery
from house_lint.config import ConfigError
from house_lint.discovery import DiscoveryError, discover_files, resolve_project
from house_lint.results import LintError


def test_full_scan_applies_builtin_gitignore_configured_excludes_and_sorting(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("ignored/\n")
    for relative in ("src/z.py", "src/a.py", "ignored/x.py", ".venv/v.py", "notes.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src", "ignored", ".venv"), excludes=("src/z.py",))

    assert result.files == (tmp_path / "src/a.py",)
    assert result.errors == ()
    assert result.files_skipped == 3


def test_no_path_scan_uses_all_documented_default_include_roots(tmp_path: Path) -> None:
    expected: list[Path] = []
    for root_name in ("src", "tests", "scripts", "tools", "examples"):
        path = tmp_path / root_name / f"{root_name}.py"
        path.parent.mkdir(parents=True)
        path.write_text("x = 1\n")
        expected.append(path)
    (tmp_path / "outside.py").write_text("x = 1\n")

    result = discover_files(tmp_path)

    assert result.files == tuple(sorted(expected))
    assert result.files_skipped == 0


def test_explicit_paths_are_strict_and_directories_recursive(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    py_file = source / "a.py"
    py_file.write_text("x = 1\n")
    (source / "readme.md").write_text("text\n")

    result = discover_files(tmp_path, explicit=(source, py_file, py_file))

    assert result.files == (py_file,)
    assert result.files_skipped == 2

    with pytest.raises(ValueError, match="does not exist"):
        discover_files(tmp_path, explicit=(tmp_path / "missing.py",))
    with pytest.raises(ValueError, match="Python"):
        discover_files(tmp_path, explicit=(source / "readme.md",))


def test_gitignore_can_be_disabled_but_builtins_remain(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "ignored.py").write_text("x = 1\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "kept-out.py").write_text("x = 1\n")

    result = discover_files(tmp_path, include=("ignored.py", ".venv"), use_gitignore=False)

    assert result.files == (tmp_path / "ignored.py",)


def test_root_gitignore_cannot_negate_builtin_excludes(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("!.venv/\n")
    excluded = tmp_path / ".venv" / "kept-out.py"
    excluded.parent.mkdir()
    excluded.write_text("x = 1\n")

    result = discover_files(tmp_path, include=(".venv",))

    assert result.files == ()
    assert result.files_skipped == 1


def test_invalid_root_gitignore_pattern_reports_a_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src" / "kept.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("ignored/\n")
    from_lines = discovery.GitIgnoreSpec.from_lines

    def fail_gitignore(lines: Iterable[str]) -> discovery.GitIgnoreSpec:
        values = list(lines)
        if values == ["ignored/"]:
            raise ValueError("invalid pattern")
        return from_lines(values)

    monkeypatch.setattr(discovery.GitIgnoreSpec, "from_lines", fail_gitignore)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == ".gitignore"
    assert result.errors[0].operation == "parse"


def test_invalid_root_gitignore_keeps_configured_excludes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = tmp_path / "src" / "kept.py"
    skipped = tmp_path / "src" / "skip.py"
    kept.parent.mkdir(parents=True)
    kept.write_text("x = 1\n")
    skipped.write_text("x = 1\n")
    (tmp_path / ".gitignore").write_text("invalid/\n")
    from_lines = discovery.GitIgnoreSpec.from_lines

    def fail_gitignore(lines: Iterable[str]) -> discovery.GitIgnoreSpec:
        values = list(lines)
        if values == ["invalid/"]:
            raise ValueError("invalid pattern")
        return from_lines(values)

    monkeypatch.setattr(discovery.GitIgnoreSpec, "from_lines", fail_gitignore)

    result = discover_files(tmp_path, include=("src",), excludes=("src/skip.py",))

    assert result.files == (kept,)
    assert result.files_skipped == 1
    assert result.errors[0].path == ".gitignore"
    assert result.errors[0].operation == "parse"


def test_nested_gitignore_is_applied(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("ignored.py\n")
    (source / "ignored.py").write_text("x = 1\n")
    kept = source / "kept.py"
    kept.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    # 2 skips: ignored.py (matched) + the .gitignore file itself (non-.py)
    assert result.files_skipped == 2


def test_nested_gitignore_patterns_are_relative_to_their_own_directory(tmp_path: Path) -> None:
    source = tmp_path / "src"
    sub = source / "sub"
    sub.mkdir(parents=True)
    # "ignored.py" without a leading slash matches at any depth under src/,
    # including inside src/sub/ — same semantics as a root .gitignore.
    (source / ".gitignore").write_text("ignored.py\n")
    nested_ignored = sub / "ignored.py"
    nested_ignored.write_text("x = 1\n")
    kept = sub / "kept.py"
    kept.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    # 2 skips: ignored.py (matched) + the .gitignore file itself (non-.py)
    assert result.files_skipped == 2


def test_nested_gitignore_pattern_preserves_a_significant_leading_space(tmp_path: Path) -> None:
    # A leading space is part of the pattern per gitwildmatch (verified directly against
    # `GitIgnoreSpec`) -- it must match a file whose name itself starts with a space, not the
    # same name with the space stripped.
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text(" ignored.py\n")
    space_prefixed = source / " ignored.py"
    space_prefixed.write_text("x = 1\n")
    kept = source / "ignored.py"
    kept.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert kept in result.files
    assert space_prefixed not in result.files


def test_nested_gitignore_pattern_preserves_an_escaped_trailing_space(tmp_path: Path) -> None:
    # Trailing whitespace is insignificant per gitwildmatch *unless* escaped with a backslash,
    # in which case it's part of the pattern -- verified directly against `GitIgnoreSpec`.
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("ignored.py\\ \n")
    space_suffixed = source / "ignored.py "
    space_suffixed.write_text("x = 1\n")
    kept = source / "ignored.py"
    kept.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert kept in result.files
    assert space_suffixed not in result.files


def test_nested_gitignore_leading_slash_anchors_to_its_own_directory(tmp_path: Path) -> None:
    source = tmp_path / "src"
    sub = source / "sub"
    sub.mkdir(parents=True)
    # A leading slash anchors the pattern to the directory that owns the .gitignore, so it
    # must match src/ignored.py but not src/sub/ignored.py.
    (source / ".gitignore").write_text("/ignored.py\n")
    anchored_ignored = source / "ignored.py"
    anchored_ignored.write_text("x = 1\n")
    not_anchored = sub / "ignored.py"
    not_anchored.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert anchored_ignored not in result.files
    assert not_anchored in result.files
    assert result.files == (not_anchored,)


def test_nested_gitignore_trailing_slash_directory_pattern_matches_at_any_depth(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    sub = source / "sub"
    sub.mkdir(parents=True)
    # A trailing-slash directory pattern with no other slash matches "build/" at any depth
    # under its owning directory, same as the no-slash file case above.
    (source / ".gitignore").write_text("build/\n")
    direct_build = source / "build" / "direct.py"
    direct_build.parent.mkdir(parents=True)
    direct_build.write_text("x = 1\n")
    nested_build = sub / "build" / "nested.py"
    nested_build.parent.mkdir(parents=True)
    nested_build.write_text("x = 1\n")
    kept = sub / "kept.py"
    kept.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert direct_build not in result.files
    assert nested_build not in result.files
    assert result.files == (kept,)


def test_multi_level_nested_gitignore_files_all_apply(tmp_path: Path) -> None:
    source = tmp_path / "src"
    sub = source / "sub"
    sub.mkdir(parents=True)
    (source / ".gitignore").write_text("from_src.py\n")
    (sub / ".gitignore").write_text("from_sub.py\n")
    (sub / "from_src.py").write_text("x = 1\n")
    (sub / "from_sub.py").write_text("x = 1\n")
    kept = sub / "kept.py"
    kept.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    # 4 skips: from_src.py + from_sub.py (matched) + the two .gitignore files (non-.py)
    assert result.files_skipped == 4


def test_nested_gitignore_negation_overrides_root_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.py\n")
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("!important.py\n")
    important = source / "important.py"
    important.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (important,)


def test_closer_nested_gitignore_negation_overrides_a_farther_one(tmp_path: Path) -> None:
    source = tmp_path / "src"
    sub = source / "sub"
    sub.mkdir(parents=True)
    (source / ".gitignore").write_text("*.py\n")
    (sub / ".gitignore").write_text("!keep.py\n")
    keep = sub / "keep.py"
    keep.write_text("x = 1\n")
    other = sub / "other.py"
    other.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (keep,)
    assert other not in result.files


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # `**` (either form) must not expand to `<prefix>/**/**`, which GitIgnoreSpec matches
        # against the prefix directory itself and, in the `**/` form, against an immediate
        # regular file that git leaves alone.
        ("**/", "src/**/*/"),
        ("**", "src/**/*"),
        # Everything else keeps the documented per-directory semantics.
        ("a.py", "src/**/a.py"),
        ("!a.py", "!src/**/a.py"),
        ("/a.py", "src/a.py"),
        ("!/a.py", "!src/a.py"),
        ("sub/", "src/**/sub/"),
        ("sub/x.py", "src/sub/x.py"),
    ],
)
def test_prefix_pattern_rewrites_nested_patterns_to_root_anchored_equivalents(
    line: str, expected: str
) -> None:
    assert discovery._prefix_pattern("src", line) == expected


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        # A trailing `/**` names a directory's contents, never the directory itself.
        ("build/**", "build/**/*"),
        ("build/**/", "build/**/*/"),
        ("!build/**", "!build/**/*"),
        # A preceding segment ending in a single `*` is an ordinary pattern and must still be
        # rewritten — only a literal `**` before the trailing `/**` is left alone.
        ("a/*/**", "a/*/**/*"),
        ("packages/*/dist/**", "packages/*/dist/**/*"),
        # Already-explicit and unrelated patterns are left exactly as written.
        ("build/**/*", "build/**/*"),
        ("a/**/b.py", "a/**/b.py"),
        ("**", "**"),
        ("a/**/**", "a/**/**"),
        ("# build/**", "# build/**"),
        ("", ""),
    ],
)
def test_normalize_contents_glob_only_rewrites_a_trailing_contents_glob(
    pattern: str, expected: str
) -> None:
    assert discovery._normalize_contents_glob(pattern) == expected


def test_ignored_directory_include_root_is_skipped_without_being_walked(tmp_path: Path) -> None:
    # `_walk` starts *inside* an include root, so the root itself is the one directory
    # `_traversable_dirs` never evaluates. A negation must not resurrect its files.
    (tmp_path / ".gitignore").write_text("src/\n!*.py\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / "tools").mkdir()
    kept = tmp_path / "tools" / "t.py"
    kept.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src", "tools"))

    assert result.files == (kept,)
    assert result.errors == ()


def test_pruned_directory_counts_as_one_skip_not_one_per_contained_file(tmp_path: Path) -> None:
    # Ignored directories are pruned rather than enumerated, which is what makes skipping a
    # large `.venv`/`node_modules` cheap. The reported count follows that: one pruned
    # directory contributes one skip regardless of how many files it holds. Pinned here so
    # the number cannot drift silently the way it did when pruning was introduced.
    (tmp_path / ".gitignore").write_text("gen/\n")
    (tmp_path / "src" / "gen" / "deep").mkdir(parents=True)
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    for relative in ("src/gen/g1.py", "src/gen/g2.py", "src/gen/deep/g3.py"):
        (tmp_path / relative).write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (tmp_path / "src" / "a.py",)
    assert result.files_skipped == 1


def test_directory_names_with_gitignore_metacharacters_are_treated_as_literal(
    tmp_path: Path,
) -> None:
    bracketed = tmp_path / "sub[1]"
    bracketed.mkdir()
    (bracketed / ".gitignore").write_text("secret.py\n")
    secret = bracketed / "secret.py"
    secret.write_text("x = 1\n")
    # A sibling whose name resembles what the (buggy) unescaped bracket pattern would
    # actually match, proving the fix isn't just "nothing matches anymore".
    lookalike = tmp_path / "sub1"
    lookalike.mkdir()
    (lookalike / "secret.py").write_text("x = 1\n")

    result = discover_files(tmp_path, include=("sub[1]", "sub1"))

    assert result.files == (lookalike / "secret.py",)
    assert secret not in result.files


def test_directory_name_starting_with_bang_is_not_read_as_negation(tmp_path: Path) -> None:
    source = tmp_path / "!important"
    source.mkdir()
    (source / ".gitignore").write_text("secret.py\n")
    secret = source / "secret.py"
    secret.write_text("x = 1\n")
    # A control file the .gitignore does not name — without it, `result.files == ()` would
    # also pass if the whole "!important" directory were (wrongly) skipped outright, which
    # wouldn't prove the directory's own .gitignore was correctly read and applied to just
    # the one file it names.
    kept = source / "kept.py"
    kept.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("!important",))

    assert result.files == (kept,)
    assert secret not in result.files


def test_gitignored_directory_is_never_descended_so_nested_negation_cannot_resurrect_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Real git never reads .gitignore files inside a directory it never descends into, so a
    # negation nested inside an excluded directory must not "resurrect" files under it.
    source = tmp_path / "src"
    source.mkdir()
    (tmp_path / ".gitignore").write_text("src/generated/\n")
    generated = source / "generated"
    generated.mkdir()
    nested_ignore = generated / ".gitignore"
    nested_ignore.write_text("!foo.py\n")
    foo = generated / "foo.py"
    foo.write_text("x = 1\n")
    kept = source / "kept.py"
    kept.write_text("x = 1\n")
    read_text = Path.read_text
    read_calls: list[Path] = []

    def spy_read_text(self: Path, *, encoding: str) -> str:
        read_calls.append(self)
        return read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", spy_read_text)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    assert foo not in result.files
    assert result.files_skipped == 1
    # The nested .gitignore was never even read, proving we didn't descend into `generated/`
    # rather than descending and merely discarding its (negating) effect afterward.
    assert nested_ignore not in read_calls


def test_explicit_path_inside_ignored_directory_cannot_be_resurrected_by_nested_negation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same scenario as the walked-directory version above, but reached via an explicit path
    # rather than a directory scan. Explicit paths skip `_traversable_dirs`'s walk-time pruning
    # and go straight to `_combined_gitignore_spec`, which must independently refuse to read a
    # nested .gitignore that lives inside an already-ignored ancestor.
    source = tmp_path / "src"
    source.mkdir()
    (tmp_path / ".gitignore").write_text("src/generated/\n")
    generated = source / "generated"
    generated.mkdir()
    nested_ignore = generated / ".gitignore"
    nested_ignore.write_text("!foo.py\n")
    foo = generated / "foo.py"
    foo.write_text("x = 1\n")
    read_text = Path.read_text
    read_calls: list[Path] = []

    def spy_read_text(self: Path, *, encoding: str) -> str:
        read_calls.append(self)
        return read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", spy_read_text)

    result = discover_files(tmp_path, explicit=(foo,))

    assert result.files == ()
    assert result.files_skipped == 1
    assert nested_ignore not in read_calls


def test_directories_with_identical_accumulated_gitignore_lines_reuse_the_same_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # sibling1/ and sibling2/ each have no .gitignore of their own, so both accumulate the same
    # (root-only) line tuple. Parsing should happen once, not once per directory.
    (tmp_path / ".gitignore").write_text("*.log\n")
    source = tmp_path / "src"
    sibling1 = source / "sibling1"
    sibling2 = source / "sibling2"
    sibling1.mkdir(parents=True)
    sibling2.mkdir(parents=True)
    (sibling1 / "a.py").write_text("x = 1\n")
    (sibling2 / "b.py").write_text("x = 1\n")
    from_lines = discovery.GitIgnoreSpec.from_lines
    parse_call_count = 0

    def counting_from_lines(lines: Iterable[str]) -> discovery.GitIgnoreSpec:
        nonlocal parse_call_count
        parse_call_count += 1
        return from_lines(lines)

    monkeypatch.setattr(discovery.GitIgnoreSpec, "from_lines", counting_from_lines)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (sibling1 / "a.py", sibling2 / "b.py")
    # One parse for BUILTIN_EXCLUDES, one for excludes=(), one to validate the root .gitignore's
    # own lines in `_load_gitignore_lines`, and one to build the combined spec for the
    # (root-only) accumulated lines shared by src/, sibling1/, and sibling2/ — the sibling
    # directories reuse that fourth parse's result via `spec_by_lines_cache` instead of
    # triggering a fifth and sixth call.
    assert parse_call_count == 4


def test_no_gitignore_disables_nested_gitignore_too(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("ignored.py\n")
    ignored = source / "ignored.py"
    ignored.write_text("x = 1\n")

    result = discover_files(tmp_path, include=("src",), use_gitignore=False)

    assert result.files == (ignored,)


def test_nested_gitignore_applies_when_explicit_path_starts_below_it(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("ignored.py\n")
    ignored = source / "ignored.py"
    ignored.write_text("x = 1\n")

    result = discover_files(tmp_path, explicit=(ignored,))

    assert result.files == ()
    assert result.files_skipped == 1


def test_invalid_nested_gitignore_pattern_reports_a_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    kept = source / "kept.py"
    kept.write_text("x = 1\n")
    (source / ".gitignore").write_text("ignored/\n")
    from_lines = discovery.GitIgnoreSpec.from_lines

    def fail_gitignore(lines: Iterable[str]) -> discovery.GitIgnoreSpec:
        values = list(lines)
        if values == ["ignored/"]:
            raise ValueError("invalid pattern")
        return from_lines(values)

    monkeypatch.setattr(discovery.GitIgnoreSpec, "from_lines", fail_gitignore)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == "src/.gitignore"
    assert result.errors[0].operation == "parse"


def test_unreadable_nested_gitignore_reports_an_error_and_keeps_reachable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    kept = source / "kept.py"
    kept.write_text("x = 1\n")
    ignore = source / ".gitignore"
    ignore.write_text("ignored/\n")
    read_text = Path.read_text

    def fail_read_text(self: Path, *, encoding: str) -> str:
        if self == ignore:
            raise OSError("permission denied")
        return read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == "src/.gitignore"
    assert result.errors[0].operation == "read"


def test_unreadable_root_gitignore_reports_an_error_and_keeps_reachable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src" / "kept.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n")
    ignore = tmp_path / ".gitignore"
    ignore.write_text("ignored/\n")
    read_text = Path.read_text

    def fail_read_text(self: Path, *, encoding: str) -> str:
        if self == ignore:
            raise OSError("permission denied")
        return read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == ".gitignore"
    assert result.errors[0].operation == "read"


def test_undecodable_root_gitignore_reports_an_error_and_keeps_reachable_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "kept.py"
    source.parent.mkdir(parents=True)
    source.write_text("x = 1\n")
    (tmp_path / ".gitignore").write_bytes(b"\xff")

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == ".gitignore"
    assert result.errors[0].operation == "read"


def test_resolve_error_reports_traversal_and_keeps_other_include_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    kept = tmp_path / "kept" / "source.py"
    kept.parent.mkdir()
    kept.write_text("x = 1\n")
    resolve = Path.resolve

    def fail_broken_resolve(self: Path, *, strict: bool = False) -> Path:
        if self == broken:
            raise OSError("permission denied")
        return resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_broken_resolve)

    result = discover_files(tmp_path, include=("broken", "kept"))

    assert result.files == (kept,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == "broken"
    assert result.errors[0].operation == "resolve"


def test_direct_symlink_file_is_safe_only_when_target_is_in_root(tmp_path: Path) -> None:
    inside = tmp_path / "inside.py"
    inside.write_text("x = 1\n")
    outside = tmp_path.parent / "outside-house-lint.py"
    outside.write_text("x = 1\n")
    try:
        inside_link = tmp_path / "inside-link.py"
        outside_link = tmp_path / "outside-link.py"
        inside_link.symlink_to(inside)
        outside_link.symlink_to(outside)

        assert discover_files(tmp_path, explicit=(inside_link,)).files == (inside_link,)
        with pytest.raises(ValueError, match="outside root"):
            discover_files(tmp_path, explicit=(outside_link,))
    finally:
        outside.unlink()


def test_walked_file_symlinks_are_not_selected(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    target = tmp_path / "target.py"
    target.write_text("x = 1\n")
    (source / "link.py").symlink_to(target)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == ()
    assert result.files_skipped == 1


def test_empty_full_scan_is_explicitly_clean(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("x = 1\n")

    result = discover_files(tmp_path, include=())

    assert result.files == ()
    assert result.files_skipped == 0
    assert result.errors == ()


def test_missing_implicit_include_root_is_an_empty_scan(tmp_path: Path) -> None:
    result = discover_files(tmp_path, include=("missing",))

    assert result.files == ()
    assert result.errors == ()


def test_nested_directory_symlink_reports_error_and_keeps_reachable_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "kept.py").write_text("x = 1\n")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "hidden.py").write_text("x = 1\n")
    (source / "linked").symlink_to(linked, target_is_directory=True)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source / "kept.py",)
    assert len(result.errors) == 1
    assert isinstance(result.errors[0], LintError)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == "src/linked"


def test_walker_error_reports_failed_directory_and_keeps_reachable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    kept = source / "kept.py"
    kept.write_text("x = 1\n")
    restricted = source / "restricted"

    def failing_walk(
        directory: Path,
        *,
        topdown: bool,
        followlinks: bool,
        onerror: object,
    ) -> object:
        assert topdown is True
        assert followlinks is False
        yield str(directory), [], [kept.name]
        assert callable(onerror)
        onerror(PermissionError(13, "Permission denied", str(restricted)))

    monkeypatch.setattr(discovery.os, "walk", failing_walk)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == "src/restricted"


def test_strict_path_failure_exposes_lint_error_conversion(tmp_path: Path) -> None:
    missing = tmp_path / "missing.py"

    with pytest.raises(DiscoveryError) as raised:
        discover_files(tmp_path, explicit=(missing,))

    assert isinstance(raised.value.error, LintError)
    assert raised.value.error.kind == "path"


def test_file_guardrail_preserves_prior_files_and_reports_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for name in ("a.py", "b.py", "c.py"):
        (source / name).write_text("x = 1\n")
    monkeypatch.setattr(discovery, "MAX_DISCOVERED_FILES", 2)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source / "a.py", source / "b.py")
    assert result.errors[0].kind == "budget"


def test_root_and_config_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL001"]\n')
    child = tmp_path / "nested"
    child.mkdir()
    monkeypatch.chdir(child)

    resolution = resolve_project()

    assert resolution.root == tmp_path
    assert resolution.config == tmp_path / "pyproject.toml"


def test_explicit_config_must_be_inside_explicit_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config = tmp_path / "outside.toml"
    config.write_text("[tool.house-lint]\n")
    with pytest.raises(ConfigError, match="inside root"):
        resolve_project(root=root, config=config)


def test_explicit_config_without_root_uses_config_parent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = project / "custom.toml"
    config_path.write_text("[tool.house-lint]\n")

    resolution = resolve_project(config=config_path)

    assert resolution == type(resolution)(project, config_path)


def test_explicit_root_accepts_an_in_root_explicit_config(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path = root / "config.toml"
    config_path.write_text("[tool.house-lint]\n")

    resolution = resolve_project(root=root, config=config_path)

    assert resolution == type(resolution)(root, config_path)


def test_explicit_root_loads_only_its_own_config(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path = root / "pyproject.toml"
    config_path.write_text("[tool.house-lint]\n")

    resolution = resolve_project(root=root)

    assert resolution == type(resolution)(root, config_path)


def test_explicit_root_does_not_search_parent_config(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.house-lint]\n")
    root = tmp_path / "root"
    root.mkdir()

    resolution = resolve_project(root=root)

    assert resolution.root == root
    assert resolution.config is None


def test_upward_search_prefers_configured_pyproject_over_fallback_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.house-lint]\n")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "pyproject.toml").write_text("[project]\nname = 'nested'\n")
    cwd = nested / "src"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    resolution = resolve_project()

    assert resolution.root == tmp_path
    assert resolution.config == tmp_path / "pyproject.toml"


@pytest.mark.parametrize("marker", [".git", "pyproject.toml"])
def test_upward_search_falls_back_to_nearest_project_marker(tmp_path: Path, marker: str) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    if marker == ".git":
        (root / marker).mkdir()
    else:
        (root / marker).write_text("[project]\nname = 'root'\n")

    resolution = resolve_project(cwd=nested)

    assert resolution == type(resolution)(root, None)


def test_invalid_ancestor_pyproject_is_a_configuration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.house-lint\n")
    child = tmp_path / "nested"
    child.mkdir()
    monkeypatch.chdir(child)

    with pytest.raises(ConfigError, match="invalid project configuration"):
        resolve_project()


def test_without_markers_falls_back_to_the_current_working_directory(tmp_path: Path) -> None:
    cwd = tmp_path / "unmarked" / "nested"
    cwd.mkdir(parents=True)

    resolution = resolve_project(cwd=cwd)

    assert resolution.root == cwd
    assert resolution.config is None
