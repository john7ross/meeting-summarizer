"""Server core self-test: reused pipeline command building + SQLite DB round-trip.

Run with the SERVER venv from the repo root:
    set SERVER_MODE=true
    server\\.venv\\Scripts\\python.exe server\\_selftest_core.py
"""
import os
import re
import sys
import asyncio
import tempfile
from types import SimpleNamespace
from datetime import datetime, timedelta
from pathlib import Path

os.environ["SERVER_MODE"] = "true"
# Isolated temp DB so the test never touches config/server.db.
_tmpdb = Path(tempfile.mkdtemp()) / "t.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdb.as_posix()}"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.processing import worker as W
from server.database.db import init_db, AsyncSessionLocal
from server.database.models import User, Meeting, UserSettings
from sqlalchemy import select

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

EMB = "backend\\python\\python.exe"

# ── 1. transcription command (reused desktop adapter) ────────────────────────
settings = {
    "transcriptionEngine": "whisperx", "whisperModel": "medium",
    "transcriptionLanguage": "en", "whisperDevice": "cpu",
    "diarizationBackend": "sherpa", "hfToken": "hf_x", "transcriptionHint": "gRPC",
    "aiProvider": "google", "aiModel": "gemini-2.5-flash", "apiKey": "k", "prompt": "SUM",
    "extractActionItems": True, "analyzeSentiment": True,
}
tx = W.T.build_command("C:/v/m.mkv", "C:/out", language="en", model="medium",
                       engine="whisperx", device="cpu", initial_prompt="gRPC",
                       diarization="sherpa", hf_token="hf_x")
txs = " ".join(tx)
check("tx_uses_embedded_python", EMB in tx[0] or EMB.replace("\\", "/") in tx[0].replace("\\", "/"), tx[0])
check("tx_calls_processor", "processor.py" in txs)
check("tx_engine_whisperx", "--engine" in tx and "whisperx" in tx)
check("tx_diarization", "--diarization" in txs and "sherpa" in tx)
check("tx_hf_token_hidden",
      "hf_x" not in tx
      and tx.environment.get("MEETING_SUMMARIZER_HF_TOKEN") == "hf_x")
check("tx_initial_prompt", "gRPC" in tx)

# ── 2. summary command ───────────────────────────────────────────────────────
sm = W.S.build_command("SUM", "C:/out/m_raw.txt", provider="google",
                       model="gemini-2.5-flash", api_key="k", endpoint="", advanced=None)
sms = " ".join(sm)
check("sum_uses_embedded_python", EMB in sm[0] or EMB.replace("\\","/") in sm[0].replace("\\","/"), sm[0])
check("sum_calls_ai_client", "ai_client.py" in sms)
check("sum_provider_google", "--provider" in sm and "google" in sm)
check("sum_model", "gemini-2.5-flash" in sm)
check("sum_secrets_hidden_from_argv",
      "k" not in sm and "SUM" not in sm
      and sm.environment.get("MEETING_SUMMARIZER_API_KEY") == "k"
      and sm.environment.get("MEETING_SUMMARIZER_PROMPT") == "SUM")

# ── 3. analysis gating + per-feature command ─────────────────────────────────
feats = W.A.enabled_features(settings)
check("analysis_features_nonempty", len(feats) >= 2, str(feats))
fc = W.A.build_feature_command(feats[0], "C:/out/m_raw.txt", settings,
                               provider="google", model="gemini-2.5-flash", api_key="k")
check("analysis_cmd_ai_client", "ai_client.py" in " ".join(fc))
# no analysis toggles => no features
check("analysis_none_when_off", W.A.enabled_features({"aiProvider": "local"}) == [])
source, linked = W._analysis_source(
    {"analysisSource": "transcript"}, "raw.txt", "summary.txt", 3)
check("analysis_transcript_source_has_no_false_summary_link",
      source == "raw.txt" and linked is None, f"{source}/{linked}")
source, linked = W._analysis_source(
    {"analysisSource": "summary"}, "raw.txt", "summary.txt", 3)
check("analysis_summary_source_links_version",
      source == "summary.txt" and linked == 3, f"{source}/{linked}")
source, linked = W._analysis_source({}, "raw.txt", "summary.txt", 3)
check("analysis_default_is_full_transcript",
      source == "raw.txt" and linked is None, f"{source}/{linked}")
check("chunking_default_is_off",
      W._DEFAULTS["chunkingEnabled"] is False)
check("analysis_object_schema_rejects_array",
      not W.A.is_valid_feature_result("sentiment", []))
check("analysis_list_schema_accepts_empty",
      W.A.is_valid_feature_result("risks", []))
check("analysis_invalid_json_is_not_empty_success",
      W.A.parse_json_response("not json at all") is None)

# A model routinely introduces its answer before the JSON, with or without a
# fence. Trailing prose was always trimmed; leading prose was not, so a response
# whose JSON was perfectly intact was reported as "invalid JSON/schema" and the
# feature came back empty. Seen on a real meeting: 2 of 8 runs of the same
# feature, nine correct action items thrown away each time.
_ITEMS = '[{"task": "a", "assignee": "b"}, {"task": "c", "assignee": "d"}]'
for _name, _wrapped in (
        ("plain", _ITEMS),
        ("fenced", f"```json\n{_ITEMS}\n```"),
        ("preamble", f"Вот JSON массив с задачами:\n\n{_ITEMS}"),
        ("preamble_and_fence", f"Вот JSON массив с задачами:\n\n```json\n{_ITEMS}\n```"),
        ("preamble_fence_and_epilogue",
         f"Вот результат:\n```json\n{_ITEMS}\n```\nЕсли нужно, могу дополнить."),
        ("object_after_preamble", 'Готово: {"overall": "neutral"}'),
):
    _parsed = W.A.parse_json_response(_wrapped)
    check(f"analysis_json_survives_{_name}",
          _parsed is not None and len(_parsed) == 2 if _name != "object_after_preamble"
          else _parsed == {"overall": "neutral"},
          repr(_wrapped[:60]))

