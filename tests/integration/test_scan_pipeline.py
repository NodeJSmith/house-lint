"""Whole-run pins for what a scan reports and what it does to the cache.

Everything here goes through `cli.check` and patches only code below the pipeline (`scanner` and
`cache` internals), so a pin keeps meaning the same thing when the pipeline around it is reshaped.
Files are scanned in sorted order, which is what puts `a.py` ahead of `b.py` in the cache pins.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from house_lint import cache, cli, scanner

LAZY_IMPORT = "def example():\n    import module\n"
OTHER_LAZY_IMPORT = "def other():\n    import other\n"
LAZY_IMPORT_MESSAGE = "import inside function body"
POISONED_MESSAGE = "poisoned cache entry"
PER_FILE_IGNORES_CONFIG = '[tool.house-lint.per-file-ignores]\n"sample.py" = ["HSL002"]\n'
FOREIGN_NAMESPACE = "9.9.9-ffffffffffffffff"


def _check(
    capsys: pytest.CaptureFixture[str], root: Path, **kwargs: Any
) -> tuple[int, dict[str, Any], str]:
    code = cli.check(root=root, select=["HSL002"], format="json", **kwargs)
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


def _entries(root: Path) -> list[Path]:
    return list((root / ".house-lint-cache").rglob("*.json"))


def _messages_by_path(result: dict[str, Any]) -> dict[str, str]:
    return {finding["path"]: finding["message"] for finding in result["findings"]}


def _error_identities(result: dict[str, Any]) -> list[tuple[str, str | None, str, str]]:
    return [
        (error["kind"], error["path"], error["phase"], error["operation"])
        for error in result["errors"]
    ]


def _poison_only_entry(root: Path) -> None:
    """Rewrite the one cache entry, so a run can show whether it read that entry back."""
    [entry] = _entries(root)
    payload = json.loads(entry.read_text())
    payload["findings"][0]["message"] = POISONED_MESSAGE
    entry.write_text(json.dumps(payload))


def _add_foreign_namespace(root: Path) -> Path:
    """Add a sibling namespace that house-lint would prune, as a different build leaves behind."""
    foreign = root / ".house-lint-cache" / FOREIGN_NAMESPACE
    foreign.mkdir(parents=True)
    (foreign / ".house-lint-version").write_text("")
    return foreign


def _fail_detectors_for(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    original = scanner.detect_candidates

    def fail(source: scanner.SourceFile, detector_inputs: object, **kwargs: object) -> object:
        if source.relative_path == path:
            raise RuntimeError("simulated failure")
        return original(source, detector_inputs, **kwargs)

    monkeypatch.setattr(scanner, "detect_candidates", fail)


def _fail_cache_writes(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Make every entry write fail as a full disk would, and return the attempts as they happen."""
    attempts: list[Path] = []

    def fail(temporary: Path) -> int:
        attempts.append(temporary)
        raise OSError("disk full")

    monkeypatch.setattr(cache, "_create_temp_exclusively", fail)
    return attempts


