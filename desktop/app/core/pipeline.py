"""Per-job pipeline orchestration and the pipeline-level scheduler.

JobRunner drives one file through the full lifecycle, updating the single
authoritative status for its id at each step:

    EXTRACTING -> TRANSCRIBING -> SUMMARIZING -> ANALYZING -> DONE   (or ERROR)

Transcription streams progress (processor.py); summary and analysis are
single-shot text passes (ai_client.py). Artifacts are written under the job's
own folder using the input file name with v2/v3 suffixes, and every version is
recorded in the HistoryStore by id.

PipelineQueue runs up to ``max_concurrency`` JobRunners at once; because each
runner owns one id, parallel jobs never cross-contaminate status.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from ..backend import analysis as A
from ..backend import speakers as SPK
from ..backend import summarization as S
from ..backend import transcription as T
from . import run_history
from .history import HistoryStore, versioned_filename
from .models import (SOURCE_LIVE, JobStatus, normalize_source, stage_to_status)
from .worker import AiWorker, FnWorker, TranscriptionWorker

logger = logging.getLogger(__name__)

# Localized progress-detail strings (the coarse JobStatus label is separate, in
# models.py). Keeps the user informed at every step, in their UI language.
_DETAIL = {
    "ru": {
        "unload": "Выгрузка локальной модели (освобождаю VRAM под транскрибацию)…",
        "restore": "Ожидание перезапуска локальной модели…",
        "summary": "Создание саммари…",
        "analysis": "Анализ: {name}",
        "done": "Готово за {total}",
        "breakdown_sep": "   ·   ",
        "st_unload": "Выгрузка локальной LLM",
        "st_unload_idle": "локальная модель не запущена, выгружать нечего",
        "st_reload": "Загрузка локальной LLM",
        "st_chunk": "Транскрибация фрагмента {i}/{n}",
        "failed": "не удалось",
        "retry": "повтор запроса",
        "an_truncated": "Ответ модели обрезан лимитом токенов (увеличьте лимит "
                        "вывода или сократите вход).",
        "an_all_failed": "Анализ не выполнен: AI-провайдер вернул ошибку на всех "
                         "фичах (проверьте агент/эндпоинт и лимиты).",
        "an_some_failed": "Анализ выполнен не полностью: ошибок {failed} из {total}. "
                          "Проверьте журнал и повторите генерацию анализа.",
        "an_no_features": "Нечего анализировать: в настройках не включён ни один "
                          "раздел анализа.",
        "an_no_summary": "Нет саммари, из которого строить анализ — сначала "
                         "перегенерируйте саммари.",
        "transcript_save_failed": "Не удалось сохранить исправленную транскрипцию: {error}",
        "tx_silent": "Речь не распознана: в файле нет звука — аудиодорожка есть, "
                     "но она пустая (пик {peak} dBFS). Проверьте, что при записи "
                     "был выбран нужный источник звука.",
        "tx_no_speech": "Речь не распознана. Проверьте, что язык транскрибации "
                        "совпадает с языком записи и что в файле действительно "
                        "есть речь.",
    },
    "en": {
        "unload": "Stopping local model (freeing VRAM for transcription)…",
        "restore": "Waiting for the local model to restart…",
        "summary": "Generating summary…",
        "analysis": "Analysis: {name}",
        "done": "Done in {total}",
        "breakdown_sep": "   ·   ",
        "st_unload": "Unloading local LLM",
        "st_unload_idle": "no local model was running, nothing to unload",
        "st_reload": "Loading local LLM",
        "st_chunk": "Transcribing chunk {i}/{n}",
        "failed": "failed",
        "retry": "retrying",
        "an_truncated": "The model's answer was cut off by the output-token limit "
                        "(raise the output limit or shorten the input).",
        "an_all_failed": "Analysis not produced: the AI provider errored on every "
                         "feature (check the agent/endpoint and its quota).",
        "an_some_failed": "Analysis is incomplete: {failed} of {total} features failed. "
                          "Check the log and regenerate the analysis.",
        "an_no_features": "Nothing to analyse: no analysis section is enabled in "
                          "the settings.",
        "an_no_summary": "There is no summary to build the analysis from — "
                         "regenerate the summary first.",
        "transcript_save_failed": "Could not save the corrected transcript: {error}",
        "tx_silent": "No speech recognised: the file has no sound — there is an "
                     "audio track, but it is empty (peak {peak} dBFS). Check that "
                     "the right input was selected when recording.",
        "tx_no_speech": "No speech recognised. Check that the transcription "
                        "language matches the recording and that the file really "
                        "does contain speech.",
    },
}
# Human names for analysis features, per language, for the live status line.
_FEATURE_NAME = {
    "ru": {"actionItems": "задачи и действия", "sentiment": "тональность",
           "category": "категория", "keyTopics": "ключевые темы", "risks": "риски",
           "quotes": "цитаты", "technologies": "технологии", "questions": "открытые вопросы",
           "recommendations": "рекомендации", "followupQuestions": "вопросы к след. встрече",
           "formalProtocol": "формальный протокол"},
    "en": {"actionItems": "action items", "sentiment": "sentiment",
           "category": "category", "keyTopics": "key topics", "risks": "risks",
           "quotes": "quotes", "technologies": "technologies", "questions": "open questions",
           "recommendations": "recommendations", "followupQuestions": "follow-up questions",
           "formalProtocol": "formal protocol"},
}
# Short label per stage for the final time breakdown.
_STAGE_NAME = {
    "ru": {JobStatus.EXTRACTING: "Извлечение аудио", JobStatus.TRANSCRIBING: "Транскрибация",
           JobStatus.SUMMARIZING: "Создание саммари", JobStatus.ANALYZING: "Расширенный анализ"},
    "en": {JobStatus.EXTRACTING: "Audio extraction", JobStatus.TRANSCRIBING: "Transcription",
           JobStatus.SUMMARIZING: "Summary", JobStatus.ANALYZING: "Analysis"},
}


def fmt_duration(seconds: float) -> str:
    """'5м 12с' / '5m 12s'-ish compact duration."""
    seconds = int(round(seconds))
    m, s = divmod(seconds, 60)
    return f"{m}м {s}с" if m else f"{s}с"


# Progress weighting. Transcription is the FAST part on a GPU; summary and
# analysis dominate wall-clock on a local model, so they get most of the bar.
PCT_TRANSCRIBE_END = 40    # transcription spans 0..40%
PCT_SUMMARY_START = 45     # summary sits at 45%
PCT_ANALYSIS_START = 50    # analysis spans 50..99%
PCT_ANALYSIS_END = 99

# ai_client.py prints this phrase to stderr when the provider reported that it
# stopped at the output-token ceiling. The answer then ends mid-sentence, so the
# JSON never closes — a token-budget problem wearing a JSON error's clothes.
_TRUNCATION_MARK = "CUT OFF mid-sentence"


class JobRunner(QObject):
    status_changed = Signal(object, object)   # job_id, JobStatus
    progress = Signal(object, int, str)        # job_id, percent, detail
    stage_done = Signal(object, str, float)    # job_id, stage label, seconds
    completed = Signal(object, bool, str)      # job_id, ok, error
    # Emitted when WhisperX diarisation markers found; pipeline pauses until
    # resume_summary() or skip_speakers() is called.
    speakers_needed = Signal(object, str)      # job_id, transcript_text

    def __init__(self, entry_id: int, video_path: str, settings: dict,
                 store: HistoryStore, *,
                 participants=None, python_exe=None, processor_script=None,
                 ai_client_script=None, run_store=None, parent=None):
        super().__init__(parent)
        self.entry_id = int(entry_id)
        self.video_path = str(video_path)
        self.stem = Path(self.video_path).stem
        self.settings = settings
        self.store = store
        # Optional processing journal (config/processing_history.json). Optional on
        # purpose: a runner without one behaves exactly as before, so tests and
        # embedders are unaffected.
        self.run_store = run_store
        self._run_record: Optional[dict] = None
        self.participants = participants
        self._python_exe = python_exe
        self._processor_script = processor_script
        self._ai_client_script = ai_client_script

        self._waiting_for_speakers = False
        self._cancelled = False
        self._gpu_held = False
        self._lang = str(settings.get("language", "ru"))
        if self._lang not in _DETAIL:
            self._lang = "ru"
        self._stage_t0: Optional[float] = None      # monotonic start of current stage
        self._timings: list[tuple] = []             # [(JobStatus, seconds), ...]
        # Flame-graph spans for the Diagnostics profile: every stage with its REAL
        # [start,end] (monotonic, relative to job start) so nesting is derived by
        # time-containment — job ⊃ stages ⊃ chunks/features. Covers the WHOLE job,
        # not just the transcription subprocess.
        self._job_t0: Optional[float] = None
        self._spans: list[dict] = []
        self._chunk_accum: float = 0.0              # chunk offset within transcription
        self._feat_t0: Optional[float] = None       # start of the current analysis feature
        self._provider = settings.get("aiProvider", "local")
        self._api_key = settings.get("apiKey", "")
        self._endpoint = (settings.get("localEndpoint") or "").strip()
        if not self._endpoint and self._provider == "local":
            # Clearing the endpoint field silently fell back to LM Studio's port
            # 1234 inside ai_client, so the run died on a port the user never
            # configured. llamaPort is the one place the port is stated.
            self._endpoint = (
                f"http://127.0.0.1:{int(settings.get('llamaPort', 8080) or 8080)}/v1")
        self._model = settings.get("aiModel", "")
        self._advanced = (settings.get("advancedSettings") or {}).get(self._provider)
        self._status: Optional[JobStatus] = None
        self._transcript_path: Optional[str] = None
        self._summary_path: Optional[str] = None
        self._summary_version: int = 0
        self._tw: Optional[TranscriptionWorker] = None
        self._aw: Optional[AiWorker] = None
        self._analysis_results: dict = {}
        self._analysis_features: deque = deque()
        self._current_feature: Optional[str] = None
        self._total_features = 0
        self._done_features = 0
        # What this run is allowed to produce: 'full' (transcribe → summary →
        # analysis), 'summary' (summary only) or 'analysis' (analysis only, from
        # the summary/transcript already on disk).
        self._scope = "full"
        # The journal follows the runner's own signals — one place to hook, so no
        # stage/status can be reported to the UI and missed by the log.
        self.status_changed.connect(self._journal_status)
        self.stage_done.connect(self._journal_stage)

    # -- processing journal --------------------------------------------
    def _journal_open(self, kind: str) -> None:
        if self.run_store is None or self._run_record is not None:
            return
        entry = self.store.get(self.entry_id)
        try:
            self._run_record = self.run_store.new_run(
                entry_id=self.entry_id,
                video_name=(getattr(entry, "video_name", "") or Path(self.video_path).name),
                video_path=self.video_path, kind=kind,
                provider=str(self._provider or ""), model=str(self._model or ""),
                engine=str(self.settings.get("transcriptionEngine", "") or ""),
                source=normalize_source(getattr(entry, "source", "")))
        except Exception:  # noqa: BLE001 - the journal must never break a run
            logger.exception("Could not open the processing journal for job %s",
                             self.entry_id)
            self._run_record = None

    def _journal_flush(self) -> None:
        if self.run_store is None or self._run_record is None:
            return
        try:
            self.run_store.upsert(self._run_record)
        except Exception:  # noqa: BLE001
            logger.exception("Could not write the processing journal for job %s",
                             self.entry_id)

    def _journal_status(self, _job_id, status) -> None:
        if self._run_record is None:
            return
        run_history.add_event(
            self._run_record, status.value if isinstance(status, JobStatus) else str(status))
        self._journal_flush()      # flush per transition: a crash keeps the trail

    def _journal_stage(self, _job_id, label: str, seconds: float) -> None:
        if self._run_record is None:
            return
        run_history.add_stage(self._run_record, label, seconds)

    def _journal_close(self, status: str, error: str = "") -> None:
        if self._run_record is None:
            return
        elapsed = (time.monotonic() - self._job_t0) if self._job_t0 is not None \
            else sum(sec for _, sec in self._timings)
        run_history.finish(self._run_record, status, error=error, duration_sec=elapsed)
        self._journal_flush()

    # -- lifecycle -----------------------------------------------------
    def _record_span(self, name: str, start_mono: float, end_mono: float) -> None:
        """Append a flame-graph span with times relative to the job start."""
        if self._job_t0 is None:
            self._job_t0 = start_mono
        self._spans.append({"name": name,
                            "start": max(0.0, start_mono - self._job_t0),
                            "end": max(0.0, end_mono - self._job_t0)})

    def start(self) -> None:
        self._job_t0 = time.monotonic()
        self._scope = "full"
        self._journal_open("full")
        self._set_status(JobStatus.EXTRACTING)
        out_dir = self.store.job_dir(self.entry_id)
        self._tx_command = T.build_command(
            self.video_path, out_dir,
            language=self.settings.get("transcriptionLanguage", "ru"),
            model=self.settings.get("whisperModel", "medium"),
            engine=self.settings.get("transcriptionEngine", "faster-whisper"),
            device=self.settings.get("whisperDevice", "auto"),
            initial_prompt=self.settings.get("transcriptionHint", ""),
            diarization=self.settings.get("diarizationBackend", "sherpa"),
            hf_token=self.settings.get("hfToken", ""),
            python_exe=self._python_exe, processor_script=self._processor_script)
        # Optionally stop the local LLM first so transcription gets the full VRAM.
        # The hand-off shells out to PowerShell and sleeps, so it runs OFF the UI
        # thread; transcription launches from the worker's completion, on the main
        # thread. No hand-off → launch immediately.
        if self.settings.get("gpuHandoff", False):
            self.progress.emit(self.entry_id, 0, self._t("unload"))
            self._ha = FnWorker(self._do_acquire, parent=self)
            self._ha.done.connect(self._launch_transcription)
            self._ha.start()
        else:
            self._launch_transcription()

    def _do_acquire(self) -> None:
        """Runs in an FnWorker thread — stop the local LLM (bounded, best-effort).
        ``acquire`` now VERIFIES the port actually went free; we mark the model as
        held (and later restore it) only if it truly stopped, and show the truth in
        the timeline (✔ freed vs ✖ couldn't stop → VRAM not freed)."""
        t0 = time.monotonic()
        status, why = "stuck", ""
        try:
            gpu = self._gpu_handoff_module()
            port = int(self.settings.get("llamaPort", 8080) or 8080)
            status = gpu.acquire_status(port=port)
        except Exception as exc:  # noqa: BLE001
            # Swallowing this reported a bare "failed" with no way to find out why.
            status, why = "stuck", f": {exc}"
        freed = status == "freed"
        self._gpu_held = freed
        t1 = time.monotonic()
        if freed:
            self._record_span(self._t("st_unload"), t0, t1)
            self.stage_done.emit(self.entry_id, self._t("st_unload"), t1 - t0)
        elif status == "idle":
            # Nothing was running, so nothing had to be unloaded. Calling that a
            # failure made a normal cloud-provider run look broken every time.
            self.stage_done.emit(
                self.entry_id,
                f"{self._t('st_unload')} — {self._t('st_unload_idle')}", t1 - t0)
        else:
            # Something IS holding the port and survived the kill: VRAM was not
            # freed. This is the only real failure - show it, don't fake a ✔.
            self.stage_done.emit(
                self.entry_id,
                f"✖ {self._t('st_unload')} — {self._t('failed')}{why}", t1 - t0)

    def _launch_transcription(self) -> None:
        if self._cancelled:
            return
        self._tw = TranscriptionWorker(self.entry_id, self._tx_command, parent=self)
        self._tw.progress.connect(self._on_tx_progress)
        self._tw.done.connect(self._on_tx_done)
        self._tw.start()

    def start_from_transcript(self, transcript_path: str,
                              scope: str = "both") -> None:
        """Regenerate from an existing transcript; transcription is skipped.

        Used by the "Regenerate" action, which lets the user choose WHAT to redo —
        a run that died on one analysis feature should not have to redo the summary
        as well (and used to have no way not to):

        * ``'summary'``  — a new summary version only;
        * ``'analysis'`` — a new analysis version only, from the summary/transcript
          already on disk;
        * ``'both'``     — summary followed by analysis (the original behaviour).

        Everything attaches to the same id as NEW versions. The speakers gate is
        not re-applied — the transcript is already final (possibly user-edited).
        """
        self._scope = "analysis" if scope == "analysis" else (
            "summary" if scope == "summary" else "full")
        self._job_t0 = time.monotonic()
        entry = self.store.get(self.entry_id)
        channel = normalize_source(getattr(entry, "source", ""))
        # One line that says which of the two things this is: a user re-running a
        # meeting they already processed, or a meeting that arrived through the
        # live channel and never needed transcription in the first place.
        logger.info("job %s starts from an existing transcript (scope=%s, source=%s)",
                    self.entry_id, self._scope, channel)
        # In the journal a regeneration is never "full processing": transcription
        # did not run, and the history window must not imply that it did.
        self._journal_open(
            "summary+analysis" if self._scope == "full" else self._scope)
        self._transcript_path = str(transcript_path)
        try:
            self.store.set_transcript(self.entry_id, self._transcript_path)
        except KeyError:
            pass
        if self._scope == "analysis":
            self._start_analysis_only()
            return
        self._start_summary()

    def _start_analysis_only(self) -> None:
        """Redo just the analysis, reusing the newest summary already recorded.

        The summary is not regenerated, so the new analysis version is linked to
        the summary version it was really built from (or to none, when the analysis
        is transcript-sourced), keeping the recorded link truthful.
        """
        entry = self.store.get(self.entry_id)
        versions = list(getattr(entry, "summary_versions", []) or []) if entry else []
        if versions:
            self._summary_path = versions[-1].path
            self._summary_version = int(getattr(versions[-1], "version", len(versions)))
        from_summary = self.settings.get("analysisSource", "transcript") != "transcript"
        if from_summary and not versions:
            # Asking for a summary-sourced analysis with no summary on disk can
            # only produce a transcript-sourced one behind the user's back.
            self._fail(self._t("an_no_summary"))
            return
        self._analysis_features = deque(A.enabled_features(self.settings))
        if not self._analysis_features:
            # "Done" with nothing produced is the one outcome the user cannot act
            # on: say what is switched off instead.
            self._fail(self._t("an_no_features"))
            return
        self._start_analysis()

    def _on_tx_progress(self, jid: int, event) -> None:
        mapped = stage_to_status(event.stage)
        if mapped is not None:
            self._set_status(mapped)
        # Turn each completed chunk into its own timeline entry with its time. The
        # backend already reports "Chunk i/N done in Xs" — parse it so the finished
        # chunk stays visible in the per-file stage list (not just the live detail).
        m = re.search(r"[Cc]hunk (\d+)/(\d+) done in ([\d.]+)\s*s",
                      event.details or "")
        if m:
            dur = float(m.group(3))
            label = self._t("st_chunk", i=m.group(1), n=m.group(2))
            # Lay the chunk inside the transcription stage (sequential), so it nests
            # under the transcription bar in the flame graph.
            base = self._stage_t0 if self._stage_t0 is not None else self._job_t0 or 0.0
            self._record_span(label, base + self._chunk_accum,
                              base + self._chunk_accum + dur)
            self._chunk_accum += dur
            self.stage_done.emit(jid, label, dur)
        # Scale the backend's 0..100 transcription progress into its share of the
        # overall bar — transcription is not 85% of the work.
        pct = int(max(0, min(100, event.progress)) * PCT_TRANSCRIBE_END / 100)
        self.progress.emit(jid, pct, event.details)

    def cancel(self) -> None:
        """Stop this job now: kill the running subprocess, restore the LLM, mark
        it cancelled. The kill triggers the worker's failure callback, which the
        ``_cancelled`` guard turns into a no-op (we've already reported)."""
        if self._cancelled or self._status in (JobStatus.DONE, JobStatus.ERROR):
            return
        self._cancelled = True
        for w in (self._tw, self._aw):
            if w is not None:
                try:
                    w.stop()
                except Exception:  # noqa: BLE001
                    pass
        self._gpu_release_bg()
        self._sweep_temp_audio()
        self._set_status(JobStatus.CANCELLED, error=self._t("cancelled_by_user")
                         if "cancelled_by_user" in _DETAIL[self._lang] else "")
        self._journal_close(JobStatus.CANCELLED.value)
        self.completed.emit(self.entry_id, False, "__cancelled__")

    def _on_tx_done(self, jid: int, result) -> None:
        if self._cancelled:
            return
        if not result.success:
            self._gpu_release_bg()   # restore the LLM even on failure
            self._fail(self._explain_tx_error(result.error))
            return
        self._transcript_path = result.output
        try:
            self.store.set_transcript(jid, result.output)
        except KeyError:
            pass
        # Transcription done — bring the local LLM back.
        #  * provider 'local': the summary/analysis NEED it → wait for it to be
        #    ready (off the UI thread) before continuing.
        #  * agent / cloud provider: it isn't used for summary → restore it in the
        #    background and get straight to work; no reason to stall on its reload.
        if self._gpu_held and self._provider == "local":
            self.progress.emit(self.entry_id, PCT_TRANSCRIBE_END, self._t("restore"))
            self._hb = FnWorker(self._do_release, parent=self)
            self._hb.done.connect(self._post_transcription)
            self._hb.start()
        else:
            if self._gpu_held:
                self._gpu_release_bg()   # non-local provider → restore off the hot path
            self._post_transcription()

    def _explain_tx_error(self, error: str) -> str:
        """Say an empty transcription in the user's language.

        The backend tags these two cases (`SILENT_AUDIO:` / `NO_SPEECH:`) precisely
        so the UI can name the cause instead of echoing an English sentence — the
        owner hit the silent-track case and could not tell it was the file.
        """
        text = error or "Transcription failed"
        if "SILENT_AUDIO:" in text:
            peak = re.search(r"peak (-?\d+(?:\.\d+)?) dBFS", text)
            return self._t("tx_silent", peak=peak.group(1) if peak else "?")
        if "NO_SPEECH:" in text:
            return self._t("tx_no_speech")
        return text

    def _do_release(self) -> None:
        """Runs in an FnWorker thread — restore the local LLM (waits for it)."""
        t0 = time.monotonic()
        self._gpu_release()
        t1 = time.monotonic()
        self._record_span(self._t("st_reload"), t0, t1)
        self.stage_done.emit(self.entry_id, self._t("st_reload"), t1 - t0)

    def _post_transcription(self) -> None:
        if self._cancelled:
            return
        # Gate: if engine is whisperx AND transcript has diarisation markers,
        # pause and ask the user to assign speaker names.
        engine = self.settings.get("transcriptionEngine", "faster-whisper")
        if engine == "whisperx":
            try:
                transcript_text = Path(self._transcript_path).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                transcript_text = ""
            if SPK.extract_speakers(transcript_text):
                self._waiting_for_speakers = True
                self.speakers_needed.emit(self.entry_id, transcript_text)
                return   # pipeline suspended until resume_summary() called
        # The local model may still be reloading (our hand-off restart, or the
        # user's external watchdog). We do NOT fail here: the summary request's
        # own connection-retry policy (aiRetries / aiRetryDelay) is what waits for
        # it — that's the user's configured budget, not something to pre-empt.
        self._start_summary()

    def resume_summary(self, transcript_text: str, participants) -> None:
        """Called by UI after user saves speaker names.

        *transcript_text* is the rebuilt transcript (with display names).
        *participants* is a list of display names for the --participants arg.
        The updated transcript is written back to the transcript file so that
        all downstream consumers (analysis, Obsidian) use the renamed version.
        """
        if not self._waiting_for_speakers:
            return
        self._waiting_for_speakers = False
        # Overwrite the raw transcript file with the renamed version
        try:
            Path(self._transcript_path).write_text(
                transcript_text, encoding="utf-8")
        except OSError as exc:
            logger.exception("Could not persist renamed transcript for job %s", self.entry_id)
            self._fail(self._t("transcript_save_failed", error=exc))
            return
        self.participants = list(participants) if participants else self.participants
        self._start_summary()

    def skip_speakers(self) -> None:
        """Called by UI when user clicks Cancel in the speakers dialog.

        Pipeline continues with the original transcript unchanged.
        """
        if not self._waiting_for_speakers:
            return
        self._waiting_for_speakers = False
        self._start_summary()

    def _contextual_memory_block(self) -> str:
        """When 'useContextualMemory' is on, gather the latest summaries of PRIOR
        meetings in the same project (from history) so the model can reference them.
        Returns a text block to append to the summary prompt, or '' if disabled /
        no project / no prior meetings. Bounded: last 3 meetings, ~1500 chars each,
        ~6000 total, so it never blows the context."""
        if not self.settings.get("useContextualMemory"):
            return ""
        project = (self.settings.get("projectId") or "").strip()
        if not project:
            return ""
        try:
            entries = [e for e in self.store.load()
                       if getattr(e, "project", "") == project
                       and e.id != self.entry_id and e.summary_versions]
        except Exception:
            return ""
        entries.sort(key=lambda e: e.id, reverse=True)   # id = ms timestamp → recency
        parts, total = [], 0
        for e in entries[:3]:
            try:
                path = e.summary_versions[-1].path or e.summary_path
                text = open(path, encoding="utf-8", errors="replace").read().strip()
            except Exception:
                continue
            if not text:
                continue
            text = text[:1500]
            parts.append(f"### Встреча «{e.video_name}» ({e.processed_at or ''}):\n{text}")
            total += len(text)
            if total >= 6000:
                break
        if not parts:
            return ""
        return ("\n\n---\nКонтекст из предыдущих встреч проекта «" + project +
                "» (для связности; не пересказывай их, только учитывай):\n\n"
                + "\n\n".join(parts))

    def _ai_processing_kwargs(self) -> dict:
        """Runtime AI flags shared by the summary and analysis passes, from the
        user's settings (chunking opt-out, reasoning, timeout, retries)."""
        s = self.settings

        def _int(key):
            try:
                return int(s.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        return {
            "agent_command": s.get("agentCommand", ""),
            "agent_cwd": s.get("agentCwd", ""),
            "timeout": _int("aiTimeout"),
            "no_think": bool(s.get("disableReasoning", False)),
            "chunk_chars": _int("chunkChars"),
            "no_chunk": not bool(s.get("chunkingEnabled", False)),
            "retries": _int("aiRetries"),
            "retry_delay": _int("aiRetryDelay"),
        }

    def _start_summary(self) -> None:
        self._set_status(JobStatus.SUMMARIZING)
        # No sub-detail: the status label already reads "Создание саммари…" and the
        # live timeline ticks it. Emitting _t("summary") here just repeated the same
        # words in a second place. Clear the detail line instead.
        self.progress.emit(self.entry_id, PCT_SUMMARY_START, "")
        prompt = self.settings.get("prompt", "") + self._contextual_memory_block()
        command = S.build_command(
            prompt, self._transcript_path,
            provider=self._provider, api_key=self._api_key,
            endpoint=self._endpoint, model=self._model, advanced=self._advanced,
            participants=self.participants,
            output_language=S.resolve_output_language(self.settings),
            transcription_language=self.settings.get("transcriptionLanguage", "ru"),
            **self._ai_processing_kwargs(),
            python_exe=self._python_exe, ai_client_script=self._ai_client_script)
        self._aw = AiWorker(self.entry_id, command, parent=self)
        self._aw.done.connect(self._on_summary_done)
        self._aw.start()

    def _on_summary_done(self, jid: int, ok: bool, text: str, error: str) -> None:
        if self._cancelled:
            return
        if not ok:
            self._fail(error or "Summary generation failed")
            return
        out_dir = self.store.job_dir(jid)
        entry = self.store.get(jid)
        version = len(entry.summary_versions) + 1 if entry else 1
        path = out_dir / versioned_filename(self.stem, "summary", version, ".txt")
        path.write_text(text, encoding="utf-8")
        self._summary_version = self.store.add_summary_version(
            jid, path, provider=self._provider)
        self._summary_path = str(path)
        if self._run_record is not None:
            run_history.add_artifact(self._run_record, "summary", self._summary_version)
        if self._scope == "summary":
            # The user asked for the summary alone — a fresh analysis is exactly
            # what they chose not to spend the model's time on.
            self._finish_ok()
            return
        self._analysis_features = deque(A.enabled_features(self.settings))
        if not self._analysis_features:
            self._finish_ok()
            return
        self._start_analysis()

    def _start_analysis(self) -> None:
        self._set_status(JobStatus.ANALYZING)
        self._analysis_results = A.empty_results()
        self._total_features = len(self._analysis_features)
        self._done_features = 0
        self._failed_features = 0        # AI call returned an error for this feature
        self._analysis_errors: list[str] = []
        # Attempts already spent per feature. A model is sampled, not deterministic:
        # the same prompt that returned unusable JSON once returns clean JSON on the
        # next pass, and one such feature used to cost the whole meeting its green
        # status AND its Obsidian/Sheets export. Give each feature one second try.
        self._feature_attempts: dict[str, int] = {}
        self._analysis_from_transcript = (
            self.settings.get("analysisSource", "transcript") == "transcript")
        # The meeting's real date/time, taken from the file name once per run: the
        # formal protocol must state them instead of inventing a year.
        entry = self.store.get(self.entry_id)
        self._protocol_facts = A.protocol_facts(
            (getattr(entry, "video_name", "") or Path(self.video_path).name),
            duration=(getattr(entry, "duration", "") or ""),
            language=S.resolve_output_language(self.settings))
        self._run_next_feature()

    def _run_next_feature(self) -> None:
        if not self._analysis_features:
            self._finish_analysis()
            return
        feature = self._analysis_features.popleft()
        self._current_feature = feature
        # An analysis-only run has no transcription/summary ahead of it, so the
        # features span the whole bar instead of starting at the halfway mark.
        first = 0 if self._scope == "analysis" else PCT_ANALYSIS_START
        pct = first + int(
            (PCT_ANALYSIS_END - first)
            * self._done_features / max(1, self._total_features))
        name = _FEATURE_NAME[self._lang].get(feature, feature)
        self.progress.emit(self.entry_id, pct, self._t("analysis", name=name))
        # Full transcript is the quality-first default. Summary-based analysis
        # remains an explicit faster/less-complete option.
        if self.settings.get("analysisSource", "transcript") == "transcript":
            source = self._transcript_path or self._summary_path
        else:
            source = self._summary_path or self._transcript_path
        command = A.build_feature_command(
            feature, source, self.settings,
            provider=self._provider, api_key=self._api_key,
            endpoint=self._endpoint, model=self._model, advanced=self._advanced,
            facts=getattr(self, "_protocol_facts", None),
            **self._ai_processing_kwargs(),
            python_exe=self._python_exe, ai_client_script=self._ai_client_script)
        self._feat_t0 = time.monotonic()      # time this feature for the flame graph
        self._aw = AiWorker(self.entry_id, command, parent=self)
        self._aw.done.connect(self._on_feature_done)
        self._aw.start()

    def _on_feature_done(self, jid: int, ok: bool, text: str, error: str) -> None:
        if self._cancelled:
            return
        # Flame graph: a depth-2 span per feature, nested under the analysis stage.
        # AND surface each finished feature as its own line in the status timeline
        # (analysis is multi-step — one line per feature, like transcription chunks;
        # the coarse "Расширенный анализ" line is suppressed in _set_status).
        fname = _FEATURE_NAME[self._lang].get(self._current_feature, self._current_feature)
        secs = time.monotonic() - self._feat_t0 if self._feat_t0 is not None else 0.0
        unusable_json = False       # the call succeeded, its answer did not parse
        # A single bad feature must not abort the whole analysis — BUT it must NOT be
        # reported as done either. On an AI-call failure (provider error/timeout/empty)
        # mark it ✖ in the timeline and count it; a run where EVERY feature failed is a
        # real failure (see _finish_analysis), not a silent empty "success".
        if ok:
            parsed = A.parse_json_response(text)
            if A.is_valid_feature_result(self._current_feature, parsed):
                if self._current_feature == "formalProtocol":
                    # Facts win over the model: the exports read these fields.
                    parsed = A.apply_protocol_facts(
                        parsed, getattr(self, "_protocol_facts", None))
                A.store_feature_result(
                    self._analysis_results, self._current_feature, parsed)
                self._record_span(fname, self._feat_t0, self._feat_t0 + secs)
                self.stage_done.emit(
                    self.entry_id, self._t("analysis", name=fname), secs)
            else:
                # ``error`` already holds the child's stderr on a zero exit code —
                # that is where the provider names the reason (e.g. the answer hit
                # the token ceiling and stops mid-sentence). Keeping it points at
                # the setting to change instead of at the model's formatting.
                truncated = _TRUNCATION_MARK in (error or "")
                ok, unusable_json = False, True
                error = (
                    (self._t("an_truncated") + " " if truncated else "")
                    + "AI returned invalid JSON/schema; response starts with: "
                    + (text or "")[:300].replace("\n", " ")
                )
                self._dump_failed_response(text)
        if not ok:
            # Sampling makes a model non-deterministic: an answer that came back
            # unusable is worth one more pass before it costs the run its status.
            # Only unparsable ANSWERS are retried — a provider error or timeout has
            # already spent ai_client's own retry budget, and repeating it here
            # would just double a wait that is already minutes long.
            feature = self._current_feature
            attempts = self._feature_attempts.get(feature, 0) + 1
            self._feature_attempts[feature] = attempts
            if unusable_json and attempts < 2:
                logger.warning(
                    "Analysis feature %s failed for job %s (attempt %d) — retrying: %s",
                    feature, self.entry_id, attempts, error or "unknown error")
                self.stage_done.emit(
                    self.entry_id,
                    f"↻ {self._t('analysis', name=fname)} — {self._t('retry')}", secs)
                self._analysis_features.appendleft(feature)
                self._run_next_feature()
                return
            self._failed_features += 1
            self._analysis_errors.append(f"{fname}: {error or 'unknown error'}")
            logger.error(
                "Analysis feature %s failed for job %s: %s",
                self._current_feature, self.entry_id, error or "unknown error")
            self.stage_done.emit(
                self.entry_id,
                f"✖ {self._t('analysis', name=fname)} — {self._t('failed')}", secs)
        self._done_features += 1
        self._run_next_feature()

    def _dump_failed_response(self, text: str) -> None:
        """Keep the model's raw answer next to the artifacts when it won't parse.

        Only the first 300 characters ever reached the user, and nothing kept the
        rest — so "AI returned invalid JSON/schema" could not be diagnosed after
        the fact: whether the answer was truncated, fenced, or simply not JSON was
        unknowable once the run ended. Writing it costs a few kB and is never
        allowed to fail the run.
        """
        if not text:
            return
        try:
            out_dir = self.store.job_dir(self.entry_id)
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{self.stem}_failed_{self._current_feature}.txt"
            path.write_text(text, encoding="utf-8")
            logger.info("Unparsable %s response saved for job %s: %s",
                        self._current_feature, self.entry_id, path)
        except OSError as exc:
            logger.warning("Could not save the unparsable %s response: %s",
                           self._current_feature, exc)

    def _finish_analysis(self) -> None:
        out_dir = self.store.job_dir(self.entry_id)
        entry = self.store.get(self.entry_id)
        version = len(entry.analysis_versions) + 1 if entry else 1
        path = out_dir / versioned_filename(self.stem, "analysis", version, ".json")
        path.write_text(
            json.dumps(self._analysis_results, ensure_ascii=False, indent=2),
            encoding="utf-8")
        # Source link is truthful: only record the summary version when the analysis
        # was actually built FROM the summary (so the UI never lies "из саммари" for a
        # transcript-sourced analysis).
        src_ver = 0 if getattr(self, "_analysis_from_transcript", False) \
            else getattr(self, "_summary_version", 0)
        an_version = self.store.add_analysis_version(
            self.entry_id, path, provider=self._provider,
            source_summary_version=src_ver)
        if self._run_record is not None:
            run_history.add_artifact(self._run_record, "analysis", an_version)
        # Any missing section means the requested analysis is incomplete. Keep
        # the partial artifact for recovery/debugging, but never mark the meeting
        # green: the user can correct the provider and regenerate the analysis.
        if self._failed_features:
            if self._failed_features >= self._total_features:
                message = self._t("an_all_failed")
            else:
                message = self._t(
                    "an_some_failed", failed=self._failed_features,
                    total=self._total_features)
            if self._analysis_errors:
                message += "\n" + "\n".join(self._analysis_errors[:3])
            self._fail(message)
            return
        self._finish_ok()

    # -- helpers -------------------------------------------------------
    def _set_status(self, status: JobStatus, *, previous_succeeded: bool = True,
                    error: str = "") -> None:
        if status == self._status:
            return
        # Record how long the stage we're leaving took (for the final breakdown).
        now = time.monotonic()
        if self._status is not None and self._stage_t0 is not None:
            elapsed = now - self._stage_t0
            self._timings.append((self._status, elapsed))
            label = _STAGE_NAME[self._lang].get(self._status)
            if label:
                # Flame graph: record the coarse stage span (incl. TRANSCRIBING —
                # it's the PARENT the chunks nest under).
                self._record_span(label, self._stage_t0, now)
                # UI timeline: skip the coarse TRANSCRIBING / ANALYZING lines —
                # their per-chunk / per-feature entries are the finer, truer
                # timeline (emitted from _on_tx_progress / _on_feature_done).
                if self._status not in (JobStatus.TRANSCRIBING, JobStatus.ANALYZING):
                    shown = label if previous_succeeded else \
                        f"✖ {label} — {self._t('failed')}"
                    self.stage_done.emit(self.entry_id, shown, elapsed)
        self._stage_t0 = now
        self._status = status
        if status == JobStatus.TRANSCRIBING:
            self._chunk_accum = 0.0        # chunks lay out from the stage start
        try:
            self.store.set_status(self.entry_id, status, error=error)
        except KeyError:
            pass
        self.status_changed.emit(self.entry_id, status)

    def _t(self, key: str, **kw) -> str:
        return _DETAIL[self._lang].get(key, key).format(**kw)

    def _time_breakdown(self) -> str:
        """'аудио 3с · транскрибация 5м 2с · саммари 6м · анализ 12м' — per-stage
        timings collected across the run."""
        names = _STAGE_NAME[self._lang]
        parts = [f"{names.get(st, str(st.value))} {fmt_duration(sec)}"
                 for st, sec in self._timings if st in names]
        return _DETAIL[self._lang]["breakdown_sep"].join(parts)

    def _sweep_temp_audio(self, delay_ms: int = 2000) -> None:
        """Delete this job's extracted audio (``<stem>_temp*.wav``).

        A cancelled run has its subprocess KILLED, so processor.py never reaches
        its own cleanup: one cancelled 12-minute meeting left 328 MB behind (a
        171 MB full WAV plus nine chunks). The kill is not instantaneous and
        Windows refuses to unlink a file the dying process still holds, so the
        sweep is attempted now and once more shortly after. Strictly scoped to this
        job's own temporary WAVs — never to any produced artifact.
        """
        def _try_once() -> None:
            try:
                out_dir = self.store.job_dir(self.entry_id)
            except Exception:      # noqa: BLE001
                return
            for leftover in Path(out_dir).glob(f"{self.stem}_temp*.wav"):
                try:
                    leftover.unlink()
                except OSError:
                    pass           # still held by the dying process; retried below

        _try_once()
        if delay_ms > 0:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(delay_ms, _try_once)

    def _fail(self, message: str) -> None:
        self._gpu_release_bg()   # never leave the LLM stopped after an error
        self._sweep_temp_audio()
        # The stage we are leaving failed. It must not be rendered with a ✔ in
        # the timeline merely because changing to ERROR closes its timer.
        # Persist WHY: a restart used to leave the row saying "error" and nothing
        # more, because the reason lived only in this process.
        self._set_status(JobStatus.ERROR, previous_succeeded=False, error=message)
        self._journal_close(JobStatus.ERROR.value, error=message)
        self.completed.emit(self.entry_id, False, message)

    # -- GPU hand-off (optional) --------------------------------------
    def _gpu_release(self) -> bool:
        """Restore the local LLM (blocks while it waits for the port). Called from
        an FnWorker thread, never the UI thread. Returns whether the endpoint is
        listening afterwards, so the caller can fail fast instead of hanging on a
        model that never came back."""
        if not self._gpu_held:
            return True
        self._gpu_held = False
        try:
            return bool(self._gpu_handoff_module().release())
        except Exception:  # noqa: BLE001
            return False

    def _gpu_release_bg(self) -> None:
        """Fire-and-forget restore for error/cancel paths — must not block the UI
        thread (release() waits for the model). Pure-Python module, safe in a
        daemon thread."""
        if not self._gpu_held:
            return
        self._gpu_held = False
        try:
            mod = self._gpu_handoff_module()
            threading.Thread(target=mod.release, daemon=True).start()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _gpu_handoff_module():
        import sys
        from .. import paths
        backend_dir = str(paths.BACKEND_DIR)
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        import gpu_handoff
        return gpu_handoff

    def _write_trace(self) -> None:
        """Persist the whole-job profile (Diagnostics → Processing profile). The
        transcription subprocess writes only its own extract+transcribe spans, so
        the profile used to be blind to reload/summary/analysis — which actually
        dominate. Here we (over)write it with EVERY stage in order, laid out
        sequentially, so the bars reflect where the time really went."""
        if not self._spans:
            return
        total = max(s["end"] for s in self._spans)
        # Root span (depth 0) wraps the whole job; the rest nest by their times.
        spans = [{"name": "video_processing", "start": 0.0, "end": total,
                  "duration": total * 1000.0}]
        for s in self._spans:
            spans.append({"name": s["name"], "start": s["start"], "end": s["end"],
                          "duration": (s["end"] - s["start"]) * 1000.0})
        data = {"name": "video_processing",
                "timestamp": datetime.now().isoformat(),
                "startTime": 0.0, "endTime": total, "duration": total * 1000.0,
                "status": "completed", "spans": spans}
        try:
            path = self.store.job_dir(self.entry_id) / f"{self.stem}_trace.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError:
            logger.exception("Could not persist processing trace for job %s", self.entry_id)

    def _finish_ok(self) -> None:
        self._set_status(JobStatus.DONE)   # stamps the final stage's duration
        self._write_trace()
        self._maybe_export_obsidian()
        self._maybe_export_gsheets()
        total = fmt_duration(sum(sec for _, sec in self._timings))
        detail = self._t("done", total=total)
        breakdown = self._time_breakdown()
        if breakdown:
            detail += "   —   " + breakdown
        self.progress.emit(self.entry_id, 100, detail)
        self._journal_close(JobStatus.DONE.value)
        self.completed.emit(self.entry_id, True, "")

    def _analysis_for_export(self) -> dict:
        """The analysis that describes this meeting RIGHT NOW.

        A summary-only regeneration produces no analysis of its own, and the empty
        in-memory result would then be exported as the truth: a Google Sheets row
        with every analysis column blank for a meeting that HAS an analysis. The
        newest recorded version is what the meeting actually holds.
        """
        if isinstance(self._analysis_results, dict) and self._analysis_results:
            return self._analysis_results
        entry = self.store.get(self.entry_id)
        versions = list(getattr(entry, "analysis_versions", []) or []) if entry else []
        if not versions:
            return {}
        try:
            data = json.loads(Path(versions[-1].path).read_text(
                encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _maybe_export_obsidian(self) -> None:
        """Auto-write summary + analysis notes to the vault if enabled. Best
        effort: an Obsidian failure must never fail the job."""
        if not self.settings.get("obsidianIntegration"):
            return
        vault = self.settings.get("obsidianVaultPath", "")
        if not vault:
            return
        try:
            from ..backend import obsidian
            entry = self.store.get(self.entry_id)
            summary_text = ""
            if self._summary_path and Path(self._summary_path).exists():
                summary_text = Path(self._summary_path).read_text(
                    encoding="utf-8", errors="replace")
            transcript_text = ""
            if self._transcript_path and Path(self._transcript_path).exists():
                transcript_text = Path(self._transcript_path).read_text(
                    encoding="utf-8", errors="replace")
            analysis = self._analysis_for_export()
            # Write only the notes this meeting really has. A scoped regeneration
            # can leave one side untouched, and an empty note named after a version
            # is worse than no note at all. (Version numbers are the LENGTHS of the
            # lists because versions are append-only — v_n is always the n-th.)
            kinds = tuple(kind for kind, present in
                          (("summary", bool(summary_text)), ("analysis", bool(analysis)))
                          if present)
            if not kinds:
                return
            written = obsidian.export_to_obsidian(
                vault, stem=self.stem,
                video_name=(entry.video_name if entry else self.stem),
                summary_text=summary_text, analysis=analysis, settings=self.settings,
                duration=(getattr(entry, "duration", "") or ""),
                summary_version=(len(entry.summary_versions) if entry else 1),
                analysis_version=(len(entry.analysis_versions) if entry else 1),
                transcript_text=transcript_text,
                language=self.settings.get("transcriptionLanguage", "ru"),
                kinds=kinds)
            logger.info("Obsidian notes written for job %s: %s", self.entry_id,
                        ", ".join(str(v) for k, v in (written or {}).items()
                                  if k != "vault" and v) or "none")
        except Exception:
            # The meeting remains complete, but the failed optional export must
            # remain visible in Diagnostics instead of disappearing silently.
            logger.exception("Automatic Obsidian export failed for job %s", self.entry_id)

    def _maybe_export_gsheets(self) -> None:
        """Auto-append a row to the user's Google Sheet via their Apps Script
        webhook if enabled. Best effort: a Sheets failure must never fail the
        job (mirrors the old app's non-fatal auto-export)."""
        if not self.settings.get("googleSheetsIntegration"):
            return
        url = (self.settings.get("googleSheetsUrl") or "").strip()
        if not url:
            return
        try:
            from ..backend import gsheets
            entry = self.store.get(self.entry_id)
            summary_text = ""
            if self._summary_path and Path(self._summary_path).exists():
                summary_text = Path(self._summary_path).read_text(
                    encoding="utf-8", errors="replace")
            transcript_text = ""
            if self._transcript_path and Path(self._transcript_path).exists():
                transcript_text = Path(self._transcript_path).read_text(
                    encoding="utf-8", errors="replace")
            analysis = self._analysis_for_export()
            values = gsheets.build_values(
                (entry.video_name if entry else self.stem), summary_text, analysis,
                duration=(getattr(entry, "duration", "") or ""),
                transcript_text=transcript_text, participants=self.participants)
            answer = gsheets.export(
                url, values, token=(self.settings.get("googleSheetsToken") or "").strip())
            # Say that it WORKED, not only that it broke. Only failures were ever
            # recorded, so "the row is not in my sheet" could not be told apart from
            # "the export never ran" — for eleven meetings at once.
            logger.info("Google Sheets row appended for job %s (%s): %s",
                        self.entry_id, (entry.video_name if entry else self.stem),
                        (answer or "")[:120])
        except Exception:
            # The meeting remains complete, but the failed optional export must
            # remain visible in Diagnostics instead of disappearing silently.
            logger.exception("Automatic Google Sheets export failed for job %s", self.entry_id)


class PipelineQueue(QObject):
    status_changed = Signal(object, object)   # job_id, JobStatus
    progress = Signal(object, int, str)        # job_id, percent, detail
    stage_done = Signal(object, str, float)    # job_id, stage label, seconds
    job_finished = Signal(object, bool, str)   # job_id, ok, error
    speakers_needed = Signal(object, str)      # job_id, transcript_text
    active_changed = Signal(int)
    all_done = Signal()

    def __init__(self, max_concurrency: int,
                 runner_factory: Callable[[int, str], JobRunner], parent=None):
        super().__init__(parent)
        self.max_concurrency = max(1, int(max_concurrency))
        self._factory = runner_factory
        self._pending: deque = deque()
        self._active: dict[int, JobRunner] = {}

    def enqueue(self, entry_id: int, video_path: str) -> None:
        # transcript_path=None => full pipeline (transcribe → summary → analysis)
        self._pending.append((int(entry_id), str(video_path), None, "both"))
        self._pump()

    def enqueue_regenerate(self, entry_id: int, video_path: str,
                           transcript_path: str, scope: str = "both") -> None:
        """Queue a regenerate job: skip transcription and redo *scope* from
        *transcript_path* (already written with any user edits).

        ``scope`` is 'summary', 'analysis' or 'both' — see
        :meth:`JobRunner.start_from_transcript`."""
        self._pending.append(
            (int(entry_id), str(video_path), str(transcript_path), str(scope)))
        self._pump()

    def active_count(self) -> int:
        return len(self._active)

    def pending_count(self) -> int:
        return len(self._pending)

    def set_max_concurrency(self, n: int) -> None:
        """Update the concurrency cap (e.g. once CUDA is detected — a single GPU is
        VRAM-bound). Running jobs are unaffected; the new cap applies to the next
        pump, and if it was raised, more queued jobs start immediately."""
        self.max_concurrency = max(1, int(n))
        self._pump()

    def _pump(self) -> None:
        changed = False
        while len(self._active) < self.max_concurrency and self._pending:
            entry_id, video_path, transcript_path, scope = self._pending.popleft()
            runner = self._factory(entry_id, video_path)
            runner.setParent(self)
            runner.status_changed.connect(self.status_changed)
            runner.progress.connect(self.progress)
            runner.stage_done.connect(self.stage_done)
            runner.completed.connect(self._on_completed)
            runner.speakers_needed.connect(self.speakers_needed)
            self._active[entry_id] = runner
            changed = True
            if transcript_path:
                runner.start_from_transcript(transcript_path, scope)
            else:
                runner.start()
        if changed:
            self.active_changed.emit(len(self._active))

    def runner(self, entry_id: int) -> "JobRunner | None":
        """Return the active runner for *entry_id*, or None."""
        return self._active.get(int(entry_id))

    def pending_ids(self) -> set:
        """Ids waiting to start (already enqueued, not yet running)."""
        return {int(t[0]) for t in self._pending}

    def cancel(self, entry_id: int) -> bool:
        """Cancel exactly one active or waiting job without touching its peers."""
        entry_id = int(entry_id)
        before = len(self._pending)
        self._pending = deque(item for item in self._pending if int(item[0]) != entry_id)
        removed_pending = len(self._pending) != before
        runner = self._active.get(entry_id)
        if runner is not None:
            runner.cancel()  # completed -> _on_completed removes it and pumps the next job
            return True
        return removed_pending

    def cancel_all(self) -> None:
        """Drop everything pending and stop every running job.

        The active map is force-drained afterwards: a runner whose cancel() is a
        no-op (already finishing) must never stay stuck in ``_active``, or the
        concurrency slot leaks and every later job sits at 'queued' forever."""
        self._pending.clear()
        for runner in list(self._active.values()):
            try:
                runner.cancel()   # emits completed -> _on_completed removes it
            except Exception:     # noqa: BLE001
                pass
        if self._active:
            self._active.clear()
            self.active_changed.emit(0)

    def _on_completed(self, entry_id: int, ok: bool, error: str) -> None:
        self.job_finished.emit(entry_id, ok, error)
        self._active.pop(entry_id, None)
        self.active_changed.emit(len(self._active))
        self._pump()
        if not self._active and not self._pending:
            self.all_done.emit()
