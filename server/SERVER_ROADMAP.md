# Server (web layer) — port state & roadmap

> **Language:** internal development document, deliberately English-only - unlike
> the user-facing docs (README, ARCHITECTURE, DEPLOYMENT, MCP_USAGE, google-sheets,
> WHISPER_ENGINES_COMPATIBILITY), which all ship in both English and Russian.

Multi-user **web** version of Meeting Summarizer: a FastAPI backend + web dashboard
that wraps the SAME verified processing backend the desktop client uses. The heavy
work runs in the embedded runtime (`backend/python/python.exe`) via subprocess; the
FastAPI process itself needs no torch.

## Architecture (owner decisions)

- **Feature scope:** core-first (auth → upload → transcribe → summary → analysis →
  download), then the rest in phase 2.
- **DB:** SQLite (`config/server.db`, via aiosqlite) — zero external DB server;
  `DATABASE_URL` env overrides (e.g. Postgres) if ever needed.
- **Runtime split:** FastAPI runs in its own light venv (`server/.venv`, no torch);
  processing is invoked as a subprocess into the embedded Python.
- **No duplication:** the worker reuses the desktop's Qt-free adapters
  (`desktop.app.backend.transcription / summarization / analysis`) which build the
  argv for `processor.py` / `ai_client.py` and default to the embedded runtime.

## Run

```
server\.venv\Scripts\python.exe -m pip install -r server\requirements.txt   # one-time
server\.venv\Scripts\python.exe server\run_server.py                         # PORT=8000 default
```
Web UI at `/`, API docs at `/api/docs`, health at `/health`.

## Phase 1 — core [DONE, live-verified 2026-07-02]

- **SQLite** — `database/db.py` reworked (aiosqlite async + sync engine for alembic);
  `requirements.txt` dropped asyncpg/psycopg2, added aiosqlite. Tables auto-create on
  startup.
- **Package consistency** — the server is now a real `server.*` package (added
  `__init__.py` to server/api/auth/database; `main.py` switched from absolute
  (`from auth…`) to relative imports). Fixes the previous "attempted relative import
  beyond top-level package" break.
- **Worker = full pipeline** — `processing/worker.py` rewritten: transcribe (all
  engines + diarization) → summary (ai_client) → analysis (per enabled feature,
  reusing the analysis prompts/gating/parsing) → paths stored in DB; progress streamed
  over WebSocket. Uses the embedded Python (was `sys.executable`). Per-feature analysis
  failures are non-fatal.
- **Settings** — per-user `UserSettings.settings_json` mirrors the desktop settings
  keys; the worker merges user settings over sensible defaults.
- **Auth fixes** — JWT `sub` now stored as a string (jose rejects a non-string sub →
  was the "Could not validate credentials" bug); `bcrypt` pinned to `4.0.1` (passlib
  1.7.4 crashes on bcrypt ≥ 4.1).
- **Wiring** — upload → `processing_queue.add_meeting` → N async workers →
  `worker.process_meeting`; `main.py` startup runs `init_db()` + `queue.start()`.
- **Verified:** `server/_selftest_core.py` (16 — command building on the embedded
  python for transcription/summary/analysis, gating, `_ai_kwargs`, SQLite round-trip,
  settings merge) + a LIVE API smoke (register → login → `/me` → settings put/get →
  meetings list → queue status all 200 on a real uvicorn + SQLite).

## Phase 2 — personal cabinet (in progress)

**Already there (ported), verified against the code:** multi-worker parallel processing
+ queue (`processing/queue.py`, N async workers, adjustable via `/api/queue/workers/{n}`),
per-user processing **history** + coarse **statuses** (`Meeting` + `ProcessingLog`,
`GET /api/meetings/`).

- **[DONE] Live progress + stage + ETA (persisted).** Added `Meeting.progress/stage/
  eta_seconds`; the worker persists them at each step (throttled) via `_set_progress`
  (ETA = elapsed-rate estimate) — so the cabinet shows real status across page reloads,
  not only over WebSocket. Exposed in `MeetingResponse` and a light
  `GET /api/meetings/{id}/status` for polling. Verified: `_selftest_core.py` (18 —
  progress persisted + ETA computed).
- **[DONE] Engines/models catalog API.** `GET /api/engines` returns the SAME catalog the
  desktop uses (`models_cli catalog` in the embedded runtime: 8 engines + per-model
  availability). `POST /api/engines/{engine}/models/{model}/download` (admin-only, shared
  resource) runs a background download with progress in `GET /api/engines/downloads`.
  Verified live (catalog lists all engines + availability; admin gate returns 403 for
  a normal user).
