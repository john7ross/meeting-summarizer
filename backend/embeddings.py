#!/usr/bin/env python3
"""Embedding provider — pluggable backends behind one interface.

Backends (selected by ``provider`` string), all lazy-imported so an unused or
not-installed backend never blocks the others or the rest of the app:

  * ``local``               POST to an OpenAI-compatible /v1/embeddings endpoint
                            (llama.cpp server, Hermes agent, LM Studio, etc.).
                            No heavy deps — uses ``requests``.
  * ``openai``              OpenAI cloud embeddings via the ``openai`` SDK.
  * ``sentence-transformers``  Self-contained local model. Heavy; only imported
                            when explicitly selected, so it can't break the
                            whisper/transformers stack for everyone else.

Public API
----------
    provider = EmbeddingProvider(
        backend="local",
        model="nomic-embed-text",
        endpoint="http://localhost:8080/v1",
        api_key="")
    vectors = provider.embed(["text one", "text two"])   # -> list[list[float]]
    dim = provider.dimension                               # int, known after first embed

This module is import-safe with zero optional deps installed; errors are raised
only when a specific backend's ``embed`` is actually called.
"""
from __future__ import annotations

from typing import Optional


class EmbeddingError(RuntimeError):
    """Raised when an embedding backend cannot produce vectors."""


class EmbeddingProvider:
    VALID_BACKENDS = ("local", "openai", "sentence-transformers")

    def __init__(self, backend: str = "local", model: str = "",
                 endpoint: str = "", api_key: str = "",
                 timeout: float = 120.0):
        backend = (backend or "local").strip().lower()
        if backend not in self.VALID_BACKENDS:
            raise EmbeddingError(
                f"Unknown embedding backend '{backend}'. "
                f"Valid: {', '.join(self.VALID_BACKENDS)}")
        self.backend = backend
        self.model = model.strip()
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._dimension: Optional[int] = None
        self._st_model = None  # cached sentence-transformers model

    @property
    def dimension(self) -> Optional[int]:
        return self._dimension

    # -- public --------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text (same order)."""
        if not texts:
            return []
        if self.backend == "local":
            vecs = self._embed_local(texts)
        elif self.backend == "openai":
            vecs = self._embed_openai(texts)
        else:
            vecs = self._embed_sentence_transformers(texts)
        if vecs and self._dimension is None:
            self._dimension = len(vecs[0])
        return vecs

    def embed_one(self, text: str) -> list[float]:
        out = self.embed([text])
        return out[0] if out else []

    # -- backends ------------------------------------------------------
    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        """OpenAI-compatible /v1/embeddings POST (llama.cpp / Hermes / LM Studio)."""
        import requests  # always available in this env

        if not self.endpoint:
            raise EmbeddingError(
                "Local embedding backend requires an endpoint "
                "(e.g. http://localhost:8080/v1).")
        url = f"{self.endpoint}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"input": texts}
        if self.model:
            payload["model"] = self.model
        try:
            resp = requests.post(url, json=payload, headers=headers,
                                 timeout=self.timeout)
        except requests.RequestException as exc:
            raise EmbeddingError(
                f"Could not reach embeddings endpoint at {url}: {exc}") from exc
        if resp.status_code != 200:
            raise EmbeddingError(
                f"Embeddings endpoint returned HTTP {resp.status_code}: "
                f"{resp.text[:300]}")
        try:
            data = resp.json()
            items = sorted(data["data"], key=lambda d: d.get("index", 0))
            return [item["embedding"] for item in items]
        except (KeyError, ValueError, TypeError) as exc:
            raise EmbeddingError(
                f"Unexpected embeddings response shape: {exc}") from exc

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise EmbeddingError(
                "openai package is not installed for the 'openai' backend."
            ) from exc
        if not self.api_key:
            raise EmbeddingError("OpenAI embedding backend requires an API key.")
        model = self.model or "text-embedding-3-small"
        client_kwargs = {"api_key": self.api_key}
        if self.endpoint:  # allow Azure / proxy base URLs
            client_kwargs["base_url"] = self.endpoint
        try:
            client = OpenAI(**client_kwargs)
            resp = client.embeddings.create(model=model, input=texts)
        except Exception as exc:  # openai raises many subclasses
            raise EmbeddingError(f"OpenAI embeddings call failed: {exc}") from exc
        items = sorted(resp.data, key=lambda d: d.index)
        return [list(item.embedding) for item in items]

    def _embed_sentence_transformers(self, texts: list[str]) -> list[list[float]]:
        if self._st_model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError(
                    "sentence-transformers is not installed. Install it in the "
                    "backend python, or choose the 'local'/'openai' backend "
                    "instead. (It is intentionally optional to keep the "
                    "transcription stack stable.)") from exc
            model_name = self.model or "sentence-transformers/all-MiniLM-L6-v2"
            try:
                device = None
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except Exception:
                    device = "cpu"
                self._st_model = SentenceTransformer(model_name, device=device)
            except Exception as exc:
                raise EmbeddingError(
                    f"Could not load sentence-transformers model "
                    f"'{model_name}': {exc}") from exc
        vectors = self._st_model.encode(
            texts, convert_to_numpy=True, normalize_embeddings=False)
        return [v.tolist() for v in vectors]


def provider_from_settings(settings: dict) -> EmbeddingProvider:
    """Build a provider from the app settings dict.

    Recognised keys (all optional, sane defaults applied):
      ragEmbeddingBackend   -> 'local' | 'openai' | 'sentence-transformers'
      ragEmbeddingModel     -> model name/id
      ragEmbeddingEndpoint  -> base URL for 'local' (or 'openai' proxy)
      ragEmbeddingApiKey    -> API key for 'openai' (or auth for 'local')

    Falls back to the summary provider's endpoint/key when the RAG-specific
    ones are not set, so a user who already configured a local LLM endpoint
    gets embeddings working with no extra setup if their server exposes
    /v1/embeddings.
    """
    # Default to the self-contained offline embedder: a local chat server
    # (llama.cpp/LM Studio) generally cannot serve /v1/embeddings, so 'local' is
    # a poor default. An explicit ragEmbeddingBackend setting still overrides.
    backend = settings.get("ragEmbeddingBackend") or "sentence-transformers"
    model = settings.get("ragEmbeddingModel") or ""
    if backend == "sentence-transformers" and not model:
        model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    endpoint = (settings.get("ragEmbeddingEndpoint")
                or settings.get("localEndpoint") or "")
    api_key = (settings.get("ragEmbeddingApiKey")
               or settings.get("apiKey") or "")
    return EmbeddingProvider(backend=backend, model=model,
                             endpoint=endpoint, api_key=api_key)


if __name__ == "__main__":
    # Tiny smoke harness: echo the resolved config (no network call).
    import json, sys
    cfg = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    p = provider_from_settings(cfg)
    print(json.dumps({
        "backend": p.backend, "model": p.model,
        "endpoint": p.endpoint, "has_key": bool(p.api_key),
    }))
