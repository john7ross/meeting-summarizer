"""Self-test for the live wiring on the desktop side: recorder tap, LiveSession,
recorder dialog.

Everything here runs against the REAL subprocesses — a real streaming engine for
transcription and a fake agent CLI for the summary — because the parts that
break in this feature are the seams: the PCM tap, the process pipe, the JSON
line protocol and the "never blank a good summary" rule. Testing those with
mocks would test the mocks.

Needs an offline ASR model on disk (sherpa-onnx or vosk). Without one the
transcription half is skipped and reported as skipped, never as passed.

Run:
    set QT_QPA_PLATFORM=offscreen && backend\\python\\python.exe desktop\\_selftest_live_ui.py
"""
import math
import os
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                 # noqa: E402
app = QApplication.instance() or QApplication(sys.argv)

import engines_registry as reg                             # noqa: E402
from app.backend import live as live_backend               # noqa: E402
from app.core import recorder as recorder_mod              # noqa: E402
from app.core.live_session import LiveSession              # noqa: E402
from app.core.loopback import SystemAudioCapture, probe    # noqa: E402
from app.ui.recorder_dialog import RecorderDialog          # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


def skip(name, why):
    results.append(f"SKIP  {name}  {why}")


def pump(seconds: float) -> None:
    """Spin the Qt event loop so QProcess signals are actually delivered."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def pump_until(predicate, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


RATE = 16000
tmp = Path(tempfile.mkdtemp())

# ── recorder: stereo interleaving is the mic/system layout ───────────────────
left = struct.pack("<4h", 1, 2, 3, 4)
right = struct.pack("<4h", -1, -2, -3, -4)
mixed = recorder_mod.interleave_stereo(left, right)
check("interleave_length_doubles", len(mixed) == len(left) + len(right))
check("interleave_order_is_L_R",
      struct.unpack("<8h", mixed) == (1, -1, 2, -2, 3, -3, 4, -4),
      str(struct.unpack("<8h", mixed)))
short = recorder_mod.interleave_stereo(left, struct.pack("<2h", 9, 9))
check("interleave_pads_the_short_side", len(short) == len(left) * 2,
      "a lagging device must not shift the other channel")
check("interleave_of_nothing_is_nothing",
      recorder_mod.interleave_stereo(b"", b"") == b"")

rec = recorder_mod.AudioRecorder()
check("recorder_defaults_to_mono", rec.channels == 1)
check("recorder_system_inactive_by_default", rec.system_active is False)
check("recorder_no_system_error_when_not_asked", rec.system_error == "")

# ── loopback probe answers instead of raising ───────────────────────────────
available, reason = probe()
check("loopback_probe_returns_a_reason", isinstance(available, bool) and bool(reason),
      f"available={available} reason={reason}")
capture = SystemAudioCapture(sample_rate=RATE)
check("loopback_read_before_start_is_silence",
      capture.read(160) == b"\x00" * 320,
      "a consumer must always get exactly what it asked for")

if available:
    started = capture.start()
    check("loopback_starts", started, capture.error)
    if started:
        pump(0.6)
        block = capture.read(1600)          # 100 ms
        check("loopback_read_is_exact_length", len(block) == 3200, f"{len(block)} bytes")
        check("loopback_reports_device", bool(capture.device_name), capture.device_name)
        capture.stop()
        check("loopback_stops", not capture.running)
else:
    skip("loopback_starts", f"no loopback on this machine ({reason})")

# ── the live protocol: parsing what the worker prints ───────────────────────
seg = live_backend.parse_line(
    '{"type":"segment","index":2,"start":11.1,"duration":3.0,'
    '"timestamp":"[00:00:11]","source":"system","text":"согласны","latency":0.4}')
check("parse_segment", isinstance(seg, live_backend.Segment))
check("segment_line_carries_source",
      seg.line == "[00:00:11] [SYSTEM]: согласны", seg.line)
mic_seg = live_backend.parse_line(
    '{"type":"segment","index":1,"start":1.0,"duration":1.0,"timestamp":"[00:00:01]",'
    '"source":"mic","text":"привет"}')
check("mic_segment_labelled_mic", mic_seg.line.startswith("[00:00:01] [MIC]:"))
ready = live_backend.parse_line(
    '{"type":"ready","engine":"vosk","model":"small-ru","device":"cpu"}')
check("parse_status", isinstance(ready, live_backend.Status) and ready.kind == "ready")
check("ready_describes_engine_model_device",
      ready.describe() == "vosk / small-ru (cpu)", ready.describe())
check("parse_garbage_line_is_none", live_backend.parse_line("not json") is None)
check("parse_empty_line_is_none", live_backend.parse_line("") is None)
check("parse_unknown_shape_is_none", live_backend.parse_line('{"a":1}') is None)

# ── the live protocol: building argv ────────────────────────────────────────
cmd = live_backend.build_stt_command(engine="faster-whisper", model="medium",
                                     language="ru", channels=2,
                                     transcript_file=str(tmp / "t.txt"))
check("stt_command_has_channels", "--channels" in cmd and "2" in cmd)
check("stt_command_has_transcript_file", "--transcript-file" in cmd)
try:
    live_backend.build_stt_command(engine="funasr")
    check("stt_command_rejects_batch_only_engine", False, "no exception")
except ValueError:
    check("stt_command_rejects_batch_only_engine", True)

sum_cmd = live_backend.build_summary_command(
    "update", str(tmp / "state.json"), chunk_file=str(tmp / "c.txt"),
    provider="openai", api_key="sk-secret", model="gpt-4o")
check("summary_command_keeps_the_key_out_of_argv",
      "sk-secret" not in " ".join(str(p) for p in sum_cmd),
      "an API key in argv is visible to every process on the machine")
check("summary_command_passes_the_key_in_the_environment",
      sum_cmd.environment.get("MEETING_SUMMARIZER_API_KEY") == "sk-secret")
try:
    live_backend.build_summary_command("nonsense", str(tmp / "s.json"))
    check("summary_command_rejects_unknown_mode", False, "no exception")
except ValueError:
    check("summary_command_rejects_unknown_mode", True)

# ── rendering: everything extracted reaches the panel ───────────────────────
state = {
    "short_summary": "## Релиз\n- перенесли на пятницу",
    "decisions": ["Перенести релиз"],
    "action_items": [{"owner": "Иван", "task": "релиз-ноуты", "deadline": "пт"},
                     {"task": "без владельца"}, {"owner": "никто"}],
    "open_questions": ["Кто ревьюит"],
}
rendered = live_backend.render_summary(state, "Релиз: перенесли")
check("render_keeps_topics", "## Релиз" in rendered)
check("render_shows_decisions", "Перенести релиз" in rendered)
check("render_shows_action_items", "Иван: релиз-ноуты" in rendered
      and "(срок: пт)" in rendered)
check("render_keeps_ownerless_task", "без владельца" in rendered)
check("render_drops_taskless_item", rendered.count("никто") == 0,
      "an action item with no task is noise, not content")
check("render_shows_open_questions", "Кто ревьюит" in rendered)
check("render_shows_delta", "Релиз: перенесли" in rendered)
check("render_of_garbage_is_empty", live_backend.render_summary("nope") == "")

# ── LiveSession end to end against a real engine ────────────────────────────
def pick_live_engine():
    """A model that is actually on disk; sherpa/vosk are the fast ones."""
    for engine, model in (("sherpa-onnx", "sherpa-onnx-small-zipformer-ru-2024-09-18"),
                          ("vosk", "vosk-model-small-ru-0.22"),
                          ("sherpa-onnx", "sherpa-onnx-zipformer-small-en-2023-06-26"),
                          ("vosk", "vosk-model-small-en-us-0.15")):
        if reg.resolve_model_path(engine, model):
            return engine, model
    return "", ""


def speech_pcm():
    """Real speech if the sherpa test wavs are on disk, else a synthetic tone.

    A tone will not transcribe into words, so it only exercises the plumbing;
    real audio also proves text comes out. Both are worth having.
    """
    base = reg.resolve_model_path(
        "sherpa-onnx", "sherpa-onnx-small-zipformer-ru-2024-09-18")
    if base:
        wavs = sorted(Path(base, "test_wavs").glob("*.wav"))
        if wavs:
            out = b"\x00\x00" * RATE
            for path in wavs[:2]:
                with wave.open(str(path), "rb") as fh:
                    out += fh.readframes(fh.getnframes()) + b"\x00\x00" * RATE
            return out, True
    tone = b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * i / RATE)))
                    for i in range(RATE * 2))
    return b"\x00\x00" * RATE + tone + b"\x00\x00" * RATE, False


engine, model = pick_live_engine()
if not engine:
    skip("live_session_transcribes", "no offline ASR model on disk")
    skip("live_session_writes_transcript", "no offline ASR model on disk")
    skip("live_summary_updates_from_live_transcript", "no offline ASR model on disk")
else:
    pcm, is_speech = speech_pcm()
    stem = tmp / "session" / "meeting"
    session = LiveSession()
    seen = []
    statuses = []
    session.segment.connect(seen.append)
    session.status.connect(lambda kind, msg: statuses.append((kind, msg)))
    settings = {"liveEngine": engine, "liveModel": model,
                "transcriptionLanguage": "ru", "whisperDevice": "cpu",
                "liveSummary": False}
    ok = session.start(settings, stem, channels=1, sample_rate=RATE)
    check("live_session_starts", ok, str(statuses[-1:]))
    ready = pump_until(lambda: any(k == "ready" for k, _ in statuses), 180)
    check("live_session_reports_ready", ready, str(statuses[-3:]))
    for i in range(0, len(pcm), 8192):
        session.feed(pcm[i:i + 8192], 1, RATE)
        pump(0.01)
    session.stop()
    pump(1.0)
    check("live_session_transcribes", len(seen) >= 1,
          f"{len(seen)} segment(s); statuses={statuses[-3:]}")
    if is_speech and seen:
        check("live_transcription_produces_words",
              any(len(s.text.split()) >= 2 for s in seen),
              " | ".join(s.text for s in seen)[:120])
    transcript = Path(session.transcript_path)
    check("live_session_writes_transcript",
          transcript.is_file() and transcript.read_text(encoding="utf-8").strip() != "",
          str(transcript))
    if seen:
        check("transcript_file_matches_the_panel",
              seen[0].line in transcript.read_text(encoding="utf-8"))

# ── LiveSession summary scheduling, driven by the fake agent ────────────────
FAKE = str(Path(__file__).resolve().parent / "_fake_live_ai.py")
summary_settings = {
    "liveEngine": "faster-whisper", "liveSummary": True,
    "aiProvider": "agent",
    "agentCommand": f'"{sys.executable}" "{FAKE}"',
    "transcriptionLanguage": "ru", "liveSummaryInterval": 10,
    "liveSummaryStrategy": "regen",
}
session = LiveSession()
emitted = []
session.summary.connect(lambda text, status: emitted.append((text, status)))
stem2 = tmp / "summary" / "meeting"
stem2.parent.mkdir(parents=True, exist_ok=True)
# Drive the summary engine directly: transcription is already covered above and
# a model load would only make this slower without testing anything new.
session._transcript_path = str(stem2) + "_live_transcript.txt"
session._state_path = str(stem2) + "_live_summary.json"
session._chunk_path = str(stem2) + "_live_chunk.txt"
session._recent_path = str(stem2) + "_live_recent.txt"
Path(session._transcript_path).write_text(
    "[00:00:05] MIC: давайте перенесём релиз на пятницу\n", encoding="utf-8")
session._running = True
session._reset_summary_state()
session._configure_summary(summary_settings)
session._last_update = 0.0          # due immediately

check("strategy_regen_forces_rebuild", session._next_mode() == "regen")
session._strategy = "auto"
session._provider = "local"
check("auto_rebuilds_on_a_local_model", session._next_mode() == "regen",
      "local tokens are free, so there is no reason to accept drift")
session._provider = "openai"
check("auto_is_incremental_on_a_cloud_model", session._next_mode() == "update")
session._updates = 7
check("cloud_rebuilds_periodically", session._next_mode() == "regen",
      "an incremental summary must be rebuilt from the source now and then")
session._updates = 4
check("cloud_consolidates_periodically", session._next_mode() == "consolidate")
session._updates = 0
session._strategy = "regen"
session._provider = "agent"

session._append_transcript(live_backend.Segment(
    index=1, start=5.0, duration=3.0, timestamp="[00:00:05]", source="mic",
    text="давайте перенесём релиз на пятницу, релиз-ноуты готовит Иван, "
         "остальное обсудим в понедельник"))
got = pump_until(lambda: any(status == "" and text for text, status in emitted), 120)
check("live_summary_arrives", got, str(emitted[-2:])[:200])
final_text = [text for text, status in emitted if status == "" and text]
check("live_summary_text_rendered",
      bool(final_text) and "Запуск продукта" in final_text[-1],
      (final_text[-1][:80] if final_text else ""))
check("live_summary_state_file_written", Path(session._state_path).is_file())
check("live_summary_counts_updates", session.updates >= 1, str(session.updates))
check("updating_status_never_replaces_the_text",
      all(status.startswith("updating:") is False or text == session.summary_text
          or text == "" for text, status in emitted),
      "the panel text must stay put while an update is in flight")

good = session.summary_text
os.environ["FAKE_LIVE_MODE"] = "garbage"
# The fake reads its mode from the environment of the CHILD, which inherits ours.
session._last_update = 0.0
session._chunk_lines = ["[00:01:00] MIC: " + "ещё текст " * 20]
emitted.clear()
session._maybe_update_summary()
pump_until(lambda: any(status.startswith("error:") for _t, status in emitted), 120)
check("a_failed_update_reports_an_error",
      any(status.startswith("error:") for _t, status in emitted),
      str(emitted[-2:])[:200])
check("a_failed_update_keeps_the_good_summary",
      session.summary_text == good and all(text == good for text, _s in emitted),
      "this is the failure that makes a live summary look broken")
os.environ.pop("FAKE_LIVE_MODE", None)

# ── spend cap ───────────────────────────────────────────────────────────────
session._max_updates = 1
session._updates = 1
session._chunk_lines = ["[00:02:00] MIC: " + "текст " * 40]
emitted.clear()
session._maybe_update_summary()
check("update_limit_stops_further_spending",
      any(status == "limit" for _t, status in emitted), str(emitted)[:160])
check("update_limit_keeps_the_summary_visible",
      all(text == good for text, _s in emitted))
session.stop()
check("stop_is_idempotent", session.stop() is None)
check("scratch_files_cleaned_up",
      not Path(session._chunk_path).exists() and not Path(session._recent_path).exists())
check("transcript_and_state_survive_stop",
      Path(session._transcript_path).is_file() and Path(session._state_path).is_file(),
      "the artifacts are the point; only the scratch goes away")

# ── recorder dialog wiring ─────────────────────────────────────────────────
saved = {}
dlg = RecorderDialog(str(tmp), language="ru",
                     settings={"liveTranscription": False, "liveSummary": False,
                               "recordSystemAudio": False},
                     on_settings_changed=saved.update)
check("dialog_hides_live_panels_when_off", not dlg.tabs.isVisible())
check("dialog_summary_disabled_without_live", not dlg.chk_summary.isEnabled())
dlg.chk_live.setChecked(True)
check("dialog_persists_live_toggle", saved.get("liveTranscription") is True)
check("dialog_summary_enabled_with_live", dlg.chk_summary.isEnabled())
dlg.chk_summary.setChecked(True)
check("dialog_persists_summary_toggle", saved.get("liveSummary") is True)
dlg.chk_live.setChecked(False)
check("dialog_turns_summary_off_with_live", not dlg.chk_summary.isChecked(),
      "no transcript stream means no summary input")
check("dialog_saves_summary_off_too", saved.get("liveSummary") is False)
dlg._on_live_summary("сводка", "updating:regen")
check("dialog_shows_a_separate_updating_line",
      dlg.txt_summary.toPlainText() == "сводка"
      and "пересборка" in dlg.lbl_live_status.text().lower(),
      dlg.lbl_live_status.text())
dlg._on_live_summary("сводка", "error:боль")
check("dialog_keeps_text_on_error", dlg.txt_summary.toPlainText() == "сводка")
check("dialog_reports_the_error", "боль" in dlg.lbl_live_status.text())
dlg._on_segment(live_backend.Segment(
    index=1, start=1.0, duration=1.0, timestamp="[00:00:01]", source="mic",
    text="здравствуйте"))
check("dialog_appends_transcript_lines",
      "[00:00:01] [MIC]: здравствуйте" in dlg.txt_transcript.toPlainText())

# ── "process from live text": offered only when there is text to process ────
check("live_processing_hidden_before_a_recording", not dlg.btn_use_live.isVisible())
dlg._live._transcript_path = str(tmp / "no_such_transcript.txt")
dlg._offer_live_processing()
check("live_processing_not_offered_without_a_transcript",
      not dlg.btn_use_live.isEnabled() and dlg.live_transcript_path == "")

empty_live = tmp / "empty_live_transcript.txt"
empty_live.write_text("   \n", encoding="utf-8")
dlg._live._transcript_path = str(empty_live)
dlg._offer_live_processing()
check("live_processing_not_offered_for_an_empty_transcript",
      not dlg.btn_use_live.isEnabled(),
      "queuing an empty transcript would fail two stages later")

real_live = tmp / "real_live_transcript.txt"
real_live.write_text("[00:00:05] [MIC]: обсудили релиз\n", encoding="utf-8")
dlg._live._transcript_path = str(real_live)
dlg._offer_live_processing()
check("live_processing_offered_for_a_real_transcript", dlg.btn_use_live.isEnabled())
check("live_transcript_path_exposed", dlg.live_transcript_path == str(real_live))
check("live_processing_is_opt_in", dlg.use_live_transcript is False,
      "the plain Process button must keep transcribing the file")
dlg._accept_with_live_transcript()
check("live_processing_button_sets_the_flag", dlg.use_live_transcript is True)

# ── the window hands it to the queue as a normal meeting ────────────────────
from app.core.history import HistoryStore                    # noqa: E402
from app.ui.main_window import MainWindow                    # noqa: E402

mw_root = tmp / "window"
mw_root.mkdir()
store = HistoryStore(path=mw_root / "history.json",
                     transcripts_root=mw_root / "transcripts")


class _QueueSpy:
    def __init__(self):
        self.regenerated = []
        self.enqueued = []

    def enqueue(self, entry_id, video_path):
        self.enqueued.append((entry_id, video_path))

    def enqueue_regenerate(self, entry_id, video_path, transcript_path, scope="both"):
        self.regenerated.append((entry_id, video_path, transcript_path, scope))

    def set_max_concurrency(self, n):
        """The window's CUDA probe calls this when it lands."""


