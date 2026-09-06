"""Optional single-user authentication with TOTP-based MFA.

Auth is OFF until `data/auth.json` exists with `"enabled": true` (written by
`python -m app.auth_setup`). When on, `/api/*` routes require a bearer token
issued by `/api/auth/login`, which checks password + 6-digit TOTP code.

Everything here is stdlib except nothing — TOTP (RFC 6238) and scrypt password
hashing are implemented on hashlib/hmac so there is no crypto dependency.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from typing import Optional

from .config import DATA_DIR

AUTH_FILE = DATA_DIR / "auth.json"
TOKEN_TTL = 12 * 3600
_TOTP_STEP = 30
_TOTP_DIGITS = 6

# Trust X-Forwarded-For for the login throttle only when explicitly told to
# (i.e. the app sits behind a reverse proxy we control).
_TRUST_PROXY = os.environ.get("GAMECRC_TRUST_PROXY", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

# In-process guards. Reset on restart, which is acceptable for a personal tool.
_used_totp: dict[str, int] = {}
_failures: dict[str, list[float]] = {}
_LOCK_AFTER = 5
_LOCK_WINDOW = 300.0

# Constant-work hash so a wrong username costs the same as a wrong password.
_dummy_hash: Optional[str] = None


# --------------------------------------------------------------------------- #
# Config file
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    try:
        return json.loads(AUTH_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    """Write auth.json readable only by the current user (0600 on create *and*
    for a pre-existing file)."""
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(AUTH_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.chmod(AUTH_FILE, 0o600)


def auth_enabled() -> bool:
    cfg = load_config()
    return bool(cfg.get("enabled") and cfg.get("password_hash") and cfg.get("totp_secret"))


# --------------------------------------------------------------------------- #
# Password hashing (scrypt)
# --------------------------------------------------------------------------- #
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.scrypt(
            pw.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(hash_hex) // 2,
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def _burn_password_time(pw: str) -> None:
    """Run a throwaway scrypt so an unknown username takes as long as a known
    one (no timing oracle for enumeration)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("dummy-password-for-constant-timing")
    verify_password(pw, _dummy_hash)


# --------------------------------------------------------------------------- #
# TOTP (RFC 6238)
# --------------------------------------------------------------------------- #
def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _totp_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**_TOTP_DIGITS)).zfill(_TOTP_DIGITS)


def verify_totp(secret: str, code: str, window: int = 1) -> Optional[int]:
    """Return the matched time-step counter, or None. Caller must reject replays."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != _TOTP_DIGITS or not code.isdigit():
        return None
    base = int(time.time()) // _TOTP_STEP
    for drift in range(-window, window + 1):
        counter = base + drift
        if hmac.compare_digest(_totp_at(secret, counter), code):
            return counter
    return None


def totp_uri(secret: str, username: str, issuer: str = "srrverify") -> str:
    from urllib.parse import quote

    label = quote(f"{issuer}:{username}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={_TOTP_DIGITS}&period={_TOTP_STEP}"
    )


# --------------------------------------------------------------------------- #
# Stateless session tokens (HMAC-signed)
# --------------------------------------------------------------------------- #
def _server_secret() -> bytes:
    cfg = load_config()
    sec = cfg.get("server_secret")
    if not sec:
        raise RuntimeError("auth config missing server_secret")
    return bytes.fromhex(sec)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(_server_secret(), body.encode(), hashlib.sha256).digest())


def make_token(username: str, ttl: int = TOKEN_TTL) -> str:
    body = _b64(
        json.dumps(
            {"u": username, "exp": int(time.time()) + ttl}, separators=(",", ":")
        ).encode()
    )
    return f"{body}.{_sign(body)}"


def verify_token(token: str) -> Optional[str]:
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(body)):
            return None
        payload = json.loads(_unb64(body))
        if int(payload["exp"]) < time.time():
            return None
        return str(payload["u"])
    except (ValueError, KeyError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Login throttle
# --------------------------------------------------------------------------- #
def locked_out(key: str) -> bool:
    now = time.time()
    hits = [t for t in _failures.get(key, []) if now - t < _LOCK_WINDOW]
    _failures[key] = hits
    return len(hits) >= _LOCK_AFTER


def record_failure(key: str) -> None:
    _failures.setdefault(key, []).append(time.time())


def clear_failures(*keys: str) -> None:
    for key in keys:
        _failures.pop(key, None)


def bearer_from_header(header: str) -> str:
    """Extract the token from an `Authorization: Bearer <token>` header value."""
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def client_key(request) -> str:
    """Throttle key for a login request: the peer address, or the first
    X-Forwarded-For hop when GAMECRC_TRUST_PROXY is set."""
    if _TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# --------------------------------------------------------------------------- #
# Login
# --------------------------------------------------------------------------- #
class AuthError(Exception):
    pass


def login(username: str, password: str, code: str, ip_key: str) -> str:
    # Two independent throttles: one per source address (stops a single host
    # hammering) and one per username (stops a distributed guess against the
    # one real account).
    user_key = f"user:{username}"
    if locked_out(ip_key) or locked_out(user_key):
        raise AuthError("Too many attempts. Wait a few minutes and try again.")

    cfg = load_config()
    ok_user = hmac.compare_digest(username or "", cfg.get("username", ""))
    if ok_user:
        ok_pw = verify_password(password, cfg.get("password_hash", ""))
    else:
        _burn_password_time(password)
        ok_pw = False
    counter = verify_totp(cfg.get("totp_secret", ""), code) if ok_pw else None

    if counter is None or not ok_pw:
        record_failure(ip_key)
        record_failure(user_key)
        raise AuthError("Invalid credentials or code.")

    if _used_totp.get(username, -1) >= counter:
        record_failure(ip_key)
        record_failure(user_key)
        raise AuthError("That code was already used. Wait for the next one.")
    _used_totp[username] = counter

    clear_failures(ip_key, user_key)
    return make_token(username)
