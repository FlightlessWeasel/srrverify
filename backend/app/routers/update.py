"""Version info and the in-UI self-update trigger."""
from fastapi import APIRouter, HTTPException

from .. import updater
from ..config import API_PREFIX

router = APIRouter(prefix=API_PREFIX)


@router.get("/update/status")
async def update_status(refresh: bool = False) -> dict:
    info = await updater.get_release_info(force=refresh)
    return info.as_dict()


@router.post("/update/apply")
async def update_apply() -> dict:
    info = await updater.get_release_info()
    if not info.latest:
        raise HTTPException(409, "No release is available to update to")
    if not info.update_available:
        raise HTTPException(409, "Already on the latest release")
    try:
        updater.start_update()
    except RuntimeError as exc:
        raise HTTPException(403, str(exc))
    return {"started": True, "target": info.latest}