@pytest.mark.parametrize(
    ("source", "config", "expected_findings", "suppressed_count", "exit_code"),
    [
        pytest.param(
            """\
            def example():
                import module  # house-lint: ignore[HSL002] - imported lazily on purpose
            """,
            "",
            [],
            1,
            0,
            id="ignore",
        ),
        pytest.param(
            """\
            def example():
                # house-lint: ignore-next[HSL002] - imported lazily on purpose
                import module
            """,
            "",
            [],
            1,
            0,
            id="ignore-next",
        ),
        pytest.param(
            """\
            # house-lint: ignore-file[HSL002] - imported lazily on purpose
            def example():
                import module
            """,
            "",
            [],
            1,
            0,
            id="ignore-file",
        ),
        pytest.param(
            """\
            def example():
                import module  # house-lint: ignore[HSL002]
            """,
            "",
            [
                ("HSL002", 2, LAZY_IMPORT_MESSAGE),
                ("HSL900", 2, "malformed suppression pragma"),
            ],
            0,
            1,
            id="malformed",
        ),
        pytest.param(
            """\
            def example():
                import module  # house-lint: ignore[HSL999] - no such rule exists
            """,
            "",
            [
                ("HSL002", 2, LAZY_IMPORT_MESSAGE),
                ("HSL900", 2, "unknown suppression rule HSL999"),
            ],
            0,
            1,
            id="unknown-rule",
        ),
        pytest.param(
            """\
            def example():
                import module  # house-lint: ignore[HSL001] - rule is not selected
            """,
            "",
            [
                ("HSL002", 2, LAZY_IMPORT_MESSAGE),
                ("HSL900", 2, "unused suppression for disabled rule HSL001"),
            ],
            0,
            1,
            id="disabled-by-selection",
        ),
        pytest.param(
            """\
            def example():
                import module  # house-lint: ignore[HSL002] - rule is off for this file
            """,
            PER_FILE_IGNORES_CONFIG,
            [("HSL900", 2, "unused suppression for disabled rule HSL002")],
            0,
            1,
            id="disabled-by-per-file-ignores",
        ),
        pytest.param(
            """\
            def example():
                # house-lint: ignore[HSL002] - belongs at the end of a statement
                import module
            """,
            "",
            [
                ("HSL900", 2, "misplaced ignore suppression"),
                ("HSL002", 3, LAZY_IMPORT_MESSAGE),
            ],
            0,
            1,
            id="misplaced-ignore",
        ),
        pytest.param(
            """\
            def example():
                import module  # house-lint: ignore-next[HSL002] - belongs on its own line
            """,
            "",
            [
                ("HSL002", 2, LAZY_IMPORT_MESSAGE),
                ("HSL900", 2, "misplaced ignore-next suppression"),
            ],
            0,
            1,
            id="misplaced-ignore-next",
        ),
        pytest.param(
            """\
            def example():
                # house-lint: ignore-file[HSL002] - belongs before the first statement
                import module
            """,
            "",
            [
                ("HSL900", 2, "misplaced ignore-file suppression"),
                ("HSL002", 3, LAZY_IMPORT_MESSAGE),
            ],
            0,
            1,
            id="misplaced-ignore-file",
        ),
        pytest.param(
            """\
            value = 1  # house-lint: ignore[HSL002] - nothing here to suppress
            """,
            "",
            [("HSL900", 1, "unused suppression for HSL002")],
            0,
            1,
            id="unused",
        ),
        pytest.param(
            """\
            def example():
                # house-lint: ignore-next[HSL002] - imported lazily on purpose
                import module  # house-lint: ignore[HSL002] - imported lazily on purpose
            """,
            "",
            [
                ("HSL900", 2, "conflicting suppression for HSL002"),
                ("HSL002", 3, LAZY_IMPORT_MESSAGE),
                ("HSL900", 3, "conflicting suppression for HSL002"),
            ],
            0,
            1,
            id="conflicting",
        ),
    ],
)
def test_suppression_outcome_is_the_same_cold_and_warm(
    tmp_path: Path,
    write_sample: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source: str,
    config: str,
    expected_findings: list[tuple[str, int, str]],
    suppressed_count: int,
    exit_code: int,
) -> None:
    write_sample(source)
    if config:
        (tmp_path / "pyproject.toml").write_text(config)

    cold_code, cold, _ = _check(capsys, tmp_path)

    actual = [(item["rule_id"], item["line"], item["message"]) for item in cold["findings"]]
    assert actual == expected_findings
    assert {item["path"] for item in cold["findings"]} <= {"sample.py"}
    assert cold["summary"]["suppressed_count"] == suppressed_count
    assert cold_code == exit_code

    # A warm run that missed the cache would call this and report an internal error, so a pass
    # below means the outcome was replayed rather than recomputed.
    _fail_detectors_for(monkeypatch, "sample.py")

    warm_code, warm, _ = _check(capsys, tmp_path)

    assert warm == cold
    assert warm_code == cold_code


def test_reads_continue_after_a_failed_write_disables_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "b.py").write_text(LAZY_IMPORT)
    _check(capsys, tmp_path)
    _poison_only_entry(tmp_path)
    (tmp_path / "a.py").write_text(OTHER_LAZY_IMPORT)
    attempts = _fail_cache_writes(monkeypatch)

    _, result, _ = _check(capsys, tmp_path)

    assert len(attempts) == 1
    assert _messages_by_path(result) == {"a.py": LAZY_IMPORT_MESSAGE, "b.py": POISONED_MESSAGE}


def test_a_run_that_stops_early_still_prunes_when_it_wrote_an_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.py").write_text(LAZY_IMPORT)
    (tmp_path / "b.py").write_text(OTHER_LAZY_IMPORT)
    foreign = _add_foreign_namespace(tmp_path)
    _fail_detectors_for(monkeypatch, "b.py")

    code, result, _ = _check(capsys, tmp_path)

    assert code == 4
    assert _error_identities(result) == [("internal", "b.py", "analysis", "rule-dispatch")]
    assert not foreign.exists()


def test_a_run_that_stops_before_writing_does_not_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.py").write_text(LAZY_IMPORT)
    foreign = _add_foreign_namespace(tmp_path)
    _fail_detectors_for(monkeypatch, "a.py")

    code, result, _ = _check(capsys, tmp_path)

    assert code == 4
    assert _error_identities(result) == [("internal", "a.py", "analysis", "rule-dispatch")]
    assert foreign.exists()


def test_a_stop_result_is_never_written_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.py").write_text(LAZY_IMPORT)
    _fail_detectors_for(monkeypatch, "a.py")

    code, result, _ = _check(capsys, tmp_path)

    assert code == 4
    assert _error_identities(result) == [("internal", "a.py", "analysis", "rule-dispatch")]
    assert _entries(tmp_path) == []


