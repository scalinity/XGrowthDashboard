"""scripts/embed_posts.py — backfill `post_embeddings` (§28.13).

Resumable: skips rows that already have a `post_embeddings` row keyed to
their `posts.id`. Respects the configured provider's rate limit
(`provider.rate_limit_sleep_seconds` between batches).

Usage:

  uv run python scripts/embed_posts.py                  # incremental
  uv run python scripts/embed_posts.py --re-embed-all   # provider swap
  uv run python scripts/embed_posts.py --batch-size 8   # tune throughput
  uv run python scripts/embed_posts.py --db data/dashboard.db

The script writes one row per `posts.id` (PK on post_embeddings.post_id).
`--re-embed-all` re-writes existing rows in place — use after editing
DEFAULT_PROVIDER in `app/agent/embeddings.py`.

Rate-limit cooldown (P58R-12)
-----------------------------
`sleep_for_rate_limit()` runs between batches *within* one invocation
(after every batch except the last). It does NOT enforce a cooldown
across separate script invocations. If you re-run with `--re-embed-all`
in a tight loop (e.g. a wrapper script), back-to-back invocations can
exceed the provider's documented per-minute cap. For Voyage AI's
documented ~3 RPM ceiling on `voyage-3-lite`, sleep at least
20 seconds between invocations:

  uv run python scripts/embed_posts.py --re-embed-all && \\
    sleep 20 && \\
    uv run python scripts/embed_posts.py --re-embed-all
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent import embeddings as _embeddings  # noqa: E402
from app.agent.repetition_guard import text_hash  # noqa: E402
from app.db import DEFAULT_DB_PATH, connect  # noqa: E402


def _candidate_post_ids(conn: sqlite3.Connection, *, re_embed_all: bool) -> list[tuple[int, str]]:
    """Return [(post_id, text), ...] for rows that need embedding.

    Without `--re-embed-all`, skips posts that already have a
    `post_embeddings` row. With it, returns every shipped post.
    """
    if re_embed_all:
        rows = conn.execute(
            """
            SELECT id, text FROM posts
            WHERE x_post_id IS NOT NULL AND text IS NOT NULL AND text != ''
            ORDER BY id
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT p.id, p.text FROM posts p
            LEFT JOIN post_embeddings pe ON pe.post_id = p.id
            WHERE p.x_post_id IS NOT NULL
              AND p.text IS NOT NULL AND p.text != ''
              AND pe.post_id IS NULL
            ORDER BY p.id
            """
        ).fetchall()
    return [(int(r["id"]), str(r["text"])) for r in rows]


def _upsert_embedding(
    conn: sqlite3.Connection,
    *,
    post_id: int,
    text: str,
    result: _embeddings.EmbeddingResult,
    vec_index: int,
) -> None:
    vec = result.vectors[vec_index]
    blob = _embeddings.vector_to_blob(vec)
    conn.execute(
        """
        INSERT INTO post_embeddings
          (post_id, embedding_blob, embedding_dim, model_name, model_version,
           created_at_utc, source_text_hash)
        VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
        ON CONFLICT(post_id) DO UPDATE SET
          embedding_blob = excluded.embedding_blob,
          embedding_dim = excluded.embedding_dim,
          model_name = excluded.model_name,
          model_version = excluded.model_version,
          created_at_utc = datetime('now'),
          source_text_hash = excluded.source_text_hash
        """,
        (
            int(post_id),
            blob,
            int(result.embedding_dim),
            result.model_name,
            result.model_version,
            text_hash(text),
        ),
    )


def backfill(
    conn: sqlite3.Connection,
    *,
    batch_size: int = 8,
    re_embed_all: bool = False,
    verbose: bool = True,
) -> int:
    """Run the backfill. Returns the count of embeddings written."""
    candidates = _candidate_post_ids(conn, re_embed_all=re_embed_all)
    if not candidates:
        if verbose:
            print("nothing to embed — all shipped posts have embeddings.")
        return 0
    if verbose:
        provider = _embeddings.DEFAULT_PROVIDER
        print(
            f"embedding {len(candidates)} posts via {provider.model_name} "
            f"in batches of {batch_size}"
        )
    written = 0
    for i in range(0, len(candidates), batch_size):
        chunk = candidates[i : i + batch_size]
        texts = [c[1] for c in chunk]
        result = _embeddings.embed_batch(texts)
        for j, (post_id, text) in enumerate(chunk):
            _upsert_embedding(
                conn, post_id=post_id, text=text, result=result, vec_index=j
            )
        conn.commit()
        written += len(chunk)
        if verbose:
            print(f"  {written}/{len(candidates)} embedded")
        if i + batch_size < len(candidates):
            _embeddings.sleep_for_rate_limit()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--re-embed-all", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    conn = connect(Path(args.db))
    try:
        backfill(
            conn,
            batch_size=int(args.batch_size),
            re_embed_all=bool(args.re_embed_all),
            verbose=not args.quiet,
        )
    except _embeddings.EmbeddingsUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
