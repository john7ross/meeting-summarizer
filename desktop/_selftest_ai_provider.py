"""AI provider selection + advanced-config wiring (no network).

Verifies the reworked ai_client.py: --model selection, OpenAI-compatible base-URL
override, the Advanced-API templated path, google model in URL (no dead
gemini-pro), and that summarization/analysis build_command forward --model /
--advanced. requests.post is monkeypatched so nothing hits the network.

Run:
    backend\\python\\python.exe desktop\\_selftest_ai_provider.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

import ai_client
from desktop.app.backend import summarization as S
from desktop.app.backend import analysis as A

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))


# ── pure helpers ─────────────────────────────────────────────────────────────
sub = ai_client._substitute(
    {"model": "{{model}}", "messages": [{"content": "{{prompt}}"}, {"content": "{{text}}"}],
     "temp": 0.7},
    {"{{model}}": "m1", "{{prompt}}": "P", "{{text}}": "T", "{{apiKey}}": "K"})
check("substitute_model", sub["model"] == "m1")
check("substitute_nested", sub["messages"][1]["content"] == "T")
check("substitute_keeps_nonstr", sub["temp"] == 0.7)
check("extract_openai", ai_client._extract_text(
    {"choices": [{"message": {"content": "hi"}}]}) == "hi")
check("extract_anthropic", ai_client._extract_text(
    {"content": [{"text": "yo"}]}) == "yo")
check("extract_google", ai_client._extract_text(
    {"candidates": [{"content": {"parts": [{"text": "gg"}]}}]}) == "gg")
check("google_default_not_pro", ai_client.DEFAULT_MODELS["google"] != "gemini-pro")

# ── monkeypatch requests.post to capture calls ───────────────────────────────
calls = []
class _Resp:
    status_code = 200
    text = "{}"
    headers = {}
    def raise_for_status(self): pass
    def json(self):
        return {"choices": [{"message": {"content": "OK-SUMMARY"}}],
                "content": [{"text": "OK-SUMMARY"}],
                "candidates": [{"content": {"parts": [{"text": "OK-SUMMARY"}]}}]}
def _fake_post(url, **kw):
    calls.append({"url": url, "json": kw.get("json"), "headers": kw.get("headers")})
    return _Resp()
ai_client.requests.post = _fake_post

def last():
    return calls[-1]

# openai with explicit model
ai_client.AIClient("openai", "KEY", "", "PROMPT", model="gpt-4o-mini").generate_summary("TXT")
check("openai_url", last()["url"] == "https://api.openai.com/v1/chat/completions", last()["url"])
check("openai_model", last()["json"]["model"] == "gpt-4o-mini")
check("openai_auth", last()["headers"]["Authorization"] == "Bearer KEY")

# openai-compatible base-URL override (gateway/proxy)
ai_client.AIClient("openai", "K", "https://gw.local/v1", "P", model="m").generate_summary("T")
check("openai_endpoint_override", last()["url"] == "https://gw.local/v1/chat/completions", last()["url"])

# deepseek shares the compatible path
ai_client.AIClient("deepseek", "K", "", "P", model="deepseek-chat").generate_summary("T")
check("deepseek_url", last()["url"] == "https://api.deepseek.com/v1/chat/completions", last()["url"])

# google uses the chosen model in the URL (not gemini-pro)
ai_client.AIClient("google", "GKEY", "", "P", model="gemini-1.5-pro").generate_summary("T")
check("google_model_in_url", "models/gemini-1.5-pro:generateContent" in last()["url"], last()["url"])
check("google_no_pro", "gemini-pro:" not in last()["url"])

# Google default is current and disable-reasoning reaches provider-native
# thinking controls (2.5 budget=0, 3.x minimal level).
ai_client.AIClient("google", "GKEY", "", "P", no_think=True).generate_summary("T")
check("google_current_default", "models/gemini-2.5-flash:generateContent" in last()["url"])
check("google_25_no_think",
      last()["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0})
ai_client.AIClient("google", "GKEY", "", "P", model="gemini-3.5-flash",
                   no_think=True).generate_summary("T")
check("google_3_no_think",
      last()["json"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"})

# advanced config wins: custom endpoint + templated body
adv = {"endpoint": "https://custom.ai/v1/chat", "model": "custom-1",
       "headers": {"Authorization": "Bearer {{apiKey}}", "Content-Type": "application/json"},
       "body": {"model": "{{model}}", "messages": [
           {"role": "system", "content": "{{prompt}}"}, {"role": "user", "content": "{{text}}"}]}}
ai_client.AIClient("openai", "AKEY", "", "SYS", model="", advanced=adv).generate_summary("BODY")
check("advanced_url", last()["url"] == "https://custom.ai/v1/chat", last()["url"])
check("advanced_header_subst", last()["headers"]["Authorization"] == "Bearer AKEY")
check("advanced_body_model", last()["json"]["model"] == "custom-1", str(last()["json"].get("model")))
check("advanced_body_text", last()["json"]["messages"][1]["content"] == "BODY")

# ── build_command forwards model + advanced ──────────────────────────────────
cmd = S.build_command("P", "t.txt", provider="openai", api_key="K",
                      model="gpt-4o-mini", advanced={"endpoint": "x"},
                      python_exe="py", ai_client_script="ai.py")
check("build_has_model", "--model" in cmd and "gpt-4o-mini" in cmd)
check("build_has_advanced",
      '"endpoint"' in cmd.environment.get("MEETING_SUMMARIZER_ADVANCED", ""))
check("build_hides_api_key",
      "K" not in cmd and cmd.environment.get("MEETING_SUMMARIZER_API_KEY") == "K")
check("build_hides_prompt",
      "P" not in cmd and cmd.environment.get("MEETING_SUMMARIZER_PROMPT") == "P")

acmd = A.build_feature_command("actionItems", "t.txt", {"transcriptionLanguage": "ru"},
                               provider="openai", model="gpt-4o", advanced={"endpoint": "y"},
                               python_exe="py", ai_client_script="ai.py")
check("analysis_has_model", "--model" in acmd and "gpt-4o" in acmd)
check("analysis_has_advanced",
      '"endpoint"' in acmd.environment.get("MEETING_SUMMARIZER_ADVANCED", ""))
check("analysis_schema_accepts_empty_list",
      A.is_valid_feature_result("risks", []))
check("analysis_schema_rejects_object_fallback_list",
      not A.is_valid_feature_result("category", []))
check("analysis_schema_accepts_object",
      A.is_valid_feature_result("category", {"category": "Статус"}))

# Cloud generation rate limits are retried using the provider's wait hint.
retry_calls, retry_sleeps = [], []
class _RetryResp(_Resp):
    def __init__(self, status, text="{}"):
        self.status_code, self.text, self.headers = status, text, {}
    def raise_for_status(self):
        if self.status_code >= 400:
            raise ai_client.requests.exceptions.HTTPError(response=self)

def _retry_post(url, **kw):
    retry_calls.append(url)
    return (_RetryResp(429, '{"retryDelay":"2s"}')
            if len(retry_calls) == 1 else _RetryResp(200))

ai_client.requests.post = _retry_post
original_sleep = ai_client.time.sleep
ai_client.time.sleep = retry_sleeps.append
try:
    recovered = ai_client.AIClient(
        "google", "GKEY", "", "P", retries=1, retry_delay=60
    ).generate_summary("T")
finally:
    ai_client.time.sleep = original_sleep
check("cloud_429_retried", recovered == "OK-SUMMARY" and len(retry_calls) == 2)
check("cloud_retry_honors_hint", retry_sleeps == [2], str(retry_sleeps))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
