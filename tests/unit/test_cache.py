import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from house_lint import __version__
from house_lint.cache import (
    CachedFileResult,
    CacheReporter,
    code_identity,
    default_cache_base,
    default_cache_base_is_safe,
    hash_effective_config,
    hash_source_content,
    prepare_cache_dir,
    prune_stale_cache_dirs,
    read_cached_result,
    versioned_cache_dir,
    write_cached_result,
)
from house_lint.config import HSL101Options, HSL102Options, HSL103Options, TokenFamily
from house_lint.results import Finding, LintError

# Well-formed cache payloads, as `_finding_to_payload`/`_error_to_payload` write them (the
# `path` field is stripped on write and re-attached by the reader).
_FINDING = {
    "rule_id": "HSL002",
    "line": 1,
    "column": 5,
    "end_line": 1,
    "end_column": 20,
    "message": "import inside function",
}
_ERROR = {
    "code": "read-error",
    "kind": "read",
    "line": None,
    "column": None,
    "end_line": None,
    "end_column": None,
    "phase": "read",
    "operation": "bounded-read",
    "rule_id": None,
    "message": "could not read",
}


def test_hash_source_content_is_stable_and_changes_with_content() -> None:
    first = hash_source_content(b"x = 1\n")
    assert first is not None
    assert hash_source_content(b"x = 1\n") == first
    assert hash_source_content(b"x = 2\n") != first


def test_hash_source_content_is_none_without_bytes() -> None:
    # None is what `SourceFile.content_bytes` reports for a file it could not read at all —
    # missing, non-regular, or permission-denied. Such a file is simply never cached.
    assert hash_source_content(None) is None


def test_hash_source_content_is_none_when_oversized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("house_lint.cache.MAX_SOURCE_BYTES", 2)

    assert hash_source_content(b"x = 1\n") is None


def test_hash_effective_config_is_order_independent_and_content_sensitive() -> None:
    hsl101, hsl102, hsl103 = HSL101Options(), HSL102Options(), HSL103Options()

    # Explicit parameters rather than `**kwargs`: a typo'd keyword would be silently ignored by
    # `kwargs.get`, leaving the final assertion comparing two identical hashes and passing while
    # testing nothing.
    def h(
        enabled_rules: tuple[str, ...],
        *,
        options101: HSL101Options = hsl101,
        options102: HSL102Options = hsl102,
        options103: HSL103Options = hsl103,
    ) -> str:
        return hash_effective_config(
            enabled_rules, options101, options102, options103, filename="a.py"
        )

    assert h(("HSL001", "HSL002")) == h(("HSL002", "HSL001"))
    assert h(("HSL001",)) != h(("HSL001", "HSL002"))
    assert h(("HSL102",), options102=HSL102Options(max_lines=100)) != h(("HSL102",))


def test_hash_effective_config_changes_with_python_version() -> None:
    hsl101, hsl102, hsl103 = HSL101Options(), HSL102Options(), HSL103Options()

    def h(python_version: tuple[int, int]) -> str:
        return hash_effective_config(
            ("HSL001",), hsl101, hsl102, hsl103, filename="a.py", python_version=python_version
        )

    assert h((3, 11)) != h((3, 12))
    assert h((3, 12)) == h((3, 12))


def test_hash_effective_config_defaults_python_version_to_the_running_interpreter() -> None:
    hsl101, hsl102, hsl103 = HSL101Options(), HSL102Options(), HSL103Options()

    default = hash_effective_config(("HSL001",), hsl101, hsl102, hsl103, filename="a.py")
    explicit = hash_effective_config(
        ("HSL001",),
        hsl101,
        hsl102,
        hsl103,
        filename="a.py",
        python_version=tuple(sys.version_info[:2]),
    )

    assert default == explicit


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
    assert versioned_cache_dir(base) == base / f"{__version__}-{code_identity()}"


