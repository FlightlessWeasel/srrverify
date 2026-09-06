"""Self-update: decide whether a newer release exists and hand off to the
privileged helper that installs it.

This module never touches the filesystem outside a cache: it queries the GitHub
Releases API, compares versions, and launches ``scripts/update.sh`` (installed
at ``<prefix>/bin/update.sh``) detached. That script does the download, the
atomic swap and the ``systemctl restart`` — and rolls itself back if the new
version fails its health check.
"""
import asyncio
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .config import (
    GITHUB_API,
    REPO_SLUG,
    UPDATE_CHECK_TTL,
    UPDATE_HTTP_TIMEOUT,
    UPDATE_SCRIPT,
)
from .version import __version__

logger = logging.getLogger("srrverify.updater")


@dataclass
class ReleaseInfo:
    current: str
    latest: Optional[str]
    update_available: bool
    can_apply: bool
    checked_at: float
    notes_url: Optional[str] = None
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "current": self.current,
            "latest": self.latest,
            "update_available": self.update_available,
            "can_apply": self.can_apply,
            "checked_at": self.checked_at,
            "notes_url": self.notes_url,
            "error": self.error,
        }


# Last successful (or errored) check, reused until UPDATE_CHECK_TTL elapses.
_cache: Optional[ReleaseInfo] = None
_lock = asyncio.Lock()


def can_apply() -> bool:
    # The helper only exists on a scripts/install.sh deployment; a dev checkout
    # gets a read-only version panel.
    return os.access(UPDATE_SCRIPT, os.X_OK)


def _version_key(tag: str) -> Optional[tuple[int, ...]]:
    """``(1, 4, 0)`` for ``v1.4.0``. ``None`` when the tag is not a plain dotted
    number — a pre-release suffix like ``0.0.0-dev`` is deliberately unorderable
    so any published release compares as newer than it."""
    core = tag.lstrip("vV")
    base = core.split("-", 1)[0].split("+", 1)[0]
    if not base or core != base:
        return None  # empty, or carries a pre-release / build suffix
    parts = []
    for chunk in base.split("."):
        if not chunk.isdigit():
            return None
        parts.append(int(chunk))
    # Pad so (1, 4) and (1, 4, 0) compare equal.
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _is_newer(latest: str, current: str) -> bool:
    lk, ck = _version_key(latest), _version_key(current)
    if lk is not None and ck is not None:
        return lk > ck
    # One side is unorderable (e.g. current is '0.0.0-dev', or a tag scheme we
    # don't parse). A parseable latest against an unparseable current is an
    # update; otherwise fall back to "different string means update".
    if lk is not None and ck is None:
        return True
    return latest.lstrip("vV") != current.lstrip("vV")


async def get_release_info(force: bool = False) -> ReleaseInfo:
    global _cache
    async with _lock:
        if (
            _cache is not None
            and not force
            and (time.time() - _cache.checked_at) < UPDATE_CHECK_TTL
        ):
            return _cache

        info = ReleaseInfo(
            current=__version__,
            latest=None,
            update_available=False,
            can_apply=can_apply(),
            checked_at=time.time(),
        )
        url = f"{GITHUB_API}/repos/{REPO_SLUG}/releases/latest"
        try:
            async with httpx.AsyncClient(timeout=UPDATE_HTTP_TIMEOUT) as client:
                resp = await client.get(
                    url, headers={"Accept": "application/vnd.github+json"}
                )
            if resp.status_code == 404:
                info.error = "No published releases yet"
            else:
                resp.raise_for_status()
                body = resp.json()
                tag = body.get("tag_name")
                info.latest = tag
                info.notes_url = body.get("html_url")
                info.update_available = bool(tag) and _is_newer(tag, __version__)
        except httpx.HTTPError as exc:
            # Keep the repr out of the client-facing snapshot (see CODING_STANDARDS).
            logger.warning("update check failed: %r", exc)
            info.error = "Update check failed; see server logs"

        _cache = info
        return info


def start_update() -> None:
    """Launch the helper (targeting the latest release) and return immediately.
    Raises if updates are disabled on this install."""
    global _cache
    if not can_apply():
        raise RuntimeError("In-app update is not available on this install")

    # Fixed argv — matches the pinned sudoers rule exactly, no version pin.
    cmd = ["sudo", "-n", str(UPDATE_SCRIPT), "--from-app"]

    # The helper re-launches itself into a transient systemd unit, so it
    # survives the restart of this service. Detach fully regardless.
    subprocess.Popen(  # noqa: S603 - fixed argv, shell=False
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _cache = None
