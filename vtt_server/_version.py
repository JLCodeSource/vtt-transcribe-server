"""The installed version of this package (the core transcription package has its own)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vtt-transcribe-server")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
