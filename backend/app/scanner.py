import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from .crc import ScanCancelled, crc32_file
from .db import get_conn, utcnow
from .srrdb import fetch_release, parse_expected_crc

STATUS_MATCH = "MATCH"
STATUS_MISMATCH = "MISMATCH"
STATUS_NOT_FOUND = "NOT_FOUND"
STATUS_ERROR = "ERROR"


@dataclass
class ScanState:
    library_id: int
    library_path: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    phase: str = "starting"
    files_total: int = 0
    files_done: int = 0
    bytes_total: int = 0
    bytes_done: int = 0
    current_file: Optional[str] = None
    message: Optional[str] = None
    counts: dict = field(default_factory=lambda: {
        STATUS_MATCH: 0, STATUS_MISMATCH: 0, STATUS_NOT_FOUND: 0, STATUS_ERROR: 0
    })

    def as_dict(self) -> dict:
        return {
            "library_id": self.library_id,
            "phase": self.phase,
            "files_total": self.files_total,
            "files_done": self.files_done,
            "bytes_total": self.bytes_total,
            "bytes_done": self.bytes_done,
            "current_file": self.current_file,
            "message": self.message,
            "counts": self.counts,
        }


class ScanManager:
    def __init__(self) -> None:
        self.state: Optional[ScanState] = None
        self._task: Optional[asyncio.Task] = None
        self._cancel = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def snapshot(self) -> Optional[dict]:
        return self.state.as_dict() if self.state else None

    def cancel(self) -> None:
        self._cancel = True

    async def start(self, library_id: int, library_path: str, force: bool) -> None:
        if self.running:
            raise RuntimeError("A scan is already running")
        self._cancel = False
        self.state = ScanState(library_id=library_id, library_path=library_path)
        self._task = asyncio.create_task(self._run(library_id, library_path, force))

    # ------------------------------------------------------------------

    async def _run(self, library_id: int, library_path: str, force: bool) -> None:
        st = self.state
        assert st is not None
        conn = get_conn()
        try:
            st.phase = "discovering"
            discovered = _discover(library_path)
            st.files_total = len(discovered)
            st.bytes_total = sum(d["size"] for d in discovered)

            if not discovered:
                st.phase = "done"
                st.message = "No files found in library."
                _record_scan(conn, library_id, "done", st, "No files found")
                return

            existing = {
                row["path"]: row
                for row in conn.execute(
                    "SELECT path, size, mtime_ns, crc32 FROM files WHERE library_id=?",
                    (library_id,),
                )
            }

            release_names = sorted({d["release"] for d in discovered})
            if force:
                conn.execute("DELETE FROM files WHERE library_id=?", (library_id,))
                conn.executemany(
                    "DELETE FROM releases WHERE name=?", [(n,) for n in release_names]
                )
                conn.commit()
                existing = {}

            st.phase = "releases"
            release_data = await self._load_releases(conn, release_names, force)

            st.phase = "hashing"
            loop = asyncio.get_running_loop()
            seen_paths: list[str] = []

            for item in discovered:
                if self._cancel:
                    raise ScanCancelled()

                st.current_file = item["rel_path"]
                seen_paths.append(item["path"])
                prev = existing.get(item["path"])
                base = st.bytes_done

                reuse = (
                    prev
                    and prev["crc32"]
                    and prev["size"] == item["size"]
                    and prev["mtime_ns"] == item["mtime_ns"]
                )
                crc: Optional[str]
                if reuse:
                    crc = prev["crc32"]
                    st.bytes_done = base + item["size"]
                else:
                    def progress(read: int, _total: int, _base=base) -> None:
                        st.bytes_done = _base + read

                    try:
                        crc = await loop.run_in_executor(
                            None,
                            crc32_file,
                            item["path"],
                            progress,
                            lambda: self._cancel,
                        )
                    except ScanCancelled:
                        raise
                    except OSError as exc:
                        crc = None
                        st.message = f"Could not read {item['name']}: {exc}"

                expected, status = _evaluate(crc, item, release_data.get(item["release"]))
                st.counts[status] = st.counts.get(status, 0) + 1
                _upsert_file(conn, library_id, item, crc, expected, status)
                st.files_done += 1
                if st.files_done % 25 == 0:
                    conn.commit()

            # Drop rows for files that no longer exist.
            placeholders = ",".join("?" * len(seen_paths)) or "''"
            conn.execute(
                f"DELETE FROM files WHERE library_id=? AND path NOT IN ({placeholders})",
                [library_id, *seen_paths],
            )
            conn.commit()

            st.current_file = None
            st.phase = "done"
            st.message = "Scan complete."
            _record_scan(conn, library_id, "done", st, None)

        except ScanCancelled:
            conn.commit()
            st.phase = "cancelled"
            st.message = "Scan cancelled."
            _record_scan(conn, library_id, "cancelled", st, "Cancelled by user")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            conn.commit()
            st.phase = "error"
            st.message = f"{type(exc).__name__}: {exc}"
            _record_scan(conn, library_id, "error", st, st.message)

    async def _load_releases(
        self, conn, names: list[str], force: bool
    ) -> dict[str, dict]:
        out: dict[str, dict] = {}
        pending: list[str] = []
        for name in names:
            row = conn.execute(
                "SELECT name, found, response_json FROM releases WHERE name=?", (name,)
            ).fetchone()
            if row and not force:
                out[name] = {
                    "found": bool(row["found"]),
                    "data": json.loads(row["response_json"]) if row["response_json"] else {},
                }
            else:
                pending.append(name)

        if pending:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                for i, name in enumerate(pending):
                    if self._cancel:
                        raise ScanCancelled()
                    self.state.message = f"Looking up release {i + 1}/{len(pending)}: {name}"
                    result = await fetch_release(client, name)
                    out[name] = {"found": result["found"], "data": result["data"]}
                    conn.execute(
                        "INSERT INTO releases(name, resolved_name, found, response_json, fetched_at) "
                        "VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                        "resolved_name=excluded.resolved_name, found=excluded.found, "
                        "response_json=excluded.response_json, fetched_at=excluded.fetched_at",
                        (
                            name,
                            result["resolved_name"],
                            1 if result["found"] else 0,
                            json.dumps(result["data"]),
                            utcnow(),
                        ),
                    )
                    conn.commit()
        self.state.message = None
        return out


