"""Embedding provider adapter for the §28.13 repetition guard.

**This is an adapter, not a setting.** Switching providers requires:

  1. Editing this file (specifically `DEFAULT_PROVIDER`).
  2. Running `scripts/embed_posts.py --re-embed-all` to backfill new
     embeddings (the prior model's vectors live alongside until
     re-embed completes).
  3. Bumping `model_version` if the same model line changes its
     dimensionality or normalization behavior between versions.

Two providers are wired today:

  * `voyage-3-lite` (Voyage AI) — DEFAULT. Cheap, 512-dim, English-strong
    embeddings well-matched to short-form post text. Requires
    `VOYAGE_API_KEY` in `.env`. Documentation: https://docs.voyageai.com/
  * `text-embedding-3-small` (OpenAI) — documented alternative.
    Requires `OPENAI_API_KEY` in `.env`. 1536-dim.

Both providers are called via stdlib `urllib` so we don't pin to a
specific SDK. The HTTP shape is small and stable for both.

Provider unavailable / API key missing / network error → `embed_one`
and `embed_batch` raise `EmbeddingsUnavailable`. Callers (the repetition
guard) catch and degrade gracefully — the guard returns
`similarity_warning_json = NULL`, and the agent draft proceeds.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Sequence

import numpy as np


class EmbeddingsUnavailable(RuntimeError):
    """Provider unavailable (missing key, network error, rate-limited).

    Distinct exception so the repetition guard can degrade gracefully
    without swallowing programming errors.
    """


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: np.ndarray  # shape (n_texts, embedding_dim), float32
    model_name: str
    model_version: str | None
    embedding_dim: int


# ---------------------------------------------------------------------------
# Provider adapters.
# ---------------------------------------------------------------------------
class _ProviderAdapter:
    """Base — one method to override."""

    model_name: str = ""
    model_version: str | None = None
    embedding_dim: int = 0
    rate_limit_sleep_seconds: float = 1.0

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        raise NotImplementedError


class VoyageAIAdapter(_ProviderAdapter):
    """Voyage AI `voyage-3-lite` adapter.

    Endpoint: https://api.voyageai.com/v1/embeddings
    Request: {"input": [...], "model": "voyage-3-lite"}
    Response: {"data": [{"embedding": [...], "index": 0}, ...], "usage": {...}}
    """

    model_name = "voyage-3-lite"
    model_version = "2024-09-01"
    embedding_dim = 512
    rate_limit_sleep_seconds = 0.5

    _ENDPOINT = "https://api.voyageai.com/v1/embeddings"

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(
                vectors=np.zeros((0, self.embedding_dim), dtype=np.float32),
                model_name=self.model_name,
                model_version=self.model_version,
                embedding_dim=self.embedding_dim,
            )
        api_key = os.environ.get("VOYAGE_API_KEY")
        if not api_key:
            raise EmbeddingsUnavailable(
                "VOYAGE_API_KEY is not set. Add it to .env to enable "
                "the repetition guard (§28.13)."
            )
        body = json.dumps({"input": list(texts), "model": self.model_name}).encode("utf-8")
        req = urllib.request.Request(
            self._ENDPOINT,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise EmbeddingsUnavailable(
                f"Voyage API HTTP {exc.code}: {exc.reason} · {body_text}"
            ) from exc
        except urllib.error.URLError as exc:
            raise EmbeddingsUnavailable(f"Voyage API network error: {exc}") from exc
        # Order is by `index`; sort defensively.
        items = sorted(payload.get("data", []), key=lambda d: d.get("index", 0))
        vectors = np.asarray(
            [item["embedding"] for item in items], dtype=np.float32
        )
        return EmbeddingResult(
            vectors=vectors,
            model_name=self.model_name,
            model_version=self.model_version,
            embedding_dim=vectors.shape[1] if vectors.size else self.embedding_dim,
        )


class OpenAIAdapter(_ProviderAdapter):
    """OpenAI `text-embedding-3-small` adapter. Documented alternative."""

    model_name = "text-embedding-3-small"
    model_version = None
    embedding_dim = 1536
    rate_limit_sleep_seconds = 0.3

    _ENDPOINT = "https://api.openai.com/v1/embeddings"

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(
                vectors=np.zeros((0, self.embedding_dim), dtype=np.float32),
                model_name=self.model_name,
                model_version=self.model_version,
                embedding_dim=self.embedding_dim,
            )
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EmbeddingsUnavailable(
                "OPENAI_API_KEY is not set. Either configure OpenAI or "
                "switch DEFAULT_PROVIDER back to VoyageAI in embeddings.py."
            )
        body = json.dumps({"input": list(texts), "model": self.model_name}).encode("utf-8")
        req = urllib.request.Request(
            self._ENDPOINT,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise EmbeddingsUnavailable(
                f"OpenAI API HTTP {exc.code}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise EmbeddingsUnavailable(f"OpenAI API network error: {exc}") from exc
        items = sorted(payload.get("data", []), key=lambda d: d.get("index", 0))
        vectors = np.asarray(
            [item["embedding"] for item in items], dtype=np.float32
        )
        return EmbeddingResult(
            vectors=vectors,
            model_name=self.model_name,
            model_version=self.model_version,
            embedding_dim=vectors.shape[1] if vectors.size else self.embedding_dim,
        )


# Adapter selection. Switch DEFAULT_PROVIDER to OpenAIAdapter() if Voyage
# isn't available — then run `uv run python scripts/embed_posts.py --re-embed-all`.
DEFAULT_PROVIDER: _ProviderAdapter = VoyageAIAdapter()


# ---------------------------------------------------------------------------
# Public entry points used by the repetition guard and the backfill script.
# ---------------------------------------------------------------------------
def embed_one(text: str, *, provider: _ProviderAdapter | None = None) -> EmbeddingResult:
    return embed_batch([text], provider=provider)


def embed_batch(
    texts: Sequence[str], *, provider: _ProviderAdapter | None = None
) -> EmbeddingResult:
    return (provider or DEFAULT_PROVIDER).embed(texts)


def sleep_for_rate_limit(provider: _ProviderAdapter | None = None) -> None:
    """Block for the provider's documented inter-batch sleep."""
    p = provider or DEFAULT_PROVIDER
    if p.rate_limit_sleep_seconds > 0:
        time.sleep(p.rate_limit_sleep_seconds)


