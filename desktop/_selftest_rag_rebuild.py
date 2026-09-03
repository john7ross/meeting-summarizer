"""TODO #13a — RAG rebuild: source_lookup from history.json + store.rebuild.

No network: a deterministic fake embedder. Verifies that rebuild re-embeds every
document from FRESH source text (read via config/history.json, latest summary
version + transcript), and that documents whose source is missing are skipped.

Run:
    backend\\python\\python.exe desktop\\_selftest_rag_rebuild.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from rag import RagStore, _source_lookup_from_history
from embeddings import EmbeddingProvider

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))


class FakeProvider(EmbeddingProvider):
    """Deterministic 8-dim char-bucket embedder (backend/model tagged)."""
    def __init__(self, backend="local", model="fake"):
        super().__init__(backend=backend, model=model, endpoint="http://x")
    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 8
            for ch in (t or ""):
                v[ord(ch) % 8] += 1.0
            norm = (sum(x * x for x in v) ** 0.5) or 1.0
            out.append([x / norm for x in v])
        if out and self._dimension is None:
            self._dimension = len(out[0])
        return out


tmp = tempfile.mkdtemp()
src = Path(tmp) / "src"
src.mkdir()

# real source files for doc "100"
(src / "s1.txt").write_text("summary version one", encoding="utf-8")
(src / "s2.txt").write_text("summary version two latest", encoding="utf-8")
(src / "t.txt").write_text("transcript body of the meeting", encoding="utf-8")

# a history.json: doc 100 has 2 summary versions (latest=v2) + transcript; doc 200 has no source
history = Path(tmp) / "history.json"
history.write_text(json.dumps([
    {"id": 100, "transcriptPath": str(src / "t.txt"),
     "summaryPath": str(src / "s1.txt"),
     "summaryVersions": [{"version": 1, "path": str(src / "s1.txt")},
                         {"version": 2, "path": str(src / "s2.txt")}]},
    {"id": 300, "transcriptPath": "", "summaryPath": ""},   # present but empty source
]), encoding="utf-8")

# ── _source_lookup_from_history ──────────────────────────────────────────────
lookup = _source_lookup_from_history(str(history))
s, t = lookup("100")
check("lookup_latest_summary", s == "summary version two latest", s)
check("lookup_transcript", t == "transcript body of the meeting", t)
check("lookup_unknown_empty", lookup("999") == ("", ""))
check("lookup_empty_source", lookup("300") == ("", ""))

# ── seed the store, then rebuild ─────────────────────────────────────────────
rag_dir = Path(tmp) / "rag"
store = RagStore(str(rag_dir), FakeProvider())
store.add("100", "projA", "Meeting 100", "2026-07-01", "old summary", "old transcript")
store.add("200", "projB", "Meeting 200", "2026-07-01", "gone", "gone")  # no history entry
before = store.stats()
check("seeded_two_docs", before["documents"] == 2, str(before))

res = store.rebuild(_source_lookup_from_history(str(history)))
check("rebuild_ok", res.get("success") is True)
check("rebuilt_only_doc100", res.get("rebuilt") == 1, str(res))
check("skipped_doc200", "200" in (res.get("skipped") or []), str(res.get("skipped")))

after = store.stats()
check("after_one_doc", after["documents"] == 1, str(after))
# the rebuilt doc must reflect the FRESH source (latest summary v2 text), searchable
hit = store.search("latest", top_k=3)
docids = [h["doc_id"] for h in hit["results"]]
check("rebuilt_doc_searchable", "100" in docids, str(docids))

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)}): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
