"""Headless self-test for the engine-aware Settings UI (TODO #14b).

Hermetic: a fake catalog is injected, so no subprocess / network is touched.
Verifies the catalog-driven engine + model selectors, the availability
indicator, Download/Check-update button gating, and that a non-implemented
engine is listed-but-disabled and ungated. Run with QT_QPA_PLATFORM=offscreen.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication  # noqa: E402
from app.ui.settings_dialog import SettingsDialog, _combo_val  # noqa: E402

CATALOG = {"engines": [
    {"id": "whisper", "label": {"ru": "OpenAI Whisper", "en": "OpenAI Whisper"},
     "implemented": True, "default_model": "medium", "models": [
        {"id": "tiny", "label": {"ru": "Tiny", "en": "Tiny"}, "available": True},
        {"id": "small", "label": {"ru": "Small", "en": "Small"}, "available": False},
        {"id": "medium", "label": {"ru": "Medium", "en": "Medium"}, "available": True}]},
    {"id": "faster-whisper", "label": {"ru": "Faster", "en": "Faster"},
     "implemented": True, "default_model": "medium", "models": [
        {"id": "medium", "label": {"ru": "Medium", "en": "Medium"}, "available": True}]},
    {"id": "whisperx", "label": {"ru": "WhisperX", "en": "WhisperX"},
     "implemented": True, "default_model": "medium", "models": [
        {"id": "medium", "label": {"ru": "Medium", "en": "Medium"}, "available": True}]},
    {"id": "vosk", "label": {"ru": "Vosk", "en": "Vosk"},
     "implemented": True, "default_model": "vosk-model-small-ru-0.22", "models": [
        {"id": "vosk-model-small-ru-0.22", "label": {"ru": "RU small", "en": "RU small"}, "available": True},
        {"id": "vosk-model-ru-0.22", "label": {"ru": "RU large", "en": "RU large"}, "available": False}]},
    {"id": "sherpa-onnx", "label": {"ru": "sherpa-onnx", "en": "sherpa-onnx"},
     "implemented": False, "default_model": None, "models": []},
]}

results = []
def check(name, ok, detail=""):
    results.append((f"PASS  {name}" if ok else f"FAIL  {name}  {detail}").rstrip())

app = QApplication.instance() or QApplication(sys.argv)

try:
    settings = {"transcriptionEngine": "whisper", "whisperModel": "medium",
                "transcriptionLanguage": "ru"}
    dlg = SettingsDialog(settings, language="ru", catalog=CATALOG,
                         python_exe="x", models_cli_script="y")

    # engine combo built from catalog; non-implemented engine disabled
    check("engine_count_5", dlg.cb_engine.count() == 5, str(dlg.cb_engine.count()))
    sidx = dlg.cb_engine.findData("sherpa-onnx")
    check("sherpa_item_disabled", not dlg.cb_engine.model().item(sidx).isEnabled())

    # initial load: whisper/medium is available
    check("init_model_medium", _combo_val(dlg.cb_model) == "medium", _combo_val(dlg.cb_model))
    check("init_status_installed", dlg.lbl_model_status.text() == dlg._t("st_installed"),
          dlg.lbl_model_status.text())
    check("init_download_disabled", not dlg.btn_download.isEnabled())
    check("init_check_enabled", dlg.btn_check.isEnabled())

    # select a missing model -> download enabled, check disabled
    dlg.cb_model.setCurrentIndex(dlg.cb_model.findData("small"))
    check("small_status_missing", dlg.lbl_model_status.text() == dlg._t("st_missing"))
    check("small_download_enabled", dlg.btn_download.isEnabled())
    check("small_check_disabled", not dlg.btn_check.isEnabled())

    # switch engine to vosk -> models repopulate to vosk names
    dlg.cb_engine.setCurrentIndex(dlg.cb_engine.findData("vosk"))
    ids = {dlg.cb_model.itemData(i) for i in range(dlg.cb_model.count())}
    check("vosk_models_listed", ids == {"vosk-model-small-ru-0.22", "vosk-model-ru-0.22"}, str(ids))
    dlg.cb_model.setCurrentIndex(dlg.cb_model.findData("vosk-model-ru-0.22"))
    check("vosk_large_download_enabled", dlg.btn_download.isEnabled())
    dlg.cb_model.setCurrentIndex(dlg.cb_model.findData("vosk-model-small-ru-0.22"))
    check("vosk_small_check_enabled", dlg.btn_check.isEnabled())

    # non-implemented engine: status + everything gated off
    dlg.cb_engine.setCurrentIndex(dlg.cb_engine.findData("sherpa-onnx"))
    check("sherpa_status_noadapter", dlg.lbl_model_status.text() == dlg._t("st_noadapter"))
    check("sherpa_download_disabled", not dlg.btn_download.isEnabled())
    check("sherpa_check_disabled", not dlg.btn_check.isEnabled())
    check("sherpa_model_disabled", not dlg.cb_model.isEnabled())

    # collect reflects the active selection
    dlg.cb_engine.setCurrentIndex(dlg.cb_engine.findData("faster-whisper"))
    dlg.cb_model.setCurrentIndex(dlg.cb_model.findData("medium"))
    got = dlg._collect()
    check("collect_engine", got["transcriptionEngine"] == "faster-whisper")
    check("collect_model", got["whisperModel"] == "medium")
except Exception as exc:  # noqa: BLE001
    results.append(f"FAIL  harness  {exc!r}")
    results.append("      " + traceback.format_exc().replace("\n", "\n      "))

print("\n".join(results))
ok_all = bool(results) and all(r.startswith("PASS") for r in results)
print("SUMMARY " + (f"ALL_PASS ({len(results)} checks)" if ok_all else "HAS_FAILURES"))
sys.stdout.flush()
sys.stderr.flush()
os._exit(0 if ok_all else 1)
