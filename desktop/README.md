# Meeting Summarizer — Desktop (PySide6)

**English** · [Русский](README.ru.md)

Native desktop client for turning meeting recordings into **transcripts → summaries →
advanced analysis**, offline-first and multi-engine. It is a PySide6 rewrite of the UI
layer of the original Electron app; the verified Python backend is reused unchanged and
driven as CLI subprocesses (process isolation, natural parallelism).

Version 1.3.0.

## What it does

- **Transcription — 7 built-in engines**, so you can compare and pick per meeting:
  OpenAI Whisper, Faster-Whisper, WhisperX (with speaker diarization), Vosk, sherpa-onnx,
  whisper.cpp, and FunASR (SenseVoice/Paraformer, English). RU+EN local coverage on all
  except FunASR (EN-only). Plus download-only **extra** community models (GigaAM RU,
  Moonshine EN) that are never bundled.
- **Speaker diarization** — offline **sherpa-onnx** by default (ungated, no token, works
  out of the box for every user), optional **pyannote** for users who supply their own HF
  token, or off. After a diarized run you can rename speakers and edit utterances.
- **Summaries** — 9 AI providers (local/OpenAI-compatible/Anthropic/Google/xAI/…), with
  explicit **model selection** and an Advanced-API modal for fully custom endpoints. Prompt
  **templates**: 13 meeting types × RU/EN (incl. "Видео из сети" for educational videos),
  each with a speaker-aware variant, plus your own saved templates (edit/rename/import/export).
  The default prompt is the full detailed original.
- **Output language** — summary + analysis can be produced in a language different from the
  transcription (e.g. an English recording → a Russian summary); `outputLanguage` = auto/ru/en.
- **Add by URL** — paste a YouTube or file-server link; the video is downloaded (yt-dlp, with
  automatic browser-cookie reuse for YouTube sign-in) and then processed exactly like an
  uploaded file.
- **Record a meeting, and watch it being transcribed** — a built-in recorder (🎙 in the
  header) captures straight to 16 kHz WAV (device picker, level meter, pause). On stop the
  recording enters the normal flow: trim into segments, then the queue. Three optional
  switches, in the recorder window and in Settings → *Recording & live mode*:
  - **System audio** — WASAPI loopback records what the speakers play, i.e. the other people
    on a call, as the second channel of the same WAV (mic left, system right). Without it a
    recording of an online meeting holds only your half. Needs the optional `soundcard`
    package; if it is missing, or no output device supports loopback, the recorder stays
    mic-only and says which of the two it is.
  - **Live transcription** — the PCM going to the WAV is also streamed to a recognition
    engine held open for the whole meeting (`backend/live_stt.py`), and phrases appear in a
    panel as they are spoken, labelled MIC/SYSTEM from the channel that was louder. Offline,
    on the engines already installed: `faster-whisper`, `whisperx`, `whisper`, `vosk`,
    `sherpa-onnx`, `whisper-cpp`. Live can use a different engine/model than the batch pass.
  - **Live summary** — a rolling summary (topics, decisions, action items, open questions)
    refreshed every ~30 s via `backend/live_summary.py`, through the same AI provider as the
    post-meeting summary. On a local model each update is rebuilt from the transcript
    (a mistake cannot set); on a metered cloud it is incremental with periodic rebuilds, and
    `liveSummaryMaxUpdates` caps the spend. A failed or unparsable update never blanks the
    summary already on screen.

  Both live outputs are written next to the recording as they arrive
  (`*_live_transcript.txt`, `*_live_summary.json`). They are **drafts**: unless you ask
  otherwise, the final transcript is still produced from the file by the configured engine,
  from scratch, so live can lag or fail without costing the result anything.
- **Process a live meeting without transcribing it twice** — on stop the recorder offers
  *Process from live text* next to the usual *Process*. It writes the live transcript into the
  meeting's own folder under the standard `<stem>_raw.txt` name and runs summary → analysis
  over it. **The artifact set is identical to any other meeting** — one summary version, one
  analysis version, the same names, the same exports, Obsidian note and Sheets row. Only the
  transcription stage is absent, because it genuinely did not run in that job (which is also
  what the Diagnostics profile shows). Trimming is skipped for this route: the live transcript
  covers the whole recording, so splitting the audio would leave every segment carrying the
  text of all the others.
- **`live` is a first-class intake channel** — alongside a dropped file and a URL. It is stored
  on the history entry (`source`), shown as a `● live` badge in the queue row and named in the
  results panel ("Source: Live meeting"), recorded in the processing journal (its own column in
  the 🕘 window) and written to the log when the job starts. Meetings recorded before this
  release, and every file-based meeting, read as `file` — never as "unknown".
- **MCP server — the meeting archive as tools for any agent** (`backend/mcp_server.py`). The
  inverse of the provider above: instead of pushing a transcript out, an agent (Claude Code,
  Codex, Hermes…) reaches into the archive itself. Tools: `list_meetings`, `get_transcript`,
  `get_summary`, `get_analysis` (whole or one feature), `search_transcripts` (literal) and
  `search_knowledge` (semantic RAG). Dependency-free stdio JSON-RPC — no MCP SDK required.
  MCP searches every local desktop/server/shared catalog. RAG settings keep storage
  isolated by default or connect desktop and a server account in the same installation to
  one catalog through a generated secret code.
  Get the registration snippet with
  `backend\python\python.exe backend\mcp_server.py --print-registration`, e.g.
  `claude mcp add meetings -- <python> <mcp_server.py>`.
