"""Offline speaker diarization via sherpa-onnx (ungated — no HF token).

Distribution-friendly alternative to pyannote (whose models are HF-gated: every
end user would need an account + token + accepting terms). This uses the freely
downloadable ONNX models from the k2-fsa sherpa-onnx releases, run through the
already-installed ``sherpa_onnx.OfflineSpeakerDiarization``. Returns speaker
segments that the whisper-family engines assign to their transcript segments.
"""
import os
import sys
import tarfile

# Freely downloadable (ungated) models from the k2-fsa sherpa-onnx releases.
SEG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMB_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx")
SEG_DIR = "sherpa-onnx-pyannote-segmentation-3-0"
EMB_FILE = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"


def models_dir(res_dir=None) -> str:
    here = os.path.dirname(os.path.abspath(__file__))          # backend/processing
    root = res_dir or os.path.normpath(os.path.join(here, "..", "..", "resources"))
    return os.path.join(root, "diarization_models")


def seg_model_path(res_dir=None):
    p = os.path.join(models_dir(res_dir), SEG_DIR, "model.onnx")
    return p if os.path.isfile(p) else None


def emb_model_path(res_dir=None):
    p = os.path.join(models_dir(res_dir), EMB_FILE)
    return p if os.path.isfile(p) else None


def is_available(res_dir=None) -> bool:
    return bool(seg_model_path(res_dir) and emb_model_path(res_dir))


def download(res_dir=None, on_progress=None):
    """Fetch the ungated segmentation (.tar.bz2) + embedding (.onnx) models.
    Returns ``(seg_path, emb_path)``. Skips files already present."""
    on_progress = on_progress or (lambda *a: None)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/
    from download_model import _http_download_resume

    root = models_dir(res_dir)
    os.makedirs(root, exist_ok=True)
    if not seg_model_path(res_dir):
        archive = os.path.join(root, SEG_DIR + ".tar.bz2")
        on_progress(2, "Downloading segmentation model…")
        _http_download_resume(SEG_URL, archive, on_progress)
        on_progress(45, "Extracting…")
        with tarfile.open(archive, "r:bz2") as tf:
            tf.extractall(root)
        try:
            os.remove(archive)
        except OSError:
            pass
    if not emb_model_path(res_dir):
        on_progress(55, "Downloading speaker-embedding model…")
        _http_download_resume(EMB_URL, os.path.join(root, EMB_FILE), on_progress)
    on_progress(100, "done")
    return seg_model_path(res_dir), emb_model_path(res_dir)


def diarize(audio_samples, sample_rate, num_speakers=-1, threshold=0.5, res_dir=None):
    """Diarise float32 mono @16k samples → ``[(start_sec, end_sec, 'SPEAKER_NN')]``.

    ``num_speakers`` -1 = auto-detect via ``threshold`` (good for meetings where the
    count is unknown); a positive int forces that many speakers. Raises if the
    models are not downloaded or sherpa-onnx is missing.
    """
    import sherpa_onnx
    seg = seg_model_path(res_dir)
    emb = emb_model_path(res_dir)
    if not (seg and emb):
        raise RuntimeError(
            "diarization models are not downloaded (resources/diarization_models/)")
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=seg)),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=emb),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=int(num_speakers), threshold=float(threshold)),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    result = sd.process(audio_samples).sort_by_start_time()
    return [(float(r.start), float(r.end), f"SPEAKER_{int(r.speaker):02d}") for r in result]
