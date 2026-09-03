"""Launch the Meeting Summarizer web server.

Runs uvicorn with SERVER_MODE enabled and the repo root on sys.path so the
``server.*`` package imports resolve. Use the server venv:

    server\\.venv\\Scripts\\python.exe server\\run_server.py

Env: PORT (default 8000), HOST (default 0.0.0.0), TRUSTED_PROXIES (X-Forwarded-*
senders, default 127.0.0.1), DATABASE_URL (default SQLite
file at config/server.db).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("SERVER_MODE", "true")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def _missing_dependencies() -> list:
    """Server-only packages, in the order a user would notice them missing."""
    import importlib.util
    # module -> distribution name, because they differ often enough to matter.
    # Kept in step with server/requirements.txt by _selftest_core.py.
    needed = {"fastapi": "fastapi", "uvicorn": "uvicorn", "sqlalchemy": "sqlalchemy",
              "aiosqlite": "aiosqlite", "jose": "python-jose", "passlib": "passlib",
              "bcrypt": "bcrypt", "multipart": "python-multipart"}
    return [pkg for mod, pkg in needed.items()
            if importlib.util.find_spec(mod) is None]


if __name__ == "__main__":
    # The web cabinet is an OPTIONAL component of the min install. Without this
    # check, declining it and then double-clicking SERVER.bat produced a raw
    # "ModuleNotFoundError: No module named 'sqlalchemy'" traceback - which reads
    # like a broken build rather than a component that was never selected.
    # (uvicorn alone proves nothing: chromadb pulls it in for the RAG feature.)
    _missing = _missing_dependencies()
    if _missing:
        print("\n[!] The web cabinet is not installed / Веб-кабинет не установлен\n")
        print("    Missing: " + ", ".join(_missing))
        print("    Run INSTALL.bat again and tick 'Web cabinet' in the component list.")
        print("    Запустите INSTALL.bat ещё раз и отметьте «Веб-кабинет» в списке")
        print("    компонентов. Десктоп (RUN.bat) при этом работает и без него.\n")
        raise SystemExit(1)

    import uvicorn
    uvicorn.run(
        "server.api.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
        # Behind a TLS-terminating reverse proxy the app otherwise sees every
        # request as plain http from the proxy's own address: client IPs vanish
        # from the logs and any scheme-aware behaviour is wrong. TRUSTED_PROXIES
        # restricts which peers may set the X-Forwarded-* headers ("*" trusts the
        # immediate peer only, which is the proxy on a single-host deployment).
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("TRUSTED_PROXIES", "127.0.0.1"),
    )
