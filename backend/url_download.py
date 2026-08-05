#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Download media from a URL for processing (Feature 2: "video from the network").

Given a URL (YouTube, a file server, or hundreds of other sites) this downloads
the VIDEO into a target directory and streams JSON-lines progress on stdout, the
same shape ``models_cli.py`` uses, so the server worker and the desktop pipeline
can drive it as a subprocess in the embedded runtime.

The downloaded file is a normal video that then goes through the EXACT same
pipeline as an uploaded meeting video (no separate audio path). We prefer a
single progressive MP4 and otherwise merge separate video/audio streams with
the bundled FFmpeg. For a plain direct-media link that yt-dlp's generic
extractor can't handle, we fall back to a straight HTTP download.

Emits, one JSON object per line:
    {"event": "progress", "percent": <0-100>, "detail": "<text>"}
    {"event": "done", "path": "<file>", "title": "<title>"}
    {"event": "error", "message": "<why>"}

Usage:
    python url_download.py <url> --out-dir <dir> [--name <basename>]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MEDIA_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".mp3", ".wav", ".m4a",
              ".aac", ".ogg", ".flac", ".mpeg", ".mpg", ".wmv", ".flv"}


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _progress(percent: int, detail: str = "") -> None:
    _emit({"event": "progress", "percent": max(0, min(100, int(percent))), "detail": detail})


# --------------------------------------------------------------------------
# yt-dlp path
# Browsers we auto-probe for cookies when YouTube demands sign-in (anti-bot).
CANDIDATE_BROWSERS = ("chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium")
AUTH_HINT = ("This site requires sign-in verification (YouTube anti-bot). "
             "Sign into the site in your browser (Chrome/Edge/Firefox) on this "
             "machine — the app reuses those cookies automatically — or the video "
             "may be private / age- / region-restricted.")


def _js_runtime_options() -> dict:
    """Return yt-dlp EJS options for a bundled or system JavaScript runtime."""
    backend_dir = Path(__file__).resolve().parent
    bundled = (
        ("node", backend_dir / "JavaScript" / "node.exe"),
        ("deno", backend_dir / "JavaScript" / "deno.exe"),
        ("bun", backend_dir / "JavaScript" / "bun.exe"),
        ("quickjs", backend_dir / "JavaScript" / "qjs.exe"),
    )
    for name, executable in bundled:
        if executable.is_file():
            return {
                "js_runtimes": {name: {"path": str(executable)}},
                "remote_components": {"ejs:github"},
            }
    for name, command in (("node", "node"), ("deno", "deno"), ("bun", "bun"),
                          ("quickjs", "qjs")):
        if executable := shutil.which(command):
            return {
                "js_runtimes": {name: {"path": executable}},
                "remote_components": {"ejs:github"},
            }
    return {}


def _is_auth_error(err) -> bool:
    """Is this failure one that the user's browser cookies could fix?

    Wording differs per site: YouTube says "Private video. Sign in if you've been
    granted access", others just "This video is private". Matching only
    "private video" missed the second form, so a private recording the user CAN
    see failed without ever trying their cookies.
    """
    s = str(err).lower()
    return any(k in s for k in ("sign in", "confirm you", "not a bot", "cookies",
                                "private video", "is private", "members-only",
                                "members only", "login", "log in",
                                "age-restricted", "age restricted"))


def _download_ytdlp(url: str, out_dir: Path, name: str | None,
                    cookies_mode: str | None = None) -> tuple[str, str]:
    """yt-dlp with a native cookie story: ``auto`` (default) downloads without
    cookies and, only if the site demands sign-in, transparently retries with the
    cookies of each installed browser the user is logged into. ``off`` never uses
    cookies; a specific browser name uses only that one."""
    mode = (cookies_mode or "auto").strip().lower()
    if mode and mode not in ("auto", "off"):
        return _try_ytdlp(url, out_dir, name, mode)
    try:
        return _try_ytdlp(url, out_dir, name, None)
    except Exception as err:  # noqa: BLE001
        if mode == "auto" and _is_auth_error(err):
            for br in CANDIDATE_BROWSERS:
                _progress(0, f"sign-in required — trying {br} cookies")
                try:
                    return _try_ytdlp(url, out_dir, name, br)
                except Exception:  # noqa: BLE001
                    continue
        raise


