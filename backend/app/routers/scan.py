"""Start / observe / cancel the single running scan."""
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import API_PREFIX
from ..db import get_conn
from ..scanner import manager

router = APIRouter(prefix=API_PREFIX)


class ScanIn(BaseModel):
    force: bool = False


@router.post("/libraries/{library_id}/scan")
async def start_scan(library_id: int, body: ScanIn) -> dict:
    conn = get_conn()
    lib = conn.execute(
        "SELECT * FROM libraries WHERE id=?", (library_id,)
    ).fetchone()
    if not lib:
        raise HTTPException(404, "Library not found")
    if not os.path.isdir(lib["path"]):
        raise HTTPException(400, "Library folder is missing")
    try:
        await manager.start(library_id, lib["path"], body.force)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"started": True, "library_id": library_id}


@router.get("/scan/current")
def scan_current() -> dict:
    return {"running": manager.running, "state": manager.snapshot()}


@router.post("/scan/cancel")
def scan_cancel() -> dict:
    manager.cancel()
    return {"cancelling": True}