- **[DONE] Structured settings.** `/api/settings` is now TYPED (`SettingsData`): GET
  returns defaults merged with the user's saved values; PUT accepts a partial update and
  merges it (unknown/future keys preserved). Small enums validated — `whisperDevice ∈
  {auto,cuda,cpu}`, language, diarization — so the cabinet can safely pick e.g. CUDA for
  GPU transcription (invalid values → 422). Still stored as the JSON blob the worker reads.
- **[DONE — mechanics; live-validate on a long meeting] Long-meeting handling.** A 3-4h
  meeting on a local LLM used to hit the ai_client 10-min timeout (huge transcript +
  reasoning overhead). Added, in the shared `backend/ai_client.py`:
  * **Chunking is a FALLBACK, not the norm.** A transcript over `--chunk-chars`
    (default **48000** ≈ 20k tokens) is processed in parts then combined; below it, the
    transcript goes WHOLE (full context, best quality). Chunking costs some cross-part
    context, so the threshold should match the model: big-context models (Qwen 262k) set
    `chunkChars` high (~400000) so even a 4h meeting is ONE pass. **Summary** uses
    map-reduce (`chunk_mode=summary`: parts summarised → combined, recursive if huge);
    **analysis** uses `chunk_mode=uniform` (the feature prompt runs on each part, results
    combined) so a feature still works when the transcript exceeds context.
  * **Flexible timeout** — `--timeout` (per-request seconds; old hardcoded 600 = default).
    Setting `aiTimeout`.
  * **Reasoning toggle** — `--no-think` (`chat_template_kwargs.enable_thinking=false`) to
    skip the <think> phase. Setting `disableReasoning` (user decides — quality vs speed).
  * **Retry policy** — the local model may crash and be auto-restarted by a watchdog
    (~2-3 min); ai_client retries CONNECTION failures with escalating backoff. Settings
    `aiRetries` (default 3) / `aiRetryDelay` (60).
  * **GPU hand-off** — `backend/gpu_handoff.py`: when `gpuHandoff` is on and the device is
    cuda/auto, the worker stops the local LLM (frees VRAM) + drops a lock the user's
    watchdog honours, transcribes on the GPU, then releases the lock (watchdog restarts the
    LLM; retry waits). Lets a 22GB LLM and GPU transcription coexist by TIME-sharing the
    card. Watchdog change: `~/.hermes/scripts/healthcheck.py` skips the LLM restart while a
    fresh lock exists (stale >90min ignored). Settings `gpuHandoff` / `llamaPort`.
  * **Chunking is opt-in** — setting `chunkingEnabled` defaults off. Off =
    the transcript is ALWAYS sent whole (best quality, no context break); if it exceeds the
    model context it simply errors — the user's call, since chunking affects output quality.
    A settings UI must WARN about this trade-off (TODO for the web dashboard).
  Threaded `summarization.build_command` / `analysis.build_feature_command` →
  `worker._ai_kwargs` → settings. Verified: `_selftest_ai_chunking.py` (26), `_selftest_ai_provider`
  (22), server core (18); gpu_handoff lock + healthcheck staleness unit-checked.
  **LIVE-VALIDATED (2026-07-03):** full local pipeline on a real 4h meeting via Qwen (35B MoE,
  262k ctx). Two runs (reasoning ON / OFF), each ~29-30 min: clean GPU hand-off (transcription
  alone on the card ~3GB, Qwen unloaded, watchdog held the lock) → hand-off release → Qwen
  restarted by the watchdog → **retry waited ~6 min for it** → summary + analysis over the WHOLE
  72k-token transcript (chunkChars 400000 → no chunking, full context) → completed. Output
  quality high both ways; reasoning OFF was as good (better participant coverage) AND faster
  (~4 vs ~7 min summary). NOTE: the watchdog edit had to go into the RUNNING file
  `%LOCALAPPDATA%\hermes\scripts\healthcheck.py` (the ``~/.hermes`` copy the
  owner pointed to was a stale dev copy — both patched).
