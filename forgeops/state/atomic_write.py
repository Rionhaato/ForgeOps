"""Atomic text-file replacement, shared by every ForgeOps state writer
(currently `checkpoint` and `handoff`). Writes to a temporary sibling
file in the same directory (so the final rename is same-filesystem and
therefore atomic on both POSIX and Windows), flushes and fsyncs it, then
replaces the destination with `os.replace` - a single filesystem
operation that never leaves a partially-written destination visible,
even if the process is killed mid-write. On any failure while writing
the temporary file, that temporary file is removed and the destination
is left completely untouched."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically replace `path` with `content`. `path`'s parent directory
    is created if missing. Raises OSError on failure (disk full,
    permission denied, ...); the destination file is left unchanged in
    that case - only a same-directory temporary file may be left behind
    if the failure happens after creation but before cleanup, and even
    that is removed in the common failure paths handled here."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
