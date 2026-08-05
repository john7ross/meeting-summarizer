"""Safe RAG catalog selection shared by desktop, server and MCP.

A shared catalog is a capability: clients that know the same high-entropy key
resolve to the same directory.  The key itself is never used as a path segment;
only its SHA-256 digest is, preventing traversal and accidental disclosure in
logs or directory listings.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP_RAG_DIR = ROOT / "rag_knowledge_base"
SERVER_RAG_ROOT = ROOT / "rag_data"
SHARED_RAG_ROOT = ROOT / "rag_shared"

MODE_ISOLATED = "isolated"
MODE_SHARED = "shared"
VALID_MODES = (MODE_ISOLATED, MODE_SHARED)
_KEY_RE = re.compile(r"^rsc_[A-Za-z0-9_-]{40,128}$")


class CatalogConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Catalog:
    catalog_id: str
    kind: str
    path: Path


def generate_shared_key() -> str:
    """Return a 256-bit URL-safe capability key."""
    return "rsc_" + secrets.token_urlsafe(32)


def validate_shared_key(value: str) -> str:
    key = str(value or "").strip()
    if not _KEY_RE.fullmatch(key):
        raise CatalogConfigError(
            "Shared RAG catalog key must start with 'rsc_' and contain "
            "a generated 256-bit URL-safe value")
    return key


def shared_catalog_id(key: str) -> str:
    key = validate_shared_key(key)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def shared_catalog_dir(key: str, *, create: bool = True) -> Path:
    path = SHARED_RAG_ROOT / shared_catalog_id(key)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _mode(settings: dict | None) -> str:
    mode = str((settings or {}).get("ragCatalogMode", MODE_ISOLATED)).strip().lower()
    if mode not in VALID_MODES:
        raise CatalogConfigError(f"Unknown RAG catalog mode: {mode!r}")
    return mode


def desktop_catalog_dir(settings: dict | None, *, create: bool = True) -> Path:
    if _mode(settings) == MODE_SHARED:
        return shared_catalog_dir(
            str((settings or {}).get("ragSharedCatalogKey", "")), create=create)
    if create:
        DESKTOP_RAG_DIR.mkdir(parents=True, exist_ok=True)
    return DESKTOP_RAG_DIR


def server_catalog_dir(user_id: int, settings: dict | None,
                       *, create: bool = True) -> Path:
    if _mode(settings) == MODE_SHARED:
        return shared_catalog_dir(
            str((settings or {}).get("ragSharedCatalogKey", "")), create=create)
    path = SERVER_RAG_ROOT / f"u{int(user_id)}"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _initialized(path: Path) -> bool:
    return (path / "meta.json").is_file() or (path / "chroma").is_dir()


def discover_catalogs(*, include_empty: bool = False) -> list[Catalog]:
    """Discover every local catalog MCP is allowed to aggregate."""
    found: list[Catalog] = []

    def add(catalog_id: str, kind: str, path: Path) -> None:
        resolved = path.resolve()
        if include_empty or _initialized(resolved):
            found.append(Catalog(catalog_id, kind, resolved))

    add("desktop", "desktop", DESKTOP_RAG_DIR)
    # Historical MCP versions wrote a collection directly into rag_data/.
    add("server-legacy-root", "legacy", SERVER_RAG_ROOT)
    if SERVER_RAG_ROOT.is_dir():
        for path in sorted(SERVER_RAG_ROOT.iterdir()):
            if path.is_dir() and re.fullmatch(r"u\d+", path.name):
                add(f"server:{path.name}", "server-user", path)
    if SHARED_RAG_ROOT.is_dir():
        for path in sorted(SHARED_RAG_ROOT.iterdir()):
            if path.is_dir() and re.fullmatch(r"[0-9a-f]{64}", path.name):
                add(f"shared:{path.name[:12]}", "shared", path)

    # Resolve/deduplicate defensively if paths are linked or configured oddly.
    unique: dict[str, Catalog] = {}
    for item in found:
        unique.setdefault(str(item.path).casefold(), item)
    return list(unique.values())


@contextmanager
def catalog_write_lock(catalog_dir: str | Path, *, timeout: float = 120.0):
    """Serialize mutating operations against the same catalog."""
    path = Path(catalog_dir)
    path.mkdir(parents=True, exist_ok=True)
    lock_path = path / ".write.lock"
    handle = open(lock_path, "a+b")  # noqa: SIM115 - held for context lifetime
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + max(0.0, float(timeout))
    locked = False
    try:
        while not locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"RAG catalog is busy: {path}") from None
                time.sleep(0.1)
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
