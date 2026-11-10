"""`house-lint check --base <ref>`: report only the findings the checked-out commit introduced.

Both commits are extracted from git objects into their own temporary directories, so the working
tree is never read. Each is scanned by the normal pipeline under the head commit's config, and a
head finding is new when it pairs with no base finding (see `matching`). The base commit is the
merge-base of `<ref>` and `HEAD`, so only the head's own commits can introduce a finding.
"""

import os
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from house_lint.config import ConfigError, LintConfig, SelectionOverrides
from house_lint.discovery import (
    GitFailure,
    inside_repository,
    resolve_project,
    run_git,
)
from house_lint.language import Language, language_for
from house_lint.matching import RenameMap, SpanText, strip_whitespace
from house_lint.pipeline import CacheLocation, run_scan
from house_lint.python_source import decode_python
from house_lint.results import BaseComparison, Finding, LintError, ScanResult, error
from house_lint.rule_catalog import base_matcher
from house_lint.source import LoadFailure, load_file

GIT_HINT = "--base needs a git repository holding both commits; in CI, fetch full history"
# A commit's own index, so extracting it never touches the repository's index or working tree.
INDEX_VARIABLE = "GIT_INDEX_FILE"


class GateFailure(Exception):
    """Something `--base` needs before it can scan, such as a commit, is missing."""


@dataclass(frozen=True)
class Commits:
    merge_base: str
    head: str


@dataclass(frozen=True)
class Tree:
    """One commit's tracked files under `directory`, and the project root mapped into it."""

    directory: Path
    root: Path

    def in_project(self, message: str, top: Path) -> str:
        """`message` naming the repository's paths rather than this temporary tree's."""
        return message.replace(str(self.directory), str(top))


@dataclass(frozen=True)
class GateRun:
    """A `--base` run's result, and the head commit's config it ran under.

    `config` is None when the run stopped before loading one, such as on a missing commit.
    """

    result: ScanResult
    config: LintConfig | None


def run_gate(
    ref: str,
    *,
    root: Path,
    config: Path | None,
    overrides: SelectionOverrides,
    read_cache: bool,
    cache_dir: Path | None,
    debug: bool,
) -> GateRun:
    """Scan `HEAD` and the merge-base of `ref` and `HEAD`, and report the head's new findings.

    `root` is the project root resolved in the working tree, and `config` the explicit `--config`,
    if any. Both only locate the project in the repository: the config that applies is the one
    at the same place in the head commit. Raises `ConfigError` for a config `--base` can't use.
    """
    if config is not None and not inside_repository(config.parent):
        raise ConfigError("--base needs --config to be inside the git repository")
    try:
        top = repository_top(root)
        commits = resolve_commits(top, ref)
        root_relative = root.relative_to(top)
        renames = rename_map(top, commits, root_relative)
        with tempfile.TemporaryDirectory(prefix="house-lint-base-") as temporary:
            workspace = Path(temporary).resolve()
            head_tree = extract(top, commits.head, workspace / "head", root_relative)
            base_tree = extract(top, commits.merge_base, workspace / "base", root_relative)
            head_config_path, lint_config = load_head_config(
                head_tree, top, root, config, overrides
            )

            def scan(tree: Tree) -> ScanResult:
                # The extraction holds no `.git` and only tracked files, so a plain walk lists
                # what `git ls-files` would. `config_path` only labels the result, replaced below.
                return run_scan(
                    (),
                    root=tree.root,
                    config_path=None,
                    config=lint_config,
                    use_git=False,
                    read_cache=read_cache,
                    cache=CacheLocation.for_project(root, cache_dir),
                    debug=debug,
                )

            head_result = scan(head_tree)
            base_result = scan(base_tree)
            new = new_findings(
                head_result.findings,
                base_result.findings,
                head_text=span_texts(head_result.findings, head_tree.root),
                base_text=span_texts(base_result.findings, base_tree.root),
                renames=renames,
            )
            errors = (
                *labelled(head_result.errors, "head", head_tree, top),
                *labelled(base_result.errors, "base", base_tree, top),
            )
    except (GitFailure, GateFailure) as exc:
        failed = ScanResult(
            root, config, (), 0, errors=(error("git", "base", "resolve", str(exc)),)
        )
        return GateRun(failed, None)
    comparison = BaseComparison(
        ref, commits.merge_base, commits.head, len(head_result.findings) - len(new)
    )
    result = ScanResult(
        root,
        head_config_path,
        head_result.enabled_rules,
        head_result.files_scanned,
        new,
        head_result.suppressed_count,
        errors,
        comparison,
    )
    return GateRun(result, lint_config)


