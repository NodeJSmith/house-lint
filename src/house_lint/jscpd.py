"""Running jscpd 5.4.1 on a list of files and reading its report as typed clone pairs.

Every failure raises `ProjectRuleFailure`, since a project rule is the only caller, with a message
that names the `duplicates` extra: a broken or mismatched install is the likeliest cause of each.
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
INSTALL_HINT = (
    f"install the house-lint[duplicates] extra (jscpd=={JSCPD_VERSION}), "
    "or run uvx --from 'house-lint[duplicates]' house-lint check"
)


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


class JscpdReport(msgspec.Struct):
    duplicates: list[ReportDuplicate]


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


def find_clones(paths: Sequence[Path], *, min_tokens: int, min_lines: int) -> list[ClonePair]:
    """Run jscpd in weak mode on exactly `paths`, and return the clone pairs it reports.

    `paths` should be absolute and resolved: each fragment's `path` is the entry jscpd named, and
    a name that isn't one of them is a failure. jscpd reports a block of 3+ copies as pairs
    against one copy only, so grouping them is the caller's job.
    """
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
                    # house-lint's discovery already decided the file set.
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
        run_jscpd([binary, "-c", str(config)], cwd=output)
        report = output / REPORT_NAME
        if not report.is_file():
            raise install_failure(f"jscpd wrote no {REPORT_NAME}")
        raw = report.read_bytes()
    try:
        decoded = msgspec.json.decode(raw, type=JscpdReport)
    except msgspec.DecodeError as exc:
        raise install_failure(f"jscpd's report could not be read: {exc}") from exc
    return [
        ClonePair(report_fragment(dup.first_file, given), report_fragment(dup.second_file, given))
        for dup in decoded.duplicates
    ]


def find_binary() -> str:
    """Return jscpd next to house-lint's own interpreter, else on PATH."""
    found = shutil.which("jscpd", path=str(Path(sys.executable).parent)) or shutil.which("jscpd")
    if found is None:
        raise install_failure("jscpd was not found")
    return found


def check_version(binary: str) -> None:
    """Refuse any jscpd but the pinned version: its output format and thresholds are tuned to it."""
    words = run_jscpd([binary, "--version"]).split()
    version = words[-1] if words else "<none>"
    if version != JSCPD_VERSION:
        raise install_failure(f"jscpd {JSCPD_VERSION} is required, found {version} at {binary}")


def run_jscpd(command: list[str], *, cwd: Path | None = None) -> str:
    """Run one jscpd command and return its stdout, raising on launch failure, timeout, or exit."""
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise install_failure(f"jscpd did not finish within {TIMEOUT_SECONDS} seconds") from exc
    except OSError as exc:
        raise install_failure(f"jscpd could not be run ({exc})") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        reason = f": {detail[-1]}" if detail else ""
        raise install_failure(f"jscpd exited with status {completed.returncode}{reason}")
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


__all__ = ["JSCPD_VERSION", "TIMEOUT_SECONDS", "ClonePair", "Fragment", "find_clones"]
