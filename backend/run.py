"""Entry point: `python run.py` (from the backend/ directory).

Listens on 0.0.0.0:8000 by default. Override with GAMECRC_HOST / GAMECRC_PORT.
When authentication is configured (see `python -m app.auth_setup`) every
/api route except login/status/health requires a bearer token.
"""
import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("GAMECRC_HOST", "0.0.0.0")
    port = int(os.environ.get("GAMECRC_PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