# The refusal path opens a modal, which would block a headless run for ever.
# Capture it instead: what is under test is that it refuses, and says so.
from PySide6.QtWidgets import QMessageBox                      # noqa: E402
_warnings = []
QMessageBox.warning = staticmethod(
    lambda *args, **kwargs: _warnings.append(args[2] if len(args) > 2 else ""))


spy = _QueueSpy()
# Built without a queue and given the spy afterwards: MainWindow wires Qt signals
# on a real PipelineQueue at construction time, and what is under test here is
# what _add_live_recording HANDS to the queue, not the queue itself.
window = MainWindow({"language": "ru", "theme": "dark"}, store)
window.queue = spy
recording = mw_root / "Запись 2026-08-21 10-00-00.wav"
recording.write_bytes(b"not a real wav")
window._add_live_recording(str(recording), str(real_live))

check("live_recording_is_queued_without_transcription",
      len(spy.regenerated) == 1 and not spy.enqueued, str(spy.regenerated))
if spy.regenerated:
    live_entry_id, _video, queued_transcript, scope = spy.regenerated[0]
    check("live_recording_runs_summary_and_analysis", scope == "both", scope)
    queued = Path(queued_transcript)
    check("live_transcript_lands_in_the_job_folder_under_the_standard_name",
          queued.name.endswith("_raw.txt") and queued.parent == store.job_dir(live_entry_id),
          str(queued))
    check("live_transcript_content_is_carried_over",
          queued.read_text(encoding="utf-8") == real_live.read_text(encoding="utf-8"))
    live_entry = store.get(live_entry_id)
    check("live_transcript_is_recorded_on_the_entry",
          Path(live_entry.transcript_path or "") == queued, str(live_entry.transcript_path))
    check("live_recording_appears_in_history", live_entry.video_path == str(recording))

