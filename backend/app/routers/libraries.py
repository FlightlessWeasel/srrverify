"""Library CRUD and the scan-results query."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import API_PREFIX
from ..db import get_conn, utcnow
from ..status import Status

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


@dataclass
class _ReleaseAgg:
    """Per-folder tally built up from the grouped file rows."""

    release: str
    resolved_name: Optional[str]
    found: bool
    counts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        total = sum(self.counts.values())
        matched = self.counts.get(Status.MATCH.value, 0)
        return {
            "release": self.release,
            "resolved_name": self.resolved_name,
            "found": self.found,
            "total": total,
            "counts": self.counts,
            "match_pct": round(matched / total * 100) if total else 0,
        }


@router.get("/libraries/{library_id}/releases")
def library_releases(library_id: int) -> list[dict]:
    """One row per game folder, with its per-status counts and srrdb link.

    `release` is the on-disk folder name; `resolved_name` is what srrdb.com
    actually calls the release (may differ) and drives the external link.
    """
    conn = get_conn()
    # resolved_name / found are the same for every row of a folder (the join key
    # is the folder name), so MAX() just carries that one value per group.
    rows = conn.execute(
        "SELECT f.release, f.status, COUNT(*) count, "
        "       MAX(r.resolved_name) resolved_name, MAX(r.found) found "
        "FROM files f LEFT JOIN releases r ON r.name = f.release "
        "WHERE f.library_id=? "
        "GROUP BY f.release, f.status",
        (library_id,),
    ).fetchall()

    aggs: dict[str, _ReleaseAgg] = {}
    for r in rows:
        agg = aggs.get(r["release"])
        if agg is None:
            agg = _ReleaseAgg(
                release=r["release"],
                resolved_name=r["resolved_name"],
                found=bool(r["found"]),
            )
            aggs[r["release"]] = agg
        agg.counts[r["status"]] = r["count"]

    return [
        a.as_dict()
        for a in sorted(aggs.values(), key=lambda a: a.release.lower())
    ]


@router.get("/libraries/{library_id}/files")
def library_files(
    library_id: int,
    status: Optional[str] = None,
    search: Optional[str] = None,
    release: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    conn = get_conn()
    where = ["library_id=?"]
    params: list = [library_id]
    if status and status != "ALL":
        where.append("status=?")
        params.append(status)
    if release:
        where.append("release=?")
        params.append(release)
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
