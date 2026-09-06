"""Auth endpoints and the always-open liveness check."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import auth
from ..config import API_PREFIX

router = APIRouter(prefix=API_PREFIX)


class LoginIn(BaseModel):
    username: str
    password: str
    code: str


@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.get("/auth/status")
def auth_status(request: Request) -> dict:
    if not auth.auth_enabled():
        return {"enabled": False, "authenticated": True}
    token = auth.bearer_from_header(request.headers.get("authorization", ""))
    user = auth.verify_token(token) if token else None
    return {"enabled": True, "authenticated": user is not None, "username": user}


@router.post("/auth/login")
def auth_login(body: LoginIn, request: Request) -> dict:
    if not auth.auth_enabled():
        raise HTTPException(400, "Authentication is not configured")
    try:
        token = auth.login(
            body.username, body.password, body.code, auth.client_key(request)
        )
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc))
    return {"token": token, "username": body.username}
