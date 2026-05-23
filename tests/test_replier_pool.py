"""Phase 5.9 / §28.20 — replier-pool candidate discovery.

Covers:

  1. parse_replier_paste — accepts handles-only, @handle: excerpt,
     blank-line-separated multi-line excerpts.
  2. thread_context_fit_score — 0..3 ladder from niche_person overlap.
  3. score_replier — composite recommended_action via the §29.3
     resolver.
  4. score_replier_pool — refuses when niche is undefined; happy path
     lands rows with source='replier_under_thread'; idempotent on
     (thread_url, handle) — re-pasting refreshes scores in place.
  5. Tool wrapper invokes the orchestrator correctly.
"""

from __future__ import annotations

import sqlite3

from app.agent import niche as _niche
from app.agent import replier_pool
from app.agent.tools import get_tool


# ---------------------------------------------------------------------------
# parse_replier_paste — lenient input shapes.
# ---------------------------------------------------------------------------
def test_parse_empty_returns_empty() -> None:
    assert replier_pool.parse_replier_paste("") == []
    assert replier_pool.parse_replier_paste("   \n  \n") == []


def test_parse_handles_only_block() -> None:
    out = replier_pool.parse_replier_paste("@alpha\n@beta\n@gamma")
    assert [r.handle for r in out] == ["alpha", "beta", "gamma"]
    assert all(r.text is None for r in out)


def test_parse_handle_with_excerpt_colon_separator() -> None:
    out = replier_pool.parse_replier_paste("@alpha: this is a great take")
    assert len(out) == 1
    assert out[0].handle == "alpha"
    assert out[0].text == "this is a great take"


def test_parse_multi_line_excerpt() -> None:
    payload = (
        "@alpha:\n"
        "first line of excerpt\n"
        "second line of excerpt\n"
        "\n"
        "@beta: a different replier"
    )
    out = replier_pool.parse_replier_paste(payload)
    assert len(out) == 2
    assert out[0].handle == "alpha"
    assert "first line" in out[0].text and "second line" in out[0].text
    assert out[1].handle == "beta"
    assert out[1].text == "a different replier"


# ---------------------------------------------------------------------------
# thread_context_fit_score — deterministic 0..3 ladder.
# ---------------------------------------------------------------------------
def test_fit_zero_when_no_overlap() -> None:
    assert replier_pool.thread_context_fit_score(
        "completely unrelated text", "educational creators"
    ) == 0


def test_fit_one_when_single_token_overlaps() -> None:
    out = replier_pool.thread_context_fit_score(
        "I'm a creators-first thinker", "educational creators"
    )
    assert out == 1


def test_fit_three_when_three_or_more_tokens_overlap() -> None:
    out = replier_pool.thread_context_fit_score(
        "educational creators teaching educational frameworks for creators",
        "educational creators teaching frameworks",
    )
    # niche tokens (non-stopword len>=3): educational, creators, teaching,
    # frameworks. All four appear in excerpt → cap at 3.
    assert out == 3


def test_fit_handles_empty_inputs() -> None:
    assert replier_pool.thread_context_fit_score("", "educational creators") == 0
    assert replier_pool.thread_context_fit_score("some text", "") == 0
    assert replier_pool.thread_context_fit_score(None, "anything") == 0


# ---------------------------------------------------------------------------
# score_replier — composite recommended_action via the §29.3 resolver.
# ---------------------------------------------------------------------------
def test_score_replier_with_strong_fit_recommends_reply_now() -> None:
    excerpt = replier_pool.ReplierExcerpt(
        handle="alpha",
        text="educational creators teaching frameworks daily",
    )
    cand = replier_pool.score_replier(
        excerpt, niche_person="educational creators teaching frameworks"
    )
    assert cand.thread_context_fit_score == 3
    assert cand.relevance_score == 3
    assert cand.reply_opportunity_score == 3
    # All four MVP dims >= 2 → reply_now per §29.3.
    assert cand.recommended_action_label == "reply_now"
    assert "thread_context_fit" in cand.score_rationale


def test_score_replier_with_zero_fit_skips() -> None:
    excerpt = replier_pool.ReplierExcerpt(handle="alpha", text="unrelated stuff")
    cand = replier_pool.score_replier(excerpt, niche_person="educational creators")
    assert cand.thread_context_fit_score == 0
    # relevance = 0 → resolver kicks 'skip'.
    assert cand.recommended_action_label == "skip"


def test_score_replier_handle_only_record() -> None:
    excerpt = replier_pool.ReplierExcerpt(handle="alpha", text=None)
    cand = replier_pool.score_replier(excerpt, niche_person="educational creators")
    assert cand.thread_context_fit_score == 0
    assert cand.recommended_action_label == "skip"


# ---------------------------------------------------------------------------
# score_replier_pool — persistence + idempotency.
# ---------------------------------------------------------------------------
def test_pool_refuses_when_niche_unset(db_conn: sqlite3.Connection) -> None:
    out = replier_pool.score_replier_pool(
        db_conn,
        thread_url="https://x.com/foo/status/1",
        replier_handles_or_excerpts="@bar",
    )
    assert "error" in out
    assert "niche" in out["error"].lower()
    n = db_conn.execute("SELECT COUNT(*) FROM reply_targets").fetchone()[0]
    assert n == 0


