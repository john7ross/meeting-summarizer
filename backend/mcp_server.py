#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP server — expose the meeting archive as tools for any MCP-capable agent.

This is the INVERSE of the 'agent' AI provider. There, we push a transcript out
to an agent. Here, an agent (Claude Code, Codex, Hermes, …) reaches INTO the
archive on its own: list meetings, pull a transcript/summary/analysis, grep
across transcripts, or run a semantic search over the knowledge base.

Transport: MCP stdio — newline-delimited JSON-RPC 2.0 on stdin/stdout.
STDOUT IS THE PROTOCOL: nothing may be printed there except JSON-RPC messages;
all diagnostics go to stderr.

Dependency-free by design (no MCP SDK needed in the embedded runtime).

Register with an agent, e.g. Claude Code::

    claude mcp add meetings -- <python> <path to this file>
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = ROOT / "config" / "history.json"
SETTINGS_FILE = ROOT / "config" / "settings.json"
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "meeting-summarizer"


def _log(msg: str) -> None:
    print(f"[mcp] {msg}", file=sys.stderr, flush=True)


def _app_version() -> str:
    try:
        return str(json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
                   .get("version", "0"))
    except Exception:      # noqa: BLE001
        return "0"


# -- archive access ----------------------------------------------------
def _load_history() -> list:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _entry(meeting_id) -> dict:
    wanted = str(meeting_id).strip()
    for e in _load_history():
        if str(e.get("id")) == wanted:
            return e
    raise ValueError(f"Meeting {meeting_id} not found")


def _read(path, max_chars: int = 0) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace")
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + f"\n… [обрезано, всего {len(text)} символов]"
    return text


def _versions(entry: dict, kind: str) -> list:
    return entry.get("summaryVersions" if kind == "summary" else "analysisVersions", []) or []


def _pick_version(entry: dict, kind: str, version: int = 0) -> dict:
    items = _versions(entry, kind)
    if not items:
        raise ValueError(f"Meeting {entry.get('id')} has no {kind}")
    if version:
        for v in items:
            if int(v.get("version", 0)) == int(version):
                return v
        raise ValueError(f"{kind} version {version} not found "
                         f"(available: {[v.get('version') for v in items]})")
    return items[-1]


def _duration(e: dict) -> str:
    """Length of the meeting, falling back to the transcript's last timestamp.

    Only recordings the app measured itself carry a ``duration``; everything
    imported by URL or already trimmed has none, and the listing then showed an
    empty field for meetings whose transcript states the length on every line.
    The same fallback already serves the exports, Sheets and Obsidian.
    """
    stored = (e.get("duration") or "").strip()
    if stored:
        return stored
    text = _read(e.get("transcriptPath"), 0)
    if not text:
        return ""
    try:
        sys.path.insert(0, str(ROOT / "desktop"))
        from app.backend.media import duration_from_transcript
        return duration_from_transcript(text) or ""
    except Exception:      # noqa: BLE001 - a listing must never fail on this
        return ""


def _brief(e: dict) -> dict:
    return {
        "id": e.get("id"),
        "name": e.get("videoName", ""),
        "date": e.get("processedAt", ""),
        "project": e.get("project", ""),
        "status": e.get("status", ""),
        "duration": _duration(e),
        "summary_versions": len(e.get("summaryVersions", []) or []),
        "analysis_versions": len(e.get("analysisVersions", []) or []),
        "has_transcript": bool(e.get("transcriptPath")),
    }


# -- tools -------------------------------------------------------------
def tool_list_meetings(limit: int = 50, project: str = "", only_with_summary: bool = False) -> str:
    items = _load_history()
    if project:
        items = [e for e in items if (e.get("project") or "") == project]
    if only_with_summary:
        items = [e for e in items if e.get("summaryVersions")]
    items = list(reversed(items))[:max(1, int(limit or 50))]
    return json.dumps({"count": len(items), "meetings": [_brief(e) for e in items]},
                      ensure_ascii=False, indent=2)


def tool_get_transcript(meeting_id, max_chars: int = 0) -> str:
    e = _entry(meeting_id)
    text = _read(e.get("transcriptPath"), int(max_chars or 0))
    if not text:
        raise ValueError(f"Meeting {meeting_id} has no transcript on disk")
    return text


def tool_get_summary(meeting_id, version: int = 0) -> str:
    e = _entry(meeting_id)
    v = _pick_version(e, "summary", int(version or 0))
    text = _read(v.get("path"))
    if not text:
        raise ValueError(f"Summary file is missing: {v.get('path')}")
    return text


