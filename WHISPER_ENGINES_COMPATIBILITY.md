# Whisper Engines Compatibility

**English** · [Русский](WHISPER_ENGINES_COMPATIBILITY.ru.md)

Which transcription engines ship, what they depend on, and why the versions are pinned.
Everything here is applied automatically by the bundled runtime — you do **not** need to edit
any installed package by hand.

## Verified stack

Live-verified on Windows 11 with an NVIDIA RTX 4060 Ti (CUDA 12.4 build of PyTorch):

| Package | Version | Why this version |
|---|---|---|
| openai-whisper | 20250625 | Reference implementation |
| faster-whisper | 1.0.3 | Last version whose `TranscriptionOptions` WhisperX 3.1.1 can drive |
| whisperx | 3.1.1 | Needs the runtime patch below |
| vosk | 0.3.45 | Lightweight offline engine |
| sherpa-onnx | 1.13.2 | Runtime for the sherpa-onnx, FunASR and offline-diarization paths |
| torch / torchaudio | 2.6.0+cu124 | Transformers requires torch ≥ 2.6 for the safe `torch.load` default |
| numpy | 1.26.4 | Must stay `<2.0` for pyannote.audio 3.1.1 |
| pyannote.audio | 3.1.1 | Optional (gated) diarization backend |
| speechbrain | 0.5.16 | 1.0+ pulls in `k2`, which has heavy CUDA dependencies |
| ctranslate2 | 4.7.2 | faster-whisper inference runtime |
| huggingface-hub | 1.15.0 | 1.x removed `use_auth_token` — see the patch notes |
| transformers | 5.8.1 | Pins `huggingface-hub >= 1.5` |

The **full** distribution bundles this stack; the **min** distribution installs it from
`backend/requirements.txt` during `INSTALL.bat`.

## Engines and their runtimes

Seven selectable transcription engines, plus a download-only pack of extra sherpa models:

| Engine | Runtime | Notes |
|---|---|---|
| `whisper` | openai-whisper + torch | Reference quality, slowest |
| `faster-whisper` | ctranslate2 | 2–4× faster than the reference |
| `whisperx` | faster-whisper + torch | Fastest, and the only engine that aligns speaker segments |
| `vosk` | vosk | Small models, CPU-friendly, fully offline |
| `sherpa-onnx` | sherpa-onnx | Offline zipformer-transducer via onnxruntime |
| `whisper-cpp` | pywhispercpp | ggml models, CPU-efficient |
| `funasr` | sherpa-onnx | SenseVoice / Paraformer. EN/ZH/JA/KO only — **no Russian**; the registry gates it to `language=en`. Deliberately runs on the sherpa-onnx runtime, so no `funasr`/modelscope package is installed and the torch stack stays untouched |


### Live (streaming) transcription

Live mode keeps ONE model loaded for the whole meeting and decodes short
utterances as they are spoken (`backend/processing/live_engines.py`), instead of
loading a model per run and decoding files. Six of the seven engines have a
streaming adapter:

| Engine | Live | Note |
|---|---|---|
| `faster-whisper` | yes | Default. `beam_size=1` — a beam search on a 3-second utterance costs latency nobody wants and buys accuracy nobody sees |
| `whisperx` | yes | Runs on its faster-whisper weights. **No diarization live** — that is a batch stage, and a 2-second utterance has no speaker structure to find |
| `whisper` | yes | Reference quality, noticeably slower per utterance |
| `vosk` | yes | Natively streaming; the lightest CPU option |
| `sherpa-onnx` | yes | Fast on the CPU, which makes it the sensible pick when the GPU is busy with a local LLM |
| `whisper-cpp` | yes | CPU-efficient ggml |
| `funasr` | no | Decodes fixed-shape windows; unsuitable for sub-second latency. Selecting it for live raises a clear error instead of silently substituting another engine |

The engine's own VAD is switched OFF for live: the stream is already segmented
into utterances upstream, and running a second VAD over a 2-second clip drops
quiet endings. The vocabulary hint (`transcriptionHint`) IS passed to the
whisper family live, which is what keeps names and abbreviations intact.

## The WhisperX runtime patch

WhisperX 3.1.1 does not run unmodified against faster-whisper 1.0.3 and huggingface-hub 1.x.
The fixes live in [`backend/whisperx_patch.py`](backend/whisperx_patch.py) (`patch_whisperx()`,
applied by `whisperx_engine.py` before the model loads) — version-controlled and portable,
rather than hand-edited into `site-packages`. It does all of the following:

