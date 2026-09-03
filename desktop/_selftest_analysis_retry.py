"""One flaky analysis feature must not cost the meeting its run.

A model is sampled, not deterministic: the same feature prompt that returns a
clean JSON array on one pass returns something unparsable on the next. That
used to end the job — "Анализ выполнен не полностью: ошибок 1 из 11" at 94% —
and with it the Obsidian note and the Google Sheets row, even though the other
ten features had succeeded. Each feature now gets one second attempt.

Covered here, end to end through the real pipeline with the fake AI CLI:

* an unparsable first answer is retried and the run finishes green;
* every feature's result is present in the artifact after the retry;
* a feature that fails BOTH attempts still fails the run (the schema check is
  not weakened — only given a second chance);
* the raw unparsable answer is kept next to the artifacts for diagnosis.

Run:
    backend\\python\\python.exe desktop\\_selftest_analysis_retry.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PySide6.QtCore import QCoreApplication, QTimer     # noqa: E402

from app import paths                                    # noqa: E402
from app.core.history import HistoryStore                # noqa: E402
from app.core.pipeline import JobRunner, PipelineQueue   # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")


app = QCoreApplication.instance() or QCoreApplication(sys.argv)
PY = str(paths.python_executable())
FAKE_PROC = str(HERE / "_fake_processor_cli.py")
FAKE_AI = str(HERE / "_fake_ai_cli.py")

CONF = {
    "transcriptionEngine": "faster-whisper", "whisperModel": "medium",
    "transcriptionLanguage": "ru", "whisperDevice": "auto",
    "analysisSource": "transcript", "aiProvider": "local",
    "localEndpoint": "http://localhost:1234/v1",
    "prompt": "Сделай саммари.",
    # Keep the feature set small and predictable: actionItems + risks + questions
    # + recommendations come from this one toggle.
    "extractActionItems": True,
}


def run_pipeline(unparsable_answers: int):
    """Process one file with the first *n* analysis answers made unparsable.

    Returns ``(ok, analysis_dict, job_dir)``.
    """
    root = Path(tempfile.mkdtemp())
    video = root / "2026-08-17 15-33-43.mkv"
    video.write_bytes(b"not a real video")
    counter = root / "flaky.count"
    store = HistoryStore(path=root / "history.json",
                         transcripts_root=root / "transcripts")
    conf = dict(CONF)
    conf["advancedSettings"] = {}
    os.environ["FAKE_AI_FLAKY_FILE"] = str(counter)
    os.environ["FAKE_AI_FLAKY_CALLS"] = str(unparsable_answers)
    outcome = {}
    try:
        queue = PipelineQueue(
            1, lambda i, v: JobRunner(i, v, conf, store, python_exe=PY,
                                      processor_script=FAKE_PROC,
                                      ai_client_script=FAKE_AI))
        queue.job_finished.connect(
            lambda jid, ok, msg: outcome.update({"ok": ok, "msg": msg}))
        queue.all_done.connect(lambda: QTimer.singleShot(0, app.quit))
        entry_id = store.add(str(video), "12м 47с", "18.2 MB")
        queue.enqueue(entry_id, str(video))
        QTimer.singleShot(120000, app.quit)
        app.exec()
    finally:
        os.environ.pop("FAKE_AI_FLAKY_FILE", None)
        os.environ.pop("FAKE_AI_FLAKY_CALLS", None)
    entry = store.get(entry_id)
    analysis = {}
    if entry and entry.analysis_versions:
        analysis = json.loads(
            Path(entry.analysis_versions[-1].path).read_text(encoding="utf-8"))
    return outcome.get("ok"), analysis, store.job_dir(entry_id)


# ── the baseline: nothing flaky, the run is green ───────────────────────────
ok, analysis, _ = run_pipeline(0)
check("baseline_run_succeeds", ok is True)
check("baseline_produced_action_items", bool(analysis.get("actionItems")),
      str(analysis.get("actionItems"))[:80])

# ── one unparsable answer: retried, and the run still finishes green ────────
ok, analysis, job_dir = run_pipeline(1)
check("one_unparsable_answer_does_not_fail_the_run", ok is True)
check("the_retried_feature_is_present_after_the_retry",
      bool(analysis.get("actionItems")), str(analysis.get("actionItems"))[:80])
check("no_feature_was_left_empty_by_the_retry",
      all(analysis.get(key) for key in ("actionItems", "risks", "questions",
                                        "recommendations")),
      str({k: len(analysis.get(k) or []) for k in
           ("actionItems", "risks", "questions", "recommendations")}))
check("the_unparsable_answer_was_kept_for_diagnosis",
      any(p.name.startswith("2026-08-17 15-33-43_failed_")
          for p in Path(job_dir).glob("*.txt")),
      str([p.name for p in Path(job_dir).glob("*.txt")]))

# ── both attempts unparsable: the run still fails (schema check intact) ─────
ok, analysis, _ = run_pipeline(2)
check("two_unparsable_answers_still_fail_the_run", ok is False)

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
