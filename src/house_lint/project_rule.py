"""What a project rule returns and how it reports failure, with no language layer attached."""

from dataclasses import dataclass

from house_lint.results import validate_location
from house_lint.source import LoadedFile


@dataclass(frozen=True)
class Location:
    """One place a project candidate occurs, in a file from the rule's own input.

    `file` is the record from the rule's input, not a path string, so a rule has no path to
    misspell and no record to hand for a file left out of its input. The phase doesn't check it.
    """

    file: LoadedFile
    line: int | None
    column: int | None
    end_line: int | None
    end_column: int | None

    def __post_init__(self) -> None:
        validate_location(self.line, self.column, self.end_line, self.end_column)


@dataclass(frozen=True)
class ProjectCandidate:
    """One issue a project rule found, at locations that are equals: none of them is the primary.

    The project phase reports one finding per location, under the ID of the rule that ran.
    """

    message: str
    locations: tuple[Location, ...]

    def __post_init__(self) -> None:
        if not self.locations:
            raise ValueError("a project candidate needs at least one location")


class ProjectRuleFailure(Exception):
    """A project rule could not run, for a reason the user can act on.

    The message reaches the user. Any other exception a rule raises is treated as a bug, and its
    details stay hidden behind the generic internal error.
    """


__all__ = ["Location", "ProjectCandidate", "ProjectRuleFailure"]
