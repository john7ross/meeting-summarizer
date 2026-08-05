"""Self-test for configurable RAG catalogs and MCP aggregation.

No network, embeddings, or persistent user data are used.
"""
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import mcp_server  # noqa: E402
import rag_catalogs as catalogs  # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(("PASS  " if condition else "FAIL  ") + name
          + (f"  ({detail})" if detail and not condition else ""))


with tempfile.TemporaryDirectory() as tmp_raw:
    tmp = Path(tmp_raw)
    original_roots = (
        catalogs.DESKTOP_RAG_DIR,
        catalogs.SERVER_RAG_ROOT,
        catalogs.SHARED_RAG_ROOT,
    )
    catalogs.DESKTOP_RAG_DIR = tmp / "desktop"
    catalogs.SERVER_RAG_ROOT = tmp / "server"
    catalogs.SHARED_RAG_ROOT = tmp / "shared"
    try:
        key = catalogs.generate_shared_key()
        check("generated_key_valid", catalogs.validate_shared_key(key) == key)
        digest = catalogs.shared_catalog_id(key)
        shared = catalogs.shared_catalog_dir(key)
        check("shared_path_is_digest", shared.name == digest and key not in str(shared))
        check("desktop_server_share_by_key",
              catalogs.desktop_catalog_dir({
                  "ragCatalogMode": "shared", "ragSharedCatalogKey": key,
              }) == catalogs.server_catalog_dir(7, {
                  "ragCatalogMode": "shared", "ragSharedCatalogKey": key,
              }))
        check("isolated_catalogs_differ",
              catalogs.desktop_catalog_dir({}) != catalogs.server_catalog_dir(7, {}))

        rejected = False
        try:
            catalogs.shared_catalog_dir("../../escape")
        except catalogs.CatalogConfigError:
            rejected = True
        check("path_traversal_rejected", rejected)

        lock_contended = False
        with catalogs.catalog_write_lock(tmp / "locked"):
            try:
                with catalogs.catalog_write_lock(tmp / "locked", timeout=0.1):
                    pass
            except TimeoutError:
                lock_contended = True
        check("shared_writes_are_serialized", lock_contended)

        for path in (
            catalogs.DESKTOP_RAG_DIR,
            catalogs.SERVER_RAG_ROOT / "u7",
            shared,
        ):
            path.mkdir(parents=True, exist_ok=True)
            (path / "meta.json").write_text("{}", encoding="utf-8")
        discovered = catalogs.discover_catalogs()
        kinds = {item.kind for item in discovered}
        check("discovers_all_catalog_kinds",
              {"desktop", "server-user", "shared"} <= kinds, str(kinds))

        # MCP aggregation: two searchable catalogs, one incompatible catalog,
        # duplicate document retained only from the higher-scoring source.
        cat_a, cat_b, cat_bad = (
            catalogs.Catalog("desktop", "desktop", tmp / "cat-a"),
            catalogs.Catalog("server:u7", "server-user", tmp / "cat-b"),
            catalogs.Catalog("shared:bad", "shared", tmp / "cat-bad"),
        )
        for cat in (cat_a, cat_b, cat_bad):
            cat.path.mkdir()
        (cat_b.path / "meta.json").write_text(
            '{"provider":"openai","model":"catalog-model"}', encoding="utf-8")

        original_discover = catalogs.discover_catalogs
        catalogs.discover_catalogs = lambda: [cat_a, cat_b, cat_bad]
        provider_calls = []

        class FakeStore:
            def __init__(self, rag_dir, provider):
                self.name = Path(rag_dir).name

            def search(self, query, project="", top_k=5):
                if self.name == "cat-bad":
                    raise RuntimeError("incompatible embedding dimensions")
                duplicate = {
                    "doc_id": "same", "title": "Meeting", "date": "2026-07-25",
                    "text": "same text", "score": 0.8 if self.name == "cat-a" else 0.9,
                }
                extra = [] if self.name == "cat-a" else [{
                    "doc_id": "other", "title": "Other", "date": "2026-07-24",
                    "text": "other text", "score": 0.7,
                }]
                return {"results": [duplicate, *extra]}

        fake_rag = types.ModuleType("rag")
        fake_rag.RagStore = FakeStore
        fake_embeddings = types.ModuleType("embeddings")
        fake_embeddings.provider_from_settings = (
            lambda settings: provider_calls.append(dict(settings)) or object())
        old_rag = sys.modules.get("rag")
        old_embeddings = sys.modules.get("embeddings")
        sys.modules["rag"] = fake_rag
        sys.modules["embeddings"] = fake_embeddings
        old_settings_file = mcp_server.SETTINGS_FILE
        mcp_server.SETTINGS_FILE = tmp / "settings.json"
        mcp_server.SETTINGS_FILE.write_text("{}", encoding="utf-8")
        try:
            aggregated = json.loads(
                mcp_server.tool_search_knowledge("architecture", top_k=5))
        finally:
            mcp_server.SETTINGS_FILE = old_settings_file
            catalogs.discover_catalogs = original_discover
            if old_rag is None:
                sys.modules.pop("rag", None)
            else:
                sys.modules["rag"] = old_rag
            if old_embeddings is None:
                sys.modules.pop("embeddings", None)
            else:
                sys.modules["embeddings"] = old_embeddings

        check("mcp_searches_compatible_catalogs",
              aggregated["catalogs_searched"] == ["desktop", "server:u7"],
              str(aggregated["catalogs_searched"]))
        check("mcp_reports_incompatible_catalog",
              len(aggregated["catalogs_skipped"]) == 1
              and aggregated["catalogs_skipped"][0]["catalog"] == "shared:bad",
              str(aggregated["catalogs_skipped"]))
        check("mcp_deduplicates_globally",
              aggregated["count"] == 2
              and aggregated["results"][0]["catalog"] == "server:u7",
              str(aggregated["results"]))
        check("mcp_uses_catalog_embedding_metadata",
              any(call.get("ragEmbeddingModel") == "catalog-model"
                  and call.get("ragEmbeddingBackend") == "openai"
                  for call in provider_calls),
              str(provider_calls))
    finally:
        (catalogs.DESKTOP_RAG_DIR,
         catalogs.SERVER_RAG_ROOT,
         catalogs.SHARED_RAG_ROOT) = original_roots

print()
if FAIL:
    print(f"SUMMARY FAIL ({len(FAIL)} failed): {', '.join(FAIL)}")
    raise SystemExit(1)
print(f"SUMMARY ALL_PASS ({len(PASS)} checks)")