before = len(spy.regenerated)
blank = tmp / "blank_live.txt"
blank.write_text("\n", encoding="utf-8")
_warnings.clear()
window._add_live_recording(str(recording), str(blank))
check("an_empty_live_transcript_is_refused_not_queued",
      len(spy.regenerated) == before, str(spy.regenerated[before:]))
check("refusing_an_empty_live_transcript_says_why", bool(_warnings), str(_warnings))

_warnings.clear()
window._add_live_recording(str(recording), str(tmp / "does_not_exist.txt"))
check("a_missing_live_transcript_is_refused_not_queued",
      len(spy.regenerated) == before and bool(_warnings), str(_warnings))

# ── the intake channel is recorded and visible ──────────────────────────────
from app.core.models import SOURCE_FILE, SOURCE_LIVE, source_badge, source_label  # noqa: E402

if spy.regenerated:
    live_entry = store.get(spy.regenerated[0][0])
    check("live_meeting_is_marked_as_the_live_channel",
          live_entry.source == SOURCE_LIVE, live_entry.source)
    file_id = store.add(str(recording), "1m", "1 MB")
    check("a_dropped_file_stays_the_file_channel",
          store.get(file_id).source == SOURCE_FILE, store.get(file_id).source)
    check("the_channel_survives_a_reload",
          [e.source for e in store.load() if e.id == live_entry.id] == [SOURCE_LIVE])
    check("a_legacy_entry_without_a_channel_reads_as_file",
          source_label({}.get("source"), "ru") == source_label(SOURCE_FILE, "ru"),
          "entries written before this release must not read as 'unknown'")
    check("only_the_live_channel_gets_a_badge",
          source_badge(SOURCE_LIVE) and not source_badge(SOURCE_FILE),
          "a badge on every row would be noise, not information")

    window.add_job_row(live_entry)
    live_row = window._rows[live_entry.id]
    cell = window.table.item(live_row, window.COL_FILE)
    check("the_queue_row_shows_the_live_badge",
          source_badge(SOURCE_LIVE) in cell.text(), cell.text()[:60])
    check("the_queue_row_tooltip_names_the_channel",
          source_label(SOURCE_LIVE, "ru") in cell.toolTip(), cell.toolTip()[:60])
    window.add_job_row(store.get(file_id))
    file_cell = window.table.item(window._rows[file_id], window.COL_FILE)
    check("a_file_row_carries_no_badge",
          not file_cell.text().startswith("●"), file_cell.text()[:40])

    window._load_results(live_entry.id)
    check("the_results_panel_names_the_channel",
          source_label(SOURCE_LIVE, "ru") in window.lbl_source.text(),
          window.lbl_source.text())
    window.language = "en"
    window._retranslate_source_label()
    check("the_channel_follows_the_ui_language",
          source_label(SOURCE_LIVE, "en") in window.lbl_source.text(),
          window.lbl_source.text())
    window.language = "ru"

