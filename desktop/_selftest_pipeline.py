"""Full-pipeline self-test: 2 jobs (same file twice) at concurrency 2, using
fakes for processor.py and ai_client.py. Proves the complete status lifecycle,
id-keyed versioning, artifact naming, and no cross-talk. Real Qt loop, no GUI.
"""
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from app import paths  # noqa: E402
from app.core.history import HistoryStore  # noqa: E402
from app.core.models import JobStatus  # noqa: E402
from app.core.pipeline import JobRunner, PipelineQueue  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


HERE = Path(__file__).resolve().parent
PY = str(paths.python_executable())
FAKE_PROC = str(HERE / "_fake_processor_cli.py")
FAKE_AI = str(HERE / "_fake_ai_cli.py")

app = QCoreApplication.instance() or QCoreApplication(sys.argv)

tmp = tempfile.TemporaryDirectory()
root = Path(tmp.name)
video = root / "meeting.mkv"
video.write_bytes(b"not a real video")

store = HistoryStore(path=root / "history.json", transcripts_root=root / "transcripts")
settings = {
    "transcriptionEngine": "faster-whisper", "whisperModel": "medium",
    "transcriptionLanguage": "ru", "whisperDevice": "auto",
    "analysisSource": "transcript",
    "aiProvider": "local", "apiKey": "", "localEndpoint": "http://localhost:1234/v1",
    "prompt": "Сделай структурированное саммари встречи по транскрипции.",
    # analysis toggles: enable all five so every feature pass runs
    "extractActionItems": True, "analyzeSentiment": True,
    "categorizeAutomatically": True, "generateFollowupQuestions": True,
    "generateFormalProtocol": True,
}
def factory(entry_id, video_path):
    return JobRunner(entry_id, video_path, settings, store,
                     python_exe=PY, processor_script=FAKE_PROC, ai_client_script=FAKE_AI)


queue = PipelineQueue(max_concurrency=2, runner_factory=factory)

status_seq: dict[int, list] = {}
finished: dict[int, tuple] = {}
max_active = {"v": 0}

queue.active_changed.connect(lambda n: max_active.update(v=max(max_active["v"], n)))
queue.status_changed.connect(
    lambda jid, st: status_seq.setdefault(jid, []).append(st))
queue.job_finished.connect(lambda jid, ok, err: finished.__setitem__(jid, (ok, err)))
queue.all_done.connect(lambda: QTimer.singleShot(0, app.quit))

ids = [store.add(str(video), "5m 49s", "18.2 MB"),
       store.add(str(video), "5m 49s", "18.2 MB")]  # same file twice -> 2 ids
for entry_id in ids:
    queue.enqueue(entry_id, str(video))

# Safety net so a hang cannot block the suite forever. It must be generous: the
# two jobs spawn real subprocesses, and when this runs right after the rest of the
# suite the machine is loaded — a tight cap made this test flake and produced
# misleading assertion failures. If it DOES trip, say so explicitly instead of
# letting the checks below report a phantom pipeline bug.
timed_out = {"v": False}


def _safety_timeout():
    timed_out["v"] = True
    app.quit()


QTimer.singleShot(180000, _safety_timeout)

