#!/usr/bin/env python3
"""
Патч для совместимости WhisperX 3.1.1 с faster-whisper 1.0.3+
Исправляет проблему с TranscriptionOptions API
"""
import sys
import os


def whisperx_vad_safe_globals():
    """Types used by WhisperX's trusted, fixed VAD checkpoint.

    PyTorch 2.6 correctly defaults ``torch.load`` to ``weights_only=True``.
    WhisperX 3.1.1 ships an older Lightning/OmegaConf checkpoint, so loading it
    requires an explicit, tightly scoped allowlist. Keep this list limited to
    the names reported by ``get_unsafe_globals_in_checkpoint`` for
    ``whisperx-vad-segmentation.bin``; never disable safe loading globally.
    """
    from collections import defaultdict
    from typing import Any
    from omegaconf.base import Metadata, ContainerMetadata
    from omegaconf.listconfig import ListConfig
    from omegaconf.nodes import AnyNode
    from pyannote.audio.core.task import Problem, Resolution, Specifications
    from pyannote.audio.core.model import Introspection
    from torch.torch_version import TorchVersion

    return [
        defaultdict, Metadata, Any, list, Problem, dict, Resolution,
        ContainerMetadata, ListConfig, Specifications, Introspection, int,
        AnyNode, TorchVersion,
    ]


def _patch_hf_use_auth_token():
    """Shim the removed ``use_auth_token`` kwarg for diarization.

    huggingface_hub 1.x removed ``use_auth_token`` (now ``token``), but whisperX
    3.1.1 / pyannote 3.1.1 still pass it → ``DiarizationPipeline`` crashed with
    "hf_hub_download() got an unexpected keyword argument 'use_auth_token'". We
    can't downgrade hf_hub (transformers pins >=1.5.0). So wrap the download fns
    to translate the old kwarg, and REBIND the reference inside every module that
    already did ``from huggingface_hub import hf_hub_download`` (pyannote imports
    it at load time, before this patch runs).
    """
    import sys as _sys
    import huggingface_hub as _hf

    def _wrap(orig):
        if getattr(orig, "_uat_shim", False):
            return orig

        def shim(*args, **kwargs):
            if "use_auth_token" in kwargs:
                tok = kwargs.pop("use_auth_token")
                kwargs.setdefault("token", tok)
            return orig(*args, **kwargs)
        shim._uat_shim = True
        return shim

    for name in ("hf_hub_download", "snapshot_download"):
        orig = getattr(_hf, name, None)
        if orig is None:
            continue
        shim = _wrap(orig)
        setattr(_hf, name, shim)                       # future imports get the shim
        for mod in list(_sys.modules.values()):        # rebind already-imported refs
            try:
                if getattr(mod, name, None) is orig:
                    setattr(mod, name, shim)
            except Exception:
                pass


