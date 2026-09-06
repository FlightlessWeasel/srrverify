"""Running version string, resolved once at import.

CI writes the real version into the repo-root ``VERSION`` file when it builds a
release tarball (see ``.github/workflows/release.yml``). A plain source checkout
keeps the committed ``0.0.0-dev``.
"""
from pathlib import Path

# version.py -> app -> backend -> repo root
_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"

try:
    __version__ = _VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0-dev"
except OSError:
    __version__ = "0.0.0-dev"
