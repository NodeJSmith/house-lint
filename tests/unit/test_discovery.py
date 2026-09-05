from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from house_lint import discovery
from house_lint.config import STANDALONE_CONFIG_NAMES, ConfigError
from house_lint.discovery import DiscoveryError, ProjectResolution, discover_files, resolve_project
from house_lint.results import LintError

PY_CONTENT = "x = 1\n"


@pytest.fixture
def read_text_spy(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Records every `Path.read_text` call while active, for asserting a file was never read."""
    read_text = Path.read_text
    read_calls: list[Path] = []

    def spy_read_text(self: Path, *, encoding: str) -> str:
        read_calls.append(self)
        return read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", spy_read_text)
    return read_calls


def test_builtin_excludes_matches_ruffs_default_exclude_list_plus_house_lint_extras() -> None:
    # Pins `BUILTIN_EXCLUDES` to Ruff's full default exclude list (25 entries, per
    # `ruff check --isolated --show-settings` against the locked ruff version -- `--isolated`
    # matters because this repo's own `pyproject.toml` sets `exclude = ["design"]`, which replaces
    # Ruff's defaults rather than extending them) plus house-lint's own extras: `__pycache__/`
    # and `.house-lint-cache/` (from `cache.CACHE_DIRNAME`) -- 27 total. Ruff has no concept of
    # house-lint's own cache directory, so it is not part of the Ruff-parity set; it is load-
    # bearing for a different reason -- once the default include scans from the project root,
    # a default scan would otherwise walk into house-lint's own `.house-lint-cache/` and
    # enumerate its version marker, `.gitignore`, and cached `<hash>.json` entries as skipped
    # non-Python files. A silent shrink of this tuple would widen the default scan surface
    # without any other test catching it.
    assert discovery.BUILTIN_EXCLUDES == (
        ".bzr/",
        ".direnv/",
        ".eggs/",
        ".git/",
        ".git-rewrite/",
        ".hg/",
        ".house-lint-cache/",
        ".ipynb_checkpoints/",
        ".mypy_cache/",
        ".nox/",
        ".pants.d/",
        ".pyenv/",
        ".pytest_cache/",
        ".pytype/",
        ".ruff_cache/",
        ".svn/",
        ".tox/",
        ".venv/",
        ".vscode/",
        "__pycache__/",
        "__pypackages__/",
        "_build/",
        "buck-out/",
        "dist/",
        "node_modules/",
        "site-packages/",
        "venv/",
    )
    assert len(discovery.BUILTIN_EXCLUDES) == 27


def test_full_scan_applies_builtin_gitignore_configured_excludes_and_sorting(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("ignored/\n")
    for relative in ("src/z.py", "src/a.py", "ignored/x.py", ".venv/v.py", "notes.txt"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=("src", "ignored", ".venv"), excludes=("src/z.py",))

    assert result.files == (tmp_path / "src/a.py",)
    assert result.errors == ()
    assert result.files_skipped == 3


def test_no_path_scan_discovers_files_anywhere_under_root(tmp_path: Path) -> None:
    # Default include is now `(".",)` -- a root scan must find Python files in non-standard
    # directories (e.g. `packages/`), not just the previously hardcoded roots, while builtin
    # excludes still prune vendored/tooling directories like `.venv/` and `.git/`.
    expected: list[Path] = []
    for root_name in ("src", "tests", "scripts", "tools", "examples", "packages", "lib"):
        path = tmp_path / root_name / f"{root_name}.py"
        path.parent.mkdir(parents=True)
        path.write_text(PY_CONTENT)
        expected.append(path)
    top_level = tmp_path / "top.py"
    top_level.write_text(PY_CONTENT)
    expected.append(top_level)
    for excluded_dir in (".venv", ".git", "__pycache__", "node_modules", ".house-lint-cache"):
        excluded_path = tmp_path / excluded_dir / "excluded.py"
        excluded_path.parent.mkdir(parents=True)
        excluded_path.write_text(PY_CONTENT)

    result = discover_files(tmp_path)

    assert result.files == tuple(sorted(expected))
    for excluded_dir in (".venv", ".git", "__pycache__", "node_modules", ".house-lint-cache"):
        assert not any(excluded_dir in path.parts for path in result.files), (
            f"{excluded_dir} should be pruned by BUILTIN_EXCLUDES"
        )


def test_explicit_paths_are_strict_and_directories_recursive(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    py_file = source / "a.py"
    py_file.write_text(PY_CONTENT)
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
    (tmp_path / "ignored.py").write_text(PY_CONTENT)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "kept-out.py").write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=("ignored.py", ".venv"), use_gitignore=False)

    assert result.files == (tmp_path / "ignored.py",)


def test_root_gitignore_cannot_negate_builtin_excludes(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("!.venv/\n")
    excluded = tmp_path / ".venv" / "kept-out.py"
    excluded.parent.mkdir()
    excluded.write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=(".venv",))

    assert result.files == ()
    assert result.files_skipped == 1


def test_invalid_root_gitignore_pattern_reports_a_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src" / "kept.py"
    source.parent.mkdir(parents=True)
    source.write_text(PY_CONTENT)
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
    kept.write_text(PY_CONTENT)
    skipped.write_text(PY_CONTENT)
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
    (source / "ignored.py").write_text(PY_CONTENT)
    kept = source / "kept.py"
    kept.write_text(PY_CONTENT)

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
    nested_ignored.write_text(PY_CONTENT)
    kept = sub / "kept.py"
    kept.write_text(PY_CONTENT)

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
    space_prefixed.write_text(PY_CONTENT)
    kept = source / "ignored.py"
    kept.write_text(PY_CONTENT)

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
    space_suffixed.write_text(PY_CONTENT)
    kept = source / "ignored.py"
    kept.write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=("src",))

    assert kept in result.files
    assert space_suffixed not in result.files


def test_root_gitignore_pattern_preserves_an_escaped_trailing_space() -> None:
    # Same backslash-parity check as the nested-directory case above, but exercised directly
    # against _build_patterns/_match_patterns rather than through full file discovery. A file
    # literally named "ignored.py " (trailing space) never reaches gitignore matching at all --
    # `_consider` filters it out earlier via its `path.suffix != ".py"` check, since
    # Path("ignored.py ").suffix is ".py " (space included), not ".py" -- so routing this through
    # `discover_files` can't actually observe the escape-parity behavior for the excluded side.
    # `_build_patterns` is the one code path root and nested `.gitignore`s both funnel through
    # (`_own_matcher(self.root)` for root, `_own_matcher(ancestor)` for nested), so calling it
    # directly with root-shaped input closes the symmetry gap without depending on file-suffix
    # filtering.
    patterns = _build_patterns_or_empty(("ignored.py\\ ",))

    # If the escaped trailing space were wrongly stripped, the pattern would collapse to
    # "ignored.py" and this would incorrectly return True instead of None.
    assert discovery._match_patterns(patterns, "ignored.py", is_dir=False) is None
    # The pattern with its escaped space intact matches only the exact space-suffixed name.
    assert discovery._match_patterns(patterns, "ignored.py ", is_dir=False) is True


def test_nested_gitignore_leading_slash_anchors_to_its_own_directory(tmp_path: Path) -> None:
    source = tmp_path / "src"
    sub = source / "sub"
    sub.mkdir(parents=True)
    # A leading slash anchors the pattern to the directory that owns the .gitignore, so it
    # must match src/ignored.py but not src/sub/ignored.py.
    (source / ".gitignore").write_text("/ignored.py\n")
    anchored_ignored = source / "ignored.py"
    anchored_ignored.write_text(PY_CONTENT)
    not_anchored = sub / "ignored.py"
    not_anchored.write_text(PY_CONTENT)

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
    direct_build.write_text(PY_CONTENT)
    nested_build = sub / "build" / "nested.py"
    nested_build.parent.mkdir(parents=True)
    nested_build.write_text(PY_CONTENT)
    kept = sub / "kept.py"
    kept.write_text(PY_CONTENT)

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
    (sub / "from_src.py").write_text(PY_CONTENT)
    (sub / "from_sub.py").write_text(PY_CONTENT)
    kept = sub / "kept.py"
    kept.write_text(PY_CONTENT)

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
    important.write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (important,)


def test_closer_nested_gitignore_negation_overrides_a_farther_one(tmp_path: Path) -> None:
    source = tmp_path / "src"
    sub = source / "sub"
    sub.mkdir(parents=True)
    (source / ".gitignore").write_text("*.py\n")
    (sub / ".gitignore").write_text("!keep.py\n")
    keep = sub / "keep.py"
    keep.write_text(PY_CONTENT)
    other = sub / "other.py"
    other.write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (keep,)
    assert other not in result.files


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
def test_normalized_gitignore_line_only_rewrites_a_trailing_contents_glob(
    pattern: str, expected: str
) -> None:
    assert discovery._normalized_gitignore_line(pattern) == expected


def _build_patterns_or_empty(lines: tuple[str, ...]) -> discovery.IgnorePatterns:
    """Test helper: `discovery._build_patterns` with errors discarded, for tests not exercising
    failure."""
    return discovery._build_patterns(lines, lambda _message: None)


def test_match_patterns_directory_only_pattern_matches_when_is_dir_true() -> None:
    patterns = _build_patterns_or_empty(("build/",))

    assert discovery._match_patterns(patterns, "build", is_dir=True) is True


def test_match_patterns_directory_only_pattern_is_skipped_when_is_dir_false() -> None:
    patterns = _build_patterns_or_empty(("build/",))

    assert discovery._match_patterns(patterns, "build", is_dir=False) is None


def test_match_patterns_last_match_wins_negation_overrides_a_wildcard_ignore() -> None:
    patterns = _build_patterns_or_empty(("*.py", "!a.py"))

    assert discovery._match_patterns(patterns, "a.py", is_dir=False) is False


def test_match_patterns_negation_wins_over_an_earlier_exact_ignore() -> None:
    patterns = _build_patterns_or_empty(("a.py", "!a.py"))

    assert discovery._match_patterns(patterns, "a.py", is_dir=False) is False


def test_match_patterns_empty_tuple_has_no_opinion() -> None:
    assert discovery._match_patterns((), "a.py", is_dir=False) is None


def test_match_patterns_file_probe_matches_a_non_directory_only_pattern() -> None:
    patterns = _build_patterns_or_empty(("*.py",))

    assert discovery._match_patterns(patterns, "a.py", is_dir=False) is True


def test_match_patterns_anchored_ambiguous_prefix_does_not_match_a_deeper_probe() -> None:
    # "src/sub" is anchored (has an embedded slash) but has no trailing slash, so it is ambiguous:
    # it could mean the file "src/sub" or the directory "src/sub". A deeper probe like
    # "src/sub/deep" matches "src/sub" only as a directory-boundary prefix, with path remaining
    # after the boundary -- the _DIR_MARK guard must reject that partial match rather than letting
    # it stand in for a full match on the deeper probe.
    patterns = _build_patterns_or_empty(("src/sub",))
    assert [is_anchored for _pattern, _is_dir_only, is_anchored in patterns] == [True]

    assert discovery._match_patterns(patterns, "src/sub/deep", is_dir=False) is None


def test_build_patterns_produces_correct_length_and_is_dir_only_flags() -> None:
    patterns = _build_patterns_or_empty(("*.py", "build/", "!a.py"))

    assert len(patterns) == 3
    assert [is_dir_only for _pattern, is_dir_only, _is_anchored in patterns] == [
        False,
        True,
        False,
    ]


def test_build_patterns_marks_unanchored_patterns_as_not_anchored() -> None:
    # "*.py" and "build/" have no embedded slash -- gitignore matches them at any depth below
    # their owning directory, so they must not be threaded a full multi-segment relative path.
    patterns = _build_patterns_or_empty(("*.py", "build/"))

    assert [is_anchored for _pattern, _is_dir_only, is_anchored in patterns] == [False, False]


def test_build_patterns_marks_slash_containing_patterns_as_anchored() -> None:
    # An embedded or leading slash pins the pattern to its owning directory's own depth.
    patterns = _build_patterns_or_empty(("sub/x.py", "/a.py"))

    assert [is_anchored for _pattern, _is_dir_only, is_anchored in patterns] == [True, True]


def test_build_patterns_marks_a_collapsed_double_star_run_as_not_anchored() -> None:
    # "**/**/" collapses (per git's own rule) to exactly what "**/" means: an unanchored
    # "matches every directory at any depth" pattern -- despite the raw text having a "middle"
    # slash between the two "**" segments, which a naive text-based check would misread as
    # anchoring it.
    patterns = _build_patterns_or_empty(("**/**/",))

    assert [is_anchored for _pattern, _is_dir_only, is_anchored in patterns] == [False]


def test_build_patterns_does_not_collapse_a_double_star_fused_to_a_literal_segment() -> None:
    # "foo**/**" is NOT a double-star run -- the leading "**" is fused to the literal "foo",
    # forming its own ordinary (non-recursive) segment, not a standalone "**" path component.
    # git keeps this pattern anchored to its owning directory (verified against real
    # `git check-ignore`: a nested "foo"-prefixed directory two levels below the owning
    # directory is not ignored, only one directly inside it). Collapsing "foo**/**" down to
    # "foo**" would erase the embedded slash and misclassify it as unanchored.
    patterns = _build_patterns_or_empty(("foo**/**",))

    assert [is_anchored for _pattern, _is_dir_only, is_anchored in patterns] == [True]


def test_build_patterns_marks_leading_double_star_slash_patterns_as_anchored() -> None:
    # Gitignore-unanchored (match at any depth), but is_anchored=True here because
    # _match_patterns uses this flag for path truncation: these patterns need the full
    # multi-segment path, and pathspec's regex already handles any-depth via (?:.+/)?.
    # See the leading-**/ parity scenarios for differential proof.
    patterns = _build_patterns_or_empty(("**/x/**foo", "**/sub/deep.py"))

    assert [is_anchored for _pattern, _is_dir_only, is_anchored in patterns] == [True, True]


def test_build_patterns_returns_an_empty_tuple_and_the_error_on_parse_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_gitignore(lines: Iterable[str]) -> discovery.GitIgnoreSpec:
        raise ValueError("invalid pattern")

    monkeypatch.setattr(discovery.GitIgnoreSpec, "from_lines", fail_gitignore)
    errors: list[str] = []

    patterns = discovery._build_patterns(("*.py",), errors.append)

    assert patterns == ()
    assert errors == ["invalid pattern"]


def test_nested_gitignore_normalization_failure_is_reported_as_a_combine_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A line valid in `_load_gitignore_lines`'s raw-parse check can still fail once
    `_build_patterns` rewrites it (`_normalized_gitignore_line`'s trailing-`/**` rewrite). That
    failure must be surfaced via `self.errors` by `_own_matcher`, not silently dropped -- an empty
    matcher for this directory would otherwise let the stack evaluator fall through to an outer
    directory's matcher, which can silently re-exclude a file git would include."""
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("build/**\n")
    kept = source / "kept.py"
    kept.write_text(PY_CONTENT)
    from_lines = discovery.GitIgnoreSpec.from_lines

    def fail_on_normalized(lines: Iterable[str]) -> discovery.GitIgnoreSpec:
        values = list(lines)
        if values == ["build/**/*"]:
            raise ValueError("invalid pattern")
        return from_lines(values)

    monkeypatch.setattr(discovery.GitIgnoreSpec, "from_lines", fail_on_normalized)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    combine_errors = [error for error in result.errors if error.operation == "combine"]
    assert len(combine_errors) == 1
    assert combine_errors[0].kind == "traversal"
    assert combine_errors[0].path == "src"


def test_root_gitignore_normalization_failure_is_reported_as_a_combine_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root `.gitignore`'s combine failure must be reported with the human-readable path
    `.gitignore`, not `.` (the result of `path.relative_to(self.root)` for the root itself) --
    matching the convention `_own_gitignore_lines`'s `on_error` callback already uses for the
    same file's read/parse failures."""
    (tmp_path / ".gitignore").write_text("build/**\n")
    (tmp_path / "src").mkdir()
    kept = tmp_path / "src" / "kept.py"
    kept.write_text(PY_CONTENT)
    from_lines = discovery.GitIgnoreSpec.from_lines

    def fail_on_normalized(lines: Iterable[str]) -> discovery.GitIgnoreSpec:
        values = list(lines)
        if values == ["build/**/*"]:
            raise ValueError("invalid pattern")
        return from_lines(values)

    monkeypatch.setattr(discovery.GitIgnoreSpec, "from_lines", fail_on_normalized)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    combine_errors = [error for error in result.errors if error.operation == "combine"]
    assert len(combine_errors) == 1
    assert combine_errors[0].kind == "traversal"
    assert combine_errors[0].path == ".gitignore"


def test_ignored_directory_include_root_is_skipped_without_being_walked(tmp_path: Path) -> None:
    # `_walk` starts *inside* an include root, so the root itself is the one directory
    # `_traversable_dirs` never evaluates. A negation must not resurrect its files.
    (tmp_path / ".gitignore").write_text("src/\n!*.py\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(PY_CONTENT)
    (tmp_path / "tools").mkdir()
    kept = tmp_path / "tools" / "t.py"
    kept.write_text(PY_CONTENT)

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
    (tmp_path / "src" / "a.py").write_text(PY_CONTENT)
    for relative in ("src/gen/g1.py", "src/gen/g2.py", "src/gen/deep/g3.py"):
        (tmp_path / relative).write_text(PY_CONTENT)

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
    secret.write_text(PY_CONTENT)
    # A sibling whose name resembles what the (buggy) unescaped bracket pattern would
    # actually match, proving the fix isn't just "nothing matches anymore".
    lookalike = tmp_path / "sub1"
    lookalike.mkdir()
    (lookalike / "secret.py").write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=("sub[1]", "sub1"))

    assert result.files == (lookalike / "secret.py",)
    assert secret not in result.files


def test_directory_name_starting_with_bang_is_not_read_as_negation(tmp_path: Path) -> None:
    source = tmp_path / "!important"
    source.mkdir()
    (source / ".gitignore").write_text("secret.py\n")
    secret = source / "secret.py"
    secret.write_text(PY_CONTENT)
    # A control file the .gitignore does not name — without it, `result.files == ()` would
    # also pass if the whole "!important" directory were (wrongly) skipped outright, which
    # wouldn't prove the directory's own .gitignore was correctly read and applied to just
    # the one file it names.
    kept = source / "kept.py"
    kept.write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=("!important",))

    assert result.files == (kept,)
    assert secret not in result.files


