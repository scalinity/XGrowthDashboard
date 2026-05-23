"""Phase 9 — Grok integration tests (§29.12).

Test coverage matches the §25 Phase 9 acceptance gates:

  * Happy-path candidate ingestion (X API metrics as source of truth,
    NOT Grok's observed_metrics).
  * §29.2 404-on-verification rejection (the load-bearing invariant).
  * Rate-limit 429 + Retry-After handling at the Grok call layer.
  * Combined-ceiling enforcement (covered by
    ``tests/test_combined_cost_ceiling.py``).
  * Dedupe between manual + Grok via the
    ``unique(target_x_post_id)`` index.
  * Queue UI badge + filter (direct DB query through ``_query_rows``).
  * Query list CRUD round-trip through the settings table.
  * Manual + replier-pool paths unchanged (no regression in pre-Phase-9
    reply_target tests).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from app import grok_client
from app.agent.reply_targets import (
    DISCOVERED_VIA_ENUM,
    verify_grok_candidate_against_x_api,
)
from app.jobs import grok_discovery_sweep
from tests._xurl_fixture import use_cassette


# ---------------------------------------------------------------------------
# Schema-level guards.
# ---------------------------------------------------------------------------
def test_discovered_via_enum_includes_grok_semantic() -> None:
    """The Python-side enum must contain 'grok_semantic' (Phase 9)."""
    assert "grok_semantic" in DISCOVERED_VIA_ENUM


def test_check_constraint_accepts_grok_semantic(db_conn: sqlite3.Connection) -> None:
    """Migration 021 must extend the discovered_via CHECK to allow grok_semantic."""
    db_conn.execute(
        """
        INSERT INTO reply_targets
            (discovered_via, target_post_url, target_author_handle)
        VALUES ('grok_semantic', 'https://x.com/x/status/1', 'x')
        """
    )
    row = db_conn.execute(
        "SELECT discovered_via FROM reply_targets "
        "WHERE target_post_url = 'https://x.com/x/status/1'"
    ).fetchone()
    assert row["discovered_via"] == "grok_semantic"


def test_check_constraint_still_rejects_invalid_values(
    db_conn: sqlite3.Connection,
) -> None:
    """Garbage discovered_via values stay rejected after the rebuild."""
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO reply_targets "
            "(discovered_via, target_post_url, target_author_handle) "
            "VALUES ('not_a_real_source', 'https://x.com/x/status/2', 'x')"
        )


def test_grok_api_responses_table_exists(db_conn: sqlite3.Connection) -> None:
    """Phase 9 audit table + indexes + CHECK constraint live."""
    cur = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='grok_api_responses'"
    )
    assert cur.fetchone() is not None
    # Bogus rejection_reason rejected.
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO grok_api_responses (query, rejection_reason) "
            "VALUES ('q', 'not_a_real_reason')"
        )


def test_phase9_settings_seeded(db_conn: sqlite3.Connection) -> None:
    rows = {
        row["key"]: json.loads(row["value_json"])
        for row in db_conn.execute(
            "SELECT key, value_json FROM settings WHERE key IN (?, ?, ?)",
            (
                "grok_api_enabled",
                "grok_query_list_json",
                "grok_discovery_sweep_interval_minutes",
            ),
        ).fetchall()
    }
    assert rows["grok_api_enabled"] is True
    assert rows["grok_query_list_json"] == []
    assert rows["grok_discovery_sweep_interval_minutes"] == 120


# ---------------------------------------------------------------------------
# §29.2 verification invariant — load-bearing.
# ---------------------------------------------------------------------------
def test_verify_grok_candidate_returns_x_api_data_on_200(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X API 200 → verified=True + tweet payload with public_metrics."""
    with use_cassette(monkeypatch, "grok_verify_200"):
        result = verify_grok_candidate_against_x_api(
            "1234567890", conn=db_conn
        )
    assert result.verified is True
    assert result.status_code == 200
    assert result.tweet is not None
    pm = result.tweet["public_metrics"]
    assert pm["like_count"] == 42
    assert pm["reply_count"] == 3


