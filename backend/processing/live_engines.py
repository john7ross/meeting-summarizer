"""Streaming adapters: a recognition engine held OPEN across a whole meeting.

The batch adapters in ``processing/engines/`` are built for a different shape of
work — they take a list of chunk FILES, load the model, decode everything and
throw the model away. Loading ``medium`` costs seconds; doing that per utterance
would make live transcription slower than the meeting.

So the live path needs the mirror image: load once, then decode short PCM
buffers over and over. That is all a ``StreamingEngine`` is. Model *resolution*
is not duplicated — it goes through ``engines_registry`` exactly like the batch
adapters, so an engine that has its model downloaded works live too, with no
second copy on disk and no second settings key.

Not every engine is here. ``funasr`` and ``sherpa-extra`` decode fixed-shape
windows that do not fit sub-second latency, and ``whisperx`` has no model of its
own — live falls back to the faster-whisper weights it already runs on, WITHOUT
diarisation (a 2-second utterance carries no speaker structure to find). That is
stated, never silently substituted: ``load()`` raises for anything unsupported.
"""
from __future__ import annotations

import glob
import os
from typing import Optional

try:
    import engines_registry as reg
except ImportError:                                    # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import engines_registry as reg


# Engines with a streaming adapter. ``whisperx`` maps onto faster-whisper.
SUPPORTED = ("faster-whisper", "whisperx", "whisper", "vosk",
             "sherpa-onnx", "whisper-cpp")

# Engines that exist in the registry but cannot serve the live path.
UNSUPPORTED_REASON = {
    "funasr": "FunASR decodes fixed-shape windows and is not suitable for live",
    "sherpa-extra": "community models are batch-only in this build",
}


def _pcm_to_float32(pcm: bytes):
    """int16 PCM bytes -> float32 numpy array in [-1, 1] (what every engine wants)."""
    import numpy as np
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _resolve_device(device: str) -> str:
    """'auto' -> cuda when torch says it is really there, else cpu."""
    wanted = (device or "auto").lower()
    if wanted == "cpu":
        return "cpu"
    try:
        import torch
        available = bool(torch.cuda.is_available())
    except Exception:                                   # noqa: BLE001
        available = False
    if wanted == "cuda":
        return "cuda" if available else "cpu"
    return "cuda" if available else "cpu"


class StreamingEngine:
    """Base: a loaded model that turns one PCM buffer into one line of text."""

    name = "base"

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        raise NotImplementedError

    def close(self) -> None:
        return None


class FasterWhisperStreaming(StreamingEngine):
    """faster-whisper (CTranslate2) — the default, and the best latency/quality
    trade-off we ship. ``beam_size=1`` because a beam search on a 3-second
    utterance buys accuracy nobody sees in a live panel and costs latency
    everybody feels."""

    name = "faster-whisper"

    def __init__(self, model_size: str, language: str, device: str,
                 initial_prompt: str = "", res_dir: Optional[str] = None):
        from faster_whisper import WhisperModel
        size = model_size if model_size in reg.ENGINES["faster-whisper"]["models"] \
            else reg.default_model("faster-whisper")
        models_dir = os.path.join(reg.resources_dir(res_dir), "whisper_models")
        os.makedirs(models_dir, exist_ok=True)
        local_only = reg.resolve_model_path("faster-whisper", size, res_dir=res_dir) is not None
        actual = _resolve_device(device)
        self._model = WhisperModel(
            size, device=actual,
            compute_type="float16" if actual == "cuda" else "int8",
            download_root=models_dir, local_files_only=local_only)
        self._language = language or None
        self._prompt = (initial_prompt or "").strip() or None
        self.device = actual
        self.model_id = size

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        segments, _info = self._model.transcribe(
            _pcm_to_float32(pcm),
            language=self._language,
            beam_size=1,
            # Our own segmenter already removed the silence; running the
            # engine's VAD again on a 2-second utterance drops quiet endings.
            vad_filter=False,
            condition_on_previous_text=False,
            initial_prompt=self._prompt)
        return " ".join((seg.text or "").strip() for seg in segments).strip()

    def close(self) -> None:
        self._model = None


