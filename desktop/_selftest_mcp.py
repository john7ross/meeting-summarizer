"""Self-test for the MCP server (backend/mcp_server.py).

Drives the REAL server over its real transport — a subprocess speaking
newline-delimited JSON-RPC on stdin/stdout — against a temporary archive, so the
protocol contract is verified end to end (handshake, tools/list, tools/call,
error shape) rather than by calling functions directly.

    backend\\python\\python.exe desktop\\_selftest_mcp.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import paths                     # noqa: E402

SERVER = ROOT / "backend" / "mcp_server.py"
results = []


def check(name, ok, detail=""):
    results.append((f"PASS  {name}  {detail}" if ok else f"FAIL  {name}  {detail}").rstrip())


# -- temporary archive -------------------------------------------------
tmp = Path(tempfile.mkdtemp())
art = tmp / "meeting one"
art.mkdir(parents=True)
(tr := art / "m_raw.txt").write_text(
    "[00:00:01] Иван: Обсуждаем ЗЕБРА-ПРОЕКТ и сроки.\n"
    "[00:00:09] Мария: Беру на себя пилот в августе.\n", encoding="utf-8")
(sm := art / "m_summary.txt").write_text("Решили: пилот ЗЕБРА-ПРОЕКТ в августе, ответственная Мария.",
                                         encoding="utf-8")
(an := art / "m_analysis.json").write_text(json.dumps({
    "actionItems": [{"task": "Запустить пилот", "assignee": "Мария", "priority": "high"}],
    "risks": [{"description": "Мало ресурсов", "severity": "medium"}],
}, ensure_ascii=False), encoding="utf-8")

history = [{
    "id": 111, "videoName": "meeting one.mkv", "processedAt": "2026-07-21T10:00:00",
    "project": "alpha", "status": "done", "duration": "0:12:00", "folder": "meeting one",
    "transcriptPath": str(tr), "summaryPath": str(sm),
    "summaryVersions": [{"version": 1, "path": str(sm), "provider": "local"}],
    "analysisVersions": [{"version": 1, "path": str(an), "provider": "local"}],
}, {
    "id": 222, "videoName": "meeting two.mkv", "processedAt": "2026-07-21T12:00:00",
    "project": "beta", "status": "done", "transcriptPath": "", "summaryVersions": [],
    "analysisVersions": [],
}]
cfg = tmp / "config"
cfg.mkdir()
(cfg / "history.json").write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")

# Point the server at the temp archive by running it with a patched ROOT.
shim = tmp / "run_server.py"
shim.write_text(
    "import sys, pathlib\n"
    f"sys.path.insert(0, r'{ROOT / 'backend'}')\n"
    "import mcp_server as m\n"
    f"m.ROOT = pathlib.Path(r'{tmp}')\n"
    f"m.HISTORY_FILE = pathlib.Path(r'{cfg / 'history.json'}')\n"
    "raise SystemExit(m.main())\n", encoding="utf-8")


class Server:
    """Minimal MCP stdio client."""

    def __init__(self):
        self.p = subprocess.Popen(
            [str(paths.python_executable()), str(shim)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._id = 0

    def call(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        if notify:
            return None
        return json.loads(self.p.stdout.readline())

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:       # noqa: BLE001
            self.p.kill()


s = Server()
try:
    # -- handshake ------------------------------------------------------
    r = s.call("initialize", {"protocolVersion": "2024-11-05",
                              "clientInfo": {"name": "selftest", "version": "1"},
                              "capabilities": {}})
    res = r.get("result", {})
    check("initialize_ok", "protocolVersion" in res, str(res.get("protocolVersion")))
    check("initialize_declares_tools", "tools" in res.get("capabilities", {}))
    check("initialize_server_name", res.get("serverInfo", {}).get("name") == "meeting-summarizer",
          str(res.get("serverInfo")))
    check("echoes_client_protocol", res.get("protocolVersion") == "2024-11-05")
    s.call("notifications/initialized", notify=True)

    # -- tools/list -----------------------------------------------------
    r = s.call("tools/list")
    tools = r.get("result", {}).get("tools", [])
    names = {t["name"] for t in tools}
    check("tools_listed", len(tools) >= 6, f"{len(tools)}: {sorted(names)}")
    expected = {"list_meetings", "get_transcript", "get_summary", "get_analysis",
                "search_transcripts", "search_knowledge"}
    check("tools_expected_set", expected <= names, str(sorted(expected - names)))
    check("tools_have_schema", all("inputSchema" in t and "description" in t for t in tools))

    def call_tool(name, args=None):
        r = s.call("tools/call", {"name": name, "arguments": args or {}})
        out = r.get("result", {})
        text = "".join(c.get("text", "") for c in out.get("content", []))
        return text, bool(out.get("isError"))

    # -- list_meetings --------------------------------------------------
    text, err = call_tool("list_meetings")
    data = json.loads(text)
    check("list_meetings", not err and data["count"] == 2, text[:80])
    check("list_meetings_newest_first", data["meetings"][0]["id"] == 222,
          str(data["meetings"][0]["id"]))
    text, _ = call_tool("list_meetings", {"project": "alpha"})
    check("list_filter_project", json.loads(text)["count"] == 1)
    text, _ = call_tool("list_meetings", {"only_with_summary": True})
    check("list_filter_summary", json.loads(text)["count"] == 1)

    # -- content tools --------------------------------------------------
    text, err = call_tool("get_transcript", {"meeting_id": "111"})
    check("get_transcript", not err and "ЗЕБРА-ПРОЕКТ" in text, text[:60])
    text, _ = call_tool("get_transcript", {"meeting_id": "111", "max_chars": 20})
    check("transcript_truncation", "обрезано" in text, text[-40:])
    text, err = call_tool("get_summary", {"meeting_id": "111"})
    check("get_summary", not err and "пилот" in text, text[:60])
    text, err = call_tool("get_analysis", {"meeting_id": "111"})
    check("get_analysis", not err and "actionItems" in text)
    text, err = call_tool("get_analysis", {"meeting_id": "111", "feature": "risks"})
    parsed = json.loads(text)
    check("get_analysis_feature", set(parsed) == {"risks"}, str(list(parsed)))

    # -- search ---------------------------------------------------------
    text, err = call_tool("search_transcripts", {"query": "зебра-проект"})
    data = json.loads(text)
    check("search_case_insensitive", not err and data["count"] >= 1, text[:80])
    check("search_returns_meeting_id", data["hits"][0]["meeting_id"] == 111)

    # -- error handling -------------------------------------------------
    text, err = call_tool("get_summary", {"meeting_id": "999"})
    check("unknown_meeting_is_tool_error", err and "not found" in text.lower(), text[:60])
    text, err = call_tool("get_summary", {"meeting_id": "222"})
    check("missing_summary_is_tool_error", err and "no summary" in text.lower(), text[:60])
    text, err = call_tool("get_analysis", {"meeting_id": "111", "feature": "nope"})
    check("unknown_feature_is_tool_error", err and "unknown feature" in text.lower(), text[:60])
    text, err = call_tool("search_transcripts", {})
    check("missing_required_arg", err, text[:60])
    r = s.call("tools/call", {"name": "no_such_tool", "arguments": {}})
    check("unknown_tool_is_rpc_error", "error" in r, str(r)[:80])
    r = s.call("no/such/method")
    check("unknown_method_is_rpc_error", r.get("error", {}).get("code") == -32601, str(r)[:80])
    r = s.call("ping")
    check("ping", "result" in r)
finally:
    s.close()


# -- duration fallback (direct import; the archive rarely stores one) --------
# Only recordings the app measured itself carry "duration"; URL intake and
# trimmed files do not, and the listing used to show an empty field for
# meetings whose transcript states the length on every line.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import mcp_server as _mcp                      # noqa: E402

check("duration_keeps_a_stored_value",
      _mcp._duration({"duration": "0:12:00", "transcriptPath": str(tr)}) == "0:12:00")
_derived = _mcp._duration({"duration": "", "transcriptPath": str(tr)})
check("duration_falls_back_to_the_transcript", _derived == "0m 9s", _derived)
check("duration_is_empty_without_a_transcript",
      _mcp._duration({"duration": "", "transcriptPath": ""}) == "")

print("\n".join(results))
failed = [r for r in results if r.startswith("FAIL")]
print(f"SUMMARY {'HAS_FAILURES' if failed else 'ALL_PASS'} ({len(results)} checks)")
sys.exit(1 if failed else 0)
