"""Record meetings and transcribe them with speaker diarization."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mento")
except PackageNotFoundError:  # running from a source tree
    __version__ = "0+unknown"