# ---------------------------------------------------------------------------
# Numpy cosine helpers — pulled out of the guard so they're unit-testable.
# ---------------------------------------------------------------------------
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """One-pair cosine similarity. Returns 0 if either norm is zero."""
    a = a.astype(np.float32, copy=False)
    b = b.astype(np.float32, copy=False)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def cosine_similarities(query: np.ndarray, corpus: np.ndarray) -> np.ndarray:
    """Row-wise cosine similarities of `query` vs every row of `corpus`.

    Both arrays are 1-D / 2-D float32. Returns a 1-D vector of length
    `corpus.shape[0]`. Zero-norm rows return 0.
    """
    query = query.astype(np.float32, copy=False)
    corpus = corpus.astype(np.float32, copy=False)
    if corpus.ndim == 1:
        corpus = corpus.reshape(1, -1)
    qn = float(np.linalg.norm(query))
    if qn == 0.0:
        return np.zeros((corpus.shape[0],), dtype=np.float32)
    corpus_norms = np.linalg.norm(corpus, axis=1)
    safe_norms = np.where(corpus_norms == 0.0, 1.0, corpus_norms)
    sims = (corpus @ query) / (qn * safe_norms)
    sims = np.where(corpus_norms == 0.0, 0.0, sims)
    return sims.astype(np.float32)


# ---------------------------------------------------------------------------
# BLOB helpers — store float32 little-endian; pair with embedding_dim.
# ---------------------------------------------------------------------------
def vector_to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype="<f4").tobytes()


def blob_to_vector(blob: bytes, *, dim: int) -> np.ndarray:
    arr = np.frombuffer(blob, dtype="<f4")
    if arr.size != dim:
        raise ValueError(
            f"BLOB length {arr.size} does not match expected dim {dim}; "
            "row may have been written with a different model."
        )
    return arr
