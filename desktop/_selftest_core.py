"""Self-test for the id-centric core (models + history). Uses temp files only;
the real config/history.json is never touched. Run with bundled python:
    backend\\python\\python.exe desktop\\_selftest_core.py
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

results = []


def check(name, fn):
    try:
        detail = fn()
        results.append(f"PASS  {name}  {detail or ''}".rstrip())
    except Exception as exc:  # noqa: BLE001
        results.append(f"FAIL  {name}  {exc!r}")
        results.append("      " + traceback.format_exc().replace("\n", "\n      "))


def _naming():
    from app.core.history import versioned_filename as vf
    assert vf("meeting", "", 1, ".txt") == "meeting.txt", vf("meeting", "", 1, ".txt")
    assert vf("meeting", "summary", 1, ".md") == "meeting_summary.md"
    assert vf("meeting", "summary", 2, ".md") == "meeting_summary_v2.md"
    assert vf("meeting", "analysis", 3, "json") == "meeting_analysis_v3.json"
    return "transcript/summary/analysis names + version suffix ok"


def _id_centric_versioning():
    from app.core.history import HistoryStore
    with tempfile.TemporaryDirectory() as d:
        store = HistoryStore(path=Path(d) / "history.json",
                             transcripts_root=Path(d) / "transcripts")
        id1 = store.add("C:/x/meeting.mkv", "5m 49s", "18.2 MB")
        id2 = store.add("C:/x/meeting.mkv", "5m 49s", "18.2 MB")  # same file again
        assert id1 != id2, (id1, id2)

        store.set_status(id1, "transcribing")
        store.set_transcript(id1, Path(d) / "transcripts" / str(id1) / "meeting.txt")
        assert store.add_summary_version(id1, "s1.md", provider="local", model="qwen") == 1
        assert store.add_summary_version(id1, "s2.md", provider="openai", model="gpt-4o") == 2
        assert store.add_analysis_version(id1, "a1.json", "local", "qwen") == 1
        assert store.add_analysis_version(id1, "a2.json", "openai", "gpt-4o") == 2

        # reload from disk -> persistence is real, not in-memory
        store2 = HistoryStore(path=Path(d) / "history.json",
                              transcripts_root=Path(d) / "transcripts")
        e = store2.get(id1)
        assert e is not None
        assert len(e.summary_versions) == 2 and len(e.analysis_versions) == 2
        assert e.summary_path == "s2.md", e.summary_path  # mirror = latest
        assert e.status == "transcribing"
        assert e.summary_versions[1].model == "gpt-4o"
        return f"id1={id1} id2={id2}; 2 summary + 2 analysis versions persisted"


def _preserves_unknown_keys():
    from app.core.history import HistoryStore
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "history.json"
        # Pre-seed a raw entry containing a key the model doesn't know about.
        p.write_text(json.dumps([{
            "id": 111, "videoPath": "C:/x/a.mkv", "videoName": "a.mkv",
            "customElectronField": "keep-me",
        }]), encoding="utf-8")
        store = HistoryStore(path=p, transcripts_root=Path(d) / "t")
        store.set_status(111, "done")  # triggers load->modify->save round-trip
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert raw[0].get("customElectronField") == "keep-me", raw[0]
        assert raw[0].get("status") == "done"
        return "unknown Electron key survived round-trip"


def _labels():
    from app.core import models as m
    assert m.stage_to_status("status.transcribing") == m.JobStatus.TRANSCRIBING
    assert m.main_label(m.JobStatus.SUMMARIZING, "ru") == "Создание саммари…"
    assert m.main_label(m.JobStatus.ANALYZING, "en") == "Deep analysis…"
    return "stage mapping + ru/en labels ok"



def _gpu_handoff_covers_the_builtin_model():
    """The hand-off must free the app's OWN model too, not only the user's server.

    It used to stop whatever listened on ``llamaPort`` and nothing else, so the
    built-in llama.cpp on its own port kept holding VRAM through the entire
    transcription. Driven with a stub ``local_ai`` so no real model is loaded.
    """
    import types
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    import gpu_handoff as G

    calls = []
    stub = types.SimpleNamespace(
        DEFAULT_PORT=8081,
        status=lambda port=8081: {"running": True, "port": 8081, "model_id": "m1"},
        stop=lambda port=8081: calls.append(("stop", port)) or True,
        start=lambda mid, port=8081, wait=0: calls.append(("start", mid, port)),
    )
    original_local_ai, original_listening = G._local_ai, G._port_listening
    tmp_lock = Path(tempfile.mkdtemp()) / "GPU.lock"
    original_lock = G.LOCK_FILE
    try:
        G._local_ai = lambda: stub
        G.LOCK_FILE = tmp_lock
        # No external server anywhere; only ours is up (and dies when stopped).
        G._port_listening = lambda port: False
        assert G.acquire(port=8080, settle=0) is True, "built-in alone must count as a hand-off"
        assert ("stop", 8081) in calls, calls
        lock = json.loads(tmp_lock.read_text(encoding="utf-8"))
        assert lock["builtin"] == {"port": 8081, "model_id": "m1"}, lock
        assert lock["port"] == 0, lock          # nothing external was recorded
        assert G.release(grace=1.0) is True
        assert ("start", "m1", 8081) in calls, calls
        # Nothing of ours and nothing external -> nothing was taken.
        stub.status = lambda port=8081: {"running": False}
        assert G.acquire(port=8080, settle=0) is False
        # ...and "nothing to unload" must be distinguishable from "could not
        # unload". Reporting both as a failure made every cloud-provider run show
        # "Выгрузка локальной LLM - не удалось" in the status timeline.
        assert G.acquire_status(port=8080, settle=0) == "idle"
        # A port that keeps listening after the kill IS a real failure.
        G._port_listening = lambda port: True
        G._capture_cmdlines = lambda port: []
        G._kill_listeners = lambda port: None
        assert G.acquire_status(port=8080, settle=0) == "stuck"
        # And a model that really goes away is "freed".
        stub.status = lambda port=8081: {"running": True, "port": 8081, "model_id": "m1"}
        G._port_listening = lambda port: False
        assert G.acquire_status(port=8080, settle=0) == "freed"
        assert G.acquire(port=8080, settle=0) is True, "acquire() still means 'held'"
    finally:
        G._local_ai, G._port_listening, G.LOCK_FILE = (
            original_local_ai, original_listening, original_lock)
    return "built-in model stopped and restarted with the hand-off"


check("artifact_naming", _naming)
check("id_centric_versioning", _id_centric_versioning)
check("preserves_unknown_keys", _preserves_unknown_keys)
check("status_labels", _labels)
check("gpu_handoff_covers_the_builtin_model",
      _gpu_handoff_covers_the_builtin_model)

print("\n".join(results))
print("SUMMARY " + ("ALL_PASS" if all(r.startswith("PASS") for r in results)
                     else "HAS_FAILURES"))
sys.exit(0 if results and not any(r.startswith("FAIL") for r in results) else 1)