def test_gitignored_directory_is_never_descended_so_nested_negation_cannot_resurrect_files(
    tmp_path: Path, read_text_spy: list[Path]
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
    foo.write_text(PY_CONTENT)
    kept = source / "kept.py"
    kept.write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (kept,)
    assert foo not in result.files
    assert result.files_skipped == 1
    # The nested .gitignore was never even read, proving we didn't descend into `generated/`
    # rather than descending and merely discarding its (negating) effect afterward.
    assert nested_ignore not in read_text_spy


def test_explicit_path_inside_ignored_directory_cannot_be_resurrected_by_nested_negation(
    tmp_path: Path, read_text_spy: list[Path]
) -> None:
    # Same scenario as the walked-directory version above, but reached via an explicit path
    # rather than a directory scan. Explicit paths skip `_traversable_dirs`'s walk-time pruning
    # and go straight to `_is_gitignore_excluded`, which must independently refuse to read a
    # nested .gitignore that lives inside an already-ignored ancestor.
    source = tmp_path / "src"
    source.mkdir()
    (tmp_path / ".gitignore").write_text("src/generated/\n")
    generated = source / "generated"
    generated.mkdir()
    nested_ignore = generated / ".gitignore"
    nested_ignore.write_text("!foo.py\n")
    foo = generated / "foo.py"
    foo.write_text(PY_CONTENT)

    result = discover_files(tmp_path, explicit=(foo,))

    assert result.files == ()
    assert result.files_skipped == 1
    assert nested_ignore not in read_text_spy


def test_explicit_path_inside_excluded_directory_cannot_be_resurrected_by_a_negated_exclude(
    tmp_path: Path,
) -> None:
    # The `exclude`-config counterpart of the .gitignore case above. A walk prunes `generated`
    # and never reaches the negation, so the two entry points disagreed: a full scan skipped the
    # file and naming it explicitly linted it.
    generated = tmp_path / "src" / "generated"
    generated.mkdir(parents=True)
    foo = generated / "foo.py"
    foo.write_text(PY_CONTENT)
    excludes = ("src/generated/", "!src/generated/foo.py")

    explicit = discover_files(tmp_path, explicit=(foo,), excludes=excludes, use_gitignore=False)
    walked = discover_files(tmp_path, include=("src",), excludes=excludes, use_gitignore=False)

    assert explicit.files == ()
    assert explicit.files_skipped == 1
    assert walked.files == ()


def test_explicit_directory_below_an_excluded_directory_cannot_be_resurrected(
    tmp_path: Path,
) -> None:
    # The directory-branch counterpart. A bare `src/generated/` already matches everything
    # beneath it, so the ancestor check only earns its keep once a negation re-includes the
    # subdirectory: last-matching-line-wins then hands back a directory git considers excluded.
    nested = tmp_path / "src" / "generated" / "nested"
    nested.mkdir(parents=True)
    (nested / "a.py").write_text(PY_CONTENT)

    result = discover_files(
        tmp_path,
        explicit=(nested,),
        excludes=("src/generated/", "!src/generated/nested/"),
        use_gitignore=False,
    )

    assert result.files == ()


def _fail_build(message: str) -> None:
    raise AssertionError(f"builtin excludes failed to compile: {message}")


def _make_selector(root: Path, *, use_gitignore: bool = True) -> discovery._FileSelector:
    return discovery._FileSelector(
        root=root,
        builtin_patterns=discovery._build_patterns(discovery.BUILTIN_EXCLUDES, _fail_build),
        exclude_patterns=(),
        errors=[],
        use_gitignore=use_gitignore,
    )


def test_is_gitignore_excluded_innermost_matcher_wins_over_outermost(tmp_path: Path) -> None:
    # Root ignores every `.py` file; `src/.gitignore` negates one back in. The negation in the
    # closer, more-specific `.gitignore` must win over the farther, less-specific one.
    source = tmp_path / "src"
    source.mkdir()
    (tmp_path / ".gitignore").write_text("*.py\n")
    (source / ".gitignore").write_text("!kept.py\n")
    selector = _make_selector(tmp_path)

    assert selector._is_gitignore_excluded(source, "kept.py", is_dir=False) is False


def test_is_gitignore_excluded_outermost_fallback_when_inner_has_no_opinion(tmp_path: Path) -> None:
    # `src/.gitignore` has patterns, but none of them mention `kept.py` -- the stack must fall
    # back to the root matcher instead of treating the inner directory's silence as "not
    # excluded".
    source = tmp_path / "src"
    source.mkdir()
    (tmp_path / ".gitignore").write_text("kept.py\n")
    (source / ".gitignore").write_text("other.py\n")
    selector = _make_selector(tmp_path)

    assert selector._is_gitignore_excluded(source, "kept.py", is_dir=False) is True


def test_is_gitignore_excluded_ancestor_exclusion_short_circuits_before_reading_descendant(
    tmp_path: Path, read_text_spy: list[Path]
) -> None:
    # `src/generated/` is excluded by the root .gitignore. Real git never reads a `.gitignore`
    # inside a directory it never descends into, so the nested file's negation must not even be
    # read, let alone win.
    (tmp_path / ".gitignore").write_text("src/generated/\n")
    generated = tmp_path / "src" / "generated"
    generated.mkdir(parents=True)
    nested_ignore = generated / ".gitignore"
    nested_ignore.write_text("!foo.py\n")
    selector = _make_selector(tmp_path)

    assert selector._is_gitignore_excluded(generated, "foo.py", is_dir=False) is True
    assert nested_ignore not in read_text_spy


def test_is_gitignore_excluded_returns_false_immediately_when_use_gitignore_disabled(
    tmp_path: Path, read_text_spy: list[Path]
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("kept.py\n")
    selector = _make_selector(tmp_path, use_gitignore=False)

    assert selector._is_gitignore_excluded(source, "kept.py", is_dir=False) is False
    assert read_text_spy == []


def test_directory_gitignore_context_is_cached_per_directory(tmp_path: Path) -> None:
    # A second call for the same directory must return the exact cached tuple rather than
    # rebuilding it -- proves the cache is actually consulted, not just present and unused.
    source = tmp_path / "src" / "nested"
    source.mkdir(parents=True)
    (tmp_path / ".gitignore").write_text("*.py\n")
    selector = _make_selector(tmp_path)

    first = selector._directory_gitignore_context(source)
    second = selector._directory_gitignore_context(source)

    assert second is first
    assert selector.directory_gitignore_context_cache[source] is first


def test_is_gitignore_excluded_reuses_directory_context_across_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every file in the same directory shares an identical ancestor verdict and pattern stack.
    # `_ancestor_chain` is only invoked while building that stack, so two files sharing a
    # directory must trigger exactly one call, not one per file -- this is the redundant
    # per-file recomputation the caching fix eliminates.
    source = tmp_path / "src" / "nested"
    source.mkdir(parents=True)
    (tmp_path / ".gitignore").write_text("kept.py\nother.py\n")
    selector = _make_selector(tmp_path)
    original_ancestor_chain = discovery._FileSelector._ancestor_chain
    calls: list[Path] = []

    def spy_ancestor_chain(self: discovery._FileSelector, directory: Path) -> list[Path]:
        calls.append(directory)
        return original_ancestor_chain(self, directory)

    monkeypatch.setattr(discovery._FileSelector, "_ancestor_chain", spy_ancestor_chain)

    assert selector._is_gitignore_excluded(source, "kept.py", is_dir=False) is True
    assert selector._is_gitignore_excluded(source, "other.py", is_dir=False) is True

    assert calls == [source]


def test_no_gitignore_disables_nested_gitignore_too(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("ignored.py\n")
    ignored = source / "ignored.py"
    ignored.write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=("src",), use_gitignore=False)

    assert result.files == (ignored,)


def test_nested_gitignore_applies_when_explicit_path_starts_below_it(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("ignored.py\n")
    ignored = source / "ignored.py"
    ignored.write_text(PY_CONTENT)

    result = discover_files(tmp_path, explicit=(ignored,))

    assert result.files == ()
    assert result.files_skipped == 1


def test_explicit_directory_spelled_through_dotdot_ignores_the_traversed_sibling(
    tmp_path: Path,
) -> None:
    """A `..` in an explicit directory must not make the directory it steps out of an ancestor.

    `src/../tests` names `tests`, whose only ignore-file ancestor is the root — `src` is not
    above the resolved target and its `.gitignore` has no say. Matching the unresolved spelling
    walked `src` as an ancestor and applied its patterns, so `check src/../tests` silently
    skipped files that `check tests` selects. Mirrors the rule `per-file-ignores` already
    follows (`docs/configuration.md`): match the resolved location, not the spelling used to
    reach it.
    """
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("*.py\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    kept = tests / "a.py"
    kept.write_text(PY_CONTENT)

    direct = discover_files(tmp_path, explicit=(tests,))
    through_dotdot = discover_files(tmp_path, explicit=(source / ".." / "tests",))

    assert direct.files == (kept,)
    assert through_dotdot.files == (kept,)
    assert through_dotdot.files_skipped == 0


def test_explicit_file_spelled_through_dotdot_ignores_the_traversed_sibling(
    tmp_path: Path,
) -> None:
    """The same rule as the directory case, on the branch an explicit *file* takes.

    Fixing only the directory branch left this one walking the unresolved spelling, so
    `check src/../tests/a.py` still applied `src/.gitignore` to a file under `tests`. The two
    branches have to agree: whichever way a path is named, its ignore ancestry is decided by
    where it resolves to.
    """
    source = tmp_path / "src"
    source.mkdir()
    (source / ".gitignore").write_text("*.py\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    kept = tests / "a.py"
    kept.write_text(PY_CONTENT)

    direct = discover_files(tmp_path, explicit=(kept,))
    through_dotdot = discover_files(tmp_path, explicit=(source / ".." / "tests" / "a.py",))

    assert direct.files == (kept,)
    assert through_dotdot.files_skipped == 0
    assert [path.resolve() for path in through_dotdot.files] == [kept]


def test_invalid_nested_gitignore_pattern_reports_a_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    kept = source / "kept.py"
    kept.write_text(PY_CONTENT)
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
    kept.write_text(PY_CONTENT)
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
    source.write_text(PY_CONTENT)
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
    source.write_text(PY_CONTENT)
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
    kept.write_text(PY_CONTENT)
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
    inside.write_text(PY_CONTENT)
    outside = tmp_path.parent / "outside-house-lint.py"
    outside.write_text(PY_CONTENT)
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


def test_result_maps_each_file_to_the_resolved_target_it_validated(tmp_path: Path) -> None:
    """The scan reads `resolved_paths[file]`, not its own fresh `resolve()`, so containment is
    checked and the read is performed against the same target — see `SourceFile.__init__`."""
    source = tmp_path / "src"
    source.mkdir()
    plain = source / "a.py"
    plain.write_text("value = 1\n")
    link = source / "link.py"
    link.symlink_to(plain)

    result = discover_files(tmp_path, explicit=(plain, link))

    assert set(result.resolved_paths) == set(result.files)
    for reported, resolved in result.resolved_paths.items():
        assert resolved == reported.resolve()

    # `selected` is keyed by resolved path, so passing both keeps `plain` and drops `link` as a
    # duplicate — leaving every surviving entry with `resolved == reported` and the mapping's
    # whole reason to exist unexercised. Reaching the symlink alone is the only case where the
    # reported path and the validated target actually differ.
    link_only = discover_files(tmp_path, explicit=(link,))

    assert link_only.files == (link,)
    assert link_only.resolved_paths == {link: plain}


def test_walked_file_symlinks_are_not_selected(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    target = tmp_path / "target.py"
    target.write_text(PY_CONTENT)
    (source / "link.py").symlink_to(target)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == ()
    assert result.files_skipped == 1


def test_empty_full_scan_is_explicitly_clean(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text(PY_CONTENT)

    result = discover_files(tmp_path, include=())

    assert result.files == ()
    assert result.files_skipped == 0
    assert result.errors == ()


def test_empty_root_with_default_include_is_an_empty_scan(tmp_path: Path) -> None:
    # With `include=(".",)` a missing root is impossible -- the root always exists -- so the
    # empty-scan case that matters now is a root with no Python files at all.
    result = discover_files(tmp_path)

    assert result.files == ()
    assert result.errors == ()


def test_nested_directory_symlink_reports_error_and_keeps_reachable_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "kept.py").write_text(PY_CONTENT)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "hidden.py").write_text(PY_CONTENT)
    (source / "linked").symlink_to(linked, target_is_directory=True)

    result = discover_files(tmp_path, include=("src",))

    assert result.files == (source / "kept.py",)
    assert len(result.errors) == 1
    assert isinstance(result.errors[0], LintError)
    assert result.errors[0].kind == "traversal"
    assert result.errors[0].path == "src/linked"


def test_a_symlinked_excluded_directory_is_pruned_without_a_traversal_error(
    tmp_path: Path,
) -> None:
    # A directory this walk was never going to descend into regardless (here, house-lint's own
    # default `.house-lint-cache/`, a BUILTIN_EXCLUDES entry) must be pruned silently, even when
    # it happens to be a symlink -- not surface the "directory symlink is not traversed" error
    # `test_nested_directory_symlink_reports_error_and_keeps_reachable_files` pins for a
    # non-excluded symlinked directory. A cloned repository controls the path a project-relative
    # `.house-lint-cache` resolves to, so a scan must succeed cleanly even when that path is a
    # symlink pointing outside the checkout.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "kept.py").write_text(PY_CONTENT)
    outside = tmp_path / "outside-cache"
    outside.mkdir()
    (tmp_path / ".house-lint-cache").symlink_to(outside, target_is_directory=True)

    result = discover_files(tmp_path)

    assert result.files == (tmp_path / "src" / "kept.py",)
    assert result.errors == ()


def test_a_symlinked_builtin_excluded_directory_is_pruned_without_a_traversal_error(
    tmp_path: Path,
) -> None:
    # Unlike the CACHE_DIRNAME special-case above (dropped before either check, so it never
    # exercised this ordering), an ordinary BUILTIN_EXCLUDES entry (here `.venv/`) used to reach
    # `_traversable_dirs`'s symlink stat before its exclusion check, so a symlinked `.venv`,
    # `venv`, or `node_modules` under the root-wide default scan produced a "directory symlink
    # is not traversed" traversal error and made an otherwise-clean `check` exit 3 instead of
    # pruning the directory like its non-symlinked counterpart.
    (tmp_path / "a.py").write_text(PY_CONTENT)
    outside = tmp_path / "outside-venv"
    outside.mkdir()
    (tmp_path / ".venv").symlink_to(outside, target_is_directory=True)

    result = discover_files(tmp_path)

    assert result.files == (tmp_path / "a.py",)
    assert result.errors == ()


def test_default_cache_directory_at_root_contributes_no_skip_count(tmp_path: Path) -> None:
    # Unlike every other pruned directory (see
    # `test_pruned_directory_counts_as_one_skip_not_one_per_contained_file`, which pins the
    # general "1 skip per pruned directory" rule), house-lint's own default cache base
    # (`root / CACHE_DIRNAME`) is created by the very run that would be counting it -- `cli.py`
    # calls `prepare_cache_dir` after discovery has already produced its result. Two scans of the
    # same otherwise-untouched root must report identical `files_skipped` regardless of whether
    # an earlier run already created `.house-lint-cache/`; a mismatch here is exactly what broke
    # `test_clean_check_is_equivalent_and_json_is_parseable` and
    # `test_cache_hit_never_scans_the_source` in `tests/integration/test_cli.py` once the default
    # scan became root-wide.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "kept.py").write_text(PY_CONTENT)

    before = discover_files(tmp_path)

    cache_base = tmp_path / ".house-lint-cache"
    (cache_base / "0.0.0-abc123").mkdir(parents=True)
    (cache_base / ".house-lint-version").write_text("")
    (cache_base / "0.0.0-abc123" / "entry.json").write_text("{}")

    after = discover_files(tmp_path)

    assert before.files == after.files == (tmp_path / "src" / "kept.py",)
    assert before.files_skipped == after.files_skipped == 0


def test_walker_error_reports_failed_directory_and_keeps_reachable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    kept = source / "kept.py"
    kept.write_text(PY_CONTENT)
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
        (source / name).write_text(PY_CONTENT)
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


def test_upward_search_stops_at_git_boundary_ignoring_outer_ancestor_config(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL001"]\n')
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "src"
    nested.mkdir()

    resolution = resolve_project(cwd=nested)

    assert resolution == type(resolution)(repo, None)


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


def test_upward_walk_finds_standalone_house_lint_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "house-lint.toml"
    config_path.write_text('[house-lint]\nselect = ["HSL001"]\n')
    child = tmp_path / "nested"
    child.mkdir()

    resolution = resolve_project(cwd=child)

    assert resolution == type(resolution)(tmp_path, config_path)


def test_upward_walk_finds_standalone_dot_house_lint_toml(tmp_path: Path) -> None:
    config_path = tmp_path / ".house-lint.toml"
    config_path.write_text('[house-lint]\nselect = ["HSL001"]\n')
    child = tmp_path / "nested"
    child.mkdir()

    resolution = resolve_project(cwd=child)

    assert resolution == type(resolution)(tmp_path, config_path)


def test_house_lint_toml_takes_precedence_over_pyproject_in_same_directory(
    tmp_path: Path,
) -> None:
    standalone = tmp_path / "house-lint.toml"
    standalone.write_text('[house-lint]\nselect = ["HSL001"]\n')
    (tmp_path / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL002"]\n')

    resolution = resolve_project(cwd=tmp_path)

    assert resolution.config == standalone
    assert resolution.shadowed == (tmp_path / "pyproject.toml",)


def test_house_lint_toml_without_table_falls_through_to_pyproject(tmp_path: Path) -> None:
    (tmp_path / "house-lint.toml").write_text('[not-house-lint]\nfoo = "bar"\n')
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[tool.house-lint]\nselect = ["HSL001"]\n')

    resolution = resolve_project(cwd=tmp_path)

    assert resolution == type(resolution)(tmp_path, pyproject)


def test_root_without_config_finds_standalone_config_in_root_directory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path = root / "house-lint.toml"
    config_path.write_text('[house-lint]\nselect = ["HSL001"]\n')

    resolution = resolve_project(root=root)

    assert resolution == type(resolution)(root, config_path)


def test_root_without_config_finds_dot_standalone_config_in_root_directory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path = root / ".house-lint.toml"
    config_path.write_text('[house-lint]\nselect = ["HSL001"]\n')

    resolution = resolve_project(root=root)

    assert resolution == type(resolution)(root, config_path)


def test_explicit_config_path_to_standalone_house_lint_toml_resolves_correctly(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = project / "house-lint.toml"
    config_path.write_text('[house-lint]\nselect = ["HSL001"]\n')

    resolution = resolve_project(config=config_path)

    assert resolution == type(resolution)(project, config_path)


def test_malformed_lower_precedence_config_does_not_block_a_valid_winner(
    tmp_path: Path,
) -> None:
    winner = tmp_path / "house-lint.toml"
    winner.write_text('[house-lint]\nselect = ["HSL001"]\n')
    (tmp_path / ".house-lint.toml").write_text("not valid toml {{{\n")

    resolution = resolve_project(cwd=tmp_path)

    assert resolution.config == winner
    assert resolution.shadowed == ()


def test_malformed_sole_config_at_a_directory_is_still_a_configuration_failure(
    tmp_path: Path,
) -> None:
    (tmp_path / "house-lint.toml").write_text("not valid toml {{{\n")

    with pytest.raises(ConfigError, match="invalid project configuration"):
        resolve_project(cwd=tmp_path)


@pytest.mark.parametrize(
    "resolve",
    [
        lambda root: resolve_project(cwd=root),
        lambda root: resolve_project(root=root),
    ],
    ids=["upward-walk", "root-without-config"],
)
def test_discovery_order_matches_documented_precedence(
    tmp_path: Path, resolve: Callable[[Path], ProjectResolution]
) -> None:
    """`docs/configuration.md`'s "Discovery and precedence" section claims the order
    `house-lint.toml` -> `.house-lint.toml` -> `pyproject.toml` (with `[tool.house-lint]`), for
    both the upward walk (item 4) and `--root` without `--config` (item 3). Pins
    `STANDALONE_CONFIG_NAMES`' own ordering plus `_recognized_configs`' pyproject-last placement
    together, so a future reordering of either forces this test -- and the doc it backs -- to be
    updated in the same change. Follows the naming convention
    `test_no_path_scan_discovers_files_anywhere_under_root` sets for pinning a doc-claimed default
    to a test named after it.
    """
    assert STANDALONE_CONFIG_NAMES == ("house-lint.toml", ".house-lint.toml")
    (tmp_path / "house-lint.toml").write_text('[house-lint]\nselect = ["HSL001"]\n')
    (tmp_path / ".house-lint.toml").write_text('[house-lint]\nselect = ["HSL002"]\n')
    (tmp_path / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL003"]\n')

    resolution = resolve(tmp_path)

    assert resolution.config == tmp_path / "house-lint.toml"
    assert resolution.shadowed == (
        tmp_path / ".house-lint.toml",
        tmp_path / "pyproject.toml",
    )
