"""Directory browser used by the "add library" folder picker.

Exposes directory *names* only, never file contents. Linux-only: the single
root is `/`.
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from ..config import API_PREFIX

router = APIRouter(prefix=f"{API_PREFIX}/fs")


@router.get("/list")
def fs_list(path: Optional[str] = None) -> dict:
    if not path:
        return {"path": None, "parent": None, "entries": [{"name": "/", "path": "/"}]}

    p = Path(path)
    if not p.exists() or not p.is_dir():
        raise HTTPException(404, "Not a directory")

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
        raise HTTPException(403, "Permission denied")

    parent = None if p.parent == p else str(p.parent)
    return {"path": str(p), "parent": parent, "entries": entries}