routes_source = (ROOT / "server" / "api" / "routes" / "meetings.py").read_text(encoding="utf-8")
dashboard_source = (ROOT / "server" / "web" / "js" / "dashboard.js").read_text(encoding="utf-8")
check("export_uses_selected_artifact_version",
      '"version": selected_version' in routes_source
      and "default_export_path(out_dir, stem, kind, selected_version, fmt)" in routes_source)
check("summary_and_analysis_have_independent_version_pickers",
      "summaryVersionPicker" in dashboard_source
      and "analysisVersionPicker" in dashboard_source
      and "`${kind}VersionSelect`" in dashboard_source)
check("failed_ai_run_keeps_partial_results_and_regenerate",
      "if (meeting.summary_path || meeting.analysis_path)" in dashboard_source
      and "if (meeting.transcript_path)" in dashboard_source
      and "meeting.transcript_path || meeting.summary_path || meeting.analysis_path" in dashboard_source)

# ── 4. worker _ai_kwargs mapping ─────────────────────────────────────────────
kw = W.worker._ai_kwargs(settings)
check("ai_kwargs", kw["provider"] == "google" and kw["model"] == "gemini-2.5-flash"
      and kw["api_key"] == "k", str(kw))

# ── 5. SQLite DB round-trip + settings merge ─────────────────────────────────
async def db_test():
    await init_db()
    async with AsyncSessionLocal() as db:
        u = User(username="bob", email="b@x.io", password_hash="h")
        db.add(u); await db.commit(); await db.refresh(u)
        db.add(UserSettings(user_id=u.id, settings_json='{"transcriptionEngine":"vosk","whisperModel":"vosk-model-small-ru-0.22"}'))
        m = Meeting(user_id=u.id, filename="m.mkv", original_filename="m.mkv", video_path="C:/v/m.mkv")
        db.add(m); await db.commit(); await db.refresh(m)
        # read back
        got = (await db.execute(select(Meeting).where(Meeting.id == m.id))).scalar_one()
        check("db_meeting_roundtrip", got.user_id == u.id and got.filename == "m.mkv")
        merged = await W.worker._load_settings(db, got)
        check("settings_merge_user_over_default",
              merged["transcriptionEngine"] == "vosk" and merged["aiProvider"] == "local",
              merged.get("transcriptionEngine"))
        # progress/stage/ETA persistence (the cabinet's live-status feature)
        got.status = "processing"
        got.processing_started_at = datetime.utcnow() - timedelta(seconds=10)
        await db.commit()
        await W.worker._set_progress(db, got, "status.transcribing", 50)
        check("progress_persisted", got.progress == 50 and got.stage == "status.transcribing",
              f"{got.progress}/{got.stage}")
        check("eta_computed", isinstance(got.eta_seconds, int) and got.eta_seconds > 0,
              str(got.eta_seconds))

asyncio.run(db_test())


# ── 5b. jobs orphaned by a restart must be recoverable ──────────────────────
# Nothing survives the process, so a row left "processing" belongs to a worker
# that died with the previous instance. Untouched it spins forever: the queue no
# longer owns it, so Cancel answers "neither queued nor processing", and one
# orphaned during TRANSCRIPTION has no transcript, so Regenerate refuses too.
async def orphan_recovery_test():
    from server.api.main import reconcile_orphaned_jobs
    async with AsyncSessionLocal() as db:
        u = User(username="orph", email="o@x.io", password_hash="h")
        db.add(u); await db.commit(); await db.refresh(u)
        tx = Path(tempfile.mkdtemp()) / "kept_raw.txt"
        tx.write_text("[00:00:00] text", encoding="utf-8")
        mid = Meeting(user_id=u.id, filename="mid.mkv", original_filename="mid.mkv",
                      video_path="C:/v/mid.mkv", status="processing", progress=85,
                      stage="status.summarizing", transcript_path=str(tx))
        early = Meeting(user_id=u.id, filename="early.mkv", original_filename="early.mkv",
                        video_path="C:/v/early.mkv", status="processing", progress=40,
                        stage="status.transcribing")
        queued = Meeting(user_id=u.id, filename="q.mkv", original_filename="q.mkv",
                         video_path="C:/v/q.mkv", status="queued")
        done = Meeting(user_id=u.id, filename="d.mkv", original_filename="d.mkv",
                       video_path="C:/v/d.mkv", status="completed", progress=100)
        for m in (mid, early, queued, done):
            db.add(m)
        await db.commit()
        for m in (mid, early, queued, done):
            await db.refresh(m)
        ids = (mid.id, early.id, queued.id, done.id)
        # Earlier tests in this file share the temp DB and leave their own rows
        # behind, so count what is actually unfinished instead of hardcoding 3.
        unfinished = len((await db.execute(select(Meeting).where(
            Meeting.status.in_(("processing", "queued"))))).scalars().all())

    restored = await reconcile_orphaned_jobs()
    check("orphan_reconcile_restores_every_unfinished_row",
          restored == unfinished and restored >= 3,
          f"restored={restored} of {unfinished} unfinished")

    async with AsyncSessionLocal() as db:
        got = {m.id: m for m in (await db.execute(
            select(Meeting).where(Meeting.id.in_(ids)))).scalars().all()}
        # transcript survived -> recoverable via Regenerate, and says why
        check("orphan_with_transcript_is_failed_not_stuck",
              got[ids[0]].status == "failed" and got[ids[0]].stage is None
              and "Regenerate" in (got[ids[0]].error_message or ""),
              f"{got[ids[0]].status} / {got[ids[0]].error_message!r}")
        # no transcript -> nothing to regenerate from, so it must be startable again
        check("orphan_without_transcript_returns_to_uploaded",
              got[ids[1]].status == "uploaded" and got[ids[1]].progress == 0
              and got[ids[1]].error_message is None, got[ids[1]].status)
        check("orphan_queued_returns_to_uploaded",
              got[ids[2]].status == "uploaded", got[ids[2]].status)
        check("orphan_reconcile_leaves_finished_alone",
              got[ids[3]].status == "completed" and got[ids[3]].progress == 100,
              got[ids[3]].status)

