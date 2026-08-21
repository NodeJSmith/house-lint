import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from house_lint import cli, scanner
from house_lint import source as source_module
from house_lint.analysis import MAX_CANDIDATES_PER_FILE


def _run(
    root: Path,
    *args: str,
    module: bool = False,
    prelude: str | None = None,
    pythonpath: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run house-lint in a subprocess against this checkout, or against `pythonpath` instead."""
    command = (
        [sys.executable, "-c", prelude]
        if prelude is not None
        else [sys.executable, "-m", "house_lint"]
        if module
        else [str(shutil.which("house-lint"))]
    )
    source = pythonpath if pythonpath is not None else Path(__file__).parents[2] / "src"
    environment = os.environ | {"PYTHONPATH": str(source)}
    return subprocess.run(
        command + list(args), cwd=root, env=environment, text=True, capture_output=True, check=False
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "clean.py").write_text("value = 1\n")
    return tmp_path


def test_console_and_module_have_equivalent_rule_listing(repository: Path) -> None:
    console = _run(repository, "rules", "--format", "json")
    module = _run(repository, "rules", "--format", "json", module=True)

    assert console.returncode == module.returncode == 0
    assert console.stderr == module.stderr == ""
    assert json.loads(console.stdout) == json.loads(module.stdout)
    assert [item["id"] for item in json.loads(console.stdout)["rules"]] == [
        "HSL001",
        "HSL002",
        "HSL003",
        "HSL004",
        "HSL101",
        "HSL102",
        "HSL103",
        "HSL900",
    ]

    text = _run(repository, "rules")
    assert text.returncode == 0
    assert text.stderr == ""
    assert [line.split()[0] for line in text.stdout.splitlines()] == [
        "HSL001",
        "HSL002",
        "HSL003",
        "HSL004",
        "HSL101",
        "HSL102",
        "HSL103",
        "HSL900",
    ]
    assert "[default]" in text.stdout
    assert "[opt-in]" in text.stdout
    assert "[always]" in text.stdout


def test_clean_check_is_equivalent_and_json_is_parseable(repository: Path) -> None:
    console = _run(repository, "check", "--root", str(repository), "--format", "json")
    module = _run(repository, "check", "--root", str(repository), "--format", "json", module=True)

    assert console.returncode == module.returncode == 0
    assert console.stderr == module.stderr == ""
    assert json.loads(console.stdout) == json.loads(module.stdout)
    assert json.loads(console.stdout)["summary"] == {
        "finding_count": 0,
        "error_count": 0,
        "suppressed_count": 0,
    }

    text = _run(repository, "check", "--root", str(repository))
    assert text.returncode == 0
    assert text.stderr == ""
    assert text.stdout.splitlines() == [
        f"root: {repository.resolve()}",
        "config: <none>",
        "enabled rules: HSL001, HSL002, HSL003, HSL004, HSL900",
        "files: scanned 1, skipped 0",
        "summary: 0 findings, 0 errors, 0 suppressed",
    ]


def test_check_selects_repeatable_comma_separated_rule_ids(repository: Path) -> None:
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001,HSL002",
        "--select",
        "HSL003",
        "--ignore",
        "HSL001,HSL003",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["enabled_rules"] == ["HSL002", "HSL900"]
    assert [finding["rule_id"] for finding in result["findings"]] == ["HSL002"]


def test_cli_extend_select_adds_a_rule_without_dropping_configured_select(
    repository: Path,
) -> None:
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    (repository / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL002"]\n')

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--extend-select",
        "HSL001",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["enabled_rules"] == ["HSL001", "HSL002", "HSL900"]


def test_cli_extend_ignore_subtracts_from_extend_select(repository: Path) -> None:
    (repository / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL001"]\n')

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--extend-select",
        "HSL002",
        "--extend-ignore",
        "HSL002",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert result["enabled_rules"] == ["HSL001", "HSL900"]


def test_per_file_ignores_silences_a_rule_only_for_matching_files(repository: Path) -> None:
    (repository / "tests").mkdir()
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    (repository / "tests" / "test_finding.py").write_text("def example():\n    import module\n")
    (repository / "pyproject.toml").write_text(
        '[tool.house-lint]\nselect = ["HSL002"]\n'
        '[tool.house-lint.per-file-ignores]\n"tests/**" = ["HSL002"]\n'
    )

    completed = _run(repository, "check", "--root", str(repository), "--format", "json")

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["enabled_rules"] == ["HSL002", "HSL900"]
    assert [finding["path"] for finding in result["findings"]] == ["src/finding.py"]


def test_per_file_ignores_match_a_path_spelled_with_parent_traversal(repository: Path) -> None:
    """An explicit path keeps the spelling the user typed all the way to pattern matching, so
    `src/../tests/x.py` was matched literally and `"tests/**"` missed it — running a rule the
    config disabled for everything under `tests/`."""
    (repository / "tests").mkdir()
    (repository / "tests" / "test_finding.py").write_text("def example():\n    import module\n")
    (repository / "pyproject.toml").write_text(
        '[tool.house-lint]\nselect = ["HSL002"]\n'
        '[tool.house-lint.per-file-ignores]\n"tests/**" = ["HSL002"]\n'
    )

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "src/../tests/test_finding.py",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert result["findings"] == []


def test_per_file_ignores_follow_a_symlinked_component_rather_than_the_spelling(
    repository: Path,
) -> None:
    """A lexical `..` collapse is only correct when nothing traversed is a symlink. With
    `link/ -> src/nested/`, the OS reads `link/../finding.py` as `src/finding.py` while the
    lexical form reads `finding.py` — so a pattern written for the file's real location would
    stop matching the file house-lint actually opens."""
    (repository / "src" / "nested").mkdir(parents=True, exist_ok=True)
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    (repository / "link").symlink_to(repository / "src" / "nested")
    (repository / "pyproject.toml").write_text(
        '[tool.house-lint]\nselect = ["HSL002"]\n'
        '[tool.house-lint.per-file-ignores]\n"src/**" = ["HSL002"]\n'
    )

    completed = _run(
        repository, "check", "--root", str(repository), "--format", "json", "link/../finding.py"
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert result["findings"] == []


def test_per_file_ignores_flags_a_pragma_naming_a_rule_disabled_for_that_file(
    repository: Path,
) -> None:
    (repository / "tests").mkdir()
    (repository / "tests" / "test_finding.py").write_text(
        "def example():\n    import module  # house-lint: ignore[HSL002] - stale suppression\n"
    )
    (repository / "pyproject.toml").write_text(
        '[tool.house-lint]\nselect = ["HSL002"]\n'
        '[tool.house-lint.per-file-ignores]\n"tests/**" = ["HSL002"]\n'
    )

    completed = _run(repository, "check", "--root", str(repository), "--format", "json")

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert [(finding["rule_id"], finding["message"]) for finding in result["findings"]] == [
        ("HSL900", "unused suppression for disabled rule HSL002")
    ]


def test_cache_is_populated_and_reused_across_runs(repository: Path) -> None:
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")

    first = _run(
        repository, "check", "--root", str(repository), "--select", "HSL002", "--format", "json"
    )
    assert first.returncode == 1

    # repository also contains a clean src/clean.py with no findings, so it gets its own
    # (empty) cache entry — select finding.py's entry specifically by its non-empty content.
    entries = list((repository / ".house-lint-cache").rglob("*.json"))
    assert entries
    entry = next(e for e in entries if json.loads(e.read_text())["findings"])

    # Poison the cache entry directly (bypassing the real scan) to prove a normal run reads
    # it back rather than re-deriving the same result independently.
    poisoned = {
        "findings": [
            {
                "rule_id": "HSL002",
                "line": 1,
                "column": 1,
                "end_line": 1,
                "end_column": 2,
                "message": "poisoned cache entry",
            }
        ],
        "errors": [],
        "suppressed_count": 0,
        "files_scanned": 1,
    }
    entry.write_text(json.dumps(poisoned))

    second = _run(
        repository, "check", "--root", str(repository), "--select", "HSL002", "--format", "json"
    )
    second_result = json.loads(second.stdout)
    assert [finding["message"] for finding in second_result["findings"]] == ["poisoned cache entry"]

    # --no-cache must ignore the poisoned entry (real scan runs) but still overwrite it
    # afterward, keeping the cache warm for the next normal run.
    third = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--select",
        "HSL002",
        "--no-cache",
        "--format",
        "json",
    )
    third_result = json.loads(third.stdout)
    assert [finding["message"] for finding in third_result["findings"]] == [
        "import inside function body"
    ]
    assert json.loads(entry.read_text())["findings"][0]["message"] == "import inside function body"


def test_cache_dir_flag_overrides_the_default_location(repository: Path) -> None:
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    custom_cache = repository.parent / "custom-cache"

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--select",
        "HSL002",
        "--cache-dir",
        str(custom_cache),
        "--format",
        "json",
    )

    assert completed.returncode == 1
    assert not (repository / ".house-lint-cache").exists()
    assert list(custom_cache.rglob("*.json"))


def test_cache_dir_never_writes_a_gitignore_into_the_directory_it_is_given(
    repository: Path,
) -> None:
    """`--cache-dir` names a directory the user already owns, so house-lint must not drop a
    wildcard `.gitignore` into it. Pointed at the project root, that one file would hide the
    entire project from `git status`."""
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--select",
        "HSL002",
        "--cache-dir",
        str(repository),
        "--format",
        "json",
    )

    assert completed.returncode == 1
    assert not (repository / ".gitignore").exists()
    assert (repository / "src" / "finding.py").exists()


def test_cache_is_invalidated_when_rule_code_changes_without_a_version_bump(
    repository: Path, tmp_path: Path
) -> None:
    """`__version__` only moves at release time, so it cannot be the sole invalidation signal:
    editing a detector in a working checkout and re-running would otherwise replay the previous
    detector's findings for every file whose content and config are unchanged."""
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    package_source = Path(__file__).parents[2] / "src"
    patched_source = tmp_path / "patched-src"
    shutil.copytree(package_source, patched_source)

    def namespaces() -> set[str]:
        return {
            entry.name for entry in (repository / ".house-lint-cache").iterdir() if entry.is_dir()
        }

    arguments = ("check", "--root", str(repository), "--select", "HSL002", "--format", "json")
    first = _run(repository, *arguments)
    assert first.returncode == 1
    assert json.loads(first.stdout)["findings"]
    first_namespaces = namespaces()
    assert len(first_namespaces) == 1

    detector = patched_source / "house_lint" / "rules" / "lazy_imports.py"
    original = detector.read_text()
    assert "def detect(" in original
    detector.write_text(original.replace("def detect(", "def detect(  # patched\n", 1))

    second = _run(repository, *arguments, pythonpath=patched_source)

    # The edit is behaviour-preserving, so the findings must match — what must differ is the
    # cache namespace, proving the second run could not have replayed the first's entry. The
    # superseded namespace is pruned rather than left to accumulate, so exactly one remains.
    assert second.returncode == 1
    assert json.loads(second.stdout)["findings"] == json.loads(first.stdout)["findings"]
    second_namespaces = namespaces()
    assert len(second_namespaces) == 1
    assert second_namespaces != first_namespaces


def test_a_corrupted_cache_field_degrades_to_a_miss_instead_of_crashing(repository: Path) -> None:
    """Rendering happens outside `check()`'s exception boundary, so a cached value that only
    breaks at sort time (`int < str` in `ScanResult.to_dict()`) surfaced as a traceback with no
    output at all, rather than the documented cache miss."""
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    arguments = ("check", "--root", str(repository), "--select", "HSL002", "--format", "json")
    first = _run(repository, *arguments)
    assert first.returncode == 1
    expected = json.loads(first.stdout)["findings"]

    entry = next(
        candidate
        for candidate in (repository / ".house-lint-cache").rglob("*.json")
        if json.loads(candidate.read_text())["findings"]
    )
    payload = json.loads(entry.read_text())
    poisoned = dict(payload["findings"][0])
    poisoned["message"] = 12345
    payload["findings"] = [payload["findings"][0], poisoned]
    entry.write_text(json.dumps(payload))

    second = _run(repository, *arguments)

    payload = json.loads(second.stdout)
    assert second.returncode == 1
    assert "Traceback" not in second.stderr
    assert payload["errors"] == [], "a corrupted cache entry must never become a scan error"
    # Re-scanned from source, so the real finding comes back and the poison is discarded.
    assert payload["findings"] == expected


def test_a_symlinked_default_cache_directory_disables_caching(
    repository: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A cloned repository controls the path `<root>/.house-lint-cache` resolves to. Pointed at a
    directory outside the checkout, `mkdir(parents=True, exist_ok=True)` follows the link and
    house-lint's version marker, entries, and wildcard `.gitignore` all land there. The scan must
    still succeed, and must leave the linked directory exactly as it found it."""
    outside = tmp_path_factory.mktemp("outside")
    (repository / ".house-lint-cache").symlink_to(outside, target_is_directory=True)

    result = _run(repository, "check", "--root", str(repository), "--format", "json")

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert "caching disabled" in result.stderr
    assert json.loads(result.stdout)["errors"] == []
    assert list(outside.iterdir()) == []


def test_a_symlinked_cache_dir_override_is_still_honoured(
    repository: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The symlink refusal above is scoped to the default base. `--cache-dir` names a directory
    the user chose, so house-lint neither self-ignores it nor second-guesses how they linked it."""
    target = tmp_path_factory.mktemp("target")
    link = tmp_path_factory.mktemp("links") / "cache"
    link.symlink_to(target, target_is_directory=True)

    result = _run(
        repository, "check", "--root", str(repository), "--cache-dir", str(link), "--format", "json"
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert list(target.iterdir()) != []
    assert not (target / ".gitignore").exists()


def test_a_run_of_pure_cache_hits_does_not_prune_another_namespace(repository: Path) -> None:
    """Pruning is not race-free: a concurrent house-lint on a different version or build sharing
    a `--cache-dir` can have its in-progress namespace deleted. Tying the sweep to "this run
    actually wrote an entry" keeps that window narrow — a scan that writes nothing must not
    delete anything."""
    arguments = ("check", "--root", str(repository), "--select", "HSL002", "--format", "json")
    assert _run(repository, *arguments).returncode == 0

    foreign = repository / ".house-lint-cache" / "9.9.9-ffffffffffffffff"
    foreign.mkdir(parents=True)
    (foreign / ".house-lint-version").write_text("")
    (foreign / "in-progress.json").write_text("{}")

    second = _run(repository, *arguments)

    assert second.returncode == 0
    assert json.loads(second.stdout)["files_scanned"] == 1
    assert foreign.exists(), "a zero-write run must not prune another namespace"

    # A run that does write an entry still sweeps it, so namespaces cannot accumulate.
    (repository / "src" / "new.py").write_text("value = 2\n")
    assert _run(repository, *arguments).returncode == 0
    assert not foreign.exists()


def test_an_unusable_cache_dir_warns_once_without_debug_and_still_scans(repository: Path) -> None:
    """The runs this tool is built for — CI, pre-commit — never pass `--debug`. Without a
    default-visible line, an unwritable cache directory means every scan silently pays the full
    re-analysis cost with nothing to explain why. The scan itself must be unaffected."""
    blocked = repository / "blocked"
    blocked.write_text("not a directory")
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")

    result = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--select",
        "HSL002",
        "--cache-dir",
        str(blocked / "cache"),
        "--format",
        "json",
    )

    assert result.returncode == 1, result.stderr
    warnings = [line for line in result.stderr.splitlines() if line.startswith("warning: ")]
    assert len(warnings) == 1, result.stderr
    assert "cache" in warnings[0]
    payload = json.loads(result.stdout)
    assert [finding["rule_id"] for finding in payload["findings"]] == ["HSL002"]
    assert payload["errors"] == [], "a cache failure must never become a scan error"


def test_cache_hit_never_scans_the_source(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")

    first_code = cli.check(root=repository, select=["HSL002"], format="json")
    first_output = capsys.readouterr().out
    assert first_code == 1

    calls: list[object] = []

    def record_call(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("scan_source must not be called on a cache hit")

    monkeypatch.setattr(cli, "scan_source", record_call)

    second_code = cli.check(root=repository, select=["HSL002"], format="json")
    second_output = capsys.readouterr().out

    assert calls == [], "scan_source must not be called on a cache hit"
    assert second_code == first_code
    assert json.loads(second_output) == json.loads(first_output)


@pytest.mark.parametrize("cache_state", ["cold", "warm"])
def test_each_scanned_file_is_read_exactly_once(
    repository: Path, monkeypatch: pytest.MonkeyPatch, cache_state: str
) -> None:
    # A cache entry is a promise that this exact content produced these exact findings. That
    # promise only holds because the cache key and the findings come from the same buffer: the
    # file is read once, and everything downstream derives from it. A second read of the same
    # path would reopen a TOCTOU window in which an edit landing between reads lets the entry be
    # written under a key describing content that was never scanned. Counting reads is what
    # guards the structure — the race is unobservable once it is gone, but a regression would
    # show up here as a count above one. The counter intercepts `source.read_regular_file_bytes`,
    # which is the only file-reading entry point on the scan path (`cli` and `cache` no longer
    # import one); a re-read added through some other API would need its own guard.
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    if cache_state == "warm":
        assert cli.check(root=repository, select=["HSL002"], format="json") == 1

    real_read = source_module.read_regular_file_bytes
    reads: dict[str, int] = {}

    def counting_read(path: Path, *, max_bytes: int) -> bytes | None:
        reads[path.name] = reads.get(path.name, 0) + 1
        return real_read(path, max_bytes=max_bytes)

    monkeypatch.setattr(source_module, "read_regular_file_bytes", counting_read)

    assert cli.check(root=repository, select=["HSL002"], format="json") == 1

    assert reads, "the scan must have read at least one file"
    assert reads["finding.py"] == 1
    assert set(reads.values()) == {1}, f"every file must be read exactly once, got {reads}"


def test_cache_does_not_cross_contaminate_hsl101_filename_findings_between_same_content_files(
    repository: Path,
) -> None:
    # Both files have identical content, so a cache key that ignores the filename would let
    # whichever file is scanned first "poison" the entry the other one reads back.
    (repository / "src" / "TASK123.py").write_text("x = 1\n")
    (repository / "src" / "plain.py").write_text("x = 1\n")
    (repository / "pyproject.toml").write_text(
        '[tool.house-lint]\nselect = ["HSL101"]\n'
        "[[tool.house-lint.rules.HSL101.tokens]]\n"
        'prefixes = ["TASK"]\nscopes = ["filenames"]\n'
    )

    completed = _run(repository, "check", "--root", str(repository), "--format", "json")

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert [finding["path"] for finding in result["findings"]] == ["src/TASK123.py"]


def test_hsl101_zero_config_detects_builtin_token_families(repository: Path) -> None:
    """Selecting HSL101 with no `[tool.house-lint.rules.HSL101]` table at all must still detect
    the built-in spec, task, and known-issues families."""
    (repository / "src" / "finding.py").write_text("# AC1 FR#2a T05 KI-001 WP03\nvalue = 1\n")
    (repository / "pyproject.toml").write_text('[tool.house-lint]\nextend-select = ["HSL101"]\n')

    completed = _run(repository, "check", "--root", str(repository), "--format", "json")

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    messages = {finding["message"] for finding in result["findings"]}
    assert {
        "spec token AC1 in comment",
        "spec token FR#2a in comment",
        "spec token T05 in comment",
        "spec token KI-001 in comment",
        "spec token WP03 in comment",
    } <= messages


def test_hsl101_user_tokens_stack_on_top_of_builtin_families(repository: Path) -> None:
    """A user-defined token family adds to, rather than replaces, the built-in families."""
    (repository / "src" / "finding.py").write_text("# JIRA-1 AC1 KI-001\nvalue = 1\n")
    (repository / "pyproject.toml").write_text(
        '[tool.house-lint]\nextend-select = ["HSL101"]\n'
        "[[tool.house-lint.rules.HSL101.tokens]]\n"
        'prefixes = ["JIRA"]\nscopes = ["comments"]\nseparator = "dash"\nmin_digits = 1\n'
    )

    completed = _run(repository, "check", "--root", str(repository), "--format", "json")

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    messages = {finding["message"] for finding in result["findings"]}
    assert {
        "spec token JIRA-1 in comment",
        "spec token AC1 in comment",
        "spec token KI-001 in comment",
    } <= messages


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--select", "HSL001,"),
        ("--select", "HSL001,,HSL002"),
        ("--ignore", " "),
        ("--extend-select", "HSL001,"),
        ("--extend-ignore", " "),
    ],
)
def test_empty_cli_rule_id_elements_are_usage_errors(
    repository: Path, option: str, value: str
) -> None:
    completed = _run(repository, "check", "--root", str(repository), option, value)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "unknown or forbidden rule ID" in completed.stderr


def test_config_and_syntax_errors_have_documented_stream_ownership(repository: Path) -> None:
    bad_config = repository / "bad.toml"
    bad_config.write_text("[tool.house-lint\n")
    config_failure = _run(repository, "check", "--config", str(bad_config), "--format", "json")
    assert config_failure.returncode == 2
    assert config_failure.stderr == ""
    assert json.loads(config_failure.stdout)["files_scanned"] == 0

    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    (repository / "src" / "broken.py").write_text("def broken()\n    pass\n")
    incomplete = _run(repository, "check", "--root", str(repository), "--format", "json")
    result = json.loads(incomplete.stdout)
    assert incomplete.returncode == 3
    assert incomplete.stderr == ""
    assert result["errors"][0]["kind"] == "syntax"

    text_incomplete = _run(repository, "check", "--root", str(repository), "--format", "text")
    assert text_incomplete.returncode == 3
    assert "src/finding.py:2:5: HSL002 import inside function body" in text_incomplete.stdout
    assert "summary:" in text_incomplete.stdout
    assert "error: src/broken.py: [syntax-error analysis/ast-parse]" in text_incomplete.stderr


def test_unavailable_pep263_codec_is_a_decode_error_and_preserves_sibling_findings(
    repository: Path,
) -> None:
    (repository / "src" / "unknown_codec.py").write_bytes(b"# coding: unknown-codec\n")
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")

    completed = _run(repository, "check", "--root", str(repository), "--format", "json")

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert any(
        error["path"] == "src/unknown_codec.py" and error["kind"] == "decode"
        for error in result["errors"]
    )
    assert any(
        finding["path"] == "src/finding.py" and finding["rule_id"] == "HSL002"
        for finding in result["findings"]
    )


def test_json_parser_usage_error_has_a_schema_result(repository: Path) -> None:
    completed = _run(repository, "check", "--format", "json", "--root")

    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert completed.stderr == ""
    assert result["root"] is None
    assert result["config"] is None
    assert result["files_scanned"] == result["files_skipped"] == 0
    assert result["findings"] == []
    assert result["errors"][0]["kind"] == "config"


def test_debug_operational_details_stay_on_stderr_for_json_output(repository: Path) -> None:
    (repository / "src" / "broken.py").write_text("def broken()\n    pass\n")

    completed = _run(repository, "check", "--root", str(repository), "--format", "json", "--debug")

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "syntax"
    assert "debug: syntax error during analysis/ast-parse:" in completed.stderr
    assert "SyntaxError:" in completed.stderr
    assert "def broken()" in completed.stderr


def test_debug_tracebacks_survive_a_warm_cache(repository: Path) -> None:
    """`--debug` output must not depend on whether a previous run cached the error.

    The traceback is printed by `scan_source`, which a cache hit skips entirely — so the first
    `--debug` run showed the exception type and the offending source, and every identical run
    after it showed only the one-line structured error. Someone reaching for `--debug` to
    diagnose a parse failure would get less information the second time they asked, with nothing
    on screen to explain why.
    """
    (repository / "src" / "broken.py").write_text("def broken()\n    pass\n")

    cold = _run(repository, "check", "--root", str(repository), "--debug")
    warm = _run(repository, "check", "--root", str(repository), "--debug")

    for completed in (cold, warm):
        assert completed.returncode == 3
        assert "SyntaxError:" in completed.stderr
        assert "def broken()" in completed.stderr


def test_debug_reports_shadowed_config_when_standalone_and_pyproject_both_exist(
    repository: Path,
) -> None:
    (repository / "house-lint.toml").write_text('[house-lint]\nselect = ["HSL001"]\n')
    (repository / "pyproject.toml").write_text('[tool.house-lint]\nselect = ["HSL001"]\n')

    debug_run = _run(repository, "check", "--root", str(repository), "--debug")
    quiet_run = _run(repository, "check", "--root", str(repository))

    assert debug_run.returncode == 0
    assert quiet_run.returncode == 0
    assert "debug: config" in debug_run.stderr
    assert "house-lint.toml used; shadowed:" in debug_run.stderr
    assert "pyproject.toml" in debug_run.stderr
    assert "shadowed" not in quiet_run.stderr


def test_debug_omits_shadow_line_when_only_one_config_source_exists(repository: Path) -> None:
    (repository / "house-lint.toml").write_text('[house-lint]\nselect = ["HSL001"]\n')

    completed = _run(repository, "check", "--root", str(repository), "--debug")

    assert completed.returncode == 0
    assert "shadowed" not in completed.stderr


def test_invalid_check_format_writes_only_a_usage_diagnostic_to_stderr(repository: Path) -> None:
    completed = _run(repository, "check", "--root", str(repository), "--format", "xml")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr == "error: [config-error config/load] --format must be text or json\n"


def test_json_config_error_preserves_resolved_root_and_config(repository: Path) -> None:
    bad_config = repository / "bad.toml"
    bad_config.write_text("[tool.house-lint\n")

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--config",
        str(bad_config),
        "--format",
        "json",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert completed.stderr == ""
    assert result["root"] == str(repository.resolve())
    assert result["config"] == str(bad_config.resolve())
    assert result["files_scanned"] == result["files_skipped"] == 0
    assert result["findings"] == []
    assert result["errors"][0]["kind"] == "config"


def test_json_missing_explicit_config_preserves_resolved_root_and_config(repository: Path) -> None:
    missing_config = repository / "missing.toml"

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--config",
        str(missing_config),
        "--format",
        "json",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert result["root"] == str(repository.resolve())
    assert result["config"] == str(missing_config.resolve())
    assert result["files_scanned"] == result["files_skipped"] == 0


def test_json_root_not_a_directory_reports_canonical_root(tmp_path: Path) -> None:
    (tmp_path / "notadir").write_text("not a directory\n")
    cwd = tmp_path / "sub"
    cwd.mkdir()

    completed = _run(cwd, "check", "--root", "../notadir", "--format", "json")

    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert result["root"] == str((tmp_path / "notadir").resolve())
    assert result["errors"][0]["kind"] == "config"
    assert "root is not a directory" in result["errors"][0]["message"]


def test_json_auto_discovery_config_error_preserves_resolved_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.house-lint\n")

    completed = _run(tmp_path, "check", "--format", "json")

    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert completed.stderr == ""
    assert result["root"] is not None
    assert result["config"] is None
    assert result["errors"][0]["kind"] == "config"


def test_source_checkout_module_entry_point_does_not_require_distribution_metadata(
    repository: Path,
) -> None:
    prelude = """
import importlib.metadata
import sys
import runpy

def missing_distribution(_: str) -> str:
    raise importlib.metadata.PackageNotFoundError

importlib.metadata.version = missing_distribution
sys.argv = ["house-lint", "rules", "--format", "json"]
runpy.run_module("house_lint", run_name="__main__")
"""

    completed = _run(repository, prelude=prelude)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["schema_version"] == 1


def test_candidate_budget_is_an_incomplete_subprocess_result(repository: Path) -> None:
    (repository / "src" / "finding.py").write_text("def example():\n    import module\n")
    prelude = """
from house_lint import cli, scanner

scanner.MAX_CANDIDATES_PER_FILE = 0
cli.main()
"""

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        prelude=prelude,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "budget"
    assert result["files_scanned"] == 2


def test_hsl001_stops_at_the_candidate_budget(repository: Path) -> None:
    (repository / "src" / "overflow.py").write_text(
        "\n".join("# utilize this" for _ in range(MAX_CANDIDATES_PER_FILE + 2))
    )

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "budget"
    assert len(result["findings"]) == MAX_CANDIDATES_PER_FILE
    assert {finding["rule_id"] for finding in result["findings"]} == {"HSL001"}


def test_suppression_diagnostics_respect_the_candidate_budget(repository: Path) -> None:
    (repository / "src" / "pragma.py").write_text("# house-lint: ignore[] - generated module\n")
    prelude = """
from house_lint import cli, scanner

scanner.MAX_CANDIDATES_PER_FILE = 0
cli.main()
"""

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        prelude=prelude,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "budget"
    assert result["findings"] == []


def test_suppression_budget_preserves_the_bounded_candidate_prefix(repository: Path) -> None:
    (repository / "src" / "overflow.py").write_text(
        "\n".join("# utilize this" for _ in range(MAX_CANDIDATES_PER_FILE))
        + "\n# house-lint: ignore[] - generated module\n"
    )

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "budget"
    assert len(result["findings"]) == MAX_CANDIDATES_PER_FILE
    assert {finding["rule_id"] for finding in result["findings"]} == {"HSL001"}


def test_detector_and_suppression_budget_preserve_the_bounded_candidate_prefix(
    repository: Path,
) -> None:
    (repository / "src" / "overflow.py").write_text(
        "\n".join("# utilize this" for _ in range(MAX_CANDIDATES_PER_FILE + 1))
        + "\n# house-lint: ignore[] - generated module\n"
    )

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["errors"][0]["kind"] == "budget"
    assert len(result["findings"]) == MAX_CANDIDATES_PER_FILE
    assert {finding["rule_id"] for finding in result["findings"]} == {"HSL001"}


def test_budget_error_preserves_findings_from_completed_files(repository: Path) -> None:
    first = repository / "src" / "a.py"
    overflow = repository / "src" / "overflow.py"
    first.write_text("def example():\n    import module\n")
    overflow.write_text("\n".join("# utilize this" for _ in range(MAX_CANDIDATES_PER_FILE + 1)))

    completed = _run(
        repository,
        "check",
        str(first),
        str(overflow),
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001,HSL002",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert any(
        finding["path"] == "src/a.py" and finding["rule_id"] == "HSL002"
        for finding in result["findings"]
    )


def test_zero_capacity_detector_overflow_applies_known_suppressions(repository: Path) -> None:
    (repository / "src" / "overflow.py").write_text(
        "# house-lint: ignore-file[HSL001] - generated module\n"
        + "\n".join("# utilize this" for _ in range(MAX_CANDIDATES_PER_FILE))
        + "\ndef example():\n    import package  # house-lint: ignore[HSL002] - lazy dependency\n"
    )

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001,HSL002",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert result["findings"] == []
    assert result["summary"]["suppressed_count"] == MAX_CANDIDATES_PER_FILE


def test_suppression_budget_applies_completed_suppressions(repository: Path) -> None:
    (repository / "src" / "overflow.py").write_text(
        "# house-lint: ignore-file[HSL001] - generated module\n"
        + "\n".join("# utilize this" for _ in range(MAX_CANDIDATES_PER_FILE - 1))
        + "\n# house-lint: ignore[] - generated module\n"
        + "# house-lint: ignore[] - generated module\n"
    )

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--select",
        "HSL001",
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert [finding["rule_id"] for finding in result["findings"]] == ["HSL900"]
    assert result["summary"]["suppressed_count"] == MAX_CANDIDATES_PER_FILE - 1


def test_budget_error_counts_the_file_when_rule_execution_begins(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = repository / "src" / "finding.py"
    source.write_text("def example():\n    import module\n")
    monkeypatch.setattr(scanner, "MAX_CANDIDATES_PER_FILE", 0)

    code = cli.check(paths=[source], root=repository, format="json")
    result = json.loads(capsys.readouterr().out)

    assert code == 3
    assert result["files_scanned"] == 1


def test_subprocess_internal_failure_exits_four_with_parseable_json(repository: Path) -> None:
    prelude = """
from house_lint import cli, scanner

def fail(*_args: object) -> object:
    raise RuntimeError("simulated failure")

scanner.detect_candidates = fail
cli.main()
"""

    completed = _run(
        repository,
        "check",
        "--root",
        str(repository),
        "--format",
        "json",
        "--debug",
        prelude=prelude,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 4
    assert result["errors"][0]["kind"] == "internal"
    assert "Traceback" in completed.stderr


def test_subprocess_internal_error_precedes_incomplete_scan_and_preserves_findings(
    repository: Path,
) -> None:
    first = repository / "src" / "a.py"
    broken = repository / "src" / "b.py"
    failing = repository / "src" / "c.py"
    first.write_text("def example():\n    import module\n")
    broken.write_text("def broken()\n    pass\n")
    failing.write_text("value = 1\n")
    prelude = """
from house_lint import cli, scanner

original = scanner.detect_candidates

def fail_c(source, detector_inputs, **kwargs):
    if source.relative_path == "src/c.py":
        raise RuntimeError("simulated failure")
    return original(source, detector_inputs, **kwargs)

scanner.detect_candidates = fail_c
cli.main()
"""

    completed = _run(
        repository,
        "check",
        str(first),
        str(broken),
        str(failing),
        "--root",
        str(repository),
        "--format",
        "json",
        prelude=prelude,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 4
    assert [finding["path"] for finding in result["findings"]] == ["src/a.py"]
    assert {error["kind"] for error in result["errors"]} == {"syntax", "internal"}
    assert completed.stderr == ""


def test_internal_error_preserves_completed_results_and_writes_debug_to_stderr(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = repository / "src" / "a.py"
    second = repository / "src" / "b.py"
    first.write_text("def example():\n    import module\n")
    second.write_text("value = 1\n")
    original = scanner.detect_candidates

    def fail_second(
        source: scanner.SourceFile, detector_inputs: object, **kwargs: object
    ) -> object:
        if source.relative_path == "src/b.py":
            raise RuntimeError("simulated failure")
        return original(source, detector_inputs, **kwargs)

    monkeypatch.setattr(scanner, "detect_candidates", fail_second)

    code = cli.check(paths=[first, second], root=repository, format="json", debug=True)
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert code == 4
    assert result["findings"][0]["path"] == "src/a.py"
    assert result["errors"][0]["kind"] == "internal"
    assert result["errors"][0]["message"] == "an unexpected internal error occurred"
    assert "simulated failure" not in json.dumps(result)
    assert "Traceback" in captured.err
    assert "simulated failure" in captured.err
    assert "internal error during analysis/rule-dispatch" in captured.err
    assert result["files_scanned"] == 2


def test_source_construction_failure_preserves_completed_results(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = repository / "src" / "a.py"
    second = repository / "src" / "b.py"
    first.write_text("def example():\n    import module\n")
    second.write_text("value = 1\n")
    source_file = scanner.SourceFile

    def fail_second(path: Path, root: Path, **kwargs: object) -> scanner.SourceFile:
        if path == second:
            raise RuntimeError("simulated construction failure")
        return source_file(path, root)

    monkeypatch.setattr(scanner, "SourceFile", fail_second)

    code = cli.check(paths=[first, second], root=repository, format="json")
    result = json.loads(capsys.readouterr().out)

    assert code == 4
    assert [finding["path"] for finding in result["findings"]] == ["src/a.py"]
    assert result["files_scanned"] == 1
    assert result["errors"][0]["kind"] == "internal"
    assert result["errors"][0]["operation"] == "source-load"


def test_cli_boundary_internal_error_preserves_resolved_context(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_scan(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("simulated scan setup failure")

    monkeypatch.setattr(cli, "_scan", fail_scan)

    code = cli.check(root=repository, format="json")
    result = json.loads(capsys.readouterr().out)

    assert code == 4
    assert result["root"] == str(repository.resolve())
    assert result["config"] is None


def test_root_resolution_runtime_error_is_caught_as_internal_error(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original_resolve = Path.resolve

    def fail_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if self == repository:
            raise RuntimeError("simulated symlink cycle")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    code = cli.check(root=repository, format="json")
    result = json.loads(capsys.readouterr().out)

    assert code == 4
    assert result["errors"][0]["kind"] == "internal"
    assert result["root"] is None