- **[DONE] Exports.** `GET /api/meetings/{id}/export/{kind}/{fmt}` — raw/summary/analysis
  × txt/md/json/html/pdf/docx, reusing the desktop Qt-free `exporter` in-process (reportlab
  + python-docx added to the server venv). Live-verified all six formats (valid PDF/DOCX/
  HTML/JSON signatures; bad format → 400). Google Sheets auto-export on finish
  (`worker._maybe_export_gsheets` reusing `gsheets`, best-effort; settings
  `googleSheetsIntegration`/`googleSheetsUrl`). Obsidian is N/A for the server (the vault
  lives on the client machine).
- **[DONE] Summary/analysis versions + Regenerate.** New `Artifact` table (meeting_id,
  kind, version, path, provider, source_summary_version) — each run/Regenerate writes a
  NEW version (v1 = `<stem>_summary.txt`, v2+ = `_v2`, matching the desktop) instead of
  overwriting; `Meeting.summary_path`/`analysis_path` keep pointing at the latest.
  `GET /api/meetings/{id}/versions` lists them; `POST /api/meetings/{id}/regenerate`
  re-runs summary+analysis from the existing transcript (queue gained a `regenerate` flag
  → `worker.process_meeting(regenerate=True)` skips transcription); the export endpoint
  takes `?version=N`. Live-verified: versions listed, v1≠v2 export, v99→404, regenerate→202.
- **[DONE] RAG + search.** `server/api/routes/rag.py`: semantic KB reusing `backend/rag.py`
  (chromadb) as a subprocess in the embedded runtime — per-user `rag_data/u<id>` isolation
  by default, or explicit capability-keyed sharing with desktop through
  `rag_shared/<sha256>` in the same installation. Shared writes are serialized. It uses
  the user's own embedding settings. Endpoints: `POST /api/rag/meetings/{id}` (index),
  `GET /api/rag/search`, `/library`, `/stats`, `DELETE /api/rag/meetings/{id}`, and
  `GET /api/rag/textsearch` (plain-text/regex over the user's transcripts, in-process via the
  desktop `textsearch`). Added a `Meeting.project` column (RAG + contextual-memory scope) with
  a light SQLite migration in `init_db` (`_ensure_columns` ALTERs old DBs for project/progress/
  stage/eta). Live-verified: textsearch (Redis/CI-CD hits), stats, per-user routes; **semantic
  add/search wiring reaches the embeddings endpoint** but the owner's llama.cpp needs
  `--pooling mean` for OpenAI-compatible `/v1/embeddings` (it returned "Pooling type 'none' is
  not OAI compatible") — a model-launch config, not a server bug (or use a dedicated embedding
  model / cloud `ragEmbedding*`).
- **[DONE] Prompt templates.** `server/api/routes/templates.py`: the built-in library
  (12 meeting types × RU/EN, each with a speaker-aware variant) is served read-only, reused
  verbatim from the desktop's Qt-free `templates.builtin_templates(lang, speaker)`; user
  templates are per-user `UserTemplate` rows with create/edit-rename/delete. `GET /api/templates
  ?lang=&speaker=` returns `{builtin, user}`; `POST` (save, replaces same-name), `PUT/{id}`
  (edit/rename), `DELETE/{id}`. Live-verified: 13 built-ins, speaker variant differs, full user
  CRUD.
- **[DONE] Contextual memory.** `worker._contextual_memory_block` — opt-in
  (`useContextualMemory` setting, default off) and STRICTLY project-scoped: when on and the
  meeting has a `project`, it appends the latest summaries of PRIOR meetings of the SAME
  project (same user) to the summary prompt (bounded: last 3, ~1500 chars each, ~6000 total).
  A different-topic meeting is never mixed in. Verified: `ctx_test.py` (7 — same-project
  included, other project + self excluded, off/no-project empty).
- **[DONE] Speaker management.** Reusing the desktop Qt-free `speakers.py`:
  `GET /api/meetings/{id}/speakers` (diarisation labels + per-speaker stats),
  `POST /api/meetings/{id}/speakers/rename` (map SPEAKER_NN → display names, rewrites the
  transcript in place so downstream regenerate uses the renamed version),
  `GET /api/meetings/{id}/export-by-speaker` (one file per speaker, returned as a zip).
  Live-verified: labels+stats, rename persisted, zip export.

**→ Desktop-parity advanced features are COMPLETE** (exports, versions/Regenerate, RAG+search,
templates, contextual memory, speakers). The web layer now covers the desktop's feature set
via the API.

- **Remaining (non-parity):**
- **[DONE] Worker count** — `get_optimal_workers()` now probes CUDA + VRAM via the
  embedded python (`_probe_gpu()` subprocess), since the server venv is torch-free.
  Live: detects the 16 GB GPU → 4 workers (was CPU-fallback 2).
