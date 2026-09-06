"""Domain vocabulary: file statuses and scan phases.

Single source of truth for the backend. The frontend mirrors the same strings
in `src/api.ts`; `crc32-iso.sh` carries its own copy and is frozen. Changing a
value here means changing it in both those places too.
"""
from enum import Enum


class Status(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"
    PENDING = "PENDING"


class Phase(str, Enum):
    STARTING = "starting"
    DISCOVERING = "discovering"
    RELEASES = "releases"
    HASHING = "hashing"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


PHASE_LABELS: dict[Phase, str] = {
    Phase.STARTING: "Starting",
    Phase.DISCOVERING: "Discovering files",
    Phase.RELEASES: "Looking up releases on srrdb",
    Phase.HASHING: "Computing CRC32",
    Phase.DONE: "Scan complete",
    Phase.CANCELLED: "Scan cancelled",
    Phase.ERROR: "Scan failed",
}

# Status counters a scan reports live (PENDING is a stored default, never counted).
COUNTED_STATUSES: tuple[Status, ...] = (
    Status.MATCH,
    Status.MISMATCH,
    Status.NOT_FOUND,
    Status.ERROR,
)