def repository_top(root: Path) -> Path:
    """The top level of the repository holding `root`. Every other git call runs there."""
    if not inside_repository(root):
        raise GateFailure(f"--base needs a git repository, and {root} is not inside one")
    output = run_git(root, ("rev-parse", "--show-toplevel"), hint=GIT_HINT).stdout
    top = Path(os.fsdecode(output).rstrip("\n")).resolve()
    if not root.is_relative_to(top):
        raise GateFailure(f"{root} is not inside the repository at {top}")
    return top


def resolve_commits(top: Path, ref: str) -> Commits:
    """Resolve `ref`, `HEAD`, and their merge-base to commit SHAs.

    `ref` is resolved on its own first, after `--end-of-options`, so a value starting with `-` is
    never read as an option, and every later call uses the SHA.
    """
    try:
        ref_commit = rev_parse(top, "--end-of-options", f"{ref}^{{commit}}")
    except GitFailure as exc:
        raise GateFailure(f"--base {ref!r} does not name a commit; {GIT_HINT}") from exc
    try:
        head = rev_parse(top, "HEAD^{commit}")
    except GitFailure as exc:
        raise GateFailure("HEAD does not name a commit yet") from exc
    found = run_git(top, ("merge-base", ref_commit, head), accepted=(0, 1), hint=GIT_HINT)
    if found.returncode == 1:
        raise GateFailure(
            f"{ref} and HEAD have no merge-base: their histories are unrelated, or a shallow "
            f"clone lacks it, so fetch full history"
        )
    return Commits(found.stdout.decode().strip(), head)


def rev_parse(top: Path, *args: str) -> str:
    return run_git(top, ("rev-parse", "--verify", *args), hint=GIT_HINT).stdout.decode().strip()


def rename_map(top: Path, commits: Commits, root_relative: Path) -> RenameMap:
    """Map each file renamed between the commits to its base path, both relative to the root.

    `-l0` lifts the rename limit, so git never warns on a large change and fails `run_git`. `-z`,
    `--no-color`, and `--no-relative` keep user config from changing the output. A rename that
    git scores below its similarity threshold reads as a delete plus an add.
    """
    args = ("diff", "-z", "--name-status", "-M", "-l0", "--no-color", "--no-relative")
    output = run_git(top, (*args, commits.merge_base, commits.head), hint=GIT_HINT).stdout
    # Each entry is a status then its paths: two paths for a rename (`R<score>`), one for any
    # other status. Without `-C`, git reports no copies, the only other two-path status.
    fields = iter(os.fsdecode(field) for field in output.split(b"\0") if field)
    renames: dict[str, str] = {}
    for status in fields:
        if not status.startswith("R"):
            next(fields)
            continue
        old = under_root(next(fields), root_relative)
        new = under_root(next(fields), root_relative)
        if old is not None and new is not None:
            renames[new] = old
    return renames


def under_root(path: str, root_relative: Path) -> str | None:
    """A repository-relative path made root-relative, or None when it lies outside the root."""
    relative = Path(path)
    if not relative.is_relative_to(root_relative):
        return None
    return relative.relative_to(root_relative).as_posix()


def extract(top: Path, commit: str, directory: Path, root_relative: Path) -> Tree:
    """Write every file tracked at `commit` under `directory`, through a temporary index.

    Runs from the top level, because `checkout-index` run from a subdirectory extracts only that
    subtree. The root is created when the commit has nothing there, so it scans as empty.
    """
    env = {INDEX_VARIABLE: str(directory.with_suffix(".index"))}
    run_git(top, ("read-tree", commit), env=env, hint=GIT_HINT)
    run_git(top, ("checkout-index", "-a", f"--prefix={directory}/"), env=env, hint=GIT_HINT)
    root = directory / root_relative
    root.mkdir(parents=True, exist_ok=True)
    return Tree(directory, root)