# ── the journal records the channel too ────────────────────────────────────
from app.core.run_history import RunHistoryStore                # noqa: E402

journal = RunHistoryStore(path=mw_root / "processing_history.json")
rec_live = journal.new_run(entry_id=1, video_name="live.wav", kind="summary+analysis",
                           source=SOURCE_LIVE)
rec_file = journal.new_run(entry_id=2, video_name="file.mkv", kind="full")
check("journal_records_the_live_channel", rec_live.get("source") == SOURCE_LIVE)
check("journal_defaults_to_the_file_channel", rec_file.get("source") == SOURCE_FILE,
      str(rec_file.get("source")))
check("journal_channel_survives_a_reload",
      sorted(r.get("source") for r in journal.load()) == [SOURCE_FILE, SOURCE_LIVE],
      str([r.get("source") for r in journal.load()]))

from app.ui.history_dialog import HistoryDialog                 # noqa: E402

journal_dlg = HistoryDialog(journal, language="ru")
rows = {journal_dlg.table.item(r, journal_dlg.COL_FILE).text()
        for r in range(journal_dlg.table.rowCount())}
check("journal_window_marks_the_live_run",
      any(source_badge(SOURCE_LIVE) in text for text in rows), str(rows))
check("journal_window_leaves_a_file_run_unmarked",
      any(text == "file.mkv" for text in rows), str(rows))

print("\n".join(results))
failed = [r for r in results if r.startswith("FAIL")]
skipped = [r for r in results if r.startswith("SKIP")]
print(f"SUMMARY {'HAS_FAILURES' if failed else 'ALL_PASS'} "
      f"({len(results)} checks, {len(skipped)} skipped)")
sys.exit(1 if failed else 0)