def _try_ytdlp(url: str, out_dir: Path, name: str | None,
               cookies_from_browser: str | None = None) -> tuple[str, str]:
    import yt_dlp

    last = {"pct": -1}

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = int(done * 100 / total) if total else 0
            if pct != last["pct"]:
                last["pct"] = pct
                _progress(pct, "downloading")
        elif d.get("status") == "finished":
            _progress(99, "post-processing")

    outtmpl = str(out_dir / ((name + ".%(ext)s") if name else "%(title).80s.%(ext)s"))
    opts = {
        # Processing needs clear audio, not a multi-hundred-MB 4K/1080p stream.
        # Prefer a combined MP4 up to 480p: on current YouTube this is also more
        # reliable than some direct DASH streams, which can reject with 403.
        # Fall back to split 720p video + best audio for other sites.
        "format": (
            "best[height<=480][ext=mp4]/"
            "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=720]+bestaudio/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,        # keep stdout clean: only our JSON lines, not yt-dlp's bar
        "progress_hooks": [hook],
        "restrictfilenames": True,
    }
    opts.update(_js_runtime_options())
    bundled_ffmpeg = Path(__file__).resolve().parent / "FFmpeg"
    bundled_ffmpeg_bin = bundled_ffmpeg / "bin"
    if (bundled_ffmpeg / "ffmpeg.exe").is_file():
        opts["ffmpeg_location"] = str(bundled_ffmpeg)
    elif (bundled_ffmpeg_bin / "ffmpeg.exe").is_file():
        opts["ffmpeg_location"] = str(bundled_ffmpeg_bin)
    # YouTube increasingly requires auth; pull cookies from an installed browser
    # (e.g. "chrome"/"firefox") the user is signed into.
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Resolve the actual on-disk path (handles post-processing/merges).
        path = ""
        reqs = info.get("requested_downloads") or []
        candidates = [info.get("filepath")]
        candidates += [item.get("filepath") for item in reqs]
        candidates.append(ydl.prepare_filename(info))
        path = next((str(p) for p in candidates if p and Path(p).is_file()), "")
        if not path and name:
            # A merged output can have a different extension from the selected
            # component streams, so resolve the actual finished file on disk.
            matches = [
                p for p in out_dir.glob(name + ".*")
                if p.is_file() and p.suffix.lower() in MEDIA_EXTS
            ]
            if matches:
                path = str(max(matches, key=lambda p: p.stat().st_mtime))
        if not path:
            path = str(ydl.prepare_filename(info))
        title = info.get("title") or Path(path).stem
        return path, title


# --------------------------------------------------------------------------
# plain HTTP fallback (direct media links a generic extractor may miss)
# --------------------------------------------------------------------------
def _looks_like_direct_media(url: str) -> bool:
    path = urllib.parse.urlparse(url).path
    return Path(path).suffix.lower() in MEDIA_EXTS


def _download_http(url: str, out_dir: Path, name: str | None) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    base = name or (Path(parsed.path).stem or "download")
    ext = Path(parsed.path).suffix or ".bin"
    dest = out_dir / f"{base}{ext}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        last_pct = -1
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = int(done * 100 / total)
                    if pct != last_pct:
                        last_pct = pct
                        _progress(pct, "downloading")
    return str(dest), base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--name", default=None, help="base filename (no extension)")
    ap.add_argument("--cookies-from-browser", default="auto",
                    help="cookie source for auth-gated sites: 'auto' (try installed "
                         "browsers on sign-in), 'off', or a browser name (chrome/firefox/edge/…)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        path, title = _download_ytdlp(args.url, out_dir, args.name,
                                      cookies_mode=args.cookies_from_browser)
        _emit({"event": "done", "path": str(path), "title": title})
        return 0
    except Exception as yt_err:  # noqa: BLE001
        # Fallback: a plain direct-media link yt-dlp's generic extractor missed.
        if _looks_like_direct_media(args.url):
            try:
                path, title = _download_http(args.url, out_dir, args.name)
                _emit({"event": "done", "path": str(path), "title": title})
                return 0
            except Exception as http_err:  # noqa: BLE001
                m = f"http: {http_err}"
                _emit({"event": "error", "message": m, "error": m})  # both keys for all consumers
                return 1
        # Turn the raw yt-dlp bot-check error into an actionable message.
        msg = str(yt_err)[:400]
        if _is_auth_error(yt_err):
            msg = f"{AUTH_HINT} [{msg}]"
        _emit({"event": "error", "message": msg, "error": msg})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