def tool_get_analysis(meeting_id, version: int = 0, feature: str = "") -> str:
    e = _entry(meeting_id)
    v = _pick_version(e, "analysis", int(version or 0))
    raw = _read(v.get("path"))
    if not raw:
        raise ValueError(f"Analysis file is missing: {v.get('path')}")
    data = json.loads(raw)
    if feature:
        if feature not in data:
            raise ValueError(f"Unknown feature {feature!r}; available: {sorted(data)}")
        data = {feature: data[feature]}
    return json.dumps(data, ensure_ascii=False, indent=2)


def tool_search_transcripts(query: str, limit: int = 20, context: int = 200) -> str:
    """Plain substring search across all transcripts (case-insensitive)."""
    if not (query or "").strip():
        raise ValueError("query is required")
    needle, hits = query.lower(), []
    for e in _load_history():
        text = _read(e.get("transcriptPath"))
        if not text:
            continue
        low, start = text.lower(), 0
        while len(hits) < int(limit or 20):
            i = low.find(needle, start)
            if i < 0:
                break
            a, b = max(0, i - int(context) // 2), min(len(text), i + len(needle) + int(context) // 2)
            hits.append({"meeting_id": e.get("id"), "name": e.get("videoName", ""),
                         "date": e.get("processedAt", ""),
                         "excerpt": text[a:b].replace("\n", " ").strip()})
            start = i + len(needle)
        if len(hits) >= int(limit or 20):
            break
    return json.dumps({"query": query, "count": len(hits), "hits": hits},
                      ensure_ascii=False, indent=2)


def tool_search_knowledge(query: str, project: str = "", top_k: int = 5) -> str:
    """Semantic search across every discovered desktop/server/shared catalog."""
    if not (query or "").strip():
        raise ValueError("query is required")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import rag
        from embeddings import provider_from_settings
        from rag_catalogs import discover_catalogs
    except ImportError as exc:
        raise ValueError(f"Knowledge base unavailable: {exc}")
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    limit = max(1, int(top_k or 5))
    hits, searched, skipped = [], [], []
    for catalog in discover_catalogs():
        try:
            catalog_settings = dict(settings)
            meta_path = catalog.path / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("provider"):
                    catalog_settings["ragEmbeddingBackend"] = meta["provider"]
                if meta.get("model"):
                    catalog_settings["ragEmbeddingModel"] = meta["model"]
            provider = provider_from_settings(catalog_settings)
            store = rag.RagStore(rag_dir=str(catalog.path), provider=provider)
            result = store.search(query, project=project or "", top_k=limit)
            searched.append(catalog.catalog_id)
            for item in result.get("results", []):
                hit = dict(item)
                hit["catalog"] = catalog.catalog_id
                hit["catalog_kind"] = catalog.kind
                hits.append(hit)
        except Exception as exc:  # one incompatible catalog must not hide the rest
            skipped.append({"catalog": catalog.catalog_id, "reason": str(exc)})

    hits.sort(key=lambda item: float(item.get("score", 0)), reverse=True)
    deduped, seen = [], set()
    for hit in hits:
        fingerprint = (
            str(hit.get("title", "")).strip().casefold(),
            str(hit.get("date", "")).strip(),
            str(hit.get("text", "")).strip().casefold(),
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(hit)
        if len(deduped) >= limit:
            break
    return json.dumps({
        "success": bool(searched),
        "query": query,
        "project": project or "",
        "count": len(deduped),
        "results": deduped,
        "catalogs_searched": searched,
        "catalogs_skipped": skipped,
    }, ensure_ascii=False, indent=2)


TOOLS = [
    {
        "name": "list_meetings",
        "description": "List processed meetings (newest first) with id, name, date, "
                       "project, status and how many summary/analysis versions exist.",
        "handler": tool_list_meetings,
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "Max meetings to return (default 50)"},
            "project": {"type": "string", "description": "Filter by project id"},
            "only_with_summary": {"type": "boolean",
                                  "description": "Only meetings that already have a summary"},
        }},
    },
    {
        "name": "get_transcript",
        "description": "Full transcript text of one meeting.",
        "handler": tool_get_transcript,
        "inputSchema": {"type": "object", "properties": {
            "meeting_id": {"type": "string", "description": "Meeting id from list_meetings"},
            "max_chars": {"type": "integer", "description": "Truncate to N chars (0 = full)"},
        }, "required": ["meeting_id"]},
    },
    {
        "name": "get_summary",
        "description": "Summary of a meeting (latest version by default).",
        "handler": tool_get_summary,
        "inputSchema": {"type": "object", "properties": {
            "meeting_id": {"type": "string"},
            "version": {"type": "integer", "description": "Specific version (0 = latest)"},
        }, "required": ["meeting_id"]},
    },
    {
        "name": "get_analysis",
        "description": "Structured analysis JSON of a meeting: action items, risks, "
                       "sentiment, category, key topics, quotes, technologies, questions, "
                       "recommendations, follow-ups, formal protocol.",
        "handler": tool_get_analysis,
        "inputSchema": {"type": "object", "properties": {
            "meeting_id": {"type": "string"},
            "version": {"type": "integer", "description": "Specific version (0 = latest)"},
            "feature": {"type": "string",
                        "description": "Return only one feature, e.g. actionItems or risks"},
        }, "required": ["meeting_id"]},
    },
    {
        "name": "search_transcripts",
        "description": "Literal text search across every transcript; returns excerpts "
                       "with the meeting they came from.",
        "handler": tool_search_transcripts,
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "description": "Max hits (default 20)"},
            "context": {"type": "integer", "description": "Excerpt size in chars (default 200)"},
        }, "required": ["query"]},
    },
    {
        "name": "search_knowledge",
        "description": "Semantic search across every local desktop, per-user server and "
                       "shared RAG catalog. Results identify their source catalog; "
                       "incompatible catalogs are reported explicitly.",
        "handler": tool_search_knowledge,
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "project": {"type": "string", "description": "Restrict to one project"},
            "top_k": {"type": "integer", "description": "How many results (default 5)"},
        }, "required": ["query"]},
    },
]
_BY_NAME = {t["name"]: t for t in TOOLS}


