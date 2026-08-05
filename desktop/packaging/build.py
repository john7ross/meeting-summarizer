"""Portable build packager (TODO #12) — assembles the min / full distributions.

Run with the embedded Python:

    backend\\python\\python.exe desktop\\packaging\\build.py --variant min  --out dist
    backend\\python\\python.exe desktop\\packaging\\build.py --variant full --out dist [--dry-run]

Variants (owner's principle: a smaller build drops heavy MODELS, never FEATURES —
every variant ships every engine's code):

  min  — source (desktop/ + backend/*.py) + ffmpeg + an installer entry point.
         The recipient runs INSTALL.bat once (pip-installs torch + requirements
         into a local Python); heavy ASR models are fetched on demand. The small
         RU/EN Vosk models and default offline-diarization models are bundled.
  full — everything bundled: the embedded Python runtime (backend/python), ffmpeg,
         and the VARIANTS["full"] model set (every engine, one medium-tier model
         each, both languages). Unzip and run RUN.bat — no install, no network.

The archive is written by STREAMING files straight into the zip (no multi-GB
intermediate copy), so peak disk use is just the final archive size.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                      # repo root (parent of desktop/ and backend/)
sys.path.insert(0, str(ROOT / "backend"))

with (ROOT / "package.json").open("r", encoding="utf-8") as _manifest:
    APP_VERSION = str(json.load(_manifest)["version"])

# The interpreter range INSTALL.bat prefers must be the one installer.py enforces;
# hardcoding it here once drifted to 3.10 while the installer still accepted 3.9.
sys.path.insert(0, str(HERE))
from installer import MIN_PYTHON, MAX_PYTHON      # noqa: E402

# Directory/file names never packaged (user data, caches, vcs, scratch, prior builds).
IGNORE_NAMES = {"__pycache__", ".git", "dist", "node_modules", ".pytest_cache",
                ".locks", "config", "transcripts", "logs", "graphify-out",
                ".venv", "uploads", "rag_data",
                # user data / downloaded-on-demand extras — never ship these
                "recordings", "segments", "local_ai",
                # rendered PlantUML output: regenerable from the .puml sources
                # that DO ship, and a stale render is worse than none
                "out"}
# Junk that is junk EVERYWHERE, including inside vendored third-party trees.
JUNK_GLOBS = ["*.pyc", "*.pyo", "*.log", "*.err", "*.pid", "_tmp_*", "*.tmp"]

# OUR dev/test scaffolding. These names are ours; third-party packages use the
# same shapes for real code, so this list must NEVER be applied to a vendored
# tree. It was, and the embedded runtime lost 67 genuine files - among them
# torch/_subclasses/_fake_tensor_utils.py, which made `import torch` raise
# ModuleNotFoundError in every full build ever shipped: the offline variant
# could not transcribe at all, and nothing in the repo showed it.
PROJECT_GLOBS = ["_dl*", "_pip*", "_check_sherpa.py", "*_old",
                 "_selftest*", "_livetest*", "_fake_*", "_e2e_*",
                 "test_setup.py", "conftest.py", "run_coverage.py", "pytest.ini"]

IGNORE_GLOBS = JUNK_GLOBS + PROJECT_GLOBS

NODE_VERSION = "24.15.0"
NODE_ARCHIVE = f"node-v{NODE_VERSION}-win-x64.zip"
NODE_URL = f"https://nodejs.org/dist/v{NODE_VERSION}/{NODE_ARCHIVE}"
NODE_SHA256 = "cc5149eabd53779ce1e7bdc5401643622d0c7e6800ade18928a767e940bb0e62"


def _ignored(name: str, vendored: bool = False) -> bool:
    """Should this name be skipped?

    ``vendored=True`` for trees we did not write (the embedded interpreter, the
    downloaded model bundles): only real junk is dropped there, never anything
    that merely LOOKS like our scaffolding.
    """
    if name in IGNORE_NAMES:
        return True
    globs = JUNK_GLOBS if vendored else IGNORE_GLOBS
    return any(fnmatch.fnmatch(name, g) for g in globs)


def _iter_tree(src_root: Path, arc_prefix: str, vendored: bool = False):
    """Yield (file_path, arcname) under src_root, skipping ignored names."""
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = [d for d in dirnames if not _ignored(d, vendored)]
        for fn in filenames:
            if _ignored(fn, vendored):
                continue
            fp = Path(dirpath) / fn
            rel = fp.relative_to(src_root)
            yield fp, f"{arc_prefix}/{rel.as_posix()}"


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def collect_common(root: Path):
    """(path, arcname) pairs shared by both variants: source + ffmpeg + docs."""
    items = []
    # desktop/ (UI + core + adapters + docs), minus build/ itself. The self-tests,
    # live-tests and fakes are filtered out by IGNORE_GLOBS - they never ship.
    for fp, arc in _iter_tree(root / "desktop", "desktop"):
        if arc.startswith("desktop/build/"):
            continue
        items.append((fp, arc))
    # backend/ Python sources only (NOT the embedded runtime, resources, or the
    # stray legacy model caches under backend/models — those are bundled, if at
    # all, via the resources model set in the full variant, never in min source).
    for item in (root / "backend").iterdir():
        if item.name in ("python", "resources", "FFmpeg", "models") or _ignored(item.name):
            continue
        if item.is_dir():
            items += list(_iter_tree(item, f"backend/{item.name}"))
        elif item.suffix in (".py", ".txt", ".md"):
            items.append((item, f"backend/{item.name}"))
    # server/ (FastAPI web cabinet) — the SECOND front-end. It runs on the SAME
    # embedded python (its deps are installed there in the full build; min's
    # INSTALL.bat adds them). ``.venv`` and ``web/node_modules`` are skipped via
    # IGNORE; the compiled ``web/css/app.css`` IS shipped (the tailwind source is
    # harmless to include for rebuilds).
    srv = root / "server"
    if srv.exists():
        items += list(_iter_tree(srv, "server"))
    # ffmpeg binary (required, not pip-installable) — both variants
    items += list(_iter_tree(root / "backend" / "FFmpeg", "backend/FFmpeg"))
    # ...and its licence BESIDE it. The bundled build is configured with
    # --enable-gpl --enable-version3, so the binary is GPLv3; a recipient looks
    # for its terms next to the executable, not in a folder elsewhere in the zip.
    for src, arc in ((root / "licenses" / "GPL-3.0.txt",
                      "backend/FFmpeg/LICENSE-GPL-3.0.txt"),
                     (root / "licenses" / "ffmpeg-build-configuration.txt",
                      "backend/FFmpeg/BUILD-CONFIGURATION.txt")):
        if src.exists():
            items.append((src, arc))
    # The application icon. `resources/` is otherwise excluded (it holds the
    # multi-GB model sets, bundled per variant), so the icon the desktop loads at
    # startup shipped in NEITHER archive and every distributed copy fell back to
    # the default Qt window icon.
    app_icon = root / "resources" / "icon.png"
    if app_icon.exists():
        items.append((app_icon, "resources/icon.png"))
    # Top-level entry points for the recipient: the doc map (both languages), the
    # licence, and the engine-compat doc the READMEs link to. Without these the
    # unzipped distribution has no starting page and no licence.
    # ``package.json`` is not documentation: it is the single source of truth for
    # the app version, read at runtime by the server, the MCP server, the export
    # footer and the Obsidian notes. Left out of the archive, every shipped build
    # reported itself as 0.0.0.
    for name in ("README.md", "README.ru.md", "LICENSE", "package.json",
                 "WHISPER_ENGINES_COMPATIBILITY.md",
                 "WHISPER_ENGINES_COMPATIBILITY.ru.md",
                 # Third-party notices are not optional paperwork: the archives
                 # redistribute a GPLv3 ffmpeg build and LGPL Qt, and a copyleft
                 # binary shipped without its licence text is a real violation
                 # that no test can see. These files name every bundled
                 # component and point at the texts in licenses/.
                 "THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.ru.md",
                 "SECURITY.md", "SECURITY.ru.md",
                 # The shipped READMEs link to these; a doc map whose links dangle
                 # inside the archive is a defect the repo can never show.
                 "CONTRIBUTING.md", "CONTRIBUTING.ru.md",
                 # the only image either README embeds - without it the shipped
                 # README renders a broken picture
                 "donate-qr.png"):
        doc = root / name
        if doc.exists():
            items.append((doc, name))
    # licenses/ — the actual GPL/LGPL/Apache texts the notices reference. They
    # must travel INSIDE every archive; a link to gnu.org is not compliance.
    lic = root / "licenses"
    if lic.exists():
        items += list(_iter_tree(lic, "licenses"))
    # docs/ — agent-facing usage guides (e.g. driving the MCP server) must travel
    # WITH the distribution, otherwise the shipped MCP surface is undocumented.
    docs = root / "docs"
    if docs.exists():
        items += list(_iter_tree(docs, "docs"))
    return items


def collect_js_runtime():
    """Download and verify the pinned Node runtime needed by yt-dlp EJS.

    The runtime and its license are placed only in the release archive, not in
    the source tree. This keeps Git clean while making URL downloads work on a
    recipient machine that does not already have Node installed.
    """
    cache = Path(tempfile.gettempdir()) / "meeting-summarizer-build" / NODE_VERSION
    archive = cache / NODE_ARCHIVE
    extracted = cache / f"node-v{NODE_VERSION}-win-x64"
    node = extracted / "node.exe"
    license_file = extracted / "LICENSE"
    if not node.is_file() or not license_file.is_file():
        cache.mkdir(parents=True, exist_ok=True)
        if not archive.is_file():
            print(f"[common] downloading pinned Node.js {NODE_VERSION} runtime...")
            urllib.request.urlretrieve(NODE_URL, archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != NODE_SHA256:
            archive.unlink(missing_ok=True)
            raise RuntimeError(
                f"Node.js archive checksum mismatch: {digest} != {NODE_SHA256}")
        with zipfile.ZipFile(archive) as zf:
            prefix = f"node-v{NODE_VERSION}-win-x64/"
            for member in (prefix + "node.exe", prefix + "LICENSE"):
                zf.extract(member, cache)
    return [
        (node, "backend/JavaScript/node.exe"),
        (license_file, "backend/JavaScript/NODE-LICENSE.txt"),
    ]


# Models that are cheap but come from a FRAGILE source (alphacephei is a single
# small site that has been flaky), so we ship them even in `min` as insurance —
# the small RU+EN Vosk pair gives both-language offline ASR without the installer
# needing that site. Everything else (HF / k2-fsa / PyPI) is reliably re-fetchable.
MIN_SAFETY_MODELS = [
    ("vosk", "vosk-model-small-ru-0.22"),
    ("vosk", "vosk-model-small-en-us-0.15"),
]


def collect_min_safety(root: Path):
    """(path, arcname) pairs for the fragile-source models bundled into min."""
    import engines_registry as reg
    res_src = root / "resources"
    items, missing = [], []
    for engine, model in MIN_SAFETY_MODELS:
        try:
            path = reg.resolve_model_path(engine, model)
        except Exception:
            path = None
        if not path or not Path(path).exists():
            missing.append(f"{engine}/{model}")
            continue
        rel = Path(path).relative_to(res_src)
        items += list(_iter_tree(Path(path), f"resources/{rel.as_posix()}",
                                 vendored=True))
    # Offline sherpa diarization is the shipped default for WhisperX.  Without
    # these small models a fresh min install fails on its default path and offers
    # no UI action that can repair it.
    diar = res_src / "diarization_models"
    if diar.exists():
        items += list(_iter_tree(diar, "resources/diarization_models",
                                 vendored=True))
    else:
        missing.append("diarization/offline-default")
    return items, missing


def collect_full_runtime(root: Path):
    """(path, arcname) pairs for the embedded runtime + VARIANTS['full'] models."""
    import engines_registry as reg
    # vendored=True: third-party trees, where our scaffolding globs would delete
    # real code (see PROJECT_GLOBS).
    items = list(_iter_tree(root / "backend" / "python", "backend/python",
                            vendored=True))

    res_src = root / "resources"
    # Always ship offline diarization models (the default backend needs them).
    diar = res_src / "diarization_models"
    if diar.exists():
        items += list(_iter_tree(diar, "resources/diarization_models",
                                 vendored=True))
    # VARIANTS['full'] models, resolved to their on-disk paths via the registry.
    missing = []
    for engine, model in reg.VARIANTS["full"]:
        try:
            path = reg.resolve_model_path(engine, model)
        except Exception:
            path = None
        if not path or not Path(path).exists():
            missing.append(f"{engine}/{model}")
            continue
        path = Path(path)
        rel = path.relative_to(res_src)
        if path.is_dir():
            items += list(_iter_tree(path, f"resources/{rel.as_posix()}",
                                     vendored=True))
        else:
            items.append((path, f"resources/{rel.as_posix()}"))
    return items, missing


def launcher_items(variant: str):
    """Write RUN.bat (+ INSTALL.bat for min) to a temp dir; return (path, arc)."""
    tmp = Path(tempfile.mkdtemp(prefix="msbuild_"))
    out = []

    def _bat(name: str, text: str) -> Path:
        """Write a .bat with the CRLF it already contains.

        ``write_text`` translates "\\n" to os.linesep on Windows, so the "\\r\\n"
        these strings carry became "\\r\\r\\n" - every shipped launcher had a
        doubled carriage return on every line. cmd tolerates it, but `type`
        shows a blank line between each command and it is plainly wrong.
        """
        path = tmp / name
        path.write_text(text, encoding="utf-8", newline="")
        return path
    if variant == "full":
        run = _bat("RUN.bat",
                   "@echo off\r\ncd /d \"%~dp0\"\r\n"
                   "backend\\python\\python.exe desktop\\run.py\r\npause\r\n")
        out.append((run, "RUN.bat"))
        # The web cabinet runs on the SAME bundled python (server deps included).
        srv = _bat("SERVER.bat",
                   "@echo off\r\ncd /d \"%~dp0\"\r\n"
                   "echo Web cabinet on http://localhost:8000  (Ctrl+C to stop)\r\n"
                   "backend\\python\\python.exe server\\run_server.py\r\npause\r\n")
        out.append((srv, "SERVER.bat"))
    else:
        # Use the interpreter the INSTALLER actually installed into, not whatever
        # "python" happens to mean on PATH. Found on a clean Windows 11: the box
        # had 3.13 first on PATH, the installer correctly used 3.11 (3.13 is
        # unsupported - no numpy<2.0 wheels), and then RUN.bat launched 3.13 and
        # died on "No module named PySide6" after a perfectly good install.
        # installer.py records its own sys.executable in config\interpreter.txt.
        pick = ("set \"PY=python\"\r\n"
                "if exist \"config\\interpreter.txt\" set /p PY=<config\\interpreter.txt\r\n")
        run = _bat("RUN.bat",
                   "@echo off\r\ncd /d \"%~dp0\"\r\n" + pick +
                   "\"%PY%\" desktop\\run.py\r\npause\r\n")
        srv = _bat("SERVER.bat",
                   "@echo off\r\ncd /d \"%~dp0\"\r\n" + pick +
                   "echo Web cabinet on http://localhost:8000  (Ctrl+C to stop)\r\n"
                   "\"%PY%\" server\\run_server.py\r\npause\r\n")
        # Hands over to the interactive installer: it scans the machine, proposes
        # what fits it, and installs only what the user ticks. Blindly installing
        # the CUDA torch stack on a CPU-only laptop wasted gigabytes.
        # `%*` is NOT decoration: both READMEs document `--recommended --yes` and
        # `--plan-only`, and without it INSTALL.bat swallowed every one of them,
        # so an unattended install silently dropped into the interactive menu.
        # Finding the interpreter is OUR job, not `python`'s. A clean Windows 11
        # carries a zero-byte Microsoft Store stub named python.exe on PATH; running
        # it prints "Python was not found; run without arguments to install from the
        # Microsoft Store" and nothing of ours ever executes. That advice is actively
        # wrong here - the Store ships 3.13+, which this stack does not support - so
        # the recipient installs the wrong version and only then meets our own
        # "too new" wall. Probe for a REAL interpreter, preferring a supported minor,
        # and if there is none say so in our words. Verified on a clean Windows 11 VM.
        inst = _bat(
            "INSTALL.bat",
            "@echo off\r\n"
            # The file is UTF-8 and the message below is Russian; without this the
            # console renders it in cp866 as mojibake.
            "chcp 65001 >nul\r\n"
            "cd /d \"%~dp0\"\r\n"
            "set \"PY=\"\r\n"
            # First pass: an interpreter this stack actually supports. Bare `py`
            # is LAST even here - it means "newest installed", which is the one
            # most likely to be unsupported (on the build box it selected 3.14
            # while a perfectly good 3.11 sat on PATH as `python`).
            "call :supported \"py -3.11\"\r\n"
            "call :supported \"py -3.12\"\r\n"
            "call :supported \"py -3.10\"\r\n"
            "call :supported \"py -3.9\"\r\n"
            "call :supported \"python\"\r\n"
            "call :supported \"python3\"\r\n"
            "call :supported \"py\"\r\n"
            # Second pass: any real interpreter, so installer.py can explain
            # precisely what is wrong with it instead of us guessing.
            "call :any \"python\"\r\n"
            "call :any \"python3\"\r\n"
            "call :any \"py\"\r\n"
            # Still nothing on PATH? Ask the bootstrap, which also looks in the
            # per-user install directories and at config\\interpreter.txt. Without
            # this the .bat offered to install Python that was ALREADY installed:
            # PrependPath only rewrites the registry, so the console the user
            # opens next still has the old PATH.
            "if not defined PY (\r\n"
            "  for /f \"usebackq delims=\" %%P in (`powershell -NoProfile"
            " -ExecutionPolicy Bypass -File \"desktop\\packaging\\bootstrap_python.ps1\""
            " -ProbeOnly`) do set \"PY=%%P\"\r\n"
            ")\r\n"
            "if not defined PY goto :nopython\r\n"
            "%PY% desktop\\packaging\\installer.py %*\r\n"
            "if errorlevel 1 (\r\n"
            "  echo.\r\n"
            "  echo [!] Setup did not finish. See the message above.\r\n"
            ")\r\n"
            "pause\r\n"
            "exit /b 0\r\n"
            "\r\n"
            ":supported\r\n"
            "if defined PY goto :eof\r\n"
            f"%~1 -c \"import sys;raise SystemExit(0 if "
            f"{MIN_PYTHON}<=sys.version_info[:2]<={MAX_PYTHON} else 1)\""
            " >nul 2>&1 && set \"PY=%~1\"\r\n"
            "goto :eof\r\n"
            "\r\n"
            ":any\r\n"
            "if defined PY goto :eof\r\n"
            "%~1 -c \"import sys\" >nul 2>&1 && set \"PY=%~1\"\r\n"
            "goto :eof\r\n"
            "\r\n"
            ":nopython\r\n"
            "echo.\r\n"
            "echo [!] Python not found / Python не найден\r\n"
            "echo.\r\n"
            "echo     This project needs Python 3.11. It can be installed now,\r\n"
            "echo     for this user only, from python.org (about 25 MB).\r\n"
            "echo     Проекту нужен Python 3.11. Могу поставить его прямо сейчас,\r\n"
            "echo     только для текущего пользователя, с python.org (~25 МБ).\r\n"
            "echo.\r\n"
            # An unattended run (--yes) must not sit on a prompt, and `set /p` at
            # EOF leaves the variable untouched - hence the default below.
            "set \"ANS=Y\"\r\n"
            "echo %* | find /i \"--yes\" >nul || set /p \"ANS=Install it now? / Поставить сейчас? [Y/n]: \"\r\n"
            "if /i \"%ANS%\"==\"n\" goto :manual\r\n"
            "for /f \"usebackq delims=\" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass"
            " -File \"desktop\\packaging\\bootstrap_python.ps1\"`) do set \"PY=%%P\"\r\n"
            "if not defined PY goto :manual\r\n"
            "echo   using %PY%\r\n"
            "\"%PY%\" desktop\\packaging\\installer.py %*\r\n"
            "if errorlevel 1 (\r\n"
            "  echo.\r\n"
            "  echo [!] Setup did not finish. See the message above.\r\n"
            ")\r\n"
            "pause\r\n"
            "exit /b 0\r\n"
            "\r\n"
            ":manual\r\n"
            "echo.\r\n"
            "echo     Install Python 3.11 from https://www.python.org/downloads/\r\n"
            "echo     and tick \"Add python.exe to PATH\" on the first screen.\r\n"
            "echo     Do NOT install it from the Microsoft Store: that build is\r\n"
            "echo     3.13 or newer, which this project does not support.\r\n"
            "echo.\r\n"
            "echo     Установите Python 3.11 с https://www.python.org/downloads/\r\n"
            "echo     и отметьте \"Add python.exe to PATH\" на первом экране.\r\n"
            "echo     Версия из Microsoft Store НЕ подойдёт: это 3.13 и новее,\r\n"
            "echo     а закреплённый numpy^<2.0 под неё колёс не публикует.\r\n"
            "echo.\r\n"
            "pause\r\n"
            "exit /b 1\r\n")
        out.append((run, "RUN.bat"))
        out.append((srv, "SERVER.bat"))
        out.append((inst, "INSTALL.bat"))
    return out


def build(variant: str, out_dir: Path, dry_run: bool) -> None:
    root = ROOT
    print(f"[{variant}] collecting file list...")
    items = collect_common(root)
    items += collect_js_runtime()
    missing = []
    if variant == "full":
        full_items, missing = collect_full_runtime(root)
        items += full_items
    else:  # min: bundle the fragile-source safety models
        safe_items, missing = collect_min_safety(root)
        items += safe_items
    launchers = launcher_items(variant)
    items += launchers

    total = sum(fp.stat().st_size for fp, _ in items)
    print(f"[{variant}] {len(items)} files, {_human(total)} uncompressed")
    if missing:
        print("  WARNING - full-variant models NOT on disk (download first):")
        for m in missing:
            print(f"    - {m}")
    try:
        if dry_run:
            print(f"[{variant}] --dry-run: not writing archive.")
            return

        archive = out_dir / f"meeting-summarizer-{variant}-v{APP_VERSION}.zip"
        if archive.exists():
            archive.unlink()
        print(f"[{variant}] writing {archive.name} (this can take a while)...")
        # level 1: models/torch are already-compressed binaries; fast + still shrinks .py/.dll
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for i, (fp, arc) in enumerate(items, 1):
                zf.write(fp, arc)
                if i % 2000 == 0:
                    print(f"    {i}/{len(items)} files...")
        print(f"[{variant}] DONE: {archive}  ({_human(archive.stat().st_size)})")
    finally:
        # The launcher .bat files are generated into a fresh temp directory every
        # run and were never removed - a build machine had 367 stray msbuild_*
        # directories in %TEMP%. Cleaned even when the build fails or is killed.
        for parent in {fp.parent for fp, _ in launchers}:
            shutil.rmtree(parent, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["min", "full"], required=True)
    ap.add_argument("--out", default="dist")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    out_dir = (ROOT / args.out) if not os.path.isabs(args.out) else Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    build(args.variant, out_dir, args.dry_run)


if __name__ == "__main__":
    main()