- **[DONE] Modernization** — `@app.on_event` startup/shutdown replaced with an
  `asynccontextmanager` `lifespan` handler (no deprecation; boot verified).
- **[DONE] Deployment** — `server/start_server.ps1` (production launcher: venv + repo-root
  CWD + env) and `server/DEPLOYMENT.md` (prereqs, env vars, autostart via Task Scheduler/
  NSSM, reverse-proxy/HTTPS, backups, updating). Docker intentionally skipped (Windows
  embedded-python + CUDA topology). Launcher verified (`/health` 200). Security: JWT signing
  key no longer defaults to a hardcoded value — `JWT_SECRET_KEY` env, else a persisted random
  secret at `config/.jwt_secret` (gitignored).
- **[DONE] Live full e2e** — a real 18 MB meeting processed end-to-end through the WEB:
  upload → CPU transcription (faster-whisper medium) → summary via local **Qwen**
  (`localEndpoint=http://127.0.0.1:8080/v1`, alias `gpt-4o`, reasoning off) → analysis
  (all 11 features) → completed (~11 min, transcription CPU-bound so Qwen kept the GPU).
  Verified: structured RU summary from the **restored** default prompt; full analysis JSON
  (actionItems/sentiment/category/risks/quotes/technologies/questions/recommendations/
  keyTopics/followupQuestions/formalProtocol); exports valid (docx/pdf/html/md); result
  renders in the redesigned UI. **Web layer now fully live-verified — mirrors desktop #11.**

**→ SERVER_ROADMAP COMPLETE.** All phases (core, cabinet parity, web UI, deployment, live e2e) done.

## Web UI (dashboard) — [DONE, redesigned]

Full Tailwind redesign (compiled static `app.css`, no runtime deps): login, dashboard,
meeting-detail modal (previews, versions, 6-format export, regenerate, speakers, RAG),
and a full **settings** modal at desktop parity — catalog-driven engine/model dropdowns,
transcription language (Русский/English), AI provider, prompt editor + template library
(the full original default prompt restored from `_old`), 5 analysis-feature toggles,
chunking opt-in with a quality warning, timeout/retries, GPU hand-off, contextual memory,
Google Sheets. Dark/light themes; `Cache-Control: no-cache` on `/static` and the HTML routes
so updates never serve stale assets. `GET /dashboard.html` route added (auth redirected to a
404 before).

## New features — both fronts (desktop + web), [DONE]

- **Output language ≠ transcription** (`outputLanguage` auto/ru/en): summary + analysis can be
  produced in a different language than the recording (translation). Shared in
  `desktop.app.backend.summarization.resolve_output_language`/`apply_output_language`; the
  worker passes it to the summary pass, analysis picks `ADVANCED_PROMPTS[output_lang]`. UI
  selector in the settings modal. Live-verified RU recording → EN summary+analysis via Qwen.
- **Ingest a video by URL** (YouTube / file server): `POST /api/meetings/from-url` stores a
  `source_url`; the worker downloads it first (`backend/url_download.py`: yt-dlp + fallback
  HTTP, single-file video, auto browser-cookie retry for YouTube anti-bot + a clear message;
  `youtubeCookiesBrowser` setting), then the SAME pipeline runs. Web UI: "Add by URL" field.
  Empty-transcript guard (silent media → clear error). New 14th template "Видео из сети".
  Live-verified: a real video pulled from a local file server → full pipeline → completed.

## Release closeout — 2026-07-29 (pre-GitHub audit, v1.2.1)

Full-surface audit with the emphasis on the server layer. Suite: **47 runners /
1279 checks, green twice in sequence**; exit codes proven honest in both
directions by injecting a deliberate failure into 8 runners.

**Reachability — five server capabilities the cabinet could never invoke.** Each
was implemented, tested and completely unreachable from the browser, i.e. a
feature no user could use:

| Capability | Endpoint | Was | Now |
|---|---|---|---|
| Knowledge-base listing | `GET /api/rag/library` | no client method, no UI | Knowledge-base modal |
| Drop one indexed document | `DELETE /api/rag/meetings/{id}` | no client method, no UI | Remove button per row |
| Knowledge-base size | `GET /api/rag/stats` | `ragStats()` existed, nothing called it | modal header |
| Edit a saved template | `PUT /api/templates/{id}` | no client method, no UI | "Update" button |
| Reset settings | `DELETE /api/settings/` | `resetSettings()` existed, nothing called it | "Reset to defaults" |

