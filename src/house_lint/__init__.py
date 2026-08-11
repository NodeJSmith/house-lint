"""Public package boundary for house-lint."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("house-lint")
except PackageNotFoundError:
    # Source checkouts have no installed distribution metadata.
    __version__ = "0.1.1"
