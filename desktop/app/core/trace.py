"""Load + normalise a job's performance trace for the Diagnostics timeline (#10).

The backend writes ``<id>/<stem>_trace.json`` (see backend/processing/tracing.py)
on every transcription — spans with start/end/duration/metadata. This turns that
REAL data into relative bars for a timeline view. Qt-free.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path


def find_trace(job_dir) -> "str | None":
    """Latest ``*_trace.json`` in a meeting's job directory, or None."""
    files = sorted(glob.glob(os.path.join(str(job_dir), "*_trace.json")))
    return files[-1] if files else None


def load_trace(path) -> "dict | None":
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _depth(a: dict, spans: list) -> int:
    """Flame-graph depth = how many other spans strictly contain this one in time.
    Nesting is derived from the spans' [start,end] ranges (a parent encloses its
    children), so it works for both the new whole-job traces (root → stages →
    chunks/features) and old flat traces (everything at depth 0)."""
    n = 0
    for b in spans:
        if b is a:
            continue
        if b["s0"] <= a["s0"] and b["s1"] >= a["s1"] and (b["s0"] < a["s0"] or b["s1"] > a["s1"]):
            n += 1
    return n


def layout(trace: dict) -> dict:
    """Normalise spans to relative [0,1] offset/width + a nesting ``depth`` for a
    flame-graph view.

    Returns ``{"total_ms", "name", "timestamp", "max_depth", "bars": [{name,
    offset, width, ms, depth, metadata}]}``. Robust to missing startTime/endTime
    (derived from spans).
    """
    raw = list(trace.get("spans", []) or [])
    starts = [s.get("start", 0) for s in raw if isinstance(s.get("start"), (int, float))]
    ends = [s.get("end", s.get("start", 0)) for s in raw]
    start = trace.get("startTime") or (min(starts) if starts else 0)
    end = trace.get("endTime") or (max(ends) if ends else start)
    total = max(1e-9, float(end) - float(start))
    # Pre-resolve absolute [s0,s1] for containment, then derive depth.
    resolved = []
    for s in raw:
        s0 = float(s.get("start", start))
        s1 = float(s.get("end", s0))
        dur = s.get("duration")
        resolved.append({"name": s.get("name", ""), "s0": s0, "s1": s1,
                         "ms": round(dur if isinstance(dur, (int, float))
                                     else (s1 - s0) * 1000, 1),
                         "metadata": s.get("metadata", {}) or {}})
    bars = []
    for a in resolved:
        bars.append({
            "name": a["name"],
            "offset": max(0.0, min(1.0, (a["s0"] - start) / total)),
            "width": max(0.0, min(1.0, (a["s1"] - a["s0"]) / total)),
            "ms": a["ms"],
            "depth": _depth(a, resolved),
            "metadata": a["metadata"],
        })
    max_depth = max((b["depth"] for b in bars), default=0)
    return {"total_ms": round(total * 1000, 1), "bars": bars, "max_depth": max_depth,
            "name": trace.get("name", ""), "timestamp": trace.get("timestamp", "")}