The desktop had all of these. Reachability is now **0 dead client methods**; the
six routes without a JS caller are page navigation (`/`, `/dashboard.html`) and
health (`/health`, `/api/health`), plus two the scanner cannot see because they
build their URL in a variable (`listMeetings`, `exportArtifact` — both verified
reachable by hand).

**Defects found and fixed**

- **Engine probe/install asked the wrong interpreter.** `GET /api/admin/engines/packages`
  called `importlib.util.find_spec()` *in the FastAPI process*, whose venv is
  deliberately torch-free, while engines actually run under `backend/python`. Every
  admin therefore saw all seven engines as "not installed" **while transcription
  worked perfectly**, and `POST /api/admin/engines/{engine}/install` used
  `sys.executable`, so it would have pip'd torch/CUDA into the torch-free venv
  without fixing anything. Both now target the embedded runtime; verified live —
  all 7 report `installed=true`.
- **…and then that runtime was hardcoded, which broke every `min` installation.**
  A `min` install has no `backend/python` at all: the engines go into whichever
  Python the user already had, recorded by `INSTALL.bat` in `config/interpreter.txt`.
  Spawning the absent path raised `FileNotFoundError` *before* any handler ran, so
  `GET /api/engines/`, `GET /api/admin/engines/packages` and the whole RAG section
  (index, stats, library, semantic search) answered a bare **500 "Internal Server
  Error"** — while the very same box transcribed and summarised fine, because the
  worker asks `paths.python_executable()`, which falls back. `server/runtime.py`
  now resolves it once (embedded → recorded interpreter → this process) and the
  three routes report an unrunnable interpreter *by name* instead of a bare 500.
  Found on a clean Windows 11 VM; re-verified there after the fix — RAG indexes
  (`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 384-dim) and the
  package probe truthfully reports `faster-whisper` present, `whisper` missing.
- **Registration refused intranet e-mail addresses.** `EmailStr` rejects special-use
  domains, so `user@nas.local`, `user@corp.internal` and the RFC 8375 home-network
  `user@host.home.arpa` all answered 422 *"special-use or reserved name"* — reading
  like a typo rather than policy, on exactly the deployment this cabinet targets.
  That rule exists to stop software mailing into the void; this project contains no
  `smtplib` at all, so the address is only an account identifier and a uniqueness
  key. Those names are now accepted. Syntax validation is untouched, including the
  requirement that the domain carry a dot, so `admin@localhost` and `not-an-email`
  are still refused, as is `.invalid` (RFC 2606 reserves it to mean precisely that).
- **A server restart stranded in-flight meetings forever.** Nothing survives the
  process, so a row left `processing`/`queued` belonged to a dead worker. The queue
  no longer owned it, so Cancel answered *"neither queued nor processing"*, and a
  meeting orphaned during **transcription** has no transcript, so Regenerate refused
  too — no way out at all. `reconcile_orphaned_jobs()` now runs at startup: a kept
  transcript → `failed` with a reason pointing at Regenerate; nothing kept →
  `uploaded` so Process can start again; finished rows untouched.
- **A restarted run kept showing the previous run's error.** `regenerate` and
  `process` left `error_message` set, so a meeting actively re-processing displayed
  "Interrupted: the server stopped…". The desktop already cleared it on the
  transition to processing (`history.set_status`) — the server was the outlier.
- **`api.js` defined `updateMeeting()` twice**; the second silently won. Removed.
- **Stale architecture boundary.** ARCHITECTURE(.ru).md still listed the microphone
  recorder and Trim preview as desktop-only after both shipped in the cabinet. Now
  only the local-AI manager, Diagnostics and MCP registration are desktop-only, each
  because it drives the local machine.

**Verified end-to-end on real data** — a 350 s recording through the live pipeline
(faster-whisper → summary → 11-feature analysis on a local 34B llama.cpp model):
`duration` matches ffprobe to the second; two concurrent jobs stay independent;
Cancel yields `cancelled` (not `failed`), kills the child process and leaves the
parallel job alone; all **12 exports decode for real** with body text identical
across md/html/docx/pdf (98/98 words); versions v1/v2/v3 are separate files with
different content; transcript edits persist; RAG indexes 10 chunks and answers
semantic search; Obsidian writes 21 notes; all 6 MCP tools return real data; delete
cascades to files and directories and leaves other meetings intact.
