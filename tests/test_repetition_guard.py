"""Repetition guard (§28.13) — tests with a deterministic stub provider.

The guard is a thin wrapper around a numpy cosine scan. We exercise the
labelling thresholds, the re-embed-on-drift path, the graceful
degradation when the provider is unavailable, and the soft-check
contract (never blocks publish).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import numpy as np
import pytest

from app.agent import embeddings as _embeddings
from app.agent import repetition_guard as _guard


# ---------------------------------------------------------------------------
# Stub provider — deterministic, no network. Maps a tiny vocabulary to
# fixed unit vectors so we can construct collisions on purpose.
# ---------------------------------------------------------------------------
class _StubProvider(_embeddings._ProviderAdapter):
    model_name = "stub-3d"
    model_version = "test"
    embedding_dim = 3
    rate_limit_sleep_seconds = 0

    def __init__(self, vocab: dict[str, np.ndarray] | None = None) -> None:
        self.vocab = vocab or {}
        self.calls: list[list[str]] = []

    def _embed_text(self, text: str) -> np.ndarray:
        # Tokenize crudely, sum the per-token vectors, then normalize.
        # Keeps tests easy to reason about.
        lower = text.lower()
        vec = np.zeros(self.embedding_dim, dtype=np.float32)
        for word, v in self.vocab.items():
            if word in lower:
                vec += v
        n = float(np.linalg.norm(vec))
        if n == 0:
            # Fallback: hash the string into one of the three axes so we
            # never return a zero vector that hides bugs.
            axis = abs(hash(text)) % self.embedding_dim
            vec[axis] = 1.0
            return vec
        return vec / n

    def embed(self, texts):
        self.calls.append(list(texts))
        rows = [self._embed_text(t) for t in texts]
        vectors = np.asarray(rows, dtype=np.float32)
        return _embeddings.EmbeddingResult(
            vectors=vectors,
            model_name=self.model_name,
            model_version=self.model_version,
            embedding_dim=self.embedding_dim,
        )


def _install_stub(monkeypatch, vocab: dict[str, np.ndarray] | None = None) -> _StubProvider:
    stub = _StubProvider(vocab=vocab)
    monkeypatch.setattr(_embeddings, "DEFAULT_PROVIDER", stub)
    return stub


def _seed_post_with_embedding(
    conn: sqlite3.Connection, *, text: str, vec: np.ndarray, days_back: int = 1
) -> int:
    when = (date.today() - timedelta(days=days_back)).isoformat()
    post_id = conn.execute(
        """
        INSERT INTO posts
          (created_date, created_at_utc, text, type, posted_via,
           manual_confirmation_status, x_post_id)
        VALUES (?, ?, ?, 'standalone', 'manual', 'confirmed', ?)
        RETURNING id
        """,
        (when, f"{when}T12:00:00Z", text, f"x_{abs(hash(text))}"),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO post_embeddings
          (post_id, embedding_blob, embedding_dim, model_name, model_version,
           source_text_hash)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(post_id),
            _embeddings.vector_to_blob(vec),
            int(vec.shape[0]),
            "stub-3d",
            "test",
            _guard.text_hash(text),
        ),
    )
    return int(post_id)


# ---------------------------------------------------------------------------
# Cosine helpers (pure)
# ---------------------------------------------------------------------------
def test_cosine_similarity_handles_zero_norm() -> None:
    z = np.zeros(3, dtype=np.float32)
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert _embeddings.cosine_similarity(z, a) == 0.0


def test_cosine_similarities_row_wise() -> None:
    q = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    corpus = np.array(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.5, 0.5, 0.0]], dtype=np.float32
    )
    sims = _embeddings.cosine_similarities(q, corpus)
    assert sims.shape == (3,)
    assert sims[0] == pytest.approx(1.0)
    assert sims[1] == pytest.approx(0.0)
    assert sims[2] == pytest.approx(0.7071, abs=1e-3)


def test_blob_round_trip() -> None:
    vec = np.array([0.1, 0.2, -0.3, 0.4], dtype=np.float32)
    blob = _embeddings.vector_to_blob(vec)
    restored = _embeddings.blob_to_vector(blob, dim=4)
    assert np.allclose(restored, vec)


def test_blob_dimension_mismatch_raises() -> None:
    vec = np.array([0.1, 0.2, -0.3], dtype=np.float32)
    blob = _embeddings.vector_to_blob(vec)
    with pytest.raises(ValueError):
        _embeddings.blob_to_vector(blob, dim=4)


# ---------------------------------------------------------------------------
# Guard threshold labelling
# ---------------------------------------------------------------------------
def test_check_returns_none_when_corpus_empty(db_conn, monkeypatch) -> None:
    _install_stub(monkeypatch, vocab={"shipped": np.array([1, 0, 0], dtype=np.float32)})
    result = _guard.check(
        db_conn, draft_text="Shipped the build today.", draft_kind="standalone"
    )
    assert result is None


def test_check_labels_near_duplicate(db_conn, monkeypatch) -> None:
    stub = _install_stub(
        monkeypatch,
        vocab={"shipped": np.array([1, 0, 0], dtype=np.float32)},
    )
    _seed_post_with_embedding(
        db_conn,
        text="Shipped the build today.",
        vec=stub._embed_text("Shipped the build today."),
    )
    result = _guard.check(
        db_conn,
        draft_text="Shipped the build again today.",
        draft_kind="standalone",
    )
    assert result is not None
    assert result["label"] == "near_duplicate"
    assert result["max_cosine"] >= 0.92
    assert "shipped" in result["nearest_text_excerpt"].lower()


def test_check_labels_close_echo(db_conn, monkeypatch) -> None:
    stub = _install_stub(
        monkeypatch,
        vocab={
            "shipped": np.array([1, 0, 0], dtype=np.float32),
            "earned": np.array([0, 1, 0], dtype=np.float32),
        },
    )
    # Past post: pure "shipped".
    _seed_post_with_embedding(
        db_conn,
        text="Shipped the build today.",
        vec=stub._embed_text("Shipped the build today."),
    )
    # New draft: contains BOTH tokens → vec = normalize([1,1,0]) ~ [.707,.707,0]
    # → cosine vs [1,0,0] is ~.707 → close_echo (>= 0.78? no, but boundary).
    # Use thresholds tuned to land cleanly inside close_echo: lower the
    # close_echo threshold for this DB.
    db_conn.execute(
        "UPDATE settings SET value_json = '0.6' "
        "WHERE key = 'repetition_guard_close_echo_threshold'"
    )
    result = _guard.check(
        db_conn,
        draft_text="The thing I shipped earned its launch.",
        draft_kind="standalone",
    )
    assert result is not None
    assert result["label"] == "close_echo"
    assert 0.6 <= result["max_cosine"] < 0.92


def test_check_labels_distinct(db_conn, monkeypatch) -> None:
    stub = _install_stub(
        monkeypatch,
        vocab={
            "shipped": np.array([1, 0, 0], dtype=np.float32),
            "kitchen": np.array([0, 0, 1], dtype=np.float32),
        },
    )
    _seed_post_with_embedding(
        db_conn,
        text="Shipped the build today.",
        vec=stub._embed_text("Shipped the build today."),
    )
    result = _guard.check(
        db_conn,
        draft_text="The kitchen scan flow is finally usable.",
        draft_kind="standalone",
    )
    assert result is not None
    assert result["label"] == "distinct"


def test_check_returns_none_when_provider_unavailable(db_conn, monkeypatch) -> None:
    class UnavailableProvider(_StubProvider):
        def embed(self, texts):
            raise _embeddings.EmbeddingsUnavailable("API key missing")

    stub = UnavailableProvider(
        vocab={"shipped": np.array([1, 0, 0], dtype=np.float32)},
    )
    monkeypatch.setattr(_embeddings, "DEFAULT_PROVIDER", stub)
    _seed_post_with_embedding(
        db_conn,
        text="Shipped the build today.",
        vec=_StubProvider(vocab=stub.vocab)._embed_text("Shipped the build today."),
    )
    result = _guard.check(
        db_conn, draft_text="anything", draft_kind="standalone"
    )
    assert result is None


# ---------------------------------------------------------------------------
# Re-embed on source_text drift
# ---------------------------------------------------------------------------
def test_check_re_embeds_when_source_text_hash_mismatches(db_conn, monkeypatch) -> None:
    stub = _install_stub(
        monkeypatch,
        vocab={
            "shipped": np.array([1, 0, 0], dtype=np.float32),
            "kitchen": np.array([0, 0, 1], dtype=np.float32),
        },
    )
    # Seed an embedding tagged with a WRONG source_text_hash so the guard
    # detects drift and triggers an inline re-embed.
    post_id = _seed_post_with_embedding(
        db_conn,
        text="Shipped the build today.",
        vec=stub._embed_text("Shipped the build today."),
    )
    db_conn.execute(
        "UPDATE post_embeddings SET source_text_hash = 'stale_hash' WHERE post_id = ?",
        (post_id,),
    )
    db_conn.execute(
        "UPDATE posts SET text = 'Kitchen scan rewritten today.' WHERE id = ?",
        (post_id,),
    )

    result = _guard.check(
        db_conn,
        draft_text="The kitchen scan flow is finally usable.",
        draft_kind="standalone",
    )
    assert result is not None
    # The post text was rewritten to be about kitchens. Draft is also about
    # kitchens. After re-embed, cosine should be high (near_duplicate).
    assert result["label"] in ("near_duplicate", "close_echo")

    # And the BLOB was rewritten in place — its source_text_hash now
    # matches the new text.
    refreshed_hash = db_conn.execute(
        "SELECT source_text_hash FROM post_embeddings WHERE post_id = ?",
        (post_id,),
    ).fetchone()[0]
    assert refreshed_hash == _guard.text_hash("Kitchen scan rewritten today.")


# ---------------------------------------------------------------------------
# Status / settings reads
# ---------------------------------------------------------------------------
def test_status_reports_counts_and_thresholds(db_conn, monkeypatch) -> None:
    stub = _install_stub(monkeypatch, vocab={})
    _ = stub  # silence linter — we just need the provider swapped
    snapshot = _guard.status(db_conn)
    assert snapshot["provider"] == "stub-3d"
    assert snapshot["embedded_count"] == 0
    assert snapshot["near_duplicate_threshold"] == pytest.approx(0.92)
    assert snapshot["close_echo_threshold"] == pytest.approx(0.78)


def test_check_never_blocks_returns_dict_not_raises(db_conn, monkeypatch) -> None:
    """Contract: a successful check returns the dict; failures return None;
    neither path raises."""
    stub = _install_stub(monkeypatch, vocab={})
    _ = stub
    out = _guard.check(db_conn, draft_text="", draft_kind="standalone")
    assert out is None


# ---------------------------------------------------------------------------
# Integration with _save_draft_post — end-to-end happy path
# ---------------------------------------------------------------------------
def test_save_draft_post_writes_similarity_warning(db_conn, monkeypatch) -> None:
    from app.agent import tools as _tools

    stub = _install_stub(
        monkeypatch,
        vocab={"shipped": np.array([1, 0, 0], dtype=np.float32)},
    )
    _seed_post_with_embedding(
        db_conn,
        text="Shipped the build today.",
        vec=stub._embed_text("Shipped the build today."),
    )
    result = _tools._save_draft_post(
        db_conn,
        text="Shipped the build again today, with a few fixes.",
        pillar="build",
        audience="icp",
        cta="none",
    )
    # Score still wired.
    assert result["prepublish_label"] in ("weak", "viable", "strong")
    # Similarity warning persisted.
    warning_raw = db_conn.execute(
        "SELECT similarity_warning_json FROM agent_drafts WHERE id = ?",
        (result["draft_id"],),
    ).fetchone()[0]
    assert warning_raw is not None
    parsed = json.loads(warning_raw)
    assert parsed["label"] in ("near_duplicate", "close_echo", "distinct")
