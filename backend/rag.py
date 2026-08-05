#!/usr/bin/env python3
"""RAG knowledge base — real semantic memory over past meetings.

Replaces the old ``rag_integration.py`` JSON dump (which stored documents but
had no actual retrieval). This stores meeting chunks as vectors in a persistent
Chroma collection, so search is by meaning and can be scoped to a project.

Storage layout (under the required --rag-dir selected by rag_catalogs.py):
    chroma/                  Chroma persistent client data
    meta.json                provider/model/dimension guard + doc registry
    .write.lock              cross-process serialization for mutations

CLI (JSON in / JSON out, like summarization.py and analysis.py):

    add     --rag-dir D --doc-id ID --project PID --title T --date ISO \\
            --summary-file S --transcript-file R [--settings JSON]
    search  --rag-dir D --query Q [--project PID] [--top-k N] [--settings JSON]
    list    --rag-dir D [--project PID]
    stats   --rag-dir D
    delete  --rag-dir D --doc-id ID
    clear   --rag-dir D
    rebuild --rag-dir D --history-file H [--settings JSON]
                                   (re-embeds every stored doc from fresh source
                                    text in history.json; use after a provider/
                                    model change)

All commands print a single JSON object to stdout. Errors print
{"success": false, "error": "..."} to stderr and exit non-zero.

Embeddings come from embeddings.EmbeddingProvider (local endpoint / openai /
sentence-transformers), configured via --settings.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Allow running both as a module and as a bare script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from embeddings import EmbeddingProvider, EmbeddingError, provider_from_settings


# ── chunking ──────────────────────────────────────────────────────────────────

def chunk_text(text: str, target_chars: int = 1200, overlap: int = 150) -> list[str]:
    """Split *text* into overlapping chunks on paragraph/line boundaries.

    Keeps chunks near *target_chars*, never cutting mid-line. Overlap carries a
    little tail context into the next chunk so retrieval doesn't lose meaning at
    boundaries. Returns [] for empty input.
    """
    text = (text or "").strip()
    if not text:
        return []
    # Prefer splitting on blank lines, then single lines.
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if not current:
            current = para
        elif len(current) + 1 + len(para) <= target_chars:
            current += "\n" + para
        else:
            chunks.append(current)
            # start next chunk with an overlap tail from the previous one
            tail = current[-overlap:] if overlap and len(current) > overlap else ""
            current = (tail + "\n" + para).strip() if tail else para
    if current:
        chunks.append(current)
    return chunks


# ── store ───────────────────────────────────────────────────────────────────

class RagStore:
    """Thin wrapper around a persistent Chroma collection + a meta sidecar."""

    COLLECTION = "meetings"

    def __init__(self, rag_dir: str, provider: Optional[EmbeddingProvider] = None):
        self.rag_dir = Path(rag_dir)
        self.rag_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir = self.rag_dir / "chroma"
        self.meta_path = self.rag_dir / "meta.json"
        self.provider = provider
        self._client = None
        self._collection = None

    # -- chroma lazy init ---------------------------------------------
    def _coll(self):
        if self._collection is None:
            import chromadb
            self._client = chromadb.PersistentClient(path=str(self.chroma_dir))
            # cosine space: best for normalized/text embeddings
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION, metadata={"hnsw:space": "cosine"})
        return self._collection

    # -- meta ----------------------------------------------------------
    def _read_meta(self) -> dict:
        if self.meta_path.exists():
            try:
                return json.loads(self.meta_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        return {"provider": "", "model": "", "dimension": 0, "documents": []}

    def _write_meta(self, meta: dict) -> None:
        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(self.meta_path)

    # -- guard: provider/model consistency ----------------------------
    def _check_provider_match(self, meta: dict) -> None:
        """Warn-by-raising if the active provider differs from what's stored.

        Mixing embedding models in one collection makes cosine distances
        meaningless, so we refuse silently-wrong adds. The caller can rebuild.
        """
        if not self.provider:
            return
        stored_p = meta.get("provider") or ""
        stored_m = meta.get("model") or ""
        # Only enforce once the store actually has vectors.
        if meta.get("documents") and (stored_p or stored_m):
            cur_p = self.provider.backend
            cur_m = self.provider.model
            if (stored_p, stored_m) != (cur_p, cur_m):
                raise EmbeddingError(
                    f"Embedding provider/model changed "
                    f"(stored: {stored_p}/{stored_m or '?'}, "
                    f"now: {cur_p}/{cur_m or '?'}). Run 'rebuild' to re-embed "
                    f"the knowledge base with the new model, or revert the "
                    f"embedding settings.")

    # -- add -----------------------------------------------------------
    def add(self, doc_id: str, project: str, title: str, date: str,
            summary: str, transcript: str) -> dict:
        if not self.provider:
            raise EmbeddingError("add requires an embedding provider")
        meta = self._read_meta()
        self._check_provider_match(meta)

        # Remove any prior version of this doc id first (idempotent re-add).
        self.delete(doc_id, _skip_meta=True)

        # Build chunks: summary as its own chunk(s) + transcript chunks.
        summary = (summary or "").strip()
        transcript = (transcript or "").strip()
        pieces: list[tuple[str, str]] = []  # (kind, text)
        for c in chunk_text(summary):
            pieces.append(("summary", c))
        for c in chunk_text(transcript):
            pieces.append(("transcript", c))
        if not pieces:
            raise EmbeddingError(f"document '{doc_id}' has no content to index")

        texts = [p[1] for p in pieces]
        vectors = self.provider.embed(texts)
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"embedding count mismatch: {len(vectors)} vs {len(texts)} chunks")

        ids = [f"{doc_id}::{i}" for i in range(len(pieces))]
        metadatas = [{
            "doc_id": doc_id, "project": project or "", "title": title or "",
            "date": date or "", "kind": kind, "chunk": i,
        } for i, (kind, _) in enumerate(pieces)]

        self._coll().add(ids=ids, embeddings=vectors,
                         documents=texts, metadatas=metadatas)

        # Update meta registry.
        meta["provider"] = self.provider.backend
        meta["model"] = self.provider.model
        meta["dimension"] = self.provider.dimension or meta.get("dimension", 0)
        meta["documents"] = [d for d in meta.get("documents", [])
                             if d.get("doc_id") != doc_id]
        meta["documents"].append({
            "doc_id": doc_id, "project": project or "", "title": title or "",
            "date": date or "", "chunks": len(pieces),
            "added_at": datetime.now().isoformat(timespec="seconds"),
        })
        self._write_meta(meta)
        return {"success": True, "doc_id": doc_id, "chunks": len(pieces),
                "project": project or ""}

    # -- search --------------------------------------------------------
    def search(self, query: str, project: str = "", top_k: int = 5) -> dict:
        if not self.provider:
            raise EmbeddingError("search requires an embedding provider")
        query = (query or "").strip()
        if not query:
            raise EmbeddingError("empty query")
        qvec = self.provider.embed_one(query)
        where = {"project": project} if project else None
        collection = self._coll()
        total_chunks = collection.count()
        meta_docs = self._read_meta().get("documents", [])
        if project:
            meta_docs = [d for d in meta_docs if d.get("project", "") == project]
        target_unique = min(max(1, int(top_k)), len(meta_docs))

        # Chroma ranks chunks, while the UI promises meeting/document results.
        # One long meeting can otherwise occupy every fetched slot and hide all
        # other documents. Grow the query until enough unique docs are present
        # (or the complete collection has been inspected), then collapse.
        # Termination must not hinge on a single up-front ``count()``: if it
        # under-reports (chunks landing concurrently, a store written by another
        # process), the window is capped below what the collection holds and the
        # loop stops while a long meeting still occupies every slot - the search
        # then returns that one document and hides the rest.  Re-read the size
        # each round and stop on "the query returned everything it could".
        n = max(top_k * 8, top_k + 10)
        docs = metas = dists = []
        seen_rows = -1
        while n > 0:
            available = collection.count() or total_chunks
            asked = min(n, available) if available else n
            if asked <= 0:
                break
            res = collection.query(
                query_embeddings=[qvec], n_results=asked, where=where)
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            if len({m.get("doc_id", "") for m in metas}) >= target_unique:
                break
            if len(metas) >= available or len(metas) <= seen_rows:
                break          # whole collection inspected / no longer growing
            seen_rows = len(metas)
            n *= 2

        # Collapse to best chunk per doc_id, keep order by score.
        best: dict[str, dict] = {}
        for text, m, dist in zip(docs, metas, dists):
            did = m.get("doc_id", "")
            score = 1.0 - float(dist)  # cosine distance -> similarity
            if did not in best or score > best[did]["score"]:
                best[did] = {
                    "doc_id": did, "project": m.get("project", ""),
                    "title": m.get("title", ""), "date": m.get("date", ""),
                    "kind": m.get("kind", ""), "score": round(score, 4),
                    "text": text,
                }
        # Chroma's HNSW index is APPROXIMATE: asking for every vector in the
        # collection still returns a subset, and which vectors are dropped varies
        # between runs. A short meeting sitting next to a long one can therefore
        # be missing from the ranking entirely - the knowledge base would silently
        # hide whole meetings from search. Widening the query cannot fix that, so
        # fill the remaining slots exactly: pull the missing documents' chunks by
        # metadata and score them against the query vector directly.
        if len(best) < target_unique:
            self._fill_missing_documents(collection, meta_docs, best, qvec,
                                         target_unique)

        hits = sorted(best.values(), key=lambda h: h["score"], reverse=True)[:top_k]
        return {"success": True, "query": query, "project": project or "",
                "count": len(hits), "results": hits}

    @staticmethod
    def _cosine(a, b) -> float:
        num = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(x * x for x in b)) or 1.0
        return num / (na * nb)

    def _fill_missing_documents(self, collection, meta_docs, best: dict,
                                qvec, target_unique: int) -> None:
        """Score documents the ANN ranking skipped, so every known meeting can
        surface. Exact and bounded: only the still-missing documents are read."""
        for doc in meta_docs:
            if len(best) >= target_unique:
                return
            doc_id = str(doc.get("doc_id", ""))
            if not doc_id or doc_id in best:
                continue
            try:
                got = collection.get(where={"doc_id": doc_id},
                                     include=["documents", "embeddings",
                                              "metadatas"])
            except Exception:      # noqa: BLE001 - a broken doc must not kill search
                continue
            texts = got.get("documents") or []
            vectors = got.get("embeddings")
            metadatas = got.get("metadatas") or []
            if vectors is None or len(vectors) == 0 or not texts:
                continue
            top = None
            for text, vec, meta in zip(texts, vectors, metadatas):
                score = self._cosine(qvec, vec)
                if top is None or score > top[0]:
                    top = (score, text, meta or {})
            if top is None:
                continue
            score, text, meta = top
            best[doc_id] = {
                "doc_id": doc_id, "project": meta.get("project", ""),
                "title": meta.get("title", ""), "date": meta.get("date", ""),
                "kind": meta.get("kind", ""), "score": round(score, 4),
                "text": text,
            }

    # -- list / stats / delete / clear / rebuild ----------------------
    def list_docs(self, project: str = "") -> dict:
        meta = self._read_meta()
        docs = meta.get("documents", [])
        if project:
            docs = [d for d in docs if d.get("project") == project]
        docs = sorted(docs, key=lambda d: d.get("date", ""), reverse=True)
        return {"success": True, "count": len(docs), "documents": docs}

    def stats(self) -> dict:
        meta = self._read_meta()
        docs = meta.get("documents", [])
        projects: dict[str, int] = {}
        total_chunks = 0
        for d in docs:
            projects[d.get("project", "")] = projects.get(d.get("project", ""), 0) + 1
            total_chunks += int(d.get("chunks", 0))
        # vector count straight from the collection (source of truth)
        try:
            vec_count = self._coll().count()
        except Exception:
            vec_count = total_chunks
        return {"success": True, "documents": len(docs),
                "chunks": vec_count, "projects": projects,
                "provider": meta.get("provider", ""),
                "model": meta.get("model", ""),
                "dimension": meta.get("dimension", 0)}

    def delete(self, doc_id: str, _skip_meta: bool = False) -> dict:
        try:
            self._coll().delete(where={"doc_id": doc_id})
        except Exception:
            pass
        if not _skip_meta:
            meta = self._read_meta()
            before = len(meta.get("documents", []))
            meta["documents"] = [d for d in meta.get("documents", [])
                                 if d.get("doc_id") != doc_id]
            self._write_meta(meta)
            return {"success": True, "doc_id": doc_id,
                    "removed": before - len(meta["documents"])}
        return {"success": True, "doc_id": doc_id}

    def clear(self) -> dict:
        # Drop and recreate the collection, reset meta (keep provider/model).
        try:
            import chromadb
            client = self._client or chromadb.PersistentClient(
                path=str(self.chroma_dir))
            try:
                client.delete_collection(self.COLLECTION)
            except Exception:
                pass
            self._client = client
            self._collection = client.get_or_create_collection(
                name=self.COLLECTION, metadata={"hnsw:space": "cosine"})
        except Exception as exc:
            raise EmbeddingError(f"clear failed: {exc}") from exc
        meta = self._read_meta()
        meta["documents"] = []
        self._write_meta(meta)
        return {"success": True, "cleared": True}

    def rebuild(self, source_lookup) -> dict:
        """Re-embed every stored document with the current provider.

        *source_lookup* is a callable doc_id -> (summary_text, transcript_text)
        used to fetch fresh source content. Docs whose source can't be found are
        skipped and reported.
        """
        if not self.provider:
            raise EmbeddingError("rebuild requires an embedding provider")
        meta = self._read_meta()
        docs = list(meta.get("documents", []))
        self.clear()
        rebuilt, skipped = 0, []
        for d in docs:
            did = d.get("doc_id")
            try:
                summary, transcript = source_lookup(did)
            except Exception:
                summary, transcript = "", ""
            if not (summary or transcript):
                skipped.append(did)
                continue
            self.add(did, d.get("project", ""), d.get("title", ""),
                     d.get("date", ""), summary, transcript)
            rebuilt += 1
        return {"success": True, "rebuilt": rebuilt, "skipped": skipped}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _read(path: Optional[str]) -> str:
    if not path:
        return ""
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _emit(obj: dict) -> None:
    # The desktop worker decodes this CLI contract as UTF-8.  On Windows a
    # redirected stdout may otherwise inherit a legacy console code page.
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _fail(msg: str) -> None:
    payload = json.dumps(
        {"success": False, "error": msg}, ensure_ascii=False
    ).encode("utf-8") + b"\n"
    sys.stderr.buffer.write(payload)
    sys.stderr.buffer.flush()
    sys.exit(1)


def _source_lookup_from_history(history_file: str):
    """Build a ``doc_id -> (summary_text, transcript_text)`` lookup from the app's
    ``config/history.json``. The RAG doc-id is the history entry id (str); summary
    is the LATEST summary version (falling back to ``summaryPath``), transcript is
    ``transcriptPath``. Missing sources return ("","") so rebuild skips them."""
    entries: list = []
    if history_file:
        p = Path(history_file)
        if p.exists():
            try:
                entries = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                entries = []
    by_id = {str(e.get("id")): e for e in entries if isinstance(e, dict)}

    def lookup(doc_id):
        e = by_id.get(str(doc_id))
        if not e:
            return ("", "")
        versions = e.get("summaryVersions") or []
        spath = ""
        if versions:
            latest = max(versions, key=lambda v: int(v.get("version", 0)))
            spath = latest.get("path", "")
        spath = spath or e.get("summaryPath") or ""
        return (_read(spath), _read(e.get("transcriptPath") or ""))

    return lookup


def _provider(args) -> EmbeddingProvider:
    settings = {}
    if getattr(args, "settings", None):
        try:
            settings = json.loads(args.settings)
        except ValueError:
            pass
    return provider_from_settings(settings)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RAG knowledge base")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--rag-dir", required=True)
        sp.add_argument("--settings", default="")

    sp_add = sub.add_parser("add"); common(sp_add)
    sp_add.add_argument("--doc-id", required=True)
    sp_add.add_argument("--project", default="")
    sp_add.add_argument("--title", default="")
    sp_add.add_argument("--date", default="")
    sp_add.add_argument("--summary-file", default="")
    sp_add.add_argument("--transcript-file", default="")

    sp_search = sub.add_parser("search"); common(sp_search)
    sp_search.add_argument("--query", required=True)
    sp_search.add_argument("--project", default="")
    sp_search.add_argument("--top-k", type=int, default=5)

    sp_list = sub.add_parser("list"); common(sp_list)
    sp_list.add_argument("--project", default="")

    sp_stats = sub.add_parser("stats"); common(sp_stats)

    sp_del = sub.add_parser("delete"); common(sp_del)
    sp_del.add_argument("--doc-id", required=True)

    sp_clear = sub.add_parser("clear"); common(sp_clear)

    sp_rebuild = sub.add_parser("rebuild"); common(sp_rebuild)
    sp_rebuild.add_argument("--history-file", default="")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "add":
            from rag_catalogs import catalog_write_lock
            with catalog_write_lock(args.rag_dir):
                store = RagStore(args.rag_dir, _provider(args))
                _emit(store.add(
                    args.doc_id, args.project, args.title, args.date,
                    _read(args.summary_file), _read(args.transcript_file)))
        elif args.cmd == "search":
            store = RagStore(args.rag_dir, _provider(args))
            _emit(store.search(args.query, args.project, args.top_k))
        elif args.cmd == "list":
            store = RagStore(args.rag_dir)
            _emit(store.list_docs(args.project))
        elif args.cmd == "stats":
            store = RagStore(args.rag_dir)
            _emit(store.stats())
        elif args.cmd == "delete":
            from rag_catalogs import catalog_write_lock
            with catalog_write_lock(args.rag_dir):
                store = RagStore(args.rag_dir)
                _emit(store.delete(args.doc_id))
        elif args.cmd == "clear":
            from rag_catalogs import catalog_write_lock
            with catalog_write_lock(args.rag_dir):
                store = RagStore(args.rag_dir)
                _emit(store.clear())
        elif args.cmd == "rebuild":
            from rag_catalogs import catalog_write_lock
            with catalog_write_lock(args.rag_dir):
                store = RagStore(args.rag_dir, _provider(args))
                _emit(store.rebuild(_source_lookup_from_history(args.history_file)))
        else:
            _fail(f"unknown command {args.cmd}")
    except EmbeddingError as exc:
        _fail(str(exc))
    except Exception as exc:
        _fail(f"{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
