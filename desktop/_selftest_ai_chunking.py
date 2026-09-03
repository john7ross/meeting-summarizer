"""ai_client: map-reduce chunking + flexible timeout + no-think — no live model.

The provider call (_dispatch) is monkeypatched so we verify the ORCHESTRATION
(split → per-part summary → reduce) and the request-shaping flags without any HTTP.

Run: backend\\python\\python.exe desktop\\_selftest_ai_chunking.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import ai_client as A

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))

# ── splitter ─────────────────────────────────────────────────────────────────
text = "".join(f"[00:{i:02d}:00] line number {i} some words here\n" for i in range(200))
parts = A._split_text_by_lines(text, 500, 80)
check("split_multiple_parts", len(parts) > 3, str(len(parts)))
check("no_line_broken", all(p.endswith("\n") for p in parts))
check("parts_within_size_ish", all(len(p) <= 500 + 120 for p in parts),
      str(max(len(p) for p in parts)))
# overlap: some content of a part reappears at the start of the next
check("overlap_present", any(parts[i].splitlines()[-1] in parts[i + 1]
                             for i in range(len(parts) - 1)))
check("short_text_one_part", len(A._split_text_by_lines("a\nb\n", 500, 80)) == 1)

# ── ctor flags ───────────────────────────────────────────────────────────────
c = A.AIClient("local", "", "", "P", timeout=1800, no_think=True, chunk_chars=1000)
check("timeout_set", c.timeout == 1800)
check("nothink_flag", c.no_think is True)
check("chunk_chars_set", c.chunk_chars == 1000)
check("timeout_default", A.AIClient("local", "", "", "P").timeout == 600)
check("chunk_default", A.AIClient("local", "", "", "P").chunk_chars == A.CHUNK_CHARS)
check("nothink_body_on", c._nothink_body() == {"chat_template_kwargs": {"enable_thinking": False}})
check("nothink_body_off", A.AIClient("local", "", "", "P")._nothink_body() == {})

# ── map-reduce orchestration (monkeypatch _dispatch) ─────────────────────────
calls = []
def fake_dispatch(self, t):
    calls.append({"prompt": self.prompt, "len": len(t)})
    return f"SUMMARY({len(t)})"
A.AIClient._dispatch = fake_dispatch

# chunk_chars=8000 keeps the combined partials under the threshold -> one reduce pass
long_text = "".join(f"[00:{i:02d}:00] discussion point {i}\n" for i in range(2000))
client = A.AIClient("local", "", "", "USER-PROMPT", chunk_chars=8000)
out = client.generate_summary(long_text)
n_parts = len(A._split_text_by_lines(long_text, 8000, A.CHUNK_OVERLAP))
check("chunked_called_parts_plus_reduce", len(calls) == n_parts + 1,
      f"calls={len(calls)} parts={n_parts}")
check("partial_prompt_used", all("ЧАСТЬ" in c["prompt"] for c in calls[:-1]))
check("reduce_uses_user_prompt", calls[-1]["prompt"] == "USER-PROMPT")
check("client_prompt_restored", client.prompt == "USER-PROMPT")
check("returns_reduce_output", out == f"SUMMARY({calls[-1]['len']})")
# recursion path: a tiny threshold forces a recursive reduce (combined still huge)
calls.clear()
A.AIClient("local", "", "", "UP", chunk_chars=1500).generate_summary(long_text)
check("recursive_reduce_terminates", len(calls) > n_parts and calls[-1]["prompt"] == "UP",
      str(len(calls)))

# short text -> single dispatch, no chunking
calls.clear()
client2 = A.AIClient("local", "", "", "USER-PROMPT", chunk_chars=100000)
client2.generate_summary("[00:00:00] short meeting\n")
check("short_single_dispatch", len(calls) == 1 and calls[0]["prompt"] == "USER-PROMPT")

# ── uniform chunk mode (analysis): EVERY part uses the feature prompt ─────────
calls.clear()
cu = A.AIClient("local", "", "", "FEATURE-PROMPT", chunk_chars=8000, chunk_mode="uniform")
cu.generate_summary(long_text)
check("uniform_all_parts_feature_prompt", all(c["prompt"] == "FEATURE-PROMPT" for c in calls),
      str(set(c["prompt"] for c in calls)))
check("uniform_mode_stored", cu.chunk_mode == "uniform")

# ── chunking DISABLED (opt-out): long text goes whole in ONE dispatch ─────────
calls.clear()
cd = A.AIClient("local", "", "", "P", chunk_chars=8000, chunk_enabled=False)
cd.generate_summary(long_text)   # far exceeds chunk_chars, but chunking is off
check("no_chunk_single_dispatch", len(calls) == 1, str(len(calls)))
check("chunk_enabled_default_true", A.AIClient("local", "", "", "P").chunk_enabled is True)

# ── retry on local connection failure (watchdog restart) ─────────────────────
import types
posts = {"n": 0}
def boom(*a, **k):
    posts["n"] += 1
    raise A.requests.exceptions.ConnectionError("refused")
A.requests.post = boom
A.time.sleep = lambda s: None          # don't actually wait
cr = A.AIClient.__new__(A.AIClient)     # bypass monkeypatched _dispatch
A.AIClient.__init__(cr, "local", "", "http://127.0.0.1:8080/v1", "P", retries=2, retry_delay=1)
try:
    cr._call_local("hi"); raised = False
except Exception as e:
    raised = "Cannot connect" in str(e)
check("retry_attempts_count", posts["n"] == 3, str(posts["n"]))   # 1 + 2 retries
check("retry_raises_after_exhaust", raised)
check("retries_stored", cr.retries == 2)

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}"); sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)"); sys.exit(0)