def test_write_then_read_round_trips_and_reattaches_the_caller_supplied_path(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    prepare_cache_dir(cache_dir, self_ignore=False, reporter=CacheReporter())
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

    write_cached_result(
        cache_dir,
        "content-hash",
        "config-hash",
        result,
        self_ignore=False,
        reporter=CacheReporter(),
    )
    cached = read_cached_result(
        cache_dir,
        "content-hash",
        "config-hash",
        relative_path="actual/path.py",
        reporter=CacheReporter(),
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
    assert (
        read_cached_result(cache_dir, "x", "y", relative_path="a.py", reporter=CacheReporter())
        is None
    )


def test_read_cached_result_is_a_miss_on_corrupted_entries(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "content-hash-config-hash.json").write_text("not valid json {{{")

    assert (
        read_cached_result(
            cache_dir, "content-hash", "config-hash", relative_path="a.py", reporter=CacheReporter()
        )
        is None
    )


def test_read_cached_result_is_a_miss_when_a_required_field_is_missing(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "content-hash-config-hash.json").write_text('{"findings": []}')

    assert (
        read_cached_result(
            cache_dir, "content-hash", "config-hash", relative_path="a.py", reporter=CacheReporter()
        )
        is None
    )


def test_read_cached_result_is_a_miss_when_a_scalar_field_has_the_wrong_type(
    tmp_path: Path,
) -> None:
    """A corrupted-but-valid-JSON entry (e.g. `suppressed_count` as a string) must be treated
    as a cache miss here, not accepted and left to raise `TypeError` later when the caller
    accumulates it (`suppressed_count += cached.suppressed_count` in `cli.py`)."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    payload = '{"findings": [], "errors": [], "suppressed_count": "1", "files_scanned": 1}'
    (cache_dir / "content-hash-config-hash.json").write_text(payload)

    assert (
        read_cached_result(
            cache_dir, "content-hash", "config-hash", relative_path="a.py", reporter=CacheReporter()
        )
        is None
    )


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("negative suppressed_count", '{"suppressed_count": -7, "files_scanned": 1}'),
        ("negative files_scanned", '{"suppressed_count": 0, "files_scanned": -1}'),
    ],
)
def test_read_cached_result_is_a_miss_when_a_count_is_negative(
    label: str, payload: str, tmp_path: Path
) -> None:
    """Negative counts reach the same accumulation the type checks above exist to protect. No
    real scan produces one — suppression counts are nonnegative and `files_scanned` is 0 or 1 —
    so a negative value is corruption, and accepting it would silently lower the run's totals."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    entry = json.loads(payload) | {"findings": [], "errors": []}
    (cache_dir / "content-hash-config-hash.json").write_text(json.dumps(entry))

    assert (
        read_cached_result(
            cache_dir, "content-hash", "config-hash", relative_path="a.py", reporter=CacheReporter()
        )
        is None
    )


def test_read_cached_result_is_a_miss_when_the_entry_is_not_utf8(tmp_path: Path) -> None:
    """Invalid UTF-8 raises `UnicodeDecodeError` at `read_text`, before the parse block. It is a
    `ValueError`, so the `OSError` handler does not catch it either — left unhandled it escapes
    `_scan` and aborts the run with an internal error instead of degrading to a miss."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "content-hash-config-hash.json").write_bytes(b"\xff\xfe not utf-8")

    assert (
        read_cached_result(
            cache_dir, "content-hash", "config-hash", relative_path="a.py", reporter=CacheReporter()
        )
        is None
    )


@pytest.mark.parametrize(
    ("label", "findings", "errors"),
    [
        ("finding message is not a string", [{**_FINDING, "message": 12345}], []),
        ("finding rule_id is not a string", [{**_FINDING, "rule_id": 2}], []),
        ("finding is not an object", ["not an object"], []),
        ("error message is not a string", [], [{**_ERROR, "message": []}]),
        ("error kind is not a string", [], [{**_ERROR, "kind": 7}]),
        ("error rule_id is neither string nor null", [], [{**_ERROR, "rule_id": 3}]),
        ("error is not an object", [], [42]),
        # `_finding_from_payload`/`_error_from_payload` splat the whole payload into the
        # dataclass constructor, so an extra key raises `TypeError: unexpected keyword argument`.
        # Pinned here so a later change to those constructor calls cannot quietly turn a
        # corrupted entry into an uncaught crash.
        ("finding carries an unexpected key", [{**_FINDING, "stop": True}], []),
        ("error carries an unexpected key", [], [{**_ERROR, "path": "leaked.py"}]),
    ],
)
def test_read_cached_result_is_a_miss_when_a_text_field_has_the_wrong_type(
    label: str, findings: list[object], errors: list[object], tmp_path: Path
) -> None:
    """`Finding`/`LintError` validate their location fields but accept any type elsewhere, so a
    corrupted-but-valid-JSON entry would construct fine and only blow up later — `int < str`
    while `ScanResult.to_dict()` sorts findings, during rendering, outside `check()`'s exception
    boundary. That surfaces as a traceback rather than the documented cache miss."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    payload = {
        "findings": findings,
        "errors": errors,
        "suppressed_count": 0,
        "files_scanned": 1,
    }
    (cache_dir / "content-hash-config-hash.json").write_text(json.dumps(payload))

    assert (
        read_cached_result(
            cache_dir, "content-hash", "config-hash", relative_path="a.py", reporter=CacheReporter()
        )
        is None
    ), label


def test_read_cached_result_accepts_a_well_formed_entry_with_a_null_error_rule_id(
    tmp_path: Path,
) -> None:
    """The nullable text fields must stay nullable — the validation above rejects wrong types,
    not legitimately absent values."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    payload = {
        "findings": [_FINDING],
        "errors": [_ERROR],
        "suppressed_count": 0,
        "files_scanned": 1,
    }
    (cache_dir / "content-hash-config-hash.json").write_text(json.dumps(payload))

    cached = read_cached_result(
        cache_dir, "content-hash", "config-hash", relative_path="a.py", reporter=CacheReporter()
    )

    assert cached is not None
    assert cached.findings[0].message == "import inside function"
    assert cached.errors[0].rule_id is None


def test_corrupted_entry_is_reported_without_debug(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "content-hash-config-hash.json").write_text("not valid json {{{")

    read_cached_result(
        cache_dir, "content-hash", "config-hash", relative_path="a.py", reporter=CacheReporter()
    )

    captured = capsys.readouterr().err
    assert captured.startswith("warning: ")
    assert "a.py" in captured


def test_write_failure_is_reported_without_debug(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")

    write_cached_result(
        blocked / "cache",
        "x",
        "y",
        CachedFileResult(),
        self_ignore=False,
        reporter=CacheReporter(),
    )

    assert capsys.readouterr().err.startswith("warning: ")


def test_reporter_warns_once_then_falls_back_to_debug_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A broken cache directory fails once per scanned file. Reporting every one by default would
    bury the single fact worth reporting, so only the first failure of a run is unconditional."""
    quiet = CacheReporter()
    quiet.failure("first failure")
    quiet.failure("second failure")

    captured = capsys.readouterr().err
    assert captured == "warning: first failure\n"

    verbose = CacheReporter(debug=True)
    verbose.failure("first failure")
    verbose.failure("second failure")

    assert capsys.readouterr().err == "warning: first failure\ndebug: second failure\n"


def test_write_cached_result_is_best_effort_and_does_not_raise_on_a_bad_directory(
    tmp_path: Path,
) -> None:
    # cache_dir collides with an existing file, so mkdir(parents=True) must fail silently.
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    result = CachedFileResult(findings=(), errors=(), suppressed_count=0, files_scanned=1)

    write_cached_result(
        blocked / "cache", "x", "y", result, self_ignore=False, reporter=CacheReporter()
    )  # must not raise


def test_write_cached_result_writes_atomically_and_leaves_no_temp_file(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    prepare_cache_dir(cache_dir, self_ignore=False, reporter=CacheReporter())
    result = CachedFileResult()

    write_cached_result(
        cache_dir,
        "content-hash",
        "config-hash",
        result,
        self_ignore=False,
        reporter=CacheReporter(),
    )

    entries = {entry.name for entry in cache_dir.iterdir()}
    assert entries == {"content-hash-config-hash.json", ".house-lint-version"}
    assert not any(name.endswith(".tmp") for name in entries)


def test_write_cached_result_removes_its_temp_file_when_the_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stranded `.<pid>.tmp` file is unrecognisable to every later run, so nothing would ever
    clean it up — repeated interrupted or failing writes would accumulate forever."""
    cache_dir = tmp_path / "cache"
    prepare_cache_dir(cache_dir, self_ignore=False, reporter=CacheReporter())

    def failing_replace(source: object, target: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "replace", failing_replace)
    write_cached_result(
        cache_dir,
        "content-hash",
        "config-hash",
        CachedFileResult(),
        self_ignore=False,
        reporter=CacheReporter(),
    )

    assert [entry.name for entry in cache_dir.iterdir()] == [".house-lint-version"]


def test_write_cached_result_recreates_a_namespace_pruned_out_from_under_it(
    tmp_path: Path,
) -> None:
    """`prepare_cache_dir` runs once per scan and is never retried, so a concurrent house-lint
    process pruning this namespace mid-run would otherwise cost every remaining write. The write
    restores the directory once and retries, bounding the loss to the entry in flight."""
    cache_dir = tmp_path / ".house-lint-cache" / "1.0.0"
    prepare_cache_dir(cache_dir, self_ignore=True, reporter=CacheReporter())

    shutil.rmtree(cache_dir)  # stands in for a sibling process's prune

    assert write_cached_result(
        cache_dir,
        "content-hash",
        "config-hash",
        CachedFileResult(files_scanned=1),
        self_ignore=True,
        reporter=CacheReporter(),
    )
    assert (cache_dir / "content-hash-config-hash.json").is_file()
    assert (cache_dir / ".house-lint-version").is_file(), "the namespace marker must be restored"


def test_write_cached_result_does_not_retry_a_failure_that_cannot_resolve_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a vanished directory is worth retrying. A permissions or out-of-space failure will
    not fix itself between two adjacent calls, so retrying would just pay twice."""
    cache_dir = tmp_path / "cache"
    prepare_cache_dir(cache_dir, self_ignore=False, reporter=CacheReporter())
    attempts = 0

    def failing_replace(source: object, target: object) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "replace", failing_replace)

    assert not write_cached_result(
        cache_dir,
        "content-hash",
        "config-hash",
        CachedFileResult(),
        self_ignore=False,
        reporter=CacheReporter(),
    )
    assert attempts == 1


def test_prepare_cache_dir_creates_self_ignoring_gitignore_for_the_default_base(
    tmp_path: Path,
) -> None:
    base = tmp_path / ".house-lint-cache"

    prepare_cache_dir(base / "1.2.3", self_ignore=True, reporter=CacheReporter())

    assert (base / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_prepare_cache_dir_never_writes_a_gitignore_into_a_user_supplied_cache_dir(
    tmp_path: Path,
) -> None:
    """`--cache-dir` names a directory the user already owns. Writing `*` into it would change
    Git's behaviour for every unrelated sibling — and pointing it at a project root would hide
    the whole project from `git status`."""
    base = tmp_path / "shared-cache"
    (base / "someone-elses-data").mkdir(parents=True)

    prepare_cache_dir(base / "1.2.3", self_ignore=False, reporter=CacheReporter())

    assert not (base / ".gitignore").exists()
    assert (base / "someone-elses-data").exists()


def test_prepare_cache_dir_does_not_overwrite_an_existing_gitignore_marker(
    tmp_path: Path,
) -> None:
    base = tmp_path / ".house-lint-cache"
    base.mkdir(parents=True)
    (base / ".gitignore").write_text("custom content\n", encoding="utf-8")

    prepare_cache_dir(base / "1.2.3", self_ignore=True, reporter=CacheReporter())

    assert (base / ".gitignore").read_text(encoding="utf-8") == "custom content\n"


def test_prune_stale_cache_dirs_removes_superseded_sibling_namespaces(tmp_path: Path) -> None:
    base = tmp_path / ".house-lint-cache"
    old_version_dir = base / "0.9.0-aaaaaaaaaaaaaaaa"
    old_version_dir.mkdir(parents=True)
    (old_version_dir / "stale-entry.json").write_text("{}")
    (old_version_dir / ".house-lint-version").write_text("")
    current_version_dir = base / "1.0.0-bbbbbbbbbbbbbbbb"

    prepare_cache_dir(current_version_dir, self_ignore=True, reporter=CacheReporter())
    prune_stale_cache_dirs(current_version_dir, reporter=CacheReporter())

    assert not old_version_dir.exists()
    assert current_version_dir.exists()


def test_prepare_cache_dir_does_not_prune_on_its_own(tmp_path: Path) -> None:
    """Pruning is not race-free, so it stays tied to an actual write rather than running for
    every scan. `prepare_cache_dir` must leave a sibling namespace alone."""
    base = tmp_path / ".house-lint-cache"
    old_version_dir = base / "0.9.0-aaaaaaaaaaaaaaaa"
    old_version_dir.mkdir(parents=True)
    (old_version_dir / ".house-lint-version").write_text("")

    prepare_cache_dir(base / "1.0.0-bbbbbbbbbbbbbbbb", self_ignore=True, reporter=CacheReporter())

    assert old_version_dir.exists()


def test_prune_stale_cache_dirs_does_not_prune_directories_without_the_version_marker(
    tmp_path: Path,
) -> None:
    """A `--cache-dir` pointed at a pre-existing shared directory (e.g. `~/.cache`) must never
    have its unrelated sibling directories swept up as "stale house-lint versions" — only
    directories house-lint itself created (marked via `.house-lint-version`) are eligible."""
    base = tmp_path / ".cache"
    unrelated_dir = base / "some-other-tool"
    unrelated_dir.mkdir(parents=True)
    (unrelated_dir / "important-data.txt").write_text("do not delete")

    prepare_cache_dir(base / "1.0.0", self_ignore=False, reporter=CacheReporter())
    prune_stale_cache_dirs(base / "1.0.0", reporter=CacheReporter())

    assert unrelated_dir.exists()
    assert (unrelated_dir / "important-data.txt").exists()


def test_prepare_cache_dir_leaves_the_current_namespace_untouched(tmp_path: Path) -> None:
    base = tmp_path / ".house-lint-cache"
    current_version_dir = base / "1.0.0"
    current_version_dir.mkdir(parents=True)
    (current_version_dir / "existing-entry.json").write_text("{}")

    prepare_cache_dir(current_version_dir, self_ignore=True, reporter=CacheReporter())
    write_cached_result(
        current_version_dir,
        "content-hash",
        "config-hash",
        CachedFileResult(),
        # Matches the `prepare_cache_dir` call above, as `write_cached_result` documents: it
        # re-invokes `prepare_cache_dir` with this value on the vanished-directory retry path.
        self_ignore=True,
        reporter=CacheReporter(),
    )

    assert (current_version_dir / "existing-entry.json").exists()
    assert (current_version_dir / "content-hash-config-hash.json").exists()


def test_prepare_cache_dir_is_best_effort_on_an_unusable_directory(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")

    prepare_cache_dir(
        blocked / "cache", self_ignore=True, reporter=CacheReporter()
    )  # must not raise


def test_default_cache_base_is_unsafe_when_it_is_a_symlink(tmp_path: Path) -> None:
    """A repository can ship `.house-lint-cache` as a symlink. `mkdir(exist_ok=True)` follows it,
    so without this check a plain `house-lint check` on a fresh clone would write its entries and
    a wildcard `.gitignore` into whatever directory outside the checkout the link names."""
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "repository"
    root.mkdir()
    base = default_cache_base(root)
    base.symlink_to(outside, target_is_directory=True)

    assert not default_cache_base_is_safe(base)
    assert default_cache_base_is_safe(default_cache_base(outside))


def test_code_identity_is_stable_within_a_process_and_shapes_the_cache_namespace(
    tmp_path: Path,
) -> None:
    identity = code_identity()

    assert identity == code_identity()
    assert identity and identity != "unknown"
    assert versioned_cache_dir(tmp_path).name == f"{__version__}-{identity}"
