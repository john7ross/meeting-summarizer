"""Self-test for the segment-trimming helpers (app/backend/media.py).

Covers timecode parsing/formatting, segment naming, and a REAL ffmpeg cut on a
self-generated tone file (no dependency on any user media). Run headless:
    backend\\python\\python.exe desktop\\_selftest_media.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "desktop"))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app import paths                       # noqa: E402
from app.backend import media               # noqa: E402
import url_download                         # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


# -- timecodes ---------------------------------------------------------
check("fmt_zero", media.format_timecode(0) == "0:00:00", media.format_timecode(0))
check("fmt_hms", media.format_timecode(3930.4) == "1:05:30", media.format_timecode(3930.4))
check("parse_hms", media.parse_timecode("1:05:30") == 3930.0)
check("parse_ms", media.parse_timecode("05:30") == 330.0)
check("parse_s", media.parse_timecode("90") == 90.0)
check("roundtrip", media.parse_timecode(media.format_timecode(4271)) == 4271.0)
for bad in ("", "abc", "1:2:3:4", "1::2"):
    try:
        media.parse_timecode(bad)
        check(f"reject_{bad!r}", False, "accepted a bad timecode")
    except ValueError:
        check(f"reject_{bad!r}", True)

# -- naming ------------------------------------------------------------
name = media.segment_filename("C:/x/Meeting 5.mkv", 60, 3930)
check("segment_name", name == "Meeting 5 (0-01-00 - 1-05-30).wav", name)
check("name_no_colons", ":" not in name, name)

# -- real ffmpeg cut ---------------------------------------------------
tmp = Path(tempfile.mkdtemp())
src = tmp / "tone.wav"
gen = subprocess.run(
    [paths.ffmpeg_executable(), "-y", "-f", "lavfi",
     "-i", "sine=frequency=440:duration=12", "-ar", "16000", "-ac", "1", str(src)],
    capture_output=True, text=True)
if gen.returncode != 0 or not src.exists():
    check("ffmpeg_available", False, (gen.stderr or "")[-120:])
else:
    check("ffmpeg_available", True)
    dur = media.probe_duration(src)
    check("probe_duration", abs(dur - 12.0) < 0.5, f"{dur:.2f}s")

    dst = tmp / media.segment_filename(src, 3, 8)
    media.cut_segment(src, dst, 3, 8)
    cut = media.probe_duration(dst)
    check("cut_length", abs(cut - 5.0) < 0.3, f"{cut:.2f}s (expected 5)")
    check("cut_exists", dst.exists() and dst.stat().st_size > 0)

    try:                                    # end <= start must be rejected
        media.cut_segment(src, tmp / "bad.wav", 8, 3)
        check("reject_inverted_range", False, "accepted end<=start")
    except ValueError:
        check("reject_inverted_range", True)

check("probe_missing_file_is_zero", media.probe_duration(tmp / "nope.mkv") == 0.0)

# -- an empty transcription must fail HERE, naming the cause ------------------
# A 15-minute screen recording whose audio track was digital silence transcribed
# "successfully" into nothing: every chunk was ticked green and the run died two
# stages later on "No text provided or text is empty", blaming the AI provider.
import processor                              # noqa: E402
from processing.audio import peak_dbfs        # noqa: E402

silent = tmp / "silent.wav"
subprocess.run(
    [paths.ffmpeg_executable(), "-y", "-f", "lavfi",
     "-i", "anullsrc=r=16000:cl=mono", "-t", "3", str(silent)],
    capture_output=True, text=True)
loud = src                                     # the 440 Hz tone generated above

peak_silent, peak_loud = peak_dbfs(str(silent)), peak_dbfs(str(loud))
check("peak_dbfs_reads_silence", peak_silent is not None and peak_silent <= -60,
      str(peak_silent))
check("peak_dbfs_reads_sound", peak_loud is not None and peak_loud > -60, str(peak_loud))
check("peak_dbfs_survives_a_missing_file", peak_dbfs(str(tmp / "nope.wav")) is None)


def _verdict(transcript_text, audio):
    doc = tmp / "raw.txt"
    doc.write_text(transcript_text, encoding="utf-8")
    try:
        processor.verify_transcript_has_speech(str(doc), str(audio))
        return "ok"
    except Exception as exc:                   # noqa: BLE001
        return str(exc)


check("silent_track_is_named_as_the_cause",
      _verdict("", silent).startswith("SILENT_AUDIO:"), _verdict("", silent)[:60])
check("silent_verdict_quotes_the_measured_level",
      "dBFS" in _verdict("", silent), _verdict("", silent)[:80])
check("timestamps_without_words_count_as_empty",
      _verdict("[00:00:00]\n[00:00:05]\n", silent).startswith("SILENT_AUDIO:"))
check("whitespace_counts_as_empty",
      _verdict("   \n\n ", silent).startswith("SILENT_AUDIO:"))
check("audible_but_wordless_is_not_blamed_on_silence",
      _verdict("", loud).startswith("NO_SPEECH:"), _verdict("", loud)[:60])
check("a_real_transcript_passes_through",
      _verdict("[00:00:00] Итак, коллеги, начнём.", silent) == "ok")
# The guard runs BEFORE cleanup deletes the temp audio, or there is nothing left
# to measure and the diagnosis degrades to a guess.
_proc_src = (PROJECT_ROOT / "backend" / "processor.py").read_text(encoding="utf-8")
check("guard_runs_before_the_temp_audio_is_deleted",
      _proc_src.index("verify_transcript_has_speech(output_path") <
      _proc_src.index("os.remove(temp_audio)"),
      "measuring a deleted file is impossible")

# -- network-video downloader contract (no network) -------------------
# Modern YouTube commonly has no progressive MP4.  The downloader must permit
# a split video+audio fallback and point yt-dlp at the bundled FFmpeg.
import yt_dlp
real_ytdl = yt_dlp.YoutubeDL
captured = {}


class FakeYDL:
    def __init__(self, opts):
        captured.update(opts)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=True):
        out = tmp / "network.mp4"
        out.write_bytes(b"media")
        return {"title": "Network", "filepath": str(out), "requested_downloads": []}

    def prepare_filename(self, info):
        return str(tmp / "network.mp4")


yt_dlp.YoutubeDL = FakeYDL
try:
    net_path, net_title = url_download._try_ytdlp(
        "https://example.invalid/watch?v=x", tmp, "network")
finally:
    yt_dlp.YoutubeDL = real_ytdl
check("youtube_split_stream_fallback",
      "bestvideo" in captured.get("format", "")
      and "+bestaudio" in captured.get("format", "")
      and "height<=720" in captured.get("format", ""),
      captured.get("format", ""))
check("youtube_uses_bundled_ffmpeg",
      (Path(captured.get("ffmpeg_location", "")) / "ffmpeg.exe").is_file(),
      captured.get("ffmpeg_location", ""))
check("youtube_enables_ejs_runtime",
      bool(captured.get("js_runtimes"))
      and "ejs:github" in captured.get("remote_components", set()),
      str(captured.get("js_runtimes", {})))
check("youtube_returns_finished_file",
      Path(net_path).is_file() and net_title == "Network", net_path)

# -- which failures are worth retrying with the user's cookies ---------
# Sites word this differently; matching only "private video" missed
# "This video is private", so a recording the user CAN see failed without ever
# trying their cookies. Equally, ordinary failures must NOT trigger a pointless
# retry against every installed browser.
_needs_signin = [
    "Sign in to confirm you're not a bot",
    "Please sign in",
    "This video is private",
    "Private video. Sign in if you have been granted access",
    "This content is private",
    "Join this channel to get access to members-only content",
    "Video unavailable. This video is age-restricted",
    "Login required",
]
_ordinary = [
    "HTTP Error 404: Not Found",
    "Unable to download webpage: timed out",
    "Requested format is not available",
    "No space left on device",
    "Connection reset by peer",
    "Private network unreachable",
    "SSL certificate verify failed",
]
_missed = [e for e in _needs_signin if not url_download._is_auth_error(e)]
_false = [e for e in _ordinary if url_download._is_auth_error(e)]
check("sign_in_errors_all_recognised", not _missed, str(_missed))
check("ordinary_errors_never_trigger_cookie_retries", not _false, str(_false))
check("auto_mode_has_browsers_to_try",
      len(url_download.CANDIDATE_BROWSERS) >= 2,
      str(url_download.CANDIDATE_BROWSERS))

print("\n".join(results))
failed = [r for r in results if r.startswith("FAIL")]
print(f"SUMMARY {'HAS_FAILURES' if failed else 'ALL_PASS'} ({len(results)} checks)")
sys.exit(1 if failed else 0)
