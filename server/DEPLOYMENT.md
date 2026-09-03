# Deployment — Meeting Summarizer web server

**English** · [Русский](DEPLOYMENT.ru.md)

The web layer is a FastAPI app (`server/`) on **SQLite** that drives processing through the
**embedded python runtime** (`backend/python/python.exe`) as a subprocess. There is no
Postgres and no separate model service — a single machine with the bundled runtime runs
everything. Docker is intentionally **not** used: the processing runtime is a Windows
embedded python with CUDA, which does not fit a Linux container. Deploy as a **service /
scheduled task** on the host instead.

## 1. Prerequisites

- The repo with the bundled `backend/python/` runtime and the transcription/AI models.
- A **server venv** (light, no torch) with the web dependencies:
  ```powershell
  py -m venv server\.venv
  server\.venv\Scripts\pip install -r server\requirements.txt
  ```
- For GPU transcription and worker auto-scaling: an NVIDIA GPU (the embedded runtime has
  torch/CUDA; the server venv does not — GPU is probed via the embedded python).

## 2. Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `JWT_SECRET_KEY` | *(auto-generated, persisted to `config/.jwt_secret`)* | **Set this in production.** Signs auth tokens. If unset, a random secret is generated and stored so tokens survive restarts, but setting it explicitly is recommended (and lets you rotate/scale). |
| `PORT` | `8000` | Listen port. |
| `HOST` | `0.0.0.0` | Bind address. |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:8000` | Comma-separated CORS origins. Set to your real origin(s). |
| `MAX_UPLOAD_BYTES` | `10737418240` (10 GiB) | Hard per-file upload limit, enforced while streaming even when `Content-Length` is absent or forged. |
| `ALLOW_PRIVATE_URLS` | `false` | Keep `false` on an Internet-facing server to reject URL imports that resolve to loopback, private, link-local, or other non-public addresses. Set `true` only for a trusted internal deployment that intentionally imports intranet media. |
| `DATABASE_URL` | `sqlite:///<repo>/config/server.db` | Async SQLite URL. Change only to relocate the DB. |
| `TRUSTED_PROXIES` | `127.0.0.1` | Which peers may set `X-Forwarded-For`/`X-Forwarded-Proto`. Set to the reverse proxy's address when it is not on this host. |
| `SERVER_MODE` | *(set by the launcher)* | Must be `true`; the launcher sets it. |

Changing `JWT_SECRET_KEY` invalidates all existing tokens once (everyone re-logs in).

## 3. First run and the administrator

The **first account registered on a fresh installation becomes the administrator** — that is
the operator's account. Everyone who registers afterwards is a normal user who sees only
their own meetings.

An administrator gets an **Administration** button in the cabinet header (nobody else is
shown it) covering everything that is shared by the whole installation:

| Operation | Effect | Endpoint |
|---|---|---|
| Parallel workers | load management for the machine; **persisted**, so it survives a restart instead of reverting to hardware auto-detection | `PUT /api/admin/settings`, `POST /api/queue/workers/{n}` |
| Download a transcription model | a file on disk that every account then uses | `POST /api/engines/{engine}/models/{model}/download` |
| Check a model for updates | compares the local revision with the published one | `GET /api/engines/{engine}/models/{model}/update-check` |
| Install an engine's Python packages | changes the installation for every user | `POST /api/admin/engines/{engine}/install` |

Everything else — AI provider, prompts, analysis features, Obsidian vault, RAG — stays
**per-user**: two accounts on one server can work with different models and providers.
Interface language and theme are chosen in the browser and are not server settings at all.

To promote somebody later, set the role directly in the database (the server may be
running):

```powershell
backend\python\python.exe -c "import sqlite3; c=sqlite3.connect(r'config/server.db'); c.execute(\"update users set role='admin' where username=?\", ('ivan',)); c.commit(); print(c.execute('select username, role from users').fetchall())"
```

If you deploy from a **distribution archive** rather than the repository, the venv step
above is not needed:

- **full** — unzip and run `SERVER.bat`; the bundled runtime already has every dependency.
- **min** — unzip, run `INSTALL.bat` once and tick *Web cabinet* in the component list,
  then run `SERVER.bat`. Python does not have to be installed beforehand: if the machine
  has none, `INSTALL.bat` offers to install 3.11 itself. If the component is left
  unticked, `SERVER.bat` says what is missing instead of raising ImportError.

