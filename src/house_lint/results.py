"""Public, schema-versioned result data transfer objects."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _validate_location(
    line: int | None, column: int | None, end_line: int | None, end_column: int | None
) -> None:
    location = (line, column, end_line, end_column)
    if all(value is None for value in location):
        return
    if any(value is None for value in location) or not all(
        type(value) is int and value >= 1 for value in location
    ):
        raise ValueError("locations must be all null or valid 1-based coordinates")
    assert (
        line is not None and column is not None and end_line is not None and end_column is not None
    )
    if (end_line, end_column) < (line, column):
        raise ValueError("location end must not precede its start")


@dataclass(frozen=True)
class Finding:
    rule_id: str
    path: str
    line: int | None
    column: int | None
    end_line: int | None
    end_column: int | None
    message: str

    def __post_init__(self) -> None:
        _validate_location(self.line, self.column, self.end_line, self.end_column)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "message": self.message,
        }


@dataclass(frozen=True)
class LintError:
    code: str
    kind: str
    path: str | None
    line: int | None
    column: int | None
    end_line: int | None
    end_column: int | None
    phase: str
    operation: str
    rule_id: str | None
    message: str

    def __post_init__(self) -> None:
        _validate_location(self.line, self.column, self.end_line, self.end_column)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "phase": self.phase,
            "operation": self.operation,
            "rule_id": self.rule_id,
            "message": self.message,
        }


def error(
    kind: str, phase: str, operation: str, message: str, *, path: str | None = None
) -> LintError:
    return LintError(
        f"{kind}-error", kind, path, None, None, None, None, phase, operation, None, message
    )


def internal_error(phase: str, operation: str, *, path: str | None = None) -> LintError:
    """Create a stable public error without exposing exception details."""
    return error("internal", phase, operation, "an unexpected internal error occurred", path=path)


@dataclass(frozen=True)
class ScanResult:
    root: Path | None
    config: Path | None
    enabled_rules: tuple[str, ...]
    files_scanned: int
    files_skipped: int
    findings: tuple[Finding, ...] = ()
    suppressed_count: int = 0
    errors: tuple[LintError, ...] = ()

    def __post_init__(self) -> None:
        if self.root is not None:
            object.__setattr__(self, "root", self.root.absolute())
        if self.config is not None:
            object.__setattr__(self, "config", self.config.absolute())

    @property
    def is_clean(self) -> bool:
        return not self.findings and not self.errors

    @property
    def is_zero_file_scan(self) -> bool:
        return self.files_scanned == 0 and self.is_clean

    def to_dict(self) -> dict[str, Any]:
        findings = sorted(
            self.findings,
            key=lambda item: (
                item.path,
                item.line or 0,
                item.column or 0,
                item.rule_id,
                item.message,
            ),
        )
        errors = sorted(
            self.errors, key=lambda item: (item.path or "", item.line or 0, item.kind, item.message)
        )
        return {
            "schema_version": 1,
            "root": str(self.root) if self.root is not None else None,
            "config": str(self.config) if self.config is not None else None,
            "enabled_rules": sorted(self.enabled_rules),
            "files_scanned": self.files_scanned,
            "files_skipped": self.files_skipped,
            "findings": [finding.to_dict() for finding in findings],
            "errors": [err.to_dict() for err in errors],
            "summary": {
                "finding_count": len(findings),
                "error_count": len(errors),
                "suppressed_count": self.suppressed_count,
            },
        }


@dataclass(frozen=True)
class RuleInfo:
    id: str
    name: str
    description: str
    enablement: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enablement": self.enablement,
        }


@dataclass(frozen=True)
class RuleList:
    rules: tuple[RuleInfo, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "rules": [rule.to_dict() for rule in self.rules]}
