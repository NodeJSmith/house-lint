"""Public, schema-versioned result data transfer objects."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _location(value: int | None) -> int | None:
    return value


def _relative_posix(path: Path, root: Path) -> str:
    return path.absolute().relative_to(root.absolute()).as_posix()


@dataclass(frozen=True)
class Finding:
    rule_id: str
    path: str
    line: int | None
    column: int | None
    end_line: int | None
    end_column: int | None
    message: str

    @classmethod
    def from_span(
        cls,
        rule_id: str,
        path: Path,
        root: Path,
        line: int,
        column: int,
        end_line: int,
        end_column: int,
        message: str,
    ) -> "Finding":
        """Convert an AST/token span into the public location contract."""
        return cls(
            rule_id,
            _relative_posix(path, root),
            line,
            column + 1,
            end_line,
            end_column + 1,
            message,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "path": self.path,
            "line": _location(self.line),
            "column": _location(self.column),
            "end_line": _location(self.end_line),
            "end_column": _location(self.end_column),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "kind": self.kind,
            "path": self.path,
            "line": _location(self.line),
            "column": _location(self.column),
            "end_line": _location(self.end_line),
            "end_column": _location(self.end_column),
            "phase": self.phase,
            "operation": self.operation,
            "rule_id": self.rule_id,
            "message": self.message,
        }


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

    @property
    def is_clean(self) -> bool:
        return not self.findings and not self.errors

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
            "errors": [error.to_dict() for error in errors],
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
