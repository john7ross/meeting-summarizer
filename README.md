# Meeting Summarizer

![Version](https://img.shields.io/badge/version-1.3.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

**English** · [Русский](README.ru.md)

<p align="center">
  <img src="Promo-GitHub.gif" alt="promo" width="200"/>
</p>

Local-first tool that turns a meeting recording into a structured **summary** and a rich
**analysis** — transcription (7 engines, offline-capable) → summary → 11-feature analysis →
exports (txt/md/json/html/pdf/docx, Obsidian, Google Sheets). Everything can run on your own
machine; cloud AI providers are optional.

## Two front-ends, one backend

The verified Python backend (`backend/`) is shared by two independent front-ends:

- **Desktop client** (`desktop/`) — native **PySide6** app: drag-and-drop, add by URL,
  meeting recording (microphone **and system audio**, with live transcription and a live
  summary while the meeting runs), per-meeting trimming, live queue, a separate processing
  history (the journal of every run, with stage timings and errors), scoped regeneration
  (summary / analysis / both), speaker management, analysis panels, RAG search,
  diagnostics.
- **Web cabinet** (`server/`) — multi-user **FastAPI** service (JWT auth, SQLite) with a
  compiled-Tailwind dashboard. Same feature set, over HTTP — including microphone
  recording in the browser (needs HTTPS or a localhost origin), splitting one
  recording into separate meetings on a waveform, search, statistics, managing the
  knowledge base (what is indexed, its size, dropping a document) and export to
  an Obsidian vault. Whatever the whole installation shares — the worker count,
  model downloads and updates, engine installation — is **administrator-only** and
  applies to every account; everything else is per-user.

Both drive the same embedded runtime (`backend/python`) as subprocesses.

## Run it

You need a running AI target — a local endpoint, a cloud key, a local agent CLI, or the
built-in local AI (below).

**Launch — just double-click, no console needed:** `RUN.bat` (desktop) or `SERVER.bat` (web
cabinet, then open `http://localhost:8000`). The same files sit at the root of the portable
build. Console equivalents: `backend\python\python.exe desktop\run.py` /
`backend\python\python.exe server\run_server.py`.

Then: **Settings** → pick how the AI runs (see below); engine `faster-whisper`, model
`medium`. If a local LLM occupies the whole GPU, tick **"Free VRAM for transcription"** — the
app stops it during transcription and restores it afterwards. Add a file (or record one),
optionally split it into per-meeting segments, press **Process**.

## Four ways to process a transcript

1. **Local endpoint** — any OpenAI-compatible server you already run (llama.cpp, LM Studio,
   Ollama).
2. **Cloud** — OpenAI / Anthropic / Google / xAI / Qwen / Mistral / DeepSeek by API key, plus
   a fully custom Advanced-API request.
3. **Local agent CLI** — hand the work to **Claude Code**, **Codex**, **Hermes** or any
   command that reads stdin and prints an answer. Keys and model stay in the agent's config.
4. **Built-in local AI** — for users who run nothing of their own: the app downloads the
   current llama.cpp build plus a model sized to the machine and starts it. Not part of the
   distribution — fetched on demand.

On a single GPU a resident LLM leaves no room for the transcription engine, so the app can
hand the GPU over: it stops the local model (yours by port, its own by id) for the duration of
the transcription and brings it back afterwards. To keep it up across crashes and reboots as
well, run the watchdog — it stays out of the way while a transcription holds the GPU:

```bash
python backend/local_ai_watchdog.py --model qwen3-14b        # supervise continuously
python backend/local_ai_watchdog.py --once                   # one check, for Task Scheduler
```

An agent can also work the other way round: the **MCP server** exposes the meeting archive as
tools (`list_meetings`, `get_transcript`, `get_summary`, `get_analysis`, `search_transcripts`,
`search_knowledge`) — see [docs/MCP_USAGE.md](docs/MCP_USAGE.md).

## Live mode: watch the meeting being transcribed

Recording a meeting can run two extra passes **while it happens**, both optional and both off
by default (Settings → *Recording & live mode*, or the switches in the recorder window):

- **System audio.** The microphone records you; WASAPI loopback records what your speakers
  play — i.e. everyone else on the call. They are written as the two channels of one WAV
  (mic left, system right), so the archived recording holds the whole conversation and the
  live panel can tell "you" from "them" without diarisation. Needs the optional `soundcard`
  package; without it the recorder simply stays mic-only and says so.
- **Live transcription.** The same PCM that goes to the WAV is streamed to a recognition
  engine held open for the whole meeting, and recognised phrases appear as they are spoken.
  It runs on the engines you already have — `faster-whisper`, `whisperx`, `whisper`, `vosk`,
  `sherpa-onnx`, `whisper-cpp` — offline, with no cloud account. You can point live at a
  different engine/model than the batch pass, e.g. keep `medium` on the GPU for the archived
  transcript and run live on a small CPU model.
- **Live summary.** A rolling summary of the meeting — topics, decisions, action items, open
  questions — refreshed every ~30 seconds through the SAME AI provider the post-meeting
  summary uses (local endpoint, cloud key, agent CLI or the built-in model). On a local model
  every update is rebuilt from the transcript, so an early mistake cannot set; on a metered
  cloud it goes incremental with periodic rebuilds, and an update cap keeps the bill bounded.

Both live results are written next to the recording (`*_live_transcript.txt`,
`*_live_summary.json`) as they are produced, so closing the window does not lose them.

**When the meeting ends you choose how it is processed.** *Process* transcribes the recording
again exactly as before. *Process from live text* takes the transcript live mode already
produced and goes straight to summary and analysis — the same summary, the same 11-feature
analysis, the same versions, exports, Obsidian and Sheets. Only the transcription pass is
skipped, because it already happened during the meeting.

**Live is a third intake channel, and everything works with it.** A meeting now arrives as a
file, as a URL, or live — and the channel travels with it: the queue row and the results panel
say which it was, the processing journal records it, and the Diagnostics profile shows the
stages that really ran. What is *produced* is deliberately identical either way; how it got
in is the only thing that differs, so that is the only thing that is marked.

## Running from a git clone

Most people should take a [release](https://github.com/john7ross/meeting-summarizer/releases) — a clone is source only. The
embedded runtime (6.4 GB) and FFmpeg (385 MB) are not in git: GitHub rejects any
file over 100 MB and both FFmpeg binaries are 193 MB each. Run `INSTALL.bat` once
and it fills in everything that is missing, including Python itself and FFmpeg:

```
git clone https://github.com/john7ross/meeting-summarizer.git
cd meeting-summarizer
INSTALL.bat
RUN.bat          :: or SERVER.bat for the web cabinet
```

`RUN.bat` and `SERVER.bat` are the same files the archives ship: they find the
interpreter at run time (embedded runtime → the one `INSTALL.bat` recorded → PATH),
so they work in a clone, a `min` install and an unpacked `full` build alike.

## Build the distributions

```
backend\python\python.exe desktop\packaging\build.py --variant min  --out dist
backend\python\python.exe desktop\packaging\build.py --variant full --out dist
```

- **min** (~320 MB) — source + ffmpeg + installer; the recipient runs `INSTALL.bat` once.
  It scans the machine (Python, RAM, disk, GPU/VRAM), recommends a CUDA or CPU torch build,
  and lets the user pick engines, models, RAG, the web cabinet and an optional local LLM —
  saying plainly when the hardware cannot run one. `--recommended --yes` skips the menus;
  `--plan-only` prints the plan without installing.
- **full** (~12 GB) — embedded runtime + a medium model per engine; unzip and run, no network.

## Requirements

Windows; an NVIDIA GPU (CUDA 12.4 runtime) recommended for fast transcription (CPU works, slower). The
full build bundles the runtime; the min build only needs `INSTALL.bat`, which installs Python itself
if the machine has none. Local AI needs an
OpenAI-compatible endpoint or an agent CLI; cloud providers need an API key.

**The `min` build needs Python 3.9 – 3.12 (3.11 recommended).** If there is no Python at all,
`INSTALL.bat` offers to install 3.11 itself — for the current user only, from python.org, and only
after verifying the Python Software Foundation signature on the download. Decline and it prints what
to install by hand. Not 3.13+ — the pinned `numpy<2.0`, which the current torch and engine versions
require, publishes no wheels for it, so pip would try to compile numpy from source. If such a version
is already installed, `INSTALL.bat` stops with instructions rather than failing halfway; several
versions can coexist (`py -0p` lists them) and it picks a supported one on its own. Note that on a
clean Windows 11 the `python` on PATH is a Microsoft Store stub, not an interpreter — do not install
from there, it is 3.13+. The `full` build is unaffected — it carries its own 3.11.

## Documentation map

- [desktop/README.md](desktop/README.md) — desktop: features, run, models, settings.
- [desktop/ARCHITECTURE.md](desktop/ARCHITECTURE.md) — layers and data flow (both front-ends) +
  [C4 component](desktop/architecture-c4-component.puml) and
  [sequence](desktop/architecture-sequence.puml) diagrams.
- [desktop/ROADMAP.md](desktop/ROADMAP.md) — state and history of the desktop.
- [docs/MCP_USAGE.md](docs/MCP_USAGE.md) — driving the meeting archive from an agent.
- [docs/google-sheets/README.md](docs/google-sheets/README.md) — Google Sheets integration (`code.gs` + setup).
- [server/DEPLOYMENT.md](server/DEPLOYMENT.md) — deploy the web cabinet; API docs at `/api/docs`.
- [server/SERVER_ROADMAP.md](server/SERVER_ROADMAP.md) — state and history of the web layer.
- [WHISPER_ENGINES_COMPATIBILITY.md](WHISPER_ENGINES_COMPATIBILITY.md) — engines and models.
- [CONTRIBUTING.md](CONTRIBUTING.md) — how to run it, how the self-tests work, what a PR needs.
- [SECURITY.md](SECURITY.md) — reporting a vulnerability privately, and what never to attach.
- [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) — what the archives bundle and under which
  licence. The distribution ships a **GPLv3** FFmpeg build and (in `full`) **LGPL** Qt; their
  texts travel inside the archives in [licenses/](licenses/).

## Support author

<p align="center">
  <img src="donate-qr.png" alt="Donate QR" width="200"/>
</p>

BTC: bc1q3frrup5neh7nhfg944etu2agd4j9u0vg3jyee6

ETH(Arbitrum): 0x43B349d8Cea83215D707EBa3bc35e9917f746b0a

TRX: THSzvy49KNeqRjXsGkurh2A5G4avV4RgN4

XRP: rLWZjS3DMupC4ZdXCX3BVYn4dEtC3iNhgy

SOL: 3xwfybxJ6Tz5t6pjBBkL5yYQCZo6wfbv932UNA4ThdP8

ADA: addr1q926ys75jp5wn2pv32a3t8r8pdhr7w02v0t9j4a8pmg0ruww5rlkctu4lnz2hfcwa5qfn3zhsd0s23r22uqwzx9gu6cq5c4e76

TON: UQC4qlAOD9Nly4K_66GJ_yCsSM3x2sB0vZ2GrBQbc--gZUui

DOGE: DTjNYmbtymzcjUiV4MsZY8MP4dM7MJ6qLC

XMR: 44qRqM6YtnxXUhkgCFqDDrKMPjWriu69FLBoop8Kwp7e1VQsBUJoVQ8JYQjfMV5C6uidTUgSSyoJ65mq8aYG2esZ1rrqfwt
