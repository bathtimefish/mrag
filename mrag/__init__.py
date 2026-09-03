from importlib.metadata import PackageNotFoundError, version as _pkg_version

# Fallback used when distribution metadata is unavailable — e.g. a PyInstaller
# frozen build without --copy-metadata. Keep in sync with pyproject.toml.
_FALLBACK_VERSION = "1.0.1"

try:
    __version__ = _pkg_version("mrag")
except PackageNotFoundError:
    __version__ = _FALLBACK_VERSION
