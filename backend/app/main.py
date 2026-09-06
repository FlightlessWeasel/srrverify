from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .config import API_PREFIX, FRONTEND_DIST
from .db import init_db
from .routers import auth as auth_router
from .routers import fs as fs_router
from .routers import libraries as libraries_router
from .routers import scan as scan_router
from .routers import update as update_router

app = FastAPI(title="srrverify")

# Idempotent; also re-run on startup so a fresh worker thread is covered.
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Requests that must work before/without a session.
_AUTH_EXEMPT = {
    f"{API_PREFIX}/health",
    f"{API_PREFIX}/auth/status",
    f"{API_PREFIX}/auth/login",
}


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    path = request.url.path
    needs_auth = (
        path.startswith(f"{API_PREFIX}/")
        and path not in _AUTH_EXEMPT
        and request.method != "OPTIONS"
        and auth.auth_enabled()
    )
    if needs_auth:
        token = auth.bearer_from_header(request.headers.get("authorization", ""))
        if not token or auth.verify_token(token) is None:
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)


@app.on_event("startup")
def _startup() -> None:
    init_db()


app.include_router(auth_router.router)
app.include_router(fs_router.router)
app.include_router(libraries_router.router)
app.include_router(scan_router.router)
app.include_router(update_router.router)


# --------------------------------------------------------------------------- #
# Frontend (served when built)
# --------------------------------------------------------------------------- #
if FRONTEND_DIST.is_dir():
    _DIST = FRONTEND_DIST.resolve()
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/")
    def _index() -> FileResponse:
        return FileResponse(_DIST / "index.html")

    @app.get("/{full_path:path}")
    def _spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(404, "Not found")
        candidate = (_DIST / full_path).resolve()
        # Never serve anything outside the build directory: a request like
        # /..%2f..%2fbackend/data/auth.json must fall through to index.html.
        if candidate.is_file() and candidate.is_relative_to(_DIST):
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
