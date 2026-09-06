import os
import zlib
from typing import Callable, Optional

CHUNK = 1024 * 1024


class ScanCancelled(Exception):
    pass


def crc32_file(
    path: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> str:
    """Stream a file through zlib.crc32 and return the 8-char lowercase hex digest."""
    size = os.path.getsize(path)
    crc = 0
    read = 0
    with open(path, "rb") as fh:
        while True:
            if should_cancel and should_cancel():
                raise ScanCancelled()
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            read += len(chunk)
            if on_progress:
                on_progress(read, size)
    return f"{crc & 0xFFFFFFFF:08x}"