try:
    app.exec()

    check("completed_without_safety_timeout", not timed_out["v"],
          "safety timeout fired - machine too slow/loaded, not a pipeline failure")
    check("two_distinct_ids", ids[0] != ids[1], f"{ids}")
    check("both_finished_ok",
          all(finished.get(i, (False,))[0] for i in ids), f"{finished}")
    check("concurrency_cap_held", max_active["v"] == 2, f"peak={max_active['v']}")

    expected = [JobStatus.EXTRACTING, JobStatus.TRANSCRIBING,
                JobStatus.SUMMARIZING, JobStatus.ANALYZING, JobStatus.DONE]
    seq_ok = all(status_seq.get(i) == expected for i in ids)
    check("status_lifecycle_in_order", seq_ok,
          "" if seq_ok else f"{ {i: [s.value for s in status_seq.get(i, [])] for i in ids} }")

    # versioning + artifacts per id
    art_ok = True
    detail = ""
    for i in ids:
        e = store.get(i)
        sv = e.summary_versions
        av = e.analysis_versions
        if not (e.transcript_path and len(sv) == 1 and len(av) == 1):
            art_ok = False
            detail = f"id={i} transcript={bool(e.transcript_path)} sv={len(sv)} av={len(av)}"
            break
        if av[0].source_summary_version != 0:
            art_ok = False
            detail = ("transcript-sourced analysis was falsely linked to summary "
                      f"v{av[0].source_summary_version}")
            break
        sp, ap = Path(sv[0].path), Path(av[0].path)
        if sp.name != "meeting_summary.txt" or ap.name != "meeting_analysis.json":
            art_ok = False
            detail = f"names: {sp.name}, {ap.name}"
            break
        if "[SUMMARY]" not in sp.read_text(encoding="utf-8"):
            art_ok = False
            detail = "summary file missing SUMMARY marker"
            break
        try:
            adata = __import__("json").loads(ap.read_text(encoding="utf-8"))
        except ValueError:
            art_ok = False
            detail = "analysis file is not valid JSON"
            break
        if not isinstance(adata, dict) or "characteristics" not in adata:
            art_ok = False
            detail = f"analysis JSON missing schema keys: {type(adata).__name__}"
            break
        ai_items = adata.get("actionItems")
        if not (isinstance(ai_items, list) and ai_items
                and ai_items[0].get("task") == "demo"):
            art_ok = False
            detail = f"analysis actionItems not merged from feature passes: {ai_items}"
            break
    check("artifacts_and_versioning", art_ok, detail)

    # each job gets its own artifact folder (named by the file, deduped on
    # collision), and the two are distinct — no cross-contamination.
    d0, d1 = store.job_dir(ids[0]), store.job_dir(ids[1])
    dirs_ok = d0.exists() and d1.exists() and d0 != d1 and ids[0] != ids[1]
    check("per_job_folders", dirs_ok, f"{d0.name} / {d1.name}")

    # ── a live-sourced meeting must be indistinguishable in what it produces ──
    # Live transcription gives the transcript a second way in. That must change
    # WHERE the text came from and nothing else: same artifact set, same names,
    # same versioning. Otherwise "how was this recorded" leaks into every
    # downstream consumer — the exports, Obsidian, the version pickers.
    live_recording = root / "live meeting.wav"
    live_recording.write_bytes(b"not a real wav")
    live_id = store.add(str(live_recording), "3m 12s", "5.8 MB", source="live")
    live_raw = store.job_dir(live_id) / f"{Path(live_recording).stem}_raw.txt"
    live_raw.parent.mkdir(parents=True, exist_ok=True)
    # Exactly what backend/live_stt.py writes during a two-source recording.
    live_raw.write_text(
        "[00:00:05] [MIC]: давайте перенесём релиз на пятницу\n"
        "[00:00:19] [SYSTEM]: согласны, релиз-ноуты за Иваном\n",
        encoding="utf-8")
    store.set_transcript(live_id, live_raw)

    live_queue = PipelineQueue(max_concurrency=1, runner_factory=factory)
    live_statuses = []
    live_result = {}
    live_queue.status_changed.connect(lambda _j, st: live_statuses.append(st))
    live_queue.job_finished.connect(
        lambda jid, ok, err: live_result.update(id=jid, ok=ok, error=err))
    live_queue.all_done.connect(lambda: QTimer.singleShot(0, app.quit))
    live_queue.enqueue_regenerate(live_id, str(live_recording), str(live_raw), "both")
    live_timed_out = {"v": False}
    QTimer.singleShot(120000, lambda: (live_timed_out.update(v=True), app.quit()))
    app.exec()

    check("live_sourced_job_finished_ok",
          not live_timed_out["v"] and live_result.get("ok") is True,
          str(live_result))
    live_entry = store.get(live_id)
    batch_entry = store.get(ids[0])
    check("live_sourced_job_never_transcribes",
          JobStatus.TRANSCRIBING not in live_statuses
          and JobStatus.EXTRACTING not in live_statuses,
          str([s.value for s in live_statuses]))
    check("live_sourced_job_still_summarises_and_analyses",
          JobStatus.SUMMARIZING in live_statuses
          and JobStatus.ANALYZING in live_statuses
          and live_statuses[-1] == JobStatus.DONE,
          str([s.value for s in live_statuses]))
    check("live_sourced_artifact_count_matches_a_batch_job",
          (len(live_entry.summary_versions), len(live_entry.analysis_versions))
          == (len(batch_entry.summary_versions), len(batch_entry.analysis_versions)),
          f"live={len(live_entry.summary_versions)}/{len(live_entry.analysis_versions)} "
          f"batch={len(batch_entry.summary_versions)}/{len(batch_entry.analysis_versions)}")
    live_names = sorted(Path(v.path).name.split("_", 1)[1]
                        for v in (list(live_entry.summary_versions)
                                  + list(live_entry.analysis_versions)))
    batch_names = sorted(Path(v.path).name.split("_", 1)[1]
                         for v in (list(batch_entry.summary_versions)
                                   + list(batch_entry.analysis_versions)))
    check("live_sourced_artifact_names_match_a_batch_job",
          live_names == batch_names, f"{live_names} vs {batch_names}")
    check("live_transcript_is_recorded_on_the_entry",
          Path(live_entry.transcript_path or "").name.endswith("_raw.txt"),
          str(live_entry.transcript_path))
    check("live_sourced_analysis_is_real",
          Path(live_entry.analysis_versions[0].path).is_file()
          and "characteristics" in __import__("json").loads(
              Path(live_entry.analysis_versions[0].path).read_text(encoding="utf-8")))
    check("live_meeting_is_marked_as_the_live_channel",
          live_entry.source == "live", live_entry.source)
    check("a_batch_meeting_stays_the_file_channel",
          batch_entry.source == "file", batch_entry.source)

    # Diagnostics must work for a live meeting too: the profile is written and
    # renders the stages that DID run. Transcription is absent because it really
    # did not run in this job — the text was recognised during the meeting.
    from app.core import trace as trace_mod
    live_trace_path = trace_mod.find_trace(store.job_dir(live_id))
    check("live_sourced_job_writes_a_profile", bool(live_trace_path),
          str(live_trace_path))
    if live_trace_path:
        live_layout = trace_mod.layout(trace_mod.load_trace(live_trace_path) or {})
        bar_names = [b["name"] for b in live_layout["bars"]]
        check("live_profile_renders_bars", len(live_layout["bars"]) >= 2, str(bar_names))
        # Compared against the pipeline's OWN stage names, so the check does not
        # quietly encode one language's wording.
        from app.core.pipeline import _STAGE_NAME
        stage_ru = _STAGE_NAME["ru"]
        check("live_profile_covers_the_stages_that_ran",
              stage_ru[JobStatus.SUMMARIZING] in bar_names
              and stage_ru[JobStatus.ANALYZING] in bar_names, str(bar_names))
        check("live_profile_claims_no_transcription",
              stage_ru[JobStatus.TRANSCRIBING] not in bar_names
              and stage_ru[JobStatus.EXTRACTING] not in bar_names,
              f"a stage that did not run must not appear: {bar_names}")

    # A partially failed requested analysis must never produce a green job.
    partial_id = store.add(str(video), "5m 49s", "18.2 MB")
    partial = factory(partial_id, str(video))
    partial._analysis_results = {"characteristics": {}}
    partial._analysis_from_transcript = True
    partial._summary_version = 1
    partial._total_features = 2
    partial._failed_features = 1
    partial._analysis_errors = ["Категория: provider quota exceeded"]
    partial_fail = {"message": ""}
    partial._fail = lambda message: partial_fail.update(message=message)
    partial._finish_ok = lambda: partial_fail.update(message="FALSE_SUCCESS")
    partial._finish_analysis()
    check("partial_analysis_is_not_green",
          partial_fail["message"] and partial_fail["message"] != "FALSE_SUCCESS",
          partial_fail["message"])

    # Closing a failed summary stage must emit a failed timeline item, never a
    # check-marked successful "Creating summary" entry.
    failed_stage_id = store.add(str(video), "5m 49s", "18.2 MB")
    failed_stage = factory(failed_stage_id, str(video))
    failed_stage_events = []
    failed_stage.stage_done.connect(
        lambda _jid, label, _secs: failed_stage_events.append(label))
    failed_stage._set_status(JobStatus.SUMMARIZING)
    failed_stage.completed.connect(lambda *_args: None)
    failed_stage._fail("provider quota")
    check("failed_summary_timeline_is_not_success",
          len(failed_stage_events) == 1
          and failed_stage_events[0].startswith("✖ "),
          f"events={len(failed_stage_events)}")

    # An empty transcription must reach the user in HIS language, naming the
    # cause. The backend tags it; echoing the raw tag would be no better than the
    # "No text provided or text is empty" that sent the owner looking at the AI.
    xl = factory(store.add(str(video), "5m 49s", "18.2 MB"), str(video))
    silent_err = ("SILENT_AUDIO: no speech recognised because the audio track is "
                  "silent (peak -91.0 dBFS). ...")
    for lang, needle in (("ru", "нет звука"), ("en", "no sound")):
        xl._lang = lang
        shown = xl._explain_tx_error(silent_err)
        check(f"silent_audio_is_explained_in_{lang}",
              needle in shown and "SILENT_AUDIO" not in shown, shown[:70])
    xl._lang = "ru"
    check("silent_audio_keeps_the_measured_level",
          "-91.0" in xl._explain_tx_error(silent_err),
          xl._explain_tx_error(silent_err)[:80])
    no_speech = xl._explain_tx_error("NO_SPEECH: the audio was read but no speech ...")
    check("no_speech_is_explained_separately",
          "язык транскрибации" in no_speech and "NO_SPEECH" not in no_speech,
          no_speech[:70])
    # Anything else must pass through untouched — this must not swallow errors.
    check("other_errors_are_not_rewritten",
          xl._explain_tx_error("CUDA out of memory") == "CUDA out of memory")
    check("a_missing_error_still_says_something",
          bool(xl._explain_tx_error("")), xl._explain_tx_error(""))

    # A cancelled run has its subprocess killed, so processor.py never reaches its
    # own cleanup: one cancelled meeting left 328 MB of extracted audio on disk
    # (a 171 MB WAV + nine chunks). The sweep must take those and NOTHING else.
    sweep_id = store.add(str(video), "1m", "1 MB")
    sweep_dir = store.job_dir(sweep_id)
    sweep_stem = Path(video).stem
    (sweep_dir / f"{sweep_stem}_temp.wav").write_bytes(b"0" * 64)
    for n in range(3):
        (sweep_dir / f"{sweep_stem}_temp_chunk_{n}.wav").write_bytes(b"0" * 32)
    (sweep_dir / f"{sweep_stem}_raw.txt").write_text("transcript", encoding="utf-8")
    (sweep_dir / f"{sweep_stem}_summary.txt").write_text("summary", encoding="utf-8")
    JobRunner(sweep_id, str(video), settings, store)._sweep_temp_audio(delay_ms=0)
    left = sorted(p.name for p in sweep_dir.iterdir())
    check("cancel_sweeps_every_temp_wav",
          not any(name.endswith(".wav") for name in left), str(left))
    check("cancel_sweep_keeps_the_artifacts",
          left == [f"{sweep_stem}_raw.txt", f"{sweep_stem}_summary.txt"], str(left))
except Exception as exc:  # noqa: BLE001
    results.append(f"FAIL  harness  {exc!r}")
    results.append("      " + traceback.format_exc().replace("\n", "\n      "))
finally:
    tmp.cleanup()

print("\n".join(results))
print("SUMMARY " + ("ALL_PASS" if all(r.startswith("PASS") for r in results)
                    else "HAS_FAILURES"))
sys.exit(0 if results and not any(r.startswith("FAIL") for r in results) else 1)
