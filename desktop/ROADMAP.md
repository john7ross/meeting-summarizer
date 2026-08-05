# Meeting Summarizer — Native (PySide) Migration: PROJECT STATE & TODO

> Read this first in a fresh session. It is the single source of truth for the
> state of the PySide rewrite. Everything below was verified by running it in
> the project's own embedded Python, not assumed.
>
> **Language:** this file is an internal development journal and is deliberately
> English-only — unlike the user-facing docs (README, ARCHITECTURE, DEPLOYMENT,
> MCP_USAGE, google-sheets, WHISPER_ENGINES_COMPATIBILITY), which all ship in
> both English and Russian.

## An empty transcription blamed the wrong stage (2026-08-05)

Owner processed a 15-minute screen recording: faster-whisper for transcription,
Hermes agent for summary/analysis. Every transcription step reported success —
`✔ Транскрибация фрагмента 1/2 — 3с`, `✔ 2/2 — 1с` — and the run then died with
**"No text provided or text is empty"**, which points squarely at the AI provider.
It took a measurement to find that the AI was never the problem.

**What it actually was.** The recording's audio track is digital silence:
`mean_volume: -91.0 dB`, `max_volume: -91.0 dB` — measured on the SOURCE file, not
after our processing, so this was the recorder, not us. Extraction and chunking
were verified correct on that same file (29.5 MB WAV for 922 s, chunks of 600 s and
322 s). Whisper had nothing to transcribe, produced an empty file in 3 seconds, and
the pipeline carried the emptiness two stages downstream before anyone noticed.

**Two defects, both ours.** An empty transcript was reported as success; and the
failure surfaced with a message describing a *symptom* at the wrong stage. A user
whose screen recorder did not capture audio — which is a very common accident —
was told the AI had no text, and had no way to reach the real cause.

`processor.verify_transcript_has_speech()` now runs the moment transcription
returns, **before** the temp audio is deleted (a check asserts that ordering — the
diagnosis needs something to measure). Timestamp markers with no words count as
empty. The two causes are then told apart and tagged:

- `SILENT_AUDIO:` — the track measures at or below -60 dBFS, so nothing was ever
  recorded into it. The message quotes the measured peak.
- `NO_SPEECH:` — there IS a signal, but no words came back; points at the
  transcription language and at whether the file contains speech at all.

Both front-ends translate the tags instead of echoing them: the desktop through
`_DETAIL` (`tx_silent` / `tx_no_speech`), the cabinet through `SERVER_MESSAGES` →
`errors.silentAudio`, with the measured level substituted into the sentence. The
generic `No speech recognised` rule had to be ordered AFTER the tagged ones, which
it would otherwise swallow. 18 checks cover it, including that an unrelated error
(`CUDA out of memory`) still passes through untouched.

## Clean-machine install audit (2026-08-05) — the `min` first run

Everything below was found by running the SHIPPED archive on a Windows 11 VM
rolled back to a clean snapshot, not by reading code. The theme: `min` promised
"unzip, run INSTALL.bat once", and on a genuinely clean machine that was untrue.

**A clean Windows 11 has no Python — it has a decoy.** `python` on PATH is a
zero-byte Microsoft Store stub. `INSTALL.bat` invoked it blind, so the recipient's
entire first-run experience was Windows saying *"Python was not found; run without
arguments to install from the Microsoft Store"* — advice that leads to 3.13+, the
one version this stack refuses (`numpy<2.0` publishes no wheels for it). Nothing of
ours ever executed.

**So the installer now installs Python.** `installer.py` cannot: it is itself a
Python program and can never bootstrap the interpreter it already runs on. The new
`desktop/packaging/bootstrap_python.ps1` does, and `INSTALL.bat` offers it (declinable,
skipped by `--yes`). It fetches 3.11.9 from python.org, **verifies the Authenticode
signature is Python Software Foundation's before executing it**, and installs
per-user. Proven end to end on the VM: no Python at 02:29, full stack installed and
`Готово.` at 03:07.

Five defects surfaced while getting there, each one only visible by running it:

- **`py` means "newest installed".** The first probe order put bare `py` before
  `python` and selected 3.14 on a box whose PATH `python` was a perfectly good
  3.11. Supported minors are now tried first, bare `py` last, and the version gate
  is imported from `installer.py` instead of duplicated — which immediately caught
  that the installer accepts 3.9 while both READMEs claimed 3.10.
- **A backwards range hung the whole install.** `$parts[1..($parts.Length-1)]` on a
  single-word command yields `($null, "python")`, so the empty argument reached the
  interpreter and it sat on stdin forever, silently. The self-test now *runs* the
  probe under a timeout; a static check could never have seen it.
- **winget is not a dependency we control.** With its output redirected — which
  `for /f` forces — it sat for 8 minutes installing nothing and explaining nothing.
  Dropped for a direct, verified download.
- **An unattended install must never need elevation.** `Include_launcher=1` puts
  `py.exe` in `C:\Windows`, which requires admin; under `/quiet` the request has
  nowhere to appear, so the installer waited on an invisible `consent.exe`. Three
  were queued before this was understood. Now `InstallLauncherAllUsers=0`, plus a
  hard 15-minute timeout so this class of hang fails loudly.
- **`PrependPath` only rewrites the registry.** Every process started before the
  install — including `explorer.exe`, and therefore the next console the user opens —
  still has the old PATH, so a second run of `INSTALL.bat` offered to install Python
  that was already on disk. Discovery now also scans the per-user install
  directories and `config/interpreter.txt`, and runs BEFORE the offer.

**`SERVER.bat` blamed the build for a component that was never selected.** The web
cabinet is opt-in; declining it and running `SERVER.bat` produced a raw
`ModuleNotFoundError: No module named 'sqlalchemy'`. (uvicorn being present proves
nothing — chromadb pulls it in for RAG.) `run_server.py` now names the missing
distributions and points at the component list, in both languages; a check keeps
that list in step with `server/requirements.txt`.

**Packaging hygiene.** Every generated `.bat` shipped with `\r\r\n` on every line
(`write_text` translating the `\n` in an already-CRLF string) — cosmetic, but it is
what `type RUN.bat` had been showing all along. `build.py` also never removed the
temp directory it creates per run: 372 `msbuild_*` directories had accumulated on
the build machine. Both fixed, both pinned by checks.

Verified afterwards on that same fresh install: cabinet healthy, registration with
`vmuser@nas.local` accepted (the relaxed special-use-domain rule, shipping), speech
synthesised on the VM and transcribed by faster-whisper/small (291 chars with
timestamps), analysis 11/11 features, all 12 exports 200.

## Release audit for 1.2.1 (2026-07-29) — pre-GitHub, server-focused

Full-surface audit before publication. Suite **47 runners / 1279 checks**, green
twice in sequence; exit codes proven honest both ways by injecting a deliberate
failure into 8 runners (clean run 0, injected run 1, repo left untouched).

**The export-parity flake, root-caused.** `_selftest_export_parity` failed once in
a full run and passed 12/12 in isolation — the classic order/load-dependent ghost.
It was neither: `_pdf_streams` stripped the ASCII85 terminator with
`rstrip(b">~")`, and `>` is a **legal ASCII85 data character**. Every payload whose
last data byte was `>` lost it, `a85decode` returned truncated bytes, zlib refused
them, the decoder silently fell back to the raw chunk, no `Tj` was found and
`_read_pdf` returned `""`. Because the export timestamp is part of the compressed
content, whether the last byte landed on `>` changed with the clock: a sweep of 113
timestamps reproduced it on **6 (5.3%)**. Cross-read with pypdf in a throwaway venv:
**every one of those PDFs was perfectly readable** — so this was a harness bug, not
a product defect, and no assertion was weakened. Fixed with `_a85_body()` (strip
only the two-byte `~>`), the six triggering stamps are **pinned in `STAMPS`** as
permanent regressions, and a direct `a85_body_keeps_a_trailing_gt` check guards the
helper itself. The suite also leaked its temp tree on every green run (161 had
accumulated); it now cleans up on success and keeps the evidence on failure.

**Round 4 — the coverage the owner had to ask for twice.** Three items were
reported as "not tested" with reasons that did not survive one question:

- **Built-in local AI, the FULL cycle**, not just the catalog: the product's own
  installer downloaded gemma3-4b (2374 MB) end to end, the product's own GPU
  hand-off freed the card from the owner's resident 22 GB model, the built-in
  server started on :8081 and answered HTTP 200, a real Russian summary came back
  in 1 s, the model stopped, and `release()` put the owner's model back (HTTP 200
  after 239 s). The Local-AI dialog then showed the truth: a tick on BOTH installed
  models, a star on the recommended one, and Start/Stop/Install gated correctly.
  The previous report skipped this "so as not to occupy the GPU" - a constraint
  nobody had given.
- **ARCHITECTURE named only 40 of 85 owned modules.** Added a **Module map** to
  both languages, generated from each module's own docstring so it cannot drift
  from the tree; seven modules that had no docstring at all
  (`processing/audio.py`, `progress.py`, `tracing.py` and four engine adapters)
  got one.
- **The diagrams existed only as `.puml`,** which GitHub does not render inline -
  a reader clicking the link got raw source. Both are now rendered INTO the
  repository and embedded in ARCHITECTURE(.ru).md, with the re-render command
  written next to them. The PNGs ship in both archives.

Donation sections were already present and correct in both READMEs (QR + 10
addresses); verified rather than assumed.

**Round 3 — publication readiness (legal + repo hygiene).** Everything below is
invisible to every functional test: the product works identically with or without
it, and it is exactly what surfaces *after* a public release.

- **The archives redistributed a GPLv3 binary with no licence text.** The bundled
  ffmpeg is configured `--enable-gpl --enable-version3` (libx264/x265/xvid), so it
  is **GPLv3**, not the LGPL build ffmpeg also publishes — and `backend/FFmpeg/`
  shipped exactly two `.exe` files and nothing else. Qt/PySide6 (LGPL-3.0) shipped
  only `LicenseRef-Qt-Commercial.txt`, which describes the licence that does *not*
  apply here. Nine Python wheels carry no licence text in their metadata, and most
  bundled model weights carried none either. Fixed: `licenses/` (canonical GPL-3.0,
  LGPL-3.0, Apache-2.0 texts + the recorded ffmpeg configure line) now ships in
  BOTH variants, the GPL text and the build configuration also sit **beside**
  `ffmpeg.exe`, and `THIRD-PARTY-NOTICES.md`/`.ru.md` name every bundled component,
  its licence, its upstream and where to obtain corresponding source. ffmpeg is
  invoked as a separate process, so the aggregation keeps this project MIT.
- **`LICENSE` named no real holder** ("Meeting Summarizer contributors"). Now
  `Sergey Lebedev`, on the owner's instruction. The file is still the bare MIT
  template — 21 lines, no appended prose, because extra text makes GitHub report
  `NOASSERTION` instead of MIT.
- **Added `SECURITY.md`/`.ru.md`** — this is a server holding JWTs, other people's
  recordings and API keys, and it had no private reporting channel. It also lists
  what a reporter must never attach (tokens, the Apps Script URL, `server.db`,
  transcripts) and notes that GitHub's private vulnerability reporting is OFF by
  default even when the account-level switch is on.
- **Added `.gitattributes`** — without it the committed bytes depended on each
  contributor's `core.autocrlf`. `.bat` launchers are pinned to CRLF (cmd.exe
  mis-parses LF-only batch files, and `INSTALL.bat` is the first thing a recipient
  double-clicks). Verified to introduce zero renormalisation churn.
- **Added `CONTRIBUTING.md`/`.ru.md`** — how to run both front-ends, how the
  standalone `_selftest_*` runners work, and the four rules this audit kept
  re-learning: assert behaviour not source text, check the exit code, never weaken
  an assertion, treat a flake as a bug.
- Seven checks in `_selftest_build_composition` now pin all of the above against
  the COLLECTED FILE LIST, so a licence can never quietly stop shipping.

**Round 2 — the coverage the first pass talked itself out of.** The first report
listed cloud providers, the other ASR engines and the GPU hand-off as "not
tested", with reasons that did not survive contact with the owner: nothing was
blocking them. Redone properly:

- **All 8 engines on real audio**, not just imported: whisper 37.0s,
  faster-whisper 33.1s, whisperx 52.9s (with `[SPEAKER_nn]` labels), vosk 4.2s,
  sherpa-onnx 1.8s, whisper-cpp 2.5s, sherpa-extra 4.1s on a 45 s Russian clip,
  and funasr on English (it has no Russian) in 2.6s. All offline.
