"""Per-file scan orchestration: load, detect, and apply suppressions for one file."""

import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from house_lint.analysis import MAX_CANDIDATES_PER_FILE, CandidateBudgetExceeded, CandidateFinding
from house_lint.config import DetectorInput
from house_lint.registry import detect_candidates
from house_lint.results import Finding, LintError, error, internal_error
from house_lint.source import SourceFile
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


def scan_file(
    path: Path,
    *,
    root: Path,
    enabled_rules: tuple[str, ...],
    detector_inputs: tuple[DetectorInput, ...],
    debug: bool,
) -> FileScanResult:
    """Scan one selected file after resolving source-load failures."""
    source = _load_source(path, root=root, debug=debug)
    if isinstance(source, FileScanResult):
        return source
    return _scan_ready_source(source, enabled_rules=enabled_rules, detector_inputs=detector_inputs, debug=debug)


def _load_source(path: Path, *, root: Path, debug: bool) -> SourceFile | FileScanResult:
    """Load a source file or convert source-load failures into a scan result."""
    source: SourceFile | None = None
    try:
        source = SourceFile(path, root)
        if source.error is not None:
            if debug and source.debug_exception is not None:
                traceback.print_exception(source.debug_exception, file=sys.stderr)
            return FileScanResult(errors=(source.error,))
        return source
    except Exception:  # noqa: BLE001 - this is the process-boundary internal-error path.
        error_path = source.relative_path if source is not None else path.relative_to(root).as_posix()
        if debug:
            traceback.print_exc(file=sys.stderr)
        return FileScanResult(
            errors=(internal_error("analysis", "source-load", path=error_path),), stop=True
        )


def _scan_ready_source(
    source: SourceFile,
    *,
    enabled_rules: tuple[str, ...],
    detector_inputs: tuple[DetectorInput, ...],
    debug: bool,
) -> FileScanResult:
    """Run detectors and suppressions for a successfully loaded source file."""
    candidates: list[CandidateFinding] = []
    try:
        for detector_input in detector_inputs:
            candidates.extend(
                detect_candidates(
                    source,
                    (detector_input,),
                    limit=MAX_CANDIDATES_PER_FILE - len(candidates),
                )
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
            errors=(
                internal_error("analysis", "rule-dispatch", path=source.relative_path),
            ),
            files_scanned=1,
            stop=True,
        )
    return FileScanResult(
        findings=suppressed.findings,
        suppressed_count=suppressed.suppressed_count,
        files_scanned=1,
    )


def _recover_candidate_budget(
    source: SourceFile,
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
