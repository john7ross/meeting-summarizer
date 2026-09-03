"""Small, dependency-free helpers for durable JSON state on Windows.

The desktop can be opened twice and antivirus/indexing software may briefly hold
new files.  A fixed ``*.tmp`` name plus an unguarded read-modify-write loses data
or raises ``WinError 5`` in those cases.  These helpers provide:

* an in-process lock plus an OS-level one-byte lock for other app instances;
* a unique temporary file in the destination directory;
* flush + fsync before atomic replacement;
* a short retry window for transient Windows file locks.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.RLock] = {}


def _local_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def file_lock(path: Path, timeout: float = 8.0):
    """Serialize a complete read-modify-write transaction for ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    local = _local_lock(lock_path)
    with local:
        handle = lock_path.open("a+b")
        try:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out locking {path}")
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def atomic_write_json(path: Path, value, *, lock: bool = True) -> None:
    """Write JSON durably and replace the destination without a partial file."""
    path = Path(path)

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=path.parent,
                    prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
                tmp_name = handle.name
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            last_error = None
            for attempt in range(8):
                try:
                    os.replace(tmp_name, path)
                    tmp_name = None
                    return
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(0.04 * (attempt + 1))
            raise last_error  # type: ignore[misc]
        finally:
            if tmp_name:
                try:
                    Path(tmp_name).unlink(missing_ok=True)
                except OSError:
                    pass

    if lock:
        with file_lock(path):
            _write()
    else:
        _write()