- **GPU hand-off, live, against the owner's own llama-server.** acquire ->
  `freed` in 8.3 s; transcription on the freed GPU took **9.9 s vs 33.1 s on
  CPU** for the identical clip; release polled through the 503 "loading model"
  window and returned HTTP 200 after 236.8 s; the model then answered normally.
  The desktop run shows the same cycle in its own timeline ("Выгрузка локальной
  LLM — 10с").
- **Both diarization backends**: bundled sherpa-onnx and gated pyannote (with a
  HuggingFace token) each found 3 distinct speakers on the same audio.
- **AI transports**: google/gemini, an OpenAI-compatible gateway via
  `--endpoint`, the local model, the agent CLI, the fully custom Advanced API
  (`{{apiKey}}`/`{{prompt}}`/`{{text}}`), and chunked map-reduce (43 k chars ->
  8 parts). Provider errors surface verbatim and actionably (a real HTTP 402
  "Insufficient Balance" was reported as exactly that).
- **URL intake**: a direct HTTP file and a YouTube video, both downloaded with
  progress events. **Google Sheets**: a live append returned `{"ok":true,"row":12}`
  with no token — the token is optional by design, as the script only enforces it
  when one is configured.

**Desktop: duration and size were displayed but never produced.**
`_render_history_head()` renders `(processed_at, duration, size)` and drops empty
values, so the two fields were simply invisible - `_add_files()` called
`store.add(path)` without them although `HistoryStore.add()` has always accepted
them and `media.probe_duration()` reads audio-only files fine. Same "displayed
field with no producer" class already fixed for the cabinet's duration column;
the desktop was the front-end still carrying it. Now filled via
`_probe_duration_label()` / `_file_size_label()` (best-effort - an unreadable file
must never block adding it), pinned by
`_selftest_ui.history_entry_records_duration_and_size`.

**The full build shipped a broken torch — every full build ever made.**
`build.py`'s `IGNORE_GLOBS` exist to keep OUR dev scaffolding out of a release
(`_fake_processor.py`, `_selftest_*`, `conftest.py`). They were also applied to the
**vendored** embedded runtime, where third-party packages legitimately use those
same shapes for real code. 67 genuine files were silently dropped from
`backend/python`, among them `torch/_subclasses/_fake_tensor_utils.py` — so
`import torch` raised `ModuleNotFoundError` in the extracted archive and the
"unzip and run, no network" variant could not transcribe at all. Nothing in the
repo could reveal it: the file is present on disk and absent only from the zip,
which is why this was caught by *running the packaged artefact* rather than by
reading the packager. Fixed by splitting `JUNK_GLOBS` (junk everywhere) from
`PROJECT_GLOBS` (ours only) and passing `vendored=True` for `backend/python` and
the downloaded model bundles; `_selftest_build_composition` gained 5 checks that
pin the rule and name the torch module. Verified by rebuilding and running the
packaged runtime with `HF_HUB_OFFLINE=1`: torch 2.6.0+cu124 with CUDA, and a real
40 s Russian clip transcribed in 11.2 s off the bundled faster-whisper medium.

**Cross-front-end check.** The defect class (`.rstrip()` with a multi-character
argument eating real data) was swept repo-wide: the only other hits are intentional
filename/label hygiene (`history.py`, `obsidian.py`, `main_window.py`). No product
code decodes ASCII85 anywhere.

**Desktop-side confirmations while auditing the server.** `history.set_status()`
already clears a previous failure the moment a job returns to `processing`, and the
table's detail cell is cleared with it — the *server* was the front-end that kept a
stale `error_message` on a running job, and it has been brought in line. Desktop-only
surfaces are now honestly documented as the local-AI manager, Diagnostics and MCP
registration; ARCHITECTURE(.ru).md had still claimed the microphone recorder and Trim
preview were desktop-only after both shipped in the web cabinet.

Server-layer findings from this same audit (five unreachable capabilities, the
engine-probe interpreter bug and the orphaned-job recovery) are recorded in
`server/SERVER_ROADMAP.md`.

## Release audit for 1.2.1 (2026-07-25)

Feature work is closed; this pass hunted defects that only appear when the app is
actually used. Suite: **1132 checks, 0 failures** (44 desktop runners + 2 server).
Every runner now exits non-zero on failure — seven of them used to report success
regardless, so a red suite could pass unnoticed.

Defects found and fixed in this pass — each one reachable by a normal user:

- **Analysis panel painted new content over old.** Panels that build a nested
  layout were not cleared, so switching analysis version, meeting or language
  left the previous render alive underneath (doubled, overlapping rows).
  `_Panel._purge` now empties nested layouts and detaches widgets immediately.
- **Processing queue collapsed to one visible row.** The table lives in a
  scrollable column and only ever got its size hint. It now has a floor of five
  rows and grows with the backlog up to twelve.
- **Duration reported as N/A in exports.** The analysis panel derived the length
  from the transcript's last timestamp; Google Sheets, Obsidian and the server
  exports had no such fallback. One implementation now serves all of them
  (`media.duration_from_transcript`).
- **Google Sheets columns did not match the app.** The sheet counted items
  ("Action Items: 20") and truncated the summary to its first line. It now has
  one column per section carrying that section's content (18 columns).
- **Analysis failed at random with WinError 32.** The agent's scratch directory
  was removed in a `finally`; on Windows the agent's own children still hold it,
  and the raised error REPLACED an answer that had already been produced
  ("ошибок 2 из 11" on a run whose text was fine).
- **PDF silently lost all Cyrillic.** When no Unicode font could be registered
  the exporter fell back to Helvetica, which has no Cyrillic glyphs — txt/md/
  html/docx were intact while the PDF was empty of Russian. Registration now
  retries, and a non-ASCII document refuses to export rather than lose text.
- **Model list ignored the transcription language.** The registry filters models
  per language, but Settings asked for the catalog without one, so FunASR (no
  Russian at all) could be selected for a Russian meeting and returned confident
  nonsense. The list now follows the language and explains an empty result.
- **Interface language switch missed the analysis panel.** Its language was only
  updated when an analysis was already loaded, leaving the placeholder in the
  previous language on every meeting without one.
- **Knowledge-base search could hide whole meetings.** Chroma's HNSW index is
  approximate: asking for every vector still returned a subset, and a long
  meeting could crowd out short ones entirely. Documents the ranking misses are
  now scored directly, so coverage no longer depends on ANN recall.
- **The server produced no analysis for most users.** The worker carried its own
  hand-copied copy of the default settings, and that copy was missing the five
  analysis feature flags. Any cabinet user who had not saved settings explicitly
  got a meeting that finished "successfully" with a transcript and a summary, no
  analysis artifact, and a 404 from every analysis export. The worker now reads
  the very dict the settings API serves.
- **Deleting a meeting orphaned its artifacts and logs.** They are not
  ORM-cascaded, so only the meeting row went away. SQLite reuses the freed id,
  and the next meeting inherited the previous one's version history and
  processing log - belonging to a different user. Both are now deleted with the
  meeting, in the same transaction.
- **GPU hand-off ignored the app's own model.** It stopped whatever listened on
  `llamaPort` and nothing else, so the built-in llama.cpp on its own port kept
  holding VRAM through the whole transcription - exactly the situation the
  hand-off exists to prevent. It is now stopped and restarted through `local_ai`
  by recorded model id, so that module's state stays truthful.
- **Nothing brought the local model back after a crash.** The hand-off restores
  it only when the app completes the run; a crash, an OOM kill or a reboot left
  it down until someone noticed. New `backend/local_ai_watchdog.py` supervises
  it and stands down while the hand-off lock is held.
- **Every distributed build reported itself as 0.0.0.** `package.json` is the
  single source of truth for the version - read at runtime by the server, the
  MCP server, the export footer and the Obsidian notes - and it was not in the
  archive at all. Found by booting the unpacked build, not by reading the code.
- **Regenerated diagram PNGs travelled in the archive.** They are gitignored
  build output; a stale render shipping next to the `.puml` sources it no longer
  matches is worse than shipping none.
- **No meeting ever showed its length in the cabinet or over MCP.** The server
  never wrote `Meeting.duration` at all, so the card and the detail modal showed
  nothing; `list_meetings` returned an empty field for the same reason. The
  worker now measures the media with ffprobe and falls back to the transcript's
  last timestamp, and MCP applies the same fallback the exports already used.
- **The cabinet offered a per-speaker export on meetings that have no speakers.**
  The button was rendered for every completed meeting, so on anything transcribed
  without diarisation (Vosk, faster-whisper) clicking it produced an error alert.
  It now appears with the speakers section, only when the transcript is diarised.
- **The cabinet could not record a meeting at all.** Desktop/server feature parity
  was a project requirement, but the web intake accepted only an uploaded file or a
  URL - a browser user with a microphone had no way in. The cabinet now records with
  `MediaRecorder` (WebM/Opus, MP4/AAC on Safari), shows an elapsed-time indicator and
  posts the result to the same upload endpoint, with distinct messages for a denied
  permission, a missing device and a non-secure origin (browsers withhold the
  microphone outside HTTPS/localhost).
- **The cabinet had no search at all.** `/api/rag/search`, `/api/rag/textsearch`
  and their API-client methods all existed and were covered by tests, but no part
  of the dashboard ever called them, so a browser user could not search their own
  archive. The cabinet now has one search box with both modes - literal (with an
  optional regular expression) and semantic over the knowledge base - and a hit
  opens the meeting it came from.
- **The cabinet showed no statistics.** The desktop has a Statistics dialog;
  the web front-end had neither an endpoint nor a panel. `GET /api/meetings/stats`
  now returns the same seven metrics that dialog computes, and the dashboard shows
  them above the meeting list.
- **The cabinet could not split a recording into meetings.** Desktop/server
  parity was a project requirement and the desktop has a Trim dialog, but a
  browser user could only ever process a whole file - one recording holding three
  meetings became one blended summary. The cabinet now holds the upload back from
  the queue (`process=false`), draws an ffmpeg-computed waveform (only the peaks
  travel, never the media), lets the user mark spans on it, and cuts each one into
  its own queued meeting; the source recording is left untouched.

Deliberately NOT ported to the web, decided with the owner on 2026-07-26:
diagnostics (a local troubleshooting view), managing the built-in local model (an
admin action on a shared host) and the Obsidian export (it writes into a vault on
the user's own machine - the cabinet already exports Markdown).

- **Nobody could ever be an administrator.** Every registration wrote
  `role="user"`, and no route, script or documented step granted the role - so the
  worker-count control and shared model downloads were unreachable on every
  installation. The first account on a fresh database is now the administrator,
  and DEPLOYMENT explains how to promote another user later.
- **The server did not honour a reverse proxy.** uvicorn ran without
  `proxy_headers`, so behind TLS termination every request appeared to come from
  the proxy over plain http - client addresses vanished from the logs. Now on, with
  `TRUSTED_PROXIES` deciding who may set the forwarded headers. Verified by putting
  a real proxy in front: the cabinet logs the client address, and register / login /
  settings / upload / segments / patch / delete all pass through it.
- **The application icon shipped in neither archive.** `resources/icon.png` is
  tracked and the desktop loads it at startup, but the packager excluded all of
  `resources/` (it holds the multi-GB model sets), so every distributed copy fell
  back to the default Qt window icon. The icon now travels in both variants, and
  the composition test asserts it. The three `ico*.png` at the repo root are the
  original drafts - `ico3.png` is byte-identical to the shipped icon - untracked in
  8527024 and kept on disk only as sources.
- **The web cabinet had no tab icon at all.** Neither page carried a `<link
  rel="icon">`; the browser showed a blank tab. Both pages now link a 5 KB
  favicon derived from the same purple mark.
- **The queue could only be emptied one row at a time.** After a batch the user
  had to select and remove rows by hand; there was no "clear queue" at all. Added,
  keeping anything actually running (dropping a row whose subprocess is alive
  would leave it writing into an entry the table forgot) and saying how many were
  kept.
- **Settings always had a horizontal scrollbar.** A combo box holding whole agent
  command lines sized itself to its longest item, so the form demanded 2662 px of
  width at any window size. Combos are now capped (`theme.cap_combo_width`), the
  dialog's minimum width matches what the form really needs, and a suite check
  fails if ANY window demands more than 1000 px. The same defect was hiding in
  Diagnostics: engine label + full model name on one unwrappable checkbox made it
  2380 px wide; the model moved to its own wrapping line, 889 px now.
- **Emptying the queue left the results on screen.** The transcript, summary,
  analysis, project field, version pickers and the Regenerate / Add-to-RAG /
  speaker actions all belong to the SELECTED row, and the selection handler only
  ever loaded - it never reset. With the queue cleared the panels kept showing a
  meeting the queue no longer contained. Both removal paths now clear every
  result surface once the table is empty.
- **A cleared queue came back on the next start.** Removing a row only touched
  the table: `HistoryStore` had no delete at all, and the queue is rebuilt from
  history on every launch. Both removal paths now delete the record for good
  (produced files stay on disk, and the tooltips say so).
- **Hints were cut off ("Проект (необяз…").** Fields carried hard-coded maximum
  widths tuned to one font; at the real UI font the placeholder no longer fitted.
  Widths are measured from the placeholder now, and re-measured after the
  stylesheet is applied - the style lands after construction and enlarges the
  font, so a width measured once was measured against the wrong metrics.
- **Diagnostics clipped its own controls.** The engine-comparison tab has no
  scroll area, and once each engine gained a second line it no longer fitted the
  dialog: Qt compressed the file field, the buttons and the table header until
  their text was cut. The tab scrolls now.
- **Selecting a meeting restored its artifacts but not its status.** Clicking a
  row loaded the transcript, summary and analysis, while the status panel stayed
  blank: the stage timeline lived only in memory, populated by signals during a
  run, so anything processed in an earlier session had none. It is now rebuilt
  from what is persisted - the processed-at header, per-stage timings read from
  the meeting's `*_trace.json` (the same file the flame graph uses), the summary
  and analysis versions with their provider, and the failure reason - and it is
  rebuilt again when the interface language changes.
- **An instant stage lost its time.** Restoring the local model takes no
  measurable time when it is already listening, so its span carries
  `duration: 0.0` - and the rebuilt timeline treated 0 as "no value" and printed
  the label bare, which read as a stage that was never timed. Zero is a
  measurement: it renders as `0с` now.
- **A failed meeting never said why.** `HistoryEntry` had no field for it, so the
  reason died with the process: after a restart the row read "Error / 0%" with an
  empty Details cell. The reason is stored now and shown in the Details column
  (with the full text in a tooltip), in the status line and in the timeline.
- **Export ignored the version picker.** Choosing v2 and pressing Export wrote
  v4: the handler read `versions[-1]` and numbered the file `len(versions)`, so
  the CONTENT was always the newest and every selection collided on one file name
  (which is why a second export "did nothing"). Export, the Obsidian export and
  Add-to-RAG now all take the version the picker shows, and the file is named
  after that version's real number - the same defect the server export had.
- **`/api/health` answered 404.** Every other endpoint lives under `/api`, so
  probes and reverse proxies looked there. Health is now served on both paths.
- **Personal data was tracked in git.** `config/history.json` (23 meetings, full
  paths, two Windows accounts) was committed before it was gitignored;
  `resources/local_ai/` (9.5 GB of downloaded model) was not ignored at all.

Verified by running it, not by reading it: seven ASR engines on one recording,
diarization, microphone capture through to transcript, YouTube and direct-HTTP
intake, model download then immediate use, the built-in llama.cpp through
install/start/answer/stop, RAG isolated and shared across desktop and server,
MCP search across catalogs, exports byte-checked in all six formats including
PDF text decoding, versioning, cancellation and re-run, parallel processing of
two files, and all 54 settings surviving a restart.

## Current hardening (2026-07-25)

- Desktop queue/status state is id-owned: selection projects one job's live state and
  artifacts, progress from another job cannot steal the panel, cancellation targets one
  id, and the fixed-height stage timeline scrolls.
- Files and accepted Trim results are enqueued automatically. `PipelineQueue` starts up to
  its resolved concurrency cap; the `auto` value intentionally becomes 1 on a single CUDA
  GPU because full ASR pipelines compete for VRAM.
- Settings/history persistence now uses `atomic_io` (cross-process lock, unique temporary
  file, fsync, atomic replace and Windows-lock retries).
- Quality-first defaults are full-transcript analysis and whole-transcript AI processing;
  summary-based analysis and map-reduce chunking are explicit speed/context compromises.
- The web intake boundary now enforces bounded streaming uploads, safe file names,
  ownership-authorized WebSockets and a private/local URL policy. Its queue deduplicates
  ids and retires busy workers safely when the cap is lowered.
- RAG storage is configurable: isolated desktop/per-user catalogs remain the default;
  a capability key can connect desktop and one server account in the same installation to
  `rag_shared/<sha256>`. MCP discovers and searches all local catalogs, labels sources,
  deduplicates results and reports incompatible embedding stores without hiding good hits.
- `ARCHITECTURE.md` / `ARCHITECTURE.ru.md`, the C4 component view and the processing
  sequence were revalidated against the current source and updated together.
- **Cancel no longer starts work.** The trim dialog offers three answers - whole file,
  process segments, Cancel - and Cancel was folded in with "whole file", so declining the
  dialog began a full transcription of the recording the user had just refused. Cancel now
  adds nothing at all; cancelling the cutting progress adds nothing; and a total cut failure
  no longer quietly transcribes the whole file instead, with the warning saying so in both
  languages.
- **The export selector no longer cuts its own words** ("Транскри"). Qt caches a combo's size
  hint from the font at construction time and the stylesheet enlarges it afterwards, so
  `theme.fit_combo` measures every item with the widget's real metrics, again after the style
  is polished, and again after a language switch. The clipping sweep now covers the MAIN
  WINDOW too - it only ever checked dialogs, which is exactly how this shipped - and a combo
  must fit its entries unless it was deliberately capped by `cap_combo_width`.
- **"Выгрузка локальной LLM — не удалось" was usually a lie.** `acquire()` returned False both
  when a model could not be stopped and when there was nothing running to stop, so every run
  on a cloud provider reported a broken hand-off. `acquire_status()` now returns
  `freed` / `idle` / `stuck`; the desktop timeline shows "локальная модель не запущена,
  выгружать нечего" for idle and keeps ✖ for a model that survived the kill, and the exception
  text is no longer swallowed. The server logs the three cases distinctly.
- **The cabinet gained the queue actions the desktop had.** `DELETE /meetings/finished`
  (declared before `/{meeting_id}`, keeps and reports anything still processing) behind a
  "Очистить" button, so recordings no longer have to be deleted one at a time; a recording
  uploaded with "trim first" can be started from its card instead of being stranded with only
  a Cancel button; and `GET /{id}/trace` finally serves the per-stage timings the backend was
  already writing, rendered as a stage timeline where a 0 ms stage still shows its time. One
  `_purge_meeting` helper now backs both the single and the bulk delete.
- **Deleting a meeting takes its status timeline with it.** Removing the only row from the
  history left "✔ Обработка — 10м 18с" of the deleted meeting on screen beside an empty queue
  and a 0% bar: `_clear_results` reset every artifact surface and `_active_job`, but the
  timeline is rendered HTML, and nothing re-rendered it. It now re-renders, both removal paths
  drop the id's `_stages_by_job`/`_live_by_job` entries, and deleting the SHOWN meeting resets
  the panels even when other rows remain (previously only an emptied queue did).
- **Seven UI checks had never actually run.** `desktop/_selftest_ui.py` has a boolean
  `check(name, ok, detail)` helper, and seven calls passed it a FUNCTION OBJECT - always
  truthy, so they printed PASS without executing a single line of their bodies. `check` now
  calls a callable, reports its return value as the detail and turns an assertion or crash
  into a FAIL; verified in both directions by forcing a failure. Making them real immediately
  exposed a live defect: the settings dialog needed 918 px of content in an 866 px viewport,
  i.e. **a horizontal scrollbar on every open** - the hard-coded 900 px minimum had been
  measured once by hand and the form had grown since. It is now derived from the form's own
  measured width (re-measured after the stylesheet lands, capped at 1000 px).
- **Known, tracked:** constructing any of the three dialogs (settings, diagnostics, RAG) makes
  CPython terminate abnormally (exit 127) while unwinding Qt AFTER the program's own exit -
  a MainWindow alone does not, and a parent or a kept reference makes no difference. Nothing is
  visible to the user (the crash is past the last line of work), but it made an all-green test
  run report as failed, so `_selftest_ui.py` now leaves through `os._exit` with the verdict it
  computed. The teardown itself is NOT fixed and stays on this list.
- **English no longer leaks into the Russian cabinet.** API messages are English by design
  (one API, many clients), and the UI rendered `detail` / `error_message` raw: "No speech
  recognised…", "Username already registered". `i18n.serverMessage()` maps the known server
  strings to translations (keeping specifics such as the endpoint URL via `{0}`) and passes
  anything unknown through unchanged, and all 32 message sites now go through it. The mapping
  keys off the server's own wording, so a suite check asserts each string still exists on BOTH
  sides - reword one without the map and a test fails instead of the UI quietly reverting to
  English. The waveform failure also stopped dumping ffmpeg's stderr tail at the user, which
  arrived as a multi-line English diagnostic sliced mid-word ("…ata found when processing
  input"); the detail goes to the server log and the user gets one sentence.
- **Downloads have real names again.** The client overwrote the server's filename with
  `` `${fileType}_${meetingId}` `` - no extension at all, so `video_8` could not even be
  opened - while the format exports next to it used a helper that reads
  `Content-Disposition` correctly. Both paths now share that helper, which also understands
  `filename*=utf-8''` (Starlette's form for any non-ASCII name, i.e. every Cyrillic
  recording). The server offers the name the user recognises instead of the stored
  `5_20260727_003647_c9ee8152c661_Запись….webm`, and artifacts get `<stem>_summary_v2.txt`.
  Verified end to end: a browser-uploaded `Запись 2026-07-27 03-00-00.webm` downloads under
  exactly that name.
- **A button is offered only when there is something behind it.** Download buttons were gated
  on a PATH, and a run that recognised no speech still records a transcript path - so
  "Download transcript" handed over a zero-byte file. `MeetingResponse` now computes
  `has_source` / `has_transcript` / `has_summary` / `has_analysis` from real file size, the UI
  gates on those, and the download endpoint refuses an empty file with a clear reason.
- **The speaker-prompt switch now changes the prompt.** Toggling it reloaded the template LIST,
  and both variants share every template NAME - so nothing visibly changed. It re-renders the
  prompt textarea from the new variant, keeps the selected template, and was verified by
  measurement (805 → 1003 characters and back). Microphone recordings DO reach the trim window
  when "split first" is ticked (checked live: the meeting stays `uploaded` and the modal
  opens), and a real webm/opus recording is readable - 3.008 s, 800 peaks - so "no speech
  recognised" on a silent take is an honest verdict, not a format failure.
- **Three former "justified skips" were reversed on the owner's instruction, and the
  admin/user boundary is now explicit.** Obsidian export exists in the cabinet: a per-user
  vault path plus the note toggles, `POST /meetings/{id}/obsidian`, and a `→ Obsidian` chip
  that writes THE VERSION SHOWN (verified with three summary versions: exporting the middle
  one produced `obs_test_summary_v2.md` containing v2 and not v3). Notes come from the same
  Qt-free module the desktop uses, so both front-ends produce identical files. Model and
  engine management is administrator-only and shared by the whole installation: model
  download (already existed), `GET …/update-check` (which `models_cli` could always answer
  and nothing exposed) and `POST /admin/engines/{engine}/install` for an engine's Python
  packages. The worker count became an installation setting rather than a per-user
  preference: a new `server_settings` row persists it and it is re-applied at startup - it
  used to live only in the queue object and silently reverted to hardware auto-detection on
  every restart. The Administration button is hidden for non-admins rather than merely
  403-guarded, and language/theme were never server settings at all (they are chosen in the
  browser), which is now stated in both deployment guides along with the full admin/per-user
  split.
- **A settings key that cannot be saved is now impossible to ship.** `SettingsUpdate` is a
  Pydantic model dumped with `exclude_unset=True`, so any key absent from it was discarded in
  silence - `DEFAULT_SETTINGS` listed it, the form sent it, the save reported success and kept
  the old value. Two suite checks close the class: every default key must be declared on the
  schema, and every key the cabinet's form binds must be a real default.
- **The AI endpoint no longer falls back to a port nobody configured.** With `localEndpoint`
  empty, `ai_client` defaulted to LM Studio's `localhost:1234` and a run died with "Cannot
  connect to local API at http://localhost:1234/v1" while the model was on `llamaPort`. Both
  front-ends now derive the endpoint from `llamaPort` when the field is blank, and the error
  names the two settings that decide the address.
- **Cabinet ↔ desktop settings parity.** A full key diff found six real gaps behind the
  owner's questions: `projectId` (contextual memory had no project to group by, so the feature
  was inert - uploads start processing immediately, and a per-meeting tag could only be applied
  afterwards; new uploads are now stamped with the default project), `useSpeakerPrompt` (the
  built-in library ships a speaker-aware variant of every template and the cabinet hard-coded
  `false`, so diarisation never reached the prompt), `agentCommand`/`agentCwd` (the desktop
  offers ten AI providers, the cabinet nine - the local agent CLI was missing outright, and the
  rest were bare ids like "openai" instead of "OpenAI (ChatGPT)"), `hfToken` (pyannote
  diarisation was unusable), `googleSheetsToken` (the Sheets checkbox existed but its secret
  could not be entered) and `ragEmbeddingBackend`/`ragEmbeddingModel`. Obsidian, the built-in
  model manager, `language`/`theme` and `parallelWorkers` remain justified skips.
- **Progress no longer disagrees with itself.** The meeting modal rendered a hard-coded 0% and
  "Ожидание обновлений…" while the card beside it already showed "Создание саммари ~8с": the
  modal ignored the progress it had just fetched and waited for the next websocket event, which
  a one-minute stage does not send. It now seeds from the fetched meeting and is refreshed by
  the same poll that updates the cards.
- **Analysis features toggle in one click** (All / None) instead of five, and the settings grids
  align their inputs (`items-end`) - a label wrapping to two lines used to push its field below
  the one next to it.
- **The project field fits its own hint.** `w-48` cut "id для группировки и RAG" to "…и RA(";
  `w-full` inside a content-sized flex row was no better (183 px for 168 px of text). It now
  has an explicit minimum, may wrap, and was verified by measuring the placeholder against the
  field's real metrics.
- **A wrong password now says so instead of resetting the form.** `/auth/login` answers 401 for
  bad credentials, and the API client treated EVERY 401 as an expired session:
  `window.location.href = '/'` reloaded the login page, so the error appeared for a fraction of
  a second and the form cleared itself with nothing shown. `handleResponse` takes
  `redirectOn401`, the sign-in request opts out, and the redirect is additionally suppressed
  while already on the login page so no other 401 there can loop. The message is localised
  (`auth.invalidCredentials`) rather than echoing the server's English detail, and the thrown
  error now carries `status` so callers can phrase a case themselves. The session-expiry
  redirect still works - verified with a garbage token on the dashboard.
- **Browser behaviour is tested, not just its source text.**
  `server/_selftest_web_behaviour.cjs` runs the real `api.js` in a Node sandbox with a stubbed
  location/localStorage/fetch and asserts all three 401 cases (sign-in must not navigate,
  an expired session must, the login page must never loop). Both new guards were validated by
  reintroducing the original defect and watching them fail.
- **Browser scripts are parsed by the suite** (`server/_selftest_web_js.py`). A stray comma
  after a class method in `api.js` left `api` undefined and the cabinet stuck on "Загрузка
  встреч…" with an empty console, while 87 source-substring checks stayed green - they cannot
  see a broken file. `node --check` now parses every shipped script, and the check was
  validated by reintroducing the exact defect.
- The export-parity suite no longer depends on the wall clock. Left unpinned, the exporter
  stamps `datetime.now()`, and which digits it contains decides which glyphs enter the PDF's
  embedded font subset, so every run produced different PDF bytes and `v2_content_in_pdf`
  failed roughly once in twenty runs with no way to reproduce it. `export_date` is now pinned
  in the fixture, six timestamps (all ten digits, epoch, end-of-century, plus the live clock)
  are swept deliberately, and a failing assertion keeps the offending PDF and reports the
  registered font, decoded length and size. The export path itself was cleared: it raises
  rather than silently falling back to Helvetica, and Malgun, Arial and Segoe UI were each
  verified end to end in separate processes. A new guard pins the reader's real limit -
  reportlab subsets a TTF at 255 glyphs per embedded font, the fixture measures 241, and past
  the cliff a word can straddle two subsets and arrive as two literals, so the guard fails
  first and names the fix instead of resurfacing as a flake somewhere else.

## Latest additions (2026-07-04) — new features on BOTH fronts (desktop + web server)

- **Restored the full default summary prompt.** The port had reduced the original
  detailed prompt to a one-liner; the ~1730-char RU/EN default (+speaker variant)
  from the Electron app is back as `templates.BUILTIN["custom"]` and the seeded
  `prompt` default (`templates.default_prompt`). `_selftest_templates.py` = 32.
- **Feature 1 — output language ≠ transcription language.** New `outputLanguage`
  setting (auto/ru/en). `summarization.resolve_output_language` +
  `apply_output_language` append a translate directive when it differs; analysis
  picks `ADVANCED_PROMPTS[output_lang]`. Wired in `core/pipeline.py` (desktop) and
  the server worker. Settings UI on both fronts. Live-verified RU→EN via Qwen.
- **Feature 2 — process a video by URL** (YouTube / file server). New
  `backend/url_download.py` (yt-dlp + fallback HTTP, single-file video, auto
  browser-cookie retry for YouTube anti-bot with a clear message; setting
  `youtubeCookiesBrowser`). Desktop: "Add by URL" field → `MainWindow._add_url`
  downloads via `ModelsWorker`, then the file goes through the normal pipeline.
  New 14th built-in template **"Видео из сети"** (educational video → written
  guide). yt-dlp added to `backend/requirements.txt`.
- **AI-processing settings parity** (earlier this batch; default changed in the 2026-07-25
  hardening): explicit opt-in chunking with a quality warning, disable-reasoning, request timeout, retries, GPU hand-off,
  contextual memory — all exposed in the desktop Settings dialog and wired to
  `summarization`/`analysis` and the transcription hand-off.

## 1. Goal & decisions (agreed with the owner, Sergey)

- Rewrite the **UI layer** of the existing Electron app natively in **PySide6**,
  in a new folder `desktop/`, while **keeping ALL features** (exports, server
  part, monitoring, profilers, A/B, flame graphs — nothing is cut). Migration is
  **incremental, component by component**.
- The native client drives the Python backend through isolated CLI subprocesses. The
  backend has since evolved into shared registry-driven CLIs used by both desktop and web;
  the stable architectural boundary is the argv + UTF-8 JSON-line contract.
- Design must look the same: VS Code-style dark/light theme (tokens reproduced
  1:1 in QSS). Owner likes the look — do not change it, only the render engine.
- Final target: **portable, zero external deps, no install**; CUDA auto-detected
  with CPU fallback. (Packaging not started yet — see TODO.)

## 2. Environment (verified)

- Embedded runtime: **Python 3.11.8** at `backend/python/python.exe` (portable).
  This is what the app uses — NOT the system `python` (3.14, no torch wheels).
- Installed there: `torch 2.5.1+cu121`, `whisperx`, `faster_whisper`, `numpy`,
  `reportlab`, `requests`, `jinja2`, and now **`PySide6 6.11.1`** (+ Addons,
  Essentials, shiboken6). CUDA build = 12.1.
- Now also installed & **live-verified** (2026-07-01): `sherpa-onnx 1.13.2`,
  `vosk 0.3.45`, `pywhispercpp 1.5.0` — all produce a real transcript on this
  machine (see §6 "TODO #14 live-verified"). FunASR (SenseVoice/Paraformer) runs
  through the SAME `sherpa-onnx` package — deliberately NOT the heavy `funasr`
  package, to protect the torch/whisperx stack. PyPI `markdown` is still absent
  (note: the markdown EXPORT feature still works — it doesn't depend on it).
- Owner will install any extra deps on request — just ask.

## 3. How to run

```
# Launch the app (real window):
backend\python\python.exe desktop\run.py

# Run the smoke tests (all currently PASS):
backend\python\python.exe desktop\_selftest.py            # foundation (paths/config/adapters)
backend\python\python.exe desktop\_selftest_core.py       # models + history + versioning
backend\python\python.exe desktop\_selftest_worker.py     # concurrent workers, id routing, no cross-talk
backend\python\python.exe desktop\_selftest_pipeline.py   # full lifecycle transcribe->summary->analysis
set QT_QPA_PLATFORM=offscreen && backend\python\python.exe desktop\_selftest_ui.py   # window skeleton
set QT_QPA_PLATFORM=offscreen && backend\python\python.exe desktop\_selftest_settings.py  # settings dialog (load/visibility/save round-trip)
backend\python\python.exe desktop\_selftest_export.py     # exporter core: all kinds x formats, no data loss, footer, naming
set QT_QPA_PLATFORM=offscreen && backend\python\python.exe desktop\_selftest_export_ui.py  # export UI wiring + version-aware naming
backend\python\python.exe desktop\_selftest_obsidian.py   # Obsidian vault export (both notes, People/Topics/index/Dataview, no loss)
backend\python\python.exe desktop\_selftest_rag_rebuild.py  # RAG rebuild: source_lookup from history.json + re-embed (#13a)
set QT_QPA_PLATFORM=offscreen && backend\python\python.exe desktop\_selftest_diagnostics.py  # metrics + trace + diagnostics dialog + engine compare (#10)
backend\python\python.exe desktop\_selftest_device.py     # CUDA probe (real) + resolve_workers(cuda) + queue cap (#9)
set QT_QPA_PLATFORM=offscreen && backend\python\python.exe desktop\_selftest_device_ui.py  # device indicator + concurrency adjust (#9)

# LIVE engine checks (need the model on disk + the pip package; real transcription):
backend\python\python.exe desktop\_livetest_sherpa.py     # sherpa-onnx: real RU transcript on the model's bundled test_wavs
backend\python\python.exe desktop\_livetest_vosk.py       # vosk: real RU transcript on vosk-model-small-ru-0.22 (reuses sherpa test_wavs)
```

## 4. Architecture & data flow

Everything is keyed on a unique **history id** (single source of truth — this is
what fixes the status-sync chaos the Electron app had).

```
add file -> HistoryStore.add() -> unique id (ms timestamp; same file x10 = 10 ids)
PipelineQueue (<= N concurrent JobRunners)
  JobRunner(id):  EXTRACTING -> TRANSCRIBING -> SUMMARIZING -> ANALYZING -> DONE/ERROR
    transcription: QProcess -> processor.py  (streams progress JSON)
    summary:       QProcess -> ai_client.py  (single-shot text)
    analysis:      QProcess -> ai_client.py  x N  (one pass per enabled feature;
                   input = the TRANSCRIPT; each pass -> JSON, merged to one file)
  artifacts -> transcripts/<id>/  named by input file, versions v2/v3...
  versions recorded in HistoryStore by id
UI: queue row per id; status/progress slots routed by id (no cross-talk).
```

### Verified backend contracts (do not re-invent)

- **processor.py**: `--video --language <ru|en> --model --engine
  <whisper|faster-whisper|whisperx|vosk> --device <auto|cuda|cpu> --output-dir`.
  stdout = newline JSON: progress `{"stage","progress","details"}`, then terminal
  `{"success","output","trace"}` or `{"success":false,"error":...}`.
  Stages seen: `status.extracting`, `status.transcribing`, `status.complete`,
  `status.error`. It writes `<stem>_raw.txt` to output-dir and deletes temp
  audio + chunks itself.
- **ai_client.py**: `--provider --api-key --endpoint --prompt --text-file
  [--participants "a,b"]`. Generated text -> stdout; errors -> stderr + exit!=0.
  Providers: local, openai, anthropic, google, xai, gemma, qwen, mistral, deepseek.
- **history.json** (config/history.json): legacy keys `id, processId, videoPath,
  videoName, duration, size, processedAt, transcriptPath, summaryPath`. The
  native client ADDS `status, summaryVersions[], analysisVersions[]` and
  preserves unknown keys (Electron-compatible round-trip). `summaryPath` mirrors
  the latest summary version.
- **Obsidian export** (example real output studied): per-meeting folder with
  `<name>_summary.md` and `<name>_analysis.md`; `_index/By Date.md`; `_queries/*`
  (Dataview). The analysis .md is a RENDER of a structured JSON (sections:
  characteristics, action items, sentiment, category, risks, quotes,
  technologies, open questions, recommendations...). NOTE: the legacy backend
  CLIs `markdown_exporter_cli.py` / `multi_format_exporter_cli.py` proved
  summary-centric and lossy (analysis dropped 9/11 features, misread the
  protocol keys, broken HTML footer via bad `.format`, timestamp naming) — so
  export was REBUILT as a unified Qt-free module `app/backend/exporter.py`
  (see §6, TODO #3a). Obsidian vault structure is still TODO #3b.

## 5. File map (`desktop/`) — all implemented & verified

```
desktop/
  run.py                         launcher (sys.path + app.main.main)
  PROJECT_STATE.md               this file
  app/
    __init__.py
    paths.py                     portable paths (root, embedded python, processor/ai_client, ffmpeg); PyInstaller-aware
    config.py                    load/save the shared settings.json (schema 1:1, atomic, defaults for missing keys)
    main.py                      build_app() + main(): settings -> store -> PipelineQueue -> MainWindow
    backend/
      __init__.py
      transcription.py           build_command + parse_event + iter_events (Qt-free) for processor.py
      summarization.py           build_command + run() (Qt-free) for ai_client.py (summary pass)
      analysis.py                ADVANCED_PROMPTS (en/ru, 11 feats) + gating + parse_json_response (port of renderer)
      exporter.py                unified raw/summary/analysis -> txt/md/json/html/pdf/docx (block model, footer, version-aware naming, no data loss)
      obsidian.py                Obsidian export: Meetings/<stem>/<stem>_summary.md + <stem>_analysis.md (rich format), _index, _queries(4), People/Topics
    core/
      __init__.py
      models.py                  JobStatus, STAGE_TO_STATUS, ru/en STATUS_LABELS, HistoryEntry + version dataclasses
      history.py                 HistoryStore (id-keyed CRUD, versioning, atomic, naming rule), versioned_filename()
      worker.py                  TranscriptionWorker (QProcess) + AiWorker (QProcess, text) + ExportWorker + ObsidianWorker (QThread)
      queue_manager.py           QueueManager (transcription-only) + resolve_workers(parallelWorkers, cuda)
      pipeline.py                JobRunner (full lifecycle by id) + PipelineQueue (N concurrent runners)
    ui/
      __init__.py
      theme.py                   QSS dark/light from VS Code tokens (build_stylesheet)
      main_window.py             QMainWindow: header (Settings/theme/language) + Upload/Queue/Status/Results (+ export bar: format + Obsidian), drag&drop, slots
      settings_dialog.py         SettingsDialog + AdvancedApiDialog: full settings modal, conditional visibility, config load/save
  # smoke tests (kept, real) and fakes:
  _selftest.py _selftest_core.py _selftest_worker.py _selftest_pipeline.py _selftest_ui.py _selftest_settings.py _selftest_export.py _selftest_export_ui.py _selftest_obsidian.py
  _fake_processor.py _fake_processor_cli.py _fake_ai_cli.py
```

Naming rule (owner): EVERY output is named after the input video stem (incl. the
wav track and chunks, which processor.py creates then deletes after a successful
transcription). For input `123.mkv`: `123.wav`, `123_chunk1.wav` (temp),
`123_raw.txt` (transcript, not versioned), `123_summary.txt` (v1; then
`123_summary_v2.txt`, ...), `123_analysis.json` (v1; then `123_analysis_v2.json`,
...). In `transcripts/<id>/` transcript + summary are `.txt`, analysis is
`.json`; `.md` is ONLY for Obsidian export or an explicit user "export to md".

## 6. DONE & verified

- Foundation, config, id-centric core with versioning, concurrent worker layer
  (proven NO cross-talk between ids), full pipeline lifecycle
  (EXTRACTING->...->DONE), window skeleton (bootstrap + theme + id-routed slots).
- Fixed a real bug: Qt signals used `int` for job_id but ids are ms timestamps
  (>2^31) -> 32-bit overflow / "Slot not found". All job_id signal params are now
  `Signal(object, ...)`. (This was the exact class of bug behind the old desync.)
- **TODO #1 done — advanced analysis ported.** `backend/analysis.py` ports the
  renderer's `ADVANCED_PROMPTS` (en+ru, all 11 features) verbatim, the exact
  feature gating from `performAdvancedAnalysis`, and `parseJSONResponse` /
  `fixIncompleteJSON` (fences, trailing commas, `assigneee` typo, truncation
  recovery, `[]` fallback). `pipeline.py` now runs analysis as N sequential
  `AiWorker` passes (one per enabled feature) over the **transcript**, merges
  the per-feature JSON into the renderer's analysis object, and writes one
  `<stem>_analysis.json` version. Routed through `ai_client.py` (decided: reuse
  backend; the local path already uses temp 0.7 / max 8000), not a new HTTP
  path. Verified on the embedded Python: both files compile; 11/11 prompts;
  gating all-on + subsets; JSON parse of fenced/trailing-comma/typo/truncated/
  garbage; argv built with `{transcript}` stripped + transcript as `--text-file`.
  Corrects two earlier doc errors: analysis input is the TRANSCRIPT (not the
  summary), and it is multi-call per feature (not a single prompt).
- **TODO #2 done — settings window.** `ui/settings_dialog.py` (`SettingsDialog`
  + `AdvancedApiDialog`) ports the full Electron settings modal: Whisper, AI
  provider, prompt templates (selector present), prompt, Markdown/Obsidian,
  Google Sheets, Advanced AI toggles, and the Advanced API modal
  (`advancedSettings[provider]` = endpoint/model/headers/body JSON, validated).
  Every persisted field maps 1:1 to a real `settings.json` key; conditional
  visibility (provider local↔api-key, obsidian/sheets/contextual-memory
  sub-fields) matches Electron. Edits a copy and on Save merges back so unknown
  keys survive (Electron round-trip), then `config.save_settings`. Opened from
  the main-window header (⚙). Pure action buttons (Download Model, Check Update,
  Save/Manage Templates) are rendered DISABLED with a TODO tooltip — deferred to
  their own tasks; built-in template texts await the template system (TODO #1
  remainder). `theme`/`language` stay owned by the toolbar. Verified headless:
  compile + 18 checks (load, visibility, collect, save round-trip, unknown-key &
  advancedSettings preservation, advanced-modal JSON validation).
- **TODO #3a done — unified exporter + Export UI.** `app/backend/exporter.py`
  (Qt-free) exports the three artifacts — raw / summary / analysis — into a
  common set `txt, md, json, html, pdf, docx`. ONE block model (h/p/ul/ol/kv/
  rule/footer) is built per artifact and every writer consumes it, so structure
  is identical across formats and NO field is dropped (the legacy bug). Analysis
  renders ALL 11 features with the real schema keys. Every file ends with a
  `Meeting Summarizer v<package.json>` footer (HTML block / PDF+DOCX paragraph /
  md+txt line / JSON `_generator`). Naming via `versioned_filename` →
  `<stem>_<kind>[_vN].<fmt>` (raw never versioned); the export reads the LATEST
  version from `HistoryStore`, so the exported version always matches the UI
  (fixes "v3 in UI, v1 on disk"). `core/worker.py` adds `ExportWorker` (QThread,
  off the UI thread); `main_window.py` Results gains an export bar (kind + format
  + Export). PDF/DOCX reuse reportlab / python-docx with Cyrillic font
  registration. Verified: `_selftest_export.py` (18 files, 13 feature sentinels
  present in txt/md/html + docx, footer everywhere, naming, JSON round-trip) and
  `_selftest_export_ui.py` (export through the worker: latest-version naming,
  no-loss docx, footer, status) — both ALL_PASS; base UI test still ALL_PASS.
- **TODO #7 done — REAL vector RAG + plain-text Search (rewrite, not a port).**
  The old `rag_integration.py` was a JSON dump with no retrieval (an imitation of
  RAG); per the user this was rebuilt as genuine semantic memory.
  NEW BACKEND:
  * `backend/embeddings.py` — `EmbeddingProvider` with three lazy backends:
    `local` (OpenAI-compatible /v1/embeddings via requests — for llama.cpp /
    Hermes), `openai` (SDK), `sentence-transformers` (optional, lazy-imported so
    it never drags the already-broken transformers stack into the whisper path).
    `provider_from_settings()` reads ragEmbedding* keys, falls back to the
    summary endpoint/key.
  * `backend/rag.py` — chromadb persistent store (cosine), chunking with overlap,
    embeds summary+transcript, semantic search scoped by `project`, collapse to
    one hit per doc. CLI: add/search/list/stats/delete/clear. Guard refuses to
    mix embedding models in one collection (distances would be meaningless) —
    tells the user to rebuild.
  * `backend/textsearch.py` (in desktop/app/backend) — honest literal/regex grep
    over transcripts (port of Electron search.js). FORMAT FIX: speaker filter
    must skip the leading [HH:MM:SS] timestamp; the speaker tag is the SECOND
    bracket, not the first (search.js assumed first).
  DEPS: chromadb 1.5.9 installed into backend/python; verified whisper/torch/
  onnxruntime still import afterwards. Env: Python 3.11.8, CUDA available,
  transformers 5.8.1 was ALREADY broken (tokenizers 0.23.1 vs required <=0.23.0)
  — hence sentence-transformers is optional/lazy.
  NEW UI:
  * `desktop/app/core/worker.py` — `RagWorker(QThread+subprocess)` runs rag.py,
    parses JSON, emits (op, ok, data, error). Off the UI thread.
  * `desktop/app/ui/rag_dialog.py` — tabs: semantic Search (query+project+top-k,
    ranked hits with %), Library (list/delete per project), Stats (counts,
    projects, provider/model/dim, Clear all).
  * `desktop/app/ui/search_dialog.py` — plain-text search across all transcripts
    (regex/case/date/speaker filters, highlighted context, export to txt),
    file reads on a background QThread.
  * `models.py`/`history.py` — `HistoryEntry.project` field (JSON key `project`)
    + `HistoryStore.set_project()`. `paths.py` — `RAG_SCRIPT`.
  * `main_window.py` — Project field on the Results section (persists per entry),
    "➕ В базу знаний" (add latest summary+transcript to KB, gated on a summary),
    "🧠 База знаний" (opens RagDialog), "🔎 Поиск" (opens SearchDialog).
  QSS: projectEdit, searchResults/ragResults/ragLibrary/ragStats, <mark>.
  Verified: `_selftest_rag.py` (23, real chromadb + semantic ranking),
  `_selftest_textsearch.py` (25), `_selftest_rag_ui.py` (16). All 13 prior
  selftests ALL_PASS — 16 sets total, no regression.
- **TODO #6 done — Regenerate + version switching + vosk timestamp fix.**
  VERSION LINKAGE: added `AnalysisVersion.source_summary_version` (camelCase
  `sourceSummaryVersion` in JSON, defaults 0 for legacy) so a regenerated summary
  without a paired analysis no longer silently drifts — the UI shows "← from
  summary vN" on each analysis version. `history.add_analysis_version` accepts
  `source_summary_version` (defaults to the latest summary present at the time).
  `pipeline.JobRunner`: tracks `_summary_version`, links analysis to it; new
  `start_from_transcript()` begins at the summary stage (skips transcription and
  the speakers gate). `PipelineQueue.enqueue_regenerate()` + 3-tuple pending
  (transcript_path=None => full pipeline). `main_window`: per-section version
  switchers (◀ / dropdown / ▶) for summary and analysis, "🔄 Перегенерировать"
  button (writes the edited transcript from txt_raw back to the raw file, then
  enqueues regenerate; QMessageBox confirm). `_load_results` rewritten to drive
  selection indices; nav auto-hides for single-version, disables at ends.
  QSS: verNav / verCombo.
  VOSK FIX: `backend/processing/engines/vosk_engine.py` now writes `[HH:MM:SS]`
  timestamps (first word 'start' + chunk offset) like the whisper engines; was
  the only engine emitting plain text with no timestamps. (faster-whisper,
  openai-whisper, whisperx already wrote timestamps — verified.)
  Verified: `_selftest_versions.py` (19) + `_selftest_versions_ui.py` (23) ALL_PASS;
  all 11 prior selftests ALL_PASS.
- **TODO #5 done — Transcript editing + speaker management.**
  IMPORTANT format discovery: the REAL diarised line format (from
  `backend/processing/engines/whisperx_engine.py::_format_whisperx_with_speakers`)
  is `[HH:MM:SS] [SPEAKER_NN]: text` — timestamp FIRST, then `[SPEAKER_NN]:`
  (uppercase, leading zero, colon OUTSIDE the bracket). The other file
  `backend/whisperx_transcriber.py` is a DIFFERENT (unused) implementation with
  `[Speaker N]:` and no timestamp — do not parse against it. faster-whisper
  output is `[HH:MM:SS] text` with NO speaker label, so `extract_speakers()`
  returns [] for it (correctly gating the feature off).
  `app/backend/speakers.py`: Utterance dataclass; `parse_utterances`,
  `extract_speakers` (diarised labels only, numeric sort), `speaker_stats`,
  `rename_in_transcript` (label swap, timestamps+order preserved),
  `apply_edited_utterances` (per-line edits by global index, order preserved).
  `app/ui/speakers_dialog.py`: one `_SpeakerCard` per speaker, each holding that
  speaker's utterances as chronological `_UtteranceRow`s (timestamp label +
  editable QPlainTextEdit), stats sidebar with speaking-share bars. Save renames
  + applies edits without reordering; Cancel continues unchanged.
  `core/pipeline.py`: JobRunner gains `speakers_needed` signal,
  `_waiting_for_speakers` flag, `resume_summary()` / `skip_speakers()`; gate in
  `_on_tx_done` (engine==whisperx AND markers). PipelineQueue re-emits
  `speakers_needed` and exposes `runner(id)`. `ui/main_window.py`: imports dialog,
  adds "👥 Спикеры" button (enabled only on diarised transcript via
  `_update_speakers_button`), `on_speakers_needed` (auto modal → resume/skip),
  manual `_do_speakers`, and writes renamed transcript back to the raw file.
  QSS: speakerCard / speakerNameEdit / utteranceTs / utteranceText.
  Verified: `_selftest_speakers.py` (33), `_selftest_speakers_ui.py` (34) ALL_PASS.
- **TODO #4 done — Advanced Analysis panels.**
  `app/ui/analysis_widget.py`: `AnalysisWidget` (QScrollArea) wrapping 11 `_Panel`
  subclasses (QGroupBox). Each panel renders one feature from the analysis JSON in
  the same structure as the Electron renderer's render* functions (renderActionItems,
  renderSentiment, etc.). Panels use `_has_data` flag to support offscreen visibility
  testing. QSS tokens added to `theme.py` for `analysisPanel`, `analysisCard`,
  `quoteCard`, `categoryBadge`, `categoryTag`, `techContext`, `protocolText`, etc.
  `main_window.py` updated: imports `AnalysisWidget`; `_section_results()` adds the
  widget below the export bar; `_load_results()` reads latest `analysis_versions` JSON
  and calls `analysis_widget.load()`; `toggle_language()` re-renders on language switch.
  Verified: `_selftest_analysis_ui.py` (27 checks ALL_PASS). No regression on prior 8
  selftests.
- **TODO #3b-i done — Obsidian export (matches the user's real vault exactly).**
  `app/backend/obsidian.py` writes `Meetings/<stem>/<stem>_summary.md` and
  `<stem>_analysis.md` (stem-named folder + files, matching `transcripts/`). The
  analysis note is the rich legacy format — `# 📊 Meeting Analysis`,
  `**File:**`/`**Generated:**`, a Характеристики table (Длительность/Участники/
  Количество слов/Ключевые темы), then emoji sections (✅ action items with RU
  priority, 😊 sentiment table + dominance, 📂 category, 🔴 risks, 💬 quotes,
  💻 technologies grouped by category, ❓ questions, 💡 recommendations; +🔄/📜
  for followup/protocol), footer `*Generated by Meeting Summarizer v1.1.1*`. The
  summary note is frontmatter (`type/date/title/tags/app_version`) + body +
  footer. `_index/By Date.md` gets `- [[<stem>/<stem>_summary|<stem>]] - <topic>`
  under `## <date>`; `_queries/` gets the 4 static Dataview files (Action
  Items / By Person / By Topic / Recent Meetings); People notes (per
  dominanceDistribution speaker, `(NN% speaking time)`) and Topics notes (per
  keyTopics+tags) at vault root — all gated by the real settings toggles. Word
  count is computed from the transcript. Auto-writes on completion when
  `obsidianIntegration` is on (best-effort in `pipeline._finish_ok`); manual
  "→ Obsidian" button via `ObsidianWorker` (QThread). NOTE: the original
  generator is not in THIS repo, but the full pre-edit project is backed up at
  `C:\Scripts\meeting-summarizer_old` (see §8) — its
  `electron/renderer.js` has the real Obsidian/analysis generator to verify
  against. This module was reconstructed from the user's real vault artifacts;
  an earlier attempt used the wrong layout
  (`Meetings/YYYY/MM-Month/<date> - <name>.md`) and was corrected. Verified:
  `_selftest_obsidian.py` (18 checks: exact names, rich format, word count, no
  loss, index, 4 queries, People/Topics, versioning) + `_selftest_export_ui.py`
  (manual button) — ALL_PASS; pipeline + base UI no regression.

## 7. TODO (priority order)

1. **[DONE — see §6] Analysis prompts ported** (`backend/analysis.py`: multi-
   feature, transcript-input, via `ai_client.py`). REMAINING sub-task: wire the
   per-meeting/per-template **summary** prompts (settings has `prompt`;
   templates list in `index.html` / `prompts.js` default+speakerAware). The
   `analysis_prompt` ctor arg on `JobRunner` is now unused (gating comes from
   settings) — drop it (and the `main.py` line that passes it) when next editing
   `main.py`.
2. **[DONE — see §6] Settings window** (`ui/settings_dialog.py`). REMAINING
   sub-tasks (own tasks): wire the action buttons (model download / whisper
   update → backend; Save/Manage Templates → template system); the built-in
   prompt-template texts (TODO #1 remainder). Owner reminded: the app has MANY
   windows — Settings is the first; Speakers/Templates/RAG modals still to come.
3. **[3a + 3b-i DONE — see §6] Exporters + Obsidian.** Unified
   `app/backend/exporter.py` + Export bar (raw/summary/analysis → 6 formats, no
   data loss, footer, version-aware naming) and `app/backend/obsidian.py` +
   auto/manual Obsidian write. REMAINING **3b-ii — Google Sheets**: NOT a port —
   the Electron app has no Sheets implementation (no endpoint/fetch, no
   googleapis dependency; settings UI only). A real implementation would need a
   live mechanism (Apps Script webhook `/exec` accepting POST; an API key alone
   cannot write to Sheets) — deferred pending owner's webhook. Also: analysis
   HTML is a faithful card layout; exact Electron-CSS/QWebEngine parity is
   decided in TODO #4. Optional later: a "Save to…" custom-path dialog (exports
   currently land in `transcripts/<id>/`).
4. **[DONE — see §6] Advanced Analysis panels** (11 panels) — `app/ui/analysis_widget.py`
   renders the analysis JSON in the UI: characteristics (key topics), action items,
   sentiment (metrics + bars + dominance), category (badge + tags), risks, quotes,
   technologies (grouped by category), questions, recommendations, follow-up questions,
   formal protocol (full ГОСТ/ISO layout). Panels auto-hide when data absent;
   language-switchable; QSS-styled. Loaded from `_load_results()` in `main_window.py`;
   refreshes on language toggle. Verified: `_selftest_analysis_ui.py` (27 checks ALL_PASS)
   + all 8 prior selftests ALL_PASS (no regression).
5. **[DONE — see §6] Transcript editing + speaker management.**
   `app/backend/speakers.py` (parser for the REAL WhisperX format
   `[HH:MM:SS] [SPEAKER_NN]: text`) + `app/ui/speakers_dialog.py` (per-speaker
   cards, chronological editable utterance rows with timestamps, stats sidebar).
   Pipeline gates after transcription: if engine==whisperx AND diarisation
   markers present, emits `speakers_needed`; UI shows modal; Save→`resume_summary`
   (renamed transcript + participants), Cancel→`skip_speakers` (continue as-is).
   "👥 Спикеры" button in Results bar is enabled ONLY when the loaded transcript
   has diarisation. Verified: `_selftest_speakers.py` (33) + `_selftest_speakers_ui.py`
   (34) ALL_PASS; all 9 prior selftests ALL_PASS.
6. **[DONE — see §6] Regenerate** summary/analysis as a NEW version (compare
   models), with summary↔analysis version linkage (`source_summary_version`),
   per-section version switchers (arrows + dropdown), and the vosk timestamp fix.
   Verified: `_selftest_versions.py` (19) + `_selftest_versions_ui.py` (23)
   ALL_PASS; all 11 prior selftests ALL_PASS.
7. **[DONE — see §6] RAG** modal + add-to-RAG actions (`backend/rag.py` and
   its CLIs); **Search** panel.
8. **[SUPERSEDED by #14] vosk** — was "install package + wire engine". The engine
   adapter + UI option already exist; what was missing (package install, model
   availability/resolution, engine-aware model selection, download/update) is now
   the engine/model subsystem in **#14**. `vosk 0.3.45` is now installed in
   `backend/python` and **live-verified** (2026-07-01): `desktop/_livetest_vosk.py`
   ran the real vosk adapter against `vosk-model-small-ru-0.22` and produced correct
   Russian text with `[HH:MM:SS]` timestamps (the #6 timestamp fix).
9. **[DONE] CUDA auto-detect.** `core/device.py::probe()` runs
   `torch.cuda.is_available()` + device name in a SUBPROCESS (torch import is slow)
   so startup never blocks; `core/worker.py::DeviceWorker(QThread)` runs it off the
   UI thread and emits `detected(cuda, name)`. `main_window` shows a header indicator
   (`⏳ Checking GPU…` → `🟢 GPU (CUDA)` +device-name tooltip / `⚪ CPU`), i18n + theme
   styled (`#deviceIndicator`), and on detection recomputes
   `resolve_workers(parallelWorkers, cuda=…)` (a single GPU is VRAM-bound → `auto`
   drops to 1 worker) applying it live via the new
   `PipelineQueue.set_max_concurrency`. Explicit worker counts ignore CUDA. Verified:
   `_selftest_device.py` (10 — REAL probe found NVIDIA RTX 4060 Ti / torch 2.5.1+cu121)
   + `_selftest_device_ui.py` (9). NOTE: the full event-loop smoke (DeviceWorker thread
   → signal → label) is flaky to run under this headless automation shell; the probe
   and the UI wiring are each covered by the two selftests above.
10. **[DONE] Diagnostics window + engine A/B (owner-approved scope).** Instead of a
    literal port of the Electron dev-suite (much of it Electron-specific and would be
    stubs), built a REAL `ui/diagnostics_dialog.py` (header 📊) with 4 tabs on real data:
    - **System** — live CPU/RAM/GPU via `core/metrics.py` (psutil + `nvidia-smi`),
      QTimer-sampled while open (primes `cpu_percent`); GPU gracefully absent.
    - **Processing profile** — renders a meeting's REAL backend trace
      (`core/trace.py` normalises the `<id>/<stem>_trace.json` spans that
      `PerformanceTracer` already writes) as a timeline (flat spans → gantt bars, not
      a fake nested flamegraph).
    - **Compare engines** (the reimagined A/B, useful with 8 engines) — pick a file +
      engines (catalog-driven, per-language runnable model + availability) → run the
      REAL processor once per engine via `core/worker.py::CompareWorker` (QThread,
      sequential, per-engine error isolation) → table of time/chars/status + transcript
      preview. Real multi-engine RUN is exercised in #11; orchestration is unit-tested
      with the fake processor.
    - **Logs** — tails `logs/app.log`, now actually written by a new
      `logging_setup.py` (rotating handler, wired in `build_app`; per-job lines via the
      queue's existing signals — pipeline internals untouched).
    DELIBERATELY OMITTED (do not apply to PySide/QProcess — would be stubs, against
    DEVELOPMENT_RULES): Electron IPC metrics, the JS eval console, watched vars / call
    stack / heap snapshots, and the JS-side regression/coverage harness. NEW DEP:
    `psutil 7.2.2` (add to packaging #12). Verified: `_selftest_diagnostics.py` (18 —
    real metrics incl. RTX 4060 Ti, trace layout, 4 tabs, compare model-resolution +
    CompareWorker over the fake processor); full UI regression green.
11. **[DONE — LIVE-VERIFIED] End-to-end run on a real 5-min meeting video.**
    Transcribe→summary→analysis validated on both a LOCAL LLM and a CLOUD LLM.
    - **Transcription**: faster-whisper `medium` on CPU → clean RU transcript (134
      lines) of the real meeting. NOTE (important, real constraint): whisperX on GPU
      failed with `CUDA out of memory` because the user's local 35B LLM (llama.cpp)
      already occupies the RTX 4060 Ti's 16 GB — GPU transcription and a big GPU LLM
      can't run at once. Practical guidance: transcribe on CPU (or use a smaller LLM,
      or sequence them). openai-whisper/faster-whisper/vosk/sherpa/whisper.cpp all run
      on CPU.
    - **Summary + analysis — LOCAL** (llama.cpp Qwen3-30B, OpenAI-compatible at
      :8080/v1): works via the reworked `local` path. Summary 4478 chars, structured;
      analysis actionItems → valid JSON (4 items, `parse_json_response` handled the
      reasoning-model output). BUT SLOW (summary ~299 s, one analysis feature ~115 s)
      and the reasoning `<think>` block eats `max_tokens=8000`, so the summary
      TRUNCATED (missing the last theme). Tuning needed for reasoning models: raise
      `max_tokens` and/or disable thinking. (Qwen3 is a thinking model; `ai_client`
      correctly returns `content`, not `reasoning_content`.)
    - **Summary + analysis — CLOUD** (Gemini): fast + complete + high quality. Summary
      5020 chars in ~25 s (all 5 themes, named people); analysis actionItems (list) +
      sentiment (dict) valid JSON in ~16–34 s each. Best for weak machines (owner's
      expectation confirmed). FIXED en route: the default Google model `gemini-1.5-flash`
      is retired (404) → updated to `gemini-2.5-flash` + refreshed the Settings model
      list (verified against the key's ListModels).
    Both AI paths use the reworked `ai_client.py` (provider/model/endpoint). Test
    artifacts are gitignored (real meeting data). FOLLOW-UP DONE: `max_tokens` is now
    per-provider auto (`--max-tokens 0`): local/gemma → 32000 (reasoning models need
    headroom; local context is 131k/262k), cloud → 8000 (APIs cap output + reject
    oversized values); explicit override still honoured. Thinking is kept ON (quality)
    — a `/no_think` toggle is left as a future opt-in, not default.
12. **[DONE] Packaging** — `desktop/packaging/build.py` streams files straight into a
    zip (no multi-GB staging copy), two variants:
    - **min** (`meeting-summarizer-min-v1.1.1.zip`, **238 MB**): source (desktop/ +
      backend/*.py) + ffmpeg + `INSTALL.bat` (pip torch cu121 + `desktop/requirements.txt`)
      + `RUN.bat`. Per owner's "risky-to-download → also ship in min" rule, it also bundles
      the small Vosk RU+EN models (alphacephei is a fragile single-site source); everything
      else (PyPI / HuggingFace / k2-fsa) is reliably re-fetchable on demand.
    - **full** (`meeting-summarizer-full-v1.1.1.zip`, **12.2 GB**, 38 184 files): the
      embedded Python+CUDA runtime + ffmpeg + `RUN.bat` (no install, no network) + the
      `VARIANTS["full"]` model set — every engine, ONE medium-tier model each, both
      languages: whisper medium, faster-whisper medium (also whisperx), whisper-cpp medium,
      Vosk ×4, sherpa-onnx RU+EN, funasr SenseVoice, offline diarization. NOT sherpa-extra.
    `VARIANTS["full"]` was corrected to this spec (was an over-broad tiny→large list that
    omitted sherpa/funasr). Fetched the 2 missing models first (whisper-cpp medium, sherpa
    EN). Verified: min + full CRC-integrity OK; structure/leak checks pass (no user data,
    no __pycache__, no tiny/base whisper, no sherpa-extra; full has the runtime, min does
    not). Archives land in `dist/` (gitignored). NOTE: a full extract-and-run wasn't done
    on this machine (12 GB free < 19 GB extracted); the bundled `backend/python` is a
    byte-copy of the runtime all 32 selftest sets pass on.
13. **RAG follow-ups (deferred from TODO #7).**
    - **13a — expose `rebuild` in CLI/UI. [DONE]** `rag.py` gains a `rebuild`
      subcommand: `_source_lookup_from_history(history_file)` builds the required
      `doc_id -> (summary, transcript)` lookup from `config/history.json` — doc-id is
      the history entry id (`str`), summary is the LATEST summary version (falls back
      to `summaryPath`), transcript is `transcriptPath`; missing sources return ("","")
      so `RagStore.rebuild` skips + reports them. `rebuild --rag-dir D --history-file H
      --settings JSON` re-embeds every doc from FRESH source with the CURRENT provider
      (the point: after switching the embedding model). UI: `RagDialog` Stats tab gains
      a "Re-index" button (confirm → `_do_rebuild` → `_run("rebuild", ["--history-file",
      …])`, settings included), `history_file` threaded from `main_window`
      (`store.path`). Verified: `_selftest_rag_rebuild.py` (10 — source_lookup latest-
      version/missing + end-to-end rebuild: doc rebuilt from fresh source, missing-source
      doc skipped, rebuilt doc searchable), `_selftest_rag_ui.py` now 19 (rebuild button
      + command wiring), base `_selftest_rag.py` (23) green, CLI smoke `{"rebuilt":0}`.
      ENV REPAIR (2026-07-01): chromadb was broken — `pydantic`, `tenacity`,
      `python-dotenv` were MISSING from `backend/python` (pre-existing; not caused by
      the engine installs), so RAG couldn't import at all. Reinstalled `pydantic 2.13.4`
      + `tenacity 9.1.4` + `python-dotenv 1.2.2` (chromadb 1.5.9 deps); torch/whisper
      verified intact. Packaging (#12) must include these.
    - **13b — packaging reminders for RAG (relates to #12).** `backend/embeddings.py`
      and `backend/rag.py` live under `backend/` (NOT `desktop/`) — the portable
      build must include them. `chromadb` (1.5.9) is a NEW dependency installed
      into `backend/python` — add it to the freeze/build manifest. (Both already
      noted in §9; tracked here so packaging doesn't miss them.)
14. **Engine/model management subsystem (expands #8 — agreed with owner).** #8 is
    no longer "just install vosk": the app must ship/check/download models for
    MANY engines and stay extensible to new OSS engines. Old Electron vosk was a
    never-run stub — do NOT port it. STEP 1 DONE: `backend/engines_registry.py`
    (declarative — engines → selectable models → on-disk path → language/tier →
    build variant; whisperx has no own model, resolves to faster-whisper;
    `is_implemented` flag; `VARIANTS` minimal/medium/full where EVERY variant
    ships EVERY engine and only the heavy models differ; vosk via alphacephei
    zip, whisper/faster via their own loaders). `_selftest_engines.py` (35)
    ALL_PASS against the real disk. On branch `feat/engine-model-management`.
    REMAINING:
    - **14a — download/update. [DONE — backend mechanics]** `backend/download_model.py`
      rebuilt registry-driven: per-engine source — whisper = openai-whisper
      `_download` (SHA-verified); faster/whisperx = `huggingface_hub.snapshot_download`
      into the HF cache layout; vosk = alphacephei zip + HTTP-range resume + extract.
      Pure `plan()` (no network) + `download()` (skips if present, refuses engines
      with `implemented=False`) + `check_update()` (whisper/vosk static; faster/
      whisperx compare HF revision). `backend/models_cli.py` JSON facade
      (engines/list/available/resolve/download/check-update). NO single unified
      loader (owner). `_selftest_models_cli.py` (25) ALL_PASS — exercises the CLI
      + all no-network paths. The actual network downloads are NOT yet run (verify
      at first real download in 14b / live run #11). `ModelsWorker` moved to 14b
      (built where it is wired).
    - **14b — engine-aware Settings UI. [DONE]** Model dropdown is catalog-driven
      and switches by engine (whisper sizes ↔ concrete vosk model names); a
      ``models_cli.py catalog`` snapshot feeds it (injectable for tests, static
      fallback offline). Availability indicator (✓ installed / ⬇ missing),
      working Download + Check-update buttons over a `ModelsWorker`
      (QProcess streaming JSON-lines) in `core/worker.py`. Engines with
      `implemented=False` are listed but disabled (not selectable/downloadable).
      Startup check (`main_window._check_configured_model`, QTimer.singleShot)
      warns + offers Settings if the configured model is missing.
      `_selftest_engines_ui.py` (18) ALL_PASS (hermetic, injected catalog);
      `_selftest_models_cli.py` now 30 (catalog); `_selftest_settings`,
      `_selftest_ui`, `_selftest_versions_ui` regress green. NOTE: the real
      network download + the startup modal are not headless-tested (verify on a
      real download / first launch).
    - **14c — registry-driven dispatch. [DONE]** `backend/processor.py` no longer
      has an if/elif engine chain: `_build_adapters()` maps engine id -> a callable
      taking the 6 common transcribe args (each adapter's differing trailing args —
      tracer / missing-dep / cuda-status — bound via `_adapter`), and
      `resolve_engine()` picks the engine (known -> itself; declared-but-not-
      implemented (implemented=False) -> clear error; unknown -> default whisper). Adding
      an engine = registry entry + one line in `_build_adapters` + its module.
      `_selftest_dispatch.py` (12) ALL_PASS — invariant `set(adapters) ==
      implemented engines`, resolve logic, and full per-engine arg wiring (via
      monkeypatch, no real transcription).
    - **14d — adapters for declared engines. [sherpa-onnx DONE (code), live-unverified]**
      `backend/processing/engines/sherpa_onnx_engine.py` implements
      `transcribe_audio_sherpa_onnx` against the documented sherpa-onnx Python API
      (`OfflineRecognizer.from_transducer`; per-chunk create_stream/accept_waveform/
      decode_streams/result.text -> one `[HH:MM:SS] text` line in `<base>_raw.txt`).
      Files are found by glob (prefers fp32 over int8); model resolved via the
      registry (`_resolve_sherpa_model`, honours name / language fallback / clear
      error if missing). Registry: 4 verified transducer models (RU + EN, small +
      large), kind `sherpa_transducer` (dir with tokens.txt + encoder/decoder/
      joiner .onnx) under `resources/sherpa_models/` (gitignored); download via
      `sherpa_targz` (k2-fsa release `.tar.bz2` -> tarfile extract). Wired into
      `processor._build_adapters`; `implemented=True`. Tests: `_selftest_sherpa.py`
      (13), `_selftest_dispatch.py` (12), `_selftest_engines.py` (36),
      `_selftest_models_cli.py` (30), UI regress green.
      **LIVE-VERIFIED (2026-07-01):** `sherpa-onnx 1.13.2` installed into
      `backend/python`; the RU model `sherpa-onnx-small-zipformer-ru-2024-09-18`
      downloaded via `models_cli.py download` (range-resume works) and extracted to
      `resources/sherpa_models/`; `desktop/_livetest_sherpa.py` ran the real adapter
      on the model's bundled `test_wavs` and produced correct Russian text with
      `[HH:MM:SS]` lines ("я тебя люблю" / "родион потапыч высчитывал…"). The
      adapter is now RUN, not just code-reviewed.
      Remaining for #14d: whisper.cpp, FunASR adapters (future).
    - **14e — vosk model selection. [DONE]** `vosk_engine.py` now resolves its
      model via `engines_registry` (`_resolve_vosk_model`): it honours the
      concrete vosk model NAME from `whisperModel` (e.g. `vosk-model-small-ru-0.22`,
      and a cross-language pick wins over the `language` arg); legacy callers
      passing a whisper size / blank fall back to the small model for the
      language; a known-but-not-downloaded model raises with guidance (no silent
      wrong model). `_selftest_vosk_resolve.py` (8) ALL_PASS; `_selftest_dispatch`
      regress 11/11 (processor + vosk_engine import clean).
    - **14f — whisper.cpp + FunASR adapters.** Goal (owner): give the user EVERY
      free local RU/EN transcription option so they can compare and choose. Each
      honours the same download / reload / check-version / update flow.
      - **whisper.cpp [DONE — LIVE-VERIFIED 2026-07-01].** Registry engine
        `whisper-cpp` (multilingual, ru+en), models = ggml `.bin` (tiny..large-v3)
        from `ggerganov/whisper.cpp` on HF; kind `whispercpp_ggml` →
        `resources/whispercpp_models/<file>`; download via `_http_download_resume`
        (single .bin, range-resume). Adapter `whispercpp_engine.py` via
        `pywhispercpp.model.Model` → per-segment `[HH:MM:SS] text` lines (offset
        `idx*600 + t0/100`). Wired into `processor._build_adapters`. Installed
        `pywhispercpp 1.5.0`; downloaded ggml-base (141 MB) via `models_cli`; ran
        `desktop/_livetest_whispercpp.py` → correct punctuated Russian
        ("Я тебя люблю." / "…высчитывал каждый новый вершок углубления,"). Tests:
        `_selftest_engines.py` (43), `_selftest_dispatch.py` (15), `_selftest_models_cli`
        (30) ALL_PASS.
      - **FunASR [DONE — SenseVoice LIVE-VERIFIED 2026-07-01] — EN-only.** FunASR/
        Paraformer/SenseVoice have NO Russian model (only en/zh/ja/ko/yue), so per the
        owner it is an EN-only engine (registry `multilingual=False`, `lang="en"` →
        hidden for language=ru). CRUCIAL: it runs through the ALREADY-installed
        **sherpa-onnx** runtime (`from_sense_voice` / `from_paraformer`) — NO heavy
        `funasr`/modelscope package, so the fragile torch/transformers/whisperx stack
        is untouched (owner picked this over the literal `funasr` package). New engine
        `funasr` (kind `funasr_onnx` → `resources/funasr_models/<dir>`, validated by
        tokens.txt + model*.onnx); models are the SAME k2-fsa release .tar.bz2 as
        sherpa (download reuses `sherpa_download_url` + a bz2 extract). Two models
        (owner wanted several): SenseVoice `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-
        int8-2024-07-17` (en/zh/ja/ko/yue) and Paraformer `sherpa-onnx-paraformer-en-
        2024-03-09` (English). Adapter `funasr_engine.py` dispatches the loader by
        `model_type`. LIVE-VERIFIED (both loaders): SenseVoice (235 MB, `from_sense_voice`)
        → "The tribal chieftain called for the boy and presented him with 50 pieces of
        gold."; Paraformer-en (220 MB, `from_paraformer`) → "after early nightfall the
        yellow lamps would light up here and there the squalid quarter of the brothels".
        Both via `desktop/_livetest_funasr.py` on the models' bundled `en.wav`. Tests:
        `_selftest_engines.py` (51), `_selftest_dispatch.py` (17), `_selftest_models_cli`
        (30), `_selftest_engines_ui` (18) ALL_PASS.
      Remaining for #14f (future): whisper.cpp CUDA build, more FunASR/NeMo models.
      NET RESULT — the app now ships **7 transcription engines**: whisper, faster-whisper,
      whisperx, vosk, sherpa-onnx, whisper-cpp, funasr. RU+EN free-local coverage:
      whisper/faster/whisperx/vosk/sherpa/whisper.cpp do RU+EN; funasr is EN-only.
    - **14g — extra / download-only community models. [DONE — both loaders LIVE-VERIFIED].**
      New engine `sherpa-extra` (registry `extra: True`) surfaces OPTIONAL models that
      run on the already-installed sherpa-onnx runtime but are **NOT bundled in ANY build
      variant (not even full)** — download-only; a `_selftest_engines` check enforces they
      never enter VARIANTS. Adapter `sherpa_extra_engine.py` dispatches the loader by
      `model_type`: `nemo_ctc`→`from_nemo_ctc`, `moonshine`→`from_moonshine` (4-file bundle).
      kind `sherpa_extra_dir` → `resources/sherpa_extra_models/<dir>`, per-type validation
      (`_is_extra_model_dir`); download reuses the k2-fsa `.tar.bz2` mechanism
      (`sherpa_extra_targz`). Curated set (all archive names + file layouts verified against
      k2-fsa docs/releases): RU — GigaAM v2/v1 CTC (`sherpa-onnx-nemo-ctc-giga-am-v2-russian-
      2025-04-19`, …2024-10-24; **non-commercial licence**, noted in the label); EN — Moonshine
      tiny/base (`sherpa-onnx-moonshine-{tiny,base}-en-int8`). `models_cli` catalog now carries
      an `extra` flag so the UI can label the engine "download-only, not bundled". LIVE:
      Moonshine tiny → "After early nightfall, the yellow lamps would light up…"; GigaAM v2 →
      "ничьих не требуя похвал, счастлив уж я надеждой сладкой…" (Russian). Adding more k2-fsa
      models = one registry row (+ a loader branch for a
      new architecture). Tests: `_selftest_engines` (59), `_selftest_dispatch` (19), `_selftest_models_cli` (30) ALL_PASS.

## 7b. Port-completeness audit (2026-07-01) — gaps found vs `meeting-summarizer_old`

Cross-checked the old Electron app (`electron/renderer.js` 15 266 lines, `main.js`
29 IPC handlers, `index.html` modals) against the ported `desktop/`. Ported & verified:
transcription (4 engines + registry/download/update), summary (9 providers), advanced
analysis (11 panels), exports (6 formats), Obsidian, speakers+transcript editing,
history+versioning+regenerate, RAG+search, settings, drag&drop, batch/queue, cancel
(queue_manager.cancel + worker.kill). **Gaps that must NOT be lost:**

15. **[DONE] Contextual Memory — implemented, opt-in, strictly project-scoped.**
    Default is now `useContextualMemory=False` (opt-in; was True — risked mixing unrelated
    meetings that shared the default project "meets"). It ONLY injects prior summaries of
    the SAME `projectId` — a retrospective never pulls a different topic's context unless
    the user tags both with the same project. Settings shows a hint spelling this out; the
    toggle fully disables it. `JobRunner._contextual_memory_block()`:
    when `useContextualMemory` is on and `projectId` is set, it gathers the latest
    summaries of PRIOR meetings in the SAME project (from `HistoryStore`), bounded (last
    3 meetings, ~1500 chars each, ~6000 total) and appends them to the summary prompt so
    the model keeps continuity across a project's meetings. Robust (no RAG/embedding
    dependency at summary time; graceful '' when disabled/no project/no priors). Verified:
    `_selftest_contextual_memory.py` (7 — includes same-project, excludes other projects,
    empty when off/no-project/no-priors). [was a stub — see below]

    _Original gap:_ Settings had the
    `useContextualMemory` checkbox + project field (ported), but NO backend consumes
    it. Old app (`renderer.js:10783–11029`): when on, it builds
    `"Контекст из предыдущих встреч проекта:\n" + <prior summaries>` and APPENDS it to
    the summary/analysis prompts (`transcript + contextualMemoryText`). New app saves
    the toggle but the pipeline ignores it → the feature silently does nothing
    (violates DEVELOPMENT_RULES "UI без execution"). TODO: in
    `desktop/app/backend/summarization.py` / `analysis.py` build the prior-meetings
    context (by `project` from `HistoryStore`) and inject it into the prompt when the
    toggle is on. Gate: only prior meetings of the same project.

16. **[DONE] Templates system.** `app/backend/templates.py`: built-in library ported
    from the Electron app + user templates persisted to `config/prompt_templates.json`
    (save/load/delete/export/import). Settings: catalog-driven selector (built-in + user,
    user marked `•`); picking one fills the prompt; Save/Manage are REAL (were stubs).

    **Completeness pass (owner review):**
    - **Speaker/non-speaker variants** — each built-in now carries BOTH `prompt` and
      `prompt_speaker` (exactly like the old app's `useSpeaker ? … : …`); the selector
      serves the speaker-aware variant when "Use speaker-aware prompt" is ticked, and
      re-fills live when the box is toggled. (This was a real port gap — only the plain
      variant had been carried over.)
    - **12 meeting types** — the original 7 (general/standup/retrospective/planning/
      brainstorming/client/interview) plus 5 new (one_on_one, status, kickoff, demo,
      all_hands), RU+EN, both variants.
    - **Edit + rename** — `ManageTemplatesDialog` gains Edit (name+prompt editor);
      `save_user(name, prompt, old_name=…)` supports in-place edit and rename.
    Verified: `_selftest_templates.py` (32 — 12-type shape, speaker-variant differ,
    custom falls back empty, edit/rename/CRUD/export-import) + `_selftest_settings`
    (selector ≥13, apply fills prompt).

    _Original gap:_ Old app has full prompt-template
    CRUD: `saveTemplate / loadTemplate / editTemplate / deleteTemplate / importTemplate
    / exportTemplate / applyTemplate / renderTemplatesList` + a built-in template
    library (7 meeting types) + speaker-aware variant. New app: settings has only a
    template SELECTOR placeholder; the modal, CRUD, and built-in texts are NOT ported
    ("Save/Manage Templates" buttons are disabled stubs — see §6 TODO #2 remainder).
    TODO: `desktop/app/ui/templates_dialog.py` + persistence + wire the selector to
    the summary prompt.

17. **[DONE] Session/meeting Stats modal.** `app/ui/stats_dialog.py` — header 📈 button.
    The old app kept an in-memory per-session counter; the port instead aggregates the
    PERSISTENT history (survives restarts, more useful): total meetings, how many reached
    transcript/summary/analysis, total transcribed words, and breakdowns by status and by
    project. `aggregate(store)` is Qt-free (unit-tested); the dialog renders an HTML table
    with Refresh/Close. Verified: `_selftest_stats.py` (8 — counts, word sum, project/status
    breakdown, dialog renders). [was missing — see below]

    _Original gap:_ Old app `openStatsModal` /
    `updateSessionStats` / `updateStatsUI`: aggregate session/processing stats
    (totalMeetings, totalTime, avgTime, totalWords, processingTime). Not ported.
    TODO: `desktop/app/ui/stats_dialog.py` reading `HistoryStore`.

18. **[DONE] Google Sheets export — Apps Script webhook.** The old app called the Sheets
    REST API v4 with only an API key, which cannot write (append needs OAuth) — so its
    export never actually worked. Reimplemented via a user-deployed Apps Script web app:
    `backend/gsheets.py` (Qt-free, stdlib `urllib`, no deps) assembles a row in the old
    column schema (Date, Meeting Name, Summary, Duration, Participants, Word Count, Action
    Items, Sentiment, Category, Key Topics) and POSTs `{headers, values}` to the `/exec`
    URL; the script appends headers on first use. `JobRunner._maybe_export_gsheets()`
    auto-appends after each run (best-effort, never fails the job — mirrors old app).
    Settings: URL field + help + "Copy Apps Script" button (API-key field removed — it was
    dead). Selftest `_selftest_gsheets.py` = 22.

19. **[DONE] Export-by-speaker.** Was NOT ported → added `speakers.export_by_speaker(
    transcript, out_dir, base_name, name_map)`: writes one `<base>_<speaker>.txt` per
    speaker (chronological `[ts] text`, honours renamed speakers), like the old app.
    Main-window Results gains a "📤 По спикерам" button (enabled only on a diarised
    transcript, same gate as 👥 Спикеры) → folder picker → per-speaker files. Verified:
    `_selftest_export_speaker.py` (6 — per-speaker grouping, timestamps, rename→filename,
    empty for non-diarised) + UI regression (speakers 33/34, ui) green.

NOTE — **`server/` (web version) is OUT OF SCOPE for the PySide port.** The old repo's
`server/` (FastAPI: api/auth/database/migrations/web) is a separate web deployment,
present unchanged in the current repo too — it is NOT being reimplemented in the desktop
client. The "monitoring/telemetry/profiler/A-B/flamegraph/IPC-metrics/debug-console"
suite (device indicator #9, hardware/python monitor, IPC metrics, traces, watched vars,
snapshots, flamegraph, A/B testing, regression testing, coverage) is the big remaining
UI chunk = **TODO #10**.

## 7c. Carried-over backend problems (rethink, don't just re-run)

The port reused the verified backend, but a few files came over UNTOUCHED with the old
dev's latent problems. Audit + fixes:

- **[DONE] AI provider/model selection (`backend/ai_client.py`) reworked.** Old file
  hardcoded a model per provider (no choice), defaulted Google to the RETIRED `gemini-pro`
  (would fail) and xAI to `grok-beta`, IGNORED the `advancedSettings` block the settings UI
  stores (Advanced-API modal was a stub), ignored `--endpoint` for cloud providers, and its
  local 400-retry OVERWROTE the user's prompt with hardcoded Russian text. Now: `--model`
  selects the model (per-provider current defaults; `""` = default); openai/xai/mistral/
  deepseek share one OpenAI-compatible path honouring `--endpoint` as a base-URL override;
  `--advanced` (JSON endpoint/model/headers/body with `{{apiKey}}/{{model}}/{{prompt}}/{{text}}`
  placeholders) makes the Advanced modal REAL; google uses the chosen model in the URL; the
  local retry keeps the user's prompt. Wired through `summarization.build_command` /
  `analysis.build_feature_command` / `pipeline` (reads `aiModel` + `advancedSettings[provider]`).
  Settings UI: editable per-provider model list (`MODELS_BY_PROVIDER`) + the model persists as
  `aiModel`. Verified: `_selftest_ai_provider.py` (22 — routing/model/advanced/endpoint override,
  monkeypatched requests) + `_selftest_settings` (aiModel load/custom/round-trip).
- **[DONE] Transcription `initial_prompt` hardcode removed.** `openai_whisper_engine.py`
  and `faster_whisper_engine.py` used to pass a hardcoded RUSSIAN IT-jargon `initial_prompt`
  ("Это техническая встреча IT компании… API, KCI, SQL, PostgreSQL, DDD, bounded context…")
  to EVERY transcription regardless of language/topic — biasing EN and non-IT meetings. Now
  it's the optional `transcriptionHint` setting (default EMPTY = neutral), threaded
  `settings → pipeline → transcription.build_command --initial-prompt → processor.py →
  _build_adapters` (bound only to the whisper-family adapters that accept it; vosk/sherpa/
  whispercpp/funasr/extra untouched). Settings gains a "Vocabulary/terms" field. Verified:
  `_selftest_dispatch` (19 — wiring incl. the hint), `_selftest_settings` (hint default-empty
  + round-trip), and a LIVE openai-whisper run (medium.pt) succeeded with an empty hint (no
  bias, no crash). Hardcode grep-confirmed removed.
- **[DONE] faster-whisper `medium` model fixed.** The HF snapshot on disk had broken
  76-byte text "symlink pointers" (`../../blobs/…`) instead of real files (Windows transfer
  artifact) → CTranslate2 read a garbage binary version (`v774843950`). Blobs were intact, so
  the fix was: delete the stale pointer files + re-run `snapshot_download` (recreates proper
  symlinks, no 1.5 GB re-download). faster-whisper now loads + transcribes live
  (`[00:00:04] Я. Тебя.`). Packaging (#12) must ship real files, not symlink pointers.
- **[DONE — LIVE-VERIFIED] whisperX + faster-whisper both work (the owner's long-standing
  pain point).** whisperX 3.1.1 was incompatible with the installed stack in FIVE places; the
  carried-over `whisperx_patch.py` was ineffective (patched `asr.load_model` but the engine
  calls the top-level `whisperx.load_model` re-export) and incomplete. Guided by
  `WHISPER_ENGINES_COMPATIBILITY.md`, the patch was reworked to: patch the top-level re-export;
  instantiate whisperX's OWN `WhisperModel` subclass (has `generate_segment_batched`); filter
  `TranscriptionOptions` to real fields (drop `multilingual`); load VAD on CPU; move mel
  features to CPU in `encode` (fixes "can't convert cuda:0 tensor to numpy"); and shim
  `use_auth_token`→`token` for hf_hub 1.x (transformers pins hf_hub>=1.5.0, so no downgrade).
  RESULT: faster-whisper AND whisperX-with-diarization both run on CUDA in the same env —
  `[00:00:04] [SPEAKER_00]: Я тебя люблю.` Full details in `WHISPER_ENGINES_COMPATIBILITY.md`
  (2026-07 section).
- **[DONE — LIVE-VERIFIED] Distribution-friendly diarization: offline sherpa-onnx (default) +
  optional pyannote.** pyannote models are HF-GATED (every end user would need an account +
  token + accepting terms) → unusable out-of-the-box when the app is distributed. So diarization
  now has a backend choice (setting `diarizationBackend`): **`sherpa`** (default) — offline
  `sherpa_onnx.OfflineSpeakerDiarization` on FREE ungated ONNX models (pyannote-segmentation-3.0
  ONNX + a 3D-Speaker embedding, from k2-fsa releases, into `resources/diarization_models/`), NO
  token; **`pyannote`** — the gated path for users who paste their own `hfToken`; **`off`**.
  Backend `processing/diarization.py` (download + `diarize()`); whisperX assigns the sherpa
  speaker segments to its transcript segments by time-overlap (`_assign_sherpa_speakers`) →
  same `[SPEAKER_NN]` format. Threaded settings→`transcription.build_command --diarization/--hf-token`
  →processor→whisperx adapter. Settings UI: backend dropdown + conditional HF-token field + a hint
  explaining the trade-off; `hfToken`/`diarizationBackend` persist. LIVE: offline sherpa diarization
  downloaded its models and produced `[SPEAKER_00]:` with NO token; standalone test separated 2
  speakers. Verified: `_selftest_dispatch` (19, whisperx wiring incl. backend+token), `_selftest_settings`
  (diar default + round-trip). Owner-approved: keep BOTH so users choose (offline for most, token for
  advanced). REMAINING polish: a Settings "download offline diarization models" button + docs page.

## 7d. Release-readiness audit (2026-07-02, pre-packaging)

Ran the `project-release-audit` skill before building. Result: **fit for use; only #12
packaging remains.**

- **Tests (Phase 1):** full suite **32/32 selftest sets ALL_PASS** on the embedded Python
  (`_selftest_contextual_memory` passes standalone — exit 0; a batch-loop "no summary" is
  just `os._exit` flushing under command-substitution, not a failure).
- **Static audit (Phase 2):** removed two pieces of dead code — `SettingsDialog._todo_button`
  (+ its orphaned `"todo"` label, leftover from when action buttons were disabled stubs) and
  the unused `JobRunner(analysis_prompt=…)` ctor arg (set from a non-existent `analysisPrompt`
  key, never read; dropped in `pipeline.py` + `main.py` + `_selftest_pipeline.py`). The lone
  `NotImplementedError` (`analysis_widget._Panel.render`) is a legit abstract-method idiom
  (subclasses override). Engine schema↔dispatch parity is asserted by `_selftest_dispatch`
  (`set(adapters) == implemented engines`). App imports clean.
  **RESOLVED in the 2026-07-21 audit** (these were previously only *flagged*, which is why
  they rotted):
  * bare `except:` in `backend/*` — all 13 rewritten to `except Exception:` (a bare except
    also swallows `KeyboardInterrupt`/`SystemExit`, so Ctrl-C could not interrupt a job);
  * the legacy exporters (`markdown_exporter*.py`, `multi_format_exporter*.py`,
    `html_to_pdf*.py`) and `whisperx_transcriber.py` — **deleted**. Verified unreferenced by
    any code (the only hits were their own CLI wrappers and the regenerable `graphify-out/`
    cache); the desktop/server use `desktop/app/backend/exporter.py` and
    `backend/processing/engines/whisperx_engine.py` instead. `whisperx_transcriber.py` did
    not even parse (`SyntaxError: invalid character '¿'`) — it could never have been imported;
  * `desktop/_extract_prompts.py` — already gone (this line was itself stale).

  **Follow-up (same day): a SYSTEMATIC sweep, not a targeted one.** The pass above only
  chased the files the earlier audit had named, so two more Electron-era leftovers survived.
  A sweep over ALL 62 project modules (references searched across `.py/.bat/.ps1/.json/.md`,
  excluding the regenerable `graphify-out/` cache) found exactly two with **zero references
  anywhere** — `backend/faster_whisper_transcriber.py` and `backend/vosk_transcriber.py`,
  present since the `initial state` commit and superseded by the registry + per-engine
  adapters in `backend/processing/engines/`. Both deleted; everything else in the tree is
  referenced. Lesson: audit by sweeping the whole surface, not by working a pre-existing list.
- **Docs (Phase 3/4):** added the port's own docs — `desktop/README.md` (what/run/features/
  quick-check/doc-map), `desktop/ARCHITECTURE.md` plus
  `desktop/architecture-c4-component.puml` and `desktop/architecture-sequence.puml`.
  ROADMAP remains the state/history source. The repo-root Electron-era docs + `server/`
  are explicitly out of scope for this artefact. (No agent-consumable usage skill needed — this
  is an end-user GUI, not a tool/library/API.)
- **Deps (Phase 5):** the port's real import set reconciled into `desktop/requirements.txt`
  (pinned to installed versions; required vs optional split; torch cu121-index note; ffmpeg +
  on-demand models noted as non-package assets). The old `backend/requirements.txt` was
  Electron-era — missing PySide6, sherpa-onnx, pywhispercpp, chromadb (+pydantic/tenacity/
  python-dotenv), psutil; and listed weasyprint/beautifulsoup4 which only the DEAD legacy
  exporters import. pyannote.audio + sentence-transformers correctly optional/lazy.
- **Cleanup + branch (Phase 6/7):** working tree shows only intended changes; stray
  `_dl*`/`_pip*`/`_check_sherpa` scratch files are gone; `graphify-out/` is gitignored.
  Branch `feat/engine-model-management` — `master` is a strict ancestor → trivial
  fast-forward when the owner merges.
- **Packaging (Phase 9 / #12):** DONE — min (238 MB) + full (12.2 GB) built into `dist/`
  and verified (see §7 item 12). "full" is single-variant (full_cpu/full_gpu share deps).
- **REMAINING:** owner's final commit, then the `server/` layer port.

## 8. Environment quirks (save budget — these cost time to rediscover)

- This automation shell's **PowerShell has a restricted PATH**: cannot run `git`
  or even `cmd` by name. Run git ONLY via the `cmd.exe` shell with explicit
  `set "PATH=C:\Program Files\Git\cmd;%PATH%"`. The cmd wrapper also mangles
  quoted args (commit via `git commit -F <file>`) and chokes piping to
  `find`/`findstr` ("Access denied") — redirect to a file and read it instead.
- Running the **embedded python**: pipe capture is unreliable. Use
  `Start-Process -FilePath <python> -ArgumentList <script> -RedirectStandardOutput
  <file> -RedirectStandardError <file>.err -PassThru -WindowStyle Hidden`, then
  `WaitForExit(ms)` and read the file. Passing `-c "code with spaces"` via
  Start-Process breaks on spaces — use a script file.
- **Qt signals**: job_id must be `Signal(object,...)` (ms-timestamp overflows
  32-bit int). Don't revert to `int`.
- Headless tests: `QT_QPA_PLATFORM=offscreen`. The "Cannot find font directory"
  stderr warning is benign.
- **Git is owned by Sergey** — he commits himself (his PowerShell works). Don't
  burn budget fighting git here; just tell him what to commit. Workflow rule:
  surgical `edit_block` (str_replace) on existing files, never blind rewrite of
  his backend; commit through git.
- Scratch outputs `_*_out.txt` are gitignored; the `_selftest_*`/`_fake_*` files
  are real, kept smoke tests.
- **Legacy reference: `C:\Scripts\meeting-summarizer_old`** — the duplicated
  `backend/` runtime, Python environment, models and FFmpeg were intentionally removed on
  2026-07-25 after a hash/runtime audit. The remaining read-only reference still contains
  the Electron UI source, legacy Git state and unique transcripts/RAG data pending
  migration. Electron logic lives in `meeting-summarizer_old/electron/renderer.js`
  (i18n, advanced prompts, renderers and export generators); it is historical evidence,
  not a runtime dependency or the architectural source of truth.

## 9. Suggested commit for this batch
`feat(rag): real vector RAG + plain-text search (TODO #7)`

backend/embeddings.py (local/openai/sentence-transformers, lazy), backend/rag.py
(chromadb semantic store, project-scoped, add/search/list/stats/delete/clear),
backend/app/textsearch.py (literal/regex transcript grep). UI: RagDialog,
SearchDialog, RagWorker (QThread), Project field + Add-to-KB/KB/Search buttons.
HistoryEntry.project added. chromadb installed into backend/python (whisper
verified intact). _selftest_rag.py (23) + _selftest_textsearch.py (25) +
_selftest_rag_ui.py (16) ALL_PASS; all 13 prior selftests ALL_PASS (16 sets).

NOTE: backend/embeddings.py, backend/rag.py are under backend/ (not desktop/).
chromadb is a new dependency in backend/python — include it when packaging.

PRIOR batch commit (TODO #6): `feat(desktop): regenerate + version switching, vosk timestamps`

Summary/analysis version switchers (arrows + dropdown), Regenerate button
(edits transcript in txt_raw → raw file → new summary+analysis versions via
PipelineQueue.enqueue_regenerate / JobRunner.start_from_transcript).
AnalysisVersion.source_summary_version links analysis to the summary it derived
from (prevents silent drift; shown as "← from summary vN"). vosk_engine now
emits [HH:MM:SS] timestamps like the other engines. `_selftest_versions.py` (19)
+ `_selftest_versions_ui.py` (23) ALL_PASS; all 11 prior selftests ALL_PASS.

NOTE: vosk_engine.py is under backend/ (not desktop/) — commit separately or
together as the user prefers.

PRIOR batch commit (TODO #5): `feat(desktop): speaker management + transcript editing`

`app/backend/speakers.py` (real WhisperX `[HH:MM:SS] [SPEAKER_NN]: text` parser),
`app/ui/speakers_dialog.py` (per-speaker chronological editable utterances +
stats), pipeline gating (speakers_needed/resume_summary/skip_speakers),
main_window integration (auto modal after whisperx transcription; "👥 Спикеры"
button enabled only on diarised transcripts). Names + per-line edits applied
without reordering; timestamps preserved. `_selftest_speakers.py` (33) +
`_selftest_speakers_ui.py` (34) ALL_PASS. All 9 prior selftests ALL_PASS.

PRIOR batch commit (TODO #4): `feat(desktop): Advanced Analysis UI — 11-panel AnalysisWidget`

`app/ui/analysis_widget.py`: AnalysisWidget (QScrollArea, 11 collapsible _Panel
subclasses) rendering all 11 features of the analysis JSON — characteristics,
action items, sentiment (with bars + dominance chart), category (badge+tags),
risks, quotes, technologies (grouped), questions, recommendations, follow-up
questions, formal protocol (ГОСТ/ISO). QSS tokens added to theme.py. MainWindow
updated: loads analysis on job finish, re-renders on language switch.
`_selftest_analysis_ui.py` (27 checks, ALL_PASS). All 8 prior selftests ALL_PASS.

Rewrote `app/backend/obsidian.py` to the user's actual vault structure
(`Meetings/<stem>/<stem>_summary.md` + `<stem>_analysis.md`, rich emoji analysis
report, `_index`, 4 static `_queries`, People/Topics) — the previous
YYYY/MM-Month layout was wrong. Callers updated to pass the transcript (word
count): `app/core/pipeline.py`, `app/ui/main_window.py`. `_selftest_obsidian.py`
rewritten (18 checks). Headless-verified (obsidian + export UI + pipeline + base
UI all ALL_PASS). No backend (`backend/*`) files changed.
