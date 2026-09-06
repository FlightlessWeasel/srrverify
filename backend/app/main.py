import os
import string
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth
from .config import FRONTEND_DIST
from .db import get_conn, init_db, utcnow
from .scanner import manager

app = FastAPI(title="Game CRC Checker")

# Idempotent; also re-run on startup so a fresh worker thread is covered.
init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Requests that must work before/without a session.
_AUTH_EXEMPT = {"/api/health", "/api/auth/status", "/api/auth/login"}


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    path = request.url.path
    needs_auth = (
        path.startswith("/api/")
        and path not in _AUTH_EXEMPT
        and request.method != "OPTIONS"
        and auth.auth_enabled()
    )
    if needs_auth:
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if not token or auth.verify_token(token) is None:
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class LibraryIn(BaseModel):
    path: str


class ScanIn(BaseModel):
    force: bool = False


class LoginIn(BaseModel):
    username: str
    password: str
    code: str


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict:
    if not auth.auth_enabled():
        return {"enabled": False, "authenticated": True}
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    user = auth.verify_token(token) if token else None
    return {"enabled": True, "authenticated": user is not None, "username": user}


@app.post("/api/auth/login")
def auth_login(body: LoginIn, request: Request) -> dict:
    if not auth.auth_enabled():
        raise HTTPException(400, "Authentication is not configured")
    client = request.client.host if request.client else "unknown"
    try:
        token = auth.login(body.username, body.password, body.code, client)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc))
    return {"token": token, "username": body.username}


# --------------------------------------------------------------------------- #
# Filesystem browser
# --------------------------------------------------------------------------- #
def _list_drives() -> list[dict]:
    if os.name == "nt":
        drives = []
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                drives.append({"name": root, "path": root})
        return drives
    return [{"name": "/", "path": "/"}]


@app.get("/api/fs/list")
def fs_list(path: Optional[str] = None) -> dict:
    if not path:
        return {"path": None, "parent": None, "entries": _list_drives()}

    p = Path(path)
    if not p.exists() or not p.is_dir():
        raise HTTPException(404, f"Not a directory: {path}")

    p = p.resolve()
    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            try:
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, f"Permission denied: {path}")

    parent = None if p.parent == p else str(p.parent)
    return {"path": str(p), "parent": parent, "entries": entries}


# --------------------------------------------------------------------------- #
# Libraries
# --------------------------------------------------------------------------- #
def _summary(library_id: int) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT status, COUNT(*) c, COALESCE(SUM(size), 0) b FROM files "
        "WHERE library_id=? GROUP BY status",
        (library_id,),
    ).fetchall()
    counts = {r["status"]: r["c"] for r in rows}
    total = sum(counts.values())
    last = conn.execute(
        "SELECT finished_at, state FROM scans WHERE library_id=? "
        "ORDER BY id DESC LIMIT 1",
        (library_id,),
    ).fetchone()
    return {
        "total": total,
        "counts": counts,
        "last_scan": dict(last) if last else None,
    }


@app.get("/api/libraries")
def list_libraries() -> list[dict]:
    conn = get_conn()
    out = []
    for row in conn.execute("SELECT * FROM libraries ORDER BY name"):
        lib = dict(row)
        lib["summary"] = _summary(lib["id"])
        lib["exists"] = os.path.isdir(lib["path"])
        out.append(lib)
    return out


@app.post("/api/libraries", status_code=201)
def create_library(body: LibraryIn) -> dict:
    p = Path(body.path)
    if not p.exists() or not p.is_dir():
        raise HTTPException(400, f"Not a directory: {body.path}")
    resolved = str(p.resolve())
    conn = get_conn()
    existing = conn.execute(
        "SELECT * FROM libraries WHERE path=?", (resolved,)
    ).fetchone()
    if existing:
        return dict(existing)
    cur = conn.execute(
        "INSERT INTO libraries(path, name, created_at) VALUES(?,?,?)",
        (resolved, p.name or resolved, utcnow()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM libraries WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


@app.delete("/api/libraries/{library_id}", status_code=204)
def delete_library(library_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM files WHERE library_id=?", (library_id,))
    conn.execute("DELETE FROM libraries WHERE id=?", (library_id,))
    conn.commit()


@app.get("/api/libraries/{library_id}/summary")
def library_summary(library_id: int) -> dict:
    return _summary(library_id)


@app.get("/api/libraries/{library_id}/files")
def library_files(
    library_id: int,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    conn = get_conn()
    where = ["library_id=?"]
    params: list = [library_id]
    if status and status != "ALL":
        where.append("status=?")
        params.append(status)
    if search:
        where.append("(rel_path LIKE ? OR release LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    clause = " AND ".join(where)

    total = conn.execute(
        f"SELECT COUNT(*) c FROM files WHERE {clause}", params
    ).fetchone()["c"]
    rows = conn.execute(
        f"SELECT id, rel_path, name, release, size, crc32, expected_crc, status, scanned_at "
        f"FROM files WHERE {clause} ORDER BY "
        f"CASE status WHEN 'MISMATCH' THEN 0 WHEN 'ERROR' THEN 1 "
        f"WHEN 'NOT_FOUND' THEN 2 ELSE 3 END, rel_path "
        f"LIMIT ? OFFSET ?",
        [*params, max(1, min(limit, 1000)), max(0, offset)],
    ).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #
@app.post("/api/libraries/{library_id}/scan")
async def start_scan(library_id: int, body: ScanIn) -> dict:
    conn = get_conn()
    lib = conn.execute(
        "SELECT * FROM libraries WHERE id=?", (library_id,)
    ).fetchone()
    if not lib:
        raise HTTPException(404, "Library not found")
    if not os.path.isdir(lib["path"]):
        raise HTTPException(400, f"Library folder is missing: {lib['path']}")
    if manager.running:
        raise HTTPException(409, "A scan is already running")
    await manager.start(library_id, lib["path"], body.force)
    return {"started": True, "library_id": library_id}


@app.get("/api/scan/current")
def scan_current() -> dict:
    return {"running": manager.running, "state": manager.snapshot()}


@app.post("/api/scan/cancel")
def scan_cancel() -> dict:
    manager.cancel()
    return {"cancelling": True}


# --------------------------------------------------------------------------- #
# Frontend (served when built)
# --------------------------------------------------------------------------- #
if FRONTEND_DIST.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_DIST / "assets"),
        name="assets",
    )

    @app.get("/")
    def _index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{full_path:path}")
    def _spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(404, "Not found")
        candidate = FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