class OpenAiWhisperStreaming(StreamingEngine):
    """openai-whisper — the reference implementation; slower, kept for parity
    with the batch engine list."""

    name = "whisper"

    def __init__(self, model_size: str, language: str, device: str,
                 initial_prompt: str = "", res_dir: Optional[str] = None):
        import whisper
        size = model_size if model_size in reg.ENGINES["whisper"]["models"] \
            else reg.default_model("whisper")
        models_dir = os.path.join(reg.resources_dir(res_dir), "whisper_models")
        os.makedirs(models_dir, exist_ok=True)
        self.device = _resolve_device(device)
        self._model = whisper.load_model(size, download_root=models_dir,
                                         device=self.device)
        self._language = language or None
        self._prompt = (initial_prompt or "").strip() or None
        self.model_id = size

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        result = self._model.transcribe(
            _pcm_to_float32(pcm), language=self._language,
            initial_prompt=self._prompt, condition_on_previous_text=False,
            fp16=(self.device == "cuda"))
        return str(result.get("text") or "").strip()

    def close(self) -> None:
        self._model = None


class VoskStreaming(StreamingEngine):
    """Vosk — the only engine here that is natively streaming. The model stays
    loaded and a fresh recogniser is created per utterance, which is cheap
    (milliseconds) and keeps every utterance independent."""

    name = "vosk"

    def __init__(self, model_name: str, language: str, device: str,
                 initial_prompt: str = "", res_dir: Optional[str] = None):
        from vosk import Model, SetLogLevel
        SetLogLevel(-1)
        path, resolved = _resolve_vosk_model(model_name, language, res_dir)
        self._Recognizer = _import_vosk_recognizer()
        self._model = Model(path)
        self.model_id = resolved
        self.device = "cpu"

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        import json
        rec = self._Recognizer(self._model, float(sample_rate))
        rec.AcceptWaveform(pcm)
        payload = json.loads(rec.FinalResult() or "{}")
        return str(payload.get("text") or "").strip()

    def close(self) -> None:
        self._model = None


def _import_vosk_recognizer():
    from vosk import KaldiRecognizer
    return KaldiRecognizer


def _resolve_vosk_model(model, language, res_dir=None):
    """Same rule as the batch adapter: honour a known name, else the language's
    small model, else say exactly what is missing."""
    name = model if reg.intended_path("vosk", model or "") else None
    if name is None:
        name = ("vosk-model-small-ru-0.22" if (language or "ru") == "ru"
                else "vosk-model-small-en-us-0.15")
    path = reg.resolve_model_path("vosk", name, res_dir=res_dir)
    if path:
        return path, name
    raise RuntimeError(
        f"Vosk model '{name}' is not downloaded. Download it in Settings "
        f"(or choose an available model).")


class SherpaOnnxStreaming(StreamingEngine):
    """sherpa-onnx offline zipformer transducer. Fast on CPU, which makes it the
    sensible live choice on a machine whose GPU is busy with the local LLM."""

    name = "sherpa-onnx"

    def __init__(self, model_name: str, language: str, device: str,
                 initial_prompt: str = "", res_dir: Optional[str] = None):
        import sherpa_onnx
        path, resolved = _resolve_sherpa_model(model_name, language, res_dir)
        provider = "cuda" if (_resolve_device(device) == "cuda"
                              and _onnx_has_cuda()) else "cpu"
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=_pick_onnx(path, "encoder"),
            decoder=_pick_onnx(path, "decoder"),
            joiner=_pick_onnx(path, "joiner"),
            tokens=os.path.join(path, "tokens.txt"),
            num_threads=2, decoding_method="greedy_search", provider=provider)
        self.model_id = resolved
        self.device = provider

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, _pcm_to_float32(pcm))
        self._recognizer.decode_streams([stream])
        return str(getattr(stream.result, "text", "") or "").strip()

    def close(self) -> None:
        self._recognizer = None