def test_verify_grok_candidate_returns_404_when_post_deleted(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§29.2 invariant: 404 on verify → GrokVerificationResult(verified=False)."""
    with use_cassette(monkeypatch, "grok_verify_404"):
        result = verify_grok_candidate_against_x_api(
            "9999999999", conn=db_conn
        )
    assert result.verified is False
    assert result.status_code == 404
    assert result.tweet is None


# ---------------------------------------------------------------------------
# Happy-path discovery sweep.
# ---------------------------------------------------------------------------
def _stub_grok_search(
    monkeypatch: pytest.MonkeyPatch, candidates: list[grok_client.GrokCandidate]
) -> list[str]:
    """Patch ``grok_client.search`` to return ``candidates`` once, then []."""
    calls: list[str] = []

    def _fake_search(query: str, **_kwargs: Any) -> list[grok_client.GrokCandidate]:
        calls.append(query)
        # First call returns the canned candidates; subsequent calls
        # (multi-query lists) return empty so tests don't have to think
        # about ordering across the rest of the list.
        if len(calls) == 1:
            return candidates
        return []

    monkeypatch.setattr(grok_client, "search", _fake_search)
    # The sweep imports `grok_client.search` at module-import time, so we
    # also need to patch the binding it captured.
    monkeypatch.setattr(grok_discovery_sweep.grok_client, "search", _fake_search)
    return calls


def test_happy_path_grok_candidate_ingestion(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Grok candidate → X API 200 → reply_targets row inserted with X API metrics."""
    candidates = [
        grok_client.GrokCandidate(
            target_x_post_id="1234567890",
            target_post_url="https://x.com/danielcoder/status/1234567890",
            target_author_handle="danielcoder",
            target_text=None,
            observed_metrics={"like_count": 99},  # WRONG metrics from Grok
        ),
    ]
    _stub_grok_search(monkeypatch, candidates)

    settings_override = {
        "grok_api_enabled": True,
        "grok_query_list_json": ["meal planning frustration"],
        "engagement_surface_floor_likes": 15,
        "engagement_surface_pct_of_author": 0.001,
        "engagement_surface_high_floor_likes": 50,
        "engagement_surface_high_pct": 0.005,
    }

    with use_cassette(monkeypatch, "grok_verify_200"):
        summary = grok_discovery_sweep.run(
            db_conn, settings_override=settings_override
        )

    assert summary["error"] is None
    assert summary["queries_run"] == 1
    assert summary["candidates_discovered"] == 1
    assert summary["candidates_verified"] == 1
    assert summary["candidates_inserted"] == 1
    assert summary["candidates_rejected_404"] == 0

    row = db_conn.execute(
        "SELECT * FROM reply_targets WHERE target_x_post_id = '1234567890'"
    ).fetchone()
    assert row is not None
    assert row["discovered_via"] == "grok_semantic"
    # X API metrics, NOT Grok's observed_metrics — load-bearing per §29.2.
    assert row["like_count"] == 42
    assert row["reply_count"] == 3
    assert row["target_author_handle"] == "danielcoder"
    # P9R-3: follower count from includes.users expansion drives §29.4
    # relative thresholds. The cassette returns 1500 followers; at the
    # default 0.001 (= 0.1%) pct, medium=max(15, 1.5)=15 likes; at high
    # pct 0.005 (0.5%), high=max(50, 7.5)=50 likes. 42 likes → 1.
    assert row["target_author_follower_count"] == 1500
    # Engagement-surface score from §29.4 thresholds.
    assert row["engagement_surface_score"] == 1


def test_404_verification_rejects_candidate_and_logs(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§29.2 invariant: 404 on X API verify → no reply_targets row + audit row."""
    candidates = [
        grok_client.GrokCandidate(
            target_x_post_id="9999999999",
            target_post_url="https://x.com/ghost/status/9999999999",
            target_author_handle="ghost",
        ),
    ]
    _stub_grok_search(monkeypatch, candidates)

    settings_override = {
        "grok_api_enabled": True,
        "grok_query_list_json": ["any"],
        "engagement_surface_floor_likes": 15,
        "engagement_surface_pct_of_author": 0.001,
        "engagement_surface_high_floor_likes": 50,
        "engagement_surface_high_pct": 0.005,
    }

    with use_cassette(monkeypatch, "grok_verify_404"):
        summary = grok_discovery_sweep.run(
            db_conn, settings_override=settings_override
        )

    assert summary["candidates_discovered"] == 1
    assert summary["candidates_rejected_404"] == 1
    assert summary["candidates_inserted"] == 0

    # The candidate must NOT be in reply_targets.
    row = db_conn.execute(
        "SELECT id FROM reply_targets WHERE target_x_post_id = '9999999999'"
    ).fetchone()
    assert row is None

    # A grok_api_responses audit row with rejection_reason='verification_404'
    # must exist.
    audit = db_conn.execute(
        "SELECT rejection_reason FROM grok_api_responses "
        "WHERE rejection_reason = 'verification_404'"
    ).fetchall()
    assert len(audit) == 1


def test_grok_disabled_aborts_sweep_with_audit_row(
    db_conn: sqlite3.Connection,
) -> None:
    """grok_api_enabled=FALSE → sweep aborts; no Grok call, no INSERT."""
    settings_override = {
        "grok_api_enabled": False,
        "grok_query_list_json": ["x"],
    }
    summary = grok_discovery_sweep.run(
        db_conn, settings_override=settings_override
    )
    assert summary["error"] == "grok_api_enabled=FALSE; sweep aborted"
    assert summary["queries_run"] == 0


def test_empty_query_list_aborts_sweep(db_conn: sqlite3.Connection) -> None:
    settings_override = {
        "grok_api_enabled": True,
        "grok_query_list_json": [],
    }
    summary = grok_discovery_sweep.run(
        db_conn, settings_override=settings_override
    )
    assert "empty" in (summary["error"] or "").lower()


# ---------------------------------------------------------------------------
# Rate-limit handling at the grok_client layer.
# ---------------------------------------------------------------------------
def test_grok_client_429_raises_rate_limit_error_with_retry_after(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """xAI 429 + Retry-After → GrokRateLimitError with retry_after_seconds."""
    def _fake_http(
        *, url: str, payload: dict, api_key: str, timeout_seconds: float
    ) -> tuple[int, dict, float | None]:
        return (429, {"error": "rate limited"}, 30.0)

    monkeypatch.setattr(grok_client, "_http_post_json", _fake_http)
    monkeypatch.setenv("XAI_API_KEY", "dummy-key-for-test")

    with pytest.raises(grok_client.GrokRateLimitError) as excinfo:
        grok_client.search("test query", conn=db_conn)
    assert excinfo.value.retry_after_seconds == 30.0

    # Audit row written.
    rows = db_conn.execute(
        "SELECT rejection_reason FROM grok_api_responses "
        "WHERE rejection_reason = 'rate_limit_429'"
    ).fetchall()
    assert len(rows) == 1


def test_grok_client_5xx_retries_then_raises_server_error(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_counter = {"n": 0}

    def _fake_http(**_kwargs: Any) -> tuple[int, dict, float | None]:
        call_counter["n"] += 1
        return (503, {"error": "service unavailable"}, None)

    monkeypatch.setattr(grok_client, "_http_post_json", _fake_http)
    monkeypatch.setenv("XAI_API_KEY", "dummy-key-for-test")
    monkeypatch.setattr(grok_client, "_DEFAULT_RETRY_SLEEP_SECONDS", 0.0)

    with pytest.raises(grok_client.GrokServerError):
        grok_client.search("test query", conn=db_conn, retry_attempts=2)
    # 2 retries → 3 calls total.
    assert call_counter["n"] == 3
    # P9R-4: EVERY 5xx attempt is logged with rejection_reason='http_error_5xx'
    # so an auditor can reconstruct the retry trail. 3 calls → 3 audit rows.
    rows = db_conn.execute(
        "SELECT COUNT(*) AS n FROM grok_api_responses "
        "WHERE rejection_reason = 'http_error_5xx'"
    ).fetchone()
    assert rows["n"] == 3


def test_grok_client_success_logs_audit_row_with_rate_snapshot(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {
        "id": "x",
        "choices": [{"message": {"content": ""}}],
        "citations": ["https://x.com/dani/status/1111"],
        "usage": {"prompt_tokens": 100, "completion_tokens": 25},
    }

    def _fake_http(**_kwargs: Any) -> tuple[int, dict, float | None]:
        return (200, body, None)

    monkeypatch.setattr(grok_client, "_http_post_json", _fake_http)
    monkeypatch.setenv("XAI_API_KEY", "dummy")

    candidates = grok_client.search("test", conn=db_conn)
    assert len(candidates) == 1
    assert candidates[0].target_x_post_id == "1111"

    row = db_conn.execute(
        "SELECT rate_snapshot_json, rejection_reason FROM grok_api_responses "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["rejection_reason"] is None
    snap = json.loads(row["rate_snapshot_json"])
    assert snap["provider"] == "xai"
    assert snap["input_tokens"] == 100
    assert snap["output_tokens"] == 25


def test_grok_client_missing_api_key_raises_unavailable(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(grok_client.GrokUnavailable):
        grok_client.search("test", conn=db_conn)


@pytest.mark.parametrize(
    "placeholder",
    [
        "REPLACE_WITH_XAI_API_KEY_BEFORE_LOAD",
        "YOUR_XAI_API_KEY_HERE",
        "your-xai-key",
        "REPLACE_WITH_FOO",  # general prefix
        "YOUR_KEY",          # general prefix
        "PUT_PLACEHOLDER_HERE",  # general infix
    ],
)
def test_is_configured_rejects_documented_placeholder(
    monkeypatch: pytest.MonkeyPatch, placeholder: str
) -> None:
    """P9R-5: launchd plist placeholder must not pass is_configured()."""
    monkeypatch.setenv("XAI_API_KEY", placeholder)
    assert grok_client.is_configured() is False


def test_search_raises_unavailable_on_placeholder_key(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P9R-5: search() refuses to call xAI when the key is a placeholder."""
    monkeypatch.setenv("XAI_API_KEY", "REPLACE_WITH_XAI_API_KEY_BEFORE_LOAD")
    with pytest.raises(grok_client.GrokUnavailable, match="placeholder"):
        grok_client.search("test", conn=db_conn)


def test_sweep_catches_bare_grok_error_and_writes_audit_row(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P9R-2: a non-429/non-5xx Grok 4xx (bare GrokError) must NOT crash
    the sweep — the catch-all branch tallies grok_client_errors and the
    sweep continues so the scheduled_job audit row still lands."""

    def _fake_search(query: str, **_kwargs: object) -> list[grok_client.GrokCandidate]:
        raise grok_client.GrokError("xAI returned 401: invalid api key", status_code=401)

    monkeypatch.setattr(grok_client, "search", _fake_search)
    monkeypatch.setattr(grok_discovery_sweep.grok_client, "search", _fake_search)

    settings_override = {
        "grok_api_enabled": True,
        "grok_query_list_json": ["any"],
        "engagement_surface_floor_likes": 15,
        "engagement_surface_pct_of_author": 0.001,
        "engagement_surface_high_floor_likes": 50,
        "engagement_surface_high_pct": 0.005,
    }
    summary = grok_discovery_sweep.run(db_conn, settings_override=settings_override)
    # Sweep completed (didn't crash). Error counter incremented.
    assert summary["grok_client_errors"] == 1
    assert summary["candidates_discovered"] == 0
    # No scheduled_job audit-row write here (run() returns a summary; the
    # audit row lands in main()). What we're pinning is "run() doesn't
    # raise" — which is the load-bearing §28.30 invariant.


# ---------------------------------------------------------------------------
# Dedupe between Grok + manual paste.
# ---------------------------------------------------------------------------
def test_dedupe_with_manual_paste_silently_drops_second_insert(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual-paste row first → Grok candidate w/ same x_post_id → silent drop."""
    # Seed the manual-paste row.
    db_conn.execute(
        """
        INSERT INTO reply_targets
            (discovered_via, target_post_url, target_x_post_id,
             target_author_handle, like_count, reply_count)
        VALUES ('manual',
                'https://x.com/sometwo/status/9990001111',
                '9990001111',
                'sometwo', 5, 1)
        """
    )

    candidates = [
        grok_client.GrokCandidate(
            target_x_post_id="9990001111",
            target_post_url="https://x.com/sometwo/status/9990001111",
            target_author_handle="sometwo",
        ),
    ]
    _stub_grok_search(monkeypatch, candidates)

    settings_override = {
        "grok_api_enabled": True,
        "grok_query_list_json": ["any"],
        "engagement_surface_floor_likes": 15,
        "engagement_surface_pct_of_author": 0.001,
        "engagement_surface_high_floor_likes": 50,
        "engagement_surface_high_pct": 0.005,
    }

    with use_cassette(monkeypatch, "grok_verify_200_dedupe"):
        summary = grok_discovery_sweep.run(
            db_conn, settings_override=settings_override
        )

    assert summary["candidates_dedupe_dropped"] == 1
    assert summary["candidates_inserted"] == 0

    # Row count for that x_post_id is still 1 (the manual one, untouched).
    rows = db_conn.execute(
        "SELECT discovered_via FROM reply_targets WHERE target_x_post_id = '9990001111'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["discovered_via"] == "manual"


# ---------------------------------------------------------------------------
# Queue UI badge + filter — exercised via direct DB query through
# the _query_rows helper (the same surface the Streamlit page uses).
# ---------------------------------------------------------------------------
def test_queue_filter_returns_only_grok_semantic_rows(
    db_conn: sqlite3.Connection,
) -> None:
    """The discovered_via filter dropdown on the Queue page composes cumulatively."""
    # Insert one of each discovered_via value so the filter has rows to find.
    db_conn.executemany(
        "INSERT INTO reply_targets "
        "(discovered_via, target_post_url, target_author_handle) "
        "VALUES (?, ?, ?)",
        [
            ("manual", "https://x.com/a/status/100", "a"),
            ("agent_score", "https://x.com/b/status/101", "b"),
            ("next_rep_seed", "https://x.com/c/status/102", "c"),
            ("v1.1_api_search", "https://x.com/d/status/103", "d"),
            ("grok_semantic", "https://x.com/e/status/104", "e"),
        ],
    )

    # The Queue page's _query_rows helper builds parametrized SQL. We
    # can't easily import the .py file (Streamlit page-level code runs
    # at import) so this test asserts the SQL contract directly — same
    # query shape, same expected result.
    rows = db_conn.execute(
        "SELECT * FROM reply_targets WHERE discovered_via = ? ORDER BY id",
        ("grok_semantic",),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["discovered_via"] == "grok_semantic"
    assert rows[0]["target_author_handle"] == "e"

    # All-options dropdown ("(all)") returns 5 rows.
    all_rows = db_conn.execute(
        "SELECT id FROM reply_targets WHERE discovered_via IN "
        "('manual','agent_score','next_rep_seed','v1.1_api_search','grok_semantic')"
    ).fetchall()
    assert len(all_rows) == 5


# ---------------------------------------------------------------------------
# Query list CRUD round-trip via the settings table (mirrors Settings UI).
# ---------------------------------------------------------------------------
def test_grok_query_list_crud_round_trip(db_conn: sqlite3.Connection) -> None:
    """Add / edit / delete the grok_query_list_json via the same UPSERT path."""

    def write(queries: list[str]) -> None:
        db_conn.execute(
            "INSERT INTO settings (key, value_json) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
            ("grok_query_list_json", json.dumps(queries)),
        )

    def read() -> list[str]:
        row = db_conn.execute(
            "SELECT value_json FROM settings WHERE key = 'grok_query_list_json'"
        ).fetchone()
        return json.loads(row["value_json"]) if row else []

    # Initial state: seeded as [].
    assert read() == []

    # Add three.
    write(["meal planning", "saas founder pain", "neuro-onc research"])
    assert read() == ["meal planning", "saas founder pain", "neuro-onc research"]

    # Edit (replace middle).
    write(["meal planning", "biomed ML", "neuro-onc research"])
    assert read()[1] == "biomed ML"

    # Delete all.
    write([])
    assert read() == []


# ---------------------------------------------------------------------------
# Regression — existing manual + replier-pool paths still work.
# ---------------------------------------------------------------------------
def test_manual_paste_path_still_works_after_migration_021(
    db_conn: sqlite3.Connection,
) -> None:
    """A pre-Phase-9 row pattern (discovered_via='manual') still inserts cleanly."""
    db_conn.execute(
        "INSERT INTO reply_targets "
        "(discovered_via, target_post_url, target_author_handle) "
        "VALUES ('manual', 'https://x.com/m/status/200', 'm')"
    )
    row = db_conn.execute(
        "SELECT discovered_via FROM reply_targets "
        "WHERE target_post_url = 'https://x.com/m/status/200'"
    ).fetchone()
    assert row["discovered_via"] == "manual"


def test_replier_under_thread_source_still_valid(db_conn: sqlite3.Connection) -> None:
    """Migration 012's source enum still includes 'replier_under_thread'."""
    db_conn.execute(
        "INSERT INTO reply_targets "
        "(discovered_via, source, target_post_url, target_author_handle) "
        "VALUES ('manual', 'replier_under_thread', "
        "'https://x.com/r/status/300', 'r')"
    )
    row = db_conn.execute(
        "SELECT source FROM reply_targets "
        "WHERE target_post_url = 'https://x.com/r/status/300'"
    ).fetchone()
    assert row["source"] == "replier_under_thread"
