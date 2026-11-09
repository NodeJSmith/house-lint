"""Running jscpd 5.4.1 on a list of files and reading its report as typed clone pairs.

Every failure raises `ProjectRuleFailure`, since a project rule is the only caller. A failure whose
likeliest cause is a broken or mismatched install names the `duplicates` extra: a missing binary, a
wrong version, a version check that fails, or a report that is missing, unreadable, or names a file
jscpd wasn't given. A timeout or crash of the analysis run happens after the version check passed,
so its message gives the cause instead.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import msgspec
from msgspec import Meta

from house_lint.project_rule import ProjectRuleFailure
from house_lint.source import MAX_SOURCE_BYTES

JSCPD_VERSION = "5.4.1"
TIMEOUT_SECONDS = 120
REPORT_NAME = "jscpd-report.json"
PYTHON_FORMAT = "python"
PYTHON_SUFFIX = ".py"
# `--version` answers in milliseconds, so a hang there shouldn't wait out the analysis limit.
VERSION_TIMEOUT_SECONDS = 10
INSTALL_HINT = (
    f"install the house-lint[duplicates] extra (jscpd=={JSCPD_VERSION}), "
    "or run uvx --from 'house-lint[duplicates]' house-lint check"
)


class JscpdRunError(Exception):
    """A jscpd command failed to launch, timed out, or exited non-zero. The caller adds the advice."""


class JscpdTimeout(JscpdRunError):
    pass


class ReportPosition(msgspec.Struct):
    line: Annotated[int, Meta(ge=1)]
    column: Annotated[int, Meta(ge=0)]


class ReportSide(msgspec.Struct, rename="camel"):
    name: str
    start_loc: ReportPosition
    end_loc: ReportPosition


class ReportDuplicate(msgspec.Struct, rename="camel"):
    first_file: ReportSide
    second_file: ReportSide


class FormatStatistics(msgspec.Struct):
    sources: Annotated[int, Meta(ge=0)]


class ReportStatistics(msgspec.Struct):
    formats: dict[str, FormatStatistics]


class JscpdReport(msgspec.Struct):
    duplicates: list[ReportDuplicate]
    statistics: ReportStatistics


@dataclass(frozen=True)
class Fragment:
    """One side of a clone pair: a path jscpd was given, and its 1-based span.

    `end_line` is the span's last line. `end_column` is exclusive, as in house-lint's AST spans.
    """

    path: Path
    line: int
    column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class ClonePair:
    first: Fragment
    second: Fragment


@dataclass(frozen=True)
class CloneReport:
    """jscpd's clone pairs, and how many of the given files it analyzed.

    jscpd lists no file it analyzed except those in a clone, and counts only files with at least
    `min_tokens` tokens, so `analyzed` can fall short of the files given without any being skipped.
    """

    pairs: list[ClonePair]
    analyzed: int


def find_clones(paths: Sequence[Path], *, min_tokens: int, min_lines: int) -> CloneReport:
    """Run jscpd in weak mode on exactly `paths`, and return the clone pairs it reports.

    jscpd picks a file's language from its name's suffix, and silently drops a suffix it doesn't
    know, so a path without `.py` (a `.py` symlink's target) is a failure before jscpd runs.

    `paths` should be absolute and resolved: each fragment's `path` is the entry jscpd named, and
    a name that isn't one of them is a failure. jscpd reports a block of 3+ copies as pairs
    against one copy only, so grouping them is the caller's job.
    """
    misnamed = sorted(str(path) for path in paths if path.suffix != PYTHON_SUFFIX)
    if misnamed:
        raise ProjectRuleFailure(
            f"jscpd can't read {misnamed[0]} as Python, since it has no {PYTHON_SUFFIX} suffix; "
            "a .py symlink to it does this, so exclude the symlink or rename its target"
        )
    binary = find_binary()
    check_version(binary)
    given = {str(path): path for path in paths}
    with tempfile.TemporaryDirectory(prefix="house-lint-jscpd-") as workdir:
        output = Path(workdir)
        config = output / "jscpd.json"
        config.write_text(
            json.dumps(
                {
                    "path": list(given),
                    # house-lint's discovery already decided the file set. jscpd 5.4.1 compares a
                    # listed file even when gitignored; this keeps that so if a later version
                    # changes it.
                    "gitignore": False,
                    # Full names, so each maps back to the path it was given as.
                    "absolute": True,
                    # Comments aren't tokens, so a copy with different comments still matches.
                    "mode": "weak",
                    # jscpd skips files over 1 MB by default, silently; match house-lint's limit.
                    "maxSize": str(MAX_SOURCE_BYTES),
                    "minTokens": min_tokens,
                    "minLines": min_lines,
                    "reporters": ["json"],
                    "output": str(output),
                }
            ),
            encoding="utf-8",
        )
        # `-c` replaces any `.jscpd.json` in the project, and the temporary working directory keeps
        # jscpd from finding one there either.
        try:
            run_jscpd([binary, "-c", str(config)], cwd=output, timeout=TIMEOUT_SECONDS)
        except JscpdTimeout as exc:
            raise ProjectRuleFailure(
                f"{exc} on {len(given)} files; the limit is fixed, so exclude generated or "
                "vendored code, or turn HSL104 off for this scope"
            ) from exc
        except JscpdRunError as exc:
            # The version check passed, so the install is sound: no install hint here.
            raise ProjectRuleFailure(str(exc)) from exc
        report = output / REPORT_NAME
        if not report.is_file():
            raise install_failure(f"jscpd wrote no {REPORT_NAME}")
        raw = report.read_bytes()
    try:
        decoded = msgspec.json.decode(raw, type=JscpdReport)
    except msgspec.DecodeError as exc:
        raise install_failure(f"jscpd's report could not be read: {exc}") from exc
    python = decoded.statistics.formats.get(PYTHON_FORMAT)
    return CloneReport(
        [
            ClonePair(
                report_fragment(dup.first_file, given), report_fragment(dup.second_file, given)
            )
            for dup in decoded.duplicates
        ],
        python.sources if python is not None else 0,
    )


def find_binary() -> str:
    """Return jscpd next to house-lint's own interpreter, else on PATH."""
    found = shutil.which("jscpd", path=str(Path(sys.executable).parent)) or shutil.which("jscpd")
    if found is None:
        raise install_failure("jscpd was not found")
    return found


def check_version(binary: str) -> None:
    """Refuse any jscpd but the pinned version: its output format and thresholds are tuned to it."""
    try:
        words = run_jscpd([binary, "--version"], timeout=VERSION_TIMEOUT_SECONDS).split()
    except JscpdRunError as exc:
        raise install_failure(str(exc)) from exc
    version = words[-1] if words else "<none>"
    if version != JSCPD_VERSION:
        raise install_failure(f"jscpd {JSCPD_VERSION} is required, found {version} at {binary}")


def run_jscpd(command: list[str], *, timeout: float, cwd: Path | None = None) -> str:
    """Run one jscpd command and return its stdout, raising `JscpdRunError` if it fails."""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise JscpdTimeout(f"jscpd did not finish within {timeout:g} seconds") from exc
    except OSError as exc:
        raise JscpdRunError(f"jscpd could not be run ({exc})") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        reason = f": {detail[-1]}" if detail else ""
        raise JscpdRunError(f"jscpd exited with status {completed.returncode}{reason}")
    return completed.stdout


def install_failure(reason: str) -> ProjectRuleFailure:
    """Build the error for `reason`, ending with how to install the pinned jscpd."""
    return ProjectRuleFailure(f"{reason}; {INSTALL_HINT}")


def report_fragment(side: ReportSide, given: dict[str, Path]) -> Fragment:
    path = given.get(side.name)
    if path is None:
        raise install_failure(f"jscpd reported a file it was not given: {side.name}")
    # jscpd's columns are 0-based UTF-8 byte offsets, end exclusive; house-lint's are 1-based.
    return Fragment(
        path,
        side.start_loc.line,
        side.start_loc.column + 1,
        side.end_loc.line,
        side.end_loc.column + 1,
    )


__all__ = [
    "JSCPD_VERSION",
    "TIMEOUT_SECONDS",
    "VERSION_TIMEOUT_SECONDS",
    "ClonePair",
    "CloneReport",
    "Fragment",
    "find_clones",
]
