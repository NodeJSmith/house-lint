"""Reading one file's bytes, the one read path every language shares."""

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from house_lint.results import LintError, error

MAX_SOURCE_BYTES = 10 * 1024 * 1024


def read_regular_file_bytes(path: Path, *, max_bytes: int) -> bytes | None:
    """Read up to `max_bytes` + 1 from a regular file via a nonblocking descriptor.

    The nonblocking descriptor prevents a raced FIFO from stalling the read. Returns None if
    the path isn't a regular file; raises OSError for other failures (missing file, permission
    denied, etc.) so callers can decide how to report them. `load_file` turns both cases into a
    `LoadFailure`.

    `O_NOFOLLOW` narrows the window between discovery resolving a path and the scan opening it.
    Callers pass an already-fully-resolved path, so its final component is by construction not a
    symlink — unless it was replaced with one after discovery approved it, which is exactly the
    case that must not be read. Refusing to follow it turns that race into an ordinary read
    error rather than an out-of-root read. This does not close the window on the *directory*
    components of the path, which would need an `openat`-based descent from the root.
    """
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        return handle.read(max_bytes + 1)


@dataclass(frozen=True)
class LoadedFile:
    """One file's path and bytes. Each language layer decodes `content` by its own rules.

    `content` is the only read of a scanned file, so the cache key and every rule derive from the
    same buffer. See `pipeline._cache_keys`.
    """

    path: Path
    relative_path: str
    content: bytes


@dataclass(frozen=True)
class LoadFailure:
    """A file with no usable bytes: outside the root, non-regular, oversized, or unreadable.

    With no `LoadedFile` to key on, its outcome is never cached. `debug_exception` is preserved for `--debug` output only.
    """

    error: LintError
    debug_exception: BaseException | None = None


def load_file(path: Path, root: Path, *, resolved_path: Path) -> LoadedFile | LoadFailure:
    """Read one file, failing closed before any language layer can inspect it.

    `resolved_path` is discovery's own `resolve()` result, threaded through rather than recomputed.
    Resolving a second time here would reopen a window in which a symlink retargeted after the
    containment check sends the read somewhere discovery never approved. Containment is still
    re-checked here, so passing one in cannot widen what this function will read.
    """
    absolute = path.absolute()
    try:
        relative_path = absolute.relative_to(root.absolute()).as_posix()
    except ValueError:
        return _path_escape(path.name)
    try:
        resolved_path.relative_to(root.resolve())
    except ValueError:
        return _path_escape(relative_path)
    try:
        content = read_regular_file_bytes(resolved_path, max_bytes=MAX_SOURCE_BYTES)
    except IsADirectoryError:
        return _not_regular(relative_path)
    except OSError as exc:
        message = "source could not be read"
        return LoadFailure(error("read", "read", "bounded-read", message, path=relative_path), exc)
    if content is None:
        return _not_regular(relative_path)
    if len(content) > MAX_SOURCE_BYTES:
        return LoadFailure(
            LintError(
                "source-too-large",
                "budget",
                relative_path,
                None,
                None,
                None,
                None,
                "source-check",
                "bounded-read",
                None,
                "source file exceeds 10 MiB",
            )
        )
    return LoadedFile(absolute, relative_path, content)


def _path_escape(path: str) -> LoadFailure:
    message = "selected path escapes root"
    return LoadFailure(error("path", "source-check", "root-check", message, path=path))


def _not_regular(path: str) -> LoadFailure:
    message = "not a regular file"
    return LoadFailure(error("path", "source-check", "regular-file", message, path=path))


__all__ = ["MAX_SOURCE_BYTES", "LoadFailure", "LoadedFile", "load_file", "read_regular_file_bytes"]
