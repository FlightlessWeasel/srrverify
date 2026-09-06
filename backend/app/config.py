import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GAMECRC_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "gamecrc.db"

# Frontend build output, served by FastAPI when present.
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

# All HTTP API routes live under this prefix; everything else is the SPA.
API_PREFIX = "/api"

# srrdb is polite about request rate; keep lookups serialized-ish.
SRRDB_TIMEOUT = 60.0

# --------------------------------------------------------------------------- #
# Self-update
# --------------------------------------------------------------------------- #
# GitHub repo the update check queries for the latest release.
REPO_SLUG = os.environ.get("GAMECRC_REPO", "FlightlessWeasel/srrverify")
GITHUB_API = "https://api.github.com"

# Install prefix used by scripts/install.sh (the systemd layout). The running
# service also gets this as GAMECRC_PREFIX from the unit file.
INSTALL_PREFIX = Path(os.environ.get("GAMECRC_PREFIX", "/opt/srrverify"))

# Privileged helper that performs "download new release, swap, restart". It is
# installed outside the versioned tree at <prefix>/bin/update.sh so its path is
# stable for the sudoers rule. A dev checkout has no such file, which is what
# disables the in-UI "Update" button.
UPDATE_SCRIPT = Path(
    os.environ.get("GAMECRC_UPDATE_SCRIPT", INSTALL_PREFIX / "bin" / "update.sh")
)

# How long a GitHub "latest release" lookup is cached before the next check.
UPDATE_CHECK_TTL = float(os.environ.get("GAMECRC_UPDATE_TTL", "3600"))

# Timeout for the GitHub Releases request itself.
UPDATE_HTTP_TIMEOUT = 15.0
