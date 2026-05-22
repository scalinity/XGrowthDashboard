"""Repetition guard via embedding similarity (§28.13).

A soft check that fires at save_draft_* time. Embeds the new draft, cosine-
scans against `post_embeddings` rows whose parent post falls inside the
lookback window, and labels the result as
`near_duplicate | close_echo | distinct`.

Hard rules baked into this module:

  1. **Never blocks publish.** The guard's only output is the JSON the
     orchestrator writes to `agent_drafts.similarity_warning_json`. The
     §28.10 click-handler never reads that column. IWH + dark-pattern
     lint are the hard gates.
  2. **Graceful degradation.** Embedding provider unavailable
     (`EmbeddingsUnavailable`), no embedded rows in the lookback, or any
     numpy / SQL error → returns `None`. The orchestrator persists NULL
     and the draft proceeds.
  3. **Source-text drift detection.** If a post was edited after its
     embedding was written (`source_text_hash` mismatch), the guard
     re-embeds that single row inline before comparing. The on-disk
     `posts.text` is the source of truth; the BLOB catches up.

The similarity thresholds (`near_duplicate >= 0.92`, `close_echo >= 0.78`)
live in `settings` rows so they can be calibrated post-launch without a
code change. Lookback days is similarly a setting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass

import numpy as np

from app.agent import embeddings as _embeddings

_LOG = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 180
DEFAULT_NEAR_DUPLICATE = 0.92
DEFAULT_CLOSE_ECHO = 0.78


@dataclass(frozen=True)
class SimilarityWarning:
    max_cosine: float
    nearest_post_id: int
    nearest_text_excerpt: str
    label: str  # "near_duplicate" | "close_echo" | "distinct"

    def as_dict(self) -> dict:
        return {
            "max_cosine": round(self.max_cosine, 4),
            "nearest_post_id": int(self.nearest_post_id),
            "nearest_text_excerpt": self.nearest_text_excerpt,
            "label": self.label,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict())


# ---------------------------------------------------------------------------
# Settings reads.
# ---------------------------------------------------------------------------
def _get_setting_float(conn: sqlite3.Connection, key: str, default: float) -> float:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    try:
        return float(json.loads(row["value_json"]))
    except (json.JSONDecodeError, ValueError, TypeError):
        return default


def _get_setting_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    try:
        return int(json.loads(row["value_json"]))
    except (json.JSONDecodeError, ValueError, TypeError):
        return default


def get_thresholds(conn: sqlite3.Connection) -> tuple[float, float]:
    return (
        _get_setting_float(
            conn, "repetition_guard_near_duplicate_threshold", DEFAULT_NEAR_DUPLICATE
        ),
        _get_setting_float(
            conn, "repetition_guard_close_echo_threshold", DEFAULT_CLOSE_ECHO
        ),
    )


def get_lookback_days(conn: sqlite3.Connection) -> int:
    return _get_setting_int(
        conn, "repetition_guard_lookback_days", DEFAULT_LOOKBACK_DAYS
    )


# ---------------------------------------------------------------------------
# Hashing helper used by both the guard and the backfill script.
# ---------------------------------------------------------------------------
def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# DB read — corpus selection.
# ---------------------------------------------------------------------------
def _load_corpus(
    conn: sqlite3.Connection, *, lookback_days: int
) -> list[tuple[int, np.ndarray, str, str, int]]:
    """Return [(post_id, vector, text, source_text_hash, embedding_dim), ...].

    Rows are filtered to:
      - parent post is within the lookback window;
      - parent post has a non-null x_post_id (shipped, not a draft);
      - embedding_dim matches the current provider's dim (mismatches are
        skipped — operator must run `embed_posts.py --re-embed-all`).

    Perf note (P58R-24): the row-by-row blob_to_vector list-comp here
    is followed by a np.stack at call site (`check()`). For
    low-thousands corpora at 512-1536 dim float32, total allocation is
    a few MB and the loop dominates only marginally over the SQL fetch.
    If the corpus crosses ~10k posts, replace with a pre-allocated
    `np.empty((n, dim))` and `np.frombuffer` row-slice fill that skips
    the intermediate Python list + stack copy.
    """
    target_dim = _embeddings.DEFAULT_PROVIDER.embedding_dim
    rows = conn.execute(
        """
        SELECT pe.post_id, pe.embedding_blob, pe.embedding_dim,
               pe.source_text_hash, p.text
        FROM post_embeddings pe
        JOIN posts p ON p.id = pe.post_id
        WHERE p.x_post_id IS NOT NULL
          AND p.text IS NOT NULL
          AND date(COALESCE(p.created_at_utc, p.created_date))
              >= date('now', ?)
          AND pe.embedding_dim = ?
        """,
        (f"-{int(lookback_days)} days", int(target_dim)),
    ).fetchall()
    out: list[tuple[int, np.ndarray, str, str, int]] = []
    for r in rows:
        try:
            vec = _embeddings.blob_to_vector(r["embedding_blob"], dim=int(r["embedding_dim"]))
        except ValueError:
            # Row predates a model change; skip rather than crash.
            continue
        out.append(
            (int(r["post_id"]), vec, r["text"], r["source_text_hash"], int(r["embedding_dim"]))
        )
    return out


# ---------------------------------------------------------------------------
# Re-embed-on-drift helper.
# ---------------------------------------------------------------------------
def _refresh_stale_embedding(
    conn: sqlite3.Connection, *, post_id: int, current_text: str
) -> np.ndarray | None:
    """If posts.text has drifted since the embedding was written, re-embed.

    Returns the fresh vector on success, None if the provider is
    unavailable (in which case the guard treats the stale row as
    unusable and excludes it from comparison).
    """
    try:
        result = _embeddings.embed_one(current_text)
    except _embeddings.EmbeddingsUnavailable as exc:
        _LOG.warning("inline re-embed failed for post %s: %s", post_id, exc)
        return None
    fresh_vec = result.vectors[0]
    conn.execute(
        """
        UPDATE post_embeddings
        SET embedding_blob = ?, embedding_dim = ?, model_name = ?,
            model_version = ?, source_text_hash = ?,
            created_at_utc = datetime('now')
        WHERE post_id = ?
        """,
        (
            _embeddings.vector_to_blob(fresh_vec),
            int(result.embedding_dim),
            result.model_name,
            result.model_version,
            text_hash(current_text),
            int(post_id),
        ),
    )
    return fresh_vec


# ---------------------------------------------------------------------------
# Main entry point.
# ---------------------------------------------------------------------------
def check(
    conn: sqlite3.Connection,
    *,
    draft_text: str,
    draft_kind: str,  # noqa: ARG001 — reserved; replies vs standalones may diverge
    lookback_days: int | None = None,
) -> dict | None:
    """Run the cosine-similarity check. Returns the JSON-ready dict, or
    `None` when the guard cannot run (no embeddings yet, provider down,
    etc.) — caller persists NULL in that case.

    Side effect (P58R-26): writes to `post_embeddings` inside the
    caller's transaction via `_refresh_stale_embedding` when a corpus
    row's `source_text_hash` no longer matches its parent `posts.text`.
    If the caller's outer transaction rolls back (e.g. a CHECK
    violation on the new draft), the inline re-embed is dropped too —
    callers composing this inside narrower transactions should know
    that the re-embed is not durable on rollback.
    """
    if not draft_text or not draft_text.strip():
        return None
    effective_lookback = (
        int(lookback_days) if lookback_days is not None else get_lookback_days(conn)
    )
    near_dup_thr, close_echo_thr = get_thresholds(conn)

    corpus = _load_corpus(conn, lookback_days=effective_lookback)
    if not corpus:
        return None

    # Refresh any rows whose source_text_hash has drifted since they were
    # embedded. Mutating during a list comprehension is fine here — we
    # iterate by index and update DB out-of-band.
    refreshed: list[tuple[int, np.ndarray, str]] = []
    for post_id, vec, text, stored_hash, _dim in corpus:
        if text_hash(text) != stored_hash:
            fresh = _refresh_stale_embedding(conn, post_id=post_id, current_text=text)
            if fresh is None:
                # Skip this row entirely — comparing against a stale embed
                # would be silently wrong.
                continue
            refreshed.append((post_id, fresh, text))
        else:
            refreshed.append((post_id, vec, text))

    if not refreshed:
        return None

    # Embed the draft.
    try:
        draft_result = _embeddings.embed_one(draft_text)
    except _embeddings.EmbeddingsUnavailable as exc:
        _LOG.info("repetition_guard skipped: %s", exc)
        return None

    if draft_result.vectors.size == 0:
        return None
    draft_vec = draft_result.vectors[0]

    corpus_arr = np.stack([row[1] for row in refreshed], axis=0)
    sims = _embeddings.cosine_similarities(draft_vec, corpus_arr)

    nearest_idx = int(np.argmax(sims))
    max_cos = float(sims[nearest_idx])
    nearest_post_id, _vec, nearest_text = refreshed[nearest_idx]
    excerpt = (nearest_text or "").strip().replace("\n", " ")
    if len(excerpt) > 160:
        excerpt = excerpt[:157] + "…"

    if max_cos >= near_dup_thr:
        label = "near_duplicate"
    elif max_cos >= close_echo_thr:
        label = "close_echo"
    else:
        label = "distinct"

    warning = SimilarityWarning(
        max_cosine=max_cos,
        nearest_post_id=nearest_post_id,
        nearest_text_excerpt=excerpt,
        label=label,
    )
    return warning.as_dict()


# ---------------------------------------------------------------------------
# Status — Settings panel uses this.
# ---------------------------------------------------------------------------
def status(conn: sqlite3.Connection) -> dict:
    """Snapshot for Settings → Growth Agent → Repetition guard."""
    row_count = int(
        conn.execute("SELECT COUNT(*) FROM post_embeddings").fetchone()[0]
    )
    parent_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM posts WHERE x_post_id IS NOT NULL"
        ).fetchone()[0]
    )
    provider = _embeddings.DEFAULT_PROVIDER
    near_dup_thr, close_echo_thr = get_thresholds(conn)
    return {
        "embedded_count": row_count,
        "shipped_post_count": parent_count,
        "provider": provider.model_name,
        "provider_version": provider.model_version,
        "embedding_dim": provider.embedding_dim,
        "lookback_days": get_lookback_days(conn),
        "near_duplicate_threshold": near_dup_thr,
        "close_echo_threshold": close_echo_thr,
    }