1. **Patches the top-level re-export as well.** `whisperx/__init__.py` does
   `from .asr import load_model`, so `whisperx.load_model` is a separate binding from
   `whisperx.asr.load_model`. Patching only the latter has no effect — the engine calls the
   former.
2. **Instantiates WhisperX's own `WhisperModel` subclass**, not `faster_whisper.WhisperModel`.
   Only the subclass defines `generate_segment_batched`, which the batched pipeline calls.
3. **Filters `TranscriptionOptions` to the installed version's real `_fields`**, dropping the
   hardcoded `multilingual` that faster-whisper 1.0.3 rejects.
4. **Loads VAD on the CPU** even when ASR runs on CUDA.
5. **Fixes CUDA encoding.** `WhisperModel.encode` → `get_ctranslate2_storage` →
   `np.ascontiguousarray` needs CPU tensors, but the mel features arrive on `cuda:0`. The
   patch moves them to the CPU inside `encode`.
6. **Shims `use_auth_token` → `token`.** huggingface-hub 1.x removed `use_auth_token`, but
   WhisperX and pyannote 3.1.1 still pass it. The shim wraps `hf_hub_download` /
   `snapshot_download` and rebinds the reference inside modules that imported it earlier.

## Diarization backends

pyannote models are **gated on Hugging Face** — every user would need an account, a token and
to accept the terms — so they cannot be the default in a distributed app. The
`diarizationBackend` setting picks one of:

- **`sherpa`** (default) — offline `sherpa_onnx.OfflineSpeakerDiarization` on free, ungated
  ONNX models fetched into `resources/diarization_models/`: segmentation
  `sherpa-onnx-pyannote-segmentation-3-0`, embedding
  `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` (both from the k2-fsa model
  releases). No token needed. Implemented in `backend/processing/diarization.py`.
- **`pyannote`** — the gated path, higher quality. The user pastes their own token into the
  `hfToken` setting and must accept the model terms (see below).
- **`off`** — no diarization.

WhisperX assigns the chosen backend's speaker segments to its transcript segments by time
overlap, so the output is the same either way:

```
[00:00:00] [SPEAKER_00]: first speaker's text
[00:00:15] [SPEAKER_01]: second speaker's text
```

That format is what the speaker-management UI parses.

### Enabling the gated pyannote backend

1. Create a Hugging Face token with read access at <https://huggingface.co/settings/tokens>.
2. Accept the terms on both model pages — "Agree and access repository":
   - <https://huggingface.co/pyannote/segmentation-3.0>
   - <https://huggingface.co/pyannote/speaker-diarization-3.1>
3. Paste the token into Settings → `hfToken` (it is passed to the backend as `--hf-token`),
   or cache it once with `huggingface-cli login`.

`k2_fsa` warnings during diarization are harmless — it is an optional dependency.

## FFmpeg

FFmpeg is **required**: audio extraction and `whisperx.load_audio()` both call it as a
subprocess. Without it on `PATH` you get `[WinError 2] File not found`. The full distribution
bundles ffmpeg and puts it on `PATH` for every spawned subprocess; for the min distribution,
install ffmpeg system-wide and add it to the Windows `PATH`.

## Known issues

**Broken model "symlink pointers" after a Windows file transfer.** A Hugging Face snapshot
copied between Windows machines can end up with 76-byte text files containing
`../../blobs/…` instead of real files or symlinks. CTranslate2 then reads a garbage binary
version. Fix: delete the stale pointer files and re-run `snapshot_download` — the blobs are
usually intact, so nothing is re-downloaded and proper symlinks are recreated. The packaging
step verifies real files, not pointers, before archiving.

**FunASR has no Russian.** The registry only offers it for `language=en`. Use Whisper-family
engines, Vosk or sherpa-onnx for Russian audio.

## Checking your installation

```python
import torch, whisperx
from faster_whisper import WhisperModel

print("CUDA available:", torch.cuda.is_available())
print("GPU:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU only")

model = whisperx.load_model("tiny", "cuda", compute_type="float16", language="en")
```

Or use the app: **Diagnostics** reports the detected device, CUDA availability and per-engine
readiness without leaving the UI.
