"""End-to-end test for the RAG store (real chromadb, fake deterministic embedder).

No network: a FakeProvider maps text -> a small bag-of-words vector over a fixed
vocabulary, so "semantically" related texts (sharing words) score higher. This
exercises the real chromadb add/query/delete/clear paths and the per-document
collapse + project filtering logic.

Run:
    backend\\python\\python.exe desktop\\_selftest_rag.py
"""
import sys, tempfile, math, json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from rag import RagStore, chunk_text
from embeddings import EmbeddingProvider

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f"  ({detail})" if (detail and not cond) else ""))


# ── deterministic fake embedder over a fixed vocabulary ───────────────────────
VOCAB = ["бюджет","сроки","архитектура","api","база","данные","деплой",
         "тест","релиз","команда","проект","встреча","решение","риск",
         "клиент","оплата","договор","скоринг","микросервис","редис"]

class FakeProvider(EmbeddingProvider):
    def __init__(self):
        super().__init__(backend="local", model="fake", endpoint="http://x")
    def embed(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            v = [float(low.count(w)) for w in VOCAB]
            # add a tiny constant so all-zero vectors don't break cosine
            v.append(0.01)
            norm = math.sqrt(sum(x*x for x in v)) or 1.0
            out.append([x / norm for x in v])
        if out and self._dimension is None:
            self._dimension = len(out[0])
        return out


# ── chunking unit checks ──────────────────────────────────────────────────────
check("chunk_empty", chunk_text("") == [])
long = "\n".join([f"строка про бюджет и сроки номер {i}" for i in range(200)])
chunks = chunk_text(long, target_chars=400, overlap=50)
check("chunk_splits_long", len(chunks) > 1, str(len(chunks)))
check("chunk_respects_size", all(len(c) <= 600 for c in chunks))

# The desktop worker consumes the CLI output as UTF-8.  Verify the byte
# contract because redirected stdout on Windows may otherwise use cp1251.
emit_probe = subprocess.run(
    [
        sys.executable, "-c",
        "import sys; "
        f"sys.path.insert(0, {str(ROOT / 'backend')!r}); "
        "from rag import _emit; _emit({'text':'Привет, встреча'})",
    ],
    capture_output=True,
)
emit_data = json.loads(emit_probe.stdout.decode("utf-8"))
check("cli_stdout_is_utf8", emit_data["text"] == "Привет, встреча")

# ── store add/search ──────────────────────────────────────────────────────────
tmp = tempfile.mkdtemp()
store = RagStore(tmp, FakeProvider())

store.add("m1", project="alpha", title="Архитектура API", date="2026-01-10",
          summary="Обсудили архитектуру и api микросервисов.",
          transcript="[00:00:01] Нужно спроектировать api и базу данных.\n"
                     "[00:00:10] Деплой через релиз микросервисов.")
store.add("m2", project="alpha", title="Бюджет проекта", date="2026-02-01",
          summary="Согласовали бюджет и сроки проекта.",
          transcript="[00:00:01] Бюджет ограничен.\n[00:00:08] Сроки сжатые, риск.")
store.add("m3", project="beta", title="Скоринг клиента", date="2026-02-15",
          summary="Скоринг клиента и оплата по договору.",
          transcript="[00:00:01] Скоринг влияет на оплату.\n[00:00:05] Договор с клиентом.")

st = store.stats()
check("stats_3_docs", st["documents"] == 3, str(st))
check("stats_has_chunks", st["chunks"] >= 3)
check("stats_projects", st["projects"].get("alpha") == 2 and st["projects"].get("beta") == 1, str(st["projects"]))

# semantic-ish search: query about api/architecture should rank m1 first
r = store.search("вопросы по api и архитектуре", top_k=3)
check("search_returns_hits", r["count"] >= 1)
check("search_top_is_m1", r["results"][0]["doc_id"] == "m1", str([h["doc_id"] for h in r["results"]]))

# query about money should rank m2 first
r2 = store.search("бюджет и сроки", top_k=3)
check("search_budget_top_m2", r2["results"][0]["doc_id"] == "m2", str([h["doc_id"] for h in r2["results"]]))

# one hit per document (collapse), not per chunk
r3 = store.search("api", top_k=5)
ids = [h["doc_id"] for h in r3["results"]]
check("search_collapses_per_doc", len(ids) == len(set(ids)), str(ids))

# ── project filter ────────────────────────────────────────────────────────────
r_beta = store.search("оплата", project="beta", top_k=5)
check("project_filter_only_beta", all(h["project"] == "beta" for h in r_beta["results"]),
      str([h["project"] for h in r_beta["results"]]))
check("project_filter_finds_m3", any(h["doc_id"] == "m3" for h in r_beta["results"]))

r_alpha = store.search("скоринг клиента", project="alpha", top_k=5)
check("project_filter_excludes_other", all(h["doc_id"] != "m3" for h in r_alpha["results"]))

# A long, highly relevant document must not monopolise the initial chunk window
# and hide every other meeting after per-document collapse.
dominant_store = RagStore(tempfile.mkdtemp(), FakeProvider())
dominant_store.add(
    "dominant", "", "Many API chunks", "2026-03-01", "",
    "\n".join(f"api архитектура микросервис строка {i} " * 8 for i in range(500)))
dominant_store.add("other-1", "", "Budget", "2026-03-02",
                   "бюджет", "сроки проекта")
dominant_store.add("other-2", "", "Client", "2026-03-03",
                   "клиент", "договор оплата")
diverse = dominant_store.search("api архитектура", top_k=3)
check("search_fills_unique_document_limit",
      diverse["count"] == 3
      and {h["doc_id"] for h in diverse["results"]}
      == {"dominant", "other-1", "other-2"},
      str([h["doc_id"] for h in diverse["results"]]))

# Chroma's HNSW index is approximate: querying every vector in the collection
# still drops some, and which ones varies per run - observed live, it returned
# 161 of 171 chunks, ALL from the long meeting, so both short meetings vanished
# from search. The exact fill-in below is what makes coverage deterministic, so
# test it directly rather than relying on the ANN happening to behave.
_coll = dominant_store._coll()
_qvec = dominant_store.provider.embed_one("бюджет сроки проекта")
_best = {"dominant": {"doc_id": "dominant", "score": 0.1, "text": "x",
                      "project": "", "title": "", "date": "", "kind": ""}}
_meta_docs = dominant_store._read_meta().get("documents", [])
dominant_store._fill_missing_documents(_coll, _meta_docs, _best, _qvec, 3)
check("ann_misses_are_filled_in", set(_best) == {"dominant", "other-1", "other-2"},
      str(sorted(_best)))
check("filled_docs_carry_real_scores",
      all(isinstance(_best[d]["score"], float) and _best[d]["text"]
          for d in ("other-1", "other-2")),
      str({d: _best[d]["score"] for d in ("other-1", "other-2")}))
check("filled_scores_rank_by_relevance",
      _best["other-1"]["score"] > _best["other-2"]["score"],
      f"budget={_best['other-1']['score']} client={_best['other-2']['score']}")
_stop = dict(_best)
dominant_store._fill_missing_documents(_coll, _meta_docs, _stop, _qvec, 1)
check("fill_respects_the_unique_target", len(_stop) == len(_best),
      "already satisfied -> no extra reads")

# ── list ──────────────────────────────────────────────────────────────────────
lst = store.list_docs()
check("list_all_3", lst["count"] == 3)
lst_alpha = store.list_docs(project="alpha")
check("list_alpha_2", lst_alpha["count"] == 2)
# sorted by date desc
check("list_sorted_desc", lst["documents"][0]["date"] >= lst["documents"][-1]["date"])

# ── idempotent re-add (same id replaces, no dup) ──────────────────────────────
store.add("m1", project="alpha", title="Архитектура API v2", date="2026-01-11",
          summary="Обновлённая архитектура api.", transcript="[00:00:01] api база данные.")
st2 = store.stats()
check("readd_no_dup", st2["documents"] == 3, str(st2["documents"]))
lst2 = store.list_docs(project="alpha")
titles = [d["title"] for d in lst2["documents"] if d["doc_id"] == "m1"]
check("readd_updated_title", titles and titles[0] == "Архитектура API v2", str(titles))

# ── delete ────────────────────────────────────────────────────────────────────
store.delete("m3")
check("delete_removed", store.stats()["documents"] == 2)
r_after = store.search("оплата", project="beta", top_k=5)
check("delete_gone_from_search", all(h["doc_id"] != "m3" for h in r_after["results"]))

# ── provider/model guard ──────────────────────────────────────────────────────
class OtherProvider(FakeProvider):
    def __init__(self):
        EmbeddingProvider.__init__(self, backend="openai", model="other", endpoint="")
guard_store = RagStore(tmp, OtherProvider())
raised = False
try:
    guard_store.add("mX", "alpha", "x", "2026-03-01", "бюджет", "сроки")
except Exception:
    raised = True
check("provider_change_guard_raises", raised)

# ── clear ─────────────────────────────────────────────────────────────────────
store.clear()
check("clear_empties", store.stats()["documents"] == 0)
check("clear_search_empty", store.search("api", top_k=3)["count"] == 0)

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    sys.exit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
sys.exit(0)