def test_an_unhashable_file_does_not_disable_caching_for_later_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.py").write_bytes(b"#" * (cache.MAX_SOURCE_BYTES + 1))
    (tmp_path / "b.py").write_text(LAZY_IMPORT)

    code, result, stderr = _check(capsys, tmp_path)

    assert code == 3
    assert [error["code"] for error in result["errors"]] == ["source-too-large"]
    assert stderr == ""
    [entry] = _entries(tmp_path)
    assert json.loads(entry.read_text())["findings"][0]["message"] == LAZY_IMPORT_MESSAGE


@pytest.mark.parametrize(
    ("debug", "expected_stderr"),
    [
        pytest.param(False, ["warning: cache write failed: disk full"], id="default"),
        pytest.param(
            True,
            [
                "warning: cache write failed: disk full",
                (
                    "debug: cache writes disabled for the rest of this run after the first failure "
                    "(at a.py)"
                ),
            ],
            id="debug",
        ),
    ],
)
def test_one_failed_write_stops_further_writes_for_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    debug: bool,
    expected_stderr: list[str],
) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text(LAZY_IMPORT)
    foreign = _add_foreign_namespace(tmp_path)
    attempts = _fail_cache_writes(monkeypatch)

    _, result, stderr = _check(capsys, tmp_path, debug=debug)

    assert len(attempts) == 1
    assert stderr.splitlines() == expected_stderr
    assert result["files_scanned"] == 3
    assert foreign.exists(), (
        "a run whose only write failed contributed nothing, so it must not prune"
    )


def test_a_failed_prepare_does_not_disable_reads_or_writes_that_would_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "b.py").write_text(LAZY_IMPORT)
    _check(capsys, tmp_path)
    _poison_only_entry(tmp_path)
    (tmp_path / "a.py").write_text(OTHER_LAZY_IMPORT)
    [namespace] = (tmp_path / ".house-lint-cache").glob("*-*")
    real_mkdir = Path.mkdir

    def refuse_namespace(self: Path, *args: Any, **kwargs: Any) -> None:
        if self == namespace:
            raise OSError("mkdir refused")
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", refuse_namespace)

    _, result, stderr = _check(capsys, tmp_path)

    assert stderr.splitlines() == ["warning: cache directory could not be created: mkdir refused"]
    assert _messages_by_path(result)["b.py"] == POISONED_MESSAGE
    assert len(_entries(tmp_path)) == 2


def test_a_symlinked_default_cache_is_not_read_or_written(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "a.py").write_text(LAZY_IMPORT)
    _check(capsys, tmp_path)
    _poison_only_entry(tmp_path)
    linked = tmp_path_factory.mktemp("outside") / "cache"
    (tmp_path / ".house-lint-cache").rename(linked)
    (tmp_path / ".house-lint-cache").symlink_to(linked, target_is_directory=True)

    _, result, stderr = _check(capsys, tmp_path)

    assert _messages_by_path(result) == {"a.py": LAZY_IMPORT_MESSAGE}
    assert "caching disabled" in stderr
    [entry] = _entries(tmp_path)
    assert json.loads(entry.read_text())["findings"][0]["message"] == POISONED_MESSAGE


def test_a_run_that_discovers_nothing_does_not_touch_the_cache(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, stderr = _check(capsys, tmp_path)

    assert code == 0
    assert stderr == ""
    assert not (tmp_path / ".house-lint-cache").exists()


def test_a_relative_cache_dir_resolves_against_the_working_directory_not_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    workdir = tmp_path / "workdir"
    project.mkdir()
    workdir.mkdir()
    (project / "a.py").write_text(LAZY_IMPORT)
    monkeypatch.chdir(workdir)

    _check(capsys, project, cache_dir=Path("relative-cache"))

    assert list((workdir / "relative-cache").rglob("*.json"))
    assert not (project / "relative-cache").exists()
    assert not (project / ".house-lint-cache").exists()
    assert not (workdir / "relative-cache" / ".gitignore").exists()


def test_a_home_relative_cache_dir_expands_to_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    workdir = tmp_path / "workdir"
    project.mkdir()
    home.mkdir()
    workdir.mkdir()
    (project / "a.py").write_text(LAZY_IMPORT)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workdir)

    _check(capsys, project, cache_dir=Path("~/home-cache"))

    assert list((home / "home-cache").rglob("*.json"))
    assert not (workdir / "~").exists()
    assert not (home / "home-cache" / ".gitignore").exists()


def test_an_unresolvable_cache_dir_is_an_internal_error_even_when_discovery_would_also_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The cache location resolves before discovery, so its failure is the one reported.
    code, result, _ = _check(
        capsys,
        tmp_path,
        paths=[tmp_path / "missing.py"],
        cache_dir=Path("~no-such-user-for-house-lint/cache"),
    )

    assert code == 4
    assert [(error["kind"], error["phase"]) for error in result["errors"]] == [("internal", "cli")]
