"""Thin client + parser for the srrdb.com API.

Ported from crc32-iso.sh: look up a scene release by name, then find the
expected CRC for a given file inside it (by basename, with a size-based
fallback for single-ISO archives).
"""
import re
from typing import Any, Optional
from urllib.parse import quote

import httpx

from .config import SRRDB_TIMEOUT

BASE = "https://api.srrdb.com/v1"
_HEX8 = re.compile(r"[0-9a-fA-F]{8}")


def _basename(name: str) -> str:
    return re.split(r"[\\/]", name)[-1]


def parse_expected_crc(
    data: dict[str, Any], target_name: str, local_size: Optional[int]
) -> Optional[str]:
    target = _basename(target_name).casefold()

    for key in ("files", "archived-files"):
        for entry in data.get(key) or []:
            name = entry.get("name")
            if isinstance(name, str) and _basename(name).casefold() == target:
                crc = entry.get("crc")
                if isinstance(crc, str) and _HEX8.fullmatch(crc):
                    return crc.lower()

    # Single unambiguous size match among archived .iso files.
    if local_size is not None and target.endswith(".iso"):
        matches = [
            entry
            for entry in (data.get("archived-files") or [])
            if isinstance(entry.get("name"), str)
            and entry["name"].casefold().endswith(".iso")
            and isinstance(entry.get("size"), (int, float))
            and entry["size"] == local_size
        ]
        if len(matches) == 1:
            crc = matches[0].get("crc")
            if isinstance(crc, str) and _HEX8.fullmatch(crc):
                return crc.lower()

    return None


def _has_file_data(data: dict[str, Any]) -> bool:
    return bool((data.get("files") or []) or (data.get("archived-files") or []))


async def _details(client: httpx.AsyncClient, release: str) -> Optional[dict[str, Any]]:
    url = f"{BASE}/details/{quote(release, safe='')}"
    resp = await client.get(url, timeout=SRRDB_TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def _search_exact(client: httpx.AsyncClient, query: str) -> Optional[str]:
    url = f"{BASE}/search/{quote(query, safe='')}"
    resp = await client.get(url, timeout=SRRDB_TIMEOUT)
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        return None
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        return None
    for res in results:
        name = res.get("release")
        if isinstance(name, str) and name.casefold() == query.casefold():
            return name
    if len(results) == 1 and isinstance(results[0].get("release"), str):
        return results[0]["release"]
    return None


async def fetch_release(client: httpx.AsyncClient, release: str) -> dict[str, Any]:
    """Return {found, resolved_name, data}. Never raises for 'not found'."""
    try:
        data = await _details(client, release)
        resolved = release
        if data is None or not _has_file_data(data):
            alt = await _search_exact(client, release)
            if alt and alt.casefold() != release.casefold():
                alt_data = await _details(client, alt)
                if alt_data is not None and _has_file_data(alt_data):
                    data, resolved = alt_data, alt
        found = data is not None and _has_file_data(data)
        return {"found": found, "resolved_name": resolved if found else None, "data": data or {}}
    except httpx.HTTPError as exc:
        return {"found": False, "resolved_name": None, "data": {}, "error": str(exc)}