# -- JSON-RPC / MCP ----------------------------------------------------
def _result(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(msg: dict):
    """Handle one JSON-RPC message; returns a response dict, or None for notifications."""
    method, req_id = msg.get("method"), msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        # Echo the client's protocol version when it sends one (forward-compatible).
        version = params.get("protocolVersion") or PROTOCOL_VERSION
        return _result(req_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": _app_version()},
        })
    if method in ("notifications/initialized", "initialized"):
        return None                       # notification — no response
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": [
            {k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        tool = _BY_NAME.get(name)
        if tool is None:
            return _error(req_id, -32602, f"Unknown tool: {name}")
        try:
            text = tool["handler"](**args)
        except TypeError as exc:          # bad/missing arguments
            return _result(req_id, {"content": [{"type": "text", "text": f"Bad arguments: {exc}"}],
                                    "isError": True})
        except Exception as exc:          # noqa: BLE001 — report, never crash the server
            _log(f"tool {name} failed: {exc}")
            return _result(req_id, {"content": [{"type": "text", "text": str(exc)}],
                                    "isError": True})
        return _result(req_id, {"content": [{"type": "text", "text": text}]})
    if req_id is None:
        return None                       # unknown notification — ignore
    return _error(req_id, -32601, f"Method not found: {method}")


def main() -> int:
    # UTF-8 on both pipes MUST be set here, not under __main__: an agent launcher
    # (or any embedder that imports and calls main()) would otherwise read Cyrillic
    # requests through the Windows ANSI codepage and mangle them.
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            sys.stdin.reconfigure(encoding="utf-8")
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:      # noqa: BLE001 — already-wrapped streams
            pass
    _log(f"{SERVER_NAME} v{_app_version()} ready; history={HISTORY_FILE}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps(_error(None, -32700, "Parse error")) + "\n")
            sys.stdout.flush()
            continue
        try:
            response = handle(msg)
        except Exception as exc:          # noqa: BLE001
            _log("handler crashed: " + traceback.format_exc())
            response = _error(msg.get("id"), -32603, f"Internal error: {exc}")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


def print_registration() -> None:
    """Ready-to-paste registration snippets for common agents (not the protocol —
    prints to stdout only in this explicit mode, then exits)."""
    py, srv = sys.executable, str(Path(__file__).resolve())
    print("Claude Code:")
    print(f'  claude mcp add meetings -- "{py}" "{srv}"\n')
    print("Codex / any client with a JSON config (mcpServers section):")
    print(json.dumps({"mcpServers": {"meetings": {"command": py, "args": [srv]}}}, indent=2))
    print("\nTools: " + ", ".join(t["name"] for t in TOOLS))


if __name__ == "__main__":
    if "--print-registration" in sys.argv:
        print_registration()
        raise SystemExit(0)
    raise SystemExit(main())
