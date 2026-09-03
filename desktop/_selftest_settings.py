"""Headless self-test for the settings dialog. Verifies load->widgets,
conditional visibility, collect + atomic save round-trip (to a TEMP path, never
the real settings.json), unknown-key preservation, and the Advanced API modal's
JSON handling. Run with QT_QPA_PLATFORM=offscreen.
"""
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import py_compile  # noqa: E402
for f in ["app/ui/settings_dialog.py", "app/ui/main_window.py"]:
    py_compile.compile(str(Path(__file__).resolve().parent / f), doraise=True)

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config  # noqa: E402
from app.ui.settings_dialog import (  # noqa: E402
    AGENT_PRESETS, AdvancedApiDialog, SettingsDialog, _combo_set, _combo_val)

results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


app = QApplication.instance() or QApplication(sys.argv)

check("quality_defaults",
      config.DEFAULTS["analysisSource"] == "transcript"
      and config.DEFAULTS["chunkingEnabled"] is False,
      f"{config.DEFAULTS['analysisSource']}/{config.DEFAULTS['chunkingEnabled']}")
with tempfile.TemporaryDirectory() as migration_tmp:
    migration_path = Path(migration_tmp) / "settings.json"
    legacy_commands = {
        "hermes {prompt}": config.HERMES_AGENT_COMMAND,
        "claude -p {prompt}": config.CLAUDE_AGENT_COMMAND,
        "codex exec --sandbox read-only --skip-git-repo-check {prompt}":
            config.CODEX_AGENT_COMMAND,
        "gemini -p {prompt}": config.GEMINI_AGENT_COMMAND,
        ('gemini --skip-trust -p "Follow the system instructions and process the '
         'meeting transcript provided on stdin. Return only the requested answer."'):
            config.GEMINI_AGENT_COMMAND,
        ('gemini --skip-trust -p "Read the system instructions from {prompt_file} and '
         'the meeting transcript from {text_file}. Follow the system instructions '
         'exactly and return only the requested answer."'):
            config.GEMINI_AGENT_COMMAND,
    }
    migrated_ok = True
    for old_command, new_command in legacy_commands.items():
        import json
        migration_path.write_text(
            json.dumps({"agentCommand": old_command}), encoding="utf-8")
        migrated_ok = (
            migrated_ok
            and config.load_settings(migration_path)["agentCommand"] == new_command
        )
check("legacy_agent_presets_migrated", migrated_ok)
check("agent_presets_keep_long_content_off_argv",
      all("{prompt}" not in command for command, _label in AGENT_PRESETS))
check("gemini_preset_uses_utf8_stdin",
      "stdin" in config.GEMINI_AGENT_COMMAND
      and "{prompt}" not in config.GEMINI_AGENT_COMMAND
      and "{text_file}" not in config.GEMINI_AGENT_COMMAND)

settings = {
    "transcriptionEngine": "whisperx", "whisperModel": "large",
    "transcriptionLanguage": "en", "outputLanguage": "ru",
    "whisperDevice": "cpu", "parallelWorkers": "3",
    "aiProvider": "openai", "aiModel": "gpt-4o-mini", "apiKey": "sk-secret",
    "localEndpoint": "http://localhost:1234/v1", "useSpeakerPrompt": False,
    "agentCommand": "custom-agent {prompt_file} {text_file}", "agentCwd": "C:/agent",
    "analysisSource": "transcript", "chunkingEnabled": False, "chunkChars": 64000,
    "disableReasoning": False, "aiTimeout": 1800, "aiRetries": 4,
    "aiRetryDelay": 75, "gpuHandoff": True, "llamaPort": 9090,
    "youtubeCookiesBrowser": "edge",
    "prompt": "PROMPT-X", "enableMarkdownExport": False, "obsidianIntegration": True,
    "obsidianVaultPath": "V:/vault", "updateMeetingIndex": False,
    "createPeopleNotes": False, "createTopicNotes": True, "createDataviewQueries": False,
    "googleSheetsIntegration": True, "googleSheetsUrl": "http://s",
    "googleSheetsToken": "shared-secret", "googleApiKey": "gk",
    "ragCatalogMode": "shared",
    "ragSharedCatalogKey": "rsc_0123456789012345678901234567890123456789012",
    "extractActionItems": False, "analyzeSentiment": True,
    "categorizeAutomatically": False, "generateFollowupQuestions": True,
    "generateFormalProtocol": False, "useContextualMemory": True, "projectId": "proj-7",
    "theme": "light", "language": "en",          # owned elsewhere -> must survive
    "advancedSettings": {"openai": {"endpoint": "http://old", "model": "gpt-4o"}},
    "UNKNOWN_KEY": {"keep": 123},                 # must survive round-trip
}