asyncio.run(orphan_recovery_test())

# ── 6. GPU hand-off truthfulness + async server integration ─────────────────
async def handoff_test():
    import gpu_handoff

    # An unused port is not a successfully stopped model and must not create a
    # restore obligation.
    check("handoff_absent_model_is_false",
          gpu_handoff.acquire(65431, settle=0) is False)

    events = []
    original_acquire, original_release = gpu_handoff.acquire, gpu_handoff.release
    original_log = W.worker._log

    async def fake_log(db, meeting_id, level, message):
        events.append((level, message))

    original_status = gpu_handoff.acquire_status
    settings = {"gpuHandoff": True, "whisperDevice": "cuda", "llamaPort": 8080}
    try:
        W.worker._log = fake_log
        # "Nothing was running" is NOT a failure. Logging it as one is what made
        # every cloud-provider run look like the hand-off had broken.
        gpu_handoff.acquire_status = lambda port: "idle"
        held = await W.worker._gpu_acquire(None, SimpleNamespace(id=7), settings)
        check("server_handoff_respects_false", held is False)
        check("server_handoff_idle_is_not_a_warning",
              any(level == "INFO" and "nothing to unload" in msg
                  for level, msg in events)
              and not any(level == "WARNING" for level, _ in events), str(events))

        # A model that survives the stop IS a failure: VRAM was not freed.
        events.clear()
        gpu_handoff.acquire_status = lambda port: "stuck"
        held = await W.worker._gpu_acquire(None, SimpleNamespace(id=7), settings)
        check("server_handoff_stuck_is_a_warning",
              held is False and any(level == "WARNING" and "NOT" in msg
                                    for level, msg in events), str(events))

        events.clear()
        gpu_handoff.acquire_status = lambda port: "freed"
        held = await W.worker._gpu_acquire(None, SimpleNamespace(id=7), settings)
        check("server_handoff_freed_creates_restore_duty", held is True, str(events))
        events.clear()

        events.clear()
        gpu_handoff.release = lambda: False
        await W.worker._gpu_release(None, SimpleNamespace(id=7))
        check("server_release_failure_logged",
              any(level == "WARNING" and "did not become ready" in msg
                  for level, msg in events), str(events))
    finally:
        gpu_handoff.acquire, gpu_handoff.release = original_acquire, original_release
        gpu_handoff.acquire_status = original_status
        W.worker._log = original_log

asyncio.run(handoff_test())

# ── 7. Dynamic dashboard i18n regressions ───────────────────────────────────
dashboard_js = (ROOT / "server" / "web" / "js" / "dashboard.js").read_text(
    encoding="utf-8")
i18n_js = (ROOT / "server" / "web" / "js" / "i18n.js").read_text(encoding="utf-8")
check("dashboard_translates_live_stage",
      "formatStage(meeting.stage)" in dashboard_js
      and "formatStage(st.stage)" in dashboard_js)
check("dashboard_retranslates_js_rendered_status",
      "['urlMsg', 'recordMsg'].forEach" in dashboard_js
      and "el.dataset.i18nKey" in dashboard_js
      and "upload.urlQueued" in dashboard_js,
      "setLanguage() only walks [data-i18n] markup, so JS-rendered "
      "messages must be re-rendered from their stored key")
# The cabinet must be able to CAPTURE a meeting, not only receive one: the
# desktop client has a recorder, and a browser user had no way to record at all.
i18n_js_all = (ROOT / "server" / "web" / "js" / "i18n.js").read_text(encoding="utf-8")
dashboard_html = (ROOT / "server" / "web" / "dashboard.html").read_text(encoding="utf-8")
check("cabinet_has_a_microphone_recorder",
      'id="recordBtn"' in dashboard_html
      and "navigator.mediaDevices.getUserMedia" in dashboard_js
      and "new MediaRecorder(" in dashboard_js)
check("recorder_uploads_through_the_normal_path",
      "handleFileUpload(file)" in dashboard_js and "new File([blob]" in dashboard_js)
check("recorder_only_records_accepted_containers",
      "'audio/webm;codecs=opus'" in dashboard_js and "'.webm'" in dashboard_js
      and "'audio/mp4'" in dashboard_js and "'.m4a'" in dashboard_js,
      "the upload endpoint rejects anything else")
check("recorder_explains_an_insecure_origin",
      "window.isSecureContext === false" in dashboard_js
      and "'upload.recordInsecure': 'The browser only gives" in i18n_js_all
      and "'upload.recordInsecure': 'Браузер даёт доступ" in i18n_js_all)
check("recorder_strings_exist_in_both_languages",
      all(i18n_js_all.count(f"'upload.{k}'") == 2 for k in
          ("recordStart", "recordStop", "recordPrefix", "recordHint", "recordDenied",
           "recordNoDevice", "recordInsecure", "recordUnsupported", "recordTooShort",
           "recordFailed")))
