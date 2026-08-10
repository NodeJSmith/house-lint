"""Private detector provenance models.

These types intentionally do not form part of the reporter/configuration API.
"""

from dataclasses import dataclass
from enum import Enum

MAX_CANDIDATES_PER_FILE = 10_000


class CandidateBudgetExceeded(RuntimeError):
    """Raised when one file produces more candidates than the fixed safety limit."""

    def __init__(self, path: str, limit: int = MAX_CANDIDATES_PER_FILE) -> None:
        self.path = path
        self.limit = limit
        super().__init__(f"candidate limit exceeded for {path}: {limit}")


class SourceKind(Enum):
    STATEMENT = "statement"
    FILE = "file"
    FILENAME = "filename"


@dataclass(frozen=True)
class StatementKey:
    start_line: int
    start_column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class CandidateFinding:
    rule_id: str
    path: str
    message: str
    line: int | None
    column: int | None
    end_line: int | None
    end_column: int | None
    source_kind: SourceKind
    owner: StatementKey | None = None