def load_head_config(
    head_tree: Tree, top: Path, root: Path, config: Path | None, overrides: SelectionOverrides
) -> tuple[Path | None, LintConfig]:
    """Load the config that applies at the head commit, and its path in the project.

    An explicit `--config` is looked up at the same repository path in the head commit, and is a
    `ConfigError` when the commit has no file there: the user named it, so it never silently
    gives way to the built-in config. Otherwise the root's own config sources at the head
    commit decide, as in a normal run, with the root passed explicitly so nothing above it is read.
    The returned path is built from the project's own paths, never by un-mapping the resolved one,
    which a symlinked config could take outside the extraction.
    """
    try:
        if config is None:
            resolution = resolve_project(root=head_tree.root)
        else:
            committed = config.relative_to(top)
            candidate = head_tree.directory / committed
            if not candidate.is_file():
                raise ConfigError(
                    f"--config {committed.as_posix()} is not committed at HEAD; "
                    "--base reads the config from the commit"
                )
            resolution = resolve_project(root=head_tree.root, config=candidate)
        loaded = overrides.load(resolution.config)
    except ConfigError as exc:
        raise ConfigError(head_tree.in_project(str(exc), top)) from exc
    if resolution.config is None:
        return None, loaded
    # A config the root discovered sits in the root itself.
    return (config if config is not None else root / resolution.config.name), loaded


def new_findings(
    head_findings: Iterable[Finding],
    base_findings: Iterable[Finding],
    *,
    head_text: SpanText,
    base_text: SpanText,
    renames: RenameMap,
) -> tuple[Finding, ...]:
    """Pair each rule's findings with its own matcher and return the head findings left over."""
    base_by_rule = by_rule(base_findings)
    new: list[Finding] = []
    for rule_id, findings in by_rule(head_findings).items():
        matcher = base_matcher(rule_id)
        new.extend(
            matcher(
                findings,
                base_by_rule.get(rule_id, []),
                head_text=head_text,
                base_text=base_text,
                renames=renames,
            )
        )
    return tuple(new)


def by_rule(findings: Iterable[Finding]) -> Mapping[str, list[Finding]]:
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.rule_id].append(finding)
    return grouped


def span_texts(findings: Iterable[Finding], root: Path) -> dict[Finding, str]:
    """Each located finding's span text, whitespace removed, read back from its extraction.

    Read through `load_file`, so containment and the size bound apply as in the scan, and decoded
    as the file's language layer decodes it. A finding whose file fails either is left out, and so
    pairs on its message alone. The extraction is private and nothing edits it, so this second
    read sees the bytes that were scanned.
    """
    located: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        # A location is all four coordinates or none (`results.validate_location`).
        if finding.line is not None:
            located[finding.path].append(finding)
    texts: dict[Finding, str] = {}
    for relative, file_findings in located.items():
        lines = file_lines(root, relative)
        if lines is None:
            continue
        for finding in file_findings:
            texts[finding] = strip_whitespace(span_of(lines, finding))
    return texts


def file_lines(root: Path, relative: str) -> list[str] | None:
    """A file's decoded lines, split on `\\n` as the tokenizer counts them (`PythonSource.lines`)."""
    path = root / relative
    loaded = load_file(path, root, resolved_path=path.resolve())
    if isinstance(loaded, LoadFailure):
        return None
    try:
        if language_for(path) is Language.PYTHON:
            text = decode_python(loaded.content)
        else:
            text = loaded.content.decode("utf-8")
    except (UnicodeDecodeError, LookupError, SyntaxError):
        return None
    return text.split("\n")


def span_of(lines: list[str], finding: Finding) -> str:
    """The text between a finding's 1-based start and its exclusive end, by UTF-8 byte column."""
    line, column = finding.line, finding.column
    end_line, end_column = finding.end_line, finding.end_column
    assert line is not None and column is not None
    assert end_line is not None and end_column is not None
    spanned = [text.encode() for text in lines[line - 1 : end_line]]
    if not spanned:
        return ""
    # The end first: on a one-line span both cuts apply to the same line, and the end column
    # counts from the line's start, not from the start column.
    spanned[-1] = spanned[-1][: end_column - 1]
    spanned[0] = spanned[0][column - 1 :]
    return b"\n".join(spanned).decode(errors="replace")


def labelled(
    errors: Iterable[LintError], side: str, tree: Tree, top: Path
) -> tuple[LintError, ...]:
    """Say which run each error came from, naming the repository's paths rather than the tree's.

    An error's `path` is already root-relative; only its message can hold an extraction path.
    """
    return tuple(
        replace(err, message=f"{side} run: {tree.in_project(err.message, top)}") for err in errors
    )


__all__ = ["GateRun", "run_gate"]