def patch_whisperx():
    """Патчит whisperx.asr для работы с новым API faster-whisper"""
    try:
        import whisperx.asr as asr_module
        import faster_whisper.transcribe

        # Сохраняем оригинальную функцию
        original_load_model = asr_module.load_model

        def patched_load_model(
            whisper_arch,
            device,
            device_index=0,
            compute_type="float16",
            asr_options=None,
            language=None,
            vad_model_fp=None,
            vad_options=None,
            model=None,
            task="transcribe",
            download_root=None,
            threads=4
        ):
            """Патченная версия load_model с фиксом TranscriptionOptions"""
            import torch
            from whisperx.vad import load_vad_model
            from whisperx.asr import FasterWhisperPipeline

            if model is None:
                # CRITICAL: use whisperX's OWN WhisperModel subclass (whisperx.asr),
                # NOT faster_whisper.WhisperModel. Only the subclass defines
                # generate_segment_batched, which FasterWhisperPipeline.transcribe
                # calls; instantiating the base class made transcription fail with
                # "'WhisperModel' object has no attribute 'generate_segment_batched'".
                from whisperx.asr import WhisperModel
                model = WhisperModel(
                    whisper_arch,
                    device=device,
                    device_index=device_index,
                    compute_type=compute_type,
                    cpu_threads=threads,
                    download_root=download_root
                )

            default_asr_options = {
                "beam_size": 5,
                "best_of": 5,
                "patience": 1,
                "length_penalty": 1,
                "repetition_penalty": 1,
                "no_repeat_ngram_size": 0,
                "temperatures": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                "compression_ratio_threshold": 2.4,
                "log_prob_threshold": -1.0,
                "no_speech_threshold": 0.6,
                "condition_on_previous_text": False,
                "prompt_reset_on_temperature": 0.5,
                "initial_prompt": None,
                "prefix": None,
                "suppress_blank": True,
                "suppress_tokens": [-1],
                "without_timestamps": True,
                "max_initial_timestamp": 0.0,
                "word_timestamps": False,
                "prepend_punctuations": "\"'([{-",
                "append_punctuations": "\"'.,:)]}",
                "suppress_numerals": False,
                # Новые параметры для faster-whisper 1.0.3+
                "max_new_tokens": None,
                "clip_timestamps": "0",
                "hallucination_silence_threshold": None,
                "hotwords": None,
                "multilingual": False
            }

            if asr_options is not None:
                default_asr_options.update(asr_options)

            suppress_numerals = default_asr_options.pop("suppress_numerals", False)

            # Build TranscriptionOptions with ONLY the fields THIS faster-whisper
            # version declares. The old hardcoded set included keys (e.g.
            # 'multilingual') that faster-whisper 1.0.3 doesn't accept, so it fell
            # back to options=None and whisperX silently degraded. Filtering by the
            # real _fields keeps it working across faster-whisper versions.
            valid_fields = set(faster_whisper.transcribe.TranscriptionOptions._fields)
            filtered = {k: v for k, v in default_asr_options.items() if k in valid_fields}
            missing = valid_fields - set(filtered)
            if missing:
                print(f"Warning: TranscriptionOptions missing {sorted(missing)}", file=sys.stderr)
            default_asr_options = faster_whisper.transcribe.TranscriptionOptions(**filtered)

            default_vad_options = {
                "vad_onset": 0.500,
                "vad_offset": 0.363
            }

            if vad_options is not None:
                default_vad_options.update(vad_options)

            # VAD runs on CPU even when ASR is on CUDA: whisperX 3.1.1's VAD merge
            # calls .numpy() on the VAD output, which raises "can't convert cuda:0
            # tensor to numpy" if VAD is on GPU. CPU VAD (tiny, fast) avoids that and
            # keeps segmentation quality (the doc's alternative was disabling VAD).
            vad_device = torch.device("cpu")
            # PyTorch 2.6+ defaults torch.load to the safe weights-only mode.
            # The trusted WhisperX VAD checkpoint contains a small set of
            # OmegaConf/pyannote metadata classes, allowlisted only while that
            # fixed checkpoint is loaded.
            safe_globals = getattr(torch.serialization, "safe_globals", None)
            if safe_globals is None:
                from contextlib import nullcontext
                load_scope = nullcontext()
            else:
                load_scope = safe_globals(whisperx_vad_safe_globals())
            with load_scope:
                if vad_model_fp is not None:
                    vad_model = load_vad_model(
                        vad_device, use_auth_token=None,
                        **default_vad_options, model_fp=vad_model_fp)
                else:
                    vad_model = load_vad_model(
                        vad_device, use_auth_token=None, **default_vad_options)

            return FasterWhisperPipeline(
                model=model,
                vad=vad_model,
                options=default_asr_options,
                tokenizer=None,
                device=device,
                framework="pt",
                language=language,
                suppress_numerals=suppress_numerals,
                vad_params=default_vad_options,
            )

        # Применяем патч. КРИТИЧНО: патчим НЕ ТОЛЬКО asr.load_model, но и
        # верхнеуровневый re-export whisperx.load_model. whisperx/__init__.py делает
        # `from .asr import load_model`, поэтому whisperx.load_model — ОТДЕЛЬНАЯ
        # ссылка на оригинал, и движок зовёт именно её. Без этой строки патч не
        # действовал и whisperX падал с TranscriptionOptions (диаризация не работала).
        import whisperx as _wx
        asr_module.load_model = patched_load_model
        _wx.load_model = patched_load_model

        # transformers 5.x no longer exposes sampling_rate directly on
        # Wav2Vec2Processor; WhisperX 3.1.1 still reads that compatibility
        # attribute during alignment. Delegate it to the feature extractor.
        import whisperx.alignment as _alignment
        processor_cls = _alignment.Wav2Vec2Processor
        if not hasattr(processor_cls, "sampling_rate"):
            processor_cls.sampling_rate = property(
                lambda self: self.feature_extractor.sampling_rate)

        # CUDA fix: the transformers Pipeline moves the mel features to the ASR
        # device (cuda), but whisperX.encode -> faster_whisper.get_ctranslate2_storage
        # -> np.ascontiguousarray() needs CPU/numpy, so on GPU it raised "can't
        # convert cuda:0 tensor to numpy". Move features to CPU before encode
        # (ctranslate2 manages the GPU itself and takes CPU/numpy input).
        _orig_encode = asr_module.WhisperModel.encode
        def _encode_cpu(self, features):
            try:
                if hasattr(features, "is_cuda") and features.is_cuda:
                    features = features.cpu()
            except Exception:
                pass
            return _orig_encode(self, features)
        asr_module.WhisperModel.encode = _encode_cpu

        _patch_hf_use_auth_token()

        print("✓ WhisperX patched successfully "
              "(asr + top-level + cuda-encode + alignment + hf-token)",
              file=sys.stderr)
        return True

    except Exception as e:
        print(f"✗ Failed to patch WhisperX: {e}", file=sys.stderr)
        return False


if __name__ == '__main__':
    patch_whisperx()