- **Local agent CLIs as an AI provider** — besides HTTP endpoints and cloud keys, the summary
  and analysis passes can be handed to an agent already installed on the machine: **Claude
  Code** (`claude -p {prompt}`), **Codex** (`codex exec {prompt}`), **Gemini CLI**, **Hermes**,
  or any command that reads stdin and prints an answer. The transcript is piped on **stdin**
  (an 80k-char meeting cannot fit in a command-line argument), the prompt is substituted for
  `{prompt}`, and `{prompt_file}` / `{text_file}` are available for agents that prefer files.
  Keys and model selection stay in the agent's own config. Settings → AI Provider → “Локальный
  агент”.
- **Built-in local AI (optional, downloaded on demand)** — a user with no local model of their
  own can have the app fetch the current llama.cpp server (resolved live from the GitHub
  releases API, CUDA or CPU as appropriate) plus a fresh curated GGUF sized to their VRAM (Qwen3 / Gemma 3), or any GGUF by URL,
  start it on port 8081 (so an existing LLM on 8080 is untouched) and point the app at it. This
  is **not** part of the distribution — it lands in `resources/local_ai/` only if requested.
  Settings → AI Provider → “Встроенный локальный ИИ”.
- **Split one recording into per-meeting segments** — when a file holds several back-to-back
  meetings, the add-file dialog offers a video preview with a draggable timeline: mark each
  meeting's start/end, add it as a segment, and every segment is cut (ffmpeg, 16 kHz mono) and
  queued as an **independent job** with its own transcript, summary and analysis — instead of
  one blended summary. "Process the whole file" skips trimming.
- **Advanced analysis** — 11 features (action items, sentiment, category, risks, quotes,
  technologies, open questions, recommendations, follow-ups, key topics, formal ГОСТ/ISO
  protocol), rendered as panels; each feature is individually toggleable. Analysis uses the
  full transcript by default for maximum completeness; summary-based analysis and chunking
  are explicit speed/context-window trade-offs. The formal protocol's date, start–end time and
  number come from the recording's file name (`2026-08-17 15-33-43.mkv`) rather than from the
  model's imagination; a file whose name has no date leaves the model to decide.
- **Contextual memory** (opt-in, strictly project-scoped) — feeds prior summaries of the
  *same* project into the summary prompt for continuity across a project's meetings.
- **Exports** — txt/md/json/html/pdf/docx (no data loss), Obsidian vault, per-speaker
  files, and Google Sheets (via a user-deployed Apps Script webhook).
- **Knowledge** — real semantic RAG (chromadb) + plain-text transcript search.
- **Ops** — version history + a scoped **Regenerate** (summary only / analysis only / both, so
  a run that died on one analysis section does not have to redo the summary), a **Processing
  history** window (🕘) journalling every run — kind, start, duration, status transitions,
  stage timings, artifacts and the full error — kept in `config/processing_history.json`
  separately from the queue, so removing a meeting from the table never erases it. Plus CUDA
  auto-detect with CPU fallback, a Diagnostics window (system metrics / processing profile /
  engine A-B / logs), and a Stats modal.

## Requirements

- **Embedded Python 3.11** at `backend/python/python.exe` (the app uses this, not system
  Python). `ffmpeg` (located via `paths.py`) for audio extraction. CUDA is optional —
  auto-detected, with CPU fallback.
- Install dependencies into the embedded runtime:
  ```
  # torch first, from the CUDA 12.4 index (CPU-only: use the PyTorch CPU index):
  backend\python\python.exe -m pip install torch==2.6.0+cu124 torchaudio==2.6.0+cu124 \
      torchvision==0.21.0+cu124 --index-url https://download.pytorch.org/whl/cu124
  backend\python\python.exe -m pip install -r desktop\requirements.txt
  ```
  See [requirements.txt](requirements.txt) for the required vs optional split and the
  non-package assets (ffmpeg, engine models).

## Run

```
backend\python\python.exe desktop\run.py
```

Engine models are fetched on demand from Settings (or `backend/models_cli.py download …`)
into `resources/<engine>_models/`. Configuration lives in `config/settings.json` (shared
1:1 with the Electron app's schema, so the two front-ends stay interchangeable).

## Quick check (self-tests)

The port ships real, runnable smoke tests (no network) — run any, or the whole suite:

```
set QT_QPA_PLATFORM=offscreen
backend\python\python.exe desktop\_selftest_pipeline.py      # full lifecycle transcribe→summary→analysis
backend\python\python.exe desktop\_selftest_settings.py      # settings load/visibility/save round-trip
backend\python\python.exe desktop\_selftest_engines.py       # engine registry
backend\python\python.exe desktop\_selftest_templates.py     # prompt templates
...                                                          # 41 desktop suites (+2 in server/);
                                                             # each prints SUMMARY ALL_PASS and exits non-zero on failure
```

Live engine checks (need the model on disk) are the `desktop/_livetest_*.py` scripts.

## Documentation map

- [ROADMAP.md](ROADMAP.md) — the single source of truth for project state and history:
  what's done, what each module does, and the out-of-scope boundaries.
- [ARCHITECTURE.md](ARCHITECTURE.md) — components, data flow, and dependencies (covers both
  front-ends), with a [C4 component](architecture-c4-component.puml) and a
  [sequence](architecture-sequence.puml) diagram.
- [../WHISPER_ENGINES_COMPATIBILITY.md](../WHISPER_ENGINES_COMPATIBILITY.md) — engine setup,
  the WhisperX compatibility patch, and diarization backends.
- [../README.md](../README.md) — top-level project overview (this desktop client **and** the
  `server/` web cabinet, which is the second front-end over the same backend) and doc map.
- [../server/DEPLOYMENT.md](../server/DEPLOYMENT.md) — deploy the web cabinet.