def _discover(root: str) -> list[dict]:
    items: list[dict] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            items.append(
                {
                    "path": full,
                    "rel_path": os.path.relpath(full, root),
                    "name": fname,
                    "release": os.path.basename(dirpath),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
    items.sort(key=lambda d: d["rel_path"].lower())
    return items


def _evaluate(crc: Optional[str], item: dict, release: Optional[dict]) -> tuple[Optional[str], str]:
    if crc is None:
        return None, STATUS_ERROR
    if not release or not release.get("found"):
        return None, STATUS_NOT_FOUND
    expected = parse_expected_crc(release["data"], item["name"], item["size"])
    if expected is None:
        return None, STATUS_NOT_FOUND
    return expected, STATUS_MATCH if expected.lower() == crc.lower() else STATUS_MISMATCH


def _upsert_file(conn, library_id, item, crc, expected, status) -> None:
    conn.execute(
        "INSERT INTO files(library_id, path, rel_path, name, release, size, mtime_ns, "
        "crc32, expected_crc, status, scanned_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(library_id, path) DO UPDATE SET "
        "rel_path=excluded.rel_path, name=excluded.name, release=excluded.release, "
        "size=excluded.size, mtime_ns=excluded.mtime_ns, crc32=excluded.crc32, "
        "expected_crc=excluded.expected_crc, status=excluded.status, scanned_at=excluded.scanned_at",
        (
            library_id,
            item["path"],
            item["rel_path"],
            item["name"],
            item["release"],
            item["size"],
            item["mtime_ns"],
            crc,
            expected,
            status,
            utcnow(),
        ),
    )


def _record_scan(conn, library_id, state, st: ScanState, message) -> None:
    conn.execute(
        "INSERT INTO scans(library_id, state, started_at, finished_at, files_total, files_done, message) "
        "VALUES(?,?,?,?,?,?,?)",
        (library_id, state, st.started_at, utcnow(), st.files_total, st.files_done, message),
    )
    conn.commit()


manager = ScanManager()
