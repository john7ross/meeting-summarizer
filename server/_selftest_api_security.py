"""Live FastAPI regressions for WebSocket tenancy and queue controls."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["SERVER_MODE"] = "true"
os.environ["MAX_UPLOAD_BYTES"] = "32"
_tmp = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + (Path(_tmp.name) / "server.db").as_posix()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.api.main import app
from server.auth.auth_handler import decode_access_token
from sqlalchemy import select
from server.database.db import AsyncSessionLocal
from server.database.models import Meeting, Artifact, ProcessingLog
from server.processing.queue import ProcessingQueue
from server.api.routes import meetings as meeting_routes
from server.api.routes.meetings import _safe_upload_name, _remove_meeting_directory

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(("PASS  " if condition else "FAIL  ") + name +
          (f"  ({detail})" if detail and not condition else ""))


async def add_meeting(user_id):
    async with AsyncSessionLocal() as db:
        row = Meeting(user_id=user_id, filename="owned.mkv",
                      original_filename="owned.mkv", status="uploaded")
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def queue_checks():
    q = ProcessingQueue(max_workers=2)
    await q.add_meeting(42)
    await q.add_meeting(42)
    check("queue_deduplicates_waiting_id", q.queue.qsize() == 1, str(q.queue.qsize()))

    q2 = ProcessingQueue(max_workers=2)
    await q2.start()
    q2.set_max_workers(1)
    await asyncio.sleep(0.05)
    check("worker_decrease_is_real",
          q2.get_status()["max_workers"] == 1
          and q2.get_status()["active_workers"] == 1,
          str(q2.get_status()))
    await q2.stop()



async def add_artifact_and_log(meeting_id):
    async with AsyncSessionLocal() as db:
        db.add(Artifact(meeting_id=meeting_id, kind="summary", version=1,
                        path="nowhere.txt", provider="local"))
        db.add(ProcessingLog(meeting_id=meeting_id, log_level="INFO", message="x"))
        await db.commit()


async def leftovers(meeting_id):
    async with AsyncSessionLocal() as db:
        arts = (await db.execute(select(Artifact).where(
            Artifact.meeting_id == meeting_id))).scalars().all()
        logs = (await db.execute(select(ProcessingLog).where(
            ProcessingLog.meeting_id == meeting_id))).scalars().all()
        return len(arts), len(logs)


with TestClient(app) as client:
    tokens = []
    for n in (1, 2):
        username = f"user{n}"
        client.post("/api/auth/register", json={
            "username": username, "email": f"u{n}@example.com",
            "password": "strongpass123"})
        response = client.post("/api/auth/login", json={
            "username": username, "password": "strongpass123"})
        tokens.append(response.json()["access_token"])
    owner_id = int(decode_access_token(tokens[0])["sub"])
    meeting_id = asyncio.run(add_meeting(owner_id))

    for name, url in (
            ("websocket_rejects_missing_token", f"/ws/{meeting_id}"),
            ("websocket_rejects_other_user",
             f"/ws/{meeting_id}?token={tokens[1]}")):
        try:
            with client.websocket_connect(url):
                check(name, False, "connection unexpectedly accepted")
        except WebSocketDisconnect as exc:
            check(name, exc.code == 1008, str(exc.code))

    with client.websocket_connect(
            f"/ws/{meeting_id}?token={tokens[0]}") as ws:
        msg = ws.receive_json()
        check("websocket_accepts_owner",
              msg.get("type") == "connected" and msg.get("meeting_id") == meeting_id,
              str(msg))

    # The first account on a fresh installation is the administrator (nothing else
    # could ever grant the role), so user2 is the "normal user" here.
    admin_status = client.get(
        "/api/queue/status", headers={"Authorization": f"Bearer {tokens[0]}"})
    check("first_registered_account_is_the_admin",
          admin_status.status_code == 200
          and "processing_meetings" in admin_status.json(),
          str(admin_status.json()))
    status_response = client.get(
        "/api/queue/status", headers={"Authorization": f"Bearer {tokens[1]}"})
    check("normal_user_queue_status_hides_other_ids",
          status_response.status_code == 200
          and "processing_meetings" not in status_response.json(),
          str(status_response.json()))
    check("normal_user_cannot_resize_the_worker_pool",
          client.post("/api/queue/workers/2",
                      headers={"Authorization": f"Bearer {tokens[1]}"}).status_code == 403)

    # Health must answer under /api too: everything else lives there, so probes
    # and reverse proxies look for /api/health and used to get a 404.
    for path in ("/health", "/api/health"):
        probe = client.get(path)
        check(f"health_answers_on_{path.strip('/').replace('/', '_')}",
              probe.status_code == 200 and probe.json().get("status") == "healthy",
              f"{probe.status_code} {probe.text[:60]}")

    # Deleting a meeting must take its artifacts and logs with it. They are not
    # ORM-cascaded; leaving them behind let the next meeting (SQLite reuses the
    # freed id) inherit another user's version history and processing log.
    doomed = asyncio.run(add_meeting(owner_id))
    asyncio.run(add_artifact_and_log(doomed))
    deleted = client.delete(f"/api/meetings/{doomed}",
                            headers={"Authorization": f"Bearer {tokens[0]}"})
    arts, logs = asyncio.run(leftovers(doomed))
    check("meeting_delete_removes_artifacts_and_logs",
          deleted.status_code in (200, 204) and arts == 0 and logs == 0,
          f"status={deleted.status_code} artifacts={arts} logs={logs}")

    auth = {"Authorization": f"Bearer {tokens[0]}"}
    default_settings = client.get("/api/settings/", headers=auth)
    check("rag_catalog_default_is_isolated",
          default_settings.status_code == 200
          and default_settings.json()["settings"]["ragCatalogMode"] == "isolated",
          default_settings.text)
    invalid_rag = client.put("/api/settings/", headers=auth, json={
        "ragCatalogMode": "shared", "ragSharedCatalogKey": "../../escape",
    })
    check("shared_rag_rejects_invalid_key",
          invalid_rag.status_code == 400, invalid_rag.text)
    shared_key = "rsc_0123456789012345678901234567890123456789012"
    valid_rag = client.put("/api/settings/", headers=auth, json={
        "ragCatalogMode": "shared", "ragSharedCatalogKey": shared_key,
    })
    check("shared_rag_settings_roundtrip",
          valid_rag.status_code == 200
          and valid_rag.json()["settings"]["ragCatalogMode"] == "shared"
          and valid_rag.json()["settings"]["ragSharedCatalogKey"] == shared_key,
          valid_rag.text)

    private_url = client.post(
        "/api/meetings/from-url",
        headers=auth,
        json={"url": "http://127.0.0.1/private.mkv"})
    check("url_import_rejects_private_destination",
          private_url.status_code == 400, private_url.text)

    oversized = client.post(
        "/api/meetings/upload",
        headers=auth,
        files={"file": ("meeting.mkv", b"x" * 33, "video/x-matroska")})
    check("upload_limit_is_enforced",
          oversized.status_code == 413, oversized.text)

    check("upload_name_strips_client_paths",
          _safe_upload_name(r"..\..\secret\meeting.mkv") == "meeting.mkv"
          and _safe_upload_name("../../meeting.mkv") == "meeting.mkv")

    old_transcripts_dir = meeting_routes.TRANSCRIPTS_DIR
    try:
        meeting_routes.TRANSCRIPTS_DIR = Path(_tmp.name) / "transcripts"
        artifact_dir = meeting_routes.TRANSCRIPTS_DIR / "42"
        (artifact_dir / "exports").mkdir(parents=True)
        (artifact_dir / "trace.json").write_text("trace", encoding="utf-8")
        (artifact_dir / "exports" / "summary.html").write_text(
            "export", encoding="utf-8")
        removed = _remove_meeting_directory(42)
        check("meeting_delete_removes_trace_and_exports",
              removed and not artifact_dir.exists())
        check("meeting_delete_is_scoped",
              not _remove_meeting_directory(43)
              and meeting_routes.TRANSCRIPTS_DIR.exists())
    finally:
        meeting_routes.TRANSCRIPTS_DIR = old_transcripts_dir

asyncio.run(queue_checks())
_tmp.cleanup()

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}")
    raise SystemExit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
