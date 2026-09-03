# Meeting Summarizer 1.3.0

**English** · [Русский](https://github.com/john7ross/meeting-summarizer/blob/main/RELEASE_NOTES.ru.md)

Local-first tool that turns a meeting recording into a structured **summary** and a rich
**analysis**. Transcription → summary → 11-feature analysis → exports. Everything can run on
your own machine, offline; cloud AI providers are optional.

Second release. New: **live mode** — system-audio capture, live transcription and a live summary while the meeting is still happening. Everything from 1.2.1 is unchanged and carried over.

---

## What to download

| Build | Who it is for |
|---|---|
| **min** — `meeting-summarizer-min-v1.3.0.zip` | The normal choice. Unzip and run `INSTALL.bat` once — it installs what your particular machine needs. |
| **full** — `meeting-summarizer-full-v1.3.0.zip` | Machines with no or restricted internet. Bundles Python, every dependency and one model per engine. Unzip and run. |

**min** is attached to this release. **full** is too large for GitHub and is published
separately; its link is posted with the release.

**SHA-256 checksums are published together with the archives.** Verify what you downloaded
rather than trusting the file name:

```powershell
Get-FileHash .\meeting-summarizer-min-v1.3.0.zip -Algorithm SHA256
```

> **Coming from 1.2.1?** The file names carry the version, so nothing is overwritten. Settings,
> history and the meeting archive live outside the build and are picked up as they are; the new
> live-mode switches default to off, so an upgraded installation behaves exactly as before until
> you turn them on.

---

## How min differs from full, and which to take

**The two builds can do the same things.** This is not a "lite" and a "pro" edition: the code in
both is byte for byte identical — all 7 engines, all 11 analysis features, both front-ends, in
each. What differs is only what is already inside the archive.

Compare the file lists and it is simple: **min is strictly a subset of full plus `INSTALL.bat`**.
full additionally carries the embedded Python with every package, and the heavy models (the large
Vosk ru/en, sherpa-onnx, FunASR, the medium Whisper models). Everything else is the same —
including the small Vosk ru/en and the offline speaker-diarization models, which ship in **both**,
so min can transcribe offline out of the box.

Neither build carries the embedding model weights for the knowledge base; those are fetched the
first time you use it.

**Can min be turned into full?** Functionally, yes. Tick every engine in `INSTALL.bat` and the
installer offers 25 models — all 10 of full's model set are among them, none missing. So min can
be brought to complete feature parity.

**Structurally, no — and the difference is a practical one.** The installer puts packages into
**your** Python (the one recorded in `config/interpreter.txt`), not into `backend/python`. Hence:

| | min | full |
|---|---|---|
| Where dependencies land | in your Python, on this machine | in an isolated `backend/python` inside the folder |
| Portability | tied to a specific Python installation on a specific machine | copy the folder to a USB stick and run it on a machine with no internet and no Python |
| Internet needed to install | yes (packages and models are downloaded) | no |
| Download size | 330 MiB plus whatever you add | 12.35 GiB up front |
| Adding things later | `INSTALL.bat` stays in the folder — run it again to add engines, models or the web cabinet | no `INSTALL.bat`; add models from the app itself (**Settings → models**) |

**Choosing.** An ordinary work machine with internet — take **min**: the download is 40× smaller
and you install exactly what your hardware needs (the installer decides whether a CUDA torch build
makes sense). No internet, an air-gapped environment, handing it around on a USB stick, or simply
not wanting Python installed system-wide — take **full**.

---

## Requirements

Windows. An NVIDIA GPU (CUDA 12.4 runtime) is recommended for fast transcription, but
everything also runs on the CPU — slower.

**min** needs Python 3.9 – 3.12 (3.11 recommended). You do not have to install it first: if the
machine has no Python, `INSTALL.bat` offers to install 3.11 itself — from python.org, only
after verifying the Python Software Foundation signature, and for the current user only. The
Microsoft Store build will not do: it is 3.13 or newer, and the pinned `numpy<2.0` publishes no
wheels for those.

**full** needs none of this — it carries its own Python 3.11.

Disk: after installation min occupies roughly 2 GB (CPU) to 6 GB (CUDA torch build plus a
model). The installer prints an exact estimate before it does anything.

---

## Installing

**min:** unzip → run `INSTALL.bat` → pick engines, models and components → wait. Then `RUN.bat`
(desktop) or `SERVER.bat` (web cabinet at `http://localhost:8000`).

The web cabinet is its own entry in the component list; leave it unticked if you do not need
it. Unattended: `INSTALL.bat --recommended --yes`. Print the plan and install nothing:
`INSTALL.bat --plan-only`.

**full:** unzip → `RUN.bat` or `SERVER.bat`.

**From a git clone:** the repository is source only — the embedded runtime (6.4 GB) and FFmpeg
(385 MB) cannot live in git, since GitHub rejects any file over 100 MB and each FFmpeg binary is
193 MB. `INSTALL.bat` fills in everything that is missing, FFmpeg and Python included:

```
git clone https://github.com/john7ross/meeting-summarizer.git
cd meeting-summarizer
INSTALL.bat
RUN.bat          :: or SERVER.bat for the web cabinet
```

`RUN.bat` and `SERVER.bat` are the same files in all three cases — they resolve the interpreter
at run time (embedded runtime → the one `INSTALL.bat` recorded → PATH).

---

## What is in it

- **Two front-ends over one backend** — a PySide6 desktop client and a multi-user FastAPI web
  cabinet (JWT, SQLite). The same feature set in both.
- **7 transcription engines** — OpenAI Whisper, Faster-Whisper, WhisperX, Vosk, sherpa-onnx,
  whisper.cpp, FunASR; plus separately installed community models (GigaAM RU, Moonshine EN).
- **Speaker diarization** — offline via sherpa-onnx with no token, optionally pyannote.
- **11-feature analysis** — action items, sentiment, category, key topics, risks, quotes,
  technologies, questions, recommendations, follow-ups, formal protocol.
- **Four ways to attach an AI** — a local OpenAI-compatible endpoint, a cloud key
  (OpenAI / Anthropic / Google / xAI / Qwen / Mistral / DeepSeek), a local agent CLI
  (Claude Code, Codex, Hermes), or the built-in AI fetched on demand.
- **GPU hand-off** — on a single card a resident LLM is stopped for the duration of the
  transcription and brought back afterwards.
- **Exports** — txt, md, json, html, pdf, docx, an Obsidian vault, Google Sheets.
- **Knowledge base and search** — semantic search across the meeting archive (chromadb) plus
  plain-text search.
- **MCP server** — the meeting archive exposed to an AI agent as tools.
- **Intake** — a file, a URL (YouTube included), or a recording made in the app (microphone
  **and system audio**); a long recording can be cut into separate meetings on a waveform.
- **Live mode** — live transcription and a rolling live summary while the meeting is still
  running, offline on the engines already installed and through the AI provider already
  configured.
- **Processing history** — a journal of every run (what ran, when, how long, the status
  transitions, the stage timings, what it produced, the full error), kept separately from the
  queue in `config/processing_history.json`, so clearing the queue never erases it.
- **Scoped regeneration** — rebuild the summary only, the analysis only, or both, from the
  transcript you can edit on screen.

Details in [README.md](https://github.com/john7ross/meeting-summarizer/blob/main/README.md) and [desktop/ARCHITECTURE.md](https://github.com/john7ross/meeting-summarizer/blob/main/desktop/ARCHITECTURE.md).

---

## New in 1.3.0 — live mode

Until now the app only worked on a file that already existed: you recorded a meeting, stopped,
and then found out what was in it. 1.3.0 adds the part that happens **while the meeting runs**,
and the part that was missing from the recording itself.

- **System audio is recorded too.** The microphone captures you; WASAPI loopback captures what
  the speakers play — everyone else on the call. Both go into one WAV as its two channels (mic
  left, system right), so the archive holds the whole conversation instead of your half of it.
  Optional (the `soundcard` package); if it is unavailable the recorder stays mic-only and says
  which of the two reasons applies, rather than quietly recording half a meeting.
- **Live transcription.** The same PCM that is written to the WAV is streamed to a recognition
  engine kept open for the whole meeting, and recognised phrases appear as they are spoken,
  labelled MIC or SYSTEM from the channel that was louder — speaker attribution for free, out of
  the two capture sources. It runs **offline** on the engines already installed: faster-whisper,
  whisperx, whisper, vosk, sherpa-onnx, whisper.cpp. Live can use a different engine and model
  than the batch pass, so `medium` can stay on the GPU for the archived transcript while live
  runs on a small CPU model.
- **Live summary.** A rolling summary — topics, decisions, action items, open questions —
  refreshed roughly every 30 seconds through the **same** AI provider the post-meeting summary
  uses: local endpoint, cloud key, agent CLI or the built-in model. Nothing about "which AI" is
  configured twice.
- **The summary does not drift, and does not vanish.** An incremental update builds on its own
  previous answer, so a mistake made at minute 3 is quoted back to the model for the rest of the
  meeting. On a local model, where tokens cost nothing but time, every update is therefore
  rebuilt from the transcript instead; on a metered cloud, incremental updates alternate with
  periodic rebuilds and consolidation, and an update cap bounds the spend. A failed request, a
  killed process or an answer that will not parse leaves the summary already on screen exactly
  where it is and puts the complaint on a separate status line.
- **Live results are on disk as they are produced** — `*_live_transcript.txt` and
  `*_live_summary.json` next to the recording. Closing the window does not lose them.

- **Processing a live meeting does not transcribe it twice.** On stop the recorder offers
  *Process from live text* beside the usual *Process*: the live transcript becomes the meeting's
  transcript and the run goes straight to summary and analysis. **The result is indistinguishable
  from any other meeting** — one summary version, one analysis version, the same file names, the
  same exports, Obsidian note and Sheets row. Only the transcription stage is missing, because it
  genuinely did not run in that job. A different way in must not produce a different-looking
  result, and the suite now checks that artifact-for-artifact against a batch-processed meeting.
- **`live` is a first-class intake channel**, alongside a dropped file and a URL, and everything
  else keeps working with it. The channel is stored on the meeting, badged in the queue row,
  named in the results panel, recorded in the processing journal, written to the log when the job
  starts, and the Diagnostics profile renders the stages that really ran. Meetings from before
  this release read as `file`, never as "unknown".

**Live is a draft and cannot degrade the result.** The recording still goes through the normal
pipeline afterwards — trim → queue → transcription → summary → analysis — and the final
transcript is produced from the file, by the configured engine, from scratch. The tap is one-way:
PCM reaches the WAV first and is only then offered to the live worker, so live lagging, failing
or being switched off never costs the recording a sample.

**Also in this release**

- The version is now shown in the desktop client (Diagnostics → System). It was visible in the
  web cabinet's footer and in exported reports, but a desktop user reporting a problem had no way
  to say which build they were on.
- A self-test now compares every version claimed in the documentation — both README badges, both
  desktop READMEs, the project overview — against `package.json`. Nothing checked them before, so
  a bump could ship an archive named v1.3.0 whose README said 1.2.1.

---

## Fixed in this build

### Added in the 2026-08-19 rebuild

- **A valid analysis answer could be rejected as malformed JSON.** Before parsing, the response's
  `{}` and `[]` were counted with `str.count` — which also counts the ones **inside string
  values**. A single unmatched bracket in a question's own text ("clause [3", "the { report
  format") made a perfectly good answer look unbalanced, so `json.loads` was never attempted and
  the run was reported as "AI returned invalid JSON/schema". Reproduced deterministically; the
  parser now walks the response and tracks string literals and escapes, so intact JSON is parsed
  as-is and the repair pass only ever sees what genuinely does not parse.
- **A truncated answer lost everything it had already produced.** The repair pass closed
  containers in the wrong order (every `]` before every `}` — which cannot close an array of
  objects) and cut the tail at the last comma, which in Russian prose is almost always inside a
  sentence. Measured on the fixture: two complete questions present, zero recovered. Recovery now
  keeps every element that finished and drops only the one being written.
- **Running out of output tokens was reported as the model's bad formatting.** No provider's
  `finish_reason` / `stop_reason` was ever read, so an answer cut off at the ceiling travelled on
  as if complete. All four provider families are now checked, and the failure says the answer was
  cut off by the token limit instead of blaming the JSON.
- **One flaky feature no longer costs the whole meeting.** A model is sampled, not deterministic:
  the same prompt that returns a clean array on one pass returns something unusable on the next.
  That ended the run at 94% — "analysis incomplete: 1 of 11 failed" — and took the Obsidian note
  and the Google Sheets row with it, although the other ten features had succeeded. Each feature
  now gets one second attempt. Provider errors and timeouts are **not** retried here: they have
  already spent `ai_client`'s own retry budget. Desktop and web cabinet both.
- **An unparsable answer is kept for diagnosis.** Only its first 300 characters ever reached the
  user and nothing kept the rest, so after the run it was impossible to tell a truncated answer
  from a fenced one from plain prose. The raw response is now written next to the artifacts as
  `<meeting>_failed_<feature>.txt`.
- **The Obsidian note took today's date from a dotted file name.** `Path(name).stem` was applied
  to a name that was already a stem, so `Планёрка 17.08.2026 15-33` lost `.2026 15-33` and the
  date with it. Only names carrying a dotted date were affected; `2026-08-18 15-00-40.mkv` was
  always read correctly.

### In the original release

- **The formal protocol invented its own date.** Measured across 14 real analyses: ten were
  dated "24.10.2023", one said "current date (based on the transcript)", and the protocol
  number had a different shape every time — while the meeting's real date and start time sat
  in the file name (`2026-08-17 15-33-43.mkv`). One parser now reads that name (year-first,
  day-first, compact and `T`-separated forms, with range validation), and the date, the
  start–end interval (start + the recorded duration) and the protocol number are both stated
  to the model and written over its answer. A file whose name carries no date is untouched:
  the model still decides. Desktop and web cabinet share it.
- **A long failure message forced a horizontal scrollbar.** The status label had no word wrap,
  so a three-line provider error demanded 3783 px and the whole window scrolled sideways. It
  now wraps and the panel grows in height; the queue's Details cell elides with the full text
  in its tooltip, and the error is escaped before it reaches the rich-text timeline (provider
  messages quote the model's own `<think>` output, which was being eaten as markup).
- **A cancelled run left its extracted audio on disk.** Measured on one cancelled meeting:
  328 MB (a 171 MB WAV plus nine chunks). The backend deleted only the full WAV on its error
  path, and a cancelled run is killed outright so it reached no cleanup at all. Both ends now
  sweep the job's own `*_temp*.wav`, immediately and once again shortly after.
- **Pressing Cancel was logged as an error**, so a deliberate action looked like a defect in
  the Diagnostics log. It is now an INFO line; real failures still log as errors.
- **The optional exports were invisible when they worked.** Only failures were logged, so "my
  Google Sheets row is missing" could not be told apart from "the export never ran". Both the
  Sheets append and the Obsidian notes now log their success too.
- **The full build shipped a dead knowledge base.** The packager's directory-skip list describes
  *this project's* layout — `config`, `logs`, `uploads`, `dist` — and was also being applied to
  the bundled Python runtime. A name collision on `logs` amputated
  `opentelemetry/proto/collector/logs`, which the OTLP exporter imports unconditionally, so every
  full build died on `import chromadb`. Semantic search and the knowledge base did not work at
  all; everything else was unaffected. The skip list now applies only to our own tree, and a
  self-test asserts the module ships. **Only `full` was affected** — `min` installs its packages
  through pip and never had the problem.
- **An analysis feature could fail on a perfectly good answer.** The response parser trimmed
  prose *after* the JSON but not *before* it, and a model routinely introduces its answer
  ("Here is the JSON array with the extracted tasks:") before opening a fence. The data was
  intact and was thrown away, the feature came back empty, and the run was reported as
  "AI returned invalid JSON/schema" — pointing at the model rather than at the parser.
  Measured against a local Qwen on one real meeting: 2 of 8 runs of the same feature, nine
  correct action items discarded each time; 0 of 10 after the fix. Both front-ends share the
  parser, so both were affected.
- **A silent recording now says so.** A file with no audible speech used to finish "successfully"
  with an empty transcript, and the confusion surfaced much later as "No text provided". The
  backend now checks the transcript against the audio's peak level and reports `SILENT_AUDIO` or
  `NO_SPEECH`, localised in both front-ends.
- **min installs no longer return bare 500s.** The web cabinet used a hardcoded path to the
  embedded interpreter; it now resolves the interpreter the way the launchers do.
- **`INSTALL.bat` installs FFmpeg** when it is missing — a verified direct download, no package
  manager. A second run no longer re-offers a Python install it has already made.
- **A fresh clone is runnable**: `git clone` → `INSTALL.bat` → `RUN.bat`, with no manual venv work.
- **Intranet addresses are accepted.** The web cabinet no longer rejects `.local` / `.internal`
  e-mail addresses at registration.

---

## What was verified before release

- **53 self-test runners, 1786 checks** — green, run twice in sequence.

**Verified for 1.3.0:** the whole suite (53 runners, 1786 checks, zero failures). Two new
runners cover the live path end to end rather than through mocks — `_selftest_live.py`
(87 checks: utterance segmentation from synthetic PCM, the summary state schema, tolerant
parsing, and the rule that a bad answer never replaces a good summary, driven through the real
`AIClient`) and `_selftest_live_ui.py` (100 checks: the PCM tap, the streaming worker over a real
process pipe with a real offline engine, the update strategies, the spend cap, and the recorder
dialog's wiring). WASAPI loopback was verified by capturing a tone the test itself played through
the default output — not by asserting that the API returned without error. Live transcription was
run against real Russian speech through the whole chain and produced correct text: sherpa-onnx at
~0.1 s per utterance on the CPU, faster-whisper `medium` at ~0.6 s on the GPU, both with the
vocabulary hint honoured. **Not re-run for this release:** the clean-machine VM install and the
per-provider matrix — nothing in these changes touches the installer or the providers, and the
live summary reaches every provider through the same `ai_client.py` those tests already cover.

**Re-verified for the 2026-08-19 rebuild:** the whole suite (51 runners, 1581 checks, zero
failures); two new runners covering exactly what broke — `_selftest_json_recovery.py` (11 checks:
valid JSON is never touched by the repair pass, a truncated answer keeps every completed element)
and `_selftest_analysis_retry.py` (7 checks, end to end through the real pipeline: one unusable
answer is retried and the run finishes green, two in a row still fail it). The recovery runner was
also run against the *previous* parser and fails 5 of its 11 checks there, so it pins the actual
defect rather than the new implementation. The truncation warning was verified against a live
local llama.cpp server with the output ceiling forced to 12 tokens.
**Not re-run for this rebuild:** the clean-machine VM install, the per-engine and per-provider
matrix, and the knowledge-base end-to-end run — they were verified on an earlier build of this
same tree, and nothing in these changes touches the engines, the providers or the RAG layer.

**Re-verified for the 2026-08-17 rebuild:** the suite twice; documentation, the C4 component
diagram and the sequence diagram updated and re-rendered; both archives rebuilt from one frozen
tree and inspected file by file; the `min` archive extracted to a clean directory and its
`INSTALL.bat` executed from that copy (machine scan, Russian output, correct actionable verdict).
- **A clean install on a Windows 11 snapshot with no Python** — from unzipping the archive to a
  working cabinet: installing Python, installing the stack, registration, a real transcription,
  11/11 analysis features, all 12 exports.
- **Every transcription engine** run on a real file; **every cloud provider** with real keys;
  the GPU hand-off against a live local model.
- **The knowledge base end to end** on the runtime as shipped — documents indexed, a semantic
  query returning the right one.
- Both archives were built from one tree, and every shipped file was compared against it
  byte for byte: the full build's runtime matches the tested one at 40 335 files to 40 335.

An honest caveat: **full was not tested on a clean machine** — 12.3 GB could not be transferred
into the test VM. What was verified is the archive's composition and that it was built from the
same tree as the min build, which did go through the full clean-machine run.

---

## Licences

The project is MIT. Both builds ship an **FFmpeg build under GPLv3**, and **full** additionally
bundles Qt under the **LGPL**; their texts travel inside the archives in `licenses/`. The full
list of what is bundled and under which terms is in
[THIRD-PARTY-NOTICES.md](https://github.com/john7ross/meeting-summarizer/blob/main/THIRD-PARTY-NOTICES.md).

Found a vulnerability? [SECURITY.md](https://github.com/john7ross/meeting-summarizer/blob/main/SECURITY.md) — please not in a public issue.

---

## Support the author

Details at the end of [README.md](https://github.com/john7ross/meeting-summarizer/blob/main/README.md).
