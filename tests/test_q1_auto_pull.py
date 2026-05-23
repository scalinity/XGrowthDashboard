"""Phase 7 Q1 promotions — auto_scan / auto_pull round-trip tests.

Three surfaces share the Phase 7 xurl wrapper:

- §28.20 score_replier_pool(auto_scan=True) → /2/tweets/search/recent
- §28.24 _analyze_account_to_dict(auto_pull=True) → /2/users/by/username
  AND /2/users/{id}/tweets
- §28.25 _audit_profile_to_dict(auto_pull_bio=True) → /2/users/by/username

All three: manual fallback path (auto_scan=False / auto_pull=False) is
the always-available default and is tested separately. The auto-pull
path returns a 'failed' status dict on X API failure — the caller is
expected to prompt for paste rather than silently fall back.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import x_client
from app.agent.replier_pool import (
    _extract_x_post_id_from_url,
    score_replier_pool,
)
from app.db import apply_migrations, connect
from scripts.seed_settings import seed_settings


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "q1.db"
    conn = connect(db_path)
    apply_migrations(conn)
    seed_settings(conn)
    # Niche must be defined for replier-pool scoring.
    import json as _json
    conn.execute(
        "UPDATE settings SET value_json = ? WHERE key = 'niche_problem'",
        (_json.dumps("parents need a meal-planning app"),),
    )
    conn.execute(
        "UPDATE settings SET value_json = ? WHERE key = 'niche_person'",
        (_json.dumps("working parents juggling weeknight dinners"),),
    )
    yield conn
    conn.close()


def _fake_response(body, status=200):
    return x_client.XApiResponse(
        status_code=status, body=body, raw_response_id=None,
        endpoint="(test)", method="GET", elapsed_seconds=0.001,
    )


# ---------------------------------------------------------------------------
# §28.20 — replier-pool auto_scan.
# ---------------------------------------------------------------------------
def test_extract_x_post_id_from_url_handles_x_and_twitter():
    assert _extract_x_post_id_from_url(
        "https://x.com/danny/status/1234567890"
    ) == "1234567890"
    assert _extract_x_post_id_from_url(
        "https://twitter.com/someone/status/987"
    ) == "987"
    assert _extract_x_post_id_from_url("not a url") is None


def test_score_replier_pool_auto_scan_round_trip(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_body = {
        "data": [
            {
                "id": "111",
                "author_id": "10",
                "text": "I struggle with weeknight dinners every single week",
            },
            {
                "id": "112",
                "author_id": "11",
                "text": "Yeah meal planning is a real problem for working parents",
            },
        ],
        "includes": {
            "users": [
                {"id": "10", "username": "parent_a"},
                {"id": "11", "username": "parent_b"},
            ]
        },
    }
    monkeypatch.setattr(x_client, "request", lambda *a, **kw: _fake_response(fake_body))
    result = score_replier_pool(
        db_conn,
        thread_url="https://x.com/big_account/status/9001",
        auto_scan=True,
    )
    assert result.get("error") is None, result
    assert result["created_count"] >= 1
    rows = db_conn.execute(
        "SELECT target_author_handle FROM reply_targets "
        "WHERE target_post_url LIKE 'https://x.com/big_account/status/9001%'"
    ).fetchall()
    assert len(rows) >= 1


def test_score_replier_pool_auto_scan_falls_back_when_api_fails(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rate_limit(*a, **kw):
        raise x_client.XApiRateLimited("rate-limited", retry_after_seconds=60.0)

    monkeypatch.setattr(x_client, "request", rate_limit)
    result = score_replier_pool(
        db_conn,
        thread_url="https://x.com/big_account/status/9002",
        auto_scan=True,
    )
    assert "fall back to manual paste" in (result.get("error") or "")
    assert result["created_count"] == 0


def test_score_replier_pool_manual_paste_still_works(
    db_conn: sqlite3.Connection,
) -> None:
    """auto_scan=False path is unaffected — paste flow remains intact."""
    result = score_replier_pool(
        db_conn,
        thread_url="https://x.com/big_account/status/9003",
        replier_handles_or_excerpts="@parent_a: weeknight dinners are exhausting",
        auto_scan=False,
    )
    assert result.get("error") is None, result
    assert result["created_count"] == 1


# ---------------------------------------------------------------------------
# §28.24 — Account Researcher auto_pull.
# ---------------------------------------------------------------------------
def test_analyze_account_auto_pull_populates_bio_and_recent_posts(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent.tools import _analyze_account_to_dict

    # Two-call sequence: user lookup, then tweets lookup.
    call_state = {"n": 0}

    def fake_request(endpoint, **kw):
        call_state["n"] += 1
        if "users/by/username" in endpoint:
            return _fake_response({
                "data": {
                    "id": "777",
                    "username": "target_account",
                    "name": "Target Account",
                    "description": "I build kitchen tools for busy parents",
                    "public_metrics": {"followers_count": 4_500, "tweet_count": 500},
                }
            })
        if "/2/users/777/tweets" in endpoint:
            return _fake_response({
                "data": [
                    {"id": "1", "text": "first sample post about meal planning"},
                    {"id": "2", "text": "second sample post about kitchen UX"},
                ]
            })
        pytest.fail(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(x_client, "request", fake_request)

    # Stub the analyze call so we don't hit Anthropic.
    from app.agent import account_research as _ar

    def fake_analyze(**kw):
        assert "build kitchen tools" in kw["target_bio_text"]
        assert "first sample post" in kw["target_recent_posts_text"]
        return _ar.AccountResearchAnalysis(
            posting_patterns=_ar.PostingPatterns(
                cadence="daily", topics=["meal planning"], common_hooks=[]
            ),
            positioning=_ar.Positioning(
                primary_audience="parents", value_proposition="meal kits",
                voice_markers=[],
            ),
            reply_strategy=_ar.ReplyStrategy(
                best_entry_topics=["dinner prep"], tone_to_match="warm",
                what_to_avoid=[],
            ),
            niche_alignment_with_daniel=_ar.NicheAlignment(
                overlap_score=2, rationale="aligned"
            ),
            model_used="claude-opus-4-7", tokens_used=100,
            target_handle="@target_account",
        )

    monkeypatch.setattr(_ar, "analyze", fake_analyze)
    monkeypatch.setattr(_ar, "save", lambda conn, **kw: 42)

    result = _analyze_account_to_dict(
        db_conn,
        target_handle="@target_account",
        target_url=None,
        target_display_name=None,
        auto_pull=True,
    )
    assert result["status"] == "saved"
    assert call_state["n"] == 2  # both endpoints invoked


def test_analyze_account_auto_pull_failure_returns_failed_status(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent.tools import _analyze_account_to_dict

    def fail(*a, **kw):
        raise x_client.XApiUnavailable("xurl not authenticated")

    monkeypatch.setattr(x_client, "request", fail)
    result = _analyze_account_to_dict(
        db_conn,
        target_handle="@target_account",
        target_url=None,
        target_display_name=None,
        auto_pull=True,
    )
    assert result["status"] == "failed"
    assert "fall back to manual paste" in result["error"]


def test_analyze_account_paste_path_still_works(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto_pull=False (default) — paste-driven path unaffected by Phase 7."""
    from app.agent import account_research as _ar
    from app.agent.tools import _analyze_account_to_dict

    monkeypatch.setattr(
        x_client, "request",
        lambda *a, **kw: pytest.fail("paste path must not call X API"),
    )
    monkeypatch.setattr(
        _ar, "analyze",
        lambda **kw: _ar.AccountResearchAnalysis(
            posting_patterns=_ar.PostingPatterns(
                cadence="weekly", topics=[], common_hooks=[]
            ),
            positioning=_ar.Positioning(
                primary_audience="x", value_proposition="x", voice_markers=[]
            ),
            reply_strategy=_ar.ReplyStrategy(
                best_entry_topics=[], tone_to_match="x", what_to_avoid=[]
            ),
            niche_alignment_with_daniel=_ar.NicheAlignment(
                overlap_score=1, rationale="x"
            ),
            model_used="haiku", tokens_used=10,
            target_handle="@x",
        ),
    )
    monkeypatch.setattr(_ar, "save", lambda conn, **kw: 99)
    result = _analyze_account_to_dict(
        db_conn,
        target_handle="@x",
        target_bio_text="pasted bio",
        target_recent_posts_text="pasted recent post",
        target_url=None,
        target_display_name=None,
    )
    assert result["status"] == "saved"