# The desktop loads resources/icon.png at startup and the cabinet had no tab icon
# at all; neither file was in any archive, so every distributed copy looked default.
icon_png = ROOT / "resources" / "icon.png"
favicon = ROOT / "server" / "web" / "favicon.png"
main_window_src = (ROOT / "desktop" / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
index_html = (ROOT / "server" / "web" / "index.html").read_text(encoding="utf-8")
check("application_icon_exists_and_is_loaded",
      icon_png.exists() and 'paths.ROOT / "resources" / "icon.png"' in main_window_src
      and "setWindowIcon" in main_window_src)
check("cabinet_pages_link_a_favicon",
      favicon.exists() and favicon.stat().st_size < 100_000
      and 'href="/static/favicon.png"' in index_html
      and 'href="/static/favicon.png"' in dashboard_html,
      "a 1 MB source image as favicon is a defect of its own")

# A self-hosted server needs an administrator, and nothing in the product could
# create one: every registration wrote role="user".
auth_source = (ROOT / "server" / "api" / "routes" / "auth.py").read_text(encoding="utf-8")
deploy_en = (ROOT / "server" / "DEPLOYMENT.md").read_text(encoding="utf-8")
deploy_ru = (ROOT / "server" / "DEPLOYMENT.ru.md").read_text(encoding="utf-8")
check("first_account_becomes_the_administrator",
      'role="admin" if existing == 0 else "user"' in auth_source)
check("deployment_guide_explains_the_admin_and_promotion",
      "administrator" in deploy_en.lower() and "update users set role" in deploy_en
      and "администратор" in deploy_ru.lower() and "update users set role" in deploy_ru)
run_server_source = (ROOT / "server" / "run_server.py").read_text(encoding="utf-8")
dashboard_js_all = (ROOT / "server" / "web" / "js" / "dashboard.js").read_text(encoding="utf-8")
api_js = (ROOT / "server" / "web" / "js" / "api.js").read_text(encoding="utf-8")
check("server_trusts_reverse_proxy_headers",
      "proxy_headers=True" in run_server_source
      and "forwarded_allow_ips" in run_server_source,
      "behind TLS termination every client is otherwise the proxy, over plain http")
check("client_follows_the_public_origin",
      "window.location.origin + '/api'" in api_js
      and "window.location.protocol === 'https:' ? 'wss:' : 'ws:'" in dashboard_js_all,
      "a hardcoded host or ws:// breaks the cabinet the moment it is served over HTTPS")
check("deployment_guide_covers_the_proxy_pitfalls",
      all(k in deploy_en for k in ("client_max_body_size", "proxy_read_timeout",
                                   "X-Forwarded-Proto", "TRUSTED_PROXIES"))
      and all(k in deploy_ru for k in ("client_max_body_size", "proxy_read_timeout",
                                       "X-Forwarded-Proto", "TRUSTED_PROXIES")),
      "default body size and read timeout reject uploads and kill the live socket")
check("deployment_guide_covers_the_packaged_install",
      "SERVER.bat" in deploy_en and "INSTALL.bat" in deploy_en
      and "SERVER.bat" in deploy_ru and "INSTALL.bat" in deploy_ru,
      "a recipient of the archive has no repo and no venv step")

# Parity: the desktop can stop a job, correct a transcript and tag a meeting
# with a project. The cabinet could do none of the three.
queue_source = (ROOT / "server" / "processing" / "queue.py").read_text(encoding="utf-8")
worker_source = (ROOT / "server" / "processing" / "worker.py").read_text(encoding="utf-8")
schemas_source = (ROOT / "server" / "api" / "schemas.py").read_text(encoding="utf-8")

check("cabinet_can_cancel_a_run",
      '@router.post("/{meeting_id}/cancel")' in routes_source
      and "async def cancel_meeting" in queue_source
      and "api.cancelMeeting(" in dashboard_js)
check("cancel_targets_one_meeting_only",
      "processes_by_meeting" in worker_source and "def _track(" in worker_source,
      "a flat process list would kill every parallel worker's run")
check("a_cancelled_run_is_not_reported_as_failed",
      "class Cancelled(Exception)" in worker_source
      and 'meeting.status = "cancelled"' in worker_source
      and "'filters.cancelled'" in i18n_js_all)
check("delete_stops_the_run_first",
      "if await processing_queue.cancel_meeting(meeting_id) != \"idle\":" in routes_source,
      "deleting a processing meeting left the worker transcribing into a removed folder")
check("cabinet_transcript_is_editable",
      '@router.put("/{meeting_id}/transcript")' in routes_source
      and 'id="transcriptEditor"' in dashboard_js
      and "api.saveTranscript(" in dashboard_js)
check("empty_transcript_cannot_be_saved",
      "Refusing to save an empty transcript" in routes_source)
check("meeting_project_can_be_set_and_read",
      "project: Optional[str] = None" in schemas_source
      and 'id="meetingProject"' in dashboard_js and "api.updateMeeting(" in dashboard_js)
check("cancel_and_edit_strings_exist_in_both_languages",
      all(i18n_js_all.count(f"'{k}'") == 2 for k in
          ("meetings.cancel", "meetings.cancelFailed", "modal.transcript",
           "modal.saveTranscript", "modal.transcriptSaved", "modal.project",
           "modal.saveProject")))

# Feature parity with the desktop's queue actions. Each of these was a real
# dead end in the cabinet, found by the owner while testing the desktop:
# recordings could only be deleted ONE at a time, a recording uploaded with
# "trim first" could never be started because the card offered only Cancel, and
# a finished run showed a status badge but not a single stage timing.
check("cabinet_can_clear_the_list_in_bulk",
      '@router.delete("/finished")' in routes_source
      and "api.clearFinishedMeetings(" in dashboard_js
      and 'id="clearFinishedBtn"' in dashboard_html,
      "deleting recordings one by one was the only option")
check("bulk_clear_is_declared_before_the_id_route",
      routes_source.index('@router.delete("/finished")')
      < routes_source.index('@router.delete("/{meeting_id}"'),
      "otherwise FastAPI parses 'finished' as a meeting id")
check("bulk_clear_keeps_a_running_job",
      "if processing_queue.is_processing(meeting.id):" in routes_source
      and '"skipped": skipped' in routes_source)
check("one_cleanup_path_for_both_deletes",
      routes_source.count("async def _purge_meeting") == 1
      and routes_source.count("await _purge_meeting(") == 2,
      "a second hand-copied cleanup is how artifacts and logs got orphaned")
check("uploaded_meeting_can_be_started_from_the_card",
      "async function processMeeting(" in dashboard_js
      and "data-role=\"process\"" in dashboard_js
      and '@router.post("/{meeting_id}/process"' in routes_source,
      "upload with 'trim first' + close the trim window = stranded recording")
check("cabinet_shows_the_stage_timeline",
      '@router.get("/{meeting_id}/trace")' in routes_source
      and "api.meetingTrace(" in dashboard_js
      and "loadStageTimeline(" in dashboard_js,
      "the trace file was written but never served")
check("a_zero_duration_stage_still_shows_its_time",
      "s.duration === null || s.duration === undefined" in dashboard_js,
      "0 ms is a measurement, not a missing value")
check("a_failed_sign_in_shows_an_error_instead_of_reloading",
      "redirectOn401: false" in (ROOT / "server" / "web" / "js" / "api.js").read_text(encoding="utf-8")
      and i18n_js_all.count("'auth.invalidCredentials'") == 2,
      "a 401 from /auth/login is 'wrong password', not 'your session expired'")
check("queue_action_strings_exist_in_both_languages",
      all(i18n_js_all.count(f"'{k}'") == 2 for k in
          ("meetings.process", "meetings.processFailed", "meetings.clearFinished",
           "meetings.clearConfirm", "meetings.clearSkipped", "meetings.clearFailed",
           "modal.stages", "stage.extractAudio", "stage.transcribe",
           "stage.summarize", "stage.analysis")))

# Feature parity: one recording often holds several meetings. The desktop has a
# Trim dialog; the cabinet could only ever process a whole file.
check("cabinet_can_split_a_recording",
      'id="trimCanvas"' in dashboard_html and 'id="trimBeforeProcessing"' in dashboard_html
      and "api.cutSegments(" in dashboard_js and "api.meetingWaveform(" in dashboard_js)
check("trim_endpoints_exist",
      '@router.get("/{meeting_id}/waveform")' in routes_source
      and '@router.post("/{meeting_id}/segments"' in routes_source
      and '@router.post("/{meeting_id}/process"' in routes_source)
check("trim_upload_can_be_held_back",
      "process: bool = Form(True)" in routes_source and "if process:" in routes_source,
      "the file must not be queued while the user is still choosing segments")
check("waveform_never_ships_the_media",
      '"-f", "s16le"' in routes_source and "peak_level" in routes_source,
      "peaks are computed server-side, the recording itself never travels")
check("segments_are_validated_before_cutting",
      "Invalid segment" in routes_source and "is past the " in routes_source
      and "end of the recording" in routes_source)
check("trim_strings_exist_in_both_languages",
      all(i18n_js_all.count(f"'trim.{k}'") == 2 for k in
          ("enable", "title", "intro", "add", "remove", "cut", "whole", "none",
           "cutting", "queued", "failed", "waveformFailed", "selection")))

# The desktop has a Statistics dialog; the cabinet reported nothing at all.
check("cabinet_has_archive_statistics",
      'id="statsBody"' in dashboard_html and "api.meetingStats(" in dashboard_js
      and '@router.get("/stats")' in routes_source)
check("stats_route_precedes_the_id_route",
      routes_source.index('@router.get("/stats")')
      < routes_source.index('@router.get("/{meeting_id}"'),
      "FastAPI matches in order; a literal path after the parameterised one 422s")
check("stats_metrics_match_the_desktop_dialog",
      all(k in routes_source for k in
          ('"total"', '"with_tx"', '"with_sum"', '"with_an"', '"words"',
           '"by_status"', '"by_project"')))
check("stats_strings_exist_in_both_languages",
      all(i18n_js_all.count(f"'stats.{k}'") == 2 for k in
          ("title", "refresh", "total", "withTx", "withSum", "withAn", "words",
           "byStatus", "byProject", "noProject", "failed")))

# Both search endpoints and their API-client methods existed and were tested,
# but nothing in the cabinet ever called them - a browser user could not search.
check("cabinet_exposes_both_searches",
      'id="searchInput"' in dashboard_html and 'id="searchMode"' in dashboard_html
      and "api.textSearch(" in dashboard_js and "api.ragSearch(" in dashboard_js)
check("search_regex_switch_is_literal_only",
      "document.getElementById('searchRegex').closest('label').style.display" in dashboard_js,
      "a regex means nothing to the semantic search")
check("search_results_open_the_meeting",
      "showMeetingDetail(${g.meeting_id})" in dashboard_js)
check("search_strings_exist_in_both_languages",
      all(i18n_js_all.count(f"'search.{k}'") == 2 for k in
          ("title", "placeholder", "modeText", "modeRag", "regex", "run",
           "searching", "nothing", "failed", "hits", "ragEmpty")))
check("per_speaker_export_is_offered_only_when_diarised",
      'id="exportSpeakersBtn" style="display:none"' in dashboard_js
      and "exportBtn.style.display = ''" in dashboard_js,
      "the button used to show on every meeting and alert an error")
check("dashboard_localizes_eta_units",
      "i18n.t('time.minute')" in dashboard_js
      and "'time.minute': 'm'" in i18n_js
      and "'time.minute': 'м'" in i18n_js)

# ── 8. Worker defaults ARE the settings API defaults ────────────────────────
# A hand-copied duplicate here once lost the five analysis feature flags, so a
# user who had never saved settings got no analysis at all while the meeting
# still reported "completed" and every analysis export answered 404.
from server.api.routes.settings import DEFAULT_SETTINGS
from desktop.app.backend import analysis as _A
check("worker_defaults_are_the_api_defaults", W._DEFAULTS is DEFAULT_SETTINGS)
check("bare_defaults_enable_every_analysis_feature",
      len(_A.enabled_features(dict(W._DEFAULTS))) == len(_A.FEATURE_ORDER),
      f"{len(_A.enabled_features(dict(W._DEFAULTS)))} of {len(_A.FEATURE_ORDER)}")

# ── 9. Meeting length is filled in, not left blank ──────────────────
# Nothing ever wrote Meeting.duration, so the cabinet card and the detail modal
# showed no length for every real meeting; ffprobe first, transcript as fallback.
_transcript = "[00:00:01] a\n[00:05:40] b\n"
check("duration_from_a_transcript",
      W._measure_duration("", _transcript) == "5m 40s",
      W._measure_duration("", _transcript))
check("duration_survives_a_missing_file",
      W._measure_duration(str(Path(tempfile.mkdtemp()) / "gone.mkv"), "") == "")
check("duration_is_empty_when_nothing_is_known",
      W._measure_duration("", "") == "")

# Every settings key the server DEFAULTS to must be DECLARED on the update schema.
# SettingsUpdate is a Pydantic model dumped with exclude_unset=True, so a key that
# is missing there is discarded in silence: the cabinet sent projectId, the form
# showed it, DEFAULT_SETTINGS listed it, and the save quietly kept the old value.
_settings_src = (ROOT / "server" / "api" / "routes" / "settings.py").read_text(encoding="utf-8")
_schema_src = (ROOT / "server" / "api" / "schemas.py").read_text(encoding="utf-8")
_defaults_block = re.search(r"DEFAULT_SETTINGS\s*=\s*\{(.*?)\n\}", _settings_src, re.S)
_default_keys = set(re.findall(r'"([A-Za-z_]+)"\s*:', _defaults_block.group(1)))
_schema_block = re.search(r"class SettingsData\(BaseModel\):(.*?)\nSettingsUpdate\s*=",
                          _schema_src, re.S)
_declared = set(re.findall(r"^\s{4}([A-Za-z_]+)\s*:", _schema_block.group(1), re.M))
_unsaveable = sorted(_default_keys - _declared)
check("every_default_setting_is_saveable", not _unsaveable,
      f"declared nowhere in SettingsData: {_unsaveable}")

# ...and every field the cabinet's form binds must be a real, saveable key.
_dash = (ROOT / "server" / "web" / "js" / "dashboard.js").read_text(encoding="utf-8")
_bound = set(re.findall(r"(?:txt|chk|selOpts)\('([A-Za-z_]+)'", _dash))
_bound |= set(re.findall(r'data-key="([A-Za-z_]+)"', _dash))
_orphans = sorted(k for k in _bound if k not in _default_keys)
check("cabinet_form_binds_only_real_settings", not _orphans, str(_orphans))

# ── Obsidian export, and installation-wide administration ───────────────────
# Three capabilities the cabinet lacked while the desktop had them, each closed on
# the owner's instruction: notes in an Obsidian vault, admin-owned model/engine
# management whose effect is shared by every account, and a worker count that is
# load management for the machine rather than a per-user preference.
_admin_src = (ROOT / "server" / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
_engines_src = (ROOT / "server" / "api" / "routes" / "engines.py").read_text(encoding="utf-8")
_models_src = (ROOT / "server" / "database" / "models.py").read_text(encoding="utf-8")
_main_src = (ROOT / "server" / "api" / "main.py").read_text(encoding="utf-8")
_queue_src = (ROOT / "server" / "api" / "routes" / "queue.py").read_text(encoding="utf-8")

check("cabinet_exports_to_obsidian",
      '@router.post("/{meeting_id}/obsidian")' in routes_source
      and "api.exportObsidian(" in dashboard_source
      and "settings.obsidianVault" in i18n_js_all)
check("obsidian_export_follows_the_version_picker",
      "summary_version: int = 0" in _schema_src
      and "`${kind}VersionSelect`" in dashboard_source
      and "body.summary_version" in dashboard_source,
      "exporting v2 must not write v4 into the vault")
check("obsidian_reports_every_written_kind",
      'for k in ("summary", "analysis", "transcript")' in routes_source,
      "reporting only the summary made the other kinds look dead")
check("obsidian_refuses_a_missing_vault",
      "No Obsidian vault is configured" in routes_source
      and "does not exist on the server" in routes_source)

check("installation_settings_are_persisted",
      'class ServerSettings(Base)' in _models_src
      and "load_server_settings" in _main_src
      and "apply_server_settings" in _main_src,
      "the worker count used to reset to auto-detection on every restart")
check("worker_count_endpoint_persists_too",
      "update_server_settings(ServerSettingsUpdate(parallelWorkers=count)" in _queue_src)
check("installation_settings_are_admin_only",
      _admin_src.count("Depends(get_current_admin_user)") >= 3
      and "get_current_admin_user" in _admin_src)
check("admin_ui_is_hidden_from_regular_users",
      "adminBtn.style.display = ''" in dashboard_source
      and "if (user.role === 'admin')" in dashboard_source,
      "a 403 alone still shows a control the user cannot use")
check("cabinet_can_update_and_install_engines",
      '@router.get("/{engine}/models/{model}/update-check")' in _engines_src
      and '@router.post("/engines/{engine}/install"' in _admin_src
      and "api.installEngine(" in dashboard_source
      and "api.checkModelUpdate(" in dashboard_source,
      "models_cli could always check-update; nothing exposed it")
check("engine_package_map_matches_the_installer",
      all(e in _admin_src for e in ("whisperx", "vosk", "sherpa-onnx")),
      "the registry carries no dependency metadata, so the map must stay in sync")
check("admin_strings_exist_in_both_languages",
      all(i18n_js_all.count(f"'{k}'") == 2 for k in
          ("admin.title", "admin.hint", "admin.workers", "admin.engines",
           "admin.install", "admin.update", "settings.obsidian",
           "settings.obsidianVault", "modal.obsidianDone")))

# ── English strings must not reach a Russian UI ──────────────────────────────
# API messages are English by design (one API, many clients), so the cabinet
# translates the known ones. That map keys off the SERVER's wording, which makes
# those strings a contract: if one is reworded and the map is not, the message
# silently reverts to English. Assert both directions here.
_i18n_src = (ROOT / "server" / "web" / "js" / "i18n.js").read_text(encoding="utf-8")
_api_src = (ROOT / "server" / "web" / "js" / "api.js").read_text(encoding="utf-8")
_worker_src = (ROOT / "server" / "processing" / "worker.py").read_text(encoding="utf-8")
_auth_src = (ROOT / "server" / "api" / "routes" / "auth.py").read_text(encoding="utf-8")
_ai_src = (ROOT / "backend" / "ai_client.py").read_text(encoding="utf-8")

check("cabinet_translates_server_messages",
      "serverMessage(" in _i18n_src and "SERVER_MESSAGES" in _i18n_src
      and "i18n.serverMessage(" in dashboard_source,
      "error_message and detail used to render raw English")
for _needle, _where, _src in (
        ("No speech recognised", "worker", _worker_src),
        ("Username already registered", "auth", _auth_src),
        ("Email already registered", "auth", _auth_src),
        ("Cannot connect to local API at", "ai_client", _ai_src),
        ("Could not read the recording", "meetings", routes_source)):
    check(f"server_still_emits_{_needle.split()[0].lower()}_{_where}",
          _needle in _src and _needle.split(" at")[0] in _i18n_src,
          f"'{_needle}' must exist in both the server and the translation map")
check("no_raw_ffmpeg_dump_in_the_ui",
      "Could not decode the media" not in routes_source
      and "[waveform] ffmpeg failed" in routes_source,
      "the stderr tail arrived sliced mid-word, in English")
check("every_shown_server_error_is_translated",
      "escapeHtml(i18n.serverMessage(meeting.error_message))" in dashboard_source
      and "i18n.serverMessage(error.message)" in
          (ROOT / "server" / "web" / "js" / "auth.js").read_text(encoding="utf-8"))

# ── downloads: a real name, a real extension, and only when there IS content ──
check("download_offers_a_recognisable_name",
      "_safe_upload_name(meeting.original_filename)" in routes_source
      and 'f"{stem}_{file_type}{suffix}{stored.suffix}"' in routes_source,
      "the stored name carries an internal prefix and meant nothing to the user")
check("client_keeps_the_server_filename",
      r"filename\*=\s*utf-8''" in _api_src,
      "a.download used to be overwritten with an extensionless 'video_8'")
check("download_helper_is_shared_by_every_path",
      _api_src.count("_downloadBlob(") >= 4,
      "one path had its own implementation and lost the name")
check("empty_artifacts_are_not_downloadable",
      "there is nothing to download" in routes_source
      and "def _has_content" in _schema_src
      and "has_transcript" in dashboard_source,
      "a no-speech run left an empty transcript and the button handed it over")

# ── the speaker switch must change the PROMPT, not just the list ─────────────
check("speaker_toggle_rerenders_the_prompt",
      "textarea[data-key=\"prompt\"]" in dashboard_source
      and "active.prompt" in dashboard_source,
      "both variants share every template NAME; only the text differs")

# A bulk delete MUST be scoped to its owner. An unscoped one would wipe every
# account's recordings from one click, and SQLite's id reuse makes cross-tenant
# mistakes in this area silent rather than loud.
_clear_body = routes_source.split("async def clear_finished_meetings")[1].split("@router")[0]
check("bulk_clear_is_scoped_to_its_owner",
      "Meeting.user_id == current_user.id" in _clear_body,
      "an unscoped clear would delete other users' meetings")
check("single_delete_is_scoped_to_its_owner",
      routes_source.count("Meeting.user_id == current_user.id") >= 8,
      "every meeting lookup must be filtered by the caller")

# ── engine packages belong to the EMBEDDED runtime, not this process ────────
# Transcription runs as a subprocess under backend/python; the server venv is
# deliberately torch-free. Probing with sys.executable reported all seven engines
# "not installed" while transcription worked, and Install would have pip'd
# torch/CUDA into the wrong interpreter without fixing anything.
_admin_source = (ROOT / "server" / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
check("engine_probe_uses_the_backend_runtime",
      "_PY = backend_python()" in _admin_source and "str(_PY), \"-c\", probe" in _admin_source,
      "importlib.util.find_spec() in-process answers about the server venv")
check("engine_install_targets_the_embedded_runtime",
      'argv=[str(_PY), "-m", "pip", "install"' in _admin_source,
      "sys.executable would install into the torch-free server venv")
# Comments legitimately NAME the old bug, so strip them before looking for it -
# otherwise the explanation of the fix reads as the fix being absent.
_admin_code = "\n".join(line.split("#", 1)[0] for line in _admin_source.splitlines())
check("engine_probe_no_longer_asks_this_process",
      "sys.executable" not in _admin_code,
      "no code path may fall back to the FastAPI interpreter")

# ── the same runtime must be RESOLVED, not hardcoded ────────────────────────
# A min installation has no backend/python: hardcoding it made engines, RAG and
# admin packages answer a bare 500 (FileNotFoundError from create_subprocess_exec
# before any handler ran), while the very same box transcribed fine - the worker
# asks paths.python_executable(), which falls back. Found on a clean Windows 11 VM.
_runtime_source = (ROOT / "server" / "runtime.py").read_text(encoding="utf-8")
check("runtime_prefers_the_embedded_interpreter",
      'embedded = _REPO / "backend" / "python" / "python.exe"' in _runtime_source
      and "if embedded.exists():" in _runtime_source,
      "the full build must keep using its own runtime")
check("runtime_falls_back_to_the_recorded_interpreter",
      'recorded = _REPO / "config" / "interpreter.txt"' in _runtime_source,
      "the min build installs the engines into the interpreter INSTALL.bat recorded")
for _mod in ("api/routes/engines.py", "api/routes/rag.py", "api/routes/admin.py",
             "processing/worker.py", "processing/queue.py"):
    _src = (ROOT / "server" / _mod).read_text(encoding="utf-8")
    check(f"no_hardcoded_runtime_in_{_mod.replace('/', '_')}",
          'backend" / "python" / "python.exe"' not in _src,
          "spawning a path that a min installation does not have raises FileNotFoundError")
for _route in ("engines.py", "rag.py", "admin.py"):
    _src = (ROOT / "server" / "api" / "routes" / _route).read_text(encoding="utf-8")
    check(f"unrunnable_interpreter_is_reported_in_{_route}",
          "except OSError as exc:" in _src and "not runnable at" in _src,
          "a missing interpreter must name itself, not surface as 'Internal Server Error'")

# The resolver itself, against a fabricated min-installation layout: no
# backend/python, only the interpreter INSTALL.bat recorded.
from server import runtime as _RT
_real_repo = _RT._REPO
try:
    _fake = Path(tempfile.mkdtemp())
    _RT._REPO = _fake
    check("min_layout_falls_back_to_this_process",
          _RT.backend_python() == Path(sys.executable),
          "nothing recorded and nothing embedded → the running interpreter")
    (_fake / "config").mkdir(parents=True, exist_ok=True)
    # A path that is NOT sys.executable, or the check would pass even if the
    # recorded-interpreter branch were deleted entirely.
    _recorded = _fake / "recorded-python.exe"
    _recorded.write_bytes(b"")
    (_fake / "config" / "interpreter.txt").write_text(str(_recorded), encoding="utf-8")
    check("min_layout_uses_the_recorded_interpreter",
          _RT.backend_python() == _recorded,
          "config/interpreter.txt names the Python the engines went into")
    (_fake / "config" / "interpreter.txt").write_text(
        str(_fake / "gone" / "python.exe"), encoding="utf-8")
    check("a_stale_recorded_interpreter_is_ignored",
          _RT.backend_python() == Path(sys.executable),
          "a path that no longer exists must not be spawned")
    _emb = _fake / "backend" / "python"
    _emb.mkdir(parents=True, exist_ok=True)
    (_emb / "python.exe").write_bytes(b"")
    check("the_embedded_runtime_still_wins",
          _RT.backend_python() == _emb / "python.exe",
          "a full build must keep using the runtime it ships")
finally:
    _RT._REPO = _real_repo

# ── an intranet address is a valid account identifier ───────────────────────
# The cabinet sends no mail, so email-validator's special-use domain rule only
# refused the addresses a LAN deployment actually has. Registering with
# user@host.local answered 422 "special-use or reserved name".
from pydantic import ValidationError as _VErr
from server.api.schemas import UserRegister as _Reg


def _accepts(address: str) -> bool:
    try:
        _Reg(username="tester", email=address, password="secret1")
        return True
    except _VErr:
        return False


for _addr in ("user@nas.local", "user@corp.internal", "user@host.home.arpa",
              "user@vm.test", "user@example.com"):
    check(f"registration_accepts_{_addr}", _accepts(_addr),
          "an intranet host is a legitimate account identifier here")
for _addr in ("user@example.invalid", "admin@localhost", "not-an-email",
              "user@", "@host.local", "two words@host.local"):
    check(f"registration_still_refuses_{_addr}", not _accepts(_addr),
          "relaxing special-use domains must not turn off syntax validation")

# ── SERVER.bat must explain a missing OPTIONAL component ────────────────────
# The web cabinet is opt-in in the min installer. Declining it and then running
# SERVER.bat printed a raw "No module named 'sqlalchemy'" traceback, which reads
# as a broken build. Seen on a clean Windows 11 VM after `--recommended --yes`.
import importlib.util as _ilu  # noqa: E402
_launcher = ROOT / "server" / "run_server.py"
_spec = _ilu.spec_from_file_location("_run_server_probe", _launcher)
_rs = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_rs)          # __name__ != "__main__": nothing starts
check("server_launcher_checks_its_dependencies",
      callable(getattr(_rs, "_missing_dependencies", None)),
      "a missing optional component must be named, not raise ImportError")
check("server_launcher_sees_this_venv_as_complete",
      _rs._missing_dependencies() == [],
      "the server venv has every server dependency, so the check must pass here")
_launcher_src = _launcher.read_text(encoding="utf-8")
check("server_launcher_points_at_the_installer",
      "INSTALL.bat" in _launcher_src and "Веб-кабинет" in _launcher_src,
      "the message must say how to fix it, in both languages")
# The map drifts the moment a dependency is added to the manifest and not here.
_manifest = {ln.split("==")[0].split("[")[0].split("#")[0].strip().lower()
             for ln in (ROOT / "server" / "requirements.txt").read_text(
                 encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")}
_checked = {"fastapi", "uvicorn", "sqlalchemy", "aiosqlite",
            "python-jose", "passlib", "bcrypt", "python-multipart"}
check("server_dependency_check_matches_the_manifest",
      _checked <= _manifest,
      f"not in server/requirements.txt: {sorted(_checked - _manifest)}")

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}"); sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)"); sys.exit(0)