def test_pool_lands_rows_with_correct_source(db_conn: sqlite3.Connection) -> None:
    _niche.set_niche(db_conn, problem="x", person="educational creators")
    out = replier_pool.score_replier_pool(
        db_conn,
        thread_url="https://x.com/bigaccount/status/12345",
        replier_handles_or_excerpts=(
            "@alpha: educational creators are the niche I serve\n"
            "@beta: random unrelated content here"
        ),
    )
    assert "error" not in out
    assert out["created_count"] == 2
    assert out["updated_count"] == 0
    # Both rows landed with source='replier_under_thread'.
    rows = db_conn.execute(
        "SELECT source FROM reply_targets"
    ).fetchall()
    assert all(r["source"] == "replier_under_thread" for r in rows)
    # The on-niche replier should outrank the off-niche one.
    high_fit = next(
        c for c in out["candidates"] if c["handle"] == "alpha"
    )
    low_fit = next(c for c in out["candidates"] if c["handle"] == "beta")
    assert high_fit["thread_context_fit_score"] > low_fit["thread_context_fit_score"]


def test_pool_is_idempotent_on_thread_handle(db_conn: sqlite3.Connection) -> None:
    _niche.set_niche(db_conn, problem="x", person="educational creators")
    first = replier_pool.score_replier_pool(
        db_conn,
        thread_url="https://x.com/bigaccount/status/9",
        replier_handles_or_excerpts="@alpha: educational creators here",
    )
    second = replier_pool.score_replier_pool(
        db_conn,
        thread_url="https://x.com/bigaccount/status/9",
        replier_handles_or_excerpts="@alpha: educational creators here",
    )
    assert first["created_count"] == 1
    assert second["created_count"] == 0
    assert second["updated_count"] == 1
    # Still exactly one reply_targets row for that anchor.
    n = db_conn.execute(
        "SELECT COUNT(*) FROM reply_targets WHERE source = 'replier_under_thread'"
    ).fetchone()[0]
    assert n == 1


def test_handle_less_anchor_is_process_stable(
    db_conn: sqlite3.Connection,
) -> None:
    """P59A-C2 regression: re-pasting the same handle-less excerpt across
    process restarts must hit the same reply_targets row, not insert a
    duplicate. Before the sha1 fix, Python's built-in hash() randomized
    the anchor per interpreter and the idempotency contract failed for
    excerpts without a handle.

    Static check (we can't actually restart the interpreter mid-test):
    invoke score_replier_pool twice with the same handle-less payload
    and assert exactly one reply_targets row results. With the old
    hash() this would still pass within one process — the stronger
    static check is that the produced anchor uses hashlib, which we
    verify by checking the URL fragment length (sha1[:12] = 12 hex
    chars) and that it's deterministic across calls.
    """
    _niche.set_niche(db_conn, problem="x", person="educational creators")
    payload = "educational creators are aligned with the niche"
    out1 = replier_pool.score_replier_pool(
        db_conn,
        thread_url="https://x.com/big/status/42",
        replier_handles_or_excerpts=payload,
    )
    out2 = replier_pool.score_replier_pool(
        db_conn,
        thread_url="https://x.com/big/status/42",
        replier_handles_or_excerpts=payload,
    )
    assert out1["created_count"] == 1
    assert out2["created_count"] == 0
    assert out2["updated_count"] == 1
    # Confirm the URL fragment is the sha1[:12] shape (12 hex chars).
    url = db_conn.execute(
        "SELECT target_post_url FROM reply_targets WHERE source = 'replier_under_thread'"
    ).fetchone()[0]
    fragment = url.split("#replier=_", 1)[1]
    assert len(fragment) == 12
    assert all(c in "0123456789abcdef" for c in fragment)


def test_pool_handles_unparseable_payload(db_conn: sqlite3.Connection) -> None:
    _niche.set_niche(db_conn, problem="x", person="y")
    out = replier_pool.score_replier_pool(
        db_conn,
        thread_url="https://x.com/foo/status/1",
        replier_handles_or_excerpts="   \n   ",
    )
    assert "error" in out
    assert out["created_count"] == 0


# ---------------------------------------------------------------------------
# Tool wrapper.
# ---------------------------------------------------------------------------
def test_tool_wrapper_invokes_orchestrator(db_conn: sqlite3.Connection) -> None:
    _niche.set_niche(db_conn, problem="x", person="educational creators")
    tool = get_tool("score_replier_pool")
    out = tool.handler(
        db_conn,
        thread_url="https://x.com/bigaccount/status/77",
        replier_handles_or_excerpts_json="@alpha: educational creators are here",
        lookback_minutes=30,
    )
    assert "error" not in out
    assert out["created_count"] == 1


def test_tool_in_registry_has_required_thread_url_and_payload() -> None:
    """Post-RV2-1: thread_url remains required, but the paste payload is
    NOT required because auto_scan=True (Phase 7 X API path) makes it
    optional. The function defaults it to empty string and falls back to
    the xurl /2/tweets/search/recent path."""
    tool = get_tool("score_replier_pool")
    assert "thread_url" in tool.input_schema["required"]
    # The paste payload is still a declared input — just no longer required.
    assert "replier_handles_or_excerpts_json" in tool.input_schema["properties"]
    # RV2-1: auto_scan flag must be declared so the agent can trigger it.
    assert "auto_scan" in tool.input_schema["properties"]
    assert tool.input_schema["properties"]["auto_scan"]["type"] == "boolean"