## 4. Run

```powershell
# defaults (0.0.0.0:8000)
server\start_server.ps1

# custom port + production secret
$env:JWT_SECRET_KEY = "<long-random-string>"
server\start_server.ps1 -Port 9000
```

The launcher sets `SERVER_MODE`, runs from the repo root (so `uploads/`, `transcripts/`,
`config/` resolve consistently) and starts uvicorn via the server venv. The UI is served at
`/` (login) and `/dashboard.html`; the API docs at `/api/docs`.

## 5. Autostart (run on boot, restart on crash)

**Task Scheduler** (simplest, no extra tools):

```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"C:\Scripts\meeting-summarizer\server\start_server.ps1`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$set     = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "MeetingSummarizerServer" -Action $action -Trigger $trigger `
    -Settings $set -RunLevel Highest -User "SYSTEM"
```

Set `JWT_SECRET_KEY` (and any other vars) as **system** environment variables so the task
picks them up, or add them at the top of `start_server.ps1`.

For a true Windows *service* with supervision, wrap the same command with
[NSSM](https://nssm.cc/): `nssm install MeetingSummarizerServer powershell.exe "-NoProfile -ExecutionPolicy Bypass -File C:\Scripts\meeting-summarizer\server\start_server.ps1"`.

## 6. Network / HTTPS

The app serves plain HTTP. For remote/production access put it behind a reverse proxy
(IIS/ARR, Caddy, or nginx) terminating TLS and forwarding to `HOST:PORT`. Restrict
`ALLOWED_ORIGINS` to the public origin.

The browser side needs nothing configured: the UI derives the API base from
`window.location.origin` and upgrades the live-progress socket to `wss://` by itself when the
page is served over HTTPS. The proxy, however, needs three settings that defaults get wrong:

| Setting | Why |
|---|---|
| **WebSocket upgrade on `/ws/`** with a long read timeout (≥ 1 h) | live progress is one connection held open for the whole run; nginx's default 60 s read timeout closes it mid-transcription |
| **Max request body ≥ your upload limit** (`client_max_body_size` in nginx, `maxAllowedContentLength`/`maxRequestLength` in IIS) | uploads are whole meeting recordings; nginx's 1 MB default rejects every one of them. Keep it in step with `MAX_UPLOAD_BYTES` |
| **Forward `X-Forwarded-For` / `X-Forwarded-Proto`** | the server reads them (`proxy_headers=True`); set `TRUSTED_PROXIES` to the proxy's address if it is not `127.0.0.1` |

nginx, the parts that matter:

```nginx
server {
    server_name meetings.example.com;
    client_max_body_size 10g;                  # match MAX_UPLOAD_BYTES

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

Caddy needs only `reverse_proxy 127.0.0.1:8000` (WebSockets and forwarded headers are
automatic) plus `request_body { max_size 10GB }`.

Note on the host: the processing runtime is a **Windows** embedded python with CUDA, so the
VPS must run Windows. A Linux VPS can only serve as the TLS front — the app itself does not
run there.

## 7. Data & backups

Persisted state lives under the repo:

- `config/server.db` — users, meetings, settings, artifact versions.
- `config/.jwt_secret` — the signing key (back up if you rely on it instead of the env var).
- `uploads/` — original media; `transcripts/` — transcripts + generated exports.
- `rag_data/u<id>/` — isolated per-user knowledge base (the default).
- `rag_shared/<key sha256>/` — catalogs shared by a server account and desktop in the same
  installation via a secret code. The raw code is stored in the user's DB settings.

Back up `config/`, `transcripts/`, `rag_data/` and `rag_shared/` (and `uploads/` if you keep
source media). SQLite is a
single file — copy `config/server.db` while the server is stopped, or use `.backup`.

## 8. Updating

1. Stop the service/task.
2. Pull the new code; if `server/requirements.txt` changed:
   `server\.venv\Scripts\pip install -r server\requirements.txt`.
3. If the web UI changed, rebuild the stylesheet: `cd server\web; npm run build:css`.
4. Start the service. Static assets send `Cache-Control: no-cache`, so browsers pick up new
   JS/CSS on the next load (no hard refresh needed). DB migrations are additive and applied
   automatically on startup (`init_db` → `_ensure_columns`).