try:
    # ---- load + visibility (this instance gets mutated freely) ----
    dlg = SettingsDialog(dict(settings), language="ru")
    check("short_display_keeps_actions_reachable", dlg.minimumHeight() <= 480,
          str(dlg.minimumHeight()))
    check("load_combo", _combo_val(dlg.cb_engine) == "whisperx"
          and _combo_val(dlg.cb_provider) == "openai")
    check("load_text", dlg.ed_prompt.toPlainText() == "PROMPT-X"
          and dlg.ed_apikey.text() == "sk-secret")
    check("load_ai_model", dlg.cb_ai_model.currentText() == "gpt-4o-mini",
          dlg.cb_ai_model.currentText())
    check("hint_default_empty", dlg.ed_hint.text() == "", dlg.ed_hint.text())
    check("diar_default_sherpa", _combo_val(dlg.cb_diar) == "sherpa", _combo_val(dlg.cb_diar))
    # templates: selector populated (custom + 12 built-ins), selecting one fills the prompt
    check("tpl_selector_populated", dlg.cb_template.count() >= 13, str(dlg.cb_template.count()))
    _si = next((i for i in range(dlg.cb_template.count())
                if (dlg.cb_template.itemData(i) or {}).get("id") == "standup"), -1)
    dlg._on_template_selected(_si)
    # The prompt is rendered in the OUTPUT language (here 'auto' -> transcription
    # language 'en'), so accept the standup template in either language.
    _p = dlg.ed_prompt.toPlainText().lower()
    check("tpl_applies_prompt", ("стендап" in _p) or ("standup" in _p),
          dlg.ed_prompt.toPlainText()[:60])
    check("tpl_buttons_enabled", dlg.btn_save_tpl.isEnabled() and dlg.btn_manage_tpl.isEnabled())
    check("load_checks", dlg.chk_sentiment.isChecked() and not dlg.chk_action.isChecked())
    check("load_workers", _combo_val(dlg.cb_workers) == "3")
    check("load_processing_controls",
          _combo_val(dlg.cb_output_lang) == "ru"
          and _combo_val(dlg.cb_analysis_source) == "transcript"
          and not dlg.chk_chunking.isChecked()
          and dlg.sp_chunk_chars.value() == 64000
          and not dlg.chk_reasoning.isChecked()
          and dlg.sp_timeout.value() == 1800
          and dlg.sp_retries.value() == 4
          and dlg.sp_retry_delay.value() == 75
          and dlg.chk_gpu.isChecked()
          and dlg.sp_llama_port.value() == 9090
          and _combo_val(dlg.cb_yt_cookies) == "edge")
    check("load_integrations",
          dlg.ed_sheets_url.text() == "http://s"
          and dlg.ed_sheets_token.text() == "shared-secret"
          and dlg.ed_vault.text() == "V:/vault")
    check("load_shared_rag",
          _combo_val(dlg.cb_rag_catalog_mode) == "shared"
          and dlg.ed_rag_shared_key.text() == settings["ragSharedCatalogKey"])
    check("vis_shared_rag_on", dlg.w_rag_shared_key.isVisibleTo(dlg))
    _combo_set(dlg.cb_rag_catalog_mode, "isolated")
    check("vis_shared_rag_off", not dlg.w_rag_shared_key.isVisibleTo(dlg))
    hermes = next((cmd for cmd, label in AGENT_PRESETS if label == "Hermes agent"), "")
    check("hermes_preset_file_safe",
          "hermes -z" in hermes
          and "{prompt_file}" in hermes and "{text_file}" in hermes,
          hermes)
    codex = next((cmd for cmd, label in AGENT_PRESETS if label == "Codex"), "")
    check("codex_preset_read_only_portable",
          "--sandbox read-only" in codex
          and "--skip-git-repo-check" in codex
          and codex.endswith(" -"),
          codex)

    check("vis_openai", dlg.w_apikey.isVisibleTo(dlg) and not dlg.w_endpoint.isVisibleTo(dlg))
    _combo_set(dlg.cb_provider, "local")
    check("vis_local", dlg.w_endpoint.isVisibleTo(dlg) and not dlg.w_apikey.isVisibleTo(dlg))
    _combo_set(dlg.cb_provider, "agent")
    check("vis_agent",
          dlg.w_agent_cmd.isVisibleTo(dlg) and dlg.w_agent_cwd.isVisibleTo(dlg)
          and not dlg.w_apikey.isVisibleTo(dlg) and not dlg.w_endpoint.isVisibleTo(dlg))
    check("vis_obsidian_on", dlg.w_obsidian_extra.isVisibleTo(dlg))
    dlg.chk_obsidian.setChecked(False)
    check("vis_obsidian_off", not dlg.w_obsidian_extra.isVisibleTo(dlg))
    check("vis_project_on", dlg.w_project_wrap.isVisibleTo(dlg))
    dlg.chk_memory.setChecked(False)
    check("vis_project_off", not dlg.w_project_wrap.isVisibleTo(dlg))

    # ---- collect + round-trip on a FRESH instance (provider stays openai) ----
    dlg2 = SettingsDialog(dict(settings), language="ru")
    dlg2.ed_prompt.setPlainText("NEW-PROMPT")
    dlg2.chk_action.setChecked(True)
    _combo_set(dlg2.cb_model, "small")
    dlg2.cb_ai_model.setCurrentText("custom-model-x")   # editable: free-text model id
    dlg2.ed_hint.setText("Kubernetes, gRPC")            # transcription vocabulary hint
    _combo_set(dlg2.cb_diar, "pyannote")
    dlg2.ed_hf_token.setText("hf_secret")
    dlg2.sp_chunk_chars.setValue(32000)
    dlg2.sp_timeout.setValue(900)
    dlg2.sp_retries.setValue(2)
    dlg2.sp_retry_delay.setValue(45)
    dlg2.sp_llama_port.setValue(8088)
    _combo_set(dlg2.cb_output_lang, "en")
    _combo_set(dlg2.cb_analysis_source, "summary")
    _combo_set(dlg2.cb_yt_cookies, "off")
    _combo_set(dlg2.cb_rag_catalog_mode, "shared")
    dlg2.ed_rag_shared_key.setText(
        "rsc_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN")
    updated = dlg2._collect()
    check("collect_hint", updated["transcriptionHint"] == "Kubernetes, gRPC", updated.get("transcriptionHint"))
    check("collect_diar", updated["diarizationBackend"] == "pyannote" and updated["hfToken"] == "hf_secret",
          str((updated.get("diarizationBackend"), updated.get("hfToken"))))
    check("collect_changes", updated["prompt"] == "NEW-PROMPT"
          and updated["extractActionItems"] is True and updated["whisperModel"] == "small")
    check("collect_ai_model", updated["aiModel"] == "custom-model-x", updated.get("aiModel"))
    check("collect_processing_controls",
          updated["chunkChars"] == 32000 and updated["aiTimeout"] == 900
          and updated["aiRetries"] == 2 and updated["aiRetryDelay"] == 45
          and updated["llamaPort"] == 8088 and updated["outputLanguage"] == "en"
          and updated["analysisSource"] == "summary"
          and updated["youtubeCookiesBrowser"] == "off")
    check("collect_shared_rag",
          updated["ragCatalogMode"] == "shared"
          and updated["ragSharedCatalogKey"].startswith("rsc_"))
    check("collect_preserves_unknown", updated.get("UNKNOWN_KEY") == {"keep": 123}
          and updated.get("theme") == "light" and updated.get("language") == "en")
    check("collect_preserves_adv", updated["advancedSettings"]["openai"]["model"] == "gpt-4o")

    tmp = Path(tempfile.mkdtemp()) / "settings.json"
    config.save_settings(updated, path=tmp)
    reloaded = config.load_settings(path=tmp)
    check("roundtrip", reloaded["prompt"] == "NEW-PROMPT"
          and reloaded["UNKNOWN_KEY"] == {"keep": 123} and reloaded["aiProvider"] == "openai")

    # ---- advanced api modal ----
    adv = AdvancedApiDialog({"endpoint": "http://x", "model": "m", "headers": {"A": "B"}},
                            language="ru")
    check("adv_load", adv.ed_endpoint.text() == "http://x" and "A" in adv.ed_headers.toPlainText())
    bad = False
    try:
        adv._parse_json_field("{not json", "h")
    except ValueError:
        bad = True
    check("adv_bad_json_raises", bad)
    check("adv_good_json", adv._parse_json_field('{"x":1}', "h") == {"x": 1}
          and adv._parse_json_field("", "h") is None)

    # ---- pending advanced edits flow into collect ----
    dlg2._adv_pending["openai"] = {"endpoint": "http://new", "model": "gpt-4o-mini",
                                   "headers": {"H": "1"}}
    u2 = dlg2._collect()
    check("adv_pending_merge", u2["advancedSettings"]["openai"]["endpoint"] == "http://new"
          and u2["advancedSettings"]["openai"]["model"] == "gpt-4o-mini")

    # The model dropdown must follow the transcription language. Offering every
    # model regardless lets a user run FunASR - which has NO Russian - on a
    # Russian meeting; it does not fail, it returns confident nonsense.
    dlg3 = SettingsDialog(dict(settings), language="ru")

    def _pick(combo, value):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return True
        return False

    def _offered(engine, lang):
        _pick(dlg3.cb_engine, engine)
        _pick(dlg3.cb_lang, lang)
        dlg3._repopulate_models()
        dlg3._refresh_model_status()
        return [dlg3.cb_model.itemData(i) for i in range(dlg3.cb_model.count())]

    ru_funasr = _offered("funasr", "ru")
    check("no_russian_model_for_an_english_only_engine", ru_funasr == [], str(ru_funasr))
    check("empty_model_list_explains_itself",
          "язык" in dlg3.lbl_model_status.text().lower()
          or "language" in dlg3.lbl_model_status.text().lower(),
          dlg3.lbl_model_status.text()[:60])
    check("download_disabled_when_nothing_fits", not dlg3.btn_download.isEnabled())

    en_funasr = _offered("funasr", "en")
    check("english_models_offered_for_english", len(en_funasr) >= 1, str(en_funasr))

    ru_vosk = _offered("vosk", "ru")
    en_vosk = _offered("vosk", "en")
    check("language_specific_engine_filtered_ru",
          bool(ru_vosk) and all("-ru-" in m for m in ru_vosk), str(ru_vosk))
    check("language_specific_engine_filtered_en",
          bool(en_vosk) and all("-en-" in m for m in en_vosk), str(en_vosk))

    multi = _offered("faster-whisper", "ru")
    check("multilingual_engine_keeps_every_model", len(multi) >= 4, str(multi))
except Exception as exc:  # noqa: BLE001
    results.append(f"FAIL  harness  {exc!r}")
    results.append("      " + traceback.format_exc().replace("\n", "\n      "))

print("\n".join(results))
ok_all = bool(results) and all(r.startswith("PASS") for r in results)
print("SUMMARY " + ("ALL_PASS" if ok_all else "HAS_FAILURES"))
# Bypass Qt's interpreter-shutdown teardown (offscreen can crash on exit and
# swallow output); flush first, then hard-exit with a clean code.
sys.stdout.flush()
sys.stderr.flush()
import os  # noqa: E402
os._exit(0 if ok_all else 1)
