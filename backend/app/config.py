import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GAMECRC_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "gamecrc.db"

# Frontend build output, served by FastAPI when present.
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

# srrdb is polite about request rate; keep lookups serialized-ish.
SRRDB_TIMEOUT = 60.0