# ---------------------------------------------------------------------------
# §28.25 — Profile Audit bio auto_pull.
# ---------------------------------------------------------------------------
def test_audit_profile_bio_auto_pull_populates_from_x_api(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent.tools import _audit_profile_to_dict

    pulled = {"bio": ""}

    def fake_request(endpoint, **kw):
        assert "/2/users/by/username/" in endpoint
        assert "?user.fields=description" in endpoint
        return _fake_response({
            "data": {
                "id": "1",
                "username": "dannyscalant",
                "description": "build in public — meal planning app for parents",
            }
        })

    monkeypatch.setattr(x_client, "request", fake_request)

    # Stub the profile_audit call so we don't hit Anthropic.
    from app.agent import profile_audit as _pa

    class _StubAnalysis:
        overall_consistency_score = 3
        top_three_actions = ["a1", "a2", "a3"]
        tokens_used = 50

        def to_dict(self):
            return {"ok": True}

    def fake_audit(conn, *, bio_text, **kw):
        pulled["bio"] = bio_text
        return _StubAnalysis(), {"snapshot": True}

    monkeypatch.setattr(_pa, "audit", fake_audit)
    monkeypatch.setattr(_pa, "save", lambda conn, **kw: 7)

    result = _audit_profile_to_dict(
        db_conn,
        bio_text="",
        pinned_post_text="my pinned post",
        recent_post_window_days=30,
        pinned_post_id=None,
        auto_pull_bio=True,
    )
    assert result["status"] == "saved"
    assert "meal planning app" in pulled["bio"]


def test_audit_profile_bio_auto_pull_failure_returns_failed_status(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent.tools import _audit_profile_to_dict

    def fail(*a, **kw):
        raise x_client.XApiUnavailable("xurl not authenticated")

    monkeypatch.setattr(x_client, "request", fail)
    result = _audit_profile_to_dict(
        db_conn,
        bio_text="",
        pinned_post_text="my pinned post",
        recent_post_window_days=30,
        pinned_post_id=None,
        auto_pull_bio=True,
    )
    assert result["status"] == "failed"
    assert "fall back to paste" in result["error"]


def test_audit_profile_explicit_bio_text_wins_over_auto_pull(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When bio_text is supplied explicitly AND auto_pull_bio=True, the
    paste wins — the X API call is skipped entirely."""
    from app.agent.tools import _audit_profile_to_dict
    from app.agent import profile_audit as _pa

    pulled = {"bio": ""}

    monkeypatch.setattr(
        x_client, "request",
        lambda *a, **kw: pytest.fail("must not call X API when paste is supplied"),
    )

    class _StubAnalysis:
        overall_consistency_score = 2
        top_three_actions = ["a"]
        tokens_used = 10
        def to_dict(self):
            return {}

    def fake_audit(conn, *, bio_text, **kw):
        pulled["bio"] = bio_text
        return _StubAnalysis(), {}

    monkeypatch.setattr(_pa, "audit", fake_audit)
    monkeypatch.setattr(_pa, "save", lambda conn, **kw: 1)

    result = _audit_profile_to_dict(
        db_conn,
        bio_text="paste-supplied bio with annotations",
        pinned_post_text="my pinned post",
        recent_post_window_days=30,
        pinned_post_id=None,
        auto_pull_bio=True,  # ignored because bio_text is non-empty
    )
    assert result["status"] == "saved"
    assert pulled["bio"] == "paste-supplied bio with annotations"
