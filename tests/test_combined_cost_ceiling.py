"""Phase 9 — Combined Anthropic + xAI cost-ceiling enforcement (§28.6 / §29.12).

The §28.6 ceiling is one number across both providers. At 100%:

  * ``app/grok_client.py::search`` refuses new calls with
    ``GrokCostCeilingError`` and logs the rejection to
    ``grok_api_responses.rejection_reason='cost_ceiling_hit'``.
  * ``app/agent/cost.py::check_ceiling_or_raise`` raises
    ``MonthlyCostCeilingExceeded`` — which the Anthropic client calls
    before every ``messages.create`` round trip.

This test file pins the cross-provider invariant: a $30 cap, $15 already
spent on Anthropic + $15 already spent on xAI, must refuse BOTH the
next Anthropic call AND the next Grok call.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from app import grok_client
from app.agent import cost


def _seed_anthropic_spend(
    conn: sqlite3.Connection,
    *,
    total_usd: float,
    model: str = "claude-opus-4-7",
) -> None:
    """Seed one ``agent_messages`` row that reconstructs to ``total_usd``."""
    rate_in, rate_out = cost.get_model_rates(model)
    # Spend it all as input tokens to make the math obvious.
    input_tokens = int(total_usd * 1_000_000.0 / rate_in)
    snapshot = {
        "version": cost.RATE_TABLE_VERSION,
        "model": model,
        "input_per_million_usd": rate_in,
        "output_per_million_usd": rate_out,
    }
    # agent_messages requires conversation_id (FK) so create a conversation.
    cur = conn.execute(
        "INSERT INTO agent_conversations (status, model_default) "
        "VALUES ('active', ?) RETURNING id",
        (model,),
    )
    conv_id = int(cur.fetchone()[0])
    conn.execute(
        """
        INSERT INTO agent_messages
            (conversation_id, role, content,
             input_tokens, output_tokens, model, rate_snapshot_json)
        VALUES (?, 'assistant', 'seed-spend',
                ?, 0, ?, ?)
        """,
        (conv_id, input_tokens, model, json.dumps(snapshot)),
    )


def _seed_xai_spend(conn: sqlite3.Connection, *, total_usd: float) -> None:
    """Seed one ``grok_api_responses`` row reconstructing to ``total_usd``."""
    rate_in, rate_out = cost.get_model_rates("grok-4.3")
    input_tokens = int(total_usd * 1_000_000.0 / rate_in)
    snapshot = {
        "provider": "xai",
        "model": "grok-4.3",
        "version": cost.RATE_TABLE_VERSION,
        "input_tokens": input_tokens,
        "output_tokens": 0,
        "input_per_million_usd": rate_in,
        "output_per_million_usd": rate_out,
    }
    conn.execute(
        "INSERT INTO grok_api_responses "
        "(query, response_status_code, rate_snapshot_json) "
        "VALUES (?, 200, ?)",
        ("seed-spend", json.dumps(snapshot)),
    )


def _set_ceiling(conn: sqlite3.Connection, *, cap_usd: float) -> None:
    conn.execute(
        "INSERT INTO settings (key, value_json) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
        ("combined_ai_monthly_cost_ceiling_usd", json.dumps(cap_usd)),
    )


# ---------------------------------------------------------------------------
# Spend reconstruction.
# ---------------------------------------------------------------------------
def test_anthropic_spend_reconstructs_from_agent_messages(
    db_conn: sqlite3.Connection,
) -> None:
    _seed_anthropic_spend(db_conn, total_usd=15.0)
    mtd = cost.month_to_date_spend_usd(db_conn)
    assert mtd == pytest.approx(15.0, abs=0.01)


def test_xai_spend_reconstructs_from_grok_api_responses(
    db_conn: sqlite3.Connection,
) -> None:
    _seed_xai_spend(db_conn, total_usd=15.0)
    mtd_xai = cost.xai_month_to_date_spend_usd(db_conn)
    assert mtd_xai == pytest.approx(15.0, abs=0.01)


def test_combined_spend_is_sum_of_both_providers(
    db_conn: sqlite3.Connection,
) -> None:
    _seed_anthropic_spend(db_conn, total_usd=10.0)
    _seed_xai_spend(db_conn, total_usd=8.0)
    assert cost.combined_month_to_date_spend_usd(db_conn) == pytest.approx(
        18.0, abs=0.02
    )


# ---------------------------------------------------------------------------
# Ceiling enforcement — both providers refuse at 100%.
# ---------------------------------------------------------------------------
def test_anthropic_client_refuses_call_at_combined_ceiling(
    db_conn: sqlite3.Connection,
) -> None:
    """check_ceiling_or_raise raises when combined spend meets the cap."""
    _set_ceiling(db_conn, cap_usd=30.0)
    _seed_anthropic_spend(db_conn, total_usd=15.0)
    _seed_xai_spend(db_conn, total_usd=15.0)
    # MTD = $30 against $30 cap → `>=` refuses at the cap exactly.
    with pytest.raises(cost.MonthlyCostCeilingExceeded):
        cost.check_ceiling_or_raise(db_conn)


def test_grok_client_refuses_call_at_combined_ceiling(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """grok_client.search raises GrokCostCeilingError before HTTP fires."""
    _set_ceiling(db_conn, cap_usd=30.0)
    _seed_anthropic_spend(db_conn, total_usd=15.0)
    _seed_xai_spend(db_conn, total_usd=15.0)

    # If the HTTP path fires, the test fails — the preflight should
    # refuse before any network call.
    def _fail_if_called(**_kwargs: Any) -> Any:
        raise AssertionError(
            "grok_client._http_post_json should not be called when ceiling hit"
        )

    monkeypatch.setattr(grok_client, "_http_post_json", _fail_if_called)
    monkeypatch.setenv("XAI_API_KEY", "dummy")

    with pytest.raises(grok_client.GrokCostCeilingError):
        grok_client.search("test query", conn=db_conn)

    # Audit row written with rejection_reason='cost_ceiling_hit'.
    row = db_conn.execute(
        "SELECT rejection_reason FROM grok_api_responses "
        "WHERE rejection_reason = 'cost_ceiling_hit'"
    ).fetchone()
    assert row is not None


def test_both_providers_pass_when_below_ceiling(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity: a healthy state should NOT trip either provider's gate."""
    _set_ceiling(db_conn, cap_usd=30.0)
    _seed_anthropic_spend(db_conn, total_usd=5.0)
    _seed_xai_spend(db_conn, total_usd=5.0)
    # Anthropic gate passes.
    cost.check_ceiling_or_raise(db_conn)  # no raise
    # Grok gate also passes — stub the HTTP layer to deliver a success.

    def _fake_http(**_kwargs: Any) -> tuple[int, dict, float | None]:
        return (
            200,
            {
                "choices": [{"message": {"content": ""}}],
                "citations": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            None,
        )

    monkeypatch.setattr(grok_client, "_http_post_json", _fake_http)
    monkeypatch.setenv("XAI_API_KEY", "dummy")
    candidates = grok_client.search("test", conn=db_conn)
    # No candidates expected (empty citations), but the call succeeded.
    assert candidates == []


def test_is_combined_ceiling_breached_predicate(
    db_conn: sqlite3.Connection,
) -> None:
    """The cheap predicate matches check_ceiling_or_raise behavior."""
    _set_ceiling(db_conn, cap_usd=30.0)
    _seed_anthropic_spend(db_conn, total_usd=10.0)
    _seed_xai_spend(db_conn, total_usd=10.0)
    # Combined ~$20 vs cap $30 — not breached.
    assert cost.is_combined_ceiling_breached(db_conn) is False
    # With a $15 projected cost — total ~$35 trips the gate.
    assert cost.is_combined_ceiling_breached(
        db_conn, projected_call_cost_usd=15.0
    ) is True


def test_xai_spend_zero_when_grok_api_responses_table_missing(
    empty_db_conn: sqlite3.Connection,
) -> None:
    """xai_month_to_date_spend_usd degrades to 0.0 on pre-migration-021 DB."""
    # empty_db_conn has migrations applied → grok_api_responses exists.
    # Drop it to simulate the pre-migration-021 state.
    empty_db_conn.execute("DROP TABLE IF EXISTS grok_api_responses")
    mtd = cost.xai_month_to_date_spend_usd(empty_db_conn)
    assert mtd == 0.0


def test_xai_spend_only_counts_current_month(db_conn: sqlite3.Connection) -> None:
    """Rows from before the month-start window do NOT count toward MTD."""
    rate_in, _ = cost.get_model_rates("grok-4.3")
    snap = {
        "provider": "xai",
        "model": "grok-4.3",
        "version": cost.RATE_TABLE_VERSION,
        "input_tokens": int(10.0 * 1_000_000 / rate_in),
        "output_tokens": 0,
        "input_per_million_usd": rate_in,
        "output_per_million_usd": 2.50,
    }
    # Backdate to 60 days ago — must NOT be summed.
    db_conn.execute(
        "INSERT INTO grok_api_responses "
        "(query, response_status_code, rate_snapshot_json, created_at_utc) "
        "VALUES (?, 200, ?, datetime('now', '-60 days'))",
        ("ancient", json.dumps(snap)),
    )
    assert cost.xai_month_to_date_spend_usd(db_conn) == 0.0

    # Now seed a current-month row → MTD reflects it.
    _seed_xai_spend(db_conn, total_usd=3.0)
    assert cost.xai_month_to_date_spend_usd(db_conn) == pytest.approx(3.0, abs=0.05)
