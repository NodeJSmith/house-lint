from pathlib import Path

import pytest

from house_lint import __version__
from house_lint.cache import (
    CachedFileResult,
    default_cache_base,
    hash_effective_config,
    hash_file_content,
    read_cached_result,
    versioned_cache_dir,
    write_cached_result,
)
from house_lint.config import HSL101Options, HSL102Options, HSL103Options, TokenFamily
from house_lint.results import Finding, LintError


def test_hash_file_content_is_stable_and_changes_with_content(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("x = 1\n")

    first = hash_file_content(path)
    second = hash_file_content(path)
    assert first is not None
    assert first == second

    path.write_text("x = 2\n")
    assert hash_file_content(path) != first


def test_hash_file_content_is_none_for_missing_or_non_regular_files(tmp_path: Path) -> None:
    assert hash_file_content(tmp_path / "missing.py") is None
    assert hash_file_content(tmp_path) is None


def test_hash_file_content_is_none_when_oversized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "big.py"
    path.write_text("x = 1\n")
    monkeypatch.setattr("house_lint.cache.MAX_SOURCE_BYTES", 2)

    assert hash_file_content(path) is None


def test_hash_effective_config_is_order_independent_and_content_sensitive() -> None:
    hsl101, hsl102, hsl103 = HSL101Options(), HSL102Options(), HSL103Options()

    def h(enabled_rules: tuple[str, ...], **kwargs: object) -> str:
        return hash_effective_config(
            enabled_rules,
            kwargs.get("hsl101", hsl101),
            kwargs.get("hsl102", hsl102),
            kwargs.get("hsl103", hsl103),
            filename="a.py",
        )

    assert h(("HSL001", "HSL002")) == h(("HSL002", "HSL001"))
    assert h(("HSL001",)) != h(("HSL001", "HSL002"))
    assert h(("HSL102",), hsl102=HSL102Options(max_lines=100)) != h(("HSL102",))


def test_hash_effective_config_includes_filename_only_when_hsl101_scopes_to_filenames() -> None:
    scoped = HSL101Options(
        tokens=(TokenFamily(prefixes=("TASK",), scopes=("filenames",)),),
    )
    unscoped = HSL101Options(
        tokens=(TokenFamily(prefixes=("TASK",), scopes=("comments",)),),
    )
    other_rule_options = (HSL102Options(), HSL103Options())

    assert hash_effective_config(
        ("HSL101",), scoped, *other_rule_options, filename="a.py"
    ) != hash_effective_config(("HSL101",), scoped, *other_rule_options, filename="b.py")
    assert hash_effective_config(
        ("HSL101",), unscoped, *other_rule_options, filename="a.py"
    ) == hash_effective_config(("HSL101",), unscoped, *other_rule_options, filename="b.py")
    # HSL101 configured with a filenames-scoped family, but not currently enabled: filename
    # cannot affect output for this run, so it must not affect the hash either.
    assert hash_effective_config(
        (), scoped, *other_rule_options, filename="a.py"
    ) == hash_effective_config((), scoped, *other_rule_options, filename="b.py")


def test_default_cache_base_and_versioned_cache_dir(tmp_path: Path) -> None:
    base = default_cache_base(tmp_path)
    assert base == tmp_path / ".house-lint-cache"
    assert versioned_cache_dir(base) == base / __version__


def test_write_then_read_round_trips_and_reattaches_the_caller_supplied_path(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    result = CachedFileResult(
        findings=(Finding("HSL002", "wrong/path.py", 1, 5, 1, 20, "import inside function"),),
        errors=(
            LintError(
                "read-error",
                "read",
                "wrong/path.py",
                None,
                None,
                None,
                None,
                "read",
                "bounded-read",
                None,
                "could not read",
            ),
        ),
        suppressed_count=1,
        files_scanned=1,
    )

    write_cached_result(cache_dir, "content-hash", "config-hash", result)
    cached = read_cached_result(
        cache_dir, "content-hash", "config-hash", relative_path="actual/path.py"
    )

    assert cached is not None
    assert cached.findings == (
        Finding("HSL002", "actual/path.py", 1, 5, 1, 20, "import inside function"),
    )
    assert cached.errors == (
        LintError(
            "read-error",
            "read",
            "actual/path.py",
            None,
            None,
            None,
            None,
            "read",
            "bounded-read",
            None,
            "could not read",
        ),
    )
    assert cached.suppressed_count == 1
    assert cached.files_scanned == 1


def test_read_cached_result_is_a_miss_when_no_entry_exists(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    assert read_cached_result(cache_dir, "x", "y", relative_path="a.py") is None


def test_read_cached_result_is_a_miss_on_corrupted_entries(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "content-hash-config-hash.json").write_text("not valid json {{{")

    assert (
        read_cached_result(cache_dir, "content-hash", "config-hash", relative_path="a.py") is None
    )


def test_read_cached_result_is_a_miss_when_a_required_field_is_missing(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "content-hash-config-hash.json").write_text('{"findings": []}')

    assert (
        read_cached_result(cache_dir, "content-hash", "config-hash", relative_path="a.py") is None
    )


def test_corrupted_entry_is_silent_by_default_but_reported_under_debug(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "content-hash-config-hash.json").write_text("not valid json {{{")

    read_cached_result(cache_dir, "content-hash", "config-hash", relative_path="a.py")
    assert capsys.readouterr().err == ""

    read_cached_result(cache_dir, "content-hash", "config-hash", relative_path="a.py", debug=True)
    assert "a.py" in capsys.readouterr().err


def test_write_failure_is_silent_by_default_but_reported_under_debug(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    result = CachedFileResult()

    write_cached_result(blocked / "cache", "x", "y", result)
    assert capsys.readouterr().err == ""

    write_cached_result(blocked / "cache", "x", "y", result, debug=True)
    assert capsys.readouterr().err != ""


def test_write_cached_result_is_best_effort_and_does_not_raise_on_a_bad_directory(
    tmp_path: Path,
) -> None:
    # cache_dir collides with an existing file, so mkdir(parents=True) must fail silently.
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    result = CachedFileResult(findings=(), errors=(), suppressed_count=0, files_scanned=1)

    write_cached_result(blocked / "cache", "x", "y", result)  # must not raise
