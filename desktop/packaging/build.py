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


# Junk in ANY tree, ours or somebody else's.
JUNK_NAMES = {"__pycache__", ".git", ".pytest_cache"}


def _ignored(name: str, vendored: bool = False) -> bool:
    """Should this name be skipped?

    ``vendored=True`` for trees we did not write (the embedded interpreter, the
    downloaded model bundles): only real junk is dropped there, never anything
    that merely LOOKS like our scaffolding.

    IGNORE_NAMES describes OUR project layout - `config`, `logs`, `uploads`,
    `dist`. Third-party packages use those words too, and applying the list to
    vendored trees quietly amputated them: `opentelemetry/proto/collector/logs`
    never shipped, so every full build's `import chromadb` died with
    "No module named 'opentelemetry.proto.collector.logs'" - the knowledge base
    and semantic search were dead in the box. Found by restoring a runtime from
    the archive and running the RAG self-tests against it.
    """
    if name in JUNK_NAMES:
        return True
    if not vendored and name in IGNORE_NAMES:
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
                 # every image either README embeds - without them the shipped
                 # README renders a broken picture. A check in
                 # _selftest_build_composition.py derives this list from the
                 # READMEs themselves, so adding an image to a README and not
                 # here fails the suite instead of shipping a broken page.
                 "donate-qr.png", "Promo-GitHub.gif"):
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
    """(path, arcname) pairs for the launchers this variant ships.

    These are REAL FILES in the repository root, not strings generated here.
    Generating them meant two copies of the same thing and they drifted: the repo
    kept its own RUN.bat / SERVER.bat wired to ``backend\python``, a path a git
    clone does not have, so anyone arriving from GitHub could not start the
    project at all. One file, shipped as-is, cannot disagree with itself.

    The launchers resolve the interpreter at run time (embedded runtime -> the
    one INSTALL.bat recorded -> PATH), which is why the same file serves the full
    build, a min install and a clone. INSTALL.bat is min-only: the full build has
    nothing left to install.

    WARNING to anyone refactoring this: the returned paths are INSIDE the working
    tree. Nothing may delete them or their parent. build() used to remove
    ``fp.parent`` to tidy up the temp directory these files once lived in; after
    they moved here, that line erased the repository, its git history and both
    release archives. A check in _selftest_build_composition.py now fails if any
    caller of this function also mentions rmtree.
    """
    names = ["RUN.bat", "SERVER.bat"] + ([] if variant == "full" else ["INSTALL.bat"])
    out = []
    for name in names:
        path = ROOT / name
        if not path.is_file():
            raise FileNotFoundError(f"launcher missing from the repo root: {path}")
        out.append((path, name))
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
    items += launcher_items(variant)

    total = sum(fp.stat().st_size for fp, _ in items)
    print(f"[{variant}] {len(items)} files, {_human(total)} uncompressed")
    if missing:
        print("  WARNING - full-variant models NOT on disk (download first):")
        for m in missing:
            print(f"    - {m}")
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
    # There is deliberately NO cleanup step here. This used to remove the temp
    # directory the launchers were generated into; once they became files in the
    # repository root, the very same call deleted the working tree, .git and both
    # release archives. Building is read-only over the source: it writes exactly
    # one file - the archive above - and removes nothing.


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
