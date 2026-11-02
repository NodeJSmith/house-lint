"""Per-file scan orchestration: load, detect, and apply suppressions for one file."""

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from house_lint.analysis import MAX_CANDIDATES_PER_FILE, CandidateBudgetExceeded, CandidateFinding
from house_lint.config import DetectorInput
from house_lint.python_source import ParseFailure, PythonSource, parse_python
from house_lint.results import Finding, LintError, error, internal_error
from house_lint.source import LoadedFile, LoadFailure, load_file
from house_lint.suppressions import SuppressionBudgetExceeded, apply_suppressions


def _candidate_budget_error(path: str) -> LintError:
    return error(
        "budget",
        "analysis",
        "candidate-count",
        str(CandidateBudgetExceeded(path)),
        path=path,
    )


@dataclass(frozen=True)
class FileScanResult:
    findings: tuple[Finding, ...] = ()
    errors: tuple[LintError, ...] = ()
    suppressed_count: int = 0
    files_scanned: int = 0
    stop: bool = False


def open_source(
    path: Path, *, root: Path, resolved_path: Path, debug: bool
) -> LoadedFile | LoadFailure | FileScanResult:
    """Perform this file's one read of its bytes.

    Split out from `scan_source` so the caller can compute a cache key from the bytes this read
    produced and skip scanning on a hit, without any second read of the path.

    A `FileScanResult` comes back only for a process-boundary failure (`stop=True`). Ordinary load
    failures (path escape, non-regular file, oversize, read error) come back as a `LoadFailure`.
    """
    try:
        return load_file(path, root, resolved_path=resolved_path)
    except Exception:  # noqa: BLE001 - this is the process-boundary internal-error path.
        if debug:
            traceback.print_exc(file=sys.stderr)
        return FileScanResult(
            errors=(internal_error("analysis", "source-load", path=_fallback_path(path, root)),),
            stop=True,
        )


def failure_result(failure: LoadFailure | ParseFailure, *, debug: bool) -> FileScanResult:
    """Report one file's failure, printing its preserved exception under `--debug`."""
    if debug and failure.debug_exception is not None:
        traceback.print_exception(failure.debug_exception, file=sys.stderr)
    return FileScanResult(errors=(failure.error,))


def _fallback_path(path: Path, root: Path) -> str:
    """Best-effort reporting path for a file whose load raised unexpectedly."""
    try:
        return path.absolute().relative_to(root.absolute()).as_posix()
    except ValueError:
        return path.name


def scan_source(
    loaded: LoadedFile,
    *,
    enabled_rules: tuple[str, ...],
    detector_inputs: tuple[DetectorInput, ...],
    debug: bool,
) -> FileScanResult:
    """Parse a loaded file, then run detectors and suppressions on it if it parsed."""
    try:
        source = parse_python(loaded)
        if isinstance(source, ParseFailure):
            return failure_result(source, debug=debug)
    except Exception:  # noqa: BLE001 - this is the process-boundary internal-error path.
        if debug:
            traceback.print_exc(file=sys.stderr)
        return FileScanResult(
            # "source-analyze", not "source-load": `open_source` already completed the read and
            # owns the "source-load" label, so a raise here comes from decoding or parsing, not I/O.
            errors=(internal_error("analysis", "source-analyze", path=loaded.relative_path),),
            stop=True,
        )
    return _scan_ready_source(
        source, enabled_rules=enabled_rules, detector_inputs=detector_inputs, debug=debug
    )


def _scan_ready_source(
    source: PythonSource,
    *,
    enabled_rules: tuple[str, ...],
    detector_inputs: tuple[DetectorInput, ...],
    debug: bool,
) -> FileScanResult:
    """Run detectors and suppressions for a successfully parsed source file."""
    candidates: list[CandidateFinding] = []
    try:
        for rule, options in detector_inputs:
            candidates.extend(
                rule.detect(source, options, limit=MAX_CANDIDATES_PER_FILE - len(candidates))
            )
        suppressed = apply_suppressions(
            source, tuple(candidates), enabled_rules, limit=MAX_CANDIDATES_PER_FILE
        )
    except SuppressionBudgetExceeded as exc:
        return FileScanResult(
            findings=exc.result.findings,
            errors=(_candidate_budget_error(exc.path),),
            suppressed_count=exc.result.suppressed_count,
            files_scanned=1,
        )
    except CandidateBudgetExceeded as exc:
        return _recover_candidate_budget(source, candidates, enabled_rules, exc)
    except Exception:  # noqa: BLE001 - this is the process-boundary internal-error path.
        if debug:
            traceback.print_exc(file=sys.stderr)
        return FileScanResult(
            errors=(internal_error("analysis", "rule-dispatch", path=source.relative_path),),
            files_scanned=1,
            stop=True,
        )
    return FileScanResult(
        findings=suppressed.findings,
        suppressed_count=suppressed.suppressed_count,
        files_scanned=1,
    )


def _recover_candidate_budget(
    source: PythonSource,
    candidates: list[CandidateFinding],
    enabled_rules: tuple[str, ...],
    exceeded: CandidateBudgetExceeded,
) -> FileScanResult:
    """Merge the overflowing detector's partial prefix before applying known suppressions."""
    candidates.extend(exceeded.candidates)
    findings: tuple[Finding, ...] = ()
    suppressed_count = 0
    try:
        suppressed = apply_suppressions(
            source,
            tuple(candidates),
            enabled_rules,
            candidates_complete=False,
            limit=MAX_CANDIDATES_PER_FILE,
        )
    except SuppressionBudgetExceeded as exc:
        findings = exc.result.findings
        suppressed_count = exc.result.suppressed_count
    except CandidateBudgetExceeded:
        pass
    else:
        findings = suppressed.findings
        suppressed_count = suppressed.suppressed_count
    return FileScanResult(
        findings=findings,
        errors=(_candidate_budget_error(exceeded.path),),
        suppressed_count=suppressed_count,
        files_scanned=1,
    )