def _onnx_has_cuda() -> bool:
    """Whether the installed onnxruntime can actually run on the GPU.

    torch having CUDA says nothing about sherpa: the shipped sherpa-onnx wheel
    is usually a CPU build, and asking it for the CUDA provider only makes it
    print a fallback notice to stderr while we report 'device: cuda' to the UI.
    Reporting the device we really got is worth this one extra check.
    """
    try:
        import onnxruntime
        return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:                                   # noqa: BLE001
        return False


def _resolve_sherpa_model(model, language, res_dir=None):
    name = model if reg.intended_path("sherpa-onnx", model or "") else None
    if name is None:
        name = ("sherpa-onnx-small-zipformer-ru-2024-09-18" if (language or "ru") == "ru"
                else "sherpa-onnx-zipformer-small-en-2023-06-26")
    path = reg.resolve_model_path("sherpa-onnx", name, res_dir=res_dir)
    if path:
        return path, name
    raise RuntimeError(
        f"sherpa-onnx model '{name}' is not downloaded. Download it in Settings "
        f"(or choose an available model).")


def _pick_onnx(model_dir, prefix):
    files = sorted(glob.glob(os.path.join(model_dir, prefix + "*.onnx")))
    if not files:
        raise RuntimeError(f"sherpa model is missing {prefix}*.onnx in {model_dir}")
    non_int8 = [f for f in files if "int8" not in os.path.basename(f)]
    return (non_int8 or files)[0]


class WhisperCppStreaming(StreamingEngine):
    """whisper.cpp (pywhispercpp) — the CPU-efficient option."""

    name = "whisper-cpp"

    def __init__(self, model_size: str, language: str, device: str,
                 initial_prompt: str = "", res_dir: Optional[str] = None):
        from pywhispercpp.model import Model
        size = model_size if reg.intended_path("whisper-cpp", model_size or "") \
            else reg.default_model("whisper-cpp")
        path = reg.resolve_model_path("whisper-cpp", size, res_dir=res_dir)
        if not path:
            raise RuntimeError(
                f"whisper.cpp model '{size}' is not downloaded. Download it in Settings.")
        use_gpu = _resolve_device(device) == "cuda"
        self._model = Model(path, n_threads=max(2, (os.cpu_count() or 4) // 2),
                            language=(language or "auto"), print_progress=False,
                            print_realtime=False, context_params={"use_gpu": use_gpu})
        self.model_id = size
        self.device = "cuda" if use_gpu else "cpu"

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        segments = self._model.transcribe(_pcm_to_float32(pcm))
        return " ".join((getattr(s, "text", "") or "").strip()
                        for s in segments).strip()

    def close(self) -> None:
        self._model = None


_ADAPTERS = {
    "faster-whisper": FasterWhisperStreaming,
    # whisperx has no weights of its own; live runs the faster-whisper model it
    # is built on. Diarisation is a batch-only stage and does not apply here.
    "whisperx": FasterWhisperStreaming,
    "whisper": OpenAiWhisperStreaming,
    "vosk": VoskStreaming,
    "sherpa-onnx": SherpaOnnxStreaming,
    "whisper-cpp": WhisperCppStreaming,
}


def supports(engine: str) -> bool:
    return engine in _ADAPTERS


def load(engine: str, model: str, language: str, device: str = "auto",
         initial_prompt: str = "", res_dir: Optional[str] = None) -> StreamingEngine:
    """Load ``engine`` for live use, or raise with a reason a user can act on."""
    adapter = _ADAPTERS.get(engine)
    if adapter is None:
        reason = UNSUPPORTED_REASON.get(
            engine, f"'{engine}' has no streaming adapter")
        raise RuntimeError(
            f"Live transcription is not available for this engine: {reason}. "
            f"Supported live engines: {', '.join(SUPPORTED)}.")
    try:
        return adapter(model, language, device, initial_prompt, res_dir)
    except ImportError as exc:
        raise RuntimeError(
            f"Live transcription needs the '{engine}' engine package, which is "
            f"not installed in this runtime ({exc}). Install it or choose "
            f"another engine in settings.") from exc
