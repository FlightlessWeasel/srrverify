"""Library CRUD and the scan-results query."""
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import API_PREFIX
from ..db import get_conn, utcnow

router = APIRouter(prefix=API_PREFIX)


class LibraryIn(BaseModel):
    path: str


def summary(library_id: int) -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT status, COUNT(*) count FROM files WHERE library_id=? GROUP BY status",
        (library_id,),
    ).fetchall()
    counts = {r["status"]: r["count"] for r in rows}
    last = conn.execute(
        "SELECT finished_at, state FROM scans WHERE library_id=? ORDER BY id DESC LIMIT 1",
        (library_id,),
    ).fetchone()
    return {
        "total": sum(counts.values()),
        "counts": counts,
        "last_scan": dict(last) if last else None,
    }


@router.get("/libraries")
def list_libraries() -> list[dict]:
    conn = get_conn()
    out = []
    for row in conn.execute("SELECT * FROM libraries ORDER BY name"):
        lib = dict(row)
        lib["summary"] = summary(lib["id"])
        lib["exists"] = os.path.isdir(lib["path"])
        out.append(lib)
    return out


@router.post("/libraries", status_code=201)
def create_library(body: LibraryIn) -> dict:
    p = Path(body.path)
    if not p.exists() or not p.is_dir():
        raise HTTPException(400, "Not a directory")
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
    row = conn.execute(
        "SELECT * FROM libraries WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    return dict(row)


@router.delete("/libraries/{library_id}", status_code=204)
def delete_library(library_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM files WHERE library_id=?", (library_id,))
    conn.execute("DELETE FROM libraries WHERE id=?", (library_id,))
    conn.commit()


@router.get("/libraries/{library_id}/summary")
def library_summary(library_id: int) -> dict:
    return summary(library_id)


@router.get("/libraries/{library_id}/files")
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
        f"SELECT COUNT(*) count FROM files WHERE {clause}", params
    ).fetchone()["count"]
    rows = conn.execute(
        f"SELECT id, rel_path, name, release, size, crc32, expected_crc, status, scanned_at "
        f"FROM files WHERE {clause} ORDER BY "
        f"CASE status WHEN 'MISMATCH' THEN 0 WHEN 'ERROR' THEN 1 "
        f"WHEN 'NOT_FOUND' THEN 2 ELSE 3 END, rel_path "
        f"LIMIT ? OFFSET ?",
        [*params, max(1, min(limit, 1000)), max(0, offset)],
    ).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}
